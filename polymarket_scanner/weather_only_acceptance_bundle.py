from __future__ import annotations

"""Final verifiable bundle for weather W7 silent-shadow acceptance.

The existing W7 evidence envelope already binds every latency number to an embedded
measurement record.  This outer bundle adds the pieces that cannot honestly be
represented by hand-written zero counters: read-only database containment, scanner
process continuity, and the exact read-only weather runtime/CLOB code surface.

It also binds each latency receipt to the 30-second sample interval that contains it,
so a valid old measurement cannot be replayed into a later sample merely by changing
which SHA reference the sample carries.

This module evaluates evidence only.  It cannot start services, send Telegram, place
orders, fetch market/weather data, or grant financial authority.
"""

import copy
import hashlib
import json
import math
import re
from dataclasses import asdict, dataclass, field, replace

from .weather_only_acceptance import WeatherW7AcceptanceReport, WeatherW7Policy
from .weather_only_acceptance_containment import (
    WEATHER_W7_CONTAINMENT_VERSION,
    WeatherW7ContainmentError,
    WeatherW7ContainmentManifest,
    WeatherW7DatabaseSnapshot,
    WeatherW7ProcessIdentity,
    WeatherW7ReadOnlySurfaceAttestation,
    derive_weather_w7_containment_counters,
    validate_weather_w7_containment_manifest,
    weather_w7_containment_reasons,
)
from .weather_only_acceptance_evidence import (
    WeatherW7EvidenceEnvelope,
    WeatherW7EvidenceError,
    dump_weather_w7_evidence_json,
    load_weather_w7_evidence_json,
    validate_weather_w7_evidence_envelope,
)


WEATHER_W7_BUNDLE_VERSION = "weather_w7_bundle_v1_measurements_containment_interval_binding"
_SHA64_RE = re.compile(r"^[0-9a-f]{64}$")

_BUNDLE_KEYS = frozenset({
    "version",
    "w7_evidence",
    "containment_manifest",
    "bundle_sha256",
    "financial_authority",
    "financial_delivery",
    "detector_promotion_authority",
    "automatic_order_placement",
})
_DB_KEYS = frozenset({
    "version", "captured_at", "database_identity_sha256", "telegram_outbox_rows",
    "signals_rows", "manual_trades_rows", "telegram_outbox_state_sha256",
    "signals_state_sha256", "manual_trades_state_sha256", "schema_evidence_sha256",
    "evidence_sha256", "financial_authority",
})
_PROCESS_KEYS = frozenset({
    "version", "process_id", "boot_id_sha256", "start_time_ticks", "cmdline_sha256",
    "evidence_sha256", "financial_authority",
})
_SURFACE_KEYS = frozenset({
    "version", "clob_methods", "runtime_methods", "clob_source_sha256",
    "runtime_source_sha256", "evidence_sha256", "order_api_exposed",
    "financial_delivery_api_exposed", "financial_authority",
})
_CONTAINMENT_KEYS = frozenset({
    "version", "before_database", "after_database", "before_process", "after_process",
    "read_only_surface", "evidence_sha256", "financial_authority", "financial_delivery",
    "automatic_order_placement",
})


class WeatherW7BundleError(RuntimeError):
    def __init__(self, code: str):
        self.code = str(code)
        super().__init__(self.code)


def _canonical(value: object) -> str:
    try:
        return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False)
    except (TypeError, ValueError):
        raise WeatherW7BundleError("W7_BUNDLE_JSON_INVALID") from None


def _sha(value: object) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _exact(value: object, keys: frozenset[str], code: str) -> dict:
    if not isinstance(value, dict) or set(value) != keys:
        raise WeatherW7BundleError(code)
    return value


def _false(value: object, code: str = "W7_BUNDLE_AUTHORITY_BOUNDARY_BROKEN") -> bool:
    if type(value) is not bool or value is not False:
        raise WeatherW7BundleError(code)
    return False


def _text(value: object, code: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise WeatherW7BundleError(code)
    return value.strip()


def _sha64(value: object, code: str) -> str:
    text = _text(value, code).lower()
    if not _SHA64_RE.fullmatch(text):
        raise WeatherW7BundleError(code)
    return text


def _number(value: object, code: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise WeatherW7BundleError(code)
    out = float(value)
    if not math.isfinite(out) or out < 0.0:
        raise WeatherW7BundleError(code)
    return out


def _integer(value: object, code: str, *, positive: bool = False) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < (1 if positive else 0):
        raise WeatherW7BundleError(code)
    return value


def _db_from_dict(raw: object) -> WeatherW7DatabaseSnapshot:
    row = _exact(raw, _DB_KEYS, "W7_BUNDLE_DB_SCHEMA_INVALID")
    _false(row["financial_authority"])
    obj = WeatherW7DatabaseSnapshot(
        version=_text(row["version"], "W7_BUNDLE_DB_VERSION_INVALID"),
        captured_at=_number(row["captured_at"], "W7_BUNDLE_DB_TIME_INVALID"),
        database_identity_sha256=_sha64(row["database_identity_sha256"], "W7_BUNDLE_DB_SHA_INVALID"),
        telegram_outbox_rows=_integer(row["telegram_outbox_rows"], "W7_BUNDLE_DB_COUNT_INVALID"),
        signals_rows=_integer(row["signals_rows"], "W7_BUNDLE_DB_COUNT_INVALID"),
        manual_trades_rows=_integer(row["manual_trades_rows"], "W7_BUNDLE_DB_COUNT_INVALID"),
        telegram_outbox_state_sha256=_sha64(row["telegram_outbox_state_sha256"], "W7_BUNDLE_DB_SHA_INVALID"),
        signals_state_sha256=_sha64(row["signals_state_sha256"], "W7_BUNDLE_DB_SHA_INVALID"),
        manual_trades_state_sha256=_sha64(row["manual_trades_state_sha256"], "W7_BUNDLE_DB_SHA_INVALID"),
        schema_evidence_sha256=_sha64(row["schema_evidence_sha256"], "W7_BUNDLE_DB_SHA_INVALID"),
        evidence_sha256=_sha64(row["evidence_sha256"], "W7_BUNDLE_DB_SHA_INVALID"),
    )
    return obj


def _process_from_dict(raw: object) -> WeatherW7ProcessIdentity:
    row = _exact(raw, _PROCESS_KEYS, "W7_BUNDLE_PROCESS_SCHEMA_INVALID")
    _false(row["financial_authority"])
    return WeatherW7ProcessIdentity(
        version=_text(row["version"], "W7_BUNDLE_PROCESS_VERSION_INVALID"),
        process_id=_integer(row["process_id"], "W7_BUNDLE_PROCESS_ID_INVALID", positive=True),
        boot_id_sha256=_sha64(row["boot_id_sha256"], "W7_BUNDLE_PROCESS_SHA_INVALID"),
        start_time_ticks=_integer(row["start_time_ticks"], "W7_BUNDLE_PROCESS_START_INVALID", positive=True),
        cmdline_sha256=_sha64(row["cmdline_sha256"], "W7_BUNDLE_PROCESS_SHA_INVALID"),
        evidence_sha256=_sha64(row["evidence_sha256"], "W7_BUNDLE_PROCESS_SHA_INVALID"),
    )


def _surface_from_dict(raw: object) -> WeatherW7ReadOnlySurfaceAttestation:
    row = _exact(raw, _SURFACE_KEYS, "W7_BUNDLE_SURFACE_SCHEMA_INVALID")
    _false(row["order_api_exposed"])
    _false(row["financial_delivery_api_exposed"])
    _false(row["financial_authority"])
    clob = row["clob_methods"]
    runtime = row["runtime_methods"]
    if not isinstance(clob, list) or not all(isinstance(value, str) and value for value in clob):
        raise WeatherW7BundleError("W7_BUNDLE_SURFACE_METHODS_INVALID")
    if not isinstance(runtime, list) or not all(isinstance(value, str) and value for value in runtime):
        raise WeatherW7BundleError("W7_BUNDLE_SURFACE_METHODS_INVALID")
    return WeatherW7ReadOnlySurfaceAttestation(
        version=_text(row["version"], "W7_BUNDLE_SURFACE_VERSION_INVALID"),
        clob_methods=tuple(clob),
        runtime_methods=tuple(runtime),
        clob_source_sha256=_sha64(row["clob_source_sha256"], "W7_BUNDLE_SURFACE_SHA_INVALID"),
        runtime_source_sha256=_sha64(row["runtime_source_sha256"], "W7_BUNDLE_SURFACE_SHA_INVALID"),
        evidence_sha256=_sha64(row["evidence_sha256"], "W7_BUNDLE_SURFACE_SHA_INVALID"),
    )


def _containment_from_dict(raw: object) -> WeatherW7ContainmentManifest:
    row = _exact(raw, _CONTAINMENT_KEYS, "W7_BUNDLE_CONTAINMENT_SCHEMA_INVALID")
    _false(row["financial_authority"])
    _false(row["financial_delivery"])
    _false(row["automatic_order_placement"])
    obj = WeatherW7ContainmentManifest(
        version=_text(row["version"], "W7_BUNDLE_CONTAINMENT_VERSION_INVALID"),
        before_database=_db_from_dict(row["before_database"]),
        after_database=_db_from_dict(row["after_database"]),
        before_process=_process_from_dict(row["before_process"]),
        after_process=_process_from_dict(row["after_process"]),
        read_only_surface=_surface_from_dict(row["read_only_surface"]),
        evidence_sha256=_sha64(row["evidence_sha256"], "W7_BUNDLE_CONTAINMENT_SHA_INVALID"),
    )
    try:
        return validate_weather_w7_containment_manifest(obj)
    except WeatherW7ContainmentError as exc:
        raise WeatherW7BundleError(f"W7_BUNDLE_CONTAINMENT_INVALID:{exc.code}") from exc


def _validate_interval_binding(envelope: WeatherW7EvidenceEnvelope) -> None:
    samples = envelope.run_evidence.samples
    if not samples:
        return
    times = [sample.observed_at for sample in samples]
    if any(current <= previous for previous, current in zip(times, times[1:])):
        raise WeatherW7BundleError("W7_BUNDLE_SAMPLE_ORDER_INVALID")

    inc_by_sha = {
        row.measurement_evidence_sha256: row
        for row in envelope.measurement_manifest.incremental_measurements
    }
    source_by_sha = {
        row.measurement_evidence_sha256: row
        for row in envelope.measurement_manifest.source_update_measurements
    }
    max_gap = WeatherW7Policy().max_sample_gap_seconds
    for index, sample in enumerate(samples):
        lower = times[index - 1] if index else max(0.0, sample.observed_at - max_gap)
        inc = inc_by_sha.get(sample.incremental_evaluation_evidence_sha256)
        if inc is None:
            raise WeatherW7BundleError(f"W7_BUNDLE_INCREMENTAL_MEASUREMENT_MISSING:{index}")
        if not (lower < inc.evaluation_finished_at <= sample.observed_at + 1e-9):
            raise WeatherW7BundleError(f"W7_BUNDLE_INCREMENTAL_MEASUREMENT_OUTSIDE_SAMPLE_INTERVAL:{index}")
        if sample.source_update_evidence_sha256 is not None:
            src = source_by_sha.get(sample.source_update_evidence_sha256)
            if src is None:
                raise WeatherW7BundleError(f"W7_BUNDLE_SOURCE_MEASUREMENT_MISSING:{index}")
            if not (lower < src.evaluation_finished_at <= sample.observed_at + 1e-9):
                raise WeatherW7BundleError(f"W7_BUNDLE_SOURCE_MEASUREMENT_OUTSIDE_SAMPLE_INTERVAL:{index}")


def _validate_containment_links(
    envelope: WeatherW7EvidenceEnvelope,
    containment: WeatherW7ContainmentManifest,
) -> tuple[str, ...]:
    try:
        manifest = validate_weather_w7_containment_manifest(containment)
        counters = derive_weather_w7_containment_counters(manifest)
    except WeatherW7ContainmentError as exc:
        raise WeatherW7BundleError(f"W7_BUNDLE_CONTAINMENT_INVALID:{exc.code}") from exc
    run = envelope.run_evidence
    for name, expected in counters.items():
        if getattr(run, name) != expected:
            raise WeatherW7BundleError(f"W7_BUNDLE_CONTAINMENT_COUNTER_MISMATCH:{name}")
    if run.samples:
        first = run.samples[0].observed_at
        last = run.samples[-1].observed_at
        if manifest.before_database.captured_at > first + 1e-9:
            raise WeatherW7BundleError("W7_BUNDLE_CONTAINMENT_STARTED_AFTER_SAMPLES")
        if manifest.after_database.captured_at + 1e-9 < last:
            raise WeatherW7BundleError("W7_BUNDLE_CONTAINMENT_ENDED_BEFORE_SAMPLES")
    return weather_w7_containment_reasons(manifest)


@dataclass(frozen=True, slots=True)
class WeatherW7AcceptanceBundle:
    version: str
    w7_evidence: WeatherW7EvidenceEnvelope
    containment_manifest: WeatherW7ContainmentManifest
    bundle_sha256: str
    financial_authority: bool = field(init=False, default=False)
    financial_delivery: bool = field(init=False, default=False)
    detector_promotion_authority: bool = field(init=False, default=False)
    automatic_order_placement: bool = field(init=False, default=False)

    def as_dict(self) -> dict:
        return {
            "version": self.version,
            "w7_evidence": self.w7_evidence.as_dict(),
            "containment_manifest": self.containment_manifest.as_dict(),
            "bundle_sha256": self.bundle_sha256,
            "financial_authority": False,
            "financial_delivery": False,
            "detector_promotion_authority": False,
            "automatic_order_placement": False,
        }


def _bundle_payload(row: WeatherW7AcceptanceBundle) -> dict:
    value = row.as_dict()
    value.pop("bundle_sha256", None)
    return value


def validate_weather_w7_acceptance_bundle(
    row: object,
    *,
    expected_release_sha: str | None = None,
) -> WeatherW7AcceptanceReport:
    if not isinstance(row, WeatherW7AcceptanceBundle) or row.version != WEATHER_W7_BUNDLE_VERSION:
        raise WeatherW7BundleError("W7_BUNDLE_TYPE_OR_VERSION_INVALID")
    if any((
        row.financial_authority is not False,
        row.financial_delivery is not False,
        row.detector_promotion_authority is not False,
        row.automatic_order_placement is not False,
    )):
        raise WeatherW7BundleError("W7_BUNDLE_AUTHORITY_BOUNDARY_BROKEN")
    try:
        base_report = validate_weather_w7_evidence_envelope(
            row.w7_evidence,
            expected_release_sha=expected_release_sha,
        )
    except WeatherW7EvidenceError as exc:
        raise WeatherW7BundleError(f"W7_BUNDLE_EVIDENCE_INVALID:{exc.code}") from exc
    _validate_interval_binding(row.w7_evidence)
    containment_reasons = _validate_containment_links(row.w7_evidence, row.containment_manifest)
    supplied = _sha64(row.bundle_sha256, "W7_BUNDLE_SHA_INVALID")
    if supplied != _sha(_bundle_payload(row)):
        raise WeatherW7BundleError("W7_BUNDLE_DIGEST_MISMATCH")
    if not containment_reasons:
        return base_report
    combined = tuple(dict.fromkeys((*base_report.reasons, *containment_reasons)))
    return replace(base_report, passed=False, reasons=combined)


def build_weather_w7_acceptance_bundle(
    *,
    w7_evidence: WeatherW7EvidenceEnvelope,
    containment_manifest: WeatherW7ContainmentManifest,
) -> WeatherW7AcceptanceBundle:
    try:
        validate_weather_w7_evidence_envelope(w7_evidence)
        validated_containment = validate_weather_w7_containment_manifest(containment_manifest)
    except (WeatherW7EvidenceError, WeatherW7ContainmentError) as exc:
        code = getattr(exc, "code", type(exc).__name__)
        raise WeatherW7BundleError(f"W7_BUNDLE_COMPONENT_INVALID:{code}") from exc
    shell = WeatherW7AcceptanceBundle(
        version=WEATHER_W7_BUNDLE_VERSION,
        w7_evidence=w7_evidence,
        containment_manifest=validated_containment,
        bundle_sha256="0" * 64,
    )
    final = WeatherW7AcceptanceBundle(
        version=shell.version,
        w7_evidence=shell.w7_evidence,
        containment_manifest=shell.containment_manifest,
        bundle_sha256=_sha(_bundle_payload(shell)),
    )
    validate_weather_w7_acceptance_bundle(final)
    return final


def dump_weather_w7_acceptance_bundle_json(row: WeatherW7AcceptanceBundle) -> str:
    validate_weather_w7_acceptance_bundle(row)
    return json.dumps(row.as_dict(), sort_keys=True, indent=2, allow_nan=False)


def load_weather_w7_acceptance_bundle_json(
    raw_json: str,
    *,
    expected_release_sha: str | None = None,
) -> tuple[WeatherW7AcceptanceBundle, WeatherW7AcceptanceReport]:
    if not isinstance(raw_json, str) or not raw_json.strip():
        raise WeatherW7BundleError("W7_BUNDLE_JSON_EMPTY")
    try:
        raw = json.loads(raw_json)
    except json.JSONDecodeError:
        raise WeatherW7BundleError("W7_BUNDLE_JSON_INVALID") from None
    value = _exact(raw, _BUNDLE_KEYS, "W7_BUNDLE_SCHEMA_INVALID")
    for key in (
        "financial_authority", "financial_delivery", "detector_promotion_authority",
        "automatic_order_placement",
    ):
        _false(value[key])
    try:
        envelope, _ = load_weather_w7_evidence_json(
            json.dumps(value["w7_evidence"], sort_keys=True, allow_nan=False),
            expected_release_sha=expected_release_sha,
        )
    except WeatherW7EvidenceError as exc:
        raise WeatherW7BundleError(f"W7_BUNDLE_EVIDENCE_INVALID:{exc.code}") from exc
    containment = _containment_from_dict(value["containment_manifest"])
    bundle = WeatherW7AcceptanceBundle(
        version=_text(value["version"], "W7_BUNDLE_VERSION_INVALID"),
        w7_evidence=envelope,
        containment_manifest=containment,
        bundle_sha256=_sha64(value["bundle_sha256"], "W7_BUNDLE_SHA_INVALID"),
    )
    report = validate_weather_w7_acceptance_bundle(bundle, expected_release_sha=expected_release_sha)
    return bundle, report
