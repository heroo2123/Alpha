"""Synthetic, fail-closed v1 immutable store with historical seal receipts.

This is an offline evidence interface. It has no provider I/O, clock attestation,
feature admission or budget authority. Filesystem rollback without an external head
is not detectable. No recovery path removes or repairs names.
"""
from __future__ import annotations

import base64
from dataclasses import dataclass, replace
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import secrets
import stat
import threading
import time
from typing import Callable

from tools.v11_r09_gate3_launch import LaunchContractError, check, digest

MAX_OBJECT = 4 * 1024 * 1024
MAX_JOURNAL = 64 * 1024 * 1024
MAX_RECORD = 64 * 1024
MAX_CLOCK_RAW = 16 * 1024
MAX_EVENTS = 10000
MAX_OBJECTS = 4096
REPORT_RESERVE = 64 * 1024
FLAGS = os.O_CLOEXEC | os.O_NOFOLLOW


def _bytes(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True,
                      allow_nan=False).encode('ascii')


def _pairs(items):
    out = {}
    for key, value in items:
        check(key not in out, 'JOURNAL_OR_IDENTITY_INVALID')
        out[key] = value
    return out


def _parse(raw: bytes):
    try:
        value = json.loads(raw, object_pairs_hook=_pairs,
                           parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
        check(_bytes(value) == raw, 'JOURNAL_OR_IDENTITY_INVALID')
        return value
    except (ValueError, TypeError, UnicodeError, OverflowError) as exc:
        raise LaunchContractError('JOURNAL_OR_IDENTITY_INVALID') from exc


def _hash(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _inventory_hash(receipts: dict[str, 'ObjectReceipt']) -> str:
    return _hash(_bytes(sorted((sha, receipt.length, receipt.commit_hash)
                               for sha, receipt in receipts.items())))


def _file(fd: int, name: str, *, limit: int, expected_size: int | None = None) -> bytes:
    """Inspect without following or blocking; compare O_PATH and data descriptors."""
    probe = os.open(name, os.O_PATH | FLAGS, dir_fd=fd)
    try:
        before = os.fstat(probe)
        check(stat.S_ISREG(before.st_mode) and before.st_uid == os.getuid() and
              stat.S_IMODE(before.st_mode) == 0o600 and before.st_nlink == 1 and
              0 <= before.st_size <= limit and
              (expected_size is None or before.st_size == expected_size),
              'OBJECT_FILE_IDENTITY')
        data_fd = os.open(name, os.O_RDONLY | os.O_NONBLOCK | FLAGS, dir_fd=fd)
        try:
            current = os.fstat(data_fd)
            check((before.st_dev, before.st_ino, before.st_size) ==
                  (current.st_dev, current.st_ino, current.st_size) and
                  stat.S_ISREG(current.st_mode), 'OBJECT_FILE_IDENTITY')
            out = bytearray()
            while len(out) <= before.st_size:
                part = os.read(data_fd, min(1024 * 1024, before.st_size + 1 - len(out)))
                if not part:
                    break
                out.extend(part)
            check(len(out) == before.st_size and
                  os.fstat(data_fd).st_size == before.st_size,
                  'OBJECT_DIGEST_MISMATCH')
            return bytes(out)
        finally:
            os.close(data_fd)
    finally:
        os.close(probe)


def _write_all(fd: int, raw: bytes):
    view = memoryview(raw)
    while view:
        count = os.write(fd, view)
        check(count > 0, 'STORE_SHORT_WRITE')
        view = view[count:]


def _names(fd: int, limit: int) -> set[str]:
    names = set()
    with os.scandir(fd) as entries:
        for entry in entries:
            check(len(names) < limit and len(entry.name) <= 255,
                  'STORE_NAMESPACE_CAPACITY')
            names.add(entry.name)
    return names


def _clock_to_dict(sample):
    from tools.v11_r09_gate3_offline_io import MeasuredClock
    check(type(sample) is ClockEvidence and type(sample.reading) is MeasuredClock and
          type(sample.raw) is bytes and 0 < len(sample.raw) <= MAX_CLOCK_RAW and
          type(sample.method) is str and 0 < len(sample.method) <= 128 and
          _hash(sample.raw) == sample.reading.evidence_sha256,
          'CLOCK_RAW_EVIDENCE')
    reading = sample.reading
    return {'phase': sample.phase, 'utc': reading.utc_seconds,
            'mono': reading.monotonic_seconds, 'uncertainty': reading.uncertainty_seconds,
            'measured_mono': reading.measured_monotonic_seconds,
            'boot': reading.boot_id, 'evidence_sha256': reading.evidence_sha256,
            'method': sample.method, 'raw': base64.b64encode(sample.raw).decode('ascii')}


def _clock_from_dict(value):
    from tools.v11_r09_gate3_offline_io import MeasuredClock
    check(type(value) is dict and set(value) == {'phase', 'utc', 'mono', 'uncertainty',
          'measured_mono', 'boot', 'evidence_sha256', 'method', 'raw'},
          'JOURNAL_OR_IDENTITY_INVALID')
    try:
        raw = base64.b64decode(value['raw'], validate=True)
    except (ValueError, TypeError) as exc:
        raise LaunchContractError('CLOCK_RAW_EVIDENCE') from exc
    reading = MeasuredClock(value['utc'], value['mono'], value['uncertainty'],
                            value['measured_mono'], value['boot'], value['evidence_sha256'])
    sample = ClockEvidence(value['phase'], reading, raw, value['method'])
    check(_clock_to_dict(sample) == value, 'CLOCK_RAW_EVIDENCE')
    return sample


def _validate_clocks(samples, *, complete: bool, max_age: int):
    from tools.v11_r09_gate3_offline_io import ClockSequence
    check(type(samples) in (tuple, list) and len(samples) == (4 if complete else 3),
          'CLOCK_PHASE_ORDER')
    check(sum(len(s.raw) for s in samples if type(s) is ClockEvidence) <= MAX_CLOCK_RAW,
          'CLOCK_RAW_EVIDENCE')
    for sample in samples:
        _clock_to_dict(sample)
    sequence = ClockSequence(boot_id=samples[0].reading.boot_id,
                             max_measurement_age_seconds=max_age)
    for sample in samples:
        sequence.record(sample.phase, sample.reading)
    return sequence


@dataclass(frozen=True)
class ClockEvidence:
    phase: str
    reading: object
    raw: bytes
    method: str


@dataclass(frozen=True)
class ObjectProvenance:
    kind: str
    request_id: str
    attempt_id: str
    source_pin: str
    decoder_pin: str
    clock_policy_sha256: str
    dependencies: tuple[str, ...] = ()

    def checked(self):
        check(self.kind in ('RAW', 'FEATURE_MANIFEST') and
              all(type(v) is str and 0 < len(v) <= 256 for v in
                  (self.request_id, self.attempt_id)) and
              type(self.dependencies) is tuple and
              all(type(v) is str for v in self.dependencies) and
              len(self.dependencies) == len(set(self.dependencies)) and
              len(self.dependencies) <= 256,
              'STORE_PROVENANCE_SHAPE')
        for item in (self.source_pin, self.decoder_pin, self.clock_policy_sha256,
                     *self.dependencies):
            digest(item, 'STORE_PROVENANCE_DIGEST')
        check((self.kind == 'FEATURE_MANIFEST') == bool(self.dependencies),
              'STORE_FEATURE_DEPENDENCIES')
        return self


@dataclass(frozen=True)
class ObjectReceipt:
    operation_id: str
    object_sha256: str
    length: int
    commit_hash: str
    clocks: tuple[ClockEvidence, ...]
    provenance: ObjectProvenance
    acknowledgement: str
    object_seal_witnessed: bool = True
    historical_feature_eligible: bool = False


@dataclass(frozen=True)
class RecoveryReport:
    classification: str
    reasons: tuple[str, ...]
    recovery_validated: bool
    rollback_assurance: str
    journal_head: str | None
    receipt_count: int


class VersionedImmutableObjectStore:
    """Exclusive v1 store. Construct only on disposable, externally pinned roots."""
    def __init__(self, root: Path, *, manifest_sha256: str, policy_sha256: str,
                 build_id: str, clock_method: str, max_clock_age_seconds: int,
                 host_id: str, boot_id: str, expected_head: tuple[int, str] | None = None,
                 expected_descriptor_sha256: str | None = None):
        self.root = Path(root)
        for value in (manifest_sha256, policy_sha256):
            digest(value, 'STORE_CONTEXT_DIGEST')
        check(type(build_id) is str and 0 < len(build_id) <= 128 and
              type(clock_method) is str and 0 < len(clock_method) <= 128 and
              type(host_id) is str and 0 < len(host_id) <= 128 and
              type(boot_id) is str and 0 < len(boot_id) <= 128 and
              type(max_clock_age_seconds) is int and 1 <= max_clock_age_seconds <= 3600,
              'STORE_CONTEXT_SHAPE')
        self.context = dict(manifest=manifest_sha256, policy=policy_sha256,
                            build=build_id, clock_method=clock_method,
                            max_clock_age=max_clock_age_seconds)
        self.host_id, self.boot_id = host_id, boot_id
        self.expected_head = expected_head
        if expected_descriptor_sha256 is not None:
            digest(expected_descriptor_sha256, 'STORE_DESCRIPTOR_PIN')
        self.expected_descriptor_sha256 = expected_descriptor_sha256
        self._pid = os.getpid()
        self._mutex = threading.RLock()
        self._callback = False
        self._failed = False
        self.root_fd = self.dir_fd = self.journal_fd = None
        self._metadata_identity = None
        self.receipts: dict[str, ObjectReceipt] = {}
        self._operations: set[str] = set()
        self._seq, self._head = 0, '0' * 64
        self._cross_boot = False
        self.report = RecoveryReport('RECOVERY_INCOMPLETE', (), False, 'UNAVAILABLE', None, 0)
        check(self.root.is_absolute() and self.root == self.root.resolve() and
              all(not p.is_symlink() for p in (self.root, *self.root.parents)),
              'OBJECT_ROOT_PATH')
        self.root_fd = os.open(self.root, os.O_RDONLY | os.O_DIRECTORY | FLAGS)
        try:
            fcntl.flock(self.root_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.root_identity = self._dir_identity(self.root_fd)
            self._check_root()
            self.dir_fd = os.open('objects', os.O_RDONLY | os.O_DIRECTORY | FLAGS,
                                  dir_fd=self.root_fd)
            self.objects_identity = self._dir_identity(self.dir_fd)
            self._check_dirs()
            os.register_at_fork(after_in_child=self._fork_child)
            self._open_or_initialize()
        except BaseException:
            self.close()
            raise

    def _fork_child(self):
        # Closing a copied open-file description does not unlock the parent.
        self._pid = -1
        for attr in ('journal_fd', 'dir_fd', 'root_fd'):
            fd = getattr(self, attr, None)
            if fd is not None:
                os.close(fd)
                setattr(self, attr, None)
        self._failed = True

    def _dir_identity(self, fd):
        st = os.fstat(fd)
        check(stat.S_ISDIR(st.st_mode) and st.st_uid == os.getuid() and
              stat.S_IMODE(st.st_mode) == 0o700, 'OBJECT_DIRECTORY_PRIVATE')
        return st.st_dev, st.st_ino

    def _check_root(self):
        check(os.getpid() == self._pid and self.root_fd is not None,
              'STORE_OWNER_PROCESS')
        path_fd = os.open(self.root, os.O_RDONLY | os.O_DIRECTORY | FLAGS)
        try:
            check(all(not p.is_symlink() for p in (self.root, *self.root.parents)) and
                  self._dir_identity(self.root_fd) == self.root_identity and
                  self._dir_identity(path_fd) == self.root_identity,
                  'OBJECT_ROOT_CHANGED')
        finally:
            os.close(path_fd)

    def _check_dirs(self):
        self._check_root()
        st = os.stat('objects', dir_fd=self.root_fd, follow_symlinks=False)
        check((st.st_dev, st.st_ino) == self.objects_identity and
              self._dir_identity(self.dir_fd) == self.objects_identity,
              'OBJECT_DIRECTORY_CHANGED')
        if self._metadata_identity is not None:
            for name, identity in self._metadata_identity.items():
                st = os.stat(name, dir_fd=self.root_fd, follow_symlinks=False)
                check((st.st_dev, st.st_ino) == identity and
                      stat.S_ISREG(st.st_mode) and st.st_uid == os.getuid() and
                      stat.S_IMODE(st.st_mode) == 0o600 and st.st_nlink == 1,
                      'OBJECT_METADATA_CHANGED')

    def _usable(self):
        check(not self._failed and self.report.classification == 'VALID',
              'OBJECT_DURABILITY_UNCERTAIN')
        try:
            self._check_dirs()
        except BaseException:
            self._failed = True
            raise

    def _pin_metadata(self):
        self._metadata_identity = {}
        for name in ('store-v1.json', 'seals-v1.jsonl'):
            st = os.stat(name, dir_fd=self.root_fd, follow_symlinks=False)
            self._metadata_identity[name] = st.st_dev, st.st_ino
        self._check_dirs()

    def _open_or_initialize(self):
        try:
            root_names = _names(self.root_fd, 3)
            object_names = _names(self.dir_fd, MAX_OBJECTS + 1)
        except (OSError, LaunchContractError) as exc:
            self.report = RecoveryReport('RECOVERY_INCOMPLETE', (str(exc),),
                                         False, 'UNAVAILABLE', None, 0)
            self._failed = True
            return
        if root_names == {'objects'} and not object_names:
            self._initialize()
        elif {'store-v1.json', 'seals-v1.jsonl'} <= root_names:
            self._recover(root_names, object_names)
        else:
            classification = ('LEGACY_PROVENANCE_MISSING'
                              if root_names == {'objects'} else
                              'JOURNAL_OR_IDENTITY_INVALID')
            self.report = RecoveryReport(classification,
                 ('missing v1 descriptor or journal',), False, 'UNAVAILABLE', None, 0)
            self._failed = True

    def _initialize(self):
        self._check_dirs()
        root_st, objects_st = os.fstat(self.root_fd), os.fstat(self.dir_fd)
        descriptor = {'version': 1, 'store_id': secrets.token_hex(16),
            'root_dev': root_st.st_dev, 'root_ino': root_st.st_ino,
            'objects_dev': objects_st.st_dev, 'objects_ino': objects_st.st_ino,
            **self.context, 'max_object': MAX_OBJECT, 'max_journal': MAX_JOURNAL,
            'max_record': MAX_RECORD, 'max_events': MAX_EVENTS,
            'max_objects': MAX_OBJECTS, 'max_clock_raw': MAX_CLOCK_RAW}
        raw = _bytes(descriptor)
        try:
            fd = os.open('store-v1.json', os.O_WRONLY | os.O_CREAT | os.O_EXCL | FLAGS,
                         0o600, dir_fd=self.root_fd)
            try:
                _write_all(fd, raw)
                os.fsync(fd)
            finally:
                os.close(fd)
            self.journal_fd = os.open('seals-v1.jsonl', os.O_RDWR | os.O_CREAT |
                                      os.O_EXCL | FLAGS, 0o600, dir_fd=self.root_fd)
            os.fsync(self.journal_fd)
            os.fsync(self.root_fd)
            self.descriptor = descriptor
            self.store_id = descriptor['store_id']
            self.descriptor_sha256 = _hash(raw)
            self._pin_metadata()
            self._append('INIT', {'descriptor_sha256': self.descriptor_sha256})
            self.report = RecoveryReport('VALID', (), False, 'UNAVAILABLE',
                                         self._head, 0)
        except BaseException:
            self._failed = True
            raise

    def _read_descriptor(self):
        raw = _file(self.root_fd, 'store-v1.json', limit=4096)
        value = _parse(raw)
        check(type(value) is dict and set(value) == {'version', 'store_id', 'root_dev',
              'root_ino', 'objects_dev', 'objects_ino', *self.context,
              'max_object', 'max_journal', 'max_record', 'max_events',
              'max_objects', 'max_clock_raw'}, 'JOURNAL_OR_IDENTITY_INVALID')
        expected = {**self.context, 'version': 1, 'root_dev': self.root_identity[0],
                    'root_ino': self.root_identity[1],
                    'objects_dev': self.objects_identity[0],
                    'objects_ino': self.objects_identity[1],
                    'max_object': MAX_OBJECT, 'max_journal': MAX_JOURNAL,
                    'max_record': MAX_RECORD, 'max_events': MAX_EVENTS,
                    'max_objects': MAX_OBJECTS, 'max_clock_raw': MAX_CLOCK_RAW}
        check(all(type(value[k]) is type(v) and value[k] == v
                  for k, v in expected.items()) and
              type(value['store_id']) is str and len(value['store_id']) == 32 and
              all(c in '0123456789abcdef' for c in value['store_id']),
              'JOURNAL_OR_IDENTITY_INVALID')
        self.descriptor, self.store_id = value, value['store_id']
        self.descriptor_sha256 = _hash(raw)
        check(self.expected_descriptor_sha256 == self.descriptor_sha256,
              'JOURNAL_OR_IDENTITY_INVALID')

    def _append(self, event: str, data: dict):
        self._check_dirs()
        check(self._seq < MAX_EVENTS and os.fstat(self.journal_fd).st_size +
              3 * MAX_RECORD + REPORT_RESERVE <= MAX_JOURNAL,
              'STORE_JOURNAL_CAPACITY')
        base = {'version': 1, 'seq': self._seq + 1, 'prev': self._head,
                'store_id': self.store_id, 'event': event, 'data': data}
        event_hash = _hash(_bytes(base))
        raw = _bytes({**base, 'hash': event_hash}) + b'\n'
        check(len(raw) <= MAX_RECORD, 'STORE_RECORD_CAPACITY')
        _write_all(self.journal_fd, raw)
        os.fsync(self.journal_fd)
        self._seq += 1
        self._head = event_hash
        return event_hash

    def _replay(self, raw: bytes):
        check(raw and raw.endswith(b'\n') and len(raw) <= MAX_JOURNAL and
              raw.count(b'\n') <= MAX_EVENTS, 'JOURNAL_OR_IDENTITY_INVALID')
        pending = None
        committed = {}
        seen_heads = {}
        self._committed_identity = {}
        for line in raw.splitlines():
            check(0 < len(line) <= MAX_RECORD, 'JOURNAL_OR_IDENTITY_INVALID')
            record = _parse(line)
            check(type(record) is dict and set(record) == {'version', 'seq', 'prev',
                  'store_id', 'event', 'data', 'hash'} and record['version'] == 1 and
                  type(record['seq']) is int and record['seq'] == self._seq + 1 and
                  record['prev'] == self._head and record['store_id'] == self.store_id and
                  record['hash'] == _hash(_bytes({k: v for k, v in record.items()
                                                   if k != 'hash'})),
                  'JOURNAL_OR_IDENTITY_INVALID')
            event, data = record['event'], record['data']
            check(type(data) is dict, 'JOURNAL_OR_IDENTITY_INVALID')
            check(self._seq != 0 or event == 'INIT', 'JOURNAL_OR_IDENTITY_INVALID')
            if event == 'INIT':
                check(self._seq == 0 and data == {'descriptor_sha256':
                      self.descriptor_sha256}, 'JOURNAL_OR_IDENTITY_INVALID')
            elif event == 'PREPARE':
                check(pending is None and set(data) == {'operation_id', 'sha', 'length',
                      'temporary', 'provenance', 'prefix'}, 'JOURNAL_OR_IDENTITY_INVALID')
                op, sha = data['operation_id'], data['sha']
                check(type(op) is str and len(op) == 32 and op not in self._operations and
                      type(sha) is str and sha not in self.receipts and
                      type(data['length']) is int and 0 < data['length'] <= MAX_OBJECT and
                      data['temporary'] == '.tmp-' + op,
                      'JOURNAL_OR_IDENTITY_INVALID')
                digest(sha, 'JOURNAL_OR_IDENTITY_INVALID')
                check(type(data['provenance']) is dict and
                      set(data['provenance']) == set(ObjectProvenance.__dataclass_fields__) and
                      type(data['provenance']['dependencies']) is list,
                      'JOURNAL_OR_IDENTITY_INVALID')
                provenance = ObjectProvenance(**{**data['provenance'],
                    'dependencies': tuple(data['provenance']['dependencies'])}).checked()
                prefix = tuple(_clock_from_dict(c) for c in data['prefix'])
                _validate_clocks(prefix, complete=False,
                                 max_age=self.context['max_clock_age'])
                for dep in provenance.dependencies:
                    check(dep in committed, 'JOURNAL_OR_IDENTITY_INVALID')
                self._operations.add(op)
                pending = (record['hash'], data, provenance, prefix)
            elif event == 'COMMIT':
                check(pending is not None and set(data) == {'prepare_hash',
                      'operation_id', 'sha', 'length', 'final_identity', 'clocks',
                      'clock_policy_sha256'} and
                      data['prepare_hash'] == pending[0] and
                      data['operation_id'] == pending[1]['operation_id'] and
                      data['sha'] == pending[1]['sha'] and
                      data['length'] == pending[1]['length'] and
                      data['clock_policy_sha256'] ==
                      pending[2].clock_policy_sha256,
                      'JOURNAL_OR_IDENTITY_INVALID')
                clocks = tuple(_clock_from_dict(c) for c in data['clocks'])
                check(clocks[:3] == pending[3], 'JOURNAL_OR_IDENTITY_INVALID')
                _validate_clocks(clocks, complete=True,
                                 max_age=self.context['max_clock_age'])
                identity = data['final_identity']
                check(type(identity) is dict and set(identity) == {'dev', 'ino'},
                      'JOURNAL_OR_IDENTITY_INVALID')
                check(all(type(identity[k]) is int and identity[k] > 0
                          for k in ('dev', 'ino')), 'JOURNAL_OR_IDENTITY_INVALID')
                receipt = ObjectReceipt(data['operation_id'], data['sha'],
                    data['length'], record['hash'], clocks, pending[2], 'UNKNOWN')
                self._cross_boot |= clocks[0].reading.boot_id != self.boot_id
                self.receipts[data['sha']] = receipt
                self._committed_identity[data['sha']] = (identity['dev'], identity['ino'])
                committed[record['hash']] = receipt
                pending = None
            elif event == 'RECOVERY_VALIDATED':
                check(pending is None and set(data) == {'prior_head', 'inventory_sha256',
                      'session_id', 'host_id', 'boot_id', 'recovery_utc'} and
                      data['prior_head'] == self._head and
                      data['inventory_sha256'] == _inventory_hash(self.receipts) and
                      type(data['session_id']) is str and
                      len(data['session_id']) == 32 and
                      all(c in '0123456789abcdef' for c in data['session_id']) and
                      all(type(data[k]) is str and 0 < len(data[k]) <= 128
                          for k in ('host_id', 'boot_id')) and
                      type(data['recovery_utc']) in (int, float) and
                      math.isfinite(data['recovery_utc']),
                      'JOURNAL_OR_IDENTITY_INVALID')
            else:
                raise LaunchContractError('JOURNAL_OR_IDENTITY_INVALID')
            self._seq += 1
            self._head = record['hash']
            seen_heads[self._seq] = self._head
        if self.expected_head is not None:
            seq, head = self.expected_head
            check(type(seq) is int and seen_heads.get(seq) == head,
                  'JOURNAL_OR_IDENTITY_INVALID')
        return pending

    def _recover(self, root_names, object_names):
        stage = 'namespace'
        try:
            check(root_names == {'objects', 'store-v1.json', 'seals-v1.jsonl'},
                  'NAMESPACE_CONFLICT')
            stage = 'journal'
            self._read_descriptor()
            raw = _file(self.root_fd, 'seals-v1.jsonl', limit=MAX_JOURNAL)
            pending = self._replay(raw)
            if pending is not None:
                self.report = RecoveryReport('UNRESOLVED_PREPARE',
                    (pending[1]['operation_id'],), False, 'UNAVAILABLE', self._head,
                    len(self.receipts))
                self._failed = True
                return
            stage = 'namespace'
            check(len(object_names) == len(self.receipts) <= MAX_OBJECTS and
                  object_names == set(self.receipts), 'NAMESPACE_CONFLICT')
            for sha, receipt in self.receipts.items():
                stage = 'namespace'
                data = _file(self.dir_fd, sha, limit=MAX_OBJECT,
                             expected_size=receipt.length)
                check(_hash(data) == sha, 'NAMESPACE_CONFLICT')
                st = os.stat(sha, dir_fd=self.dir_fd, follow_symlinks=False)
                check((st.st_dev, st.st_ino) == self._committed_identity[sha],
                      'NAMESPACE_CONFLICT')
                stage = 'barrier'
                fd = os.open(sha, os.O_RDONLY | os.O_NONBLOCK | FLAGS,
                             dir_fd=self.dir_fd)
                try:
                    os.fsync(fd)
                finally:
                    os.close(fd)
            stage = 'barrier'
            self.journal_fd = os.open('seals-v1.jsonl', os.O_RDWR | os.O_NONBLOCK |
                                      FLAGS, dir_fd=self.root_fd)
            st = os.fstat(self.journal_fd)
            check(stat.S_ISREG(st.st_mode) and st.st_uid == os.getuid() and
                  stat.S_IMODE(st.st_mode) == 0o600 and st.st_nlink == 1 and
                  st.st_size == len(raw), 'JOURNAL_OR_IDENTITY_INVALID')
            self._pin_metadata()
            os.fsync(self.journal_fd)
            os.fsync(self.dir_fd)
            os.fsync(self.root_fd)
            inventory = _inventory_hash(self.receipts)
            os.lseek(self.journal_fd, 0, os.SEEK_END)
            self._append('RECOVERY_VALIDATED', {'prior_head': self._head,
                'inventory_sha256': inventory, 'session_id': secrets.token_hex(16),
                'host_id': self.host_id, 'boot_id': self.boot_id,
                'recovery_utc': time.time()})
            self.report = RecoveryReport('VALID', (), True,
                'EXTERNAL_HEAD_MATCH' if self.expected_head else 'UNAVAILABLE',
                self._head, len(self.receipts))
        except BaseException as exc:
            reason = str(exc)
            classification = ('NAMESPACE_CONFLICT' if stage == 'namespace' else
                              'JOURNAL_OR_IDENTITY_INVALID' if stage == 'journal' else
                              'RECOVERY_INCOMPLETE')
            self.report = RecoveryReport(classification, (reason,), False,
                                         'UNAVAILABLE', self._head, len(self.receipts))
            self._failed = True

    def seal_with_provenance(self, data: bytes, provenance: ObjectProvenance,
                             prefix: tuple[ClockEvidence, ClockEvidence, ClockEvidence],
                             recorder: Callable[[], ClockEvidence]) -> ObjectReceipt:
        with self._mutex:
            self._usable()
            check(not self._callback and type(data) is bytes and
                  0 < len(data) <= MAX_OBJECT and
                  type(provenance) is ObjectProvenance and callable(recorder),
                  'STORE_SEAL_INTERFACE')
            check(not self._cross_boot, 'STORE_CROSS_BOOT_ACQUISITION_HELD')
            provenance.checked()
            _validate_clocks(prefix, complete=False,
                             max_age=self.context['max_clock_age'])
            check(prefix[0].reading.boot_id == self.boot_id,
                  'CLOCK_BOOT_ID')
            check(all(s.method == self.context['clock_method'] for s in prefix),
                  'CLOCK_METHOD_MISMATCH')
            for dep in provenance.dependencies:
                check(any(r.commit_hash == dep for r in self.receipts.values()),
                      'STORE_DEPENDENCY_MISSING')
            sha = _hash(data)
            check(sha not in self.receipts and sha not in _names(self.dir_fd, MAX_OBJECTS + 1),
                  'OBJECT_ALREADY_EXISTS')
            check(len(self.receipts) < MAX_OBJECTS and self._seq + 3 <= MAX_EVENTS and
                  os.fstat(self.journal_fd).st_size + 3 * MAX_RECORD +
                  REPORT_RESERVE <= MAX_JOURNAL, 'STORE_JOURNAL_CAPACITY')
            fs = os.fstatvfs(self.dir_fd)
            check(fs.f_bavail * fs.f_frsize >= len(data) +
                  3 * MAX_RECORD + REPORT_RESERVE,
                  'STORE_DISK_RESERVE')
            op = secrets.token_hex(16)
            temp = '.tmp-' + op
            prepared = {'operation_id': op, 'sha': sha, 'length': len(data),
                        'temporary': temp, 'provenance': {**provenance.__dict__,
                          'dependencies': list(provenance.dependencies)},
                        'prefix': [_clock_to_dict(s) for s in prefix]}
            try:
                prepare_hash = self._append('PREPARE', prepared)
                fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | FLAGS,
                             0o600, dir_fd=self.dir_fd)
                try:
                    _write_all(fd, data)
                    os.fsync(fd)
                finally:
                    os.close(fd)
                self._check_dirs()
                os.link(temp, sha, src_dir_fd=self.dir_fd,
                        dst_dir_fd=self.dir_fd, follow_symlinks=False)
                os.fsync(self.dir_fd)
                self._check_dirs()
                os.unlink(temp, dir_fd=self.dir_fd)
                os.fsync(self.dir_fd)
                self._check_dirs()
                check(_file(self.dir_fd, sha, limit=MAX_OBJECT,
                            expected_size=len(data)) == data, 'OBJECT_DIGEST_MISMATCH')
                st = os.stat(sha, dir_fd=self.dir_fd, follow_symlinks=False)
                self._callback = True
                try:
                    final = recorder()
                finally:
                    self._callback = False
                check(type(final) is ClockEvidence and
                      final.method == self.context['clock_method'],
                      'CLOCK_RECORDER_RESULT')
                clocks = (*prefix, final)
                _validate_clocks(clocks, complete=True,
                                 max_age=self.context['max_clock_age'])
                commit = {'prepare_hash': prepare_hash, 'operation_id': op,
                    'sha': sha, 'length': len(data),
                    'final_identity': {'dev': st.st_dev, 'ino': st.st_ino},
                    'clocks': [_clock_to_dict(s) for s in clocks],
                    'clock_policy_sha256': provenance.clock_policy_sha256}
                commit_hash = self._append('COMMIT', commit)
                receipt = ObjectReceipt(op, sha, len(data), commit_hash,
                                        clocks, provenance, 'ACKNOWLEDGED_THIS_SESSION')
                self.receipts[sha] = receipt
                self._operations.add(op)
                self.report = replace(self.report, journal_head=self._head,
                                      receipt_count=len(self.receipts))
                return receipt
            except BaseException:
                self._failed = True
                raise

    def read_receipt(self, receipt: ObjectReceipt) -> bytes:
        with self._mutex:
            self._usable()
            check(not self._callback and type(receipt) is ObjectReceipt and
                  receipt.object_sha256 in self.receipts and
                  self.receipts[receipt.object_sha256] == receipt,
                  'OBJECT_RECEIPT_MISMATCH')
            try:
                data = _file(self.dir_fd, receipt.object_sha256, limit=MAX_OBJECT,
                             expected_size=receipt.length)
                check(_hash(data) == receipt.object_sha256, 'OBJECT_DIGEST_MISMATCH')
                return data
            except BaseException:
                self._failed = True
                raise

    def close(self):
        if os.getpid() != self._pid:
            self._fork_child()
            return
        for attr in ('journal_fd', 'dir_fd', 'root_fd'):
            fd = getattr(self, attr, None)
            if fd is not None:
                os.close(fd)
                setattr(self, attr, None)
        self._failed = True

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()
