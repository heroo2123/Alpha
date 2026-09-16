from __future__ import annotations

"""Strict Layer-2 coverage boundary for same-day weather research.

Layer 2 covers the short partial interval between a decision time and the first future
full ensemble grid cell.  A nearby PWS reading or an official-looking feed is not
sufficient by itself: the evidence must explicitly claim a forecast extreme covering
the *whole* requested segment, with source identity, issue/receipt times and an
immutable source digest.

This module does not implement or bless a particular PWS/nowcast provider.  It makes
the acceptance contract explicit so a later provider adapter cannot silently gain
settlement authority.  No current live-paper service enables same-day delivery from
this type.
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


NEAR_TERM_COVERAGE_VERSION = "weather_near_term_coverage_v1_full_segment_extreme"


class NearTermCoverageError(RuntimeError):
    def __init__(self, code: str):
        self.code = str(code)
        super().__init__(self.code)


def _finite(value: object, code: str) -> float:
    if value is None or isinstance(value, bool):
        raise NearTermCoverageError(code)
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        raise NearTermCoverageError(code) from None
    if not math.isfinite(number):
        raise NearTermCoverageError(code)
    return number


def _identity(value: object, code: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise NearTermCoverageError(code)
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
        raise NearTermCoverageError("NEAR_TERM_JSON_INVALID") from None


def _sha(value: object) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class VerifiedNearTermCoverage:
    version: str
    station: str
    unit: str
    family: str
    as_of: float
    segment: TimeSegment
    source_role: str
    source_id: str
    issued_at: float
    received_at: float
    predicted_extreme: float
    source_evidence_sha256: str
    coverage_method: str
    evidence_sha256: str
    research_segment_coverage_complete: bool = field(init=False, default=True)
    calibrated_probability: bool = field(init=False, default=False)
    settlement_authority: bool = field(init=False, default=False)
    calibration_label_authority: bool = field(init=False, default=False)
    financial_authority: bool = field(init=False, default=False)
    same_day_delivery_authority: bool = field(init=False, default=False)

    def as_dict(self) -> dict:
        value = asdict(self)
        value["segment"] = self.segment.as_dict()
        return value


def _payload(value: VerifiedNearTermCoverage) -> dict:
    return {
        "version": value.version,
        "station": value.station,
        "unit": value.unit,
        "family": value.family,
        "as_of": value.as_of,
        "segment": value.segment.as_dict(),
        "source_role": value.source_role,
        "source_id": value.source_id,
        "issued_at": value.issued_at,
        "received_at": value.received_at,
        "predicted_extreme": value.predicted_extreme,
        "source_evidence_sha256": value.source_evidence_sha256,
        "coverage_method": value.coverage_method,
        "research_segment_coverage_complete": value.research_segment_coverage_complete,
        "calibrated_probability": value.calibrated_probability,
        "settlement_authority": value.settlement_authority,
        "calibration_label_authority": value.calibration_label_authority,
        "financial_authority": value.financial_authority,
        "same_day_delivery_authority": value.same_day_delivery_authority,
    }


def verify_near_term_segment_coverage(
    evidence: NearTermEvidence,
    *,
    family: str,
    as_of: float,
    segment: TimeSegment,
    source_evidence_sha256: str,
    coverage_method: str,
    full_segment_extreme_certified: bool,
) -> VerifiedNearTermCoverage:
    """Promote source evidence only to research segment coverage, never authority.

    ``full_segment_extreme_certified`` must come from a reviewed provider adapter that
    knows its returned statistic covers the complete requested segment.  A current
    sensor/PWS observation cannot set it merely because it is fresh.
    """
    if not isinstance(evidence, NearTermEvidence):
        raise NearTermCoverageError("NEAR_TERM_EVIDENCE_TYPE_INVALID")
    if family not in {DAILY_HIGH, DAILY_LOW}:
        raise NearTermCoverageError("NEAR_TERM_FAMILY_INVALID")
    if not isinstance(segment, TimeSegment):
        raise NearTermCoverageError("NEAR_TERM_SEGMENT_TYPE_INVALID")
    if type(full_segment_extreme_certified) is not bool or not full_segment_extreme_certified:
        raise NearTermCoverageError("NEAR_TERM_FULL_SEGMENT_COVERAGE_UNPROVEN")
    cutoff = _finite(as_of, "NEAR_TERM_AS_OF_INVALID")
    if abs(float(segment.start) - cutoff) > 1e-6:
        raise NearTermCoverageError("NEAR_TERM_SEGMENT_MUST_START_AT_AS_OF")
    if evidence.received_at > cutoff + 1e-6 or evidence.issued_at > evidence.received_at + 1e-6:
        raise NearTermCoverageError("NEAR_TERM_LOOKAHEAD")
    if evidence.horizon_end < float(segment.end) - 1e-6:
        raise NearTermCoverageError("NEAR_TERM_HORIZON_INCOMPLETE")
    if evidence.source_role not in {OFFICIAL_NOWCAST_ROLE, PWS_ROLE}:
        raise NearTermCoverageError("NEAR_TERM_ROLE_INVALID")
    source_sha = _identity(source_evidence_sha256, "NEAR_TERM_SOURCE_DIGEST_MISSING").lower()
    if len(source_sha) != 64 or any(ch not in "0123456789abcdef" for ch in source_sha):
        raise NearTermCoverageError("NEAR_TERM_SOURCE_DIGEST_INVALID")
    method = _identity(coverage_method, "NEAR_TERM_COVERAGE_METHOD_MISSING")

    shell = VerifiedNearTermCoverage(
        version=NEAR_TERM_COVERAGE_VERSION,
        station=str(evidence.station).upper(),
        unit=evidence.unit,
        family=family,
        as_of=cutoff,
        segment=segment,
        source_role=evidence.source_role,
        source_id=_identity(evidence.source_id, "NEAR_TERM_SOURCE_ID_MISSING"),
        issued_at=float(evidence.issued_at),
        received_at=float(evidence.received_at),
        predicted_extreme=float(evidence.predicted_extreme),
        source_evidence_sha256=source_sha,
        coverage_method=method,
        evidence_sha256="0" * 64,
    )
    return VerifiedNearTermCoverage(
        **{
            name: getattr(shell, name)
            for name, definition in shell.__dataclass_fields__.items()
            if definition.init and name != "evidence_sha256"
        },
        evidence_sha256=_sha(_payload(shell)),
    )


def verify_near_term_coverage_integrity(value: object) -> VerifiedNearTermCoverage:
    if not isinstance(value, VerifiedNearTermCoverage):
        raise NearTermCoverageError("NEAR_TERM_VERIFIED_TYPE_INVALID")
    if value.evidence_sha256 != _sha(_payload(value)):
        raise NearTermCoverageError("NEAR_TERM_EVIDENCE_DIGEST_MISMATCH")
    if any((
        value.calibrated_probability,
        value.settlement_authority,
        value.calibration_label_authority,
        value.financial_authority,
        value.same_day_delivery_authority,
    )):
        raise NearTermCoverageError("NEAR_TERM_AUTHORITY_BOUNDARY_BROKEN")
    return value
