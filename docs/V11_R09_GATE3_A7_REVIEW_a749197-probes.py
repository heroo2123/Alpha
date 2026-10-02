import hashlib
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import time
from unittest.mock import patch

sys.dont_write_bytecode = True
ROOT = Path('/tmp/alpha-v11-gate3-a7-review-a749197')
os.chdir(ROOT)
sys.path[:0] = [str(ROOT / 'tests'), str(ROOT)]
from test_v11_r09_gate3_a7_decoder import fixture, invoke
from tools import v11_r09_gate3_a7_decoder as a7

original_connect = socket.socket.connect
def offline_connect(self, address):
    if self.family in (socket.AF_INET, socket.AF_INET6):
        raise RuntimeError('offline probe forbids network')
    return original_connect(self, address)
socket.socket.connect = offline_connect

results = {}
files = {
    '/proc/meminfo': 'MemAvailable: 8388608 kB\n',
    '/proc/self/cgroup': '0::/\n',
    '/proc/self/mountinfo': '34 25 0:29 / /sys/fs/cgroup rw - cgroup2 cgroup2 rw\n',
    '/sys/fs/cgroup/memory.max': 'max\n',
    '/sys/fs/cgroup/memory.current': '0\n',
}
original_read = Path.read_text
def view_read(path, *args, **kwargs):
    key = str(path)
    if key in files:
        return files[key]
    return original_read(path, *args, **kwargs)
raw, identity, pins = fixture()
with patch.object(Path, 'read_text', view_read):
    visible = a7._visible_memory_headroom()
    try:
        a7._available_memory()
    except a7.A7Refusal as exc:
        admission = str(exc)
    else:
        raise AssertionError('hidden-ancestor view admitted')
    with patch.object(a7.subprocess, 'Popen', side_effect=AssertionError('worker spawned')):
        try:
            invoke(raw, identity, pins)
        except a7.A7Refusal as exc:
            run_admission = str(exc)
        else:
            raise AssertionError('run admitted hidden-ancestor view')
assert visible == 8 * 1024**3 and admission == run_admission == 'A7_HEADROOM_UNKNOWN'
results['namespace_root'] = {'visible_diagnostic_bytes': visible, 'modeled_hidden_effective_bytes': 64 * 1024**2, 'direct_refusal': admission, 'run_refusal': run_admission, 'worker_started': False}

# Force the child to exit after a nonblocking wait reports it live, before SIGKILL.
for mode in ('exited_before_kill', 'esrch_after_exit'):
    proc = subprocess.Popen([sys.executable, '-I', '-B', '-c', 'import time;time.sleep(0.08)'])
    original_kill = os.kill
    sends = []
    def raced_kill(pid, sig):
        assert pid == proc.pid and sig == signal.SIGKILL
        sends.append(sig)
        os.waitid(os.P_PID, pid, os.WEXITED | os.WNOWAIT)
        if mode == 'esrch_after_exit':
            raise ProcessLookupError('injected ESRCH')
        return original_kill(pid, sig)
    try:
        with patch.object(a7.os, 'kill', raced_kill):
            try:
                a7._wait_with_usage(proc, 0.01)
            except a7.A7Refusal as exc:
                assert str(exc) == 'A7_WALL_LIMIT'
                usage = exc.child_usage
            else:
                raise AssertionError('deadline admitted')
        assert proc.returncode == 0 and usage.ru_maxrss > 0 and len(sends) == 1
        try:
            os.wait4(proc.pid, os.WNOHANG)
        except ChildProcessError:
            reaped_once = True
        else:
            raise AssertionError('child remained reappable')
        results[mode] = {'reason': 'A7_WALL_LIMIT', 'returncode': proc.returncode, 'rss_kib': usage.ru_maxrss, 'cpu_seconds': usage.ru_utime + usage.ru_stime, 'single_reap': reaped_once}
    finally:
        if proc.returncode is None:
            original_kill(proc.pid, signal.SIGKILL)
            os.waitpid(proc.pid, 0)

# Independently replace only the worker command. It emits valid output and then
# consumes CPU and memory, so the parent must count work after serialization.
real_popen = subprocess.Popen
real_wait4 = os.wait4
reaped = []
script = ('import json,time\n'
          'print(json.dumps({"ok":True,"point":{},"inner_cpu_seconds":0.0,"error":None}),flush=True)\n'
          'hold=bytearray(64*1024**2)\n'
          'start=time.process_time()\n'
          'while time.process_time()-start<0.12: hold[0]=1\n')
def replacement_popen(command, **kwargs):
    return real_popen([sys.executable, '-I', '-B', '-c', script], **kwargs)
def capture_wait4(pid, options):
    result = real_wait4(pid, options)
    if result[0]: reaped.append(result)
    return result
with patch.object(a7, '_available_memory', return_value=8*1024**3), patch.object(a7.subprocess, 'Popen', replacement_popen), patch.object(a7.os, 'wait4', capture_wait4):
    measured = invoke(raw, identity, pins)
assert len(reaped) == 1
usage = reaped[0][2]
assert measured['cpu_seconds'] == usage.ru_utime + usage.ru_stime
assert measured['max_rss_bytes'] == usage.ru_maxrss * 1024
assert measured['cpu_seconds'] >= 0.12 and measured['max_rss_bytes'] >= 64*1024**2
results['post_output_resources'] = {'full_child_cpu_seconds': measured['cpu_seconds'], 'full_child_peak_rss_bytes': measured['max_rss_bytes'], 'independent_wait4_cpu_seconds': usage.ru_utime + usage.ru_stime, 'independent_wait4_peak_rss_bytes': usage.ru_maxrss*1024}

# A delayed Popen return must consume the wall budget even if its child exits.
def delayed_popen(command, **kwargs):
    proc = real_popen([sys.executable, '-I', '-B', '-c', 'pass'], **kwargs)
    time.sleep(1.1)
    return proc
with patch.object(a7, '_available_memory', return_value=8*1024**3), patch.object(a7.subprocess, 'Popen', delayed_popen):
    try:
        invoke(raw, identity, pins, bounds=a7.Bounds(cpu_seconds=1, wall_seconds=1))
    except a7.A7Refusal as exc:
        assert str(exc) == 'A7_WALL_LIMIT' and exc.child_usage is not None
        results['startup_deadline'] = {'reason': str(exc), 'child_usage_retained': True}
    else:
        raise AssertionError('process-start time was not charged')

path = Path('/tmp/alpha-v11-gate3-a7-review-a749197-probes.json')
path.write_text(json.dumps(results, indent=2, sort_keys=True) + '\n')
print(path.read_text())
