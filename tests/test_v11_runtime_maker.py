from copy import deepcopy
from dataclasses import replace

import pytest

from polymarket_scanner.v11.evidence import EvidenceError
from polymarket_scanner.v11.paper_runtime import PaperRuntime,RuntimePolicy
from polymarket_scanner.v11.runtime_health import SourceNeed
from test_v11_maker_research import rig, factory, setup, bundle, maker, propose
from test_v11_runtime_health import monitor,ready
from test_v11_paper_runtime import queue,GatedEvaluator


def integrated(rig,monkeypatch):
    event=rig['context'].event_id
    m=monitor(rig,monkeypatch,scopes={event:('MAKER_RESEARCH',)},sources=(SourceNeed(event,'MAKER_RESEARCH','MODEL','fixture','model-1',120.),))
    ready(rig,m);mk=maker(rig)
    rt=PaperRuntime(rig['coordinator'],queue(rig),m,RuntimePolicy('fixture'),evaluator=GatedEvaluator(rig['store']),maker=mk)
    return rt,mk


@pytest.mark.parametrize('backward',[False,True])
def test_runtime_retires_passive_research_on_clock_failure_even_with_backward_wall(rig,monkeypatch,backward):
    rt,mk=integrated(rig,monkeypatch)
    assert propose(rig)['outcome']=='OBSERVING_RESEARCH_QUOTE'
    if backward:rig['now'][0]-=10;rig['mono'][0]+=1
    else:rig['sync'][0]=False
    d=rt.tick('unsafe-clock')['body']['details']
    assert len(d['retired_quote_ids'])==1,d
    q=mk._state(mk._head())['quote']
    assert q['status']=='RETIRED' and q['retired_at']==rig['now'][0]
    assert q['fill_status']=='UNKNOWN_NOT_AN_ORDER' and rig['coordinator']._head() is None
    assert mk._head()['body']['chronology']=='SAFETY_SEQUENCE_WITH_RAW_WALL_TIME'


def test_unhealthy_runtime_cannot_propose_new_maker_quote(rig,monkeypatch):
    rt,mk=integrated(rig,monkeypatch);rig['sync'][0]=False;rt.health.sample('sync-lost')
    d=propose(rig)
    assert d['outcome']=='GATED' and d['reason']=='RUNTIME_CLOCK_OR_LIVENESS_GATED'
    assert not mk._state(mk._head())


def test_safety_retirement_cannot_change_quote_price_or_create_quote(rig,monkeypatch):
    rt,mk=integrated(rig,monkeypatch);propose(rig)
    d=deepcopy(mk._head()['body']['details'])
    d['request']=dict(action='RETIRE',quote_id='quote',reason='test')
    d['quotes']['quote'].update(status='RETIRED',retired_at=rig['now'][0],retirement_reason='test')
    d['quotes']['quote']['request']['limit_price']='.9'
    with pytest.raises(EvidenceError,match='STATE_MUTATION_REFUSED'):
        rig['store'].safety_audit('bad',event_id=mk.key,kind='MEASUREMENT',details=d)


def test_runtime_retires_maker_research_on_rule_quarantine_without_order_or_cash_action(rig,monkeypatch):
    from polymarket_scanner.v11.rules import RuleGuard
    rt,mk=integrated(rig,monkeypatch);assert propose(rig)['outcome']=='OBSERVING_RESEARCH_QUOTE'
    event=rig['context'].event_id
    raw=rig['store'].capture('unsupported-rule-raw',event_id=event,kind='RULES',provider='fixture',source_identity=event,
        revision='new',payload={'event':{'id':event,'description':'Unsupported new contract definition'}},evidence_class='SYNTHETIC')
    RuleGuard(rig['store']).invalidate('unsupported-guard',event_id=event,raw_evidence_id=raw['id'],reason='UNSUPPORTED_RULE')
    d=rt.tick('quarantine')['body']['details']
    assert d['retired_quote_ids'] and mk._state(mk._head())['quote']['retirement_reason']=='MAKER_RULE_QUARANTINED'
    assert rig['coordinator']._head() is None and not rig['store'].records(kind='TRADE')


def test_rule_retirement_runs_before_a_busy_census_queue_can_interrupt_event_work(rig,monkeypatch):
    import fcntl
    import os
    from polymarket_scanner.v11.rules import RuleGuard
    rt,mk=integrated(rig,monkeypatch);assert propose(rig)['outcome']=='OBSERVING_RESEARCH_QUOTE'
    event=rig['context'].event_id
    raw=rig['store'].capture('changed-raw',event_id=event,kind='RULES',provider='fixture',source_identity=event,
        revision='changed',payload={'event':{'id':event,'description':'Unsupported rule'}},evidence_class='SYNTHETIC')
    RuleGuard(rig['store']).invalidate('changed-guard',event_id=event,raw_evidence_id=raw['id'],reason='UNSUPPORTED')
    fd=os.open(rt.store.path.with_name(rt.store.path.name+'.events.lock'),os.O_CREAT|os.O_WRONLY,0o600)
    try:
        fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
        d=rt.tick('busy-census')['body']['details']
    finally:os.close(fd)
    assert d['outcome']=='DEGRADED' and d['retired_quote_ids']
    assert any(e['reason']=='EVENT_WORK_DEFERRED_BUSY_WORKER' for e in d['errors'])
    assert mk._state(mk._head())['quote']['retirement_reason']=='MAKER_RULE_QUARANTINED'
    assert rig['coordinator']._head() is None


def test_retained_retired_quotes_cannot_hide_new_observing_quote_from_minimum_safety_budget(rig,monkeypatch):
    old,mk=integrated(rig,monkeypatch)
    rt=PaperRuntime(old.coordinator,old.queue,old.health,RuntimePolicy('one',maximum_updates=1),evaluator=old.evaluator,maker=mk)
    assert propose(rig,'first',quote_id='a')['outcome']=='OBSERVING_RESEARCH_QUOTE'
    mk.retire('retire-a',quote_id='a',reason='SYNTHETIC_END')
    assert propose(rig,'second',quote_id='b')['outcome']=='OBSERVING_RESEARCH_QUOTE'
    rig['sync'][0]=False
    rt.tick('unsafe')
    assert mk._state(mk._head())['b']['status']=='RETIRED'
    assert mk._state(mk._head())['a']['retirement_reason']=='SYNTHETIC_END'
    assert rig['coordinator']._head() is None


def test_healthy_observing_quote_cannot_starve_later_expired_quote(rig,monkeypatch):
    from test_v11_maker_research import book,features
    from test_v11_runtime_health import advance
    old,mk=integrated(rig,monkeypatch)
    rt=PaperRuntime(old.coordinator,old.queue,old.health,RuntimePolicy('one',maximum_updates=1),evaluator=old.evaluator,maker=mk)
    assert propose(rig,'first',quote_id='a')['outcome']=='OBSERVING_RESEARCH_QUOTE'
    market=rig['rule'].payload['partition'][1]['market_id']
    book(rig,'other',market_id=market);features(rig,'other','other-micro')
    result=propose(rig,'second',quote_id='b',thesis_id='b',market_id=market,microstructure_id='other-micro',expires_at=rig['now'][0]+.5)
    assert result['outcome']=='OBSERVING_RESEARCH_QUOTE',result
    advance(rig,1)
    rt.tick('a-first');assert mk._state(mk._head())['b']['status']=='OBSERVING'
    rt=PaperRuntime(old.coordinator,old.queue,old.health,rt.policy,evaluator=old.evaluator,maker=mk)
    rt.tick('b-next')
    state=mk._state(mk._head())
    assert state['a']['status']=='OBSERVING' and state['b']['retirement_reason']=='MAKER_QUOTE_EXPIRED'
    assert rig['coordinator']._head() is None
