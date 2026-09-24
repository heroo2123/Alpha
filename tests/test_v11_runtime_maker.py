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
