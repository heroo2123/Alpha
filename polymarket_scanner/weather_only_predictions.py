from __future__ import annotations

"""Prospective prediction evidence for weather-only calibration.

The ensemble layer produces a categorical distribution across all buckets in one
weather event. Treating every bucket as an independent calibration observation
would inflate the effective sample size, while selecting a convenient bucket after
settlement would introduce retrospective selection bias.

This module therefore freezes exactly one calibration candidate per event *before*
resolution under a named deterministic selection policy. The current foundation
supports one policy only: choose the bucket with the highest raw ensemble member
frequency, breaking exact ties by market id. The resulting probability is still a
raw, uncalibrated research prediction.

Only a later exact-rule-state settlement label with matching event/market/station/
date identity can be joined to the prospective prediction. Proxy observations can
never be upgraded into labels by setting authority booleans. Prediction records bind
the complete mapped forecast snapshot, while settlement labels bind their underlying
source-evidence digest. The bridge emits the existing ``ProbabilityCalibrationSample``
consumed by the preregistered calibration engine and grants no financial authority.
"""

import hashlib
import json
import math
import re
from dataclasses import asdict, dataclass, field, replace
from datetime import date

from .weather_only_calibration import (
    SETTLEMENT_LABEL_EVIDENCE_VERSION,
    ProbabilityCalibrationSample,
)
from .weather_only_forecast import (
    FORECAST_ADAPTER_VERSION,
    OPEN_METEO_GEFS_MODEL,
    EnsembleBucketForecast,
)


PROSPECTIVE_PREDICTION_VERSION = "weather_prospective_prediction_v2_full_forecast_snapshot"
SELECTION_ENGINE_VERSION = "weather_candidate_selector_v1_max_raw_frequency_market_id_asc"
SELECTION_STRATEGY = "MAX_RAW_MEMBER_FREQUENCY"
SELECTION_TIE_BREAK = "MARKET_ID_ASC"
MODEL_FAMILY_VERSION = "weather_gefs_top_bucket_raw_frequency_v1"
LABEL_DIGEST_VERSION = "weather_exact_settlement_label_digest_v1"
NWS_WRH_EXACT_LABEL_ADAPTER = "NWS_WRH_EXACT_RULE_STATE_V1"
EXACT_SETTLEMENT_SOURCE_ROLE = "SETTLEMENT_RULE_STATE"


class WeatherPredictionError(RuntimeError):
    def __init__(self, code: str):
        self.code = str(code)
        super().__init__(self.code)


def _finite_probability(value: object, code: str) -> float:
    if value is None or isinstance(value, bool):
        raise WeatherPredictionError(code)
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        raise WeatherPredictionError(code)
    if not math.isfinite(number) or not 0.0 <= number <= 1.0:
        raise WeatherPredictionError(code)
    return number


def _finite_timestamp(value: object, code: str) -> float:
    if value is None or isinstance(value, bool):
        raise WeatherPredictionError(code)
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        raise WeatherPredictionError(code)
    if not math.isfinite(number) or number < 0.0:
        raise WeatherPredictionError(code)
    return number


def _sha256(value: object, code: str) -> str:
    text = str(value or "").strip().lower()
    if not re.fullmatch(r"[0-9a-f]{64}", text):
        raise WeatherPredictionError(code)
    return text


def _nonempty(value: object, code: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise WeatherPredictionError(code)
    return text


def _hash_payload(payload: dict) -> str:
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    ).hexdigest()


@dataclass(frozen=True, slots=True)
class ProspectiveSelectionPolicy:
    policy_id: str
    strategy: str = SELECTION_STRATEGY
    tie_break: str = SELECTION_TIE_BREAK

    def __post_init__(self) -> None:
        if not isinstance(self.policy_id, str) or not self.policy_id.strip():
            raise ValueError("policy_id is required")
        if self.policy_id != self.policy_id.strip():
            raise ValueError("policy_id must not contain surrounding whitespace")
        if self.strategy != SELECTION_STRATEGY:
            raise ValueError("unsupported prospective selection strategy")
        if self.tie_break != SELECTION_TIE_BREAK:
            raise ValueError("unsupported prospective selection tie-break")


@dataclass(frozen=True, slots=True)
class ProspectiveBucketPrediction:
    evidence_version: str
    selector_version: str
    selection_policy_id: str
    model_version: str
    event_id: str
    market_id: str
    condition_id: str
    station: str
    target_date: date
    family: str
    unit: str
    provider_model: str
    forecast_adapter: str
    source_evidence_sha256: str
    forecast_snapshot_sha256: str
    mapping_policy_id: str
    quantization: str
    included_control: bool
    member_count: int
    raw_predicted_probability: float
    captured_at: float
    prediction_evidence_sha256: str
    calibrated_probability: bool = field(init=False, default=False)
    financial_authority: bool = field(init=False, default=False)

    def as_dict(self) -> dict:
        value = asdict(self)
        value["target_date"] = self.target_date.isoformat()
        return value


@dataclass(frozen=True, slots=True)
class ExactBucketSettlementLabel:
    event_id: str
    market_id: str
    station: str
    target_date: date
    final_payout: float
    label_adapter: str
    source_role: str
    evidence_version: str
    label_authority: bool
    settlement_state_reconstructable: bool
    finalized_at: float
    source_evidence_sha256: str
    label_evidence_sha256: str
    financial_authority: bool = field(init=False, default=False)

    def __post_init__(self) -> None:
        _nonempty(self.event_id, "LABEL_EVENT_ID_MISSING")
        _nonempty(self.market_id, "LABEL_MARKET_ID_MISSING")
        station = _nonempty(self.station, "LABEL_STATION_MISSING").upper()
        if len(station) != 4 or not station.isalnum():
            raise WeatherPredictionError("LABEL_STATION_INVALID")
        if type(self.target_date) is not date:
            raise WeatherPredictionError("LABEL_TARGET_DATE_INVALID")
        payout = _finite_probability(self.final_payout, "LABEL_PAYOUT_INVALID")
        if payout not in {0.0, 1.0}:
            raise WeatherPredictionError("LABEL_PAYOUT_NOT_BINARY")
        _nonempty(self.label_adapter, "LABEL_ADAPTER_MISSING")
        _nonempty(self.source_role, "LABEL_SOURCE_ROLE_MISSING")
        _nonempty(self.evidence_version, "LABEL_EVIDENCE_VERSION_MISSING")
        if type(self.label_authority) is not bool:
            raise WeatherPredictionError("LABEL_AUTHORITY_TYPE_INVALID")
        if type(self.settlement_state_reconstructable) is not bool:
            raise WeatherPredictionError("LABEL_RECONSTRUCTABLE_TYPE_INVALID")
        _finite_timestamp(self.finalized_at, "LABEL_FINALIZED_AT_INVALID")
        _sha256(self.source_evidence_sha256, "LABEL_SOURCE_EVIDENCE_SHA_INVALID")
        _sha256(self.label_evidence_sha256, "LABEL_EVIDENCE_SHA_INVALID")

    def as_dict(self) -> dict:
        value = asdict(self)
        value["target_date"] = self.target_date.isoformat()
        return value


def _model_version(forecast: EnsembleBucketForecast, policy: ProspectiveSelectionPolicy) -> str:
    identity = {
        "model_family": MODEL_FAMILY_VERSION,
        "forecast_adapter": forecast.adapter,
        "provider_model": forecast.provider_model,
        "mapping_policy_id": forecast.mapping_policy_id,
        "quantization": forecast.quantization,
        "included_control": forecast.included_control,
        "selector_version": SELECTION_ENGINE_VERSION,
        "selection_policy_id": policy.policy_id,
        "strategy": policy.strategy,
        "tie_break": policy.tie_break,
    }
    return f"{MODEL_FAMILY_VERSION}:{_hash_payload(identity)}"


def _validate_forecast(forecast: EnsembleBucketForecast) -> None:
    if not isinstance(forecast, EnsembleBucketForecast):
        raise WeatherPredictionError("PREDICTION_FORECAST_TYPE_INVALID")
    if forecast.adapter != FORECAST_ADAPTER_VERSION or forecast.provider_model != OPEN_METEO_GEFS_MODEL:
        raise WeatherPredictionError("PREDICTION_FORECAST_IDENTITY_MISMATCH")
    _nonempty(forecast.event_id, "PREDICTION_EVENT_ID_MISSING")
    station = _nonempty(forecast.station, "PREDICTION_STATION_MISSING").upper()
    if len(station) != 4 or not station.isalnum():
        raise WeatherPredictionError("PREDICTION_STATION_INVALID")
    if type(forecast.target_date) is not date:
        raise WeatherPredictionError("PREDICTION_TARGET_DATE_INVALID")
    _nonempty(forecast.family, "PREDICTION_FAMILY_MISSING")
    if forecast.unit not in {"F", "C"}:
        raise WeatherPredictionError("PREDICTION_UNIT_INVALID")
    source_sha = _sha256(forecast.source_evidence_sha256, "PREDICTION_SOURCE_EVIDENCE_SHA_INVALID")
    if source_sha != forecast.source_evidence_sha256.lower():
        raise WeatherPredictionError("PREDICTION_SOURCE_EVIDENCE_SHA_INVALID")
    _nonempty(forecast.mapping_policy_id, "PREDICTION_MAPPING_POLICY_MISSING")
    _nonempty(forecast.quantization, "PREDICTION_QUANTIZATION_MISSING")
    if type(forecast.included_control) is not bool:
        raise WeatherPredictionError("PREDICTION_CONTROL_FLAG_INVALID")
    if isinstance(forecast.member_count, bool) or not isinstance(forecast.member_count, int) or forecast.member_count <= 0:
        raise WeatherPredictionError("PREDICTION_MEMBER_COUNT_INVALID")
    forecast_probability_sum = _finite_probability(
        forecast.probability_sum,
        "PREDICTION_FORECAST_PROBABILITY_SUM_INVALID",
    )
    if forecast.calibrated is not False or forecast.settlement_authority is not False or forecast.financial_authority is not False:
        raise WeatherPredictionError("PREDICTION_FORECAST_AUTHORITY_BOUNDARY_BROKEN")
    if not isinstance(forecast.bucket_frequencies, tuple) or not forecast.bucket_frequencies:
        raise WeatherPredictionError("PREDICTION_BUCKETS_MISSING")

    seen_markets: set[str] = set()
    seen_conditions: set[str] = set()
    hit_sum = 0
    probability_sum = 0.0
    for row in forecast.bucket_frequencies:
        market_id = _nonempty(row.market_id, "PREDICTION_BUCKET_MARKET_ID_MISSING")
        condition_id = _nonempty(row.condition_id, "PREDICTION_BUCKET_CONDITION_ID_MISSING")
        _nonempty(row.yes_token, "PREDICTION_BUCKET_YES_TOKEN_MISSING")
        _nonempty(row.no_token, "PREDICTION_BUCKET_NO_TOKEN_MISSING")
        if market_id in seen_markets:
            raise WeatherPredictionError("PREDICTION_BUCKET_MARKET_DUPLICATE")
        if condition_id in seen_conditions:
            raise WeatherPredictionError("PREDICTION_BUCKET_CONDITION_DUPLICATE")
        seen_markets.add(market_id)
        seen_conditions.add(condition_id)
        if row.mapping_policy_id != forecast.mapping_policy_id:
            raise WeatherPredictionError("PREDICTION_BUCKET_MAPPING_POLICY_MISMATCH")
        if row.calibrated is not False or row.financial_authority is not False:
            raise WeatherPredictionError("PREDICTION_BUCKET_AUTHORITY_BOUNDARY_BROKEN")
        if isinstance(row.member_count, bool) or row.member_count != forecast.member_count:
            raise WeatherPredictionError("PREDICTION_BUCKET_MEMBER_COUNT_MISMATCH")
        if isinstance(row.member_hits, bool) or not isinstance(row.member_hits, int) or not 0 <= row.member_hits <= forecast.member_count:
            raise WeatherPredictionError("PREDICTION_BUCKET_MEMBER_HITS_INVALID")
        probability = _finite_probability(row.raw_member_frequency, "PREDICTION_BUCKET_PROBABILITY_INVALID")
        no_probability = _finite_probability(row.raw_no_frequency, "PREDICTION_BUCKET_NO_PROBABILITY_INVALID")
        if abs(no_probability - (1.0 - probability)) > 1e-12:
            raise WeatherPredictionError("PREDICTION_BUCKET_BINARY_PROBABILITY_INCONSISTENT")
        if abs(probability - row.member_hits / forecast.member_count) > 1e-12:
            raise WeatherPredictionError("PREDICTION_BUCKET_FREQUENCY_HIT_MISMATCH")
        hit_sum += row.member_hits
        probability_sum += probability

    if hit_sum != forecast.member_count or abs(probability_sum - 1.0) > 1e-12:
        raise WeatherPredictionError("PREDICTION_CATEGORICAL_DISTRIBUTION_INVALID")
    if abs(forecast_probability_sum - probability_sum) > 1e-12:
        raise WeatherPredictionError("PREDICTION_FORECAST_PROBABILITY_SUM_MISMATCH")


def _forecast_snapshot_digest(forecast: EnsembleBucketForecast) -> str:
    rows = []
    for row in sorted(forecast.bucket_frequencies, key=lambda value: str(value.market_id)):
        rows.append({
            "market_id": row.market_id,
            "condition_id": row.condition_id,
            "yes_token": row.yes_token,
            "no_token": row.no_token,
            "lower": row.lower,
            "upper": row.upper,
            "member_hits": row.member_hits,
            "member_count": row.member_count,
            "raw_member_frequency": row.raw_member_frequency,
            "raw_no_frequency": row.raw_no_frequency,
            "mapping_policy_id": row.mapping_policy_id,
        })
    return _hash_payload({
        "forecast_adapter": forecast.adapter,
        "event_id": forecast.event_id,
        "station": forecast.station.upper(),
        "target_date": forecast.target_date.isoformat(),
        "family": forecast.family,
        "unit": forecast.unit,
        "provider_model": forecast.provider_model,
        "source_evidence_sha256": forecast.source_evidence_sha256.lower(),
        "mapping_policy_id": forecast.mapping_policy_id,
        "quantization": forecast.quantization,
        "included_control": forecast.included_control,
        "member_count": forecast.member_count,
        "probability_sum": forecast.probability_sum,
        "bucket_frequencies": rows,
    })


def _prediction_digest_payload(prediction: ProspectiveBucketPrediction) -> dict:
    return {
        "evidence_version": prediction.evidence_version,
        "selector_version": prediction.selector_version,
        "selection_policy_id": prediction.selection_policy_id,
        "model_version": prediction.model_version,
        "event_id": prediction.event_id,
        "market_id": prediction.market_id,
        "condition_id": prediction.condition_id,
        "station": prediction.station,
        "target_date": prediction.target_date.isoformat(),
        "family": prediction.family,
        "unit": prediction.unit,
        "provider_model": prediction.provider_model,
        "forecast_adapter": prediction.forecast_adapter,
        "source_evidence_sha256": prediction.source_evidence_sha256,
        "forecast_snapshot_sha256": prediction.forecast_snapshot_sha256,
        "mapping_policy_id": prediction.mapping_policy_id,
        "quantization": prediction.quantization,
        "included_control": prediction.included_control,
        "member_count": prediction.member_count,
        "raw_predicted_probability": prediction.raw_predicted_probability,
        "captured_at": prediction.captured_at,
    }


def _prediction_digest(prediction: ProspectiveBucketPrediction) -> str:
    return _hash_payload(_prediction_digest_payload(prediction))


def _label_digest_payload(label: ExactBucketSettlementLabel) -> dict:
    return {
        "digest_version": LABEL_DIGEST_VERSION,
        "event_id": label.event_id,
        "market_id": label.market_id,
        "station": label.station.upper(),
        "target_date": label.target_date.isoformat(),
        "final_payout": label.final_payout,
        "label_adapter": label.label_adapter,
        "source_role": label.source_role,
        "evidence_version": label.evidence_version,
        "label_authority": label.label_authority,
        "settlement_state_reconstructable": label.settlement_state_reconstructable,
        "finalized_at": label.finalized_at,
        "source_evidence_sha256": label.source_evidence_sha256.lower(),
    }


def _label_digest(label: ExactBucketSettlementLabel) -> str:
    return _hash_payload(_label_digest_payload(label))


def build_exact_bucket_settlement_label(
    *,
    event_id: str,
    market_id: str,
    station: str,
    target_date: date,
    final_payout: float,
    finalized_at: float,
    source_evidence_sha256: str,
    label_authority: bool = True,
    settlement_state_reconstructable: bool = True,
) -> ExactBucketSettlementLabel:
    """Build a tamper-evident NWS WRH exact-rule-state label envelope.

    This factory does not fetch WRH data and therefore does not itself prove source
    authority. A future exact settlement adapter must supply the bound source evidence
    digest. The resulting object can enter calibration only after the join gate checks
    all identity, time, adapter, source-role and digest invariants.
    """
    shell = ExactBucketSettlementLabel(
        event_id=event_id,
        market_id=market_id,
        station=station.upper(),
        target_date=target_date,
        final_payout=final_payout,
        label_adapter=NWS_WRH_EXACT_LABEL_ADAPTER,
        source_role=EXACT_SETTLEMENT_SOURCE_ROLE,
        evidence_version=SETTLEMENT_LABEL_EVIDENCE_VERSION,
        label_authority=label_authority,
        settlement_state_reconstructable=settlement_state_reconstructable,
        finalized_at=finalized_at,
        source_evidence_sha256=source_evidence_sha256.lower(),
        label_evidence_sha256="0" * 64,
    )
    return replace(shell, label_evidence_sha256=_label_digest(shell))


def select_prospective_bucket_prediction(
    forecast: EnsembleBucketForecast,
    *,
    policy: ProspectiveSelectionPolicy,
    captured_at: float,
) -> ProspectiveBucketPrediction:
    """Freeze one deterministic per-event calibration candidate before resolution."""
    _validate_forecast(forecast)
    captured = _finite_timestamp(captured_at, "PREDICTION_CAPTURED_AT_INVALID")
    if not isinstance(policy, ProspectiveSelectionPolicy):
        raise WeatherPredictionError("PREDICTION_SELECTION_POLICY_INVALID")

    candidate = sorted(
        forecast.bucket_frequencies,
        key=lambda row: (-float(row.raw_member_frequency), str(row.market_id)),
    )[0]
    shell = ProspectiveBucketPrediction(
        evidence_version=PROSPECTIVE_PREDICTION_VERSION,
        selector_version=SELECTION_ENGINE_VERSION,
        selection_policy_id=policy.policy_id,
        model_version=_model_version(forecast, policy),
        event_id=forecast.event_id,
        market_id=candidate.market_id,
        condition_id=candidate.condition_id,
        station=forecast.station.upper(),
        target_date=forecast.target_date,
        family=forecast.family,
        unit=forecast.unit,
        provider_model=forecast.provider_model,
        forecast_adapter=forecast.adapter,
        source_evidence_sha256=forecast.source_evidence_sha256.lower(),
        forecast_snapshot_sha256=_forecast_snapshot_digest(forecast),
        mapping_policy_id=forecast.mapping_policy_id,
        quantization=forecast.quantization,
        included_control=forecast.included_control,
        member_count=forecast.member_count,
        raw_predicted_probability=float(candidate.raw_member_frequency),
        captured_at=captured,
        prediction_evidence_sha256="0" * 64,
    )
    return replace(shell, prediction_evidence_sha256=_prediction_digest(shell))


def _validate_prediction(prediction: ProspectiveBucketPrediction) -> None:
    if not isinstance(prediction, ProspectiveBucketPrediction):
        raise WeatherPredictionError("CALIBRATION_PREDICTION_TYPE_INVALID")
    if prediction.evidence_version != PROSPECTIVE_PREDICTION_VERSION:
        raise WeatherPredictionError("CALIBRATION_PREDICTION_EVIDENCE_VERSION_MISMATCH")
    if prediction.selector_version != SELECTION_ENGINE_VERSION:
        raise WeatherPredictionError("CALIBRATION_SELECTOR_VERSION_MISMATCH")
    _nonempty(prediction.selection_policy_id, "CALIBRATION_SELECTION_POLICY_MISSING")
    _nonempty(prediction.model_version, "CALIBRATION_MODEL_VERSION_MISSING")
    _nonempty(prediction.event_id, "CALIBRATION_EVENT_ID_MISSING")
    _nonempty(prediction.market_id, "CALIBRATION_MARKET_ID_MISSING")
    _nonempty(prediction.condition_id, "CALIBRATION_CONDITION_ID_MISSING")
    station = _nonempty(prediction.station, "CALIBRATION_STATION_MISSING").upper()
    if len(station) != 4 or not station.isalnum():
        raise WeatherPredictionError("CALIBRATION_STATION_INVALID")
    if type(prediction.target_date) is not date:
        raise WeatherPredictionError("CALIBRATION_TARGET_DATE_INVALID")
    if prediction.forecast_adapter != FORECAST_ADAPTER_VERSION or prediction.provider_model != OPEN_METEO_GEFS_MODEL:
        raise WeatherPredictionError("CALIBRATION_FORECAST_IDENTITY_MISMATCH")
    _sha256(prediction.source_evidence_sha256, "CALIBRATION_SOURCE_EVIDENCE_SHA_INVALID")
    _sha256(prediction.forecast_snapshot_sha256, "CALIBRATION_FORECAST_SNAPSHOT_SHA_INVALID")
    _finite_probability(prediction.raw_predicted_probability, "CALIBRATION_PREDICTED_PROBABILITY_INVALID")
    _finite_timestamp(prediction.captured_at, "CALIBRATION_CAPTURED_AT_INVALID")
    supplied = _sha256(prediction.prediction_evidence_sha256, "CALIBRATION_PREDICTION_SHA_INVALID")
    if supplied != _prediction_digest(prediction):
        raise WeatherPredictionError("CALIBRATION_PREDICTION_DIGEST_MISMATCH")
    if prediction.calibrated_probability is not False or prediction.financial_authority is not False:
        raise WeatherPredictionError("CALIBRATION_PREDICTION_AUTHORITY_BOUNDARY_BROKEN")


def calibration_sample_from_exact_label(
    prediction: ProspectiveBucketPrediction,
    label: ExactBucketSettlementLabel,
) -> ProbabilityCalibrationSample:
    """Join one prospective prediction to one exact rule-state label, fail closed."""
    _validate_prediction(prediction)
    if not isinstance(label, ExactBucketSettlementLabel):
        raise WeatherPredictionError("CALIBRATION_LABEL_TYPE_INVALID")
    if label.event_id != prediction.event_id:
        raise WeatherPredictionError("CALIBRATION_EVENT_ID_MISMATCH")
    if label.market_id != prediction.market_id:
        raise WeatherPredictionError("CALIBRATION_MARKET_ID_MISMATCH")
    if label.station.strip().upper() != prediction.station.strip().upper():
        raise WeatherPredictionError("CALIBRATION_STATION_MISMATCH")
    if label.target_date != prediction.target_date:
        raise WeatherPredictionError("CALIBRATION_TARGET_DATE_MISMATCH")
    if float(label.finalized_at) < float(prediction.captured_at):
        raise WeatherPredictionError("CALIBRATION_LABEL_PREDATES_PREDICTION")
    if label.evidence_version != SETTLEMENT_LABEL_EVIDENCE_VERSION:
        raise WeatherPredictionError("CALIBRATION_LABEL_EVIDENCE_VERSION_MISMATCH")
    if label.label_authority is not True:
        raise WeatherPredictionError("CALIBRATION_LABEL_AUTHORITY_FALSE")
    if label.settlement_state_reconstructable is not True:
        raise WeatherPredictionError("CALIBRATION_SETTLEMENT_STATE_NOT_RECONSTRUCTABLE")

    adapter = label.label_adapter.strip()
    source_role = label.source_role.strip()
    if "PROXY" in adapter.upper() or "PROXY" in source_role.upper():
        raise WeatherPredictionError("CALIBRATION_PROXY_LABEL_FORBIDDEN")
    if adapter != NWS_WRH_EXACT_LABEL_ADAPTER:
        raise WeatherPredictionError("CALIBRATION_LABEL_ADAPTER_UNSUPPORTED")
    if source_role != EXACT_SETTLEMENT_SOURCE_ROLE:
        raise WeatherPredictionError("CALIBRATION_LABEL_SOURCE_ROLE_UNSUPPORTED")
    _sha256(label.source_evidence_sha256, "CALIBRATION_LABEL_SOURCE_EVIDENCE_SHA_INVALID")
    supplied_label_digest = _sha256(label.label_evidence_sha256, "CALIBRATION_LABEL_EVIDENCE_SHA_INVALID")
    if supplied_label_digest != _label_digest(label):
        raise WeatherPredictionError("CALIBRATION_LABEL_DIGEST_MISMATCH")

    return ProbabilityCalibrationSample(
        event_id=prediction.event_id,
        station=prediction.station,
        model_version=prediction.model_version,
        predicted_probability=float(prediction.raw_predicted_probability),
        final_payout=float(label.final_payout),
        label_adapter=adapter,
        source_role=source_role,
        evidence_version=label.evidence_version,
        label_authority=True,
        settlement_state_reconstructable=True,
    )
