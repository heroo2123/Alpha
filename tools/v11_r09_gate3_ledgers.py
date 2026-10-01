"""Offline durable shared/session ledgers for Gate 3 V4 slice 2
(docs/V11_R09_GATE3_TRANSPORT_RUNTIME_DESIGN.md, section 3 "Ownership,
journals and durable denial history", section 4 "Session state machine and
accounting composition", and section 7's "Persistence" crash-matrix row).

This module has no transport, decoder, credential or launch entrypoint,
exactly like tools/v11_r09_gate3_launch.py's ``DurableBudget`` and
tools/v11_r09_gate3_store_v1.py's ``VersionedImmutableObjectStore`` it
extends durability discipline from. It deliberately duplicates their
append-only hash-chain / bounded-reader-replay / O_CREAT|O_EXCL-and-fsync
pattern inside its own small ``_HashChainJournal`` base, rather than
subclassing either of those already-reviewed classes, so a change here can
never silently alter their already-accepted behavior (same rationale given
in tools/v11_r09_gate3_launch_v4.py's module docstring).

Two durable, process-restart-safe ledgers:

- ``SharedLedger``: the global, cross-purpose "shared denial root". Binds a
  single global open INTENT_OPEN/INTENT_CLOSED token (section 5: "One token
  globally covers all purposes/providers") and durable per-control-domain
  denial/cooldown history (section 3).
- ``SessionLedger``: the per-run "session root" durable order from section 4
  step 1-6 (ATTEMPT_INTENT, BUDGET_RESERVED, DISPATCH_INTENT, denial,
  TRANSPORT_CLOSED, ACCOUNTED, OBJECT_WITNESSED, terminal/report), with a
  single open attempt at a time.

Both generalize the exact defect class R2 closed in ``DurableBudget``
(commit 599dfd1: "never complete an inherited reservation") to every
persisted boundary: on a fresh open, whatever was already open/in-progress
is snapshotted as "inherited" and every further progression call for that
same key is permanently refused in every process that ever reopens the
root, matching section 3's "missing lineage or a prior unfinished intent is
an unresolved hold across future jobs" and section 4's "Do not add a
recovery path that calls complete() on an old incomplete reservation."
There is no resumption/clear method in this slice: "Resumption is an
append-only external review reference" (section 3) that this offline slice
does not grant.

No concrete socket adapter, real clock recorder, live preflight, launchable
private manifest or service wiring belongs here (section 7, after the
acceptance-slice table). Synthetic fixtures only; no network calls.
"""
from __future__ import annotations

import contextlib
import fcntl
import hashlib
import math
import os
import re
import stat
import threading
from pathlib import Path

from tools.v11_multimodel_panel import canonical
from tools.v11_r09_gate3_launch import (
    MAX_BYTES, LaunchContractError, check, digest, exact, integer,
    parse_canonical,
)
from tools.v11_r09_gate3_launch_v4 import PURPOSES

# Fixed caps (not constructor parameters), matching design section 3's
# "Proposed additional caps: each session/denial journal <=64 MiB, record
# <=64 KiB, <=32,768 events per journal". Deliberately redefined here
# (rather than imported) the same way tools/v11_r09_gate3_launch_v4.py
# redefines SESSION_JOURNAL_MAX_EVENTS independently of launch.py's own
# JOURNAL_MAX_EVENTS=131072 budget-journal cap: these are a different,
# smaller, independently-reviewable bound for a different journal family.
# A manifest or caller can never widen these by requesting a looser ledger.
LEDGER_MAX_BYTES = 64 * 1024 ** 2
LEDGER_RECORD_MAX_BYTES = 64 * 1024
LEDGER_MAX_EVENTS = 32768
LEDGER_READ_CHUNK_BYTES = 64 * 1024
# Section 3: "Reserve a separate 16 MiB report area before acquisition".
REPORT_RESERVE_BYTES = 16 * 1024 ** 2

REQUEST_ID_RE = re.compile(r'[A-Za-z0-9_-]{1,80}')


def _optional_digest(value, reason):
    if value is not None:
        digest(value, reason)


class _HashChainJournal:
    """Shared durability primitive for both ledgers below.

    Exclusive, append-only, hash-chained, fsync-disciplined journal with a
    bounded-reader replay (never allocates past the fixed caps on a crafted
    or torn record, same technique as ``DurableBudget._replay``). Subclasses
    set ``LOCK_NAME``/``JOURNAL_NAME`` and provide their own ``_state()``
    to reconstruct typed state from ``self.events``.
    """
    LOCK_NAME: str = ''
    JOURNAL_NAME: str = ''

    def __init__(self, directory):
        path = Path(directory)
        check(path.is_absolute(), 'LEDGER_DIRECTORY')
        for ancestor in (path, *path.parents):
            check(not stat.S_ISLNK(os.lstat(ancestor).st_mode), 'LEDGER_PATH_SYMLINK')
        check(path == path.resolve(), 'LEDGER_DIRECTORY')
        self._owner_pid = os.getpid()
        self._mutex = threading.Lock()
        self.dir_fd = self.lock_fd = self.fd = None
        self.failed = False
        try:
            self.dir_fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            directory_stat = os.fstat(self.dir_fd)
            check(stat.S_ISDIR(directory_stat.st_mode) and
                  directory_stat.st_uid == os.getuid() and
                  stat.S_IMODE(directory_stat.st_mode) == 0o700 and
                  os.stat(path).st_ino == directory_stat.st_ino and
                  os.stat(path).st_dev == directory_stat.st_dev,
                  'LEDGER_DIRECTORY_PRIVATE_MODE')
            self.path = path
            self.directory_identity = (directory_stat.st_dev, directory_stat.st_ino)
            self.lock_fd = os.open(self.LOCK_NAME, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW |
                                   os.O_NONBLOCK, 0o600, dir_fd=self.dir_fd)
            self._check_file(self.LOCK_NAME, self.lock_fd)
            fcntl.flock(self.lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.fd = os.open(self.JOURNAL_NAME, os.O_CREAT | os.O_RDWR | os.O_APPEND |
                              os.O_NOFOLLOW | os.O_NONBLOCK, 0o600, dir_fd=self.dir_fd)
            self._check_file(self.JOURNAL_NAME, self.fd)
            # Seal a newly created directory entry before any record.
            os.fsync(self.dir_fd)
        except BlockingIOError as exc:
            self.close()
            raise LaunchContractError('LEDGER_CONCURRENT_WRITER') from exc
        except BaseException:
            self.close()
            raise
        self.events = []
        self.prev = '0' * 64
        self._journal_bytes = 0
        try:
            os.register_at_fork(after_in_child=self._fork_child)
        except BaseException:
            self.close()
            raise

    def _fork_child(self):
        # A copied open-file description cannot be used safely by the
        # child; closing it here does not unlock the parent's flock.
        self._owner_pid = -1
        self.failed = True
        for name in ('fd', 'lock_fd', 'dir_fd'):
            fd = getattr(self, name, None)
            if fd is not None:
                try:
                    os.close(fd)
                except OSError:
                    pass
                setattr(self, name, None)

    def _check_file(self, name, fd):
        st = os.fstat(fd)
        check(stat.S_ISREG(st.st_mode) and st.st_uid == os.getuid() and
              stat.S_IMODE(st.st_mode) == 0o600 and st.st_nlink == 1,
              'LEDGER_FILE_IDENTITY')
        linked = os.stat(name, dir_fd=self.dir_fd, follow_symlinks=False)
        check((linked.st_dev, linked.st_ino) == (st.st_dev, st.st_ino),
              'LEDGER_FILE_IDENTITY')

    def _require_owner(self):
        check(os.getpid() == self._owner_pid, 'LEDGER_OWNER_PROCESS')

    def _healthy(self):
        self._require_owner()
        check(not self.failed, 'LEDGER_DURABILITY_UNCERTAIN')
        try:
            check(all(not stat.S_ISLNK(os.lstat(ancestor).st_mode)
                      for ancestor in (self.path, *self.path.parents)),
                  'LEDGER_PATH_SYMLINK')
            current = os.stat(self.path, follow_symlinks=False)
            check((current.st_dev, current.st_ino) == self.directory_identity and
                  stat.S_ISDIR(current.st_mode) and current.st_uid == os.getuid() and
                  stat.S_IMODE(current.st_mode) == 0o700,
                  'LEDGER_DIRECTORY_IDENTITY')
            self._check_file(self.LOCK_NAME, self.lock_fd)
            self._check_file(self.JOURNAL_NAME, self.fd)
        except BaseException:
            self.failed = True
            raise

    @contextlib.contextmanager
    def _guard(self):
        """Reject same-thread reentrancy and cross-thread concurrent use.

        ``threading.Lock`` is deliberately non-reentrant: a nested call from
        the same thread (reentrancy) and a concurrent call from another
        thread both fail the non-blocking acquire identically, rather than
        a reentrant lock silently allowing a nested call to re-enter half-
        mutated state.
        """
        self._require_owner()
        if not self._mutex.acquire(blocking=False):
            raise LaunchContractError('LEDGER_REENTRANT_OR_CONCURRENT_USE')
        try:
            yield
        finally:
            self._mutex.release()

    def _replay(self):
        """Bounded-reader replay: identical discipline to
        ``DurableBudget._replay`` (tools/v11_r09_gate3_launch.py). Never
        allocates past the fixed caps for a crafted or torn record.
        """
        size = os.fstat(self.fd).st_size
        check(size <= LEDGER_MAX_BYTES, 'LEDGER_CAPACITY_EXCEEDED')
        with os.fdopen(os.dup(self.fd), 'rb') as reader:
            reader.seek(0)
            total_bytes = 0
            buf = b''
            while True:
                newline_at = buf.find(b'\n')
                if newline_at == -1:
                    check(len(buf) < LEDGER_RECORD_MAX_BYTES, 'LEDGER_RECORD_TOO_LARGE')
                    chunk = reader.read(LEDGER_READ_CHUNK_BYTES)
                    if not chunk:
                        check(len(buf) < LEDGER_RECORD_MAX_BYTES, 'LEDGER_RECORD_TOO_LARGE')
                        check(not buf, 'LEDGER_TORN_RECORD')
                        break
                    total_bytes += len(chunk)
                    check(total_bytes <= LEDGER_MAX_BYTES, 'LEDGER_CAPACITY_EXCEEDED')
                    buf += chunk
                    continue
                line = buf[:newline_at]
                buf = buf[newline_at + 1:]
                check(newline_at + 1 <= LEDGER_RECORD_MAX_BYTES, 'LEDGER_RECORD_TOO_LARGE')
                check(len(self.events) < LEDGER_MAX_EVENTS, 'LEDGER_EVENT_CAPACITY')
                record = parse_canonical(line)
                exact(record, ('seq', 'prev', 'event', 'hash'), 'LEDGER_RECORD_SCHEMA')
                check(record['seq'] == len(self.events) and record['prev'] == self.prev,
                      'LEDGER_SEQUENCE')
                expected = hashlib.sha256(canonical({k: record[k] for k in
                    ('seq', 'prev', 'event')})).hexdigest()
                check(record['hash'] == expected, 'LEDGER_HASH')
                self.prev = expected
                self.events.append(record['event'])
                if b'\n' not in buf:
                    check(len(buf) < LEDGER_RECORD_MAX_BYTES, 'LEDGER_RECORD_TOO_LARGE')
        self._journal_bytes = size

    def _append(self, event):
        self._healthy()
        check(len(self.events) < LEDGER_MAX_EVENTS, 'LEDGER_EVENT_CAPACITY')
        record = {'seq': len(self.events), 'prev': self.prev, 'event': event}
        record['hash'] = hashlib.sha256(canonical(record)).hexdigest()
        data = canonical(record) + b'\n'
        check(len(data) <= LEDGER_RECORD_MAX_BYTES, 'LEDGER_RECORD_TOO_LARGE')
        check(self._journal_bytes + len(data) <= LEDGER_MAX_BYTES,
              'LEDGER_CAPACITY_EXCEEDED')
        try:
            written = os.write(self.fd, data)
            check(written == len(data), 'LEDGER_SHORT_WRITE')
            os.fsync(self.fd)
        except BaseException as exc:
            self.failed = True
            reason = ('LEDGER_SHORT_WRITE_DURABILITY_UNCERTAIN'
                      if isinstance(exc, LaunchContractError) and
                      str(exc) == 'LEDGER_SHORT_WRITE' else
                      'LEDGER_DURABILITY_UNCERTAIN')
            raise LaunchContractError(reason) from exc
        self._journal_bytes += len(data)
        self.prev = record['hash']
        self.events.append(event)

    def close(self):
        for name in ('fd', 'lock_fd', 'dir_fd'):
            fd = getattr(self, name, None)
            if fd is not None:
                os.close(fd)
                setattr(self, name, None)

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()


class SharedLedger(_HashChainJournal):
    """Durable, cross-purpose, process-restart-safe shared denial/intent root.

    One global token: at most one open INTENT_OPEN at a time (design section
    5: "One token globally covers all purposes/providers"). Durable
    per-control-domain denial/cooldown history (section 3). An intent left
    open by a prior process (crash, poisoned append, or any other stop) is
    snapshotted as ``inherited_open_request_id`` on every fresh open; no
    method in this or any future process may ever close/resolve that exact
    request, matching "missing lineage or a prior unfinished intent is an
    unresolved hold across future jobs" and the R2 lesson generalized to
    every boundary, not just budget completion.
    """
    LOCK_NAME = 'gate3_shared.lock'
    JOURNAL_NAME = 'gate3_shared.jsonl'

    def __init__(self, directory, *, manifest_sha256, boot_id='synthetic-boot',
                 expected_history_head=None, genesis_reviewed=False,
                 max_requests=3600):
        digest(manifest_sha256, 'SHARED_LEDGER_MANIFEST_DIGEST')
        check(type(boot_id) is str and boot_id, 'SHARED_LEDGER_BOOT_ID')
        _optional_digest(expected_history_head, 'SHARED_LEDGER_HISTORY_HEAD')
        check(type(genesis_reviewed) is bool, 'SHARED_LEDGER_GENESIS_FLAG')
        integer(max_requests, 1, 3600, 'SHARED_LEDGER_REQUEST_CAP')
        super().__init__(directory)
        try:
            self._replay()
            if not self.events:
                # Design section 3: "empty new files are not evidence of no
                # past denials". A brand-new empty root may only be used
                # once genesis is explicitly reviewed, and never together
                # with a claimed external head (nothing exists yet to bind).
                check(expected_history_head is None,
                      'SHARED_LEDGER_LINEAGE_HEAD_MISMATCH')
                check(genesis_reviewed is True, 'SHARED_LEDGER_LINEAGE_UNREVIEWED')
                self._append({'op': 'init', 'manifest': manifest_sha256,
                              'boot_id': boot_id, 'max_requests': max_requests})
            else:
                init = self.events[0]
                check(init.get('op') == 'init' and init.get('manifest') == manifest_sha256 and
                      init.get('max_requests') == max_requests,
                      'SHARED_LEDGER_IDENTITY_MISMATCH')
                if expected_history_head is not None:
                    check(self.prev == expected_history_head,
                          'SHARED_LEDGER_LINEAGE_HEAD_MISMATCH')
            self.manifest = manifest_sha256
            self.boot_id = boot_id
            self.max_requests = max_requests
            self._state()
            self.inherited_open_request_id = (
                self.open_intent['request_id'] if self.open_intent is not None else None)
        except BaseException:
            self.close()
            raise

    def _state(self):
        self.open_intent = None
        self.request_ids_ever = set()
        self.denials = {}
        self.open_count = 0
        self.closed_count = 0
        for event in self.events[1:]:
            op = event.get('op')
            if op == 'intent_open':
                check(self.open_intent is None, 'SHARED_LEDGER_INTENT_OVERLAP')
                rid = event['request_id']
                check(rid not in self.request_ids_ever, 'SHARED_LEDGER_REQUEST_ID_REUSE')
                self.request_ids_ever.add(rid)
                self.open_intent = {k: event[k] for k in
                    ('request_id', 'purpose', 'endpoint_id', 'control_domain_id',
                     'max_reservation_bytes')}
                self.open_count += 1
            elif op == 'intent_closed':
                check(self.open_intent is not None and
                      self.open_intent['request_id'] == event['request_id'],
                      'SHARED_LEDGER_CLOSE_WITHOUT_OPEN')
                if event['outcome'] == 'DENIED':
                    self._apply_denial(self.open_intent['control_domain_id'], event['denial'])
                self.open_intent = None
                self.closed_count += 1
            else:
                raise LaunchContractError('SHARED_LEDGER_UNKNOWN_EVENT')
        check(self.open_count <= self.max_requests, 'SHARED_LEDGER_REQUEST_CAP_EXCEEDED')

    def _apply_denial(self, control_domain_id, denial):
        no_auto_resume = denial['status'] in ('401', '403', 'EXPLICIT_DENIAL')
        if no_auto_resume:
            cooldown_until = None
        else:
            retry = denial['retry_after_seconds']
            # Design section 3: "An invalid/missing expiry remains
            # unresolved for future jobs". Finite cooldown is at least the
            # window end plus any evidenced provider expiry, measured from
            # the conservative receipt upper bound.
            cooldown_until = (None if retry is None else
                max(denial['window_end_utc'], denial['receipt_upper_bound_utc'] + retry))
        self.denials[control_domain_id] = {
            'status': denial['status'], 'reason': denial['reason'],
            'cooldown_until': cooldown_until}

    @staticmethod
    def _validate_denial(denial):
        exact(denial, ('status', 'reason', 'evidence_sha256', 'evidence_missing_cause',
                       'retry_after_seconds', 'window_end_utc', 'receipt_upper_bound_utc'),
              'SHARED_LEDGER_DENIAL_SCHEMA')
        check(denial['status'] in ('401', '403', '429', '503', 'EXPLICIT_DENIAL', 'OTHER'),
              'SHARED_LEDGER_DENIAL_STATUS')
        check(type(denial['reason']) is str and denial['reason'], 'SHARED_LEDGER_DENIAL_REASON')
        check((denial['evidence_sha256'] is None) != (denial['evidence_missing_cause'] is None),
              'SHARED_LEDGER_DENIAL_EVIDENCE')
        if denial['evidence_sha256'] is not None:
            digest(denial['evidence_sha256'], 'SHARED_LEDGER_DENIAL_EVIDENCE')
        else:
            check(type(denial['evidence_missing_cause']) is str and
                  denial['evidence_missing_cause'], 'SHARED_LEDGER_DENIAL_EVIDENCE')
        check(denial['retry_after_seconds'] is None or
              (type(denial['retry_after_seconds']) in (int, float) and
               math.isfinite(denial['retry_after_seconds']) and
               denial['retry_after_seconds'] >= 0), 'SHARED_LEDGER_DENIAL_RETRY_AFTER')
        check(type(denial['window_end_utc']) in (int, float) and
              math.isfinite(denial['window_end_utc']), 'SHARED_LEDGER_DENIAL_WINDOW')
        check(type(denial['receipt_upper_bound_utc']) in (int, float) and
              math.isfinite(denial['receipt_upper_bound_utc']),
              'SHARED_LEDGER_DENIAL_RECEIPT_BOUND')
        return dict(denial)

    def is_blocked(self, control_domain_id, *, now_utc):
        self._healthy()
        record = self.denials.get(control_domain_id)
        if record is None:
            return False
        return record['cooldown_until'] is None or now_utc < record['cooldown_until']

    def intent_open(self, request_id, *, purpose, endpoint_id, control_domain_id,
                     max_reservation_bytes, now_utc):
        with self._guard():
            self._healthy()
            check(type(request_id) is str and REQUEST_ID_RE.fullmatch(request_id),
                  'SHARED_LEDGER_REQUEST_ID')
            check(purpose in PURPOSES, 'SHARED_LEDGER_PURPOSE')
            digest(endpoint_id, 'SHARED_LEDGER_ENDPOINT_ID')
            digest(control_domain_id, 'SHARED_LEDGER_CONTROL_DOMAIN_ID')
            integer(max_reservation_bytes, 1, MAX_BYTES, 'SHARED_LEDGER_RESERVATION')
            # Covers both ordinary overlap and a permanently-inherited hold
            # uniformly: an inherited open intent is never cleared, so
            # self.open_intent stays non-None forever in that case too.
            check(self.open_intent is None, 'SHARED_LEDGER_INTENT_OPEN_HELD')
            check(request_id not in self.request_ids_ever,
                  'SHARED_LEDGER_REQUEST_ID_REUSE')
            check(self.open_count < self.max_requests,
                  'SHARED_LEDGER_REQUEST_CAP_EXCEEDED')
            check(not self.is_blocked(control_domain_id, now_utc=now_utc),
                  'SHARED_LEDGER_CONTROL_DOMAIN_COOLDOWN')
            self._append({'op': 'intent_open', 'request_id': request_id, 'purpose': purpose,
                          'endpoint_id': endpoint_id, 'control_domain_id': control_domain_id,
                          'max_reservation_bytes': max_reservation_bytes})
            self._state()

    def intent_closed(self, request_id, *, outcome, denial=None):
        with self._guard():
            self._healthy()
            check(self.open_intent is not None and
                  self.open_intent['request_id'] == request_id,
                  'SHARED_LEDGER_CLOSE_WITHOUT_OPEN')
            check(request_id != self.inherited_open_request_id,
                  'SHARED_LEDGER_INHERITED_INTENT_HELD')
            check(outcome in ('OK', 'DENIED', 'FAILED', 'AMBIGUOUS'),
                  'SHARED_LEDGER_CLOSE_OUTCOME')
            if outcome == 'DENIED':
                denial = self._validate_denial(denial)
            else:
                check(denial is None, 'SHARED_LEDGER_CLOSE_OUTCOME')
            self._append({'op': 'intent_closed', 'request_id': request_id,
                          'outcome': outcome, 'denial': denial})
            self._state()


class SessionLedger(_HashChainJournal):
    """Durable, per-run, process-restart-safe session attempt ledger.

    Implements the durable order from design section 4 steps 1-6 for a
    single frozen request at a time: ATTEMPT_INTENT, BUDGET_RESERVED,
    DISPATCH_INTENT, an optional terminal ``denial``/``refuse``, or
    TRANSPORT_CLOSED -> ACCOUNTED -> optional OBJECT_WITNESSED -> terminal
    (the report boundary). Only one attempt may be open at a time ("at most
    one unfinished intent", section 3). An attempt left open by a prior
    process is snapshotted as ``inherited_request_id``; every further
    progression call against that exact request is permanently refused in
    every later process, the same generalized R2 rule as ``SharedLedger``.
    """
    LOCK_NAME = 'gate3_session.lock'
    JOURNAL_NAME = 'gate3_session.jsonl'

    _ORDER = {'budget_reserved': 'OPEN', 'dispatch_intent': 'RESERVED',
              'transport_closed': 'DISPATCHED', 'accounted': 'CLOSED',
              'object_witnessed': 'ACCOUNTED'}
    _NEXT = {'budget_reserved': 'RESERVED', 'dispatch_intent': 'DISPATCHED',
             'transport_closed': 'CLOSED', 'accounted': 'ACCOUNTED',
             'object_witnessed': 'WITNESSED'}

    def __init__(self, directory, *, manifest_sha256, boot_id='synthetic-boot',
                 report_reserve_bytes=REPORT_RESERVE_BYTES, max_requests=3600,
                 expected_head=None):
        digest(manifest_sha256, 'SESSION_LEDGER_MANIFEST_DIGEST')
        check(type(boot_id) is str and boot_id, 'SESSION_LEDGER_BOOT_ID')
        integer(report_reserve_bytes, REPORT_RESERVE_BYTES, 2 ** 63 - 1,
                'SESSION_LEDGER_REPORT_RESERVE')
        integer(max_requests, 1, 3600, 'SESSION_LEDGER_REQUEST_CAP')
        _optional_digest(expected_head, 'SESSION_LEDGER_HEAD_MISMATCH')
        super().__init__(directory)
        try:
            self._replay()
            if not self.events:
                self._append({'op': 'init', 'manifest': manifest_sha256, 'boot_id': boot_id,
                              'report_reserve_bytes': report_reserve_bytes,
                              'max_requests': max_requests})
            else:
                init = self.events[0]
                check(init.get('op') == 'init' and init.get('manifest') == manifest_sha256 and
                      init.get('report_reserve_bytes') == report_reserve_bytes and
                      init.get('max_requests') == max_requests,
                      'SESSION_LEDGER_IDENTITY_MISMATCH')
            if expected_head is not None:
                check(self.prev == expected_head, 'SESSION_LEDGER_HEAD_MISMATCH')
            self.manifest = manifest_sha256
            self.boot_id = boot_id
            self.report_reserve_bytes = report_reserve_bytes
            self.max_requests = max_requests
            self._state()
            self.inherited_request_id = (
                self.attempt['request_id'] if self.attempt is not None and
                self.attempt['state'] != 'TERMINAL' else None)
        except BaseException:
            self.close()
            raise

    def _state(self):
        self.attempt = None
        self.request_ids_ever = set()
        self.completed_count = 0
        for event in self.events[1:]:
            op = event.get('op')
            rid = event.get('request_id')
            if op == 'attempt_intent':
                check(self.attempt is None or self.attempt['state'] == 'TERMINAL',
                      'SESSION_LEDGER_ATTEMPT_OVERLAP')
                check(rid not in self.request_ids_ever, 'SESSION_LEDGER_REQUEST_ID_REUSE')
                check(self.completed_count < self.max_requests,
                      'SESSION_LEDGER_REQUEST_CAP_EXCEEDED')
                self.request_ids_ever.add(rid)
                self.attempt = {'request_id': rid, 'state': 'OPEN', 'outcome': None}
            elif op in self._ORDER:
                check(self.attempt is not None and self.attempt['request_id'] == rid and
                      self.attempt['state'] == self._ORDER[op],
                      'SESSION_LEDGER_BAD_TRANSITION')
                self.attempt['state'] = self._NEXT[op]
            elif op == 'refuse':
                check(self.attempt is not None and self.attempt['request_id'] == rid and
                      self.attempt['state'] == 'OPEN', 'SESSION_LEDGER_BAD_TRANSITION')
                self.attempt['state'] = 'TERMINAL'
                self.attempt['outcome'] = 'REFUSED'
                self.completed_count += 1
            elif op == 'denial':
                check(self.attempt is not None and self.attempt['request_id'] == rid and
                      self.attempt['state'] in ('OPEN', 'RESERVED', 'DISPATCHED'),
                      'SESSION_LEDGER_BAD_TRANSITION')
                self.attempt['state'] = 'TERMINAL'
                self.attempt['outcome'] = 'DENIED'
                self.completed_count += 1
            elif op == 'terminal':
                check(self.attempt is not None and self.attempt['request_id'] == rid and
                      self.attempt['state'] in ('ACCOUNTED', 'WITNESSED'),
                      'SESSION_LEDGER_BAD_TRANSITION')
                self.attempt['state'] = 'TERMINAL'
                self.attempt['outcome'] = event['outcome']
                self.completed_count += 1
            else:
                raise LaunchContractError('SESSION_LEDGER_UNKNOWN_EVENT')

    def _guard_attempt(self, request_id):
        self._healthy()
        check(self.attempt is not None and self.attempt['request_id'] == request_id,
              'SESSION_LEDGER_NO_SUCH_ATTEMPT')
        # Generalized R2 rule: never advance/close/resolve an attempt this
        # process did not itself originate past construction time.
        check(request_id != self.inherited_request_id,
              'SESSION_LEDGER_INHERITED_ATTEMPT_HELD')
        return self.attempt

    def attempt_intent(self, request_id, *, purpose, endpoint_id, max_reservation_bytes,
                        range_start=None, range_end=None, validator_sha256=None,
                        denial_head=None):
        with self._guard():
            self._healthy()
            check(type(request_id) is str and REQUEST_ID_RE.fullmatch(request_id),
                  'SESSION_LEDGER_REQUEST_ID')
            check(purpose in PURPOSES, 'SESSION_LEDGER_PURPOSE')
            digest(endpoint_id, 'SESSION_LEDGER_ENDPOINT_ID')
            integer(max_reservation_bytes, 1, MAX_BYTES, 'SESSION_LEDGER_RESERVATION')
            check((range_start is None) == (range_end is None), 'SESSION_LEDGER_RANGE')
            if range_start is not None:
                integer(range_start, 0, MAX_BYTES, 'SESSION_LEDGER_RANGE')
                integer(range_end, range_start, MAX_BYTES, 'SESSION_LEDGER_RANGE')
            _optional_digest(validator_sha256, 'SESSION_LEDGER_VALIDATOR')
            _optional_digest(denial_head, 'SESSION_LEDGER_DENIAL_HEAD')
            check(self.attempt is None or self.attempt['state'] == 'TERMINAL',
                  'SESSION_LEDGER_ATTEMPT_OPEN_HELD')
            check(request_id not in self.request_ids_ever,
                  'SESSION_LEDGER_REQUEST_ID_REUSE')
            check(self.completed_count < self.max_requests,
                  'SESSION_LEDGER_REQUEST_CAP_EXCEEDED')
            self._append({'op': 'attempt_intent', 'request_id': request_id,
                          'purpose': purpose, 'endpoint_id': endpoint_id,
                          'range_start': range_start, 'range_end': range_end,
                          'validator_sha256': validator_sha256,
                          'denial_head': denial_head,
                          'max_reservation_bytes': max_reservation_bytes})
            self._state()

    def refuse(self, request_id, *, reason):
        with self._guard():
            self._guard_attempt(request_id)
            check(self.attempt['state'] == 'OPEN', 'SESSION_LEDGER_BAD_TRANSITION')
            check(type(reason) is str and reason, 'SESSION_LEDGER_REFUSE_REASON')
            self._append({'op': 'refuse', 'request_id': request_id, 'reason': reason})
            self._state()

    def budget_reserved(self, request_id, *, reserve_event_hash):
        with self._guard():
            self._guard_attempt(request_id)
            digest(reserve_event_hash, 'SESSION_LEDGER_RESERVE_HASH')
            self._append({'op': 'budget_reserved', 'request_id': request_id,
                          'reserve_event_hash': reserve_event_hash})
            self._state()

    def dispatch_intent(self, request_id, *, measured_start_monotonic):
        with self._guard():
            self._guard_attempt(request_id)
            check(type(measured_start_monotonic) in (int, float) and
                  math.isfinite(measured_start_monotonic) and
                  measured_start_monotonic >= 0, 'SESSION_LEDGER_DISPATCH_CLOCK')
            self._append({'op': 'dispatch_intent', 'request_id': request_id,
                          'measured_start_monotonic': measured_start_monotonic})
            self._state()

    def denial(self, request_id, *, reason, shared_denial_event_hash=None):
        with self._guard():
            self._guard_attempt(request_id)
            check(self.attempt['state'] in ('OPEN', 'RESERVED', 'DISPATCHED'),
                  'SESSION_LEDGER_BAD_TRANSITION')
            check(type(reason) is str and reason, 'SESSION_LEDGER_DENIAL_REASON')
            _optional_digest(shared_denial_event_hash, 'SESSION_LEDGER_DENIAL_HASH')
            self._append({'op': 'denial', 'request_id': request_id, 'reason': reason,
                          'shared_denial_event_hash': shared_denial_event_hash})
            self._state()

    def transport_closed(self, request_id, *, outcome, total_delivered_bytes,
                          denial_history_head=None, accounting_head=None):
        with self._guard():
            self._guard_attempt(request_id)
            check(outcome in ('OK', 'FAILED', 'PARTIAL'), 'SESSION_LEDGER_CLOSE_OUTCOME')
            integer(total_delivered_bytes, 0, MAX_BYTES, 'SESSION_LEDGER_DELIVERED_BYTES')
            _optional_digest(denial_history_head, 'SESSION_LEDGER_DENIAL_HISTORY_HEAD')
            _optional_digest(accounting_head, 'SESSION_LEDGER_ACCOUNTING_HEAD')
            self._append({'op': 'transport_closed', 'request_id': request_id,
                          'outcome': outcome, 'total_delivered_bytes': total_delivered_bytes,
                          'denial_history_head': denial_history_head,
                          'accounting_head': accounting_head})
            self._state()

    def accounted(self, request_id, *, completion_event_hash):
        with self._guard():
            self._guard_attempt(request_id)
            digest(completion_event_hash, 'SESSION_LEDGER_COMPLETION_HASH')
            self._append({'op': 'accounted', 'request_id': request_id,
                          'completion_event_hash': completion_event_hash})
            self._state()

    def object_witnessed(self, request_id, *, store_receipt_commit_hash):
        with self._guard():
            self._guard_attempt(request_id)
            digest(store_receipt_commit_hash, 'SESSION_LEDGER_STORE_RECEIPT_HASH')
            self._append({'op': 'object_witnessed', 'request_id': request_id,
                          'store_receipt_commit_hash': store_receipt_commit_hash})
            self._state()

    def terminal(self, request_id, *, outcome, reason, report_reserved_bytes):
        with self._guard():
            attempt = self._guard_attempt(request_id)
            check(attempt['state'] in ('ACCOUNTED', 'WITNESSED'),
                  'SESSION_LEDGER_BAD_TRANSITION')
            check(outcome in ('SUCCESS', 'FAILED', 'AMBIGUOUS_HELD'),
                  'SESSION_LEDGER_TERMINAL_OUTCOME')
            check(type(reason) is str and reason, 'SESSION_LEDGER_TERMINAL_REASON')
            # Report reserve survives ordinary failure: it is fixed at
            # construction (replayed identically on every restart), never
            # re-derived or re-negotiated at report time.
            check(report_reserved_bytes == self.report_reserve_bytes,
                  'SESSION_LEDGER_REPORT_RESERVE_MISMATCH')
            self._append({'op': 'terminal', 'request_id': request_id, 'outcome': outcome,
                          'reason': reason, 'report_reserved_bytes': report_reserved_bytes})
            self._state()
