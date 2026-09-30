"""Synthetic fixtures; no test success is historical availability or calibration."""
from dataclasses import replace
import hashlib
import json
from pathlib import Path

import httpx
import pytest

from tools import v11_ecmwf_extrema as x
from test_v11_grib_fields import message, u

def section(number, payload):
    return u(len(payload)+5, 4)+u(number, 1)+payload

RUN = '2026-09-29T00:00:00+00:00'
# Public GRIB metadata profile only; payload below is entirely synthetic.
IDENT = '00000015010062000020000107ea091d0000000001'
LOCAL = '0000001102000100010009040130303031'
GRID = '000000480300000fd7a00000000006ffffffffffffffffffffffffffffff000005a0000002d100000000ffffffff055d4a800aba950030855d4a800ab6c4700003d0900003d09000'
PRODUCT = '0000003a0400000008000002ffa10000000100000000670000000002ffffffffffff07ea091d0300000100000000020201000000030d000001c2'


def req(step=3, member=0, stat='max'):
    return x.IntervalRequest(x.Product('ifs', RUN, step, 'fc' if member == 0 else 'ef'),
                             ('mx' if stat == 'max' else 'mn')+'2t'+('3' if step <= 144 else '6'), member)


def index(request, raw, **changes):
    p = request.product
    row = dict(date='20260929', time='0000', expver='0001', domain='g',
               **{'class': 'od'}, type='fc' if p.kind == 'fc' else 'pf',
               stream=p.stream, levtype='sfc', step=str(p.step), param=request.param,
               _offset=0, _length=len(raw))
    if request.member: row['number'] = str(request.member)
    row.update(changes)
    return x.panel.canonical(row)+b'\n'


@pytest.fixture(scope='module')
def synthetic():
    ec = pytest.importorskip('eccodes')
    import numpy as np
    # Simple constant field, then encoded as CCSDS by ecCodes. Only ~200 bytes.
    packing = section(5, u(1440*721, 4)+u(0, 2)+b'\x43\x91\0\0'+b'\0'*6)
    data = message([bytes.fromhex(IDENT), bytes.fromhex(LOCAL), bytes.fromhex(GRID),
                    bytes.fromhex(PRODUCT), packing, section(6, b'\xff'), section(7, b'')])
    h = ec.codes_new_from_message(data)
    ec.codes_set(h, 'packingType', 'grid_ccsds')
    # Nonconstant synthetic values exercise real CCSDS corruption detection.
    values = 280 + np.arange(1440*721) % 32
    ec.codes_set_values(h, values.astype(float))
    raw = ec.codes_get_message(h); ec.codes_release(h)
    return raw


def change(raw, **keys):
    import eccodes as ec
    h = ec.codes_new_from_message(raw)
    try:
        for k, v in keys.items(): ec.codes_set(h, k, v)
        return ec.codes_get_message(h)
    finally: ec.codes_release(h)


def decode(raw, request=None):
    request = request or req()
    return x.decode(raw, index(request, raw), request, (x.grib.HistoricalPointTarget(33.64028, -84.42694),))


@pytest.mark.parametrize('member', [0, 1, 50])
@pytest.mark.parametrize('step,stat', [(3, 'max'), (6, 'min'), (150, 'max'), (150, 'min')])
def test_native_decodes_two_cadences_all_member_edges(synthetic, member, step, stat):
    raw = synthetic
    if member:
        raw = change(raw, productDefinitionTemplateNumber=11, typeOfGeneratingProcess=4,
                     typeOfProcessedData=4, marsStream='enfo', marsType='pf',
                     typeOfEnsembleForecast=255, perturbationNumber=member, numberOfForecastsInEnsemble=51)
    width = 3 if step <= 144 else 6
    raw = change(raw, stepType=stat, startStep=step-width, endStep=step)
    f = decode(raw, req(step, member, stat))
    assert f.end_utc-f.start_utc == width*3600
    assert f.metadata['units'] == 'K'
    assert f.points[0]['latitude'] == 33.75
    assert f.points[0]['longitude'] == -84.5
    assert f.endpoint_membership == 'NOT_ATTESTED_BY_GRIB'


@pytest.mark.parametrize('keys', [
    {'typeOfStatisticalProcessing': 0}, {'numberOfMissingInStatisticalProcess': 1},
    {'timeIncrement': 900}, {'indicatorOfUnitForTimeIncrement': 1},
    {'typeOfTimeIncrement': 1}, {'generatingProcessIdentifier': 162},
    {'tablesVersion': 33}, {'scaledValueOfFirstFixedSurface': 10},
    {'dataDate': 20260928}, {'dataTime': 600}, {'second': 1},
    {'startStep': 1}, {'endStep': 6}, {'productionStatusOfProcessedData': 1},
    {'typeOfProcessedData': 4}, {'iDirectionIncrementInDegrees': .5},
    {'longitudeOfLastGridPointInDegrees': 179.5}, {'scanningMode': 64},
    {'typeOfGeneratingProcess': 4}, {'productDefinitionTemplateNumber': 0},
])
def test_metadata_corruption_rejected(synthetic, keys):
    raw = change(synthetic, **keys)
    with pytest.raises((x.panel.PanelError, x.grib.EvidenceError, ValueError)):
        decode(raw)


def test_wrong_member_rejected(synthetic):
    raw = change(synthetic, productDefinitionTemplateNumber=11, typeOfGeneratingProcess=4,
                 typeOfProcessedData=4, marsStream='enfo', marsType='pf',
                 typeOfEnsembleForecast=255, perturbationNumber=1, numberOfForecastsInEnsemble=51)
    with pytest.raises(x.panel.PanelError, match='ENSEMBLE_IDENTITY'): decode(raw, req(member=50))


def test_self_consistent_ccsds_truncation_rejected(synthetic):
    sections = x.grib.sections(synthetic)
    body = sections[7][:-128]
    sections[7] = u(len(body), 4)+body[4:]
    raw = message(list(sections.values()))
    with pytest.raises(x.panel.PanelError, match='NATIVE_DECODE_FAILED'): decode(raw)


def test_missing_native_dependency_controlled(synthetic, monkeypatch):
    import sys
    monkeypatch.setitem(sys.modules, 'eccodes', None)
    with pytest.raises(x.panel.PanelError, match='ECCODES_UNAVAILABLE'): decode(synthetic)


@pytest.mark.parametrize('changes', [{'_offset': True}, {'_length': 0}, {'number': 0},
    {'number': '01'}, {'number': '1'}, {'param': []}, {'date': '20260928'}, {'step': 3},
    {'stream': 'enfo'}, {'class': 'ai'}, {'expver': '0002'}])
def test_index_corruption_rejected(changes):
    with pytest.raises(x.panel.PanelError): x.select(index(req(), b'a', **changes), req())


def test_duplicate_overlap_and_absent_index_rejected():
    ix = index(req(), b'abcd')
    for raw in (ix+ix, ix.replace(b'"param":', b'"param":"x","param":')):
        with pytest.raises(x.panel.PanelError): x.select(raw, req())
    with pytest.raises(x.panel.PanelError, match='ABSENT'):
        x.select(index(req(), b'a', param='2t'), req())


@pytest.mark.parametrize('step', [True, '3', -3, 1, 145, 147, 363])
def test_bad_steps(step):
    with pytest.raises(x.panel.PanelError): x.Product('ifs', RUN, step, 'fc')


def test_no_aifs_point_extrema_contract():
    with pytest.raises(x.panel.PanelError):
        x.IntervalRequest(x.Product('aifs-ens', RUN, 6, 'cf'), 'mx2t3', 0)


def field(start, end, value, member=0):
    return x.NativeField(req(member=member), start, end, 'a'*64, 'b'*64,
                         {'observed_header_sha256': 'c'*64},
                         ({'latitude': 1., 'longitude': 2., 'kelvin': value, 'grid_sha256': 'd'*64},))


def test_complete_window_is_only_native_diagnostic():
    result = x.native_window((field(3, 6, 291), field(0, 3, 290)), 0, 6)
    assert result['native_window_extrema_k'] == [291]
    assert result['exact_day_extrema_c'] is None and result['learner_admitted'] is False


@pytest.mark.parametrize('bounds', [[(0, 3), (4, 6)], [(0, 4), (3, 6)], [(-1, 3), (3, 6)],
                                   [(0, 3), (3, 7)], [(0, 3)], [(0, 3), (0, 3)]])
def test_window_cannot_split_fill_duplicate_or_extrapolate(bounds):
    with pytest.raises(x.panel.PanelError):
        x.native_window(tuple(field(a, b, 290) for a, b in bounds), 0, 6)


def test_window_identity():
    with pytest.raises(x.panel.PanelError, match='IDENTITY'):
        x.native_window((field(0, 3, 290), field(3, 6, 291, member=1)), 0, 6)


@pytest.mark.parametrize('day,zone,hours', [('2026-03-08','America/New_York',23),
    ('2026-11-01','America/New_York',25), ('2026-09-28','Asia/Kolkata',24)])
def test_dst_and_fractional_days_are_not_forced_to_native_intervals(day, zone, hours):
    start, end = x.panel.local_window(day, zone)
    assert end-start == hours*3600
    assert start % 10800 or end % 10800


def capture(tmp_path, monkeypatch, handler):
    monkeypatch.setattr(x, 'ROOT', tmp_path)
    return x.Capture(tmp_path/'private-evidence'/'capture', transport=httpx.MockTransport(handler))


def test_throttle_stops_origin_and_preserves_success(tmp_path, monkeypatch):
    calls = []
    def handler(r):
        calls.append(r)
        return httpx.Response(503 if 'amazonaws' in str(r.url) else 200, stream=httpx.ByteStream(b'public'))
    c = capture(tmp_path, monkeypatch, handler)
    try:
        a = c.get('portal', '/'); b = c.get('aws', '/x.index'); d = c.get('aws', '/y.index')
        assert a['status'] == 200 and b['status'] == 503
        assert d['status'] == 'NOT_ATTEMPTED_ORIGIN_THROTTLED' and len(calls) == 2
        assert x.read_object(c.directory, a) == b'public'
        assert a['evidence_class'] == 'SYNTHETIC'
    finally: c.close()


@pytest.mark.parametrize('status,headers,body', [(200, {}, b'x'),
    (206, {'content-range':'bytes 1-1/10'}, b'x'), (206, {'content-range':'bytes 0-0/10'}, b'xx'),
    (206, {'content-range':'bytes 0-0/10','content-encoding':'gzip'}, b'x')])
def test_bad_http_range(tmp_path, monkeypatch, status, headers, body):
    c = capture(tmp_path, monkeypatch, lambda r: httpx.Response(status, headers=headers, stream=httpx.ByteStream(body)))
    try:
        with pytest.raises(x.panel.PanelError): c.get('portal', '/x.grib2', selection=x.ByteRange(0,1,'a'*64))
    finally: c.close()


def test_cas_tamper_and_write_once(tmp_path):
    d = tmp_path; (d/'objects').mkdir(); raw=b'one'; sha=hashlib.sha256(raw).hexdigest()
    p=d/'objects'/sha
    x.panel.write_once(p,raw); x.panel.write_once(p,raw)
    with pytest.raises(x.panel.PanelError): x.panel.write_once(p,b'two')
    p.write_bytes(b'bad')
    with pytest.raises(x.panel.PanelError): x.read_object(d,dict(sha256=sha,bytes=3))


def test_real_capture_reproducible(tmp_path):
    import os
    if os.environ.get('ALPHA_R09_EXTREMA_REAL') != '1':
        pytest.skip('Explicit local raw-evidence opt-in required')
    commit = os.environ['ALPHA_R09_EXTREMA_CODE_COMMIT']
    captured = os.environ['ALPHA_R09_EXTREMA_CAPTURE']
    output = x.ROOT/'private-evidence'/'r09-extrema'/('verify-'+Path(captured).name+'-'+commit[:12])
    a = x.build('config/v11/r09_multimodel_input_pins.json', captured, output/'a', commit)
    b = x.build('config/v11/r09_multimodel_input_pins.json', captured, output/'b', commit)
    assert a == b and a['cohort_station_days'] == 541
    assert a['split_counts'] == {'TRAIN':357,'DEVELOPMENT':120,'HISTORICAL_CONFIRMATION':64}
    assert a['ifs_boundaries_aligned'] == 73 and a['ifs_boundary_crossing'] == 468
    assert a['exact_day_admitted_station_days'] == 0 and a['learner_admitted'] is False
    for name in ('dataset.json','result.json','manifest.json'):
        assert (output/'a'/name).read_bytes() == (output/'b'/name).read_bytes()


def test_oversized_cas_rejected_before_read(tmp_path):
    (tmp_path/'objects').mkdir()
    sha='a'*64
    (tmp_path/'objects'/sha).write_bytes(b'x')
    with pytest.raises(x.panel.PanelError, match='OBJECT_BYTES_BOUND'):
        x.read_object(tmp_path, dict(sha256=sha, bytes=x.MAX_RAW_BYTES+1))


@pytest.mark.parametrize('value', [float('nan'), float('inf'), -10, True])
def test_window_rejects_corrupt_values(value):
    with pytest.raises(x.grib.EvidenceError):
        x.native_window((field(0, 3, value),), 0, 3)
