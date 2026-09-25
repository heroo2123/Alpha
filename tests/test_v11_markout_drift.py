"""Synthetic archived books; no empirical fill, calibration or policy approval."""
import asyncio
from copy import deepcopy
from dataclasses import asdict, replace
from decimal import Decimal

import httpx
import pytest

from polymarket_scanner.v11 import drift_runtime as drift
from polymarket_scanner.v11.audit_reports import AuditPolicy, AuditScheduler, AuditWorker, _aggregate, _fold
from polymarket_scanner.v11.certification import StationRegistry
from polymarket_scanner.v11.evidence import EvidenceError, digest
from polymarket_scanner.v11.markout_drift import MarkoutDriftPolicy, METHOD, SELECTION, snapshot_cohort, measure_markout_window
from polymarket_scanner.v11.maker_telemetry import MakerTelemetryPolicy
from test_v11_maker_research import rig, maker, book, features, setup, bundle, factory, account_inventory_fixture
from test_v11_maker_telemetry import worker as telemetry_worker, sample as health_sample
from test_v11_runtime_health import advance, ready


def policy(**kw):
    return replace(MarkoutDriftPolicy('SYNTHETIC_ONLY','SYNTHETIC',86400.,1,'BUY',2.,'.01',1,1,1,'0',METHOD),**kw)


def cohort(r,t,p=None):
    return snapshot_cohort(t,scope=r['scope'],bundle_sha256=r['binding'].bundle_sha256,policy=p or policy(),as_of=r['now'][0])


def measure(r,t,p=None,**changes):
    p=p or policy();kw=dict(scope=r['scope'],bundle_sha256=r['binding'].bundle_sha256,policy=p,
        cohort=cohort(r,t,p),as_of=r['now'][0]);kw.update(changes)
    return measure_markout_window(t,**kw)


def publish(r,t,*,key='horizon',bid='.05'):
    advance(r,1);book(r,key,bids=[dict(price=bid,size='20')]);advance(r,2)
    t.step('publish-'+key)


def monitor(r,t,monkeypatch,p=None):
    plan=drift.DriftPlan(r['scope'],r['binding'].bundle_sha256,p or policy())
    w=drift.DriftWorker(t.research.coordinator,(plan,),maker_telemetry=t)
    review=dict(plan_key=plan.key,account_id='account',namespace='V11_PAPER',review_id='SYNTHETIC',reviewer='TEST_ONLY',
        approved_at=r['now'][0]-plan.policy.window_seconds-1,expires_at=r['now'][0]+120,
        model_state_sha256=digest(r['model_state'][0]),maximum_measurement_age_seconds=60.,selection=SELECTION,
        action='SAFETY_REDUCTION_ONLY',financial_authority=False)
    manifest=dict(version='alpha_v11_drift_reviews_v1',reviews=[review])
    monkeypatch.setattr(drift,'protected_reviews',lambda:deepcopy(manifest))
    return w,plan,manifest


def test_original_scope_first_horizon_and_explicit_cost_reproduction_are_read_only(rig,monkeypatch):
    r=rig;t=telemetry_worker(r,monkeypatch);publish(r,t);before=r['store'].pin_read_view();d=measure(r,t)
    assert d['outcome']=='DEGRADATION_CANDIDATE' and Decimal(d['scores']['mean_counterfactual_per_share'])==Decimal('-.07')
    assert d['scores']['sign_counts']=={'negative':1} and d['scores']['negative_quote_fraction']=='1'
    assert (d['scores']['n_quotes'],d['scores']['n_events'],d['scores']['n_city_days'])==(1,1,1)
    assert d['rows'][0]['admission_ref']['id']=='pin' and d['rows'][0]['book_ref']['id']=='horizon'
    assert d['selection']==SELECTION and not d['global_universe_coverage_verified']
    assert d['actual_trading_pnl'] is d['net_ev_capture'] is None and not d['financial_authority']
    assert r['store'].pin_read_view()==before and t.research.coordinator._head() is None


def test_automatic_markout_review_demotes_once_without_model_account_or_fill_mutation(rig,monkeypatch):
    r=rig;t=telemetry_worker(r,monkeypatch);publish(r,t);w,p,_=monitor(r,t,monkeypatch)
    model=deepcopy(r['model_state'][0]);head=t.research.coordinator._head();row=w.step('auto');d=row['body']['details']
    assert d['outcome']=='SCOPED_SAFETY_REDUCTION_APPLIED'
    reduction=w.store.get(d['demotion_id'])['body']['details']
    assert reduction['scope']==asdict(r['scope']) and reduction['state']=='DISABLED'
    assert reduction['reason']=='REVIEWED_MAKER_COUNTERFACTUAL_DEGRADATION'
    assert r['model_state'][0]==model and t.research.coordinator._head()==head
    advance(r,1);assert w.step('auto')==row
    assert w.step('quiet')['body']['details']['outcome']=='NO_NEW_MARKOUT_OR_REALIZATION'
    assert not r['store'].records(kind='TRADE')


@pytest.mark.parametrize('missing',['fees','book','depth','not_published'])
def test_unknown_window_members_gate_reduction_and_are_never_zero_or_dropped(rig,monkeypatch,missing):
    r=rig;t=telemetry_worker(r,monkeypatch,fee=None if missing=='fees' else '.01')
    advance(r,1)
    if missing!='book':book(r,'horizon',bids=[dict(price='.05',size='.1' if missing=='depth' else '20')])
    advance(r,2)
    if missing!='not_published':t.step('publish')
    p=policy(fee_per_share=t.policy.fee_per_share);w,_,_=monitor(r,t,monkeypatch,p)
    d=w.step('unknown')['body']['details'];m=w.store.get(d['measurement_id'])['body']['details']['result']
    assert d['outcome']=='INCOMPLETE_HORIZON_COHORT' and not d['demotion_applied']
    assert m['scores']['n_quotes']==1 and m['scores']['n_measured']==0 and m['scores']['unknown_reasons']
    assert m['scores']['mean_counterfactual_per_share'] is None


def test_snapshot_missing_markout_is_not_filled_by_later_publication(rig,monkeypatch):
    r=rig;t=telemetry_worker(r,monkeypatch);advance(r,1);book(r,'horizon');advance(r,2)
    pinned=cohort(r,t);first=measure(r,t,cohort=pinned);t.step('later-publication')
    assert measure(r,t,cohort=pinned)==first and first['outcome']=='INCOMPLETE_HORIZON_COHORT'
    assert measure(r,t)['scores']['n_measured']==1


def test_half_open_target_window_and_tolerance_close_are_enforced(rig,monkeypatch):
    r=rig;t=telemetry_worker(r,monkeypatch);created=r['now'][0]
    advance(r,1);book(r,'horizon');assert cohort(r,t)['quotes']==[]
    advance(r,1);assert cohort(r,t)['quotes']==[]
    advance(r,1);t.step('closed');assert len(cohort(r,t)['quotes'])==1
    assert len(cohort(r,t,policy(window_seconds=2.))['quotes'])==1
    advance(r,.01);assert cohort(r,t,policy(window_seconds=2.))['quotes']==[]
    assert r['now'][0]>created


@pytest.mark.parametrize('other',['horizon','direction','station','bundle'])
def test_unmatched_scope_or_horizon_never_inherits_a_known_markout(rig,monkeypatch,other):
    r=rig;t=telemetry_worker(r,monkeypatch);publish(r,t)
    p=policy(horizon_seconds=5) if other=='horizon' else policy(direction='SELL') if other=='direction' else policy()
    scope=replace(r['scope'],station='KSEA') if other=='station' else r['scope']
    bundle_sha='f'*64 if other=='bundle' else r['binding'].bundle_sha256
    c=snapshot_cohort(t,scope=scope,bundle_sha256=bundle_sha,policy=p,as_of=r['now'][0])
    d=measure(r,t,p,scope=scope,bundle_sha256=bundle_sha,cohort=c)
    assert not d['rows'] and d['outcome']=='INSUFFICIENT_COHORT'


def test_sell_direction_uses_ask_depth_and_never_books_hypothetical_income(rig,monkeypatch):
    r=rig;account_inventory_fixture(r);r['quote']=replace(r['quote'],direction='SELL',limit_price='.3')
    t=telemetry_worker(r,monkeypatch);head=r['coordinator']._head();publish(r,t)
    d=measure(r,t,policy(direction='SELL'))
    assert Decimal(d['scores']['mean_counterfactual_per_share'])==Decimal('.08')
    assert d['outcome']=='NO_DECLARED_BREACH' and r['coordinator']._head()==head


def test_small_cohort_and_exact_threshold_equality_never_restore_a_demotion(rig,monkeypatch):
    r=rig;t=telemetry_worker(r,monkeypatch);publish(r,t)
    assert measure(r,t,policy(minimum_events=2,minimum_city_days=2))['outcome']=='INSUFFICIENT_COHORT'
    w,_,_=monitor(r,t,monkeypatch,policy(maximum_adverse_mean_per_share='.07'))
    prior=StationRegistry(w.store).demote('manual',r['scope'],state='DISABLED',reason='PRIOR_FAILURE',evidence_ids=('model2',))
    d=w.step('equal')['body']['details']
    assert d['outcome']=='NO_DECLARED_BREACH' and not d['demotion_applied']
    assert w.store.latest(kind='REGISTRY',event_id=prior['event_id'])==prior


@pytest.mark.parametrize('change',[dict(horizon_seconds=True),dict(horizon_seconds=2),dict(minimum_quotes=0),
    dict(minimum_city_days=2),dict(evidence_class='LIVE'),dict(metric_method='POOL_HORIZONS'),dict(fee_per_share='NaN')])
def test_policy_must_explicitly_declare_supported_nonfinancial_scope(change):
    with pytest.raises(EvidenceError):policy(**change)


@pytest.mark.parametrize('fault',['quote_list','hash','boundary','heads'])
def test_caller_cannot_cherry_pick_or_replace_pinned_cohort(rig,monkeypatch,fault):
    r=rig;t=telemetry_worker(r,monkeypatch);publish(r,t);c=cohort(r,t)
    if fault=='quote_list':c['quotes']=[]
    elif fault=='hash':c['quotes'][0]['markout_ref']['sha256']='f'*64
    elif fault=='boundary':c['source_through_seq']=-1
    else:c['input_heads']=[]
    with pytest.raises(EvidenceError):measure(r,t,cohort=c)


@pytest.mark.parametrize('boundary',['before','after'])
def test_interrupted_reduction_recovers_original_measurement_once(rig,monkeypatch,boundary):
    r=rig;t=telemetry_worker(r,monkeypatch);publish(r,t);w,p,_=monitor(r,t,monkeypatch);save=w._save
    with monkeypatch.context() as patch:
        if boundary=='before':patch.setattr(w,'_review',lambda *a:(_ for _ in ()).throw(RuntimeError('POWER_LOSS')))
        else:
            def interrupt(*a,**kw):
                if kw['action']=='DRIFT_RESULT':raise RuntimeError('POWER_LOSS')
                return save(*a,**kw)
            patch.setattr(w,'_save',interrupt)
        with pytest.raises(RuntimeError):w.step('interrupted')
    measurements=[x for x in w.store.records(kind='MODEL_EVENT') if x['body']['details'].get('action')=='DRIFT_MEASUREMENT']
    fresh=drift.DriftWorker(w.coordinator,(p,),maker_telemetry=t);d=fresh.step('resume')['body']['details']
    assert d['demotion_applied'] and len(measurements)==1 and w.store.get(measurements[0]['id'])==measurements[0]
    advance(r,1);assert fresh.step('idle')['body']['details']['outcome']=='NO_NEW_MARKOUT_OR_REALIZATION'


def test_explicit_completed_request_replays_without_renewal(rig,monkeypatch):
    r=rig;t=telemetry_worker(r,monkeypatch);publish(r,t);w,p,_=monitor(r,t,monkeypatch)
    kw=dict(plan_key=p.key,cohort=cohort(r,t),as_of=r['now'][0]);saved=w.request_markouts('exact',**kw)
    assert w.step('measure')['body']['details']['demotion_applied']
    advance(r,10);assert w.request_markouts('exact',**kw)==saved
    with pytest.raises(EvidenceError,match='REPLAY_CONFLICT'):w.request_markouts('exact',**dict(kw,as_of=r['now'][0]))


def test_changed_quote_head_before_action_gates_then_remeasures_original_history(rig,monkeypatch):
    r=rig;t=telemetry_worker(r,monkeypatch);publish(r,t);w,_,_=monitor(r,t,monkeypatch)
    with monkeypatch.context() as patch:
        patch.setattr(w,'_review',lambda *a:(_ for _ in ()).throw(RuntimeError('POWER_LOSS')))
        with pytest.raises(RuntimeError):w.step('interrupted')
    t.research.retire('later-head',quote_id='quote',reason='RESEARCH_END');advance(r,.01)
    d=w.step('recover')['body']['details']
    assert d['outcome']=='REDUCTION_GATED' and d['reason']=='DRIFT_MARKOUT_CHANGED_BEFORE_REDUCTION'
    assert w.step('fresh')['body']['details']['demotion_applied']


def test_unreviewed_policy_cannot_reduce_or_mislabel_counterfactual_as_calibration(rig,monkeypatch):
    r=rig;t=telemetry_worker(r,monkeypatch);publish(r,t);w,_,m=monitor(r,t,monkeypatch)
    m['reviews'][0]['selection']='EXPLICIT_CAPTURE_COHORT'
    d=w.step('wrong-selection')['body']['details'];assert d['outcome']=='REDUCTION_GATED' and not d['demotion_applied']
    with pytest.raises(EvidenceError):drift.DriftPlan(replace(r['scope'],strategy='FUTURE_FORECAST'),r['binding'].bundle_sha256,policy())
    with pytest.raises(EvidenceError):drift.DriftWorker(w.coordinator,tuple(w.plans.values()))


@pytest.mark.parametrize('missing_second',[False,True])
def test_repeated_quotes_are_one_event_and_unknown_members_cannot_be_selected_away(rig,monkeypatch,missing_second):
    r=rig;t=telemetry_worker(r,monkeypatch);second_market=r['rule'].payload['partition'][1]['market_id']
    book(r,'second-initial',market_id=second_market);features(r,'second-initial','second-micro')
    q=replace(r['quote'],quote_id='second',thesis_id='second-thesis',market_id=second_market,microstructure_id='second-micro')
    assert t.research.propose('second-origin',q)['body']['details']['outcome']=='OBSERVING_RESEARCH_QUOTE'
    advance(r,1);book(r,'first-horizon',bids=[dict(price='.05',size='20')])
    if not missing_second:book(r,'second-horizon',market_id=second_market,bids=[dict(price='.15',size='20')])
    advance(r,2);t.step('both');d=measure(r,t,policy(minimum_events=2,minimum_city_days=2))
    assert (d['scores']['n_quotes'],d['scores']['n_events'],d['scores']['n_city_days'])==(2,1,1)
    assert d['outcome']==('INCOMPLETE_HORIZON_COHORT' if missing_second else 'INSUFFICIENT_COHORT')
    assert Decimal(d['scores']['mean_counterfactual_per_share'])==Decimal('-.07' if missing_second else '-.02')
    assert d['scores']['observed_subset_only'] is missing_second


def test_all_five_horizons_keep_their_own_policy_values_after_retirement(rig,monkeypatch):
    r=rig;t=telemetry_worker(r,monkeypatch);created=r['now'][0]
    t.research.retire('retire',quote_id='quote',reason='RESEARCH_END')
    expected=[]
    for i,horizon in enumerate((1,5,30,120,600)):
        advance(r,created+horizon-r['now'][0]);bid=Decimal('.05')+Decimal('.02')*i
        book(r,'book-'+str(horizon),bids=[dict(price=str(bid),size='20')]);advance(r,2);health_sample(r,t)
        t.step('horizon-'+str(horizon));d=measure(r,t,policy(horizon_seconds=horizon))
        assert len(d['rows'])==1 and d['rows'][0]['book_ref']['id']=='book-'+str(horizon)
        expected.append(Decimal(d['scores']['mean_counterfactual_per_share']))
    assert expected==list(map(Decimal,['-.07','-.05','-.03','-.01','.01']))
    assert t.research._state(t.research._head())['quote']['status']=='RETIRED'


@pytest.mark.parametrize('fault',['value','origin','admission','model','evidence_class','first_book','early_publish'])
def test_malformed_or_unmatched_horizon_provenance_is_gated(rig,monkeypatch,fault):
    # Alter a read-only view result to exercise validation; never rewrite archive
    # records or present synthetic corruption as actual provider evidence.
    from polymarket_scanner.v11.learning_sources import LearningSourceView
    r=rig;t=telemetry_worker(r,monkeypatch);publish(r,t);p=policy();c=cohort(r,t)
    mark_id=c['quotes'][0]['markout_ref']['id'];get=LearningSourceView.get
    def changed(self,key):
        row=get(self,key)
        if key==mark_id:
            d=row['body']['details']
            if fault=='value':d['markout_per_share']='1'
            elif fault=='origin':row['body']['evidence']=[]
            elif fault=='first_book':d['book_id']='initial'
            elif fault=='early_publish':d['as_of']=d['target_at']
        if key=='pin':
            if fault=='admission':row['body']['details']['request']['context']['city_id']='invented'
            elif fault=='model':row['body']['details']['assessment']['model_bundle_sha256']='f'*64
        if key=='horizon' and fault=='evidence_class':row['body']['evidence_class']='PUBLIC_OBSERVED'
        return row
    monkeypatch.setattr(LearningSourceView,'get',changed)
    with pytest.raises(EvidenceError):measure(r,t,p,cohort=c)


def test_new_measurement_during_review_fails_atomic_head_guard(rig,monkeypatch):
    r=rig;t=telemetry_worker(r,monkeypatch);publish(r,t);w,_,_=monitor(r,t,monkeypatch);review=w._review;calls=[]
    def race(plan,result):
        answer=review(plan,result);calls.append(1)
        if len(calls)==2:
            r['store'].audit('concurrent-book-analysis',event_id=r['context'].event_id,kind='MEASUREMENT',details={'synthetic':True})
        return answer
    monkeypatch.setattr(w,'_review',race);d=w.step('race')['body']['details']
    assert d['outcome']=='REDUCTION_GATED' and d['reason']=='AUDIT_GUARDED_STATE_CHANGED' and not d['demotion_applied']
    advance(r,.01);assert w.step('fresh')['body']['details']['demotion_applied']


def test_typed_candidate_automatically_monitors_retires_and_audits_original_scope(rig,monkeypatch):
    from polymarket_scanner.v11 import candidate_assembly as app
    from test_v11_candidate_assembly import scoped_plan, synthetic_clock, transport
    from test_v11_request_assembly import inputs, target
    from test_v11_audit_reports import finish
    r=rig;scope=inputs(r)
    lane=app.TemperatureLane('temperature',replace(scope,scope=replace(scope.scope,strategy='FUTURE_FORECAST')),
        (target(r),),'fixture',r['request'].valuation_policy,10.)
    cfg=scoped_plan(r,lane);p=drift.DriftPlan(r['scope'],r['binding'].bundle_sha256,policy())
    cfg=replace(cfg,candidate=replace(cfg.candidate,maximum_jobs=5),maker=app.MakerTelemetryPlan(
        r['policy'],MakerTelemetryPolicy('fixture',fee_per_share='.01'),(scope,)),drift=(p,))
    synthetic_clock(r,monkeypatch);calls=[]
    async def run():
        async with httpx.AsyncClient(transport=transport(r,calls)) as client:
            candidate=app.assemble_candidate(r['store'],client,cfg,generation='markout-integrated')
            ready(r,candidate.runtime.health);t=candidate.maker_telemetry
            assert t.research.propose('retained',r['quote'])['body']['details']['outcome']=='OBSERVING_RESEARCH_QUOTE'
            monitor(r,t,monkeypatch)
            advance(r,1);book(r,'horizon',bids=[dict(price='.05',size='20')]);advance(r,2)
            return candidate,await candidate.run('markout-candidate')
    candidate,row=asyncio.run(run());d=row['body']['details']
    assert [j['kind'] for j in d['worker_results']]==['CENSUS','DISCOVERY','AUDIT','MAKER_TELEMETRY','DRIFT']
    assert d['worker_results'][-1]['outcome']=='SCOPED_SAFETY_REDUCTION_APPLIED',d
    assert candidate.drift.maker_telemetry is candidate.maker_telemetry
    assert candidate.runtime.maker._state(candidate.runtime.maker._head())['quote']['status']=='RETIRED'
    assert candidate.runtime.coordinator._head() is None and not r['store'].records(kind='TRADE')
    assert d['all_async_jobs_drained'] and not d['forward_acceptance']
    r['now'][0]=(int(r['now'][0]//86400)+1)*86400+1
    AuditScheduler(r['store'],cfg.audits).request_due();auditor=candidate.audits
    for _ in range(3):
        report=finish(auditor)['body']['details']
        if report['operations'].get('markout_monitoring'):break
    item=report['operations']['markout_monitoring'][0]
    assert item['scope']==asdict(r['scope']) and item['horizon_seconds']==1 and item['direction']=='BUY'
    assert Decimal(item['scores']['mean_counterfactual_per_share'])==Decimal('-.07')
    assert item['actual_trading_pnl'] is item['net_ev_capture'] is None
    assert report['operations']['drift_outcomes']=={'SCOPED_SAFETY_REDUCTION_APPLIED':1}
    assert report['coverage']['semantic_coverage_complete'] and not report['financial_authority']
    with pytest.raises(EvidenceError):replace(cfg,maker=None)
    with pytest.raises(EvidenceError):replace(cfg,drift=(replace(p,policy=policy(fee_per_share='.02')),))


def test_audit_horizons_are_not_averaged_and_overflow_is_visible(rig,monkeypatch):
    r=rig;t=telemetry_worker(r,monkeypatch);publish(r,t);w,_,_=monitor(r,t,monkeypatch)
    d=w.step('record')['body']['details'];row=w.store.get(d['measurement_id']);a=_aggregate()
    window=dict(start=r['now'][0]-100,end=r['now'][0]+1)
    for _ in range(33):_fold(row,a,window)
    assert len(a['markout_monitoring'])==32 and a['metadata_overflow']
    assert a['markout_monitoring'][0]['horizon_seconds']==1 and 'mean_markout' not in a


def test_new_short_horizon_data_does_not_starve_an_already_due_longer_horizon(rig,monkeypatch):
    r=rig;t=telemetry_worker(r,monkeypatch);q=t.research._state(t.research._head())['quote']
    advance(r,1);book(r,'h1');advance(r,4);book(r,'h5');advance(r,2)
    t.research.markout(t._mark_key('quote',q,5),quote_id='quote',horizon_seconds=5,tolerance_seconds=2.,fee_per_share='.01')
    plans=tuple(drift.DriftPlan(r['scope'],r['binding'].bundle_sha256,policy(horizon_seconds=h,maximum_adverse_mean_per_share='3')) for h in (1,5))
    w=drift.DriftWorker(t.research.coordinator,plans,maker_telemetry=t)
    first=w.step('short-unknown')['body']['details'];assert first['outcome']=='INCOMPLETE_HORIZON_COHORT'
    t.research.markout(t._mark_key('quote',q,1),quote_id='quote',horizon_seconds=1,tolerance_seconds=2.,fee_per_share='.01')
    advance(r,.01);second=w.step('long-due')['body']['details']
    m=w.store.get(second['measurement_id'])['body']['details']['result']
    assert m['request']['policy']['horizon_seconds']==5
    third=w.step('new-short')['body']['details'];m=w.store.get(third['measurement_id'])['body']['details']['result']
    assert m['request']['policy']['horizon_seconds']==1 and m['scores']['n_measured']==1


@pytest.mark.parametrize('deadline',[0.,1.])
def test_read_only_snapshot_deadline_is_enforced(rig,monkeypatch,deadline):
    r=rig;t=telemetry_worker(r,monkeypatch);publish(r,t)
    with pytest.raises(EvidenceError,match='TIME_BOUND'):
        snapshot_cohort(t,scope=r['scope'],bundle_sha256=r['binding'].bundle_sha256,policy=policy(),
            as_of=r['now'][0],deadline=deadline,monotonic=lambda:2.)


def test_normalized_horizon_requires_its_original_raw_source_derivation(rig,monkeypatch):
    r=rig;t=telemetry_worker(r,monkeypatch);advance(r,1)
    book(r,'normalized-with-missing-raw',bids=[dict(price='.05',size='20')],
        raw_evidence_id='missing-raw-book',raw_evidence_sha256='f'*64)
    advance(r,2);t.step('publish');w,_,_=monitor(r,t,monkeypatch)
    d=w.step('invalid-derivation')['body']['details']
    assert d['outcome']=='MEASUREMENT_GATED' and d['reason']=='EVIDENCE_MISSING' and not d['demotion_applied']
