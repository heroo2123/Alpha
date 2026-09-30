"""Offline Gate 3 I/O interfaces. No socket, URL opener, or launch entrypoint.

These components consume synthetic response bytes. A future network adapter,
source-specific GRIB identity pins, and exact-manifest launch review are separate
work. Nothing in this module confers acquisition or learner authority.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import ipaddress
import math
import os
from pathlib import Path
import secrets
import stat
import struct
from polymarket_scanner.v11.ecmwf_grib import sections
from tools.v11_r09_gate3_launch import (DurableBudget, FIELD_LIMITS,
    LaunchContractError, _canonical_request_path, _public_https_origin,
    check, digest)


@dataclass(frozen=True)
class OfflineResponse:
    status: int
    headers: tuple[tuple[str, str], ...]
    chunks: tuple[bytes, ...]
    peer_ip: str
    tls_verified: bool
    redirects: int = 0


class SyntheticExchange:
    """Fixture-only exchange: exact request IDs map to preloaded response bytes."""

    def __init__(self, fixtures: dict[str, OfflineResponse]):
        check(type(fixtures) is dict and all(type(k) is str and
              type(v) is OfflineResponse for k, v in fixtures.items()),
              'SYNTHETIC_EXCHANGE_SHAPE')
        self._fixtures = dict(fixtures)
        self._used = set()

    def take(self, request_id: str) -> OfflineResponse:
        check(request_id in self._fixtures and request_id not in self._used,
              'SYNTHETIC_RESPONSE_MISSING_OR_REPLAYED')
        self._used.add(request_id)
        return self._fixtures[request_id]


def verify_response(request: dict, response: OfflineResponse, *, allowed_peer_ips: tuple[str, ...],
                    expected_etag: str | None = None,
                    expected_object_bytes: int | None = None,
                    max_header_bytes: int = 1024) -> bytes:
    """Verify one already received synthetic response against a frozen request.

    The caller must charge every delivered chunk to DurableBudget *before*
    invoking this verifier. No request is made here. An actual adapter must also
    enforce DNS/TLS, request-header, read-deadline and ambient-credential policy.
    """
    check(type(request) is dict and type(response) is OfflineResponse,
          'RESPONSE_INTERFACE_SHAPE')
    origin = request.get('origin')
    path = request.get('path')
    check(_public_https_origin(origin) and _canonical_request_path(origin, path),
          'RESPONSE_REQUEST_ORIGIN_PATH')
    try:
        peer_is_public = ipaddress.ip_address(response.peer_ip).is_global
    except (ValueError, TypeError):
        peer_is_public = False
    check(type(allowed_peer_ips) is tuple and allowed_peer_ips and
          type(response.peer_ip) is str and response.peer_ip in allowed_peer_ips and
          peer_is_public and
          response.tls_verified is True and response.redirects == 0,
          'RESPONSE_PEER_TLS_REDIRECT')
    check(type(max_header_bytes) is int and 1 <= max_header_bytes <= 4096 and
          type(response.headers) is tuple and len(response.headers) <= 32 and
          all(type(k) is str and type(v) is str and len(k) <= 64 and len(v) <= 1024
              and '\r' not in v and '\n' not in v for k, v in response.headers) and
          sum(len(k.encode('utf-8')) + len(v.encode('utf-8')) + 4
              for k, v in response.headers) <= max_header_bytes,
          'RESPONSE_HEADERS_BOUND')
    headers = {}
    for key, value in response.headers:
        name = key.lower()
        check(name not in headers and name.isascii(), 'RESPONSE_DUPLICATE_HEADER')
        headers[name] = value
    check(type(response.chunks) is tuple and response.chunks and
          all(type(chunk) is bytes and chunk for chunk in response.chunks),
          'RESPONSE_BODY_SHAPE')
    purpose = request.get('purpose')
    bound = request.get('reservation_bytes')
    body_length = sum(map(len, response.chunks))
    check(type(bound) is int and 0 < body_length <= bound,
          'RESPONSE_BODY_BOUND')
    body = b''.join(response.chunks)
    check(headers.get('content-encoding', 'identity').lower() == 'identity' and
          headers.get('transfer-encoding') is None and
          headers.get('content-length') == str(body_length), 'RESPONSE_LENGTH_ENCODING')
    check(type(expected_etag) is str and 1 <= len(expected_etag) <= 256 and
          headers.get('etag') == expected_etag and not expected_etag.startswith('W/'),
          'RESPONSE_OBJECT_IDENTITY')
    if purpose == 'FIELD':
        start, end = request.get('range_start'), request.get('range_end')
        check(type(start) is int and type(end) is int and 0 <= start <= end and
              type(expected_object_bytes) is int and expected_object_bytes > end and
              response.status == 206 and
              headers.get('content-range') ==
              f'bytes {start}-{end}/{expected_object_bytes}' and
              start + body_length - 1 == end, 'RESPONSE_RANGE_IDENTITY')
        check(request.get('provider') in FIELD_LIMITS and
              len(body) <= FIELD_LIMITS[request['provider']], 'RESPONSE_PROVIDER_CAP')
    else:
        check(purpose in ('INDEX', 'OBJECT_ID', 'METADATA', 'PROBE') and
              response.status == 200 and request.get('range_start') is None and
              request.get('range_end') is None and 'content-range' not in headers,
              'RESPONSE_OVERHEAD_STATUS')
    return body


def consume_synthetic_response(request: dict, exchange: SyntheticExchange,
                               budget: DurableBudget, *, started_monotonic: float,
                               allowed_peer_ips: tuple[str, ...],
                               expected_etag: str,
                               expected_object_bytes: int | None = None,
                               max_header_bytes: int = 1024) -> bytes:
    """Charge every delivered fixture chunk before response validation.

    Failure leaves an uncertain reservation in the durable journal; a fixture
    cannot be replayed and this function cannot open a network connection.
    """
    check(type(exchange) is SyntheticExchange and type(budget) is DurableBudget and
          type(request) is dict and type(request.get('request_id')) is str,
          'SYNTHETIC_CAPTURE_INTERFACE')
    key = request['request_id']
    budget.reserve(key, request.get('reservation_bytes'),
                   started_monotonic=started_monotonic)
    response = exchange.take(key)
    for chunk in response.chunks:
        budget.consume(key, chunk)
    body = verify_response(request, response, allowed_peer_ips=allowed_peer_ips,
                           expected_etag=expected_etag,
                           expected_object_bytes=expected_object_bytes,
                           max_header_bytes=max_header_bytes)
    budget.complete(key)
    return body


@dataclass(frozen=True)
class GridPoint:
    latitude: float
    longitude: float
    kelvin: float
    celsius: float
    distance_km: float
    raw_sha256: str


def _u(data: bytes, start: int, end: int) -> int:
    return int.from_bytes(data[start:end], 'big')


def _signed(data: bytes, start: int, end: int) -> int:
    value = _u(data, start, end)
    sign = 1 << (8 * (end - start) - 1)
    return -(value & (sign - 1)) if value & sign else value


def _distance(a: float, b: float, c: float, d: float) -> float:
    lat1, lat2 = math.radians(a), math.radians(c)
    delta_lat = lat2 - lat1
    delta_lon = math.radians(((d - b + 180) % 360) - 180)
    h = math.sin(delta_lat / 2)**2 + math.cos(lat1) * math.cos(lat2) * math.sin(delta_lon / 2)**2
    return 6371.0 * 2 * math.asin(min(1.0, math.sqrt(h)))


def decode_full_grid_station(raw: bytes, *, provider: str, section_sha256: dict[int, str],
                             latitude: float, longitude: float,
                             max_points: int = 1040000) -> GridPoint:
    """Bounded regular-grid GRIB2 simple/IEEE decoder for one station.

    Every section's hash must be frozen independently of the response. CCSDS,
    bitmap, complex packing and unknown grid templates fail closed. Semantic
    provider/run/member/parameter pins are a separate mandatory G3-L binding.
    """
    check(provider in FIELD_LIMITS and type(raw) is bytes and
          0 < len(raw) <= FIELD_LIMITS[provider], 'FULL_FIELD_COMPRESSED_BOUND')
    check(type(section_sha256) is dict and set(section_sha256) in
          ({1, 3, 4, 5, 6}, {1, 2, 3, 4, 5, 6}), 'FULL_FIELD_PIN_SET')
    for value in section_sha256.values():
        digest(value, 'FULL_FIELD_SECTION_PIN')
    check(all(type(x) in (int, float) and math.isfinite(x) for x in (latitude, longitude)) and
          -90 <= latitude <= 90 and -180 <= longitude <= 180,
          'FULL_FIELD_STATION_COORDINATES')
    check(type(max_points) is int and 1 <= max_points <= 1040000,
          'FULL_FIELD_POINT_POLICY')
    s = sections(raw)
    check(set(s) - {7} == set(section_sha256) and
          all(hashlib.sha256(s[n]).hexdigest() == section_sha256[n]
              for n in section_sha256),
          'FULL_FIELD_SECTION_MISMATCH')
    grid, packing = s[3], s[5]
    check(len(grid) == 72 and grid[5] == 0 and grid[10:14] == b'\0' * 4 and
          grid[14] in (6, 8) and _u(grid, 38, 42) == 0 and
          _u(grid, 42, 46) == 0xffffffff and grid[71] in (0, 64, 128, 192),
          'FULL_FIELD_REGULAR_GRID_REQUIRED')
    ni, nj, count = _u(grid, 30, 34), _u(grid, 34, 38), _u(grid, 6, 10)
    check(1 <= ni <= 1440 and 1 <= nj <= 721 and count == ni * nj and
          count <= max_points and _u(packing, 5, 9) == count,
          'FULL_FIELD_POINT_COUNT')
    check(len(s[6]) == 6 and s[6][5] == 255,
          'FULL_FIELD_BITMAP_UNSUPPORTED')
    lat0, lon0 = _signed(grid, 46, 50) / 1e6, _signed(grid, 50, 54) / 1e6
    latn, lonn = _signed(grid, 55, 59) / 1e6, _signed(grid, 59, 63) / 1e6
    dx = _u(grid, 67, 71) / 1e6 * (-1 if grid[71] & 128 else 1)
    dy = _u(grid, 63, 67) / 1e6 * (1 if grid[71] & 64 else -1)
    check(0 < abs(dx) <= 1 and 0 < abs(dy) <= 1 and
          abs(lat0 + (nj - 1) * dy - latn) <= 1e-6 and
          abs(((lon0 + (ni - 1) * dx - lonn + 180) % 360) - 180) <= 1e-6,
          'FULL_FIELD_GRID_ENDPOINT')
    check(-90 <= lat0 <= 90 and -90 <= latn <= 90, 'FULL_FIELD_GRID_LATITUDE')
    j0 = round((latitude - lat0) / dy)
    candidates = set()
    for shift in (-360, 0, 360):
        i0 = round((longitude + shift - lon0) / dx)
        for i in (i0 - 1, i0, i0 + 1, 0, ni - 1):
            for j in (j0 - 1, j0, j0 + 1, 0, nj - 1):
                if 0 <= i < ni and 0 <= j < nj:
                    plat, plon = lat0 + j * dy, ((lon0 + i * dx + 180) % 360) - 180
                    candidates.add((_distance(latitude, longitude, plat, plon), j * ni + i,
                                    plat, plon))
    check(bool(candidates), 'FULL_FIELD_STATION_NOT_ON_GRID')
    distance, index, plat, plon = min(candidates)
    check(distance <= 50, 'FULL_FIELD_STATION_TOO_FAR')
    payload = s[7][5:]
    template = _u(packing, 9, 11)
    if template == 0:
        check(len(packing) == 21 and packing[20] == 0, 'FULL_FIELD_SIMPLE_SCHEMA')
        reference = struct.unpack('>f', packing[11:15])[0]
        binary, decimal, bits = _signed(packing, 15, 17), _signed(packing, 17, 19), packing[19]
        check(math.isfinite(reference) and abs(binary) <= 32 and abs(decimal) <= 8 and
              bits <= 32 and len(payload) == (bits * count + 7) // 8,
              'FULL_FIELD_SIMPLE_LENGTH')
        padding = len(payload) * 8 - bits * count
        check(not padding or not (payload[-1] & ((1 << padding) - 1)),
              'FULL_FIELD_NONZERO_PADDING')
        if bits:
            start, end = index * bits, (index + 1) * bits
            word = int.from_bytes(payload[start // 8:(end + 7) // 8], 'big')
            number = (word >> ((8 - end % 8) % 8)) & ((1 << bits) - 1)
        else:
            number = 0
        kelvin = (reference + number * 2.**binary) * 10.**(-decimal)
    elif template == 4:
        check(len(packing) == 12 and packing[11] in (1, 2),
              'FULL_FIELD_IEEE_SCHEMA')
        width = 4 if packing[11] == 1 else 8
        check(len(payload) == count * width, 'FULL_FIELD_IEEE_LENGTH')
        kelvin = struct.unpack_from('>f' if width == 4 else '>d', payload, index * width)[0]
    else:
        raise LaunchContractError('FULL_FIELD_PACKING_UNSUPPORTED')
    check(math.isfinite(kelvin) and 150 <= kelvin <= 350,
          'FULL_FIELD_KELVIN_VALUE')
    return GridPoint(plat, plon, kelvin, kelvin - 273.15, distance,
                     hashlib.sha256(raw).hexdigest())


@dataclass(frozen=True)
class MeasuredClock:
    utc_seconds: float
    monotonic_seconds: float
    uncertainty_seconds: float
    measured_monotonic_seconds: float
    boot_id: str
    evidence_sha256: str


class ClockSequence:
    """Checks externally measured clock evidence; cannot attest sync itself."""

    PHASES = ('request_start', 'body_receipt', 'decode_complete', 'durable_seal')

    def __init__(self, *, boot_id: str, max_measurement_age_seconds: int):
        check(type(boot_id) is str and boot_id and
              type(max_measurement_age_seconds) is int and
              1 <= max_measurement_age_seconds <= 3600, 'CLOCK_POLICY')
        self.boot_id = boot_id
        self.max_age = max_measurement_age_seconds
        self.readings: list[tuple[str, MeasuredClock]] = []

    def record(self, phase: str, reading: MeasuredClock) -> None:
        check(phase in self.PHASES and len(self.readings) == self.PHASES.index(phase),
              'CLOCK_PHASE_ORDER')
        check(type(reading) is MeasuredClock and reading.boot_id == self.boot_id,
              'CLOCK_BOOT_ID')
        digest(reading.evidence_sha256, 'CLOCK_EVIDENCE_DIGEST')
        vals = (reading.utc_seconds, reading.monotonic_seconds,
                reading.uncertainty_seconds, reading.measured_monotonic_seconds)
        check(all(type(v) in (int, float) and math.isfinite(v) for v in vals) and
              0 <= reading.uncertainty_seconds <= 1 and
              0 <= reading.measured_monotonic_seconds <= reading.monotonic_seconds and
              reading.monotonic_seconds - reading.measured_monotonic_seconds <= self.max_age,
              'CLOCK_MEASUREMENT_BOUND')
        if self.readings:
            previous = self.readings[-1][1]
            check(reading.monotonic_seconds >= previous.monotonic_seconds and
                  abs((reading.utc_seconds - previous.utc_seconds) -
                      (reading.monotonic_seconds - previous.monotonic_seconds)) <=
                  reading.uncertainty_seconds + previous.uncertainty_seconds,
                  'CLOCK_STEP_OR_REVERSAL')
        self.readings.append((phase, reading))

    def causal_before(self, decision_lower_utc: float) -> bool:
        check(len(self.readings) == len(self.PHASES) and
              type(decision_lower_utc) in (int, float) and
              math.isfinite(decision_lower_utc), 'CLOCK_SEQUENCE_INCOMPLETE')
        last = self.readings[-1][1]
        return last.utc_seconds + last.uncertainty_seconds <= decision_lower_utc


class ImmutableObjectStore:
    """Private digest-named objects, sealed with fsync and exclusive hard links."""

    def __init__(self, root: Path):
        self.root = Path(root)
        check(self.root.is_absolute() and self.root == self.root.resolve() and
              all(not Path(p).is_symlink() for p in (self.root, *self.root.parents)),
              'OBJECT_ROOT_PATH')
        self.dir_fd = os.open(self.root / 'objects', os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            st = os.fstat(self.dir_fd)
            parent = os.stat(self.root, follow_symlinks=False)
            check(stat.S_ISDIR(st.st_mode) and st.st_uid == os.getuid() and
                  stat.S_IMODE(st.st_mode) == 0o700 and
                  stat.S_ISDIR(parent.st_mode) and parent.st_uid == os.getuid() and
                  stat.S_IMODE(parent.st_mode) == 0o700,
                  'OBJECT_DIRECTORY_PRIVATE')
            self.identity = (st.st_dev, st.st_ino)
            self.root_identity = (parent.st_dev, parent.st_ino)
        except BaseException:
            self.close()
            raise

    def _healthy(self):
        st = os.fstat(self.dir_fd)
        current = os.stat(self.root / 'objects', follow_symlinks=False)
        parent = os.stat(self.root, follow_symlinks=False)
        check(all(not Path(p).is_symlink() for p in (self.root, *self.root.parents)) and
              (parent.st_dev, parent.st_ino) == self.root_identity and
              stat.S_ISDIR(parent.st_mode) and parent.st_uid == os.getuid() and
              stat.S_IMODE(parent.st_mode) == 0o700, 'OBJECT_ROOT_CHANGED')
        check((st.st_dev, st.st_ino) == self.identity == (current.st_dev, current.st_ino) and
              stat.S_ISDIR(current.st_mode) and current.st_uid == os.getuid() and
              stat.S_IMODE(current.st_mode) == 0o700,
              'OBJECT_DIRECTORY_CHANGED')

    def seal(self, data: bytes) -> str:
        check(type(data) is bytes and 0 < len(data) <= 4 * 1024 * 1024,
              'OBJECT_BYTES_BOUND')
        self._healthy()
        sha = hashlib.sha256(data).hexdigest()
        temporary = '.tmp-' + secrets.token_hex(16)
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                     0o600, dir_fd=self.dir_fd)
        try:
            with os.fdopen(fd, 'wb', closefd=False) as output:
                output.write(data)
                output.flush()
            os.fsync(fd)
            self._healthy()
            try:
                os.link(temporary, sha, src_dir_fd=self.dir_fd,
                        dst_dir_fd=self.dir_fd, follow_symlinks=False)
            except FileExistsError as exc:
                raise LaunchContractError('OBJECT_ALREADY_EXISTS') from exc
            os.fsync(self.dir_fd)
            return sha
        finally:
            os.close(fd)
            os.unlink(temporary, dir_fd=self.dir_fd)
            os.fsync(self.dir_fd)

    def read(self, sha: str) -> bytes:
        digest(sha, 'OBJECT_DIGEST')
        self._healthy()
        fd = os.open(sha, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=self.dir_fd)
        try:
            st = os.fstat(fd)
            check(stat.S_ISREG(st.st_mode) and st.st_uid == os.getuid() and
                  stat.S_IMODE(st.st_mode) == 0o600 and st.st_nlink == 1 and
                  0 < st.st_size <= 4 * 1024 * 1024, 'OBJECT_FILE_IDENTITY')
            data = b''
            while len(data) <= st.st_size:
                chunk = os.read(fd, min(1024 * 1024, st.st_size + 1 - len(data)))
                if not chunk:
                    break
                data += chunk
            check(len(data) == st.st_size and hashlib.sha256(data).hexdigest() == sha,
                  'OBJECT_DIGEST_MISMATCH')
            return data
        finally:
            os.close(fd)

    def close(self):
        if getattr(self, 'dir_fd', None) is not None:
            os.close(self.dir_fd)
            self.dir_fd = None

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()
