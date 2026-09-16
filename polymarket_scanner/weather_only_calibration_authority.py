from __future__ import annotations

"""Strict production authority gate for WRH-derived weather calibration samples.

The construction bridge intentionally separates evidence capture from authority.  A
Python dataclass plus a few booleans is not itself proof.  This module is therefore
the only high-authority entrypoint intended for feeding exact NWS/WRH labels into the
calibration engine.  It independently revalidates the prediction digest, prospective
rule capture, finalized WRH digest, frozen partition winner, payout, exact label and
complete outer evidence envelope.

Successful authorization grants *calibration-label* authority only.  Financial and
trade authority remain permanently false here.
"""

import hashlib
import json
import math
import re
from dataclasses import asdict, dataclass, field

from .weather_only_calibration import ProbabilityCalibrationSample
from .weather_only_calibration_capture import (
    PROSPECTIVE_CALIBRATION_CAPTURE_VERSION,
    WRH_CALIBRATION_FINALITY_POLICY,
    WRH_SETTLEMENT_BRIDGE_VERSION,
    ProspectiveWeatherCalibrationCapture,
    WRHExactSettlementEvidence,
    WeatherCalibrationCaptureError,
    _bridge_digest_payload,
    _bridge_source_payload,
    _validate_capture,
)
from .weather_only_contracts import DAILY_HIGH, DAILY_LOW
from .weather_only_predictions import (
    WeatherPredictionError,
    _validate_prediction,
    calibration_sample_from_exact_label,
)
from .weather_only_wrh import WRHSourceError, _hash_payload as _wrh_hash_payload
from .weather_only_wrh_finality import (
    WRH_FINALITY_ADAPTER_VERSION,
    WRH_FINALITY_SOURCE_ROLE,
    WRHFinalizedRuleState,
    _finality_digest_payload,
)


WRH_CALIBRATION_AUTHORITY_VERSION = "weather_wrh_calibration_authority_v1_full_lineage_revalidation"


class WeatherCalibrationAuthorityError(RuntimeError):
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
        raise WeatherCalibrationAuthorityError(code)
    return text


def _finite(value: object, code: str) -> float:
    if value is None or isinstance(value, bool) or not isinstance(value, (int, float)):
        raise WeatherCalibrationAuthorityError(code)
    number = float(value)
    if not math.isfinite(number) or number < 0.0:
        raise WeatherCalibrationAuthorityError(code)
    return number


@dataclass(frozen=True, slots=True)
class AuthorizedWRHCalibrationSample:
    authority_version: str
    capture_evidence_sha256: str
    rule_evidence_sha256: str
    prediction_evidence_sha256: str
    finality_evidence_sha256: str
    settlement_bridge_evidence_sha256: str
    label_evidence_sha256: str
    sample: ProbabilityCalibrationSample
    authority_evidence_sha256: str
    calibration_label_authority: bool = field(init=False, default=True)
    financial_authority: bool = field(init=False, default=False)

    def as_dict(self) -> dict:
        return {
            "authority_version": self.authority_version,
            "capture_evidence_sha256": self.capture_evidence_sha256,
            "rule_evidence_sha256": self.rule_evidence_sha256,
            "prediction_evidence_sha256": self.prediction_evidence_sha256,
            "finality_evidence_sha256": self.finality_evidence_sha256,
            "settlement_bridge_evidence_sha256": self.settlement_bridge_evidence_sha256,
            "label_evidence_sha256": self.label_evidence_sha256,
            "sample": asdict(self.sample),
            "authority_evidence_sha256": self.authority_evidence_sha256,
            "calibration_label_authority": self.calibration_label_authority,
            "financial_authority": self.financial_authority,
        }


def _bucket_contains(bucket, value: int) -> bool:
    if bucket.lower is not None and value < bucket.lower:
        return False
    if bucket.upper is not None and value > bucket.upper:
        return False
    return True


def _validate_finality_state(finality: object) -> WRHFinalizedRuleState:
    if not isinstance(finality, WRHFinalizedRuleState):
        raise WeatherCalibrationAuthorityError("AUTHORITY_FINALITY_TYPE_INVALID")
    if finality.adapter != WRH_FINALITY_ADAPTER_VERSION:
        raise WeatherCalibrationAuthorityError("AUTHORITY_FINALITY_ADAPTER_MISMATCH")
    if finality.source_role != WRH_FINALITY_SOURCE_ROLE:
        raise WeatherCalibrationAuthorityError("AUTHORITY_FINALITY_SOURCE_ROLE_MISMATCH")
    if finality.finality_policy_id != WRH_CALIBRATION_FINALITY_POLICY.policy_id:
        raise WeatherCalibrationAuthorityError("AUTHORITY_FINALITY_POLICY_MISMATCH")
    if (
        finality.correction_state_reconstructable is not True
        or finality.calibration_label_authority is not True
        or finality.settlement_label_authority is not True
        or finality.financial_authority is not False
    ):
        raise WeatherCalibrationAuthorityError("AUTHORITY_FINALITY_BOUNDARY_INVALID")
    if not finality.target_display_temperatures_f:
        raise WeatherCalibrationAuthorityError("AUTHORITY_FINALITY_TARGET_TEMPERATURES_EMPTY")
    if finality.target_high_f != max(finality.target_display_temperatures_f):
        raise WeatherCalibrationAuthorityError("AUTHORITY_FINALITY_HIGH_INCONSISTENT")
    if finality.target_low_f != min(finality.target_display_temperatures_f):
        raise WeatherCalibrationAuthorityError("AUTHORITY_FINALITY_LOW_INCONSISTENT")
    previous_received = _finite(finality.previous_received_at, "AUTHORITY_FINALITY_PREVIOUS_TIME_INVALID")
    current_received = _finite(finality.current_received_at, "AUTHORITY_FINALITY_CURRENT_TIME_INVALID")
    transition_gap = _finite(finality.transition_gap_seconds, "AUTHORITY_FINALITY_GAP_INVALID")
    row_age = _finite(finality.first_following_row_age_seconds, "AUTHORITY_FINALITY_ROW_AGE_INVALID")
    if current_received <= previous_received or abs((current_received - previous_received) - transition_gap) > 1e-9:
        raise WeatherCalibrationAuthorityError("AUTHORITY_FINALITY_TIME_INCONSISTENT")
    if transition_gap > WRH_CALIBRATION_FINALITY_POLICY.max_transition_gap_seconds:
        raise WeatherCalibrationAuthorityError("AUTHORITY_FINALITY_GAP_POLICY_BROKEN")
    if row_age > WRH_CALIBRATION_FINALITY_POLICY.max_following_row_age_seconds:
        raise WeatherCalibrationAuthorityError("AUTHORITY_FINALITY_ROW_AGE_POLICY_BROKEN")
    supplied = _sha256(finality.finality_evidence_sha256, "AUTHORITY_FINALITY_SHA_INVALID")
    expected = _wrh_hash_payload(_finality_digest_payload(finality))
    if supplied != expected:
        raise WeatherCalibrationAuthorityError("AUTHORITY_FINALITY_DIGEST_MISMATCH")
    _sha256(finality.previous_snapshot_sha256, "AUTHORITY_PREVIOUS_SNAPSHOT_SHA_INVALID")
    _sha256(finality.current_snapshot_sha256, "AUTHORITY_CURRENT_SNAPSHOT_SHA_INVALID")
    _sha256(finality.target_state_sha256, "AUTHORITY_TARGET_STATE_SHA_INVALID")
    return finality


def _authority_digest_payload(
    capture: ProspectiveWeatherCalibrationCapture,
    evidence: WRHExactSettlementEvidence,
    sample: ProbabilityCalibrationSample,
) -> dict:
    # Full nested payloads are intentional here.  The lower layers already use compact
    # digest chaining; this outer authority envelope additionally makes any accidental
    # mutation of a nested label/finality/capture field visible at the trusted boundary.
    return {
        "authority_version": WRH_CALIBRATION_AUTHORITY_VERSION,
        "capture": capture.as_dict(),
        "settlement_evidence": evidence.as_dict(),
        "sample": asdict(sample),
    }


def authorize_wrh_calibration_sample(
    capture: ProspectiveWeatherCalibrationCapture,
    evidence: WRHExactSettlementEvidence,
) -> AuthorizedWRHCalibrationSample:
    """Revalidate complete lineage and authorize one exact research calibration sample."""
    try:
        frozen = _validate_capture(capture)
    except WeatherCalibrationCaptureError as exc:
        raise WeatherCalibrationAuthorityError(f"AUTHORITY_CAPTURE_INVALID:{exc.code}") from exc
    if frozen.capture_version != PROSPECTIVE_CALIBRATION_CAPTURE_VERSION:
        raise WeatherCalibrationAuthorityError("AUTHORITY_CAPTURE_VERSION_MISMATCH")
    try:
        _validate_prediction(frozen.prediction)
    except WeatherPredictionError as exc:
        raise WeatherCalibrationAuthorityError(f"AUTHORITY_PREDICTION_INVALID:{exc.code}") from exc

    if not isinstance(evidence, WRHExactSettlementEvidence):
        raise WeatherCalibrationAuthorityError("AUTHORITY_SETTLEMENT_TYPE_INVALID")
    if evidence.bridge_version != WRH_SETTLEMENT_BRIDGE_VERSION:
        raise WeatherCalibrationAuthorityError("AUTHORITY_SETTLEMENT_BRIDGE_VERSION_MISMATCH")
    if evidence.calibration_label_authority is not True or evidence.financial_authority is not False:
        raise WeatherCalibrationAuthorityError("AUTHORITY_SETTLEMENT_BOUNDARY_INVALID")
    if evidence.capture_evidence_sha256 != frozen.capture_evidence_sha256:
        raise WeatherCalibrationAuthorityError("AUTHORITY_CAPTURE_SETTLEMENT_MISMATCH")
    if (
        evidence.finality_policy_id != WRH_CALIBRATION_FINALITY_POLICY.policy_id
        or evidence.max_transition_gap_seconds != WRH_CALIBRATION_FINALITY_POLICY.max_transition_gap_seconds
        or evidence.max_following_row_age_seconds != WRH_CALIBRATION_FINALITY_POLICY.max_following_row_age_seconds
    ):
        raise WeatherCalibrationAuthorityError("AUTHORITY_FIXED_FINALITY_POLICY_MISMATCH")

    finality = _validate_finality_state(evidence.finality_state)
    rule = frozen.rule_evidence
    prediction = frozen.prediction
    if finality.station != rule.station or finality.station != prediction.station.strip().upper():
        raise WeatherCalibrationAuthorityError("AUTHORITY_STATION_IDENTITY_MISMATCH")
    if finality.target_date != rule.target_date or finality.target_date != prediction.target_date:
        raise WeatherCalibrationAuthorityError("AUTHORITY_TARGET_DATE_IDENTITY_MISMATCH")

    if rule.family == DAILY_HIGH:
        target_value = int(finality.target_high_f)
    elif rule.family == DAILY_LOW:
        target_value = int(finality.target_low_f)
    else:
        raise WeatherCalibrationAuthorityError("AUTHORITY_FAMILY_UNSUPPORTED")
    if evidence.target_value_f != target_value:
        raise WeatherCalibrationAuthorityError("AUTHORITY_TARGET_VALUE_MISMATCH")

    winners = tuple(bucket for bucket in rule.bucket_partition if _bucket_contains(bucket, target_value))
    if len(winners) != 1:
        raise WeatherCalibrationAuthorityError("AUTHORITY_FROZEN_PARTITION_NOT_EXACTLY_ONE")
    winner = winners[0]
    if evidence.winning_market_id != winner.market_id:
        raise WeatherCalibrationAuthorityError("AUTHORITY_WINNING_MARKET_MISMATCH")

    selected = next((bucket for bucket in rule.bucket_partition if bucket.market_id == prediction.market_id), None)
    if selected is None or selected.condition_id != prediction.condition_id:
        raise WeatherCalibrationAuthorityError("AUTHORITY_SELECTED_BUCKET_IDENTITY_MISMATCH")
    expected_payout = 1.0 if winner.market_id == selected.market_id else 0.0

    expected_source = _hash_payload(_bridge_source_payload(
        frozen,
        finality,
        target_value_f=target_value,
        winning_market_id=winner.market_id,
    ))
    supplied_source = _sha256(evidence.source_evidence_sha256, "AUTHORITY_SETTLEMENT_SOURCE_SHA_INVALID")
    if supplied_source != expected_source:
        raise WeatherCalibrationAuthorityError("AUTHORITY_SETTLEMENT_SOURCE_DIGEST_MISMATCH")

    label = evidence.label
    if label.event_id != prediction.event_id or label.market_id != prediction.market_id:
        raise WeatherCalibrationAuthorityError("AUTHORITY_LABEL_MARKET_IDENTITY_MISMATCH")
    if label.station.strip().upper() != prediction.station.strip().upper() or label.target_date != prediction.target_date:
        raise WeatherCalibrationAuthorityError("AUTHORITY_LABEL_SOURCE_IDENTITY_MISMATCH")
    if float(label.final_payout) != expected_payout:
        raise WeatherCalibrationAuthorityError("AUTHORITY_LABEL_PAYOUT_MISMATCH")
    if float(label.finalized_at) != float(finality.current_received_at):
        raise WeatherCalibrationAuthorityError("AUTHORITY_LABEL_FINALIZED_AT_MISMATCH")
    if label.source_evidence_sha256 != supplied_source:
        raise WeatherCalibrationAuthorityError("AUTHORITY_LABEL_SOURCE_DIGEST_MISMATCH")

    # Preserve the bridge's own evidence contract even though its v1 digest is compact.
    supplied_bridge = _sha256(evidence.bridge_evidence_sha256, "AUTHORITY_BRIDGE_SHA_INVALID")
    if supplied_bridge != _hash_payload(_bridge_digest_payload(evidence)):
        raise WeatherCalibrationAuthorityError("AUTHORITY_BRIDGE_DIGEST_MISMATCH")

    try:
        sample = calibration_sample_from_exact_label(prediction, label)
    except WeatherPredictionError as exc:
        raise WeatherCalibrationAuthorityError(f"AUTHORITY_EXACT_LABEL_INVALID:{exc.code}") from exc

    shell = AuthorizedWRHCalibrationSample(
        authority_version=WRH_CALIBRATION_AUTHORITY_VERSION,
        capture_evidence_sha256=frozen.capture_evidence_sha256,
        rule_evidence_sha256=rule.rule_evidence_sha256,
        prediction_evidence_sha256=prediction.prediction_evidence_sha256,
        finality_evidence_sha256=finality.finality_evidence_sha256,
        settlement_bridge_evidence_sha256=evidence.bridge_evidence_sha256,
        label_evidence_sha256=label.label_evidence_sha256,
        sample=sample,
        authority_evidence_sha256="0" * 64,
    )
    return AuthorizedWRHCalibrationSample(
        **{
            name: getattr(shell, name)
            for name, definition in shell.__dataclass_fields__.items()
            if definition.init and name != "authority_evidence_sha256"
        },
        authority_evidence_sha256=_hash_payload(_authority_digest_payload(frozen, evidence, sample)),
    )
