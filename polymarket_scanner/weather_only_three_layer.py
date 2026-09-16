from __future__ import annotations

"""Immutable three-layer same-day weather research decision assembler.

This module joins the three independently bounded layers only after their identities
and coverage agree:

1. accepted official O(t), represented by ``ObservedExtremeState``;
2. verified full-segment Layer-2 near-term evidence;
3. a verified 31-member Layer-3 path over future full grid cells only.

Elapsed missing observation cells are fatal. Population/grid alignment must be
explicitly certified. Layer-2 and Layer-3 segments must exactly equal the frozen U(t)
coverage plan. Deep integrity verification rebuilds the deterministic U(t) plan and
the reduced ensemble before either is trusted. The resulting member extremes are then
mapped onto the exact compiled contract partition using the named whole-degree model
mapping policy.

The output is deliberately *research only*. Raw member frequencies are not calibrated
probabilities, and this assembler cannot enable Telegram delivery or financial use.
A later acceptance gate may consume its immutable evidence, but authority cannot be
obtained by simply constructing this object.
"""

import hashlib
import json
import math
from dataclasses import asdict, dataclass, field
from decimal import Decimal, ROUND_HALF_UP

from .weather_only_conditioned_extremes import (
    ConditionedExtremeDistribution,
    ConditionedExtremeError,
    ObservedExtremeState,
    RemainingMemberExtreme,
    build_remaining_hours_ensemble,
    condition_extremes,
)
from .weather_only_conditioned_paths import VerifiedRemainingHoursPath
from .weather_only_contracts import CompiledWeatherEvent, DAILY_HIGH, DAILY_LOW
from .weather_only_forecast import EnsembleMappingPolicy, SUPPORTED_QUANTIZATION
from .weather_only_near_term import (
    VerifiedNearTermCoverage,
    verify_near_term_coverage_integrity,
)
from .weather_only_three_layer_integrity import (
    verify_remaining_path_integrity,
    verify_unresolved_coverage_integrity,
)
from .weather_only_unresolved_coverage import UnresolvedCoveragePlan


THREE_LAYER_VERSION = "weather_three_layer_same_day_v2_deep_integrity_observed_nearterm_hourly_ensemble"
EXPECTED_ENSEMBLE_MEMBERS = 31


class ThreeLayerError(RuntimeError):
    def __init__(self, code: str):
        self.code = str(code)
        super().__init__(self.code)


def _finite(value: object, code: str) -> float:
    if value is None or isinstance(value, bool):
        raise ThreeLayerError(code)
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        raise ThreeLayerError(code) from None
    if not math.isfinite(number):
        raise ThreeLayerError(code)
    return number


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
        raise ThreeLayerError("THREE_LAYER_JSON_INVALID") from None


def _sha(value: object) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _segment_identity(segment) -> tuple[float, float, str]:
    return (round(float(segment.start), 6), round(float(segment.end), 6), str(segment.reason))


def _quantize(value: float, policy: EnsembleMappingPolicy) -> float:
    if policy.quantization != SUPPORTED_QUANTIZATION:
        raise ThreeLayerError("THREE_LAYER_MAPPING_POLICY_UNSUPPORTED")
    return float(Decimal(str(value)).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def _bucket_contains(value: float, bucket) -> bool:
    return (bucket.lower is None or value >= bucket.lower) and (
        bucket.upper is None or value <= bucket.upper
    )


@dataclass(frozen=True, slots=True)
class ThreeLayerBucketFrequency:
    market_id: str
    condition_id: str
    yes_token: str
    no_token: str
    lower: float | None
    upper: float | None
    member_hits: int
    member_count: int
    raw_yes_frequency: float
    raw_no_frequency: float
    calibrated_probability: bool = field(init=False, default=False)
    financial_authority: bool = field(init=False, default=False)

    def as_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class ThreeLayerResearchDecision:
    version: str
    event_id: str
    station: str
    population_id: str
    target_date: str
    timezone: str
    unit: str
    family: str
    as_of: float
    coverage_evidence_sha256: str
    observed_evidence_sha256: str
    near_term_evidence_sha256: str
    ensemble_path_evidence_sha256: str
    mapping_policy_id: str
    quantization: str
    included_control: bool
    member_count: int
    distribution: ConditionedExtremeDistribution
    bucket_frequencies: tuple[ThreeLayerBucketFrequency, ...]
    probability_sum: float
    evidence_sha256: str
    integrity_ready_for_offline_evaluation: bool = field(init=False, default=True)
    calibrated_probability: bool = field(init=False, default=False)
    same_day_delivery_enabled: bool = field(init=False, default=False)
    settlement_authority: bool = field(init=False, default=False)
    calibration_label_authority: bool = field(init=False, default=False)
    financial_authority: bool = field(init=False, default=False)

    def as_dict(self) -> dict:
        return {
            "version": self.version,
            "event_id": self.event_id,
            "station": self.station,
            "population_id": self.population_id,
            "target_date": self.target_date,
            "timezone": self.timezone,
            "unit": self.unit,
            "family": self.family,
            "as_of": self.as_of,
            "coverage_evidence_sha256": self.coverage_evidence_sha256,
            "observed_evidence_sha256": self.observed_evidence_sha256,
            "near_term_evidence_sha256": self.near_term_evidence_sha256,
            "ensemble_path_evidence_sha256": self.ensemble_path_evidence_sha256,
            "mapping_policy_id": self.mapping_policy_id,
            "quantization": self.quantization,
            "included_control": self.included_control,
            "member_count": self.member_count,
            "distribution": self.distribution.as_dict(),
            "bucket_frequencies": [row.as_dict() for row in self.bucket_frequencies],
            "probability_sum": self.probability_sum,
            "evidence_sha256": self.evidence_sha256,
            "integrity_ready_for_offline_evaluation": self.integrity_ready_for_offline_evaluation,
            "calibrated_probability": self.calibrated_probability,
            "same_day_delivery_enabled": self.same_day_delivery_enabled,
            "settlement_authority": self.settlement_authority,
            "calibration_label_authority": self.calibration_label_authority,
            "financial_authority": self.financial_authority,
        }


def _decision_payload(value: ThreeLayerResearchDecision) -> dict:
    payload = value.as_dict()
    payload.pop("evidence_sha256", None)
    return payload


def _validate_contract(compiled: object) -> CompiledWeatherEvent:
    if not isinstance(compiled, CompiledWeatherEvent):
        raise ThreeLayerError("THREE_LAYER_CONTRACT_TYPE_INVALID")
    if compiled.family not in {DAILY_HIGH, DAILY_LOW}:
        raise ThreeLayerError("THREE_LAYER_FAMILY_INVALID")
    if compiled.target_date is None or not compiled.station_hint:
        raise ThreeLayerError("THREE_LAYER_CONTRACT_IDENTITY_INCOMPLETE")
    if (
        not compiled.partition_shape_complete
        or not compiled.exactly_one_outcome_proven
        or not compiled.buckets
        or compiled.financial_authority
    ):
        raise ThreeLayerError("THREE_LAYER_CONTRACT_PARTITION_UNPROVEN")
    if any(not bucket.yes_token or not bucket.no_token for bucket in compiled.buckets):
        raise ThreeLayerError("THREE_LAYER_BUCKET_TOKEN_IDENTITY_INCOMPLETE")
    return compiled


def build_three_layer_research_decision(
    compiled: CompiledWeatherEvent,
    observed: ObservedExtremeState,
    coverage: UnresolvedCoveragePlan,
    near_term: VerifiedNearTermCoverage,
    ensemble_path: VerifiedRemainingHoursPath,
    mapping_policy: EnsembleMappingPolicy,
    *,
    as_of: float,
) -> ThreeLayerResearchDecision:
    contract = _validate_contract(compiled)
    cutoff = _finite(as_of, "THREE_LAYER_AS_OF_INVALID")
    if not isinstance(observed, ObservedExtremeState):
        raise ThreeLayerError("THREE_LAYER_OBSERVED_TYPE_INVALID")
    if not isinstance(mapping_policy, EnsembleMappingPolicy):
        raise ThreeLayerError("THREE_LAYER_MAPPING_POLICY_TYPE_INVALID")

    try:
        coverage = verify_unresolved_coverage_integrity(coverage)
    except Exception as exc:
        code = getattr(exc, "code", type(exc).__name__)
        raise ThreeLayerError(f"THREE_LAYER_COVERAGE_INVALID:{code}") from exc
    try:
        near = verify_near_term_coverage_integrity(near_term)
    except Exception as exc:
        code = getattr(exc, "code", type(exc).__name__)
        raise ThreeLayerError(f"THREE_LAYER_NEAR_TERM_INVALID:{code}") from exc
    try:
        ensemble_path = verify_remaining_path_integrity(ensemble_path)
    except Exception as exc:
        code = getattr(exc, "code", type(exc).__name__)
        raise ThreeLayerError(f"THREE_LAYER_ENSEMBLE_PATH_INVALID:{code}") from exc

    station = str(contract.station_hint).upper()
    if any(
        value.upper() != station
        for value in (observed.station, coverage.station, near.station, ensemble_path.station)
    ):
        raise ThreeLayerError("THREE_LAYER_STATION_MISMATCH")
    if any(value != contract.unit for value in (observed.unit, near.unit, ensemble_path.unit)):
        raise ThreeLayerError("THREE_LAYER_UNIT_MISMATCH")
    if any(value != contract.family for value in (observed.family, near.family, ensemble_path.family)):
        raise ThreeLayerError("THREE_LAYER_FAMILY_MISMATCH")
    if coverage.target_date != contract.target_date.isoformat():
        raise ThreeLayerError("THREE_LAYER_TARGET_DATE_MISMATCH")
    if observed.population_id != coverage.population_id:
        raise ThreeLayerError("THREE_LAYER_POPULATION_MISMATCH")
    if any(
        abs(float(value) - cutoff) > 1e-6
        for value in (observed.as_of, coverage.as_of, near.as_of, ensemble_path.as_of)
    ):
        raise ThreeLayerError("THREE_LAYER_AS_OF_MISMATCH")

    if coverage.elapsed_gap_segments:
        raise ThreeLayerError("THREE_LAYER_ELAPSED_OBSERVATION_GAP")
    if not coverage.population_alignment_certified or not coverage.observation_grid_ready:
        raise ThreeLayerError("THREE_LAYER_POPULATION_ALIGNMENT_UNPROVEN")
    if coverage.near_term_segment is None:
        raise ThreeLayerError("THREE_LAYER_NEAR_TERM_SEGMENT_REQUIRED")
    if _segment_identity(near.segment) != _segment_identity(coverage.near_term_segment):
        raise ThreeLayerError("THREE_LAYER_NEAR_TERM_SEGMENT_MISMATCH")
    expected_ensemble_segments = tuple(
        _segment_identity(segment) for segment in coverage.ensemble_segments
    )
    actual_ensemble_segments = tuple(
        _segment_identity(segment) for segment in ensemble_path.unresolved_segments
    )
    if not expected_ensemble_segments or actual_ensemble_segments != expected_ensemble_segments:
        raise ThreeLayerError("THREE_LAYER_ENSEMBLE_SEGMENT_MISMATCH")
    if abs(float(ensemble_path.target_end) - float(coverage.target_end)) > 1e-6:
        raise ThreeLayerError("THREE_LAYER_TARGET_END_MISMATCH")
    if near.received_at > cutoff + 1e-6 or ensemble_path.received_at > cutoff + 1e-6:
        raise ThreeLayerError("THREE_LAYER_LOOKAHEAD")

    source_members = ensemble_path.ensemble.members
    if len(source_members) != EXPECTED_ENSEMBLE_MEMBERS:
        raise ThreeLayerError("THREE_LAYER_MEMBER_COUNT_MISMATCH")
    labels = tuple(member.member_label for member in source_members)
    if len(set(labels)) != len(labels):
        raise ThreeLayerError("THREE_LAYER_MEMBER_LABEL_DUPLICATE")

    combined_members: list[RemainingMemberExtreme] = []
    for member in source_members:
        source_value = float(member.extreme_value)
        near_value = float(near.predicted_extreme)
        combined = (
            min(near_value, source_value)
            if contract.family == DAILY_LOW
            else max(near_value, source_value)
        )
        combined_members.append(RemainingMemberExtreme(member.member_label, combined))

    try:
        combined_remaining = build_remaining_hours_ensemble(
            station=station,
            unit=contract.unit,
            family=contract.family,
            issued_at=max(float(near.issued_at), float(ensemble_path.issued_at)),
            received_at=max(float(near.received_at), float(ensemble_path.received_at)),
            target_end=float(coverage.target_end),
            unresolved_segments=(near.segment, *ensemble_path.unresolved_segments),
            members=tuple(combined_members),
            coverage_complete=True,
            provider="THREE_LAYER_LAYER2_PLUS_LAYER3",
            provider_run_id=(
                f"near={near.evidence_sha256}|ensemble={ensemble_path.path_evidence_sha256}"
            ),
        )
        distribution = condition_extremes(observed, combined_remaining, as_of=cutoff)
    except ConditionedExtremeError as exc:
        raise ThreeLayerError(f"THREE_LAYER_CONDITIONING_INVALID:{exc.code}") from exc

    start = 0 if mapping_policy.include_control else 1
    selected_labels = distribution.member_labels[start:]
    selected_values = distribution.final_member_extremes[start:]
    if not selected_values or len(selected_labels) != len(selected_values):
        raise ThreeLayerError("THREE_LAYER_SELECTED_MEMBERS_INVALID")
    hits = [0 for _ in contract.buckets]
    for raw in selected_values:
        quantized = _quantize(float(raw), mapping_policy)
        matched = [
            index
            for index, bucket in enumerate(contract.buckets)
            if _bucket_contains(quantized, bucket)
        ]
        if len(matched) != 1:
            raise ThreeLayerError("THREE_LAYER_MEMBER_BUCKET_MAPPING_NOT_EXACTLY_ONE")
        hits[matched[0]] += 1

    count = len(selected_values)
    rows: list[ThreeLayerBucketFrequency] = []
    for bucket, hit_count in zip(contract.buckets, hits):
        yes = hit_count / count
        rows.append(
            ThreeLayerBucketFrequency(
                market_id=bucket.market_id,
                condition_id=bucket.condition_id,
                yes_token=str(bucket.yes_token),
                no_token=str(bucket.no_token),
                lower=bucket.lower,
                upper=bucket.upper,
                member_hits=hit_count,
                member_count=count,
                raw_yes_frequency=yes,
                raw_no_frequency=1.0 - yes,
            )
        )
    probability_sum = sum(row.raw_yes_frequency for row in rows)
    if abs(probability_sum - 1.0) > 1e-12 or sum(hits) != count:
        raise ThreeLayerError("THREE_LAYER_BUCKET_FREQUENCY_SUM_INVALID")

    shell = ThreeLayerResearchDecision(
        version=THREE_LAYER_VERSION,
        event_id=contract.event_id,
        station=station,
        population_id=observed.population_id,
        target_date=contract.target_date.isoformat(),
        timezone=coverage.timezone,
        unit=contract.unit,
        family=contract.family,
        as_of=cutoff,
        coverage_evidence_sha256=coverage.evidence_sha256,
        observed_evidence_sha256=observed.evidence_sha256,
        near_term_evidence_sha256=near.evidence_sha256,
        ensemble_path_evidence_sha256=ensemble_path.path_evidence_sha256,
        mapping_policy_id=mapping_policy.policy_id,
        quantization=mapping_policy.quantization,
        included_control=mapping_policy.include_control,
        member_count=count,
        distribution=distribution,
        bucket_frequencies=tuple(rows),
        probability_sum=probability_sum,
        evidence_sha256="0" * 64,
    )
    return ThreeLayerResearchDecision(
        **{
            name: getattr(shell, name)
            for name, definition in shell.__dataclass_fields__.items()
            if definition.init and name != "evidence_sha256"
        },
        evidence_sha256=_sha(_decision_payload(shell)),
    )


def verify_three_layer_decision_integrity(value: object) -> ThreeLayerResearchDecision:
    if not isinstance(value, ThreeLayerResearchDecision):
        raise ThreeLayerError("THREE_LAYER_DECISION_TYPE_INVALID")
    if value.evidence_sha256 != _sha(_decision_payload(value)):
        raise ThreeLayerError("THREE_LAYER_DECISION_DIGEST_MISMATCH")
    if any((
        value.calibrated_probability,
        value.same_day_delivery_enabled,
        value.settlement_authority,
        value.calibration_label_authority,
        value.financial_authority,
    )):
        raise ThreeLayerError("THREE_LAYER_AUTHORITY_BOUNDARY_BROKEN")
    if abs(value.probability_sum - 1.0) > 1e-12:
        raise ThreeLayerError("THREE_LAYER_BUCKET_FREQUENCY_SUM_INVALID")
    return value
