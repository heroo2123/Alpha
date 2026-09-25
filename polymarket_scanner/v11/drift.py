"""Scoped rolling prediction quality from original captures and exact labels.

Read-only measurement, no fitting, default thresholds, calibration certification,
model publishing or financial interface. Reviewed withdrawal is a separate worker.
"""
from dataclasses import asdict, dataclass
import time

from .certification import CapabilityScope
from .evidence import EvidenceError, canonical, digest, finite, identity, sha
from .forecast_learning import ForecastLabelJoin
from .learning_capture import VERSION as FORECAST_VERSION, labeled_examples
from .learning_sources import learning_source_view
from .probability import CALIBRATION_ERROR_METHOD, score_vectors
from .rules import RuleFingerprint
from .strategy_admission import VERSION as ADMISSION_VERSION


VERSION='alpha_v11_scoped_drift_measurement_v1'
CALIBRATION_VERSION='alpha_v11_scoped_calibration_drift_measurement_v1'


@dataclass(frozen=True)
class DriftPolicy:
    version: str
    capture_family: str
    evidence_class: str
    window_seconds: float
    minimum_events: int
    minimum_city_days: int
    maximum_brier: float
    maximum_log_loss: float

    def __post_init__(self):
        identity(self.version)
        if (self.capture_family not in {'FORECAST','CONDITIONED_PAYOUT'}
                or self.evidence_class not in {'PUBLIC_OBSERVED','SYNTHETIC'}
                or not 1 <= finite(self.window_seconds) <= 366*86400
                or type(self.minimum_events) is not int or not 1 <= self.minimum_events <= 128
                or type(self.minimum_city_days) is not int or not 1 <= self.minimum_city_days <= self.minimum_events
                or not 0 <= finite(self.maximum_brier) <= 2
                or not 0 <= finite(self.maximum_log_loss) <= 1000):
            raise EvidenceError('DRIFT_POLICY_BOUND_OR_TARGET')

    @property
    def sha256(self): return digest(asdict(self))


@dataclass(frozen=True)
class CalibrationDriftPolicy(DriftPolicy):
    """Explicit additive policy; existing DriftPolicy hashes/thresholds do not change."""
    calibration_error_method: str
    maximum_calibration_error: float

    def __post_init__(self):
        super().__post_init__()
        if (self.calibration_error_method != CALIBRATION_ERROR_METHOD
                or not 0 <= finite(self.maximum_calibration_error) <= 1):
            raise EvidenceError('DRIFT_CALIBRATION_METHOD_OR_BOUND')


def measure_window(source_store, *, scope, account_id, bundle_sha256, policy, joins, as_of,
                   monotonic=time.monotonic):
    """One explicit cohort, maximum 128 captures/2048 buckets/two seconds.

    Every eligible row is reported or the job fails; no silent row selection.
    Complete vectors and repeated observations are weighted by event/city-day.
    Scope comes from the originally captured admission, never caller relabeling.
    """
    if not isinstance(scope,CapabilityScope) or not isinstance(policy,DriftPolicy):
        raise EvidenceError('DRIFT_TYPED_SCOPE_POLICY_REQUIRED')
    identity(account_id); sha(bundle_sha256); as_of=finite(as_of)
    if (source_store.namespace not in {'V11_PAPER','V11_SHADOW'} or as_of > source_store.clock()
            or type(joins) is not tuple or not 1 <= len(joins) <= 128
            or any(not isinstance(j,ForecastLabelJoin) or j.prior_exposure!='DEVELOPMENT' for j in joins)
            or len({j.capture_id for j in joins}) != len(joins)):
        raise EvidenceError('DRIFT_COHORT_NAMESPACE_CUTOFF_OR_EXPOSURE')
    request=dict(scope=asdict(scope),account_id=account_id,bundle_sha256=bundle_sha256,policy=asdict(policy),
        joins=[asdict(j) for j in joins],as_of=as_of,namespace=source_store.namespace)
    start=as_of-policy.window_seconds
    from .target_learning import VERSION as CONDITIONED_VERSION, labeled_target_examples
    version=FORECAST_VERSION if policy.capture_family=='FORECAST' else CONDITIONED_VERSION
    deadline=monotonic()+2.; rows=[]; vectors=[]; outcomes=[]; events=[]; city_days=[]; event_rules={}; buckets=0
    with learning_source_view(source_store,deadline=deadline,monotonic=monotonic) as view:
        for join in joins:
            view.check()
            capture=view.get(join.capture_id); d=capture['body'].get('details',{}); ref=d.get('admission_ref')
            if (capture['kind']!='MEASUREMENT' or d.get('version')!=version or not d.get('complete_event_vector')
                    or d.get('target')!='FINAL_CONTRACT_PAYOUT' or not d.get('parent_feature_contract_verified')
                    or not isinstance(ref,dict) or set(ref)!={'id','sha256'}
                    or not start <= capture['body']['recorded_at'] <= as_of):
                raise EvidenceError('DRIFT_ORIGINAL_SCOPED_CAPTURE_REQUIRED')
            admission=view.get(ref['id']); a=admission['body'].get('details',{}); r=a.get('request',{}); assessment=a.get('assessment',{})
            if (admission['kind']!='REGISTRY' or a.get('version')!=ADMISSION_VERSION or admission['sha256']!=ref['sha256']
                    or r.get('scope')!=asdict(scope) or r.get('context')!=d['context'] or r.get('rule')!=d['rule']
                    or r.get('binding')!=d['binding'] or d['binding']['bundle_sha256']!=bundle_sha256
                    or assessment.get('model_bundle_sha256')!=bundle_sha256 or d['context']['account_id']!=account_id
                    or admission['seq']>=capture['seq']
                    or not admission['body']['recorded_at'] <= capture['body']['recorded_at'] < assessment['valid_until']
                    or ('V11_PAPER' if r.get('stage')=='PAPER' else 'V11_SHADOW' if r.get('stage')=='SHADOW' else None)!=source_store.namespace
                    or (join.city,join.horizon,join.season)!=(d['context']['city_id'],scope.horizon,scope.season)):
                raise EvidenceError('DRIFT_ADMISSION_SCOPE_BUNDLE_OR_TIME')
            rule=RuleFingerprint(**d['rule']); rp=rule.payload
            if capture['event_id'] in event_rules and event_rules[capture['event_id']]!=rule.sha256:
                raise EvidenceError('DRIFT_EVENT_RULE_CHANGED')
            event_rules[capture['event_id']]=rule.sha256
            if (rp['station']!=scope.station or rp['source_family']!=scope.source_rule_family
                    or {'daily_high_temperature':'HIGH','daily_low_temperature':'LOW'}.get(rp['family'])!=scope.family):
                raise EvidenceError('DRIFT_RULE_SCOPE_MISMATCH')
            examples=(labeled_examples if policy.capture_family=='FORECAST' else labeled_target_examples)(view,join.capture_id,
                label_ids=dict(join.label_ids),city=join.city,horizon=join.horizon,season=join.season,prior_exposure='DEVELOPMENT')
            buckets+=len(examples)
            if buckets>2048: raise EvidenceError('DRIFT_BUCKET_BOUND')
            vector=[]; labels=[]; example_refs=[]; label_refs=[]; prediction_at=None
            for child, example in zip(d['rows'],examples):
                e=example.payload; decision=view.get(child['decision_id']); explanation=decision['body']['explanation']
                if (e['strategy']!=scope.strategy or e['binding']!=d['binding']
                        or e['label_available_at']>as_of or e['label_evidence_class']!=policy.evidence_class
                        or any(p['evidence_class']!=policy.evidence_class for p in e['provenance'])):
                    raise EvidenceError('DRIFT_LABEL_OR_PREDICTION_CLASS_SCOPE_CUTOFF')
                label=view.get(e['label_id']); lb=label['body']
                latest=view.latest_source(kind='LABEL',event_id=capture['event_id'],provider=lb['provider'],
                    source_identity=lb['source_identity'],as_of=as_of)
                if latest is None or latest['id']!=label['id']:
                    raise EvidenceError('DRIFT_LABEL_REVISION_SUPERSEDED')
                if policy.capture_family=='FORECAST':
                    point=explanation['point']; at=explanation['inference_cutoff']
                else:
                    prediction=explanation['prediction']; at=prediction['as_of']
                    if digest(prediction)!=d['prediction_sha256']:
                        raise EvidenceError('DRIFT_PREDICTION_BINDING')
                    found=[b for b in prediction['buckets'] if b['market_id']==child['target_identity']['market_id']]
                    if len(found)!=1: raise EvidenceError('DRIFT_BUCKET_PREDICTION_MISSING')
                    point=found[0]['point']
                if (explanation.get('prediction_sha256')!=d['prediction_sha256'] or not start <= finite(at) <= capture['body']['recorded_at']
                        or prediction_at is not None and prediction_at!=at
                        or not 0 <= finite(point) <= 1):
                    raise EvidenceError('DRIFT_PREDICTION_WINDOW_OR_BINDING')
                prediction_at=at; vector.append(point); labels.append(e['label_value'])
                example_refs.append(dict(decision_id=e['decision_id'],example_sha256=example.sha256))
                label_refs.append(dict(id=label['id'],sha256=label['sha256'],available_at=e['label_available_at']))
            if len(vector)!=len(d['rows']) or labels.count(1)!=1:
                raise EvidenceError('DRIFT_COMPLETE_VECTOR_REQUIRED')
            vectors.append(vector); outcomes.append(labels.index(1)); events.append(capture['event_id']); city_days.append(join.city+':'+rp['target_date'])
            rows.append(dict(capture_id=capture['id'],capture_sha256=capture['sha256'],admission_ref=ref,
                model_state_sha256=assessment['model_state_sha256'],prediction_sha256=d['prediction_sha256'],
                event_id=capture['event_id'],city_day=city_days[-1],prediction_at=prediction_at,labels=label_refs,examples=example_refs))
        view.check(); scores=score_vectors(vectors,outcomes,event_ids=events,city_days=city_days,
            calibration_error_method=policy.calibration_error_method if isinstance(policy,CalibrationDriftPolicy) else None); view.check()
        through_seq=view.snapshot_seq
    sufficient=scores['n_events']>=policy.minimum_events and scores['n_city_days']>=policy.minimum_city_days
    breaches=[]
    if scores['brier']>policy.maximum_brier: breaches.append('BRIER_ABOVE_DECLARED_MAXIMUM')
    if scores['log_loss_infinite'] or scores['log_loss']>policy.maximum_log_loss: breaches.append('LOG_LOSS_ABOVE_DECLARED_MAXIMUM')
    calibration=isinstance(policy,CalibrationDriftPolicy)
    if calibration and scores['calibration_error']['value']>policy.maximum_calibration_error:
        breaches.append('CALIBRATION_ERROR_ABOVE_DECLARED_MAXIMUM')
    result=dict(version=CALIBRATION_VERSION if calibration else VERSION,request_sha256=digest(request),request=request,source_through_seq=through_seq,
        window=dict(start_inclusive=start,end_inclusive=as_of),rows=rows,scores=scores,
        cohort_sufficient=sufficient,threshold_breaches=breaches,
        outcome='INSUFFICIENT_COHORT' if not sufficient else 'DEGRADATION_CANDIDATE' if breaches else 'NO_DECLARED_BREACH',
        selection='EXPLICIT_CAPTURE_COHORT',global_universe_coverage_verified=False,
        evidence_class=policy.evidence_class,independent_label_attestation=False,
        predeclared_policy_review_verified=False,prior_exposure='DEVELOPMENT',
        unsupported_metrics=([] if calibration else ['SCALAR_CALIBRATION_ERROR'])+['HORIZON_MATCHED_MARKOUT','NET_EV_CAPTURE','REALIZED_PNL','DRAWDOWN','SOURCE_RESIDUAL_BIAS'],
        demotion_applied=False,financial_authority=False,calibration_acceptance=False)
    if len(canonical(result).encode())>512*1024: raise EvidenceError('DRIFT_RESULT_BYTES_BOUND')
    return result
