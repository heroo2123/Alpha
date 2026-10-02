"""Independent offline exact-commit review; no candidate edits, network or launch."""
import sys
sys.dont_write_bytecode = True
from pathlib import Path
import contextlib
import hashlib
import io
import json
import os
import resource
import socket
import subprocess
import tempfile
import time
from unittest.mock import patch

ROOT = Path('/tmp/alpha-v11-gate3-a7-review-e08858b')
TERMINAL = Path('/tmp/alpha-v11-gate3-a7-review-e08858b_terminal.json')
sys.path[:0] = [str(ROOT), str(ROOT / 'tests')]
os.chdir(ROOT)
os.environ['PYTHONDONTWRITEBYTECODE'] = '1'
# -I ignores PYTHONDONTWRITEBYTECODE; add -B to Python children to preserve checkout.
original_popen = subprocess.Popen
def readonly_popen(command, *args, **kwargs):
    if isinstance(command, (list, tuple)) and 'python' in Path(command[0]).name:
        command = [command[0], '-B', *command[1:]]
    return original_popen(command, *args, **kwargs)
subprocess.Popen = readonly_popen
original_connect = socket.socket.connect
def offline_connect(self, address):
    if self.family in (socket.AF_INET, socket.AF_INET6):
        raise RuntimeError('Independent reviewer prohibits network')
    return original_connect(self, address)
socket.socket.connect = offline_connect

def sha(data):
    return hashlib.sha256(data).hexdigest()

def save(key, value):
    result = json.loads(TERMINAL.read_text()) if TERMINAL.exists() else {}
    result[key] = value
    TERMINAL.write_text(json.dumps(result, sort_keys=True, indent=2) + '\n')
    print(json.dumps({key: value}, sort_keys=True))

def tests(focused_only=False):
    import pytest
    log = io.StringIO()
    with tempfile.TemporaryDirectory(prefix='a7-review-tests-', dir='/tmp') as scratch:
        old = tempfile.tempdir
        tempfile.tempdir = scratch
        try:
            with contextlib.redirect_stdout(log), contextlib.redirect_stderr(log):
                code = pytest.main(['-q', '-p', 'no:cacheprovider', '--basetemp', scratch + '/pytest',
                    'tests/test_v11_r09_gate3_a7_decoder.py'] + ([] if focused_only else [
                    'tests/test_v11_model_panel.py', 'tests/test_v11_r09_gate3_offline_io.py']))
            size = sum(p.stat().st_size for p in Path(scratch).rglob('*') if p.is_file())
        finally:
            tempfile.tempdir = old
    output = log.getvalue()
    save('focused_retest' if focused_only else 'focused_adjacent_tests', dict(exit_code=int(code), output=output,
        output_sha256=sha(output.encode()), scratch_file_bytes_at_completion=size,
        scratch_removed=True, modifications='Python children receive -B; pytest cache disabled'))

def probes():
    from datetime import datetime, timezone
    from dataclasses import replace
    import eccodes
    import eccodeslib
    from tools import v11_r09_gate3_a7_decoder as a7
    from polymarket_scanner.v11 import ecmwf_grib as grib
    from test_v11_r09_gate3_a7_decoder import fixture, invoke, synthetic_ccsds
    from test_v11_model_panel import ecmwf_bytes
    from test_v11_grib_fields import mutate, u
    out = {}
    raw = Path('/tmp/aifs_cf.grib2').read_bytes()
    request = dict(provider='ECMWF_AIFS_ENS', model='aifs-ens',
        model_version='UNVERIFIED_LOCAL_OBSERVATION', dataset='ecmwf-open-data:0p25',
        release_evidence_sha256='0'*64,
        initialized_at=datetime(2026,9,29,tzinfo=timezone.utc).timestamp(),
        step=6, member=0, grib_signature_sha256=grib.release_signature(raw))
    library = Path(eccodeslib.__file__).parent / 'lib64'
    native = {str(p): sha(p.read_bytes()) for p in (library/'libeccodes.so', library/'libeccodes_memfs.so')}
    pins = a7.Pins(sha(a7.DECODER.read_bytes()), sha(a7.source_bytes(request)), sha(raw),
        {n: sha(s) for n,s in grib.sections(raw).items() if n != 7}, 'RETAINED_UNVERIFIED', native)
    start_cpu = resource.getrusage(resource.RUSAGE_CHILDREN)
    measurement = a7.run(raw, request=request, pins=pins, latitude=33.64, longitude=-84.43)
    end_cpu = resource.getrusage(resource.RUSAGE_CHILDREN)
    out['retained_field'] = dict(raw_sha256=sha(raw), raw_bytes=len(raw),
        decoder_sha256=pins.decoder_sha256, native=native, request=request,
        measurement=measurement, total_child_cpu_seconds=(end_cpu.ru_utime+end_cpu.ru_stime)-(start_cpu.ru_utime+start_cpu.ru_stime),
        python=sys.version, interpreter=sys.executable, eccodes=eccodes.codes_get_api_version(),
        scope='RETAINED_UNVERIFIED; derived diagnostic pins are not A2-A6 evidence')
    results = []
    for packing in (0,1,2):
        raw, identity, pin = fixture(ecmwf_bytes(packing=packing))
        result = invoke(raw, identity, pin)
        assert result['point']['kelvin'] == 290.0
        results.append(dict(packing=packing, result=result))
    out['simple_ieee'] = results
    raw, identity, pin = fixture(synthetic_ccsds())
    # A direct byte-mismatch test independent of truncation/native behavior.
    get_message = eccodes.codes_get_message
    with patch.object(eccodes, 'codes_get_message', side_effect=lambda h: get_message(h)[:-1]+b'X'):
        try:
            grib.decode_station(raw, request=a7._preflight(raw,identity,pin,33.,-84.)[0], target=grib.HistoricalPointTarget(33.,-84.))
        except grib.EvidenceError as exc:
            out['forced_exact_reencode_mismatch'] = str(exc)
        else:
            raise AssertionError('reencode mismatch accepted')
    # Guard proves the shared preflight does not enter native decode.
    with patch.object(grib, '_ccsds_values', side_effect=AssertionError('native called')):
        out['preflight_without_native'] = a7._preflight(raw, identity, pin, 33., -84.)[2]
    # Emulate a normal cgroup-v2 host hierarchy, with a finite current-leaf limit.
    actual_read = Path.read_text
    actual_exists = Path.exists
    reads = []
    fake = {'/proc/meminfo': 'MemAvailable: 8388608 kB\n',
        '/proc/self/cgroup': '0::/user.slice/review.scope\n',
        '/proc/self/mountinfo': '1 0 0:1 / /sys/fs/cgroup rw - cgroup2 cgroup rw\n',
        '/sys/fs/cgroup/user.slice/review.scope/memory.max': str(256*1024**2),
        '/sys/fs/cgroup/user.slice/review.scope/memory.current': str(192*1024**2)}
    def read(p,*args,**kwargs):
        reads.append(str(p))
        return fake[str(p)] if str(p) in fake else actual_read(p,*args,**kwargs)
    def exists(p):
        if str(p).startswith('/sys/fs/cgroup'):
            return str(p) in fake
        return actual_exists(p)
    with patch.object(Path,'read_text',read), patch.object(Path,'exists',exists):
        available = a7._available_memory()
    out['nested_cgroup_headroom'] = dict(reported_available=available,
        actual_leaf_headroom=64*1024**2, default_required=a7.Bounds().memory_bytes+a7.Bounds().memory_headroom_bytes,
        reads=reads, incorrectly_admits_default=available >= a7.Bounds().memory_bytes+a7.Bounds().memory_headroom_bytes)
    # Report the actual visible hierarchy without changing it.
    cgroup = Path('/proc/self/cgroup').read_text()
    actual = {'proc_self_cgroup': cgroup, 'runner_available': a7._available_memory(), 'limits':{}}
    leaf = next((s.split(':',2)[2] for s in cgroup.splitlines() if s.startswith('0::')), None)
    if leaf:
        p = Path('/sys/fs/cgroup') / leaf.lstrip('/')
        while str(p).startswith('/sys/fs/cgroup'):
            for name in ('memory.max','memory.current'):
                f = p/name
                if f.exists(): actual['limits'][str(f)] = f.read_text().strip()
            if p == Path('/sys/fs/cgroup'): break
            p = p.parent
    out['host_memory'] = actual
    out['extra_refusals'] = {}
    raw, identity, pin = fixture()
    for number in (2,40,255):
        bad = mutate(raw,5,9,u(number,2))
        b, ident, p = fixture(bad)
        with patch.object(a7.subprocess,'Popen',side_effect=AssertionError('worker started')):
            try: invoke(b,ident,p)
            except a7.A7Refusal as exc: out['extra_refusals'][str(number)] = str(exc)
            else: raise AssertionError('unknown packing accepted')
    save('independent_probes',out)

def bounds_probe():
    from tools import v11_r09_gate3_a7_decoder as a7
    candidate = a7.Bounds(cpu_seconds=60, wall_seconds=90,
        memory_bytes=2*1024**3, output_bytes=65536,
        memory_headroom_bytes=0, disk_headroom_bytes=0)
    candidate.validate()
    save('bounds_validation_probe', dict(accepted=True, bounds=vars(candidate),
        worker_started=False, purpose='Compare documented maxima/minima with accepted configuration'))

if __name__ == '__main__':
    {'tests': tests, 'focused': lambda: tests(True), 'probes': probes, 'bounds': bounds_probe}[sys.argv[1]]()
