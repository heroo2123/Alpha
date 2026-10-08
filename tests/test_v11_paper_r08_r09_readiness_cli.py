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


def _sidecar_listing(path):
    """The store's own file plus any -wal/-shm/-journal sidecars next to it,
    ignoring unrelated files the shared fixture directory may also contain."""
    return sorted(p.name for p in path.parent.glob(path.name + '*'))


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


def test_wal_store_without_sidecars_gains_none_on_refusal(factory, tmp_path):
    # Mirrors the review's P13: a plain WAL-mode store (no existing -wal/-shm)
    # refused for a namespace mismatch must not leave new sidecar files
    # behind even though inspecting it necessarily opens it briefly.
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
    assert _sidecar_listing(c.store.path) == before_listing


def test_wal_store_without_sidecars_gains_none_on_success(factory, tmp_path):
    rig = factory()
    c = r08_coordinator(rig)
    assert not Path(str(c.store.path) + '-wal').exists()
    before_bytes = c.store.path.read_bytes()
    before_listing = _sidecar_listing(c.store.path)
    cfg_path = tmp_path / 'cfg.json'
    cfg_path.write_text(json.dumps(_config(c)))
    assert main([str(cfg_path)]) == 0
    assert c.store.path.read_bytes() == before_bytes
    assert _sidecar_listing(c.store.path) == before_listing


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
