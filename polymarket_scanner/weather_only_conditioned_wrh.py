from __future__ import annotations

"""Layer-1 adapter: exact WRH contract observations -> accepted O(t).

The generic conditioned-extreme model must never be handed an arbitrary temperature
series and told that it is the contract observation state.  This adapter binds Layer
1 to the same pinned WRH Hourly Data parser used by the source lane, verifies the
compiled contract station/date/family/unit, enforces a strict decision-time snapshot
age, and converts only already-received target-day rows into official observations.

The value admitted to O(t) is the integer temperature displayed by the pinned WRH
viewer semantics, matching current whole-degree-F contracts.  The snapshot remains an
unfinalized as-of observation source: accepted observations can establish what has
already occurred, but they never become final settlement or financial authority.
"""

import hashlib
import json
import math
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .weather_only_conditioned_extremes import (
    OFFICIAL_OBSERVATION_ROLE,
    ObservedExtremeState,
    OfficialObservation,
    build_observed_extreme,
)
from .weather_only_contracts import (
    DAILY_HIGH,
    DAILY_LOW,
    SOURCE_NWS_WRH,
    CompiledWeatherEvent,
)
from .weather_only_wrh import WRHSourceSnapshot
from .weather_only_wrh_finality import _validate_snapshot


CONDITIONED_WRH_OBSERVED_VERSION = "weather_conditioned_wrh_observed_v1_exact_asof_hourly"
DEFAULT_MAX_SNAPSHOT_AGE_SECONDS = 180.0


class ConditionedWRHError(RuntimeError):
    def __init__(self, code: str):
        self.code = str(code)
        super().__init__(self.code)


def _finite(value: object, code: str) -> float:
    if value is None or isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ConditionedWRHError(code)
    number = float(value)
    if not math.isfinite(number):
        raise ConditionedWRHError(code)
    return number


def _sha(value: object) -> str:
    try:
        encoded = json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError):
        raise ConditionedWRHError("CONDITIONED_WRH_EVIDENCE_JSON_INVALID") from None
    return hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True, slots=True)
class WRHObservedAsOfEvidence:
    version: str
    event_id: str
    station: str
    target_date: str
    family: str
    unit: str
    population_id: str
    as_of: float
    snapshot_received_at: float
    snapshot_age_seconds: float
    source_snapshot_sha256: str
    source_payload_sha256: str
    accepted_row_count: int
    accepted_row_times: tuple[str, ...]
    observed_state: ObservedExtremeState
    evidence_sha256: str
    source_role: str = field(init=False, default=OFFICIAL_OBSERVATION_ROLE)
    settlement_authority: bool = field(init=False, default=False)
    financial_authority: bool = field(init=False, default=False)

    def as_dict(self) -> dict:
        value = asdict(self)
        value["observed_state"] = self.observed_state.as_dict()
        return value


def _contract_identity(compiled: object) -> CompiledWeatherEvent:
    if not isinstance(compiled, CompiledWeatherEvent):
        raise ConditionedWRHError("CONDITIONED_WRH_CONTRACT_TYPE_INVALID")
    if compiled.source_family != SOURCE_NWS_WRH:
        raise ConditionedWRHError("CONDITIONED_WRH_SOURCE_FAMILY_UNSUPPORTED")
    if compiled.family not in {DAILY_HIGH, DAILY_LOW}:
        raise ConditionedWRHError("CONDITIONED_WRH_FAMILY_UNSUPPORTED")
    if compiled.unit != "F":
        raise ConditionedWRHError("CONDITIONED_WRH_UNIT_UNSUPPORTED")
    if compiled.target_date is None:
        raise ConditionedWRHError("CONDITIONED_WRH_TARGET_DATE_UNRESOLVED")
    station = str(compiled.station_hint or "").strip().upper()
    if not re.fullmatch(r"[A-Z0-9]{4}", station):
        raise ConditionedWRHError("CONDITIONED_WRH_STATION_UNRESOLVED")
    if not compiled.exactly_one_outcome_proven or compiled.financial_authority:
        raise ConditionedWRHError("CONDITIONED_WRH_RULE_AUTHORITY_UNPROVEN")
    return compiled


def _local_day_bounds(snapshot: WRHSourceSnapshot) -> tuple[float, float]:
    try:
        zone = ZoneInfo(snapshot.timezone)
    except ZoneInfoNotFoundError:
        raise ConditionedWRHError("CONDITIONED_WRH_TIMEZONE_INVALID") from None
    target = snapshot.target_date
    start = datetime(target.year, target.month, target.day, tzinfo=zone)
    following = target + timedelta(days=1)
    end = datetime(following.year, following.month, following.day, tzinfo=zone)
    return start.timestamp(), end.timestamp()


def _evidence_payload(evidence: WRHObservedAsOfEvidence) -> dict:
    return {
        "version": evidence.version,
        "event_id": evidence.event_id,
        "station": evidence.station,
        "target_date": evidence.target_date,
        "family": evidence.family,
        "unit": evidence.unit,
        "population_id": evidence.population_id,
        "as_of": evidence.as_of,
        "snapshot_received_at": evidence.snapshot_received_at,
        "snapshot_age_seconds": evidence.snapshot_age_seconds,
        "source_snapshot_sha256": evidence.source_snapshot_sha256,
        "source_payload_sha256": evidence.source_payload_sha256,
        "accepted_row_count": evidence.accepted_row_count,
        "accepted_row_times": evidence.accepted_row_times,
        "observed_state_sha256": evidence.observed_state.evidence_sha256,
        "source_role": evidence.source_role,
        "settlement_authority": evidence.settlement_authority,
        "financial_authority": evidence.financial_authority,
    }


def build_wrh_observed_extreme_asof(
    snapshot: WRHSourceSnapshot,
    compiled: CompiledWeatherEvent,
    *,
    as_of: float,
    max_snapshot_age_seconds: float = DEFAULT_MAX_SNAPSHOT_AGE_SECONDS,
) -> WRHObservedAsOfEvidence:
    """Build accepted official O(t) from one digest-valid WRH snapshot."""
    contract = _contract_identity(compiled)
    try:
        source = _validate_snapshot(snapshot, "CONDITIONED")
    except Exception as exc:
        code = getattr(exc, "code", type(exc).__name__)
        raise ConditionedWRHError(f"CONDITIONED_WRH_SNAPSHOT_INVALID:{code}") from exc

    cutoff = _finite(as_of, "CONDITIONED_WRH_AS_OF_INVALID")
    max_age = _finite(max_snapshot_age_seconds, "CONDITIONED_WRH_MAX_AGE_INVALID")
    if cutoff < 0.0 or max_age <= 0.0:
        raise ConditionedWRHError("CONDITIONED_WRH_TIME_POLICY_INVALID")
    if source.received_at > cutoff:
        raise ConditionedWRHError("CONDITIONED_WRH_SNAPSHOT_LOOKAHEAD")
    age = cutoff - float(source.received_at)
    if age > max_age:
        raise ConditionedWRHError("CONDITIONED_WRH_SNAPSHOT_STALE")

    station = str(contract.station_hint).strip().upper()
    if source.station != station:
        raise ConditionedWRHError("CONDITIONED_WRH_STATION_MISMATCH")
    if source.target_date != contract.target_date:
        raise ConditionedWRHError("CONDITIONED_WRH_TARGET_DATE_MISMATCH")
    if source.response_temperature_unit.lower() != "fahrenheit":
        raise ConditionedWRHError("CONDITIONED_WRH_RESPONSE_UNIT_MISMATCH")

    target_start, target_end = _local_day_bounds(source)
    if cutoff < target_start or cutoff >= target_end:
        raise ConditionedWRHError("CONDITIONED_WRH_AS_OF_OUTSIDE_TARGET_LOCAL_DAY")

    population_id = (
        f"WRH_HOURLY_DATA:{source.source_profile}:{source.viewer_script_sha256}"
    )
    observations: list[OfficialObservation] = []
    accepted_times: list[str] = []
    for row in source.target_rows:
        observed_at = row.observation_time_local.timestamp()
        if observed_at > cutoff + 1e-9:
            raise ConditionedWRHError("CONDITIONED_WRH_ROW_AFTER_AS_OF")
        if row.displayed_temp_f is None:
            continue
        revision = _sha({
            "snapshot": source.evidence_sha256,
            "raw_time": row.observation_time_raw,
            "row_kind": row.row_kind,
            "displayed_temp_f": row.displayed_temp_f,
        })
        observations.append(OfficialObservation(
            station=station,
            population_id=population_id,
            unit="F",
            observed_at=observed_at,
            received_at=float(source.received_at),
            value=float(row.displayed_temp_f),
            source_revision=revision,
        ))
        accepted_times.append(row.observation_time_local.isoformat())

    if not observations:
        raise ConditionedWRHError("CONDITIONED_WRH_NO_ACCEPTED_TARGET_OBSERVATIONS")

    try:
        state = build_observed_extreme(
            tuple(observations),
            station=station,
            population_id=population_id,
            unit="F",
            family=contract.family,
            target_start=target_start,
            as_of=cutoff,
        )
    except Exception as exc:
        code = getattr(exc, "code", type(exc).__name__)
        raise ConditionedWRHError(f"CONDITIONED_WRH_OBSERVED_STATE_INVALID:{code}") from exc

    shell = WRHObservedAsOfEvidence(
        version=CONDITIONED_WRH_OBSERVED_VERSION,
        event_id=contract.event_id,
        station=station,
        target_date=contract.target_date.isoformat(),
        family=contract.family,
        unit="F",
        population_id=population_id,
        as_of=cutoff,
        snapshot_received_at=float(source.received_at),
        snapshot_age_seconds=age,
        source_snapshot_sha256=source.evidence_sha256,
        source_payload_sha256=source.source_payload_sha256,
        accepted_row_count=len(observations),
        accepted_row_times=tuple(accepted_times),
        observed_state=state,
        evidence_sha256="0" * 64,
    )
    return WRHObservedAsOfEvidence(
        **{
            name: getattr(shell, name)
            for name, definition in shell.__dataclass_fields__.items()
            if definition.init and name != "evidence_sha256"
        },
        evidence_sha256=_sha(_evidence_payload(shell)),
    )
