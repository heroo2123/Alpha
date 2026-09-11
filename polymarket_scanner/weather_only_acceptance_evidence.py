from __future__ import annotations

"""Canonical, tamper-evident evidence envelope for weather W7 acceptance.

This module does not start services, read credentials, contact Polymarket, mutate a
runtime database, or grant financial authority.  It exists so the future 45-minute
silent-shadow run cannot be accepted from hand-built/mixed-version Python objects.

The envelope binds:
- the exact release SHA;
- the exact weather runtime version;
- the frozen W7 policy id + policy SHA;
- every sample and containment counter;
- a canonical SHA-256 over the complete serialized evidence.

Loading is deliberately strict: unknown/missing keys, bool-as-int coercion, NaN/Inf,
policy/runtime drift, release mismatch and digest tampering all fail closed before the
existing acceptance evaluator is called.
"""

import hashlib
import json
import math
import re
from dataclasses import asdict, dataclass, field

from .weather_only_acceptance import (
    WEATHER_W7_ACCEPTANCE_VERSION,
    WEATHER_W7_POLICY_ID,
    WeatherW7AcceptanceReport,
    WeatherW7Policy,
    WeatherW7RunEvidence,
    WeatherW7Sample,
    evaluate_weather_w7_acceptance,
)
from .weather_only_runtime import WEATHER_SHADOW_RUNTIME_VERSION


WEATHER_W7_EVIDENCE_VERSION = "weather_w7_evidence_v1_exact_schema_release_runtime_policy_digest"
_SHA40_RE = re.compile(r"^[0-9a-f]{40}$")
_SHA64_RE = re.compile(r"^[0-9a-f]{64}$")

_SAMPLE_KEYS = frozenset({
    "observed_at",
    "cycle_ok",
    "process_rss_bytes",
    "swap_used_bytes",
    "host_mem_available_bytes",
    "incremental_evaluation_seconds",
    "source_update_confirmation_seconds",
    "weather_event_count",
    "non_weather_materialized_count",
    "exact_clob_required_for_candidates",
    "financial_authority",
    "financial_delivery",
    "automatic_order_placement",
})
_RUN_KEYS = frozenset({
    "release_sha",
    "samples",
    "telegram_outbox_before",
    "telegram_outbox_after",
    "detector_promotions",
    "order_attempts",
    "actual_orders_placed",
    "actual_fills_recorded",
    "service_restart_count",
})
_ENVELOPE_KEYS = frozenset({
    "version",
    "acceptance_version",
    "runtime_version",
    "policy_id",
    "policy_sha256",
    "release_sha",
    "created_at",
    "run_evidence",
    "evidence_sha256",
    "financial_authority",
    "financial_delivery",
    "detector_promotion_authority",
    "automatic_order_placement",
})


class WeatherW7EvidenceError(RuntimeError):
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
        raise WeatherW7EvidenceError("W7_EVIDENCE_JSON_INVALID") from None


def _sha(value: object) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _exact_keys(value: object, expected: frozenset[str], code: str) -> dict:
    if not isinstance(value, dict) or set(value) != expected:
        raise WeatherW7EvidenceError(code)
    return value


def _strict_float(value: object, *, nullable: bool = False) -> float | None:
    if nullable and value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise WeatherW7EvidenceError("W7_EVIDENCE_NUMBER_INVALID")
    number = float(value)
    if not math.isfinite(number) or number < 0.0:
        raise WeatherW7EvidenceError("W7_EVIDENCE_NUMBER_INVALID")
    return number


def _strict_int(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise WeatherW7EvidenceError("W7_EVIDENCE_INTEGER_INVALID")
    return value


def _strict_bool(value: object) -> bool:
    if type(value) is not bool:
        raise WeatherW7EvidenceError("W7_EVIDENCE_BOOLEAN_INVALID")
    return value


def _strict_text(value: object, code: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise WeatherW7EvidenceError(code)
    return value.strip()


def _sample_from_dict(raw: object) -> WeatherW7Sample:
    row = _exact_keys(raw, _SAMPLE_KEYS, "W7_EVIDENCE_SAMPLE_SCHEMA_INVALID")
    return WeatherW7Sample(
        observed_at=float(_strict_float(row["observed_at"])),
        cycle_ok=_strict_bool(row["cycle_ok"]),
        process_rss_bytes=_strict_int(row["process_rss_bytes"]),
        swap_used_bytes=_strict_int(row["swap_used_bytes"]),
        host_mem_available_bytes=_strict_int(row["host_mem_available_bytes"]),
        incremental_evaluation_seconds=float(_strict_float(row["incremental_evaluation_seconds"])),
        source_update_confirmation_seconds=_strict_float(
            row["source_update_confirmation_seconds"], nullable=True
        ),
        weather_event_count=_strict_int(row["weather_event_count"]),
        non_weather_materialized_count=_strict_int(row["non_weather_materialized_count"]),
        exact_clob_required_for_candidates=_strict_bool(row["exact_clob_required_for_candidates"]),
        financial_authority=_strict_bool(row["financial_authority"]),
        financial_delivery=_strict_bool(row["financial_delivery"]),
        automatic_order_placement=_strict_bool(row["automatic_order_placement"]),
    )


def _run_from_dict(raw: object) -> WeatherW7RunEvidence:
    value = _exact_keys(raw, _RUN_KEYS, "W7_EVIDENCE_RUN_SCHEMA_INVALID")
    release_sha = _strict_text(value["release_sha"], "W7_EVIDENCE_RELEASE_SHA_INVALID").lower()
    if not _SHA40_RE.fullmatch(release_sha):
        raise WeatherW7EvidenceError("W7_EVIDENCE_RELEASE_SHA_INVALID")
    samples_raw = value["samples"]
    if not isinstance(samples_raw, list):
        raise WeatherW7EvidenceError("W7_EVIDENCE_SAMPLES_INVALID")
    samples = tuple(_sample_from_dict(row) for row in samples_raw)
    return WeatherW7RunEvidence(
        release_sha=release_sha,
        samples=samples,
        telegram_outbox_before=_strict_int(value["telegram_outbox_before"]),
        telegram_outbox_after=_strict_int(value["telegram_outbox_after"]),
        detector_promotions=_strict_int(value["detector_promotions"]),
        order_attempts=_strict_int(value["order_attempts"]),
        actual_orders_placed=_strict_int(value["actual_orders_placed"]),
        actual_fills_recorded=_strict_int(value["actual_fills_recorded"]),
        service_restart_count=_strict_int(value["service_restart_count"]),
    )


def _run_as_serializable(evidence: WeatherW7RunEvidence) -> dict:
    if not isinstance(evidence, WeatherW7RunEvidence):
        raise WeatherW7EvidenceError("W7_EVIDENCE_RUN_TYPE_INVALID")
    value = asdict(evidence)
    value["samples"] = [asdict(row) for row in evidence.samples]
    return value


@dataclass(frozen=True, slots=True)
class WeatherW7EvidenceEnvelope:
    version: str
    acceptance_version: str
    runtime_version: str
    policy_id: str
    policy_sha256: str
    release_sha: str
    created_at: float
    run_evidence: WeatherW7RunEvidence
    evidence_sha256: str
    financial_authority: bool = field(init=False, default=False)
    financial_delivery: bool = field(init=False, default=False)
    detector_promotion_authority: bool = field(init=False, default=False)
    automatic_order_placement: bool = field(init=False, default=False)

    def as_dict(self) -> dict:
        value = asdict(self)
        value["run_evidence"] = _run_as_serializable(self.run_evidence)
        return value


def _payload_without_digest(envelope: WeatherW7EvidenceEnvelope) -> dict:
    payload = envelope.as_dict()
    payload.pop("evidence_sha256", None)
    return payload


def validate_weather_w7_evidence_envelope(
    envelope: WeatherW7EvidenceEnvelope,
    *,
    expected_release_sha: str | None = None,
) -> WeatherW7AcceptanceReport:
    if not isinstance(envelope, WeatherW7EvidenceEnvelope):
        raise WeatherW7EvidenceError("W7_EVIDENCE_ENVELOPE_TYPE_INVALID")
    if envelope.version != WEATHER_W7_EVIDENCE_VERSION:
        raise WeatherW7EvidenceError("W7_EVIDENCE_VERSION_MISMATCH")
    if envelope.acceptance_version != WEATHER_W7_ACCEPTANCE_VERSION:
        raise WeatherW7EvidenceError("W7_EVIDENCE_ACCEPTANCE_VERSION_MISMATCH")
    if envelope.runtime_version != WEATHER_SHADOW_RUNTIME_VERSION:
        raise WeatherW7EvidenceError("W7_EVIDENCE_RUNTIME_VERSION_MISMATCH")

    frozen = WeatherW7Policy()
    if envelope.policy_id != WEATHER_W7_POLICY_ID or envelope.policy_id != frozen.policy_id:
        raise WeatherW7EvidenceError("W7_EVIDENCE_POLICY_ID_MISMATCH")
    if envelope.policy_sha256 != frozen.policy_sha256:
        raise WeatherW7EvidenceError("W7_EVIDENCE_POLICY_SHA_MISMATCH")

    release_sha = str(envelope.release_sha or "").lower()
    if not _SHA40_RE.fullmatch(release_sha):
        raise WeatherW7EvidenceError("W7_EVIDENCE_RELEASE_SHA_INVALID")
    if envelope.run_evidence.release_sha.lower() != release_sha:
        raise WeatherW7EvidenceError("W7_EVIDENCE_RUN_RELEASE_MISMATCH")
    if expected_release_sha is not None:
        expected = str(expected_release_sha or "").lower()
        if not _SHA40_RE.fullmatch(expected) or expected != release_sha:
            raise WeatherW7EvidenceError("W7_EVIDENCE_EXPECTED_RELEASE_MISMATCH")

    created = _strict_float(envelope.created_at)
    if envelope.run_evidence.samples:
        latest = max(row.observed_at for row in envelope.run_evidence.samples)
        if float(created) + 1e-9 < latest:
            raise WeatherW7EvidenceError("W7_EVIDENCE_CREATED_BEFORE_LAST_SAMPLE")

    if any((
        envelope.financial_authority is not False,
        envelope.financial_delivery is not False,
        envelope.detector_promotion_authority is not False,
        envelope.automatic_order_placement is not False,
    )):
        raise WeatherW7EvidenceError("W7_EVIDENCE_AUTHORITY_BOUNDARY_BROKEN")

    digest = str(envelope.evidence_sha256 or "").lower()
    if not _SHA64_RE.fullmatch(digest):
        raise WeatherW7EvidenceError("W7_EVIDENCE_SHA_INVALID")
    if digest != _sha(_payload_without_digest(envelope)):
        raise WeatherW7EvidenceError("W7_EVIDENCE_SHA_MISMATCH")

    return evaluate_weather_w7_acceptance(envelope.run_evidence, policy=frozen)


def build_weather_w7_evidence_envelope(
    evidence: WeatherW7RunEvidence,
    *,
    created_at: float,
    runtime_version: str = WEATHER_SHADOW_RUNTIME_VERSION,
) -> WeatherW7EvidenceEnvelope:
    if not isinstance(evidence, WeatherW7RunEvidence):
        raise WeatherW7EvidenceError("W7_EVIDENCE_RUN_TYPE_INVALID")
    frozen = WeatherW7Policy()
    provisional = WeatherW7EvidenceEnvelope(
        version=WEATHER_W7_EVIDENCE_VERSION,
        acceptance_version=WEATHER_W7_ACCEPTANCE_VERSION,
        runtime_version=str(runtime_version),
        policy_id=frozen.policy_id,
        policy_sha256=frozen.policy_sha256,
        release_sha=evidence.release_sha.lower(),
        created_at=float(_strict_float(created_at)),
        run_evidence=evidence,
        evidence_sha256="0" * 64,
    )
    final = WeatherW7EvidenceEnvelope(
        version=provisional.version,
        acceptance_version=provisional.acceptance_version,
        runtime_version=provisional.runtime_version,
        policy_id=provisional.policy_id,
        policy_sha256=provisional.policy_sha256,
        release_sha=provisional.release_sha,
        created_at=provisional.created_at,
        run_evidence=provisional.run_evidence,
        evidence_sha256=_sha(_payload_without_digest(provisional)),
    )
    validate_weather_w7_evidence_envelope(final)
    return final


def load_weather_w7_evidence_json(
    raw_json: str,
    *,
    expected_release_sha: str | None = None,
) -> tuple[WeatherW7EvidenceEnvelope, WeatherW7AcceptanceReport]:
    if not isinstance(raw_json, str) or not raw_json.strip():
        raise WeatherW7EvidenceError("W7_EVIDENCE_JSON_EMPTY")
    try:
        raw = json.loads(raw_json)
    except json.JSONDecodeError:
        raise WeatherW7EvidenceError("W7_EVIDENCE_JSON_INVALID") from None
    value = _exact_keys(raw, _ENVELOPE_KEYS, "W7_EVIDENCE_ENVELOPE_SCHEMA_INVALID")

    version = _strict_text(value["version"], "W7_EVIDENCE_VERSION_INVALID")
    acceptance_version = _strict_text(
        value["acceptance_version"], "W7_EVIDENCE_ACCEPTANCE_VERSION_INVALID"
    )
    runtime_version = _strict_text(value["runtime_version"], "W7_EVIDENCE_RUNTIME_VERSION_INVALID")
    policy_id = _strict_text(value["policy_id"], "W7_EVIDENCE_POLICY_ID_INVALID")
    policy_sha = _strict_text(value["policy_sha256"], "W7_EVIDENCE_POLICY_SHA_INVALID").lower()
    release_sha = _strict_text(value["release_sha"], "W7_EVIDENCE_RELEASE_SHA_INVALID").lower()
    evidence_sha = _strict_text(value["evidence_sha256"], "W7_EVIDENCE_SHA_INVALID").lower()
    if not _SHA64_RE.fullmatch(policy_sha):
        raise WeatherW7EvidenceError("W7_EVIDENCE_POLICY_SHA_INVALID")
    if not _SHA40_RE.fullmatch(release_sha):
        raise WeatherW7EvidenceError("W7_EVIDENCE_RELEASE_SHA_INVALID")
    if not _SHA64_RE.fullmatch(evidence_sha):
        raise WeatherW7EvidenceError("W7_EVIDENCE_SHA_INVALID")

    run = _run_from_dict(value["run_evidence"])
    envelope = WeatherW7EvidenceEnvelope(
        version=version,
        acceptance_version=acceptance_version,
        runtime_version=runtime_version,
        policy_id=policy_id,
        policy_sha256=policy_sha,
        release_sha=release_sha,
        created_at=float(_strict_float(value["created_at"])),
        run_evidence=run,
        evidence_sha256=evidence_sha,
    )
    # Serialized authority fields are part of the digest and must be exactly false.
    for key in (
        "financial_authority",
        "financial_delivery",
        "detector_promotion_authority",
        "automatic_order_placement",
    ):
        if _strict_bool(value[key]) is not False:
            raise WeatherW7EvidenceError("W7_EVIDENCE_AUTHORITY_BOUNDARY_BROKEN")

    report = validate_weather_w7_evidence_envelope(
        envelope,
        expected_release_sha=expected_release_sha,
    )
    return envelope, report


def dump_weather_w7_evidence_json(envelope: WeatherW7EvidenceEnvelope) -> str:
    validate_weather_w7_evidence_envelope(envelope)
    return json.dumps(envelope.as_dict(), sort_keys=True, indent=2, allow_nan=False)
