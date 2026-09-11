from __future__ import annotations

"""Immutable identity manifest for the prospective weather calibration experiment.

This manifest binds the collection/model/source lineage that must remain fixed while
prospective labels accumulate. It deliberately does *not* invent a statistical
calibration policy: probability bins and readiness thresholds remain explicitly
UNFROZEN and therefore cannot grant calibrated-probability or financial authority.

The manifest is useful for service preflight, status evidence and later policy
preregistration. Any drift in the collection horizon, GEFS adapter, mapping/selection
policy, prediction model family, WRH finality policy or strict dataset reader changes
the manifest digest and requires a new experiment identity.
"""

import hashlib
import json
from dataclasses import dataclass, field

from .weather_only_calibration import CALIBRATION_ENGINE_VERSION
from .weather_only_calibration_capture import (
    PROSPECTIVE_CALIBRATION_CAPTURE_VERSION,
    PROSPECTIVE_RULE_EVIDENCE_VERSION,
    WRH_CALIBRATION_FINALITY_POLICY,
    WRH_SETTLEMENT_BRIDGE_VERSION,
)
from .weather_only_calibration_dataset import WEATHER_CALIBRATION_DATASET_VERSION
from .weather_only_calibration_horizon import (
    CAPTURE_HORIZON_EVIDENCE_VERSION,
    CAPTURE_HORIZON_LOCAL_HOUR,
    CAPTURE_HORIZON_LOCAL_MINUTE,
    CAPTURE_HORIZON_POLICY_ID,
    CAPTURE_HORIZON_WINDOW_SECONDS,
)
from .weather_only_calibration_reader import WEATHER_CALIBRATION_READER_VERSION
from .weather_only_calibration_worker import (
    CAPTURE_LOCAL_HOUR,
    CAPTURE_LOCAL_MINUTE,
    CAPTURE_POLICY_ID,
    CAPTURE_WINDOW_SECONDS,
    MAPPING_POLICY,
    SELECTION_POLICY,
    WEATHER_CALIBRATION_WORKER_VERSION,
)
from .weather_only_calibration_worker_runtime import WEATHER_CALIBRATION_WORKER_RUNTIME_VERSION
from .weather_only_forecast import (
    FORECAST_ADAPTER_VERSION,
    GEFS_TOTAL_MEMBERS,
    OPEN_METEO_GEFS_MODEL,
    SUPPORTED_QUANTIZATION,
)
from .weather_only_predictions import (
    MODEL_FAMILY_VERSION,
    PROSPECTIVE_PREDICTION_VERSION,
    SELECTION_ENGINE_VERSION,
    SELECTION_STRATEGY,
    SELECTION_TIE_BREAK,
)
from .weather_only_wrh_collector_authority import TRUSTED_WRH_COLLECTOR_AUTHORITY_VERSION


WEATHER_CALIBRATION_EXPERIMENT_VERSION = "weather_calibration_experiment_v1_collection_identity_policy_unfrozen"
STATISTICAL_POLICY_STATUS = "UNFROZEN_NO_CALIBRATED_PROBABILITY_AUTHORITY"


class WeatherCalibrationExperimentError(RuntimeError):
    def __init__(self, code: str):
        self.code = str(code)
        super().__init__(self.code)


def _hash_payload(payload: object) -> str:
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    ).hexdigest()


def _assert_internal_policy_consistency() -> None:
    if CAPTURE_POLICY_ID != CAPTURE_HORIZON_POLICY_ID:
        raise WeatherCalibrationExperimentError("EXPERIMENT_CAPTURE_POLICY_DRIFT")
    if CAPTURE_LOCAL_HOUR != CAPTURE_HORIZON_LOCAL_HOUR:
        raise WeatherCalibrationExperimentError("EXPERIMENT_CAPTURE_HOUR_DRIFT")
    if CAPTURE_LOCAL_MINUTE != CAPTURE_HORIZON_LOCAL_MINUTE:
        raise WeatherCalibrationExperimentError("EXPERIMENT_CAPTURE_MINUTE_DRIFT")
    if CAPTURE_WINDOW_SECONDS != CAPTURE_HORIZON_WINDOW_SECONDS:
        raise WeatherCalibrationExperimentError("EXPERIMENT_CAPTURE_WINDOW_DRIFT")
    if MAPPING_POLICY.quantization != SUPPORTED_QUANTIZATION or MAPPING_POLICY.include_control is not True:
        raise WeatherCalibrationExperimentError("EXPERIMENT_MAPPING_POLICY_DRIFT")


@dataclass(frozen=True, slots=True)
class WeatherCalibrationExperimentManifest:
    manifest_version: str
    worker_version: str
    runtime_version: str
    capture_policy_id: str
    capture_local_hour: int
    capture_local_minute: int
    capture_window_seconds: int
    horizon_evidence_version: str
    mapping_policy_id: str
    mapping_quantization: str
    include_control: bool
    selection_policy_id: str
    selection_engine_version: str
    selection_strategy: str
    selection_tie_break: str
    forecast_adapter_version: str
    provider_model: str
    ensemble_member_count: int
    prediction_evidence_version: str
    model_family_version: str
    rule_evidence_version: str
    capture_evidence_version: str
    trusted_wrh_collector_version: str
    wrh_settlement_bridge_version: str
    wrh_finality_policy_id: str
    wrh_max_transition_gap_seconds: int
    wrh_max_following_row_age_seconds: int
    calibration_reader_version: str
    calibration_dataset_version: str
    calibration_engine_version: str
    statistical_policy_status: str
    statistical_policy_id: str | None
    manifest_sha256: str
    prospective_collection_authority: bool = field(init=False, default=True)
    calibrated_probability_authority: bool = field(init=False, default=False)
    financial_authority: bool = field(init=False, default=False)
    financial_delivery: bool = field(init=False, default=False)
    automatic_order_placement: bool = field(init=False, default=False)

    def as_dict(self) -> dict:
        return {
            name: getattr(self, name)
            for name in self.__dataclass_fields__
        }


def _digest_payload(manifest: WeatherCalibrationExperimentManifest) -> dict:
    value = manifest.as_dict()
    value.pop("manifest_sha256", None)
    return value


def build_weather_calibration_experiment_manifest() -> WeatherCalibrationExperimentManifest:
    _assert_internal_policy_consistency()
    shell = WeatherCalibrationExperimentManifest(
        manifest_version=WEATHER_CALIBRATION_EXPERIMENT_VERSION,
        worker_version=WEATHER_CALIBRATION_WORKER_VERSION,
        runtime_version=WEATHER_CALIBRATION_WORKER_RUNTIME_VERSION,
        capture_policy_id=CAPTURE_POLICY_ID,
        capture_local_hour=CAPTURE_LOCAL_HOUR,
        capture_local_minute=CAPTURE_LOCAL_MINUTE,
        capture_window_seconds=CAPTURE_WINDOW_SECONDS,
        horizon_evidence_version=CAPTURE_HORIZON_EVIDENCE_VERSION,
        mapping_policy_id=MAPPING_POLICY.policy_id,
        mapping_quantization=MAPPING_POLICY.quantization,
        include_control=MAPPING_POLICY.include_control,
        selection_policy_id=SELECTION_POLICY.policy_id,
        selection_engine_version=SELECTION_ENGINE_VERSION,
        selection_strategy=SELECTION_STRATEGY,
        selection_tie_break=SELECTION_TIE_BREAK,
        forecast_adapter_version=FORECAST_ADAPTER_VERSION,
        provider_model=OPEN_METEO_GEFS_MODEL,
        ensemble_member_count=GEFS_TOTAL_MEMBERS,
        prediction_evidence_version=PROSPECTIVE_PREDICTION_VERSION,
        model_family_version=MODEL_FAMILY_VERSION,
        rule_evidence_version=PROSPECTIVE_RULE_EVIDENCE_VERSION,
        capture_evidence_version=PROSPECTIVE_CALIBRATION_CAPTURE_VERSION,
        trusted_wrh_collector_version=TRUSTED_WRH_COLLECTOR_AUTHORITY_VERSION,
        wrh_settlement_bridge_version=WRH_SETTLEMENT_BRIDGE_VERSION,
        wrh_finality_policy_id=WRH_CALIBRATION_FINALITY_POLICY.policy_id,
        wrh_max_transition_gap_seconds=WRH_CALIBRATION_FINALITY_POLICY.max_transition_gap_seconds,
        wrh_max_following_row_age_seconds=WRH_CALIBRATION_FINALITY_POLICY.max_following_row_age_seconds,
        calibration_reader_version=WEATHER_CALIBRATION_READER_VERSION,
        calibration_dataset_version=WEATHER_CALIBRATION_DATASET_VERSION,
        calibration_engine_version=CALIBRATION_ENGINE_VERSION,
        statistical_policy_status=STATISTICAL_POLICY_STATUS,
        statistical_policy_id=None,
        manifest_sha256="0" * 64,
    )
    return WeatherCalibrationExperimentManifest(
        **{
            name: getattr(shell, name)
            for name, definition in shell.__dataclass_fields__.items()
            if definition.init and name != "manifest_sha256"
        },
        manifest_sha256=_hash_payload(_digest_payload(shell)),
    )


def validate_weather_calibration_experiment_manifest(
    manifest: object,
) -> WeatherCalibrationExperimentManifest:
    expected = build_weather_calibration_experiment_manifest()
    if not isinstance(manifest, WeatherCalibrationExperimentManifest):
        raise WeatherCalibrationExperimentError("EXPERIMENT_MANIFEST_TYPE_INVALID")
    if manifest != expected:
        raise WeatherCalibrationExperimentError("EXPERIMENT_MANIFEST_DRIFT")
    if (
        manifest.prospective_collection_authority is not True
        or manifest.calibrated_probability_authority is not False
        or manifest.financial_authority is not False
        or manifest.financial_delivery is not False
        or manifest.automatic_order_placement is not False
    ):
        raise WeatherCalibrationExperimentError("EXPERIMENT_AUTHORITY_BOUNDARY_BROKEN")
    if manifest.statistical_policy_id is not None or manifest.statistical_policy_status != STATISTICAL_POLICY_STATUS:
        raise WeatherCalibrationExperimentError("EXPERIMENT_STATISTICAL_POLICY_UNEXPECTEDLY_FROZEN")
    return manifest
