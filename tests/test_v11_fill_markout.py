"""Reconciled PAPER execution → depth → reviewed safety → candidate audit."""
import asyncio
from copy import deepcopy
from dataclasses import asdict, replace
from decimal import Decimal

import httpx
import pytest

from polymarket_scanner.v11 import drift_runtime as drift
from polymarket_scanner.v11.audit_reports import _aggregate, _fold
from polymarket_scanner.v11.evidence import EvidenceError, digest
from polymarket_scanner.v11.fill_markout import FillMarkoutPolicy, METHOD, SELECTION, snapshot_fill_cohort
from polymarket_scanner.v11.performance import PerformanceLab
from test_v11_fill_evidence import rig, reserve, fill, proof, setup, bundle, factory, book, detailed_fill, coordinator
from test_v11_position_management import inventory, reserved_exit


def policy(**kw):
    return replace(FillMarkoutPolicy('SYNTHETIC_ONLY','SYNTHETIC',86400.,1,'BUY',2.,'.01',1,1,1,'0',METHOD),**kw)


def cohort(r,p=None):
    return snapshot_fill_cohort(coordinator(r),scope=r['scope'],bundle_sha256=r['binding'].bundle_sha256,policy=p or policy(),as_of=r['now'][0])


def measure(r,p=None,**kw):
    args=dict(scope=r['scope'],bundle_sha256=r['binding'].bundle_sha256,policy=p or policy(),cohort=cohort(r,p),as_of=r['now'][0]);args.update(kw)
    return PerformanceLab(coordinator(r)).scoped_fill_markouts(**args)


def horizon(r,*,key='horizon',fill_id='explicit',seconds=1,**changes):
    proof=r['store'].get(fill_id);d=proof['body']['payload']['execution_details']
    r['now'][0]=d['executed_at']+seconds
    return book(r,key,r['store'].get(d['post_validation_book_ref']['id']),**changes)


def filled(r,**kw):
    reserve(r);detailed_fill(r,**kw);horizon(r);r['now'][0]+=2


def monitor(r,monkeypatch,p=None):
    plan=drift.DriftPlan(r['scope'],r['binding'].bundle_sha256,p or policy());w=drift.DriftWorker(coordinator(r),(plan,))
    review=dict(plan_key=plan.key,account_id='account',namespace='V11_PAPER',review_id='SYNTHETIC',reviewer='TEST_ONLY',
        approved_at=r['now'][0]-plan.policy.window_seconds-1,expires_at=r['now'][0]+120,
        model_state_sha256=digest(r['model_state'][0]),maximum_measurement_age_seconds=60.,selection=SELECTION,
        action='SAFETY_REDUCTION_ONLY',financial_authority=False)
    manifest=dict(version='alpha_v11_drift_reviews_v1',reviews=[review]);monkeypatch.setattr(drift,'protected_reviews',lambda:deepcopy(manifest))
    return w,plan,manifest


@pytest.mark.parametrize('rig',['CROSS_TEMP_RELATIVE_VALUE','STRUCTURAL'],indirect=True)
@pytest.mark.parametrize('seconds',[1,5,30,120,600])
def test_original_scope_all_five_horizons_preserve_cost_basis_and_joint_ev(rig,seconds):
    r=rig;reserve(r);detailed_fill(r);horizon(r,seconds=seconds);r['now'][0]+=2;p=policy(horizon_seconds=seconds)
    before=r['store'].pin_read_view();d=measure(r,p);row=d['rows'][0]
    assert d['outcome']=='DEGRADATION_CANDIDATE' and Decimal(d['scores']['mean_fill_markout_per_share'])==Decimal('-.11')
    assert row['admission_ref']['id']=='pin' and row['book_ref']['id']=='horizon'
    assert row['decision']['ev_unit']=='JOINT_BASKET_TOTAL' and row['decision']['joint_ev_is_not_individual_leg_alpha']
    assert Decimal(row['price_markout_per_share'])==Decimal('-.08')
    assert d['actual_trading_pnl'] is d['net_ev_capture'] is None and not d['venue_execution_attested'] and not d['financial_authority']
    assert r['store'].pin_read_view()==before and d['execution_class']=='SYNTHETIC_PAPER_FILL'
    assert d['scores']['intent_sign_counts']=={'negative':1} and d['scores']['negative_intent_fraction']=='1'
    assert d['scores']['adverse_selection_rate_empirical'] is None


def test_sell_markout_uses_reacquisition_cost_and_keeps_realized_result_separate(rig):
    r=rig;inventory(r);p,_=reserved_exit(r);detailed_fill(r,intent_id=p.proposal_id,units='.5',price='.14')
    horizon(r);r['now'][0]+=2;d=measure(r,policy(direction='SELL'));row=d['rows'][0]
    assert Decimal(row['markout_per_share'])==Decimal('-.11') and Decimal(row['price_markout_per_share'])==Decimal('-.06')
    assert row['decision']['reason']=='NET_SALE_EXCEEDS_HOLD' and d['scores']['n_fills']==1
    assert Decimal(coordinator(r)._head()['body']['details']['state']['realized_entries'][0]['pnl'])==Decimal('-.05')


def test_single_leg_uses_its_own_signal_book_and_total_ev_without_basket_allocation(rig):
    from polymarket_scanner.v11.paper_coordinator import Proposal
    from polymarket_scanner.v11.valuation import settlement_entry
    r=rig;kw=r['kw'];leg=kw['legs'][0];p=r['proposal']
    value=settlement_entry(r['store'],'unqualified-single',rule=kw['rule'],prediction=kw['prediction'],binding=kw['binding'],
        market_id=leg.market_id,side=leg.side,units=leg.units,book_id=leg.book_id,policy=kw['policy'].valuation,costs=leg.costs)['body']['details']
    assert value['outcome']=='REJECT'
    # Same explicitly synthetic downstream fixture as the account mechanics
    # tests. It does not certify the uncalibrated model's entry economics.
    value.update(outcome='ACCEPT_RESEARCH',conservative_ev_per_share='.9',conservative_ev_total='1.8',synthetic_downstream_test_fixture=True)
    r['store'].audit('single-fixture',event_id=r['context'].event_id,kind='MEASUREMENT',details=value)
    single=Proposal('single','single-fixture-thesis',p.context,p.rule,'single-fixture',p.event_state_id,p.attribution,p.expires_at,'2',p.admission_ids)
    reserve(r,proposals=(single,));detailed_fill(r,intent_id='single');horizon(r);r['now'][0]+=2;row=measure(r)['rows'][0]
    assert row['execution_evidence']['signal_book_ref']['id']=='basket-book0'
    assert row['decision']['ev_unit']=='TOTAL' and not row['decision']['joint_ev_is_not_individual_leg_alpha']
    assert row['decision']['ev']=='1.8' and row['valuation_ref']['id']=='single-fixture'


@pytest.mark.parametrize('missing',['fee','depth','book','unhealthy','crossed','wrong_token','wrong_class'])
def test_unknown_horizon_members_never_become_zero_or_disappear(rig,missing):
    r=rig;reserve(r);detailed_fill(r);changes={}
    if missing=='depth':changes['bids']=[dict(price='.1',size='.1')]
    if missing=='unhealthy':changes['stream_healthy']=False
    if missing=='crossed':changes['bids']=[dict(price='.3',size='20')]
    if missing=='wrong_token':changes['token_id']='other'
    if missing!='book':horizon(r,**changes)
    r['now'][0]+=3
    p=policy(fee_per_share=None) if missing=='fee' else policy(evidence_class='PUBLIC_OBSERVED') if missing=='wrong_class' else policy()
    d=measure(r,p)
    assert d['outcome']=='INCOMPLETE_HORIZON_COHORT' and d['scores']['n_fills']==1
    assert d['scores']['n_measured']==0 and d['scores']['mean_fill_markout_per_share'] is None


@pytest.mark.parametrize('legacy',[True,False])
def test_legacy_and_bad_timing_are_conservatively_included_until_outside_possible_window(rig,legacy):
    r=rig;reserve(r)
    if legacy:fill(r,0)
    else:detailed_fill(r,mutate=lambda e:e.update(executed_at=999999.))
    r['now'][0]+=3;d=measure(r)
    assert d['outcome']=='INCOMPLETE_HORIZON_COHORT' and not d['execution_timing_coverage_verified']
    assert d['rows'][0]['target_at'] is None and d['scores']['n_fills']==1
    r['now'][0]+=86401;assert measure(r)['scores']['n_fills']==0


def test_first_bad_book_is_not_replaced_by_a_later_favorable_one(rig):
    r=rig;reserve(r);detailed_fill(r);horizon(r,stream_healthy=False);r['now'][0]+=.1
    book(r,'later',r['store'].get('horizon'),stream_healthy=True,bids=[dict(price='.19',size='20')]);r['now'][0]+=2
    d=measure(r);assert d['rows'][0]['book_ref']['id']=='horizon' and d['outcome']=='INCOMPLETE_HORIZON_COHORT'


def test_pinned_missing_book_remains_unknown_after_late_publication(rig):
    r=rig;reserve(r);detailed_fill(r);r['now'][0]+=3;c=cohort(r);at=r['now'][0]
    first=measure(r,cohort=c,as_of=at);book(r,'late-publication',r['store'].get('explicit-post'))
    assert measure(r,cohort=c,as_of=at)==first and first['scores']['n_measured']==0
    assert measure(r)['scores']['n_measured']==1


def test_partial_fills_weight_by_units_and_do_not_inflate_independent_intents(rig):
    r=rig;reserve(r);detailed_fill(r,units='.5',price='.16',fees='.01',other='.01');first=r['now'][0]
    detailed_fill(r,key='second',units='1.5',price='.18',fees='.015',other='.015');second=r['now'][0]
    r['now'][0]=first+1;book(r,'first-horizon',r['store'].get('explicit-post'))
    r['now'][0]=second+1;book(r,'second-horizon',r['store'].get('second-post'),bids=[dict(price='.16',size='20')])
    r['now'][0]+=2;d=measure(r)
    assert (d['scores']['n_fills'],d['scores']['n_intents'],d['scores']['n_events'])==(2,1,1)
    assert Decimal(d['scores']['mean_fill_markout_per_share'])==Decimal('-.065')
    assert measure(r,policy(minimum_intents=2))['outcome']=='INSUFFICIENT_COHORT'


def test_fractional_fill_with_repeating_cost_per_share_remains_measurable(rig):
    r=rig;filled(r,units='1.5',fees='.001',other='.001');d=measure(r)
    assert d['outcome']=='DEGRADATION_CANDIDATE' and d['scores']['n_measured']==1
    assert abs(Decimal(d['scores']['mean_fill_markout_per_share'])-Decimal('-.09133333333333333333333333333'))<Decimal('1e-27')
    assert Decimal(coordinator(r)._head()['body']['details']['state']['cash'])==Decimal('9.728')


def test_automatic_reviewed_reduction_is_idempotent_and_preserves_cash_lots_model(rig,monkeypatch):
    r=rig;filled(r);w,p,_=monitor(r,monkeypatch);account=deepcopy(w.coordinator._head());model=deepcopy(r['model_state'][0])
    result=w.step('automatic');d=result['body']['details'];reduction=r['store'].get(d['demotion_id'])['body']['details']
    assert d['outcome']=='SCOPED_SAFETY_REDUCTION_APPLIED' and reduction['reason']=='REVIEWED_PAPER_FILL_MARKOUT_DEGRADATION'
    assert reduction['scope']==asdict(r['scope']) and reduction['state']=='DISABLED'
    assert w.coordinator._head()==account and r['model_state'][0]==model
    r['now'][0]+=.1;assert w.step('automatic')==result
    assert w.step('idle')['body']['details']['outcome']=='NO_NEW_FILL_MARKOUT_OR_REALIZATION'


@pytest.mark.parametrize('change',['missing','late','selection','model'])
def test_missing_or_changed_review_never_reduces_scope(rig,monkeypatch,change):
    r=rig;filled(r);w,p,m=monitor(r,monkeypatch)
    if change=='missing':m['reviews']=[]
    elif change=='late':m['reviews'][0]['approved_at']=r['now'][0]
    elif change=='selection':m['reviews'][0]['selection']='EXPLICIT_CAPTURE_COHORT'
    else:m['reviews'][0]['model_state_sha256']='f'*64
    d=w.step('gated')['body']['details'];assert d['outcome']=='REDUCTION_GATED' and not d['demotion_applied']


def test_caller_cannot_select_only_some_fills(rig):
    r=rig;filled(r);c=cohort(r);c['fills']=[];c['history_sha256']=digest([])
    with pytest.raises(EvidenceError,match='PINNED_COHORT_CHANGED'):measure(r,cohort=c)


def test_account_change_during_review_gates_reduction(rig,monkeypatch):
    r=rig;filled(r);w,p,_=monitor(r,monkeypatch);original=w._review;changed=[]
    def race(*a):
        result=original(*a)
        if not changed:
            changed.append(True);coordinator(r).transition('cancel-during-review',intent_id='basket:leg:0',status='CANCEL_REQUESTED')
        return result
    monkeypatch.setattr(w,'_review',race);d=w.step('race')['body']['details']
    assert d['outcome']=='REDUCTION_GATED' and d['reason']=='AUDIT_GUARDED_STATE_CHANGED'


def test_book_change_during_review_is_guarded_and_new_snapshot_can_retry(rig,monkeypatch):
    r=rig;filled(r);w,p,_=monitor(r,monkeypatch);original=w._review;changed=[]
    def race(*a):
        result=original(*a)
        if not changed:
            changed.append(True);book(r,'during-review',r['store'].get('horizon'))
        return result
    monkeypatch.setattr(w,'_review',race);d=w.step('race')['body']['details']
    assert d['outcome']=='REDUCTION_GATED' and d['reason']=='AUDIT_GUARDED_STATE_CHANGED'
    r['now'][0]+=.01;assert w.step('retry')['body']['details']['demotion_applied']


def test_interruption_after_demotion_recovers_same_measurement_without_reducing_again(rig,monkeypatch):
    r=rig;filled(r);w,p,_=monitor(r,monkeypatch);original=w._save
    def interrupt(*args,**kw):
        if kw.get('action')=='DRIFT_RESULT':raise RuntimeError('SYNTHETIC_CRASH')
        return original(*args,**kw)
    monkeypatch.setattr(w,'_save',interrupt)
    with pytest.raises(RuntimeError,match='SYNTHETIC_CRASH'):w.step('crash')
    reductions=[x for x in r['store'].records(kind='REGISTRY') if x['body'].get('details',{}).get('action')=='DEMOTION']
    w=drift.DriftWorker(coordinator(r),(p,));d=w.step('recovery')['body']['details']
    assert d['demotion_applied'] and r['store'].latest(kind='REGISTRY',event_id=reductions[-1]['event_id'])==reductions[-1]
    assert len([x for x in r['store'].records(kind='MODEL_EVENT') if x['body'].get('details',{}).get('action')=='DRIFT_MEASUREMENT'])==1


def test_round_robin_services_another_horizon_before_new_short_horizon_fill(rig,monkeypatch):
    r=rig;reserve(r);detailed_fill(r);horizon(r);horizon(r,key='five',seconds=5);r['now'][0]+=2
    w,p,m=monitor(r,monkeypatch,policy(maximum_adverse_mean_per_share='1'))
    p2=drift.DriftPlan(p.scope,p.bundle_sha256,replace(p.policy,horizon_seconds=5));w=drift.DriftWorker(coordinator(r),(p,p2))
    assert w.step('first')['body']['details']['outcome']=='NO_DECLARED_BREACH'
    detailed_fill(r,key='second');horizon(r,key='second-horizon',fill_id='second');r['now'][0]+=2
    d=w.step('second')['body']['details'];result=r['store'].get(d['measurement_id'])['body']['details']['result']
    assert result['request']['policy']['horizon_seconds']==5 and result['scores']['n_fills']==1
    d=w.step('third')['body']['details'];result=r['store'].get(d['measurement_id'])['body']['details']['result']
    assert result['request']['policy']['horizon_seconds']==1 and result['scores']['n_fills']==2


@pytest.mark.parametrize('changes',[dict(horizon_seconds=2),dict(horizon_seconds=True),dict(direction='BOTH'),
    dict(evidence_class='LIVE'),dict(minimum_intents=True),dict(fee_per_share='NaN'),dict(metric_method='EMPIRICAL_FILL_RATE')])
def test_policy_rejects_unsupported_or_mixed_metric_claims(changes):
    with pytest.raises(EvidenceError):policy(**changes)


def test_window_is_half_open_and_tolerance_must_be_closed(rig):
    r=rig;reserve(r);detailed_fill(r);horizon(r);assert cohort(r)['fills']==[]
    r['now'][0]+=2;assert len(cohort(r)['fills'])==1
    assert len(cohort(r,policy(window_seconds=2))['fills'])==1
    assert cohort(r,policy(window_seconds=1))['fills']==[]


def test_normalized_book_cannot_hide_missing_raw_provenance(rig):
    r=rig;reserve(r);detailed_fill(r);horizon(r,raw_evidence_id='missing',raw_evidence_sha256='f'*64);r['now'][0]+=2
    d=measure(r);assert d['outcome']=='INCOMPLETE_HORIZON_COHORT' and d['scores']['unknown_reasons']=={'EVIDENCE_MISSING':1}


def test_snapshot_read_deadline_gates_without_writing(rig):
    r=rig;filled(r);before=r['store'].pin_read_view()
    with pytest.raises(EvidenceError,match='TIME_BOUND'):
        snapshot_fill_cohort(coordinator(r),scope=r['scope'],bundle_sha256=r['binding'].bundle_sha256,policy=policy(),
            as_of=r['now'][0],deadline=0.,monotonic=lambda:1.)
    assert r['store'].pin_read_view()==before


def test_summaries_are_separate_from_counterfactuals_and_never_pool_horizons(rig,monkeypatch):
    r=rig;filled(r);w,_,_=monitor(r,monkeypatch);d=w.step('auto')['body']['details'];measurement=r['store'].get(d['measurement_id'])
    a=_aggregate();window=dict(start=0,end=r['now'][0]+1);_fold(measurement,a,window);summary=a['fill_markout_monitoring'][0]
    assert 'markout_monitoring' not in a and summary['horizon_seconds']==1
    assert summary['execution_class']=='SYNTHETIC_PAPER_FILL' and summary['net_ev_capture'] is None
    for _ in range(32):_fold(measurement,a,window)
    assert len(a['fill_markout_monitoring'])==32 and a['metadata_overflow']


def test_candidate_automatically_measures_cancels_and_audits_without_mutating_inventory(rig,monkeypatch):
    from polymarket_scanner.v11 import candidate_assembly as app
    from polymarket_scanner.v11.audit_reports import AuditScheduler, AuditWorker
    from test_v11_audit_reports import finish
    from test_v11_candidate_assembly import plan, synthetic_clock, transport
    from test_v11_runtime_health import ready
    r=rig;filled(r);w,p,_=monitor(r,monkeypatch)
    cfg=plan(r);cfg=replace(cfg,drift=(p,),candidate=replace(cfg.candidate,maximum_jobs=4))
    synthetic_clock(r,monkeypatch);calls=[]
    async def go():
        async with httpx.AsyncClient(transport=transport(r,calls)) as client:
            candidate=app.assemble_candidate(r['store'],client,cfg,generation='fill-markout-candidate');ready(r,candidate.runtime.health)
            return await candidate.run('automatic-fill-run'),candidate
    row,candidate=asyncio.run(go());d=row['body']['details'];jobs=[j for j in d['worker_results'] if j['kind']=='DRIFT']
    assert len(jobs)==1 and jobs[0]['outcome']=='SCOPED_SAFETY_REDUCTION_APPLIED',d
    state=candidate.runtime.coordinator._state(candidate.runtime.coordinator._head())
    assert state['lots']['explicit']['units']=='1' and Decimal(state['cash'])==Decimal('9.8')
    assert all(i['cancel_requested'] for i in state['intents'].values())
    assert len(d['runtime_ids'])>=4 and d['all_async_jobs_drained'] and not d['real_orders_sent']
    r['now'][0]=(int(r['now'][0]//86400)+1)*86400+1;AuditScheduler(r['store'],cfg.audits).request_due()
    auditor=AuditWorker(candidate.runtime.coordinator,cfg.audits)
    for _ in range(3):
        report=finish(auditor)['body']['details']
        if report['operations'].get('fill_markout_monitoring'):break
    assert report['operations']['fill_markout_monitoring'][0]['scores']['n_fills']==1
    assert report['coverage']['archive_scan_complete'] and not report['financial_authority']
