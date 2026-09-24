"""Causal whole-vector forecast capture for the existing offline dataset path.

This records every YES bucket in one supported event, independent of entry
economics. It creates no labels, fills, training run or model authority. Other
prediction targets need their own feature/conditioning contracts.
"""
from dataclasses import asdict
import time

from .datasets import FeatureDefinition, FeatureSchema, archive_features, build_example
from .evidence import EvidenceError, ReleaseBinding, digest, finite, identity
from .event_risk import EventContext
from .probability import BucketPrediction, FINAL_EXTREME, _partition
from .rules import RuleFingerprint


VERSION='alpha_v11_forecast_learning_capture_v1'
TARGET='FINAL_CONTRACT_PAYOUT'


def _get(store,key):
    try:return store.get(key)
    except EvidenceError as exc:
        if str(exc)!='EVIDENCE_MISSING':raise
    return None


def capture_forecast_vector(store,record_id,*,context,rule,binding,prediction,model_input_ids,expires_at):
    identity(record_id,maximum=110)
    if (not isinstance(rule,RuleFingerprint) or not isinstance(binding,ReleaseBinding) or not isinstance(context,EventContext)
            or not isinstance(prediction,BucketPrediction) or type(model_input_ids) is not tuple
            or not 1<=len(model_input_ids)<=16 or len(set(model_input_ids))!=len(model_input_ids)):
        raise EvidenceError('LEARNING_CAPTURE_TYPED_SCOPE_REQUIRED')
    p=prediction.payload;rp=rule.payload
    request_sha=digest(dict(context=asdict(context),rule=asdict(rule),binding=asdict(binding),prediction_sha256=prediction.sha256,
                           model_input_ids=model_input_ids,expires_at=expires_at,version=VERSION))
    old=_get(store,record_id)
    if old:
        if old['body'].get('details',{}).get('request_sha256')!=request_sha: raise EvidenceError('LEARNING_CAPTURE_REPLAY_CONFLICT')
        return old
    if (p['target']!=FINAL_EXTREME or p.get('observed_constraint') is not None or p.get('remaining_coverage') is not None
            or p['rule_fingerprint']!=rule.sha256 or binding.rule_fingerprint!=rule.sha256
            or p['bundle_sha256']!=binding.bundle_sha256
            or (context.event_id,context.station_id)!=(rp['event_id'],rp['station'])):
        raise EvidenceError('LEARNING_UNCONDITIONED_FORECAST_CONTRACT_REQUIRED')
    now=finite(store.clock());expiry=finite(expires_at);cutoff=finite(p['as_of'])
    if not cutoff<=now<expiry: raise EvidenceError('LEARNING_CAPTURE_EXPIRED_OR_FUTURE')
    deadline=time.monotonic()+2.
    sources=[store.get(key) for key in model_input_ids];by_hash={s['sha256']:s for s in sources}
    components=sorted(p['model_inputs'],key=lambda c:c['model_id'])
    if (len(components)!=len(sources) or len({c['model_id'] for c in components})!=len(components)
            or {c['evidence_sha256'] for c in components}!=set(by_hash)):
        raise EvidenceError('LEARNING_CAPTURE_EXACT_MODEL_SET')
    if sum(len(c['members']) for c in components)+2>128: raise EvidenceError('LEARNING_CAPTURE_FEATURE_BOUND')
    values={};features=[];mapping={};versions={'prediction':prediction.sha256,'rule':rule.sha256}
    for i,c in enumerate(components):
        s=by_hash[c['evidence_sha256']];b=s['body'];v=b.get('payload',{}).get('temperature_input',{})
        if (s['kind']!='MODEL' or s['event_id']!=rp['event_id']
                or b.get('evidence_class') not in {'PUBLIC_OBSERVED','SYNTHETIC'}
                or b['available_at']>cutoff or b['issued_at'] is None or b['issued_at']!=c['issued_at']
                or b['received_at']!=c['received_at'] or b['available_at']!=c['feature_ready_at']
                or v.get('members')!=c['members'] or v.get('model_id')!=c['model_id']
                or v.get('target_sha256')!=c['target_sha256']):
            raise EvidenceError('LEARNING_CAPTURE_MODEL_LINEAGE_OR_CUTOFF')
        names=[]
        for j,value in enumerate(c['members']):
            name=f'model_{i}_member_{j:03}';names.append(name);values[name]=finite(value,nonnegative=False)
            features.append(FeatureDefinition(name,rp['unit'],'forecast_members',-250.,250.,False))
        mapping[c['model_id']]=names;versions[f'model_{i}']=c['model_id']
    features.extend((FeatureDefinition('lower_cut',rp['unit'],'contract_quantization',-251.,251.,True),
                     FeatureDefinition('upper_cut',rp['unit'],'contract_quantization',-251.,251.,True)))
    schema=FeatureSchema('forecast-cuts:'+digest([mapping,rp['unit'],rp['family'],p['model_quantization_hypothesis']]),tuple(features))
    # One target per mutually exclusive bucket; complementary NO claims do not
    # inflate the event's training count. Their probabilities remain available.
    rows=[];buckets=_partition(rule)
    if {b['market_id'] for b in p['buckets']}!={b['market_id'] for b in buckets}:
        raise EvidenceError('LEARNING_CAPTURE_COMPLETE_VECTOR_REQUIRED')
    for bucket in buckets:
        if time.monotonic()>=deadline: raise EvidenceError('LEARNING_CAPTURE_TIME_BOUND')
        if store.clock()>=expiry: raise EvidenceError('LEARNING_CAPTURE_EXPIRED_OR_FUTURE')
        target=dict(market_id=bucket['market_id'],condition_id=bucket['condition_id'],token_id=bucket['yes_token'],side='YES')
        prefix='forecast-data:'+digest([record_id,target]);feature_id=prefix+':features';decision_id=prefix+':prediction'
        current=dict(values,lower_cut=None if bucket['lower'] is None else bucket['lower']-.5,
                     upper_cut=None if bucket['upper'] is None else bucket['upper']+.5)
        feature=_get(store,feature_id)
        if feature is not None:
            fp=feature['body'].get('payload',{})
            if (fp.get('source_versions')!=versions or fp.get('values')!=current or fp.get('feature_schema_sha256')!=schema.sha256
                    or fp.get('dependencies')!=[dict(id=s['id'],sha256=s['sha256']) for s in sources]):
                raise EvidenceError('LEARNING_FEATURE_REPLAY_CONFLICT')
        else:
            feature=archive_features(store,feature_id,event_id=rp['event_id'],schema=schema,values=current,
                evidence_ids=model_input_ids,source_versions=versions)
        probability=prediction.binary(bucket['market_id'],'YES',required_target=FINAL_EXTREME)
        explanation=dict(version=VERSION,request_sha256=request_sha,target_identity=target,prediction_sha256=prediction.sha256,
            inference_cutoff=cutoff,point=probability.point,lower=probability.lower,upper=probability.upper,
            calibration_status=p['calibration_status'],selection='ALL_SUPPORTED_PREDICTIONS',
            selection_scope='ALL_BUCKETS_OF_THIS_EVALUATED_EVENT',global_universe_coverage_verified=False,
            economic_qualification_evaluated=False,financial_authority=False)
        decision=_get(store,decision_id)
        if decision is not None:
            if (decision['body'].get('explanation')!=explanation or decision['body'].get('binding')!=asdict(binding)
                    or decision['body'].get('evidence')!=[dict(id=feature['id'],sha256=feature['sha256'])]):
                raise EvidenceError('LEARNING_PREDICTION_REPLAY_CONFLICT')
        else:
            decision=store.decision(decision_id,event_id=rp['event_id'],strategy='FUTURE_FORECAST',binding=binding,
                evidence_ids=(feature['id'],),feature_ready_at=feature['body']['available_at'],valuation_type='SETTLEMENT',target=TARGET,
                outcome='GATED',reason='FORECAST_ONLY_NO_EXECUTABLE_ECONOMICS',explanation=explanation,expires_at=expiry)
        rows.append(dict(target_identity=target,decision_id=decision['id'],decision_sha256=decision['sha256'],
                         feature_id=feature['id'],feature_sha256=feature['sha256']))
    return store.audit(record_id,event_id=rp['event_id'],kind='MEASUREMENT',details=dict(
        version=VERSION,request_sha256=request_sha,context=asdict(context),rule=asdict(rule),prediction_sha256=prediction.sha256,
        binding=asdict(binding),inference_cutoff=cutoff,feature_schema_sha256=schema.sha256,model_feature_mapping=mapping,
        target=TARGET,selection='ALL_SUPPORTED_PREDICTIONS',selection_scope='ALL_BUCKETS_OF_THIS_EVALUATED_EVENT',
        global_universe_coverage_verified=False,rows=rows,complete_event_vector=True,labels_created=False,
        financial_authority=False,training_or_promotion_started=False),evidence_ids=model_input_ids)


def labeled_examples(store,capture_id,*,label_ids,city,horizon,season,prior_exposure='DEVELOPMENT'):
    """Join explicitly supplied exact labels; never fetch or fabricate one."""
    capture=store.get(capture_id);d=capture['body'].get('details',{})
    if (capture['kind']!='MEASUREMENT' or d.get('version')!=VERSION or not d.get('complete_event_vector')
            or type(label_ids) is not dict or set(label_ids)!={r['target_identity']['market_id'] for r in d['rows']}
            or city!=d['context']['city_id']):
        raise EvidenceError('LEARNING_COMPLETE_VECTOR_LABEL_SET_REQUIRED')
    p=RuleFingerprint(**d['rule']).payload;examples=[]
    for row in d['rows']:
        decision=store.get(row['decision_id']);feature=store.get(row['feature_id'])
        if decision['sha256']!=row['decision_sha256'] or feature['sha256']!=row['feature_sha256']:
            raise EvidenceError('LEARNING_CAPTURE_CHILD_BINDING')
        examples.append(build_example(store,decision_id=decision['id'],feature_id=feature['id'],
            label_id=label_ids[row['target_identity']['market_id']],station=p['station'],city=city,local_date=p['target_date'],
            horizon=horizon,season=season,target=TARGET,selection='ALL_SUPPORTED_PREDICTIONS',prior_exposure=prior_exposure))
    if sum(e.payload['label_value'] for e in examples)!=1:
        raise EvidenceError('LEARNING_MUTUALLY_EXCLUSIVE_PAYOUT_LABELS_REQUIRED')
    return tuple(examples)
