"""Shared account replay over protected synthetic basket/exit integrations."""
from copy import deepcopy
from decimal import Decimal

import pytest

from polymarket_scanner.v11.account_replay import replay_account_command
from polymarket_scanner.v11.audit_reports import AuditPolicy, AuditScheduler, AuditWorker
from polymarket_scanner.v11.causal_replay import ReplayPolicy
from polymarket_scanner.v11.model_registry import ActiveModelRegistry
from polymarket_scanner.v11.paper_coordinator import ACCOUNT_KEY
from test_v11_basket_coordinator import rig, factory, bundle, setup, reserve, proof, fill
from test_v11_model_governance import authority
from test_v11_pws_admission import coordinator
from test_v11_position_management import inventory, reserved_exit
from test_v11_fill_evidence import detailed_fill
from test_v11_audit_reports import finish


@pytest.mark.parametrize('execution_metadata', [False,True])
def test_protected_basket_inventory_exit_and_reconciliation_reach_audit_without_current_model_access(rig, monkeypatch, execution_metadata):
    inventory(rig); c=coordinator(rig); p,_=reserved_exit(rig)
    if execution_metadata:
        detailed_fill(rig,intent_id=p.proposal_id,units='.5',price='.14')
    else:
        c.record_fill('sell',proof(rig,p.proposal_id,'sale-proof','PAPER_FILL',fill_id='sale',
            units='.5',all_in_collateral='.05',direction='SELL'))
    c.transition('cancel-sale',intent_id=p.proposal_id,status='CANCEL_REQUESTED')
    c.reconcile_terminal('terminal-sale',proof(rig,p.proposal_id,'terminal-sale-proof','PAPER_TERMINAL',
        status='CANCELED',cumulative_fill_units='.5',all_fills_reconciled=True,
        terminal_authority='SYNTHETIC_PAPER_ENGINE_FINAL'))
    original_head=c._head();original_state=deepcopy(c._state(original_head))
    rows=rig['store'].records(kind='COORDINATOR_EVENT',event_id=ACCOUNT_KEY)
    policy=ReplayPolicy('historical-effects',maximum_seconds=5.)
    def forbidden(*a,**kw):pytest.fail('Conditional numeric replay touched current protected admission')
    monkeypatch.setattr(ActiveModelRegistry,'pin',forbidden)
    monkeypatch.setattr(ActiveModelRegistry,'revalidate',forbidden)
    monkeypatch.setattr('polymarket_scanner.v11.position_management.revalidate_exit',forbidden)
    for row in rows:
        d=replay_account_command(c,row['id'],policy=policy)
        assert d['status']=='EFFECTS_REPRODUCED',d
        assert not d['preparation_recomputed'] and not d['full_control_flow_replayed']
    config=AuditPolicy('effects',records_per_step=256,account_replay=policy)
    rig['now'][0]=(int(rig['now'][0]//86400)+1)*86400+1
    AuditScheduler(rig['store'],config).request_due()
    report=finish(AuditWorker(c,config))['body']['details']['account_replay']
    assert report['status']=='EFFECTS_REPRODUCED' and report['effect_matches']==len(rows),report
    assert c._head()==original_head and c._state(c._head())==original_state
    assert original_state['realized_entries'][0]['allocations'][0]['entry']['joint_ev_is_not_individual_leg_alpha']


def test_basket_numerical_rejection_keeps_all_legs_atomic_in_replay(rig):
    from dataclasses import replace
    from polymarket_scanner.v11.paper_coordinator import PaperCoordinator
    c=coordinator(rig)
    limited=PaperCoordinator(rig['store'],policy=replace(c.policy,initial_hypothetical_cash='1'),
        correlation=c.correlation,limits=c.limits)
    original=reserve(rig,c=limited)
    assert not original['reserved_intent_ids'] and not original['state']['intents']
    d=replay_account_command(limited,'batch',policy=ReplayPolicy('fixture'))
    assert d['status']=='EFFECTS_REPRODUCED',d


def test_missing_optional_execution_receipt_gates_replay_without_hiding_original_fill(rig,monkeypatch):
    from polymarket_scanner.v11.evidence import EvidenceError
    from polymarket_scanner.v11.learning_sources import LearningSourceView
    reserve(rig); original=detailed_fill(rig);before=coordinator(rig)._head()
    get=LearningSourceView.get
    def missing(self,key):
        if key=='explicit-post':raise EvidenceError('EVIDENCE_MISSING')
        return get(self,key)
    monkeypatch.setattr(LearningSourceView,'get',missing)
    d=replay_account_command(coordinator(rig),original['id'],policy=ReplayPolicy('fixture'))
    assert d['status']=='GATED' and d['reason']=='EVIDENCE_MISSING' and not d['comparisons']
    assert coordinator(rig)._head()==before


def test_later_day_cannot_replace_original_daily_loss_clock(rig):
    inventory(rig);c=coordinator(rig);p,_=reserved_exit(rig)
    row=detailed_fill(rig,intent_id=p.proposal_id,units='.5',price='.14')
    assert Decimal(row['body']['details']['risk']['daily_realized_losses'])==Decimal('.05')
    rig['now'][0]+=2*86400
    assert c.snapshot()['daily_realized_losses']=='0'
    d=replay_account_command(c,row['id'],policy=ReplayPolicy('fixture'))
    assert d['status']=='EFFECTS_REPRODUCED' and d['comparisons']['risk'],d
