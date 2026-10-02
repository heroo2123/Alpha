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
