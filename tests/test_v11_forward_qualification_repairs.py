"""Full synthetic ledger path for reviewed nonfinancial qualification refusals."""
from dataclasses import asdict, replace
from copy import deepcopy

import pytest

from polymarket_scanner.v11 import certification as cert, learning_capture as lc, shadow_commission as sc
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


@pytest.fixture
def synthetic_interval(monkeypatch):
    # Exercise deduplication below the protected gate with fixture-only proof.
    # Production has no such proof and always refuses forward credit.
    monkeypatch.setattr(sc, '_require_protected_interval', lambda *args: None)


def test_full_path_unique_admission_and_outcome(rig, synthetic_interval):
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


def test_second_admission_cannot_reuse_same_grouped_outcome(rig, synthetic_interval):
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


def test_status_deduplicates_preexisting_semantic_replays(rig, synthetic_interval):
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


def test_malformed_extra_label_refuses_without_status_crash(rig, synthetic_interval):
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


@pytest.mark.parametrize('early_count,roleless', [(1, False), (3, False), (1, True)])
def test_unselected_early_payout_recapture_refuses(rig, early_count, roleless):
    r = rig
    prepared(r)
    store = r['store']
    original = store.capture
    seen = []
    def earlier(key, **kwargs):
        seen.append(key)
        if len(seen) > early_count:
            kwargs['event_id'] = 'unrelated-event'
        if roleless:
            kwargs['payload'].pop('capture_role')
        return original('earlier:' + key, **kwargs)
    store.capture = earlier
    try:
        payouts(r)
    finally:
        store.capture = original
    cap = capture(r, 'recaptured-payout')
    ids = labels(r, payouts(r))
    refuses(lambda: sc.record_forward_admission(r['plan'], store,
        capture_id=cap['id'], label_ids=ids), 'FORWARD_LABEL_PROVENANCE_OR_CHRONOLOGY')
    check(sc.evidence_status(r['plan'], store)['targets'][0]['forward_admission_count'] == 0,
          'EARLY_RECAPTURE_CREDIT')


def test_payout_interleaved_before_durable_capture_refuses(rig):
    r = rig
    prepared(r)
    store = r['store']
    original_audit, original_capture = store.audit, store.capture
    def during_publication(*args, **kwargs):
        if kwargs.get('kind') == 'MEASUREMENT':
            store.capture = lambda key, **kw: original_capture('interleaved:' + key, **kw)
            try:
                payouts(r)
            finally:
                store.capture = original_capture
        return original_audit(*args, **kwargs)
    store.audit = during_publication
    cap = capture(r, 'payout-publication-interleave')
    ids = labels(r, payouts(r))
    refuses(lambda: sc.record_forward_admission(r['plan'], store,
        capture_id=cap['id'], label_ids=ids), 'FORWARD_LABEL_PROVENANCE_OR_CHRONOLOGY')
    check(sc.evidence_status(r['plan'], store)['targets'][0]['forward_admission_count'] == 0,
          'INTERLEAVED_PAYOUT_CREDIT')


@pytest.mark.parametrize('market_id', [[], {}, None, 123, ''])
def test_malformed_nested_label_identity_refuses(rig, synthetic_interval, market_id):
    r = rig
    prepared(r)
    cap = capture(r, 'malformed-nested')
    ids = labels(r, payouts(r))
    sc.record_forward_admission(r['plan'], r['store'], capture_id=cap['id'], label_ids=ids)
    r['store'].capture('bad-nested-label', event_id=r['context'].event_id,
        kind='LABEL', provider='LOCAL_TEST_FIXTURE', source_identity='junk', revision='1',
        payload={'target_identity': {'market_id': market_id, 'condition_id': 'unrelated-condition',
                                     'token_id': 'unrelated-token', 'side': 'YES'}})
    refuses(lambda: grouped_outcome(r['store'], cap['id'], ids), 'FORWARD_LABEL_SCHEMA_INVALID')
    result = sc.evidence_status(r['plan'], r['store'])
    check(result['targets'][0]['forward_admission_count'] == 0, 'MALFORMED_NESTED_CREDIT')


@pytest.mark.parametrize('complete', [False, True])
def test_unrelated_label_identity_requires_schema(rig, synthetic_interval, complete):
    r = rig
    prepared(r)
    cap = capture(r, 'unrelated-label-schema')
    ids = labels(r, payouts(r))
    sc.record_forward_admission(r['plan'], r['store'], capture_id=cap['id'], label_ids=ids)
    target = {'market_id': 'unrelated-market'}
    if complete:
        target.update(condition_id='unrelated-condition', token_id='unrelated-token', side='YES')
    r['store'].capture('unrelated-label', event_id=r['context'].event_id,
        kind='LABEL', provider='LOCAL_TEST_FIXTURE', source_identity='junk', revision='1',
        payload={'target_identity': target})
    if complete:
        check(grouped_outcome(r['store'], cap['id'], ids)['market_ids'], 'UNRELATED_LABEL_BROKE_GROUP')
        check(sc.evidence_status(r['plan'], r['store'])['targets'][0]['forward_admission_count'] == 1,
              'VALID_UNRELATED_LABEL_REMOVED_CREDIT')
    else:
        refuses(lambda: grouped_outcome(r['store'], cap['id'], ids), 'FORWARD_LABEL_SCHEMA_INVALID')
        check(sc.evidence_status(r['plan'], r['store'])['targets'][0]['forward_admission_count'] == 0,
              'MALFORMED_UNRELATED_LABEL_CREDIT')


@pytest.mark.parametrize('phase', ['decision', 'publication'])
def test_transient_protected_withdrawal_cannot_earn_credit(rig, monkeypatch, phase):
    r = rig
    prepared(r)
    store = r['store']
    manifest = cert.protected_reviews()
    current = [manifest]
    monkeypatch.setattr(cert, 'protected_reviews', lambda: deepcopy(current[0]))
    original = store.decision if phase == 'decision' else store.audit
    def interleave(*args, **kwargs):
        if phase == 'decision' or kwargs.get('kind') == 'MEASUREMENT':
            current[0] = {'version': manifest['version'], 'reviews': []}
            try:
                return original(*args, **kwargs)
            finally:
                current[0] = manifest
        return original(*args, **kwargs)
    if phase == 'decision':
        store.decision = interleave
    else:
        store.audit = interleave
    cap = capture(r, 'protected-withdrawal-' + phase)
    ids = labels(r, payouts(r))
    refuses(lambda: sc.record_forward_admission(r['plan'], store,
        capture_id=cap['id'], label_ids=ids), 'FORWARD_PROTECTED_INTERVAL_UNPROVEN')
    check(sc.evidence_status(r['plan'], store)['targets'][0]['forward_admission_count'] == 0,
          'PROTECTED_WITHDRAWAL_CREDIT')


def test_protected_interval_missing_refuses_even_with_valid_current_state(rig):
    r = rig
    prepared(r)
    cap = capture(r, 'unproven-protected-interval')
    ids = labels(r, payouts(r))
    refuses(lambda: sc.record_forward_admission(r['plan'], r['store'],
        capture_id=cap['id'], label_ids=ids), 'FORWARD_PROTECTED_INTERVAL_UNPROVEN')
    check(sc.evidence_status(r['plan'], r['store'])['targets'][0]['forward_admission_count'] == 0,
          'UNPROVEN_PROTECTED_CREDIT')


def test_preexisting_receipt_without_protected_interval_loses_status_credit(rig):
    r = rig
    pin = prepared(r)
    cap = capture(r, 'legacy-unproven-interval')
    ids = labels(r, payouts(r))
    group = grouped_outcome(r['store'], cap['id'], ids)
    target = r['plan'].targets[0]
    q = dict(group, admission_id=pin['id'], admission_sha256=pin['sha256'],
             plan_key=r['plan'].key, scope_key=target.scope.key)
    r['store'].audit('legacy-unproven-receipt', event_id='admission:' + target.scope.key,
        kind='REGISTRY', details=dict(version=sc.FORWARD_VERSION,
                                      qualification=q, label_ids=ids, financial_authority=False))
    result = sc.evidence_status(r['plan'], r['store'])
    check(result['targets'][0]['forward_admission_count'] == 0, 'LEGACY_PROTECTED_CREDIT')
    check(result['targets'][0]['qualifying_forward_sample_count'] == 0, 'LEGACY_PROTECTED_SAMPLE')


@pytest.mark.parametrize('authority_type', ['model', 'certification'])
def test_persistent_protected_change_at_publication_cannot_earn_credit(rig, monkeypatch,
                                                                         authority_type):
    r = rig
    prepared(r)
    store = r['store']
    original = store.audit
    def publish(*args, **kwargs):
        if kwargs.get('kind') == 'MEASUREMENT':
            if authority_type == 'model':
                from test_v11_model_governance import authority
                state = r['model_state'][0]
                r['model_state'][0] = authority.transition(state, action='DEMOTE',
                    expected_state_sha256=digest(state), now=r['now'][0],
                    reason='REPAIR_RACE_PROBE', size_multiplier=0.)
            else:
                monkeypatch.setattr(cert, 'protected_reviews', lambda: {
                    'version': 'alpha_v11_certification_reviews_v1', 'reviews': []})
        return original(*args, **kwargs)
    store.audit = publish
    cap = capture(r, 'persistent-protected-' + authority_type)
    ids = labels(r, payouts(r))
    refuses(lambda: sc.record_forward_admission(r['plan'], store,
        capture_id=cap['id'], label_ids=ids), 'FORWARD_PROTECTED_INTERVAL_UNPROVEN')
    check(sc.evidence_status(r['plan'], store)['targets'][0]['forward_admission_count'] == 0,
          'PERSISTENT_PROTECTED_CREDIT')
