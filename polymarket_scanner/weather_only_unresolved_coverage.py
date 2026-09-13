from __future__ import annotations

"""Fail-closed U(t) construction for the same-day three-layer weather model.

The adversarial review requires U(t) to contain *every* eligible part of the target
local day that is not established by accepted official observations.  It must not be
approximated as "everything after the last observation" because a delayed or missing
report can leave a hole earlier in the day.

This module builds an auditable research coverage plan on a frozen UTC-epoch grid:

* fully elapsed grid cells with accepted official rows are recorded as observed cells;
* fully elapsed grid cells without accepted official rows remain explicit elapsed gaps;
* the not-yet-observed remainder of the current grid cell is assigned to Layer 2
  (official-nowcast/PWS diagnostic coverage);
* only future *full* grid cells are assigned to Layer 3 (hourly ensemble paths).

Local-day boundaries are constructed from the IANA timezone independently, so 23- and
25-hour DST days naturally contain 23 or 25 one-hour UTC cells. Repeated wall-clock
labels are distinguished by epoch instant.

The plan deliberately separates a useful research-grid approximation from proof that
the grid exactly represents every report in a contract's eligible observation
population. ``population_alignment_certified`` defaults false.  Until that scientific
and source-semantic proof exists, the plan cannot authorize same-day delivery even if
there are no visible gaps.

Nothing here grants settlement, calibration-label, delivery or financial authority.
"""

import hashlib
import json
import math
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .weather_only_conditioned_extremes import TimeSegment


UNRESOLVED_COVERAGE_VERSION = "weather_unresolved_coverage_v1_local_day_grid_layers"
DEFAULT_GRID_STEP_SECONDS = 3600


class UnresolvedCoverageError(RuntimeError):
    def __init__(self, code: str):
        self.code = str(code)
        super().__init__(self.code)


def _finite(value: object, code: str) -> float:
    if value is None or isinstance(value, bool):
        raise UnresolvedCoverageError(code)
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        raise UnresolvedCoverageError(code) from None
    if not math.isfinite(number):
        raise UnresolvedCoverageError(code)
    return number


def _identity(value: object, code: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise UnresolvedCoverageError(code)
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
        raise UnresolvedCoverageError("UNRESOLVED_COVERAGE_JSON_INVALID") from None


def _sha(value: object) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def local_day_bounds(target_date: date, timezone_name: str) -> tuple[float, float]:
    if type(target_date) is not date:
        raise UnresolvedCoverageError("UNRESOLVED_TARGET_DATE_INVALID")
    if not isinstance(timezone_name, str) or not timezone_name.strip() or timezone_name != timezone_name.strip():
        raise UnresolvedCoverageError("UNRESOLVED_TIMEZONE_INVALID")
    try:
        zone = ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError:
        raise UnresolvedCoverageError("UNRESOLVED_TIMEZONE_INVALID") from None
    start = datetime(target_date.year, target_date.month, target_date.day, tzinfo=zone)
    following = target_date + timedelta(days=1)
    end = datetime(following.year, following.month, following.day, tzinfo=zone)
    return start.timestamp(), end.timestamp()


@dataclass(frozen=True, slots=True)
class CoverageGridCell:
    index: int
    start: float
    end: float
    accepted_observation_times: tuple[float, ...]
    state: str

    def as_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class UnresolvedCoveragePlan:
    version: str
    policy_id: str
    station: str
    population_id: str
    timezone: str
    target_date: str
    target_start: float
    target_end: float
    as_of: float
    grid_step_seconds: int
    grid_cell_count: int
    population_alignment_certified: bool
    cells: tuple[CoverageGridCell, ...]
    elapsed_gap_segments: tuple[TimeSegment, ...]
    near_term_segment: TimeSegment | None
    ensemble_segments: tuple[TimeSegment, ...]
    accepted_observation_count: int
    evidence_sha256: str
    observation_grid_ready: bool = field(init=False)
    requires_near_term_coverage: bool = field(init=False)
    same_day_delivery_authority: bool = field(init=False, default=False)
    settlement_authority: bool = field(init=False, default=False)
    calibration_label_authority: bool = field(init=False, default=False)
    financial_authority: bool = field(init=False, default=False)

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "observation_grid_ready",
            bool(self.population_alignment_certified and not self.elapsed_gap_segments),
        )
        object.__setattr__(self, "requires_near_term_coverage", self.near_term_segment is not None)

    def as_dict(self) -> dict:
        return {
            "version": self.version,
            "policy_id": self.policy_id,
            "station": self.station,
            "population_id": self.population_id,
            "timezone": self.timezone,
            "target_date": self.target_date,
            "target_start": self.target_start,
            "target_end": self.target_end,
            "as_of": self.as_of,
            "grid_step_seconds": self.grid_step_seconds,
            "grid_cell_count": self.grid_cell_count,
            "population_alignment_certified": self.population_alignment_certified,
            "cells": [cell.as_dict() for cell in self.cells],
            "elapsed_gap_segments": [segment.as_dict() for segment in self.elapsed_gap_segments],
            "near_term_segment": None if self.near_term_segment is None else self.near_term_segment.as_dict(),
            "ensemble_segments": [segment.as_dict() for segment in self.ensemble_segments],
            "accepted_observation_count": self.accepted_observation_count,
            "evidence_sha256": self.evidence_sha256,
            "observation_grid_ready": self.observation_grid_ready,
            "requires_near_term_coverage": self.requires_near_term_coverage,
            "same_day_delivery_authority": self.same_day_delivery_authority,
            "settlement_authority": self.settlement_authority,
            "calibration_label_authority": self.calibration_label_authority,
            "financial_authority": self.financial_authority,
        }


def _merge_segments(segments: list[TimeSegment]) -> tuple[TimeSegment, ...]:
    if not segments:
        return ()
    ordered = sorted(segments, key=lambda segment: (float(segment.start), float(segment.end)))
    merged: list[TimeSegment] = [ordered[0]]
    for segment in ordered[1:]:
        prior = merged[-1]
        if abs(float(prior.end) - float(segment.start)) <= 1e-6 and prior.reason == segment.reason:
            merged[-1] = TimeSegment(prior.start, segment.end, prior.reason)
        else:
            merged.append(segment)
    return tuple(merged)


def _evidence_payload(plan: UnresolvedCoveragePlan) -> dict:
    return {
        "version": plan.version,
        "policy_id": plan.policy_id,
        "station": plan.station,
        "population_id": plan.population_id,
        "timezone": plan.timezone,
        "target_date": plan.target_date,
        "target_start": plan.target_start,
        "target_end": plan.target_end,
        "as_of": plan.as_of,
        "grid_step_seconds": plan.grid_step_seconds,
        "grid_cell_count": plan.grid_cell_count,
        "population_alignment_certified": plan.population_alignment_certified,
        "cells": [cell.as_dict() for cell in plan.cells],
        "elapsed_gap_segments": [segment.as_dict() for segment in plan.elapsed_gap_segments],
        "near_term_segment": None if plan.near_term_segment is None else plan.near_term_segment.as_dict(),
        "ensemble_segments": [segment.as_dict() for segment in plan.ensemble_segments],
        "accepted_observation_count": plan.accepted_observation_count,
        "same_day_delivery_authority": plan.same_day_delivery_authority,
        "settlement_authority": plan.settlement_authority,
        "calibration_label_authority": plan.calibration_label_authority,
        "financial_authority": plan.financial_authority,
    }


def build_unresolved_coverage_plan(
    *,
    station: str,
    population_id: str,
    timezone: str,
    target_date: date,
    as_of: float,
    accepted_observation_times: tuple[float, ...] | list[float],
    population_alignment_certified: bool = False,
    grid_step_seconds: int = DEFAULT_GRID_STEP_SECONDS,
    policy_id: str = "WRH_VISIBLE_ROWS_TO_HOURLY_GRID_RESEARCH_V1",
) -> UnresolvedCoveragePlan:
    """Partition the target local day into observed, gap, near-term and ensemble work.

    ``population_alignment_certified`` is intentionally explicit.  A convenient
    hourly grid is not proof that a WRH all-times/hourly table and GEFS sampling share
    the same eligible population.  Current live code should leave it false until that
    mapping is independently validated.
    """
    station_id = _identity(station, "UNRESOLVED_STATION_MISSING").upper()
    population = _identity(population_id, "UNRESOLVED_POPULATION_MISSING")
    policy = _identity(policy_id, "UNRESOLVED_POLICY_ID_MISSING")
    if type(population_alignment_certified) is not bool:
        raise UnresolvedCoverageError("UNRESOLVED_POPULATION_CERTIFICATION_INVALID")
    if isinstance(grid_step_seconds, bool) or not isinstance(grid_step_seconds, int):
        raise UnresolvedCoverageError("UNRESOLVED_GRID_STEP_INVALID")
    if not 300 <= grid_step_seconds <= 21600 or 86400 % grid_step_seconds != 0:
        raise UnresolvedCoverageError("UNRESOLVED_GRID_STEP_INVALID")

    target_start, target_end = local_day_bounds(target_date, timezone)
    cutoff = _finite(as_of, "UNRESOLVED_AS_OF_INVALID")
    if not target_start < cutoff < target_end:
        raise UnresolvedCoverageError("UNRESOLVED_AS_OF_OUTSIDE_TARGET_DAY")
    duration = target_end - target_start
    cells_exact = duration / float(grid_step_seconds)
    if abs(cells_exact - round(cells_exact)) > 1e-9:
        raise UnresolvedCoverageError("UNRESOLVED_LOCAL_DAY_NOT_GRID_DIVISIBLE")
    cell_count = int(round(cells_exact))
    if cell_count <= 0:
        raise UnresolvedCoverageError("UNRESOLVED_GRID_EMPTY")

    raw_times = tuple(accepted_observation_times)
    observation_times: list[float] = []
    seen: set[float] = set()
    for raw in raw_times:
        value = _finite(raw, "UNRESOLVED_OBSERVATION_TIME_INVALID")
        if value < target_start or value >= target_end:
            raise UnresolvedCoverageError("UNRESOLVED_OBSERVATION_OUTSIDE_TARGET_DAY")
        if value > cutoff + 1e-6:
            raise UnresolvedCoverageError("UNRESOLVED_OBSERVATION_LOOKAHEAD")
        canonical = round(value, 6)
        if canonical in seen:
            raise UnresolvedCoverageError("UNRESOLVED_OBSERVATION_DUPLICATE_INSTANT")
        seen.add(canonical)
        observation_times.append(value)
    observation_times.sort()

    observations_by_cell: dict[int, list[float]] = {index: [] for index in range(cell_count)}
    for value in observation_times:
        offset = min(cell_count - 1, int((value - target_start) // grid_step_seconds))
        observations_by_cell[offset].append(value)

    cells: list[CoverageGridCell] = []
    elapsed_gaps: list[TimeSegment] = []
    near_term: TimeSegment | None = None
    ensemble_segments: list[TimeSegment] = []

    current_index = min(cell_count - 1, int((cutoff - target_start) // grid_step_seconds))
    current_start = target_start + current_index * grid_step_seconds
    current_end = min(target_end, current_start + grid_step_seconds)

    for index in range(cell_count):
        start = target_start + index * grid_step_seconds
        end = min(target_end, start + grid_step_seconds)
        obs = tuple(observations_by_cell[index])
        if end <= cutoff + 1e-6:
            if obs:
                state = "ELAPSED_VISIBLE_OFFICIAL_ROWS"
            else:
                state = "ELAPSED_UNRESOLVED_NO_ACCEPTED_ROW"
                elapsed_gaps.append(TimeSegment(start, end, "ELAPSED_NO_ACCEPTED_OFFICIAL_OBSERVATION"))
        elif index == current_index and start < cutoff < end:
            state = "CURRENT_PARTIAL_CELL"
            if not obs and cutoff - start > 1e-6:
                elapsed_gaps.append(TimeSegment(start, cutoff, "ELAPSED_PARTIAL_CELL_WITHOUT_ACCEPTED_OBSERVATION"))
            near_term = TimeSegment(cutoff, end, "LAYER2_NEAR_TERM_REMAINDER")
        else:
            state = "FUTURE_ENSEMBLE_CELL"
            ensemble_segments.append(TimeSegment(start, end, "LAYER3_FUTURE_ENSEMBLE"))
        cells.append(CoverageGridCell(
            index=index,
            start=start,
            end=end,
            accepted_observation_times=obs,
            state=state,
        ))

    # At an exact grid boundary there is no partial current cell. Reserve the first
    # future full cell for Layer 2 so a model retrieved at decision time is never
    # backdated to the boundary it was fetched after. Layer 3 starts one grid later.
    if abs(cutoff - current_start) <= 1e-6:
        first_future_index = current_index
        first_start = target_start + first_future_index * grid_step_seconds
        first_end = min(target_end, first_start + grid_step_seconds)
        near_term = TimeSegment(cutoff, first_end, "LAYER2_NEAR_TERM_REMAINDER")
        ensemble_segments = [
            segment for segment in ensemble_segments
            if float(segment.start) >= first_end - 1e-6
        ]
        cells[first_future_index] = CoverageGridCell(
            index=first_future_index,
            start=first_start,
            end=first_end,
            accepted_observation_times=tuple(observations_by_cell[first_future_index]),
            state="FUTURE_NEAR_TERM_CELL",
        )

    elapsed_tuple = _merge_segments(elapsed_gaps)
    ensemble_tuple = _merge_segments(ensemble_segments)
    shell = UnresolvedCoveragePlan(
        version=UNRESOLVED_COVERAGE_VERSION,
        policy_id=policy,
        station=station_id,
        population_id=population,
        timezone=timezone,
        target_date=target_date.isoformat(),
        target_start=target_start,
        target_end=target_end,
        as_of=cutoff,
        grid_step_seconds=grid_step_seconds,
        grid_cell_count=cell_count,
        population_alignment_certified=population_alignment_certified,
        cells=tuple(cells),
        elapsed_gap_segments=elapsed_tuple,
        near_term_segment=near_term,
        ensemble_segments=ensemble_tuple,
        accepted_observation_count=len(observation_times),
        evidence_sha256="0" * 64,
    )
    return UnresolvedCoveragePlan(
        **{
            name: getattr(shell, name)
            for name, definition in shell.__dataclass_fields__.items()
            if definition.init and name != "evidence_sha256"
        },
        evidence_sha256=_sha(_evidence_payload(shell)),
    )
