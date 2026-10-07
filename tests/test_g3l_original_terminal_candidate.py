"""Offline end-to-end checks for the historical G3-P terminal candidate."""

from contextlib import contextmanager
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest import mock

from tools import validate_g3l_original_terminal_candidate as candidate


@contextmanager
def checkout():
    """Disposable files and Git metadata; historical objects stay read-only."""
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory) / "checkout"
        root.mkdir()
        subprocess.run(["git", "init", "-q", str(root)], check=True)
        objects = subprocess.check_output(
            ["git", "-C", str(candidate.ROOT), "rev-parse", "--path-format=absolute",
             "--git-path", "objects"], text=True).strip()
        (root / ".git/objects/info/alternates").write_text(objects + "\n")
        for name in (candidate.MANIFEST, *[ref[0] for ref in candidate.EXPECTED_REFS.values()]):
            target = root / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(candidate.ROOT / name, target)
        yield root


class OriginalTerminalCandidateTests(unittest.TestCase):
    def test_exact_local_and_historical_bytes_bind(self):
        with checkout() as root:
            hashes = candidate.validate(root)
        self.assertEqual(set(hashes), {"document", "report", "terminal"})
        self.assertEqual(hashes["terminal"],
                         "414aef99c0576a0e96b86aa9244ad1ca4ffe4b02f161c69e0193f78498d3db22")

    def test_missing_changed_and_nonregular_files_refuse(self):
        names = (candidate.MANIFEST, *[ref[0] for ref in candidate.EXPECTED_REFS.values()])
        for name in names:
            for mutation in ("missing", "changed", "symlink", "fifo"):
                with self.subTest(name=name, mutation=mutation), checkout() as root:
                    path = root / name
                    original = path.read_bytes()
                    path.unlink()
                    if mutation == "changed":
                        path.write_bytes(original + b"x")
                    elif mutation == "symlink":
                        outside = root.parent / "outside"
                        outside.write_bytes(original)
                        path.symlink_to(outside)
                    elif mutation == "fifo":
                        os.mkfifo(path)
                    with self.assertRaises((ValueError, OSError)):
                        candidate.validate(root)

    def test_symlinked_ancestor_directories_refuse(self):
        for ancestor in ("docs", "docs/g3l_original_terminal_candidate"):
            with self.subTest(ancestor=ancestor), checkout() as root:
                original = root / ancestor
                outside = root.parent / "outside-directory"
                original.rename(outside)
                original.symlink_to(outside, target_is_directory=True)
                with self.assertRaises((ValueError, OSError)):
                    candidate.validate(root)

    def test_explicit_manifest_must_be_beneath_checkout(self):
        with checkout() as root:
            outside = root.parent / "identity.json"
            shutil.copyfile(root / candidate.MANIFEST, outside)
            for path in (outside, Path("../identity.json")):
                with self.subTest(path=path), self.assertRaises(ValueError):
                    candidate.validate(root, path)

    def test_malformed_and_duplicate_json_refuse(self):
        with checkout() as root:
            path = root / candidate.MANIFEST
            original = path.read_bytes()
            for raw in (b"{", b"[]", original.replace(b'"schema":', b'"schema": 0, "schema":')):
                with self.subTest(raw=raw[:20]):
                    path.write_bytes(raw)
                    with self.assertRaises(ValueError):
                        candidate.validate(root)
            path.write_bytes(original)
        for raw in (b'{"a": 1, "a": 2}', b'{"a": {"b": 1, "b": 2}}', b'[]'):
            with self.subTest(parser=raw), self.assertRaises(ValueError):
                candidate.unique_object(raw)

    def test_missing_promisor_object_never_dispatches_remote_helper(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "checkout"
            root.mkdir()
            subprocess.run(["git", "init", "-q", str(root)], check=True)
            for name in (candidate.MANIFEST, *[ref[0] for ref in candidate.EXPECTED_REFS.values()]):
                target = root / name
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(candidate.ROOT / name, target)
            for key, value in (("remote.origin.promisor", "true"),
                               ("extensions.partialclone", "origin"),
                               ("remote.origin.url", "reviewprobe::unused")):
                subprocess.run(["git", "-C", str(root), "config", key, value], check=True)
            helper_dir = Path(directory) / "bin"
            helper_dir.mkdir()
            marker = Path(directory) / "remote-invoked"
            helper = helper_dir / "git-remote-reviewprobe"
            helper.write_text("#!/bin/sh\nprintf invoked > \"$REVIEWPROBE_MARKER\"\nexit 1\n")
            helper.chmod(0o755)
            environment = {"PATH": str(helper_dir) + os.pathsep + os.environ["PATH"],
                           "REVIEWPROBE_MARKER": str(marker), "GIT_NO_LAZY_FETCH": "0",
                           "GIT_ALLOW_PROTOCOL": "reviewprobe"}
            with mock.patch.dict(os.environ, environment):
                control = subprocess.run(
                    ["git", "-C", str(root), "--no-replace-objects", "cat-file", "-t",
                     candidate.PROTOCOL_COMMIT], stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE, check=False)
                self.assertNotEqual(control.returncode, 0)
                self.assertTrue(marker.exists(), "promisor fixture did not exercise the helper")
                marker.unlink()
                with self.assertRaisesRegex(ValueError, "local Git lookup failed"):
                    candidate.validate(root)
            self.assertFalse(marker.exists(), "Git invoked the promisor remote helper")

    def test_terminal_report_mismatch_refuses(self):
        terminal = json.loads((candidate.ROOT / candidate.TERMINAL).read_bytes())
        with self.assertRaisesRegex(ValueError, "terminal does not bind"):
            candidate.check_terminal_binding(terminal, "0" * 64)

    def test_report_scope_mismatch_refuses(self):
        report = (candidate.ROOT / candidate.REPORT).read_bytes()
        with self.assertRaisesRegex(ValueError, "report does not name"):
            candidate.check_report_binding(report.replace(b"G3-P (protocol review)", b"G3-L"))


if __name__ == "__main__":
    unittest.main()
