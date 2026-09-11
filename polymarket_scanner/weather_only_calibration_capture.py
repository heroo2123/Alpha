from __future__ import annotations

"""Prospective rule + forecast capture and exact WRH calibration settlement bridge.

This is the high-authority calibration path for NWS/WRH temperature markets.  It
carries forward several hardening lessons from the full scanner:

* contract rules and the complete bucket partition are frozen before resolution;
* the forecast must map exactly to that same frozen partition;
* only one deterministic prospective prediction is selected per event;
* WRH finality is re-certified from the two original source snapshots under one
  frozen polling policy rather than trusting a caller-supplied authority boolean;
* the resulting exact settlement label is bound to the prospective capture, frozen
  rules, finality evidence, target value and winning market;
* calibration authority never implies financial/trading authority.

Lower-level prediction/label helpers remain useful test primitives, but production
calibration should enter through this module so a post-resolution rule edit or an
arbitrary exact-label envelope cannot silently become training evidence.
"""

import hashlib
import json
import math
import re
from dataclasses import asdict, dataclass, field
from datetime import date

from .weather_only_calibration import ProbabilityCalibrationSample
from .weather_only_contracts import (
    DAILY_HIGH,
    DAILY_LOW,
    SOURCE_NWS_WRH,
    CompiledWeatherEvent,
    WeatherBucket,
    compile_weather_event,
)
from .weather_only_forecast import EnsembleBucketForecast
from .weather_only_predictions import (
    ExactBucketSettlementLabel,
    ProspectiveBucketPrediction,
    ProspectiveSelectionPolicy,
    WeatherPredictionError,
    build_exact_bucket_settlement_label,
    calibration_sample_from_exact_label,
    select_prospective_bucket_prediction,
)
from .weather_only_rules import (
    RULE_AUTHORITY_VERSION,
    TemperatureRuleAuthority,
    apply_rule_authority,
    compile_temperature_rule_authority,
)
from .weather_only_wrh import WRHSourceError, WRHSourceSnapshot
from .weather_only_wrh_finality import (
    WRH_FINALITY_ADAPTER_VERSION,
    WRH_FINALITY_SOURCE_ROLE,
    WRHFinalityPolicy,
    WRHFinalizedRuleState,
    certify_wrh_first_following_transition,
)


PROSPECTIVE_RULE_EVIDENCE_VERSION = "weather_nws_rule_evidence_v1_frozen_before_resolution"
PROSPECTIVE_CALIBRATION_CAPTURE_VERSION = "weather_calibration_capture_v1_forecast_plus_rule_partition"
WRH_SETTLEMENT_BRIDGE_VERSION = "weather_wrh_exact_settlement_bridge_v1_fixed_finality_policy"

WRH_CALIBRATION_FINALITY_POLICY = WRHFinalityPolicy(
    policy_id="wrh_calibration_first_following_bracket_v1_gap120_age120",
    max_transition_gap_seconds=120,
    max_following_row_age_seconds=120,
)


class WeatherCalibrationCaptureError(RuntimeError):
    def __init__(self, code: str):
        self.code = str(code)
        super().__init__(self.code)


def _hash_payload(payload: object) -> str:
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    ).hexdigest()


def _sha256(value: object, code: str) -> str:
    text = str(value or "").strip().lower()
    if not re.fullmatch(r"[0-9a-f]{64}", text):
        raise WeatherCalibrationCaptureError(code)
    return text


def _finite_timestamp(value: object, code: str) -> float:
    if value is None or isinstance(value, bool) or not isinstance(value, (int, float)):
        raise WeatherCalibrationCaptureError(code)
    number = float(value)
    if not math.isfinite(number) or number < 0.0:
        raise WeatherCalibrationCaptureError(code)
    return number


def _canonical_rule_source_payload(event: dict) -> dict:
    if not isinstance(event, dict):
        raise WeatherCalibrationCaptureError("RULE_EVENT_PAYLOAD_INVALID")
    rows = []
    for row in event.get("markets") or []:
        if not isinstance(row, dict):
            continue
        rows.append({
            "id": str(row.get("id") or ""),
            "conditionId": str(row.get("conditionId") or ""),
            "question": str(row.get("question") or ""),
            "description": str(row.get("description") or ""),
            "resolutionSource": str(row.get("resolutionSource") or ""),
        })
    rows.sort(key=lambda row: (row["id"], row["conditionId"], row["question"]))
    return {
        "id": str(event.get("id") or ""),
        "title": str(event.get("title") or ""),
        "description": str(event.get("description") or ""),
        "resolutionSource": str(event.get("resolutionSource") or ""),
        "markets": rows,
    }


@dataclass(frozen=True, slots=True)
class FrozenRuleBucket:
    market_id: str
    condition_id: str
    lower: float | None
    upper: float | None
    unit: str

    def as_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class ProspectiveNWSRuleEvidence:
    evidence_version: str
    event_id: str
    station: str
    target_date: date
    family: str
    unit: str
    source_family: str
    compiler_version: str
    rule_authority_version: str
    rule_profile: str
    observation_population: str
    precision: str
    fallback_policy: str
    finality_policy: str
    correction_policy: str
    no_data_outcome: str
    bucket_partition: tuple[FrozenRuleBucket, ...]
    source_rules_sha256: str
    captured_at: float
    rule_evidence_sha256: str
    rule_semantics_proven: bool = field(init=False, default=True)
    exactly_one_outcome_proven: bool = field(init=False, default=True)
    financial_authority: bool = field(init=False, default=False)

    def as_dict(self) -> dict:
        value = asdict(self)
        value["target_date"] = self.target_date.isoformat()
        value["bucket_partition"] = [bucket.as_dict() for bucket in self.bucket_partition]
        return value


@dataclass(frozen=True, slots=True)
class ProspectiveWeatherCalibrationCapture:
    capture_version: str
    prediction: ProspectiveBucketPrediction
    rule_evidence: ProspectiveNWSRuleEvidence
    captured_at: float
    capture_evidence_sha256: str
    financial_authority: bool = field(init=False, default=False)

    def as_dict(self) -> dict:
        return {
            "capture_version": self.capture_version,
            "prediction": self.prediction.as_dict(),
            "rule_evidence": self.rule_evidence.as_dict(),
            "captured_at": self.captured_at,
            "capture_evidence_sha256": self.capture_evidence_sha256,
            "financial_authority": self.financial_authority,
        }


@dataclass(frozen=True, slots=True)
class WRHExactSettlementEvidence:
    bridge_version: str
    capture_evidence_sha256: str
    finality_policy_id: str
    max_transition_gap_seconds: int
    max_following_row_age_seconds: int
    finality_state: WRHFinalizedRuleState
    target_value_f: int
    winning_market_id: str
    source_evidence_sha256: str
    label: ExactBucketSettlementLabel
    bridge_evidence_sha256: str
    calibration_label_authority: bool = field(init=False, default=True)
    financial_authority: bool = field(init=False, default=False)

    def as_dict(self) -> dict:
        return {
            "bridge_version": self.bridge_version,
            "capture_evidence_sha256": self.capture_evidence_sha256,
            "finality_policy_id": self.finality_policy_id,
            "max_transition_gap_seconds": self.max_transition_gap_seconds,
            "max_following_row_age_seconds": self.max_following_row_age_seconds,
            "finality_state": self.finality_state.as_dict(),
            "target_value_f": self.target_value_f,
            "winning_market_id": self.winning_market_id,
            "source_evidence_sha256": self.source_evidence_sha256,
            "label": self.label.as_dict(),
            "bridge_evidence_sha256": self.bridge_evidence_sha256,
            "calibration_label_authority": self.calibration_label_authority,
            "financial_authority": self.financial_authority,
        }


def _bucket_from_compiled(bucket: WeatherBucket, unit: str) -> FrozenRuleBucket:
    return FrozenRuleBucket(
        market_id=str(bucket.market_id),
        condition_id=str(bucket.condition_id),
        lower=bucket.lower,
        upper=bucket.upper,
        unit=unit,
    )


def _sorted_rule_buckets(compiled: CompiledWeatherEvent) -> tuple[FrozenRuleBucket, ...]:
    if compiled.unit is None:
        raise WeatherCalibrationCaptureError("RULE_UNIT_UNRESOLVED")
    values = tuple(_bucket_from_compiled(bucket, compiled.unit) for bucket in compiled.buckets)
    return tuple(sorted(values, key=lambda row: row.market_id))


def _rule_digest_payload(evidence: ProspectiveNWSRuleEvidence) -> dict:
    return {
        "evidence_version": evidence.evidence_version,
        "event_id": evidence.event_id,
        "station": evidence.station,
        "target_date": evidence.target_date.isoformat(),
        "family": evidence.family,
        "unit": evidence.unit,
        "source_family": evidence.source_family,
        "compiler_version": evidence.compiler_version,
        "rule_authority_version": evidence.rule_authority_version,
        "rule_profile": evidence.rule_profile,
        "observation_population": evidence.observation_population,
        "precision": evidence.precision,
        "fallback_policy": evidence.fallback_policy,
        "finality_policy": evidence.finality_policy,
        "correction_policy": evidence.correction_policy,
        "no_data_outcome": evidence.no_data_outcome,
        "bucket_partition": [bucket.as_dict() for bucket in evidence.bucket_partition],
        "source_rules_sha256": evidence.source_rules_sha256,
        "captured_at": evidence.captured_at,
    }


def _capture_digest_payload(capture: ProspectiveWeatherCalibrationCapture) -> dict:
    return {
        "capture_version": capture.capture_version,
        "prediction_evidence_sha256": capture.prediction.prediction_evidence_sha256,
        "rule_evidence_sha256": capture.rule_evidence.rule_evidence_sha256,
        "captured_at": capture.captured_at,
    }


def _bridge_source_payload(
    capture: ProspectiveWeatherCalibrationCapture,
    finality: WRHFinalizedRuleState,
    *,
    target_value_f: int,
    winning_market_id: str,
) -> dict:
    return {
        "bridge_version": WRH_SETTLEMENT_BRIDGE_VERSION,
        "capture_evidence_sha256": capture.capture_evidence_sha256,
        "prediction_evidence_sha256": capture.prediction.prediction_evidence_sha256,
        "rule_evidence_sha256": capture.rule_evidence.rule_evidence_sha256,
        "finality_adapter": finality.adapter,
        "finality_source_role": finality.source_role,
        "finality_evidence_sha256": finality.finality_evidence_sha256,
        "finality_policy": {
            "policy_id": WRH_CALIBRATION_FINALITY_POLICY.policy_id,
            "max_transition_gap_seconds": WRH_CALIBRATION_FINALITY_POLICY.max_transition_gap_seconds,
            "max_following_row_age_seconds": WRH_CALIBRATION_FINALITY_POLICY.max_following_row_age_seconds,
        },
        "event_id": capture.prediction.event_id,
        "market_id": capture.prediction.market_id,
        "station": capture.prediction.station,
        "target_date": capture.prediction.target_date.isoformat(),
        "family": capture.prediction.family,
        "target_value_f": target_value_f,
        "winning_market_id": winning_market_id,
    }


def _bridge_digest_payload(evidence: WRHExactSettlementEvidence) -> dict:
    return {
        "bridge_version": evidence.bridge_version,
        "capture_evidence_sha256": evidence.capture_evidence_sha256,
        "finality_policy_id": evidence.finality_policy_id,
        "max_transition_gap_seconds": evidence.max_transition_gap_seconds,
        "max_following_row_age_seconds": evidence.max_following_row_age_seconds,
        "finality_evidence_sha256": evidence.finality_state.finality_evidence_sha256,
        "target_value_f": evidence.target_value_f,
        "winning_market_id": evidence.winning_market_id,
        "source_evidence_sha256": evidence.source_evidence_sha256,
        "label_evidence_sha256": evidence.label.label_evidence_sha256,
    }


def _validate_rule_authority(compiled: CompiledWeatherEvent, authority: TemperatureRuleAuthority) -> None:
    if compiled.source_family != SOURCE_NWS_WRH:
        raise WeatherCalibrationCaptureError("RULE_SOURCE_NOT_NWS_WRH")
    if compiled.family not in {DAILY_HIGH, DAILY_LOW}:
        raise WeatherCalibrationCaptureError("RULE_FAMILY_UNSUPPORTED")
    if compiled.unit != "F":
        raise WeatherCalibrationCaptureError("RULE_UNIT_NOT_FAHRENHEIT")
    if compiled.target_date is None:
        raise WeatherCalibrationCaptureError("RULE_TARGET_DATE_UNRESOLVED")
    station = str(compiled.station_hint or "").strip().upper()
    if not re.fullmatch(r"[A-Z0-9]{4}", station):
        raise WeatherCalibrationCaptureError("RULE_STATION_UNRESOLVED")
    if not compiled.partition_shape_complete:
        raise WeatherCalibrationCaptureError("RULE_BUCKET_PARTITION_UNPROVEN")
    if authority.version != RULE_AUTHORITY_VERSION:
        raise WeatherCalibrationCaptureError("RULE_AUTHORITY_VERSION_MISMATCH")
    if not authority.rule_semantics_proven or not authority.exactly_one_outcome_proven:
        raise WeatherCalibrationCaptureError("RULE_AUTHORITY_NOT_PROVEN")
    if authority.observation_population != "WRH_HOURLY_DATA":
        raise WeatherCalibrationCaptureError("RULE_OBSERVATION_POPULATION_NOT_HOURLY")
    if authority.precision != "WHOLE_DEGREE_F":
        raise WeatherCalibrationCaptureError("RULE_PRECISION_NOT_WHOLE_F")
    if authority.finality_policy != "FIRST_FOLLOWING_DATE_DATAPOINT_OR_NEXT_DAY_2359_ET":
        raise WeatherCalibrationCaptureError("RULE_FINALITY_POLICY_MISMATCH")
    if authority.correction_policy != "ACCEPT_REVISIONS_UNTIL_FIRST_FOLLOWING_DATE_DATAPOINT":
        raise WeatherCalibrationCaptureError("RULE_CORRECTION_POLICY_MISMATCH")
    if authority.fallback_policy != "WEATHER_UNDERGROUND_IF_WRH_UNAVAILABLE_BY_NEXT_DAY_2359_ET":
        raise WeatherCalibrationCaptureError("RULE_FALLBACK_POLICY_MISMATCH")
    if authority.no_data_outcome != "LOWEST_BRACKET":
        raise WeatherCalibrationCaptureError("RULE_NO_DATA_POLICY_MISMATCH")
    upgraded = apply_rule_authority(compiled, authority)
    if not upgraded.exactly_one_outcome_proven or upgraded.financial_authority:
        raise WeatherCalibrationCaptureError("RULE_STRUCTURAL_UPGRADE_FAILED")
    if compiled.financial_authority or authority.financial_authority:
        raise WeatherCalibrationCaptureError("RULE_FINANCIAL_AUTHORITY_BOUNDARY_BROKEN")


def capture_prospective_weather_calibration_candidate(
    event: dict,
    forecast: EnsembleBucketForecast,
    *,
    selection_policy: ProspectiveSelectionPolicy,
    captured_at: float,
) -> ProspectiveWeatherCalibrationCapture:
    """Freeze settlement rules and one forecast candidate before event resolution."""
    captured = _finite_timestamp(captured_at, "CAPTURE_TIMESTAMP_INVALID")
    compiled = compile_weather_event(event)
    authority = compile_temperature_rule_authority(event, compiled)
    _validate_rule_authority(compiled, authority)

    if not compiled.shadow_supported:
        raise WeatherCalibrationCaptureError("RULE_EVENT_NOT_SHADOW_SUPPORTED_AT_CAPTURE")
    if forecast.event_id != compiled.event_id:
        raise WeatherCalibrationCaptureError("CAPTURE_EVENT_ID_MISMATCH")
    if forecast.station.strip().upper() != str(compiled.station_hint).strip().upper():
        raise WeatherCalibrationCaptureError("CAPTURE_STATION_MISMATCH")
    if forecast.target_date != compiled.target_date:
        raise WeatherCalibrationCaptureError("CAPTURE_TARGET_DATE_MISMATCH")
    if forecast.family != compiled.family:
        raise WeatherCalibrationCaptureError("CAPTURE_FAMILY_MISMATCH")
    if forecast.unit != compiled.unit:
        raise WeatherCalibrationCaptureError("CAPTURE_UNIT_MISMATCH")

    rule_buckets = _sorted_rule_buckets(compiled)
    forecast_buckets = tuple(sorted(
        (
            FrozenRuleBucket(
                market_id=str(row.market_id),
                condition_id=str(row.condition_id),
                lower=row.lower,
                upper=row.upper,
                unit=forecast.unit,
            )
            for row in forecast.bucket_frequencies
        ),
        key=lambda row: row.market_id,
    ))
    if forecast_buckets != rule_buckets:
        raise WeatherCalibrationCaptureError("CAPTURE_FORECAST_BUCKET_PARTITION_MISMATCH")

    prediction = select_prospective_bucket_prediction(
        forecast,
        policy=selection_policy,
        captured_at=captured,
    )
    selected_rule = next((bucket for bucket in rule_buckets if bucket.market_id == prediction.market_id), None)
    if selected_rule is None or selected_rule.condition_id != prediction.condition_id:
        raise WeatherCalibrationCaptureError("CAPTURE_SELECTED_BUCKET_IDENTITY_MISMATCH")

    source_rules_sha = _hash_payload(_canonical_rule_source_payload(event))
    rule_shell = ProspectiveNWSRuleEvidence(
        evidence_version=PROSPECTIVE_RULE_EVIDENCE_VERSION,
        event_id=compiled.event_id,
        station=str(compiled.station_hint).strip().upper(),
        target_date=compiled.target_date,
        family=compiled.family,
        unit=compiled.unit,
        source_family=compiled.source_family,
        compiler_version=compiled.compiler_version,
        rule_authority_version=authority.version,
        rule_profile=authority.profile,
        observation_population=str(authority.observation_population),
        precision=str(authority.precision),
        fallback_policy=str(authority.fallback_policy),
        finality_policy=str(authority.finality_policy),
        correction_policy=str(authority.correction_policy),
        no_data_outcome=str(authority.no_data_outcome),
        bucket_partition=rule_buckets,
        source_rules_sha256=source_rules_sha,
        captured_at=captured,
        rule_evidence_sha256="0" * 64,
    )
    rule_evidence = ProspectiveNWSRuleEvidence(
        **{
            name: getattr(rule_shell, name)
            for name, definition in rule_shell.__dataclass_fields__.items()
            if definition.init and name != "rule_evidence_sha256"
        },
        rule_evidence_sha256=_hash_payload(_rule_digest_payload(rule_shell)),
    )

    capture_shell = ProspectiveWeatherCalibrationCapture(
        capture_version=PROSPECTIVE_CALIBRATION_CAPTURE_VERSION,
        prediction=prediction,
        rule_evidence=rule_evidence,
        captured_at=captured,
        capture_evidence_sha256="0" * 64,
    )
    return ProspectiveWeatherCalibrationCapture(
        capture_version=capture_shell.capture_version,
        prediction=capture_shell.prediction,
        rule_evidence=capture_shell.rule_evidence,
        captured_at=capture_shell.captured_at,
        capture_evidence_sha256=_hash_payload(_capture_digest_payload(capture_shell)),
    )


def _validate_capture(capture: object) -> ProspectiveWeatherCalibrationCapture:
    if not isinstance(capture, ProspectiveWeatherCalibrationCapture):
        raise WeatherCalibrationCaptureError("CAPTURE_TYPE_INVALID")
    if capture.capture_version != PROSPECTIVE_CALIBRATION_CAPTURE_VERSION:
        raise WeatherCalibrationCaptureError("CAPTURE_VERSION_MISMATCH")
    if capture.financial_authority is not False:
        raise WeatherCalibrationCaptureError("CAPTURE_FINANCIAL_AUTHORITY_BOUNDARY_BROKEN")
    _finite_timestamp(capture.captured_at, "CAPTURE_TIMESTAMP_INVALID")
    rule = capture.rule_evidence
    if not isinstance(rule, ProspectiveNWSRuleEvidence):
        raise WeatherCalibrationCaptureError("CAPTURE_RULE_EVIDENCE_TYPE_INVALID")
    if rule.evidence_version != PROSPECTIVE_RULE_EVIDENCE_VERSION:
        raise WeatherCalibrationCaptureError("CAPTURE_RULE_EVIDENCE_VERSION_MISMATCH")
    if rule.financial_authority is not False or not rule.rule_semantics_proven or not rule.exactly_one_outcome_proven:
        raise WeatherCalibrationCaptureError("CAPTURE_RULE_AUTHORITY_BOUNDARY_BROKEN")
    if rule.source_family != SOURCE_NWS_WRH or rule.unit != "F" or rule.family not in {DAILY_HIGH, DAILY_LOW}:
        raise WeatherCalibrationCaptureError("CAPTURE_RULE_PROFILE_UNSUPPORTED")
    if rule.captured_at != capture.captured_at or capture.prediction.captured_at != capture.captured_at:
        raise WeatherCalibrationCaptureError("CAPTURE_COMPONENT_TIMESTAMP_MISMATCH")
    if (
        rule.event_id != capture.prediction.event_id
        or rule.station != capture.prediction.station.strip().upper()
        or rule.target_date != capture.prediction.target_date
        or rule.family != capture.prediction.family
        or rule.unit != capture.prediction.unit
    ):
        raise WeatherCalibrationCaptureError("CAPTURE_COMPONENT_IDENTITY_MISMATCH")
    supplied_rule = _sha256(rule.rule_evidence_sha256, "CAPTURE_RULE_EVIDENCE_SHA_INVALID")
    if supplied_rule != _hash_payload(_rule_digest_payload(rule)):
        raise WeatherCalibrationCaptureError("CAPTURE_RULE_EVIDENCE_DIGEST_MISMATCH")
    supplied_capture = _sha256(capture.capture_evidence_sha256, "CAPTURE_EVIDENCE_SHA_INVALID")
    if supplied_capture != _hash_payload(_capture_digest_payload(capture)):
        raise WeatherCalibrationCaptureError("CAPTURE_EVIDENCE_DIGEST_MISMATCH")
    # Reuse the existing prediction validator through a zero-authority synthetic join
    # is undesirable; direct structural invariants needed here are already bound by the
    # prediction evidence digest and are fully checked again by the final calibration
    # join.  We still require the selected market/condition to exist in the frozen rule
    # partition so a post-capture object swap cannot choose a new outcome.
    selected = next((row for row in rule.bucket_partition if row.market_id == capture.prediction.market_id), None)
    if selected is None or selected.condition_id != capture.prediction.condition_id:
        raise WeatherCalibrationCaptureError("CAPTURE_SELECTED_BUCKET_NOT_IN_FROZEN_RULES")
    return capture


def _bucket_contains(bucket: FrozenRuleBucket, value: int) -> bool:
    if bucket.lower is not None and value < bucket.lower:
        return False
    if bucket.upper is not None and value > bucket.upper:
        return False
    return True


def build_wrh_exact_settlement_evidence(
    capture: ProspectiveWeatherCalibrationCapture,
    previous_snapshot: WRHSourceSnapshot,
    current_snapshot: WRHSourceSnapshot,
) -> WRHExactSettlementEvidence:
    """Resolve the frozen candidate from prospectively bracketed WRH finality evidence."""
    frozen = _validate_capture(capture)
    try:
        finality = certify_wrh_first_following_transition(
            previous_snapshot,
            current_snapshot,
            policy=WRH_CALIBRATION_FINALITY_POLICY,
        )
    except WRHSourceError:
        raise

    if finality.adapter != WRH_FINALITY_ADAPTER_VERSION or finality.source_role != WRH_FINALITY_SOURCE_ROLE:
        raise WeatherCalibrationCaptureError("SETTLEMENT_FINALITY_ADAPTER_MISMATCH")
    if finality.finality_policy_id != WRH_CALIBRATION_FINALITY_POLICY.policy_id:
        raise WeatherCalibrationCaptureError("SETTLEMENT_FINALITY_POLICY_MISMATCH")
    if finality.financial_authority is not False:
        raise WeatherCalibrationCaptureError("SETTLEMENT_FINALITY_FINANCIAL_AUTHORITY_BOUNDARY_BROKEN")
    if finality.station != frozen.rule_evidence.station or finality.station != frozen.prediction.station.strip().upper():
        raise WeatherCalibrationCaptureError("SETTLEMENT_STATION_MISMATCH")
    if finality.target_date != frozen.rule_evidence.target_date or finality.target_date != frozen.prediction.target_date:
        raise WeatherCalibrationCaptureError("SETTLEMENT_TARGET_DATE_MISMATCH")

    if frozen.rule_evidence.family == DAILY_HIGH:
        target_value = int(finality.target_high_f)
    elif frozen.rule_evidence.family == DAILY_LOW:
        target_value = int(finality.target_low_f)
    else:
        raise WeatherCalibrationCaptureError("SETTLEMENT_FAMILY_UNSUPPORTED")

    winners = tuple(bucket for bucket in frozen.rule_evidence.bucket_partition if _bucket_contains(bucket, target_value))
    if len(winners) != 1:
        raise WeatherCalibrationCaptureError("SETTLEMENT_FROZEN_PARTITION_NOT_EXACTLY_ONE")
    winner = winners[0]

    selected = next((bucket for bucket in frozen.rule_evidence.bucket_partition if bucket.market_id == frozen.prediction.market_id), None)
    if selected is None or selected.condition_id != frozen.prediction.condition_id:
        raise WeatherCalibrationCaptureError("SETTLEMENT_SELECTED_BUCKET_IDENTITY_MISMATCH")
    payout = 1.0 if winner.market_id == selected.market_id else 0.0

    source_sha = _hash_payload(_bridge_source_payload(
        frozen,
        finality,
        target_value_f=target_value,
        winning_market_id=winner.market_id,
    ))
    label = build_exact_bucket_settlement_label(
        event_id=frozen.prediction.event_id,
        market_id=frozen.prediction.market_id,
        station=frozen.prediction.station,
        target_date=frozen.prediction.target_date,
        final_payout=payout,
        finalized_at=finality.current_received_at,
        source_evidence_sha256=source_sha,
        label_authority=True,
        settlement_state_reconstructable=True,
    )

    shell = WRHExactSettlementEvidence(
        bridge_version=WRH_SETTLEMENT_BRIDGE_VERSION,
        capture_evidence_sha256=frozen.capture_evidence_sha256,
        finality_policy_id=WRH_CALIBRATION_FINALITY_POLICY.policy_id,
        max_transition_gap_seconds=WRH_CALIBRATION_FINALITY_POLICY.max_transition_gap_seconds,
        max_following_row_age_seconds=WRH_CALIBRATION_FINALITY_POLICY.max_following_row_age_seconds,
        finality_state=finality,
        target_value_f=target_value,
        winning_market_id=winner.market_id,
        source_evidence_sha256=source_sha,
        label=label,
        bridge_evidence_sha256="0" * 64,
    )
    return WRHExactSettlementEvidence(
        **{
            name: getattr(shell, name)
            for name, definition in shell.__dataclass_fields__.items()
            if definition.init and name != "bridge_evidence_sha256"
        },
        bridge_evidence_sha256=_hash_payload(_bridge_digest_payload(shell)),
    )


def calibration_sample_from_wrh_settlement_evidence(
    capture: ProspectiveWeatherCalibrationCapture,
    evidence: WRHExactSettlementEvidence,
) -> ProbabilityCalibrationSample:
    """High-authority calibration join. Raw exact-label envelopes are not accepted here."""
    frozen = _validate_capture(capture)
    if not isinstance(evidence, WRHExactSettlementEvidence):
        raise WeatherCalibrationCaptureError("SETTLEMENT_EVIDENCE_TYPE_INVALID")
    if evidence.bridge_version != WRH_SETTLEMENT_BRIDGE_VERSION:
        raise WeatherCalibrationCaptureError("SETTLEMENT_BRIDGE_VERSION_MISMATCH")
    if evidence.calibration_label_authority is not True or evidence.financial_authority is not False:
        raise WeatherCalibrationCaptureError("SETTLEMENT_EVIDENCE_AUTHORITY_BOUNDARY_BROKEN")
    if evidence.capture_evidence_sha256 != frozen.capture_evidence_sha256:
        raise WeatherCalibrationCaptureError("SETTLEMENT_CAPTURE_EVIDENCE_MISMATCH")
    if (
        evidence.finality_policy_id != WRH_CALIBRATION_FINALITY_POLICY.policy_id
        or evidence.max_transition_gap_seconds != WRH_CALIBRATION_FINALITY_POLICY.max_transition_gap_seconds
        or evidence.max_following_row_age_seconds != WRH_CALIBRATION_FINALITY_POLICY.max_following_row_age_seconds
    ):
        raise WeatherCalibrationCaptureError("SETTLEMENT_FIXED_FINALITY_POLICY_MISMATCH")
    supplied_source = _sha256(evidence.source_evidence_sha256, "SETTLEMENT_SOURCE_EVIDENCE_SHA_INVALID")
    expected_source = _hash_payload(_bridge_source_payload(
        frozen,
        evidence.finality_state,
        target_value_f=evidence.target_value_f,
        winning_market_id=evidence.winning_market_id,
    ))
    if supplied_source != expected_source:
        raise WeatherCalibrationCaptureError("SETTLEMENT_SOURCE_EVIDENCE_DIGEST_MISMATCH")
    if evidence.label.source_evidence_sha256 != supplied_source:
        raise WeatherCalibrationCaptureError("SETTLEMENT_LABEL_SOURCE_EVIDENCE_MISMATCH")
    supplied_bridge = _sha256(evidence.bridge_evidence_sha256, "SETTLEMENT_BRIDGE_EVIDENCE_SHA_INVALID")
    if supplied_bridge != _hash_payload(_bridge_digest_payload(evidence)):
        raise WeatherCalibrationCaptureError("SETTLEMENT_BRIDGE_EVIDENCE_DIGEST_MISMATCH")

    return calibration_sample_from_exact_label(frozen.prediction, evidence.label)
