"""Offline A7 decoder qualification runner; no launch or provider interface.

Pins must come from an independent frozen dossier. Fixture-derived pins in tests
exercise refusal only. This runner does not establish A2-A6 or authorize use.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
import os
from pathlib import Path
import re
import resource
import signal
import subprocess
import sys
import tempfile
import time
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
DECODER = ROOT / 'polymarket_scanner/v11/ecmwf_grib.py'
MAX_INPUT = 4 * 1024 * 1024


class A7Refusal(ValueError):
    def __init__(self, reason, *, child_usage=None):
        super().__init__(reason)
        # Timeout refusals retain the exact reaped child's complete accounting.
        self.child_usage = child_usage


def _require(condition, reason):
    if not condition:
        raise A7Refusal(reason)


def _sha(data):
    return hashlib.sha256(data).hexdigest()


def _hex(value):
    return type(value) is str and len(value) == 64 and all(c in '0123456789abcdef' for c in value)


@dataclass(frozen=True)
class Bounds:
    cpu_seconds: int = 15
    wall_seconds: int = 20
    memory_bytes: int = 512 * 1024 * 1024
    output_bytes: int = 16 * 1024
    memory_headroom_bytes: int = 128 * 1024 * 1024
    disk_headroom_bytes: int = 16 * 1024 * 1024

    def validate(self):
        _require(all(type(x) is int for x in vars(self).values()) and
                 1 <= self.cpu_seconds <= 60 and 1 <= self.wall_seconds <= 90 and
                 self.cpu_seconds <= self.wall_seconds and
                 128 * 1024**2 <= self.memory_bytes <= 2 * 1024**3 and
                 1024 <= self.output_bytes <= 65536 and
                 0 <= self.memory_headroom_bytes <= 2 * 1024**3 and
                 0 <= self.disk_headroom_bytes <= 2 * 1024**3, 'A7_BOUNDS')


@dataclass(frozen=True)
class Pins:
    # These hashes are supplied externally. The runner never derives authority
    # from the candidate field or from a matching package version.
    decoder_sha256: str
    source_sha256: str
    raw_sha256: str
    section_sha256: dict[int, str]
    evidence_class: str
    native_files_sha256: dict[str, str] = field(default_factory=dict)

    def validate(self):
        _require(all(_hex(x) for x in (self.decoder_sha256, self.source_sha256,
                                     self.raw_sha256)) and
                 type(self.section_sha256) is dict and
                 set(self.section_sha256) in ({1, 3, 4, 5, 6}, {1, 2, 3, 4, 5, 6}) and
                 all(_hex(x) for x in self.section_sha256.values()) and
                 type(self.native_files_sha256) is dict and
                 len(self.native_files_sha256) <= 32 and
                 all(type(path) is str and path.startswith('/') and _hex(sha)
                     for path, sha in self.native_files_sha256.items()) and
                 self.evidence_class in ('RETAINED_REAL', 'RETAINED_UNVERIFIED',
                                         'SYNTHETIC'), 'A7_PINS')


def source_bytes(request):
    """Canonical narrow request identity; the dossier must pin these bytes."""
    _require(type(request) is dict and set(request) ==
             {'provider', 'model', 'initialized_at', 'step', 'member',
              'grib_signature_sha256', 'model_version', 'dataset',
              'release_evidence_sha256'}, 'A7_SOURCE_SHAPE')
    provider, model = request['provider'], request['model']
    _require((provider, model) in (('ECMWF_IFS_ENS', 'ifs'),
                                   ('ECMWF_AIFS_ENS', 'aifs-ens')) and
             type(request['initialized_at']) in (int, float) and
             0 < request['initialized_at'] < 4102444800 and
             type(request['step']) is int and 0 <= request['step'] <= 360 and
             type(request['member']) is int and 0 <= request['member'] <= 50 and
             type(request['model_version']) is str and
             1 <= len(request['model_version']) <= 128 and
             request['model_version'].lower() not in ('latest', 'unknown', 'current') and
             request['dataset'] == 'ecmwf-open-data:0p25' and
             _hex(request['release_evidence_sha256']) and
             _hex(request['grib_signature_sha256']), 'A7_SOURCE_UNQUALIFIED')
    return json.dumps(request, sort_keys=True, separators=(',', ':'),
                      allow_nan=False).encode()


def _verify_decoder(pin):
    _require(_sha(DECODER.read_bytes()) == pin, 'A7_BUILD_MISMATCH')


def _verify_native(pins):
    _require(any(Path(path).name == 'libeccodes.so' for path in
                 pins.native_files_sha256), 'A7_NATIVE_BUILD_PIN_MISSING')
    try:
        matched = all(Path(path).is_file() and Path(path).resolve() == Path(path) and
                      _sha(Path(path).read_bytes()) == expected
                      for path, expected in pins.native_files_sha256.items())
    except OSError:
        matched = False
    _require(matched, 'A7_NATIVE_BUILD_MISMATCH')


def _available_memory():
    """Refuse admission until complete effective hierarchy visibility is proven.

    This offline candidate has no trusted deployment/namespace anchor. Neither
    a procfs/mountinfo '/' nor comparison with a possibly namespaced PID 1
    proves that all effective ancestors are visible. There is deliberately no
    caller flag, environment override, or namespace-inode allowlist to bypass
    this refusal. A future reviewed integration must supply that boundary.
    """
    raise A7Refusal('A7_HEADROOM_UNKNOWN')


def _visible_memory_headroom():
    """Diagnostic snapshot ONLY; hidden namespace ancestors may be tighter.

    This is not an admission source. Tests may explicitly substitute it for
    _available_memory when modeling a known complete synthetic hierarchy.
    """
    try:
        matches = [line.split() for line in Path('/proc/meminfo').read_text().splitlines()
                   if line.startswith('MemAvailable:')]
        _require(len(matches) == 1 and len(matches[0]) == 3 and
                 matches[0][2] == 'kB', 'A7_HEADROOM_UNKNOWN')
        available = int(matches[0][1]) * 1024
        _require(available >= 0, 'A7_HEADROOM_UNKNOWN')

        members = [line.split(':', 2)[2] for line in
                   Path('/proc/self/cgroup').read_text().splitlines()
                   if line.startswith('0::')]
        _require(len(members) == 1, 'A7_HEADROOM_UNKNOWN')
        member = members[0]
        _require(member.startswith('/') and '..' not in Path(member).parts,
                 'A7_HEADROOM_UNKNOWN')

        # mountinfo escapes spaces and other special characters as octal bytes.
        def unescape(value):
            return re.sub(r'\\([0-7]{3})', lambda match: chr(int(match[1], 8)), value)

        mounts = []
        for line in Path('/proc/self/mountinfo').read_text().splitlines():
            before, separator, after = line.partition(' - ')
            if separator and after.split()[0] == 'cgroup2':
                fields = before.split()
                _require(len(fields) >= 5, 'A7_HEADROOM_UNKNOWN')
                root, mountpoint = map(unescape, fields[3:5])
                if root == '/' or member == root or member.startswith(root.rstrip('/') + '/'):
                    mounts.append((root, mountpoint))
        _require(len(mounts) == 1, 'A7_HEADROOM_UNKNOWN')
        root, mountpoint = mounts[0]
        # Reject visibly truncated mounts; '/' still does not prove completeness.
        _require(root == '/' and mountpoint.startswith('/') and
                 '..' not in Path(mountpoint).parts, 'A7_HEADROOM_UNKNOWN')
        directory = Path(mountpoint) / member.lstrip('/')
        mount = Path(mountpoint)
        while True:
            try:
                maximum = (directory / 'memory.max').read_text().strip()
            except FileNotFoundError:
                maximum = None
            try:
                current_text = (directory / 'memory.current').read_text().strip()
            except FileNotFoundError:
                current_text = None
            # The v2 mount root itself normally has no memory controller files.
            if directory == mount and maximum is None and current_text is None:
                break
            _require(maximum is not None and current_text is not None,
                     'A7_HEADROOM_UNKNOWN')
            current = int(current_text)
            _require(current >= 0, 'A7_HEADROOM_UNKNOWN')
            if maximum != 'max':
                limit = int(maximum)
                _require(limit >= 0, 'A7_HEADROOM_UNKNOWN')
                available = min(available, max(0, limit - current))
            if directory == mount:
                break
            directory = directory.parent
        return available
    except (OSError, ValueError, IndexError):
        raise A7Refusal('A7_HEADROOM_UNKNOWN') from None


def _wait_with_usage(proc, timeout):
    """Sole reaping owner: return complete per-child rusage, even on exit races."""
    deadline = time.monotonic() + timeout
    while True:
        pid, status, usage = os.wait4(proc.pid, os.WNOHANG)
        remaining = deadline - time.monotonic()
        if pid:
            proc.returncode = os.waitstatus_to_exitcode(status)
            if remaining <= 0:
                raise A7Refusal('A7_WALL_LIMIT', child_usage=usage)
            return usage
        if remaining <= 0:
            # Popen.kill/send_signal/poll can reap an already exited child,
            # losing wait4's rusage. Signal directly; an unreaped child keeps
            # its PID, so exit between wait4 and kill cannot target a reused PID.
            try:
                os.kill(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass  # Exited before signal; wait4 still owns its accounting.
            _, status, usage = os.wait4(proc.pid, 0)
            proc.returncode = os.waitstatus_to_exitcode(status)
            raise A7Refusal('A7_WALL_LIMIT', child_usage=usage)
        time.sleep(min(remaining, 0.01))


def _preflight(raw, request, pins, latitude, longitude):
    pins.validate()
    _verify_decoder(pins.decoder_sha256)
    _require(type(raw) is bytes and 0 < len(raw) <= MAX_INPUT, 'A7_INPUT_BOUND')
    _require(_sha(raw) == pins.raw_sha256, 'A7_RAW_MISMATCH')
    _require(_sha(source_bytes(request)) == pins.source_sha256,
             'A7_SOURCE_MISMATCH')
    _require(type(latitude) in (int, float) and type(longitude) in (int, float),
             'A7_TARGET')
    # Import follows the reviewed decoder-byte check. A4 must additionally
    # protect its complete transitive import/native closure at point of use.
    from polymarket_scanner.v11.ecmwf_grib import (HistoricalPointTarget,
                                                    preflight_stations, sections)
    from polymarket_scanner.v11.evidence import EvidenceError
    try:
        section_map = sections(raw)
        _require(set(section_map) - {7} == set(pins.section_sha256) and
                 all(_sha(section_map[n]) == expected
                     for n, expected in pins.section_sha256.items()),
                 'A7_SECTION_MISMATCH')
        target = HistoricalPointTarget(latitude, longitude)
        source = SimpleNamespace(model=request['model'])
        req = SimpleNamespace(source=source, initialized_at=request['initialized_at'],
                              step=request['step'], member=request['member'],
                              grib_signature_sha256=request['grib_signature_sha256'])
        profile = preflight_stations(raw, request=req, targets=(target,))
        _require(profile['template'] in (0, 4, 42), 'A7_PACKING_UNQUALIFIED')
        if profile['template'] == 42:
            _verify_native(pins)
        return req, target, profile
    except EvidenceError as exc:
        raise A7Refusal(str(exc)) from None


def _child_limits(bounds):
    resource.setrlimit(resource.RLIMIT_CPU, (bounds.cpu_seconds, bounds.cpu_seconds))
    resource.setrlimit(resource.RLIMIT_AS, (bounds.memory_bytes, bounds.memory_bytes))
    resource.setrlimit(resource.RLIMIT_FSIZE, (bounds.output_bytes, bounds.output_bytes))
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))


def run(raw: bytes, *, request: dict, pins: Pins, latitude: float,
        longitude: float, bounds: Bounds = Bounds()):
    """Return measurements and point, or refuse; never an admission verdict."""
    _require(type(bounds) is Bounds and type(pins) is Pins, 'A7_INTERFACE')
    bounds.validate()
    _, _, profile = _preflight(raw, request, pins, latitude, longitude)
    stat = os.statvfs(tempfile.gettempdir())
    free_disk = stat.f_bavail * stat.f_frsize
    _require(_available_memory() >= bounds.memory_bytes + bounds.memory_headroom_bytes
             and free_disk >= 2 * MAX_INPUT + bounds.output_bytes +
             bounds.disk_headroom_bytes, 'A7_HOST_HEADROOM')
    payload = dict(raw=raw.hex(), request=request,
                   pins=dict(vars(pins), section_sha256={str(k): v for k, v in
                                                    pins.section_sha256.items()}),
                   latitude=latitude, longitude=longitude)
    with tempfile.TemporaryDirectory(prefix='a7-decoder-') as directory:
        in_path, out_path = Path(directory) / 'input', Path(directory) / 'output'
        in_path.write_text(json.dumps(payload, allow_nan=False))
        command = [sys.executable, '-I', '-B', '-c',
                   'import sys;sys.path.insert(0,sys.argv[1]);'
                   'from tools.v11_r09_gate3_a7_decoder import worker_main;worker_main()',
                   str(ROOT)]
        start = time.monotonic()
        with in_path.open('rb') as source, out_path.open('wb') as output:
            try:
                proc = subprocess.Popen(command, stdin=source, stdout=output,
                                        stderr=subprocess.DEVNULL, cwd=ROOT,
                                        env={'LANG': 'C', 'LC_ALL': 'C', 'TZ': 'UTC'},
                                        preexec_fn=lambda: _child_limits(bounds))
            except (OSError, subprocess.SubprocessError):
                raise A7Refusal('A7_PROCESS_START_FAILED') from None
            usage = _wait_with_usage(
                proc, max(0, bounds.wall_seconds - (time.monotonic() - start)))
        cpu = usage.ru_utime + usage.ru_stime
        max_rss = usage.ru_maxrss * 1024  # Linux wait4 reports KiB, through exit.
        wall = time.monotonic() - start
        if wall > bounds.wall_seconds:
            raise A7Refusal('A7_WALL_LIMIT', child_usage=usage)
        _require(proc.returncode == 0, 'A7_NATIVE_OR_RESOURCE_FAILURE')
        _require(out_path.stat().st_size < bounds.output_bytes,
                 'A7_OUTPUT_LIMIT')
        try:
            answer = json.loads(out_path.read_bytes())
        except (ValueError, UnicodeError):
            raise A7Refusal('A7_WORKER_OUTPUT') from None
        _require(type(answer) is dict and set(answer) ==
                 {'ok', 'point', 'inner_cpu_seconds', 'error'} and
                 type(answer['inner_cpu_seconds']) in (int, float), 'A7_WORKER_OUTPUT')
        if not answer['ok']:
            raise A7Refusal('A7_DECODE_REFUSED:' + str(answer['error'])[:120])
        _require(answer['error'] is None and type(answer['point']) is dict and
                 cpu <= bounds.cpu_seconds and
                 0 <= answer['inner_cpu_seconds'] <= cpu and
                 0 <= max_rss <= bounds.memory_bytes, 'A7_RESOURCE_LIMIT')
        return dict(point=answer['point'], profile=profile, wall_seconds=wall,
                    cpu_seconds=cpu,
                    inner_cpu_seconds=answer['inner_cpu_seconds'],
                    max_rss_bytes=max_rss,
                    output_bytes=out_path.stat().st_size,
                    evidence_class=pins.evidence_class)


def worker_main():
    """Internal isolated worker; stdin is only the bounded parent's fixture."""
    started = time.process_time()
    try:
        payload = json.load(sys.stdin)
        p = payload['pins']
        pins = Pins(p['decoder_sha256'], p['source_sha256'], p['raw_sha256'],
                    {int(k): v for k, v in p['section_sha256'].items()},
                    p['evidence_class'], p['native_files_sha256'])
        raw = bytes.fromhex(payload['raw'])
        req, target, _ = _preflight(raw, payload['request'], pins,
                                    payload['latitude'], payload['longitude'])
        from polymarket_scanner.v11.ecmwf_grib import decode_station
        point = decode_station(raw, request=req, target=target)
        ok, error = True, None
    except Exception as exc:
        point, ok, error = None, False, type(exc).__name__ + ':' + str(exc)[:100]
    answer = dict(ok=ok, point=point, error=error,
                  inner_cpu_seconds=time.process_time()-started)
    sys.stdout.write(json.dumps(answer, allow_nan=False, separators=(',', ':')))


if __name__ == '__main__':
    raise SystemExit('A7 runner is an offline library, not a launch entrypoint')
