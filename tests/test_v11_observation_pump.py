import asyncio
from dataclasses import replace

import httpx
import pytest

from polymarket_scanner.v11.collection import PublicCollector,SourceRequest
from polymarket_scanner.v11.observation_runtime import ObservationRuntime,ScheduledCollector
from polymarket_scanner.v11.observation_pump import ObservationPump,KEY,VERSION
from polymarket_scanner.v11.evidence import EvidenceError,digest
from test_v11_paper_coordinator import rig,coordinator,proposal
from test_v11_paper_runtime import assembled
from test_v11_weather_sources import request as madis_request


def sources(rig):
    event=rig['context'].event_id
    return (SourceRequest(provider='NOAA_AWC',url='https://aviationweather.gov/api/data/metar',event_id=event,
              kind='OFFICIAL_OBSERVATION',source_identity='KATL',revision='received'),
            replace(madis_request(),event_id=event))


def kwargs(rig):
    return dict(station_by_event={rig['context'].event_id:'KATL'},
        strategies=('OFFICIAL_DIAGNOSTIC','PWS_OBSERVATION_LEAD'),required_providers_by_strategy={
            'OFFICIAL_DIAGNOSTIC':('NOAA_AWC',),'PWS_OBSERVATION_LEAD':('NOAA_AWC','NOAA_MADIS_CWOP')})


def test_public_collector_partial_success_normalization_feed_and_runtime_are_joined(rig,monkeypatch):
    rt=assembled(rig,monkeypatch,census=False);calls=[]
    def transport(req):
        calls.append(str(req.url))
        if req.url.host=='aviationweather.gov':
            return httpx.Response(200,json=[dict(icaoId='KATL',obsTime=rig['now'][0]-1,temp=25)])
        return httpx.Response(503)
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as client:
            observer=ObservationRuntime(ScheduledCollector(PublicCollector(rig['store'],client,attempts=1)))
            pump=ObservationPump(observer,rt);first=await pump.cycle('one',sources(rig),**kwargs(rig))
            assert await pump.cycle('one',sources(rig),**kwargs(rig))==first
            return first
    d=asyncio.run(run())['body']['details']
    assert len(calls)==2 and d['outcome']=='OBSERVATION_CYCLE_RECORDED'
    assert d['observation']['collection']['partial_success']
    normalized=d['observation']['normalization'][0]
    receipt=rig['store'].get(normalized['capture_ids'][0]);payload=receipt['body']['payload']
    assert payload['observations'][0]['source_role']=='OFFICIAL_METAR_PROXY_NOT_EXACT_CONTRACT_POPULATION'
    assert not payload['settlement_authority']
    route=rig['store'].get('feed-route:'+digest(receipt['id']))
    assert route['body']['details']['result']['affected_events']==[rig['context'].event_id]
    assert not d['financial_authority'] and not d['forward_or_live_acceptance']
    assert not rig['store'].records(kind='TRADE')
    funnels={r['body']['strategy']:r['body'] for r in rig['store'].records(kind='FUNNEL')}
    assert funnels['OFFICIAL_DIAGNOSTIC']['state']=='PASS' and funnels['PWS_OBSERVATION_LEAD']['state']=='NO_DATA'


def test_next_pump_cycle_honors_persisted_provider_cooldowns(rig,monkeypatch):
    rt=assembled(rig,monkeypatch,census=False);calls=[]
    def transport(req):calls.append(req);return httpx.Response(429,headers={'Retry-After':'300'})
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as client:
            pump=ObservationPump(ObservationRuntime(ScheduledCollector(PublicCollector(rig['store'],client,attempts=1))),rt)
            await pump.cycle('one',sources(rig),**kwargs(rig))
            return await pump.cycle('two',sources(rig),**kwargs(rig))
    d=asyncio.run(run())['body']['details']
    assert len(calls)==2 and not d['observation']['collection']['sources']
    assert len(d['observation']['collection']['omitted'])==2


def test_bad_clock_skips_network_collection_but_requests_existing_cancellation(rig,monkeypatch):
    c=coordinator(rig);p=proposal(rig,units='2');c.coordinate('reserve',(p,));rt=assembled(rig,monkeypatch,census=False)
    rig['now'][0]-=10;rig['mono'][0]+=1;calls=[]
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda req:calls.append(req))) as client:
            pump=ObservationPump(ObservationRuntime(ScheduledCollector(PublicCollector(rig['store'],client))),rt)
            return await pump.cycle('clock-bad',sources(rig),**kwargs(rig))
    d=asyncio.run(run())['body']['details']
    assert not calls and d['outcome']=='COLLECTION_DEFERRED_CLOCK'
    assert c._state(c._head())['intents'][p.proposal_id]['status']=='CANCEL_REQUESTED'


def test_interrupted_collection_is_not_automatically_reissued_and_keeps_prior_captures(rig,monkeypatch):
    rt=assembled(rig,monkeypatch,census=False);calls=[]
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda req:calls.append(req))) as client:
            observer=ObservationRuntime(ScheduledCollector(PublicCollector(rig['store'],client)))
            async def interrupted(cycle_id,*args,**kw):
                rig['store'].capture('committed-before-crash',event_id=rig['context'].event_id,kind='OFFICIAL_OBSERVATION',
                    provider='fixture',source_identity='crash-fixture',revision='1',payload={'source_time_status':'NOT_YET_NORMALIZED'},evidence_class='SYNTHETIC')
                raise RuntimeError('PROCESS_CRASH')
            monkeypatch.setattr(observer,'cycle',interrupted);pump=ObservationPump(observer,rt)
            with pytest.raises(RuntimeError,match='PROCESS_CRASH'):await pump.cycle('one',sources(rig),**kwargs(rig))
            return await pump.cycle('one',sources(rig),**kwargs(rig))
    d=asyncio.run(run())['body']['details']
    assert d['outcome']=='INTERRUPTED_COLLECTION_NOT_RETRIED' and not calls
    assert rig['store'].get('committed-before-crash')


def test_same_pump_identifier_cannot_change_source_request(rig,monkeypatch):
    rt=assembled(rig,monkeypatch,census=False)
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda req:httpx.Response(503))) as client:
            pump=ObservationPump(ObservationRuntime(ScheduledCollector(PublicCollector(rig['store'],client,attempts=1))),rt)
            await pump.cycle('one',sources(rig),**kwargs(rig))
            with pytest.raises(EvidenceError,match='REPLAY_CONFLICT'):
                await pump.cycle('one',(replace(sources(rig)[0],revision='changed'),sources(rig)[1]),**kwargs(rig))
    asyncio.run(run())
