from copy import deepcopy
from dataclasses import asdict, replace
from decimal import Decimal

import pytest

from polymarket_scanner.v11.audit_reports import AuditPolicy, AuditScheduler, AuditWorker
from polymarket_scanner.v11.evidence import EvidenceError, digest
from polymarket_scanner.v11.execution_costs import ExecutionCostPolicy
from polymarket_scanner.v11.performance import PerformanceLab
from test_v11_fill_evidence import rig, setup, bundle, factory, reserve, fill, proof, detailed_fill, book, coordinator
from test_v11_position_management import inventory, reserved_exit
from test_v11_audit_reports import finish


def policy(**kw):return replace(ExecutionCostPolicy('SYNTHETIC_TEST_ONLY',30.,30.),**kw)


def costs(r,**kw):
    r['now'][0]+=.001
    args=dict(start=0.,end=r['now'][0],policy=policy());args.update(kw)
    return PerformanceLab(coordinator(r)).execution_costs(**args)


def fill_with_book(r,*,key='explicit',post=None,post_ref=None,**kw):
    def mutate(d):
        if post is not None:
            b=book(r,key+'-compared',r['store'].get(d['post_validation_book_ref']['id']),**post)
            d['post_validation_book_ref']=dict(id=b['id'],sha256=b['sha256'])
        if post_ref is not None:d['post_validation_book_ref']=post_ref
    return detailed_fill(r,key=key,mutate=mutate,**kw)


def test_buy_costs_and_price_change_conserve_without_extra_ledger_charge(rig):
    reserve(rig);fill_with_book(rig,post={'asks':[dict(price='.22',size='100')]})
    c=coordinator(rig);before=c._head();d=costs(rig);row=d['rows'][0]
    assert d['status']=='COMPLETE_SYNTHETIC_COSTS_AND_COMPARISONS',d
    assert Decimal(row['price_shortfall_vs_signal'])==Decimal('-.02')
    assert Decimal(row['price_shortfall_vs_post_validation'])==Decimal('-.04')
    assert Decimal(row['signal_to_post_price_change'])==Decimal('.02')
    assert Decimal(row['total_cost_vs_signal'])==0 and d['fees']==d['other_costs']=='0.01'
    assert row['joint_ev_is_not_allocated'] and d['realized_ev'] is d['ev_capture_ratio'] is None
    assert c._head()==before and Decimal(c._state(before)['cash'])==Decimal('9.8')
    assert d['additional_pnl_adjustment']=='0' and not d['venue_execution_attested']


def test_sell_cost_signs_keep_entry_basis_and_realized_pnl_separate(rig):
    inventory(rig);p,_=reserved_exit(rig)
    start=rig['now'][0]+.001
    fill_with_book(rig,intent_id=p.proposal_id,units='.5',price='.14',post={'bids':[dict(price='.12',size='100')]})
    d=costs(rig,start=start);row=d['rows'][0]
    assert d['complete_cost_population'] and row['direction']=='SELL'
    assert Decimal(row['price_shortfall_vs_signal'])==Decimal('-.02')
    assert Decimal(row['price_shortfall_vs_post_validation'])==Decimal('-.01')
    assert Decimal(row['signal_to_post_price_change'])==Decimal('-.01')
    report=PerformanceLab(coordinator(rig)).build(start=start,end=rig['now'][0],execution_policy=policy())
    assert Decimal(report['paper_realized_pnl'])==Decimal('-.05') and report['execution_costs']==d
    assert report['fees'] is None  # Historical realized-P&L cohort is not the execution-window cohort.


@pytest.mark.parametrize('change,reason',[
    ({'stream_healthy':False},'BOOK_UNHEALTHY'),
    ({'asks':[dict(price='.05',size='100')]},'EMPTY_OR_CROSSED'),
    ({'asks':[dict(price='.2',size='.5')]},'INSUFFICIENT_CUMULATIVE_DEPTH'),
    ({'asks':[]},'EMPTY_OR_CROSSED'),
    ({'raw_evidence_id':'missing','raw_evidence_sha256':'a'*64},'EVIDENCE_MISSING'),
])
def test_bad_benchmark_preserves_valid_costs_but_never_invents_slippage(rig,change,reason):
    reserve(rig);fill_with_book(rig,post=change);d=costs(rig);row=d['rows'][0]
    assert d['status']=='PARTIAL' and d['fees']=='0.01' and d['complete_cost_population']
    assert reason in row['post_validation']['reason'] and row['price_shortfall_vs_post_validation'] is None
    assert not d['complete_price_comparisons'] and not d['price_comparison_groups']


def test_stale_book_uses_execution_time_and_does_not_refresh_on_report(rig):
    reserve(rig);detailed_fill(rig);d=costs(rig,policy=policy(maximum_post_validation_age_seconds=.005))
    assert d['rows'][0]['post_validation']['reason']=='EXECUTION_COST_BOOK_STALE'
    rig['now'][0]+=1000
    again=costs(rig,policy=policy(maximum_post_validation_age_seconds=.005))
    assert again['rows']==d['rows'] and again['fees']==d['fees']


@pytest.mark.parametrize('mutate',[lambda d:d.update(fee_collateral='.02'),lambda d:d.update(executed_at=None),
    lambda d:d.update(timing_authority='VENUE'),lambda d:d.update(signal_book_ref={'id':'missing','sha256':'a'*64})])
def test_bad_optional_details_remain_in_population_as_unknown(rig,mutate):
    reserve(rig);detailed_fill(rig,mutate=mutate);d=costs(rig)
    assert d['selected_fill_count']==d['unknown_cost_count']==1 and d['fees'] is None
    assert d['rows'][0]['time_status']=='POSSIBLE_WINDOW_OVERLAP' and not d['complete_cost_population']
    assert Decimal(coordinator(rig)._state(coordinator(rig)._head())['cash'])==Decimal('9.8')


def test_legacy_and_partial_fills_do_not_reuse_the_same_visible_depth(rig):
    reserve(rig);first=fill_with_book(rig,post={'asks':[dict(price='.2',size='1'),dict(price='.3',size='1')]})
    ref=first['body']['details']['execution_evidence']['post_validation_book_ref']
    fill_with_book(rig,key='second',post_ref=ref)
    d=costs(rig);a,b=d['rows']
    assert a['post_validation']['prior_units']=='0' and b['post_validation']['prior_units']=='1'
    assert Decimal(a['post_validation']['gross_collateral'])==Decimal('.2')
    assert Decimal(b['post_validation']['gross_collateral'])==Decimal('.3')
    assert d['fees']=='0.02' and d['known_cost_subtotals']['fills']==2
    assert sum(Decimal(row['price_shortfall_vs_post_validation']) for row in d['rows'])==Decimal('-.14')


def test_out_of_window_partial_fill_still_consumes_original_signal_depth(rig):
    reserve(rig);detailed_fill(rig,units='.5');start=rig['now'][0]+.001
    detailed_fill(rig,key='later',units='1.5');d=costs(rig,start=start)
    assert d['selected_fill_count']==1 and d['reconciled_fill_count']==2
    assert d['rows'][0]['signal']['prior_units']=='0.5' and d['fees']=='0.01'


def test_legacy_same_intent_keeps_comparison_order_unknown_and_total_fees_partial(rig):
    reserve(rig);fill(rig,0,'1','.2');detailed_fill(rig);d=costs(rig)
    assert d['selected_fill_count']==2 and d['unknown_cost_count']==1 and d['fees'] is None
    known=next(row for row in d['rows'] if row['cost_status']=='VALIDATED_SYNTHETIC_DETAILS')
    assert known['signal']['reason']=='EXECUTION_COST_PARTIAL_FILL_ORDER_UNKNOWN'
    assert d['known_cost_subtotals']==dict(fees='0.01',other_costs='0.01',fills=1,is_full_window_total=False)


def test_missing_details_overlap_window_without_guessing_a_receipt_execution_time(rig):
    reserve(rig);start=rig['now'][0]+1;rig['now'][0]+=10;fill(rig,0)
    d=costs(rig,start=start,end=start+1)
    assert d['selected_fill_count']==1 and d['rows'][0]['executed_at'] is None
    assert d['rows'][0]['time_status']=='POSSIBLE_WINDOW_OVERLAP' and d['fees'] is None


def test_pinned_account_excludes_later_receipts_and_cost_reporting_is_read_only(rig):
    reserve(rig);detailed_fill(rig);head=coordinator(rig)._head();detailed_fill(rig,key='later')
    rig['now'][0]+=.001;before=rig['store'].pin_read_view()
    d=PerformanceLab(coordinator(rig)).execution_costs(start=0,end=rig['now'][0],account_row=head,policy=policy())
    assert d['selected_fill_count']==1 and d['fees']=='0.01' and rig['store'].pin_read_view()==before
    assert costs(rig)['selected_fill_count']==2


def test_tampered_account_or_missing_proof_cannot_publish_subset(rig):
    reserve(rig);detailed_fill(rig);head=deepcopy(coordinator(rig)._head())
    head['body']['details']['state']['fills'].clear()
    d=costs(rig,account_row=head)
    assert d['status']=='GATED' and d['reason']=='EXECUTION_COST_ACCOUNT_SNAPSHOT' and d['fees'] is None and not d['rows']


def test_cohort_and_deadline_bounds_clear_partial_totals(rig):
    reserve(rig);detailed_fill(rig);detailed_fill(rig,key='second')
    d=costs(rig,policy=policy(maximum_fills=1))
    assert d['status']=='GATED' and d['reason']=='EXECUTION_COST_COHORT_BOUND' and not d['rows'] and d['fees'] is None
    calls=[0]
    def mono():calls[0]+=1;return float(calls[0])
    d=costs(rig,policy=policy(maximum_seconds=.05),monotonic=mono)
    assert d['status']=='GATED' and d['fees'] is None and not d['rows']


def test_fractional_quantity_metrics_do_not_use_ledger_precision_limits(rig):
    reserve(rig);detailed_fill(rig,units='1.4');d=costs(rig)
    assert d['status']=='COMPLETE_SYNTHETIC_COSTS_AND_COMPARISONS',d
    assert d['fees']=='0.01' and Decimal(d['rows'][0]['total_cost_vs_signal'])==Decimal('-.008')


def test_legacy_default_performance_and_audit_identity_are_preserved(rig):
    reserve(rig);fill(rig,0);rig['now'][0]+=.01;c=coordinator(rig);lab=PerformanceLab(c)
    before=lab.build(start=0,end=rig['now'][0]);lab.execution_costs(start=0,end=rig['now'][0],policy=policy())
    assert lab.build(start=0,end=rig['now'][0])==before and 'execution_costs' not in before
    legacy=AuditPolicy('fixture');raw=dict(version='fixture',records_per_step=64,maximum_step_seconds=2.,maximum_job_records=20000)
    assert AuditScheduler(rig['store'],legacy).config==digest(raw)
    assert AuditWorker(c,legacy).schedule_config==digest(raw)


def test_scheduled_audit_pins_costs_and_replays_completed_report(rig,monkeypatch):
    reserve(rig);detailed_fill(rig);c=coordinator(rig)
    p=AuditPolicy('cost-audit',records_per_step=8,execution_costs=policy())
    rig['now'][0]=(int(rig['now'][0]//86400)+1)*86400+1
    AuditScheduler(rig['store'],p).request_due();w=AuditWorker(c,p)
    first=w.step();assert first['outcome']=='AUDIT_PARTIAL_PROGRESS'
    detailed_fill(rig,key='after-pin')
    report=finish(AuditWorker(c,p));d=report['body']['details'];cost=d['PAPER']['execution_costs']
    assert cost['selected_fill_count']==1 and cost['fees']=='0.01'
    assert d['coverage']['execution_cost_population_complete'] and d['coverage']['execution_price_comparisons_complete']
    assert d['unresolved']['slippage'].endswith('COMPLETE_SYNTHETIC_COSTS_AND_COMPARISONS')
    assert not d['financial_authority'] and not d['messages_sent']


def test_audit_restart_after_publication_preserves_exact_cost_report(rig,monkeypatch):
    reserve(rig);detailed_fill(rig);c=coordinator(rig);p=AuditPolicy('replay',records_per_step=256,execution_costs=policy())
    rig['now'][0]=(int(rig['now'][0]//86400)+1)*86400+1;AuditScheduler(rig['store'],p).request_due()
    w=AuditWorker(c,p);original=w._save
    def crash(head,state,**details):
        if details.get('outcome')=='AUDIT_COMPLETE':raise OSError('SYNTHETIC_REPORT_CRASH')
        return original(head,state,**details)
    monkeypatch.setattr(w,'_save',crash)
    with pytest.raises(OSError,match='SYNTHETIC_REPORT_CRASH'):finish(w)
    row=rig['store'].latest(kind='RUNTIME_STATUS',event_id='v11-audit-report:DAILY')
    head=c._head();again=finish(AuditWorker(c,p))
    assert again==row and again['body']['details']['PAPER']['execution_costs']['fees']=='0.01' and c._head()==head


def test_execution_window_excludes_later_fill_without_changing_earlier_comparison(rig):
    reserve(rig);detailed_fill(rig);first=costs(rig);end=rig['now'][0]
    detailed_fill(rig,key='later');later=costs(rig,end=end)
    assert later['rows']==first['rows'] and later['fees']==first['fees'] and later['reconciled_fill_count']==2


def test_cost_audit_policy_change_cannot_reuse_old_request_or_worker_identity(rig):
    reserve(rig);c=coordinator(rig);p=AuditPolicy('same-name',execution_costs=policy())
    rig['now'][0]=(int(rig['now'][0]//86400)+1)*86400+1
    first=AuditScheduler(rig['store'],p);first.request_due()
    altered=replace(p,execution_costs=policy(maximum_post_validation_age_seconds=1.))
    assert AuditWorker(c,p).config!=AuditWorker(c,altered).config
    with pytest.raises(EvidenceError,match='AUDIT_POLICY_CHANGED_REVIEW_REQUIRED'):
        AuditScheduler(rig['store'],altered).request_due()


def test_candidate_archive_receipts_reach_scheduled_cost_audit(rig,monkeypatch):
    import asyncio
    import httpx
    from polymarket_scanner.v11 import candidate_assembly as app
    from polymarket_scanner.v11.paper_reconciliation import ReconciliationPolicy
    from test_v11_candidate_assembly import plan, synthetic_clock, transport
    from test_v11_runtime_health import ready, advance
    reserve(rig);detailed_fill(rig,reconcile=False)
    cfg=plan(rig);cfg=replace(cfg,reconciliation=ReconciliationPolicy('cost-candidate'),
        audits=replace(cfg.audits,records_per_step=256,execution_costs=policy()))
    synthetic_clock(rig,monkeypatch);calls=[]
    async def run():
        async with httpx.AsyncClient(transport=transport(rig,calls)) as client:
            candidate=app.assemble_candidate(rig['store'],client,cfg,generation='execution-cost-candidate')
            ready(rig,candidate.runtime.health)
            await candidate.run('cost-input')
            assert candidate.runtime.coordinator._state(candidate.runtime.coordinator._head())['fills']
            advance(rig,(int(rig['now'][0]//86400)+1)*86400+1-rig['now'][0])
            report=None
            for i in range(12):
                row=await candidate.run('cost-report-'+str(i))
                assert not row['body']['details']['real_orders_sent']
                report=rig['store'].latest(kind='RUNTIME_STATUS',event_id='v11-audit-report:DAILY')
                if report and report['body']['details']['PAPER']['execution_costs']['fees']=='0.01':break
            return candidate,report
    candidate,report=asyncio.run(run());assert report is not None
    d=report['body']['details'];assert d['PAPER']['execution_costs']['fees']=='0.01',d
    assert d['PAPER']['execution_costs']['rows'][0]['fill_id']=='explicit'
    assert d['coverage']['execution_price_comparisons_complete'] and not d['acceptance_granted']
    assert candidate.audits.policy.execution_costs==cfg.audits.execution_costs
