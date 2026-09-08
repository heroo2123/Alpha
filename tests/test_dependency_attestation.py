from pathlib import Path

from polymarket_scanner.dependency_attestation import (
    DEPENDENCY_ATTESTATION_VERSION,
    attest_dependency_environment,
    parse_exact_requirements,
)


def test_current_ci_environment_matches_runtime_dependency_lock():
    root = Path(__file__).resolve().parents[1]
    attestation = attest_dependency_environment(root / "requirements.txt")
    assert attestation["version"] == DEPENDENCY_ATTESTATION_VERSION
    assert attestation["requirements_present"] is True
    assert len(attestation["requirements_sha256"]) == 64
    assert attestation["expected_count"] >= 20
    assert attestation["matched_count"] == attestation["expected_count"]
    assert attestation["mismatches"] == {}
    assert attestation["compatible"] is True


def test_dependency_attestation_reports_version_drift(tmp_path):
    lock = tmp_path / "requirements.txt"
    lock.write_text("pytest==0.0.1\n", encoding="utf-8")
    attestation = attest_dependency_environment(lock)
    assert attestation["compatible"] is False
    assert attestation["mismatches"]["pytest"]["expected"] == "0.0.1"
    assert attestation["mismatches"]["pytest"]["installed"] is not None


def test_dependency_parser_rejects_ranges_markers_and_nested_files(tmp_path):
    for text in (
        "httpx>=0.27\n",
        "httpx==0.27.2; python_version > '3.10'\n",
        "-r other.txt\n",
    ):
        lock = tmp_path / "requirements.txt"
        lock.write_text(text, encoding="utf-8")
        try:
            parse_exact_requirements(lock)
        except ValueError:
            pass
        else:
            raise AssertionError(f"unsafe dependency line was accepted: {text!r}")
