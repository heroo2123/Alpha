"""Causal physical feature inputs and bounded, data-only inference parameters.

Coefficients are research artifacts, never intuitive meteorology embedded in
code. Protected scoped review, actual calibration/ablation and separate payout
economics remain required. No feature supplies settlement or execution truth.
"""
from dataclasses import asdict, dataclass
import json
import math

from .datasets import FeatureSchema
from .evidence import EvidenceError, canonical, digest, finite, identity
from .forecast_features import ForecastFeatureContract
from .nowcast_features import PHYSICAL_SCHEMA
from .probability import AuxiliaryFeatures, NEXT_OBSERVATION, UNRESOLVED_EXTREME, target_identity


VERSION = 'alpha_v11_physical_model_input_v1'
FAMILY = 'GAUSSIAN_PHYSICAL_MEMBER_MIXTURE'


@dataclass(frozen=True)
class PhysicalFeatureContract:
    model_widths: tuple[tuple[str, int], ...]
    unit: str
    family: str
    input_target: str

    def __post_init__(self):
        base = ForecastFeatureContract(self.model_widths,self.unit,self.family)
        if self.input_target not in {NEXT_OBSERVATION,UNRESOLVED_EXTREME}:
            raise EvidenceError('PHYSICAL_NEXT_OR_REMAINING_TARGET_REQUIRED')
        if len(base.schema.features)+len(PHYSICAL_SCHEMA.features) > 128:
            raise EvidenceError('PHYSICAL_FEATURE_CONTRACT_BOUND')
        object.__setattr__(self,'model_widths',base.model_widths)

    @property
    def schema(self):
        base = ForecastFeatureContract(self.model_widths,self.unit,self.family)
        return FeatureSchema('physical-members:'+digest([asdict(base),self.input_target,PHYSICAL_SCHEMA.sha256]),
                             base.schema.features+PHYSICAL_SCHEMA.features)

    @property
    def bundle_target(self):
        return NEXT_OBSERVATION if self.input_target==NEXT_OBSERVATION else 'FINAL_CONTRACT_PAYOUT'

    def require_bundle(self,pinned):
        value = pinned.payload
        if (value['bundle']['target'] != self.bundle_target
                or value['bundle']['feature_schema_sha256'] != self.schema.sha256
                or value['components']['FEATURES']['parameters'] != json.loads(canonical(asdict(self.schema)))
                or value['components']['PROBABILITY']['parameters']['family'] != FAMILY
                or {m['model_id'] for m in value['components']['PROBABILITY']['parameters']['models']} != {m for m,_ in self.model_widths}):
            raise EvidenceError('PHYSICAL_INFERENCE_CONTRACT_MISMATCH')
        return value


def validate_terms(model):
    terms = model['feature_terms']; definitions = {f.name:f for f in PHYSICAL_SCHEMA.features}
    if (type(terms) is not list or not 1 <= len(terms) <= len(definitions)
            or any(type(t) is not dict or set(t) != {'feature','center','scale','coefficient'} for t in terms)
            or any(type(t['feature']) is not str for t in terms)
            or len({t['feature'] for t in terms}) != len(terms)):
        raise EvidenceError('PHYSICAL_PARAMETER_TERMS_BOUND')
    for t in terms:
        f = definitions.get(t['feature'])
        if (f is None or not f.minimum <= finite(t['center'],nonnegative=False) <= f.maximum
                or not .001 <= finite(t['scale']) <= 100000.
                or abs(finite(t['coefficient'],nonnegative=False)) > 30):
            raise EvidenceError('PHYSICAL_PARAMETER_VALUE_BOUND')
    missing = finite(model['missing_kernel_sigma'])
    if (not model['kernel_sigma'] <= missing <= 50
            or any(t['coefficient'] != 0 for t in terms) and missing <= model['kernel_sigma']):
        raise EvidenceError('MISSING_FEATURE_CANNOT_NARROW_UNCERTAINTY')


def adjusted_parameters(component,model):
    aux = component.auxiliary
    if aux is None or aux.schema_sha256 != PHYSICAL_SCHEMA.sha256:
        raise EvidenceError('PHYSICAL_FEATURE_INPUT_REQUIRED')
    values = dict(aux.values); PHYSICAL_SCHEMA.validate(values)
    changes = []; missing = False
    for term in model['feature_terms']:
        value = values[term['feature']]
        if value is None:
            missing |= term['coefficient'] != 0
        else:
            changes.append(term['coefficient']*(value-term['center'])/term['scale'])
    bias = model['bias']+math.fsum(changes)
    if not math.isfinite(bias) or abs(bias) > 30:
        raise EvidenceError('PHYSICAL_ADJUSTMENT_OUTSIDE_PROTECTED_BOUND')
    return bias, model['missing_kernel_sigma'] if missing else model['kernel_sigma']


def build_initial_physical_bundle(artifacts,*,contract,probability_parameters,provenance,quality_modifiers):
    from .model_artifacts import ARTIFACT_VERSION, validate_artifact
    if (not isinstance(contract,PhysicalFeatureContract) or provenance.get('training_status') != 'INITIAL_NO_FIT'
            or provenance.get('parent_bundle_sha256') is not None
            or probability_parameters.get('family') != FAMILY
            or {m['model_id'] for m in probability_parameters.get('models',[])} != {m for m,_ in contract.model_widths}):
        raise EvidenceError('PHYSICAL_INITIAL_RESEARCH_CONTRACT_REQUIRED')
    parameters = dict(FEATURES=json.loads(canonical(asdict(contract.schema))),PROBABILITY=probability_parameters,
        EXECUTION_COST=dict(method='NO_EMPIRICAL_EXECUTION_MODEL',additional_cost_per_share=None,evidence_class='UNKNOWN'),
        STRATEGY_QUALITY=dict(method='FIXED_REDUCTION_ONLY',modifiers=quality_modifiers))
    values = {k:dict(version=ARTIFACT_VERSION,kind=k,target=contract.bundle_target,feature_schema_sha256=contract.schema.sha256,
        parameters=p,provenance=provenance) for k,p in parameters.items()}
    values['CALIBRATION'] = dict(version=ARTIFACT_VERSION,kind='CALIBRATION',target=contract.bundle_target,
        feature_schema_sha256=contract.schema.sha256,provenance=provenance,parameters=dict(method='VACUOUS_BOUNDS',
        status='UNCALIBRATED',probability_artifact_sha256=digest(values['PROBABILITY'])))
    for value in values.values(): validate_artifact(value)
    refs = {k:artifacts.put_artifact(v) for k,v in values.items()}
    return artifacts.put_bundle(artifacts=refs,target=contract.bundle_target,feature_schema_sha256=contract.schema.sha256)


def _sources(store,row,rule,cutoff):
    p = row['body']['payload']; refs = p.get('dependencies')
    if (type(refs) is not list or not 2 <= len(refs) <= 3
            or any(type(r) is not dict or set(r) != {'id','sha256'} for r in refs)
            or len({r['id'] for r in refs}) != len(refs)):
        raise EvidenceError('PHYSICAL_MODEL_DERIVATION_REQUIRED')
    rows = [store.get(ref['id']) for ref in refs]
    for source,ref in zip(rows,refs):
        if (source['sha256'] != ref['sha256'] or source['event_id'] != row['event_id'] or source['seq'] >= row['seq']
                or source['body']['available_at'] > row['body']['available_at']
                or source['body']['evidence_class'] not in {'PUBLIC_OBSERVED','SYNTHETIC'}):
            raise EvidenceError('PHYSICAL_MODEL_SOURCE_BINDING')
    base,feature = rows[:2]; b = base['body']; f = feature['body']; fp = f.get('payload',{}); context = fp.get('context',{})
    if type(context) is not dict:
        raise EvidenceError('PHYSICAL_MODEL_CONTEXT_OR_FRESHNESS')
    if (base['kind'] != 'MODEL' or feature['kind'] != 'FEATURES' or b['payload'].get('version') == VERSION
            or b['payload'].get('temperature_input') != p.get('temperature_input')
            or b['issued_at'] != row['body']['issued_at'] or b['received_at'] != row['body']['received_at']
            or any(b['payload'].get(k) != rule.payload[k] for k in ('station','target_date','family','unit'))
            or b['payload'].get('rule_fingerprint') != rule.sha256
            or b['payload'].get('observation_context') != p.get('observation_context')
            or fp.get('feature_schema') != json.loads(canonical(asdict(PHYSICAL_SCHEMA)))
            or fp.get('feature_schema_sha256') != PHYSICAL_SCHEMA.sha256
            or context.get('version') != 'alpha_v11_physical_context_v1'
            or context.get('station') != rule.payload['station']
            or context.get('metadata_fingerprint') != rule.payload['metadata_fingerprint']
            or not finite(context.get('as_of')) <= f['available_at'] <= cutoff < finite(context.get('valid_until'))):
        raise EvidenceError('PHYSICAL_MODEL_CONTEXT_OR_FRESHNESS')
    PHYSICAL_SCHEMA.validate(fp.get('values'))
    return rows


def auxiliary_input(store,row,rule,cutoff):
    if row['body']['payload'].get('version') != VERSION: return None
    _,feature,*_ = _sources(store,row,rule,cutoff)
    return AuxiliaryFeatures(PHYSICAL_SCHEMA.sha256,feature['sha256'],tuple(sorted(feature['body']['payload']['values'].items())))


def _distinct(heads):
    result = {}
    for kind,event,seq in heads:
        if (kind,event) in result and result[kind,event] != seq:
            raise EvidenceError('PHYSICAL_SOURCE_CHANGED_DURING_READ')
        result[kind,event] = seq
    if len(result) > 64: raise EvidenceError('PHYSICAL_SOURCE_GUARD_BOUND')
    return tuple((k,e,s) for (k,e),s in result.items())


def current_physical_heads(store,row):
    from .gefs_sources import current_path_heads
    from .pws_quality import current_neighborhood_heads
    from .rules import RuleFingerprint
    p = row['body']['payload']; rule = RuleFingerprint(**p['rule'])
    sources = _sources(store,row,rule,finite(store.clock())); base,feature = sources[:2]
    refs = feature['body']['payload'].get('dependencies')
    if (type(refs) is not list or not 1 <= len(refs) <= 64
            or any(type(r) is not dict or set(r) != {'id','sha256'} for r in refs)):
        raise EvidenceError('PHYSICAL_FEATURE_DERIVATION_REQUIRED')
    pws_material = any(feature['body']['payload']['values'][f.name] is not None
                       for f in PHYSICAL_SCHEMA.features if f.family == 'PWS')
    heads = []; groups = {}
    for source in (*sources,*(store.get(ref['id']) for ref in refs)):
        b = source['body']; kind,event = source['kind'],source['event_id']
        if source not in sources and (source['seq'] >= feature['seq'] or b['available_at'] > feature['body']['available_at']
                or source['event_id'] != row['event_id']
                or dict(id=source['id'],sha256=source['sha256']) not in refs):
            raise EvidenceError('PHYSICAL_FEATURE_SOURCE_BINDING')
        key = (kind,event,b['provider'],b['source_identity'])
        if key not in groups or source['seq'] > groups[key]['seq']: groups[key] = source
        tip = store.latest(kind=kind,event_id=event)
        heads.append((kind,event,tip['seq'] if tip else 0))
        if kind == 'MODEL': heads.extend(current_path_heads(store,source))
        if kind == 'PWS_OBSERVATION' and pws_material:
            if b['payload'].get('health') != 'HEALTHY':
                raise EvidenceError('PHYSICAL_MATERIAL_PWS_REQUIRES_HEALTHY_QC')
            heads.extend(current_neighborhood_heads(store,source))
    for (kind,event,provider,channel),source in groups.items():
        current = store.latest_source(kind=kind,event_id=event,provider=provider,source_identity=channel)
        if current is None or current['id'] != source['id']:
            raise EvidenceError('PHYSICAL_INPUT_SOURCE_SUPERSEDED')
    return _distinct(heads)


def archive_physical_model(store,record_id,*,rule,base_model_id,feature_id,input_target,coverage_id=None):
    """Attach causal features; only frozen bundle inference applies coefficients."""
    from .strategy_pipeline import _model_inputs,_condition,COVERAGE_VERSION
    from types import SimpleNamespace
    identity(record_id,maximum=100)
    if input_target not in {NEXT_OBSERVATION,UNRESOLVED_EXTREME} or (coverage_id is None) != (input_target==NEXT_OBSERVATION):
        raise EvidenceError('PHYSICAL_TARGET_COVERAGE_MISMATCH')
    request_sha = digest(dict(version=VERSION,rule=asdict(rule),base_model_id=base_model_id,
                             feature_id=feature_id,input_target=input_target,coverage_id=coverage_id))
    model = None
    try: model = store.get(record_id)
    except EvidenceError as exc:
        if str(exc) != 'EVIDENCE_MISSING': raise
    if model:
        if model['kind'] != 'MODEL' or model['body']['payload'].get('request_sha256') != request_sha:
            raise EvidenceError('PHYSICAL_MODEL_REPLAY_CONFLICT')
        if coverage_id is None: return dict(model=model,coverage=None)
        try: finished = store.get(record_id+':coverage')
        except EvidenceError as exc:
            if str(exc) != 'EVIDENCE_MISSING': raise
        else:
            if finished['body']['payload'].get('model_evidence_sha256') != [model['sha256']]:
                raise EvidenceError('PHYSICAL_MODEL_REPLAY_CONFLICT')
            return dict(model=model,coverage=finished)
    now = finite(store.clock()); base = store.get(base_model_id); feature = store.get(feature_id)
    components = _model_inputs(store,rule,(base_model_id,),now,target=input_target)
    if components[0].auxiliary is not None: raise EvidenceError('PHYSICAL_MODEL_CHAIN_UNSUPPORTED')
    parents = [base,feature]; coverage = None
    if coverage_id is not None:
        coverage = store.get(coverage_id); parents.append(coverage); cp = coverage['body']['payload']
        request = SimpleNamespace(model_input_ids=(base_model_id,),observed_input_id=cp.get('observation_id'),coverage_input_id=coverage_id)
        _condition(store,rule,request,finite(cp.get('as_of')),components,available_cutoff=now)
    if model is None:
        payload = dict(version=VERSION,request_sha256=request_sha,rule=asdict(rule),rule_fingerprint=rule.sha256,
            **{k:rule.payload[k] for k in ('station','target_date','family','unit')},
            temperature_input=base['body']['payload']['temperature_input'],
            dependencies=[dict(id=r['id'],sha256=r['sha256']) for r in parents],
            source_truth_independently_attested=False,calibrated_probability=False,financial_authority=False)
        if input_target==NEXT_OBSERVATION:
            context = base['body']['payload'].get('observation_context')
            if type(context) is not dict: raise EvidenceError('PHYSICAL_NEXT_OBSERVATION_CONTEXT_REQUIRED')
            payload['observation_context'] = context
        else:
            payload['remaining_context'] = {k:cp[k] for k in ('observation_id','observation_sha256','accepted_intervals','unresolved_intervals')}
        b = base['body']; ready = finite(store.clock())
        body = dict(provider='ALPHA_PHYSICAL_MODEL',source_identity='physical-model:'+digest([rule.sha256,input_target,
            b['provider'],b['source_identity'],feature['body']['source_identity']]),revision=request_sha,payload=payload,
            observed_at=None,issued_at=b['issued_at'],published_at=None,received_at=b['received_at'],
            evidence_class='SYNTHETIC' if any(r['body']['evidence_class']=='SYNTHETIC' for r in parents) else 'PUBLIC_OBSERVED',source_kind='MODEL')
        # Verify the exact same graph as inference, before the first append.
        prospective = dict(id=record_id,kind='MODEL',event_id=rule.payload['event_id'],seq=max(r['seq'] for r in parents)+1,
                           body=dict(body,available_at=ready))
        heads = current_physical_heads(store,prospective)
        model = store._append(record_id,'MODEL',rule.payload['event_id'],body,ready,ready,expected_heads=heads)
    if coverage is None: return dict(model=model,coverage=None)
    heads = current_physical_heads(store,model); cp = coverage['body']['payload']
    payload = dict(cp,version=COVERAGE_VERSION,as_of=model['body']['available_at'],model_evidence_sha256=[model['sha256']])
    ready = finite(store.clock())
    output = store._append(record_id+':coverage','FEATURES',rule.payload['event_id'],dict(provider='ALPHA_PHYSICAL_MODEL',
        source_identity=model['body']['source_identity']+':coverage',revision=request_sha,payload=payload,
        observed_at=payload['as_of'],issued_at=None,published_at=None,received_at=ready,
        evidence_class=model['body']['evidence_class'],source_kind='FEATURES'),ready,ready,expected_heads=heads)
    return dict(model=model,coverage=output)
