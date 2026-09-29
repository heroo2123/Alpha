"""ECMWF Open Data request/index/range adapter. No full-archive entitlement.

Current rolling retention is approximately 12 runs, not a historical backfill API.
Only exact 2t ensemble fields are selected; no full file or multi-range fallback.
"""
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import asyncio
import hashlib
import re

import httpx

from .evidence import EvidenceError, finite, sha
from .model_panel import (SourceIdentity, StationTarget, ForecastSlice, MemberObservation,
                          archive_raw, read_raw, strict_json, temperature, MAX_RAW_BYTES)

BASE = 'https://data.ecmwf.int/forecasts'
MAX_INDEX_BYTES = 3 * 1024 * 1024
MAX_INDEX_ROWS = 12000
MAX_FIELD_BYTES = MAX_RAW_BYTES
MAX_TOTAL_BYTES = 16 * 1024 * 1024


@dataclass(frozen=True)
class ECMWFRequest:
    source: SourceIdentity
    initialized_at: float
    step: int
    member: int
    # Explicit reviewed header pin for the claimed release; no "latest" alias.
    grib_signature_sha256: str

    def __post_init__(self):
        if not isinstance(self.source, SourceIdentity) or self.source.provider not in {'ECMWF_IFS_ENS', 'ECMWF_AIFS_ENS'}:
            raise EvidenceError('ECMWF_ENSEMBLE_SOURCE_REQUIRED')
        if self.source.dataset != 'ecmwf-open-data:0p25':
            raise EvidenceError('ECMWF_DATASET_IDENTITY')
        run = datetime.fromtimestamp(finite(self.initialized_at), timezone.utc)
        if run.hour not in (0, 6, 12, 18) or run.minute or run.second or self.initialized_at % 1:
            raise EvidenceError('ECMWF_INITIALIZATION_CYCLE')
        # Deliberately use the shared six-hour subset for IFS, too. This is not
        # a claim that intermediate IFS three-hour products do not exist.
        limit = 144 if self.source.model == 'ifs' and run.hour in (6, 18) else 360
        if type(self.step) is not int or not 0 <= self.step <= limit or self.step % 6:
            raise EvidenceError('ECMWF_STEP_BOUND')
        if type(self.member) is not int or not 0 <= self.member <= 50:
            raise EvidenceError('ECMWF_MEMBER_BOUND')
        sha(self.grib_signature_sha256)

    @property
    def selectors(self):
        run = datetime.fromtimestamp(self.initialized_at, timezone.utc)
        if self.source.model == 'aifs-ens':
            stream, kind = 'enfo', ('cf' if self.member == 0 else 'pf')
        elif self.member == 0:
            # Since IFS Cycle 50r1 the former ENS control is identical to HRES
            # and is published in oper/fc rather than enfo/cf.
            stream, kind = 'oper', 'fc'
        else:
            stream, kind = 'enfo', 'pf'
        return {'date': run.strftime('%Y%m%d'), 'time': run.strftime('%H%M'),
                'class': 'ai' if self.source.model == 'aifs-ens' else 'od',
                'stream': stream, 'type': kind, 'step': str(self.step),
                'levtype': 'sfc', 'param': '2t', 'number': str(self.member)}

    @property
    def url(self):
        run = datetime.fromtimestamp(self.initialized_at, timezone.utc)
        selectors = self.selectors
        if self.source.model == 'aifs-ens':
            file_kind = selectors['type']
        elif self.member == 0:
            file_kind = 'fc'
        else:
            # IFS perturbed members are multiplexed in one ensemble file even
            # though individual index rows remain type=pf.
            file_kind = 'ef'
        return (f'{BASE}/{run:%Y%m%d}/{run:%H}z/{self.source.model}/0p25/{selectors["stream"]}/'
                f'{run:%Y%m%d%H}0000-{self.step}h-{selectors["stream"]}-{file_kind}.grib2')

    @property
    def identity(self):
        return dict(adapter='alpha_v11_ecmwf_open_v2', source=asdict(self.source),
                    url=self.url, selectors=self.selectors, grib_signature_sha256=self.grib_signature_sha256)


def access_state(request, *, now, historical=False):
    if type(historical) is not bool or not isinstance(request, ECMWFRequest):
        raise EvidenceError('ECMWF_ACCESS_REQUEST_INVALID')
    if historical:
        return 'EXTERNAL_ACCESS_REQUIRED'
    age = finite(now) - request.initialized_at
    if age < 0 or age >= 72*3600:
        return 'NOT_AVAILABLE'
    return 'PUBLIC_PULL_ELIGIBLE'  # Eligibility does not assert the object exists.


@dataclass(frozen=True)
class ByteRange:
    start: int
    length: int
    index_sha256: str

    def __post_init__(self):
        if (type(self.start) is not int or type(self.length) is not int
                or not 0 <= self.start < 16*1024**3 or not 1 <= self.length <= MAX_FIELD_BYTES
                or self.start+self.length > 16*1024**3):
            raise EvidenceError('ECMWF_BYTE_RANGE_BOUND')
        sha(self.index_sha256)

    @property
    def header(self): return f'bytes={self.start}-{self.start+self.length-1}'


def plan_ranges(index, requests):
    if type(index) is not bytes or not 0 < len(index) <= MAX_INDEX_BYTES:
        raise EvidenceError('ECMWF_INDEX_BYTES_BOUND')
    if (type(requests) is not tuple or not 1 <= len(requests) <= 51
            or any(not isinstance(r, ECMWFRequest) for r in requests)
            or len({r.url for r in requests}) != 1 or len({r.member for r in requests}) != len(requests)):
        raise EvidenceError('ECMWF_REQUEST_BATCH_BOUND')
    lines = index.splitlines()
    if not 1 <= len(lines) <= MAX_INDEX_ROWS:
        raise EvidenceError('ECMWF_INDEX_ROWS_BOUND')
    found = {}; previous_end = 0; index_sha = hashlib.sha256(index).hexdigest()
    for line in lines:
        row = strict_json(line, MAX_INDEX_BYTES)
        if type(row) is not dict or '_offset' not in row or '_length' not in row:
            raise EvidenceError('ECMWF_INDEX_SCHEMA')
        # Validate every range, including unselected fields, for overlap/inflation.
        offset, length = row['_offset'], row['_length']
        if (type(offset) is not int or type(length) is not int or offset < previous_end
                or length <= 0 or offset+length > 16*1024**3):
            raise EvidenceError('ECMWF_INDEX_OFFSETS')
        previous_end = offset+length
        for req in requests:
            expected = req.selectors
            actual = {k: str(row.get(k, '')) for k in expected}
            # ECMWF indexes omit number for AIFS cf and IFS oper/fc controls.
            row_type = row.get('type')
            if type(row_type) is str and row_type in {'cf', 'fc'} and 'number' not in row:
                actual['number'] = '0'
            t = actual['time']
            if t in {'0', '6', '12', '18'}: actual['time'] = f'{int(t):02}00'
            if t in {'00', '06'}: actual['time'] = t+'00'
            if actual != expected: continue
            if req.member in found:
                raise EvidenceError('ECMWF_DUPLICATE_FIELD')
            found[req.member] = ByteRange(offset, length, index_sha)
    if len(found) != len(requests):
        raise EvidenceError('ECMWF_REQUESTED_FIELD_NOT_AVAILABLE')
    result = tuple(found[r.member] for r in requests)
    if sum(r.length for r in result) > MAX_TOTAL_BYTES:
        raise EvidenceError('ECMWF_TOTAL_BYTES_BOUND')
    return result


class ECMWFCollector:
    """Anonymous, sequential, streamed GETs with one hard deadline per field.

    Owns its client so ambient auth, cookies, redirects and proxy configuration
    cannot enter a provider request. Optional transport is for offline tests.
    """
    def __init__(self, store, *, transport=None):
        self.store, self.transport = store, transport
        self.evidence_class = 'PUBLIC_OBSERVED' if transport is None else 'SYNTHETIC'

    async def _get(self, client, url, maximum, *, byte_range=None):
        headers = {'Accept-Encoding': 'identity'}
        if byte_range: headers['Range'] = byte_range.header
        client.cookies.clear()
        async with client.stream('GET', url, headers=headers) as response:
            if response.status_code in (401, 403): raise EvidenceError('EXTERNAL_ACCESS_REQUIRED')
            if response.status_code in (404, 410): raise EvidenceError('NOT_AVAILABLE')
            if response.status_code != (206 if byte_range else 200):
                raise EvidenceError('ECMWF_HTTP_STATUS_OR_RANGE_IGNORED')
            if response.headers.get('content-encoding', 'identity') != 'identity':
                raise EvidenceError('ECMWF_COMPRESSED_TRANSPORT_REFUSED')
            if byte_range:
                match = re.fullmatch(r'bytes ([0-9]+)-([0-9]+)/([0-9]+)', response.headers.get('content-range', ''))
                if (not match or int(match[1]) != byte_range.start
                        or int(match[2]) != byte_range.start+byte_range.length-1
                        or not int(match[2]) < int(match[3]) <= 16*1024**3):
                    raise EvidenceError('ECMWF_CONTENT_RANGE_MISMATCH')
            declared = response.headers.get('content-length')
            if declared is not None and (not declared.isdigit() or int(declared) > maximum):
                raise EvidenceError('ECMWF_RESPONSE_BYTES_BOUND')
            data = bytearray()
            async for chunk in response.aiter_raw(chunk_size=65536):
                if len(data)+len(chunk) > maximum: raise EvidenceError('ECMWF_RESPONSE_BYTES_BOUND')
                data.extend(chunk)
            if byte_range and len(data) != byte_range.length:
                raise EvidenceError('ECMWF_TRUNCATED_RANGE')
            if declared is not None and int(declared) != len(data):
                raise EvidenceError('ECMWF_TRUNCATED_RESPONSE')
            return bytes(data)

    async def collect(self, request, target, record_id, *, historical=False):
        state = access_state(request, now=self.store.clock(), historical=historical)
        if state != 'PUBLIC_PULL_ELIGIBLE': return dict(state=state, raw_id=None, financial_authority=False)
        try:
            async with asyncio.timeout(30):
                async with httpx.AsyncClient(transport=self.transport, trust_env=False, follow_redirects=False, timeout=10.) as client:
                    index = await self._get(client, request.url[:-6]+'.index', MAX_INDEX_BYTES)
                    # Successful index survives a subsequent denied/failed field.
                    index_request = dict(request.identity, stage='INDEX')
                    archived_index = archive_raw(self.store, record_id+':index', source=request.source, target=target,
                        request=index_request, raw=index, initialized_at=request.initialized_at, evidence_class=self.evidence_class)
                    selection, = plan_ranges(index, (request,))
                    raw = await self._get(client, request.url, selection.length, byte_range=selection)
                    archived = archive_raw(self.store, record_id, source=request.source, target=target,
                        request=dict(request.identity, selection=asdict(selection), index_id=archived_index['id'],
                                     index_evidence_sha256=archived_index['sha256']),
                        raw=raw, initialized_at=request.initialized_at, evidence_class=self.evidence_class)
            return dict(state='CAPTURED', raw_id=archived['id'], financial_authority=False)
        except (EvidenceError, httpx.HTTPError, TimeoutError) as exc:
            reason = str(exc) if isinstance(exc, EvidenceError) else 'ECMWF_TRANSPORT_UNAVAILABLE'
            return dict(state=reason if reason in {'EXTERNAL_ACCESS_REQUIRED', 'NOT_AVAILABLE'} else 'UNAVAILABLE',
                        reason=reason, raw_id=None, financial_authority=False)


def normalize_ecmwf(store, raw_id, *, request, target, index_id):
    index_row, index = read_raw(store, index_id, source=request.source, target=target,
                               request=dict(request.identity, stage='INDEX'))
    selection, = plan_ranges(index, (request,))
    row, raw = read_raw(store, raw_id, source=request.source, target=target,
        request=dict(request.identity, selection=asdict(selection), index_id=index_id,
                     index_evidence_sha256=index_row['sha256']))
    if row['body']['issued_at'] != request.initialized_at or index_row['body']['issued_at'] != request.initialized_at:
        raise EvidenceError('ECMWF_ARCHIVE_RUN_MISMATCH')
    if len(raw) != selection.length:
        raise EvidenceError('ECMWF_ARCHIVED_RANGE_LENGTH')
    if index_row['body']['available_at'] > row['body']['received_at']:
        raise EvidenceError('ECMWF_INDEX_AFTER_FIELD')
    from .ecmwf_grib import decode_station
    point = decode_station(raw, request=request, target=target)
    b = row['body']; p = b['payload']
    evidence_class = b['evidence_class']
    if index_row['body']['evidence_class'] != evidence_class:
        raise EvidenceError('ECMWF_MIXED_EVIDENCE_CLASS')
    return ForecastSlice(request.source, target, request.initialized_at, request.initialized_at+request.step*3600,
        b['received_at'], b['available_at'], b['published_at'], evidence_class,
        point['latitude'], point['longitude'], point['grid_sha256'], 'K',
        (MemberObservation(request.member, temperature(point['kelvin'], 'K', target.output_unit)),), (),
        row['id'], row['sha256'], p['response_sha256'])
