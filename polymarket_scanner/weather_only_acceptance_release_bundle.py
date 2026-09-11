from __future__ import annotations

"""Final release-bound wrapper for weather W7 acceptance evidence.

The inner acceptance bundle proves measured latency, process continuity, read-only DB
containment and code-surface containment. This outer envelope adds independently
captured before/after Git release provenance and is the artifact that the real W7 run
must persist and validate.
"""

import hashlib
import json
import math
import re
from dataclasses import dataclass, field

from .weather_only_acceptance_bundle import (
    WeatherW7AcceptanceBundle,
    WeatherW7BundleError,
    dump_weather_w7_acceptance_bundle_json,
    load_weather_w7_acceptance_bundle_json,
    validate_weather_w7_acceptance_bundle,
)
from .weather_only_acceptance_release import (
    WEATHER_W7_RELEASE_VERSION,
    WeatherW7ReleaseAttestation,
    WeatherW7ReleaseError,
    WeatherW7ReleaseManifest,
    validate_weather_w7_release_manifest,
)


WEATHER_W7_RELEASE_BUNDLE_VERSION = "weather_w7_release_bundle_v1_inner_bundle_git_release_before_after"
_SHA40_RE = re.compile(r"^[0-9a-f]{40}$")
_SHA64_RE = re.compile(r"^[0-9a-f]{64}$")
_ATTESTATION_KEYS = frozenset({
    "version", "captured_at", "release_sha", "git_head_sha", "release_marker_sha256",
    "app_dir_sha256", "scanner_cwd_sha256", "runtime_source_sha256", "scanner_process_id",
    "evidence_sha256", "tracked_tree_clean", "runtime_under_release_checkout",
    "scanner_cwd_matches_release_checkout", "financial_authority", "financial_delivery",
    "automatic_order_placement",
})
_MANIFEST_KEYS = frozenset({
    "version", "before", "after", "evidence_sha256", "financial_authority",
    "financial_delivery", "automatic_order_placement",
})
_BUNDLE_KEYS = frozenset({
    "version", "acceptance_bundle", "release_manifest", "evidence_sha256",
    "financial_authority", "financial_delivery", "detector_promotion_authority",
    "automatic_order_placement",
})


class WeatherW7ReleaseBundleError(RuntimeError):
    def __init__(self, code: str):
        self.code = str(code)
        super().__init__(self.code)


def _canonical(value: object) -> str:
    try:
        return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False)
    except (TypeError, ValueError):
        raise WeatherW7ReleaseBundleError("W7_RELEASE_BUNDLE_JSON_INVALID") from None


def _sha(value: object) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _exact(value: object, keys: frozenset[str], code: str) -> dict:
    if not isinstance(value, dict) or set(value) != keys:
        raise WeatherW7ReleaseBundleError(code)
    return value


def _false(value: object) -> None:
    if type(value) is not bool or value is not False:
        raise WeatherW7ReleaseBundleError("W7_RELEASE_BUNDLE_AUTHORITY_BOUNDARY_BROKEN")


def _sha40(value: object, code: str) -> str:
    text = str(value or "").strip().lower()
    if not _SHA40_RE.fullmatch(text):
        raise WeatherW7ReleaseBundleError(code)
    return text


def _sha64(value: object, code: str) -> str:
    text = str(value or "").strip().lower()
    if not _SHA64_RE.fullmatch(text):
        raise WeatherW7ReleaseBundleError(code)
    return text


def _number(value: object, code: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise WeatherW7ReleaseBundleError(code)
    out = float(value)
    if not math.isfinite(out) or out < 0.0:
        raise WeatherW7ReleaseBundleError(code)
    return out


def _positive_int(value: object, code: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise WeatherW7ReleaseBundleError(code)
    return value


def _attestation_from_dict(raw: object) -> WeatherW7ReleaseAttestation:
    row = _exact(raw, _ATTESTATION_KEYS, "W7_RELEASE_BUNDLE_ATTESTATION_SCHEMA_INVALID")
    for key in (
        "financial_authority", "financial_delivery", "automatic_order_placement",
    ):
        _false(row[key])
    if row["tracked_tree_clean"] is not True or row["runtime_under_release_checkout"] is not True or row["scanner_cwd_matches_release_checkout"] is not True:
        raise WeatherW7ReleaseBundleError("W7_RELEASE_BUNDLE_RELEASE_IDENTITY_FALSE")
    return WeatherW7ReleaseAttestation(
        version=str(row["version"]),
        captured_at=_number(row["captured_at"], "W7_RELEASE_BUNDLE_CAPTURE_TIME_INVALID"),
        release_sha=_sha40(row["release_sha"], "W7_RELEASE_BUNDLE_RELEASE_SHA_INVALID"),
        git_head_sha=_sha40(row["git_head_sha"], "W7_RELEASE_BUNDLE_GIT_SHA_INVALID"),
        release_marker_sha256=_sha64(row["release_marker_sha256"], "W7_RELEASE_BUNDLE_COMPONENT_SHA_INVALID"),
        app_dir_sha256=_sha64(row["app_dir_sha256"], "W7_RELEASE_BUNDLE_COMPONENT_SHA_INVALID"),
        scanner_cwd_sha256=_sha64(row["scanner_cwd_sha256"], "W7_RELEASE_BUNDLE_COMPONENT_SHA_INVALID"),
        runtime_source_sha256=_sha64(row["runtime_source_sha256"], "W7_RELEASE_BUNDLE_COMPONENT_SHA_INVALID"),
        scanner_process_id=_positive_int(row["scanner_process_id"], "W7_RELEASE_BUNDLE_PROCESS_ID_INVALID"),
        evidence_sha256=_sha64(row["evidence_sha256"], "W7_RELEASE_BUNDLE_COMPONENT_SHA_INVALID"),
    )


def _manifest_from_dict(raw: object) -> WeatherW7ReleaseManifest:
    row = _exact(raw, _MANIFEST_KEYS, "W7_RELEASE_BUNDLE_MANIFEST_SCHEMA_INVALID")
    for key in ("financial_authority", "financial_delivery", "automatic_order_placement"):
        _false(row[key])
    manifest = WeatherW7ReleaseManifest(
        version=str(row["version"]),
        before=_attestation_from_dict(row["before"]),
        after=_attestation_from_dict(row["after"]),
        evidence_sha256=_sha64(row["evidence_sha256"], "W7_RELEASE_BUNDLE_MANIFEST_SHA_INVALID"),
    )
    try:
        return validate_weather_w7_release_manifest(manifest)
    except WeatherW7ReleaseError as exc:
        raise WeatherW7ReleaseBundleError(f"W7_RELEASE_BUNDLE_MANIFEST_INVALID:{exc.code}") from exc


@dataclass(frozen=True, slots=True)
class WeatherW7ReleaseBoundBundle:
    version: str
    acceptance_bundle: WeatherW7AcceptanceBundle
    release_manifest: WeatherW7ReleaseManifest
    evidence_sha256: str
    financial_authority: bool = field(init=False, default=False)
    financial_delivery: bool = field(init=False, default=False)
    detector_promotion_authority: bool = field(init=False, default=False)
    automatic_order_placement: bool = field(init=False, default=False)

    def as_dict(self) -> dict:
        return {
            "version": self.version,
            "acceptance_bundle": self.acceptance_bundle.as_dict(),
            "release_manifest": self.release_manifest.as_dict(),
            "evidence_sha256": self.evidence_sha256,
            "financial_authority": False,
            "financial_delivery": False,
            "detector_promotion_authority": False,
            "automatic_order_placement": False,
        }


def _payload(row: WeatherW7ReleaseBoundBundle) -> dict:
    value = row.as_dict()
    value.pop("evidence_sha256", None)
    return value


def validate_weather_w7_release_bound_bundle(
    row: object,
    *,
    expected_release_sha: str | None = None,
):
    if not isinstance(row, WeatherW7ReleaseBoundBundle) or row.version != WEATHER_W7_RELEASE_BUNDLE_VERSION:
        raise WeatherW7ReleaseBundleError("W7_RELEASE_BUNDLE_TYPE_OR_VERSION_INVALID")
    if any((
        row.financial_authority is not False,
        row.financial_delivery is not False,
        row.detector_promotion_authority is not False,
        row.automatic_order_placement is not False,
    )):
        raise WeatherW7ReleaseBundleError("W7_RELEASE_BUNDLE_AUTHORITY_BOUNDARY_BROKEN")
    try:
        release = validate_weather_w7_release_manifest(row.release_manifest)
    except WeatherW7ReleaseError as exc:
        raise WeatherW7ReleaseBundleError(f"W7_RELEASE_BUNDLE_MANIFEST_INVALID:{exc.code}") from exc
    release_sha = release.before.release_sha
    if expected_release_sha is not None and _sha40(expected_release_sha, "W7_RELEASE_BUNDLE_EXPECTED_SHA_INVALID") != release_sha:
        raise WeatherW7ReleaseBundleError("W7_RELEASE_BUNDLE_EXPECTED_SHA_MISMATCH")
    try:
        report = validate_weather_w7_acceptance_bundle(
            row.acceptance_bundle,
            expected_release_sha=release_sha,
        )
    except WeatherW7BundleError as exc:
        raise WeatherW7ReleaseBundleError(f"W7_RELEASE_BUNDLE_INNER_INVALID:{exc.code}") from exc

    containment = row.acceptance_bundle.containment_manifest
    if release.before.scanner_process_id != containment.before_process.process_id or release.after.scanner_process_id != containment.after_process.process_id:
        raise WeatherW7ReleaseBundleError("W7_RELEASE_BUNDLE_PROCESS_ID_MISMATCH")
    samples = row.acceptance_bundle.w7_evidence.run_evidence.samples
    if samples:
        if release.before.captured_at > samples[0].observed_at + 1e-9:
            raise WeatherW7ReleaseBundleError("W7_RELEASE_BUNDLE_RELEASE_STARTED_AFTER_SAMPLES")
        if release.after.captured_at + 1e-9 < samples[-1].observed_at:
            raise WeatherW7ReleaseBundleError("W7_RELEASE_BUNDLE_RELEASE_ENDED_BEFORE_SAMPLES")
    supplied = _sha64(row.evidence_sha256, "W7_RELEASE_BUNDLE_SHA_INVALID")
    if supplied != _sha(_payload(row)):
        raise WeatherW7ReleaseBundleError("W7_RELEASE_BUNDLE_DIGEST_MISMATCH")
    return report


def build_weather_w7_release_bound_bundle(
    *,
    acceptance_bundle: WeatherW7AcceptanceBundle,
    release_manifest: WeatherW7ReleaseManifest,
) -> WeatherW7ReleaseBoundBundle:
    release = validate_weather_w7_release_manifest(release_manifest)
    shell = WeatherW7ReleaseBoundBundle(
        version=WEATHER_W7_RELEASE_BUNDLE_VERSION,
        acceptance_bundle=acceptance_bundle,
        release_manifest=release,
        evidence_sha256="0" * 64,
    )
    final = WeatherW7ReleaseBoundBundle(
        version=shell.version,
        acceptance_bundle=shell.acceptance_bundle,
        release_manifest=shell.release_manifest,
        evidence_sha256=_sha(_payload(shell)),
    )
    validate_weather_w7_release_bound_bundle(final)
    return final


def dump_weather_w7_release_bound_bundle_json(row: WeatherW7ReleaseBoundBundle) -> str:
    validate_weather_w7_release_bound_bundle(row)
    return json.dumps(row.as_dict(), sort_keys=True, indent=2, allow_nan=False)


def load_weather_w7_release_bound_bundle_json(
    raw_json: str,
    *,
    expected_release_sha: str | None = None,
):
    if not isinstance(raw_json, str) or not raw_json.strip():
        raise WeatherW7ReleaseBundleError("W7_RELEASE_BUNDLE_JSON_EMPTY")
    try:
        raw = json.loads(raw_json)
    except json.JSONDecodeError:
        raise WeatherW7ReleaseBundleError("W7_RELEASE_BUNDLE_JSON_INVALID") from None
    value = _exact(raw, _BUNDLE_KEYS, "W7_RELEASE_BUNDLE_SCHEMA_INVALID")
    for key in (
        "financial_authority", "financial_delivery", "detector_promotion_authority",
        "automatic_order_placement",
    ):
        _false(value[key])
    release = _manifest_from_dict(value["release_manifest"])
    try:
        inner, _ = load_weather_w7_acceptance_bundle_json(
            json.dumps(value["acceptance_bundle"], sort_keys=True, allow_nan=False),
            expected_release_sha=release.before.release_sha,
        )
    except WeatherW7BundleError as exc:
        raise WeatherW7ReleaseBundleError(f"W7_RELEASE_BUNDLE_INNER_INVALID:{exc.code}") from exc
    bundle = WeatherW7ReleaseBoundBundle(
        version=str(value["version"]),
        acceptance_bundle=inner,
        release_manifest=release,
        evidence_sha256=_sha64(value["evidence_sha256"], "W7_RELEASE_BUNDLE_SHA_INVALID"),
    )
    report = validate_weather_w7_release_bound_bundle(bundle, expected_release_sha=expected_release_sha)
    return bundle, report
