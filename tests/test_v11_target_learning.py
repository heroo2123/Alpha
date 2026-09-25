"""Synthetic target/candidate checks; no label truth or deployment acceptance."""
from copy import deepcopy
from dataclasses import asdict, replace

import pytest

from polymarket_scanner.v11 import target_learning as learning
from polymarket_scanner.v11.datasets import DatasetPlan, ExperimentJournal, build_dataset, verify_dataset
from polymarket_scanner.v11.evidence import EvidenceError, EvidenceStore, canonical, digest
from polymarket_scanner.v11.event_risk import EventContext
from polymarket_scanner.v11.learning_sources import learning_source_view
from polymarket_scanner.v11.model_registry import ActiveModelRegistry
from polymarket_scanner.v11.offline_learning import LearningEnvelope, run_research_fit
from polymarket_scanner.v11.probability import BucketPrediction, NEXT_OBSERVATION
from polymarket_scanner.v11.pws_lead import PWSObservationLead
from polymarket_scanner.v11.strategy_admission import StrategyAdmission, SourceLease
from polymarket_scanner.v11.strategy_pipeline import TemperatureStrategies
from test_v11_certification_rules import setup
from test_v11_learning_capture import bundle, labels
from test_v11_model_artifacts import provenance
from test_v11_physical_inference import rig, observe
from test_v11_pws_lead import rig as lead_rig, report
from test_v11_strategy_pipeline import factory


@pytest.fixture
def conditioned(factory):
    r=factory('SAME_DAY_LATE_LOCK');r['before']=r['store'].pin_read_view()
    r['evaluation']=TemperatureStrategies(r['store']).evaluate('conditioned',r['request'])
    status=r['evaluation']['body']['details']['learning_capture']
    assert status['status']=='CONDITIONED_VECTOR_CAPTURED_LABELS_PENDING'
    r['capture']=r['store'].get(status['capture_id'])
    return r


def arguments(r):
    p=r['evaluation']['body']['details']['prediction']
    return dict(context=r['context'],strategy=r['scope'].strategy,rule=r['rule'],binding=r['binding'],
        prediction=BucketPrediction(canonical(p),digest(p)),
        pinned_bundle=ActiveModelRegistry().pin(scope_key=r['scope'].key,mode='V11_PAPER').bundle,
        model_input_ids=r['request'].model_input_ids,observed_input_id=r['request'].observed_input_id,
        coverage_input_id=r['request'].coverage_input_id,expires_at=r['request'].expires_at)


def join_payout(r):
    ids=labels(r,r['capture'])
    before=r['store'].pin_read_view()
    with learning_source_view(r['store']) as view:
        examples=learning.labeled_target_examples(view,r['capture']['id'],label_ids=ids,city=r['context'].city_id,
            horizon='SAME_DAY',season='AUTUMN')
    assert before==r['store'].pin_read_view()
    return examples


def observation_capture(r,setup,key='pair'):
    observe(r)
    context=EventContext('account',setup[3].city,setup[3].station,r['rule'].payload['event_id'])
    pair=learning.capture_observation_pair(r['store'],key,lead_id='physical-lead',context=context,
        bundle=r['pinned'],without_pws_bundle=r['pinned'])
    return pair,context


def observation_label(r,pair,context,**changes):
    r['now'][0]+=10
    source=report(r,key='next-receipt',value=72)
    score=PWSObservationLead(r['store']).score_first_received_report('receipt-score',observation_id='physical-lead')
    p=r['rule'].payload
    payload=dict(context=dict(station=p['station'],city=context.city_id,local_date=p['target_date'],
        target=NEXT_OBSERVATION,rule_fingerprint=r['rule'].sha256),label_version='synthetic-v1',
        decision_target=NEXT_OBSERVATION,target_identity=pair['body']['details']['target_identity'],
        knowable_at=r['now'][0],value=72,evidence_type='SYNTHETIC',source_received_at=source['body']['received_at'],
        receipt_score_id=score['id'],receipt_score_sha256=score['sha256'])
    payload.update(changes)
    return r['store'].capture('observation-label',event_id=p['event_id'],kind='LABEL',provider='TEST_ONLY',source_identity='next',
        revision='synthetic-v1',payload=payload,evidence_class='SYNTHETIC')


def test_same_day_all_buckets_preserve_exact_condition_and_do_not_change_admission(conditioned):
    r=conditioned;s=r['store'];d=r['capture']['body']['details'];result=r['evaluation']['body']['details']
    assert result['reason']=='CONSERVATIVE_EV_NOT_ABOVE_THRESHOLD' and result['proposal'] is None
    assert len(d['rows'])==3 and not s.records(kind='LABEL')
    assert s.latest(kind='FEATURES',event_id=r['context'].event_id)['id']=='coverage'
    assert StrategyAdmission(s).revalidate('pin',context=r['context'],rule=r['rule'],binding=asdict(r['binding']),
        strategies=('SAME_DAY_LATE_LOCK',))['strategy']=='SAME_DAY_LATE_LOCK'
    for row in d['rows']:
        f=s.get(row['feature_id']);decision=s.get(row['decision_id'])['body']
        assert f['kind']=='LEARNING_FEATURES'
        assert f['body']['payload']['context']==d['conditioning']
        assert {x['id'] for x in f['body']['payload']['dependencies']}=={'model2','official2','coverage'}
        assert decision['explanation']['economic_qualification_evaluated'] is False
        assert decision['explanation']['prediction']==result['prediction']
    examples=join_payout(r);e=examples[0].payload
    assert e['conditioning']['remaining_coverage']==result['prediction']['remaining_coverage']
    assert e['prediction_at']<e['decision_at'] and not e['label_independently_attested']
    assert {p['id'] for p in e['provenance']} >= {'model2','official2','coverage'}


def test_learning_snapshot_cannot_satisfy_an_operational_feature_lease(conditioned):
    r=conditioned;row=r['capture']['body']['details']['rows'][0]
    leases=tuple(SourceLease(row['feature_id'],'FEATURES',120.) if x.role=='FEATURES' else x for x in r['admission_kw']['source_leases'])
    with pytest.raises(EvidenceError,match='SOURCE_NOT_CAUSAL_OR_SCOPED'):
        StrategyAdmission(r['store']).pin('bad-learning-source',**dict(r['admission_kw'],source_leases=leases))


def test_completed_capture_replays_without_new_timestamps_or_future_source_substitution(conditioned):
    r=conditioned;kw=arguments(r);before=r['store'].pin_read_view();r['now'][0]+=100
    assert learning.capture_conditioned_vector(r['store'],r['capture']['id'],**kw)==r['capture']
    assert r['store'].pin_read_view()==before
    with pytest.raises(EvidenceError,match='TIME_MISMATCH'):
        learning.capture_conditioned_vector(r['store'],'late',**kw)


def test_partial_learning_feature_resumes_without_renewal_or_changing_live_feature_heads(conditioned,monkeypatch):
    r=conditioned;s=r['store'];kw=arguments(r);head=s.latest(kind='FEATURES',event_id=r['context'].event_id)
    with monkeypatch.context() as patch:
        patch.setattr(s,'decision',lambda *a,**k:(_ for _ in ()).throw(RuntimeError('interrupted')))
        with pytest.raises(RuntimeError):learning.capture_conditioned_vector(s,'partial',**kw)
    original=s.records(kind='LEARNING_FEATURES')[-1];r['now'][0]+=1
    out=learning.capture_conditioned_vector(s,'partial',**kw)
    assert len(out['body']['details']['rows'])==3 and s.get(original['id'])==original
    assert s.latest(kind='FEATURES',event_id=r['context'].event_id)==head


@pytest.mark.parametrize('fault',['probability','observed_value','coverage','auxiliary','bundle','input'])
def test_changed_prediction_or_condition_cannot_be_captured_as_original(conditioned,fault):
    r=conditioned;kw=arguments(r);p=kw['prediction'].payload
    if fault=='probability':p['buckets'][0]['point']+=.01
    if fault=='observed_value':p['observed_constraint']['whole_degree_value']+=1
    if fault=='coverage':p['remaining_coverage']['accepted_intervals'][0][1]-=1
    if fault=='auxiliary':p['model_inputs'][0]['auxiliary']={}
    if fault=='bundle':kw['binding']=replace(kw['binding'],bundle_sha256='e'*64)
    if fault=='input':kw['model_input_ids']=('model1',)
    kw['prediction']=BucketPrediction(canonical(p),digest(p));before=r['store'].pin_read_view()
    with pytest.raises(EvidenceError):learning.capture_conditioned_vector(r['store'],'bad',**kw)
    assert before==r['store'].pin_read_view()


def test_dataset_uses_original_prediction_cutoff_and_unconditioned_fit_remains_gated(conditioned,bundle,tmp_path):
    r=conditioned;examples=join_payout(r);d=r['capture']['body']['details']
    envelope=LearningEnvelope('model-1',tuple(d['model_feature_mapping']['model-1']),'lower_cut','upper_cut',
        (0.,1.),(.2,1.),'brier',2,2,2,0.,.01,10.,100_000,100,23)
    cutoff=r['now'][0]+1
    plan=DatasetPlan('conditioned-plan',learning.PAYOUT,d['feature_schema_sha256'],0.,cutoff,cutoff+1,cutoff+2,envelope.sha256)
    dataset=build_dataset(examples,plan,as_of=r['now'][0]);checked=verify_dataset(dataset)
    assert checked['counts']['TRAIN']['city_days']==1
    assert checked['counts']['TRAIN']['decision_range']==[d['conditioning']['inference_cutoff']]*2
    root=tmp_path/'research';root.mkdir(mode=0o700)
    journal=ExperimentJournal(EvidenceStore(root/'research.sqlite','CHALLENGER:conditioned',clock=lambda:r['now'][0]),'conditioned')
    journal.register('plan',plan);before=journal.store.pin_read_view();parent=bundle[0].pin(bundle[3])
    with pytest.raises(EvidenceError,match='CONDITIONED_TARGET_UNSUPPORTED'):
        run_research_fit(artifacts=bundle[0],journal=journal,plan_record_id='plan',dataset=dataset,parent_bundle_sha256=bundle[3],
            envelope=envelope,provenance=dict(provenance(),created_at=r['now'][0],run_id='must-not-fit',
                comparison_policy_sha256=envelope.sha256),run_id='must-not-fit')
    assert journal.store.pin_read_view()==before and bundle[0].pin(bundle[3])==parent


def test_known_label_cannot_be_made_new_by_a_later_archive_receipt(conditioned):
    r=conditioned;ids=labels(r,r['capture']);old=r['store'].get(next(iter(ids.values())));p=deepcopy(old['body']['payload'])
    p.update(label_version='correction',knowable_at=r['evaluation']['body']['recorded_at']-1)
    changed=r['store'].capture('backfilled',event_id=old['event_id'],kind='LABEL',provider='TEST_ONLY',source_identity='backfilled',
        revision='correction',payload=p,evidence_class='SYNTHETIC')
    ids[p['target_identity']['market_id']]=changed['id']
    with pytest.raises(EvidenceError,match='KNOWN_BEFORE_CAPTURE'):
        learning.labeled_target_examples(r['store'],r['capture']['id'],label_ids=ids,city=r['context'].city_id,horizon='SAME_DAY',season='AUTUMN')


def test_physical_pair_reaches_read_only_dataset_with_raw_madis_awc_and_one_observation(rig,setup):
    r=rig;pair,context=observation_capture(r,setup);d=pair['body']['details']
    assert d['pair_complete'] and d['paired_target_count']==1 and d['independent_sample_count'] is None
    assert not r['store'].records(kind='LABEL')
    assert d['target_identity']['clock']=='FIRST_ALPHA_RECEIPT'
    label=observation_label(r,pair,context);before=r['store'].pin_read_view()
    with learning_source_view(r['store']) as view:
        examples=learning.labeled_target_examples(view,pair['id'],label_ids=label['id'],city=context.city_id,horizon='NEXT_120S',season='AUTUMN')
    assert r['store'].pin_read_view()==before and len(examples)==2
    a,b=(e.payload for e in examples)
    assert a['target_identity']==b['target_identity'] and a['label_id']==b['label_id']
    assert a['label_receipt_provenance']==b['label_receipt_provenance']
    assert a['label_receipt_provenance']['official_id']=='next-receipt'
    assert not a['label_receipt_provenance']['target_is_true_next_published_report']
    assert a['conditioning']['ablation']=='with_pws' and b['conditioning']['ablation']=='without_pws'
    assert a['values']['pws_weighted_median_c'] is not None and b['values']['pws_weighted_median_c'] is None
    first={n['id']:n for n in a['source_derivation']['manifest']['nodes']}
    second={n['id']:n for n in b['source_derivation']['manifest']['nodes']}
    assert {'aux-madis','aux-normalized','aux-qc','aux-awc','aux-base','aux-anchor'}<=first.keys()
    assert not {'aux-madis','aux-normalized','aux-qc'} & second.keys()
    assert first['aux-madis']['received_at']==r['store'].get('aux-madis')['body']['received_at']
    assert first['aux-awc']['sha256']==second['aux-awc']['sha256']
    cutoff=r['now'][0]+1
    plan=DatasetPlan('observation-plan',NEXT_OBSERVATION,a['feature_schema_sha256'],0.,cutoff,cutoff+1,cutoff+2,'f'*64)
    assert verify_dataset(build_dataset(examples,plan,as_of=r['now'][0]))['counts']['TRAIN']==dict(
        examples=2,events=1,city_days=1,decision_range=[a['prediction_at']]*2)


@pytest.mark.parametrize('fault',['clock','unit','anchor','score','value','window'])
def test_receipt_label_cannot_be_reused_for_published_target_or_different_pair(rig,setup,fault):
    r=rig;pair,context=observation_capture(r,setup);target=deepcopy(pair['body']['details']['target_identity']);changes={}
    if fault=='clock':target['clock']='PROVIDER_PUBLICATION'
    if fault=='unit':target['unit']='C' if target['unit']=='F' else 'F'
    if fault=='anchor':target['official_anchor_sha256']='f'*64
    if fault=='score':changes['receipt_score_sha256']='e'*64
    if fault=='value':changes['value']=71
    if fault=='window':target['window_end']+=1
    label=observation_label(r,pair,context,target_identity=target,**changes)
    with pytest.raises(EvidenceError):
        learning.labeled_target_examples(r['store'],pair['id'],label_ids=label['id'],city=context.city_id,horizon='NEXT_120S',season='AUTUMN')


def test_observation_pair_partial_commit_recovers_and_completed_pair_is_historical(rig,setup,monkeypatch):
    r=rig;observe(r);context=EventContext('account',setup[3].city,setup[3].station,r['rule'].payload['event_id'])
    kwargs=dict(lead_id='physical-lead',context=context,bundle=r['pinned'],without_pws_bundle=r['pinned'])
    original=learning._capture;calls=[]
    def interrupted(*args,**kw):
        if calls:raise RuntimeError('interrupt second member')
        out=original(*args,**kw);calls.append(out);return out
    with monkeypatch.context() as patch:
        patch.setattr(learning,'_capture',interrupted)
        with pytest.raises(RuntimeError):learning.capture_observation_pair(r['store'],'pair',**kwargs)
    r['now'][0]+=1
    result=learning.capture_observation_pair(r['store'],'pair',**kwargs)
    assert r['store'].get(calls[0]['id'])==calls[0] and result['body']['details']['pair_complete']
    before=r['store'].pin_read_view();r['now'][0]+=1000
    assert learning.capture_observation_pair(r['store'],'pair',**kwargs)==result and r['store'].pin_read_view()==before
