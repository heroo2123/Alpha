from __future__ import annotations

"""Deep integrity checks for three-layer same-day research artifacts.

The individual constructors are fail-closed, but dataclasses can still be copied with
``dataclasses.replace`` or reconstructed by callers.  This module independently
rebuilds the deterministic coverage plan and reduced remaining-hours ensemble before a
three-layer decision may trust them.

This still does not reconstruct the original raw hourly provider payload; that belongs
in the immutable evidence envelope.  It does prove that the supplied reduced artifact
is self-consistent with its frozen policy, grid, members and digests.
"""

import math
from datetime import date

from .weather_only_conditioned_extremes import build_remaining_hours_ensemble
from .weather_only_conditioned_paths import VerifiedRemainingHoursPath
from .weather_only_unresolved_coverage import (
    UnresolvedCoveragePlan,
    build_unresolved_coverage_plan,
)


class ThreeLayerIntegrityError(RuntimeError):
    def __init__(self, code: str):
        self.code = str(code)
        super().__init__(self.code)


def _finite(value: object, code: str) -> float:
    if value is None or isinstance(value, bool):
        raise ThreeLayerIntegrityError(code)
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        raise ThreeLayerIntegrityError(code) from None
    if not math.isfinite(number):
        raise ThreeLayerIntegrityError(code)
    return number


def verify_unresolved_coverage_integrity(value: object) -> UnresolvedCoveragePlan:
    if not isinstance(value, UnresolvedCoveragePlan):
        raise ThreeLayerIntegrityError("THREE_LAYER_COVERAGE_TYPE_INVALID")
    try:
        target = date.fromisoformat(value.target_date)
    except (TypeError, ValueError):
        raise ThreeLayerIntegrityError("THREE_LAYER_COVERAGE_TARGET_DATE_INVALID") from None

    flattened: list[float] = []
    for expected_index, cell in enumerate(value.cells):
        if cell.index != expected_index:
            raise ThreeLayerIntegrityError("THREE_LAYER_COVERAGE_CELL_INDEX_INVALID")
        if not math.isfinite(float(cell.start)) or not math.isfinite(float(cell.end)):
            raise ThreeLayerIntegrityError("THREE_LAYER_COVERAGE_CELL_TIME_INVALID")
        if float(cell.end) <= float(cell.start):
            raise ThreeLayerIntegrityError("THREE_LAYER_COVERAGE_CELL_TIME_INVALID")
        flattened.extend(float(raw) for raw in cell.accepted_observation_times)

    try:
        rebuilt = build_unresolved_coverage_plan(
            station=value.station,
            population_id=value.population_id,
            timezone=value.timezone,
            target_date=target,
            as_of=_finite(value.as_of, "THREE_LAYER_COVERAGE_AS_OF_INVALID"),
            accepted_observation_times=flattened,
            population_alignment_certified=value.population_alignment_certified,
            grid_step_seconds=value.grid_step_seconds,
            policy_id=value.policy_id,
        )
    except Exception as exc:
        code = getattr(exc, "code", type(exc).__name__)
        raise ThreeLayerIntegrityError(f"THREE_LAYER_COVERAGE_REBUILD_FAILED:{code}") from exc

    if rebuilt.evidence_sha256 != value.evidence_sha256 or rebuilt.as_dict() != value.as_dict():
        raise ThreeLayerIntegrityError("THREE_LAYER_COVERAGE_DIGEST_OR_SHAPE_MISMATCH")
    if any((
        value.same_day_delivery_authority,
        value.settlement_authority,
        value.calibration_label_authority,
        value.financial_authority,
    )):
        raise ThreeLayerIntegrityError("THREE_LAYER_COVERAGE_AUTHORITY_BOUNDARY_BROKEN")
    return value


def verify_remaining_path_integrity(value: object) -> VerifiedRemainingHoursPath:
    if not isinstance(value, VerifiedRemainingHoursPath):
        raise ThreeLayerIntegrityError("THREE_LAYER_REMAINING_PATH_TYPE_INVALID")
    if not value.coverage_complete:
        raise ThreeLayerIntegrityError("THREE_LAYER_REMAINING_PATH_COVERAGE_INCOMPLETE")
    if any((
        value.calibrated_probability,
        value.settlement_authority,
        value.financial_authority,
    )):
        raise ThreeLayerIntegrityError("THREE_LAYER_REMAINING_PATH_AUTHORITY_BOUNDARY_BROKEN")
    if len(value.path_evidence_sha256) != 64 or any(
        ch not in "0123456789abcdef" for ch in value.path_evidence_sha256.lower()
    ):
        raise ThreeLayerIntegrityError("THREE_LAYER_REMAINING_PATH_DIGEST_INVALID")
    if f"path_sha256={value.path_evidence_sha256}" not in value.ensemble.provider_run_id:
        raise ThreeLayerIntegrityError("THREE_LAYER_REMAINING_PATH_LINEAGE_NOT_BOUND")

    expected_points = len(value.expected_valid_times) * len(value.member_labels)
    if value.point_count != expected_points or expected_points <= 0:
        raise ThreeLayerIntegrityError("THREE_LAYER_REMAINING_PATH_POINT_COUNT_INVALID")
    if tuple(member.member_label for member in value.ensemble.members) != value.member_labels:
        raise ThreeLayerIntegrityError("THREE_LAYER_REMAINING_PATH_MEMBER_IDENTITY_MISMATCH")
    if len(set(value.member_labels)) != len(value.member_labels):
        raise ThreeLayerIntegrityError("THREE_LAYER_REMAINING_PATH_MEMBER_DUPLICATE")

    step = float(value.policy.grid_step_seconds)
    anchor = float(value.policy.grid_anchor)
    for valid_at in value.expected_valid_times:
        current = _finite(valid_at, "THREE_LAYER_REMAINING_PATH_VALID_TIME_INVALID")
        offset = (current - anchor) / step
        if abs(offset - round(offset)) > 1e-9:
            raise ThreeLayerIntegrityError("THREE_LAYER_REMAINING_PATH_GRID_ALIGNMENT_INVALID")

    try:
        rebuilt = build_remaining_hours_ensemble(
            station=value.ensemble.station,
            unit=value.ensemble.unit,
            family=value.ensemble.family,
            issued_at=value.ensemble.issued_at,
            received_at=value.ensemble.received_at,
            target_end=value.ensemble.target_end,
            unresolved_segments=value.ensemble.unresolved_segments,
            members=value.ensemble.members,
            coverage_complete=value.ensemble.coverage_complete,
            provider=value.ensemble.provider,
            provider_run_id=value.ensemble.provider_run_id,
        )
    except Exception as exc:
        code = getattr(exc, "code", type(exc).__name__)
        raise ThreeLayerIntegrityError(f"THREE_LAYER_REMAINING_PATH_REBUILD_FAILED:{code}") from exc
    if rebuilt.evidence_sha256 != value.ensemble.evidence_sha256 or rebuilt.as_dict() != value.ensemble.as_dict():
        raise ThreeLayerIntegrityError("THREE_LAYER_REMAINING_PATH_ENSEMBLE_DIGEST_MISMATCH")

    if (
        value.station != value.ensemble.station
        or value.unit != value.ensemble.unit
        or value.family != value.ensemble.family
        or abs(float(value.issued_at) - float(value.ensemble.issued_at)) > 1e-6
        or abs(float(value.received_at) - float(value.ensemble.received_at)) > 1e-6
        or abs(float(value.target_end) - float(value.ensemble.target_end)) > 1e-6
        or value.unresolved_segments != value.ensemble.unresolved_segments
    ):
        raise ThreeLayerIntegrityError("THREE_LAYER_REMAINING_PATH_REDUCTION_IDENTITY_MISMATCH")
    return value
