import asyncio
from copy import deepcopy
from dataclasses import replace
import fcntl
import os
from types import SimpleNamespace

import httpx
import pytest

from polymarket_scanner.v11 import preparation_runtime as prep
from polymarket_scanner.v11 import candidate_assembly as app
from polymarket_scanner.v11.book_inputs import PROVIDER as BOOK_PROVIDER
from polymarket_scanner.v11.evidence import EvidenceError
from polymarket_scanner.v11.nowcast_features import PHYSICAL_SCHEMA, archive_nowcast_features
from polymarket_scanner.v11.probability import NEXT_OBSERVATION, UNRESOLVED_EXTREME
from polymarket_scanner.v11.pws_lead import PWSObservationLead
from polymarket_scanner.v11.request_assembly import SourceSelector
from polymarket_scanner.v11.weather_sources import normalize_weather_capture
from test_v11_physical_inference import rig as physical_rig, lead_rig, inputs as physical_inputs, make_bundle
from test_v11_certification_rules import setup
from test_v11_gefs_sources import gefs
from test_v11_remaining_forecast import prepare, conditioned
from test_v11_runtime_health import monitor, ready, advance
from test_v11_candidate_assembly import scoped_plan, synthetic_clock, transport
from test_v11_request_assembly import inputs, target
from test_v11_strategy_pipeline import factory
from test_v11_model_artifacts import bundle
from test_v11_pws_admission import joined


def selector(row,role,age=300.):
    b=row['body'];return SourceSelector(role,b['provider'],b['source_identity'],age)


def make_physical_plan(r,metadata,**kw):
    return prep.PhysicalPreparation('physical',r['rule'],metadata,selector(r['base'],'MODEL'),NEXT_OBSERVATION,'KATL',
        300.,3600.,900.,pws=SourceSelector('PWS','ALPHA_PWS_QC','KATL',300.),**kw)


def make_worker(r,monkeypatch,plans,**settings):
    if 'context' not in r:
        r['context']=SimpleNamespace(event_id=r['rule'].payload['event_id'],station_id='KATL')
    health=monitor(r,monkeypatch);ready(r,health)
    return prep.PreparationWorker(r['store'],health,prep.PreparationSettings(tuple(plans),**settings))


@pytest.fixture
def prepared(physical_rig,setup,monkeypatch):
    r=physical_rig;s=r['store']
    normalized=normalize_weather_capture(s,'aux-awc',record_id='awc-normalized',station='KATL')
    old=r['anchor'];b=old['body']
    anchor=s.capture('normalized-anchor',event_id=old['event_id'],kind=old['kind'],provider=b['provider'],source_identity=b['source_identity'],
        revision='normalized-anchor',observed_at=b['observed_at'],payload=b['payload'],evidence_class='SYNTHETIC')
    b=r['base']['body'];p=deepcopy(b['payload']);p['observation_context'].update(official_anchor_id=anchor['id'],official_anchor_sha256=anchor['sha256'])
    p['dependencies']=[dict(id=anchor['id'],sha256=anchor['sha256'])]
    r['base']=s.capture('normalized-base',event_id=old['event_id'],kind='MODEL',provider=b['provider'],source_identity=b['source_identity'],
        revision='normalized-base',issued_at=b['issued_at'],payload=p,evidence_class='SYNTHETIC')
    r['anchor']=anchor;r['normalized']=normalized;r['prep_plan']=make_physical_plan(r,setup[3])
    r['worker']=make_worker(r,monkeypatch,(r['prep_plan'],))
    return r


def finish(r,prefix='stage'):
    rows=[]
    for i in range(4):
        rows.append(r['worker'].step(prefix+str(i)))
        if rows[-1]['body']['details']['outcome']=='PREPARATION_COMPLETE':break
        advance(r,.2)
    assert rows[-1]['body']['details']['outcome']=='PREPARATION_COMPLETE',rows[-1]['body']['details']
    return rows[-1]


def test_normalized_raw_sources_schedule_true_paired_observation_with_one_cutoff(prepared):
    r=prepared;s=r['store'];result=finish(r);d=result['body']['details'];o=d['outputs']
    a=s.get(o['with_feature_id']);b=s.get(o['without_feature_id'])
    assert a['body']['payload']['context']['as_of']==b['body']['payload']['context']['as_of']
    assert a['body']['available_at']<b['body']['available_at']
    assert [v['id'] for v in a['body']['payload']['dependencies']]==['awc-normalized',r['qc']['id']]
    for f in PHYSICAL_SCHEMA.features:
        assert b['body']['payload']['values'][f.name] is None if f.family=='PWS' else a['body']['payload']['values'][f.name]==b['body']['payload']['values'][f.name]
    report=PWSObservationLead(s).observe('scheduled-lead',rule=r['rule'],binding=replace(r['kw']['binding'],bundle_sha256=r['pinned'].sha256),
        policy=replace(r['kw']['policy'],max_pws_age_seconds=300.),official_id=r['anchor']['id'],pws_id=r['qc']['id'],
        model_ids=(o['with_model_id'],),without_pws_model_ids=(o['without_model_id'],),bundle=r['pinned'],without_pws_bundle=r['pinned'])['body']['details']
    assert report['with_pws']['buckets']!=report['without_pws']['buckets']
    assert report['paired_provenance']['with_pws']['non_pws_leaves']==report['paired_provenance']['without_pws']['non_pws_leaves']
    assert report['settlement_prediction'] is None and report['proposal'] is None
    for without,label in [(False,'with'),(True,'without')]:
        source=r['prep_plan'].output_sources(300.,without_pws=without)[0]
        assert s.latest_source(kind='MODEL',event_id=r['rule'].payload['event_id'],provider=source.provider,source_identity=source.source_identity)['id']==o[label+'_model_id']


def test_completed_command_and_unchanged_new_command_never_renew_prepared_evidence(prepared):
    r=prepared;last=finish(r);s=r['store'];ids=last['body']['details']['outputs'];before={key:s.get(value) for key,value in ids.items()}
    head=s.pin_read_view();advance(r)
    assert r['worker'].step('stage3')==last and s.pin_read_view()==head
    again=r['worker'].step('different-command')['body']['details']
    assert again['outcome']=='UNCHANGED_INPUTS_NO_RECEIPT_RENEWAL' and again['previous_outcome']=='COMPLETE'
    assert {key:s.get(value) for key,value in ids.items()}==before
    assert s.get(ids['with_model_id'])['body']['received_at']==r['base']['body']['received_at']


def test_interruption_after_feature_append_recovers_exact_saved_feature(prepared,monkeypatch):
    r=prepared;w=r['worker'];original=w._save
    def fail(key,state,**details):
        if details.get('outcome')=='PREPARATION_STAGE_RECORDED':raise OSError('simulated interruption')
        return original(key,state,**details)
    monkeypatch.setattr(w,'_save',fail)
    with pytest.raises(OSError):w.step('interrupted')
    state=w._head()['body']['details']['state'];active=state['active'];assert active['stage']==0
    # The feature ID is pinned even when the worker result was not persisted.
    feature=r['store'].latest_source(kind='FEATURES',event_id=r['rule'].payload['event_id'],
        provider='ALPHA_V11_FEATURES',source_identity=r['prep_plan'].feature_source())
    advance(r,.2);w=prep.PreparationWorker(r['store'],w.health,w.settings);r['worker']=w
    row=w.step('interrupted');assert row['body']['details']['outputs']['with_feature_id']==feature['id']
    assert r['store'].get(feature['id'])==feature
    for i in range(3):row=w.step('recovery'+str(i));advance(r,.2)
    assert row['body']['details']['outcome']=='PREPARATION_COMPLETE'


def test_new_source_during_partial_pair_gates_then_uses_new_identity(prepared):
    r=prepared;w=r['worker'];first=w.step('first')['body']['details'];f=first['outputs']['with_feature_id']
    b=r['store'].get('aux-awc')['body'];advance(r,.2)
    r['store'].capture('new-awc',event_id=r['rule'].payload['event_id'],kind='OFFICIAL_OBSERVATION',provider=b['provider'],
        source_identity=b['source_identity'],revision='new',observed_at=b['observed_at'],payload=b['payload'],evidence_class='SYNTHETIC')
    second=w.step('second')['body']['details'];assert second['reason']=='PREPARATION_SELECTED_SOURCE_CHANGED'
    assert not second['state']['active'] and r['store'].get(f)
    third=w.step('third')['body']['details'];assert third['outputs']['with_feature_id']!=f
    assert third['state']['active']['inputs']['channels']!=first['state']['active']['inputs']['channels']


def test_expired_partial_features_do_not_refresh_under_unchanged_source_ids(prepared):
    r=prepared;w=r['worker'];w.step('first');w.step('second');advance(r,301.)
    row=w.step('third')['body']['details'];assert row['outcome']=='PREPARATION_SOURCE_GATED'
    assert not row['state']['active']
    rows=r['store'].records(kind='FEATURES');again=w.step('fourth')['body']['details']
    assert again['outcome']=='PREPARATION_SOURCE_GATED' and again['reason']=='PREPARATION_REQUIRED_SOURCE_STALE'
    assert r['store'].records(kind='FEATURES')==rows


def test_clock_loss_defers_preparations_and_retains_exact_partial_state(prepared):
    r=prepared;w=r['worker'];w.step('first');active=w._head()['body']['details']['state']['active'];r['sync'][0]=False
    d=w.step('clock-loss')['body']['details'];assert d['outcome']=='DEFERRED_CLOCK_UNHEALTHY' and d['state']['active']==active


def test_capture_overflow_is_gated_without_dropping_old_inputs(prepared):
    r=prepared;w=prep.PreparationWorker(r['store'],r['worker'].health,replace(r['worker'].settings,maximum_captures=1))
    b=r['store'].get('aux-awc')['body'];r['store'].capture('one-more',event_id=r['rule'].payload['event_id'],kind='OFFICIAL_OBSERVATION',
        provider=b['provider'],source_identity=b['source_identity'],revision='2',payload=b['payload'],evidence_class='SYNTHETIC')
    d=w.step('overflow')['body']['details'];assert d['reason']=='PREPARATION_CAPTURE_WINDOW_OVERFLOW' and not d['state']['active']


def test_preparation_lock_config_and_mutation_do_not_replace_existing_state(prepared):
    r=prepared;w=r['worker'];w.step('first');before=w._head()
    other=prep.PreparationWorker(r['store'],w.health,replace(w.settings,maximum_captures=31))
    with pytest.raises(EvidenceError,match='CONFIGURATION_CHANGED'):other.step('other')
    assert w._head()==before
    fd=os.open(str(r['store'].path)+'.preparation.lock',os.O_WRONLY)
    try:
        fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
        with pytest.raises(EvidenceError,match='ALREADY_RUNNING'):w.step('locked')
    finally:os.close(fd)


def test_remaining_worker_preserves_gefs_path_and_exact_coverage_then_deduplicates(gefs,monkeypatch):
    r=gefs;path,official=prepare(r);plan=prep.RemainingPreparation('remaining',r['plan'],selector(official,'OFFICIAL'))
    w=make_worker(r,monkeypatch,(plan,));d=w.step('remaining')['body']['details'];assert d['outcome']=='PREPARATION_COMPLETE',d
    s=r['store'];pair=dict(model=s.get(d['outputs']['model_id']),coverage=s.get(d['outputs']['coverage_id']))
    components,observed,coverage=conditioned(r,pair)
    assert len(components[0].members)==31 and observed.evidence_sha256==official['sha256']
    assert pair['model']['body']['received_at']==path['body']['received_at']
    assert w.step('new')['body']['details']['outcome']=='UNCHANGED_INPUTS_NO_RECEIPT_RENEWAL'


@pytest.mark.parametrize('bad',[{'maximum_seconds':0.},{'maximum_captures':33},{'plans':()}])
def test_preparation_settings_are_explicitly_bounded(prepared,bad):
    with pytest.raises(EvidenceError):replace(prepared['worker'].settings,**bad)


def test_same_output_channel_cannot_have_conflicting_preparation_plans(prepared):
    p=prepared['prep_plan']
    with pytest.raises(EvidenceError,match='OUTPUT_CHANNEL_CONFLICT'):prep.PreparationSettings((p,replace(p,name='duplicate')))


def test_finite_candidate_runs_preparation_stages_and_keeps_other_workers(factory,setup,monkeypatch):
    r=factory('SAME_DAY_LATE_LOCK');s=r['store'];metadata=setup[3]
    raw=s.capture('candidate-awc',event_id=r['context'].event_id,kind='OFFICIAL_OBSERVATION',provider='NOAA_AWC',source_identity='KATL',revision='one',
        observed_at=r['now'][0]-1,payload=dict(response=[dict(icaoId='KATL',obsTime=r['now'][0]-1,temp=20.)]),evidence_class='SYNTHETIC')
    normalize_weather_capture(s,raw['id'],record_id='candidate-normalized',station='KATL')
    old=s.get('official2');b=old['body'];anchor=s.capture('candidate-anchor',event_id=old['event_id'],kind=old['kind'],provider=b['provider'],
        source_identity=b['source_identity'],revision='candidate',observed_at=b['observed_at'],payload=b['payload'],evidence_class='SYNTHETIC')
    p=deepcopy(s.get('coverage')['body']['payload']);p.update(observation_id=anchor['id'],observation_sha256=anchor['sha256'])
    coverage=s.capture('candidate-coverage',event_id=old['event_id'],kind='FEATURES',provider='fixture',source_identity='candidate-coverage',
        revision='candidate',payload=p,evidence_class='SYNTHETIC')
    plan=prep.PhysicalPreparation('physical',r['rule'],metadata,selector(s.get('model2'),'MODEL'),UNRESOLVED_EXTREME,'KATL',300.,3600.,900.,
        coverage=selector(coverage,'FEATURES'),pair_without_pws=False)
    lane=app.TemperatureLane('temperature',inputs(r),(target(r),),'fixture',r['request'].valuation_policy,10.)
    cfg=scoped_plan(r,lane);cfg=replace(cfg,preparations=prep.PreparationSettings((plan,)),candidate=replace(cfg.candidate,maximum_jobs=4))
    synthetic_clock(r,monkeypatch);calls=[]
    async def go():
        async with httpx.AsyncClient(transport=transport(r,calls)) as client:
            candidate=app.assemble_candidate(s,client,cfg,generation='prepared-candidate');ready(r,candidate.runtime.health)
            row=await candidate.run('candidate-preparation')
            assert candidate.preparations.health is candidate.runtime.health
            return row
    d=asyncio.run(go())['body']['details'];jobs=d['worker_results']
    assert [j['kind'] for j in jobs]==['CENSUS','DISCOVERY','AUDIT','INPUT_PREPARATION'],d
    assert jobs[-1]['outcome']=='PREPARATION_STAGE_RECORDED',d
    assert len(d['runtime_ids'])>=4 and d['all_async_jobs_drained'] and not d['real_orders_sent']
    assert not s.records(kind='TRADE')


def test_scheduled_models_flow_through_protected_candidate_pws_and_payout_scopes(joined,setup,tmp_path,monkeypatch):
    from polymarket_scanner.v11 import certification,model_registry
    from test_v11_certification_rules import approve_fixture
    from test_v11_model_governance import promote
    r=joined;s=r['store'];r.update(physical_inputs(r,setup[3],official_key='anchor',base_key='ablation-model'))
    observation,obs_store=make_bundle(tmp_path/'scheduled-observation',r['rule'],version='lead-v1')
    payout,pay_store=make_bundle(tmp_path/'scheduled-payout',r['rule'],width=3,target=UNRESOLVED_EXTREME)
    class Reader:
        def pin(self,key):return observation if key==observation.sha256 else payout if key==payout.sha256 else None
    reviews=[]
    for prefix,scope,pinned,objects in [('scheduled-obs:',r['observation_scope'],observation,obs_store),('scheduled-pay:',r['payout_scope'],payout,pay_store)]:
        review=approve_fixture(monkeypatch,(s,setup[1],scope,setup[3],r['now']),stage='PAPER',fingerprint=r['rule'].sha256,prefix=prefix)
        reviews+=review['reviews'];v=pinned.payload
        r['states'][scope.key]=promote((objects,v['components'],v['bundle']['artifacts'],pinned.sha256),r['states'][scope.key],review_id=prefix+'review')
    monkeypatch.setattr(certification,'protected_reviews',lambda:dict(reviews=reviews))
    monkeypatch.setattr(model_registry,'ApprovedArtifactReader',Reader)
    coverage=deepcopy(s.get('payout-coverage')['body']['payload'])
    coverage.update(observation_id=r['anchor']['id'],observation_sha256=r['anchor']['sha256'])
    cp=s.capture('scheduled-coverage',event_id=r['context'].event_id,kind='FEATURES',provider='fixture',source_identity='coverage',revision='scheduled',
        payload=coverage,evidence_class='SYNTHETIC')
    op=make_physical_plan(r,setup[3]);pp=replace(op,name='payout',base=selector(s.get('model2'),'MODEL'),input_target=UNRESOLVED_EXTREME,
        coverage=selector(cp,'FEATURES'),pair_without_pws=False)
    shared=(selector(r['anchor'],'OFFICIAL'),selector(r['qc'],'PWS'))
    obs=replace(inputs(r,r['observation_kw']),binding=replace(r['observation_kw']['binding'],bundle_sha256=observation.sha256),
        sources=op.output_sources(300.)+shared)
    pay=replace(inputs(r,r['payout_kw']),binding=replace(r['binding'],bundle_sha256=payout.sha256),sources=pp.output_sources(300.)+shared)
    lane=app.PWSLeadLane('lead',pay,(target(r),),BOOK_PROVIDER,r['request'].valuation_policy,10.,
        obs,op.output_sources(300.,without_pws=True),replace(r['lead_kw']['policy'],max_pws_age_seconds=300.))
    # This fixture supplies current exact observation/model/QC inputs in the
    # archive; only public books require fresh collection in its census. All
    # runtime/admission source leases and independently scoped review code run.
    cfg=scoped_plan(r,lane);event=cfg.events[0]
    cfg=replace(cfg,events=(replace(event,route=replace(event.route,required_source_kinds=()),
        census=replace(event.census,official_metar_proxy=False)),),preparations=prep.PreparationSettings((op,pp)),
        candidate=replace(cfg.candidate,maximum_jobs=24,maximum_seconds=20.))
    synthetic_clock(r,monkeypatch);calls=[]
    async def go():
        async with httpx.AsyncClient(transport=transport(r,calls)) as client:
            candidate=app.assemble_candidate(s,client,cfg,generation='scheduled-models');ready(r,candidate.runtime.health)
            row=await candidate.run('scheduled-models')
            before=s.pin_read_view();assert await candidate.run('scheduled-models')==row and s.pin_read_view()==before
            return candidate,row
    candidate,row=asyncio.run(go());d=row['body']['details']
    stages=[j for j in d['worker_results'] if j['kind']=='INPUT_PREPARATION']
    assert sum(j['outcome']=='PREPARATION_COMPLETE' for j in stages)==2,d
    assert all(j['outcome'] in {'PREPARATION_STAGE_RECORDED','PREPARATION_COMPLETE','UNCHANGED_INPUTS_NO_RECEIPT_RENEWAL'} for j in stages),d
    records=s.records(kind='MEASUREMENT',event_id=r['context'].event_id,limit=1000)
    outcomes=[x['body']['details'] for x in records if x['body']['details'].get('prediction',{} ) and x['body']['details'].get('strategy')=='PWS_OBSERVATION_LEAD']
    assert outcomes,sorted({str(x['body']['details'].get('reason')) for x in s.records(kind='RUNTIME_STATUS',event_id=r['context'].event_id,limit=1000)})
    assert outcomes[-1]['prediction']['bundle_sha256']==payout.sha256
    assert outcomes[-1]['reason']=='EVENT_STATE_SUPPRESSES_TEMPERATURE_ENTRY'
    assert outcomes[-1]['valuation']['reasons']==['CONSERVATIVE_EV_NOT_ABOVE_THRESHOLD']
    assert outcomes[-1]['proposal'] is None and outcomes[-1]['executable_exit_value'] is None
    assert candidate.runtime.coordinator.snapshot()['reserved_cash']=='0' and not s.records(kind='TRADE')


def test_new_pws_receipt_rebuilds_paired_cutoff_without_reusing_old_ablation(prepared):
    r=prepared;old=finish(r)['body']['details']['outputs'];advance(r,1.)
    # A new actual QC derivation of the same raw sample is still a new QC receipt,
    # but its sensor/source timestamps must not be refreshed by this worker.
    from polymarket_scanner.v11.pws_quality import archive_neighborhood
    from test_v11_pws_quality import policy
    qc=archive_neighborhood(r['store'],'revised-qc',event_id=r['rule'].payload['event_id'],
        capture_ids=('aux-normalized',),official=r['prep_plan'].official,policy=policy())
    new=finish(r,'new')['body']['details']['outputs'];s=r['store']
    assert new['without_feature_id']!=old['without_feature_id']
    a,b=(s.get(new[k])['body']['payload'] for k in ('with_feature_id','without_feature_id'))
    assert a['context']['as_of']==b['context']['as_of']
    assert all(ref['id']!=qc['id'] for ref in b['dependencies'])
    assert s.get(new['with_model_id'])['body']['received_at']==r['base']['body']['received_at']


def test_stale_optional_pws_is_missing_and_keeps_fresh_official_feature(prepared):
    r=prepared;p=replace(r['prep_plan'],pws=replace(r['prep_plan'].pws,maximum_age_seconds=1.))
    r['worker']=prep.PreparationWorker(r['store'],r['worker'].health,prep.PreparationSettings((p,)))
    d=finish(r)['body']['details'];f=r['store'].get(d['outputs']['with_feature_id'])['body']['payload']
    assert f['values']['pws_weighted_median_c'] is None and f['values']['official_proxy_temperature_c']==20.
    from polymarket_scanner.v11.strategy_pipeline import _model_inputs
    from polymarket_scanner.v11.model_artifacts import predict_with_bundle
    c=_model_inputs(r['store'],r['rule'],(d['outputs']['with_model_id'],),r['now'][0],target=NEXT_OBSERVATION)
    prediction=predict_with_bundle(r['pinned'],r['rule'],c,as_of=r['now'][0],max_source_age_seconds=300.).payload
    assert prediction['model_inputs'][0]['kernel_sigma']==3.


def test_repeated_budget_failure_rotates_to_other_plan_and_retains_partial_evidence(prepared,monkeypatch):
    r=prepared;p=r['prep_plan'];a=replace(p,name='a-budget',maximum_gap_seconds=901.)
    w=prep.PreparationWorker(r['store'],r['worker'].health,prep.PreparationSettings((a,p),maximum_stage_attempts=2))
    original=w._stage
    def staged(plan,active,deadline):
        if plan.name==a.name:raise EvidenceError('PREPARATION_TIME_BOUND')
        return original(plan,active,deadline)
    monkeypatch.setattr(w,'_stage',staged)
    assert w.step('budget1')['body']['details']['outcome']=='PREPARATION_PROGRESS_RETAINED'
    assert w.step('budget2')['body']['details']['outcome']=='PREPARATION_SOURCE_GATED'
    d=w.step('healthy')['body']['details'];assert d['plan']==p.name and d['outcome']=='PREPARATION_STAGE_RECORDED'


def test_interrupted_command_reprobes_clock_before_resume(prepared,monkeypatch):
    r=prepared;w=r['worker'];original=w._stage
    monkeypatch.setattr(w,'_stage',lambda *a:(_ for _ in ()).throw(OSError('interruption')))
    with pytest.raises(OSError):w.step('same-command')
    monkeypatch.setattr(w,'_stage',original);r['sync'][0]=False
    d=w.step('same-command')['body']['details'];assert d['outcome']=='DEFERRED_CLOCK_UNHEALTHY'
    assert d['state']['active']['stage']==0


def test_remaining_model_coverage_interruption_recovers_without_new_model(gefs,monkeypatch):
    r=gefs;_,official=prepare(r);p=prep.RemainingPreparation('remaining',r['plan'],selector(official,'OFFICIAL'))
    w=make_worker(r,monkeypatch,(p,));original=r['store']._append
    def append(key,kind,*a,**kw):
        if key.startswith('prepared-remaining:') and kind=='FEATURES':raise OSError('coverage interruption')
        return original(key,kind,*a,**kw)
    monkeypatch.setattr(r['store'],'_append',append)
    with pytest.raises(OSError):w.step('interrupted')
    old=r['store'].latest_source(kind='MODEL',event_id=p.event_id,provider=prep.REMAINING_PROVIDER,source_identity=p.plan.source_identity+':REMAINING')
    monkeypatch.setattr(r['store'],'_append',original);advance(r,.2)
    d=w.step('interrupted')['body']['details'];assert d['outcome']=='PREPARATION_COMPLETE' and d['outputs']['model_id']==old['id']
    assert r['store'].get(old['id'])==old


def test_preparation_telemetry_does_not_permit_clock_regressed_feature_writes(prepared):
    r=prepared;w=r['worker'];w.step('first');r['now'][0]-=10.
    d=w.step('backward')['body']['details'];assert d['outcome']=='DEFERRED_CLOCK_UNHEALTHY'
    with pytest.raises(EvidenceError,match='CLOCK_REGRESSION'):
        r['store'].capture('regressed',event_id=r['context'].event_id,kind='FEATURES',provider='fixture',source_identity='feature',revision='1',payload={},evidence_class='SYNTHETIC')
    with pytest.raises(EvidenceError,match='SAFETY_AUDIT_CANNOT_AUTHORIZE_ACTION'):
        r['store'].safety_audit('unscoped',event_id='other',kind='RUNTIME_STATUS',details=d)


def test_normalized_awc_must_retain_exact_raw_hash_and_receipt(prepared):
    r=prepared;s=r['store'];b=r['normalized']['body'];p=deepcopy(b['payload']);p['raw_evidence_sha256']='0'*64
    corrupt=s.capture('bad-normalization',event_id=r['context'].event_id,kind='OFFICIAL_OBSERVATION',provider=b['provider'],source_identity=b['source_identity'],
        revision=b['revision'],observed_at=b['observed_at'],payload=p,evidence_class='SYNTHETIC')
    with pytest.raises(EvidenceError,match='PHYSICAL_AWC_RAW_LINEAGE_MISMATCH'):
        archive_nowcast_features(s,'bad-feature',event_id=r['context'].event_id,official=r['prep_plan'].official,
            awc_capture_ids=(corrupt['id'],),max_observation_age_seconds=300.,trajectory_window_seconds=3600.,maximum_gap_seconds=900.)


def test_remaining_worker_adopts_received_newer_run_without_changing_source_policy(gefs,monkeypatch):
    r=gefs;path,official=prepare(r);seed=replace(r['plan'],initialized_at=r['plan'].initialized_at-21600.)
    p=prep.RemainingPreparation('remaining',seed,selector(official,'OFFICIAL'));w=make_worker(r,monkeypatch,(p,))
    d=w.step('newer-archived-run')['body']['details'];assert d['outcome']=='PREPARATION_COMPLETE',d
    result=r['store'].get(d['outputs']['model_id'])
    assert result['body']['issued_at']==path['body']['issued_at']>seed.initialized_at
    assert result['body']['received_at']==path['body']['received_at']


def test_missing_exact_source_does_not_prevent_next_independent_preparation(prepared):
    r=prepared;p=r['prep_plan'];missing=replace(p,name='a-missing',base=replace(p.base,provider='absent'))
    w=prep.PreparationWorker(r['store'],r['worker'].health,prep.PreparationSettings((missing,p)))
    d=w.step('missing')['body']['details'];assert d['reason']=='PREPARATION_REQUIRED_SOURCE_ABSENT'
    d=w.step('next')['body']['details'];assert d['plan']==p.name and d['outcome']=='PREPARATION_STAGE_RECORDED'
