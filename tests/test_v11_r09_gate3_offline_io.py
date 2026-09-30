"""Synthetic adversarial tests: no provider endpoint or real capture."""
from dataclasses import replace
import hashlib
import multiprocessing
import os
import struct

import pytest

from polymarket_scanner.v11.ecmwf_grib import sections
from tools.v11_r09_gate3_launch import DurableBudget, LaunchContractError
from tools.v11_r09_gate3_offline_io import (ClockSequence, ImmutableObjectStore,
    MeasuredClock, OfflineResponse, SyntheticExchange, consume_synthetic_response,
    decode_full_grid_station, verify_response)


def _request():
    return {'request_id': 'fixture_1', 'purpose': 'FIELD', 'provider': 'GEFS',
            'origin': 'https://weather.example.invalid', 'path': '/fixed/message',
            'range_start': 10, 'range_end': 13, 'reservation_bytes': 8}


def _response(**changes):
    value = OfflineResponse(206, (('Content-Length', '4'),
                                   ('Content-Range', 'bytes 10-13/100'),
                                   ('ETag', '"pinned-object"')),
                            (b'AB', b'CD'), '8.8.8.8', True)
    return replace(value, **changes)


def _verified(request=None, response=None):
    return verify_response(request or _request(), response or _response(),
                           allowed_peer_ips=('8.8.8.8',),
                           expected_etag='"pinned-object"',
                           expected_object_bytes=100)


def test_offline_transport_exact_response_and_no_replay(tmp_path):
    assert _verified() == b'ABCD'
    journal = tmp_path / 'journal'
    journal.mkdir(mode=0o700)
    exchange = SyntheticExchange({'fixture_1': _response()})
    with DurableBudget(journal, 'a' * 64, max_bytes=8) as budget:
        assert consume_synthetic_response(_request(), exchange, budget,
            started_monotonic=2, allowed_peer_ips=('8.8.8.8',),
            expected_etag='"pinned-object"', expected_object_bytes=100) == b'ABCD'
        assert budget.received == 4 and budget.in_flight is None
        with pytest.raises(LaunchContractError, match='REQUEST_KEY_REUSE'):
            consume_synthetic_response(_request(), exchange, budget,
                started_monotonic=4, allowed_peer_ips=('8.8.8.8',),
                expected_etag='"pinned-object"', expected_object_bytes=100)


@pytest.mark.parametrize('change,reason', [
    ({'status': 200}, 'RESPONSE_RANGE_IDENTITY'),
    ({'peer_ip': '127.0.0.1'}, 'RESPONSE_PEER_TLS_REDIRECT'),
    ({'tls_verified': False}, 'RESPONSE_PEER_TLS_REDIRECT'),
    ({'redirects': 1}, 'RESPONSE_PEER_TLS_REDIRECT'),
    ({'headers': (('Content-Length', '4'), ('Content-Range', 'bytes 11-14/100'),
                  ('ETag', '"pinned-object"'))}, 'RESPONSE_RANGE_IDENTITY'),
    ({'headers': (('Content-Length', '4'), ('ETag', 'W/"pinned-object"'),
                  ('Content-Range', 'bytes 10-13/100'))}, 'RESPONSE_OBJECT_IDENTITY'),
    ({'headers': (('Content-Length', '4'), ('Content-Length', '4'),
                  ('ETag', '"pinned-object"'))}, 'RESPONSE_DUPLICATE_HEADER'),
    ({'headers': (('Content-Length', '4'), ('Content-Encoding', 'gzip'),
                  ('ETag', '"pinned-object"'))}, 'RESPONSE_LENGTH_ENCODING'),
    ({'headers': (('Content-Length', '4'), ('Content-Range', 'bytes 10-13/100'),
                  ('ETag', '"pinned-object"'), ('X-Long', 'a' * 1000))},
     'RESPONSE_HEADERS_BOUND'),
])
def test_response_identity_failures(change, reason):
    with pytest.raises(LaunchContractError, match=reason):
        _verified(response=_response(**change))


@pytest.mark.parametrize('etag', ['*', 'not-quoted', '"one", "two"',
                                      'W/"weak"', '"bad\x00tag"', '"unicode-\u0100"'])
def test_response_rejects_non_strong_etag_even_when_pin_matches(etag):
    headers = tuple((key, etag if key == 'ETag' else value)
                    for key, value in _response().headers)
    with pytest.raises(LaunchContractError, match='RESPONSE_OBJECT_IDENTITY'):
        verify_response(_request(), _response(headers=headers),
            allowed_peer_ips=('8.8.8.8',), expected_etag=etag,
            expected_object_bytes=100)


@pytest.mark.parametrize('etag', ['""', '"ordinary-tag"', '"back\\slash"'])
def test_response_accepts_one_valid_strong_etag(etag):
    headers = tuple((key, etag if key == 'ETag' else value)
                    for key, value in _response().headers)
    assert verify_response(_request(), _response(headers=headers),
        allowed_peer_ips=('8.8.8.8',), expected_etag=etag,
        expected_object_bytes=100) == b'ABCD'


def test_delivered_invalid_or_oversized_bytes_remain_charged(tmp_path):
    journal = tmp_path / 'journal'
    journal.mkdir(mode=0o700)
    bad = _response(status=200)
    with DurableBudget(journal, 'a' * 64, max_bytes=8) as budget:
        with pytest.raises(LaunchContractError, match='RESPONSE_RANGE_IDENTITY'):
            consume_synthetic_response(_request(), SyntheticExchange({'fixture_1': bad}),
                budget, started_monotonic=2, allowed_peer_ips=('8.8.8.8',),
                expected_etag='"pinned-object"', expected_object_bytes=100)
        assert budget.received == 4 and budget.in_flight == 'fixture_1'
    with DurableBudget(journal, 'a' * 64, max_bytes=8) as recovered:
        assert recovered.received == 4 and recovered.in_flight == 'fixture_1'
        with pytest.raises(LaunchContractError, match='UNCERTAIN_REQUEST_HELD'):
            recovered.reserve('fixture_2', 1, started_monotonic=5)

    other = tmp_path / 'other'
    other.mkdir(mode=0o700)
    with DurableBudget(other, 'b' * 64, max_bytes=8) as budget:
        with pytest.raises(LaunchContractError, match='STREAM_ABORT_AT_ALLOWANCE'):
            consume_synthetic_response(_request(),
                SyntheticExchange({'fixture_1': _response(chunks=(b'123456789',))}),
                budget, started_monotonic=2, allowed_peer_ips=('8.8.8.8',),
                expected_etag='"pinned-object"', expected_object_bytes=100)
        assert budget.received == 9 and budget.violated


def _u(n, size):
    return n.to_bytes(size, 'big')


def _section(number, length):
    data = bytearray(length)
    data[:5] = _u(length, 4) + bytes([number])
    return data


def _grib(*, template=0, bitmap=255, count=4, values=(290, 291, 292, 293),
          di=500000, dj=500000, scan=64, swapped_endpoint=False):
    ident = _section(1, 21)
    grid = _section(3, 72)
    grid[6:10] = _u(count, 4)
    grid[14] = 6
    grid[30:38] = _u(2, 4) + _u(2, 4)
    grid[42:46] = b'\xff' * 4
    grid[46:54] = _u(33000000, 4) + _u(276000000, 4)
    xsign, ysign = (-1 if scan & 128 else 1), (1 if scan & 64 else -1)
    grid[55:63] = (_u(33000000 + ysign * (di if swapped_endpoint else dj), 4) +
                   _u(276000000 + xsign * (dj if swapped_endpoint else di), 4))
    grid[63:72] = _u(di, 4) + _u(dj, 4) + bytes([scan])
    product = _section(4, 37)
    packing = _section(5, 21 if template == 0 else 12)
    packing[5:11] = _u(count, 4) + _u(template, 2)
    if template == 0:
        packing[11:15] = struct.pack('>f', 280.)
        packing[19] = 8
        packed = bytes(int(v - 280) for v in values)
    else:
        packing[11] = 1
        packed = struct.pack('>' + 'f' * len(values), *values)
    mask = _section(6, 6)
    mask[5] = bitmap
    data = _section(7, 5 + len(packed))
    data[5:] = packed
    body = b''.join((ident, grid, product, packing, mask, data)) + b'7777'
    return b'GRIB\0\0\0\x02' + _u(16 + len(body), 8) + body


def _pins(raw):
    return {n: hashlib.sha256(s).hexdigest() for n, s in sections(raw).items() if n != 7}


@pytest.mark.parametrize('template',[0, 4])
def test_full_field_station_decodes_pinned_regular_grid(template):
    raw = _grib(template=template)
    point = decode_full_grid_station(raw, provider='GEFS',
        section_sha256=_pins(raw), latitude=33.5, longitude=-83.5)
    assert point.kelvin == 293 and abs(point.celsius - 19.85) < 1e-10
    assert point.distance_km == 0 and point.raw_sha256 == hashlib.sha256(raw).hexdigest()


@pytest.mark.parametrize('scan', [0, 64, 128, 192])
@pytest.mark.parametrize('template', [0, 4])
def test_rectangular_grid_scan_modes_decode_and_bad_endpoints_reject(scan, template):
    ysign, xsign = (1 if scan & 64 else -1), (-1 if scan & 128 else 1)
    lat, lon = 33 + ysign * .25, -84 + xsign * .5
    raw = _grib(template=template, di=500000, dj=250000, scan=scan)
    point = decode_full_grid_station(raw, provider='GEFS', section_sha256=_pins(raw),
                                     latitude=lat, longitude=lon)
    assert (point.latitude, point.longitude, point.kelvin, point.distance_km) == (
        lat, lon, 293, 0)
    bad = _grib(template=template, di=500000, dj=250000, scan=scan,
                swapped_endpoint=True)
    with pytest.raises(LaunchContractError, match='FULL_FIELD_GRID_ENDPOINT'):
        decode_full_grid_station(bad, provider='GEFS', section_sha256=_pins(bad),
                                 latitude=lat, longitude=lon)


def test_full_field_rejects_tamper_bitmap_wrong_station_and_unsupported_packing():
    raw = _grib()
    pins = _pins(raw)
    with pytest.raises(LaunchContractError, match='FULL_FIELD_SECTION_MISMATCH'):
        decode_full_grid_station(raw, provider='GEFS', section_sha256={**pins, 3: '0'*64},
                                 latitude=33.5, longitude=-83.5)
    for bad, reason in ((_grib(bitmap=0), 'FULL_FIELD_BITMAP_UNSUPPORTED'),
                        (_grib(template=42), 'FULL_FIELD_PACKING_UNSUPPORTED'),
                        (_grib(count=1000000), 'FULL_FIELD_POINT_COUNT')):
        with pytest.raises(LaunchContractError, match=reason):
            decode_full_grid_station(bad, provider='GEFS', section_sha256=_pins(bad),
                                     latitude=33.5, longitude=-83.5)
    with pytest.raises(LaunchContractError, match='FULL_FIELD_STATION_TOO_FAR'):
        decode_full_grid_station(raw, provider='GEFS', section_sha256=pins,
                                 latitude=0, longitude=0)


def _clock(utc, mono, measured=None, boot='boot-a'):
    return MeasuredClock(utc, mono, 0.2, mono if measured is None else measured,
                         boot, 'a'*64)


def test_clock_sequence_rejects_step_stale_boot_and_late_seal():
    clock = ClockSequence(boot_id='boot-a', max_measurement_age_seconds=10)
    clock.record('request_start', _clock(100, 1))
    with pytest.raises(LaunchContractError, match='CLOCK_PHASE_ORDER'):
        clock.record('decode_complete', _clock(101, 2))
    with pytest.raises(LaunchContractError, match='CLOCK_BOOT_ID'):
        clock.record('body_receipt', _clock(101, 2, boot='boot-b'))
    with pytest.raises(LaunchContractError, match='CLOCK_MEASUREMENT_BOUND'):
        clock.record('body_receipt', _clock(101, 20, measured=1))
    with pytest.raises(LaunchContractError, match='CLOCK_STEP_OR_REVERSAL'):
        clock.record('body_receipt', _clock(120, 2))
    clock.record('body_receipt', _clock(101, 2))
    clock.record('decode_complete', _clock(102, 3))
    clock.record('durable_seal', _clock(103, 4))
    assert clock.causal_before(104)
    assert not clock.causal_before(103)


def test_clock_checks_every_phase_upper_bound():
    clock = ClockSequence(boot_id='boot-a', max_measurement_age_seconds=10)
    for phase, utc, mono, uncertainty in zip(clock.PHASES,
            (100, 100.1, 100.2, 100.3), (1, 1.1, 1.2, 1.3), (1, 1, .1, 0)):
        clock.record(phase, MeasuredClock(utc, mono, uncertainty, mono,
                                         'boot-a', 'a'*64))
    assert not clock.causal_before(100.4)
    assert clock.causal_before(101.1)


def test_clock_rejects_cumulative_step_with_pairwise_overlap():
    clock = ClockSequence(boot_id='boot-a', max_measurement_age_seconds=10)
    for phase, utc, mono in zip(clock.PHASES[:2], (100, 99), (1, 1.1)):
        clock.record(phase, MeasuredClock(utc, mono, 1, mono, 'boot-a', 'a'*64))
    with pytest.raises(LaunchContractError, match='CLOCK_STEP_OR_REVERSAL'):
        clock.record('decode_complete', MeasuredClock(98, 1.2, 1, 1.2,
                                                      'boot-a', 'a'*64))


def test_private_store_seals_once_and_rejects_alias_or_mutation(tmp_path):
    root = tmp_path / 'private'
    root.mkdir(mode=0o700)
    (root / 'objects').mkdir(mode=0o700)
    with ImmutableObjectStore(root) as store:
        sha = store.seal(b'fixture bytes')
        assert store.read(sha) == b'fixture bytes'
        with pytest.raises(LaunchContractError, match='OBJECT_ALREADY_EXISTS'):
            store.seal(b'fixture bytes')
        os.link(root / 'objects' / sha, root / 'objects' / 'alias')
        with pytest.raises(LaunchContractError, match='OBJECT_FILE_IDENTITY'):
            store.read(sha)
        os.unlink(root / 'objects' / 'alias')
        (root / 'objects' / sha).write_bytes(b'mutated')
        with pytest.raises(LaunchContractError, match='OBJECT_DIGEST_MISMATCH'):
            store.read(sha)
    alias = tmp_path / 'alias'
    alias.symlink_to(root, target_is_directory=True)
    with pytest.raises(LaunchContractError, match='OBJECT_ROOT_PATH'):
        ImmutableObjectStore(alias)
    os.chmod(root / 'objects', 0o755)
    with pytest.raises(LaunchContractError, match='OBJECT_DIRECTORY_PRIVATE'):
        ImmutableObjectStore(root)


def _private_store_root(tmp_path):
    root = tmp_path / 'private'
    root.mkdir(mode=0o700)
    (root / 'objects').mkdir(mode=0o700)
    return root


@pytest.mark.parametrize('fault', ['directory_fsync_first', 'directory_fsync_last',
                                   'link_after_publication', 'unlink'])
def test_store_publication_fault_preserves_bytes_and_blocks_reopen(tmp_path,
                                                                    monkeypatch, fault):
    root = _private_store_root(tmp_path)
    raw = b'faulted synthetic object'
    sha = hashlib.sha256(raw).hexdigest()
    with ImmutableObjectStore(root) as store:
        real_fsync, real_link, real_unlink = os.fsync, os.link, os.unlink
        calls = 0
        def fault_fsync(fd):
            nonlocal calls
            if fd == store.dir_fd:
                calls += 1
                if (fault == 'directory_fsync_first' and calls == 1 or
                    fault == 'directory_fsync_last' and calls == 2):
                    raise OSError('injected directory fsync')
            return real_fsync(fd)
        def fault_link(*args, **kwargs):
            real_link(*args, **kwargs)
            if fault == 'link_after_publication':
                raise OSError('injected interrupted link')
        def fault_unlink(path, *args, **kwargs):
            if fault == 'unlink' and path.startswith('.tmp-'):
                raise OSError('injected unlink')
            return real_unlink(path, *args, **kwargs)
        with monkeypatch.context() as patch:
            patch.setattr(os, 'fsync', fault_fsync)
            patch.setattr(os, 'link', fault_link)
            patch.setattr(os, 'unlink', fault_unlink)
            with pytest.raises(OSError, match='injected'):
                store.seal(raw)
        assert (root / 'objects' / sha).read_bytes() == raw
        with pytest.raises(LaunchContractError, match='OBJECT_DURABILITY_UNCERTAIN'):
            store.read(sha)
        with pytest.raises(LaunchContractError, match='OBJECT_DURABILITY_UNCERTAIN'):
            store.seal(b'other')
    with ImmutableObjectStore(root) as reopened:
        with pytest.raises(LaunchContractError, match='OBJECT_DURABILITY_UNCERTAIN'):
            reopened.read(sha)
        with pytest.raises(LaunchContractError, match='OBJECT_DURABILITY_UNCERTAIN'):
            reopened.seal(b'other')


def test_store_file_fsync_failure_never_publishes_and_can_retry(tmp_path,
                                                                 monkeypatch):
    root = _private_store_root(tmp_path)
    raw = b'file fsync failure'
    with ImmutableObjectStore(root) as store:
        real_fsync = os.fsync
        with monkeypatch.context() as patch:
            def fault(fd):
                if fd != store.dir_fd:
                    raise OSError('injected file fsync')
                return real_fsync(fd)
            patch.setattr(os, 'fsync', fault)
            with pytest.raises(OSError, match='injected file fsync'):
                store.seal(raw)
        assert list((root / 'objects').iterdir()) == []
        assert store.read(store.seal(raw)) == raw


def test_store_link_failure_holds_temporary_and_closes_store(tmp_path, monkeypatch):
    root = _private_store_root(tmp_path)
    raw = b'link failure before publication'
    with ImmutableObjectStore(root) as store:
        with monkeypatch.context() as patch:
            def fault_link(*args, **kwargs):
                raise OSError('injected link failure')
            patch.setattr(os, 'link', fault_link)
            with pytest.raises(OSError, match='injected link failure'):
                store.seal(raw)
        assert not (root / 'objects' / hashlib.sha256(raw).hexdigest()).exists()
        temporary = list((root / 'objects').glob('.tmp-*'))
        assert len(temporary) == 1 and temporary[0].read_bytes() == raw
        with pytest.raises(LaunchContractError, match='OBJECT_DURABILITY_UNCERTAIN'):
            store.seal(b'other')
    with ImmutableObjectStore(root) as reopened:
        with pytest.raises(LaunchContractError, match='OBJECT_DURABILITY_UNCERTAIN'):
            reopened.seal(b'other')


def test_store_successful_reopen_requires_separate_recovery_review(tmp_path):
    root = _private_store_root(tmp_path)
    with ImmutableObjectStore(root) as store:
        sha = store.seal(b'good object')
        assert store.read(sha) == b'good object'
    with ImmutableObjectStore(root) as reopened:
        with pytest.raises(LaunchContractError, match='OBJECT_DURABILITY_UNCERTAIN'):
            reopened.read(sha)


def _crash_during_link(root, raw):
    with ImmutableObjectStore(root) as store:
        real_link = os.link
        def crash(*args, **kwargs):
            real_link(*args, **kwargs)
            os._exit(17)
        os.link = crash
        store.seal(raw)


def test_store_interrupted_publication_refuses_reopen(tmp_path):
    root = _private_store_root(tmp_path)
    raw = b'interrupted publication'
    child = multiprocessing.get_context('fork').Process(
        target=_crash_during_link, args=(root, raw))
    child.start()
    try:
        child.join(5)
        assert not child.is_alive() and child.exitcode == 17
    finally:
        if child.is_alive():
            child.terminate()
            child.join(5)
    sha = hashlib.sha256(raw).hexdigest()
    assert (root / 'objects' / sha).read_bytes() == raw
    with ImmutableObjectStore(root) as reopened:
        with pytest.raises(LaunchContractError, match='OBJECT_DURABILITY_UNCERTAIN'):
            reopened.read(sha)


def _read_fifo_in_child(root, sha, sender):
    with ImmutableObjectStore(root) as store:
        os.mkfifo(root / 'objects' / sha, 0o600)
        sender.send('ready')
        try:
            store.read(sha)
        except LaunchContractError as exc:
            sender.send(str(exc))


def test_store_fifo_read_rejects_without_blocking(tmp_path):
    root = _private_store_root(tmp_path)
    receiver, sender = multiprocessing.get_context('fork').Pipe(duplex=False)
    child = multiprocessing.get_context('fork').Process(
        target=_read_fifo_in_child, args=(root, 'd'*64, sender))
    child.start()
    try:
        assert receiver.poll(5) and receiver.recv() == 'ready'
        assert receiver.poll(2) and receiver.recv() == 'OBJECT_FILE_IDENTITY'
        child.join(5)
        assert child.exitcode == 0
    finally:
        if child.is_alive():
            child.terminate()
            child.join(5)
        receiver.close()
        sender.close()


def test_synthetic_bytes_seal_decode_and_clock_compose(tmp_path):
    raw = _grib()
    request = _request()
    request['range_start'] = 0
    request['range_end'] = len(raw) - 1
    request['reservation_bytes'] = len(raw)
    response = OfflineResponse(206,
        (('Content-Length', str(len(raw))),
         ('Content-Range', f'bytes 0-{len(raw)-1}/{len(raw)}'),
         ('ETag', '"pinned-object"')),
        (raw[:70], raw[70:]), '8.8.8.8', True)
    journal = tmp_path / 'journal'
    journal.mkdir(mode=0o700)
    root = tmp_path / 'private'
    root.mkdir(mode=0o700)
    (root / 'objects').mkdir(mode=0o700)
    with DurableBudget(journal, 'a' * 64, max_bytes=len(raw)) as budget:
        received = consume_synthetic_response(request,
            SyntheticExchange({'fixture_1': response}), budget,
            started_monotonic=1, allowed_peer_ips=('8.8.8.8',),
            expected_etag='"pinned-object"', expected_object_bytes=len(raw))
        assert budget.received == len(raw)
    with ImmutableObjectStore(root) as store:
        sha = store.seal(received)
        point = decode_full_grid_station(store.read(sha), provider='GEFS',
            section_sha256=_pins(raw), latitude=33.5, longitude=-83.5)
        assert point.raw_sha256 == sha and point.kelvin == 293
    clock = ClockSequence(boot_id='boot-a', max_measurement_age_seconds=10)
    for phase, utc, mono in zip(clock.PHASES, (100, 101, 102, 103), (1, 2, 3, 4)):
        clock.record(phase, _clock(utc, mono))
    assert clock.causal_before(104)
