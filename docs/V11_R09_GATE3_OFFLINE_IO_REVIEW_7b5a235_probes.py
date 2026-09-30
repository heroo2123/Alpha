"""Exact-7b5a235 independent repair acceptance probes; synthetic inputs only.
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
def test_rectangular_grid_and_invalid_endpoints(scan):
    ys, xs = (1 if scan & 64 else -1), (-1 if scan & 128 else 1)
    result = decode(grib(scan=scan), 33+ys*.25, -84+xs*.5)
    assert (result.kelvin,result.distance_km) == (293,0)
    with pytest.raises(LaunchContractError, match='FULL_FIELD_GRID_ENDPOINT'):
        decode(grib(scan=scan,swapped_endpoint=True),33+ys*.25,-84+xs*.5)


@pytest.mark.parametrize('scan',[0,64,128,192])
def test_control_square_grid_all_supported_scans(scan):
    ys, xs = (1 if scan & 64 else -1), (-1 if scan & 128 else 1)
    assert decode(grib(square=True,scan=scan),33+ys*.5,-84+xs*.5).kelvin == 293


def clock(utcs, monos, uncertainties):
    value = ClockSequence(boot_id='independent-boot', max_measurement_age_seconds=10)
    for phase,utc,mono,unc in zip(value.PHASES,utcs,monos,uncertainties):
        value.record(phase,MeasuredClock(utc,mono,unc,mono,'independent-boot','c'*64))
    return value


def test_receipt_upper_bound_after_decision_rejected():
    value = clock((100,100.1,100.2,100.3),(1,1.1,1.2,1.3),(1,1,.1,0))
    assert value.readings[1][1].utc_seconds + value.readings[1][1].uncertainty_seconds > 100.4
    assert not value.causal_before(100.4)
    assert value.causal_before(101.1)


def test_globally_inconsistent_clock_rejected():
    with pytest.raises(LaunchContractError,match='CLOCK_STEP_OR_REVERSAL'):
        clock((100,99,98,97),(1,1.1,1.2,1.3),(1,1,1,1))


def test_control_stable_clock_early_and_late_decisions():
    value = clock((100,101,102,103),(1,2,3,4),(.2,.2,.2,.2))
    assert value.causal_before(104)
    assert not value.causal_before(103)


def private_store(tmp_path):
    root = tmp_path/'store'
    root.mkdir(mode=0o700)
    (root/'objects').mkdir(mode=0o700)
    return root


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


@pytest.mark.parametrize('etag',['*','not-quoted','"one", "two"','W/"weak"','"bad\x7ftag"','"bad\ttag"','"bad\x00tag"','"bad\u0100tag"','"embedded"quote"'])
def test_non_entity_tag_identity_rejected(etag):
    request,response = request_response(etag)
    with pytest.raises(LaunchContractError,match='RESPONSE_OBJECT_IDENTITY'):
        verify_response(request,response,allowed_peer_ips=('8.8.8.8',),
                        expected_etag=etag,expected_object_bytes=100)


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


@pytest.mark.parametrize('etag',['""','"ordinary"','"back\\slash"','"\x80\xff!#~"'])
def test_strong_etag_controls(etag):
    req,res = request_response(etag)
    assert verify_response(req,res,allowed_peer_ips=('8.8.8.8',),
                           expected_etag=etag,expected_object_bytes=100) == b'ABCD'


@pytest.mark.parametrize('phase',range(4))
def test_every_phase_upper_bound_required(phase):
    uncertainties = [0,0,0,0]
    uncertainties[phase] = 1
    value = clock((100,100.125,100.25,100.375),(1,1.125,1.25,1.375),uncertainties)
    assert not value.causal_before(100.5)
    assert value.causal_before(101.375)


def refused(store,sha):
    with pytest.raises(LaunchContractError,match='OBJECT_DURABILITY_UNCERTAIN'):
        store.read(sha)
    with pytest.raises(LaunchContractError,match='OBJECT_DURABILITY_UNCERTAIN'):
        store.seal(b'another object')


@pytest.mark.parametrize('fault',['dir_first','dir_last','link_before','link_after',
                                  'unlink_before','unlink_after','cleanup_unlink','cleanup_fsync'])
def test_uncertain_store_fault_matrix(tmp_path,monkeypatch,fault):
    root = private_store(tmp_path)
    raw = b'independent publication fault'
    sha = hashlib.sha256(raw).hexdigest()
    with ImmutableObjectStore(root) as store:
        fsync,link,unlink = os.fsync,os.link,os.unlink
        calls = 0
        def bad_fsync(fd):
            nonlocal calls
            if fd == store.dir_fd:
                calls += 1
                if (fault == 'dir_first' and calls == 1 or
                    fault == 'dir_last' and calls == 2 or fault == 'cleanup_fsync'):
                    raise OSError('injected fsync')
            elif fault.startswith('cleanup_'):
                raise OSError('injected file fsync')
            return fsync(fd)
        def bad_link(*args,**kwargs):
            if fault == 'link_before':
                raise OSError('injected link before')
            result = link(*args,**kwargs)
            if fault == 'link_after':
                raise OSError('injected link after')
            return result
        def bad_unlink(*args,**kwargs):
            if fault in ('unlink_before','cleanup_unlink'):
                raise OSError('injected unlink before')
            result = unlink(*args,**kwargs)
            if fault == 'unlink_after':
                raise OSError('injected unlink after')
            return result
        with monkeypatch.context() as patch:
            patch.setattr(os,'fsync',bad_fsync)
            patch.setattr(os,'link',bad_link)
            patch.setattr(os,'unlink',bad_unlink)
            with pytest.raises(OSError,match='injected'):
                store.seal(raw)
        refused(store,sha)
    paths = list((root/'objects').iterdir())
    if fault != 'cleanup_fsync':
        assert paths and all(p.read_bytes() == raw for p in paths)
        before = {p.name:p.read_bytes() for p in paths}
        with ImmutableObjectStore(root) as reopened:
            refused(reopened,sha)
        assert {p.name:p.read_bytes() for p in paths} == before
    else:
        # No publication happened; the process is poisoned, but there is no
        # remaining object or original seal claim to recover after restart.
        assert not paths
        with ImmutableObjectStore(root) as reopened:
            assert reopened.read(reopened.seal(b'fresh')) == b'fresh'


@pytest.mark.parametrize('entry',['valid_object','orphan','fifo','symlink','directory'])
def test_all_nonempty_reopens_refused_without_cleanup(tmp_path,entry):
    root = private_store(tmp_path)
    sha = hashlib.sha256(b'good').hexdigest()
    path = root/'objects'/sha
    if entry == 'valid_object':
        with ImmutableObjectStore(root) as store:
            assert store.seal(b'good') == sha
            assert store.read(sha) == b'good'
    elif entry == 'orphan':
        (root/'objects'/'.tmp-unfamiliar').write_bytes(b'preserve')
    elif entry == 'fifo':
        os.mkfifo(path,0o600)
    elif entry == 'symlink':
        path.symlink_to(root/'absent')
    else:
        path.mkdir(mode=0o700)
    before = sorted(p.name for p in (root/'objects').iterdir())
    with ImmutableObjectStore(root) as reopened:
        refused(reopened,sha)
    assert sorted(p.name for p in (root/'objects').iterdir()) == before


def crash_worker(root,stage):
    with ImmutableObjectStore(root) as store:
        fsync,link,unlink = os.fsync,os.link,os.unlink
        count = 0
        def crash_fsync(fd):
            nonlocal count
            fsync(fd)
            if fd != store.dir_fd and stage == 'file':
                os._exit(31)
            if fd == store.dir_fd:
                count += 1
                if (stage == 'dir_first' and count == 1 or
                    stage == 'dir_last' and count == 2):
                    os._exit(31)
        def crash_link(*args,**kwargs):
            link(*args,**kwargs)
            if stage == 'link':
                os._exit(31)
        def crash_unlink(*args,**kwargs):
            unlink(*args,**kwargs)
            if stage == 'unlink':
                os._exit(31)
        os.fsync,os.link,os.unlink = crash_fsync,crash_link,crash_unlink
        store.seal(b'crash bytes')


@pytest.mark.parametrize('stage',['file','link','dir_first','unlink','dir_last'])
def test_process_exit_at_each_publication_boundary(tmp_path,stage):
    root = private_store(tmp_path)
    child = multiprocessing.get_context('fork').Process(target=crash_worker,args=(root,stage))
    child.start()
    try:
        child.join(5)
        assert child.exitcode == 31
    finally:
        if child.is_alive():
            child.terminate()
            child.join(5)
    assert list((root/'objects').iterdir())
    assert all(p.read_bytes() == b'crash bytes' for p in (root/'objects').iterdir())
    with ImmutableObjectStore(root) as reopened:
        refused(reopened,hashlib.sha256(b'crash bytes').hexdigest())


def nonregular_worker(root,kind,sender):
    with ImmutableObjectStore(root) as store:
        sha = store.seal(b'replace synthetic object')
        path = root/'objects'/sha
        path.unlink()
        if kind == 'fifo':
            os.mkfifo(path,0o600)
        elif kind == 'symlink':
            path.symlink_to('/dev/null')
        else:
            path.mkdir(mode=0o700)
        sender.send('ready')
        try:
            store.read(sha)
        except LaunchContractError as exc:
            sender.send(str(exc))


@pytest.mark.parametrize('kind',['fifo','symlink','directory'])
def test_nonregular_replacement_bounded(tmp_path,kind):
    root = private_store(tmp_path)
    ctx = multiprocessing.get_context('fork')
    receiver,sender = ctx.Pipe(duplex=False)
    child = ctx.Process(target=nonregular_worker,args=(root,kind,sender))
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


def test_duplicate_does_not_poison_successful_session(tmp_path):
    root = private_store(tmp_path)
    with ImmutableObjectStore(root) as store:
        sha = store.seal(b'first')
        with pytest.raises(LaunchContractError,match='OBJECT_ALREADY_EXISTS'):
            store.seal(b'first')
        assert store.read(sha) == b'first'
        assert store.read(store.seal(b'second')) == b'second'
        assert not list((root/'objects').glob('.tmp-*'))
