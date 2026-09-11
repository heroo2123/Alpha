from __future__ import annotations

import os
import subprocess
from dataclasses import replace
from pathlib import Path

import pytest

from polymarket_scanner.weather_only_acceptance_release import (
    WeatherW7ReleaseError,
    attest_weather_w7_release,
    build_weather_w7_release_manifest,
    validate_weather_w7_release_attestation,
    validate_weather_w7_release_manifest,
)


def _repo() -> Path:
    return Path(__file__).resolve().parents[1]


def _head() -> str:
    return subprocess.run(
        ["git", "-C", str(_repo()), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip().lower()


def _marker(tmp_path: Path, value: str) -> Path:
    path = tmp_path / "release.sha"
    path.write_text(value + "\n", encoding="ascii")
    return path


def test_live_checkout_attestation_binds_head_marker_runtime_and_current_cwd(tmp_path):
    head = _head()
    row = attest_weather_w7_release(
        app_dir=_repo(),
        release_file=_marker(tmp_path, head),
        scanner_process_id=os.getpid(),
        expected_release_sha=head,
    )
    assert row.release_sha == head
    assert row.git_head_sha == head
    assert row.app_dir_sha256 == row.scanner_cwd_sha256
    assert row.tracked_tree_clean is True
    assert row.runtime_under_release_checkout is True
    assert row.scanner_cwd_matches_release_checkout is True
    assert row.financial_authority is False
    assert len(row.evidence_sha256) == 64
    assert validate_weather_w7_release_attestation(row) == row


def test_marker_expected_head_and_runtime_digest_tamper_fail_closed(tmp_path):
    head = _head()
    marker = _marker(tmp_path, "b" * 40)
    with pytest.raises(WeatherW7ReleaseError) as mismatch:
        attest_weather_w7_release(
            app_dir=_repo(),
            release_file=marker,
            scanner_process_id=os.getpid(),
            expected_release_sha=head,
        )
    assert mismatch.value.code == "W7_RELEASE_MARKER_EXPECTED_MISMATCH"

    row = attest_weather_w7_release(
        app_dir=_repo(),
        release_file=_marker(tmp_path, head),
        scanner_process_id=os.getpid(),
        expected_release_sha=head,
    )
    with pytest.raises(WeatherW7ReleaseError) as tamper:
        validate_weather_w7_release_attestation(replace(row, runtime_source_sha256="c" * 64))
    assert tamper.value.code == "W7_RELEASE_EVIDENCE_DIGEST_MISMATCH"


def test_scanner_cwd_must_be_exact_release_checkout(tmp_path):
    head = _head()
    proc = tmp_path / "proc"
    pid = 123
    (proc / str(pid)).mkdir(parents=True)
    wrong = tmp_path / "wrong"
    wrong.mkdir()
    (proc / str(pid) / "cwd").symlink_to(wrong, target_is_directory=True)
    with pytest.raises(WeatherW7ReleaseError) as raised:
        attest_weather_w7_release(
            app_dir=_repo(),
            release_file=_marker(tmp_path, head),
            scanner_process_id=pid,
            expected_release_sha=head,
            proc_root=proc,
        )
    assert raised.value.code == "W7_RELEASE_SCANNER_CWD_MISMATCH"


def test_before_after_manifest_rejects_release_identity_change(tmp_path):
    head = _head()
    marker = _marker(tmp_path, head)
    before = attest_weather_w7_release(
        app_dir=_repo(),
        release_file=marker,
        scanner_process_id=os.getpid(),
        expected_release_sha=head,
        captured_at=100.0,
    )
    after = attest_weather_w7_release(
        app_dir=_repo(),
        release_file=marker,
        scanner_process_id=os.getpid(),
        expected_release_sha=head,
        captured_at=200.0,
    )
    manifest = build_weather_w7_release_manifest(before=before, after=after)
    assert validate_weather_w7_release_manifest(manifest) == manifest

    changed = replace(after, scanner_process_id=after.scanner_process_id + 1)
    # Re-hashing is intentionally not offered here; even raw field tamper fails first.
    with pytest.raises(WeatherW7ReleaseError):
        build_weather_w7_release_manifest(before=before, after=changed)
