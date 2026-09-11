from __future__ import annotations

"""Causal source-update latency evidence for weather W7 silent-shadow acceptance.

The frozen weather program defines the W7 latency target as *source update to
confirmed candidate*, not source poll to arbitrary computation. This module keeps
that distinction machine-checkable.

A latency sample can exist only when:

* two independently validated WRH snapshots refer to the same exact event source;
* the later snapshot contains a material event-state change (target-day state change
  or first-following-date finality transition), not merely unrelated payload drift;
* the compiled contract identity matches station/date/family/unit and already has
  exact bucket/rule semantics proven;
* the trigger freezes the exact condition/token set for that compiled event;
* the resulting shadow candidate explicitly binds the later WRH snapshot evidence;
* an exact CLOB event snapshot covers that same frozen condition/token set;
* every financial/delivery/order authority flag remains false.

This is instrumentation only. It does not fetch WRH, place orders, send Telegram,
promote detectors or create a candidate by itself.
"""

import hashlib
import json
import math
import re
import time
from dataclasses import asdict, dataclass, field
from datetime import date
from typing import Awaitable, Callable

from .weather_only_clob import WeatherExecutionSnapshot
from .weather_only_contracts import (
    DAILY_HIGH,
    DAILY_LOW,
    SOURCE_NWS_WRH,
    CompiledWeatherEvent,
)
from .weather_only_wrh import WRHSourceError, WRHSourceSnapshot
from .weather_only_wrh_finality import _validate_snapshot


WEATHER_W7_SOURCE_LATENCY_VERSION = "weather_w7_source_latency_v1_material_wrh_change_exact_clob_candidate_binding"
CHANGE_TARGET_STATE = "TARGET_STATE_CHANGE"
CHANGE_FINALITY_TRANSITION = "FINALITY_TRANSITION"
_SHA64_RE = re.compile(r"^[0-9a-f]{64}$")


class WeatherW7SourceLatencyError(RuntimeError):
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
        raise WeatherW7SourceLatencyError("W7_SOURCE_JSON_INVALID") from None


def _sha(value: object) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _sha64(value: object, code: str) -> str:
    text = str(value or "").strip().lower()
    if not _SHA64_RE.fullmatch(text):
        raise WeatherW7SourceLatencyError(code)
    return text


def _finite(value: object, code: str) -> float:
    if value is None or isinstance(value, bool) or not isinstance(value, (int, float)):
        raise WeatherW7SourceLatencyError(code)
    number = float(value)
    if not math.isfinite(number) or number < 0.0:
        raise WeatherW7SourceLatencyError(code)
    return number


@dataclass(frozen=True, slots=True)
class WeatherW7SourceUpdateTrigger:
    version: str
    event_id: str
    station: str
    target_date: date
    family: str
    change_kind: str
    condition_ids: tuple[str, ...]
    token_ids: tuple[str, ...]
    previous_snapshot_sha256: str
    current_snapshot_sha256: str
    previous_target_state_sha256: str
    current_target_state_sha256: str
    source_received_at: float
    trigger_evidence_sha256: str
    financial_authority: bool = field(init=False, default=False)
    financial_delivery: bool = field(init=False, default=False)
    automatic_order_placement: bool = field(init=False, default=False)

    def as_dict(self) -> dict:
        value = asdict(self)
        value["target_date"] = self.target_date.isoformat()
        return value


@dataclass(frozen=True, slots=True)
class WeatherW7ConfirmedShadowCandidate:
    version: str
    event_id: str
    station: str
    target_date: date
    family: str
    trigger_evidence_sha256: str
    source_snapshot_sha256: str
    source_target_state_sha256: str
    candidate_evidence_sha256: str
    exact_clob_evidence_sha256: str
    confirmation_evidence_sha256: str
    candidate_confirmed: bool = field(init=False, default=True)
    exact_clob: bool = field(init=False, default=True)
    financial_authority: bool = field(init=False, default=False)
    financial_delivery: bool = field(init=False, default=False)
    automatic_order_placement: bool = field(init=False, default=False)

    def as_dict(self) -> dict:
        value = asdict(self)
        value["target_date"] = self.target_date.isoformat()
        return value


@dataclass(frozen=True, slots=True)
class WeatherW7SourceUpdateLatencyMeasurement:
    version: str
    trigger_evidence_sha256: str
    candidate_confirmation_sha256: str
    source_received_at: float
    evaluation_started_at: float
    evaluation_finished_at: float
    incremental_evaluation_seconds: float
    source_update_confirmation_seconds: float
    measurement_evidence_sha256: str
    financial_authority: bool = field(init=False, default=False)
    financial_delivery: bool = field(init=False, default=False)
    automatic_order_placement: bool = field(init=False, default=False)

    def as_dict(self) -> dict:
        return asdict(self)

    def w7_latency_fields(self) -> dict[str, float]:
        return {
            "incremental_evaluation_seconds": self.incremental_evaluation_seconds,
            "source_update_confirmation_seconds": self.source_update_confirmation_seconds,
        }


def _trigger_digest_payload(trigger: WeatherW7SourceUpdateTrigger) -> dict:
    return {
        "version": trigger.version,
        "event_id": trigger.event_id,
        "station": trigger.station,
        "target_date": trigger.target_date.isoformat(),
        "family": trigger.family,
        "change_kind": trigger.change_kind,
        "condition_ids": trigger.condition_ids,
        "token_ids": trigger.token_ids,
        "previous_snapshot_sha256": trigger.previous_snapshot_sha256,
        "current_snapshot_sha256": trigger.current_snapshot_sha256,
        "previous_target_state_sha256": trigger.previous_target_state_sha256,
        "current_target_state_sha256": trigger.current_target_state_sha256,
        "source_received_at": trigger.source_received_at,
    }


def _confirmation_digest_payload(candidate: WeatherW7ConfirmedShadowCandidate) -> dict:
    return {
        "version": candidate.version,
        "event_id": candidate.event_id,
        "station": candidate.station,
        "target_date": candidate.target_date.isoformat(),
        "family": candidate.family,
        "trigger_evidence_sha256": candidate.trigger_evidence_sha256,
        "source_snapshot_sha256": candidate.source_snapshot_sha256,
        "source_target_state_sha256": candidate.source_target_state_sha256,
        "candidate_evidence_sha256": candidate.candidate_evidence_sha256,
        "exact_clob_evidence_sha256": candidate.exact_clob_evidence_sha256,
        "candidate_confirmed": True,
        "exact_clob": True,
        "financial_authority": False,
        "financial_delivery": False,
        "automatic_order_placement": False,
    }


def _measurement_digest_payload(measurement: WeatherW7SourceUpdateLatencyMeasurement) -> dict:
    return {
        "version": measurement.version,
        "trigger_evidence_sha256": measurement.trigger_evidence_sha256,
        "candidate_confirmation_sha256": measurement.candidate_confirmation_sha256,
        "source_received_at": measurement.source_received_at,
        "evaluation_started_at": measurement.evaluation_started_at,
        "evaluation_finished_at": measurement.evaluation_finished_at,
        "incremental_evaluation_seconds": measurement.incremental_evaluation_seconds,
        "source_update_confirmation_seconds": measurement.source_update_confirmation_seconds,
        "financial_authority": False,
        "financial_delivery": False,
        "automatic_order_placement": False,
    }


def _validate_compiled_identity(compiled: object, snapshot: WRHSourceSnapshot) -> CompiledWeatherEvent:
    if not isinstance(compiled, CompiledWeatherEvent):
        raise WeatherW7SourceLatencyError("W7_SOURCE_COMPILED_TYPE_INVALID")
    if compiled.source_family != SOURCE_NWS_WRH:
        raise WeatherW7SourceLatencyError("W7_SOURCE_CONTRACT_SOURCE_UNSUPPORTED")
    if compiled.family not in {DAILY_HIGH, DAILY_LOW}:
        raise WeatherW7SourceLatencyError("W7_SOURCE_CONTRACT_FAMILY_UNSUPPORTED")
    if compiled.unit != "F":
        raise WeatherW7SourceLatencyError("W7_SOURCE_CONTRACT_UNIT_UNSUPPORTED")
    if compiled.target_date is None or compiled.target_date != snapshot.target_date:
        raise WeatherW7SourceLatencyError("W7_SOURCE_CONTRACT_DATE_MISMATCH")
    if str(compiled.station_hint or "").strip().upper() != snapshot.station:
        raise WeatherW7SourceLatencyError("W7_SOURCE_CONTRACT_STATION_MISMATCH")
    if not compiled.partition_shape_complete or not compiled.exactly_one_outcome_proven:
        raise WeatherW7SourceLatencyError("W7_SOURCE_CONTRACT_RULES_UNPROVEN")
    if compiled.financial_authority is not False:
        raise WeatherW7SourceLatencyError("W7_SOURCE_CONTRACT_AUTHORITY_BOUNDARY_BROKEN")
    if not compiled.buckets or any(
        not bucket.trade_open or not bucket.condition_id or not bucket.yes_token or not bucket.no_token
        for bucket in compiled.buckets
    ):
        raise WeatherW7SourceLatencyError("W7_SOURCE_CONTRACT_EXECUTION_IDENTITY_INCOMPLETE")
    return compiled


def build_wrh_source_update_trigger(
    previous: WRHSourceSnapshot,
    current: WRHSourceSnapshot,
    *,
    compiled: CompiledWeatherEvent,
) -> WeatherW7SourceUpdateTrigger:
    """Create one material, source-authentic event trigger from two WRH snapshots."""
    try:
        before = _validate_snapshot(previous, "W7_PREVIOUS")
        after = _validate_snapshot(current, "W7_CURRENT")
    except WRHSourceError as exc:
        raise WeatherW7SourceLatencyError(f"W7_SOURCE_SNAPSHOT_INVALID:{exc.code}") from exc

    identity_before = (
        before.station,
        before.target_date,
        before.timezone,
        before.raw_network,
        before.normalized_network,
        before.source_profile,
        before.viewer_script_sha256,
        before.source_endpoint,
    )
    identity_after = (
        after.station,
        after.target_date,
        after.timezone,
        after.raw_network,
        after.normalized_network,
        after.source_profile,
        after.viewer_script_sha256,
        after.source_endpoint,
    )
    if identity_before != identity_after:
        raise WeatherW7SourceLatencyError("W7_SOURCE_IDENTITY_MISMATCH")
    contract = _validate_compiled_identity(compiled, after)

    previous_received = _finite(before.received_at, "W7_SOURCE_PREVIOUS_RECEIVED_AT_INVALID")
    current_received = _finite(after.received_at, "W7_SOURCE_CURRENT_RECEIVED_AT_INVALID")
    if current_received <= previous_received:
        raise WeatherW7SourceLatencyError("W7_SOURCE_RECEIPT_ORDER_INVALID")
    if before.source_payload_sha256 == after.source_payload_sha256:
        raise WeatherW7SourceLatencyError("W7_SOURCE_PAYLOAD_UNCHANGED")

    if before.target_state_sha256 != after.target_state_sha256:
        change_kind = CHANGE_TARGET_STATE
    elif before.first_following_row is None and after.first_following_row is not None:
        change_kind = CHANGE_FINALITY_TRANSITION
    else:
        # Source payloads can change for rows that do not affect this contract. Such
        # drift is valid source evidence but cannot be used to claim W7 event-update
        # responsiveness.
        raise WeatherW7SourceLatencyError("W7_SOURCE_UPDATE_NOT_EVENT_RELEVANT")

    conditions = tuple(sorted({bucket.condition_id for bucket in contract.buckets}))
    tokens = tuple(sorted({token for bucket in contract.buckets for token in (bucket.yes_token, bucket.no_token)}))
    if not conditions or not tokens:
        raise WeatherW7SourceLatencyError("W7_SOURCE_CONTRACT_EXECUTION_IDENTITY_INCOMPLETE")

    shell = WeatherW7SourceUpdateTrigger(
        version=WEATHER_W7_SOURCE_LATENCY_VERSION,
        event_id=contract.event_id,
        station=after.station,
        target_date=after.target_date,
        family=contract.family,
        change_kind=change_kind,
        condition_ids=conditions,
        token_ids=tokens,
        previous_snapshot_sha256=_sha64(before.evidence_sha256, "W7_SOURCE_PREVIOUS_SHA_INVALID"),
        current_snapshot_sha256=_sha64(after.evidence_sha256, "W7_SOURCE_CURRENT_SHA_INVALID"),
        previous_target_state_sha256=_sha64(before.target_state_sha256, "W7_SOURCE_PREVIOUS_TARGET_SHA_INVALID"),
        current_target_state_sha256=_sha64(after.target_state_sha256, "W7_SOURCE_CURRENT_TARGET_SHA_INVALID"),
        source_received_at=current_received,
        trigger_evidence_sha256="0" * 64,
    )
    return WeatherW7SourceUpdateTrigger(
        version=shell.version,
        event_id=shell.event_id,
        station=shell.station,
        target_date=shell.target_date,
        family=shell.family,
        change_kind=shell.change_kind,
        condition_ids=shell.condition_ids,
        token_ids=shell.token_ids,
        previous_snapshot_sha256=shell.previous_snapshot_sha256,
        current_snapshot_sha256=shell.current_snapshot_sha256,
        previous_target_state_sha256=shell.previous_target_state_sha256,
        current_target_state_sha256=shell.current_target_state_sha256,
        source_received_at=shell.source_received_at,
        trigger_evidence_sha256=_sha(_trigger_digest_payload(shell)),
    )


def _execution_digest(snapshot: WeatherExecutionSnapshot, trigger: WeatherW7SourceUpdateTrigger) -> str:
    if not isinstance(snapshot, WeatherExecutionSnapshot):
        raise WeatherW7SourceLatencyError("W7_SOURCE_CLOB_SNAPSHOT_TYPE_INVALID")
    if snapshot.event_id != trigger.event_id:
        raise WeatherW7SourceLatencyError("W7_SOURCE_CLOB_EVENT_MISMATCH")
    if snapshot.exact_clob is not True or snapshot.financial_authority is not False:
        raise WeatherW7SourceLatencyError("W7_SOURCE_CLOB_AUTHORITY_BOUNDARY_BROKEN")
    started = _finite(snapshot.started_at, "W7_SOURCE_CLOB_STARTED_AT_INVALID")
    finished = _finite(snapshot.finished_at, "W7_SOURCE_CLOB_FINISHED_AT_INVALID")
    if finished < started:
        raise WeatherW7SourceLatencyError("W7_SOURCE_CLOB_TIME_ORDER_INVALID")
    if set(snapshot.parameters) != set(trigger.condition_ids):
        raise WeatherW7SourceLatencyError("W7_SOURCE_CLOB_CONDITION_SET_MISMATCH")
    if set(snapshot.books) != set(trigger.token_ids):
        raise WeatherW7SourceLatencyError("W7_SOURCE_CLOB_TOKEN_SET_MISMATCH")
    for condition, params in snapshot.parameters.items():
        if params.condition_id != condition:
            raise WeatherW7SourceLatencyError("W7_SOURCE_CLOB_CONDITION_IDENTITY_MISMATCH")
        if {token for token, _ in params.token_outcomes} != {
            token for token in trigger.token_ids if token in {pair[0] for pair in params.token_outcomes}
        }:
            # This branch is intentionally unreachable for a well-formed market-info
            # object but keeps malformed/fabricated parameter objects from silently
            # becoming timing evidence. Exact per-condition token equality is checked
            # against the book set by the client before this layer.
            raise WeatherW7SourceLatencyError("W7_SOURCE_CLOB_PARAMETER_TOKEN_IDENTITY_INVALID")
    return _sha(asdict(snapshot))


def build_w7_confirmed_shadow_candidate(
    trigger: WeatherW7SourceUpdateTrigger,
    execution_snapshot: WeatherExecutionSnapshot,
    *,
    candidate_evidence_sha256: str,
    candidate_source_snapshot_sha256: str,
) -> WeatherW7ConfirmedShadowCandidate:
    """Bind one already-built shadow candidate to source trigger + exact CLOB evidence."""
    if not isinstance(trigger, WeatherW7SourceUpdateTrigger):
        raise WeatherW7SourceLatencyError("W7_SOURCE_TRIGGER_TYPE_INVALID")
    if trigger.version != WEATHER_W7_SOURCE_LATENCY_VERSION:
        raise WeatherW7SourceLatencyError("W7_SOURCE_TRIGGER_VERSION_MISMATCH")
    if _sha(_trigger_digest_payload(trigger)) != _sha64(
        trigger.trigger_evidence_sha256, "W7_SOURCE_TRIGGER_SHA_INVALID"
    ):
        raise WeatherW7SourceLatencyError("W7_SOURCE_TRIGGER_DIGEST_MISMATCH")

    candidate_sha = _sha64(candidate_evidence_sha256, "W7_SOURCE_CANDIDATE_SHA_INVALID")
    source_sha = _sha64(candidate_source_snapshot_sha256, "W7_SOURCE_CANDIDATE_SOURCE_SHA_INVALID")
    if source_sha != trigger.current_snapshot_sha256:
        raise WeatherW7SourceLatencyError("W7_SOURCE_CANDIDATE_NOT_BOUND_TO_CURRENT_SOURCE")
    clob_sha = _execution_digest(execution_snapshot, trigger)

    shell = WeatherW7ConfirmedShadowCandidate(
        version=WEATHER_W7_SOURCE_LATENCY_VERSION,
        event_id=trigger.event_id,
        station=trigger.station,
        target_date=trigger.target_date,
        family=trigger.family,
        trigger_evidence_sha256=trigger.trigger_evidence_sha256,
        source_snapshot_sha256=source_sha,
        source_target_state_sha256=trigger.current_target_state_sha256,
        candidate_evidence_sha256=candidate_sha,
        exact_clob_evidence_sha256=clob_sha,
        confirmation_evidence_sha256="0" * 64,
    )
    return WeatherW7ConfirmedShadowCandidate(
        version=shell.version,
        event_id=shell.event_id,
        station=shell.station,
        target_date=shell.target_date,
        family=shell.family,
        trigger_evidence_sha256=shell.trigger_evidence_sha256,
        source_snapshot_sha256=shell.source_snapshot_sha256,
        source_target_state_sha256=shell.source_target_state_sha256,
        candidate_evidence_sha256=shell.candidate_evidence_sha256,
        exact_clob_evidence_sha256=shell.exact_clob_evidence_sha256,
        confirmation_evidence_sha256=_sha(_confirmation_digest_payload(shell)),
    )


def _validate_confirmation(
    trigger: WeatherW7SourceUpdateTrigger,
    candidate: object,
) -> WeatherW7ConfirmedShadowCandidate:
    if not isinstance(candidate, WeatherW7ConfirmedShadowCandidate):
        raise WeatherW7SourceLatencyError("W7_SOURCE_CANDIDATE_NOT_CONFIRMED")
    if candidate.version != WEATHER_W7_SOURCE_LATENCY_VERSION:
        raise WeatherW7SourceLatencyError("W7_SOURCE_CANDIDATE_VERSION_MISMATCH")
    if (
        candidate.event_id != trigger.event_id
        or candidate.station != trigger.station
        or candidate.target_date != trigger.target_date
        or candidate.family != trigger.family
        or candidate.trigger_evidence_sha256 != trigger.trigger_evidence_sha256
        or candidate.source_snapshot_sha256 != trigger.current_snapshot_sha256
        or candidate.source_target_state_sha256 != trigger.current_target_state_sha256
    ):
        raise WeatherW7SourceLatencyError("W7_SOURCE_CANDIDATE_IDENTITY_MISMATCH")
    if any((
        candidate.candidate_confirmed is not True,
        candidate.exact_clob is not True,
        candidate.financial_authority is not False,
        candidate.financial_delivery is not False,
        candidate.automatic_order_placement is not False,
    )):
        raise WeatherW7SourceLatencyError("W7_SOURCE_CANDIDATE_AUTHORITY_BOUNDARY_BROKEN")
    _sha64(candidate.candidate_evidence_sha256, "W7_SOURCE_CANDIDATE_SHA_INVALID")
    _sha64(candidate.exact_clob_evidence_sha256, "W7_SOURCE_CLOB_SHA_INVALID")
    supplied = _sha64(candidate.confirmation_evidence_sha256, "W7_SOURCE_CONFIRMATION_SHA_INVALID")
    if supplied != _sha(_confirmation_digest_payload(candidate)):
        raise WeatherW7SourceLatencyError("W7_SOURCE_CONFIRMATION_DIGEST_MISMATCH")
    return candidate


async def measure_w7_source_update_confirmation(
    trigger: WeatherW7SourceUpdateTrigger,
    evaluator: Callable[[WeatherW7SourceUpdateTrigger], Awaitable[WeatherW7ConfirmedShadowCandidate | None]],
    *,
    wall_clock: Callable[[], float] = time.time,
    monotonic_clock: Callable[[], float] = time.monotonic,
) -> WeatherW7SourceUpdateLatencyMeasurement:
    """Measure a causally bound source-update -> exact-confirmed shadow candidate path.

    A source update that produces no confirmed candidate deliberately returns no W7
    latency sample: the caller receives ``W7_SOURCE_CANDIDATE_NOT_CONFIRMED`` and may
    retain that separately as diagnostic evidence.
    """
    if not isinstance(trigger, WeatherW7SourceUpdateTrigger):
        raise WeatherW7SourceLatencyError("W7_SOURCE_TRIGGER_TYPE_INVALID")
    if not callable(evaluator) or not callable(wall_clock) or not callable(monotonic_clock):
        raise WeatherW7SourceLatencyError("W7_SOURCE_MEASUREMENT_CALLABLE_INVALID")
    if _sha(_trigger_digest_payload(trigger)) != _sha64(
        trigger.trigger_evidence_sha256, "W7_SOURCE_TRIGGER_SHA_INVALID"
    ):
        raise WeatherW7SourceLatencyError("W7_SOURCE_TRIGGER_DIGEST_MISMATCH")

    started_wall = _finite(wall_clock(), "W7_SOURCE_MEASUREMENT_WALL_CLOCK_INVALID")
    started_mono = _finite(monotonic_clock(), "W7_SOURCE_MEASUREMENT_MONOTONIC_INVALID")
    if started_wall + 1e-9 < trigger.source_received_at:
        raise WeatherW7SourceLatencyError("W7_SOURCE_MEASUREMENT_STARTED_BEFORE_RECEIPT")

    candidate = await evaluator(trigger)

    finished_mono = _finite(monotonic_clock(), "W7_SOURCE_MEASUREMENT_MONOTONIC_INVALID")
    finished_wall = _finite(wall_clock(), "W7_SOURCE_MEASUREMENT_WALL_CLOCK_INVALID")
    if finished_mono < started_mono or finished_wall < started_wall:
        raise WeatherW7SourceLatencyError("W7_SOURCE_MEASUREMENT_CLOCK_REGRESSION")
    confirmed = _validate_confirmation(trigger, candidate)

    incremental = finished_mono - started_mono
    source_to_confirmation = finished_wall - trigger.source_received_at
    if source_to_confirmation + 1e-9 < incremental:
        # The wall interval includes queueing from receipt to evaluator start plus the
        # evaluator itself, so it cannot legitimately be shorter than monotonic work.
        raise WeatherW7SourceLatencyError("W7_SOURCE_MEASUREMENT_CLOCK_DOMAINS_INCONSISTENT")

    shell = WeatherW7SourceUpdateLatencyMeasurement(
        version=WEATHER_W7_SOURCE_LATENCY_VERSION,
        trigger_evidence_sha256=trigger.trigger_evidence_sha256,
        candidate_confirmation_sha256=confirmed.confirmation_evidence_sha256,
        source_received_at=trigger.source_received_at,
        evaluation_started_at=started_wall,
        evaluation_finished_at=finished_wall,
        incremental_evaluation_seconds=incremental,
        source_update_confirmation_seconds=source_to_confirmation,
        measurement_evidence_sha256="0" * 64,
    )
    return WeatherW7SourceUpdateLatencyMeasurement(
        version=shell.version,
        trigger_evidence_sha256=shell.trigger_evidence_sha256,
        candidate_confirmation_sha256=shell.candidate_confirmation_sha256,
        source_received_at=shell.source_received_at,
        evaluation_started_at=shell.evaluation_started_at,
        evaluation_finished_at=shell.evaluation_finished_at,
        incremental_evaluation_seconds=shell.incremental_evaluation_seconds,
        source_update_confirmation_seconds=shell.source_update_confirmation_seconds,
        measurement_evidence_sha256=_sha(_measurement_digest_payload(shell)),
    )
