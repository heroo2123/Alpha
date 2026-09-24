"""Normalize authorized archived GEFS responses without inventing a model run.

The current seamless endpoint does not bind a response to one initialization.
Its members can be archived for research, but remain ineligible model-age inputs.
This adapter performs no HTTP and does not attest access rights or calibration.
"""
from dataclasses import asdict, dataclass
from datetime import date

from ..weather_only_contracts import DAILY_HIGH, DAILY_LOW
from ..weather_only_forecast import (OPEN_METEO_ENSEMBLE, OPEN_METEO_GEFS_MODEL,
    WeatherForecastError, parse_open_meteo_gefs_daily_extreme)
from .certification import StationMetadata
from .evidence import EvidenceError, canonical, digest, finite
from .probability import FINAL_EXTREME, target_identity
from .pws_quality import geometry
from .rules import RuleFingerprint
from .strategy_pipeline import INPUT_VERSION


VERSION = 'alpha_v11_archived_gefs_normalization_v1'
PROVIDER = 'OPEN_METEO_GEFS'


@dataclass(frozen=True)
class ForecastPlan:
    rule: RuleFingerprint
    metadata: StationMetadata
    maximum_grid_distance_km: float
    maximum_receipt_age_seconds: float

    def __post_init__(self):
        if not isinstance(self.rule,RuleFingerprint) or not isinstance(self.metadata,StationMetadata):
            raise EvidenceError('FORECAST_TYPED_CONTEXT_REQUIRED')
        p=self.rule.payload
        if (p['station']!=self.metadata.station or p['timezone']!=self.metadata.timezone
                or p['metadata_fingerprint']!=self.metadata.fingerprint
                or p['family'] not in {DAILY_HIGH,DAILY_LOW}):
            raise EvidenceError('FORECAST_EXACT_RULE_METADATA_REQUIRED')
        if (not 0<finite(self.maximum_grid_distance_km)<=200
                or not 0<finite(self.maximum_receipt_age_seconds)<=86400):
            raise EvidenceError('FORECAST_SOURCE_POLICY_BOUND')

    @property
    def event_id(self):return self.rule.payload['event_id']

    @property
    def source_identity(self):
        p=self.rule.payload
        return 'GEFS_DAILY:'+p['station']+':'+p['target_date']+':'+p['family']


def request_parameters(plan):
    """Exact request identity to verify in an already authorized raw archive."""
    if not isinstance(plan,ForecastPlan):raise EvidenceError('FORECAST_PLAN_REQUIRED')
    p=plan.rule.payload
    return dict(latitude=str(plan.metadata.latitude),longitude=str(plan.metadata.longitude),
        models=OPEN_METEO_GEFS_MODEL,daily='temperature_2m_max' if p['family']==DAILY_HIGH else 'temperature_2m_min',
        temperature_unit='fahrenheit' if p['unit']=='F' else 'celsius',timezone=p['timezone'],
        start_date=p['target_date'],end_date=p['target_date'],cell_selection='nearest')


def normalize_forecast_capture(store,raw_id,*,plan,record_id):
    if not isinstance(plan,ForecastPlan):raise EvidenceError('FORECAST_PLAN_REQUIRED')
    raw=store.get(raw_id);b=raw['body'];payload=b.get('payload',{});at=finite(store.clock())
    request_sha=digest(dict(raw_id=raw_id,raw_sha256=raw['sha256'],plan=asdict(plan)))
    try:existing=store.get(record_id)
    except EvidenceError as exc:
        if str(exc)!='EVIDENCE_MISSING':raise
    else:
        if existing['body'].get('payload',{}).get('normalization_sha256')!=request_sha:
            raise EvidenceError('FORECAST_NORMALIZATION_REPLAY_CONFLICT')
        return existing
    head=store.latest(kind='MODEL',event_id=plan.event_id)
    current=store.latest_source(kind='MODEL',event_id=plan.event_id,provider=PROVIDER,source_identity=plan.source_identity)
    if (raw['event_id']!=plan.event_id or raw['kind']!='MODEL' or b.get('provider')!=PROVIDER
            or b.get('source_identity')!=plan.source_identity or current is None or current['id']!=raw_id):
        raise EvidenceError('FORECAST_CURRENT_RAW_CONTEXT_REQUIRED')
    if (b['evidence_class']=='HISTORICAL_AVAILABILITY_UNKNOWN'
            or not 0<=b['received_at']<=b['available_at']<=b['recorded_at']<=at
            or at-b['received_at']>=plan.maximum_receipt_age_seconds):
        raise EvidenceError('FORECAST_RAW_NOT_CAUSAL_OR_FRESH')
    if len(canonical(payload).encode())>768*1024:raise EvidenceError('FORECAST_RAW_BYTES_BOUND')
    expected=request_parameters(plan);params=payload.get('request_params')
    if payload.get('request_url')!=OPEN_METEO_ENSEMBLE or not isinstance(params,dict) or set(params)!=set(expected):
        raise EvidenceError('FORECAST_EXACT_REQUEST_REQUIRED')
    for k,v in expected.items():
        if k in {'latitude','longitude'}:
            try:actual=finite(float(params[k]),nonnegative=False)
            except (ValueError,TypeError):raise EvidenceError('FORECAST_REQUEST_COORDINATE_INVALID') from None
            if isinstance(params[k],bool) or actual!=float(v):raise EvidenceError('FORECAST_REQUEST_COORDINATE_MISMATCH')
        elif params[k]!=v:raise EvidenceError('FORECAST_REQUEST_TARGET_OR_MODEL_MISMATCH')
    response=payload.get('response');p=plan.rule.payload
    # This adapter requests exactly one day, never silently choosing a target
    # from an expanded or backfilled response.
    if (not isinstance(response,dict) or not isinstance(response.get('daily'),dict)
            or response['daily'].get('time')!=[p['target_date']]):
        raise EvidenceError('FORECAST_EXACT_DAY_RESPONSE_REQUIRED')
    try:
        distribution=parse_open_meteo_gefs_daily_extreme(response,station=p['station'],target_date=date.fromisoformat(p['target_date']),
            family=p['family'],unit=p['unit'],timezone=p['timezone'],requested_latitude=plan.metadata.latitude,
            requested_longitude=plan.metadata.longitude,received_at=b['received_at'])
    except WeatherForecastError as exc:raise EvidenceError(exc.code) from None
    distance,_=geometry(plan.metadata.latitude,plan.metadata.longitude,distribution.resolved_latitude,distribution.resolved_longitude)
    if distance>plan.maximum_grid_distance_km:raise EvidenceError('FORECAST_RESOLVED_GRID_OUTSIDE_POLICY')
    result=dict(version=VERSION,normalization_sha256=request_sha,raw_evidence_id=raw_id,raw_evidence_sha256=raw['sha256'],
        rule_fingerprint=plan.rule.sha256,metadata_fingerprint=plan.metadata.fingerprint,
        **{k:p[k] for k in ('station','target_date','family','unit')},
        temperature_input=dict(version=INPUT_VERSION,model_id=OPEN_METEO_GEFS_MODEL,
            target_sha256=target_identity(plan.rule,FINAL_EXTREME),members=list(distribution.member_values)),
        provider_distribution=distribution.as_dict(),grid_distance_km=distance,feature_ready_at=at,
        run_provenance=dict(status='EXACT_RUN_BINDING_UNAVAILABLE',initialization_at=None,publication_at=None,
            raw_issue_claim_trusted=False,metadata_endpoint_is_not_response_run_binding=True),
        source_admission='GATED_MODEL_RUN_AGE_UNKNOWN',settlement_authority=False,
        calibration_label_authority=False,calibrated_probability=False,financial_authority=False)
    derived=dict(provider=PROVIDER,source_identity=plan.source_identity,revision=b['revision'],payload=result,
        observed_at=None,issued_at=None,published_at=None,received_at=b['received_at'],
        evidence_class=b['evidence_class'],source_kind='MODEL')
    return store._append(record_id,'MODEL',plan.event_id,derived,at,at,expected_previous_seq=head['seq'])
