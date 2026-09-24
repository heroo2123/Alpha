import asyncio
from dataclasses import replace

import httpx
import pytest

from polymarket_scanner.v11.census_worker import CensusPlan
from polymarket_scanner.v11.event_queue import EventQueue, _census_raw_receipt
from polymarket_scanner.v11.evidence import EvidenceError, digest
from polymarket_scanner.v11.pws_quality import archive_neighborhood
from polymarket_scanner.v11.pws_runtime import PWSQualityPlan
from polymarket_scanner.v11.rules import fingerprint_event
from test_weather_final_gpt6_exact_replays import _event
from test_v11_paper_coordinator import rig
from test_v11_paper_runtime import assembled
from test_v11_pws_quality import official, policy
from test_v11_pws_runtime import xml, capture
from test_v11_census_worker import worker, prepare, transport_for


def prepared(r,monkeypatch,*,required=True):
    # Explicit synthetic metadata binding for this census-only fixture.
    r['rule']=fingerprint_event(_event(station='KATL'),station_timezone=official().timezone,metadata_fingerprint=official().fingerprint)
    rt=assembled(r,monkeypatch,census=False)
    routes=tuple(replace(route,required_source_kinds=('OFFICIAL_OBSERVATION','PWS_OBSERVATION') if required else ('OFFICIAL_OBSERVATION',))
                 for route in rt.queue.routes.values())
    p=replace(rt.queue.policy,pws_station_age_seconds=(('KATL',600.),),
              source_age_seconds=tuple((k,600. if k=='PWS_OBSERVATION' else t) for k,t in rt.queue.policy.source_age_seconds))
    rt.queue=EventQueue(r['store'],routes=routes,policy=p)
    prepare(r,rt)
    return rt,CensusPlan(r['rule'],'FIXTURE_COLLATERAL',pws=PWSQualityPlan(r['context'].event_id,official(),policy()))


def collect(r,rt,plan,calls,*,unavailable=False):
    base=transport_for(r,calls)
    def handle(req):
        if req.url.host=='madis-data.ncep.noaa.gov':
            calls.append(req)
            return httpx.Response(200,content='<mesonet/>' if unavailable else xml(r['now'][0]))
        return base(req)
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
            return await worker(r,rt,client,plan=plan).step('pws-census')
    return asyncio.run(run())['body']['details']


def test_required_pws_census_joins_new_public_mock_raw_history_and_qc(rig,monkeypatch):
    old=capture(rig,'old-history');rt,plan=prepared(rig,monkeypatch);calls=[]
    d=collect(rig,rt,plan,calls)
    assert d['outcome']=='CENSUS_SOURCE_COVERAGE_ONLY',d
    assert len(calls)==8 and len(d['source_ids'])==2 and not rt.queue.snapshot()['needs_census']
    qc=rig['store'].get(d['source_ids'][-1]);p=qc['body']['payload']
    assert p['health']=='HEALTHY' and len(p['source_captures'])==2
    assert old['id'] in {r['id'] for r in p['source_captures']}
    claim=rig['store'].get('census-step:'+digest('pws-census')+':claim')
    latest=rig['store'].get(p['source_captures'][-1]['id'])
    raw=rig['store'].get(latest['body']['payload']['raw_evidence_id'])
    assert claim['seq']<raw['seq']<latest['seq']<qc['seq']
    assert not d['financial_authority'] and not rig['store'].records(kind='TRADE')


def test_empty_required_neighborhood_remains_gated_without_losing_other_receipts(rig,monkeypatch):
    rt,plan=prepared(rig,monkeypatch);calls=[];d=collect(rig,rt,plan,calls,unavailable=True)
    assert d['outcome']=='GATED' and rt.queue.snapshot()['needs_census']
    assert len(d['book_ids'])==6 and len(d['source_ids'])==2
    assert rig['store'].get(d['source_ids'][-1])['body']['payload']['health']=='UNAVAILABLE'


def test_optional_pws_is_not_added_to_unrelated_census_dependencies(rig,monkeypatch):
    rt,plan=prepared(rig,monkeypatch,required=False);calls=[];d=collect(rig,rt,plan,calls,unavailable=True)
    assert d['outcome']=='CENSUS_SOURCE_COVERAGE_ONLY' and len(calls)==7
    assert all(req.url.host!='madis-data.ncep.noaa.gov' for req in calls)


def test_reprocessing_pre_claim_raw_cannot_clear_census_even_when_qc_is_new(rig):
    normalized=capture(rig);s=rig['store']
    claim=s.audit('claim',event_id='fixture-claim',kind='RUNTIME_STATUS',details={})
    quality=archive_neighborhood(s,'qc-after-claim',event_id=rig['context'].event_id,
        capture_ids=(normalized['id'],),official=official(),policy=policy())
    assert quality['seq']>claim['seq'] and quality['body']['payload']['health']=='HEALTHY'
    with pytest.raises(EvidenceError,match='FRESH_RAW_RECEIPT_LINEAGE_REQUIRED'):
        _census_raw_receipt(s,quality,claim)


def test_census_pws_plan_requires_exact_station_metadata_binding(rig):
    with pytest.raises(EvidenceError,match='PWS_PLAN_RULE_SCOPE'):
        CensusPlan(rig['rule'],'FIXTURE_COLLATERAL',pws=PWSQualityPlan(rig['context'].event_id,official(),policy()))
