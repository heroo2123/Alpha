"""Original numerical basket/exit proofs; all sources/markets are synthetic."""
from copy import deepcopy
from dataclasses import replace

import pytest

from polymarket_scanner.v11.account_replay import replay_account_command, account_replay_audit, fold_account_commands
from polymarket_scanner.v11.audit_reports import AuditPolicy, AuditScheduler, AuditWorker
from polymarket_scanner.v11.causal_replay import ReplayPolicy
from polymarket_scanner.v11.evidence import EvidenceError
from polymarket_scanner.v11.model_registry import ActiveModelRegistry
from polymarket_scanner.v11.paper_coordinator import ACCOUNT_KEY, PaperCoordinator
from test_v11_basket_coordinator import rig, factory, bundle, setup, reserve, proof, fill, replace_book
from test_v11_model_governance import authority
from test_v11_pws_admission import coordinator
from test_v11_position_management import inventory, reserved_exit
from test_v11_audit_reports import finish


def replay(rig, key='batch', **kw):
    return replay_account_command(coordinator(rig), key, policy=ReplayPolicy('portfolio',5.), replay_valuations=True, **kw)


def proof_row(result):
    assert result['status']=='EFFECTS_REPRODUCED', result
    values=result['prepared_valuations']
    assert values['prepared_count']==1 and values['complete_prepared_selection'], values
    return values['rows'][0]


@pytest.mark.parametrize('rig', ['CROSS_TEMP_RELATIVE_VALUE','STRUCTURAL'], indirect=True)
def test_basket_original_numeric_engine_without_journal_or_current_admission(rig,monkeypatch):
    reserve(rig); c=coordinator(rig); head=c._head()
    def forbidden(*a,**kw):pytest.fail('Historical numerical valuation issued a write or current approval')
    monkeypatch.setattr(ActiveModelRegistry,'pin',forbidden)
    monkeypatch.setattr(ActiveModelRegistry,'revalidate',forbidden)
    monkeypatch.setattr('polymarket_scanner.v11.strategy_admission.StrategyAdmission.revalidate',forbidden)
    monkeypatch.setattr('polymarket_scanner.v11.evidence.EvidenceStore.audit',forbidden)
    result=replay(rig); row=proof_row(result)
    assert row['status']=='ECONOMICS_REPRODUCED',row
    assert row['original_prediction_sha256']==row['recomputed_prediction_sha256']
    assert row['original_valuation_sha256']==row['recomputed_valuation_sha256']
    assert row['input_evidence_classes']==['SYNTHETIC'] and row['book_derivation']['input_evidence_classes']==['SYNTHETIC']
    assert row['source_derivation_status']=='NO_ARCHIVED_DERIVATION_EDGES'
    assert row['model']['retained_history_verified'] and not row['model']['current_pointer_used_for_inference']
    assert c._head()==head and not result['preparation_recomputed']


def test_exit_original_inventory_survives_later_fill_book_and_day(rig,monkeypatch):
    inventory(rig); c=coordinator(rig); original_inventory=c._head(); p,_=reserved_exit(rig)
    first=replay(rig,'reserve-exit'); row=proof_row(first)
    assert row['status']=='ECONOMICS_REPRODUCED',row
    assert row['inventory_ref']['id']==original_inventory['id']
    c.record_fill('sale',proof(rig,p.proposal_id,'sale-proof','PAPER_FILL',fill_id='sale',
        units='.5',all_in_collateral='.05',direction='SELL'))
    rig['now'][0]+=2*86400
    replace_book(rig,0,bids=[dict(price='.9',size='20')])
    head=c._head();state=deepcopy(c._state(head))
    def forbidden(*a,**kw):pytest.fail('Current protected inference was invoked')
    monkeypatch.setattr(ActiveModelRegistry,'pin',forbidden)
    monkeypatch.setattr('polymarket_scanner.v11.position_management.revalidate_exit',forbidden)
    assert replay(rig,'reserve-exit')==first
    assert c._head()==head and c._state(head)==state


@pytest.mark.parametrize('exit', [False,True])
def test_recomputation_detects_numerical_defect_without_copying_archived_value(rig,monkeypatch,exit):
    import polymarket_scanner.v11.portfolio_replay as module
    if exit: inventory(rig); reserved_exit(rig)
    else: reserve(rig)
    name='_value' if exit else 'basket_details'; original=getattr(module,name)
    def changed(*a,**kw):
        d=original(*a,**kw)
        d['sale_advantage_per_share' if exit else 'conservative_full_fill_ev_total']='100'
        return d
    monkeypatch.setattr(module,name,changed)
    result=replay(rig,'reserve-exit' if exit else 'batch');row=proof_row(result)
    assert row['status']=='MISMATCH' and row['comparisons']['prediction']
    assert not row['comparisons']['valuation'] and not result['prepared_valuations']['all_prepared_valuations_reproduced']


@pytest.mark.parametrize('missing', ['pin','model2','basket-book0','exit:start','reconcile2'])
def test_missing_original_receipt_gates_valuation_without_using_current_state(rig,monkeypatch,missing):
    from polymarket_scanner.v11.learning_sources import LearningSourceView
    inventory(rig);reserved_exit(rig);get=LearningSourceView.get
    def absent(self,key):
        if key==missing:raise EvidenceError('EVIDENCE_MISSING')
        return get(self,key)
    monkeypatch.setattr(LearningSourceView,'get',absent)
    d=replay(rig,'reserve-exit')
    if missing in {'model2','reconcile2'}:
        assert d['status']=='GATED' and not d['effects_match'] and 'prepared_valuations' not in d,d
        return
    row=proof_row(d)
    assert row['status']=='GATED' and not row['economic_match'],row
    assert 'recomputed_valuation_sha256' not in row


def test_account_rejected_basket_remains_in_numerical_denominator(rig):
    c=coordinator(rig);limited=PaperCoordinator(rig['store'],policy=replace(c.policy,initial_hypothetical_cash='1'),
        correlation=c.correlation,limits=c.limits)
    reserve(rig,c=limited)
    d=replay_account_command(limited,'batch',policy=ReplayPolicy('portfolio'),replay_valuations=True)
    assert proof_row(d)['status']=='ECONOMICS_REPRODUCED'
    assert limited._state(limited._head())['intents']=={}


def test_portfolio_and_exit_valuation_proofs_reach_existing_account_audit(rig):
    inventory(rig);reserved_exit(rig);c=coordinator(rig);head=c._head()
    config=AuditPolicy('portfolio',records_per_step=256,account_replay=ReplayPolicy('portfolio',5.),account_valuation_replay=True)
    rig['now'][0]=(int(rig['now'][0]//86400)+1)*86400+1
    AuditScheduler(rig['store'],config).request_due()
    report=finish(AuditWorker(c,config))['body']['details']
    coverage=report['account_replay']['prepared_valuation_coverage']
    assert coverage['complete'] and coverage['prepared_count']==2 and coverage['economic_matches']==2,report['account_replay']
    assert coverage['all_prepared_valuations_reproduced'] and report['coverage']['retained_prepared_valuations_reproduced']
    assert not report['account_replay']['preparation_recomputed'] and c._head()==head


@pytest.mark.parametrize('option', [1,'true',None])
def test_valuation_option_strict_and_default_policy_payload_unchanged(option):
    with pytest.raises(EvidenceError,match='AUDIT_ACCOUNT_VALUATION_REPLAY_POLICY_REQUIRED'):
        AuditPolicy('fixture',account_replay=ReplayPolicy('fixture'),account_valuation_replay=option)
    with pytest.raises(EvidenceError,match='AUDIT_ACCOUNT_VALUATION_REPLAY_POLICY_REQUIRED'):
        AuditPolicy('fixture',account_valuation_replay=True)
    assert 'account_valuation_replay' not in AuditPolicy('fixture').payload()


@pytest.mark.parametrize('exit', [False,True])
def test_original_portfolio_model_survives_promotion_and_missing_history_gates(rig,bundle,monkeypatch,exit):
    from test_v11_model_governance import new_bundle,promote
    from polymarket_scanner.v11 import model_registry
    if exit: inventory(rig);reserved_exit(rig)
    else: reserve(rig)
    key='reserve-exit' if exit else 'batch';before=replay(rig,key)
    assert proof_row(before)['status']=='ECONOMICS_REPRODUCED'
    rig['model_state'][0]=promote(new_bundle(bundle),rig['model_state'][0],review_id='newer')
    assert replay(rig,key)==before
    def missing(**kw):raise EvidenceError('PROTECTED_MODEL_STATE_UNAVAILABLE')
    monkeypatch.setattr(model_registry,'protected_state',missing)
    row=proof_row(replay(rig,key))
    assert row['status']=='GATED' and row['reason']=='PROTECTED_MODEL_STATE_UNAVAILABLE'


def audit(rig,**kw):
    c=coordinator(rig);window=dict(start=0,end=rig['now'][0]+1);aggregate={}
    commands=rig['store'].records(kind='COORDINATOR_EVENT',event_id=ACCOUNT_KEY)
    for row in commands:fold_account_commands(row,aggregate,window)
    return account_replay_audit(c,selection=aggregate['account_replay_selection'],through_seq=commands[-1]['seq'],
        window=window,archive_complete=True,policy=ReplayPolicy('portfolio',5.),replay_valuations=True,**kw)


def test_unknown_command_prevents_positive_preparation_coverage_but_keeps_supported_comparison(rig):
    reserve(rig);original=deepcopy(coordinator(rig)._head()['body']['details'])
    original['request']=dict(action='UNKNOWN_COMMAND')
    rig['store'].audit('unknown',kind='COORDINATOR_EVENT',event_id=ACCOUNT_KEY,details=original)
    d=audit(rig);v=d['prepared_valuation_coverage']
    assert d['complete_retained_selection'] and len(d['rows'])==2
    assert v['economic_matches']==1 and not v['complete'] and v['prepared_count'] is None
    assert not v['all_prepared_valuations_reproduced'] and d['rows'][1]['status']=='GATED'


def test_valuation_work_shares_whole_audit_budget_and_discards_favorable_prefix(rig,monkeypatch):
    import polymarket_scanner.v11.portfolio_replay as module
    reserve(rig);elapsed=[0.];original=module.basket_details
    def spent(*a,**kw):
        value=original(*a,**kw);elapsed[0]=6.;return value
    monkeypatch.setattr(module,'basket_details',spent)
    d=audit(rig,monotonic=lambda:elapsed[0])
    assert d['status']=='GATED' and not d['rows'] and not d['effect_matches']
    assert not d['prepared_valuation_coverage']['economic_matches']
    assert not d['prepared_valuation_coverage']['complete']


def test_published_valuation_report_recovers_without_recomputing_or_reserving(rig,monkeypatch):
    import polymarket_scanner.v11.account_replay as module
    inventory(rig);reserved_exit(rig);c=coordinator(rig);head=c._head()
    config=AuditPolicy('portfolio',records_per_step=256,account_replay=ReplayPolicy('portfolio',5.),account_valuation_replay=True)
    rig['now'][0]=(int(rig['now'][0]//86400)+1)*86400+1
    AuditScheduler(rig['store'],config).request_due();worker=AuditWorker(c,config);save=worker._save
    def crash(head,state,**kw):
        if kw.get('outcome')=='AUDIT_COMPLETE':raise OSError('SYNTHETIC_AFTER_REPORT')
        return save(head,state,**kw)
    monkeypatch.setattr(worker,'_save',crash)
    with pytest.raises(OSError,match='SYNTHETIC_AFTER_REPORT'):finish(worker)
    report=rig['store'].latest(kind='RUNTIME_STATUS',event_id='v11-audit-report:DAILY')
    assert report['body']['details']['account_replay']['prepared_valuation_coverage']['all_prepared_valuations_reproduced']
    monkeypatch.setattr(module,'replay_account_command',lambda *a,**kw:pytest.fail('Replayed already published report'))
    assert finish(AuditWorker(c,config))==report and c._head()==head


def test_typed_candidate_schedules_original_valuation_audit_while_new_risk_stays_gated(rig,monkeypatch):
    import asyncio
    import httpx
    from polymarket_scanner.v11 import candidate_assembly as app
    from test_v11_candidate_assembly import plan,synthetic_clock
    from test_v11_book_inputs import response
    from test_v11_runtime_health import ready,advance
    reserve(rig);original_ids=set(coordinator(rig)._state(coordinator(rig)._head())['intents'])
    cfg=plan(rig);cfg=replace(cfg,audits=replace(cfg.audits,records_per_step=256,
        account_replay=ReplayPolicy('candidate-portfolio',5.),account_valuation_replay=True))
    synthetic_clock(rig,monkeypatch);calls=[]
    def transport(req):
        calls.append(req)
        if req.url.host=='gamma-api.polymarket.com':return httpx.Response(200,json=dict(events=[],next_cursor=None))
        if req.url.host=='aviationweather.gov':return httpx.Response(200,json=[dict(icaoId=rig['context'].station_id,obsTime=rig['now'][0]-1,temp=25)])
        book=response(rig,req.url.params['token_id'])
        book.update(asks=[dict(price='.2',size='20')],bids=[dict(price='.19',size='20')],min_order_size='1')
        return httpx.Response(200,json=book)
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as client:
            candidate=app.assemble_candidate(rig['store'],client,cfg,generation='portfolio-candidate')
            ready(rig,candidate.runtime.health)
            first=await candidate.run('portfolio-source-decision')
            state=candidate.runtime.coordinator._state(candidate.runtime.coordinator._head())
            assert set(state['intents'])==original_ids,first['body']['details']
            decisions=[r['body']['details'] for r in rig['store'].records(kind='MEASUREMENT',limit=1000)
                if r['body']['details'].get('version')=='alpha_v11_whole_event_relative_value_v1'
                and 'outcome' in r['body']['details']]
            assert decisions and all(d['reason']=='EVENT_STATE_SUPPRESSES_BASKET_DISCOVERY' for d in decisions)
            advance(rig,(int(rig['now'][0]//86400)+1)*86400+1-rig['now'][0])
            for i in range(12):
                await candidate.run('portfolio-scheduled-audit-'+str(i))
                report=rig['store'].latest(kind='RUNTIME_STATUS',event_id='v11-audit-report:DAILY')
                if report and report['body']['details']['account_replay']['retained_command_count']:
                    return candidate,report['body']['details']
            pytest.fail('Candidate did not finish scheduled portfolio audit')
    candidate,report=asyncio.run(run());d=report['account_replay'];v=d['prepared_valuation_coverage']
    assert v['complete'] and v['prepared_count']==1 and v['economic_matches']==1,d
    proof=next(r['prepared_valuations']['rows'][0] for r in d['rows'] if r['prepared_valuations'])
    assert proof['valuation_id']=='basket-value' and proof['book_derivation']['input_evidence_classes']==['SYNTHETIC']
    assert calls and all(r.method=='GET' for r in calls) and not rig['store'].records(kind='TRADE')
    assert not report['acceptance_granted'] and not candidate.runtime.coordinator._state(candidate.runtime.coordinator._head())['financial_authority']


def test_mock_census_books_protected_strategy_and_common_account_replay_raw_lineage(rig,monkeypatch):
    from test_v11_strategy_runtime import joined,run_candidate
    from polymarket_scanner.v11.learning_sources import LearningSourceView
    # This existing integration explicitly declares synthetic healthy event
    # metrics. It is not evidence that the fully derived candidate is eligible.
    rt=joined(rig,monkeypatch);run_candidate(rig,rt)
    commands=[r for r in rig['store'].records(kind='COORDINATOR_EVENT',event_id=ACCOUNT_KEY)
        if r['body']['details']['request']['action']=='COORDINATE']
    assert commands
    results=[replay(rig,command['id']) for command in commands]
    assert all(d['status']=='EFFECTS_REPRODUCED' for d in results),results
    proofs=[r for d in results for r in d['prepared_valuations']['rows']]
    assert proofs and all(r['status']=='ECONOMICS_REPRODUCED' and r['book_derivation']['source_derivation_sha256'] for r in proofs),proofs
    row=proofs[0]
    assert not rig['store'].records(kind='TRADE')
    book=rig['store'].get(row['book_derivation']['input_refs'][0]['id'])
    raw_id=book['body']['payload']['raw_evidence_id'];get=LearningSourceView.get
    def missing(self,key):
        if key==raw_id:raise EvidenceError('EVIDENCE_MISSING')
        return get(self,key)
    monkeypatch.setattr(LearningSourceView,'get',missing)
    row=proof_row(replay(rig,commands[0]['id']))
    assert row['status']=='GATED' and row['reason']=='EVIDENCE_MISSING' and not row['economic_match']
