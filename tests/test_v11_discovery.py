import asyncio
from copy import deepcopy
from dataclasses import replace
from decimal import Decimal

import httpx
import pytest

from polymarket_scanner.v11.certification import StationMetadata, StationRegistry
from polymarket_scanner.v11.collection import PublicCollector
from polymarket_scanner.v11.discovery import MarketDiscovery, DiscoveryPolicy, catalog_request, KEY
from polymarket_scanner.v11.evidence import EvidenceError, digest
from polymarket_scanner.v11.observation_runtime import ScheduledCollector
from polymarket_scanner.v11.rules import RuleGuard
from test_v11_paper_coordinator import rig, coordinator, proposal
from test_v11_paper_runtime import assembled
from test_v11_runtime_health import advance
from test_weather_final_gpt6_exact_replays import _event


def event(eid='event-1'):
    return dict(_event(station='KATL',eid=eid),active=True,closed=False)


@pytest.fixture
def catalog(rig,monkeypatch):
    rt=assembled(rig,monkeypatch,census=False)
    payload=dict(station='KATL',latitude=33.63,longitude=-84.44,elevation_m=313)
    source=rig['store'].capture('station-raw',event_id='station:KATL',kind='STATION_METADATA',provider='fixture',source_identity='KATL',
        revision='one',payload=payload,evidence_class='SYNTHETIC')
    meta=StationMetadata('KATL','Atlanta','US',33.63,-84.44,313,'America/New_York','NOAA_WRH',('NOAA',),('GEFS',),digest(payload),rig['now'][0])
    StationRegistry(rig['store']).observe('station-metadata',meta,raw_evidence_id=source['id'])
    return rig,rt,meta


def discovery(catalog,client,**policy):
    r,rt,_=catalog
    return MarketDiscovery(ScheduledCollector(PublicCollector(r['store'],client,attempts=1)),rt.health,DiscoveryPolicy('fixture',**policy))


def test_catalog_page_is_archived_once_and_bounded_semantic_processing_resumes_after_restart(catalog):
    r,rt,meta=catalog;calls=[];rain=dict(id='rain',title='Will it rain?',tags=[dict(slug='weather')],markets=[])
    def transport(req):calls.append(req);return httpx.Response(200,json=dict(events=[event(),rain],next_cursor=None))
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as client:
            worker=discovery(catalog,client,maximum_events_per_step=1);worker.start('scan')
            first=await worker.step('one');resumed=discovery(catalog,client,maximum_events_per_step=1)
            assert await resumed.step('one')==first
            second=await resumed.step('two');return first,second
    one,two=asyncio.run(run());a=one['body']['details'];b=two['body']['details']
    assert a['state']['page_position']==1 and a['summary']['catalog_traversal_complete'] is False
    assert b['summary']['catalog_traversal_complete'] and b['summary']['weather_looking_events']==2
    assert b['summary']['strict_supported_events']==1 and b['summary']['unsupported_events']==1
    assert not b['summary']['semantic_coverage_complete'] and not b['summary']['current_universe_verified']
    assert len(calls)==1 and str(calls[0].url).startswith('https://gamma-api.polymarket.com/events/keyset?')
    row=r['store'].get(a['processed_event_ids'][0]);d=row['body']['details']
    guard=r['store'].get(d['rule_state_id']);raw=r['store'].get(d['raw_event_id']);page=r['store'].get(d['page_id'])
    assert guard['body']['details']['preimage']['metadata_fingerprint']==meta.fingerprint
    assert raw['body']['received_at']==page['body']['received_at'] and not d['strategy_eligible']
    assert len(b['summary']['template_counts'])==2 and not r['store'].records(kind='TRADE')


def test_keyset_cursor_cooldown_and_duplicate_denominator_are_durable(catalog):
    r,_,_=catalog;calls=[]
    def transport(req):
        calls.append(req)
        return httpx.Response(200,json=dict(events=[event()],next_cursor='next' if len(calls)==1 else None))
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as client:
            w=discovery(catalog,client);w.start('scan');await w.step('one');cooldown=await w.step('cooldown')
            advance(r,61);final=await discovery(catalog,client).step('two');return cooldown,final
    cool,final=asyncio.run(run());d=final['body']['details']['summary']
    assert cool['body']['details']['outcome']=='PAGE_UNAVAILABLE_OR_COOLDOWN'
    assert len(calls)==2 and calls[1].url.params['after_cursor']=='next'
    assert d['catalog_traversal_complete'] and d['event_hits']==2 and d['distinct_events']==1 and d['duplicate_hits']==1
    assert d['duplicate_revisions']==0 and d['weather_looking_events']==1 and d['strict_supported_events']==1


def test_changed_duplicate_records_new_rejection_and_quarantine_without_counting_independent_event(catalog):
    r,_,_=catalog;calls=[];bad=event();bad['description']='Arbitrary unreviewed resolution text.'
    def transport(req):
        calls.append(req);return httpx.Response(200,json=dict(events=[event() if len(calls)==1 else bad],next_cursor='next' if len(calls)==1 else None))
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as client:
            w=discovery(catalog,client);w.start('scan');await w.step('one');advance(r,61);return await w.step('two')
    d=asyncio.run(run())['body']['details']['summary']
    assert d['catalog_traversal_complete'] and d['duplicate_revisions']==1 and not d['semantic_coverage_complete']
    assert d['distinct_events']==1
    assert r['store'].latest(kind='RULE_STATE',event_id='event-1')['body']['details']['quarantined']


@pytest.mark.parametrize('bad', [dict(events=[],next_cursor='next'),dict(events='not-list',next_cursor=None),
    dict(events=[dict(id='bad',markets='wrong')],next_cursor=None),dict(events=[dict(markets=[])],next_cursor=None),
    dict(events=[],next_cursor=1),dict(events=[],next_cursor=None,unreviewed_paging=True)])
def test_bad_page_cannot_be_claimed_complete_and_raw_receipt_is_preserved(catalog,bad):
    r,_,_=catalog
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda req:httpx.Response(200,json=bad))) as client:
            w=discovery(catalog,client);w.start('scan');return await w.step('bad')
    d=asyncio.run(run())['body']['details']
    assert d['outcome']=='PAGE_REJECTED' and d['state']['phase']=='INCOMPLETE' and not d['summary']['catalog_traversal_complete']
    raw=r['store'].records(kind='RULES',event_id='v11-discovery-catalog')
    assert len(raw)==1 and raw[0]['body']['payload']['response']==bad


def test_scan_and_page_bounds_never_report_a_complete_catalog(catalog):
    r,_,_=catalog;calls=[]
    def transport(req):calls.append(req);return httpx.Response(200,json=dict(events=[event()],next_cursor='more'))
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as client:
            w=discovery(catalog,client,maximum_pages=1);w.start('scan');await w.step('one');return await w.step('bound')
    d=asyncio.run(run())['body']['details']
    assert d['outcome']=='PAGE_BOUND' and not d['summary']['catalog_traversal_complete'] and len(calls)==1


def test_processing_old_page_does_not_refresh_event_receipts_or_rule_health(catalog):
    r,_,_=catalog
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda req:httpx.Response(200,json=dict(events=[event('one'),event('two')],next_cursor=None)))) as client:
            w=discovery(catalog,client,maximum_events_per_step=1,maximum_page_age_seconds=2);w.start('scan');await w.step('one')
            advance(r,3);return await w.step('old')
    d=asyncio.run(run())['body']['details']
    assert d['outcome']=='PAGE_EXPIRED' and d['summary']['distinct_events']==1 and not d['summary']['catalog_traversal_complete']
    assert r['store'].latest(kind='RULE_STATE',event_id='two') is None


def test_missing_metadata_remains_separate_from_strict_grammar_support(catalog):
    r,_,_=catalog;e=dict(_event(station='KLGA'),active=True,closed=False)
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda req:httpx.Response(200,json=dict(events=[e],next_cursor=None)))) as client:
            w=discovery(catalog,client);w.start('scan');return await w.step('one')
    d=asyncio.run(run())['body']['details'];result=r['store'].get(d['processed_event_ids'][0])['body']['details']
    assert d['summary']['strict_supported_events']==1 and not result['rule_state_id']
    assert result['binding_reason']=='DISCOVERY_STATION_METADATA_UNAVAILABLE' and not result['strategy_eligible']


def test_interruption_after_event_record_resumes_without_second_http_or_duplicate_counts(catalog,monkeypatch):
    r,_,_=catalog;calls=[]
    def transport(req):calls.append(req);return httpx.Response(200,json=dict(events=[event()],next_cursor=None))
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as client:
            w=discovery(catalog,client);w.start('scan');original=w._progress
            def crash(*a,**kw):
                if kw.get('outcome')=='EVENT_ARCHIVED':raise RuntimeError('POWER_LOSS')
                return original(*a,**kw)
            monkeypatch.setattr(w,'_progress',crash)
            with pytest.raises(RuntimeError,match='POWER_LOSS'):await w.step('one')
            monkeypatch.setattr(w,'_progress',original)
            return await w.step('one')
    d=asyncio.run(run())['body']['details']
    assert len(calls)==1 and d['summary']['distinct_events']==1 and d['summary']['event_hits']==1
    assert len(r['store'].records(kind='RULE_STATE',event_id='event-1'))==1


def test_interrupted_http_without_receipt_is_not_repeated_under_same_command(catalog,monkeypatch):
    calls=[]
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda req:calls.append(req))) as client:
            w=discovery(catalog,client);w.start('scan')
            async def crash(*a):raise RuntimeError('POWER_LOSS')
            monkeypatch.setattr(w.scheduled,'cycle',crash)
            with pytest.raises(RuntimeError,match='POWER_LOSS'):await w.step('one')
            return await w.step('one')
    d=asyncio.run(run())['body']['details']
    assert d['outcome']=='INTERRUPTED_REQUEST_NOT_REPEATED' and not calls and not d['summary']['catalog_traversal_complete']


def test_unhealthy_clock_defers_without_requests_and_active_scan_cannot_be_discarded(catalog):
    r,_,_=catalog;calls=[]
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda req:calls.append(req))) as client:
            w=discovery(catalog,client);w.start('scan')
            with pytest.raises(EvidenceError,match='ACTIVE_SCAN'):w.start('replacement')
            r['sync'][0]=False;return await w.step('bad')
    assert asyncio.run(run())['body']['details']['outcome']=='DEFERRED_CLOCK_UNHEALTHY' and not calls


def test_transient_metadata_expiry_does_not_invent_rule_drift_or_refresh_old_binding(catalog):
    r,_,_=catalog
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda req:httpx.Response(200,json=dict(events=[event()],next_cursor=None)))) as client:
            w=discovery(catalog,client,maximum_metadata_age_seconds=10);w.start('scan');await w.step('one')
            old=r['store'].latest(kind='RULE_STATE',event_id='event-1');advance(r,61);w.start('later')
            row=await w.step('two');return old,row
    old,row=asyncio.run(run());result=r['store'].get(row['body']['details']['processed_event_ids'][0])['body']['details']
    assert result['binding_reason']=='DISCOVERY_STATION_METADATA_NOT_CURRENT' and not result['rule_state_id']
    assert r['store'].latest(kind='RULE_STATE',event_id='event-1')==old and not old['body']['details']['quarantined']


def test_clock_loss_after_http_receipt_preserves_page_without_compiling_rule(catalog):
    r,_,_=catalog
    def transport(req):r['sync'][0]=False;return httpx.Response(200,json=dict(events=[event()],next_cursor=None))
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as client:
            w=discovery(catalog,client);w.start('scan');return await w.step('one')
    d=asyncio.run(run())['body']['details']
    assert d['outcome']=='DEFERRED_CLOCK_CHANGED' and d['state']['page_id'] and not d['summary']['catalog_traversal_complete']
    assert r['store'].latest(kind='RULE_STATE',event_id='event-1') is None


def test_discovery_rule_rejection_reaches_runtime_cancel_and_preserves_reservation(rig,monkeypatch):
    c=coordinator(rig);p=proposal(rig,units='2');c.coordinate('reserve',(p,))
    rt=assembled(rig,monkeypatch,census=False)
    base=_event(station='KATL');raw=rig['store'].capture('valid-rule-raw',event_id='event-1',kind='RULES',provider='fixture',source_identity='event-1',
        revision='one',payload={'event':base},evidence_class='SYNTHETIC')
    # Preserve the exact raw preimage used by the immutable fixture fingerprint.
    RuleGuard(rig['store']).observe('valid-guard',rig['rule'],raw_evidence_id=raw['id'])
    bad=event();bad['description']='Changed to an unsupported settlement rule.'
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda req:httpx.Response(200,json=dict(events=[bad],next_cursor=None)))) as client:
            w=MarketDiscovery(ScheduledCollector(PublicCollector(rig['store'],client,attempts=1)),rt.health,DiscoveryPolicy('fixture'))
            w.start('scan');return await w.step('one')
    d=asyncio.run(run())['body']['details']
    rejected=rig['store'].get(d['processed_event_ids'][0])['body']['details']
    assert rejected['rule_state_id'] and rig['store'].get(rejected['rule_state_id'])['body']['details']['quarantined']
    rt.tick('cancel-rule-change')
    assert c._state(c._head())['intents'][p.proposal_id]['status']=='CANCEL_REQUESTED'
    assert Decimal(c.snapshot()['reserved_cash'])==Decimal('.8') and not rig['store'].records(kind='TRADE')


@pytest.mark.parametrize('changes',[{'url':'https://gamma-api.polymarket.com/orders/keyset'},
    {'provider':'OTHER'}, {'kind':'MODEL'}, {'params':(('closed','true'),('limit','10'))},
    {'params':(('closed','false'),('limit','101'))}, {'params':(('closed','false'),('limit','10'),('api_key','secret'))}])
def test_discovery_transport_cannot_expand_to_auth_orders_or_unreviewed_scope(changes):
    with pytest.raises(EvidenceError):replace(catalog_request(None,page_size=10,revision='fixture'),**changes)
