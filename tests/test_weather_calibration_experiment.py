from __future__ import annotations

import pytest

import polymarket_scanner.weather_calibration_experiment as experiment
from polymarket_scanner.weather_calibration_experiment import (
    STATISTICAL_POLICY_STATUS,
    WeatherCalibrationExperimentError,
    build_weather_calibration_experiment_manifest,
    validate_weather_calibration_experiment_manifest,
)
from polymarket_scanner.weather_only_calibration_horizon import CAPTURE_HORIZON_POLICY_ID
from polymarket_scanner.weather_only_calibration_worker import CAPTURE_POLICY_ID


def test_experiment_manifest_binds_collection_lineage_but_grants_no_model_or_money_authority():
    manifest = build_weather_calibration_experiment_manifest()
    assert manifest.capture_policy_id == CAPTURE_POLICY_ID == CAPTURE_HORIZON_POLICY_ID
    assert manifest.statistical_policy_status == STATISTICAL_POLICY_STATUS
    assert manifest.statistical_policy_id is None
    assert manifest.prospective_collection_authority is True
    assert manifest.calibrated_probability_authority is False
    assert manifest.financial_authority is False
    assert manifest.financial_delivery is False
    assert manifest.automatic_order_placement is False
    assert len(manifest.manifest_sha256) == 64
    assert validate_weather_calibration_experiment_manifest(manifest) == manifest


def test_experiment_manifest_fails_closed_if_worker_and_horizon_policy_ids_drift(monkeypatch):
    monkeypatch.setattr(experiment, "CAPTURE_POLICY_ID", "drifted-worker-policy")
    with pytest.raises(WeatherCalibrationExperimentError) as raised:
        build_weather_calibration_experiment_manifest()
    assert raised.value.code == "EXPERIMENT_CAPTURE_POLICY_DRIFT"


def test_experiment_manifest_fails_closed_if_mapping_stops_including_control(monkeypatch):
    mapping = experiment.MAPPING_POLICY
    replacement = type(mapping)(
        policy_id=mapping.policy_id,
        include_control=False,
        quantization=mapping.quantization,
    )
    monkeypatch.setattr(experiment, "MAPPING_POLICY", replacement)
    with pytest.raises(WeatherCalibrationExperimentError) as raised:
        build_weather_calibration_experiment_manifest()
    assert raised.value.code == "EXPERIMENT_MAPPING_POLICY_DRIFT"
