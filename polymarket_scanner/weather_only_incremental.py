from __future__ import annotations

"""Measured one-event incremental evaluation for weather W7 acceptance.

The full weather discovery cycle is intentionally not used as the W7 '<2 seconds'
normal incremental metric. This module represents the hot-path operation: one already
known weather event changes, so its frozen contract/rule semantics are compiled and
one exact CLOB snapshot is evaluated immediately for structural shadow lanes.

Zero opportunities is a valid incremental evaluation result. Any opportunities remain
research-only; this module has no delivery/order path and grants no financial authority.
"""

import hashlib
import json
import math
import re
import time
from dataclasses import asdict, dataclass, field
from typing import Protocol

from .weather_only_clob import WeatherExecutionSnapshot
from .weather_only_contracts import DAILY_HIGH, DAILY_LOW, CompiledWeatherEvent, compile_weather_event
from .weather_only_rules import apply_rule_authority, compile_temperature_rule_authority
from .weather_only_structural import binary_pair_underround, complete_bucket_underround


WEATHER_INCREMENTAL_VERSION = "weather_incremental_v1_one_event_exact_clob_structural_shadow"
_SHA64_RE = re.compile(r"^[0-9a-f]{64}$")


class WeatherIncrementalError(RuntimeError):
    def __init__(self, code: str):
        self.code = str(code)
        super().__init__(self.code)


class ExactEventSnapshotClient(Protocol):
    async def exact_event_snapshot(self, compiled: CompiledWeatherEvent) -> WeatherExecutionSnapshot: ...


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
        raise WeatherIncrementalError("INCREMENTAL_JSON_INVALID") from None


def _sha(value: object) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _finite(value: object, code: str) -> float:
    if value is None or isinstance(value, bool) or not isinstance(value, (int, float)):
        raise WeatherIncrementalError(code)
    number = float(value)
    if not math.isfinite(number) or number < 0.0:
        raise WeatherIncrementalError(code)
    return number


def _sha64(value: object, code: str) -> str:
    text = str(value or "").strip().lower()
    if not _SHA64_RE.fullmatch(text):
        raise WeatherIncrementalError(code)
    return text


@dataclass(frozen=True, slots=True)
class WeatherIncrementalEvaluationReceipt:
    version: str
    event_id: str
    compiled_evidence_sha256: str
    exact_clob_evidence_sha256: str
    structural_opportunity_sha256: tuple[str, ...]
    structural_opportunity_count: int
    evaluated_at: float
    receipt_evidence_sha256: str
    exact_clob: bool = field(init=False, default=True)
    research_only: bool = field(init=False, default=True)
    financial_authority: bool = field(init=False, default=False)
    financial_delivery: bool = field(init=False, default=False)
    automatic_order_placement: bool = field(init=False, default=False)

    def as_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class WeatherIncrementalLatencyMeasurement:
    version: str
    receipt_evidence_sha256: str
    evaluation_started_at: float
    evaluation_finished_at: float
    incremental_evaluation_seconds: float
    measurement_evidence_sha256: str
    financial_authority: bool = field(init=False, default=False)
    financial_delivery: bool = field(init=False, default=False)
    automatic_order_placement: bool = field(init=False, default=False)

    def as_dict(self) -> dict:
        return asdict(self)


def _receipt_payload(receipt: WeatherIncrementalEvaluationReceipt) -> dict:
    value = receipt.as_dict()
    value.pop("receipt_evidence_sha256", None)
    return value


def _measurement_payload(row: WeatherIncrementalLatencyMeasurement) -> dict:
    value = row.as_dict()
    value.pop("measurement_evidence_sha256", None)
    return value


def validate_weather_incremental_latency_measurement(
    measurement: object,
) -> WeatherIncrementalLatencyMeasurement:
    """Recompute identity/timing/digest before a W7 envelope may trust this record."""
    if not isinstance(measurement, WeatherIncrementalLatencyMeasurement):
        raise WeatherIncrementalError("INCREMENTAL_MEASUREMENT_TYPE_INVALID")
    if measurement.version != WEATHER_INCREMENTAL_VERSION:
        raise WeatherIncrementalError("INCREMENTAL_MEASUREMENT_VERSION_MISMATCH")
    _sha64(measurement.receipt_evidence_sha256, "INCREMENTAL_RECEIPT_SHA_INVALID")
    supplied = _sha64(measurement.measurement_evidence_sha256, "INCREMENTAL_MEASUREMENT_SHA_INVALID")
    started = _finite(measurement.evaluation_started_at, "INCREMENTAL_MEASUREMENT_TIME_INVALID")
    finished = _finite(measurement.evaluation_finished_at, "INCREMENTAL_MEASUREMENT_TIME_INVALID")
    elapsed = _finite(measurement.incremental_evaluation_seconds, "INCREMENTAL_MEASUREMENT_LATENCY_INVALID")
    if finished < started:
        raise WeatherIncrementalError("INCREMENTAL_MEASUREMENT_TIME_ORDER_INVALID")
    # Wall elapsed can exceed or differ from monotonic elapsed, but it cannot be
    # materially shorter in a valid same-process measurement interval.
    if (finished - started) + 1e-9 < elapsed:
        raise WeatherIncrementalError("INCREMENTAL_MEASUREMENT_CLOCK_DOMAINS_INCONSISTENT")
    if any((
        measurement.financial_authority is not False,
        measurement.financial_delivery is not False,
        measurement.automatic_order_placement is not False,
    )):
        raise WeatherIncrementalError("INCREMENTAL_MEASUREMENT_AUTHORITY_BOUNDARY_BROKEN")
    if supplied != _sha(_measurement_payload(measurement)):
        raise WeatherIncrementalError("INCREMENTAL_MEASUREMENT_DIGEST_MISMATCH")
    return measurement


def _compile_incremental_event(event: object) -> CompiledWeatherEvent:
    if not isinstance(event, dict):
        raise WeatherIncrementalError("INCREMENTAL_EVENT_INVALID")
    raw = compile_weather_event(event)
    authority = compile_temperature_rule_authority(event, raw)
    compiled = apply_rule_authority(raw, authority)
    if compiled.family not in {DAILY_HIGH, DAILY_LOW}:
        raise WeatherIncrementalError("INCREMENTAL_FAMILY_UNSUPPORTED")
    if not compiled.shadow_supported or not compiled.partition_shape_complete:
        raise WeatherIncrementalError("INCREMENTAL_CONTRACT_NOT_SHADOW_SUPPORTED")
    if compiled.financial_authority or authority.financial_authority:
        raise WeatherIncrementalError("INCREMENTAL_AUTHORITY_BOUNDARY_BROKEN")
    return compiled


def _validate_execution(compiled: CompiledWeatherEvent, snapshot: object) -> WeatherExecutionSnapshot:
    if not isinstance(snapshot, WeatherExecutionSnapshot):
        raise WeatherIncrementalError("INCREMENTAL_CLOB_SNAPSHOT_TYPE_INVALID")
    if snapshot.event_id != compiled.event_id or snapshot.exact_clob is not True or snapshot.financial_authority is not False:
        raise WeatherIncrementalError("INCREMENTAL_CLOB_IDENTITY_INVALID")
    started = _finite(snapshot.started_at, "INCREMENTAL_CLOB_TIME_INVALID")
    finished = _finite(snapshot.finished_at, "INCREMENTAL_CLOB_TIME_INVALID")
    if finished < started:
        raise WeatherIncrementalError("INCREMENTAL_CLOB_TIME_ORDER_INVALID")
    expected_conditions = {bucket.condition_id for bucket in compiled.buckets if bucket.trade_open}
    expected_tokens = {
        token
        for bucket in compiled.buckets if bucket.trade_open
        for token in (bucket.yes_token, bucket.no_token)
        if token
    }
    if set(snapshot.parameters) != expected_conditions or set(snapshot.books) != expected_tokens:
        raise WeatherIncrementalError("INCREMENTAL_CLOB_COVERAGE_INVALID")
    for bucket in compiled.buckets:
        if not bucket.trade_open:
            continue
        params = snapshot.parameters.get(bucket.condition_id)
        if params is None or params.condition_id != bucket.condition_id:
            raise WeatherIncrementalError("INCREMENTAL_CLOB_PARAMETER_IDENTITY_INVALID")
        if {token for token, _ in params.token_outcomes} != {bucket.yes_token, bucket.no_token}:
            raise WeatherIncrementalError("INCREMENTAL_CLOB_PARAMETER_TOKEN_MISMATCH")
    return snapshot


async def evaluate_weather_event_incrementally(
    event: dict,
    *,
    clob: ExactEventSnapshotClient,
) -> WeatherIncrementalEvaluationReceipt:
    compiled = _compile_incremental_event(event)
    snapshot = _validate_execution(compiled, await clob.exact_event_snapshot(compiled))

    opportunities = list(binary_pair_underround(compiled, snapshot.books, snapshot.parameters))
    complete = complete_bucket_underround(compiled, snapshot.books, snapshot.parameters)
    if complete is not None:
        opportunities.append(complete)
    opportunity_sha = tuple(sorted(_sha(row.as_dict()) for row in opportunities))
    compiled_sha = _sha(compiled.as_dict())
    clob_sha = _sha(asdict(snapshot))

    shell = WeatherIncrementalEvaluationReceipt(
        version=WEATHER_INCREMENTAL_VERSION,
        event_id=compiled.event_id,
        compiled_evidence_sha256=compiled_sha,
        exact_clob_evidence_sha256=clob_sha,
        structural_opportunity_sha256=opportunity_sha,
        structural_opportunity_count=len(opportunity_sha),
        evaluated_at=snapshot.finished_at,
        receipt_evidence_sha256="0" * 64,
    )
    return WeatherIncrementalEvaluationReceipt(
        version=shell.version,
        event_id=shell.event_id,
        compiled_evidence_sha256=shell.compiled_evidence_sha256,
        exact_clob_evidence_sha256=shell.exact_clob_evidence_sha256,
        structural_opportunity_sha256=shell.structural_opportunity_sha256,
        structural_opportunity_count=shell.structural_opportunity_count,
        evaluated_at=shell.evaluated_at,
        receipt_evidence_sha256=_sha(_receipt_payload(shell)),
    )


async def measure_weather_incremental_evaluation(
    event: dict,
    *,
    clob: ExactEventSnapshotClient,
    monotonic_clock=time.monotonic,
    wall_clock=time.time,
) -> tuple[WeatherIncrementalEvaluationReceipt, WeatherIncrementalLatencyMeasurement]:
    if not callable(monotonic_clock) or not callable(wall_clock):
        raise WeatherIncrementalError("INCREMENTAL_CLOCK_INVALID")
    started_mono = _finite(monotonic_clock(), "INCREMENTAL_MONOTONIC_INVALID")
    started_wall = _finite(wall_clock(), "INCREMENTAL_WALL_CLOCK_INVALID")
    receipt = await evaluate_weather_event_incrementally(event, clob=clob)
    finished_mono = _finite(monotonic_clock(), "INCREMENTAL_MONOTONIC_INVALID")
    finished_wall = _finite(wall_clock(), "INCREMENTAL_WALL_CLOCK_INVALID")
    if finished_mono < started_mono or finished_wall < started_wall:
        raise WeatherIncrementalError("INCREMENTAL_CLOCK_REGRESSION")
    elapsed = finished_mono - started_mono

    shell = WeatherIncrementalLatencyMeasurement(
        version=WEATHER_INCREMENTAL_VERSION,
        receipt_evidence_sha256=receipt.receipt_evidence_sha256,
        evaluation_started_at=started_wall,
        evaluation_finished_at=finished_wall,
        incremental_evaluation_seconds=elapsed,
        measurement_evidence_sha256="0" * 64,
    )
    measured = WeatherIncrementalLatencyMeasurement(
        version=shell.version,
        receipt_evidence_sha256=shell.receipt_evidence_sha256,
        evaluation_started_at=shell.evaluation_started_at,
        evaluation_finished_at=shell.evaluation_finished_at,
        incremental_evaluation_seconds=shell.incremental_evaluation_seconds,
        measurement_evidence_sha256=_sha(_measurement_payload(shell)),
    )
    validate_weather_incremental_latency_measurement(measured)
    return receipt, measured
