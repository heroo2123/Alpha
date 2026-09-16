from __future__ import annotations

"""Strict unresolved-path authority for the three-layer same-day weather model.

The conditioned-extreme foundation intentionally accepts only *member extremes* for
unresolved periods.  That is useful mathematically but it cannot itself prove that a
caller did not accidentally retain elapsed model hours, omit a missing hour, or reduce
members over different time grids.  This module closes that boundary.

It accepts full member paths on a frozen regular valid-time grid, requires every
member to cover every unresolved grid instant exactly once, rejects any point outside
U(t), derives each member's unresolved extreme internally, and binds the complete path
into evidence before handing a reduced ensemble to ``condition_extremes``.

For a delayed/missing elapsed segment, the forecast run used to fill that uncertainty
must have been issued no later than the segment start.  A new run issued after an
unobserved past period cannot retrospectively manufacture a prospective forecast for
that period.  This is deliberately conservative and may reduce coverage.

No probability produced here is calibrated and no result gains settlement, financial
or delivery authority.  Same-day directional delivery remains disabled elsewhere.
"""

import hashlib
import json
import math
from dataclasses import asdict, dataclass, field
from typing import Iterable

from .weather_only_conditioned_extremes import (
    ConditionedExtremeDistribution,
    ConditionedExtremeError,
    NearTermEvidence,
    ObservedExtremeState,
    RemainingHoursEnsemble,
    RemainingMemberExtreme,
    TimeSegment,
    build_remaining_hours_ensemble,
    condition_extremes,
)
from .weather_only_contracts import DAILY_HIGH, DAILY_LOW
from .weather_only_forecast import OPEN_METEO_GEFS_MODEL


CONDITIONED_PATH_VERSION = "weather_conditioned_path_v1_exact_unresolved_grid"
GEFS_EXPECTED_MEMBER_COUNT = 31
_GRID_TOLERANCE_SECONDS = 1e-6


class ConditionedPathError(RuntimeError):
    def __init__(self, code: str):
        self.code = str(code)
        super().__init__(self.code)


def _finite(value: object, code: str) -> float:
    if value is None or isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ConditionedPathError(code)
    number = float(value)
    if not math.isfinite(number):
        raise ConditionedPathError(code)
    return number


def _identity(value: object, code: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ConditionedPathError(code)
    return text


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
        raise ConditionedPathError("CONDITIONED_PATH_JSON_INVALID") from None


def _sha(value: object) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class MemberPathPoint:
    member_label: str
    valid_at: float
    value: float

    def __post_init__(self) -> None:
        _identity(self.member_label, "CONDITIONED_PATH_MEMBER_LABEL_MISSING")
        valid = _finite(self.valid_at, "CONDITIONED_PATH_VALID_TIME_INVALID")
        if valid < 0.0:
            raise ConditionedPathError("CONDITIONED_PATH_VALID_TIME_INVALID")
        _finite(self.value, "CONDITIONED_PATH_VALUE_INVALID")

    def as_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class PathCoveragePolicy:
    policy_id: str
    grid_step_seconds: int
    grid_anchor: float
    expected_member_count: int

    def __post_init__(self) -> None:
        _identity(self.policy_id, "CONDITIONED_PATH_POLICY_ID_MISSING")
        if isinstance(self.grid_step_seconds, bool) or not isinstance(self.grid_step_seconds, int):
            raise ConditionedPathError("CONDITIONED_PATH_GRID_STEP_INVALID")
        # Weather paths coarser than six hours are not admitted to the same-day lane.
        if not 300 <= self.grid_step_seconds <= 21600:
            raise ConditionedPathError("CONDITIONED_PATH_GRID_STEP_INVALID")
        anchor = _finite(self.grid_anchor, "CONDITIONED_PATH_GRID_ANCHOR_INVALID")
        if anchor < 0.0:
            raise ConditionedPathError("CONDITIONED_PATH_GRID_ANCHOR_INVALID")
        if isinstance(self.expected_member_count, bool) or not isinstance(self.expected_member_count, int):
            raise ConditionedPathError("CONDITIONED_PATH_MEMBER_COUNT_INVALID")
        if self.expected_member_count <= 0:
            raise ConditionedPathError("CONDITIONED_PATH_MEMBER_COUNT_INVALID")

    def as_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class VerifiedRemainingHoursPath:
    version: str
    policy: PathCoveragePolicy
    station: str
    unit: str
    family: str
    as_of: float
    issued_at: float
    received_at: float
    target_end: float
    provider: str
    provider_run_id: str
    unresolved_segments: tuple[TimeSegment, ...]
    expected_valid_times: tuple[float, ...]
    member_labels: tuple[str, ...]
    point_count: int
    path_evidence_sha256: str
    ensemble: RemainingHoursEnsemble
    coverage_complete: bool = field(init=False, default=True)
    calibrated_probability: bool = field(init=False, default=False)
    settlement_authority: bool = field(init=False, default=False)
    financial_authority: bool = field(init=False, default=False)

    def as_dict(self) -> dict:
        return {
            "version": self.version,
            "policy": self.policy.as_dict(),
            "station": self.station,
            "unit": self.unit,
            "family": self.family,
            "as_of": self.as_of,
            "issued_at": self.issued_at,
            "received_at": self.received_at,
            "target_end": self.target_end,
            "provider": self.provider,
            "provider_run_id": self.provider_run_id,
            "unresolved_segments": [segment.as_dict() for segment in self.unresolved_segments],
            "expected_valid_times": list(self.expected_valid_times),
            "member_labels": list(self.member_labels),
            "point_count": self.point_count,
            "path_evidence_sha256": self.path_evidence_sha256,
            "ensemble": self.ensemble.as_dict(),
            "coverage_complete": self.coverage_complete,
            "calibrated_probability": self.calibrated_probability,
            "settlement_authority": self.settlement_authority,
            "financial_authority": self.financial_authority,
        }


@dataclass(frozen=True, slots=True)
class VerifiedConditionedDistribution:
    version: str
    distribution: ConditionedExtremeDistribution
    remaining_path_evidence_sha256: str
    calibrated_probability: bool = field(init=False, default=False)
    settlement_authority: bool = field(init=False, default=False)
    financial_authority: bool = field(init=False, default=False)

    def as_dict(self) -> dict:
        return {
            "version": self.version,
            "distribution": self.distribution.as_dict(),
            "remaining_path_evidence_sha256": self.remaining_path_evidence_sha256,
            "calibrated_probability": self.calibrated_probability,
            "settlement_authority": self.settlement_authority,
            "financial_authority": self.financial_authority,
        }


def _aligned(value: float, policy: PathCoveragePolicy) -> bool:
    offset = (value - float(policy.grid_anchor)) / float(policy.grid_step_seconds)
    return abs(offset - round(offset)) <= _GRID_TOLERANCE_SECONDS / policy.grid_step_seconds


def _ordered_segments(
    unresolved_segments: Iterable[TimeSegment],
    *,
    target_end: float,
    policy: PathCoveragePolicy,
) -> tuple[TimeSegment, ...]:
    segments = tuple(unresolved_segments)
    if not segments:
        raise ConditionedPathError("CONDITIONED_PATH_UNRESOLVED_SEGMENTS_REQUIRED")
    if any(not isinstance(segment, TimeSegment) for segment in segments):
        raise ConditionedPathError("CONDITIONED_PATH_SEGMENT_TYPE_INVALID")
    ordered = tuple(sorted(segments, key=lambda segment: (float(segment.start), float(segment.end))))
    prior_end = -1.0
    for segment in ordered:
        start = float(segment.start)
        end = float(segment.end)
        if start < prior_end - _GRID_TOLERANCE_SECONDS or end > target_end + _GRID_TOLERANCE_SECONDS:
            raise ConditionedPathError("CONDITIONED_PATH_SEGMENT_OVERLAP_OR_RANGE_INVALID")
        if not _aligned(start, policy) or not _aligned(end, policy):
            raise ConditionedPathError("CONDITIONED_PATH_SEGMENT_NOT_GRID_ALIGNED")
        if end - start + _GRID_TOLERANCE_SECONDS < policy.grid_step_seconds:
            raise ConditionedPathError("CONDITIONED_PATH_SEGMENT_SHORTER_THAN_GRID")
        prior_end = end
    return ordered


def _expected_times(segments: tuple[TimeSegment, ...], policy: PathCoveragePolicy) -> tuple[float, ...]:
    values: list[float] = []
    step = float(policy.grid_step_seconds)
    for segment in segments:
        cursor = float(segment.start)
        # Segment bounds are already grid aligned, so repeated addition is stable
        # enough for epoch-second grids. Round only for canonical evidence equality.
        while cursor < float(segment.end) - _GRID_TOLERANCE_SECONDS:
            values.append(round(cursor, 6))
            cursor += step
    if not values or len(values) != len(set(values)):
        raise ConditionedPathError("CONDITIONED_PATH_EXPECTED_GRID_INVALID")
    return tuple(values)


def build_verified_remaining_hours_path(
    *,
    station: str,
    unit: str,
    family: str,
    as_of: float,
    issued_at: float,
    received_at: float,
    target_end: float,
    unresolved_segments: Iterable[TimeSegment],
    points: Iterable[MemberPathPoint],
    provider: str,
    provider_run_id: str,
    policy: PathCoveragePolicy,
) -> VerifiedRemainingHoursPath:
    """Derive member unresolved extremes only after exact U(t) grid coverage proof."""
    station_id = _identity(station, "CONDITIONED_PATH_STATION_MISSING").upper()
    if unit not in {"C", "F"}:
        raise ConditionedPathError("CONDITIONED_PATH_UNIT_INVALID")
    if family not in {DAILY_HIGH, DAILY_LOW}:
        raise ConditionedPathError("CONDITIONED_PATH_FAMILY_INVALID")
    cutoff = _finite(as_of, "CONDITIONED_PATH_AS_OF_INVALID")
    issued = _finite(issued_at, "CONDITIONED_PATH_ISSUED_AT_INVALID")
    received = _finite(received_at, "CONDITIONED_PATH_RECEIVED_AT_INVALID")
    end = _finite(target_end, "CONDITIONED_PATH_TARGET_END_INVALID")
    if issued < 0.0 or received < issued or received > cutoff or cutoff >= end:
        raise ConditionedPathError("CONDITIONED_PATH_TIME_ORDER_INVALID")
    provider_name = _identity(provider, "CONDITIONED_PATH_PROVIDER_MISSING")
    run_id = _identity(provider_run_id, "CONDITIONED_PATH_RUN_ID_MISSING")
    if not isinstance(policy, PathCoveragePolicy):
        raise ConditionedPathError("CONDITIONED_PATH_POLICY_TYPE_INVALID")

    segments = _ordered_segments(unresolved_segments, target_end=end, policy=policy)
    # If a segment has already elapsed by decision time, admitting a model run issued
    # after that segment began would use retrospectively assimilated information as if
    # it had been a prospective path.  Fail closed instead.
    for segment in segments:
        if float(segment.start) < cutoff and issued > float(segment.start) + _GRID_TOLERANCE_SECONDS:
            raise ConditionedPathError("CONDITIONED_PATH_RUN_POSTDATES_UNRESOLVED_SEGMENT")

    expected_times = _expected_times(segments, policy)
    expected_set = set(expected_times)
    rows = tuple(points)
    if not rows:
        raise ConditionedPathError("CONDITIONED_PATH_POINTS_REQUIRED")
    by_member: dict[str, dict[float, float]] = {}
    canonical_points: list[dict] = []
    for row in rows:
        if not isinstance(row, MemberPathPoint):
            raise ConditionedPathError("CONDITIONED_PATH_POINT_TYPE_INVALID")
        label = row.member_label.strip()
        valid = round(float(row.valid_at), 6)
        if valid not in expected_set:
            raise ConditionedPathError("CONDITIONED_PATH_POINT_OUTSIDE_UNRESOLVED_GRID")
        if not _aligned(valid, policy):
            raise ConditionedPathError("CONDITIONED_PATH_POINT_NOT_GRID_ALIGNED")
        member = by_member.setdefault(label, {})
        if valid in member:
            raise ConditionedPathError("CONDITIONED_PATH_DUPLICATE_MEMBER_TIME")
        member[valid] = float(row.value)
        canonical_points.append({"member_label": label, "valid_at": valid, "value": float(row.value)})

    labels = tuple(sorted(by_member))
    if len(labels) != policy.expected_member_count:
        raise ConditionedPathError("CONDITIONED_PATH_MEMBER_COUNT_MISMATCH")
    for label in labels:
        member_times = set(by_member[label])
        if member_times != expected_set:
            missing = expected_set - member_times
            extra = member_times - expected_set
            if missing:
                raise ConditionedPathError("CONDITIONED_PATH_MEMBER_GRID_INCOMPLETE")
            if extra:
                raise ConditionedPathError("CONDITIONED_PATH_POINT_OUTSIDE_UNRESOLVED_GRID")
            raise ConditionedPathError("CONDITIONED_PATH_MEMBER_GRID_MISMATCH")

    member_extremes: list[RemainingMemberExtreme] = []
    for label in labels:
        values = [by_member[label][valid] for valid in expected_times]
        extreme = min(values) if family == DAILY_LOW else max(values)
        member_extremes.append(RemainingMemberExtreme(member_label=label, extreme_value=extreme))

    path_payload = {
        "version": CONDITIONED_PATH_VERSION,
        "policy": policy.as_dict(),
        "station": station_id,
        "unit": unit,
        "family": family,
        "as_of": cutoff,
        "issued_at": issued,
        "received_at": received,
        "target_end": end,
        "provider": provider_name,
        "provider_run_id": run_id,
        "unresolved_segments": [segment.as_dict() for segment in segments],
        "expected_valid_times": list(expected_times),
        "points": sorted(canonical_points, key=lambda row: (row["member_label"], row["valid_at"])),
    }
    path_sha = _sha(path_payload)
    # The reduced ensemble's own digest now binds the path evidence hash through the
    # run identity; consumers cannot swap another reduction without changing lineage.
    bound_run_id = f"{run_id}|path_sha256={path_sha}"
    try:
        ensemble = build_remaining_hours_ensemble(
            station=station_id,
            unit=unit,
            family=family,
            issued_at=issued,
            received_at=received,
            target_end=end,
            unresolved_segments=segments,
            members=tuple(member_extremes),
            coverage_complete=True,
            provider=provider_name,
            provider_run_id=bound_run_id,
        )
    except ConditionedExtremeError as exc:
        raise ConditionedPathError(f"CONDITIONED_PATH_REDUCTION_INVALID:{exc.code}") from exc

    return VerifiedRemainingHoursPath(
        version=CONDITIONED_PATH_VERSION,
        policy=policy,
        station=station_id,
        unit=unit,
        family=family,
        as_of=cutoff,
        issued_at=issued,
        received_at=received,
        target_end=end,
        provider=provider_name,
        provider_run_id=run_id,
        unresolved_segments=segments,
        expected_valid_times=expected_times,
        member_labels=labels,
        point_count=len(rows),
        path_evidence_sha256=path_sha,
        ensemble=ensemble,
    )


def build_gefs_remaining_hours_path(
    *,
    station: str,
    unit: str,
    family: str,
    as_of: float,
    issued_at: float,
    received_at: float,
    target_end: float,
    unresolved_segments: Iterable[TimeSegment],
    points: Iterable[MemberPathPoint],
    provider_run_id: str,
    grid_step_seconds: int,
    grid_anchor: float,
) -> VerifiedRemainingHoursPath:
    """Frozen 31-member GEFS wrapper for the current weather research model."""
    policy = PathCoveragePolicy(
        policy_id="GEFS_31_MEMBER_EXACT_UNRESOLVED_GRID_V1",
        grid_step_seconds=grid_step_seconds,
        grid_anchor=grid_anchor,
        expected_member_count=GEFS_EXPECTED_MEMBER_COUNT,
    )
    return build_verified_remaining_hours_path(
        station=station,
        unit=unit,
        family=family,
        as_of=as_of,
        issued_at=issued_at,
        received_at=received_at,
        target_end=target_end,
        unresolved_segments=unresolved_segments,
        points=points,
        provider=OPEN_METEO_GEFS_MODEL,
        provider_run_id=provider_run_id,
        policy=policy,
    )


def condition_extremes_from_verified_path(
    observed: ObservedExtremeState,
    verified: VerifiedRemainingHoursPath,
    *,
    as_of: float,
    near_term: Iterable[tuple[NearTermEvidence, float]] = (),
) -> VerifiedConditionedDistribution:
    if not isinstance(verified, VerifiedRemainingHoursPath):
        raise ConditionedPathError("CONDITIONED_PATH_VERIFIED_TYPE_INVALID")
    cutoff = _finite(as_of, "CONDITIONED_PATH_AS_OF_INVALID")
    if abs(verified.as_of - cutoff) > _GRID_TOLERANCE_SECONDS:
        raise ConditionedPathError("CONDITIONED_PATH_AS_OF_MISMATCH")
    if f"path_sha256={verified.path_evidence_sha256}" not in verified.ensemble.provider_run_id:
        raise ConditionedPathError("CONDITIONED_PATH_LINEAGE_NOT_BOUND")
    try:
        distribution = condition_extremes(
            observed,
            verified.ensemble,
            as_of=cutoff,
            near_term=near_term,
        )
    except ConditionedExtremeError as exc:
        raise ConditionedPathError(f"CONDITIONED_PATH_DISTRIBUTION_INVALID:{exc.code}") from exc
    return VerifiedConditionedDistribution(
        version=CONDITIONED_PATH_VERSION,
        distribution=distribution,
        remaining_path_evidence_sha256=verified.path_evidence_sha256,
    )
