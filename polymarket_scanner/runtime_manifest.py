from __future__ import annotations

"""Factual, secret-free runtime/release attestation for production health output."""

import hashlib
import json
import re
import subprocess
from datetime import datetime
from pathlib import Path

from .config import settings
from .crypto_v3 import CRYPTO_FEED_VERSION
from .execution_certificate import EXECUTION_CERTIFICATE_VERSION
from .schema_contract import DATABASE_SCHEMA_VERSION
from .sports_v3 import SPORTS_CAUSAL_CACHE_VERSION, SPORTS_MAPPING_VERSION
from .weather_calibration import (
    WEATHER_CALIBRATION_EVIDENCE_VERSION,
    WEATHER_CALIBRATION_VERSION,
)
from .weather_contracts import (
    WEATHER_CONTRACT_ADAPTER,
    WEATHER_FRIEND_MODEL_VERSION,
    WEATHER_LATE_MODEL_VERSION,
)

RUNTIME_MANIFEST_VERSION = "runtime_manifest_v5_schema_and_feed_evidence_versions"
_SHA_RE = re.compile(r"^[0-9a-f]{40}$")


def _git(app_dir: Path, *args: str) -> str | None:
    try:
        completed = subprocess.run(
            ["git", "-C", str(app_dir), *args],
            check=True,
            capture_output=True,
            text=True,
            timeout=3.0,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return completed.stdout.strip()


def _release_marker(path: Path) -> tuple[bool, str | None, str | None]:
    if not path.is_file():
        return False, None, "immutable release marker missing"
    try:
        value = path.read_text(encoding="utf-8").strip().lower()
    except OSError:
        return True, None, "immutable release marker unreadable"
    if not _SHA_RE.fullmatch(value):
        return True, None, "immutable release marker malformed"
    return True, value, None


def _aware_iso(value: object) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.isoformat()


def _dependency_preflight_evidence(path: Path, authorized_sha: str | None) -> dict:
    """Read deployment-time reachability evidence without treating it as live health."""
    base = {
        "evidence_file": str(path),
        "present": path.is_file(),
        "scope": "DEPLOYMENT_TIME_REACHABILITY_NOT_LIVE_HEALTH",
        "valid_for_authorized_release": False,
        "reason": "dependency preflight evidence missing",
        "version": None,
        "release_sha": None,
        "measured_at": None,
        "ok": None,
        "required_failed": None,
        "required_max_elapsed_ms": None,
    }
    if not path.is_file():
        return base
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        base["reason"] = "dependency preflight evidence unreadable or invalid JSON"
        return base
    if not isinstance(payload, dict):
        base["reason"] = "dependency preflight evidence is not a JSON object"
        return base

    version = payload.get("version")
    release_raw = str(payload.get("release_sha") or "").strip().lower()
    release_sha = release_raw if _SHA_RE.fullmatch(release_raw) else None
    measured_at = _aware_iso(payload.get("measured_at"))
    ok = payload.get("ok") if isinstance(payload.get("ok"), bool) else None
    required_failed_raw = payload.get("required_failed")
    required_failed = (
        [str(item) for item in required_failed_raw]
        if isinstance(required_failed_raw, list) and all(isinstance(item, str) for item in required_failed_raw)
        else None
    )
    max_elapsed_raw = payload.get("required_max_elapsed_ms")
    try:
        max_elapsed = float(max_elapsed_raw) if max_elapsed_raw is not None else None
    except (TypeError, ValueError):
        max_elapsed = None
    if max_elapsed is not None and max_elapsed < 0:
        max_elapsed = None

    base.update({
        "version": str(version) if isinstance(version, str) else None,
        "release_sha": release_sha,
        "measured_at": measured_at,
        "ok": ok,
        "required_failed": required_failed,
        "required_max_elapsed_ms": max_elapsed,
    })

    reasons: list[str] = []
    if not base["version"]:
        reasons.append("preflight version missing")
    if release_sha is None:
        reasons.append("preflight release SHA missing or malformed")
    elif authorized_sha is None:
        reasons.append("authorized release SHA unavailable")
    elif release_sha != authorized_sha:
        reasons.append("preflight belongs to a different release")
    if measured_at is None:
        reasons.append("preflight measurement timestamp missing or invalid")
    if ok is not True:
        reasons.append("required dependency preflight did not pass")
    if required_failed is None:
        reasons.append("required dependency failure list missing or malformed")
    elif required_failed:
        reasons.append("required dependency failure list is non-empty")

    valid = not reasons
    base["valid_for_authorized_release"] = valid
    base["reason"] = (
        "required dependency preflight passed for this authorized release"
        if valid
        else "; ".join(reasons)
    )
    return base


def _nonsecret_policy() -> dict:
    """Return safety-relevant configuration only; never include credentials."""
    return {
        "actionable_min_edge": settings.actionable_min_edge,
        "known_outcome_max_ask": settings.known_outcome_max_ask,
        "universe_max_stale_seconds": settings.universe_max_stale_seconds,
        "sports_result_max_age_seconds": settings.sports_result_max_age_seconds,
        "crypto_boundary_tolerance_seconds": settings.crypto_boundary_tolerance_seconds,
        "weather_lock_min_probability": settings.weather_lock_min_probability,
        "weather_open_meteo_enabled": settings.weather_open_meteo_enabled,
        "market_ws_enabled": settings.market_ws_enabled,
        "sports_ws_enabled": settings.sports_ws_enabled,
        "crypto_rtds_enabled": settings.crypto_rtds_enabled,
    }


def _policy_hash(policy: dict) -> str:
    payload = json.dumps(policy, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def build_runtime_manifest(
    *,
    promoted_detectors: tuple[str, ...] | list[str] = (),
    trade_ready_version: str,
    app_dir: str | Path | None = None,
    release_file: str | Path | None = None,
    preflight_file: str | Path | None = None,
) -> dict:
    """Build a read-only release/policy manifest without exposing secret settings.

    Production systemd independently enforces the release marker before start. This
    health manifest makes that fact inspectable and also reports whether deployment-
    time dependency evidence belongs to the same authorized release. Neither field is
    a substitute for live feed-health/freshness gates.
    """
    root = Path(app_dir).resolve() if app_dir is not None else Path(__file__).resolve().parents[1]
    config_dir = Path.home() / ".polymarket-edge-scanner"
    marker = Path(release_file).expanduser() if release_file is not None else config_dir / "release.sha"
    preflight = (
        Path(preflight_file).expanduser()
        if preflight_file is not None
        else config_dir / "dependency-preflight.json"
    )

    marker_present, authorized_sha, marker_error = _release_marker(marker)
    head_raw = _git(root, "rev-parse", "HEAD")
    git_head = head_raw.lower() if head_raw and _SHA_RE.fullmatch(head_raw.lower()) else None
    status = _git(root, "status", "--porcelain", "--untracked-files=no")
    tracked_tree_clean = status == "" if status is not None else None

    reasons: list[str] = []
    if marker_error:
        reasons.append(marker_error)
    if git_head is None:
        reasons.append("git HEAD unavailable or malformed")
    if tracked_tree_clean is not True:
        reasons.append("tracked working tree is dirty or unavailable")
    if authorized_sha is not None and git_head is not None and authorized_sha != git_head:
        reasons.append("git HEAD does not match authorized release")

    attested = bool(
        marker_present
        and authorized_sha is not None
        and git_head is not None
        and authorized_sha == git_head
        and tracked_tree_clean is True
    )
    preflight_evidence = _dependency_preflight_evidence(preflight, authorized_sha)
    runtime_authority_complete = bool(
        attested and preflight_evidence["valid_for_authorized_release"]
    )
    policy = _nonsecret_policy()
    promoted = sorted({str(name) for name in promoted_detectors if str(name)})

    return {
        "version": RUNTIME_MANIFEST_VERSION,
        "app_dir": str(root),
        "release_marker": str(marker),
        "release_marker_present": marker_present,
        "authorized_release_sha": authorized_sha,
        "git_head_sha": git_head,
        "tracked_working_tree_clean": tracked_tree_clean,
        "production_release_attested": attested,
        "release_attestation_reason": "authorized release matches clean checkout" if attested else "; ".join(reasons),
        "dependency_preflight": preflight_evidence,
        "production_runtime_authority_complete": runtime_authority_complete,
        "runtime_authority_scope": (
            "IMMUTABLE_RELEASE_PLUS_DEPLOYMENT_TIME_REQUIRED_DEPENDENCY_PREFLIGHT; "
            "LIVE_FEED_HEALTH_REMAINS_SEPARATE"
        ),
        "promoted_detectors": promoted,
        "promotion_count": len(promoted),
        "p0_containment": len(promoted) == 0,
        "versions": {
            "trade_ready": trade_ready_version,
            "execution_certificate": EXECUTION_CERTIFICATE_VERSION,
            "database_schema_contract": DATABASE_SCHEMA_VERSION,
            "sports_mapping": SPORTS_MAPPING_VERSION,
            "sports_causal_cache": SPORTS_CAUSAL_CACHE_VERSION,
            "crypto_feed": CRYPTO_FEED_VERSION,
            "weather_contract": WEATHER_CONTRACT_ADAPTER,
            "weather_late_model": WEATHER_LATE_MODEL_VERSION,
            "weather_friend_model": WEATHER_FRIEND_MODEL_VERSION,
            "weather_calibration": WEATHER_CALIBRATION_VERSION,
            "weather_calibration_evidence": WEATHER_CALIBRATION_EVIDENCE_VERSION,
        },
        "nonsecret_safety_policy": policy,
        "nonsecret_safety_policy_sha256": _policy_hash(policy),
    }
