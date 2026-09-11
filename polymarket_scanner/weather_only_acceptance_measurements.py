from __future__ import annotations

"""Strict embedded measurement manifest for W7 acceptance evidence.

The W7 acceptance sample carries numeric latency fields plus SHA references.  A SHA
string alone is not provenance: a hand-authored random digest could otherwise be
placed beside a hand-authored latency.  This manifest embeds the actual measurement
records, recomputes their internal digests, rejects duplicate/replayed records and
provides exact-schema JSON loading for the outer W7 evidence envelope.

No source fetch, order, delivery, service control or financial authority exists here.
"""

import hashlib
import json
import math
import re
from dataclasses import asdict, dataclass, field

from .weather_only_acceptance_latency import (
    WEATHER_W7_SOURCE_LATENCY_VERSION,
    WeatherW7SourceUpdateLatencyMeasurement,
    _measurement_digest_payload as _source_measurement_payload,
    _sha as _source_sha,
)
from .weather_only_incremental import (
    WEATHER_INCREMENTAL_VERSION,
    WeatherIncrementalError,
    WeatherIncrementalLatencyMeasurement,
    validate_weather_incremental_latency_measurement,
)


WEATHER_W7_MEASUREMENT_MANIFEST_VERSION = "weather_w7_measurement_manifest_v1_embedded_records_exact_digest"
_SHA64_RE = re.compile(r"^[0-9a-f]{64}$")

_INCREMENTAL_KEYS = frozenset({
    "version",
    "receipt_evidence_sha256",
    "evaluation_started_at",
    "evaluation_finished_at",
    "incremental_evaluation_seconds",
    "measurement_evidence_sha256",
    "financial_authority",
    "financial_delivery",
    "automatic_order_placement",
})
_SOURCE_KEYS = frozenset({
    "version",
    "trigger_evidence_sha256",
    "candidate_confirmation_sha256",
    "source_received_at",
    "evaluation_started_at",
    "evaluation_finished_at",
    "incremental_evaluation_seconds",
    "source_update_confirmation_seconds",
    "measurement_evidence_sha256",
    "financial_authority",
    "financial_delivery",
    "automatic_order_placement",
})
_MANIFEST_KEYS = frozenset({
    "version",
    "incremental_measurements",
    "source_update_measurements",
    "manifest_sha256",
    "financial_authority",
    "financial_delivery",
    "automatic_order_placement",
})


class WeatherW7MeasurementManifestError(RuntimeError):
    def __init__(self, code: str):
        self.code = str(code)
        super().__init__(self.code)


def _canonical(value: object) -> str:
    try:
        return json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        )
    except (TypeError, ValueError):
        raise WeatherW7MeasurementManifestError("W7_MEASUREMENT_JSON_INVALID") from None


def _sha(value: object) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _finite(value: object, code: str) -> float:
    if value is None or isinstance(value, bool) or not isinstance(value, (int, float)):
        raise WeatherW7MeasurementManifestError(code)
    number = float(value)
    if not math.isfinite(number) or number < 0.0:
        raise WeatherW7MeasurementManifestError(code)
    return number


def _sha64(value: object, code: str) -> str:
    text = str(value or "").strip().lower()
    if not _SHA64_RE.fullmatch(text):
        raise WeatherW7MeasurementManifestError(code)
    return text


def _false(value: object) -> bool:
    if type(value) is not bool or value is not False:
        raise WeatherW7MeasurementManifestError("W7_MEASUREMENT_AUTHORITY_BOUNDARY_BROKEN")
    return False


def _exact_dict(value: object, keys: frozenset[str], code: str) -> dict:
    if not isinstance(value, dict) or set(value) != keys:
        raise WeatherW7MeasurementManifestError(code)
    return value


def validate_w7_source_update_latency_measurement(
    measurement: object,
) -> WeatherW7SourceUpdateLatencyMeasurement:
    if not isinstance(measurement, WeatherW7SourceUpdateLatencyMeasurement):
        raise WeatherW7MeasurementManifestError("W7_SOURCE_MEASUREMENT_TYPE_INVALID")
    if measurement.version != WEATHER_W7_SOURCE_LATENCY_VERSION:
        raise WeatherW7MeasurementManifestError("W7_SOURCE_MEASUREMENT_VERSION_MISMATCH")
    _sha64(measurement.trigger_evidence_sha256, "W7_SOURCE_TRIGGER_SHA_INVALID")
    _sha64(measurement.candidate_confirmation_sha256, "W7_SOURCE_CONFIRMATION_SHA_INVALID")
    supplied = _sha64(measurement.measurement_evidence_sha256, "W7_SOURCE_MEASUREMENT_SHA_INVALID")
    received = _finite(measurement.source_received_at, "W7_SOURCE_MEASUREMENT_TIME_INVALID")
    started = _finite(measurement.evaluation_started_at, "W7_SOURCE_MEASUREMENT_TIME_INVALID")
    finished = _finite(measurement.evaluation_finished_at, "W7_SOURCE_MEASUREMENT_TIME_INVALID")
    incremental = _finite(measurement.incremental_evaluation_seconds, "W7_SOURCE_INCREMENTAL_LATENCY_INVALID")
    source_latency = _finite(measurement.source_update_confirmation_seconds, "W7_SOURCE_LATENCY_INVALID")
    if not (received <= started <= finished):
        raise WeatherW7MeasurementManifestError("W7_SOURCE_MEASUREMENT_TIME_ORDER_INVALID")
    if abs((finished - received) - source_latency) > 1e-9:
        raise WeatherW7MeasurementManifestError("W7_SOURCE_LATENCY_VALUE_MISMATCH")
    if source_latency + 1e-9 < incremental:
        raise WeatherW7MeasurementManifestError("W7_SOURCE_CLOCK_DOMAINS_INCONSISTENT")
    if any((
        measurement.financial_authority is not False,
        measurement.financial_delivery is not False,
        measurement.automatic_order_placement is not False,
    )):
        raise WeatherW7MeasurementManifestError("W7_MEASUREMENT_AUTHORITY_BOUNDARY_BROKEN")
    if supplied != _source_sha(_source_measurement_payload(measurement)):
        raise WeatherW7MeasurementManifestError("W7_SOURCE_MEASUREMENT_DIGEST_MISMATCH")
    return measurement


@dataclass(frozen=True, slots=True)
class WeatherW7MeasurementManifest:
    version: str
    incremental_measurements: tuple[WeatherIncrementalLatencyMeasurement, ...]
    source_update_measurements: tuple[WeatherW7SourceUpdateLatencyMeasurement, ...]
    manifest_sha256: str
    financial_authority: bool = field(init=False, default=False)
    financial_delivery: bool = field(init=False, default=False)
    automatic_order_placement: bool = field(init=False, default=False)

    def as_dict(self) -> dict:
        return {
            "version": self.version,
            "incremental_measurements": [row.as_dict() for row in self.incremental_measurements],
            "source_update_measurements": [row.as_dict() for row in self.source_update_measurements],
            "manifest_sha256": self.manifest_sha256,
            "financial_authority": False,
            "financial_delivery": False,
            "automatic_order_placement": False,
        }


def _manifest_payload(manifest: WeatherW7MeasurementManifest) -> dict:
    value = manifest.as_dict()
    value.pop("manifest_sha256", None)
    return value


def validate_weather_w7_measurement_manifest(
    manifest: object,
) -> WeatherW7MeasurementManifest:
    if not isinstance(manifest, WeatherW7MeasurementManifest):
        raise WeatherW7MeasurementManifestError("W7_MEASUREMENT_MANIFEST_TYPE_INVALID")
    if manifest.version != WEATHER_W7_MEASUREMENT_MANIFEST_VERSION:
        raise WeatherW7MeasurementManifestError("W7_MEASUREMENT_MANIFEST_VERSION_MISMATCH")
    if not isinstance(manifest.incremental_measurements, tuple) or not isinstance(manifest.source_update_measurements, tuple):
        raise WeatherW7MeasurementManifestError("W7_MEASUREMENT_MANIFEST_ROWS_INVALID")
    incremental_ids: set[str] = set()
    for row in manifest.incremental_measurements:
        try:
            validated = validate_weather_incremental_latency_measurement(row)
        except WeatherIncrementalError as exc:
            raise WeatherW7MeasurementManifestError(f"W7_INCREMENTAL_MEASUREMENT_INVALID:{exc.code}") from exc
        sha = validated.measurement_evidence_sha256
        if sha in incremental_ids:
            raise WeatherW7MeasurementManifestError("W7_INCREMENTAL_MEASUREMENT_DUPLICATE")
        incremental_ids.add(sha)
    source_ids: set[str] = set()
    for row in manifest.source_update_measurements:
        validated = validate_w7_source_update_latency_measurement(row)
        sha = validated.measurement_evidence_sha256
        if sha in source_ids:
            raise WeatherW7MeasurementManifestError("W7_SOURCE_MEASUREMENT_DUPLICATE")
        source_ids.add(sha)
    supplied = _sha64(manifest.manifest_sha256, "W7_MEASUREMENT_MANIFEST_SHA_INVALID")
    if any((
        manifest.financial_authority is not False,
        manifest.financial_delivery is not False,
        manifest.automatic_order_placement is not False,
    )):
        raise WeatherW7MeasurementManifestError("W7_MEASUREMENT_AUTHORITY_BOUNDARY_BROKEN")
    if supplied != _sha(_manifest_payload(manifest)):
        raise WeatherW7MeasurementManifestError("W7_MEASUREMENT_MANIFEST_DIGEST_MISMATCH")
    return manifest


def build_weather_w7_measurement_manifest(
    *,
    incremental_measurements: tuple[WeatherIncrementalLatencyMeasurement, ...],
    source_update_measurements: tuple[WeatherW7SourceUpdateLatencyMeasurement, ...] = (),
) -> WeatherW7MeasurementManifest:
    if not isinstance(incremental_measurements, tuple) or not isinstance(source_update_measurements, tuple):
        raise WeatherW7MeasurementManifestError("W7_MEASUREMENT_MANIFEST_ROWS_INVALID")
    ordered_incremental = tuple(sorted(
        incremental_measurements,
        key=lambda row: (row.evaluation_finished_at, row.measurement_evidence_sha256),
    ))
    ordered_source = tuple(sorted(
        source_update_measurements,
        key=lambda row: (row.evaluation_finished_at, row.measurement_evidence_sha256),
    ))
    provisional = WeatherW7MeasurementManifest(
        version=WEATHER_W7_MEASUREMENT_MANIFEST_VERSION,
        incremental_measurements=ordered_incremental,
        source_update_measurements=ordered_source,
        manifest_sha256="0" * 64,
    )
    final = WeatherW7MeasurementManifest(
        version=provisional.version,
        incremental_measurements=provisional.incremental_measurements,
        source_update_measurements=provisional.source_update_measurements,
        manifest_sha256=_sha(_manifest_payload(provisional)),
    )
    return validate_weather_w7_measurement_manifest(final)


def _incremental_from_dict(raw: object) -> WeatherIncrementalLatencyMeasurement:
    row = _exact_dict(raw, _INCREMENTAL_KEYS, "W7_INCREMENTAL_MEASUREMENT_SCHEMA_INVALID")
    _false(row["financial_authority"])
    _false(row["financial_delivery"])
    _false(row["automatic_order_placement"])
    measurement = WeatherIncrementalLatencyMeasurement(
        version=str(row["version"]),
        receipt_evidence_sha256=str(row["receipt_evidence_sha256"]),
        evaluation_started_at=_finite(row["evaluation_started_at"], "W7_INCREMENTAL_MEASUREMENT_NUMBER_INVALID"),
        evaluation_finished_at=_finite(row["evaluation_finished_at"], "W7_INCREMENTAL_MEASUREMENT_NUMBER_INVALID"),
        incremental_evaluation_seconds=_finite(row["incremental_evaluation_seconds"], "W7_INCREMENTAL_MEASUREMENT_NUMBER_INVALID"),
        measurement_evidence_sha256=str(row["measurement_evidence_sha256"]),
    )
    try:
        return validate_weather_incremental_latency_measurement(measurement)
    except WeatherIncrementalError as exc:
        raise WeatherW7MeasurementManifestError(f"W7_INCREMENTAL_MEASUREMENT_INVALID:{exc.code}") from exc


def _source_from_dict(raw: object) -> WeatherW7SourceUpdateLatencyMeasurement:
    row = _exact_dict(raw, _SOURCE_KEYS, "W7_SOURCE_MEASUREMENT_SCHEMA_INVALID")
    _false(row["financial_authority"])
    _false(row["financial_delivery"])
    _false(row["automatic_order_placement"])
    measurement = WeatherW7SourceUpdateLatencyMeasurement(
        version=str(row["version"]),
        trigger_evidence_sha256=str(row["trigger_evidence_sha256"]),
        candidate_confirmation_sha256=str(row["candidate_confirmation_sha256"]),
        source_received_at=_finite(row["source_received_at"], "W7_SOURCE_MEASUREMENT_NUMBER_INVALID"),
        evaluation_started_at=_finite(row["evaluation_started_at"], "W7_SOURCE_MEASUREMENT_NUMBER_INVALID"),
        evaluation_finished_at=_finite(row["evaluation_finished_at"], "W7_SOURCE_MEASUREMENT_NUMBER_INVALID"),
        incremental_evaluation_seconds=_finite(row["incremental_evaluation_seconds"], "W7_SOURCE_MEASUREMENT_NUMBER_INVALID"),
        source_update_confirmation_seconds=_finite(row["source_update_confirmation_seconds"], "W7_SOURCE_MEASUREMENT_NUMBER_INVALID"),
        measurement_evidence_sha256=str(row["measurement_evidence_sha256"]),
    )
    return validate_w7_source_update_latency_measurement(measurement)


def weather_w7_measurement_manifest_from_dict(raw: object) -> WeatherW7MeasurementManifest:
    value = _exact_dict(raw, _MANIFEST_KEYS, "W7_MEASUREMENT_MANIFEST_SCHEMA_INVALID")
    _false(value["financial_authority"])
    _false(value["financial_delivery"])
    _false(value["automatic_order_placement"])
    incremental_raw = value["incremental_measurements"]
    source_raw = value["source_update_measurements"]
    if not isinstance(incremental_raw, list) or not isinstance(source_raw, list):
        raise WeatherW7MeasurementManifestError("W7_MEASUREMENT_MANIFEST_ROWS_INVALID")
    manifest = WeatherW7MeasurementManifest(
        version=str(value["version"]),
        incremental_measurements=tuple(_incremental_from_dict(row) for row in incremental_raw),
        source_update_measurements=tuple(_source_from_dict(row) for row in source_raw),
        manifest_sha256=str(value["manifest_sha256"]),
    )
    return validate_weather_w7_measurement_manifest(manifest)
