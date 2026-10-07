"""Offline, nonfinancial observer refusals on temporary synthetic PAPER archives."""
from dataclasses import asdict, replace
from copy import deepcopy
import socket

from polymarket_scanner.v11.evidence import digest
from polymarket_scanner.v11.paper_risk_observation import ObservationPolicy, observe
from polymarket_scanner.v11.microstructure import STREAM_VERSION
from test_v11_basket_coordinator import rig, reserve, proof
from test_v11_certification_rules import setup
from test_v11_fill_evidence import detailed_fill
import test_v11_fill_evidence as fill_fixture
from test_v11_model_artifacts import bundle
from test_v11_strategy_pipeline import factory


def check(condition, message):
    if not condition:
        raise AssertionError(message)


def policy(**changes):
    candidate = replace(ObservationPolicy('fixture-only-policy', 'a'*64,
                        20., 2., 2., 1., 10., 4096, 'basket-fixture', 'TOKEN_ID'), **changes)
    parameters = asdict(candidate); parameters.pop('policy_sha256')
    return replace(candidate, policy_sha256=digest(parameters))


def rows(store):
    result = []; after = 0
    while True:
        page = store.page_through(through_seq=store.pin_read_view()['through_seq'], after_seq=after, limit=64)
        if not page:
            return tuple(result)
        result.extend(page); after = page[-1]['seq']


def sample(r, *, configuration=None, archive=None, **scope):
    cut = archive if archive is not None else rows(r['store'])
    return observe(cut, tip_sha256=cut[-1]['sha256'], at=r['now'][0],
                   policy=configuration or policy(), account_id=scope.get('account_id', 'account'),
                   event_id=scope.get('event_id', r['rule'].payload['event_id']),
                   rule_fingerprint=scope.get('rule_fingerprint', r['rule'].sha256),
                   collateral_asset='FIXTURE_COLLATERAL')


def test_no_fill_is_unknown_for_engine_and_deterministic(rig, monkeypatch):
    reserve(rig)
    before = rig['store'].pin_read_view()
    monkeypatch.setattr(socket, 'socket', lambda *a, **kw: (_ for _ in ()).throw(AssertionError('socket used')))
    one = sample(rig); two = sample(rig)
    check(one == two, 'replay changed')
    check(one['execution_status'] == 'NO_RECONCILED_RECENT_PAPER_FILLS', one['reason'])
    check(one['diagnostic_adverse_fill_count'] == 0 and one['diagnostic_markout_collateral_per_share'] is None, 'empty population')
    check(one['event_metrics_adverse_fills'] is None and one['event_metrics_recent_markout_per_share'] is None, 'engine promotion')
    check(one['settlement_finality_status'] == 'UNKNOWN' and one['new_risk_cutoff_at'] is None, 'cutoff promotion')
    check(not one['financial_authority'] and not one['admission_eligible'], 'authority')
    check(rig['store'].pin_read_view() == before, 'observer changed archive')


def test_missing_policy_and_wrong_scope_are_unknown(rig):
    reserve(rig)
    for name in ('lookback_seconds', 'horizon_seconds', 'maximum_horizon_delay_seconds',
                 'maximum_book_receipt_delay_seconds', 'maximum_measurement_age_seconds'):
        outcome = sample(rig, configuration=policy(**{name: None}))
        check(outcome['reason'] == 'OBSERVATION_POLICY_INCOMPLETE', name)
    check(sample(rig, configuration=replace(policy(), policy_sha256='b'*64))['reason'] ==
          'OBSERVATION_POLICY_HASH_MISMATCH', 'policy hash')
    check(sample(rig, configuration=policy(complete_history_scan_bound=1))['reason'] ==
          'OBSERVATION_FRONTIER_BOUND', 'history bound')
    check(sample(rig, account_id='other')['execution_status'] == 'UNKNOWN', 'wrong account')
    check(sample(rig, event_id='other')['execution_status'] == 'UNKNOWN', 'wrong event')
    check(sample(rig, rule_fingerprint='b'*64)['execution_status'] == 'UNKNOWN', 'wrong rule')


def test_legacy_partial_and_malformed_fill_remain_unknown(rig):
    reserve(rig)
    detailed_fill(rig, mutate=lambda d: d.update(price_per_share='.7'))
    result = sample(rig)
    check(result['execution_status'] == 'UNKNOWN', result['reason'])
    check(result['diagnostic_markout_collateral_per_share'] is None, 'malformed fill produced markout')


def test_missing_post_validation_book_reference_remains_unknown(rig):
    reserve(rig)
    detailed_fill(rig, mutate=lambda d: d.update(post_validation_book_ref={}))
    check(sample(rig)['execution_status'] == 'UNKNOWN', 'missing post-validation book')


def test_missing_horizon_book_and_partial_fill_remain_unknown(rig):
    reserve(rig)
    detailed_fill(rig, units='1')
    rig['now'][0] += 3
    result = sample(rig)
    check(result['reason'] in {'OBSERVATION_HORIZON_BOOK_MISSING', 'OBSERVATION_BOOK_SEQUENCE_UNKNOWN'}, result['reason'])
    check(result['event_metrics_adverse_fills'] is None, 'partial fill promoted')


def test_immature_fill_remains_unknown(rig):
    reserve(rig)
    detailed_fill(rig)
    result = sample(rig)
    check(result['reason'] == 'PENDING_MARKOUT', result['reason'])


def test_duplicate_proof_and_frontier_gap_are_unknown(rig):
    reserve(rig)
    detailed_fill(rig)
    proof(rig, 'basket:leg:0', 'duplicate', 'PAPER_FILL', fill_id='explicit', units='1',
          all_in_collateral='.2', direction='BUY')
    check(sample(rig)['reason'] == 'OBSERVATION_DUPLICATE_ECONOMIC_FILL', 'duplicate proof')
    cut = rows(rig['store'])
    gap = tuple(r for r in cut if r['seq'] != 2)
    check(sample(rig, archive=gap)['reason'] == 'OBSERVATION_FRONTIER_INCOMPLETE', 'gap')


def test_frontier_tamper_and_replay_identity(rig):
    reserve(rig)
    cut = rows(rig['store'])
    one = sample(rig, archive=cut)
    changed = sample(rig, configuration=policy(horizon_seconds=3.), archive=cut)
    check(one['replay_sha256'] != changed['replay_sha256'], 'policy replay binding')
    bad = list(cut); bad[-1] = dict(bad[-1], sha256='b'*64)
    check(sample(rig, archive=tuple(bad))['execution_status'] == 'UNKNOWN', 'tampered tip')
    changed_rows = list(deepcopy(cut))
    changed_rows[0]['body']['revision'] = 'synthetic-replay-change'
    changed_rows[0]['sha256'] = digest(changed_rows[0]['body'])
    changed_cut = tuple(changed_rows)
    check(sample(rig, archive=changed_cut)['replay_sha256'] != one['replay_sha256'],
          'full frontier replay binding')


def sequenced_fill(r, monkeypatch, *, late=0., wrong_token=False):
    def post_with_sequence(rig, key, original, **changes):
        body = original['body']
        payload = dict(body['payload'], **changes,
                       book_sequence=dict(version=STREAM_VERSION, epoch='fixture-epoch', sequence=1, previous_sequence=0))
        return rig['store'].capture(key, event_id=original['event_id'], kind='BOOK',
               provider=body['provider'], source_identity=body['source_identity'], revision=key,
               observed_at=rig['now'][0], evidence_class=body['evidence_class'], payload=payload)
    monkeypatch.setattr(fill_fixture, 'book', post_with_sequence)
    reserve(r)
    detailed_fill(r)
    proof_row = r['store'].get('explicit')
    executed = proof_row['body']['payload']['execution_details']['executed_at']
    r['now'][0] = executed + 2. + late
    intent = r['store'].latest(kind='COORDINATOR_EVENT', event_id='v11-paper-account-state')['body']['details']['state']['intents']['basket:leg:0']
    target = dict(intent['target'])
    if wrong_token:
        target['token_id'] = 'wrong-token'
    r['store'].capture('horizon-book', event_id=r['rule'].payload['event_id'], kind='BOOK',
        provider='basket-fixture', source_identity=intent['token_id'], revision='horizon',
        observed_at=executed+2., evidence_class='PUBLIC_OBSERVED',
        payload=dict(target, rule_fingerprint=r['rule'].sha256, collateral_asset='FIXTURE_COLLATERAL',
                     stream_healthy=True, bids=[dict(price='.1', size='20')],
                     asks=[dict(price='.2', size='20')],
                     book_sequence=dict(version=STREAM_VERSION, epoch='fixture-epoch', sequence=2, previous_sequence=1)))


def test_complete_synthetic_markout_is_diagnostic_only(rig, monkeypatch):
    sequenced_fill(rig, monkeypatch)
    result = sample(rig)
    check(result['execution_status'] == 'OBSERVED_SYNTHETIC_DIAGNOSTIC', result['reason'])
    check(result['fill_count'] == 1 and result['diagnostic_adverse_fill_count'] == 1, 'fill count')
    check(result['diagnostic_markout_collateral_per_share'] == '-0.10', result['diagnostic_markout_collateral_per_share'])
    check(result['event_metrics_adverse_fills'] is None and result['event_metrics_recent_markout_per_share'] is None, 'engine promotion')
    check(not result['admission_eligible'] and not result['financial_authority'], 'authority')


def test_late_receipt_and_wrong_token_remain_unknown(rig, monkeypatch):
    sequenced_fill(rig, monkeypatch, late=1.1)
    check(sample(rig)['reason'] == 'OBSERVATION_BOOK_LATE_RECEIPT', 'late receipt')


def test_wrong_horizon_token_remains_unknown(rig, monkeypatch):
    sequenced_fill(rig, monkeypatch, wrong_token=True)
    check(sample(rig)['execution_status'] == 'UNKNOWN', 'wrong token')
