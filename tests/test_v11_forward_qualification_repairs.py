"""Full synthetic ledger path for reviewed nonfinancial qualification refusals."""
from dataclasses import asdict, replace

import pytest

from polymarket_scanner.v11 import learning_capture as lc, shadow_commission as sc
from polymarket_scanner.v11.evidence import EvidenceError, canonical, digest
from polymarket_scanner.v11.forward_qualification import grouped_outcome
from polymarket_scanner.v11.model_registry import ActiveModelRegistry
from polymarket_scanner.v11.probability import BucketPrediction
from polymarket_scanner.v11.strategy_admission import SourceLease, StrategyAdmission
from polymarket_scanner.v11.strategy_pipeline import TemperatureStrategies
from test_v11_shadow_commission import bundle, rig, setup
from test_v11_strategy_pipeline import factory


def prepared(r):
    store, now = r['store'], r['now']
    now[0] += .01
    original = store.get('model2')['body']
    source = store.capture('public-model', event_id=r['context'].event_id, kind='MODEL',
        provider='LOCAL_TEST_FIXTURE', source_identity='model-1', revision='probe',
        payload=original['payload'], issued_at=now[0]-30)
    now[0] += .01
    pin = StrategyAdmission(store).pin('public-pin', **dict(r['admission_kw'],
        source_leases=(SourceLease(source['id'], 'MODEL', 120.),)))
    request = replace(r['request'], admission_id=pin['id'], model_input_ids=(source['id'],))
    details = TemperatureStrategies(store).evaluate('repair-evaluation', request)['body']['details']
    prediction = details['prediction']
    r['capture_kw'] = dict(context=r['context'], rule=r['rule'], binding=r['binding'],
        prediction=BucketPrediction(canonical(prediction), digest(prediction)),
        pinned_bundle=ActiveModelRegistry().pin(scope_key=r['scope'].key, mode='V11_SHADOW').bundle,
        model_input_ids=(source['id'],), expires_at=request.expires_at, admission_id=pin['id'])
    return pin


def capture(r, key, *, admission_id=None):
    r['now'][0] += .01
    kw = dict(r['capture_kw'])
    if admission_id is not None:
        kw['admission_id'] = admission_id
    return lc.capture_forecast_vector(r['store'], key, **kw)


def payouts(r, *, only=None, advance=True):
    if advance:
        r['now'][0] += .01
    sources = {}
    for i, bucket in enumerate(r['rule'].payload['partition']):
        mid = bucket['market_id']
        if only is not None and mid not in only:
            continue
        value = (1, 0, 0)[i]
        response = dict(id=mid, conditionId=bucket['condition_id'], closed=True,
            clobTokenIds=[bucket['yes_token'], bucket['no_token']],
            outcomePrices=[value, 1-value])
        sources[mid] = r['store'].capture('raw:' + mid, event_id=r['context'].event_id,
            kind='RULES', provider='GAMMA_CLOSED_MARKET', source_identity='market:' + mid,
            revision='gamma-closed:' + digest(response), payload=dict(response=response,
                capture_role='EXACT_TOKEN_PAYOUT_LABEL_SOURCE', http_status=200,
                endpoint='https://gamma-api.polymarket.com/markets/' + mid))
    return sources


def labels(r, sources):
    r['now'][0] += .01
    result = {}
    rule = r['rule'].payload
    for i, bucket in enumerate(rule['partition']):
        mid = bucket['market_id']
        value = (1, 0, 0)[i]
        source = sources[mid]
        target = dict(market_id=mid, condition_id=bucket['condition_id'],
            token_id=bucket['yes_token'], side='YES')
        version = 'gamma-payout:' + digest([source['sha256'], target['token_id'], float(value)])
        row = r['store'].capture('label:' + mid, event_id=r['context'].event_id, kind='LABEL',
            provider='GAMMA_CLOSED_MARKET_EXACT_TOKEN_PAYOUT',
            source_identity=target['token_id'], revision=version,
            payload=dict(source_capture_id=source['id'], source_capture_sha256=source['sha256'],
                target_identity=target, decision_target=lc.TARGET,
                context=dict(station=rule['station'], city=r['context'].city_id,
                    local_date=rule['target_date'], target=lc.TARGET,
                    rule_fingerprint=r['rule'].sha256), evidence_type='EXACT_SOURCE_LABEL',
                financial_authority=False, label_version=version,
                knowable_at=source['body']['available_at'], value=value))
        result[mid] = row['id']
    return result


def refuses(action, code):
    with pytest.raises(EvidenceError) as caught:
        action()
    check(str(caught.value) == code, 'WRONG_REFUSAL:' + str(caught.value))


def check(condition, reason):
    if not condition:
        raise AssertionError(reason)


def test_full_path_unique_admission_and_outcome(rig):
    r = rig
    prepared(r)
    captures = [capture(r, 'repeat-' + str(i)) for i in range(10)]
    ids = labels(r, payouts(r))
    first = sc.record_forward_admission(r['plan'], r['store'],
        capture_id=captures[0]['id'], label_ids=ids)
    check(sc.record_forward_admission(r['plan'], r['store'],
        capture_id=captures[0]['id'], label_ids=ids) == first, 'REPLAY_CHANGED')
    for cap in captures[1:]:
        refuses(lambda cap=cap: sc.record_forward_admission(r['plan'], r['store'],
            capture_id=cap['id'], label_ids=ids), 'FORWARD_DUPLICATE_ADMISSION_OR_OUTCOME')
    target = sc.evidence_status(r['plan'], r['store'])['targets'][0]
    check(target['forward_admission_count'] == 1, 'DUPLICATE_ADMISSION_CREDIT')
    check(target['qualifying_forward_sample_count'] == 1, 'DUPLICATE_OUTCOME_CREDIT')
    check(target['sample_target_reached'] is False, 'SAMPLE_TARGET_INFLATED')
    check(not r['store'].records(kind='TRADE'), 'ACCOUNT_EFFECT')


def test_second_admission_cannot_reuse_same_grouped_outcome(rig):
    r = rig
    prepared(r)
    first = capture(r, 'first-capture')
    pin = StrategyAdmission(r['store']).pin('second-public-pin', **dict(r['admission_kw'],
        source_leases=(SourceLease('public-model', 'MODEL', 120.),)))
    second = capture(r, 'second-capture', admission_id=pin['id'])
    ids = labels(r, payouts(r))
    sc.record_forward_admission(r['plan'], r['store'], capture_id=first['id'], label_ids=ids)
    refuses(lambda: sc.record_forward_admission(r['plan'], r['store'],
        capture_id=second['id'], label_ids=ids), 'FORWARD_DUPLICATE_ADMISSION_OR_OUTCOME')
    target = sc.evidence_status(r['plan'], r['store'])['targets'][0]
    check((target['forward_admission_count'], target['qualifying_forward_sample_count']) == (1, 1),
          'GROUPED_OUTCOME_REUSED')


def test_status_deduplicates_preexisting_semantic_replays(rig):
    r = rig
    prepared(r)
    captures = [capture(r, 'legacy-repeat-' + str(i)) for i in range(10)]
    ids = labels(r, payouts(r))
    for i, cap in enumerate(captures):
        qualified = sc._qualified_admission(r['plan'], r['store'], r['plan'].targets[0], cap['id'], ids)
        r['store'].audit('legacy-qualification-' + str(i),
            event_id='admission:' + r['scope'].key, kind='REGISTRY',
            details=dict(version=sc.FORWARD_VERSION, qualification=qualified,
                label_ids=ids, financial_authority=False))
    target = sc.evidence_status(r['plan'], r['store'])['targets'][0]
    check(target['forward_admission_count'] == 1, 'LEGACY_ADMISSION_REPLAY_CREDIT')
    check(target['qualifying_forward_sample_count'] == 1, 'LEGACY_OUTCOME_REPLAY_CREDIT')
    check(target['sample_target_reached'] is False, 'LEGACY_REPLAY_TARGET_REACHED')


@pytest.mark.parametrize('early', ['all', 'one', 'equal_clock'])
def test_payout_must_follow_completed_capture(rig, early):
    r = rig
    prepared(r)
    if early == 'all':
        sources = payouts(r)
        cap = capture(r, 'late-capture')
    elif early == 'one':
        first = r['rule'].payload['partition'][0]['market_id']
        sources = payouts(r, only={first})
        cap = capture(r, 'partially-exposed-capture')
        sources.update(payouts(r, only={b['market_id'] for b in r['rule'].payload['partition'][1:]}))
    else:
        cap = capture(r, 'equal-clock-capture')
        sources = payouts(r, advance=False)
    ids = labels(r, sources)
    refuses(lambda: sc.record_forward_admission(r['plan'], r['store'],
        capture_id=cap['id'], label_ids=ids), 'FORWARD_LABEL_PROVENANCE_OR_CHRONOLOGY')
    check(sc.evidence_status(r['plan'], r['store'])['targets'][0]['forward_admission_count'] == 0,
          'LATE_PAYOUT_CREDIT')


def test_source_arrival_during_decisions_refuses_capture(rig):
    r = rig
    prepared(r)
    store = r['store']
    original = store.decision
    seen = []
    def decide(*args, **kwargs):
        if not seen:
            seen.append(True)
            r['now'][0] += .001
            store.capture('new-official', event_id=r['context'].event_id,
                kind='OFFICIAL_OBSERVATION', provider='LOCAL_TEST_FIXTURE',
                source_identity='KATL', revision='new', payload={'station': 'KATL'},
                observed_at=r['now'][0])
        return original(*args, **kwargs)
    store.decision = decide
    refuses(lambda: capture(r, 'source-drift'), 'STRATEGY_AUTHORITY_OR_SOURCE_CHANGED_RECOMPUTE')
    check(all(row['id'] != 'source-drift' for row in
        store.records(kind='MEASUREMENT', event_id=r['context'].event_id)), 'INVALID_CAPTURE_PUBLISHED')


@pytest.mark.parametrize('guard', ['OFFICIAL_OBSERVATION', 'MODEL', 'RULE_STATE', 'REGISTRY'])
def test_historical_source_guard_rejects_unguarded_capture(rig, guard):
    r = rig
    prepared(r)
    original = capture(r, 'original-capture')
    r['now'][0] += .001
    if guard in {'OFFICIAL_OBSERVATION', 'MODEL'}:
        r['store'].capture('late-' + guard, event_id=r['context'].event_id,
            kind=guard, provider='LOCAL_TEST_FIXTURE', source_identity='KATL',
            revision='late', payload={'station': 'KATL'})
    else:
        r['store'].audit('late-' + guard, event_id=r['context'].event_id if guard == 'RULE_STATE'
            else 'station:' + r['scope'].station, kind=guard, details={'test_only': True})
    unguarded = r['store'].audit('unguarded-capture', event_id=r['context'].event_id,
        kind='MEASUREMENT', details=original['body']['details'],
        evidence_ids=('public-model',))
    ids = labels(r, payouts(r))
    refuses(lambda: sc.record_forward_admission(r['plan'], r['store'],
        capture_id=unguarded['id'], label_ids=ids), 'FORWARD_ADMISSION_GUARD_CHANGED')
    check(sc.evidence_status(r['plan'], r['store'])['targets'][0]['forward_admission_count'] == 0,
          'INVALID_ADMISSION_CREDIT')


def test_capture_publication_atomically_guards_source_heads(rig):
    r = rig
    prepared(r)
    store = r['store']
    original = store.audit
    def audit(*args, **kwargs):
        if kwargs.get('kind') == 'MEASUREMENT':
            r['now'][0] += .001
            store.capture('publication-official', event_id=r['context'].event_id,
                kind='OFFICIAL_OBSERVATION', provider='LOCAL_TEST_FIXTURE',
                source_identity='KATL', revision='publication', payload={'station': 'KATL'})
        return original(*args, **kwargs)
    store.audit = audit
    refuses(lambda: capture(r, 'publication-race'), 'AUDIT_GUARDED_STATE_CHANGED')


def test_malformed_extra_label_refuses_without_status_crash(rig):
    r = rig
    prepared(r)
    cap = capture(r, 'status-capture')
    ids = labels(r, payouts(r))
    sc.record_forward_admission(r['plan'], r['store'], capture_id=cap['id'], label_ids=ids)
    check(sc.evidence_status(r['plan'], r['store'])['targets'][0]['forward_admission_count'] == 1,
          'VALID_BASELINE_MISSING')
    r['store'].capture('malformed-extra-label', event_id=r['context'].event_id,
        kind='LABEL', provider='LOCAL_TEST_FIXTURE', source_identity='junk', revision='1',
        payload={'target_identity': None})
    refuses(lambda: grouped_outcome(r['store'], cap['id'], ids), 'FORWARD_LABEL_SCHEMA_INVALID')
    status = sc.evidence_status(r['plan'], r['store'])
    check(status['targets'][0]['forward_admission_count'] == 0, 'MALFORMED_LABEL_ADMISSION_CREDIT')
    check(status['targets'][0]['qualifying_forward_sample_count'] == 0, 'MALFORMED_LABEL_SAMPLE_CREDIT')
    check(status['forward_evidence_available'] is False, 'MALFORMED_LABEL_FORWARD_CREDIT')
