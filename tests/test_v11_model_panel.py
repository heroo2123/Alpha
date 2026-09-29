"""Synthetic provider bytes only; these tests are not live-access evidence."""
from dataclasses import asdict, replace
from datetime import datetime, timezone
import ast
import asyncio
import base64
import hashlib
import json
from pathlib import Path
import sqlite3

import httpx
import pytest

from polymarket_scanner.v11.evidence import EvidenceError, EvidenceStore, canonical, digest
from polymarket_scanner.v11.model_panel import (
    SourceIdentity, StationTarget, ForecastSlice, MemberObservation, temperature,
    archive_raw, read_raw, persist_slice, strict_json, MAX_RAW_BYTES,
)
from polymarket_scanner.v11.ecmwf_sources import (
    ECMWFRequest, ByteRange, plan_ranges, access_state, ECMWFCollector, normalize_ecmwf,
)
from polymarket_scanner.v11.ecmwf_grib import release_signature, decode_station
from polymarket_scanner.v11.weathernext_sources import (
    WeatherNextRequest, AccessState, access_gate, archive_fixture, normalize_weathernext,
    SCHEMA, ENSEMBLE_DATASET, STATISTICS_DATASET,
)
from test_v11_grib_fields import grib, mutate, u, signed

RUN = datetime(2026, 9, 28, tzinfo=timezone.utc).timestamp()
TARGET = StationTarget('test-panel', 'KATL', '2026-09-28', 'America/New_York', 33., -84., 'a'*64, 'b'*64)


@pytest.fixture
def store(tmp_path):
    tmp_path.chmod(0o700)
    return EvidenceStore(tmp_path/'panel.sqlite', 'CHALLENGER:panel', clock=lambda: RUN+36000)


def source(provider='ECMWF_AIFS_ENS'):
    dataset = ENSEMBLE_DATASET if provider == 'GOOGLE_WEATHERNEXT3' else 'ecmwf-open-data:0p25:enfo'
    return SourceIdentity(provider, '3.0.0' if provider == 'GOOGLE_WEATHERNEXT3' else 'test-release-v1', dataset, 'c'*64)


def ecmwf_bytes(member=0, packing=0):
    data = grib(member=member, hour=6, run=RUN, packing=packing)
    data = mutate(data, 1, 5, u(98, 2)+u(0, 2))
    data = mutate(data, 4, 34, bytes([0 if member == 0 else 3, member, 51]))
    data = mutate(data, 3, 55, signed(33250000, 4)+signed(276250000, 4))
    return mutate(data, 3, 63, u(250000, 4)+u(250000, 4))


def request(data=None, provider='ECMWF_AIFS_ENS', member=0):
    data = ecmwf_bytes(member) if data is None else data
    return ECMWFRequest(source(provider), RUN, 6, member, release_signature(data))


def index_bytes(req, data, *, offset=0, **overrides):
    row = dict(req.selectors, _offset=offset, _length=len(data)); row.update(overrides)
    return canonical(row).encode()+b'\n'


def archive_ecmwf(store, req=None, data=None, *, evidence_class='SYNTHETIC'):
    data = ecmwf_bytes() if data is None else data
    req = request(data) if req is None else req
    index = index_bytes(req, data)
    ix = archive_raw(store, 'index', source=req.source, target=TARGET, request=dict(req.identity, stage='INDEX'),
                     raw=index, initialized_at=RUN, evidence_class=evidence_class)
    selection, = plan_ranges(index, (req,))
    archive_raw(store, 'raw', source=req.source, target=TARGET,
                request=dict(req.identity, selection=asdict(selection), index_id=ix['id'], index_evidence_sha256=ix['sha256']),
                raw=data, initialized_at=RUN, evidence_class=evidence_class)
    return req


def wn_request(*, statistics=False, **changes):
    s = source('GOOGLE_WEATHERNEXT3')
    if statistics: s = replace(s, dataset=STATISTICS_DATASET)
    return replace(WeatherNextRequest(s, RUN, 6, representation='STATISTICS' if statistics else 'MEMBERS'), **changes)


def wn_bytes(req=None, **changes):
    req = wn_request() if req is None else req
    d = dict(schema=SCHEMA, dataset=req.source.dataset, model_version='3.0.0', generation='synthetic-generation-1',
        initialized_at=req.initialized_at, valid_at=req.initialized_at+req.lead_hour*3600,
        lead_time_hours=req.lead_hour, lead_subtime_hours=0, variable=req.variable, unit='K', member_count=64,
        latitude=33., longitude=276., representation=req.representation,
        members=[dict(member=i, value=290.+i/100) for i in range(64)] if req.representation == 'MEMBERS' else [],
        statistics={} if req.representation == 'MEMBERS' else dict(mean=290., p10=288., p25=289., p50=290., p75=291., p90=292.),
        parent_sha256s=['d'*64])
    d.update(changes)
    return canonical(d).encode()


def test_source_identity_includes_release_dataset_and_version():
    s = source()
    assert len({s.key, replace(s, model_version='test-v2').key,
                replace(s, dataset='other').key, replace(s, release_evidence_sha256='d'*64).key}) == 4
    with pytest.raises(EvidenceError): replace(s, model_version='latest')


@pytest.mark.parametrize('provider', ['NOAA_GEFS', 'ECMWF_IFS_ENS', 'ECMWF_AIFS_ENS', 'GOOGLE_WEATHERNEXT3', 'ECMWF_AIFS_SINGLE'])
def test_all_providers_have_explicit_dependence_and_no_votes(provider):
    s = source(provider)
    assert s.dependence['independent_vote'] is False
    assert s.dependence['shared_domain'] == 'GLOBAL_ATMOSPHERE'
    assert s.dependence['independence'] == 'NEVER_ASSUMED'
    assert s.member_count <= 64
    if 'AIFS' in provider: assert s.dependence['vote_family'] == 'AIFS'
    if provider.endswith('SINGLE'): assert s.dependence['auxiliary_only']


@pytest.mark.parametrize('value,unit,out,expected', [(273.15, 'K', 'C', 0.), (0., 'C', 'F', 32.), (32., 'F', 'K', 273.15), (290., 'K', 'K', 290.)])
def test_units(value, unit, out, expected):
    assert temperature(value, unit, out) == pytest.approx(expected)


@pytest.mark.parametrize('value,unit', [(float('nan'), 'K'), (True, 'K'), (149., 'K'), (351., 'K'), (20., 'kelvin')])
def test_bad_units_and_values(value, unit):
    with pytest.raises(EvidenceError): temperature(value, unit, 'C')


@pytest.mark.parametrize('provider', ['ECMWF_IFS_ENS', 'ECMWF_AIFS_ENS'])
@pytest.mark.parametrize('member', [0, 1, 50])
@pytest.mark.parametrize('packing', [0, 1, 2])
def test_ecmwf_run_member_station_normalization_and_replay(store, provider, member, packing):
    data = ecmwf_bytes(member, packing); req = request(data, provider, member)
    archive_ecmwf(store, req, data)
    first = normalize_ecmwf(store, 'raw', request=req, target=TARGET, index_id='index')
    second = normalize_ecmwf(store, 'raw', request=req, target=TARGET, index_id='index')
    assert first == second
    assert len(first.members) == 1 and first.members[0].member_id == member
    assert first.members[0].value == pytest.approx(16.85)
    assert first.initialized_at == RUN and first.valid_at == RUN+21600
    assert first.grid_longitude == -84. and first.source.member_count == 51
    with pytest.raises(EvidenceError, match='SYNTHETIC'): first.require_causal(RUN+36000)
    with pytest.raises(EvidenceError, match='CUTOFF'): first.require_causal(RUN+35999, allow_synthetic=True)
    first.require_causal(RUN+36000, allow_synthetic=True)
    store.clock = lambda: RUN+40000
    saved = persist_slice(store, 'normalized', first)
    assert saved['body']['available_at'] == RUN+40000
    assert persist_slice(store, 'normalized', second) == saved
    assert 'normalized' not in {r['id'] for r in store.causal_inputs(TARGET.event_id, RUN+39999)}
    p = saved['body']['payload']
    for k in ('financial_authority', 'order_authority', 'settlement_authority', 'calibrated_probability'):
        assert p[k] is False
    assert 'temperature_input' not in p  # Point samples are not daily extremes.


def test_unknown_historical_receipt_is_not_backdated(store):
    req = archive_ecmwf(store, evidence_class='HISTORICAL_AVAILABILITY_UNKNOWN')
    f = normalize_ecmwf(store, 'raw', request=req, target=TARGET, index_id='index')
    assert f.received_at == RUN+36000
    with pytest.raises(EvidenceError, match='HISTORICAL'): f.require_causal(RUN+50000)
    assert store.causal_inputs(TARGET.event_id, RUN+50000) == []
    assert access_state(req, now=RUN+36000, historical=True) == 'EXTERNAL_ACCESS_REQUIRED'
    assert access_state(req, now=RUN+73*3600) == 'NOT_AVAILABLE'
    assert access_state(req, now=RUN-1) == 'NOT_AVAILABLE'


@pytest.mark.parametrize('field,value', [('initialized_at', RUN+1), ('step', 7), ('step', 366), ('member', 51), ('member', True)])
def test_ecmwf_request_bounds(field, value):
    with pytest.raises(EvidenceError): replace(request(), **{field:value})


def test_aifs_all_cycles_and_ifs_conservative_step_limit():
    for hour in (0, 6, 12, 18):
        assert replace(request(), initialized_at=RUN+hour*3600, step=360).step == 360
    with pytest.raises(EvidenceError): replace(request(provider='ECMWF_IFS_ENS'), initialized_at=RUN+21600, step=360)


@pytest.mark.parametrize('mutation', ['duplicate', 'overlap', 'missing', 'oversize', 'negative', 'bool', 'duplicate-key', 'many-rows'])
def test_ecmwf_index_rejects_ambiguous_or_unbounded_data(mutation):
    data = ecmwf_bytes(); req = request(data); ix = index_bytes(req, data)
    if mutation in ('duplicate', 'overlap'): ix += index_bytes(req, data, offset=len(data) if mutation == 'duplicate' else 0)
    elif mutation == 'missing': ix = index_bytes(req, data, param='2d')
    elif mutation == 'oversize': ix = index_bytes(req, data, _length=MAX_RAW_BYTES+1)
    elif mutation == 'negative': ix = index_bytes(req, data, _offset=-1)
    elif mutation == 'bool': ix = index_bytes(req, data, _length=True)
    elif mutation == 'duplicate-key': ix = b'{"_offset":0,"_offset":1,"_length":5}'
    else: ix = b'{}\n'*4097
    with pytest.raises(EvidenceError): plan_ranges(ix, (req,))


def test_control_number_optional_and_time_encoding():
    data = ecmwf_bytes(); req = request(data); row = json.loads(index_bytes(req, data)); del row['number']; row['time'] = '0'
    selection, = plan_ranges(canonical(row).encode(), (req,))
    assert selection.header == f'bytes=0-{len(data)-1}'


@pytest.mark.parametrize('sec,offset,value,error', [
    (1,12,u(2025,2),'RUN_MEMBER'), (4,35,b'\x01','RUN_MEMBER'),
    (4,18,u(12,4),'RUN_MEMBER'), (3,30,u(1000000,4),'GRID_POINT'),
    (3,63,u(500000,4),'RESOLUTION'), (6,5,b'\x00','BITMAP'),
    (5,9,u(40,2),'PACKING_DECODER'),
])
def test_ecmwf_grib_gates_unsupported_or_mismatched_fields(sec, offset, value, error):
    data = ecmwf_bytes(); req = request(data)
    with pytest.raises(EvidenceError, match=error): decode_station(mutate(data, sec, offset, value), request=req, target=TARGET)


def test_ecmwf_ccsds_template_42_decodes_with_eccodes():
    eccodes = pytest.importorskip('eccodes')
    simple = ecmwf_bytes()
    handle = eccodes.codes_new_from_message(simple)
    try:
        eccodes.codes_set(handle, 'packingType', 'grid_ccsds')
        data = eccodes.codes_get_message(handle)
    finally:
        eccodes.codes_release(handle)
    req = request(data)
    point = decode_station(data, request=req, target=TARGET)
    assert point['kelvin'] == pytest.approx(290.0)
    assert point['latitude'] == pytest.approx(33.0)
    assert point['longitude'] == pytest.approx(-84.0)


def test_grib_release_change_and_station_distance_fail_closed():
    data = ecmwf_bytes(); req = request(data)
    with pytest.raises(EvidenceError, match='SOURCE_VERSION'): decode_station(mutate(data, 4, 13, b'\x01'), request=req, target=TARGET)
    with pytest.raises(EvidenceError, match='DISTANCE'): decode_station(data, request=req, target=replace(TARGET, latitude=0.))


def test_archive_tamper_and_changed_source_rejected(store):
    req = archive_ecmwf(store)
    with pytest.raises(EvidenceError, match='SOURCE_VERSION_REQUEST'):
        normalize_ecmwf(store, 'raw', request=replace(req, source=replace(req.source, model_version='new-release')), target=TARGET, index_id='index')
    with sqlite3.connect(store.path) as db:
        db.execute('DROP TRIGGER v11_no_update')
        db.execute("UPDATE v11_records SET body=replace(body,'test-release-v1','test-release-v2') WHERE record_id='raw'")
    with pytest.raises(EvidenceError, match='INTEGRITY'): normalize_ecmwf(store, 'raw', request=req, target=TARGET, index_id='index')


def test_response_digest_tamper_rejected_even_inside_valid_archive(store):
    req = request(); p = dict(version='alpha_v11_model_panel_v1', source=asdict(req.source), target=asdict(TARGET),
        request=req.identity, response_base64=base64.b64encode(b'changed').decode(), response_sha256='0'*64)
    store.capture('bad', event_id=TARGET.event_id, kind='MODEL', provider=req.source.provider,
        source_identity=req.source.key, revision='bad', payload=p, issued_at=RUN, evidence_class='SYNTHETIC')
    with pytest.raises(EvidenceError, match='HASH_MISMATCH'):
        read_raw(store, 'bad', source=req.source, target=TARGET, request=req.identity)


@pytest.mark.parametrize('variable', ['station_head_temperature_2m', 'temperature_2m'])
@pytest.mark.parametrize('statistics', [False, True])
def test_weathernext_members_statistics_and_deterministic_replay(store, variable, statistics):
    req = wn_request(statistics=statistics, variable=variable)
    archive_fixture(store, 'wn', request=req, target=TARGET, raw=wn_bytes(req))
    f = normalize_weathernext(store, 'wn', request=req, target=TARGET)
    assert f == normalize_weathernext(store, 'wn', request=req, target=TARGET)
    assert len(f.members) == (0 if statistics else 64)
    assert len(f.statistics) == (6 if statistics else 0)
    assert f.source.member_count == 64 and f.grid_longitude == -84.
    assert f.evidence_class == 'SYNTHETIC' and f.payload['complete_members'] is (not statistics)
    with pytest.raises(EvidenceError, match='SYNTHETIC'): f.require_causal(RUN+50000)


@pytest.mark.parametrize('changes,error', [
    ({'member_count':65}, 'SOURCE_VERSION'), ({'members':[]}, 'COMPLETE_64'),
    ({'members':[{'member':0,'value':290.}]*64}, 'MEMBER_IDENTITY'),
    ({'unit':'C'}, 'SOURCE_VERSION'), ({'model_version':'3.1.0'}, 'SOURCE_VERSION'),
    ({'valid_at':RUN+7*3600}, 'VALID_TIME'), ({'lead_subtime_hours':1}, 'VALID_TIME'),
    ({'parent_sha256s':[]}, 'PROVENANCE'), ({'latitude':33.0123}, 'RESOLUTION'),
    ({'longitude':-84.}, 'COORDINATES'), ({'unexpected':'x'}, 'SCHEMA'),
])
def test_weathernext_schema_fail_closed(store, changes, error):
    req = wn_request(); archive_fixture(store, 'wn', request=req, target=TARGET, raw=wn_bytes(req, **changes))
    with pytest.raises(EvidenceError, match=error): normalize_weathernext(store, 'wn', request=req, target=TARGET)


def test_weathernext_time_offsets_and_interim_horizon(store):
    req = wn_request(lead_hour=1, initialized_at=RUN+3600)
    archive_fixture(store, 'wn', request=req, target=TARGET, raw=wn_bytes(req, lead_time_hours=6, lead_subtime_hours=-5))
    assert normalize_weathernext(store, 'wn', request=req, target=TARGET).valid_at == RUN+7200
    with pytest.raises(EvidenceError, match='HORIZON'): replace(req, lead_hour=49)
    with pytest.raises(EvidenceError, match='HOURLY'): replace(req, initialized_at=RUN+1)


def test_weathernext_statistics_never_become_fake_members(store):
    req = wn_request(statistics=True, surface='BIGQUERY')
    archive_fixture(store, 'wn', request=req, target=TARGET,
                    raw=wn_bytes(req, statistics=dict(mean=290., p10=300., p25=289., p50=290., p75=291., p90=292.)))
    with pytest.raises(EvidenceError, match='QUANTILE'): normalize_weathernext(store, 'wn', request=req, target=TARGET)
    with pytest.raises(EvidenceError, match='NOT_MEMBERS'): wn_request(surface='EARTH_ENGINE')


def test_weathernext_no_access_no_transport_invocation():
    class ExplodingTransport:
        def pull(self, *args, **kwargs): raise AssertionError('Must not access Google')
    for state in AccessState:
        result = access_gate(state, transport=ExplodingTransport())
        assert result != 'AVAILABLE'
    assert access_gate() == 'EXTERNAL_ACCESS_REQUIRED'
    assert access_gate(AccessState.DENIED) == 'DENIED'
    assert access_gate(AccessState.REVIEWED) == 'NOT_AVAILABLE'
    assert access_gate(AccessState.REVIEWED, historical=True) == 'EXTERNAL_ACCESS_REQUIRED'
    with pytest.raises(EvidenceError): access_gate(True)


@pytest.mark.parametrize('fault', [None, '403', '404', 'redirect', 'ignored-range', 'wrong-range', 'truncated', 'over-limit'])
def test_ecmwf_bounded_anonymous_transport(store, fault):
    data = ecmwf_bytes(); req = request(data); seen = []
    def handler(r):
        seen.append(r)
        assert r.method == 'GET' and r.url.host == 'data.ecmwf.int'
        assert not any(k in r.headers for k in ('authorization', 'cookie', 'x-api-key'))
        if r.url.path.endswith('.index'):
            return httpx.Response(200, stream=httpx.ByteStream(index_bytes(req, data)), headers={'set-cookie':'private=not-forwarded'})
        headers = {'content-range':f'bytes 0-{len(data)-1}/{len(data)}'}; status = 206; body = data
        if fault in ('403','404'): status = int(fault)
        elif fault == 'redirect': status = 302; headers = {'location':'https://example.org/forbidden'}
        elif fault == 'ignored-range': status = 200
        elif fault == 'wrong-range': headers['content-range'] = 'bytes 1-2/10'
        elif fault == 'truncated': body = data[:-1]
        elif fault == 'over-limit': body = data+b'x'
        return httpx.Response(status, headers=headers, stream=httpx.ByteStream(body))
    result = asyncio.run(ECMWFCollector(store, transport=httpx.MockTransport(handler)).collect(req, TARGET, 'download'))
    assert len(seen) == 2 and seen[1].headers['range'] == f'bytes=0-{len(data)-1}'
    assert store.get('download:index')
    if fault is None:
        assert result['state'] == 'CAPTURED'
        assert store.get('download')['body']['evidence_class'] == 'SYNTHETIC'
        assert normalize_ecmwf(store, 'download', request=req, target=TARGET, index_id='download:index').members[0].value == pytest.approx(16.85)
    else:
        assert result['state'] != 'CAPTURED' and result['raw_id'] is None
        with pytest.raises(EvidenceError, match='MISSING'): store.get('download')


def test_historical_ecmwf_never_calls_transport(store):
    def fail(_): raise AssertionError('No full archive access')
    result = asyncio.run(ECMWFCollector(store, transport=httpx.MockTransport(fail)).collect(request(), TARGET, 'x', historical=True))
    assert result['state'] == 'EXTERNAL_ACCESS_REQUIRED'


def test_normalization_modules_have_no_network_or_financial_imports():
    root = Path(__file__).parents[1]/'polymarket_scanner'/'v11'
    for name in ('model_panel.py', 'ecmwf_grib.py', 'weathernext_sources.py'):
        tree = ast.parse((root/name).read_text())
        imports = [n.module or '' for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)]
        imports += [x.name for n in ast.walk(tree) if isinstance(n, ast.Import) for x in n.names]
        assert not any(any(bad in m for bad in ('httpx','urllib','socket','execution','coordinator','wallet','signer')) for m in imports)


def test_strict_json_bounds_and_duplicate_keys():
    for raw in (b'{"x":1,"x":2}', b'{"x":NaN}', b'x'*(MAX_RAW_BYTES+1)):
        with pytest.raises(EvidenceError): strict_json(raw)


def test_derived_values_must_reproduce_raw_and_completion_time_is_enforced(store):
    from polymarket_scanner.v11.model_panel import replay_normalized
    req = archive_ecmwf(store)
    f = normalize_ecmwf(store, 'raw', request=req, target=TARGET, index_id='index')
    with pytest.raises(EvidenceError, match='DO_NOT_REPLAY'):
        persist_slice(store, 'forged', replace(f, members=(MemberObservation(0, 25.),)))
    store.clock = lambda: RUN+40000
    saved = persist_slice(store, 'normalized', f)
    with pytest.raises(EvidenceError, match='CUTOFF'):
        replay_normalized(store, saved['id'], source=req.source, target=TARGET, cutoff=RUN+39000, allow_synthetic=True)
    assert replay_normalized(store, saved['id'], source=req.source, target=TARGET, cutoff=RUN+40000, allow_synthetic=True) == f
    with pytest.raises(EvidenceError, match='CONTEXT'):
        replay_normalized(store, saved['id'], source=replace(req.source, model_version='v2'), target=TARGET, cutoff=RUN+40000)
    assert not store.records(kind='COORDINATOR_EVENT')
    assert not store.records(kind='MODEL_EVENT')
    assert not store.records(kind='LABEL')


def test_weathernext_public_claim_cannot_bypass_access_gate(store):
    req = wn_request()
    archive_raw(store, 'unreviewed', source=req.source, target=TARGET, request=req.identity,
                raw=wn_bytes(req), initialized_at=RUN, evidence_class='PUBLIC_OBSERVED')
    with pytest.raises(EvidenceError, match='CONNECTOR_REVIEW'):
        normalize_weathernext(store, 'unreviewed', request=req, target=TARGET)


def test_weathernext_offline_archive_reader_revalidates_members(store):
    from polymarket_scanner.v11.model_panel import replay_normalized
    req = wn_request()
    archive_fixture(store, 'wn', request=req, target=TARGET, raw=wn_bytes(req))
    f = normalize_weathernext(store, 'wn', request=req, target=TARGET)
    saved = persist_slice(store, 'normalized', f)
    assert replay_normalized(store, saved['id'], source=req.source, target=TARGET,
                             cutoff=store.clock(), allow_synthetic=True) == f


@pytest.mark.parametrize('changes', [
    {'received_at': RUN-1}, {'available_at':RUN+35999}, {'published_at':RUN+36001},
    {'valid_at':RUN-1}, {'members':(MemberObservation(51, 20.),)},
    {'members':(MemberObservation(0, 20.), MemberObservation(0, 21.))},
    {'statistics':(('mean',20.),)},
])
def test_common_contract_rejects_causality_and_member_errors(store, changes):
    req = archive_ecmwf(store)
    f = normalize_ecmwf(store, 'raw', request=req, target=TARGET, index_id='index')
    with pytest.raises(EvidenceError): replace(f, **changes)


def test_gefs_compatibility_bridge_uses_unchanged_decoder_and_archive(tmp_path):
    from polymarket_scanner.v11.model_panel import normalize_gefs_panel
    from polymarket_scanner.v11.gefs_sources import GEFSPlan
    from polymarket_scanner.v11.forecast_sources import ForecastPlan
    from polymarket_scanner.v11.rules import fingerprint_event
    from test_v11_gefs_sources import raw as gefs_raw
    from test_v11_pws_quality import official
    from test_weather_final_gpt6_exact_replays import _event
    from test_v11_grib_fields import RUN as GEFS_RUN
    m = official(); rule = fingerprint_event(_event(station='KATL'), station_timezone=m.timezone, metadata_fingerprint=m.fingerprint)
    plan = GEFSPlan(ForecastPlan(rule, m, 50., 3600.), GEFS_RUN)
    tmp_path.chmod(0o700)
    store = EvidenceStore(tmp_path/'gefs.sqlite', 'CHALLENGER:gefs', clock=lambda:GEFS_RUN+3600)
    r = {'store':store, 'plan':plan}; raw = gefs_raw(r); hour = plan.hours[0]
    s = SourceIdentity('NOAA_GEFS', 'test-gefs-v1', 'noaa-nomads:gefs:0p50', 'c'*64)
    f = normalize_gefs_panel(store, raw['id'], source=s, plan=plan, member=0, hour=hour, record_id='field')
    assert f.source.member_count == 31 and f.members[0].member_id == 0
    assert f.valid_at == GEFS_RUN+hour*3600
    assert f.target.rule_fingerprint == rule.sha256
    assert store.get(raw['id']) == raw
