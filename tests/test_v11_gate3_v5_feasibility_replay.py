"""Focused, offline tests for the reviewed V5 arithmetic baseline replay."""
import copy
from contextlib import redirect_stderr
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from tools import v11_gate3_v5_feasibility_arithmetic as checker


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
        current = checker.baseline_sources()
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

    def test_report_tamper_refuses_current_check_and_write_is_disabled(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for path, data in checker.baseline_sources().items():
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
        fake = subprocess.CompletedProcess([], 0, b"forged commit", b"")
        with mock.patch.object(checker.subprocess, "run", return_value=fake):
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


if __name__ == "__main__":
    unittest.main()
