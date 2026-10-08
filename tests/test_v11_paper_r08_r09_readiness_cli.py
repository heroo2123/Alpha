"""Tests for the offline, read-only PAPER R08/R09 readiness CLI.

No test here creates a socket, imports an HTTP/provider client, runs a
subprocess against a provider, or lets the CLI create a brand-new
EvidenceStore file: every store path exercised here either already exists
(built by the reused R08/R09 test fixtures) before the CLI runs, or is
deliberately left absent to prove the CLI refuses rather than creating one.
"""
from dataclasses import asdict
import json
import subprocess
import sys

import pytest

from polymarket_scanner.v11.evidence import EvidenceError
from polymarket_scanner.v11.paper_coordinator import ACCOUNT_KEY, VERSION as COORDINATOR_VERSION
from test_v11_certification_rules import setup
from test_v11_model_artifacts import bundle
from test_v11_pws_admission import coordinator as pws_coordinator, joined, synthetic_proposal
from test_v11_r08_scenario_reservation_readiness import coordinator as r08_coordinator, genuine_proposal
from test_v11_strategy_pipeline import factory

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
