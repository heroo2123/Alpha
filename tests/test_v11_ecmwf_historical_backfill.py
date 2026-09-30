"""Offline-only historical worker coverage; mock transport never opens a socket."""
import asyncio
from dataclasses import replace
import hashlib
import json
import sqlite3

import httpx
import pytest

from tools import v11_ecmwf_historical_backfill as worker
from polymarket_scanner.v11 import ecmwf_grib
from polymarket_scanner.v11.ecmwf_sources import ByteRange, MAX_INDEX_BYTES
from polymarket_scanner.v11.evidence import EvidenceError
from test_v11_model_panel import ecmwf_bytes, index_bytes, request, TARGET
from test_v11_grib_fields import mutate, u

SAFE = dict(root_used_percent=20, root_free_bytes=10**10, output_used_percent=20, output_free_bytes=10**10)


@pytest.fixture
def setup(tmp_path, monkeypatch):
    monkeypatch.setattr(worker, 'ROOT', tmp_path)
    plan = dict(financial_authority=False, promotion_authority=False, station_days=[
        dict(station=station, run_date='2026-09-28', target_date='2026-09-28', cycle=0,
             latitude=33., longitude=-84., split=split, forecast_hours=[6])
        for station, split in [('KATL', 'TRAIN'), ('TEST', 'HISTORICAL_CONFIRMATION')]])
    path = tmp_path/'plan.json'; path.write_text(json.dumps(plan))
    return dict(plan=path, output=tmp_path/'points.sqlite', progress_path=tmp_path/'progress.json',
                disk_check=lambda _: SAFE, sleep=lambda _: asyncio.sleep(0))


def mock_source(fault=None):
    seen = []
    def handler(r):
        seen.append(r)
        assert not any(k in r.headers for k in ('authorization', 'proxy-authorization', 'cookie'))
        assert r.headers['accept-encoding'] == 'identity'
        provider = 'ECMWF_AIFS_ENS' if '/aifs-ens/' in r.url.path else 'ECMWF_IFS_ENS'
        perturbed = r.url.path.endswith(('-ef.index', '-pf.index', '-ef.grib2', '-pf.grib2'))
        members = range(1, 51) if perturbed else (0,)
        fields = [ecmwf_bytes(m, provider=provider) for m in members]
        if r.url.path.endswith('.index'):
            offset = 0; rows = []
            for member, raw in zip(members, fields):
                rows.append(index_bytes(request(raw, provider, member), raw, offset=offset))
                offset += len(raw)
            return httpx.Response(200, stream=httpx.ByteStream(b''.join(rows)),
                                  headers={'set-cookie': 'unsafe=cookie'})
        start, end = map(int, r.headers['range'][6:].split('-'))
        data = b''.join(fields)[start:end+1]
        if fault == 'member':
            data = mutate(data, 4, 35, b'\x32')
        if fault == 'error':
            return httpx.Response(503)
        headers = {'content-range': f'bytes {start}-{end}/{sum(map(len, fields))}', 'content-length': str(len(data))}
        return httpx.Response(206, headers=headers, stream=httpx.ByteStream(data))
    return httpx.MockTransport(handler), seen


def run(setup, **kwargs):
    return asyncio.run(worker.run(**(setup | kwargs)))


@pytest.mark.parametrize('provider,member,suffix,kind', [
    ('AIFS', 0, '/aifs-ens/0p25/enfo/20260928000000-6h-enfo-cf.grib2', 'cf'),
    ('AIFS', 50, '/aifs-ens/0p25/enfo/20260928000000-6h-enfo-pf.grib2', 'pf'),
    ('IFS', 0, '/ifs/0p25/oper/20260928000000-6h-oper-fc.grib2', 'fc'),
    ('IFS', 50, '/ifs/0p25/enfo/20260928000000-6h-enfo-ef.grib2', 'pf')])
def test_public_layout(provider, member, suffix, kind):
    req = worker.request_for((provider, '2026-09-28', 0, 6, member))
    assert worker.public_url(req) == worker.PUBLIC_ROOT+'/20260928/00z'+suffix
    assert req.selectors['type'] == kind


def test_common_six_hour_hours_without_interpolation(setup):
    plan = json.loads(setup['plan'].read_text())
    for row in plan['station_days']:
        row['forecast_hours'] = [3, 6, 9]
    setup['plan'].write_text(json.dumps(plan))
    result = run(setup)
    assert result['providers']['AIFS'] == dict(total=51, done=0, failed=0, pending=51)
    assert result['providers']['IFS'] == dict(total=51, done=0, failed=0, pending=51)
    assert result['station_days_combined_complete'] == 0
    assert result['provider_policies']['AIFS']['unsupported_plan_hours_omitted'] == [3, 9]
    assert result['provider_policies']['IFS']['unsupported_plan_hours_omitted'] == [3, 9]
    with sqlite3.connect(setup['output']) as db:
        expected = db.execute(
            "SELECT provider,expected_values,required_hours_json,omitted_hours_json "
            "FROM station_provider_expectations WHERE station_day='KATL|2026-09-28' ORDER BY provider"
        ).fetchall()
        assert expected[0] == ('AIFS', 51, '[6]', '[3, 9]')
        assert expected[1] == ('IFS', 51, '[6]', '[3, 9]')
    with pytest.raises(EvidenceError):
        worker.request_for(('AIFS', '2026-09-28', 0, 3, 0))
    with pytest.raises(EvidenceError):
        worker.request_for(('IFS', '2026-09-28', 0, 3, 0))


@pytest.mark.parametrize('fault', ['duplicate', 'overlap', 'oversize', 'keys', 'rows', 'bytes'])
def test_strict_index_rejects_malformed_file(fault):
    raw = ecmwf_bytes(); req = request(raw); idx = index_bytes(req, raw)
    if fault == 'duplicate': idx += index_bytes(req, raw, offset=len(raw))
    if fault == 'overlap': idx += index_bytes(req, raw, offset=1)
    if fault == 'oversize': idx = index_bytes(req, raw, _length=4*1024**2+1)
    if fault == 'keys': idx = idx.replace(b'"_offset":0', b'"_offset":0,"_offset":0')
    if fault == 'rows': idx = b'{}\n'*12001
    if fault == 'bytes': idx = b' '* (MAX_INDEX_BYTES+1)
    with pytest.raises(EvidenceError):
        worker.file_ranges(idx, (req,))


@pytest.mark.parametrize('fault', ['wrong-member', 'wrong-model'])
def test_strict_index_missing_requested_selector_is_explicit(fault):
    raw = ecmwf_bytes(); req = request(raw)
    overrides = {'number':'1'} if fault == 'wrong-member' else {'class':'od'}
    idx = index_bytes(req, raw, **overrides)
    assert worker.file_ranges(idx, (req,)) == {}


@pytest.mark.parametrize('fault', ['200', 'redirect', 'encoding', 'range', 'total', 'length', 'truncated', 'oversize'])
def test_exact_range_transport(fault):
    raw = b'1234'; headers = {'content-range':'bytes 10-13/14', 'content-length':'4'}; status = 206
    if fault == '200': status = 200
    if fault == 'redirect': status = 302; headers['location'] = 'https://example.invalid'
    if fault == 'encoding': headers['content-encoding'] = 'gzip'
    if fault == 'range': headers['content-range'] = 'bytes 9-12/14'
    if fault == 'total': headers['content-range'] = 'bytes 10-13/13'
    if fault == 'length': headers['content-length'] = '5'
    if fault == 'truncated': raw = b'123'
    if fault == 'oversize': raw = b'12345'
    transport = httpx.MockTransport(lambda _: httpx.Response(status, headers=headers, stream=httpx.ByteStream(raw)))
    with pytest.raises(EvidenceError):
        asyncio.run(worker.AnonymousFetcher(transport).get(worker.PUBLIC_ROOT, 4, ByteRange(10, 4, 'a'*64)))


def test_mock_bounded_resume_dedupe_no_raw(setup, monkeypatch):
    for name in ('HTTP_PROXY', 'HTTPS_PROXY', 'ALL_PROXY'):
        monkeypatch.setenv(name, 'http://invalid.invalid:1')
    monkeypatch.setenv('NETRC', str(setup['plan']))
    transport, seen = mock_source()
    parses = []
    original_ranges = worker.file_ranges
    def counted_ranges(index, requests):
        parses.append((len(index), tuple(r.member for r in requests)))
        return original_ranges(index, requests)
    monkeypatch.setattr(worker, 'file_ranges', counted_ranges)
    result = run(setup, execute=True, provider='AIFS', max_messages=4, transport=transport)
    assert result['providers']['AIFS']['done'] == 4
    assert len(seen) == 6  # two indexes; four fields, shared across two stations
    assert len(parses) == 2  # once for cf file, once for pf file; never once/member
    assert result['financial_authority'] is False and result['promotion_authority'] is False
    assert result['historical_availability'] == 'UNKNOWN' and result['availability_at'] is None
    assert result['evidence_class'] == 'SYNTHETIC'
    assert result['operational_release_reviewed'] is False
    with sqlite3.connect(setup['output']) as db:
        assert db.execute('PRAGMA journal_mode').fetchone()[0] == 'wal'
        assert db.execute('SELECT count(*) FROM point_values').fetchone()[0] == 8
        rows = db.execute("SELECT message_key,attempts,index_sha256,index_byte_length,grib_sha256,observed_header_sha256,byte_length FROM messages WHERE status='DONE'").fetchall()
        assert all(r[1] == 1 and len(r[2]) == len(r[4]) == len(r[5]) == 64 and r[3] > 0 and r[6] > 0 for r in rows)
        assert db.execute("SELECT count(*) FROM messages WHERE index_sha256 IS NOT NULL").fetchone()[0] == 4
    first_ranges = {(str(r.url), r.headers['range']) for r in seen if 'range' in r.headers}
    seen.clear()
    again = run(setup, execute=True, provider='AIFS', max_messages=4, transport=transport)
    assert again['providers']['AIFS']['done'] == 8
    assert not first_ranges & {(str(r.url), r.headers['range']) for r in seen if 'range' in r.headers}
    assert b'GRIB' not in setup['output'].read_bytes()
    assert b'"_offset"' not in setup['output'].read_bytes()
    assert json.loads(setup['progress_path'].read_text()) == again


def test_complete_and_idempotent(setup):
    transport, seen = mock_source()
    result = run(setup, execute=True, transport=transport)
    assert result['state'] == 'COMPLETE'
    assert result['station_days_complete'] == {'IFS':2, 'AIFS':2}
    assert result['station_days_combined_complete'] == 2
    assert result['splits']['TRAIN']['combined_complete'] == 1
    assert len(seen) == 106  # 102 messages + exactly four indexes
    seen.clear()
    assert run(setup, execute=True, transport=transport)['state'] == 'COMPLETE'
    assert not seen


def test_retry_limit_persists_across_runs(setup):
    transport, seen = mock_source('error')
    result = run(setup, execute=True, provider='AIFS', max_messages=1, transport=transport)
    assert result['providers']['AIFS']['failed'] == 1
    assert len(seen) == 4  # cached successful index + 3 single ranges
    with sqlite3.connect(setup['output']) as db:
        assert db.execute('SELECT max(attempts) FROM messages').fetchone()[0] == 3
    seen.clear()
    run(setup, execute=True, provider='AIFS', max_messages=1, transport=transport)
    assert all('-cf.' not in str(r.url) for r in seen)


def test_member_mismatch_never_persists_points(setup):
    transport, seen = mock_source('member')
    result = run(setup, execute=True, provider='AIFS', max_messages=1, transport=transport)
    assert result['providers']['AIFS']['failed'] == 1
    with sqlite3.connect(setup['output']) as db:
        assert db.execute('SELECT count(*) FROM point_values').fetchone()[0] == 0
        row = db.execute("SELECT error,grib_sha256 FROM messages WHERE status='FAILED'").fetchone()
        assert 'MEMBER' in row[0] and row[1]


def test_disk_pause_start_and_midrun(setup):
    transport, seen = mock_source()
    high = SAFE | {'root_used_percent':84.}
    result = run(setup, execute=True, provider='AIFS', transport=transport, disk_check=lambda _: high)
    assert result['state'] == 'PAUSED_DISK' and not seen and not setup['output'].exists()
    calls = 0
    def disk(_):
        nonlocal calls
        calls += 1
        return SAFE if calls < 6 else high
    result = run(setup, execute=True, provider='AIFS', concurrency=1, transport=transport, disk_check=disk)
    assert result['state'] == 'PAUSED_DISK'
    assert result['providers']['AIFS']['done'] == 1
    assert len(seen) == 2


def test_identity_and_output_fences(setup):
    run(setup)
    with pytest.raises(ValueError, match='IDENTITY'):
        run(setup, provider='IFS')
    with pytest.raises(ValueError, match='OVERRIDE'):
        run(setup, source_root='https://example.org')
    with pytest.raises(ValueError, match='OVERRIDE'):
        run(setup, source_root='https://user:pass@example.org', transport=httpx.MockTransport(lambda _: None))
    with pytest.raises(ValueError, match='COLLISION'):
        run(setup, progress_path=setup['plan'])
    with pytest.raises(ValueError, match='OUTSIDE_ALLOWED'):
        run(setup, output='/tmp/not-authorized.sqlite')
    with pytest.raises(ValueError, match='CONCURRENCY'):
        run(setup, concurrency=5)
    with pytest.raises(ValueError, match='SMOKE'):
        run(setup, probe_public_smoke=True, max_messages=5)


def test_production_destination_allowlist(tmp_path, monkeypatch):
    dbroot = tmp_path/'brain'; progressroot = tmp_path/'evidence'
    dbroot.mkdir(); progressroot.mkdir()
    monkeypatch.setattr(worker, 'ROOT', worker.WORKTREE_ROOT)
    monkeypatch.setattr(worker, 'DATABASE_ROOT', dbroot)
    monkeypatch.setattr(worker, 'PROGRESS_ROOT', progressroot)
    plan = tmp_path/'plan.json'; plan.write_text('{}')
    got = worker.checked_paths(plan, dbroot/'history.sqlite', progressroot/'progress.json')
    assert got[1:] == ((dbroot/'history.sqlite').resolve(), (progressroot/'progress.json').resolve())
    with pytest.raises(ValueError, match='ALLOWED'):
        worker.checked_paths(plan, tmp_path/'elsewhere.sqlite', progressroot/'progress2.json')


def test_smoke_only_four_fields(setup):
    transport, seen = mock_source()
    result = run(setup, probe_public_smoke=True, max_messages=4, transport=transport)
    assert sum(p['done'] for p in result['providers'].values()) == 4
    assert len(seen) == 8
    with sqlite3.connect(setup['output']) as db:
        assert db.execute("SELECT distinct member FROM messages WHERE status='DONE' ORDER BY member").fetchall() == [(0,), (1,)]


def test_batch_decoder_matches_single():
    raw = ecmwf_bytes(); req = request(raw)
    targets = (TARGET, replace(TARGET, latitude=33.1))
    assert ecmwf_grib.decode_stations(raw, request=req, targets=targets) == [
        ecmwf_grib.decode_station(raw, request=req, target=t) for t in targets]


def test_ccsds_batch_decodes_once(monkeypatch):
    eccodes = pytest.importorskip('eccodes')
    handle = eccodes.codes_new_from_message(ecmwf_bytes())
    try:
        eccodes.codes_set(handle, 'packingType', 'grid_ccsds')
        raw = eccodes.codes_get_message(handle)
    finally:
        eccodes.codes_release(handle)
    calls = []; original = eccodes.codes_get_values
    def counted(handle):
        calls.append(handle)
        return original(handle)
    monkeypatch.setattr(eccodes, 'codes_get_values', counted)
    points = ecmwf_grib.decode_stations(raw, request=request(raw), targets=(TARGET,)*15)
    assert len(points) == 15 and len(calls) == 1
    assert all(p['kelvin'] == 290. for p in points)
