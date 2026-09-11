from __future__ import annotations

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


def _proc(tmp_path: Path, *, pid: int = 123, cwd: Path | None = None, cmdline: bytes | None = None) -> Path:
    root = tmp_path / "proc"
    row = root / str(pid)
    row.mkdir(parents=True, exist_ok=True)
    target = cwd or _repo()
    link = row / "cwd"
    if link.exists() or link.is_symlink():
        link.unlink()
    link.symlink_to(target, target_is_directory=True)
    (row / "cmdline").write_bytes(
        cmdline or b"/srv/Alpha/.venv/bin/python\0-m\0polymarket_scanner.weather_only_runtime\0--loop\0--interval-seconds\0300\0"
    )
    return root


def _attest(tmp_path: Path, *, captured_at: float | None = None):
    head = _head()
    return attest_weather_w7_release(
        app_dir=_repo(),
        release_file=_marker(tmp_path, head),
        scanner_process_id=123,
        expected_release_sha=head,
        proc_root=_proc(tmp_path),
        captured_at=captured_at,
    )


def test_live_checkout_attestation_binds_head_marker_runtime_cwd_and_scanner_entrypoint(tmp_path):
    head = _head()
    row = _attest(tmp_path)
    assert row.release_sha == head
    assert row.git_head_sha == head
    assert row.app_dir_sha256 == row.scanner_cwd_sha256
    assert row.tracked_tree_clean is True
    assert row.runtime_under_release_checkout is True
    assert row.scanner_cwd_matches_release_checkout is True
    assert row.scanner_entrypoint_verified is True
    assert len(row.scanner_cmdline_sha256) == 64
    assert row.financial_authority is False
    assert validate_weather_w7_release_attestation(row) == row


def test_marker_expected_runtime_digest_and_entrypoint_tamper_fail_closed(tmp_path):
    head = _head()
    with pytest.raises(WeatherW7ReleaseError) as mismatch:
        attest_weather_w7_release(
            app_dir=_repo(),
            release_file=_marker(tmp_path, "b" * 40),
            scanner_process_id=123,
            expected_release_sha=head,
            proc_root=_proc(tmp_path),
        )
    assert mismatch.value.code == "W7_RELEASE_MARKER_EXPECTED_MISMATCH"

    row = _attest(tmp_path)
    with pytest.raises(WeatherW7ReleaseError) as tamper:
        validate_weather_w7_release_attestation(replace(row, runtime_source_sha256="c" * 64))
    assert tamper.value.code == "W7_RELEASE_EVIDENCE_DIGEST_MISMATCH"

    bad_proc = _proc(
        tmp_path / "bad",
        cmdline=b"/srv/Alpha/.venv/bin/python\0-m\0polymarket_scanner.universe_builder\0--loop\0",
    )
    with pytest.raises(WeatherW7ReleaseError) as entrypoint:
        attest_weather_w7_release(
            app_dir=_repo(),
            release_file=_marker(tmp_path / "bad", head),
            scanner_process_id=123,
            expected_release_sha=head,
            proc_root=bad_proc,
        )
    assert entrypoint.value.code == "W7_RELEASE_SCANNER_ENTRYPOINT_INVALID"


def test_scanner_cwd_must_be_exact_release_checkout(tmp_path):
    head = _head()
    wrong = tmp_path / "wrong"
    wrong.mkdir()
    proc = _proc(tmp_path, cwd=wrong)
    with pytest.raises(WeatherW7ReleaseError) as raised:
        attest_weather_w7_release(
            app_dir=_repo(),
            release_file=_marker(tmp_path, head),
            scanner_process_id=123,
            expected_release_sha=head,
            proc_root=proc,
        )
    assert raised.value.code == "W7_RELEASE_SCANNER_CWD_MISMATCH"


def test_before_after_manifest_rejects_release_identity_change(tmp_path):
    before = _attest(tmp_path, captured_at=100.0)
    after = _attest(tmp_path, captured_at=200.0)
    manifest = build_weather_w7_release_manifest(before=before, after=after)
    assert validate_weather_w7_release_manifest(manifest) == manifest

    changed = replace(after, scanner_cmdline_sha256="f" * 64)
    with pytest.raises(WeatherW7ReleaseError):
        build_weather_w7_release_manifest(before=before, after=changed)
