"""Focused, offline tests for the reviewed V5 arithmetic baseline replay."""
import copy
from contextlib import redirect_stderr
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock
import zlib

from tools import v11_gate3_v5_feasibility_arithmetic as checker


def disposable_repo(root):
    subprocess.run(["git", "init", "-q", "--template=", str(root)], check=True)
    return root


def loose_object(root, kind, data, oid=None):
    raw = f"{kind} {len(data)}\0".encode() + data
    oid = oid or hashlib.sha1(raw).hexdigest()
    target = root / ".git/objects" / oid[:2] / oid[2:]
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(zlib.compress(raw))
    return oid, target


class FeasibilityReplayTests(unittest.TestCase):
    def test_exact_replay_and_declared_tree(self):
        report = json.loads(checker.REPORT.read_text(encoding="utf-8"))
        self.assertEqual(report["baseline_tree"], checker.BASELINE_TREE)
        self.assertEqual(checker.build(checker.baseline_sources()), report)
        self.assertEqual(len(report["checks_passed"]), 32)
        self.assertEqual(len(report["source_pins"]), 22)

    def test_status_only_advancement_does_not_change_historical_replay(self):
        baseline = checker.baseline_sources()
        current = dict(baseline)
        for path in checker.SOURCE_PATHS[:3]:
            current[path] += b"\nSubsequent status update.\n"
        self.assertNotEqual(checker.build(current), checker.build(baseline))
        self.assertEqual(checker.build(checker.baseline_sources()),
                         json.loads(checker.REPORT.read_text(encoding="utf-8")))

    def test_nonstatus_source_tamper_refuses_current_check(self):
        baseline = checker.baseline_sources()
        current = dict(baseline)
        source = checker.SOURCE_PATHS[3]
        current[source] += b"\nTamper.\n"
        self.assertNotEqual(checker.build(current),
                            json.loads(checker.REPORT.read_text(encoding="utf-8")))
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for path, data in current.items():
                target = root / path
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(data)
            with mock.patch.object(checker, "ROOT", root), \
                 mock.patch.object(checker, "REPORT", checker.REPORT), \
                 mock.patch.object(checker, "baseline_sources", return_value=baseline), \
                 mock.patch.object(sys, "argv", ["checker", "--check"]):
                with self.assertRaisesRegex(ValueError, "source pins or arithmetic report differ"):
                    checker.main()

    def test_report_tamper_refuses_baseline_replay(self):
        report = json.loads(checker.REPORT.read_text(encoding="utf-8"))
        for field, value in (("baseline_commit", "0" * 40),
                             ("baseline_tree", "0" * 40),
                             ("verdict", "PASS")):
            changed = copy.deepcopy(report)
            changed[field] = value
            with self.subTest(field=field), tempfile.TemporaryDirectory() as directory:
                target = Path(directory) / "report.json"
                target.write_text(json.dumps(changed, sort_keys=True, indent=2) + "\n",
                                  encoding="utf-8")
                with mock.patch.object(checker, "REPORT", target), \
                     mock.patch.object(sys, "argv", ["checker", "--replay-baseline"]):
                    with self.assertRaisesRegex(ValueError, "source pins or arithmetic report differ"):
                        checker.main()

    def test_report_newline_bytes_refuse(self):
        original = checker.REPORT.read_bytes()
        for newline in (b"\r\n", b"\r"):
            with self.subTest(newline=newline), tempfile.TemporaryDirectory() as directory:
                target = Path(directory) / "report.json"
                target.write_bytes(original.replace(b"\n", newline))
                with mock.patch.object(checker, "REPORT", target), \
                     mock.patch.object(sys, "argv", ["checker", "--replay-baseline"]):
                    with self.assertRaisesRegex(ValueError, "report differ"):
                        checker.main()
                self.assertEqual(target.read_bytes(), original.replace(b"\n", newline))

    def test_report_tamper_refuses_current_check_and_write_is_disabled(self):
        baseline = checker.baseline_sources()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for path, data in baseline.items():
                target = root / path
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(data)
            report = json.loads(checker.REPORT.read_text(encoding="utf-8"))
            report["results"]["mandatory_slots"] = 0
            target = root / "report.json"
            target.write_text(json.dumps(report, sort_keys=True, indent=2) + "\n",
                              encoding="utf-8")
            with mock.patch.object(checker, "ROOT", root), \
                 mock.patch.object(checker, "REPORT", target), \
                 mock.patch.object(checker, "baseline_sources", return_value=baseline), \
                 mock.patch.object(sys, "argv", ["checker", "--check"]):
                with self.assertRaisesRegex(ValueError, "source pins or arithmetic report differ"):
                    checker.main()
            with mock.patch.object(sys, "argv", ["checker", "--write"]):
                with redirect_stderr(io.StringIO()):
                    with self.assertRaises(SystemExit):
                        checker.main()

    def test_wrong_commit_tree_and_missing_object_refuse(self):
        with mock.patch.object(checker, "BASELINE", "0" * 40):
            with self.assertRaises(ValueError):
                checker.baseline_sources()
        with mock.patch.object(checker, "BASELINE_TREE", "0" * 40):
            with self.assertRaisesRegex(ValueError, "commit/tree mismatch"):
                checker.baseline_sources()
        original = checker.git_object

        def missing(kind, oid):
            if kind == "blob":
                raise ValueError("missing object")
            return original(kind, oid)

        with mock.patch.object(checker, "git_object", side_effect=missing):
            with self.assertRaisesRegex(ValueError, "missing object"):
                checker.baseline_sources()

    def test_replacement_bytes_and_git_environment_refuse_or_are_ignored(self):
        with tempfile.TemporaryDirectory() as directory:
            root = disposable_repo(Path(directory) / "repo")
            loose_object(root, "commit", b"forged commit", checker.BASELINE)
            with mock.patch.object(checker, "ROOT", root):
                with self.assertRaisesRegex(ValueError, "object hash mismatch"):
                    checker.git_object("commit", checker.BASELINE)
        with mock.patch.dict("os.environ", {"GIT_NO_REPLACE_OBJECTS": "0",
                                            "GIT_OBJECT_DIRECTORY": "/missing",
                                            "GIT_ALTERNATE_OBJECT_DIRECTORIES": "/missing",
                                            "GIT_CONFIG_COUNT": "1",
                                            "GIT_CONFIG_KEY_0": "core.repositoryformatversion",
                                            "GIT_CONFIG_VALUE_0": "999"}):
            self.assertEqual(checker.build(checker.baseline_sources()),
                             json.loads(checker.REPORT.read_text(encoding="utf-8")))

    def test_missing_promisor_object_cannot_execute_helper(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            root = disposable_repo(base / "repo")
            marker = base / "helper-called"
            helper = base / "upload-pack"
            helper.write_text(f"#!/bin/sh\nprintf called > '{marker}'\nexit 1\n")
            helper.chmod(0o700)
            with (root / ".git/config").open("a") as config:
                config.write(f"\n[remote \"probe\"]\n promisor = true\n"
                             f" url = {base / 'absent-local-repo'}\n uploadpack = {helper}\n")
            before = sorted(p.relative_to(root / ".git/objects")
                            for p in (root / ".git/objects").rglob("*") if p.is_file())
            with mock.patch.object(checker, "ROOT", root), \
                 mock.patch.dict(os.environ, {"GIT_ALLOW_PROTOCOL": "file:http:https",
                                            "GIT_NO_LAZY_FETCH": "0"}):
                with self.assertRaisesRegex(ValueError, "missing or unreadable"):
                    checker.git_object("commit", checker.BASELINE)
                real_popen = subprocess.Popen

                def without_lazy_fetch_setting(command, **kwargs):
                    kwargs["env"] = dict(kwargs["env"])
                    kwargs["env"].pop("GIT_NO_LAZY_FETCH")
                    return real_popen(command, **kwargs)

                # The protocol deny rule must hold even if Git ignores the
                # no-lazy-fetch setting on a deployed version.
                with mock.patch.object(checker.subprocess, "Popen",
                                       side_effect=without_lazy_fetch_setting):
                    with self.assertRaisesRegex(ValueError, "missing or unreadable"):
                        checker.git_object("commit", checker.BASELINE)
            after = sorted(p.relative_to(root / ".git/objects")
                           for p in (root / ".git/objects").rglob("*") if p.is_file())
            self.assertFalse(marker.exists())
            self.assertEqual(before, after)

    def test_fifo_and_oversized_object_are_bounded(self):
        with tempfile.TemporaryDirectory() as directory:
            root = disposable_repo(Path(directory) / "repo")
            fifo = root / ".git/objects" / checker.BASELINE[:2] / checker.BASELINE[2:]
            fifo.parent.mkdir(parents=True)
            os.mkfifo(fifo)
            with mock.patch.object(checker, "ROOT", root), \
                 mock.patch.object(checker, "GIT_TIMEOUT", 0.25):
                start = time.monotonic()
                with self.assertRaisesRegex(ValueError, "timed out"):
                    checker.git_object("commit", checker.BASELINE)
                self.assertLess(time.monotonic() - start, 2)
            fifo.unlink()
            oid, _ = loose_object(root, "commit", b"large" * 1000)
            with mock.patch.object(checker, "ROOT", root), \
                 mock.patch.object(checker, "OBJECT_LIMIT", 64):
                with self.assertRaisesRegex(ValueError, "output too large"):
                    checker.git_object("commit", oid)

    def test_timeout_kills_descendant_process_group(self):
        with tempfile.TemporaryDirectory() as directory:
            marker = Path(directory) / "descendant-finished"
            real_popen = subprocess.Popen

            def stalled(_command, **kwargs):
                return real_popen(["/bin/sh", "-c", f"(sleep 0.5; touch '{marker}') & wait"],
                                  **kwargs)

            with mock.patch.object(checker.subprocess, "Popen", side_effect=stalled), \
                 mock.patch.object(checker, "GIT_TIMEOUT", 0.1):
                with self.assertRaisesRegex(ValueError, "timed out"):
                    checker.git_object("commit", checker.BASELINE)
            time.sleep(0.65)
            self.assertFalse(marker.exists())

    def test_current_exponent_drift_refuses_before_arithmetic(self):
        baseline = checker.baseline_sources()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for path, data in baseline.items():
                target = root / path
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(data)
            target = root / "tools/v11_r09_gate3_launch.py"
            data = target.read_bytes()
            self.assertIn(b"MAX_BYTES = 1024 ** 3", data)
            for exponent in (b"2 ** (2 ** 40)", b"2**999999"):
                with self.subTest(exponent=exponent):
                    target.write_bytes(data.replace(b"MAX_BYTES = 1024 ** 3",
                                                    b"MAX_BYTES = " + exponent, 1))
                    with mock.patch.object(checker, "ROOT", root), \
                         mock.patch.object(checker, "baseline_sources", return_value=baseline), \
                         mock.patch.object(checker, "arithmetic", side_effect=AssertionError("evaluated")):
                        with self.assertRaisesRegex(ValueError, "source pins"):
                            checker.build()
                        with mock.patch.object(sys, "argv", ["checker", "--check"]):
                            with self.assertRaisesRegex(ValueError, "source pins"):
                                checker.main()


if __name__ == "__main__":
    unittest.main()
