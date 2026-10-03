"""Offline filesystem adversaries; all scratch stays under this worktree.

No native recorder is built or launched. unittest checks survive Python -O.
"""
import ast
import contextlib
import errno
import hashlib
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))
from tools import v11_gate3_clock_custody as c
from test_v11_gate3_clock_dossier import _raw, _record

META = dict(session_nonce="fixture-1", method_id="declared-method", profile_id="fixture",
            build_id="declared-build", host_id="declared-host", event_kind="SYNTHETIC")
RAW = _raw(_record())


class CustodyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix=".clock-custody-test-", dir=ROOT)
        self.anchor = Path(self.temp.name)
        self.fd = os.open(self.anchor, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        self.handles = []

    def tearDown(self):
        for handle in self.handles:
            handle.close()
        os.close(self.fd)
        self.temp.cleanup()

    def store(self, name="store", create=True, meta=None):
        handle = c.FixtureStore(self.fd, name, META if meta is None else meta, create=create)
        self.handles.append(handle)
        return handle

    def refusal(self, callback, code=None):
        with self.assertRaises(c.CustodyError) as caught:
            callback()
        if code:
            self.assertEqual(caught.exception.code, code)
        self.assertFalse(caught.exception.evidence["execution_authority"])
        self.assertEqual(caught.exception.evidence["custody_status"], "CUSTODY_UNQUALIFIED")
        return caught.exception.evidence

    def test_roundtrip_raw_projection_and_fixed_flags(self):
        s = self.store()
        compact = json.dumps(_record(), separators=(",", ":")).encode()
        a, b = s.append(RAW), s.append(compact)
        self.assertNotEqual(a["raw_sha256"], b["raw_sha256"])
        self.assertEqual(a["parsed_projection_sha256"], b["parsed_projection_sha256"])
        self.assertEqual(a["raw_sha256"], hashlib.sha256(RAW).hexdigest())
        self.assertEqual(b["prior_head"], a["object_sha256"])
        terminal = s.finish()
        report = s.replay()
        self.assertEqual(report["records"][0]["raw_bytes"], RAW)
        self.assertEqual(report["head"], terminal["head"])
        self.assertEqual(report["terminal_sha256"], terminal["terminal_sha256"])
        for item in (a, b, terminal, report):
            for flag in ("execution_authority", "provider_authority", "capture_eligibility",
                         "clock_qualification", "custody_qualification"):
                self.assertIs(item[flag], False)
            self.assertEqual(item["qualification_credit"], 0)
            self.assertEqual(item["g3l"], "NO_GO")
            self.assertEqual(item["custody_status"], "CUSTODY_UNQUALIFIED")
        s.close()
        self.assertEqual(self.store(create=False).replay(), report)

    def test_deterministic_replay_and_objects(self):
        reports = []
        for name in ("first", "second"):
            s = self.store(name)
            s.append(RAW)
            s.append(b'{"truncated":')
            s.finish()
            reports.append(s.replay())
        self.assertEqual(reports[0], reports[1])
        for path in (self.anchor / "first").iterdir():
            self.assertEqual(path.read_bytes(), (self.anchor / "second" / path.name).read_bytes())

    def test_malformed_and_refused_bytes_retained(self):
        s = self.store()
        raw = b'{"schema":"ALPHA_V11_GATE3_CLOCK_PROBE_RECORD_V1","status":"REFUSED",' \
              b'"code":"CLOCK_SOURCE_UNAVAILABLE","detail_errno":38,"partial":{}}'
        a = s.append(raw)
        b = s.append(b"\x00\xff{bad")
        self.assertEqual(a["parse_status"], "REFUSED")
        self.assertEqual(a["parse_reason"], "CLOCK_SOURCE_UNAVAILABLE")
        self.assertEqual(b["parse_status"], "PARSE_REFUSED")
        self.assertIsNone(b["parsed_projection_sha256"])
        self.assertEqual(s.replay()["records"][0]["raw_bytes"], raw)
        self.assertEqual(s.replay()["records"][1]["raw_bytes"], b"\x00\xff{bad")

    def test_path_aliases_and_types_refused(self):
        for name in (".", "..", "a/b", "a//b", "a/../b", "/absolute", "a/", "a\\b", "a\x00b", "x" * 65, b"x"):
            with self.subTest(name=name):
                self.refusal(lambda: self.store(name), "SCHEMA")
        self.assertEqual(list(self.anchor.iterdir()), [])

    def test_metadata_closed_exact_and_copied(self):
        class Spoof(str):
            def __eq__(self, other):
                raise AssertionError("untrusted equality executed")
            __hash__ = str.__hash__
        for metadata in (dict(META, execution_authority=True), dict(META, event_kind="LOCAL_OBSERVATION"),
                         dict(META, host_id=Spoof("host")), {Spoof(k): v for k, v in META.items()},
                         dict(META, build_id="x" * 4097)):
            self.refusal(lambda: self.store(meta=metadata), "SCHEMA")
        metadata = dict(META)
        s = self.store(meta=metadata)
        metadata["host_id"] = "changed"
        s.append(RAW)
        s.close()
        self.assertEqual(self.store(create=False).replay()["count"], 1)

    def test_anchor_permissions_and_store_symlink(self):
        os.chmod(self.anchor, 0o755)
        self.refusal(lambda: self.store(), "STORE_IDENTITY")
        os.chmod(self.anchor, 0o700)
        (self.anchor / "target").mkdir(mode=0o700)
        (self.anchor / "store").symlink_to("target", target_is_directory=True)
        self.refusal(lambda: self.store(create=False), "STORE_IDENTITY")
        self.assertEqual(list((self.anchor / "target").iterdir()), [])

    def test_duplicate_writer_and_exclusive_create(self):
        s = self.store()
        self.refusal(lambda: self.store(create=False), "STORE_OPEN_FAILED")
        self.refusal(lambda: self.store(), "STORE_OPEN_FAILED")
        s.append(RAW)
        s.close()
        reopened = self.store(create=False)
        self.refusal(lambda: reopened.append(RAW), "STORE_NOT_WRITABLE")

    def _fork_with_held_mutex(self, method):
        s = self.store()
        s.append(RAW)
        root = self.anchor / "store"
        before = {p.name: p.read_bytes() for p in root.iterdir()}
        state = (s._root, s._anchor, s._count, s._head, s._total, s._poisoned, s._sealed)
        held, release = threading.Event(), threading.Event()

        def hold_mutex():
            with s._mutex:
                held.set()
                release.wait()

        holder = threading.Thread(target=hold_mutex, daemon=True)
        read_fd, write_fd = os.pipe()
        child = None
        try:
            holder.start()
            self.assertTrue(held.wait(2), "parent thread did not acquire mutex")
            child = os.fork()
            if child == 0:
                # Independent child and parent watchdogs bound a regression even
                # if the inherited mutex can never be acquired. No native probe.
                signal.signal(signal.SIGALRM, signal.SIG_DFL)
                signal.alarm(2)
                result, status = b"ok", 0
                try:
                    self.assertTrue(s._mutex.locked())
                    callback = (lambda: s.append(b"child must not persist")) if method == "append" else getattr(s, method)
                    with patch.object(c.os, "close", side_effect=AssertionError("child closed descriptor")), \
                            patch.object(c.fcntl, "flock", side_effect=AssertionError("child changed flock")):
                        self.refusal(callback, "STORE_CLOSED_OR_FORKED")
                        self.refusal(s.close, "STORE_CLOSED_OR_FORKED")
                        self.refusal(callback, "STORE_CLOSED_OR_FORKED")
                    self.assertEqual((s._root, s._anchor, s._count, s._head, s._total,
                                      s._poisoned, s._sealed), state)
                    os.fstat(s._root)
                    os.fstat(s._anchor)
                except BaseException as error:
                    result, status = repr(error).encode("utf-8")[:2048], 1
                try:
                    os.write(write_fd, result)
                finally:
                    os._exit(status)

            os.close(write_fd)
            write_fd = -1
            # Keep the parent's mutex held until the child has exited. A parent
            # release cannot rescue the child's separate copy of a held lock.
            deadline = time.monotonic() + 5
            while True:
                waited, status = os.waitpid(child, os.WNOHANG)
                if waited == child:
                    child = None
                    break
                if time.monotonic() >= deadline:
                    self.fail("forked child exceeded parent watchdog")
                time.sleep(0.01)
            self.assertTrue(os.WIFEXITED(status), f"child terminated: {status}")
            result = os.read(read_fd, 2048)
            self.assertEqual(os.WEXITSTATUS(status), 0, result)
            self.assertEqual(result, b"ok")
        finally:
            if child is not None and child > 0:
                os.kill(child, signal.SIGKILL)
                os.waitpid(child, 0)
            release.set()
            if holder.ident is not None:
                holder.join(timeout=2)
            os.close(read_fd)
            if write_fd >= 0:
                os.close(write_fd)
        self.assertFalse(holder.is_alive(), "parent mutex holder did not stop")
        self.assertEqual({p.name: p.read_bytes() for p in root.iterdir()}, before)
        self.assertEqual((s._root, s._anchor, s._count, s._head, s._total,
                          s._poisoned, s._sealed), state)
        # A child LOCK_UN on the inherited open file description would let this
        # independent open succeed, even though the parent still retains its fd.
        self.refusal(lambda: self.store(create=False), "STORE_OPEN_FAILED")
        self.assertEqual(s.replay()["count"], 1)
        s.append(b"parent continues")
        s.finish()
        report = s.replay()
        self.assertEqual(report["count"], 2)
        self.assertEqual(report["records"][1]["raw_bytes"], b"parent continues")
        self.assertTrue(report["terminal_present"])
        s.close()
        s.close()
        self.assertEqual(self.store(create=False).replay(), report)

    def test_fork_held_mutex_child_replay_refused(self):
        self._fork_with_held_mutex("replay")

    def test_fork_held_mutex_child_append_refused(self):
        self._fork_with_held_mutex("append")

    def test_fork_held_mutex_child_finish_refused(self):
        self._fork_with_held_mutex("finish")

    def test_fork_held_mutex_child_close_refused(self):
        self._fork_with_held_mutex("close")

    def test_root_substitution_refused(self):
        s = self.store()
        (self.anchor / "store").rename(self.anchor / "saved")
        (self.anchor / "store").mkdir(mode=0o700)
        self.refusal(lambda: s.append(RAW), "STORE_SUBSTITUTED")
        self.assertEqual(list((self.anchor / "store").iterdir()), [])
        self.assertEqual([p.name for p in (self.anchor / "saved").iterdir()], ["session.json"])

    def test_symlink_hardlink_fifo_modes_refused(self):
        for kind in ("symlink", "hardlink", "fifo", "mode"):
            with self.subTest(kind=kind):
                s = self.store(kind)
                s.append(RAW)
                obj = self.anchor / kind / "000001.obj"
                saved = self.anchor / (kind + "-saved")
                if kind == "hardlink":
                    os.link(obj, saved)
                elif kind == "mode":
                    os.chmod(obj, 0o640)
                else:
                    obj.rename(saved)
                    if kind == "fifo":
                        os.mkfifo(obj, 0o600)
                    else:
                        obj.symlink_to(saved)
                self.refusal(s.replay)
                self.refusal(lambda: s.append(RAW))

    def test_substitution_during_read_refused(self):
        s = self.store()
        s.append(RAW)
        original = os.read
        obj = self.anchor / "store" / "000001.obj"
        done = False
        def replace(fd, size):
            nonlocal done
            data = original(fd, size)
            if not done and os.fstat(fd).st_ino == obj.stat().st_ino:
                done = True
                obj.rename(obj.with_name("original"))
                obj.write_bytes(b"replacement")
                obj.chmod(0o600)
            return data
        with patch.object(c.os, "read", replace):
            self.refusal(s.replay, "STORE_SUBSTITUTED")

    def test_truncation_corruption_sequence_gap_and_terminal(self):
        for kind in ("truncated", "raw", "swap", "gap", "terminal", "header", "extra"):
            with self.subTest(kind=kind):
                s = self.store(kind)
                s.append(RAW)
                s.append(b"second")
                s.finish()
                root = self.anchor / kind
                a, b = root / "000001.obj", root / "000002.obj"
                if kind == "truncated":
                    a.write_bytes(a.read_bytes()[:3])
                elif kind == "raw":
                    data = a.read_bytes()
                    a.write_bytes(data[:-1] + b"X")
                elif kind == "swap":
                    adata, bdata = a.read_bytes(), b.read_bytes()
                    a.write_bytes(bdata)
                    b.write_bytes(adata)
                elif kind == "gap":
                    a.unlink()
                elif kind == "terminal":
                    (root / "terminal.json").unlink()
                    (root / "terminal.json").write_bytes(b"{}")
                elif kind == "header":
                    (root / "session.json").write_bytes(b"{}")
                else:
                    (root / "unknown").write_bytes(b"?")
                self.refusal(s.replay)
                s.close()
                self.refusal(lambda: self.store(kind, create=False))

    def test_wrong_metadata_refused_on_restart(self):
        self.store().close()
        self.refusal(lambda: self.store(create=False, meta=dict(META, build_id="forged")),
                     "STORE_METADATA_MISMATCH")

    def test_old_valid_chain_rollback_not_detectable(self):
        s = self.store()
        s.append(RAW)
        root = self.anchor / "store"
        old_head, old_total = s._head, s._total
        # A same-uid adversary can replace a newer terminal with an older valid
        # terminal. No external head exists: the honest result is unqualified.
        old_terminal = c._terminal(META["session_nonce"], 1, old_head, old_total, "CALLER_FINISHED")
        s.append(b"later evidence")
        s.finish()
        s.close()
        (root / "000002.obj").unlink()
        (root / "terminal.json").write_bytes(old_terminal)
        report = self.store(create=False).replay()
        self.assertEqual(report["count"], 1)
        self.assertFalse(report["rollback_detectable"])
        self.assertFalse(report["external_checkpoint_present"])
        self.assertFalse(report["custody_qualification"])
        self.assertEqual(report["recovery_gaps"], "UNKNOWN")

    def test_live_rollback_detected_before_next_append(self):
        s = self.store()
        s.append(RAW)
        (self.anchor / "store" / "000001.obj").unlink()
        self.refusal(lambda: s.append(RAW), "STORE_CHANGED")
        self.assertFalse((self.anchor / "store" / "000001.obj").exists())

    def test_short_writes_completed_and_zero_progress_retained(self):
        s = self.store()
        write = os.write
        with patch.object(c.os, "write", lambda fd, data: write(fd, data[:7])):
            s.append(RAW)
        calls = 0
        def stop(fd, data):
            nonlocal calls
            calls += 1
            return write(fd, data[:11]) if calls == 1 else 0
        with patch.object(c.os, "write", stop):
            failure = self.refusal(lambda: s.append(RAW), "STORE_WRITE_FAILED")
        self.assertEqual(failure["bytes_write_returned"], 11)
        self.assertFalse(failure["publication_returned"])
        root = self.anchor / "store"
        self.assertEqual((root / ".pending").stat().st_size, 11)
        self.assertTrue((root / "000001.obj").exists())
        self.assertFalse((root / "000002.obj").exists())
        self.refusal(lambda: s.append(RAW), "STORE_NOT_WRITABLE")
        s.close()
        self.refusal(lambda: self.store(create=False), "STORE_INTERRUPTED")

    def test_write_errno_and_truncate_before_fsync(self):
        for kind in ("enospc", "eintr", "truncate"):
            s = self.store(kind)
            real_write = os.write
            def bad_write(fd, data):
                if kind == "truncate":
                    n = real_write(fd, data)
                    os.ftruncate(fd, 1)
                    return n
                raise OSError(errno.ENOSPC if kind == "enospc" else errno.EINTR, "injected")
            with patch.object(c.os, "write", bad_write):
                result = self.refusal(lambda: s.append(RAW), "STORE_WRITE_FAILED")
            self.assertFalse(result["file_fsync_returned"])
            self.assertTrue((self.anchor / kind / ".pending").exists())

    def test_file_and_directory_fsync_failure_truthful(self):
        real_fsync = os.fsync
        for directory in (False, True):
            name = "dir" if directory else "file"
            s = self.store(name)
            root = self.anchor / name
            def fail(fd):
                if (fd == s._root) == directory:
                    raise OSError(errno.EIO, "injected fsync")
                return real_fsync(fd)
            with patch.object(c.os, "fsync", fail):
                result = self.refusal(lambda: s.append(RAW), "STORE_WRITE_FAILED")
            self.assertEqual(result["publication_returned"], directory)
            self.assertEqual(result["file_fsync_returned"], directory)
            self.assertFalse(result["directory_fsync_returned"])
            self.assertEqual((root / "000001.obj").exists(), directory)
            self.assertEqual((root / ".pending").exists(), not directory)
            self.refusal(s.finish, "STORE_NOT_WRITABLE")
            s.close()
            if directory:
                report = self.store(name, create=False).replay()
                self.assertEqual(report["crash_durability"], "UNKNOWN_ON_REPLAY")
                self.assertFalse(report["terminal_present"])
            else:
                self.refusal(lambda: self.store(name, create=False), "STORE_INTERRUPTED")

    def test_publication_no_clobber_even_race(self):
        s = self.store()
        original = os.link
        target = self.anchor / "store" / "000001.obj"
        def collision(src, dst, **kwargs):
            target.write_bytes(b"preexisting")
            target.chmod(0o600)
            return original(src, dst, **kwargs)
        with patch.object(c.os, "link", collision):
            result = self.refusal(lambda: s.append(RAW), "STORE_WRITE_FAILED")
        self.assertEqual(result["phase"], "publish")
        self.assertEqual(target.read_bytes(), b"preexisting")
        self.assertTrue((target.parent / ".pending").exists())

    def test_crash_after_link_preserves_both_names_and_refuses_restart(self):
        class Crash(BaseException):
            pass
        s = self.store()
        original = os.link
        def crash(*args, **kwargs):
            original(*args, **kwargs)
            raise Crash()
        with patch.object(c.os, "link", crash), self.assertRaises(Crash):
            s.append(RAW)
        root = self.anchor / "store"
        self.assertEqual((root / ".pending").stat().st_nlink, 2)
        self.assertEqual((root / ".pending").read_bytes(), (root / "000001.obj").read_bytes())
        s.close()
        self.refusal(lambda: self.store(create=False), "STORE_INTERRUPTED")

    def test_missing_terminal_and_copied_mtime_no_new_time(self):
        s = self.store()
        s.append(RAW)
        s.finish()
        (self.anchor / "store" / "terminal.json").unlink()
        os.utime(self.anchor / "store" / "000001.obj", ns=(1, 1))
        s.close()
        report = self.store(create=False).replay()
        self.assertFalse(report["terminal_present"])
        self.assertEqual(report["event_time"], "SUPPLIED_FIXTURE_ONLY")
        self.assertEqual(report["records"][0]["raw_bytes"], RAW)
        self.assertEqual(report["recovery_gaps"], "UNKNOWN")

    def test_capacity_reserves_terminal_and_preserves_prefix(self):
        s = self.store()
        for _ in range(c.MAX_SAMPLES):
            s.append(b"refused fixture")
        result = self.refusal(lambda: s.append(RAW), "STORE_CAPACITY")
        self.assertTrue(result["terminal_persisted"])
        report = s.replay()
        self.assertEqual(report["count"], c.MAX_SAMPLES)
        self.assertEqual(report["terminal_reason"], "CAPACITY_EXHAUSTED")
        self.assertLessEqual(sum(p.stat().st_size for p in (self.anchor / "store").iterdir()), c.MAX_TOTAL)

    def test_total_byte_bound_before_sample_cap(self):
        s = self.store()
        for _ in range(c.MAX_SAMPLES):
            try:
                s.append(b"x" * c.MAX_RAW)
            except c.CustodyError as error:
                self.assertEqual(error.code, "STORE_CAPACITY")
                break
        else:
            self.fail("total bound never reached")
        self.assertLess(s.replay()["count"], c.MAX_SAMPLES)
        self.assertLessEqual(sum(p.stat().st_size for p in (self.anchor / "store").iterdir()), c.MAX_TOTAL)

    def test_oversized_hostile_raw_poisoned_no_success_claim(self):
        class EvilBytes(bytes):
            def __len__(self):
                raise AssertionError("hook executed")
        for i, raw in enumerate((b"x" * (c.MAX_RAW + 1), EvilBytes(b"x"), bytearray(b"x"), None)):
            s = self.store("input" + str(i))
            report = self.refusal(lambda: s.append(raw), "INPUT_BOUNDS")
            self.assertTrue(report["evidence_incomplete"])
            self.assertFalse(report["diagnostic_persisted"])
            self.refusal(s.finish, "STORE_NOT_WRITABLE")

    def test_bounded_directory_and_oversized_disk_object(self):
        s = self.store()
        for i in range(c.MAX_ENTRIES):
            (self.anchor / "store" / str(i)).touch(mode=0o600)
        self.refusal(s.replay, "STORE_BOUNDS")
        other = self.store("large")
        obj = self.anchor / "large" / "000001.obj"
        with obj.open("wb") as stream:
            stream.truncate(c.MAX_OBJECT + 1)
        obj.chmod(0o600)
        self.refusal(other.replay, "STORE_BOUNDS")

    def test_terminal_fsync_failure_and_no_retry(self):
        s = self.store()
        s.append(RAW)
        with patch.object(c.os, "fsync", side_effect=OSError(errno.EIO, "injected")):
            self.refusal(s.finish, "STORE_WRITE_FAILED")
        self.refusal(s.finish, "STORE_NOT_WRITABLE")
        self.assertTrue((self.anchor / "store" / "000001.obj").exists())
        self.assertTrue((self.anchor / "store" / ".pending").exists())

    def test_special_file_refused_before_open(self):
        s = self.store()
        os.mkfifo(self.anchor / "store" / "000001.obj", 0o600)
        original = os.open
        def guarded(name, *args, **kwargs):
            if name == "000001.obj":
                self.fail("special-file open attempted")
            return original(name, *args, **kwargs)
        with patch.object(c.os, "open", guarded):
            self.refusal(s.replay, "STORE_IDENTITY")

    def test_initial_parent_and_header_fsync_failures_preserved(self):
        for where in ("parent", "header"):
            original = os.fsync
            calls = 0
            def fail(fd):
                nonlocal calls
                calls += 1
                if calls == (1 if where == "parent" else 2):
                    raise OSError(errno.EIO, "injected")
                return original(fd)
            with patch.object(c.os, "fsync", fail):
                evidence = self.refusal(lambda: self.store(where))
            root = self.anchor / where
            self.assertTrue(root.is_dir())
            self.assertFalse((root / "session.json").exists())
            self.assertEqual((root / ".pending").exists(), where == "header")
            self.assertTrue(evidence["evidence_incomplete"])
            self.refusal(lambda: self.store(where, create=False))

    def test_replaced_pending_path_after_fsync_refused(self):
        s = self.store()
        original = os.fsync
        pending = self.anchor / "store" / ".pending"
        def replace(fd):
            original(fd)
            if fd != s._root:
                pending.rename(pending.with_name("saved-pending"))
                pending.write_bytes(b"substituted")
                pending.chmod(0o600)
        with patch.object(c.os, "fsync", replace):
            evidence = self.refusal(lambda: s.append(RAW), "STORE_WRITE_FAILED")
        self.assertEqual(evidence["cause"], "STORE_SUBSTITUTED")
        self.assertFalse(evidence["publication_returned"])
        self.assertEqual(pending.read_bytes(), b"substituted")

    def test_forged_authority_and_projection_hashes_refused(self):
        for kind in ("authority", "projection", "prior"):
            s = self.store(kind)
            s.append(RAW)
            obj = self.anchor / kind / "000001.obj"
            raw = obj.read_bytes()
            n = int.from_bytes(raw[:4], "big")
            envelope = json.loads(raw[4:4 + n])
            if kind == "authority":
                envelope["execution_authority"] = True
            elif kind == "projection":
                envelope["parsed_projection_sha256"] = "0" * 64
            else:
                envelope["prior_head"] = "0" * 64
            encoded = c._canonical(envelope)
            obj.write_bytes(len(encoded).to_bytes(4, "big") + encoded + raw[4 + n:])
            self.refusal(s.replay, "STORE_HASH_OR_CHAIN")

    def test_no_clock_native_network_process_or_account_effects(self):
        def forbidden(*args, **kwargs):
            raise AssertionError("forbidden capability used")
        with contextlib.ExitStack() as stack:
            for module, names in ((time, ("time", "time_ns", "monotonic", "monotonic_ns", "perf_counter", "clock_gettime", "clock_getres")),
                                  (socket, ("socket", "create_connection", "getaddrinfo")),
                                  (subprocess, ("Popen", "run", "call", "check_output")),
                                  (os, ("system", "fork", "posix_spawn", "execve", "getrandom", "urandom"))):
                for name in names:
                    stack.enter_context(patch.object(module, name, forbidden))
            s = self.store()
            s.append(RAW)
            s.finish()
            s.replay()
        tree = ast.parse((ROOT / "tools/v11_gate3_clock_custody.py").read_text())
        imports = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports.update(a.name for a in node.names)
            elif isinstance(node, ast.ImportFrom):
                imports.add(node.module)
        self.assertEqual(imports, {"__future__", "fcntl", "hashlib", "json", "os", "re", "stat", "threading", "tools"})
        self.assertEqual({p.name for p in self.anchor.iterdir()}, {"store"})


if __name__ == "__main__":
    unittest.main(verbosity=2)
