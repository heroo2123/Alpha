"""Exact-a804034 review probes. Tests assert observed defects, not acceptance.
Run from the candidate worktree with PYTHONPATH=.; all inputs are synthetic.
"""
import hashlib
import multiprocessing
import os
import struct
from pathlib import Path

import pytest
from polymarket_scanner.v11.ecmwf_grib import sections
from tools.v11_r09_gate3_launch import DurableBudget, LaunchContractError
from tools.v11_r09_gate3_offline_io import (
    ClockSequence, ImmutableObjectStore, MeasuredClock, OfflineResponse,
    SyntheticExchange, consume_synthetic_response, decode_full_grid_station,
    verify_response,
)


def section(number, length):
    value = bytearray(length)
    value[:4] = length.to_bytes(4, 'big')
    value[4] = number
    return value


def grib(*, swapped_endpoint=False, square=False, scan=64):
    # Independent fixture using WMO template 3.0 octets, zero-based in Python.
    ident = section(1, 21)
    grid = section(3, 72)
    grid[6:10] = (4).to_bytes(4, 'big')
    grid[14] = 6
    grid[30:34] = grid[34:38] = (2).to_bytes(4, 'big')
    grid[42:46] = b'\xff' * 4
    di, dj = 500000, 500000 if square else 250000
    xsign, ysign = (-1 if scan & 128 else 1), (1 if scan & 64 else -1)
    lastlat = 33000000 + ysign * (di if swapped_endpoint else dj)
    lastlon = 276000000 + xsign * (dj if swapped_endpoint else di)
    for offset, value in ((46,33000000),(50,276000000),(55,lastlat),(59,lastlon),
                          (63,di),(67,dj)):
        grid[offset:offset+4] = value.to_bytes(4, 'big')
    grid[71] = scan
    product = section(4, 37)
    packing = section(5, 21)
    packing[5:9] = (4).to_bytes(4, 'big')
    packing[11:15] = struct.pack('>f', 280.)
    packing[19] = 8
    bitmap = section(6, 6)
    bitmap[5] = 255
    data = section(7, 9)
    data[5:] = bytes((10,11,12,13))
    body = b''.join((ident, grid, product, packing, bitmap, data)) + b'7777'
    return b'GRIB\0\0\0\x02' + (16 + len(body)).to_bytes(8, 'big') + body


def decode(raw, lat, lon):
    pins = {n:hashlib.sha256(s).hexdigest() for n,s in sections(raw).items() if n != 7}
    return decode_full_grid_station(raw, provider='GEFS', section_sha256=pins,
                                    latitude=lat, longitude=lon)


@pytest.mark.parametrize('scan',[0,64,128,192])
def test_defect_valid_rectangular_grid_rejected(scan):
    ys, xs = (1 if scan & 64 else -1), (-1 if scan & 128 else 1)
    with pytest.raises(LaunchContractError, match='FULL_FIELD_GRID_ENDPOINT'):
        decode(grib(scan=scan), 33+ys*.25, -84+xs*.5)


def test_defect_inconsistent_rectangular_grid_accepted():
    result = decode(grib(swapped_endpoint=True), 33.5, -83.75)
    assert (result.latitude,result.longitude,result.kelvin,result.distance_km) == (33.5,-83.75,293,0)


@pytest.mark.parametrize('scan',[0,64,128,192])
def test_control_square_grid_all_supported_scans(scan):
    ys, xs = (1 if scan & 64 else -1), (-1 if scan & 128 else 1)
    assert decode(grib(square=True,scan=scan),33+ys*.5,-84+xs*.5).kelvin == 293


def clock(utcs, monos, uncertainties):
    value = ClockSequence(boot_id='independent-boot', max_measurement_age_seconds=10)
    for phase,utc,mono,unc in zip(value.PHASES,utcs,monos,uncertainties):
        value.record(phase,MeasuredClock(utc,mono,unc,mono,'independent-boot','c'*64))
    return value


def test_defect_receipt_upper_bound_after_decision_accepted():
    value = clock((100,100.1,100.2,100.3),(1,1.1,1.2,1.3),(1,1,.1,0))
    assert value.readings[1][1].utc_seconds + value.readings[1][1].uncertainty_seconds > 100.4
    assert value.causal_before(100.4)


def test_defect_pairwise_steps_hide_globally_inconsistent_clock():
    value = clock((100,99,98,97),(1,1.1,1.2,1.3),(1,1,1,1))
    offsets = [(r.utc_seconds-r.monotonic_seconds-r.uncertainty_seconds,
                r.utc_seconds-r.monotonic_seconds+r.uncertainty_seconds) for _,r in value.readings]
    assert max(x[0] for x in offsets) > min(x[1] for x in offsets)
    assert value.causal_before(98)
    assert value.readings[1][1].utc_seconds + value.readings[1][1].uncertainty_seconds > 98


def test_control_stable_clock_early_and_late_decisions():
    value = clock((100,101,102,103),(1,2,3,4),(.2,.2,.2,.2))
    assert value.causal_before(104)
    assert not value.causal_before(103)


def private_store(tmp_path):
    root = tmp_path/'store'
    root.mkdir(mode=0o700)
    (root/'objects').mkdir(mode=0o700)
    return root


def test_defect_failed_directory_seal_still_readable_same_instance_and_reopened(tmp_path,monkeypatch):
    root = private_store(tmp_path)
    raw = b'independent synthetic bytes'
    sha = hashlib.sha256(raw).hexdigest()
    real_fsync = os.fsync
    with ImmutableObjectStore(root) as store:
        def failed_dir_fsync(fd):
            if fd == store.dir_fd:
                raise OSError('injected directory durability failure')
            return real_fsync(fd)
        with monkeypatch.context() as patch:
            patch.setattr(os,'fsync',failed_dir_fsync)
            with pytest.raises(OSError,match='durability failure'):
                store.seal(raw)
            assert store.read(sha) == raw
        assert store.seal(b'another object after uncertain seal')
    with ImmutableObjectStore(root) as reopened:
        assert reopened.read(sha) == raw


def read_fifo(root, sha, connection):
    with ImmutableObjectStore(Path(root)) as store:
        connection.send('ready')
        try:
            store.read(sha)
        except Exception as exc:
            connection.send(type(exc).__name__)
        else:
            connection.send('returned')


def test_defect_fifo_blocks_before_file_type_rejection(tmp_path):
    root = private_store(tmp_path)
    sha = 'd'*64
    os.mkfifo(root/'objects'/sha,0o600)
    ctx = multiprocessing.get_context('fork')
    receiver,sender = ctx.Pipe(duplex=False)
    process = ctx.Process(target=read_fifo,args=(str(root),sha,sender))
    process.start()
    try:
        assert receiver.poll(5) and receiver.recv() == 'ready'
        assert not receiver.poll(1), 'Expected exact candidate to block on FIFO open'
        assert process.is_alive()
    finally:
        process.terminate()
        process.join(5)
        receiver.close()
        sender.close()
        assert not process.is_alive()


def test_control_store_file_fsync_failure_never_publishes(tmp_path,monkeypatch):
    root = private_store(tmp_path)
    raw = b'file fsync failure control'
    real_fsync = os.fsync
    with ImmutableObjectStore(root) as store:
        def fail_file(fd):
            if fd != store.dir_fd:
                raise OSError('injected file fsync')
            return real_fsync(fd)
        with monkeypatch.context() as patch:
            patch.setattr(os,'fsync',fail_file)
            with pytest.raises(OSError,match='file fsync'):
                store.seal(raw)
        assert not (root/'objects'/hashlib.sha256(raw).hexdigest()).exists()
        assert not list((root/'objects').glob('.tmp-*'))
        sha = store.seal(raw)
        assert store.read(sha) == raw


def request_response(etag='"object"'):
    request = dict(request_id='one',purpose='FIELD',provider='GEFS',
                   origin='https://fixture.example.invalid',path='/object',
                   range_start=0,range_end=3,reservation_bytes=8)
    response = OfflineResponse(206,(('Content-Length','4'),('Content-Range','bytes 0-3/100'),
                                   ('ETag',etag)),(b'AB',b'CD'),'8.8.8.8',True)
    return request,response


@pytest.mark.parametrize('etag',['*','not-quoted','"one", "two"'])
def test_defect_non_entity_tag_identity_accepted(etag):
    request,response = request_response(etag)
    assert verify_response(request,response,allowed_peer_ips=('8.8.8.8',),
                           expected_etag=etag,expected_object_bytes=100) == b'ABCD'


@pytest.mark.parametrize('failure',['write','fsync'])
def test_control_delivered_chunk_journal_failure_counted_and_held(tmp_path,monkeypatch,failure):
    journal = tmp_path/'journal'
    journal.mkdir(mode=0o700)
    request,response = request_response()
    with DurableBudget(journal,'a'*64,max_bytes=8) as budget:
        real_write,real_fsync = os.write,os.fsync
        def failing_write(fd,data):
            if fd == budget.fd and b'"op":"chunk"' in data:
                raise OSError('injected journal write')
            return real_write(fd,data)
        def failing_fsync(fd):
            if fd == budget.fd and budget.in_flight == 'one':
                raise OSError('injected journal fsync')
            return real_fsync(fd)
        with monkeypatch.context() as patch:
            patch.setattr(os,'write',failing_write if failure=='write' else real_write)
            patch.setattr(os,'fsync',failing_fsync if failure=='fsync' else real_fsync)
            with pytest.raises(LaunchContractError,match='DURABILITY_UNCERTAIN'):
                consume_synthetic_response(request,SyntheticExchange({'one':response}),budget,
                    started_monotonic=1,allowed_peer_ips=('8.8.8.8',),
                    expected_etag='"object"',expected_object_bytes=100)
        assert budget.failed and budget.in_flight == 'one'
        assert budget.received == 2 and budget.uncertain_received_bytes == 2
        with pytest.raises(LaunchContractError,match='DURABILITY_UNCERTAIN'):
            budget.reserve('two',1,started_monotonic=4)
    with DurableBudget(journal,'a'*64,max_bytes=8) as recovered:
        assert recovered.in_flight == 'one' and recovered.reserved == 8
        with pytest.raises(LaunchContractError,match='UNCERTAIN_REQUEST_HELD'):
            recovered.reserve('two',1,started_monotonic=4)
