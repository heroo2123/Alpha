"""Offline, nonfinancial observer refusals on temporary synthetic PAPER archives."""
from dataclasses import asdict, replace
from copy import deepcopy
from decimal import Inexact, ROUND_DOWN, ROUND_UP, localcontext
import json
import math
import pathlib
import socket
import subprocess
import sys

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


def replay_cut(r, cut, *, at=None, configuration=None, collateral_asset='FIXTURE_COLLATERAL'):
    return observe(tuple(cut), tip_sha256=cut[-1]['sha256'] if isinstance(cut[-1], dict) else 'a'*64,
                   at=r['now'][0] if at is None else at, policy=configuration or policy(),
                   account_id='account', event_id=r['rule'].payload['event_id'],
                   rule_fingerprint=r['rule'].sha256, collateral_asset=collateral_asset)


def _row(cut, key):
    return next(row for row in cut if row['id'] == key)


def _rehash(cut):
    """Keep embedded evidence hashes coherent to exercise semantic refusals."""
    for _ in range(12):
        changes = {}
        for row in cut:
            if not isinstance(row, dict) or not isinstance(row.get('body'), dict):
                continue
            updated = digest(row['body'])
            if updated != row['sha256']:
                changes[row['sha256']] = updated
                row['sha256'] = updated
        if not changes:
            return

        def replace_hash(value):
            if isinstance(value, str):
                return changes.get(value, value)
            if isinstance(value, dict):
                return {key: replace_hash(item) for key, item in value.items()}
            if isinstance(value, list):
                return [replace_hash(item) for item in value]
            if isinstance(value, tuple):
                return tuple(replace_hash(item) for item in value)
            return value

        for row in cut:
            if isinstance(row, dict):
                row['body'] = replace_hash(row['body'])
    raise AssertionError('test reference hashes did not converge')


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


def test_r1_malformed_nested_shapes_and_numeric_overflow_are_typed(rig, monkeypatch):
    sequenced_fill(rig, monkeypatch)
    original = list(rows(rig['store']))
    mutations = (
        lambda c: c.__setitem__(-1, None),
        lambda c: _row(c, 'rules')['body'].update(details=None),
        lambda c: _row(c, 'rules')['body']['details'].update(preimage=None),
        lambda c: _row(c, 'rules')['body'].update(evidence=[None]),
        lambda c: _row(c, 'record-explicit')['body']['details'].update(state=None),
        lambda c: _row(c, 'explicit')['body'].update(payload=None),
        lambda c: _row(c, 'horizon-book')['body'].update(payload=None),
        lambda c: _row(c, 'explicit')['body']['payload']['execution_details'].update(fee_collateral='1e9999999'),
    )
    for index, mutate in enumerate(mutations):
        cut = deepcopy(original); mutate(cut)
        if index:
            _rehash(cut)
        result = replay_cut(rig, cut)
        check(result['execution_status'] == 'UNKNOWN', f'R1 case {index}: {result}')


def test_r2_replay_binds_collateral_and_json_container_shape(rig, monkeypatch):
    sequenced_fill(rig, monkeypatch)
    cut = list(rows(rig['store']))
    good = replay_cut(rig, cut)
    wrong = replay_cut(rig, cut, collateral_asset='WRONG_COLLATERAL')
    check(good['execution_status'] == 'OBSERVED_SYNTHETIC_DIAGNOSTIC', good['reason'])
    check(wrong['execution_status'] == 'UNKNOWN' and wrong['replay_sha256'] != good['replay_sha256'], 'collateral replay')
    check(wrong['collateral_asset'] == 'WRONG_COLLATERAL', 'collateral output')
    alias = deepcopy(cut)
    evidence = _row(alias, 'rules')['body']['evidence']
    _row(alias, 'rules')['body']['evidence'] = tuple(evidence)
    check(digest(_row(alias, 'rules')['body']) == _row(cut, 'rules')['sha256'], 'fixture alias')
    result = replay_cut(rig, alias)
    check(result['execution_status'] == 'UNKNOWN' and result['replay_sha256'] != good['replay_sha256'], 'container replay')


def test_r3_rule_source_receipt_class_order_and_quarantine_lineage(rig, monkeypatch):
    sequenced_fill(rig, monkeypatch)
    base = list(rows(rig['store']))

    def raw_after(c):
        raw = _row(c, 'rules:raw'); c.remove(raw); c.append(raw)
        for index, row in enumerate(c, 1): row['seq'] = index

    def quarantine(c):
        earlier = deepcopy(_row(c, 'rules'))
        earlier['id'] = earlier['body']['record_id'] = 'prior-quarantine'
        earlier['body']['details'].update(state='RULE_DRIFT', quarantined=True, changed=True)
        c.insert(c.index(_row(c, 'rules')), earlier)
        for index, row in enumerate(c, 1): row['seq'] = index

    mutations = (
        lambda c: _row(c, 'rules')['body']['details'].update(source_event_sha256='f'*64),
        lambda c: _row(c, 'rules')['body']['details'].update(source_receipt_seq=9999),
        lambda c: _row(c, 'rules:raw')['body'].update(evidence_class='HISTORICAL_AVAILABILITY_UNKNOWN'),
        lambda c: _row(c, 'rules:raw')['body']['payload'].update(discovery_page_id='absent', discovery_page_sha256='a'*64, page_index=0),
        raw_after, quarantine,
    )
    for index, mutate in enumerate(mutations):
        cut = deepcopy(base); mutate(cut); _rehash(cut)
        result = replay_cut(rig, cut)
        check(result['execution_status'] == 'UNKNOWN', f'R3 case {index}: {result}')


def test_r4_account_reconciliation_provenance_and_direction(rig, monkeypatch):
    sequenced_fill(rig, monkeypatch)
    base = list(rows(rig['store']))

    def state(c): return _row(c, 'record-explicit')['body']['details']['state']
    def before_proof(c):
        account = _row(c, 'record-explicit'); c.remove(account)
        c.insert(c.index(_row(c, 'explicit')), account)
        for index, row in enumerate(c, 1): row['seq'] = index

    mutations = (
        lambda c: _row(c, 'record-explicit')['body']['details'].update(version='WRONG', policy_sha256='f'*64),
        before_proof,
        lambda c: state(c)['intents']['basket:leg:0'].update(filled_units='0'),
        lambda c: state(c)['lots'].clear(),
        lambda c: state(c)['rules'][rig['rule'].payload['event_id']].update(canonical_json='{}'),
        lambda c: (_row(c, 'explicit')['body']['payload'].update(direction='INVALID', all_in_collateral='.16'),
                   state(c)['intents']['basket:leg:0'].update(direction='INVALID')),
    )
    for index, mutate in enumerate(mutations):
        cut = deepcopy(base); mutate(cut); _rehash(cut)
        result = replay_cut(rig, cut)
        check(result['execution_status'] == 'UNKNOWN', f'R4 case {index}: {result}')


def test_r5_all_declared_book_updates_count_for_continuity(rig, monkeypatch):
    sequenced_fill(rig, monkeypatch)
    base = list(rows(rig['store']))
    hidden = deepcopy(base)
    update = deepcopy(_row(hidden, 'horizon-book'))
    update['id'] = update['body']['record_id'] = 'contradictory-update'
    update['body']['payload']['token_id'] = 'wrong-token'
    hidden.insert(hidden.index(_row(hidden, 'horizon-book')), update)
    for index, row in enumerate(hidden, 1): row['seq'] = index
    _row(hidden, 'horizon-book')['body']['payload']['book_sequence'].update(sequence=3, previous_sequence=1)
    _rehash(hidden)
    check(replay_cut(rig, hidden)['execution_status'] == 'UNKNOWN', 'hidden wrong-token update')

    prefill = deepcopy(base)
    update = deepcopy(_row(prefill, 'horizon-book'))
    update['id'] = update['body']['record_id'] = 'prefill-update'
    proof_time = _row(prefill, 'explicit')['body']['received_at']
    update['body'].update(observed_at=proof_time, received_at=proof_time,
                          available_at=proof_time, recorded_at=proof_time)
    update['body']['payload']['book_sequence'].update(sequence=2, previous_sequence=1)
    prefill.insert(prefill.index(_row(prefill, 'explicit')), update)
    for index, row in enumerate(prefill, 1): row['seq'] = index
    _row(prefill, 'record-explicit')['body']['details']['state']['lots']['explicit']['acquired_sequence'] = _row(prefill, 'explicit')['seq']
    _row(prefill, 'horizon-book')['body']['payload']['book_sequence'].update(sequence=3, previous_sequence=1)
    _rehash(prefill)
    check(replay_cut(rig, prefill)['execution_status'] == 'UNKNOWN', 'unaccounted prefill update')


def test_r5_cross_event_id_update_on_same_stream_is_refused_not_skipped(rig, monkeypatch):
    """PRO2-R5a: a same-provider/source_identity update cannot be hidden from the
    continuity scan merely because its envelope event_id contradicts the token's
    event. Previously, selecting books by event_id BEFORE scope/sequence
    continuity let this case skip the update and complete with a markout."""
    sequenced_fill(rig, monkeypatch)
    base = list(rows(rig['store']))
    crossed = deepcopy(base)
    update = deepcopy(_row(crossed, 'horizon-book'))
    update['id'] = update['body']['record_id'] = 'cross-event-update'
    update['event_id'] = update['body']['event_id'] = 'some-other-event'
    update['body']['payload']['book_sequence'].update(sequence=2, previous_sequence=1)
    crossed.insert(crossed.index(_row(crossed, 'horizon-book')), update)
    for index, row in enumerate(crossed, 1): row['seq'] = index
    _row(crossed, 'horizon-book')['body']['payload']['book_sequence'].update(sequence=3, previous_sequence=1)
    _rehash(crossed)
    result = replay_cut(rig, crossed)
    check(result['execution_status'] == 'UNKNOWN', f'cross-event update must not be silently skipped: {result}')
    check(result['reason'] == 'OBSERVATION_BOOK_SCOPE_OR_HEALTH', f'PRO2-R5a reason: {result}')


def test_r6_pre_serialization_byte_and_node_bounds(rig, monkeypatch):
    sequenced_fill(rig, monkeypatch)
    cut = list(rows(rig['store']))
    large = deepcopy(cut)
    _row(large, 'horizon-book')['body']['payload']['unused_padding'] = 'x' * (8 * 1024 * 1024 + 1)
    # The observer must reject before hashing the oversized mutation.
    check(replay_cut(rig, large)['reason'] == 'OBSERVATION_RECORD_BOUND', 'record byte bound')
    broad = deepcopy(cut)
    _row(broad, 'horizon-book')['body']['payload']['unused_nodes'] = [None] * 20_001
    check(replay_cut(rig, broad)['reason'] == 'OBSERVATION_RECORD_BOUND', 'record node bound')


def test_r7_decimal_context_is_fixed_for_validation_and_markout(rig, monkeypatch):
    sequenced_fill(rig, monkeypatch)
    cut = list(rows(rig['store']))
    precise = deepcopy(cut)
    _row(precise, 'horizon-book')['body']['payload']['bids'][0]['price'] = '.100000000000000000000000000001'
    _rehash(precise)
    with localcontext() as ctx:
        ctx.traps[Inexact] = True
        trapped = replay_cut(rig, precise)
    check(trapped['execution_status'] == 'OBSERVED_SYNTHETIC_DIAGNOSTIC', trapped['reason'])
    fractional = deepcopy(cut)
    payload = _row(fractional, 'explicit')['body']['payload']
    payload.update(units='1.5', all_in_collateral='.29')
    state = _row(fractional, 'record-explicit')['body']['details']['state']
    state['intents']['basket:leg:0']['filled_units'] = '1.5'
    state['lots']['explicit'].update(units='1.5', all_in_cost_basis='.29')
    _rehash(fractional)
    results = []
    for rounding in (ROUND_DOWN, ROUND_UP):
        with localcontext() as ctx:
            ctx.rounding = rounding
            results.append(replay_cut(rig, fractional))
    check(results[0] == results[1] and results[0]['execution_status'] == 'OBSERVED_SYNTHETIC_DIAGNOSTIC', 'ambient decimal context')


_ROOT = pathlib.Path(__file__).resolve().parents[1]

# A fresh child interpreter is required because NUMERIC_CONTEXT is fixed once,
# at module import time; a mutation of decimal.DefaultContext in this already-
# running test process cannot reproduce an import-order difference (PRO2-R7a).
_IMPORT_ORDER_PROBE = """
import decimal, json, sys
if sys.argv[1] == 'mutated':
    decimal.DefaultContext.traps[decimal.Inexact] = True
    decimal.DefaultContext.capitals = 0
from polymarket_scanner.v11 import paper_risk_observation as mod
payload = json.loads(sys.stdin.read())
policy_obj = mod.ObservationPolicy(**payload['policy'])
result = mod.observe(tuple(payload['rows']), tip_sha256=payload['rows'][-1]['sha256'],
                      at=payload['at'], policy=policy_obj, account_id=payload['account_id'],
                      event_id=payload['event_id'], rule_fingerprint=payload['rule_fingerprint'],
                      collateral_asset=payload['collateral_asset'])
context = mod.NUMERIC_CONTEXT
print(json.dumps({'result': result, 'context_traps_inexact': context.traps[decimal.Inexact],
                   'context_capitals': context.capitals, 'context_clamp': context.clamp}))
"""


def _observe_in_fresh_interpreter(payload, mode):
    proc = subprocess.run([sys.executable, '-c', _IMPORT_ORDER_PROBE, mode],
                          input=json.dumps(payload), capture_output=True, text=True,
                          cwd=str(_ROOT), timeout=30)
    check(proc.returncode == 0, f'subprocess ({mode}) failed: {proc.stderr}')
    return json.loads(proc.stdout.strip().splitlines()[-1])


def test_r7_numeric_context_is_explicit_and_import_order_independent(rig, monkeypatch):
    """PRO2-R7a: NUMERIC_CONTEXT must pin traps/flags/clamp/capitals explicitly,
    not inherit them from decimal.DefaultContext at import time. Otherwise the
    same replay input can flip between a completed result and UNKNOWN depending
    on whatever mutated the ambient decimal context before this module import."""
    sequenced_fill(rig, monkeypatch)
    cut = list(rows(rig['store']))
    fractional = deepcopy(cut)
    payload = _row(fractional, 'explicit')['body']['payload']
    payload.update(units='1.5', all_in_collateral='.29')
    state = _row(fractional, 'record-explicit')['body']['details']['state']
    state['intents']['basket:leg:0']['filled_units'] = '1.5'
    state['lots']['explicit'].update(units='1.5', all_in_cost_basis='.29')
    _rehash(fractional)
    request = dict(rows=fractional, at=rig['now'][0], policy=asdict(policy()),
                   account_id='account', event_id=rig['rule'].payload['event_id'],
                   rule_fingerprint=rig['rule'].sha256, collateral_asset='FIXTURE_COLLATERAL')
    default_run = _observe_in_fresh_interpreter(request, 'default')
    mutated_run = _observe_in_fresh_interpreter(request, 'mutated')
    check(default_run['context_traps_inexact'] is False and default_run['context_capitals'] == 1
          and default_run['context_clamp'] == 0, f'context must be explicit by default: {default_run}')
    check(mutated_run['context_traps_inexact'] is False,
          f'NUMERIC_CONTEXT must not inherit an ambient Inexact trap at import: {mutated_run}')
    check(mutated_run['context_capitals'] == 1,
          f'NUMERIC_CONTEXT must not inherit ambient capitals at import: {mutated_run}')
    check(default_run['result']['execution_status'] == 'OBSERVED_SYNTHETIC_DIAGNOSTIC', default_run['result'])
    check(default_run['result'] == mutated_run['result'],
          f'import-order must not change the outcome for the same replay input: {default_run} vs {mutated_run}')


def test_r8_positive_horizon_and_deadline_stay_representable(rig, monkeypatch):
    sequenced_fill(rig, monkeypatch)
    cut = list(rows(rig['store']))
    execution = _row(cut, 'explicit')['body']['payload']['execution_details']['executed_at']
    tiny = policy(horizon_seconds=1e-20)
    result = replay_cut(rig, cut, at=execution, configuration=tiny)
    check(result['execution_status'] == 'UNKNOWN', 'sub-ULP horizon')
    huge = policy(maximum_measurement_age_seconds=1e308)
    result = replay_cut(rig, cut, at=1e308, configuration=huge)
    check(result['execution_status'] == 'UNKNOWN' and result['valid_until'] is None, 'infinite deadline')
