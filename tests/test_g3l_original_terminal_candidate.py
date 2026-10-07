"""Focused offline checks for the historical G3-P terminal candidate."""

import json
from pathlib import Path
import tempfile
import unittest

from tools import validate_g3l_original_terminal_candidate as candidate


class OriginalTerminalCandidateTests(unittest.TestCase):
    def test_exact_local_and_historical_bytes_bind(self):
        hashes = candidate.validate()
        self.assertEqual(set(hashes), {"document", "report", "terminal"})
        self.assertEqual(hashes["terminal"],
                         "414aef99c0576a0e96b86aa9244ad1ca4ffe4b02f161c69e0193f78498d3db22")

    def test_modified_manifest_refuses(self):
        raw = (candidate.ROOT / candidate.MANIFEST).read_bytes()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "identity.json"
            path.write_bytes(raw.replace(b"UNTRUSTED_HISTORICAL", b"TRUSTED___HISTORICAL"))
            with self.assertRaisesRegex(ValueError, "manifest byte pin mismatch"):
                candidate.validate(manifest_path=path)

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
