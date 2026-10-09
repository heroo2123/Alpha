"""Mock transport integration, never forward/acceptance evidence."""
import asyncio
from dataclasses import asdict, replace
import json

import httpx
import pytest

from polymarket_scanner.v11.book_inputs import BookPolicy
from polymarket_scanner.v11.microstructure import MakerMicrostructure, MicrostructurePolicy
from polymarket_scanner.v11.collection import PublicCollector
from polymarket_scanner.v11.evidence import EvidenceError, digest
from polymarket_scanner.v11.observation_runtime import ScheduledCollector
from polymarket_scanner.v11.real_input_capture import InputReview, RealInputPlan, RealInputCapture, PURPOSE
from polymarket_scanner.v11.rules import fingerprint_event
from polymarket_scanner.v11.runtime_health import RuntimeHealth, HealthPolicy, SourceNeed
from test_v11_book_inputs import books, response
from test_v11_pws_quality import official, policy
from test_v11_pws_runtime import xml
from test_weather_final_gpt6_exact_replays import _event


@pytest.fixture
def capture_rig(books, monkeypatch):
    from polymarket_scanner.v11 import runtime_health
    r = books
    r['rule'] = fingerprint_event(_event(station='KATL'), station_timezone=official().timezone,
                                  metadata_fingerprint=official().fingerprint)
    # Build exact request review for this synthetic test fixture only.
    draft = object.__new__(RealInputPlan)
    object.__setattr__(draft, 'official', official())
    object.__setattr__(draft, 'rule', r['rule'])
    review = InputReview(digest([asdict(req) for req in draft.requests()]), 'a'*64, 'b'*64, 'c'*64,
                         PURPOSE, 'CLEAR', r['now'][0]-1, r['now'][0]+3600)
    r['plan'] = RealInputPlan(r['rule'], official(), policy(), BookPolicy('fixture'),
                             'FIXTURE_COLLATERAL', r['now'][0]+3600, 300., review)
    event = r['rule'].payload['event_id']
    monkeypatch.setattr(runtime_health, 'host_stamp', lambda store:
                        dict(boot_id='fixture-boot', monotonic=r['now'][0], wall=r['now'][0]))
    r['health'] = RuntimeHealth(r['store'], HealthPolicy('fixture',30.,30.,2.,2,.05,('capture',)),
        account_id='no-account', scopes={event: ('PWS_OBSERVATION_LEAD',)},
        sources=(SourceNeed(event,'PWS_OBSERVATION_LEAD','PWS_OBSERVATION','ALPHA_PWS_QC','KATL',600.),),
        sync_probe=lambda: dict(synchronized=True, mechanism='SYNTHETIC_OFF_HOST_FIXTURE', reason='TEST', offset_seconds=None))
    r['health'].sample('clock1'); r['now'][0] += 1; r['health'].sample('clock2')
    return r


def run(r, *, cycle='one', empty=False, status=200, stale=False, delay=0, crash=False,
        book_fail_status=None, empty_book=False, madis_transient=False, madis_malformed=False):
    calls = []
    book_calls = [0]
    def transport(req):
        calls.append(req)
        if req.url.host == 'madis-data.ncep.noaa.gov':
            if crash:
                raise RuntimeError('interrupted')
            if madis_transient:
                raise httpx.ConnectTimeout('timed out')
            if madis_malformed:
                # Invalid UTF-8 bytes: MADIS_XML decoding fails with UnicodeError,
                # the collector's distinct non-transport MALFORMED/ABSENT-style path.
                return httpx.Response(200, content=b'\xff\xfe<mesonet/>')
            return httpx.Response(status, text='<mesonet/>' if empty else xml(r['now'][0]))
        if req.url.host == 'aviationweather.gov':
            return httpx.Response(200, json=[dict(icaoId='KATL', obsTime=r['now'][0]-1, temp=25)])
        book_calls[0] += 1
        if book_fail_status is not None and book_calls[0] == 1:
            return httpx.Response(book_fail_status, text='')
        body = response(r, req.url.params['token_id'])
        if stale:
            body['timestamp'] = str(int((r['now'][0]-31)*1000))
        if empty_book:
            body['asks'] = []
        r['now'][0] += delay
        return httpx.Response(200, json=body)
    async def wait(seconds):
        r['now'][0] += seconds
    async def collect():
        async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as client:
            worker = RealInputCapture(ScheduledCollector(PublicCollector(r['store'],client,attempts=1)),
                                      r['health'],r['plan'], sleeper=wait)
            return await worker.step(cycle)
    return asyncio.run(collect()), calls


def test_source_only_end_to_end_retains_raw_qc_prices_and_no_authority(capture_rig):
    r=capture_rig; row,calls=run(r); d=row['body']['details']
    assert d['outcome']=='FRESH_SOURCE_EVIDENCE_ONLY', d
    assert len(calls)==8 and all(c.url.host=='clob.polymarket.com' for c in calls[-6:])
    assert len(d['book_ids'])==6 and len(d['raw_ids'])==8
    for normalized_id in d['normalized_ids']:
        normalized=r['store'].get(normalized_id); raw=r['store'].get(normalized['body']['payload']['raw_evidence_id'])
        assert raw['seq']<normalized['seq'] and raw['body']['received_at']==normalized['body']['received_at']
    qc=r['store'].get(d['qc_id'])['body']['payload']
    assert qc['health']=='HEALTHY' and qc['source_captures']
    assert not qc['settlement_authority'] and not qc['lead_advantage_verified']
    assert not d['financial_authority'] and not d['acceptance_granted'] and not d['real_orders_sent']
    assert not r['store'].records(kind='COORDINATOR_EVENT') and not r['store'].records(kind='TRADE')
    again, calls=run(r)
    assert again==row and not calls


@pytest.mark.parametrize('kwargs,reason', [({'empty':True},'REAL_INPUT_EMPTY_PWS_OBSERVATION'),
    ({'delay':7},'PUBLIC_BOOK_RECEIPT_STALE_OR_FUTURE')])
def test_absent_pws_or_stale_receipt_never_claims_fresh(capture_rig,kwargs,reason):
    row,_=run(capture_rig,**kwargs); d=row['body']['details']
    assert d['outcome']=='GATED' and any(reason in e for e in d['errors']),d
    assert d['raw_ids'] and not d['acceptance_granted']


def test_old_server_generation_with_fresh_rest_receipt_is_source_evidence_only(capture_rig):
    row,_=run(capture_rig,stale=True); d=row['body']['details']
    assert d['outcome']=='FRESH_SOURCE_EVIDENCE_ONLY',d
    for book_id in d['book_ids']:
        book=capture_rig['store'].get(book_id)['body']
        assert book['payload']['exchange_book_generated_at'] <= book['received_at']-31
        assert book['observed_at']==book['received_at']
        assert not book['payload']['continuous_stream_verified']
    assert not d['financial_authority'] and not d['acceptance_granted']


def test_empty_public_book_is_archived_without_executable_depth(capture_rig):
    row,_=run(capture_rig,empty_book=True); d=row['body']['details']
    assert d['outcome']=='FRESH_SOURCE_EVIDENCE_ONLY',d
    target=capture_rig['rule'].payload['partition'][0]
    selected=next(book_id for book_id in d['book_ids']
                  if capture_rig['store'].get(book_id)['body']['source_identity']==target['yes_token'])
    policy=MicrostructurePolicy('fixture','FIXTURE_COLLATERAL',30.,60.,10.,.1,2)
    result=MakerMicrostructure(capture_rig['store']).evaluate('empty-book-micro',rule=capture_rig['rule'],
        market_id=target['market_id'],side='YES',book_ids=(selected,),trade_ids=(),policy=policy)
    assert result['body']['details']['outcome']=='GATED'
    assert result['body']['details']['reason']=='MICROSTRUCTURE_BOOK_SIDE_MISSING'


def test_provider_cadence_does_not_reuse_old_pws_as_fresh(capture_rig):
    run(capture_rig)
    capture_rig['now'][0]+=2
    row,calls=run(capture_rig,cycle='two');d=row['body']['details']
    assert d['outcome']=='GATED' and 'REAL_INPUT_FRESH_PWS_ABSENT' in d['errors']
    assert all(c.url.host=='clob.polymarket.com' for c in calls)


@pytest.mark.parametrize('status',[401,403,429,503])
def test_provider_failure_is_durable_hold_with_no_retry(capture_rig,status):
    row,calls=run(capture_rig,status=status)
    assert row['body']['details']['outcome']=='PROVIDER_HELD'
    assert row['body']['details']['held_providers']==['NOAA_MADIS_CWOP']
    assert sum(c.url.host=='madis-data.ncep.noaa.gov' for c in calls)==1
    capture_rig['now'][0]+=301
    with pytest.raises(EvidenceError,match='PROVIDER_OR_INTERRUPTED_HOLD'):
        run(capture_rig,cycle='retry')


def test_madis_transient_timeout_is_deferred_not_held(capture_rig):
    r = capture_rig
    row, calls = run(r, madis_transient=True)
    d = row['body']['details']
    assert d['outcome'] == 'GATED', d
    assert 'REAL_INPUT_MADIS_TRANSIENT_TRANSPORT_DEFERRED' in d['errors']
    assert d['held_providers'] == []
    assert sum(c.url.host == 'madis-data.ncep.noaa.gov' for c in calls) == 1
    r['now'][0] += 1200
    row2, calls2 = run(r, cycle='later')
    assert sum(c.url.host == 'madis-data.ncep.noaa.gov' for c in calls2) == 1
    assert row2['body']['details']['outcome'] == 'FRESH_SOURCE_EVIDENCE_ONLY', row2['body']['details']


def test_madis_transient_cooldown_blocks_network_until_1200_seconds(capture_rig):
    r = capture_rig
    run(r, madis_transient=True)
    r['now'][0] += 1199
    row, calls = run(r, cycle='soon')
    d = row['body']['details']
    assert d['outcome'] == 'GATED' and d['errors'] == ['REAL_INPUT_MADIS_TRANSIENT_COOLDOWN'], d
    assert not calls


def test_madis_transient_timeout_third_occurrence_becomes_permanent_hold(capture_rig):
    r = capture_rig
    run(r, madis_transient=True, cycle='t1')
    r['now'][0] += 1200
    row2, _ = run(r, madis_transient=True, cycle='t2')
    assert row2['body']['details']['outcome'] == 'GATED', row2['body']['details']
    r['now'][0] += 1200
    row3, _ = run(r, madis_transient=True, cycle='t3')
    d = row3['body']['details']
    assert d['outcome'] == 'PROVIDER_HELD', d
    assert d['held_providers'] == ['NOAA_MADIS_CWOP']
    r['now'][0] += 1200
    with pytest.raises(EvidenceError, match='PROVIDER_OR_INTERRUPTED_HOLD'):
        run(r, cycle='t4')


@pytest.mark.parametrize('kwargs', [{'status': 401}, {'status': 403}, {'status': 429}, {'status': 503},
                                     {'madis_malformed': True}])
def test_madis_non_transient_failures_still_hold_permanently(capture_rig, kwargs):
    r = capture_rig
    row, _ = run(r, **kwargs)
    d = row['body']['details']
    assert d['outcome'] == 'PROVIDER_HELD', d
    assert d['held_providers'] == ['NOAA_MADIS_CWOP']
    r['now'][0] += 1200
    with pytest.raises(EvidenceError, match='PROVIDER_OR_INTERRUPTED_HOLD'):
        run(r, cycle='retry-non-transient')


def test_madis_transient_timeout_with_simultaneous_book_failure_still_holds(capture_rig):
    r = capture_rig
    row, _ = run(r, madis_transient=True, book_fail_status=503)
    d = row['body']['details']
    assert d['outcome'] == 'PROVIDER_HELD', d
    assert set(d['held_providers']) == {'NOAA_MADIS_CWOP', 'POLYMARKET_PUBLIC_CLOB'}


def test_unrelated_book_transport_failure_does_not_block_future_healthy_cycles(capture_rig):
    r=capture_rig
    row,calls=run(r,book_fail_status=503)
    d=row['body']['details']
    assert d['outcome']=='PROVIDER_HELD'
    assert d['held_providers']==['POLYMARKET_PUBLIC_CLOB']  # the book provider, never MADIS
    assert sum(c.url.host=='clob.polymarket.com' for c in calls)>=1
    r['now'][0]+=301  # past every host's own SOURCE_SCHEDULE cooldown, not the removed global latch
    row2,_=run(r,cycle='retry')
    assert row2['body']['details']['outcome']=='FRESH_SOURCE_EVIDENCE_ONLY',row2['body']['details']


def test_held_ledger_does_not_block_a_completely_different_cycle_with_no_transport_at_all(capture_rig):
    r=capture_rig
    run(r,book_fail_status=503)
    r['now'][0]+=3000  # far past any plausible backoff ceiling, still inside the review window
    row,calls=run(r,cycle='much-later')
    assert row['body']['details']['outcome']=='FRESH_SOURCE_EVIDENCE_ONLY'
    assert calls


def test_madis_hold_still_blocks_subsequent_attempts_after_any_delay(capture_rig):
    r=capture_rig
    run(r,status=429)
    r['now'][0]+=10000  # a rights-sensitive hold must never auto-clear
    with pytest.raises(EvidenceError,match='PROVIDER_OR_INTERRUPTED_HOLD'):
        run(r,cycle='much-later')


def test_legacy_parent_format_madis_hold_without_held_providers_field_still_blocks(capture_rig):
    r=capture_rig
    r['store'].audit('real-input:legacy-madis-hold', event_id='v11-real-input-capture', kind='RUNTIME_STATUS',
        details=dict(config_sha256=digest(asdict(r['plan'])), outcome='PROVIDER_HELD',
            collection=dict(sources=[dict(provider='NOAA_MADIS_CWOP', state='PROVIDER_HELD')], omitted=[]),
            errors=['REAL_INPUT_SOURCE_HELD'], raw_ids=[], normalized_ids=[], book_ids=[], qc_id=None,
            financial_authority=False, real_orders_sent=False, settlement_authority=False, acceptance_granted=False))
    r['now'][0]+=1000
    with pytest.raises(EvidenceError,match='PROVIDER_OR_INTERRUPTED_HOLD'):
        run(r,cycle='after-legacy-hold')


@pytest.mark.parametrize('collection,held_providers', [
    (dict(sources=[], omitted=[]), None),  # empty sources: nothing to recover, must not mean nothing held
    (dict(sources=[], omitted=[]), []),  # empty held_providers fallback must not be trusted either
    (None, None),  # collection itself missing or unparseable
    (dict(sources=[dict(provider='', state='TRANSPORT_FAILURE')], omitted=[]), None),
    (dict(sources=[dict(provider=None, state='TRANSPORT_FAILURE')], omitted=[]), None),
    (dict(sources=[dict(provider='NOAA_MADIS_CWOP ', state='TRANSPORT_FAILURE')], omitted=[]), None),
    (dict(sources=[dict(provider='UNRECOGNIZED', state='TRANSPORT_FAILURE')], omitted=[]), None),
    (dict(sources=[dict(state='TRANSPORT_FAILURE')], omitted=[]), ['NOAA_AWC']),
    (dict(sources=[dict(provider='NOAA_AWC', state=None)], omitted=[]), ['NOAA_AWC']),
    (None, ['']),
    (None, [None]),
    (None, ['NOAA_MADIS_CWOP ']),
    (None, ['UNRECOGNIZED']),
])
def test_malformed_or_contradictory_held_record_still_blocks(capture_rig,collection,held_providers):
    r=capture_rig
    details=dict(config_sha256=digest(asdict(r['plan'])), outcome='PROVIDER_HELD',
        errors=['REAL_INPUT_SOURCE_HELD'], raw_ids=[], normalized_ids=[], book_ids=[], qc_id=None,
        financial_authority=False, real_orders_sent=False, settlement_authority=False, acceptance_granted=False)
    if collection is not None:
        details['collection']=collection
    if held_providers is not None:
        details['held_providers']=held_providers
    r['store'].audit('real-input:malformed-held',event_id='v11-real-input-capture',kind='RUNTIME_STATUS',details=details)
    r['now'][0]+=1000
    with pytest.raises(EvidenceError,match='PROVIDER_OR_INTERRUPTED_HOLD'):
        run(r,cycle='after-malformed-hold')


def test_held_providers_fallback_recovers_an_explicit_hold_when_sources_is_empty(capture_rig):
    r=capture_rig
    r['store'].audit('real-input:empty-sources-explicit-held',event_id='v11-real-input-capture',kind='RUNTIME_STATUS',
        details=dict(config_sha256=digest(asdict(r['plan'])), outcome='PROVIDER_HELD',
            collection=dict(sources=[], omitted=[]), held_providers=['NOAA_MADIS_CWOP'],
            errors=['REAL_INPUT_SOURCE_HELD'], raw_ids=[], normalized_ids=[], book_ids=[], qc_id=None,
            financial_authority=False, real_orders_sent=False, settlement_authority=False, acceptance_granted=False))
    r['now'][0]+=1000
    with pytest.raises(EvidenceError,match='PROVIDER_OR_INTERRUPTED_HOLD'):
        run(r,cycle='after-empty-sources-explicit-held')


def test_conflicting_recovered_held_providers_fail_closed(capture_rig):
    r=capture_rig
    r['store'].audit('real-input:conflicting-held',event_id='v11-real-input-capture',kind='RUNTIME_STATUS',
        details=dict(config_sha256=digest(asdict(r['plan'])), outcome='PROVIDER_HELD',
            collection=dict(sources=[dict(provider='NOAA_AWC',state='TRANSPORT_FAILURE')], omitted=[]),
            held_providers=['NOAA_MADIS_CWOP'],  # only possible via a forged/corrupted record
            errors=['REAL_INPUT_SOURCE_HELD'], raw_ids=[], normalized_ids=[], book_ids=[], qc_id=None,
            financial_authority=False, real_orders_sent=False, settlement_authority=False, acceptance_granted=False))
    r['now'][0]+=1000
    with pytest.raises(EvidenceError,match='PROVIDER_OR_INTERRUPTED_HOLD'):
        run(r,cycle='after-conflicting-held')


def test_interrupted_request_cannot_be_retried_unattended(capture_rig):
    with pytest.raises(RuntimeError,match='interrupted'):
        run(capture_rig,crash=True)
    with pytest.raises(EvidenceError,match='PROVIDER_OR_INTERRUPTED_HOLD'):
        run(capture_rig,cycle='retry')


def test_missing_fresh_event_or_review_refuses_before_transport(capture_rig):
    r=capture_rig;r['now'][0]+=3601
    with pytest.raises(EvidenceError,match='EVENT_OR_REVIEW_EXPIRED'):
        run(r)
    assert r['store'].latest(kind='RUNTIME_STATUS',event_id='v11-real-input-capture') is None


def test_review_must_bind_supported_requests_and_clear_restriction(capture_rig):
    plan=capture_rig['plan']
    with pytest.raises(EvidenceError,match='REQUEST_REVIEW_MISMATCH'):
        replace(plan,review=replace(plan.review,requests_sha256='d'*64))
    with pytest.raises(EvidenceError,match='REVIEW_REQUIRED'):
        replace(plan.review,restriction_state='HELD')


def test_cli_preflight_is_offline_and_collect_requires_exact_review(capture_rig,tmp_path,monkeypatch,capsys):
    from tools import v11_real_input_capture as cli
    config=tmp_path/'config.json';config.write_text(json.dumps(asdict(capture_rig['plan'])))
    monkeypatch.setattr(cli.time,'time',lambda:capture_rig['now'][0])
    def forbidden(*a,**kw):raise AssertionError('STORE_OPENED')
    monkeypatch.setattr(cli,'EvidenceStore',forbidden)
    assert cli.main(['--config',str(config)])==0
    assert json.loads(capsys.readouterr().out)['config_sha256']==digest(asdict(capture_rig['plan']))
    assert cli.main(['--config',str(config),'--collect'])==2
    assert 'REVIEWED_CONFIG_STORE_CYCLE_REQUIRED' in capsys.readouterr().out


def test_actual_eleven_bucket_shape_collects_all_tokens_in_scheduled_batches(capture_rig):
    r=capture_rig
    r['rule']=fingerprint_event(_event(station='KATL', labels=['60°F or lower']+
        [f'{t}°F' for t in range(61,70)]+['70°F or higher']),
        station_timezone=official().timezone,metadata_fingerprint=official().fingerprint)
    draft=object.__new__(RealInputPlan)
    object.__setattr__(draft,'rule',r['rule']);object.__setattr__(draft,'official',official())
    review=replace(r['plan'].review,requests_sha256=digest([asdict(req) for req in draft.requests()]))
    r['plan']=replace(r['plan'],rule=r['rule'],review=review)
    row,calls=run(r);d=row['body']['details']
    assert d['outcome']=='FRESH_SOURCE_EVIDENCE_ONLY',d
    assert len(calls)==24 and len(d['book_ids'])==22


def test_long_book_batches_cannot_publish_expired_early_receipts(capture_rig):
    r=capture_rig
    r['rule']=fingerprint_event(_event(station='KATL', labels=['60°F or lower']+
        [f'{t}°F' for t in range(61,70)]+['70°F or higher']),
        station_timezone=official().timezone,metadata_fingerprint=official().fingerprint)
    draft=object.__new__(RealInputPlan)
    object.__setattr__(draft,'rule',r['rule']);object.__setattr__(draft,'official',official())
    review=replace(r['plan'].review,requests_sha256=digest([asdict(req) for req in draft.requests()]))
    r['plan']=replace(r['plan'],rule=r['rule'],review=review)
    row,calls=run(r,delay=2);d=row['body']['details']
    assert len(calls)==24 and d['outcome']=='GATED',d
    assert any('RECEIPT_STALE' in e or 'SOURCE_EXPIRED' in e for e in d['errors'])


def test_expiration_during_qc_preserves_receipts_but_gates_publication(capture_rig,monkeypatch):
    from polymarket_scanner.v11.pws_runtime import PWSQualityWorker
    original=PWSQualityWorker.step
    def delayed(self,key):
        result=original(self,key)
        capture_rig['now'][0]+=31
        return result
    monkeypatch.setattr(PWSQualityWorker,'step',delayed)
    row,_=run(capture_rig);d=row['body']['details']
    assert d['outcome']=='GATED' and 'REAL_INPUT_SOURCE_EXPIRED_BEFORE_PUBLICATION' in d['errors']
    assert len(d['raw_ids'])==8 and d['qc_id']


def test_superseded_book_before_publication_gates(capture_rig,monkeypatch):
    from polymarket_scanner.v11.pws_runtime import PWSQualityWorker
    from polymarket_scanner.v11.book_inputs import PROVIDER
    original=PWSQualityWorker.step
    def supersede(self,key):
        result=original(self,key)
        store=capture_rig['store']
        row=store.latest_source(kind='BOOK',event_id=capture_rig['rule'].payload['event_id'],
            provider=PROVIDER,source_identity=capture_rig['rule'].payload['partition'][0]['yes_token'])
        raw=store.get(row['body']['payload']['raw_evidence_id'])
        store.capture('superseding-book',event_id=raw['event_id'],kind='BOOK',provider=PROVIDER,
            source_identity=raw['body']['source_identity'],revision='later',
            payload=raw['body']['payload'],evidence_class='SYNTHETIC')
        return result
    monkeypatch.setattr(PWSQualityWorker,'step',supersede)
    row,_=run(capture_rig);d=row['body']['details']
    assert d['outcome']=='GATED' and 'REAL_INPUT_SOURCE_CHANGED' in d['errors']


def test_clock_gate_sends_no_requests(capture_rig):
    capture_rig['health'].sync_probe=lambda: dict(synchronized=False,mechanism='LOCAL_SYSTEMD_TIMEDATED')
    row,calls=run(capture_rig)
    assert row['body']['details']['outcome']=='CLOCK_GATED' and not calls


def test_source_change_during_publication_cannot_commit_fresh_result(capture_rig,monkeypatch):
    r=capture_rig; original=r['store'].audit
    def race(key,**kwargs):
        if kwargs.get('details',{}).get('outcome')=='FRESH_SOURCE_EVIDENCE_ONLY':
            r['store'].capture('raced-book',event_id=r['rule'].payload['event_id'],kind='BOOK',
                provider='fixture',source_identity='different-source',revision='one',payload={})
        return original(key,**kwargs)
    monkeypatch.setattr(r['store'],'audit',race)
    with pytest.raises(EvidenceError,match='STATE_CHANGED'):
        run(r)
    assert r['store'].latest(kind='RUNTIME_STATUS',event_id='v11-real-input-capture')['body']['details']['outcome']=='STARTED'


def test_cli_collect_opens_dedicated_store_and_runs_full_source_path(capture_rig,tmp_path,monkeypatch,capsys):
    from types import SimpleNamespace
    from tools import v11_real_input_capture as cli
    from polymarket_scanner.v11 import runtime_health
    from polymarket_scanner.v11.evidence import EvidenceStore
    r=capture_rig; config=tmp_path/'commission.json'; config.write_text(json.dumps(asdict(r['plan'])))
    destination=tmp_path/'commission.sqlite'
    original_client=httpx.AsyncClient
    calls=[]
    def transport(req):
        calls.append(req)
        if req.url.host=='madis-data.ncep.noaa.gov':return httpx.Response(200,text=xml(r['now'][0]))
        if req.url.host=='aviationweather.gov':
            return httpx.Response(200,json=[dict(icaoId='KATL',obsTime=r['now'][0]-1,temp=25)])
        return httpx.Response(200,json=response(r,req.url.params['token_id']))
    monkeypatch.setattr(httpx,'AsyncClient',lambda **kw:original_client(transport=httpx.MockTransport(transport),**kw))
    monkeypatch.setattr(cli,'EvidenceStore',lambda path,namespace:EvidenceStore(path,namespace,clock=lambda:r['now'][0]))
    monkeypatch.setattr(runtime_health.subprocess,'run',lambda *a,**kw:SimpleNamespace(stdout=b'yes\n',returncode=0))
    monkeypatch.setattr(cli.time,'time',lambda:r['now'][0])
    async def sleep(seconds):r['now'][0]+=seconds
    monkeypatch.setattr(cli.asyncio,'sleep',sleep)
    assert cli.main(['--config',str(config),'--collect','--store',str(destination),'--cycle','cli-one',
                     '--reviewed-config-sha256',digest(asdict(r['plan']))])==0
    result=json.loads(capsys.readouterr().out)
    assert result['outcome']=='FRESH_SOURCE_EVIDENCE_ONLY' and len(calls)==8
    assert result['recorded_at']==r['now'][0] and not result['acceptance_granted']
    store=EvidenceStore(destination,'CHALLENGER:real-input-capture')
    assert not store.records(kind='COORDINATOR_EVENT') and not store.records(kind='TRADE')


def test_transient_history_full_page_fails_closed(monkeypatch):
    from polymarket_scanner.v11 import real_input_capture as ric
    from polymarket_scanner.v11.evidence import EvidenceError

    class Store:
        def records(self, **kwargs):
            return [{'body': {'details': {}}}] * 1000

    capture = object.__new__(ric.RealInputCapture)
    capture.store = Store()
    try:
        capture._transient_deferral_history()
    except EvidenceError as exc:
        assert str(exc) == 'REAL_INPUT_MADIS_TRANSIENT_HISTORY_BOUND'
    else:
        raise AssertionError('full page must fail closed')
