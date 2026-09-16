from __future__ import annotations

import importlib.util
from pathlib import Path


SCRIPT = Path("deploy/verify-all-paper-first-cycle.py")
SPEC = importlib.util.spec_from_file_location("verify_all_paper_first_cycle", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)

SHA = "a" * 40
OTHER = "b" * 40
NOW = 1_800_000_000.0


def test_same_release_missing_final_marker_is_waitable():
    status = {
        "release_sha": SHA,
        "finished_at": NOW,
        "all_paper_v8_runtime_version": "intermediate-v8",
    }
    assert MODULE._waitable_intermediate(
        status, expected_release_sha=SHA, not_before=NOW - 1.0
    ) == MODULE.FINAL_INTERMEDIATE_WAIT_CODE


def test_same_release_prestart_snapshot_is_waitable_as_stale_even_without_final_marker():
    status = {"release_sha": SHA, "finished_at": NOW - 10.0}
    assert MODULE._waitable_intermediate(
        status, expected_release_sha=SHA, not_before=NOW
    ) == "ALL_PAPER_STATUS_PREDATES_START"


def test_present_wrong_final_marker_is_not_waitable():
    status = {
        "release_sha": SHA,
        "finished_at": NOW,
        "final_all_paper_runtime_version": "wrong-final-version",
    }
    assert MODULE._waitable_intermediate(
        status, expected_release_sha=SHA, not_before=NOW - 1.0
    ) is None


def test_wrong_release_is_not_waitable_even_when_final_marker_missing():
    status = {"release_sha": OTHER, "finished_at": NOW}
    assert MODULE._waitable_intermediate(
        status, expected_release_sha=SHA, not_before=NOW - 1.0
    ) is None
