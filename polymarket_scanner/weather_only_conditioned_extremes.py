from __future__ import annotations

"""Fail-closed foundation for observation-conditioned same-day weather research.

This module implements the information boundaries required by the September 2026
adversarial review without enabling a same-day trading lane by itself.

The model has three deliberately separate layers:

1. accepted *official observations* from the exact contract station/population,
   which establish the extreme that has already physically occurred;
2. short-horizon official-nowcast/PWS evidence, which is diagnostic forecast
   evidence and never becomes settlement authority merely by being nearby; and
3. an ensemble prediction for *all unresolved portions* of the target local day.

For every ensemble member, the final daily extreme is the min/max of the accepted
observed extreme and that member's prediction over the unresolved portions only.
Modelled values from elapsed periods are never retained as if they could still occur.
Missing/delayed elapsed periods must remain part of ``unresolved_segments`` and the
caller must prove that the remaining-hours evidence covers them.

Nothing here places orders, assigns calibrated probabilities, or establishes final
settlement authority.
"""

import hashlib
import json
import math
from dataclasses import asdict, dataclass
from typing import Iterable

from .weather_only_contracts import DAILY_HIGH, DAILY_LOW


CONDITIONED_EXTREME_VERSION = "weather_conditioned_extreme_v1_exact_observed_plus_unresolved_members"
OFFICIAL_OBSERVATION_ROLE = "OFFICIAL_CONTRACT_OBSERVATION"
OFFICIAL_NOWCAST_ROLE = "OFFICIAL_NOWCAST_RESEARCH"
PWS_ROLE = "PWS_NEAR_TERM_DIAGNOSTIC"


class ConditionedExtremeError(RuntimeError):
    def __init__(self, code: str):
        self.code = str(code)
        super().__init__(self.code)


def _finite(value: object, code: str) -> float:
    if value is None or isinstance(value, bool):
        raise ConditionedExtremeError(code)
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        raise ConditionedExtremeError(code) from None
    if not math.isfinite(number):
        raise ConditionedExtremeError(code)
    return number


def _family(value: str) -> str:
    family = str(value or "")
    if family not in {DAILY_HIGH, DAILY_LOW}:
        raise ConditionedExtremeError("CONDITIONED_FAMILY_UNSUPPORTED")
    return family


def _identity(value: object, code: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ConditionedExtremeError(code)
    return text


def _canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False)


@dataclass(frozen=True, slots=True)
class TimeSegment:
    """Half-open UTC epoch interval [start, end) that is not yet established."""

    start: float
    end: float
    reason: str

    def __post_init__(self) -> None:
        start = _finite(self.start, "CONDITIONED_SEGMENT_TIME_INVALID")
        end = _finite(self.end, "CONDITIONED_SEGMENT_TIME_INVALID")
        if start < 0.0 or end <= start:
            raise ConditionedExtremeError("CONDITIONED_SEGMENT_TIME_INVALID")
        if not str(self.reason or "").strip():
            raise ConditionedExtremeError("CONDITIONED_SEGMENT_REASON_MISSING")

    def as_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class OfficialObservation:
    station: str
    population_id: str
    unit: str
    observed_at: float
    received_at: float
    value: float
    source_revision: str
    source_role: str = OFFICIAL_OBSERVATION_ROLE

    def __post_init__(self) -> None:
        _identity(self.station, "CONDITIONED_OBSERVATION_STATION_MISSING")
        _identity(self.population_id, "CONDITIONED_OBSERVATION_POPULATION_MISSING")
        if self.unit not in {"C", "F"}:
            raise ConditionedExtremeError("CONDITIONED_OBSERVATION_UNIT_INVALID")
        observed = _finite(self.observed_at, "CONDITIONED_OBSERVATION_TIME_INVALID")
        received = _finite(self.received_at, "CONDITIONED_OBSERVATION_TIME_INVALID")
        _finite(self.value, "CONDITIONED_OBSERVATION_VALUE_INVALID")
        if observed < 0.0 or received < 0.0 or observed > received:
            raise ConditionedExtremeError("CONDITIONED_OBSERVATION_TIME_INVALID")
        _identity(self.source_revision, "CONDITIONED_OBSERVATION_REVISION_MISSING")
        if self.source_role != OFFICIAL_OBSERVATION_ROLE:
            raise ConditionedExtremeError("CONDITIONED_OBSERVATION_ROLE_INVALID")

    def as_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class ObservedExtremeState:
    station: str
    population_id: str
    unit: str
    family: str
    target_start: float
    as_of: float
    observation_count: int
    extreme_value: float
    latest_observed_at: float
    evidence_sha256: str
    source_role: str = OFFICIAL_OBSERVATION_ROLE
    settlement_authority: bool = False
    financial_authority: bool = False

    def as_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class NearTermEvidence:
    """Short-horizon evidence used only as a forecast diagnostic/gate.

    A PWS or official nowcast may warn that the unresolved ensemble is implausible,
    but it does not overwrite the accepted official extreme or become settlement
    authority.  Numerical blending/reweighting requires a separately validated and
    frozen policy and is intentionally not hidden in this foundation.
    """

    station: str
    unit: str
    issued_at: float
    received_at: float
    predicted_extreme: float
    horizon_end: float
    source_role: str
    source_id: str

    def __post_init__(self) -> None:
        _identity(self.station, "CONDITIONED_NEAR_TERM_STATION_MISSING")
        if self.unit not in {"C", "F"}:
            raise ConditionedExtremeError("CONDITIONED_NEAR_TERM_UNIT_INVALID")
        if self.source_role not in {OFFICIAL_NOWCAST_ROLE, PWS_ROLE}:
            raise ConditionedExtremeError("CONDITIONED_NEAR_TERM_ROLE_INVALID")
        issued = _finite(self.issued_at, "CONDITIONED_NEAR_TERM_TIME_INVALID")
        received = _finite(self.received_at, "CONDITIONED_NEAR_TERM_TIME_INVALID")
        horizon = _finite(self.horizon_end, "CONDITIONED_NEAR_TERM_TIME_INVALID")
        _finite(self.predicted_extreme, "CONDITIONED_NEAR_TERM_VALUE_INVALID")
        if issued < 0.0 or received < issued or horizon <= issued:
            raise ConditionedExtremeError("CONDITIONED_NEAR_TERM_TIME_INVALID")
        _identity(self.source_id, "CONDITIONED_NEAR_TERM_SOURCE_MISSING")

    @property
    def settlement_authority(self) -> bool:
        return False

    @property
    def financial_authority(self) -> bool:
        return False

    def as_dict(self) -> dict:
        value = asdict(self)
        value["settlement_authority"] = False
        value["financial_authority"] = False
        return value


@dataclass(frozen=True, slots=True)
class RemainingMemberExtreme:
    member_label: str
    extreme_value: float

    def __post_init__(self) -> None:
        _identity(self.member_label, "CONDITIONED_MEMBER_LABEL_MISSING")
        _finite(self.extreme_value, "CONDITIONED_MEMBER_VALUE_INVALID")

    def as_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class RemainingHoursEnsemble:
    station: str
    unit: str
    family: str
    issued_at: float
    received_at: float
    target_end: float
    unresolved_segments: tuple[TimeSegment, ...]
    members: tuple[RemainingMemberExtreme, ...]
    coverage_complete: bool
    provider: str
    provider_run_id: str
    evidence_sha256: str
    calibrated_probability: bool = False
    settlement_authority: bool = False
    financial_authority: bool = False

    def as_dict(self) -> dict:
        value = asdict(self)
        value["unresolved_segments"] = [segment.as_dict() for segment in self.unresolved_segments]
        value["members"] = [member.as_dict() for member in self.members]
        return value


@dataclass(frozen=True, slots=True)
class ConditionedExtremeDistribution:
    version: str
    station: str
    population_id: str
    unit: str
    family: str
    as_of: float
    observed_extreme: float
    member_labels: tuple[str, ...]
    final_member_extremes: tuple[float, ...]
    official_observation_sha256: str
    remaining_hours_sha256: str
    near_term_diagnostics: tuple[dict, ...]
    calibrated_probability: bool = False
    settlement_authority: bool = False
    financial_authority: bool = False

    def as_dict(self) -> dict:
        return asdict(self)


def build_observed_extreme(
    observations: Iterable[OfficialObservation],
    *,
    station: str,
    population_id: str,
    unit: str,
    family: str,
    target_start: float,
    as_of: float,
) -> ObservedExtremeState:
    family = _family(family)
    station_id = _identity(station, "CONDITIONED_STATION_MISSING").upper()
    population = _identity(population_id, "CONDITIONED_POPULATION_MISSING")
    if unit not in {"C", "F"}:
        raise ConditionedExtremeError("CONDITIONED_UNIT_INVALID")
    start = _finite(target_start, "CONDITIONED_TARGET_TIME_INVALID")
    cutoff = _finite(as_of, "CONDITIONED_AS_OF_INVALID")
    if start < 0.0 or cutoff <= start:
        raise ConditionedExtremeError("CONDITIONED_TARGET_TIME_INVALID")

    rows = tuple(observations)
    if not rows:
        raise ConditionedExtremeError("CONDITIONED_OFFICIAL_OBSERVATION_REQUIRED")

    seen_instants: set[float] = set()
    canonical_rows: list[dict] = []
    values: list[float] = []
    latest = start
    for row in rows:
        if not isinstance(row, OfficialObservation):
            raise ConditionedExtremeError("CONDITIONED_OBSERVATION_TYPE_INVALID")
        if row.station.upper() != station_id:
            raise ConditionedExtremeError("CONDITIONED_OBSERVATION_STATION_MISMATCH")
        if row.population_id != population:
            raise ConditionedExtremeError("CONDITIONED_OBSERVATION_POPULATION_MISMATCH")
        if row.unit != unit:
            raise ConditionedExtremeError("CONDITIONED_OBSERVATION_UNIT_MISMATCH")
        if row.observed_at < start or row.observed_at >= cutoff:
            raise ConditionedExtremeError("CONDITIONED_OBSERVATION_OUTSIDE_AS_OF_WINDOW")
        if row.received_at > cutoff:
            raise ConditionedExtremeError("CONDITIONED_OBSERVATION_LOOKAHEAD")
        if row.observed_at in seen_instants:
            raise ConditionedExtremeError("CONDITIONED_OBSERVATION_DUPLICATE_INSTANT")
        seen_instants.add(row.observed_at)
        values.append(float(row.value))
        latest = max(latest, float(row.observed_at))
        canonical_rows.append(row.as_dict())

    extreme = min(values) if family == DAILY_LOW else max(values)
    evidence = {
        "version": CONDITIONED_EXTREME_VERSION,
        "station": station_id,
        "population_id": population,
        "unit": unit,
        "family": family,
        "target_start": start,
        "as_of": cutoff,
        "observations": sorted(canonical_rows, key=lambda value: (value["observed_at"], value["source_revision"])),
    }
    digest = hashlib.sha256(_canonical(evidence).encode("utf-8")).hexdigest()
    return ObservedExtremeState(
        station=station_id,
        population_id=population,
        unit=unit,
        family=family,
        target_start=start,
        as_of=cutoff,
        observation_count=len(rows),
        extreme_value=float(extreme),
        latest_observed_at=latest,
        evidence_sha256=digest,
    )


def build_remaining_hours_ensemble(
    *,
    station: str,
    unit: str,
    family: str,
    issued_at: float,
    received_at: float,
    target_end: float,
    unresolved_segments: Iterable[TimeSegment],
    members: Iterable[RemainingMemberExtreme],
    coverage_complete: bool,
    provider: str,
    provider_run_id: str,
) -> RemainingHoursEnsemble:
    family = _family(family)
    station_id = _identity(station, "CONDITIONED_STATION_MISSING").upper()
    if unit not in {"C", "F"}:
        raise ConditionedExtremeError("CONDITIONED_UNIT_INVALID")
    issued = _finite(issued_at, "CONDITIONED_REMAINING_TIME_INVALID")
    received = _finite(received_at, "CONDITIONED_REMAINING_TIME_INVALID")
    end = _finite(target_end, "CONDITIONED_REMAINING_TIME_INVALID")
    if issued < 0.0 or received < issued or end <= received:
        raise ConditionedExtremeError("CONDITIONED_REMAINING_TIME_INVALID")
    if type(coverage_complete) is not bool or coverage_complete is not True:
        raise ConditionedExtremeError("CONDITIONED_UNRESOLVED_COVERAGE_INCOMPLETE")
    provider_name = _identity(provider, "CONDITIONED_REMAINING_PROVIDER_MISSING")
    run_id = _identity(provider_run_id, "CONDITIONED_REMAINING_RUN_ID_MISSING")

    segments = tuple(unresolved_segments)
    if not segments:
        raise ConditionedExtremeError("CONDITIONED_UNRESOLVED_SEGMENTS_REQUIRED")
    ordered = tuple(sorted(segments, key=lambda segment: (segment.start, segment.end)))
    last_end = -1.0
    for segment in ordered:
        if not isinstance(segment, TimeSegment):
            raise ConditionedExtremeError("CONDITIONED_SEGMENT_TYPE_INVALID")
        if segment.end > end or segment.start < 0.0 or segment.start < last_end:
            raise ConditionedExtremeError("CONDITIONED_SEGMENT_OVERLAP_OR_RANGE_INVALID")
        last_end = segment.end

    member_rows = tuple(members)
    if not member_rows:
        raise ConditionedExtremeError("CONDITIONED_MEMBERS_REQUIRED")
    labels = [member.member_label for member in member_rows]
    if len(labels) != len(set(labels)):
        raise ConditionedExtremeError("CONDITIONED_MEMBER_LABEL_DUPLICATE")

    evidence = {
        "version": CONDITIONED_EXTREME_VERSION,
        "station": station_id,
        "unit": unit,
        "family": family,
        "issued_at": issued,
        "received_at": received,
        "target_end": end,
        "coverage_complete": True,
        "provider": provider_name,
        "provider_run_id": run_id,
        "unresolved_segments": [segment.as_dict() for segment in ordered],
        "members": [member.as_dict() for member in member_rows],
    }
    digest = hashlib.sha256(_canonical(evidence).encode("utf-8")).hexdigest()
    return RemainingHoursEnsemble(
        station=station_id,
        unit=unit,
        family=family,
        issued_at=issued,
        received_at=received,
        target_end=end,
        unresolved_segments=ordered,
        members=member_rows,
        coverage_complete=True,
        provider=provider_name,
        provider_run_id=run_id,
        evidence_sha256=digest,
    )


def near_term_diagnostic(
    evidence: NearTermEvidence,
    *,
    station: str,
    unit: str,
    family: str,
    as_of: float,
    ensemble_extremes: Iterable[float],
    material_difference: float,
) -> dict:
    """Return a non-authoritative disagreement diagnostic for layer 2.

    The diagnostic intentionally does not alter the ensemble member values.  It can
    be used as a fail-closed alert gate until a prospective blending/reweighting
    policy is scientifically validated.
    """
    family = _family(family)
    station_id = _identity(station, "CONDITIONED_STATION_MISSING").upper()
    if evidence.station.upper() != station_id:
        raise ConditionedExtremeError("CONDITIONED_NEAR_TERM_STATION_MISMATCH")
    if evidence.unit != unit:
        raise ConditionedExtremeError("CONDITIONED_NEAR_TERM_UNIT_MISMATCH")
    cutoff = _finite(as_of, "CONDITIONED_AS_OF_INVALID")
    threshold = _finite(material_difference, "CONDITIONED_NEAR_TERM_THRESHOLD_INVALID")
    if threshold < 0.0 or evidence.received_at > cutoff:
        raise ConditionedExtremeError("CONDITIONED_NEAR_TERM_LOOKAHEAD_OR_THRESHOLD_INVALID")
    values = tuple(_finite(value, "CONDITIONED_MEMBER_VALUE_INVALID") for value in ensemble_extremes)
    if not values:
        raise ConditionedExtremeError("CONDITIONED_MEMBERS_REQUIRED")
    ensemble_reference = min(values) if family == DAILY_LOW else max(values)
    difference = abs(float(evidence.predicted_extreme) - ensemble_reference)
    return {
        "source_role": evidence.source_role,
        "source_id": evidence.source_id,
        "predicted_extreme": float(evidence.predicted_extreme),
        "ensemble_reference": float(ensemble_reference),
        "absolute_difference": difference,
        "material_difference": threshold,
        "material_conflict": difference >= threshold,
        "settlement_authority": False,
        "financial_authority": False,
    }


def condition_extremes(
    observed: ObservedExtremeState,
    remaining: RemainingHoursEnsemble,
    *,
    as_of: float,
    near_term: Iterable[tuple[NearTermEvidence, float]] = (),
) -> ConditionedExtremeDistribution:
    """Combine the already-observed extreme with unresolved-period member extremes."""
    cutoff = _finite(as_of, "CONDITIONED_AS_OF_INVALID")
    if observed.station.upper() != remaining.station.upper():
        raise ConditionedExtremeError("CONDITIONED_STATION_MISMATCH")
    if observed.unit != remaining.unit:
        raise ConditionedExtremeError("CONDITIONED_UNIT_MISMATCH")
    if observed.family != remaining.family:
        raise ConditionedExtremeError("CONDITIONED_FAMILY_MISMATCH")
    if abs(observed.as_of - cutoff) > 1e-6:
        raise ConditionedExtremeError("CONDITIONED_OBSERVATION_AS_OF_MISMATCH")
    if remaining.received_at > cutoff:
        raise ConditionedExtremeError("CONDITIONED_REMAINING_LOOKAHEAD")
    if not remaining.coverage_complete:
        raise ConditionedExtremeError("CONDITIONED_UNRESOLVED_COVERAGE_INCOMPLETE")
    if any(segment.start < observed.target_start or segment.end > remaining.target_end for segment in remaining.unresolved_segments):
        raise ConditionedExtremeError("CONDITIONED_SEGMENT_OUTSIDE_TARGET_DAY")

    labels: list[str] = []
    finals: list[float] = []
    unresolved_values: list[float] = []
    for member in remaining.members:
        unresolved = float(member.extreme_value)
        final_value = (
            min(float(observed.extreme_value), unresolved)
            if observed.family == DAILY_LOW
            else max(float(observed.extreme_value), unresolved)
        )
        labels.append(member.member_label)
        finals.append(final_value)
        unresolved_values.append(unresolved)

    diagnostics: list[dict] = []
    for evidence, threshold in near_term:
        diagnostics.append(near_term_diagnostic(
            evidence,
            station=observed.station,
            unit=observed.unit,
            family=observed.family,
            as_of=cutoff,
            ensemble_extremes=unresolved_values,
            material_difference=threshold,
        ))

    return ConditionedExtremeDistribution(
        version=CONDITIONED_EXTREME_VERSION,
        station=observed.station,
        population_id=observed.population_id,
        unit=observed.unit,
        family=observed.family,
        as_of=cutoff,
        observed_extreme=float(observed.extreme_value),
        member_labels=tuple(labels),
        final_member_extremes=tuple(finals),
        official_observation_sha256=observed.evidence_sha256,
        remaining_hours_sha256=remaining.evidence_sha256,
        near_term_diagnostics=tuple(diagnostics),
    )
