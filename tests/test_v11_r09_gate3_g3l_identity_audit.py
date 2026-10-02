"""Focused exact-byte regressions for the retained G3-L audit."""

from pathlib import Path

import pytest

from tools import v11_r09_gate3_g3l_identity_audit as subject


REPO = Path(__file__).resolve().parents[1]
ARGS = dict(target_date="2026-10-04", now_utc=1790977200,
            free_disk_bytes=3_000_000_000,
            available_memory_bytes=1_000_000_000)


def test_retained_evidence_counts_and_current_runtime_drift():
    result = subject.audit(REPO, **ARGS)
    assert result["launchable"] is False
    assert result["qualification_credit"] == 0
    assert result["screen"]["missing_before"] == 77
    assert result["screen"]["missing_after"] == 77
    assert result["screen"]["other_findings"] == []
    assert result["category_counts"] == {
        subject.RETAINED_SCOPED: 6, subject.OFFLINE: 1,
        subject.FUTURE: 70, subject.INVALID: 0,
    }
    assert len(result["identities"]) == 77
    assert all(row["qualified_entry"] is None for row in result["identities"].values())
    changed = [path for path, ref in result["artifacts"].items()
               if not ref["current_matches_reviewed_bytes"]]
    assert changed == ["tools/v11_r09_gate3_runtime.py"]
    assert result["identities"][subject.SLICE3_ID]["category"] == subject.FUTURE
    original = result["identities"][subject.ORIGINAL_ID]
    assert original["category"] == subject.OFFLINE
    assert original["source_refs"][-1]["path"] == subject.ORIGINAL_TERMINAL
    assert original["source_refs"][-1]["matches_named_git_bytes"] is True


def test_recovered_terminal_must_bind_exact_report(monkeypatch):
    actual = subject._json

    def changed(path):
        value = actual(path)
        if path.name == Path(subject.ORIGINAL_TERMINAL).name:
            value["report_sha256"] = "0" * 64
        return value

    monkeypatch.setattr(subject, "_json", changed)
    with pytest.raises(ValueError, match="does not bind"):
        subject.audit(REPO, **ARGS)


def test_prior_reconciliation_review_chain_must_bind(monkeypatch):
    actual = subject._json

    def changed(path):
        value = actual(path)
        if path.name == Path(subject.REVIEW_TERMINAL).name:
            value["verdict_sha256"] = "0" * 64
        return value

    monkeypatch.setattr(subject, "_json", changed)
    with pytest.raises(ValueError, match="scoped independent review"):
        subject.audit(REPO, **ARGS)


def test_historical_git_byte_mismatch_refuses(monkeypatch):
    actual = subject._git_bytes

    def changed(repo, commit, path):
        if path == subject.ORIGINAL_REPORT:
            return b"changed historical bytes"
        return actual(repo, commit, path)

    monkeypatch.setattr(subject, "_git_bytes", changed)
    with pytest.raises(ValueError, match="historical Git artifact mismatch"):
        subject.audit(REPO, **ARGS)


def _drift_live_bytes(monkeypatch, path):
    """Simulate a live (on-disk/HEAD) byte drift for one path, independent of
    its recorded historical Git commit -- the same shape of drift the real
    tools/v11_r09_gate3_runtime.py change produced."""
    actual = subject._repo_ref

    def changed(repo, rel_path, commit=None):
        result = actual(repo, rel_path, commit)
        if commit is None and rel_path == path:
            result = dict(result)
            result["sha256"] = "1" * 64
        return result

    monkeypatch.setattr(subject, "_repo_ref", changed)


def test_launch_validator_drift_downgrades_mapping_row_without_hardcoding(monkeypatch):
    # F1 regression: code.mapping_exact_commit_review's own "artifacts" list
    # never lists tools/v11_r09_gate3_launch_v4.py -- only its review report
    # and terminal. Before the fix, drift in that validator file was invisible
    # to this row. The independent review's probe confirmed this exact gap.
    _drift_live_bytes(monkeypatch, "tools/v11_r09_gate3_launch_v4.py")
    result = subject.audit(REPO, **ARGS)
    row = result["identities"]["code.mapping_exact_commit_review"]
    assert row["category"] == subject.FUTURE
    assert "tools/v11_r09_gate3_launch_v4.py" in row["remaining_obligation"]
    drifted_refs = [ref["path"] for ref in row["source_refs"]
                    if not ref["current_matches_reviewed_bytes"]]
    assert drifted_refs == ["tools/v11_r09_gate3_launch_v4.py"]
    assert result["category_counts"] == {
        subject.RETAINED_SCOPED: 5, subject.OFFLINE: 1,
        subject.FUTURE: 71, subject.INVALID: 0,
    }
    # Every other RETAINED row, and G3-L itself, are unaffected.
    assert result["g3l"] == "NO-GO"
    assert result["qualification_credit"] == 0


def test_ledgers_drift_also_caught_generically_for_slice3_row(monkeypatch):
    # The slice-3 row's own "artifacts" never list tools/v11_r09_gate3_ledgers.py
    # either, yet its historical scope statement depends on it. This is the
    # same class of gap as the validator, on a second file, proving the fix
    # is not special-cased to one path.
    _drift_live_bytes(monkeypatch, "tools/v11_r09_gate3_ledgers.py")
    result = subject.audit(REPO, **ARGS)
    row = result["identities"][subject.SLICE3_ID]
    assert row["category"] == subject.FUTURE
    drifted_refs = {ref["path"] for ref in row["source_refs"]
                    if not ref["current_matches_reviewed_bytes"]}
    # The real, already-drifted runtime file and the freshly-simulated
    # ledgers drift are both detected in the same row.
    assert drifted_refs == {"tools/v11_r09_gate3_runtime.py", "tools/v11_r09_gate3_ledgers.py"}


def test_malformed_code_byte_observation_refuses(monkeypatch):
    actual = subject._json

    def changed(path):
        value = actual(path)
        if path.name == Path(subject.RECONCILIATION).name:
            value = dict(value)
            value["code_byte_observations"] = dict(value["code_byte_observations"])
            broken = dict(value["code_byte_observations"]["launch_validator"])
            del broken["commit_oid"]
            value["code_byte_observations"]["launch_validator"] = broken
        return value

    monkeypatch.setattr(subject, "_json", changed)
    with pytest.raises(ValueError, match="malformed code_byte_observation"):
        subject.audit(REPO, **ARGS)


def test_original_terminal_marker_mismatch_refuses(monkeypatch):
    actual = subject._json

    def changed(path):
        value = actual(path)
        if path.name == Path(subject.ORIGINAL_TERMINAL).name:
            value = dict(value)
            value["marker"] = "SOMETHING_ELSE"
        return value

    monkeypatch.setattr(subject, "_json", changed)
    with pytest.raises(ValueError, match="does not bind"):
        subject.audit(REPO, **ARGS)


def test_original_terminal_nonnull_error_refuses(monkeypatch):
    actual = subject._json

    def changed(path):
        value = actual(path)
        if path.name == Path(subject.ORIGINAL_TERMINAL).name:
            value = dict(value)
            value["error"] = "unexpected"
        return value

    monkeypatch.setattr(subject, "_json", changed)
    with pytest.raises(ValueError, match="does not bind"):
        subject.audit(REPO, **ARGS)


def test_git_read_timeout_fails_closed(monkeypatch):
    import subprocess as subprocess_module

    def timed_out(*args, **kwargs):
        raise subprocess_module.TimeoutExpired(cmd="git", timeout=30)

    monkeypatch.setattr(subject.subprocess, "run", timed_out)
    with pytest.raises(ValueError, match="git read timed out"):
        subject._git_bytes(REPO, "23c11e059048257a284d514d3454b811b3866515",
                           "tools/v11_r09_gate3_launch_v4.py")


def test_screen_before_after_wording_states_single_measurement():
    result = subject.audit(REPO, **ARGS)
    assert result["screen"]["screen_runs"] == 1
    assert "same single" in result["screen"]["before_after_note"]
