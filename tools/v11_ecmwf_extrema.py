#!/usr/bin/env python3
"""Bounded offline native-extreme research. No learner or runtime registration.

A complete native statistical window is NOT automatically the contract's
half-open local day. End-point membership and causal availability remain gates.
"""
from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
import hashlib
from pathlib import Path
import platform
import re
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools import v11_multimodel_panel as panel
from polymarket_scanner.v11 import ecmwf_grib as grib
from polymarket_scanner.v11.ecmwf_sources import ByteRange, MAX_INDEX_BYTES, MAX_INDEX_ROWS
from polymarket_scanner.v11.model_panel import MAX_RAW_BYTES, temperature
from polymarket_scanner.v11.pws_quality import geometry

VERSION = 'alpha_v11_ecmwf_native_extrema_research_v1'
BASES = {'portal': 'https://data.ecmwf.int/forecasts',
         'aws': 'https://ecmwf-forecasts.s3.eu-central-1.amazonaws.com'}
PARAMS = {'mx2t3': (228026, 'max', 3), 'mn2t3': (228027, 'min', 3),
          'mx2t6': (121, 'max', 6), 'mn2t6': (122, 'min', 6)}
CANDIDATES = set(PARAMS) | {'mx2t', 'mn2t', '201', '202', '121', '122', '228026', '228027'}
FLAGS = dict(panel.FLAGS, operational_release_reviewed=False,
             historical_availability='UNKNOWN', label_knowable_time='UNKNOWN',
             rule_revision_lineage='UNKNOWN', release_binding='OBSERVED_HEADER_ONLY',
             learner_admitted=False, exact_day_extrema_c=None)
require = panel.require


@dataclass(frozen=True)
class Product:
    model: str
    run: str
    step: int
    kind: str

    def __post_init__(self):
        dt = datetime.fromisoformat(self.run)
        require(dt.tzinfo is not None and dt.utcoffset() == timedelta(0)
                and dt.hour in (0, 6, 12, 18) and not (dt.minute or dt.second or dt.microsecond), 'RUN_CYCLE')
        require(self.model in ('ifs', 'aifs-ens'), 'MODEL')
        require(self.kind in (('fc', 'ef') if self.model == 'ifs' else ('cf', 'pf')), 'LAYOUT')
        limit = 144 if self.model == 'ifs' and dt.hour in (6, 18) else 360
        require(type(self.step) is int, 'STEP')
        cadence = 3 if self.model == 'ifs' and self.step <= 144 else 6
        require(type(self.step) is int and 0 <= self.step <= limit and self.step % cadence == 0, 'STEP')

    @property
    def stream(self):
        return 'oper' if self.kind == 'fc' else 'enfo'

    @property
    def path(self):
        dt = datetime.fromisoformat(self.run)
        return (f'/{dt:%Y%m%d}/{dt:%H}z/{self.model}/0p25/{self.stream}/'
                f'{dt:%Y%m%d%H}0000-{self.step}h-{self.stream}-{self.kind}')

    @property
    def key(self):
        return self.path


@dataclass(frozen=True)
class IntervalRequest:
    product: Product
    param: str
    member: int

    def __post_init__(self):
        require(type(self.product) is Product, 'PRODUCT_TYPE')
        self.product.__post_init__()
        require(self.product.model == 'ifs' and self.param in PARAMS, 'NATIVE_IFS_EXTREME_REQUIRED')
        require(type(self.member) is int and 0 <= self.member <= 50, 'MEMBER')
        require((self.member == 0) == (self.product.kind == 'fc'), 'MEMBER_LAYOUT')
        width = PARAMS[self.param][2]
        require(self.product.step >= width and width == (3 if self.product.step <= 144 else 6), 'INTERVAL_CADENCE')


def index_rows(raw, product):
    """Validate the whole index, including unselected ranges and identity."""
    require(type(raw) is bytes and 0 < len(raw) <= MAX_INDEX_BYTES, 'INDEX_BYTES')
    lines = raw.splitlines()
    require(0 < len(lines) <= MAX_INDEX_ROWS, 'INDEX_ROWS')
    dt = datetime.fromisoformat(product.run)
    expected = dict(date=f'{dt:%Y%m%d}', time=f'{dt:%H%M}',
                    **{'class': 'od' if product.model == 'ifs' else 'ai'},
                    stream=product.stream, type='pf' if product.kind == 'ef' else product.kind,
                    step=str(product.step), expver='0001', domain='g')
    result = []; previous = 0; seen = set()
    for line in lines:
        row = panel.strict_json(line)
        require(type(row) is dict, 'INDEX_SCHEMA')
        for key in (*expected, 'param', 'levtype'):
            require(type(row.get(key)) is str, 'INDEX_SELECTOR_TYPE')
        require(all(row[k] == v for k, v in expected.items()), 'INDEX_PRODUCT_IDENTITY')
        a, n = row.get('_offset'), row.get('_length')
        require(type(a) is int and type(n) is int and a >= previous and n > 0
                and a+n <= 16*1024**3, 'INDEX_OFFSETS')
        previous = a+n
        member = row.get('number', '0')
        require(type(member) is str and member.isdigit() and str(int(member)) == member, 'INDEX_MEMBER')
        require(int(member) in (range(1, 51) if product.kind in ('ef', 'pf') else (0,)), 'INDEX_MEMBER')
        identity = tuple((k, str(v)) for k, v in sorted(row.items()) if k not in ('_offset', '_length'))
        require(identity not in seen, 'DUPLICATE_INDEX_FIELD'); seen.add(identity)
        result.append(row)
    return result


def select(raw, request):
    rows = index_rows(raw, request.product)
    selected = [r for r in rows if r['param'] == request.param and r['levtype'] == 'sfc'
                and r.get('number', '0') == str(request.member)]
    require(len(selected) == 1, 'NATIVE_FIELD_NOT_UNIQUE_OR_ABSENT')
    row = selected[0]
    return ByteRange(row['_offset'], row['_length'], hashlib.sha256(raw).hexdigest())


@dataclass(frozen=True)
class NativeField:
    request: IntervalRequest
    start_utc: float
    end_utc: float
    raw_sha256: str
    index_sha256: str
    metadata: dict
    points: tuple[dict, ...]
    endpoint_membership: str = 'NOT_ATTESTED_BY_GRIB'


META_KEYS = ('shortName', 'paramId', 'units', 'edition', 'centre', 'class', 'stream', 'type',
             'dataDate', 'dataTime', 'stepType', 'startStep', 'endStep', 'stepRange',
             'productDefinitionTemplateNumber', 'typeOfStatisticalProcessing', 'typeOfTimeIncrement',
             'numberOfTimeRange', 'lengthOfTimeRange', 'indicatorOfUnitForTimeRange',
             'indicatorOfUnitForTimeIncrement', 'timeIncrement', 'numberOfMissingInStatisticalProcess',
             'generatingProcessIdentifier', 'tablesVersion', 'localTablesVersion', 'packingType')


def decode(raw, index, request, targets):
    """Decode actual native products only, never a relabelled point field.

    Narrow observed Cycle-50r1 global CCSDS profile. Unknown metadata fails closed;
    its observed process ID is not an independently reviewed release identity.
    """
    request.__post_init__()
    selection = select(index, request)
    require(len(raw) == selection.length, 'RANGE_LENGTH')
    s = grib.sections(raw)
    require(type(targets) is tuple and 1 <= len(targets) <= 15, 'TARGET_BOUND')
    for target in targets:
        require(type(target) is grib.HistoricalPointTarget, 'TARGET_TYPE')
        target.__post_init__()
    p, grid, ident = s[4], s[3], s[1]
    control = request.member == 0
    require(len(p) == (58 if control else 61) and len(ident) == 21, 'PRODUCT_LENGTH')
    require(ident[20] == (1 if control else 4), 'PROCESSED_DATA_TYPE')
    require(datetime(int.from_bytes(ident[12:14], 'big'), *ident[14:19], tzinfo=timezone.utc)
            == datetime.fromisoformat(request.product.run), 'REFERENCE_TIME')
    require(p[5:7] == b'\0\0' and p[9:11] == b'\0\0' and p[17] == 1
            and p[22:24] == bytes([103, 0]) and int.from_bytes(p[24:28], 'big') == 2
            and p[28:34] == b'\xff'*6, 'TWO_METRE_TEMPERATURE')
    require(len(grid) == 72 and grid[5] == 0 and grid[10:14] == b'\0'*4
            and grid[14] in (6, 8) and grid[38:42] == b'\0'*4
            and grid[42:46] == b'\xff'*4 and grid[71] == 0, 'GRID_PROFILE')
    require(s[6] == b'\0\0\0\x06\x06\xff' and len(s[5]) == 25, 'PACKING_PROFILE')
    try:
        import eccodes as ec
    except Exception:
        raise panel.PanelError('ECCODES_UNAVAILABLE') from None
    h = None
    try:
        h = ec.codes_new_from_message(raw)
        require(h is not None, 'GRIB_HANDLE')
        get = lambda k: ec.codes_get(h, k)
        meta = {k: get(k) for k in META_KEYS}
        dt = datetime.fromisoformat(request.product.run)
        pid, stat, width = PARAMS[request.param]
        expected = dict(shortName=request.param, paramId=pid, units='K', edition=2, centre='ecmf',
                        **{'class': 'od'}, stream=request.product.stream, type='fc' if control else 'pf',
                        dataDate=int(f'{dt:%Y%m%d}'), dataTime=int(f'{dt:%H%M}'), stepType=stat,
                        startStep=request.product.step-width, endStep=request.product.step,
                        stepRange=f'{request.product.step-width}-{request.product.step}',
                        productDefinitionTemplateNumber=8 if control else 11,
                        typeOfStatisticalProcessing=2 if stat == 'max' else 3, typeOfTimeIncrement=2,
                        numberOfTimeRange=1, lengthOfTimeRange=width, indicatorOfUnitForTimeRange=1,
                        indicatorOfUnitForTimeIncrement=13, timeIncrement=450,
                        numberOfMissingInStatisticalProcess=0, generatingProcessIdentifier=161,
                        tablesVersion=32, localTablesVersion=0, packingType='grid_ccsds')
        require(meta == expected, 'NATIVE_METADATA_PROFILE')
        require(get('productionStatusOfProcessedData') == 0 and get('significanceOfReferenceTime') == 1,
                'OPERATIONAL_FORECAST')
        require(get('typeOfGeneratingProcess') == (2 if control else 4), 'GENERATING_PROCESS')
        if not control:
            require(get('perturbationNumber') == request.member and get('numberOfForecastsInEnsemble') == 51
                    and get('typeOfEnsembleForecast') == 255, 'ENSEMBLE_IDENTITY')
        end = dt + timedelta(hours=request.product.step)
        end_keys = ('year', 'month', 'day', 'hour', 'minute', 'second')
        require(tuple(get(k+'OfEndOfOverallTimeInterval') for k in end_keys)
                == tuple(getattr(end, k) for k in end_keys), 'INTERVAL_END_TIME')
        require(get('Ni') == 1440 and get('Nj') == 721 and get('numberOfDataPoints') == 1440*721
                and get('numberOfValues') == 1440*721 and get('bitmapPresent') == 0
                and get('iDirectionIncrementInDegrees') == .25 and get('jDirectionIncrementInDegrees') == .25
                and get('latitudeOfFirstGridPointInDegrees') == 90
                and get('latitudeOfLastGridPointInDegrees') == -90, 'GRID_DIMENSIONS')
        lon = get('longitudeOfFirstGridPointInDegrees')
        require(lon in (0, 180) and abs((lon+1439*.25)%360-get('longitudeOfLastGridPointInDegrees')) < 1e-6,
                'GRID_LONGITUDE')
        points = []; indices = []
        for target in targets:
            j0 = round((90-target.latitude)/.25)
            i0 = round(((target.longitude-lon)%360)/.25)
            candidates = {((i0+i)%1440, j) for i in (-1, 0, 1)
                          for j in (j0-1, j0, j0+1) if 0 <= j <= 720}
            position = lambda ij: (90-ij[1]*.25, (lon+ij[0]*.25+180)%360-180)
            ij = min(candidates, key=lambda ij: (geometry(target.latitude, target.longitude, *position(ij))[0], position(ij)))
            lat, lng = position(ij)
            distance = geometry(target.latitude, target.longitude, lat, lng)[0]
            require(distance <= target.maximum_grid_distance_km, 'GRID_DISTANCE')
            indices.append(ij[1]*1440+ij[0]); points.append(dict(latitude=lat, longitude=lng, distance_km=distance))
        # Same bounded full decode/re-encode defence as the reviewed point path.
        values = grib._ccsds_values(raw, tuple(indices), 1440*721, 1440, 721)
        for point, value in zip(points, values):
            point['kelvin'] = temperature(value, 'K', 'K')
            point['grid_sha256'] = hashlib.sha256(grid).hexdigest()
        meta['observed_header_sha256'] = grib.release_signature(raw)
        return NativeField(request, (end-timedelta(hours=width)).timestamp(), end.timestamp(),
                           hashlib.sha256(raw).hexdigest(), selection.index_sha256, meta, tuple(points))
    except panel.PanelError:
        raise
    except Exception as exc:
        raise panel.PanelError('NATIVE_DECODE_FAILED') from exc
    finally:
        if h is not None:
            ec.codes_release(h)


def native_window(fields, start, end):
    """Combine exact adjoining native intervals; never split a crossing interval.

    Output is a source-native window diagnostic, not a half-open settlement-day
    HIGH/LOW. There is intentionally no endpoint override or learner adapter.
    """
    require(type(fields) is tuple and 1 <= len(fields) <= 16, 'WINDOW_BOUND')
    require(all(type(f) is NativeField for f in fields), 'WINDOW_FIELD_TYPE')
    for f in fields:
        f.request.__post_init__()
        require(panel.is_sha(f.raw_sha256) and panel.is_sha(f.index_sha256)
                and panel.is_sha(f.metadata.get('observed_header_sha256')), 'WINDOW_PROVENANCE')
        require(type(f.start_utc) in (float, int) and type(f.end_utc) in (float, int)
                and f.start_utc < f.end_utc and 1 <= len(f.points) <= 15, 'WINDOW_FIELD')
        for point in f.points:
            temperature(point['kelvin'], 'K', 'K')
    first = fields[0]
    signature = lambda f: (f.request.product.model, f.request.product.run, f.request.member,
                           PARAMS[f.request.param][1], f.metadata['observed_header_sha256'],
                           tuple((p['latitude'], p['longitude'], p['grid_sha256']) for p in f.points))
    require(all(signature(f) == signature(first) for f in fields), 'WINDOW_IDENTITY')
    ordered = sorted(fields, key=lambda f: f.start_utc)
    cursor = start
    for f in ordered:
        require(f.start_utc == cursor and cursor < f.end_utc <= end, 'WINDOW_GAP_OVERLAP_OR_CROSSING')
        cursor = f.end_utc
    require(cursor == end, 'WINDOW_INCOMPLETE')
    statistic = PARAMS[first.request.param][1]
    reduce = max if statistic == 'max' else min
    return dict(native_window_extrema_k=[reduce(f.points[i]['kelvin'] for f in ordered)
                                         for i in range(len(first.points))],
                statistic=statistic, start_utc=start, end_utc=end,
                raw_sha256s=[f.raw_sha256 for f in ordered],
                endpoint_membership='NOT_ATTESTED_BY_GRIB', **FLAGS)


def private_dir(path):
    path = Path(path).resolve()
    require(path.is_relative_to(ROOT/'private-evidence'), 'PRIVATE_OUTPUT_REQUIRED')
    path.mkdir(parents=True, exist_ok=True)
    return path


class Capture:
    """Anonymous immutable responses. Sequential, no retries, stop origin on 429/503."""
    def __init__(self, directory, *, transport=None):
        import httpx
        self.directory = private_dir(directory)
        self.objects = self.directory/'objects'; self.objects.mkdir(exist_ok=True)
        self.client = httpx.Client(trust_env=False, follow_redirects=False, timeout=15, transport=transport)
        self.synthetic = transport is not None
        self.blocked = set(); self.requests = 0; self.bytes = 0

    def get(self, source, path, *, selection=None):
        require(source in BASES and path.startswith('/') and '..' not in path and '?' not in path, 'PUBLIC_URL')
        require(self.requests < 100 and self.bytes < 128*1024**2, 'CAPTURE_BUDGET')
        if source in self.blocked:
            return dict(source=source, path=path, status='NOT_ATTEMPTED_ORIGIN_THROTTLED')
        self.requests += 1
        headers = {'Accept-Encoding': 'identity', 'Accept': 'application/json'}
        if selection: headers['Range'] = selection.header
        maximum = selection.length if selection else MAX_INDEX_BYTES
        require(self.bytes+maximum <= 128*1024**2, 'CAPTURE_BYTE_BUDGET')
        url = BASES[source]+path
        started = time.monotonic()
        self.client.cookies.clear()
        with self.client.stream('GET', url, headers=headers) as response:
            status = response.status_code
            if status in (429, 503): self.blocked.add(source)
            require(response.headers.get('content-encoding', 'identity') == 'identity', 'HTTP_ENCODING')
            if selection and status == 206:
                match = re.fullmatch(r'bytes (\d+)-(\d+)/(\d+)', response.headers.get('content-range', ''))
                require(match is not None and int(match[1]) == selection.start
                        and int(match[2]) == selection.start+selection.length-1
                        and int(match[2]) < int(match[3]) <= 16*1024**3, 'HTTP_RANGE')
            require(not selection or status != 200, 'HTTP_RANGE_IGNORED')
            raw = bytearray()
            for chunk in response.iter_raw():
                require(time.monotonic()-started < 30, 'HTTP_DEADLINE')
                raw.extend(chunk)
                require(len(raw) <= maximum, 'HTTP_BODY_BOUND')
            raw = bytes(raw); self.bytes += len(raw)
            if selection and status == 206: require(len(raw) == selection.length, 'HTTP_RANGE_LENGTH')
            sha = hashlib.sha256(raw).hexdigest()
            panel.write_once(self.objects/sha, raw)
            record = dict(source=source, path=path, status=status, bytes=len(raw), sha256=sha,
                          received_at=datetime.now(timezone.utc).isoformat(),
                          evidence_class='SYNTHETIC' if self.synthetic else 'PUBLIC_OBSERVED',
                          headers={k: response.headers[k] for k in ('date', 'last-modified', 'etag', 'content-range', 'retry-after')
                                   if k in response.headers})
            receipts = self.directory/'receipts'; receipts.mkdir(exist_ok=True)
            panel.write_once(receipts/f'{self.requests:04d}.json', panel.canonical(record)+b'\n')
            return record

    def close(self):
        self.client.close()


def load_plan(pins_path):
    pins = panel.strict_json(Path(pins_path).read_bytes())
    plan = panel.artifact(pins['inputs']['plan']['path'], pins['inputs']['plan'])
    require(plan['financial_authority'] is False and plan['promotion_authority'] is False
            and plan['untouched_forward_holdout_claim'] is False, 'PLAN_AUTHORITY')
    ids = set()
    for row in plan['station_days']:
        panel.day_context(row)
        key = row['station'], row['target_date']
        require(key not in ids, 'DUPLICATE_STATION_DAY'); ids.add(key)
        require({k: row[k] for k in plan['stations'][row['station']]} == plan['stations'][row['station']], 'STATION_IDENTITY')
    require(len(ids) == 541 and Counter(r['split'] for r in plan['station_days'])
            == Counter(TRAIN=357, DEVELOPMENT=120, HISTORICAL_CONFIRMATION=64), 'FROZEN_COHORT')
    return pins, plan


def read_object(directory, record):
    require(panel.is_sha(record['sha256']), 'OBJECT_HASH_SHAPE')
    path = Path(directory)/'objects'/record['sha256']
    require(type(record['bytes']) is int and 0 <= record['bytes'] <= MAX_RAW_BYTES
            and path.stat().st_size == record['bytes'], 'OBJECT_BYTES_BOUND')
    raw = path.read_bytes()
    require(len(raw) == record['bytes'] and hashlib.sha256(raw).hexdigest() == record['sha256'], 'OBJECT_HASH')
    return raw


def collect(pins_path, output, *, probe_only=False, code_commit):
    """Feasibility capture, NOT a full field backfill. Complete retained IFS indexes
    for aligned frozen days; representative raw ranges only. No exact-day claim.
    """
    pins, plan = load_plan(pins_path)
    collector_identity = code_identity(code_commit)
    output = private_dir(output)
    require(not (output/'capture.json').exists(), 'CAPTURE_ALREADY_SEALED')
    capture = Capture(output)
    records = []; field_records = []
    try:
        listing = capture.get('portal', '/')
        require(listing['status'] == 200, 'PORTAL_LISTING_UNAVAILABLE')
        listed = panel.strict_json(read_object(output, listing))
        dates = {r['name'] for r in listed if r.get('directory') is True and re.fullmatch(r'\d{8}', r.get('name', ''))}
        products = {}
        # AWS history is probed once. Throttling is not field absence.
        earliest = min(plan['station_days'], key=lambda r: r['run_utc'])
        aws = Product('ifs', earliest['run_utc'], 3, 'fc')
        if not probe_only:
            records.append(dict(product=asdict(aws), response=capture.get('aws', aws.path+'.index')))
        for row in plan['station_days']:
            ctx = panel.day_context(row); a, b = ctx['start_forecast_hour'], ctx['end_forecast_hour']
            if probe_only or a % 3 or b % 3 or row['run_date'].replace('-', '') not in dates: continue
            for step in range(int(a)+3, int(b)+1, 3):
                for kind in ('fc', 'ef'):
                    p = Product('ifs', row['run_utc'], step, kind); products[p.key] = p
        # Current metadata and negative AIFS parameter probes, including longer steps.
        reference = '2026-09-29T00:00:00+00:00'
        for step in ((3, 150) if probe_only else (0, 3, 6, 144, 150)):
            for kind in ('fc', 'ef'):
                p = Product('ifs', reference, step, kind); products[p.key] = p
        for run in (() if probe_only else (reference, '2026-09-27T00:00:00+00:00', '2026-09-28T00:00:00+00:00')):
            for step in (6, 24):
                for kind in ('cf', 'pf'):
                    p = Product('aifs-ens', run, step, kind); products[p.key] = p
        # Explicit historical 404 probe on the rolling portal.
        old = Product('ifs', earliest['run_utc'], 3, 'fc')
        if not probe_only: products[old.key] = old
        for p in sorted(products.values(), key=lambda p: p.key):
            time.sleep(5)
            response = capture.get('portal', p.path+'.index')
            records.append(dict(product=asdict(p), response=response))
            if response['status'] != 200: continue
            raw = read_object(output, response); rows = index_rows(raw, p)
            # Verify both native cadences/control and member extremes on actual bytes.
            if p.model != 'ifs' or p.run != reference or p.step not in (3, 150): continue
            suffix = '3' if p.step == 3 else '6'
            for member in ((0,) if p.kind == 'fc' else (1, 50)):
                for param in ('mx2t'+suffix, 'mn2t'+suffix):
                    req = IntervalRequest(p, param, member); selection = select(raw, req)
                    time.sleep(5)
                    response_field = capture.get('portal', p.path+'.grib2', selection=selection)
                    field_records.append(dict(request=asdict(req), index_sha256=response['sha256'],
                                              response=response_field))
        manifest = dict(version=VERSION, collection_code=collector_identity, input_pin_sha256=panel.digest(pins), listing=listing,
                        indexes=records, fields=field_records, requests=capture.requests, bytes=capture.bytes,
                        scope='FEASIBILITY_INDEX_AUDIT_AND_REPRESENTATIVE_FIELDS_NOT_FULL_BACKFILL', **FLAGS)
        panel.write_once(output/'capture.json', panel.canonical(manifest)+b'\n')
    finally:
        capture.close()


def code_identity(code_commit):
    code_files = ('tools/v11_ecmwf_extrema.py', 'tools/v11_multimodel_panel.py',
                  'polymarket_scanner/v11/ecmwf_grib.py', 'polymarket_scanner/v11/ecmwf_sources.py',
                  'polymarket_scanner/v11/model_panel.py', 'polymarket_scanner/v11/grib_fields.py',
                  'polymarket_scanner/v11/evidence.py', 'polymarket_scanner/v11/pws_quality.py')
    code_hashes = {}
    for name in code_files:
        raw = subprocess.check_output(['git', 'show', f'{code_commit}:{name}'], cwd=ROOT)
        require(raw == (ROOT/name).read_bytes(), 'CODE_COMMIT_BYTES_MISMATCH')
        code_hashes[name] = hashlib.sha256(raw).hexdigest()
    commit = subprocess.check_output(['git', 'rev-parse', code_commit+'^{commit}'], cwd=ROOT, text=True).strip()
    tree = subprocess.check_output(['git', 'rev-parse', commit+'^{tree}'], cwd=ROOT, text=True).strip()
    return dict(code_commit=commit, code_tree=tree, code_file_sha256s=code_hashes)


def build(pins_path, capture_dir, output, code_commit):
    pins, plan = load_plan(pins_path)
    capture_dir = Path(capture_dir)
    require((capture_dir/'capture.json').stat().st_size <= 1024**2, 'CAPTURE_MANIFEST_BOUND')
    capture_raw = (capture_dir/'capture.json').read_bytes()
    captured = panel.strict_json(capture_raw)
    require(captured['version'] == VERSION and captured['input_pin_sha256'] == panel.digest(pins), 'CAPTURE_IDENTITY')
    require(len(captured['indexes']) <= 100 and len(captured['fields']) <= 100, 'CAPTURE_RECORD_BOUND')
    identity = code_identity(code_commit)
    indexes = {}; summaries = []
    read_object(capture_dir, captured['listing'])
    for entry in captured['indexes']:
        p = Product(**entry['product']); record = entry['response']
        raw = read_object(capture_dir, record) if type(record['status']) is int else b''
        require(type(record['status']) is int or record['status'] == 'NOT_ATTEMPTED_ORIGIN_THROTTLED', 'CAPTURE_STATUS')
        require(record['path'] == p.path+'.index' and record['source'] in BASES, 'CAPTURE_PRODUCT_PATH')
        rows = index_rows(raw, p) if record['status'] == 200 else []
        if rows:
            require(record['evidence_class'] == 'PUBLIC_OBSERVED', 'PUBLIC_INDEX_EVIDENCE_REQUIRED')
            key = record['source'], p.key
            require(key not in indexes, 'DUPLICATE_PRODUCT_CAPTURE'); indexes[key] = rows
        summaries.append(dict(product=asdict(p), response=record, rows=len(rows),
                              parameters=sorted({r['param'] for r in rows}),
                              native_members={param: sorted(int(r.get('number', '0')) for r in rows
                                               if r['param'] == param and r['levtype'] == 'sfc')
                                              for param in sorted(CANDIDATES & {r['param'] for r in rows})}))
    decoded = []
    targets = tuple(grib.HistoricalPointTarget(plan['stations'][name]['latitude'], plan['stations'][name]['longitude'])
                    for name in sorted(plan['stations']))
    for entry in captured['fields']:
        req = IntervalRequest(Product(**entry['request']['product']), entry['request']['param'], entry['request']['member'])
        response = entry['response']; raw = read_object(capture_dir, response)
        require(response['path'] == req.product.path+'.grib2' and response['status'] == 206
                and response['evidence_class'] == 'PUBLIC_OBSERVED', 'FIELD_RESPONSE')
        parent = [e for e in captured['indexes'] if e['response']['sha256'] == entry['index_sha256']
                  and e['product'] == asdict(req.product) and e['response']['source'] == response['source']]
        require(len(parent) == 1 and parent[0]['response']['status'] == 200, 'FIELD_INDEX_LINK')
        index = read_object(capture_dir, parent[0]['response'])
        selected = select(index, req)
        match = re.fullmatch(r'bytes (\d+)-(\d+)/(\d+)', response['headers'].get('content-range', ''))
        require(match is not None and int(match[1]) == selected.start
                and int(match[2]) == selected.start+selected.length-1
                and int(match[2]) < int(match[3]) <= 16*1024**3, 'ARCHIVED_RANGE')
        field = decode(raw, index, req, targets)
        decoded.append(asdict(field))
    aifs_indexes = [r for (source, key), r in indexes.items() if '/aifs-ens/' in key]
    aifs_status = ('NOT_INSPECTED_THIS_CAPTURE' if not aifs_indexes else
                   'NATIVE_CANDIDATE_REQUIRES_REVIEW' if any(r['param'] in CANDIDATES for rows in aifs_indexes for r in rows) else
                   'NO_NATIVE_EXTREMA_IN_INSPECTED_PRODUCTS_HISTORY_NOT_EXHAUSTIVELY_SCANNED')
    days = []
    for row in plan['station_days']:
        ctx = panel.day_context(row); a, b = ctx['start_forecast_hour'], ctx['end_forecast_hour']
        aligned = a % 3 == 0 and b % 3 == 0
        required = list(range(int(a)+3, int(b)+1, 3)) if aligned else []
        indexed = 0
        if aligned:
            for step in required:
                for kind in ('fc', 'ef'):
                    p = Product('ifs', row['run_utc'], step, kind)
                    # Never combine sources to manufacture a member-complete file.
                    rows = indexes.get(('portal', p.key), indexes.get(('aws', p.key), []))
                    for param in ('mx2t3', 'mn2t3'):
                        members = Counter(int(r.get('number', '0')) for r in rows if r['param'] == param and r['levtype'] == 'sfc')
                        indexed += sum(count == 1 for count in members.values())
        expected = len(required)*102
        days.append(dict(station_day=row['station']+'|'+row['target_date'], context=row, local_day=ctx,
                         ifs_three_hour_boundaries_aligned=aligned, ifs_required_interval_end_hours=required,
                         ifs_indexed_native_fields=indexed, ifs_required_native_fields=expected if aligned else None,
                         ifs_all_members_intervals_indexed=bool(aligned and indexed == expected),
                         native_field_backfill_complete=False, exact_day_extrema_c=None,
                         status='GATED_BOUNDARY_CROSSING' if not aligned else 'GATED_RAW_COVERAGE_AND_ENDPOINT_SEMANTICS',
                         aifs_status=aifs_status))
    import eccodes
    result = dict(version=VERSION, cohort_station_days=len(days),
                  split_counts=dict(Counter(d['context']['split'] for d in days)),
                  ifs_boundaries_aligned=sum(d['ifs_three_hour_boundaries_aligned'] for d in days),
                  ifs_boundary_crossing=sum(not d['ifs_three_hour_boundaries_aligned'] for d in days),
                  ifs_complete_indexed_station_days=sum(d['ifs_all_members_intervals_indexed'] for d in days),
                  aifs_index_products_inspected=len(aifs_indexes), aifs_status=aifs_status,
                  raw_complete_station_days=0, exact_day_admitted_station_days=0,
                  decoded_representative_fields=len(decoded),
                  decoded_fields_in_frozen_cohort=sum(f['request']['product']['run'] in
                      {d['run_utc'] for d in plan['station_days']} for f in decoded),
                  fit_status='NOT_FITTED', calibration_status='NOT_CALIBRATED', promotion_status='NO_PROMOTION',
                  weathernext_status='DEFERRED_NO_ACCESS', confirmation=panel.CONFIRMATION, **FLAGS)
    dataset = dict(result=result, station_days=days, index_evidence=summaries, decoded_fields=decoded,
                   decoded_station_order=sorted(plan['stations']))
    raw = panel.canonical(dataset)+b'\n'
    manifest = dict(version=VERSION, **identity,
                    collection_code=captured.get('collection_code', 'UNCOMMITTED_EXPLORATORY_CAPTURE'),
                    capture_sha256=hashlib.sha256(capture_raw).hexdigest(), input_pin_sha256=panel.digest(pins),
                    plan_file_sha256=pins['inputs']['plan']['file_sha256'], dataset_sha256=hashlib.sha256(raw).hexdigest(),
                    runtime=dict(python=platform.python_version(), eccodes=eccodes.codes_get_api_version()),
                    timezone_sha256s={z: panel.file_sha(Path('/usr/share/zoneinfo')/z)
                                     for z in sorted({d['timezone'] for d in plan['station_days']})}, **FLAGS)
    require((capture_dir/'capture.json').read_bytes() == capture_raw, 'CAPTURE_CHANGED')
    load_plan(pins_path)  # Recheck source bytes after assembly; no SQLite is opened.
    output = private_dir(output)
    for name, data in (('dataset.json', raw), ('result.json', panel.canonical(result)+b'\n'),
                       ('manifest.json', panel.canonical(manifest)+b'\n')):
        panel.write_once(output/name, data)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=('collect', 'build'))
    parser.add_argument('--pins', default='config/v11/r09_multimodel_input_pins.json')
    parser.add_argument('--output', required=True)
    parser.add_argument('--capture')
    parser.add_argument('--code-commit')
    parser.add_argument('--probe-only', action='store_true', help='Only four current IFS indexes and twelve native ranges; no AWS retry')
    args = parser.parse_args()
    if args.mode == 'collect':
        require(args.code_commit, 'COLLECTION_COMMIT_REQUIRED')
        collect(args.pins, args.output, probe_only=args.probe_only, code_commit=args.code_commit)
    else:
        require(args.capture and args.code_commit, 'BUILD_CAPTURE_AND_COMMIT_REQUIRED')
        print(panel.canonical(build(args.pins, args.capture, args.output, args.code_commit)).decode())


if __name__ == '__main__':
    main()
