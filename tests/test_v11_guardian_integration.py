"""Independent guardian lease joins; synthetic protected model/account fixtures."""
from dataclasses import asdict, replace

import httpx
import pytest

from polymarket_scanner.v11 import candidate_assembly, guardian_lease, paper_guardian
from polymarket_scanner.v11.evidence import EvidenceError, digest
from test_v11_paper_guardian import worker, status
from test_v11_basket_coordinator import rig as basket_rig, reserve, coordinator
from test_v11_candidate_assembly import plan
from test_v11_maker_research import rig as maker_rig, maker
from test_v11_strategy_pipeline import factory
from test_v11_certification_rules import setup
from test_v11_model_artifacts import bundle
from test_v11_model_governance import authority


def guardian_for(c,worker):
    return paper_guardian.PaperGuardian(c,policy=guardian_lease.GuardianPolicy('integration-fixture'),
        health_config='d'*64,worker=guardian_lease.process_identity(worker.pid))


def test_all_basket_leg_submission_is_guardian_fenced_but_cancellation_survives(basket_rig,worker):
    c=coordinator(basket_rig);d=reserve(basket_rig,c=c)
    assert len(d['reserved_intent_ids'])==3
    g=guardian_for(c,worker);status(g,ready=False)
    for i,pid in enumerate(d['reserved_intent_ids']):
        with pytest.raises(EvidenceError,match='LEASE_GATED'):c.transition('submit'+str(i),intent_id=pid,status='SUBMITTING')
        c.transition('cancel'+str(i),intent_id=pid,status='CANCEL_REQUESTED')
    assert c.snapshot()['reserved_cash']==d['risk']['reserved_cash']


def test_maker_missing_guardian_gates_and_activated_guardian_retires_on_observation(maker_rig,worker):
    r=maker_rig;c=r['coordinator'];m=maker(r);c.guardian_config='d'*64
    missing=m.propose('missing-guardian',r['quote'])['body']['details']
    assert missing['outcome']=='GATED' and missing['reason']=='GUARDIAN_REQUIRED_BEFORE_OPENING'
    c.guardian_config=None
    accepted=m.propose('accepted',r['quote'])['body']['details']
    assert accepted['outcome']=='OBSERVING_RESEARCH_QUOTE'
    g=guardian_for(c,worker);status(g,ready=False)
    with pytest.raises(EvidenceError,match='LEASE_GATED'):m.revalidate_admission('quote')
    row=m.observe('check',quote_id='quote',microstructure_id='micro')
    assert row['body']['details']['quotes']['quote']['status']=='RETIRED'
    assert c._head() is None
    assert m.retire('explicit-retire',quote_id='quote',reason='SYNTHETIC')


def test_maker_cannot_commit_quote_if_lease_expires_during_write(maker_rig,worker,monkeypatch):
    r=maker_rig;m=maker(r);g=guardian_for(r['coordinator'],worker);status(g)
    original=g.store._budget
    def expire(db,size):
        original(db,size);r['now'][0]+=3
    monkeypatch.setattr(g.store,'_budget',expire)
    d=m.propose('expired',r['quote'])['body']['details']
    assert d['outcome']=='GATED' and d['reason']=='GUARDIAN_LEASE_GATED' and d['quotes']=={}


def test_candidate_configuration_requires_external_guardian_without_scheduling_it(basket_rig):
    p=plan(basket_rig);client=httpx.AsyncClient(transport=httpx.MockTransport(lambda _:pytest.fail('no HTTP expected')))
    base=candidate_assembly.assemble_candidate(basket_rig['store'],client,p,generation='base')
    guarded=candidate_assembly.assemble_candidate(basket_rig['store'],client,replace(p,guardian_config='d'*64),generation='guarded')
    assert guarded.runtime.coordinator.guardian_config=='d'*64
    assert guarded.runtime.config!=base.runtime.config and guarded.assembly_sha256!=base.assembly_sha256
    original=asdict(p);original.pop('guardian_config');original.pop('reconciliation');original['audits']=p.audits.payload()
    assert base.assembly_sha256==digest(original)
    assert not hasattr(guarded,'guardian')
    with pytest.raises(EvidenceError,match='REQUIRED_BEFORE_OPENING'):
        reserve(basket_rig,c=guarded.runtime.coordinator)


def test_candidate_guardian_config_requires_exact_digest(basket_rig):
    with pytest.raises(EvidenceError,match='INVALID_DIGEST'):replace(plan(basket_rig),guardian_config='unchecked')
