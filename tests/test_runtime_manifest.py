import json
import subprocess
from pathlib import Path

from polymarket_scanner.config import settings
from polymarket_scanner.crypto_v3 import CRYPTO_FEED_VERSION
from polymarket_scanner.schema_contract import DATABASE_SCHEMA_VERSION
from polymarket_scanner.sports_v3 import SPORTS_CAUSAL_CACHE_VERSION
from polymarket_scanner.runtime_manifest import (
    RUNTIME_MANIFEST_VERSION,
    build_runtime_manifest,
)


def _git(cwd: Path, *args: str) -> str:
    return subprocess.check_output(["git", "-C", str(cwd), *args], text=True).strip()


def _repo(tmp_path: Path) -> tuple[Path, str]:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-b", "main", str(repo)], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.email", "test@example.com"], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.name", "Test"], check=True)
    (repo / "app.py").write_text("print('ok')\n", encoding="utf-8")
    (repo / "requirements.txt").write_text("pytest==8.3.3\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "app.py", "requirements.txt"], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-m", "initial"], check=True, capture_output=True)
    return repo, _git(repo, "rev-parse", "HEAD")


def _preflight(path: Path, sha: str, *, ok: bool = True, failed=None) -> None:
    path.write_text(
        json.dumps(
            {
                "version": "dependency_preflight_v5_gamma_keyset_continuation_release_bound",
                "release_sha": sha,
                "measured_at": "2026-09-08T09:12:00+00:00",
                "ok": ok,
                "required_failed": [] if failed is None else failed,
                "required_max_elapsed_ms": 123.4,
            }
        ),
        encoding="utf-8",
    )


def test_matching_marker_clean_tree_preflight_and_dependency_environment_are_fully_attested(tmp_path):
    repo, sha = _repo(tmp_path)
    marker = tmp_path / "release.sha"
    marker.write_text(sha + "\n", encoding="utf-8")
    preflight = tmp_path / "dependency-preflight.json"
    _preflight(preflight, sha)
    manifest = build_runtime_manifest(
        promoted_detectors=(),
        trade_ready_version="trade-test",
        app_dir=repo,
        release_file=marker,
        preflight_file=preflight,
    )
    assert manifest["version"] == RUNTIME_MANIFEST_VERSION
    assert manifest["authorized_release_sha"] == sha
    assert manifest["git_head_sha"] == sha
    assert manifest["tracked_working_tree_clean"] is True
    assert manifest["production_release_attested"] is True
    assert manifest["dependency_preflight"]["valid_for_authorized_release"] is True
    assert manifest["dependency_preflight"]["release_sha"] == sha
    assert manifest["dependency_preflight"]["required_max_elapsed_ms"] == 123.4
    assert manifest["dependency_environment"]["compatible"] is True
    assert manifest["dependency_environment"]["matched_count"] == 1
    assert len(manifest["dependency_environment"]["requirements_sha256"]) == 64
    assert manifest["production_runtime_authority_complete"] is True
    assert manifest["p0_containment"] is True
    assert manifest["promotion_count"] == 0
    assert manifest["versions"]["trade_ready"] == "trade-test"
    assert manifest["versions"]["database_schema_contract"] == DATABASE_SCHEMA_VERSION
    assert manifest["versions"]["crypto_feed"] == CRYPTO_FEED_VERSION
    assert manifest["versions"]["sports_causal_cache"] == SPORTS_CAUSAL_CACHE_VERSION
    policy = manifest["nonsecret_safety_policy"]
    assert policy["max_events"] == settings.max_events
    assert policy["gamma_page_size"] == settings.gamma_page_size
    assert policy["gamma_page_concurrency"] == settings.gamma_page_concurrency
    assert len(manifest["nonsecret_safety_policy_sha256"]) == 64
    assert "telegram_bot_token" not in policy
    assert "telegram_chat_id" not in policy


def test_universe_cap_is_bound_into_nonsecret_policy_hash(tmp_path, monkeypatch):
    repo, sha = _repo(tmp_path)
    marker = tmp_path / "release.sha"
    marker.write_text(sha, encoding="utf-8")
    monkeypatch.setattr(settings, "max_events", 10000)
    first = build_runtime_manifest(
        promoted_detectors=(), trade_ready_version="v", app_dir=repo, release_file=marker
    )
    monkeypatch.setattr(settings, "max_events", 20000)
    second = build_runtime_manifest(
        promoted_detectors=(), trade_ready_version="v", app_dir=repo, release_file=marker
    )
    assert first["nonsecret_safety_policy"]["max_events"] == 10000
    assert second["nonsecret_safety_policy"]["max_events"] == 20000
    assert first["nonsecret_safety_policy_sha256"] != second["nonsecret_safety_policy_sha256"]


def test_dependency_version_drift_cannot_complete_runtime_authority(tmp_path):
    repo, sha = _repo(tmp_path)
    marker = tmp_path / "release.sha"
    marker.write_text(sha, encoding="utf-8")
    preflight = tmp_path / "dependency-preflight.json"
    _preflight(preflight, sha)
    bad_lock = tmp_path / "bad-requirements.txt"
    bad_lock.write_text("pytest==0.0.1\n", encoding="utf-8")
    manifest = build_runtime_manifest(
        promoted_detectors=(), trade_ready_version="v", app_dir=repo,
        release_file=marker, preflight_file=preflight, requirements_file=bad_lock,
    )
    assert manifest["production_release_attested"] is True
    assert manifest["dependency_preflight"]["valid_for_authorized_release"] is True
    assert manifest["dependency_environment"]["compatible"] is False
    assert manifest["production_runtime_authority_complete"] is False


def test_preflight_from_different_release_cannot_complete_runtime_authority(tmp_path):
    repo, sha = _repo(tmp_path)
    marker = tmp_path / "release.sha"
    marker.write_text(sha, encoding="utf-8")
    preflight = tmp_path / "dependency-preflight.json"
    _preflight(preflight, "0" * 40)
    manifest = build_runtime_manifest(
        promoted_detectors=(),
        trade_ready_version="v",
        app_dir=repo,
        release_file=marker,
        preflight_file=preflight,
    )
    assert manifest["production_release_attested"] is True
    assert manifest["dependency_preflight"]["valid_for_authorized_release"] is False
    assert "different release" in manifest["dependency_preflight"]["reason"]
    assert manifest["production_runtime_authority_complete"] is False


def test_failed_preflight_cannot_complete_runtime_authority(tmp_path):
    repo, sha = _repo(tmp_path)
    marker = tmp_path / "release.sha"
    marker.write_text(sha, encoding="utf-8")
    preflight = tmp_path / "dependency-preflight.json"
    _preflight(preflight, sha, ok=False, failed=["polymarket_clob"])
    manifest = build_runtime_manifest(
        promoted_detectors=(),
        trade_ready_version="v",
        app_dir=repo,
        release_file=marker,
        preflight_file=preflight,
    )
    assert manifest["dependency_preflight"]["valid_for_authorized_release"] is False
    assert manifest["production_runtime_authority_complete"] is False


def test_dirty_tracked_tree_fails_runtime_attestation(tmp_path):
    repo, sha = _repo(tmp_path)
    marker = tmp_path / "release.sha"
    marker.write_text(sha, encoding="utf-8")
    (repo / "app.py").write_text("print('changed')\n", encoding="utf-8")
    manifest = build_runtime_manifest(
        promoted_detectors=(), trade_ready_version="v", app_dir=repo, release_file=marker
    )
    assert manifest["tracked_working_tree_clean"] is False
    assert manifest["production_release_attested"] is False
    assert manifest["production_runtime_authority_complete"] is False
    assert "dirty" in manifest["release_attestation_reason"]


def test_mismatched_or_malformed_release_marker_fails_attestation(tmp_path):
    repo, _ = _repo(tmp_path)
    marker = tmp_path / "release.sha"
    marker.write_text("0" * 40, encoding="utf-8")
    mismatch = build_runtime_manifest(
        promoted_detectors=(), trade_ready_version="v", app_dir=repo, release_file=marker
    )
    assert mismatch["production_release_attested"] is False
    assert "does not match" in mismatch["release_attestation_reason"]

    marker.write_text("not-a-sha", encoding="utf-8")
    malformed = build_runtime_manifest(
        promoted_detectors=(), trade_ready_version="v", app_dir=repo, release_file=marker
    )
    assert malformed["authorized_release_sha"] is None
    assert malformed["production_release_attested"] is False
    assert "malformed" in malformed["release_attestation_reason"]


def test_missing_marker_is_explicitly_unattested_not_silently_healthy(tmp_path):
    repo, _ = _repo(tmp_path)
    manifest = build_runtime_manifest(
        promoted_detectors=["example"],
        trade_ready_version="v",
        app_dir=repo,
        release_file=tmp_path / "missing.sha",
    )
    assert manifest["release_marker_present"] is False
    assert manifest["production_release_attested"] is False
    assert manifest["production_runtime_authority_complete"] is False
    assert manifest["promotion_count"] == 1
    assert manifest["p0_containment"] is False
    assert "missing" in manifest["release_attestation_reason"]


def test_untracked_files_do_not_break_same_tracked_tree_policy_as_systemd_guard(tmp_path):
    repo, sha = _repo(tmp_path)
    marker = tmp_path / "release.sha"
    marker.write_text(sha, encoding="utf-8")
    (repo / "runtime-data.db").write_text("untracked", encoding="utf-8")
    manifest = build_runtime_manifest(
        promoted_detectors=(), trade_ready_version="v", app_dir=repo, release_file=marker
    )
    assert manifest["tracked_working_tree_clean"] is True
    assert manifest["production_release_attested"] is True
