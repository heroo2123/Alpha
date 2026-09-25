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
        d=original(*a,**kw);d['reasons']=['NUMERICAL_REGRESSION'];return d
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
