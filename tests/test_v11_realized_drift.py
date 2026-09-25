"""Actual PAPER ledger/exit machinery with synthetic sources and protected reviews."""
import asyncio
from copy import deepcopy
from dataclasses import asdict, replace
from decimal import Decimal

import httpx
import pytest

from polymarket_scanner.v11 import certification
from polymarket_scanner.v11.drift import RealizedDriftPolicy, REALIZED_METHOD, REALIZED_SELECTION, measure_realized_window
from polymarket_scanner.v11.drift_runtime import DriftPlan, DriftWorker
from polymarket_scanner.v11.evidence import EvidenceError, digest
from polymarket_scanner.v11.performance import PerformanceLab
from polymarket_scanner.v11.strategy_admission import StrategyAdmission
from test_v11_certification_rules import setup
from test_v11_model_artifacts import bundle
from test_v11_strategy_pipeline import factory
from test_v11_basket_coordinator import rig, proof
from test_v11_position_management import inventory, reserved_exit, coordinator
from test_v11_drift_runtime import build


def policy():
    return RealizedDriftPolicy('SYNTHETIC_REALIZED_ONLY','SYNTHETIC',86400.,1,1,1,'0','0',REALIZED_METHOD)


def sell(r,key='sale',units='.5',proceeds='.05'):
    key=proof(r,r['exit_proposal'].proposal_id,key,'PAPER_FILL',fill_id=key,units=units,
        all_in_collateral=proceeds,direction='SELL')
    row=r['coordinator'].record_fill('record-'+key,key);r['now'][0]+=.01
    return row


@pytest.fixture
def account(rig):
    r=rig;inventory(r);p,_=reserved_exit(r);r['exit_proposal']=p;r['coordinator']=coordinator(r)
    r['coordinator'].transition('submit',intent_id=p.proposal_id,status='SUBMITTING')
    sell(r);return r


def ref(row):return dict(id=row['id'],sha256=row['sha256'],seq=row['seq'])


def measure(r,**changes):
    args=dict(scope=r['scope'],bundle_sha256=r['binding'].bundle_sha256,policy=policy(),
        account_ref=ref(r['coordinator']._head()),as_of=r['now'][0]);args.update(changes)
    return measure_realized_window(r['coordinator'],**args)


def worker(r,monkeypatch,p=None):
    w,plan,m=build(r,monkeypatch,p or policy(),c=r['coordinator'])
    m['reviews'][0]['selection']=REALIZED_SELECTION
    return w,plan,m


def test_original_entry_scope_and_fill_proofs_drive_realized_only_quality(account):
    r=account;before=r['store'].pin_read_view();d=measure(r);m=d['account_measurement'];stats=m['realization_statistics']
    assert Decimal(stats['total'])==Decimal('-.05') and Decimal(stats['max_realized_only_drawdown'])==Decimal('.05')
    assert (m['n_realizations'],m['n_events'],m['n_city_days'])==(1,1,1)
    assert m['remaining_inventory_lots']==1 and m['mark_to_market_drawdown'] is m['net_ev_capture'] is m['live_pnl'] is None
    assert d['rows'][0]['entry_intent_id']=='basket:leg:0' and d['rows'][0]['admission_ref']['id']=='pin'
    assert d['rows'][0]['entry_fill_ref']['id']=='fill-0' and d['rows'][0]['exit_fill_ref']['id']=='sale'
    assert d['threshold_breaches']==['REALIZED_WINDOW_LOSS_ABOVE_DECLARED_MAXIMUM','REALIZED_ONLY_DRAWDOWN_ABOVE_DECLARED_MAXIMUM']
    assert d['selection']==REALIZED_SELECTION and d['account_window_coverage_verified'] and not d['global_universe_coverage_verified']
    assert r['store'].pin_read_view()==before and not d['financial_authority']


def test_new_realization_automatically_reaches_reviewed_station_reduction_once(account,monkeypatch):
    r=account;w,p,_=worker(r,monkeypatch);head=w.coordinator._head();model=deepcopy(r['model_state'][0])
    row=w.step('automatic');d=row['body']['details']
    assert d['outcome']=='SCOPED_SAFETY_REDUCTION_APPLIED' and w.coordinator._head()==head and r['model_state'][0]==model
    reduction=w.store.get(d['demotion_id'])['body']['details']
    assert reduction['state']=='DISABLED' and reduction['reason']=='REVIEWED_REALIZED_PAPER_LOSS'
    with pytest.raises(EvidenceError,match='QUARANTINE_REQUIRES_NEW_REVIEW'):
        StrategyAdmission(w.store).revalidate('pin',context=r['context'],rule=r['rule'],binding=asdict(r['binding']),strategies=(r['scope'].strategy,))
    r['now'][0]+=1;assert w.step('automatic')==row
    assert w.step('idle')['body']['details']['outcome']=='NO_NEW_ACCOUNT_REALIZATION'


def test_partial_exit_fragments_do_not_inflate_event_or_city_day_counts(account,monkeypatch):
    r=account;sell(r,'sale-two');p=replace(policy(),minimum_events=2,minimum_city_days=2)
    w,_,_=worker(r,monkeypatch,p);d=w.step('cohort')['body']['details'];m=w.store.get(d['measurement_id'])['body']['details']['result']
    assert d['outcome']=='INSUFFICIENT_COHORT' and not d['demotion_applied']
    assert m['account_measurement']['n_realizations']==2 and m['account_measurement']['n_city_days']==1
    assert Decimal(m['account_measurement']['realization_statistics']['total'])==Decimal('-.1')


@pytest.mark.parametrize('other',['station','strategy','model'])
def test_known_other_scope_is_excluded_without_misattribution(account,other):
    r=account;changes={'bundle_sha256':'f'*64} if other=='model' else {'scope':replace(r['scope'],**{
        'station':'KSEA'} if other=='station' else {'strategy':'STRUCTURAL'})}
    m=measure(r,**changes)
    assert m['outcome']=='INSUFFICIENT_COHORT' and not m['rows']
    assert Decimal(m['account_measurement']['other_known_scope_pnl'])==Decimal('-.05')


def test_historical_account_snapshot_is_immutable_and_window_is_half_open(account):
    r=account;first=r['coordinator']._head();at=first['body']['details']['state']['realized_entries'][0]['at']
    before=PerformanceLab(r['coordinator']).scoped_realized(scope=r['scope'],bundle_sha256=r['binding'].bundle_sha256,
        start=at,end=r['now'][0],account_ref=ref(first))
    sell(r,'sale-two')
    replay=PerformanceLab(r['coordinator']).scoped_realized(scope=r['scope'],bundle_sha256=r['binding'].bundle_sha256,
        start=at,end=at+.01,account_ref=ref(first))
    assert replay==before
    end=PerformanceLab(r['coordinator']).scoped_realized(scope=r['scope'],bundle_sha256=r['binding'].bundle_sha256,
        start=at-1,end=at,account_ref=ref(first))
    assert end['n_realizations']==0


@pytest.mark.parametrize('fault',['unknown_entry','entry_binding','strategy_share','fill_proof','future_realization','account_gap','event_gap','fault','admission','basis'])
def test_incomplete_or_inconsistent_account_lineage_never_becomes_eligible_loss(account,monkeypatch,fault):
    r=account;c=r['coordinator'];s=c._state(c._head());piece=s['realized_entries'][0]['allocations'][0]
    if fault=='unknown_entry':piece['entry']=None
    elif fault=='entry_binding':piece['entry']['binding']['bundle_sha256']='f'*64
    elif fault=='strategy_share':piece['strategy_realized_pnl'][0]['strategy']='STRUCTURAL'
    elif fault=='fill_proof':s['fills']['sale']='f'*64
    elif fault=='future_realization':s['realized_entries'][0]['at']=r['now'][0]+10
    elif fault=='account_gap':s['event_realized_pnl']['unexplained']='1'
    elif fault=='event_gap':
        event=r['context'].event_id;s['event_realized_pnl'][event]=str(Decimal(s['event_realized_pnl'][event])+1)
        s['event_realized_pnl']['offsetting-unexplained']='-1'
    elif fault=='fault':s['faults'].append('UNRESOLVED_RECONCILIATION')
    elif fault=='admission':s['intents']['basket:leg:0']['admission_ids']=['missing']
    else:piece['allocated_basis']='.09'
    c._commit('explicit-corruption',dict(action='SYNTHETIC_FAULT'),c._head(),s,{})
    w,_,_=worker(r,monkeypatch);d=w.step('bad')['body']['details']
    assert d['outcome']=='MEASUREMENT_GATED' and not d['demotion_applied']


@pytest.mark.parametrize('boundary',['before_reduction','after_reduction'])
def test_saved_account_measurement_and_reduction_recover_once(account,monkeypatch,boundary):
    r=account;w,p,_=worker(r,monkeypatch);original=w._save
    with monkeypatch.context() as patch:
        if boundary=='before_reduction':patch.setattr(w,'_review',lambda *a:(_ for _ in ()).throw(RuntimeError('POWER_LOSS')))
        else:
            def stop(*a,**kw):
                if kw['action']=='DRIFT_RESULT':raise RuntimeError('POWER_LOSS')
                return original(*a,**kw)
            patch.setattr(w,'_save',stop)
        with pytest.raises(RuntimeError):w.step('interrupted')
    measured=[x for x in w.store.records(kind='MODEL_EVENT') if x['body']['details'].get('action')=='DRIFT_MEASUREMENT']
    assert len(measured)==1
    fresh=DriftWorker(w.coordinator,(p,));d=fresh.step('resume')['body']['details']
    assert d['demotion_applied'] and w.store.get(measured[0]['id'])==measured[0]
    assert len([x for x in w.store.records(kind='REGISTRY') if x['body']['details'].get('reason')=='REVIEWED_REALIZED_PAPER_LOSS'])==1


def test_new_account_during_recovery_gates_saved_result_and_remeasures_new_state(account,monkeypatch):
    r=account;w,_,_=worker(r,monkeypatch)
    with monkeypatch.context() as patch:
        patch.setattr(w,'_review',lambda *a:(_ for _ in ()).throw(RuntimeError('POWER_LOSS')))
        with pytest.raises(RuntimeError):w.step('interrupted')
    old=w._head()['body']['details']['state']['active'];sell(r,'sale-two')
    d=w.step('resume')['body']['details'];assert d['reason']=='DRIFT_ACCOUNT_CHANGED_BEFORE_REDUCTION' and not d['demotion_applied']
    r['now'][0]+=.01;d=w.step('new-snapshot')['body']['details'];assert d['demotion_applied']
    m=w.store.get(d['measurement_id'])['body']['details']['result']
    assert m['account_measurement']['n_realizations']==2 and m['account_ref']['id']!=old['request']['account_ref']['id']


def test_account_compare_and_swap_prevents_reduction_after_concurrent_change(account,monkeypatch):
    r=account;w,_,_=worker(r,monkeypatch);real=certification.StationRegistry.demote
    def changed(self,*a,**kw):
        c=r['coordinator'];c._commit('concurrent-snapshot',dict(action='EXPLICIT_SYNTHETIC_TEST'),c._head(),c._state(c._head()),{})
        return real(self,*a,**kw)
    with monkeypatch.context() as patch:
        patch.setattr(certification.StationRegistry,'demote',changed)
        d=w.step('raced')['body']['details']
    assert d['reason']=='AUDIT_GUARDED_STATE_CHANGED' and not d['demotion_applied']
    r['now'][0]+=.01;assert w.step('retry-new-snapshot')['body']['details']['demotion_applied']


def test_explicit_request_replay_does_not_renew_and_old_snapshot_cannot_be_new_request(account,monkeypatch):
    r=account;w,p,_=worker(r,monkeypatch);first=w.coordinator._head();now=r['now'][0]
    old=w.request_account('manual',plan_key=p.key,account_id=first['id'],as_of=now)
    w.step('done');r['now'][0]+=.01
    c=w.coordinator;c._commit('new-head',dict(action='EXPLICIT_SYNTHETIC_TEST'),c._head(),c._state(c._head()),{})
    assert w.request_account('manual',plan_key=p.key,account_id=first['id'],as_of=now)==old
    with pytest.raises(EvidenceError,match='CURRENT_ACCOUNT_SNAPSHOT'):
        w.request_account('different',plan_key=p.key,account_id=first['id'],as_of=r['now'][0])


def test_nonbreach_does_not_clear_existing_demotion(account,monkeypatch):
    r=account;w,_,_=worker(r,monkeypatch,replace(policy(),maximum_realized_loss='.05',maximum_realized_drawdown='.05'))
    old=certification.StationRegistry(w.store).demote('prior',r['scope'],state='DISABLED',reason='PRIOR',evidence_ids=('model2',))
    d=w.step('healthy')['body']['details'];assert d['outcome']=='NO_DECLARED_BREACH' and not d['demotion_applied']
    assert w.store.latest(kind='REGISTRY',event_id=old['event_id'])==old


@pytest.mark.parametrize('changes',[dict(evidence_class='PUBLIC_OBSERVED'),dict(metric_method='LIVE_PNL'),
    dict(maximum_realized_loss='-1'),dict(maximum_realized_drawdown='NaN'),dict(minimum_realizations=True)])
def test_policy_cannot_claim_live_or_unbounded_loss(changes):
    with pytest.raises(EvidenceError):replace(policy(),**changes)


def test_new_profitability_target_needs_its_own_predeclared_review(account,monkeypatch):
    r=account;w,_,m=worker(r,monkeypatch);m['reviews'][0]['selection']='EXPLICIT_CAPTURE_COHORT'
    d=w.step('wrong-target')['body']['details']
    assert d['outcome']=='REDUCTION_GATED' and not d['demotion_applied']


def test_candidate_automatically_monitors_new_realizations_and_preserves_reducing_exit(account,monkeypatch):
    from test_v11_candidate_assembly import app, plan, synthetic_clock, transport
    from test_v11_runtime_health import ready
    from polymarket_scanner.v11.drift import DriftPolicy
    r=account;p=DriftPlan(r['scope'],r['binding'].bundle_sha256,policy());cfg=plan(r)
    quality=DriftPlan(r['scope'],r['binding'].bundle_sha256,DriftPolicy('SEPARATE_QUALITY','FORECAST','SYNTHETIC',86400.,1,1,.5,1.))
    cfg=replace(cfg,drift=(quality,p),candidate=replace(cfg.candidate,maximum_jobs=4));synthetic_clock(r,monkeypatch);calls=[]
    async def go():
        async with httpx.AsyncClient(transport=transport(r,calls)) as client:
            candidate=app.assemble_candidate(r['store'],client,cfg,generation='realized-candidate')
            worker(r,monkeypatch);ready(r,candidate.runtime.health)
            row=await candidate.run('automatic-realized-run')
            return row,candidate
    row,candidate=asyncio.run(go());d=row['body']['details'];jobs=[j for j in d['worker_results'] if j['kind']=='DRIFT']
    assert len(jobs)==1 and jobs[0]['outcome']=='SCOPED_SAFETY_REDUCTION_APPLIED',d
    state=candidate.runtime.coordinator._state(candidate.runtime.coordinator._head())
    assert state['intents'][r['exit_proposal'].proposal_id]['status']=='PARTIAL' and state['lots']['fill-0']['units']=='1.5'
    assert len(d['runtime_ids'])>=4 and d['all_async_jobs_drained'] and not d['real_orders_sent']
    from polymarket_scanner.v11.audit_reports import AuditScheduler, AuditWorker
    from test_v11_audit_reports import finish
    r['now'][0]=(int(r['now'][0]//86400)+1)*86400+1;AuditScheduler(r['store'],cfg.audits).request_due()
    auditor=AuditWorker(candidate.runtime.coordinator,cfg.audits)
    for _ in range(3):
        report=finish(auditor)['body']['details']
        if report['operations']['drift_outcomes']:break
    assert report['operations']['drift_outcomes']=={'SCOPED_SAFETY_REDUCTION_APPLIED':1}
    assert report['coverage']['archive_scan_complete'] and not report['financial_authority']
