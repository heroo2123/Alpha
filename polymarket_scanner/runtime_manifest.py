from __future__ import annotations

"""Factual, secret-free runtime/release attestation for production health output."""

import hashlib
import json
import re
import subprocess
from pathlib import Path

from .config import settings
from .execution_certificate import EXECUTION_CERTIFICATE_VERSION
from .sports_v3 import SPORTS_MAPPING_VERSION
from .weather_calibration import (
    WEATHER_CALIBRATION_EVIDENCE_VERSION,
    WEATHER_CALIBRATION_VERSION,
)
from .weather_contracts import (
    WEATHER_CONTRACT_ADAPTER,
    WEATHER_FRIEND_MODEL_VERSION,
    WEATHER_LATE_MODEL_VERSION,
)

RUNTIME_MANIFEST_VERSION = "runtime_manifest_v1"
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
) -> dict:
    """Build a read-only release/policy manifest without exposing secret settings.

    Production systemd independently enforces the same release marker before start.
    This health manifest makes that fact inspectable; it is not a substitute for the
    ExecStartPre guard. A local/dev process with no marker is reported unattested.
    """
    root = Path(app_dir).resolve() if app_dir is not None else Path(__file__).resolve().parents[1]
    marker = (
        Path(release_file).expanduser()
        if release_file is not None
        else Path.home() / ".polymarket-edge-scanner" / "release.sha"
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
        "promoted_detectors": promoted,
        "promotion_count": len(promoted),
        "p0_containment": len(promoted) == 0,
        "versions": {
            "trade_ready": trade_ready_version,
            "execution_certificate": EXECUTION_CERTIFICATE_VERSION,
            "sports_mapping": SPORTS_MAPPING_VERSION,
            "weather_contract": WEATHER_CONTRACT_ADAPTER,
            "weather_late_model": WEATHER_LATE_MODEL_VERSION,
            "weather_friend_model": WEATHER_FRIEND_MODEL_VERSION,
            "weather_calibration": WEATHER_CALIBRATION_VERSION,
            "weather_calibration_evidence": WEATHER_CALIBRATION_EVIDENCE_VERSION,
        },
        "nonsecret_safety_policy": policy,
        "nonsecret_safety_policy_sha256": _policy_hash(policy),
    }
