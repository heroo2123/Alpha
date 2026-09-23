import asyncio
from datetime import datetime,timezone

import httpx
import pytest

from polymarket_scanner.v11.collection import PublicCollector,SourceRequest
from polymarket_scanner.v11.evidence import EvidenceError,EvidenceStore
from polymarket_scanner.v11.observation_runtime import ObservationRuntime,ScheduledCollector
from polymarket_scanner.v11.weather_sources import madis_request,parse_madis_xml,parse_awc_metar


AT=datetime(2026,9,23,21,0,tzinfo=timezone.utc).timestamp()
XML='<?xml version="1.0"?><mesonet><record var="V-T" shef_id="TEST1" elev="300" lat="33.6" lon="-84.4" ObTime="2026-09-23T20:50" provider="APRSWXNET" data_value="303.15" QCD="S" QCA="59" QCR="0" /></mesonet>'


def request(event='event'):
    return madis_request(event_id=event,station='KATL',latitude=33.64,longitude=-84.42)


def test_madis_kelvin_receipt_time_and_authority_are_preserved():
    params=dict(request().params)
    a=parse_madis_xml(XML,received_at=AT,params=params)['observations'][0]
    b=parse_madis_xml(XML,received_at=AT+1,params=params)['observations'][0]
    assert a['temperature_c']==pytest.approx(30)
    assert a['age_at_receipt_seconds']==600 and a['local_received_at']==AT
    assert a['provider_received_at'] is None
    assert a['observation_identity']==b['observation_identity']
    assert not a['settlement_authority'] and not a['calibration_label_authority']
    assert not a['local_qc_certified'] and a['provider_checks_without_failures']


@pytest.mark.parametrize('old,new,code',[
    ('APRSWXNET','RESTRICTED_PROVIDER','PROVIDER_OR_VARIABLE'),
    ('2026-09-23T20:50','2026-09-23T21:01','STALE_OR_FUTURE'),
    ('303.15','NaN','NONFINITE'),('303.15','-99999','PHYSICAL_RANGE'),
    ('lat="33.6"','lat="60"','LOCATION'),
])
def test_bad_madis_record_keeps_response_but_never_usable_observation(old,new,code):
    result=parse_madis_xml(XML.replace(old,new),received_at=AT,params=dict(request().params))
    assert result['observations']==[]
    assert any(code in k for k in result['rejections'])


def test_xml_entities_and_geographic_unbounded_requests_are_refused():
    with pytest.raises(EvidenceError,match='DECLARATION'):
        parse_madis_xml('<!DOCTYPE a [<!ENTITY x "x">]><mesonet/>',received_at=AT,params=dict(request().params))
    with pytest.raises(EvidenceError,match='BOUND'):
        madis_request(event_id='event',station='TEST',latitude=30,longitude=0,half_width_degrees=10)
    p=dict(request().params);p['pvd']='ALL-MESO'
    with pytest.raises(EvidenceError,match='PUBLIC_SUBSET'):
        SourceRequest(provider='madis',url=request().url,event_id='event',kind='PWS_OBSERVATION',
                      source_identity='TEST',revision='1',params=tuple(p.items()),response_format='MADIS_XML')


def test_awc_proxy_is_never_exact_contract_settlement():
    result=parse_awc_metar([{'icaoId':'KATL','obsTime':AT-60,'temp':30,'rawOb':'SYNTHETIC'}],
                           station='KATL',received_at=AT,max_age_seconds=120)
    assert len(result['observations'])==1 and not result['observations'][0]['settlement_authority']
    assert parse_awc_metar([{'icaoId':'OTHER','obsTime':AT,'temp':30}],station='KATL',
                          received_at=AT,max_age_seconds=120)['observations']==[]


def test_rate_limit_blocks_same_host_remainder_and_survives_restart(tmp_path):
    tmp_path.chmod(0o700);now=[AT];calls=[]
    store=EvidenceStore(tmp_path/'evidence.sqlite','V11_PAPER',clock=lambda:now[0])
    def transport(req):
        calls.append(str(req.url));return httpx.Response(429,headers={'Retry-After':'900'})
    async def run(cycle):
        async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as client:
            # New instances on every cycle deliberately simulate process restart.
            return await ScheduledCollector(PublicCollector(store,client)).cycle(cycle,(request(),request('event2')))
    first=asyncio.run(run('first'));assert len(calls)==1
    assert first['sources'][1]['attempts']==0
    assert store.get('first:0:health')['body']['retry_not_before']==AT+900
    now[0]+=899
    second=asyncio.run(run('second'));assert len(calls)==1 and len(second['omitted'])==2
    now[0]+=1
    asyncio.run(run('third'));assert len(calls)==2


def test_normalization_failure_does_not_rollback_other_source_and_is_durable(tmp_path):
    tmp_path.chmod(0o700)
    store=EvidenceStore(tmp_path/'evidence.sqlite','V11_PAPER',clock=lambda:AT)
    official=SourceRequest(provider='NOAA_AWC',url='https://aviationweather.gov/api/data/metar',
                            event_id='event',kind='OFFICIAL_OBSERVATION',source_identity='KATL',revision='1')
    def transport(req):
        if req.url.host=='aviationweather.gov':
            return httpx.Response(200,json=[{'icaoId':'KATL','obsTime':AT-30,'temp':30}])
        return httpx.Response(200,content='<html>source problem</html>')
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as client:
            runtime=ObservationRuntime(ScheduledCollector(PublicCollector(store,client)))
            return await runtime.cycle('cycle',(request(),official),station_by_event={'event':'KATL'},
                                       strategies=('PWS_OBSERVATION_LEAD',))
    result=asyncio.run(run())
    assert {x['state'] for x in result['normalization']}=={'SUCCESS','SEMANTIC_FAILURE'}
    assert len(store.records(kind='SOURCE_RESULT'))==4
    assert store.records(kind='FUNNEL')[0]['body']['state']=='NO_DATA'
    assert store.records(kind='RUNTIME_STATUS')[0]['body']['details']==result
    assert not result['financial_authority']


def test_cycle_deadline_does_not_issue_remaining_requests(tmp_path):
    tmp_path.chmod(0o700);store=EvidenceStore(tmp_path/'archive.sqlite','V11_PAPER');calls=[]
    async def transport(req):
        calls.append(req);await asyncio.sleep(1);return httpx.Response(200,json={})
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as client:
            return await PublicCollector(store,client,attempts=1,cycle_seconds=.01).cycle('bounded',(request(),request('second')))
    result=asyncio.run(run())
    assert len(calls)==1 and result['sources'][1]['state']=='BUDGET_EXHAUSTED'
