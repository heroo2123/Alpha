"""Protected payout and received release context for non-executing maker quotes.

Point distances are diagnostics. They are neither maker EV, executable exits nor
confirmed releases, and cannot be submitted to the common account as valuations.
"""
from dataclasses import asdict
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from .certification import CapabilityScope
from .evidence import EvidenceError, canonical, finite, identity
from .maker_research import _heads, _restore
from .model_artifacts import predict_with_bundle
from .model_registry import ActiveModelRegistry
from .paper_coordinator import ACCOUNT_KEY
from .probability import FINAL_EXTREME, UNRESOLVED_EXTREME
from .scenario_risk import number, precise
from .strategy_admission import StrategyAdmission
from .strategy_pipeline import _condition, _model_inputs, _source


VERSION = 'alpha_v11_maker_context_v1'
NOTICE_VERSION = 'alpha_v11_received_release_notice_v1'


def _notice(store, key, quote, cutoff, maximum_age):
    """A provider's timestamped expectation is not evidence a release occurred."""
    result = dict(status='UNKNOWN', reason='NO_RECEIVED_NOTICE_SUPPLIED', notice_id=key,
                  expected_release_at=None, seconds_to_expected_release=None,
                  actual_release_confirmed=False, actual_release_at=None,
                  next_observation_value=None,
                  authority='RECEIVED_EXPECTATION_NOT_RELEASE_OR_SOURCE_CERTIFICATION')
    if key is None:
        return result
    try:
        row = _source(store, key, event_id=quote.context.event_id, kind='FEATURES', cutoff=cutoff)
        b, p = row['body'], row['body']['payload']
        fields = {'version', 'station', 'rule_fingerprint', 'release_kind', 'expected_release_at', 'valid_until'}
        if (set(p) != fields or p['version'] != NOTICE_VERSION
                or p['station'] != quote.context.station_id or p['rule_fingerprint'] != quote.rule.sha256
                or p['release_kind'] not in {'OFFICIAL_OBSERVATION', 'MODEL', 'WEATHER'}):
            raise EvidenceError('MAKER_RELEASE_NOTICE_SCHEMA_OR_SCOPE')
        latest = store.latest_source(kind='FEATURES', event_id=row['event_id'],
                                     provider=b['provider'], source_identity=b['source_identity'])
        if latest['id'] != row['id']:
            raise EvidenceError('MAKER_RELEASE_NOTICE_REVISION_CHANGED')
        issued = b['issued_at'] if b['issued_at'] is not None else b['observed_at']
        now = finite(store.clock())
        if (issued is None or not 0 <= cutoff-finite(issued) <= maximum_age
                or not 0 <= now-b['available_at'] <= maximum_age or now-issued > maximum_age):
            raise EvidenceError('MAKER_RELEASE_NOTICE_STALE_OR_AGE_UNKNOWN')
        due, expiry = finite(p['expected_release_at']), finite(p['valid_until'])
        if not b['available_at'] <= due < expiry <= b['available_at']+7*86400 or now >= expiry:
            raise EvidenceError('MAKER_RELEASE_NOTICE_TIME_BOUND')
        result.update(status='EXPECTED' if cutoff < due else 'EXPECTED_TIME_PASSED_UNCONFIRMED',
                      reason='ARCHIVED_PROVIDER_EXPECTATION_ONLY', release_kind=p['release_kind'],
                      expected_release_at=due, seconds_to_expected_release=due-cutoff,
                      valid_until=min(expiry, issued+maximum_age, b['available_at']+maximum_age),
                      notice_sha256=row['sha256'], provider=b['provider'],
                      source_identity=b['source_identity'], evidence_class=b['evidence_class'],
                      received_at=b['received_at'], available_at=b['available_at'])
    except EvidenceError as exc:
        result['reason'] = str(exc)
    return result


@precise
def measure_context(research, key, *, quote_id, model_input_ids, payout_admission_id=None,
                    observed_input_id=None, coverage_input_id=None, release_notice_id=None,
                    maximum_notice_age_seconds=300.):
    """Join a current quote to protected inference without altering its lifecycle.

    Same-day conditioning requires a separately reviewed SAME_DAY_LATE_LOCK pin.
    Missing/invalid notice information remains UNKNOWN, never no-release-risk.
    A repeated key returns its historical result and cannot renew any authority.
    """
    identity(key, maximum=80); identity(quote_id)
    if (type(model_input_ids) is not tuple or not 1 <= len(model_input_ids) <= 16
            or len(set(model_input_ids)) != len(model_input_ids)
            or not 0 < finite(maximum_notice_age_seconds) <= 86400):
        raise EvidenceError('MAKER_CONTEXT_INPUT_BOUND')
    for value in (*model_input_ids, payout_admission_id, observed_input_id, coverage_input_id, release_notice_id):
        if value is not None:
            identity(value)
    store = research.store
    request = dict(quote_id=quote_id, model_input_ids=list(model_input_ids), payout_admission_id=payout_admission_id,
                   observed_input_id=observed_input_id, coverage_input_id=coverage_input_id,
                   release_notice_id=release_notice_id, maximum_notice_age_seconds=maximum_notice_age_seconds)

    def previous(record_id, stage):
        try:
            row = store.get(record_id)
        except EvidenceError as exc:
            if str(exc) == 'EVIDENCE_MISSING':
                return None
            raise
        d = row['body'].get('details', {})
        if (row['kind'] != 'MEASUREMENT' or d.get('version') != VERSION
                or d.get('stage') != stage or d.get('policy_sha256') != research.policy_sha
                or canonical(d.get('request')) != canonical(request)):
            raise EvidenceError('MAKER_CONTEXT_REPLAY_CONFLICT')
        return row

    old = previous(key, 'COMPLETE')
    if old:
        return old
    head = research._head(); quotes = research._state(head)
    if quote_id not in quotes:
        raise EvidenceError('MAKER_QUOTE_MISSING')
    q = quotes[quote_id]; quote = _restore(q['request']); event_id = quote.context.event_id
    start = previous(key+':start', 'STARTED')
    if start is None:
        start = store.audit(key+':start', event_id=event_id, kind='MEASUREMENT', details=dict(
                    version=VERSION, policy_sha256=research.policy_sha, request=request, stage='STARTED',
                    quote_head_id=head['id']), evidence_ids=tuple(dict.fromkeys((head['id'], q['origin_record_id']))))
    cutoff = start['body']['recorded_at']; refs = [start['id'], head['id'], q['origin_record_id']]
    heads = [('MEASUREMENT', research.key, head['seq'])]
    fair = prediction = model = risk = event = None; expiry = None
    notice = dict(status='UNKNOWN', reason='CONTEXT_NOT_EVALUATED', actual_release_confirmed=False)
    outcome, reason = 'GATED', None
    try:
        now = finite(store.clock())
        if (start['body']['details']['quote_head_id'] != head['id'] or q['status'] != 'OBSERVING'
                or not cutoff <= now < q['expires_at']):
            raise EvidenceError('MAKER_CONTEXT_QUOTE_CHANGED_OR_EXPIRED')
        event, maker_admission, _, guards = research._admission(quote, now); heads.extend(guards)
        feature, book, book_policy, guard = research._feature(q['last_microstructure_id'], quote, now); heads.append(guard)
        if book['body']['available_at'] > cutoff:
            raise EvidenceError('MAKER_CONTEXT_BOOK_AFTER_CUTOFF')
        account_head = research.coordinator._head()
        heads.append(('COORDINATOR_EVENT', ACCOUNT_KEY, account_head['seq'] if account_head else 0))
        risk = research._project(research.coordinator._state(account_head), quotes)
        refs.extend((quote.admission_id, quote.event_state_id, q['last_microstructure_id'], book['id']))
        aid = payout_admission_id or quote.admission_id
        admission = StrategyAdmission(store).revalidate(aid, context=quote.context, rule=quote.rule,
                         binding=asdict(quote.binding), strategies=('MAKER_RESEARCH', 'FUTURE_FORECAST', 'SAME_DAY_LATE_LOCK'))
        original = store.get(aid)['body']['details']['request']; scope = CapabilityScope(**original['scope'])
        heads.extend(admission['heads']); refs.append(aid)
        day = date.fromisoformat(quote.rule.payload['target_date']); tz = ZoneInfo(quote.rule.payload['timezone'])
        day_start = datetime.combine(day, time.min, tz).timestamp()
        day_end = datetime.combine(day+timedelta(days=1), time.min, tz).timestamp()
        same_day = day_start <= cutoff
        if now >= day_end or cutoff < day_start <= now:
            raise EvidenceError('MAKER_CONTEXT_LOCAL_DAY_CHANGED_OR_ENDED')
        if same_day and scope.strategy != 'SAME_DAY_LATE_LOCK':
            raise EvidenceError('MAKER_SAME_DAY_REVIEWED_PAYOUT_PIN_REQUIRED')
        if not same_day and (scope.strategy == 'SAME_DAY_LATE_LOCK' or observed_input_id or coverage_input_id):
            raise EvidenceError('MAKER_FUTURE_DAY_CONDITION_MISMATCH')
        leases = {s['evidence_id']:s for s in original['source_leases']}
        if set(model_input_ids) != {k for k, s in leases.items() if s['role'] == 'MODEL'}:
            raise EvidenceError('ALL_INFERENCE_MODELS_REQUIRE_ADMISSION_LEASE')
        inference_cutoff = cutoff
        if same_day:
            if any(k not in leases or leases[k]['role'] != role for k, role in
                   ((observed_input_id, 'OFFICIAL'), (coverage_input_id, 'FEATURES'))):
                raise EvidenceError('SAME_DAY_CONDITION_REQUIRES_ADMISSION_LEASE')
            coverage_row = _source(store, coverage_input_id, event_id=event_id, kind='FEATURES', cutoff=cutoff)
            inference_cutoff = finite(coverage_row['body']['payload'].get('as_of'))
            if inference_cutoff > cutoff:
                raise EvidenceError('COVERAGE_CUTOFF_IN_FUTURE')
        components = _model_inputs(store, quote.rule, model_input_ids, inference_cutoff,
                                    target=UNRESOLVED_EXTREME if same_day else FINAL_EXTREME)
        condition = SimpleNamespace(observed_input_id=observed_input_id, coverage_input_id=coverage_input_id)
        observed, coverage = _condition(store, quote.rule, condition, inference_cutoff, components,
                                       available_cutoff=cutoff) if same_day else (None, None)
        model = ActiveModelRegistry().pin(scope_key=scope.key,
                                         mode='V11_PAPER' if original['stage'] == 'PAPER' else 'V11_SHADOW')
        if model.state_sha256 != admission['model_state_sha256'] or model.bundle.sha256 != quote.binding.bundle_sha256:
            raise EvidenceError('MAKER_CONTEXT_MODEL_CHANGED')
        prediction = predict_with_bundle(model.bundle, quote.rule, components, as_of=inference_cutoff,
                   max_source_age_seconds=min(leases[k]['maximum_age_seconds'] for k in model_input_ids),
                   observed=observed, remaining_coverage=coverage)
        interval = prediction.binary(quote.market_id, quote.side, required_target=FINAL_EXTREME)
        point, lower, upper = (Decimal(str(v)) for v in (interval.point, interval.lower, interval.upper))
        price = number(quote.limit_price)
        midpoint = number(feature['features']['midpoint'])
        fair = dict(target=FINAL_EXTREME, contract=q['target'], point=str(point), lower=str(lower), upper=str(upper),
                    calibration_status=prediction.payload['calibration_status'],
                    quote_minus_point=str(price-point), quote_minus_probability_interval=[str(price-upper), str(price-lower)],
                    midpoint_minus_point=str(midpoint-point), inference_cutoff=inference_cutoff,
                    prediction_sha256=prediction.sha256, model_epoch=model.epoch,
                    model_state_sha256=model.state_sha256, bundle_sha256=model.bundle.sha256,
                    artifact_refs=model.bundle.payload['bundle']['artifacts'],
                    classification='PAYOUT_MODEL_DISTANCE_NOT_MAKER_EV_OR_EXECUTABLE_EXIT')
        refs.extend(model_input_ids)
        refs.extend(k for k in (observed_input_id, coverage_input_id) if k is not None)
        if release_notice_id is not None:
            notice_head = store.latest(kind='FEATURES', event_id=event_id)
            heads.append(('FEATURES', event_id, notice_head['seq'] if notice_head else 0))
        notice = _notice(store, release_notice_id, quote, cutoff, maximum_notice_age_seconds)
        # Only existing exact-event records can be evidence references.
        if notice.get('notice_sha256'):
            refs.append(release_notice_id)
        expiry = min(q['expires_at'], event['valid_until'], maker_admission['valid_until'], admission['valid_until'],
                     book['body']['observed_at']+book_policy.maximum_book_age_seconds,
                     book['body']['received_at']+book_policy.maximum_book_age_seconds,
                     feature['as_of']+research.policy.maximum_feature_age_seconds, day_end if same_day else day_start)
        if notice.get('valid_until') is not None:
            expiry = min(expiry, notice['valid_until'])
        for pin in dict.fromkeys((quote.admission_id, aid)):
            check = StrategyAdmission(store).revalidate(pin, context=quote.context, rule=quote.rule,
                         binding=asdict(quote.binding), strategies=('MAKER_RESEARCH', 'FUTURE_FORECAST', 'SAME_DAY_LATE_LOCK'))
            heads.extend(check['heads'])
        if not ActiveModelRegistry().revalidate(model)['passed'] or not cutoff <= finite(store.clock()) < expiry:
            raise EvidenceError('MAKER_CONTEXT_CHANGED_OR_EXPIRED_DURING_INFERENCE')
        outcome, reason = 'MEASURED_RESEARCH_CONTEXT', 'POINT_DISTANCE_IS_NOT_EXECUTION_AUTHORITY'
    except EvidenceError as exc:
        reason = str(exc)

    def save(accepted):
        return store.audit(key, event_id=event_id, kind='MEASUREMENT', details=dict(
            version=VERSION, stage='COMPLETE', policy_sha256=research.policy_sha, request=request, as_of=cutoff,
            outcome=outcome if accepted else 'GATED', reason=reason,
            quote_head_id=head['id'], quote_age_seconds=cutoff-q['created_at'], valid_until=expiry if accepted else None,
            fair_value=fair if accepted else None, prediction=prediction.payload if accepted else None,
            release_context=notice if accepted else dict(status='UNKNOWN', reason='CONTEXT_GATED', actual_release_confirmed=False),
            projected_risk=risk if accepted else None, event_state=event['state'] if accepted else None,
            actual_trading_pnl=None, actual_settled_payout=None, executable_early_exit_value=None,
            fill_probability=None, queue_position=None, conservative_maker_net_ev=None,
            account_ledger_mutated=False, orders_submitted=False, financial_authority=False),
            evidence_ids=tuple(dict.fromkeys(refs)), expected_heads=_heads(heads) if accepted else ())
    try:
        return save(outcome == 'MEASURED_RESEARCH_CONTEXT')
    except EvidenceError as exc:
        if outcome != 'MEASURED_RESEARCH_CONTEXT':
            raise
        reason = str(exc)
        return save(False)
