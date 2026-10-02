"""SYNTHETIC ONLY: A7 refusal and limit controls, never qualification evidence."""
from dataclasses import replace
import hashlib
from pathlib import Path
import subprocess
import sys

import pytest

from polymarket_scanner.v11.ecmwf_grib import sections
from test_v11_grib_fields import message, mutate, u
from test_v11_model_panel import ecmwf_bytes, request as fixture_request
from tools import v11_r09_gate3_a7_decoder as a7


def fixture(raw=None):
    raw = ecmwf_bytes() if raw is None else raw
    req = fixture_request(raw)
    identity = dict(provider=req.source.provider, model=req.source.model,
                    model_version=req.source.model_version,
                    dataset=req.source.dataset,
                    release_evidence_sha256=req.source.release_evidence_sha256,
                    initialized_at=req.initialized_at, step=req.step,
                    member=req.member,
                    grib_signature_sha256=req.grib_signature_sha256)
    native_files = {}
    if int.from_bytes(sections(raw)[5][9:11], 'big') == 42:
        import eccodeslib
        library = Path(eccodeslib.__file__).parent / 'lib64'
        native_files = {str(path): hashlib.sha256(path.read_bytes()).hexdigest()
                        for path in (library / 'libeccodes.so',
                                     library / 'libeccodes_memfs.so')}
    pins = a7.Pins(hashlib.sha256(a7.DECODER.read_bytes()).hexdigest(),
                   hashlib.sha256(a7.source_bytes(identity)).hexdigest(),
                   hashlib.sha256(raw).hexdigest(),
                    {n: hashlib.sha256(s).hexdigest() for n, s in
                    sections(raw).items() if n != 7}, 'SYNTHETIC', native_files)
    return raw, identity, pins


def invoke(raw, identity, pins, **kwargs):
    return a7.run(raw, request=identity, pins=pins, latitude=33.,
                  longitude=-84., **kwargs)


def test_synthetic_simple_field_runs_in_bounded_child(monkeypatch):
    monkeypatch.setattr(a7, '_available_memory', lambda: 8*1024**3)
    raw, identity, pins = fixture()
    result = invoke(raw, identity, pins)
    assert result['point']['kelvin'] == pytest.approx(290.)
    assert result['profile']['template'] == 0
    assert result['evidence_class'] == 'SYNTHETIC'
    assert result['cpu_seconds'] >= result['inner_cpu_seconds'] >= 0
    assert result['wall_seconds'] >= 0
    assert result['output_bytes'] <= a7.Bounds().output_bytes


@pytest.mark.parametrize('fault,reason', [
    ('build', 'A7_BUILD_MISMATCH'), ('source', 'A7_SOURCE_MISMATCH'),
    ('raw', 'A7_RAW_MISMATCH'), ('section', 'A7_SECTION_MISMATCH'),
    ('provider', 'A7_SOURCE_UNQUALIFIED'),
])
def test_independent_pins_and_provider_gate_refuse_before_worker(monkeypatch, fault, reason):
    raw, identity, pins = fixture()
    if fault == 'build': pins = replace(pins, decoder_sha256='0'*64)
    if fault == 'source': pins = replace(pins, source_sha256='0'*64)
    if fault == 'raw': pins = replace(pins, raw_sha256='0'*64)
    if fault == 'section': pins = replace(pins, section_sha256={**pins.section_sha256, 3: '0'*64})
    if fault == 'provider': identity['provider'] = 'NOAA_GEFS'
    monkeypatch.setattr(a7.subprocess, 'Popen', lambda *a, **k: pytest.fail('worker started'))
    with pytest.raises(a7.A7Refusal, match=reason): invoke(raw, identity, pins)


@pytest.mark.parametrize('fault,reason', [
    ('truncated', 'ECMWF_GRIB_ENVELOPE'),
    ('bitmap', 'ECMWF_BITMAP_DECODER_UNAVAILABLE'),
    ('count', 'ECMWF_GRID_POINT_BOUND'),
    ('packing_count', 'ECMWF_PACKED_COUNT_MISMATCH'),
    ('distance', 'PANEL_GRID_DISTANCE_BOUND'),
    ('complex', 'ECMWF_PACKING_DECODER_UNAVAILABLE'),
    ('jpeg', 'ECMWF_PACKING_DECODER_UNAVAILABLE'),
])
def test_structural_and_distance_refusals_precede_native(monkeypatch, fault, reason):
    raw, _, _ = fixture()
    if fault == 'truncated': raw = raw[:-1]
    if fault == 'bitmap': raw = mutate(raw, 6, 5, b'\x00')
    if fault == 'count': raw = mutate(raw, 3, 6, u(5, 4))
    if fault == 'packing_count': raw = mutate(raw, 5, 5, u(5, 4))
    if fault == 'complex': raw = mutate(raw, 5, 9, u(2, 2))
    if fault == 'jpeg': raw = mutate(raw, 5, 9, u(40, 2))
    _, identity, pins = fixture(raw) if fault != 'truncated' else fixture()
    if fault == 'truncated': pins = replace(pins, raw_sha256=hashlib.sha256(raw).hexdigest())
    monkeypatch.setattr(a7.subprocess, 'Popen', lambda *a, **k: pytest.fail('worker started'))
    with pytest.raises(a7.A7Refusal, match=reason):
        a7.run(raw, request=identity, pins=pins,
               latitude=0. if fault == 'distance' else 33., longitude=-84.)


def synthetic_ccsds():
    ec = pytest.importorskip('eccodes')
    raw = ecmwf_bytes()
    handle = ec.codes_new_from_message(raw)
    try:
        ec.codes_set(handle, 'packingType', 'grid_ccsds')
        return ec.codes_get_message(handle)
    finally:
        ec.codes_release(handle)


def test_synthetic_ccsds_full_decode_and_exact_reencode(monkeypatch):
    monkeypatch.setattr(a7, '_available_memory', lambda: 8*1024**3)
    raw, identity, pins = fixture(synthetic_ccsds())
    result = invoke(raw, identity, pins)
    assert result['profile']['template'] == 42
    assert result['point']['kelvin'] == pytest.approx(290.)


def test_ccsds_native_build_pin_refuses_before_worker(monkeypatch):
    raw, identity, pins = fixture(synthetic_ccsds())
    monkeypatch.setattr(a7.subprocess, 'Popen', lambda *a, **k: pytest.fail('worker started'))
    with pytest.raises(a7.A7Refusal, match='A7_NATIVE_BUILD_PIN_MISSING'):
        invoke(raw, identity, replace(pins, native_files_sha256={}))
    changed = dict(pins.native_files_sha256)
    changed[next(iter(changed))] = '0'*64
    with pytest.raises(a7.A7Refusal, match='A7_NATIVE_BUILD_MISMATCH'):
        invoke(raw, identity, replace(pins, native_files_sha256=changed))


def test_self_consistent_ccsds_truncation_and_native_absence(monkeypatch):
    monkeypatch.setattr(a7, '_available_memory', lambda: 8*1024**3)
    raw = synthetic_ccsds()
    s = sections(raw)
    s[7] = u(6, 4) + s[7][4:6]
    truncated = message(list(s.values()))
    bad, identity, pins = fixture(truncated)
    with pytest.raises(a7.A7Refusal, match='CCSDS_INTEGRITY'):
        invoke(bad, identity, pins)
    raw, identity, pins = fixture(raw)
    monkeypatch.setattr(a7.sys, 'executable', '/usr/bin/python3')
    with pytest.raises(a7.A7Refusal, match='CCSDS_DECODER_UNAVAILABLE'):
        invoke(raw, identity, pins)


def test_native_decode_failure_is_explicit(monkeypatch):
    ec = pytest.importorskip('eccodes')
    from polymarket_scanner.v11 import ecmwf_grib
    raw = synthetic_ccsds()
    def failed_values(_):
        raise RuntimeError('injected native failure')
    monkeypatch.setattr(ec, 'codes_get_values', failed_values)
    with pytest.raises(ecmwf_grib.EvidenceError, match='CCSDS_DECODE_FAILED'):
        ecmwf_grib._ccsds_values(raw, (0,), 4, 2, 2)


def test_capacity_refuses_before_worker(monkeypatch):
    raw, identity, pins = fixture()
    monkeypatch.setattr(a7, '_available_memory', lambda: 1)
    monkeypatch.setattr(a7.subprocess, 'Popen', lambda *a, **k: pytest.fail('worker started'))
    with pytest.raises(a7.A7Refusal, match='A7_HOST_HEADROOM'):
        invoke(raw, identity, pins)
    with pytest.raises(a7.A7Refusal, match='A7_BOUNDS'):
        invoke(raw, identity, pins, bounds=replace(a7.Bounds(), output_bytes=100))


@pytest.fixture
def complete_cgroup_view(monkeypatch):
    """Only these synthetic hierarchies are assumed complete, never the host."""
    monkeypatch.setattr(a7, '_available_memory', a7._visible_memory_headroom)


def _cgroup_memory(monkeypatch, *, member='/team/leaf', leaf=('max', 0),
                   ancestor=('max', 0), root=('max', 0), missing=None,
                   unreadable=None, mount_root='/'):
    """A synthetic mounted cgroup-v2 hierarchy; no host controls are changed."""
    files = {
        '/proc/meminfo': 'MemAvailable: 8388608 kB\n',
        '/proc/self/cgroup': f'0::{member}\n',
        '/proc/self/mountinfo':
            f'34 25 0:29 {mount_root} /sys/fs/cgroup rw - cgroup2 cgroup2 rw\n',
    }
    for name, values in (('', root), ('team', ancestor), ('team/leaf', leaf)):
        directory = '/sys/fs/cgroup' + ('/' + name if name else '')
        files[directory + '/memory.max'] = str(values[0]) + '\n'
        files[directory + '/memory.current'] = str(values[1]) + '\n'
    if missing:
        del files[missing]
    original = Path.read_text
    def read(path, *args, **kwargs):
        key = str(path)
        if key == unreadable:
            raise PermissionError(key)
        if key in files:
            return files[key]
        if key.startswith('/sys/fs/cgroup') or key.startswith('/proc/self/'):
            raise FileNotFoundError(key)
        return original(path, *args, **kwargs)
    monkeypatch.setattr(Path, 'read_text', read)
    return files


def test_nested_leaf_limit_controls_host_admission(monkeypatch, complete_cgroup_view):
    mib = 1024 ** 2
    _cgroup_memory(monkeypatch, leaf=(256*mib, 192*mib))
    assert a7._available_memory() == 64*mib
    raw, identity, pins = fixture()
    monkeypatch.setattr(a7.subprocess, 'Popen', lambda *a, **k: pytest.fail('worker started'))
    with pytest.raises(a7.A7Refusal, match='A7_HOST_HEADROOM'):
        invoke(raw, identity, pins)


def test_constrained_ancestor_and_unlimited_root(monkeypatch, complete_cgroup_view):
    mib = 1024 ** 2
    _cgroup_memory(monkeypatch, leaf=('max', 20*mib),
                   ancestor=(800*mib, 200*mib), root=('max', 500*mib))
    assert a7._available_memory() == 600*mib
    raw, identity, pins = fixture()
    monkeypatch.setattr(a7.subprocess, 'Popen', lambda *a, **k: pytest.fail('worker started'))
    with pytest.raises(a7.A7Refusal, match='A7_HOST_HEADROOM'):
        invoke(raw, identity, pins)


def test_unlimited_root_still_uses_host_memavailable(monkeypatch, complete_cgroup_view):
    _cgroup_memory(monkeypatch)
    assert a7._available_memory() == 8*1024**3


def test_mount_root_without_controller_files_is_valid(monkeypatch, complete_cgroup_view):
    _cgroup_memory(monkeypatch, missing='/sys/fs/cgroup/memory.max')
    original = Path.read_text
    def no_root_controller(path, *args, **kwargs):
        if str(path) == '/sys/fs/cgroup/memory.current':
            raise FileNotFoundError(str(path))
        return original(path, *args, **kwargs)
    monkeypatch.setattr(Path, 'read_text', no_root_controller)
    assert a7._available_memory() == 8*1024**3


@pytest.mark.parametrize('change', ['missing_mount', 'missing_member',
                                    'missing_controller', 'unreadable_controller',
                                    'hidden_ancestor'])
def test_unknown_cgroup_capacity_refuses_closed(monkeypatch, complete_cgroup_view, change):
    leaf_max = '/sys/fs/cgroup/team/leaf/memory.max'
    options = {}
    if change == 'missing_controller': options['missing'] = leaf_max
    if change == 'unreadable_controller': options['unreadable'] = leaf_max
    if change == 'hidden_ancestor': options['mount_root'] = '/team'
    _cgroup_memory(monkeypatch, **options)
    if change == 'missing_mount':
        original = Path.read_text
        monkeypatch.setattr(Path, 'read_text',
                            lambda path, *a, **k: '' if str(path) == '/proc/self/mountinfo'
                            else original(path, *a, **k))
    if change == 'missing_member':
        original = Path.read_text
        monkeypatch.setattr(Path, 'read_text',
                            lambda path, *a, **k: '' if str(path) == '/proc/self/cgroup'
                            else original(path, *a, **k))
    with pytest.raises(a7.A7Refusal, match='A7_HEADROOM_UNKNOWN'):
        a7._available_memory()
    raw, identity, pins = fixture()
    monkeypatch.setattr(a7.subprocess, 'Popen', lambda *a, **k: pytest.fail('worker started'))
    with pytest.raises(a7.A7Refusal, match='A7_HEADROOM_UNKNOWN'):
        invoke(raw, identity, pins)


def test_exact_memory_threshold_admits_and_one_byte_less_refuses(monkeypatch, complete_cgroup_view):
    raw, identity, pins = fixture()
    required = a7.Bounds().memory_bytes + a7.Bounds().memory_headroom_bytes
    files = _cgroup_memory(monkeypatch, leaf=(required + 1, 2))
    monkeypatch.setattr(a7.os, 'statvfs', lambda _: type('Stat', (),
                        {'f_bavail': 1024**3, 'f_frsize': 1})())
    monkeypatch.setattr(a7.subprocess, 'Popen',
                        lambda *a, **k: (_ for _ in ()).throw(OSError('injected')))
    with pytest.raises(a7.A7Refusal, match='A7_HOST_HEADROOM'):
        invoke(raw, identity, pins)
    files['/sys/fs/cgroup/team/leaf/memory.current'] = '1\n'
    with pytest.raises(a7.A7Refusal, match='A7_PROCESS_START_FAILED'):
        invoke(raw, identity, pins)


def test_cpu_includes_child_work_after_worker_measurement(monkeypatch):
    raw, identity, pins = fixture()
    monkeypatch.setattr(a7, '_available_memory', lambda: 8*1024**3)
    real_popen = subprocess.Popen
    script = ('import json,time\n'
              'print(json.dumps(dict(ok=True,point={},error=None,'
              'inner_cpu_seconds=0.0)),flush=True)\n'
              'start=time.process_time()\n'
              'while time.process_time()-start < 0.15: pass\n')
    def delayed_popen(command, **kwargs):
        return real_popen([sys.executable, '-I', '-B', '-c', script], **kwargs)
    monkeypatch.setattr(a7.subprocess, 'Popen', delayed_popen)
    result = invoke(raw, identity, pins)
    assert result['inner_cpu_seconds'] == 0
    assert result['cpu_seconds'] >= 0.15


def test_worker_start_failure_refuses(monkeypatch):
    raw, identity, pins = fixture()
    monkeypatch.setattr(a7, '_available_memory', lambda: 8*1024**3)
    def unavailable(*args, **kwargs):
        raise OSError('injected process failure')
    monkeypatch.setattr(a7.subprocess, 'Popen', unavailable)
    with pytest.raises(a7.A7Refusal, match='A7_PROCESS_START_FAILED'):
        invoke(raw, identity, pins)


@pytest.mark.parametrize('script,bounds,reason', [
    ('import time;time.sleep(30)', replace(a7.Bounds(), cpu_seconds=1,
                                            wall_seconds=2), 'A7_WALL_LIMIT'),
    ('while True: pass', replace(a7.Bounds(), cpu_seconds=1,
                                  wall_seconds=20), 'A7_NATIVE_OR_RESOURCE_FAILURE'),
    ('import os;os.write(1,b"x"*100000)',
     replace(a7.Bounds(), output_bytes=1024), 'A7_OUTPUT_LIMIT'),
    ('x=bytearray(300000000)', replace(a7.Bounds(), memory_bytes=128*1024**2),
     'A7_NATIVE_OR_RESOURCE_FAILURE'),
])
def test_child_process_limits_refuse_adversarial_worker(monkeypatch, script, bounds, reason):
    raw, identity, pins = fixture()
    monkeypatch.setattr(a7, '_available_memory', lambda: 8*1024**3)
    real_popen = subprocess.Popen
    def adversarial_popen(command, **kwargs):
        return real_popen([sys.executable, '-I', '-B', '-c', script], **kwargs)
    monkeypatch.setattr(a7.subprocess, 'Popen', adversarial_popen)
    with pytest.raises(a7.A7Refusal, match=reason):
        invoke(raw, identity, pins, bounds=bounds)


@pytest.mark.parametrize('member', ['/', '/team/leaf'])
@pytest.mark.parametrize('root_controls', [True, False])
def test_namespace_root_never_proves_complete_capacity(monkeypatch, member, root_controls):
    # Both self and PID 1 may be inside the same cgroup namespace. A hidden
    # ancestor has limit 256 MiB, current 192 MiB, despite this visible 8 GiB.
    files = _cgroup_memory(monkeypatch, member=member)
    files['/proc/1/cgroup'] = files['/proc/self/cgroup']
    files['/proc/1/mountinfo'] = files['/proc/self/mountinfo']
    if not root_controls:
        del files['/sys/fs/cgroup/memory.max']
        del files['/sys/fs/cgroup/memory.current']
    assert a7._visible_memory_headroom() == 8*1024**3
    with pytest.raises(a7.A7Refusal, match='^A7_HEADROOM_UNKNOWN$'):
        a7._available_memory()
    raw, identity, pins = fixture()
    monkeypatch.setattr(a7.subprocess, 'Popen', lambda *a, **k: pytest.fail('worker started'))
    with pytest.raises(a7.A7Refusal, match='^A7_HEADROOM_UNKNOWN$'):
        invoke(raw, identity, pins)


@pytest.mark.parametrize('race', ['live', 'exited', 'signal_esrch'])
def test_timeout_has_one_reaper_and_complete_usage(monkeypatch, race):
    import os
    import signal
    raw, identity, pins = fixture()
    monkeypatch.setattr(a7, '_available_memory', lambda: 8*1024**3)
    real_popen, real_wait4, real_kill = subprocess.Popen, os.wait4, os.kill
    children, reaped = [], []

    def popen(command, **kwargs):
        script = 'import time;time.sleep(1.1)' if race != 'live' else 'import time;time.sleep(30)'
        proc = real_popen([sys.executable, '-I', '-B', '-c', script], **kwargs)
        children.append(proc)
        # Calling any Popen reaping helper would recreate the original defect.
        for name in ('kill', 'send_signal', 'poll', 'wait'):
            monkeypatch.setattr(proc, name, lambda *a, **k: pytest.fail('second reaping owner'))
        return proc

    def kill(pid, sig):
        assert sig == signal.SIGKILL
        if race != 'live':
            # Observe exit without reaping: exact exit-between-wait4-and-kill race.
            os.waitid(os.P_PID, pid, os.WEXITED | os.WNOWAIT)
        if race == 'signal_esrch':
            raise ProcessLookupError('injected ESRCH after exit')
        return real_kill(pid, sig)

    def wait4(pid, options):
        result = real_wait4(pid, options)
        if result[0]:
            reaped.append(result)
        return result

    monkeypatch.setattr(a7.subprocess, 'Popen', popen)
    monkeypatch.setattr(a7.os, 'kill', kill)
    monkeypatch.setattr(a7.os, 'wait4', wait4)
    with pytest.raises(a7.A7Refusal, match='^A7_WALL_LIMIT$') as caught:
        invoke(raw, identity, pins, bounds=replace(a7.Bounds(), cpu_seconds=1, wall_seconds=1))
    assert len(children) == len(reaped) == 1
    proc = children[0]
    assert proc.returncode == (-signal.SIGKILL if race == 'live' else 0)
    assert caught.value.child_usage == reaped[0][2]
    assert caught.value.child_usage.ru_utime + caught.value.child_usage.ru_stime > 0
    assert caught.value.child_usage.ru_maxrss > 0
    with pytest.raises(ChildProcessError):
        real_wait4(proc.pid, os.WNOHANG)


def test_already_exited_child_at_deadline_still_reports_wall_refusal():
    import os
    proc = subprocess.Popen([sys.executable, '-I', '-B', '-c', 'pass'])
    os.waitid(os.P_PID, proc.pid, os.WEXITED | os.WNOWAIT)
    with pytest.raises(a7.A7Refusal, match='^A7_WALL_LIMIT$') as caught:
        a7._wait_with_usage(proc, 0)
    assert proc.returncode == 0
    assert caught.value.child_usage.ru_maxrss > 0
    with pytest.raises(ChildProcessError):
        os.wait4(proc.pid, os.WNOHANG)


@pytest.mark.parametrize('phase', ['serialization', 'shutdown'])
def test_rss_includes_real_worker_serialization_and_shutdown(monkeypatch, phase):
    import os
    raw, identity, pins = fixture()
    monkeypatch.setattr(a7, '_available_memory', lambda: 8*1024**3)
    real_popen, real_wait4 = subprocess.Popen, os.wait4
    reaped = []
    # Retain the real decoder and output, injecting resident memory only after
    # decode. Read the earlier high-water mark independently from child output.
    script = (
        'import sys,resource,atexit\n'
        'sys.path.insert(0,sys.argv[1])\n'
        'from tools import v11_r09_gate3_a7_decoder as a\n'
        'dump=a.json.dumps\n'
        'def allocate():\n'
        ' a._test_allocation=bytearray(96*1024**2)\n'
        'def serialize(value,*args,**kwargs):\n'
        ' if isinstance(value,dict) and "inner_cpu_seconds" in value:\n'
        '  value["point"]["rss_before_serialization"]=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss*1024\n'
        + ('  allocate()\n' if phase == 'serialization' else '  atexit.register(allocate)\n') +
        ' return dump(value,*args,**kwargs)\n'
        'a.json.dumps=serialize\n'
        'a.worker_main()\n'
    )

    def popen(command, **kwargs):
        return real_popen([sys.executable, '-I', '-B', '-c', script, str(a7.ROOT)], **kwargs)

    def wait4(pid, options):
        result = real_wait4(pid, options)
        if result[0]:
            reaped.append(result)
        return result

    monkeypatch.setattr(a7.subprocess, 'Popen', popen)
    monkeypatch.setattr(a7.os, 'wait4', wait4)
    result = invoke(raw, identity, pins)
    assert result['point']['kelvin'] == pytest.approx(290.)
    assert len(reaped) == 1
    usage = reaped[0][2]
    assert result['max_rss_bytes'] == usage.ru_maxrss * 1024
    assert result['cpu_seconds'] == usage.ru_utime + usage.ru_stime
    assert result['max_rss_bytes'] > result['point']['rss_before_serialization']
    assert result['max_rss_bytes'] >= 96*1024**2
