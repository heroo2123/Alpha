"""Standalone Linux/POSIX offline fixture custody; never an authority consumer.

Only caller-supplied bytes are parsed. No observation, recorder invocation,
clock, network, account, credential or production integration capability.
See V11_GATE3_CLOCK_CUSTODY_HANDOFF_20261003.md for the trust boundary.
"""
from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import stat
import threading

from tools import v11_gate3_clock_dossier as dossier

MAX_RAW = 16_384
MAX_OBJECT = 65_536
MAX_TOTAL = 1_048_576
MAX_SAMPLES = 64
TERMINAL_RESERVE = 4_096
MAX_ENTRIES = MAX_SAMPLES + 3
_COMPONENT = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}\Z")
_META_KEYS = {"session_nonce", "method_id", "profile_id", "build_id", "host_id", "event_kind"}
_DIR_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
_FILE_FLAGS = os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK


def _flags():
    return dict(dossier.FIXED_AUTHORITY_FLAGS, custody_qualification=False,
                custody_status="CUSTODY_UNQUALIFIED")


class CustodyError(ValueError):
    """Bounded local diagnostic, never a claim that the diagnostic was stored."""
    def __init__(self, code, **evidence):
        self.code = code
        self.evidence = dict(_flags(), code=code, evidence_incomplete=True,
                             diagnostic_persisted=False, **evidence)
        super().__init__(code)


def _fail(code):
    raise CustodyError(code)


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=True, allow_nan=False).encode("ascii")


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


def _metadata(value):
    if (type(value) is not dict or len(value) != len(_META_KEYS) or
            any(type(k) is not str for k in value) or value.keys() != _META_KEYS):
        _fail("SCHEMA")
    for item in value.values():
        if type(item) is not str or _COMPONENT.fullmatch(item) is None:
            _fail("SCHEMA")
    if value["event_kind"] != "SYNTHETIC":
        _fail("SCHEMA")
    return dict(value)


def _header(metadata):
    return _canonical(dict(_flags(), schema="CLOCK_CUSTODY_FIXTURE_V1", metadata=metadata))


def _object(raw, nonce, sequence, prior):
    if type(raw) is not bytes or len(raw) > MAX_RAW:
        _fail("INPUT_BOUNDS")
    try:
        projection = dossier.parse_probe_record(raw)
        projection_hash = _sha(dossier._canonical(projection))
        status, reason = projection["status"], projection.get("code")
    except dossier.Refusal as error:
        # Even rejected/truncated fixture input remains exact binary evidence.
        projection_hash, status, reason = None, "PARSE_REFUSED", error.code
    envelope = dict(_flags(), schema="CLOCK_CUSTODY_OBJECT_V1", session_nonce=nonce,
                    sequence=sequence, prior_head=prior, raw_sha256=_sha(raw),
                    raw_byte_length=len(raw), parsed_projection_sha256=projection_hash,
                    parse_status=status, parse_reason=reason)
    encoded = _canonical(envelope)
    result = len(encoded).to_bytes(4, "big") + encoded + raw
    if len(result) > MAX_OBJECT:
        _fail("INPUT_BOUNDS")
    return result, envelope


def _terminal(nonce, count, head, total, reason):
    return _canonical(dict(_flags(), schema="CLOCK_CUSTODY_TERMINAL_V1",
                           session_nonce=nonce, count=count, head=head,
                           chain_byte_length=total, reason=reason))


def _identity(st):
    return st.st_dev, st.st_ino


def _private(st, directory=False, links=1):
    mode = 0o700 if directory else 0o600
    if (not (stat.S_ISDIR(st.st_mode) if directory else stat.S_ISREG(st.st_mode)) or
            stat.S_IMODE(st.st_mode) != mode or st.st_uid != os.geteuid() or
            (not directory and st.st_nlink != links)):
        _fail("STORE_IDENTITY")


class FixtureStore:
    """One session under an already-open, private directory anchor.

    `create=True` exclusively creates a new store. Existing stores are replay
    only: recovery never silently resumes an interrupted observation session.
    The anchor is caller-owned trust input; all descendant access is relative
    to pinned descriptors. Close explicitly or use a context manager.
    """
    def __init__(self, anchor_fd, name, metadata, *, create=False):
        self._anchor = self._root = -1
        self._mutex = threading.Lock()
        self._pid = os.getpid()
        self._poisoned = True
        self._writer = False
        self._metadata = _metadata(metadata)
        if (type(anchor_fd) is not int or anchor_fd < 0 or type(name) is not str or
                _COMPONENT.fullmatch(name) is None or type(create) is not bool):
            _fail("SCHEMA")
        self._name = name
        self._count, self._head, self._total = 0, None, 0
        self._sealed = False
        try:
            self._anchor = os.dup(anchor_fd)
            _private(os.fstat(self._anchor), directory=True)
            if create:
                os.mkdir(name, 0o700, dir_fd=self._anchor)
            named = os.stat(name, dir_fd=self._anchor, follow_symlinks=False)
            _private(named, directory=True)
            self._root = os.open(name, _DIR_FLAGS, dir_fd=self._anchor)
            self._root_id = _identity(named)
            self._check()
            # Lock the directory inode, not a replaceable lock-file pathname.
            fcntl.flock(self._root, fcntl.LOCK_EX | fcntl.LOCK_NB)
            if create:
                if self._names():
                    _fail("STORE_CONFLICT")
                os.fsync(self._anchor)
                self._publish("session.json", _header(self._metadata))
            report = self._replay()
            self._count, self._head, self._total = report["count"], report["head"], report["chain_byte_length"]
            self._writer = create
            self._poisoned = False
        except (OSError, CustodyError) as error:
            self.close()
            if isinstance(error, CustodyError):
                raise
            raise CustodyError("STORE_OPEN_FAILED", errno=error.errno) from None

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    def close(self):
        with self._mutex:
            for attr in ("_root", "_anchor"):
                fd = getattr(self, attr)
                setattr(self, attr, -1)
                if fd >= 0:
                    os.close(fd)
            self._poisoned = True

    def _check(self):
        if self._pid != os.getpid() or self._root < 0:
            _fail("STORE_CLOSED_OR_FORKED")
        _private(os.fstat(self._anchor), directory=True)
        root = os.fstat(self._root)
        named = os.stat(self._name, dir_fd=self._anchor, follow_symlinks=False)
        _private(root, directory=True)
        _private(named, directory=True)
        if (_identity(root) != self._root_id or _identity(named) != self._root_id or
                root.st_dev != os.fstat(self._anchor).st_dev):
            _fail("STORE_SUBSTITUTED")

    def _names(self):
        self._check()
        names = set()
        with os.scandir(self._root) as entries:
            for entry in entries:
                if len(names) >= MAX_ENTRIES:
                    _fail("STORE_BOUNDS")
                names.add(entry.name)
        return names

    def _file_check(self, fd, name, links=1):
        self._check()
        opened = os.fstat(fd)
        named = os.stat(name, dir_fd=self._root, follow_symlinks=False)
        _private(opened, links=links)
        _private(named, links=links)
        if (_identity(opened) != _identity(named) or opened.st_dev != self._root_id[0]):
            _fail("STORE_SUBSTITUTED")
        return opened

    def _read(self, name):
        self._check()
        named = os.stat(name, dir_fd=self._root, follow_symlinks=False)
        _private(named)
        if named.st_size > MAX_OBJECT:
            _fail("STORE_BOUNDS")
        fd = os.open(name, os.O_RDONLY | _FILE_FLAGS, dir_fd=self._root)
        try:
            before = self._file_check(fd, name)
            if _identity(before) != _identity(named):
                _fail("STORE_SUBSTITUTED")
            if before.st_size > MAX_OBJECT:
                _fail("STORE_BOUNDS")
            data = bytearray()
            # Bound both bytes and syscall attempts, including short reads.
            for _ in range(MAX_OBJECT + 1):
                part = os.read(fd, min(8192, MAX_OBJECT + 1 - len(data)))
                if not part:
                    break
                data.extend(part)
                if len(data) > MAX_OBJECT:
                    _fail("STORE_BOUNDS")
            after = self._file_check(fd, name)
            if len(data) != before.st_size or after.st_size != before.st_size:
                _fail("STORE_CHANGED")
            return bytes(data)
        finally:
            os.close(fd)

    def _publish(self, name, data):
        """Preserve .pending on any uncertain failure; never retry or erase it."""
        if type(data) is not bytes or len(data) > MAX_OBJECT:
            _fail("INPUT_BOUNDS")
        phase, written, fd = "create_pending", 0, -1
        file_synced = published = directory_synced = False
        try:
            self._check()
            fd = os.open(".pending", os.O_RDWR | os.O_CREAT | os.O_EXCL | _FILE_FLAGS,
                         0o600, dir_fd=self._root)
            self._file_check(fd, ".pending")
            phase = "write"
            for _ in range(MAX_OBJECT + 1):
                if written == len(data):
                    break
                progress = os.write(fd, data[written:])
                if progress <= 0 or progress > len(data) - written:
                    _fail("STORE_SHORT_WRITE")
                written += progress
            if written != len(data):
                _fail("STORE_SHORT_WRITE")
            phase = "verify_written"
            if self._read(".pending") != data:
                _fail("STORE_CHANGED")
            self._file_check(fd, ".pending")
            phase = "file_fsync"
            os.fsync(fd)
            file_synced = True
            phase = "publish"
            self._file_check(fd, ".pending")
            os.link(".pending", name, src_dir_fd=self._root, dst_dir_fd=self._root,
                    follow_symlinks=False)
            published = True
            self._file_check(fd, name, links=2)
            self._file_check(fd, ".pending", links=2)
            phase = "unlink_pending"
            os.unlink(".pending", dir_fd=self._root)
            self._file_check(fd, name)
            phase = "directory_fsync"
            os.fsync(self._root)
            directory_synced = True
            phase = "verify_published"
            if self._read(name) != data:
                _fail("STORE_CHANGED")
        except (OSError, CustodyError) as error:
            self._poisoned = True
            raise CustodyError("STORE_WRITE_FAILED", phase=phase, object_name=name,
                               bytes_write_returned=written, file_fsync_returned=file_synced,
                               publication_returned=published,
                               directory_fsync_returned=directory_synced,
                               cause=error.code if isinstance(error, CustodyError) else "OS_ERROR",
                               errno=getattr(error, "errno", None)) from None
        finally:
            if fd >= 0:
                os.close(fd)

    def _replay(self):
        names = self._names()
        if ".pending" in names:
            _fail("STORE_INTERRUPTED")
        if "session.json" not in names:
            _fail("STORE_INCOMPLETE")
        header = self._read("session.json")
        if header != _header(self._metadata):
            _fail("STORE_METADATA_MISMATCH")
        head, total, records = _sha(header), len(header), []
        expected = {"session.json"}
        for sequence in range(1, MAX_SAMPLES + 1):
            name = f"{sequence:06d}.obj"
            if name not in names:
                break
            data = self._read(name)
            if len(data) < 4:
                _fail("STORE_TRUNCATED")
            length = int.from_bytes(data[:4], "big")
            if length > len(data) - 4:
                _fail("STORE_TRUNCATED")
            raw = data[4 + length:]
            recomputed, envelope = _object(raw, self._metadata["session_nonce"], sequence, head)
            if data != recomputed:
                _fail("STORE_HASH_OR_CHAIN")
            total += len(data)
            if total > MAX_TOTAL - TERMINAL_RESERVE:
                _fail("STORE_BOUNDS")
            head = _sha(data)
            records.append(dict(envelope, raw_bytes=raw, object_sha256=head))
            expected.add(name)
        terminal_reason = terminal_hash = None
        if "terminal.json" in names:
            terminal = self._read("terminal.json")
            for reason in ("CALLER_FINISHED", "CAPACITY_EXHAUSTED"):
                if terminal == _terminal(self._metadata["session_nonce"], len(records), head, total, reason):
                    terminal_reason = reason
                    break
            if terminal_reason is None:
                _fail("STORE_TERMINAL_MISMATCH")
            if len(terminal) > TERMINAL_RESERVE:
                _fail("STORE_BOUNDS")
            terminal_hash = _sha(terminal)
            expected.add("terminal.json")
        if expected != names or self._names() != names:
            _fail("STORE_LAYOUT")
        return dict(_flags(), count=len(records), head=head, chain_byte_length=total,
                    records=records, terminal_present=terminal_reason is not None,
                    terminal_reason=terminal_reason, terminal_sha256=terminal_hash,
                    external_checkpoint_present=False,
                    rollback_detectable=False, crash_durability="UNKNOWN_ON_REPLAY",
                    recovery_gaps="UNKNOWN", event_time="SUPPLIED_FIXTURE_ONLY")

    def replay(self):
        with self._mutex:
            try:
                return self._replay()
            except OSError as error:
                raise CustodyError("STORE_READ_FAILED", errno=error.errno) from None

    def _before_write(self):
        if self._poisoned or not self._writer or self._sealed:
            _fail("STORE_NOT_WRITABLE")
        report = self._replay()
        if (report["count"], report["head"], report["chain_byte_length"], report["terminal_present"]) != (
                self._count, self._head, self._total, False):
            _fail("STORE_CHANGED")

    def append(self, raw):
        with self._mutex:
            try:
                self._before_write()
                data, envelope = _object(raw, self._metadata["session_nonce"], self._count + 1, self._head)
                if self._count >= MAX_SAMPLES or self._total + len(data) > MAX_TOTAL - TERMINAL_RESERVE:
                    self._finish("CAPACITY_EXHAUSTED")
                    raise CustodyError("STORE_CAPACITY", terminal_persisted=True)
                self._publish(f"{self._count + 1:06d}.obj", data)
                self._count += 1
                self._head = _sha(data)
                self._total += len(data)
                return dict(envelope, object_sha256=self._head,
                            local_fsync_returned=True, external_checkpoint_present=False)
            except (OSError, CustodyError) as error:
                self._poisoned = True
                if isinstance(error, CustodyError):
                    raise
                raise CustodyError("STORE_WRITE_FAILED", errno=error.errno) from None

    def _finish(self, reason):
        terminal = _terminal(self._metadata["session_nonce"], self._count, self._head, self._total, reason)
        if len(terminal) > TERMINAL_RESERVE:
            _fail("STORE_BOUNDS")
        self._publish("terminal.json", terminal)
        self._sealed = True
        return dict(_flags(), count=self._count, head=self._head,
                    terminal_sha256=_sha(terminal), local_fsync_returned=True,
                    external_checkpoint_present=False)

    def finish(self):
        with self._mutex:
            try:
                self._before_write()
                return self._finish("CALLER_FINISHED")
            except (OSError, CustodyError) as error:
                self._poisoned = True
                if isinstance(error, CustodyError):
                    raise
                raise CustodyError("STORE_WRITE_FAILED", errno=error.errno) from None
