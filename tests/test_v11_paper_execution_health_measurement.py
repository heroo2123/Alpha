"""Adversarial coverage for the execution-health MEASUREMENT writer/discovery lane.

Every genuine-archive scenario below reuses the same `basket_rig`/`sequenced_fill`/
`reserve`/`observation_policy` fixture machinery `test_v11_paper_risk_observation.py`
and `test_v11_paper_execution_health_promotion.py` already use, for the same reason
those files state: proving a writer genuinely persists and a promoter genuinely
re-verifies requires a real archive, not a hand-built fixture claiming to be one.
"""
import pytest

from polymarket_scanner.v11.evidence import EvidenceError, EvidenceStore
from polymarket_scanner.v11.paper_execution_health_measurement import (
    current_execution_health_record_id, observe_and_promote_execution_health,
    record_execution_health_observation,
)
from polymarket_scanner.v11.paper_execution_health_promotion import (
    ExecutionHealthPromotion, promote_execution_health,
)

from test_v11_basket_coordinator import rig as basket_rig, reserve
from test_v11_certification_rules import setup
from test_v11_model_artifacts import bundle
from test_v11_strategy_pipeline import factory
from test_v11_fill_evidence import detailed_fill
import test_v11_fill_evidence as fill_fixture
from test_v11_paper_risk_observation import policy as observation_policy, sequenced_fill


ACCOUNT_ID = 'account'
COLLATERAL_ASSET = 'FIXTURE_COLLATERAL'


def scope(r, *, policy=None):
    p = policy or observation_policy()
    return dict(account_id=ACCOUNT_ID, event_id=r['rule'].payload['event_id'],
               rule_fingerprint=r['rule'].sha256, collateral_asset=COLLATERAL_ASSET, policy=p)


def promote(r, record_id, *, policy=None):
    return promote_execution_health(r['store'], record_id, account_id=ACCOUNT_ID,
                                    event_id=r['rule'].payload['event_id'], rule_fingerprint=r['rule'].sha256,
                                    collateral_asset=COLLATERAL_ASSET, policy=policy or observation_policy())


def current_id(r, *, policy=None):
    p = policy or observation_policy()
    return current_execution_health_record_id(r['store'], account_id=ACCOUNT_ID,
                                               event_id=r['rule'].payload['event_id'],
                                               rule_fingerprint=r['rule'].sha256, collateral_asset=COLLATERAL_ASSET,
                                               policy_sha256=p.policy_sha256)


def test_writer_persists_genuine_observation_and_promotion_sees_real_numbers(basket_rig, monkeypatch):
    sequenced_fill(basket_rig, monkeypatch)
    store = basket_rig['store']
    row = record_execution_health_observation(store, **scope(basket_rig))
    assert row is not None and row['kind'] == 'MEASUREMENT'
    assert row['event_id'] == basket_rig['rule'].payload['event_id']
    details = row['body']['details']
    assert details['execution_status'] == 'OBSERVED_SYNTHETIC_DIAGNOSTIC', details['reason']
    assert details['fill_count'] == 1 and details['diagnostic_adverse_fill_count'] == 1
    assert details['diagnostic_markout_collateral_per_share'] == '-0.10'
    promoted = promote(basket_rig, row['id'])
    assert promoted == ExecutionHealthPromotion(1, -0.10, 'PROMOTED', None, tuple(details['evidence_ids']))
    assert current_id(basket_rig) == row['id']


def test_observe_and_promote_single_call_matches_two_step_path(basket_rig, monkeypatch):
    sequenced_fill(basket_rig, monkeypatch)
    store = basket_rig['store']
    combined = observe_and_promote_execution_health(store, **scope(basket_rig))
    assert combined == ExecutionHealthPromotion(1, -0.10, 'PROMOTED', None, combined.evidence_ids)
    again = promote(basket_rig, current_id(basket_rig))
    assert combined == again


def test_rerun_on_unchanged_tip_is_idempotent_not_conflicting(basket_rig, monkeypatch):
    sequenced_fill(basket_rig, monkeypatch)
    store = basket_rig['store']
    first = record_execution_health_observation(store, **scope(basket_rig))
    before = store.pin_read_view()
    basket_rig['now'][0] += 500.  # Wall-clock drift alone must not change the written body.
    second = record_execution_health_observation(store, **scope(basket_rig))
    assert first['id'] == second['id'] and first['sha256'] == second['sha256']
    assert store.pin_read_view() == before, 'idempotent rerun must not append a new row'


def test_new_archive_activity_produces_a_fresh_row_and_promotion(basket_rig, monkeypatch):
    sequenced_fill(basket_rig, monkeypatch)
    store = basket_rig['store']
    first = record_execution_health_observation(store, **scope(basket_rig))
    # A different leg's token than the one `sequenced_fill` actually traded,
    # so this extra capture advances the real archive tip without entering
    # the fill's own horizon-book continuity scan (which would otherwise
    # demand a `book_sequence` this inert touch does not declare).
    store.capture('extra-book-touch', event_id=basket_rig['rule'].payload['event_id'], kind='BOOK',
                 provider='basket-fixture', source_identity=basket_rig['rule'].payload['partition'][1]['yes_token'],
                 revision='2', observed_at=basket_rig['now'][0], evidence_class='PUBLIC_OBSERVED',
                 payload={'stream_healthy': True, 'bids': [{'price': '.1', 'size': '1'}],
                          'asks': [{'price': '.9', 'size': '1'}]})
    second = record_execution_health_observation(store, **scope(basket_rig))
    assert second is not None and second['id'] != first['id']
    promoted = promote(basket_rig, second['id'])
    assert promoted.status == 'PROMOTED'


def test_empty_recent_fills_is_written_honestly_and_promotion_is_unknown(basket_rig):
    reserve(basket_rig)
    store = basket_rig['store']
    row = record_execution_health_observation(store, **scope(basket_rig))
    assert row is not None
    details = row['body']['details']
    assert details['execution_status'] == 'NO_RECONCILED_RECENT_PAPER_FILLS', details
    promoted = promote(basket_rig, row['id'])
    assert promoted.status == 'UNKNOWN' and promoted.adverse_fills is None
    assert promoted.reason == 'EXECUTION_HEALTH_NO_RECONCILED_RECENT_PAPER_FILLS'


def test_completely_empty_store_never_writes(tmp_path):
    tmp_path.chmod(0o700)
    store = EvidenceStore(tmp_path / 'evidence.sqlite', 'V11_PAPER', clock=lambda: 1000.)
    args = dict(account_id='account', event_id='event', rule_fingerprint='a' * 64,
               collateral_asset='USDC', policy=observation_policy())
    assert current_execution_health_record_id(store, account_id='account', event_id='event',
                                               rule_fingerprint='a' * 64, collateral_asset='USDC',
                                               policy_sha256=observation_policy().policy_sha256) is None
    row = record_execution_health_observation(store, **args)
    assert row is None
    combined = observe_and_promote_execution_health(store, **args)
    assert combined.status == 'UNKNOWN' and combined.reason == 'EXECUTION_HEALTH_MEASUREMENT_SCAN_INCOMPLETE'
    assert combined.adverse_fills is None and combined.recent_markout_per_share is None


def test_scan_bound_truncation_never_writes_a_partial_prefix(basket_rig, monkeypatch):
    sequenced_fill(basket_rig, monkeypatch)
    store = basket_rig['store']
    through_seq = store.pin_read_view()['through_seq']
    assert through_seq > 1, 'fixture must produce more than one row for this bound to matter'
    tiny = observation_policy(complete_history_scan_bound=1)
    row = record_execution_health_observation(store, account_id=ACCOUNT_ID,
                                               event_id=basket_rig['rule'].payload['event_id'],
                                               rule_fingerprint=basket_rig['rule'].sha256,
                                               collateral_asset=COLLATERAL_ASSET, policy=tiny)
    assert row is None, 'a scan bound too small to reach the real tip must never produce a write'


def test_inserted_row_conflict_is_refused_and_legitimate_write_stays_available(basket_rig, monkeypatch):
    """Once this writer's deterministic id has genuinely been used, nothing --
    including a later attempt under the same id with different content -- can
    silently replace it; `EvidenceStore`'s own append-only conflict check
    refuses. A legitimate rerun (identical content, since the tip has not
    moved) remains unaffected by the refused forgery.
    """
    sequenced_fill(basket_rig, monkeypatch)
    store = basket_rig['store']
    first = record_execution_health_observation(store, **scope(basket_rig))
    with pytest.raises(EvidenceError) as exc:
        store.audit(first['id'], event_id=first['event_id'], kind='MEASUREMENT',
                   details=dict(first['body']['details'], diagnostic_adverse_fill_count=999), evidence_ids=())
    assert str(exc.value) == 'RECORD_ID_CONFLICT'
    again = record_execution_health_observation(store, **scope(basket_rig))
    assert again['id'] == first['id'] and again['sha256'] == first['sha256']


def test_tainted_account_event_or_rule_scope_never_promotes_real_numbers(basket_rig, monkeypatch):
    sequenced_fill(basket_rig, monkeypatch)
    store = basket_rig['store']
    event_id = basket_rig['rule'].payload['event_id']
    for tainted in (dict(account_id='someone-else'), dict(event_id='someone-elses-event'),
                    dict(rule_fingerprint='b' * 64)):
        args = dict(account_id=ACCOUNT_ID, event_id=event_id, rule_fingerprint=basket_rig['rule'].sha256,
                   collateral_asset=COLLATERAL_ASSET, policy=observation_policy())
        args.update(tainted)
        promoted = observe_and_promote_execution_health(store, **args)
        assert promoted.status == 'UNKNOWN', f'{tainted}: {promoted}'
        assert promoted.adverse_fills is None and promoted.recent_markout_per_share is None


def test_no_provider_authority_never_promotes_real_numbers(basket_rig, monkeypatch):
    sequenced_fill(basket_rig, monkeypatch)
    store = basket_rig['store']
    unauthorized = observation_policy(book_provider='unauthorized-provider-never-seen')
    promoted = observe_and_promote_execution_health(store, **scope(basket_rig, policy=unauthorized))
    assert promoted.status == 'UNKNOWN'
    assert promoted.adverse_fills is None and promoted.recent_markout_per_share is None


def test_stale_measurement_goes_unknown_after_real_elapsed_time(basket_rig, monkeypatch):
    sequenced_fill(basket_rig, monkeypatch)
    store = basket_rig['store']
    row = record_execution_health_observation(store, **scope(basket_rig))
    fresh = promote(basket_rig, row['id'])
    assert fresh.status == 'PROMOTED'
    basket_rig['now'][0] += 100000.  # Comfortably past both the policy and module age ceilings.
    stale = promote(basket_rig, row['id'])
    assert stale.status == 'UNKNOWN' and stale.reason == 'EXECUTION_HEALTH_OBSERVATION_STALE'


def test_no_policy_or_wrong_policy_type_fails_closed(basket_rig, monkeypatch):
    sequenced_fill(basket_rig, monkeypatch)
    store = basket_rig['store']
    base = scope(basket_rig)
    for bad_policy in (None, 'not-a-policy', {}):
        args = dict(base); args['policy'] = bad_policy
        with pytest.raises(EvidenceError) as exc:
            record_execution_health_observation(store, **args)
        assert str(exc.value) == 'EXECUTION_HEALTH_MEASUREMENT_POLICY_REQUIRED'


def test_record_id_is_dedicated_per_policy_fingerprint(basket_rig, monkeypatch):
    sequenced_fill(basket_rig, monkeypatch)
    store = basket_rig['store']
    policy_a = observation_policy()
    policy_b = observation_policy(horizon_seconds=3.)
    assert policy_a.policy_sha256 != policy_b.policy_sha256
    row_a = record_execution_health_observation(store, **scope(basket_rig, policy=policy_a))
    row_b = record_execution_health_observation(store, **scope(basket_rig, policy=policy_b))
    assert row_a['id'] != row_b['id']
    assert current_id(basket_rig, policy=policy_a) == row_a['id']
    assert current_id(basket_rig, policy=policy_b) == row_b['id']


def test_discovery_read_never_appends(basket_rig, monkeypatch):
    sequenced_fill(basket_rig, monkeypatch)
    store = basket_rig['store']
    before = store.pin_read_view()
    current_id(basket_rig)
    assert store.pin_read_view() == before, 'a pure discovery read must never append'
