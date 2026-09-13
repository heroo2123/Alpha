from __future__ import annotations

"""Timestamped Layer-2 path evidence for same-day weather research.

A single fresh PWS reading is not a forecast of the rest of the current interval.
Likewise, a handful of 15-minute model values must not be described as a continuous
physical maximum/minimum.  This module freezes exactly what a reviewed near-term
provider supplied: timestamped model/sensor-path points on a declared sampling grid.

A complete sampled path may be reduced to a *model-path extreme hypothesis* for the
Layer-2 segment.  That reduction is suitable for offline research only and preserves
the sampling-hypothesis ID.  It never claims settlement truth, calibration, continuous
physical coverage or same-day delivery authority.
"""

import hashlib
import json
import math
from dataclasses import asdict, dataclass, field

from .weather_only_conditioned_extremes import (
    NearTermEvidence,
    OFFICIAL_NOWCAST_ROLE,
    PWS_ROLE,
    TimeSegment,
)
from .weather_only_contracts import DAILY_HIGH, DAILY_LOW
from .weather_only_near_term import VerifiedNearTermCoverage, verify_near_term_segment_coverage


NEAR_TERM_PATH_VERSION = "weather_near_term_path_v1_timestamped_sampled_hypothesis"
DEFAULT_MAX_SAMPLE_STEP_SECONDS = 900


class NearTermPathError(RuntimeError):
    def __init__(self, code: str):
        self.code = str(code)
        super().__init__(self.code)


def _finite(value: object, code: str) -> float:
    if value is None or isinstance(value, bool):
        raise NearTermPathError(code)
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        raise NearTermPathError(code) from None
    if not math.isfinite(number):
        raise NearTermPathError(code)
    return number


def _identity(value: object, code: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise NearTermPathError(code)
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
        raise NearTermPathError("NEAR_TERM_PATH_JSON_INVALID") from None


def _sha(value: object) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class NearTermPathPoint:
    valid_at: float
    value: float

    def as_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class VerifiedNearTermPath:
    version: str
    station: str
    unit: str
    family: str
    source_role: str
    source_id: str
    issued_at: float
    received_at: float
    as_of: float
    segment: TimeSegment
    sampling_step_seconds: int
    sampling_hypothesis_id: str
    points: tuple[NearTermPathPoint, ...]
    sampled_extreme: float
    source_payload_sha256: str
    evidence_sha256: str
    sampled_path_complete: bool = field(init=False, default=True)
    continuous_physical_extreme_certified: bool = field(init=False, default=False)
    calibrated_probability: bool = field(init=False, default=False)
    settlement_authority: bool = field(init=False, default=False)
    calibration_label_authority: bool = field(init=False, default=False)
    same_day_delivery_authority: bool = field(init=False, default=False)
    financial_authority: bool = field(init=False, default=False)

    def as_dict(self) -> dict:
        return {
            "version": self.version,
            "station": self.station,
            "unit": self.unit,
            "family": self.family,
            "source_role": self.source_role,
            "source_id": self.source_id,
            "issued_at": self.issued_at,
            "received_at": self.received_at,
            "as_of": self.as_of,
            "segment": self.segment.as_dict(),
            "sampling_step_seconds": self.sampling_step_seconds,
            "sampling_hypothesis_id": self.sampling_hypothesis_id,
            "points": [point.as_dict() for point in self.points],
            "sampled_extreme": self.sampled_extreme,
            "source_payload_sha256": self.source_payload_sha256,
            "evidence_sha256": self.evidence_sha256,
            "sampled_path_complete": self.sampled_path_complete,
            "continuous_physical_extreme_certified": self.continuous_physical_extreme_certified,
            "calibrated_probability": self.calibrated_probability,
            "settlement_authority": self.settlement_authority,
            "calibration_label_authority": self.calibration_label_authority,
            "same_day_delivery_authority": self.same_day_delivery_authority,
            "financial_authority": self.financial_authority,
        }


def _payload(value: VerifiedNearTermPath) -> dict:
    result = value.as_dict()
    result.pop("evidence_sha256", None)
    return result


def _expected_times(segment: TimeSegment, step_seconds: int) -> tuple[float, ...]:
    start = float(segment.start)
    end = float(segment.end)
    if end <= start:
        raise NearTermPathError("NEAR_TERM_PATH_SEGMENT_INVALID")
    # Require a point at decision time and one at segment end. The final interval can
    # be shorter than the named sampling step when a decision occurs off-grid.
    result = [start]
    current = start
    while current + step_seconds < end - 1e-6:
        current += step_seconds
        result.append(current)
    if end - result[-1] > 1e-6:
        result.append(end)
    return tuple(result)


def build_near_term_sample_path(
    *,
    station: str,
    unit: str,
    family: str,
    source_role: str,
    source_id: str,
    issued_at: float,
    received_at: float,
    as_of: float,
    segment: TimeSegment,
    sampling_step_seconds: int,
    sampling_hypothesis_id: str,
    points: tuple[NearTermPathPoint, ...] | list[NearTermPathPoint],
    source_payload_sha256: str,
) -> VerifiedNearTermPath:
    station_id = _identity(station, "NEAR_TERM_PATH_STATION_MISSING").upper()
    if unit not in {"F", "C"}:
        raise NearTermPathError("NEAR_TERM_PATH_UNIT_INVALID")
    if family not in {DAILY_HIGH, DAILY_LOW}:
        raise NearTermPathError("NEAR_TERM_PATH_FAMILY_INVALID")
    if source_role not in {OFFICIAL_NOWCAST_ROLE, PWS_ROLE}:
        raise NearTermPathError("NEAR_TERM_PATH_ROLE_INVALID")
    source = _identity(source_id, "NEAR_TERM_PATH_SOURCE_ID_MISSING")
    hypothesis = _identity(sampling_hypothesis_id, "NEAR_TERM_PATH_HYPOTHESIS_MISSING")
    issued = _finite(issued_at, "NEAR_TERM_PATH_ISSUED_AT_INVALID")
    received = _finite(received_at, "NEAR_TERM_PATH_RECEIVED_AT_INVALID")
    cutoff = _finite(as_of, "NEAR_TERM_PATH_AS_OF_INVALID")
    if issued > received + 1e-6 or received > cutoff + 1e-6:
        raise NearTermPathError("NEAR_TERM_PATH_LOOKAHEAD")
    if not isinstance(segment, TimeSegment) or abs(float(segment.start) - cutoff) > 1e-6:
        raise NearTermPathError("NEAR_TERM_PATH_SEGMENT_MUST_START_AT_AS_OF")
    if isinstance(sampling_step_seconds, bool) or not isinstance(sampling_step_seconds, int):
        raise NearTermPathError("NEAR_TERM_PATH_STEP_INVALID")
    if not 60 <= sampling_step_seconds <= DEFAULT_MAX_SAMPLE_STEP_SECONDS:
        raise NearTermPathError("NEAR_TERM_PATH_STEP_INVALID")
    source_sha = _identity(source_payload_sha256, "NEAR_TERM_PATH_SOURCE_DIGEST_MISSING").lower()
    if len(source_sha) != 64 or any(ch not in "0123456789abcdef" for ch in source_sha):
        raise NearTermPathError("NEAR_TERM_PATH_SOURCE_DIGEST_INVALID")

    rows = tuple(points)
    expected = _expected_times(segment, sampling_step_seconds)
    if len(rows) != len(expected):
        raise NearTermPathError("NEAR_TERM_PATH_COVERAGE_INCOMPLETE")
    values: list[float] = []
    prior = None
    for row, expected_at in zip(rows, expected):
        if not isinstance(row, NearTermPathPoint):
            raise NearTermPathError("NEAR_TERM_PATH_POINT_TYPE_INVALID")
        valid_at = _finite(row.valid_at, "NEAR_TERM_PATH_POINT_TIME_INVALID")
        value = _finite(row.value, "NEAR_TERM_PATH_POINT_VALUE_INVALID")
        if abs(valid_at - expected_at) > 1e-6:
            raise NearTermPathError("NEAR_TERM_PATH_GRID_MISMATCH")
        if prior is not None and valid_at <= prior:
            raise NearTermPathError("NEAR_TERM_PATH_POINT_ORDER_INVALID")
        prior = valid_at
        values.append(value)
    sampled_extreme = min(values) if family == DAILY_LOW else max(values)

    shell = VerifiedNearTermPath(
        version=NEAR_TERM_PATH_VERSION,
        station=station_id,
        unit=unit,
        family=family,
        source_role=source_role,
        source_id=source,
        issued_at=issued,
        received_at=received,
        as_of=cutoff,
        segment=segment,
        sampling_step_seconds=sampling_step_seconds,
        sampling_hypothesis_id=hypothesis,
        points=rows,
        sampled_extreme=sampled_extreme,
        source_payload_sha256=source_sha,
        evidence_sha256="0" * 64,
    )
    return VerifiedNearTermPath(
        **{
            name: getattr(shell, name)
            for name, definition in shell.__dataclass_fields__.items()
            if definition.init and name != "evidence_sha256"
        },
        evidence_sha256=_sha(_payload(shell)),
    )


def verify_near_term_path_integrity(value: object) -> VerifiedNearTermPath:
    if not isinstance(value, VerifiedNearTermPath):
        raise NearTermPathError("NEAR_TERM_PATH_TYPE_INVALID")
    rebuilt = build_near_term_sample_path(
        station=value.station,
        unit=value.unit,
        family=value.family,
        source_role=value.source_role,
        source_id=value.source_id,
        issued_at=value.issued_at,
        received_at=value.received_at,
        as_of=value.as_of,
        segment=value.segment,
        sampling_step_seconds=value.sampling_step_seconds,
        sampling_hypothesis_id=value.sampling_hypothesis_id,
        points=value.points,
        source_payload_sha256=value.source_payload_sha256,
    )
    if rebuilt.evidence_sha256 != value.evidence_sha256 or rebuilt.as_dict() != value.as_dict():
        raise NearTermPathError("NEAR_TERM_PATH_DIGEST_OR_SHAPE_MISMATCH")
    if any((
        value.continuous_physical_extreme_certified,
        value.calibrated_probability,
        value.settlement_authority,
        value.calibration_label_authority,
        value.same_day_delivery_authority,
        value.financial_authority,
    )):
        raise NearTermPathError("NEAR_TERM_PATH_AUTHORITY_BOUNDARY_BROKEN")
    return value


def reduce_sample_path_to_research_coverage(
    value: VerifiedNearTermPath,
) -> VerifiedNearTermCoverage:
    """Reduce a complete sampled path to a Layer-2 model hypothesis.

    The existing scalar boundary requires an explicit full-segment certification. Here
    the meaning is deliberately narrower: the *declared sampled model path* covers the
    full segment grid. It is not a claim that reality between samples is bounded by the
    sampled extreme. The hypothesis ID remains part of the immutable source identity.
    """
    path = verify_near_term_path_integrity(value)
    scalar = NearTermEvidence(
        station=path.station,
        unit=path.unit,
        issued_at=path.issued_at,
        received_at=path.received_at,
        predicted_extreme=path.sampled_extreme,
        horizon_end=float(path.segment.end),
        source_role=path.source_role,
        source_id=f"{path.source_id}|sampled_path={path.sampling_hypothesis_id}",
    )
    return verify_near_term_segment_coverage(
        scalar,
        family=path.family,
        as_of=path.as_of,
        segment=path.segment,
        source_evidence_sha256=path.evidence_sha256,
        coverage_method=f"SAMPLED_MODEL_PATH:{path.sampling_hypothesis_id}",
        full_segment_extreme_certified=True,
    )
