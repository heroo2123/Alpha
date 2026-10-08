"""Tests for the offline, read-only PAPER R08/R09 readiness CLI.

No test here creates a socket, imports an HTTP/provider client, runs a
subprocess against a provider, or lets the CLI create a brand-new
EvidenceStore file: every store path exercised here either already exists
(built by the reused R08/R09 test fixtures) before the CLI runs, or is
deliberately left absent to prove the CLI refuses rather than creating one.
"""
from dataclasses import asdict, replace
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import pytest

from polymarket_scanner.v11.evidence import EvidenceError, EvidenceStore
from polymarket_scanner.v11.paper_coordinator import ACCOUNT_KEY, VERSION as COORDINATOR_VERSION
from test_v11_certification_rules import setup
from test_v11_model_artifacts import bundle
from test_v11_pws_admission import coordinator as pws_coordinator, joined, synthetic_proposal
from test_v11_r08_scenario_reservation_readiness import coordinator as r08_coordinator, genuine_proposal
from test_v11_strategy_pipeline import factory

import tools.v11_paper_r08_r09_readiness_cli as cli_module
from tools.v11_paper_r08_r09_readiness_cli import SCHEMA, evaluate, main
from tools.v11_r08_scenario_reservation_readiness import (
    OUTCOME_DEMONSTRATED as R08_DEMONSTRATED,
    OUTCOME_NO_RESERVATION as R08_NO_RESERVATION,
    evaluate_scenario_reservation_readiness,
)
from tools.v11_r09_pws_lead_readiness import (
    OUTCOME_DEMONSTRATED as R09_DEMONSTRATED,
    OUTCOME_NOT_DEMONSTRATED as R09_NOT_DEMONSTRATED,
)

CLI_PATH = __import__('tools.v11_paper_r08_r09_readiness_cli', fromlist=['x']).__file__
REPO_ROOT = Path(CLI_PATH).resolve().parents[1]
_SUBPROCESS_ENV = dict(os.environ, PYTHONDONTWRITEBYTECODE='1')


def _sidecar_listing(path):
    """The store's own file plus any -wal/-shm/-journal sidecars next to it,
    ignoring unrelated files the shared fixture directory may also contain."""
    return sorted(p.name for p in path.parent.glob(path.name + '*'))


def _wal_size(path):
    wal = Path(str(path) + '-wal')
    return wal.stat().st_size if wal.exists() else None


def _assert_listing_unchanged_or_gained_only_empty_sidecars(path, before_listing, before_wal_size):
    # The CLI never removes a `-wal`/`-shm` sidecar (removing one could
    # destroy a concurrent writer's already-committed record -- see
    # `_anchored_snapshot`'s docstring). A store that had none may gain an
    # empty `-wal` plus a `-shm`, the same residue `EvidenceStore.__init__`'s
    # own `mode=ro` inspection already leaves; it must never lose a sidecar
    # or grow a non-empty one.
    after_listing = _sidecar_listing(path)
    allowed = set(before_listing) | {path.name + '-wal', path.name + '-shm'}
    assert set(after_listing) <= allowed
    assert set(before_listing) <= set(after_listing)
    after_wal_size = _wal_size(path)
    if before_wal_size is not None:
        assert after_wal_size == before_wal_size
    else:
        assert after_wal_size in (None, 0)


def _membership_dict(m):
    return dict(station=m.station, city=m.city, region=m.region,
               weather_groups=list(m.weather_groups), source_groups=list(m.source_groups),
               model_groups=list(m.model_groups), metadata_fingerprint=m.metadata_fingerprint)


def _config(c, *, r09=None):
    return {
        "store": {"path": str(c.store.path), "namespace": c.store.namespace},
        "account_policy": asdict(c.policy),
        "correlation": {
            "version": c.correlation.version,
            "evidence_sha256": c.correlation.evidence_sha256,
            "memberships": [_membership_dict(m) for m in c.correlation.memberships],
        },
        "scenario_limits": asdict(c.limits),
        "r09": r09,
    }


def _r09_block(rig, *, preconfirmation_id='paired-pin', payout_admission_ids=('payout-pin',)):
    return {
        "preconfirmation_id": preconfirmation_id,
        "context": asdict(rig['context']),
        "rule": asdict(rig['rule']),
        "binding": asdict(rig['binding']),
        "payout_admission_ids": list(payout_admission_ids),
    }


def _forge_account_head(rig, c, state, record_id):
    head = c._head()
    rig['store'].audit(record_id, event_id=ACCOUNT_KEY, kind='COORDINATOR_EVENT',
                       details=dict(version=COORDINATOR_VERSION, policy_sha256=c.policy_sha,
                                    request={}, state=state),
                       expected_previous_seq=head['seq'] if head else 0)


def _bare_store_cfg(store_path, namespace='V11_PAPER'):
    return {
        "store": {"path": str(store_path), "namespace": namespace},
        "account_policy": {"policy_version": "p", "account_id": "a", "collateral_asset": "C",
                           "initial_hypothetical_cash": "10", "capital_limit": "10",
                           "per_intent_cash_limit": "10", "daily_loss_limit": "10",
                           "minimum_ev_per_share": ".01", "retained_cash": "0",
                           "maximum_intent_lifetime_seconds": 60.0, "max_active_intents": 10},
        "correlation": {"version": "v", "evidence_sha256": "a" * 64, "memberships": [
            {"station": "KATL", "city": "Atlanta", "region": "SE", "weather_groups": ["W"],
             "source_groups": ["S"], "model_groups": ["M"], "metadata_fingerprint": "b" * 64},
        ]},
        "scenario_limits": {"policy_version": "p", "per_event": "10", "per_city": "20", "per_region": "30",
                           "per_weather_group": "30", "per_source_group": "30", "per_model_group": "30",
                           "portfolio": "50", "max_position_units": "100"},
    }


def _wait_for(predicate, timeout=10.0):
    # A bounded poll, not a correctness assertion: keeps a broken
    # interleaving from hanging the test suite instead of failing it.
    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() > deadline:
            raise AssertionError('timed out waiting for condition')
        time.sleep(0.001)


class _SchemaReadHook:
    """Run ``action()`` synchronously the first time any connection the CLI
    module opens executes a read of ``sqlite_schema`` -- the anchored
    snapshot's own first query against the caller's source store. This
    forces a deterministic writer/CLI interleaving instead of a
    timing-based sleep, so the concurrency regression tests below are not
    flaky and see the same interleaving under both normal and `-O`
    interpreters.
    """
    def __init__(self, action):
        self.action = action
        self.fired = False
        self.real_connect = cli_module.sqlite3.connect

    def __enter__(self):
        hook = self

        class _Connection(sqlite3.Connection):
            def execute(self, sql, *args):
                result = super().execute(sql, *args)
                if not hook.fired and 'sqlite_schema' in sql:
                    hook.fired = True
                    hook.action()
                return result

        def connect(*args, **kwargs):
            kwargs.setdefault('factory', _Connection)
            return hook.real_connect(*args, **kwargs)

        cli_module.sqlite3.connect = connect
        return self

    def __exit__(self, *exc_info):
        cli_module.sqlite3.connect = self.real_connect


_CONCURRENT_WRITER_SCRIPT = """
import sys
sys.path.insert(0, %r)
from pathlib import Path
from polymarket_scanner.v11.evidence import EvidenceStore
store = EvidenceStore(Path(%r), %r)
store.audit(%r, event_id='concurrent-writer-probe', kind='OPERATOR_EVENT', details={'n': 1})
"""


def _run_concurrent_writer(store_path, namespace, record_id):
    script = _CONCURRENT_WRITER_SCRIPT % (str(REPO_ROOT), str(store_path), namespace, record_id)
    subprocess.run([sys.executable, '-c', script], check=True, timeout=30, env=_SUBPROCESS_ENV)


def _record_count(store_path, record_id):
    con = sqlite3.connect(store_path.as_uri() + '?mode=ro', uri=True)
    try:
        return con.execute('SELECT COUNT(*) FROM v11_records WHERE record_id=?', (record_id,)).fetchone()[0]
    finally:
        con.close()


# -- Config/file-level fail-closed behavior (no real evidence store needed) --

def test_missing_config_file_refuses(tmp_path):
    assert main([str(tmp_path / 'missing.json')]) == 1


def test_symlinked_config_file_refuses(tmp_path):
    real = tmp_path / 'real.json'
    real.write_text('{}')
    link = tmp_path / 'link.json'
    link.symlink_to(real)
    assert main([str(link)]) == 1


def test_malformed_json_refuses(tmp_path):
    bad = tmp_path / 'bad.json'
    bad.write_text('not json')
    assert main([str(bad)]) == 1


def test_oversized_config_refuses(tmp_path):
    big = tmp_path / 'big.json'
    big.write_text('x' * 70000)
    assert main([str(big)]) == 1


@pytest.mark.parametrize('cfg', [
    {},
    {"store": {}, "account_policy": {}, "correlation": {}},
    {"store": {}, "account_policy": {}, "correlation": {}, "scenario_limits": {}, "unknown_extra": 1},
])
def test_bad_top_level_schema_refuses(tmp_path, cfg):
    path = tmp_path / 'cfg.json'
    path.write_text(json.dumps(cfg))
    assert main([str(path)]) == 1


def test_store_path_must_preexist(tmp_path):
    missing_store = tmp_path / 'does-not-exist.sqlite'
    cfg = {
        "store": {"path": str(missing_store), "namespace": "V11_PAPER"},
        "account_policy": {"policy_version": "p", "account_id": "a", "collateral_asset": "C",
                           "initial_hypothetical_cash": "10", "capital_limit": "10",
                           "per_intent_cash_limit": "10", "daily_loss_limit": "10",
                           "minimum_ev_per_share": ".01", "retained_cash": "0",
                           "maximum_intent_lifetime_seconds": 60.0, "max_active_intents": 10},
        "correlation": {"version": "v", "evidence_sha256": "a" * 64, "memberships": []},
        "scenario_limits": {"policy_version": "p", "per_event": "10", "per_city": "20", "per_region": "30",
                           "per_weather_group": "30", "per_source_group": "30", "per_model_group": "30",
                           "portfolio": "50", "max_position_units": "100"},
    }
    path = tmp_path / 'cfg.json'
    path.write_text(json.dumps(cfg))
    assert main([str(path)]) == 1
    # Fail-closed, not merely non-zero: the CLI must never create the store
    # it was about to read just because the caller mistyped its path.
    assert not missing_store.exists()


def test_store_path_must_be_absolute(tmp_path):
    cfg = {
        "store": {"path": "relative/evidence.sqlite", "namespace": "V11_PAPER"},
        "account_policy": {"policy_version": "p", "account_id": "a", "collateral_asset": "C",
                           "initial_hypothetical_cash": "10", "capital_limit": "10",
                           "per_intent_cash_limit": "10", "daily_loss_limit": "10",
                           "minimum_ev_per_share": ".01", "retained_cash": "0",
                           "maximum_intent_lifetime_seconds": 60.0, "max_active_intents": 10},
        "correlation": {"version": "v", "evidence_sha256": "a" * 64, "memberships": []},
        "scenario_limits": {"policy_version": "p", "per_event": "10", "per_city": "20", "per_region": "30",
                           "per_weather_group": "30", "per_source_group": "30", "per_model_group": "30",
                           "portfolio": "50", "max_position_units": "100"},
    }
    path = tmp_path / 'cfg.json'
    path.write_text(json.dumps(cfg))
    assert main([str(path)]) == 1


@pytest.mark.parametrize('interpreter_flags', [(), ('-O',)])
def test_store_path_must_preexist_under_subprocess(tmp_path, interpreter_flags):
    # Same fail-closed refusal as test_store_path_must_preexist, but run as a
    # real standalone-script subprocess under both normal and `python -O`
    # interpreters: this production module uses explicit `raise`, never
    # `assert`, so `-O` (which strips assert statements) must not change its
    # refusal behavior.
    missing_store = tmp_path / 'does-not-exist.sqlite'
    cfg = {
        "store": {"path": str(missing_store), "namespace": "V11_PAPER"},
        "account_policy": {"policy_version": "p", "account_id": "a", "collateral_asset": "C",
                           "initial_hypothetical_cash": "10", "capital_limit": "10",
                           "per_intent_cash_limit": "10", "daily_loss_limit": "10",
                           "minimum_ev_per_share": ".01", "retained_cash": "0",
                           "maximum_intent_lifetime_seconds": 60.0, "max_active_intents": 10},
        "correlation": {"version": "v", "evidence_sha256": "a" * 64, "memberships": [
            {"station": "KATL", "city": "Atlanta", "region": "SE", "weather_groups": ["W"],
             "source_groups": ["S"], "model_groups": ["M"], "metadata_fingerprint": "b" * 64},
        ]},
        "scenario_limits": {"policy_version": "p", "per_event": "10", "per_city": "20", "per_region": "30",
                           "per_weather_group": "30", "per_source_group": "30", "per_model_group": "30",
                           "portfolio": "50", "max_position_units": "100"},
    }
    path = tmp_path / 'cfg.json'
    path.write_text(json.dumps(cfg))
    proc = subprocess.run([sys.executable, *interpreter_flags, CLI_PATH, str(path)],
                         capture_output=True, text=True, timeout=30)
    assert proc.returncode == 1
    assert 'STORE_PATH_MUST_PREEXIST' in proc.stderr
    assert not missing_store.exists()


# -- Real-evidence-store behavior via evaluate() (direct, clock-injected) ----

def test_type_checked_schema_round_trips(factory):
    rig = factory()
    c = r08_coordinator(rig)
    result = evaluate(_config(c), clock=lambda: rig['now'][0])
    assert result['schema'] == SCHEMA
    assert result['financial_authority'] is False


def test_empty_account_is_accurately_reported_as_no_reservation(factory):
    # Absent evidence: nothing was ever coordinated into this store.
    rig = factory()
    c = r08_coordinator(rig)
    result = evaluate(_config(c), clock=lambda: rig['now'][0])
    assert result['r08']['outcome'] == R08_NO_RESERVATION
    assert result['r08']['reserved_intent_count'] == 0
    assert result['r09_status'] == 'R09_NOT_REQUESTED'
    assert result['r09'] is None


def test_genuine_reservation_is_demonstrated_and_matches_direct_call(factory):
    rig = factory()
    c = r08_coordinator(rig)
    p = genuine_proposal(rig)
    outcome = c.coordinate('batch', (p,))['body']['details']
    assert outcome['reserved_intent_ids'] == [p.proposal_id]
    direct = evaluate_scenario_reservation_readiness(c)
    result = evaluate(_config(c), clock=lambda: rig['now'][0])
    assert result['r08']['outcome'] == R08_DEMONSTRATED
    # The CLI must reuse, not re-derive, the reviewed evaluator's own output.
    assert result['r08'] == direct.to_dict()


def test_replay_is_idempotent(factory):
    rig = factory()
    c = r08_coordinator(rig)
    c.coordinate('batch', (genuine_proposal(rig),))
    cfg = _config(c)
    first = evaluate(cfg, clock=lambda: rig['now'][0])
    second = evaluate(cfg, clock=lambda: rig['now'][0])
    assert first == second


def test_stale_admission_cannot_be_laundered_into_demonstrated(factory):
    rig = factory()
    p = genuine_proposal(rig)
    rig['now'][0] += 10000.  # expire the admission's certification/rule window
    c = r08_coordinator(rig)
    result = c.coordinate('batch', (p,))['body']['details']
    assert result['reserved_intent_ids'] == []
    probe = evaluate(_config(c), clock=lambda: rig['now'][0])
    assert probe['r08']['outcome'] == R08_NO_RESERVATION


def test_forged_unprovenanced_intent_is_not_genuine(factory):
    rig = factory()
    c = r08_coordinator(rig)
    state = c._state(c._head())
    state['intents']['forged'] = dict(
        proposal_id='forged', event_id=rig['context'].event_id, token_id='token',
        direction='BUY', units='2', filled_units='0', unit_collateral_bound='.5',
        attribution=[dict(strategy=rig['scope'].strategy, weight='1')],
        status='RESERVED', cancel_requested=False,
    )
    _forge_account_head(rig, c, state, 'forged-head')
    result = evaluate(_config(c), clock=lambda: rig['now'][0])
    assert result['r08']['outcome'] == R08_NO_RESERVATION
    assert 'RESERVED_INTENT_PROVENANCE_UNVERIFIED' in result['r08']['reasons']


def test_malformed_account_evidence_refuses_rather_than_fabricates(factory):
    # Genuinely malformed retained evidence (no account_id) must make the
    # whole CLI run fail closed, never be silently reported as some typed
    # "no reservation" outcome standing in for an actually corrupt store.
    rig = factory()
    c = r08_coordinator(rig)
    _forge_account_head(rig, c, {}, 'malformed-head')
    with pytest.raises(EvidenceError):
        evaluate(_config(c), clock=lambda: rig['now'][0])
    # Exercised through main() too: fail-closed, non-zero, no fabricated stdout result.
    cfg_path = c.store.path.parent / 'malformed.json'
    cfg_path.write_text(json.dumps(_config(c)))
    assert main([str(cfg_path)]) == 1


# -- R09 behavior (reuses the same `evaluate()`, via its own real pair) -----

def test_r09_not_requested_is_distinct_from_not_demonstrated(joined):
    c = pws_coordinator(joined)
    result = evaluate(_config(c), clock=lambda: joined['now'][0])
    assert result['r09_status'] == 'R09_NOT_REQUESTED'
    assert result['r09'] is None


def test_r09_paired_but_unreserved_is_not_demonstrated(joined):
    c = pws_coordinator(joined)  # pair exists; nothing coordinated yet
    cfg = _config(c, r09=_r09_block(joined))
    result = evaluate(cfg, clock=lambda: joined['now'][0])
    assert result['r09_status'] == 'R09_EVALUATED'
    assert result['r09']['outcome'] == R09_NOT_DEMONSTRATED
    assert result['r09']['preconfirmation_revalidates'] is True
    assert result['r09']['pws_never_settlement_authority'] is True
    assert result['r09']['unpaired_pws_proposal_refused'] is True
    assert result['r09']['genuine_pws_reservation_present'] is False
    assert result['r09']['financial_authority'] is False


def test_r09_missing_pin_is_not_demonstrated(joined):
    c = pws_coordinator(joined)
    cfg = _config(c, r09=_r09_block(joined, preconfirmation_id='never-pinned'))
    result = evaluate(cfg, clock=lambda: joined['now'][0])
    assert result['r09']['outcome'] == R09_NOT_DEMONSTRATED
    assert result['r09']['preconfirmation_revalidates'] is False
    assert any(r.startswith('PRECONFIRMATION_DOES_NOT_REVALIDATE:') for r in result['r09']['reasons'])


def test_r09_genuine_reservation_is_demonstrated(joined):
    p = synthetic_proposal(joined)
    c = pws_coordinator(joined)
    outcome = c.coordinate('batch', (p,))['body']['details']
    assert outcome['reserved_intent_ids'] == ['one']
    cfg = _config(c, r09=_r09_block(joined))
    result = evaluate(cfg, clock=lambda: joined['now'][0])
    assert result['r09']['outcome'] == R09_DEMONSTRATED
    assert result['r09']['genuine_pws_reservation_present'] is True
    assert result['r09']['financial_authority'] is False
    assert result['r08']['outcome'] == R08_DEMONSTRATED


# -- No socket/subprocess/provider client text in the new module itself -----

def test_module_source_contains_no_network_or_process_calls():
    with open(CLI_PATH, 'r', encoding='utf-8') as fh:
        source = fh.read()
    for banned in ('import socket', 'import subprocess', 'import urllib', 'import requests',
                   'import httpx', 'import aiohttp', 'os.system', 'Popen'):
        assert banned not in source


# -- One true end-to-end run through the standalone script entrypoint -------

@pytest.mark.parametrize('interpreter_flags', [(), ('-O',)])
def test_standalone_script_entrypoint_end_to_end(tmp_path, interpreter_flags):
    from polymarket_scanner.v11.evidence import EvidenceStore
    store_path = tmp_path / 'evidence.sqlite'
    tmp_path.chmod(0o700)
    EvidenceStore(store_path, 'V11_PAPER')  # pre-exists before the CLI ever runs
    cfg = {
        "store": {"path": str(store_path), "namespace": "V11_PAPER"},
        "account_policy": {"policy_version": "p", "account_id": "a", "collateral_asset": "C",
                           "initial_hypothetical_cash": "10", "capital_limit": "10",
                           "per_intent_cash_limit": "10", "daily_loss_limit": "10",
                           "minimum_ev_per_share": ".01", "retained_cash": "0",
                           "maximum_intent_lifetime_seconds": 60.0, "max_active_intents": 10},
        "correlation": {"version": "v", "evidence_sha256": "a" * 64, "memberships": [
            {"station": "KATL", "city": "Atlanta", "region": "SE", "weather_groups": ["W"],
             "source_groups": ["S"], "model_groups": ["M"], "metadata_fingerprint": "b" * 64},
        ]},
        "scenario_limits": {"policy_version": "p", "per_event": "10", "per_city": "20", "per_region": "30",
                           "per_weather_group": "30", "per_source_group": "30", "per_model_group": "30",
                           "portfolio": "50", "max_position_units": "100"},
    }
    cfg_path = tmp_path / 'cfg.json'
    cfg_path.write_text(json.dumps(cfg))
    proc = subprocess.run([sys.executable, *interpreter_flags, CLI_PATH, str(cfg_path)],
                         capture_output=True, text=True, timeout=30)
    assert proc.returncode == 0
    payload = json.loads(proc.stdout)
    assert payload['schema'] == SCHEMA
    assert payload['financial_authority'] is False
    assert payload['r08']['outcome'] == R08_NO_RESERVATION
    assert payload['r09_status'] == 'R09_NOT_REQUESTED'


# -- Adversarial file-layer regression tests (independent review F1/F2) -----
#
# These prove the CLI never changes the caller's own evidence-store bytes,
# sidecars, or directory listing -- on success *and* on refusal -- and never
# creates a store via a TOCTOU race, by actually inspecting the file system
# before and after a real `main()`/`evaluate()` run, rather than trusting the
# module's own claims.

def test_rollback_journal_store_success_is_byte_and_mtime_identical(factory, tmp_path):
    rig = factory()
    c = r08_coordinator(rig)
    con = sqlite3.connect(c.store.path)
    con.execute('PRAGMA journal_mode=DELETE')
    con.close()
    assert sqlite3.connect(c.store.path).execute('PRAGMA journal_mode').fetchone() == ('delete',)
    before_bytes = c.store.path.read_bytes()
    before_mtime = c.store.path.stat().st_mtime_ns
    before_listing = _sidecar_listing(c.store.path)
    cfg_path = tmp_path / 'cfg.json'
    cfg_path.write_text(json.dumps(_config(c)))
    assert main([str(cfg_path)]) == 0
    assert c.store.path.read_bytes() == before_bytes
    assert c.store.path.stat().st_mtime_ns == before_mtime
    assert _sidecar_listing(c.store.path) == before_listing


def test_rollback_journal_store_refusal_is_byte_and_mtime_identical(factory, tmp_path):
    # Mirrors the review's P5: a rollback-journal store plus a config whose
    # account policy no longer matches the one already bound into the
    # account's persisted head must refuse (PAPER_ACCOUNT_POLICY_OR_IDENTITY_
    # CHANGED) -- and must do so without ever converting the store to WAL.
    rig = factory()
    c = r08_coordinator(rig)
    c.coordinate('batch', (genuine_proposal(rig),))
    con = sqlite3.connect(c.store.path)
    con.execute('PRAGMA journal_mode=DELETE')
    con.close()
    before_bytes = c.store.path.read_bytes()
    before_mtime = c.store.path.stat().st_mtime_ns
    before_listing = _sidecar_listing(c.store.path)
    cfg = _config(c)
    cfg['account_policy'] = asdict(replace(c.policy, capital_limit='999'))
    cfg_path = tmp_path / 'cfg.json'
    cfg_path.write_text(json.dumps(cfg))
    assert main([str(cfg_path)]) == 1
    assert c.store.path.read_bytes() == before_bytes
    assert c.store.path.stat().st_mtime_ns == before_mtime
    assert _sidecar_listing(c.store.path) == before_listing


def test_wal_store_without_sidecars_gains_only_empty_sidecars_on_refusal(factory, tmp_path):
    # Revised after independent review F1: the CLI must never unlink a
    # `-wal`/`-shm` sidecar, including one its own `mode=ro` inspection just
    # created, because a concurrent writer could be relying on it (see
    # `_anchored_snapshot`'s docstring). So a plain WAL-mode store that had
    # no sidecars may gain an empty `-wal`/`-shm` pair, but never loses one
    # and never gains a non-empty one.
    rig = factory()
    c = r08_coordinator(rig)
    assert not Path(str(c.store.path) + '-wal').exists()
    before_bytes = c.store.path.read_bytes()
    before_listing = _sidecar_listing(c.store.path)
    cfg = _config(c)
    cfg['store']['namespace'] = 'CHALLENGER:wrong'
    cfg_path = tmp_path / 'cfg.json'
    cfg_path.write_text(json.dumps(cfg))
    assert main([str(cfg_path)]) == 1
    assert c.store.path.read_bytes() == before_bytes
    _assert_listing_unchanged_or_gained_only_empty_sidecars(c.store.path, before_listing, None)


def test_wal_store_without_sidecars_gains_only_empty_sidecars_on_success(factory, tmp_path):
    rig = factory()
    c = r08_coordinator(rig)
    assert not Path(str(c.store.path) + '-wal').exists()
    before_bytes = c.store.path.read_bytes()
    before_listing = _sidecar_listing(c.store.path)
    cfg_path = tmp_path / 'cfg.json'
    cfg_path.write_text(json.dumps(_config(c)))
    assert main([str(cfg_path)]) == 0
    assert c.store.path.read_bytes() == before_bytes
    _assert_listing_unchanged_or_gained_only_empty_sidecars(c.store.path, before_listing, None)


def test_live_uncheckpointed_wal_sidecars_are_neither_checkpointed_nor_deleted(factory, tmp_path, capsys):
    # Mirrors the review's P3b: a byte-level retained copy of a *live*
    # ledger -- main file plus an uncheckpointed -wal/-shm pair, taken while
    # a writer still holds the database open -- must be readable correctly
    # (the genuine reservation is still visible) without the CLI merging the
    # WAL into the main file or deleting either sidecar.
    rig = factory()
    c = r08_coordinator(rig)
    # An idle second connection keeps WAL frames from being auto-checkpointed
    # away when the coordinator's own connection closes, the same way a real
    # live writer process would.
    idle = sqlite3.connect(c.store.path)
    idle.execute('PRAGMA journal_mode=WAL')
    p = genuine_proposal(rig)
    outcome = c.coordinate('batch', (p,))['body']['details']
    assert outcome['reserved_intent_ids'] == [p.proposal_id]
    wal_path = Path(str(c.store.path) + '-wal')
    assert wal_path.exists() and wal_path.stat().st_size > 0
    retained = tmp_path / 'retained'
    retained.mkdir(mode=0o700)
    for suffix in ('', '-wal', '-shm'):
        src = Path(str(c.store.path) + suffix)
        if src.exists():
            shutil.copy2(src, retained / src.name)
    idle.close()  # only now does the original, non-retained copy get checkpointed
    retained_store = retained / c.store.path.name
    before = {q.name: q.read_bytes() for q in retained.iterdir()}
    before_listing = sorted(before)
    cfg = _config(c)
    cfg['store']['path'] = str(retained_store)
    cfg_path = tmp_path / 'cfg.json'
    cfg_path.write_text(json.dumps(cfg))
    capsys.readouterr()
    assert main([str(cfg_path)]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload['r08']['outcome'] == R08_DEMONSTRATED  # correctly saw the live WAL's data
    after = {q.name: q.read_bytes() for q in retained.iterdir()}
    assert sorted(after) == before_listing  # no file added, removed, or renamed
    assert after[retained_store.name] == before[retained_store.name]  # main bytes untouched
    assert after[retained_store.name + '-wal'] == before[retained_store.name + '-wal']  # WAL untouched


def test_read_only_media_fails_closed_without_mutation(factory, tmp_path):
    # Mirrors the review's P4: a store on genuinely read-only media (no
    # pre-existing sidecars to reuse) cannot be opened at all -- SQLite
    # itself cannot attach a WAL-mode database without creating a wal-index
    # -- but that must fail closed, never partially mutate the source.
    rig = factory()
    c = r08_coordinator(rig)
    cfg_path = tmp_path / 'cfg.json'
    cfg_path.write_text(json.dumps(_config(c)))
    before_bytes = c.store.path.read_bytes()
    parent = c.store.path.parent
    os.chmod(c.store.path, 0o400)
    os.chmod(parent, 0o500)
    try:
        assert main([str(cfg_path)]) == 1
    finally:
        os.chmod(parent, 0o700)
        os.chmod(c.store.path, 0o600)
    assert c.store.path.read_bytes() == before_bytes


def test_store_removed_between_identity_check_and_backup_does_not_create_one(factory, tmp_path, monkeypatch):
    # Mirrors the review's P12/F2: even if the caller's path vanishes in the
    # narrow window after this CLI has confirmed it exists, the CLI must
    # never fall through to a code path that creates a brand-new ledger
    # there. Since evaluation now only ever opens a private snapshot copy,
    # not the original path, there is no `EvidenceStore()` call against the
    # original left to race.
    rig = factory()
    c = r08_coordinator(rig)
    store_path = c.store.path
    original_check = cli_module._check_source_identity

    def racing_check(path):
        original_check(path)
        path.unlink()  # simulate concurrent removal right after our own check

    monkeypatch.setattr(cli_module, '_check_source_identity', racing_check)
    cfg_path = tmp_path / 'cfg.json'
    cfg_path.write_text(json.dumps(_config(c)))
    assert main([str(cfg_path)]) == 1
    assert not store_path.exists()


def test_store_replaced_between_identity_check_and_backup_is_refused(factory, tmp_path, monkeypatch):
    # The window this guards is between `_anchored_snapshot`'s own pre-backup
    # `stat()` and its post-backup `stat()`: swap in a different, but still
    # valid, V11 store with the same name right as the backup connection is
    # about to be opened, and confirm the dev/ino mismatch is caught rather
    # than silently evaluating the swapped-in file.
    rig = factory()
    c = r08_coordinator(rig)
    store_path = c.store.path
    replacement_dir = tmp_path / 'replacement'
    replacement_dir.mkdir(mode=0o700)
    replacement_path = replacement_dir / 'other.sqlite'
    EvidenceStore(replacement_path, 'V11_PAPER')
    original_connect = cli_module.sqlite3.connect
    swapped = []

    def racing_connect(*args, **kwargs):
        if not swapped:
            swapped.append(True)
            os.replace(replacement_path, store_path)  # same name, new inode
        return original_connect(*args, **kwargs)

    monkeypatch.setattr(cli_module.sqlite3, 'connect', racing_connect)
    cfg_path = tmp_path / 'cfg.json'
    cfg_path.write_text(json.dumps(_config(c)))
    assert main([str(cfg_path)]) == 1


def test_scratch_snapshot_directories_are_always_removed(factory, tmp_path):
    rig = factory()
    c = r08_coordinator(rig)
    scratch_glob = '.v11-paper-readiness-snapshot-*'
    before = set(Path(tempfile.gettempdir()).glob(scratch_glob))
    cfg_path = tmp_path / 'cfg.json'
    cfg_path.write_text(json.dumps(_config(c)))
    assert main([str(cfg_path)]) == 0  # success path
    cfg = _config(c)
    cfg['store']['namespace'] = 'CHALLENGER:wrong'
    bad_cfg_path = tmp_path / 'bad_cfg.json'
    bad_cfg_path.write_text(json.dumps(cfg))
    assert main([str(bad_cfg_path)]) == 1  # refusal path
    after = set(Path(tempfile.gettempdir()).glob(scratch_glob))
    assert after == before


@pytest.mark.parametrize('interpreter_flags', [(), ('-O',)])
def test_rollback_journal_store_byte_identical_under_subprocess(tmp_path, interpreter_flags):
    store_path = tmp_path / 'evidence.sqlite'
    tmp_path.chmod(0o700)
    EvidenceStore(store_path, 'V11_PAPER')
    con = sqlite3.connect(store_path)
    con.execute('PRAGMA journal_mode=DELETE')
    con.close()
    before_bytes = store_path.read_bytes()
    before_mtime = store_path.stat().st_mtime_ns
    before_listing = _sidecar_listing(store_path)
    cfg = {
        "store": {"path": str(store_path), "namespace": "V11_PAPER"},
        "account_policy": {"policy_version": "p", "account_id": "a", "collateral_asset": "C",
                           "initial_hypothetical_cash": "10", "capital_limit": "10",
                           "per_intent_cash_limit": "10", "daily_loss_limit": "10",
                           "minimum_ev_per_share": ".01", "retained_cash": "0",
                           "maximum_intent_lifetime_seconds": 60.0, "max_active_intents": 10},
        "correlation": {"version": "v", "evidence_sha256": "a" * 64, "memberships": [
            {"station": "KATL", "city": "Atlanta", "region": "SE", "weather_groups": ["W"],
             "source_groups": ["S"], "model_groups": ["M"], "metadata_fingerprint": "b" * 64},
        ]},
        "scenario_limits": {"policy_version": "p", "per_event": "10", "per_city": "20", "per_region": "30",
                           "per_weather_group": "30", "per_source_group": "30", "per_model_group": "30",
                           "portfolio": "50", "max_position_units": "100"},
    }
    cfg_path = tmp_path / 'cfg.json'
    cfg_path.write_text(json.dumps(cfg))
    proc = subprocess.run([sys.executable, *interpreter_flags, CLI_PATH, str(cfg_path)],
                         capture_output=True, text=True, timeout=30)
    assert proc.returncode == 0
    assert store_path.read_bytes() == before_bytes
    assert store_path.stat().st_mtime_ns == before_mtime
    assert _sidecar_listing(store_path) == before_listing


# -- Concurrent-writer regression tests (independent review F1) ------------
#
# On 490a00d, `_anchored_snapshot`'s own `mode=ro` connection creates empty
# `-wal`/`-shm` sidecars next to a store that has none, then unlinks them in
# its `finally` block because they were "absent at check time". SQLite
# identifies those files by path, not by creator, so a writer that attaches
# during the snapshot shares exactly the sidecars the CLI just created and
# is relying on them: the writer's commit returns success, but the record
# lands only in the WAL (the CLI's held read transaction blocks the
# writer's close-time checkpoint from folding it into the main file), and
# the CLI's unlink then silently destroys it. Each test here forces that
# exact interleaving deterministically, through `_SchemaReadHook`, rather
# than relying on timing, so it fails the same way under both the normal
# and the `-O` interpreter.

def test_concurrent_writer_commit_during_snapshot_survives_on_success(tmp_path):
    tmp_path.chmod(0o700)
    store_path = tmp_path / 'evidence.sqlite'
    EvidenceStore(store_path, 'V11_PAPER')
    assert not Path(str(store_path) + '-wal').exists()
    before_bytes = store_path.read_bytes()
    before_mtime = store_path.stat().st_mtime_ns
    record_id = 'concurrent-commit-success'
    cfg_path = tmp_path / 'cfg.json'
    cfg_path.write_text(json.dumps(_bare_store_cfg(store_path)))

    with _SchemaReadHook(lambda: _run_concurrent_writer(store_path, 'V11_PAPER', record_id)):
        rc = main([str(cfg_path)])

    if rc != 0:
        raise AssertionError(f'CLI success path refused: {rc}')
    if store_path.read_bytes() != before_bytes or store_path.stat().st_mtime_ns != before_mtime:
        raise AssertionError('the source database main file changed during the snapshot')
    if not _wal_size(store_path):
        raise AssertionError('the concurrent writer\'s committed WAL frames were removed')
    if _record_count(store_path, record_id) != 1:
        raise AssertionError('a committed concurrent record was lost on the success path')


def test_concurrent_writer_commit_during_snapshot_survives_on_refusal(tmp_path):
    # Same interleaving, but the CLI's own evaluation refuses (namespace
    # mismatch): a refusal must not be allowed to destroy evidence either.
    tmp_path.chmod(0o700)
    store_path = tmp_path / 'evidence.sqlite'
    EvidenceStore(store_path, 'V11_PAPER')
    assert not Path(str(store_path) + '-wal').exists()
    before_bytes = store_path.read_bytes()
    before_mtime = store_path.stat().st_mtime_ns
    record_id = 'concurrent-commit-refusal'
    cfg = _bare_store_cfg(store_path, namespace='CHALLENGER:wrong')
    cfg_path = tmp_path / 'cfg.json'
    cfg_path.write_text(json.dumps(cfg))

    with _SchemaReadHook(lambda: _run_concurrent_writer(store_path, 'V11_PAPER', record_id)):
        rc = main([str(cfg_path)])

    if rc != 1:
        raise AssertionError(f'CLI refusal path returned {rc}')
    if store_path.read_bytes() != before_bytes or store_path.stat().st_mtime_ns != before_mtime:
        raise AssertionError('the source database main file changed during the refusal')
    if not _wal_size(store_path):
        raise AssertionError('the concurrent writer\'s committed WAL frames were removed')
    if _record_count(store_path, record_id) != 1:
        raise AssertionError('a committed concurrent record was lost on the refusal path')


def test_long_lived_and_fresh_writer_split_brain_all_records_survive(tmp_path):
    # Mirrors the independent review's A3: a writer holds a connection open
    # across the CLI's entire snapshot and commits once during it and once
    # after the CLI has exited; a second, entirely separate writer process
    # commits in between, after the CLI is gone but while the long-lived
    # writer is still open. On 490a00d the long-lived writer kept writing to
    # the inodes the CLI had already unlinked, while the fresh writer's
    # commit landed on new ones created at the same path -- a split brain
    # that silently dropped the fresh writer's record.
    tmp_path.chmod(0o700)
    store_path = tmp_path / 'evidence.sqlite'
    EvidenceStore(store_path, 'V11_PAPER')
    assert not Path(str(store_path) + '-wal').exists()
    ctl = tmp_path / 'ctl'
    ctl.mkdir(mode=0o700)
    long_lived_script = f"""
import sqlite3, time
from pathlib import Path
ctl = Path({str(ctl)!r})
c = sqlite3.connect({str(store_path)!r}, timeout=5)
c.execute('PRAGMA journal_mode=WAL')
c.execute('PRAGMA synchronous=FULL')
c.execute("INSERT INTO v11_meta VALUES('lw-1', 'x')")
c.commit()
(ctl / 'committed1').touch()
while not (ctl / 'go2').exists():
    time.sleep(0.001)
c.execute("INSERT INTO v11_meta VALUES('lw-2', 'x')")
c.commit()
(ctl / 'committed2').touch()
while not (ctl / 'close').exists():
    time.sleep(0.001)
c.close()
"""
    long_lived_proc = []

    def start_long_lived_writer():
        long_lived_proc.append(subprocess.Popen([sys.executable, '-c', long_lived_script], env=_SUBPROCESS_ENV))
        _wait_for(lambda: (ctl / 'committed1').exists())

    cfg_path = tmp_path / 'cfg.json'
    cfg_path.write_text(json.dumps(_bare_store_cfg(store_path)))
    try:
        with _SchemaReadHook(start_long_lived_writer):
            rc = main([str(cfg_path)])
        if rc != 0:
            raise AssertionError(f'CLI success path refused: {rc}')

        (ctl / 'go2').touch()
        _wait_for(lambda: (ctl / 'committed2').exists())
        fresh_writer_script = (
            "import sqlite3;"
            f"c = sqlite3.connect({str(store_path)!r}, timeout=5);"
            "c.execute('PRAGMA journal_mode=WAL');"
            "c.execute(\"INSERT INTO v11_meta VALUES('fresh-3', 'x')\");"
            "c.commit(); c.close()"
        )
        subprocess.run([sys.executable, '-c', fresh_writer_script], check=True, timeout=30, env=_SUBPROCESS_ENV)
    finally:
        if long_lived_proc:
            (ctl / 'go2').touch()
            (ctl / 'close').touch()
            try:
                long_lived_proc[0].wait(timeout=5)
            except subprocess.TimeoutExpired:
                long_lived_proc[0].kill()
                long_lived_proc[0].wait(timeout=5)
    if long_lived_proc[0].returncode != 0:
        raise AssertionError(f'long-lived writer exited {long_lived_proc[0].returncode}')

    con = sqlite3.connect(store_path.as_uri() + '?mode=ro', uri=True)
    try:
        keys = {row[0] for row in con.execute('SELECT key FROM v11_meta')}
        integrity = con.execute('PRAGMA integrity_check').fetchone()[0]
    finally:
        con.close()
    if integrity != 'ok':
        raise AssertionError(f'SQLite integrity_check returned {integrity}')
    missing = {'lw-1', 'lw-2', 'fresh-3'} - keys
    if missing:
        raise AssertionError(f'missing={missing} keys={keys}')


# -- Regression test for F2 (identity anchor against a symlink swap) -------

def test_symlink_swapped_in_right_after_identity_check_is_refused(tmp_path, monkeypatch):
    # The window F2 covers is between `_check_source_identity` returning and
    # `_anchored_snapshot`'s own first stat of the path: swap the real store
    # out for a symlink to a *different*, otherwise-valid V11 store with the
    # same name, and confirm the CLI refuses rather than silently evaluating
    # the swapped-in store. `_check_source_identity` itself already rejects
    # a symlinked path; this proves the window after that check is closed by
    # the `lstat`-based before/after check in `_anchored_snapshot`, not just
    # by the identity check that ran before the swap.
    tmp_path.chmod(0o700)
    real_path = tmp_path / 'evidence.sqlite'
    EvidenceStore(real_path, 'V11_PAPER')
    other_dir = tmp_path / 'other'
    other_dir.mkdir(mode=0o700)
    other_path = other_dir / 'other.sqlite'
    EvidenceStore(other_path, 'V11_PAPER')
    other = sqlite3.connect(other_path)
    try:
        other.execute("INSERT INTO v11_meta VALUES('marker-other','x')")
        other.commit()
    finally:
        other.close()

    original_check = cli_module._check_source_identity

    def racing_check(path):
        original_check(path)
        moved = path.parent / 'moved-aside.sqlite'
        os.replace(path, moved)
        os.symlink(other_path, path)

    monkeypatch.setattr(cli_module, '_check_source_identity', racing_check)
    cfg_path = tmp_path / 'cfg.json'
    cfg_path.write_text(json.dumps(_bare_store_cfg(real_path)))
    rc = main([str(cfg_path)])
    if rc != 1:
        raise AssertionError(f'symlink swap was evaluated: rc={rc}')


@pytest.mark.parametrize('alias_kind', ('main-as-shm', 'foreign-as-shm', 'main-as-wal'))
def test_hard_linked_sidecar_refuses_before_victim_changes(tmp_path, capsys, alias_kind):
    tmp_path.chmod(0o700)
    store_path = tmp_path / 'evidence.sqlite'
    EvidenceStore(store_path, 'V11_PAPER')
    victim = store_path
    if alias_kind == 'foreign-as-shm':
        victim = tmp_path / 'foreign.sqlite'
        with sqlite3.connect(victim) as connection:
            connection.execute('CREATE TABLE preserve_me(x)')
        victim.chmod(0o600)
    suffix = '-wal' if alias_kind == 'main-as-wal' else '-shm'
    sidecar = Path(str(store_path) + suffix)
    os.link(victim, sidecar)
    before_main = (store_path.read_bytes(), store_path.stat().st_mtime_ns)
    before_victim = (victim.read_bytes(), victim.stat().st_mtime_ns)
    cfg_path = tmp_path / 'cfg.json'
    cfg_path.write_text(json.dumps(_bare_store_cfg(store_path)))

    capsys.readouterr()
    rc = main([str(cfg_path)])
    captured = capsys.readouterr()
    if rc != 1 or captured.out or 'UNSAFE_STORE_SIDECAR_REFUSED' not in captured.err:
        raise AssertionError(f'unsafe sidecar was not refused: rc={rc}, output={captured}')
    if (store_path.read_bytes(), store_path.stat().st_mtime_ns) != before_main:
        raise AssertionError('source main database changed before refusal')
    if (victim.read_bytes(), victim.stat().st_mtime_ns) != before_victim:
        raise AssertionError('hard-link victim changed before refusal')
    if not sidecar.exists() or sidecar.stat().st_ino != victim.stat().st_ino:
        raise AssertionError('CLI removed or replaced an unsafe sidecar')


def test_persistent_ancestor_symlink_swap_is_refused(tmp_path, monkeypatch, capsys):
    container = tmp_path / 'container'
    container.mkdir(mode=0o777)
    container.chmod(0o777)
    original_dir = container / 'original'
    replacement_dir = tmp_path / 'replacement'
    original_dir.mkdir(mode=0o700)
    replacement_dir.mkdir(mode=0o700)
    source = original_dir / 'evidence.sqlite'
    replacement = replacement_dir / source.name
    EvidenceStore(source, 'V11_PAPER')
    EvidenceStore(replacement, 'V11_PAPER')
    with sqlite3.connect(replacement) as connection:
        connection.execute("INSERT INTO v11_meta VALUES ('replacement-only', 'yes')")
    original_bytes = source.read_bytes()
    original_mtime = source.stat().st_mtime_ns
    seen = []
    real_evaluator = cli_module.evaluate_scenario_reservation_readiness

    def observing_evaluator(coordinator):
        with sqlite3.connect(coordinator.store.path) as connection:
            seen.extend(connection.execute("SELECT value FROM v11_meta WHERE key='replacement-only'").fetchall())
        return real_evaluator(coordinator)

    real_check = cli_module._check_source_identity

    def swap_after_check(path):
        real_check(path)
        original_dir.rename(container / 'saved-original')
        original_dir.symlink_to(replacement_dir, target_is_directory=True)

    monkeypatch.setattr(cli_module, '_check_source_identity', swap_after_check)
    monkeypatch.setattr(cli_module, 'evaluate_scenario_reservation_readiness', observing_evaluator)
    cfg_path = tmp_path / 'cfg.json'
    cfg_path.write_text(json.dumps(_bare_store_cfg(source)))
    capsys.readouterr()
    rc = main([str(cfg_path)])
    captured = capsys.readouterr()
    if rc != 1 or captured.out or 'STORE_PATH_REPLACED_DURING_CAPTURE' not in captured.err:
        raise AssertionError(f'ancestor substitution was not refused: rc={rc}, output={captured}')
    if seen:
        raise AssertionError(f'replacement store reached evaluator: {seen}')
    saved = container / 'saved-original' / source.name
    if not original_dir.is_symlink() or (saved.read_bytes(), saved.stat().st_mtime_ns) != (original_bytes, original_mtime):
        raise AssertionError('persistent swap or original source was changed')
