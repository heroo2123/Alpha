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
  denial/cooldown history (section 3). The root itself carries its own
  genesis/lineage identity (a digest-bound review reference, plus a
  mandatory expected history head on every reopen of a non-empty root); it
  is not bound to any one caller's manifest, so distinct jobs with distinct
  manifests may legitimately share it across time (never concurrently: only
  one open intent at a time). Each intent instead records its own caller
  manifest. A denial is recorded durably and immediately via
  ``denial_observed`` while the intent stays open; closing the token
  (``intent_closed``) is a strictly later, separate event, so the control
  domain is blocked at once without releasing the global token early.
  Known, explicitly stated limitation (section 3: "Different denial roots
  are not interchangeable"): this module cannot and does not correlate a
  control domain's denial history *across two distinct root directories*.
  A fresh root has no memory of a denial recorded in a different root.
  Only an out-of-scope, owner-reviewed root installation step establishes
  that a given directory is *the* reviewed shared root for a control
  domain; this offline slice does not perform or simulate that step.
- ``SessionLedger``: the per-run "session root" durable order from section 4
  step 1-6 (ATTEMPT_INTENT, BUDGET_RESERVED, DISPATCH_INTENT, an optional
  non-terminal denial annotation, TRANSPORT_CLOSED, ACCOUNTED, OBJECT_
  WITNESSED, terminal/report), with a single open attempt at a time. A
  denial observed in headers/status can only be recorded once an attempt is
  DISPATCHED (pre-dispatch blocking is ``refuse``, never ``denial``); it is
  a durable annotation on that attempt, not a terminal outcome, so the
  attempt still requires TRANSPORT_CLOSED and ACCOUNTED before any
  non-success terminal. SUCCESS is only reachable from WITNESSED (ACCOUNTED
  alone proves accounting, not a store receipt). An observed overdelivery
  (more bytes than the attempt's own reservation) is recorded durably,
  blocks that attempt from ever reaching SUCCESS, and permanently poisons
  the whole session ledger against any further attempt, surviving restart.

Both ledgers compare the caller-supplied ``boot_id`` against the one
recorded at genesis on every reopen (section 5: "Cross-boot acquisition is
refused"); a mismatch never aborts construction (read-only inspection of a
foreign-boot root stays possible) but permanently refuses every further
progression call in that process. ``boot_id`` has no default: a caller must
state it explicitly rather than inherit a shared placeholder.

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

import base64
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

# Gate 3 V4 slice-3 repair (independent review commit 8e446fd, findings R2-R4,
# R7): both ledgers gained new durable fields/ops (denial raw-evidence bytes,
# the elapsed-deadline/closure-monotonic/report-completion records below).
# An old slice-2 journal predates these and is never silently reinterpreted:
# every genesis record now carries an explicit ``schema_version``, and
# reopening a root whose genesis lacks the current value refuses outright,
# before any new-field access can ever reach an old-format event.
SHARED_LEDGER_SCHEMA_VERSION = 2
SESSION_LEDGER_SCHEMA_VERSION = 2
# R4: "Raw evidence must be durably retained or have an explicit
# missing-evidence cause; hashing an ephemeral header tuple does not retain
# its bytes." Bounded like the store's own clock raw-evidence cap in spirit
# (tools/v11_r09_gate3_store_v1.py's MAX_CLOCK_RAW), sized for the header
# bound (<=4096 bytes) plus status/structure overhead.
MAX_DENIAL_EVIDENCE_RAW_BYTES = 8192


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
        # Reopening a root recorded under a different boot never aborts
        # construction (read-only inspection stays possible); it instead
        # permanently refuses every further progression call via
        # ``_healthy()``. Subclasses flip this to False after comparing the
        # replayed genesis ``boot_id`` against the caller-supplied one.
        self._boot_ok = True
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
        check(self._boot_ok, 'LEDGER_BOOT_ID_MISMATCH')
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

    The root's own genesis/lineage identity is a digest-bound review
    reference (``genesis_review_digest``), recorded once when the root is
    empty; every later reopen of a non-empty root must state the exact
    ``expected_history_head`` it observed last, or it refuses (a hash chain
    alone cannot detect a coherent rollback to a valid prefix without an
    independently retained head). The root is not bound to any single
    caller manifest: each intent records its own ``manifest_sha256``, so
    distinct jobs may legitimately reuse the same reviewed root over time.
    """
    LOCK_NAME = 'gate3_shared.lock'
    JOURNAL_NAME = 'gate3_shared.jsonl'

    def __init__(self, directory, *, boot_id, genesis_review_digest=None,
                 expected_history_head=None, max_requests=3600):
        check(type(boot_id) is str and boot_id, 'SHARED_LEDGER_BOOT_ID')
        _optional_digest(genesis_review_digest, 'SHARED_LEDGER_GENESIS_DIGEST')
        _optional_digest(expected_history_head, 'SHARED_LEDGER_HISTORY_HEAD')
        integer(max_requests, 1, 3600, 'SHARED_LEDGER_REQUEST_CAP')
        super().__init__(directory)
        try:
            self._replay()
            if not self.events:
                # Design section 3: "empty new files are not evidence of no
                # past denials". A brand-new empty root may only be used
                # once genesis is explicitly, digest-bound reviewed, and
                # never together with a claimed external head (nothing
                # exists yet to bind).
                check(expected_history_head is None,
                      'SHARED_LEDGER_LINEAGE_HEAD_MISMATCH')
                check(genesis_review_digest is not None,
                      'SHARED_LEDGER_LINEAGE_UNREVIEWED')
                self._append({'op': 'init', 'genesis_review_digest': genesis_review_digest,
                              'boot_id': boot_id, 'max_requests': max_requests,
                              'schema_version': SHARED_LEDGER_SCHEMA_VERSION})
            else:
                # Genesis is recorded once, at creation; it is never
                # re-asserted or re-reviewed on a later reopen.
                check(genesis_review_digest is None,
                      'SHARED_LEDGER_GENESIS_ALREADY_RECORDED')
                # Mandatory on every non-empty root: a hash chain alone
                # cannot detect rollback to a valid prefix.
                check(expected_history_head is not None,
                      'SHARED_LEDGER_LINEAGE_HEAD_REQUIRED')
                check(self.prev == expected_history_head,
                      'SHARED_LEDGER_LINEAGE_HEAD_MISMATCH')
                init = self.events[0]
                # A root genesis written before this repair has no
                # ``schema_version`` key at all: refuse explicitly rather
                # than silently replaying new-field events it could never
                # have written (slice-3 repair, R2/R4; never a silent
                # journal migration).
                check(init.get('op') == 'init' and init.get('max_requests') == max_requests and
                      init.get('schema_version') == SHARED_LEDGER_SCHEMA_VERSION,
                      'SHARED_LEDGER_IDENTITY_MISMATCH')
                if init.get('boot_id') != boot_id:
                    self._boot_ok = False
            self.genesis_review_digest = self.events[0].get('genesis_review_digest')
            # The constructor has verified this caller supplied current head
            # before any runtime can append to this acquired root.
            self.expected_history_head = expected_history_head
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
                     'manifest_sha256', 'max_reservation_bytes')}
                self.open_intent['denial_recorded'] = False
                self.open_count += 1
            elif op == 'denial_observed':
                check(self.open_intent is not None and
                      self.open_intent['request_id'] == event['request_id'],
                      'SHARED_LEDGER_DENIAL_WITHOUT_OPEN')
                check(not self.open_intent['denial_recorded'],
                      'SHARED_LEDGER_DENIAL_ALREADY_RECORDED')
                self._apply_denial(self.open_intent['control_domain_id'], event['denial'])
                self.open_intent['denial_recorded'] = True
            elif op == 'restriction_unresolved':
                check(self.open_intent is not None and
                      self.open_intent['request_id'] == event['request_id'] and
                      not self.open_intent['denial_recorded'],
                      'SHARED_LEDGER_RESTRICTION_WITHOUT_OPEN')
                restriction = self._validate_restriction(event['restriction'])
                self.denials[self.open_intent['control_domain_id']] = {
                    'status': restriction['status'], 'reason': restriction['reason'],
                    'cooldown_until': None}
                self.open_intent['denial_recorded'] = True
            elif op == 'intent_closed':
                check(self.open_intent is not None and
                      self.open_intent['request_id'] == event['request_id'],
                      'SHARED_LEDGER_CLOSE_WITHOUT_OPEN')
                if event['outcome'] == 'DENIED':
                    check(self.open_intent['denial_recorded'],
                          'SHARED_LEDGER_DENIAL_NOT_OBSERVED')
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
        previous = self.denials.get(control_domain_id)
        if previous is not None:
            prior_until = previous['cooldown_until']
            if prior_until is None or cooldown_until is None:
                cooldown_until = None
            else:
                cooldown_until = max(prior_until, cooldown_until)
        self.denials[control_domain_id] = {
            'status': denial['status'], 'reason': denial['reason'],
            'cooldown_until': cooldown_until}

    @staticmethod
    def _validate_denial(denial):
        exact(denial, ('status', 'reason', 'origin', 'evidence_sha256',
                       'evidence_raw_b64', 'evidence_missing_cause',
                       'receipt_clock_sha256', 'receipt_clock_raw_b64',
                       'retry_after_seconds', 'window_end_utc',
                       'receipt_upper_bound_utc'), 'SHARED_LEDGER_DENIAL_SCHEMA')
        check(type(denial['origin']) is str and denial['origin'].startswith('https://'),
              'SHARED_LEDGER_DENIAL_ORIGIN')
        try:
            clock_raw = base64.b64decode(denial['receipt_clock_raw_b64'], validate=True)
        except (ValueError, TypeError) as exc:
            raise LaunchContractError('SHARED_LEDGER_DENIAL_CLOCK') from exc
        check(0 < len(clock_raw) <= 16384 and
              hashlib.sha256(clock_raw).hexdigest() == denial['receipt_clock_sha256'],
              'SHARED_LEDGER_DENIAL_CLOCK')
        check(denial['status'] in ('401', '403', '429', '503', 'EXPLICIT_DENIAL', 'OTHER'),
              'SHARED_LEDGER_DENIAL_STATUS')
        check(type(denial['reason']) is str and denial['reason'], 'SHARED_LEDGER_DENIAL_REASON')
        check((denial['evidence_sha256'] is None) != (denial['evidence_missing_cause'] is None),
              'SHARED_LEDGER_DENIAL_EVIDENCE')
        if denial['evidence_sha256'] is not None:
            digest(denial['evidence_sha256'], 'SHARED_LEDGER_DENIAL_EVIDENCE')
            # R4: "hashing an ephemeral header tuple does not retain its
            # bytes" -- the raw status/header evidence is retained inline,
            # bounded, alongside its hash, never just the digest alone.
            check(type(denial['evidence_raw_b64']) is str and denial['evidence_raw_b64'],
                  'SHARED_LEDGER_DENIAL_EVIDENCE')
            try:
                raw = base64.b64decode(denial['evidence_raw_b64'], validate=True)
            except (ValueError, TypeError) as exc:
                raise LaunchContractError('SHARED_LEDGER_DENIAL_EVIDENCE') from exc
            check(0 < len(raw) <= MAX_DENIAL_EVIDENCE_RAW_BYTES and
                  hashlib.sha256(raw).hexdigest() == denial['evidence_sha256'],
                  'SHARED_LEDGER_DENIAL_EVIDENCE')
        else:
            check(denial['evidence_raw_b64'] is None, 'SHARED_LEDGER_DENIAL_EVIDENCE')
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

    @staticmethod
    def _validate_restriction(restriction):
        """An unresolved observation has its own strict schema. It never
        asserts that an invalid or missing receipt clock was verified."""
        exact(restriction, ('status', 'reason', 'origin', 'evidence_sha256',
                           'evidence_raw_b64', 'evidence_missing_cause',
                           'receipt_evidence_cause'),
              'SHARED_LEDGER_RESTRICTION_SCHEMA')
        check(restriction['status'] in ('401', '403', '429', '503', 'OTHER') and
              type(restriction['reason']) is str and restriction['reason'] and
              type(restriction['origin']) is str and
              restriction['origin'].startswith('https://') and
              type(restriction['receipt_evidence_cause']) is str and
              0 < len(restriction['receipt_evidence_cause']) <= 512,
              'SHARED_LEDGER_RESTRICTION_VALUE')
        check((restriction['evidence_sha256'] is None) !=
              (restriction['evidence_missing_cause'] is None),
              'SHARED_LEDGER_RESTRICTION_EVIDENCE')
        if restriction['evidence_sha256'] is not None:
            digest(restriction['evidence_sha256'], 'SHARED_LEDGER_RESTRICTION_EVIDENCE')
            try:
                raw = base64.b64decode(restriction['evidence_raw_b64'], validate=True)
            except (ValueError, TypeError) as exc:
                raise LaunchContractError('SHARED_LEDGER_RESTRICTION_EVIDENCE') from exc
            check(0 < len(raw) <= MAX_DENIAL_EVIDENCE_RAW_BYTES and
                  hashlib.sha256(raw).hexdigest() == restriction['evidence_sha256'],
                  'SHARED_LEDGER_RESTRICTION_EVIDENCE')
        else:
            check(restriction['evidence_raw_b64'] is None and
                  type(restriction['evidence_missing_cause']) is str and
                  restriction['evidence_missing_cause'],
                  'SHARED_LEDGER_RESTRICTION_EVIDENCE')
        return dict(restriction)

    def is_blocked(self, control_domain_id, *, now_utc):
        self._healthy()
        check(type(now_utc) in (int, float) and math.isfinite(now_utc),
              'SHARED_LEDGER_NOW_UTC')
        record = self.denials.get(control_domain_id)
        if record is None:
            return False
        return record['cooldown_until'] is None or now_utc < record['cooldown_until']

    def intent_open(self, request_id, *, purpose, endpoint_id, control_domain_id,
                     manifest_sha256, max_reservation_bytes, now_utc):
        with self._guard():
            self._healthy()
            check(type(request_id) is str and REQUEST_ID_RE.fullmatch(request_id),
                  'SHARED_LEDGER_REQUEST_ID')
            check(purpose in PURPOSES, 'SHARED_LEDGER_PURPOSE')
            digest(endpoint_id, 'SHARED_LEDGER_ENDPOINT_ID')
            digest(control_domain_id, 'SHARED_LEDGER_CONTROL_DOMAIN_ID')
            digest(manifest_sha256, 'SHARED_LEDGER_MANIFEST_DIGEST')
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
                          'manifest_sha256': manifest_sha256,
                          'max_reservation_bytes': max_reservation_bytes})
            self._state()

    def denial_observed(self, request_id, *, denial):
        """Record a denial immediately when headers/status make it known.

        Legal only while ``request_id``'s intent is still open. Blocks the
        control domain at once (updates ``self.denials``) but deliberately
        does **not** clear ``open_intent``: the single global token stays
        held until a later, separate ``intent_closed`` call, matching
        section 4's "Keep a single global token until transport is closed
        and settlement complete."
        """
        with self._guard():
            self._healthy()
            check(self.open_intent is not None and
                  self.open_intent['request_id'] == request_id,
                  'SHARED_LEDGER_CLOSE_WITHOUT_OPEN')
            check(request_id != self.inherited_open_request_id,
                  'SHARED_LEDGER_INHERITED_INTENT_HELD')
            check(not self.open_intent['denial_recorded'],
                  'SHARED_LEDGER_DENIAL_ALREADY_RECORDED')
            denial = self._validate_denial(denial)
            self._append({'op': 'denial_observed', 'request_id': request_id,
                          'denial': denial})
            self._state()

    def restriction_unresolved(self, request_id, *, restriction):
        with self._guard():
            self._healthy()
            check(self.open_intent is not None and
                  self.open_intent['request_id'] == request_id and
                  request_id != self.inherited_open_request_id and
                  not self.open_intent['denial_recorded'],
                  'SHARED_LEDGER_RESTRICTION_WITHOUT_OPEN')
            restriction = self._validate_restriction(restriction)
            self._append({'op': 'restriction_unresolved', 'request_id': request_id,
                          'restriction': restriction})
            self._state()

    def intent_closed(self, request_id, *, outcome, accounting_head=None,
                       total_delivered_bytes=None):
        with self._guard():
            self._healthy()
            check(self.open_intent is not None and
                  self.open_intent['request_id'] == request_id,
                  'SHARED_LEDGER_CLOSE_WITHOUT_OPEN')
            check(request_id != self.inherited_open_request_id,
                  'SHARED_LEDGER_INHERITED_INTENT_HELD')
            # AMBIGUOUS is deliberately not an accepted close outcome:
            # ambiguity cannot complete. A caller facing it must simply not
            # close the intent, so the token is inherited and held exactly
            # like a crash would (the generalized R2 rule above), rather
            # than being released by a close call that lacks exact closure
            # evidence (section 4: "future jobs inherit a control-domain
            # uncertainty hold unless exact closure evidence exists").
            check(outcome in ('OK', 'DENIED', 'FAILED'), 'SHARED_LEDGER_CLOSE_OUTCOME')
            if outcome == 'DENIED':
                check(self.open_intent['denial_recorded'],
                      'SHARED_LEDGER_DENIAL_NOT_OBSERVED')
            else:
                # R3: outcome must be DENIED exactly when a denial was
                # observed on this intent; OK/FAILED cannot paper over a
                # recorded denial.
                check(not self.open_intent['denial_recorded'],
                      'SHARED_LEDGER_CLOSE_OUTCOME_DENIAL_MISMATCH')
            # R3: "Shared INTENT_CLOSED must contain a complete bounded
            # outcome; it cannot merely mean the caller invoked close()".
            # Bind an accounting head and the exact delivered byte count the
            # caller observed, plus this root's own current chain head as
            # the denial-history binding (it already commits to every
            # denial_observed event recorded for this intent).
            digest(accounting_head, 'SHARED_LEDGER_CLOSE_ACCOUNTING_HEAD')
            integer(total_delivered_bytes, 0, MAX_BYTES,
                    'SHARED_LEDGER_CLOSE_DELIVERED_BYTES')
            self._append({'op': 'intent_closed', 'request_id': request_id,
                          'outcome': outcome, 'accounting_head': accounting_head,
                          'total_delivered_bytes': total_delivered_bytes,
                          'denial_history_head': self.prev})
            self._state()


class SessionLedger(_HashChainJournal):
    """Durable, per-run, process-restart-safe session attempt ledger.

    Implements the durable order from design section 4 steps 1-6 for a
    single frozen request at a time: ATTEMPT_INTENT, BUDGET_RESERVED,
    DISPATCH_INTENT, an optional non-terminal ``denial`` annotation, then
    TRANSPORT_CLOSED -> ACCOUNTED -> optional OBJECT_WITNESSED -> terminal
    (the report boundary). Only one attempt may be open at a time ("at most
    one unfinished intent", section 3). An attempt left open by a prior
    process is snapshotted as ``inherited_request_id``; every further
    progression call against that exact request is permanently refused in
    every later process, the same generalized R2 rule as ``SharedLedger``.

    A denial observed in headers/status can only be recorded once an
    attempt has been dispatched (pre-dispatch blocking uses ``refuse``
    instead, section 3) and does not end the attempt: section 4 steps 4-6
    still apply, so TRANSPORT_CLOSED and ACCOUNTED (and the outstanding
    budget reservation they settle) are still required before any terminal.
    SUCCESS is only reachable from WITNESSED; ACCOUNTED alone "proves
    accounting only" (section 6) and may terminate as FAILED. An observed
    overdelivery (section 4: "An observed overdelivery halts") is recorded
    durably, blocks that attempt's own terminal from ever being SUCCESS,
    and permanently poisons the whole session ledger against any further
    ``attempt_intent``, surviving restart via ordinary replay.
    """
    LOCK_NAME = 'gate3_session.lock'
    JOURNAL_NAME = 'gate3_session.jsonl'

    _ORDER = {'budget_reserved': 'OPEN', 'dispatch_intent': 'RESERVED',
              'transport_closed': 'DISPATCHED', 'accounted': 'CLOSED',
              'object_witnessed': 'ACCOUNTED'}
    _NEXT = {'budget_reserved': 'RESERVED', 'dispatch_intent': 'DISPATCHED',
             'transport_closed': 'CLOSED', 'accounted': 'ACCOUNTED',
             'object_witnessed': 'WITNESSED'}

    def __init__(self, directory, *, manifest_sha256, boot_id,
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
                              'max_requests': max_requests,
                              'schema_version': SESSION_LEDGER_SCHEMA_VERSION})
            else:
                init = self.events[0]
                # See SharedLedger's identical rationale: a pre-repair
                # genesis has no ``schema_version`` and must refuse rather
                # than replay the new elapsed-deadline/closure-monotonic/
                # report-completion events below against it.
                check(init.get('op') == 'init' and init.get('manifest') == manifest_sha256 and
                      init.get('report_reserve_bytes') == report_reserve_bytes and
                      init.get('max_requests') == max_requests and
                      init.get('schema_version') == SESSION_LEDGER_SCHEMA_VERSION,
                      'SESSION_LEDGER_IDENTITY_MISMATCH')
                if init.get('boot_id') != boot_id:
                    self._boot_ok = False
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
        self.overdelivery_poisoned = False
        # Gate 3 V4 slice-3 repair (R2/R3/R7): durable, ledger-wide (not
        # per-attempt) state that must survive a fresh attempt_intent
        # overwriting ``self.attempt`` and must survive process restart via
        # ordinary replay.
        self.elapsed_deadline_mono = None
        self.clock_offset_interval = None
        self.last_clock_monotonic = None
        self.runtime_context_sha256 = None
        self.last_closure_monotonic = None
        self.attempt_history = {}
        self.capture_receipts = {}
        self.report_completed_sha256 = None
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
                self.attempt = {'request_id': rid, 'state': 'OPEN', 'outcome': None,
                                 'purpose': event['purpose'],
                                 'max_reservation_bytes': event['max_reservation_bytes'],
                                 'denial_observed': False, 'overdelivered': False}
            elif op in self._ORDER:
                check(self.attempt is not None and self.attempt['request_id'] == rid and
                      self.attempt['state'] == self._ORDER[op],
                      'SESSION_LEDGER_BAD_TRANSITION')
                self.attempt['state'] = self._NEXT[op]
                if op == 'budget_reserved':
                    self.attempt['reserve_event_hash'] = event['reserve_event_hash']
                elif op == 'accounted':
                    self.attempt['completion_event_hash'] = event['completion_event_hash']
                elif op == 'object_witnessed':
                    self.attempt['store_receipt_commit_hash'] = event['store_receipt_commit_hash']
                if op == 'transport_closed':
                    try:
                        close_raw = base64.b64decode(event['closure_evidence_raw_b64'],
                                                     validate=True)
                    except (ValueError, TypeError) as exc:
                        raise LaunchContractError('SESSION_LEDGER_CLOSURE_EVIDENCE') from exc
                    check(0 < len(close_raw) <= 8192 and
                          hashlib.sha256(close_raw).hexdigest() ==
                          event['closure_evidence_sha256'],
                          'SESSION_LEDGER_CLOSURE_EVIDENCE')
                    self.attempt['closure_evidence_sha256'] = event[
                        'closure_evidence_sha256']
                    # R4: recompute independently from the recorded delivery
                    # count and the attempt's own reservation at every
                    # replay, rather than trusting a stored boolean flag.
                    if event['total_delivered_bytes'] > self.attempt['max_reservation_bytes']:
                        self.attempt['overdelivered'] = True
                        self.overdelivery_poisoned = True
                    self.attempt['total_delivered_bytes'] = event['total_delivered_bytes']
                    # R3: the durable closure monotonic sample this attempt's
                    # own TRANSPORT_CLOSED recorded, exposed ledger-wide so
                    # the *next* attempt's pre-dispatch pacing check (owned
                    # by the runtime, not this ledger) can require its own
                    # dispatch sample >= this value + the frozen interval,
                    # including across restart.
                    self.attempt['closure_monotonic'] = event['closure_monotonic']
                    self.last_closure_monotonic = event['closure_monotonic']
            elif op == 'refuse':
                check(self.attempt is not None and self.attempt['request_id'] == rid and
                      self.attempt['state'] == 'OPEN', 'SESSION_LEDGER_BAD_TRANSITION')
                self.attempt['state'] = 'TERMINAL'
                self.attempt['outcome'] = 'REFUSED'
                self.attempt['reason'] = event['reason']
                reasons = event.get('reasons', [event['reason']])
                check(type(reasons) is list and reasons and
                      all(type(value) is str and value for value in reasons) and
                      len(set(reasons)) == len(reasons) and
                      event['reason'] in reasons, 'SESSION_LEDGER_REFUSE_REASONS')
                self.attempt['reasons'] = reasons
                self.completed_count += 1
                self.attempt_history[rid] = dict(self.attempt)
            elif op == 'elapsed_deadline_fixed':
                # R2: persisted once, from the first valid session clock;
                # never recomputed or widened on a later reopen (enforced by
                # ``fix_elapsed_deadline`` refusing a differing value).
                check(self.elapsed_deadline_mono is None, 'SESSION_LEDGER_ELAPSED_DEADLINE_RESET')
                self.elapsed_deadline_mono = event['value']
            elif op == 'request_deadline_fixed':
                check(self.attempt is not None and
                      self.attempt['request_id'] == rid and
                      self.attempt['state'] == 'RESERVED' and
                      'deadline_monotonic' not in self.attempt and
                      type(event['value']) in (int, float) and
                      math.isfinite(event['value']) and event['value'] >= 0,
                      'SESSION_LEDGER_REQUEST_DEADLINE')
                self.attempt['deadline_monotonic'] = event['value']
            elif op == 'runtime_context_bound':
                digest(event['sha256'], 'SESSION_LEDGER_RUNTIME_CONTEXT')
                check(self.runtime_context_sha256 is None,
                      'SESSION_LEDGER_RUNTIME_CONTEXT_REBOUND')
                self.runtime_context_sha256 = event['sha256']
            elif op == 'clock_observed':
                try:
                    raw = base64.b64decode(event['raw_b64'], validate=True)
                except (ValueError, TypeError) as exc:
                    raise LaunchContractError('SESSION_LEDGER_CLOCK_EVIDENCE') from exc
                check(0 < len(raw) <= 16384 and
                      hashlib.sha256(raw).hexdigest() == event['evidence_sha256'],
                      'SESSION_LEDGER_CLOCK_EVIDENCE')
                vals = (event['monotonic'], event['offset_low'], event['offset_high'])
                check(all(type(v) in (int, float) and math.isfinite(v) for v in vals) and
                      event['offset_low'] <= event['offset_high'],
                      'SESSION_LEDGER_CLOCK_EVENT')
                check(self.last_clock_monotonic is None or
                      event['monotonic'] >= self.last_clock_monotonic,
                      'SESSION_LEDGER_CLOCK_REVERSAL')
                previous = self.clock_offset_interval or (-math.inf, math.inf)
                low = max(previous[0], event['offset_low'])
                high = min(previous[1], event['offset_high'])
                check(low <= high, 'SESSION_LEDGER_CLOCK_STEP')
                self.clock_offset_interval = (low, high)
                self.last_clock_monotonic = event['monotonic']
            elif op == 'report_completed':
                check(self.report_completed_sha256 is None,
                      'SESSION_LEDGER_REPORT_ALREADY_COMPLETED')
                self.report_completed_sha256 = event['report_sha256']
            elif op == 'capture_receipt':
                record = event['record']
                exact(record, ('version', 'manifest', 'plan_sha256', 'request_sha256',
                    'request_id', 'purpose', 'endpoint_id', 'source_pin', 'decoder_pin',
                    'outcome', 'session_terminal_head', 'shared_head', 'budget_head',
                    'store_receipt_commit_hash', 'raw_sha256', 'clock_evidence_sha256',
                    'dependencies', 'prerequisite_request_ids',
                    'known_delivered_bytes', 'deadline_monotonic',
                    'closure_monotonic',
                    'closure_evidence_sha256'),
                    'SESSION_LEDGER_CAPTURE_SCHEMA')
                check(record['version'] == 1 and record['manifest'] == self.manifest and
                      record['request_id'] in self.attempt_history and
                      record['request_id'] not in self.capture_receipts and
                      record['purpose'] == self.attempt_history[record['request_id']]['purpose'] and
                      record['outcome'] == self.attempt_history[record['request_id']]['outcome'],
                      'SESSION_LEDGER_CAPTURE_CONTEXT')
                for key in ('plan_sha256', 'request_sha256', 'endpoint_id',
                            'source_pin', 'decoder_pin', 'session_terminal_head',
                            'shared_head', 'budget_head'):
                    digest(record[key], 'SESSION_LEDGER_CAPTURE_DIGEST')
                for key in ('store_receipt_commit_hash', 'raw_sha256',
                            'closure_evidence_sha256'):
                    if record[key] is not None:
                        digest(record[key], 'SESSION_LEDGER_CAPTURE_DIGEST')
                check(type(record['clock_evidence_sha256']) is list and
                      all(type(h) is str for h in record['clock_evidence_sha256']) and
                      type(record['dependencies']) is list and
                      all(type(h) is str for h in record['dependencies']) and
                      type(record['prerequisite_request_ids']) is list and
                      all(type(v) is str and REQUEST_ID_RE.fullmatch(v)
                          for v in record['prerequisite_request_ids']),
                      'SESSION_LEDGER_CAPTURE_GRAPH')
                for h in record['clock_evidence_sha256'] + record['dependencies']:
                    digest(h, 'SESSION_LEDGER_CAPTURE_DIGEST')
                integer(record['known_delivered_bytes'], 0, MAX_BYTES,
                        'SESSION_LEDGER_CAPTURE_BYTES')
                check(record['closure_monotonic'] is None or
                      (type(record['closure_monotonic']) in (int, float) and
                       math.isfinite(record['closure_monotonic'])),
                      'SESSION_LEDGER_CAPTURE_CLOSURE')
                check(record['deadline_monotonic'] ==
                      self.attempt_history[record['request_id']].get(
                          'deadline_monotonic'),
                      'SESSION_LEDGER_CAPTURE_DEADLINE')
                check(record['closure_evidence_sha256'] ==
                      self.attempt_history[record['request_id']].get(
                          'closure_evidence_sha256'),
                      'SESSION_LEDGER_CAPTURE_CLOSURE')
                self.capture_receipts[record['request_id']] = record
            elif op == 'denial':
                # A non-terminal annotation: pre-dispatch blocking is
                # ``refuse``, and the attempt still requires
                # TRANSPORT_CLOSED/ACCOUNTED before any terminal (section 4
                # steps 4-6 still apply after a denial is observed).
                check(self.attempt is not None and self.attempt['request_id'] == rid and
                      self.attempt['state'] == 'DISPATCHED', 'SESSION_LEDGER_BAD_TRANSITION')
                check(not self.attempt['denial_observed'],
                      'SESSION_LEDGER_DENIAL_ALREADY_RECORDED')
                self.attempt['denial_observed'] = True
            elif op == 'terminal':
                check(self.attempt is not None and self.attempt['request_id'] == rid and
                      self.attempt['state'] in ('ACCOUNTED', 'WITNESSED'),
                      'SESSION_LEDGER_BAD_TRANSITION')
                self.attempt['state'] = 'TERMINAL'
                self.attempt['outcome'] = event['outcome']
                self.attempt['reason'] = event['reason']
                self.completed_count += 1
                # R7: a durable, replay-derived history of every terminal
                # attempt this session ever made, keyed by request_id, so a
                # terminal report can be built from verified ledger state
                # instead of trusting an arbitrary caller-constructed dict.
                self.attempt_history[rid] = dict(self.attempt)
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
            # Durable, permanent hold: once any attempt in this session has
            # ever been observed to overdeliver, no further attempt may be
            # opened (section 4: "An observed overdelivery halts").
            check(not self.overdelivery_poisoned, 'SESSION_LEDGER_OVERDELIVERY_POISONED')
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

    def refuse(self, request_id, *, reason, reasons=None):
        with self._guard():
            self._guard_attempt(request_id)
            check(self.attempt['state'] == 'OPEN', 'SESSION_LEDGER_BAD_TRANSITION')
            check(type(reason) is str and reason, 'SESSION_LEDGER_REFUSE_REASON')
            reasons = [reason] if reasons is None else list(reasons)
            check(reasons and len(set(reasons)) == len(reasons) and
                  reason in reasons and all(type(value) is str and value
                  for value in reasons), 'SESSION_LEDGER_REFUSE_REASONS')
            self._append({'op': 'refuse', 'request_id': request_id,
                          'reason': reason, 'reasons': reasons})
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
        """Record a denial observed in headers/status as a durable,
        non-terminal annotation on a DISPATCHED attempt. Pre-dispatch
        blocking must use ``refuse`` instead; a denial cannot be observed
        before dispatch. The attempt still requires TRANSPORT_CLOSED and
        ACCOUNTED before any (necessarily non-SUCCESS) terminal.
        """
        with self._guard():
            self._guard_attempt(request_id)
            check(self.attempt['state'] == 'DISPATCHED', 'SESSION_LEDGER_BAD_TRANSITION')
            check(not self.attempt['denial_observed'],
                  'SESSION_LEDGER_DENIAL_ALREADY_RECORDED')
            check(type(reason) is str and reason, 'SESSION_LEDGER_DENIAL_REASON')
            _optional_digest(shared_denial_event_hash, 'SESSION_LEDGER_DENIAL_HASH')
            self._append({'op': 'denial', 'request_id': request_id, 'reason': reason,
                          'shared_denial_event_hash': shared_denial_event_hash})
            self._state()

    def transport_closed(self, request_id, *, outcome, total_delivered_bytes,
                          closure_monotonic, closure_evidence_raw,
                          denial_history_head=None, accounting_head=None):
        with self._guard():
            self._guard_attempt(request_id)
            check(outcome in ('OK', 'FAILED', 'PARTIAL'), 'SESSION_LEDGER_CLOSE_OUTCOME')
            integer(total_delivered_bytes, 0, MAX_BYTES, 'SESSION_LEDGER_DELIVERED_BYTES')
            # R3: the actual monotonic sample taken after the transport's own
            # explicit, known closure -- "sample monotonic time after each
            # known transport closure, persist it in TRANSPORT_CLOSED" -- so
            # the next attempt's dispatch can be bound to it durably, across
            # restart, rather than only to the (earlier, fsync-delayable)
            # reservation/dispatch-intent samples.
            check(type(closure_monotonic) in (int, float) and
                  math.isfinite(closure_monotonic) and closure_monotonic >= 0,
                  'SESSION_LEDGER_CLOSURE_MONOTONIC')
            check(type(closure_evidence_raw) is bytes and
                  0 < len(closure_evidence_raw) <= 8192,
                  'SESSION_LEDGER_CLOSURE_EVIDENCE')
            # R3: mandatory, not optional (no default binding was ever
            # evidence of a complete bounded outcome).
            digest(denial_history_head, 'SESSION_LEDGER_DENIAL_HISTORY_HEAD')
            digest(accounting_head, 'SESSION_LEDGER_ACCOUNTING_HEAD')
            self._append({'op': 'transport_closed', 'request_id': request_id,
                          'outcome': outcome, 'total_delivered_bytes': total_delivered_bytes,
                          'closure_monotonic': closure_monotonic,
                          'closure_evidence_raw_b64':
                              base64.b64encode(closure_evidence_raw).decode('ascii'),
                          'closure_evidence_sha256':
                              hashlib.sha256(closure_evidence_raw).hexdigest(),
                          'denial_history_head': denial_history_head,
                          'accounting_head': accounting_head})
            self._state()

    def accounted(self, request_id, *, completion_event_hash):
        with self._guard():
            attempt = self._guard_attempt(request_id)
            # R4: an observed overdelivery blocks ACCOUNTED, not merely
            # SUCCESS; ACCOUNTED binds a budget completion-event hash, which
            # an overdelivered attempt must never receive. The attempt stays
            # held in CLOSED state (the session is already poisoned).
            check(not attempt['overdelivered'],
                  'SESSION_LEDGER_OVERDELIVERY_BLOCKS_ACCOUNTED')
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
            # AMBIGUOUS_HELD is deliberately not an accepted terminal
            # outcome: an ambiguous outcome must never become
            # TERMINAL-and-released. A caller facing ambiguity must simply
            # not call terminal, so the attempt is inherited and held on
            # restart exactly like a crash (the generalized R2 rule above).
            check(outcome in ('SUCCESS', 'FAILED'), 'SESSION_LEDGER_TERMINAL_OUTCOME')
            if outcome == 'SUCCESS':
                # Section 6: "ACCOUNTED without a store receipt proves
                # accounting only". SUCCESS requires the store receipt.
                check(attempt['state'] == 'WITNESSED',
                      'SESSION_LEDGER_SUCCESS_REQUIRES_WITNESS')
                check(not attempt['overdelivered'],
                      'SESSION_LEDGER_OVERDELIVERY_BLOCKS_SUCCESS')
                # R2 regression fix: a denied attempt is "(necessarily
                # non-SUCCESS) terminal" per this module's own docstring, but
                # the S5 repair (a non-terminal denial annotation) never
                # actually enforced that. A denied body is not a successful
                # validated body (section 6).
                check(not attempt['denial_observed'],
                      'SESSION_LEDGER_SUCCESS_AFTER_DENIAL')
            check(type(reason) is str and reason, 'SESSION_LEDGER_TERMINAL_REASON')
            # Report reserve survives ordinary failure: it is fixed at
            # construction (replayed identically on every restart), never
            # re-derived or re-negotiated at report time.
            check(report_reserved_bytes == self.report_reserve_bytes,
                  'SESSION_LEDGER_REPORT_RESERVE_MISMATCH')
            self._append({'op': 'terminal', 'request_id': request_id, 'outcome': outcome,
                          'reason': reason, 'report_reserved_bytes': report_reserved_bytes})
            self._state()

    def fix_elapsed_deadline(self, value):
        """Persist the session elapsed deadline exactly once (R2: "Persist
        that elapsed deadline from the first valid session clock"). Replaying
        this same value on every later attempt/restart is an idempotent
        no-op; a caller that ever computes a *different* value (the only way
        that could happen is a bug, since the design forbids widening it) is
        refused rather than silently accepted, matching "it never extends an
        already fixed request deadline."
        """
        with self._guard():
            self._healthy()
            check(type(value) in (int, float) and math.isfinite(value) and value >= 0,
                  'SESSION_LEDGER_ELAPSED_DEADLINE_VALUE')
            if self.elapsed_deadline_mono is not None:
                check(self.elapsed_deadline_mono == value,
                      'SESSION_LEDGER_ELAPSED_DEADLINE_WIDENED')
                return
            self._append({'op': 'elapsed_deadline_fixed', 'value': value})
            self._state()

    def fix_request_deadline(self, request_id, value):
        """Persist the one absolute transport deadline before dispatch."""
        with self._guard():
            self._guard_attempt(request_id)
            check(self.attempt['state'] == 'RESERVED' and
                  'deadline_monotonic' not in self.attempt and
                  type(value) in (int, float) and math.isfinite(value) and
                  value >= 0, 'SESSION_LEDGER_REQUEST_DEADLINE')
            self._append({'op': 'request_deadline_fixed',
                          'request_id': request_id, 'value': value})
            self._state()

    def bind_runtime_context(self, sha256):
        with self._guard():
            self._healthy()
            digest(sha256, 'SESSION_LEDGER_RUNTIME_CONTEXT')
            if self.runtime_context_sha256 is not None:
                check(self.runtime_context_sha256 == sha256,
                      'SESSION_LEDGER_RUNTIME_CONTEXT_MISMATCH')
                return
            self._append({'op': 'runtime_context_bound', 'sha256': sha256})
            self._state()

    def observe_clock(self, *, monotonic, offset_low, offset_high,
                      raw, evidence_sha256):
        with self._guard():
            self._healthy()
            vals = (monotonic, offset_low, offset_high)
            check(all(type(v) in (int, float) and math.isfinite(v) for v in vals) and
                  offset_low <= offset_high, 'SESSION_LEDGER_CLOCK_EVENT')
            check(self.last_clock_monotonic is None or
                  monotonic >= self.last_clock_monotonic, 'SESSION_LEDGER_CLOCK_REVERSAL')
            previous = self.clock_offset_interval or (-math.inf, math.inf)
            check(max(previous[0], offset_low) <= min(previous[1], offset_high),
                  'SESSION_LEDGER_CLOCK_STEP')
            check(type(raw) is bytes and 0 < len(raw) <= 16384 and
                  hashlib.sha256(raw).hexdigest() == evidence_sha256,
                  'SESSION_LEDGER_CLOCK_EVIDENCE')
            self._append({'op': 'clock_observed', 'monotonic': monotonic,
                          'offset_low': offset_low, 'offset_high': offset_high,
                          'raw_b64': base64.b64encode(raw).decode('ascii'),
                          'evidence_sha256': evidence_sha256})
            self._state()

    def report_completed(self, *, report_sha256):
        """Durably bind the one terminal report this session ever produces
        to its exact digest (R7: "a runtime/report completion record links
        a durable report digest to its context"). Recorded once; a caller
        that already completed a report for this session and calls again
        with a different digest is refused rather than silently replacing
        the earlier binding.
        """
        with self._guard():
            self._healthy()
            digest(report_sha256, 'SESSION_LEDGER_REPORT_DIGEST')
            if self.report_completed_sha256 is not None:
                check(self.report_completed_sha256 == report_sha256,
                      'SESSION_LEDGER_REPORT_ALREADY_COMPLETED')
                return
            self._append({'op': 'report_completed', 'report_sha256': report_sha256})
            self._state()

    def capture_receipt(self, record):
        with self._guard():
            self._healthy()
            check(type(record) is dict and type(record.get('request_id')) is str and
                  record['request_id'] in self.attempt_history,
                  'SESSION_LEDGER_CAPTURE_CONTEXT')
            check(len(canonical(record)) <= 16384, 'SESSION_LEDGER_CAPTURE_BOUND')
            self._append({'op': 'capture_receipt', 'record': record})
            self._state()
