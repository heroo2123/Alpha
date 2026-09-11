from __future__ import annotations

import pytest

from polymarket_scanner.weather_calibration_policy import WEATHER_GEFS_CALIBRATION_POLICY_ID
from polymarket_scanner.weather_calibration_readiness import (
    WeatherCalibrationReadinessError,
    assess_dataset_readiness,
)
from polymarket_scanner.weather_only_calibration import (
    SETTLEMENT_LABEL_EVIDENCE_VERSION,
    ProbabilityCalibrationSample,
)
from polymarket_scanner.weather_only_calibration_dataset import ReconstructedCalibrationDataset
from polymarket_scanner.weather_only_calibration_horizon import CAPTURE_HORIZON_POLICY_ID
from polymarket_scanner.weather_only_predictions import (
    EXACT_SETTLEMENT_SOURCE_ROLE,
    NWS_WRH_EXACT_LABEL_ADAPTER,
)


def _sample(i: int, *, model: str = "model-a", probability: float = 0.95, payout: float = 1.0):
    return ProbabilityCalibrationSample(
        event_id=f"{model}-event-{i}",
        station=f"K{i % 8:03d}"[-4:],
        model_version=model,
        predicted_probability=probability,
        final_payout=payout,
        label_adapter=NWS_WRH_EXACT_LABEL_ADAPTER,
        source_role=EXACT_SETTLEMENT_SOURCE_ROLE,
        evidence_version=SETTLEMENT_LABEL_EVIDENCE_VERSION,
        label_authority=True,
        settlement_state_reconstructable=True,
    )


def _dataset(samples, *, capture_policy=CAPTURE_HORIZON_POLICY_ID):
    samples = tuple(samples)
    return ReconstructedCalibrationDataset(
        version="fixture-strict-dataset",
        reader_version="fixture-reader",
        authorized_row_count=len(samples),
        model_versions=tuple(sorted({sample.model_version for sample in samples})),
        capture_policy_ids=(capture_policy,) if samples else (),
        samples=samples,
    )


def test_readiness_report_keeps_models_separate_and_never_promotes():
    samples = [_sample(i, model="model-a") for i in range(100)]
    samples += [_sample(i, model="model-b", probability=0.55) for i in range(20)]
    report = assess_dataset_readiness(_dataset(samples))

    assert report["statistical_policy_id"] == WEATHER_GEFS_CALIBRATION_POLICY_ID
    assert len(report["experiment_manifest_sha256"]) == 64
    assert len(report["statistical_policy_sha256"]) == 64
    assert report["model_versions"] == ["model-a", "model-b"]
    models = {row["model_version"]: row for row in report["models"]}

    assert models["model-a"]["clean_total_resolved"] == 100
    assert models["model-a"]["ready_bin_indices"] == [9]
    assert models["model-a"]["research_calibration_ready_any_bin"] is True
    assert models["model-b"]["clean_total_resolved"] == 20
    assert models["model-b"]["ready_bin_indices"] == []
    assert models["model-b"]["research_calibration_ready_any_bin"] is False

    assert report["research_calibration_ready_any_model_bin"] is True
    assert report["promotion_authority"] is False
    assert report["calibrated_probability_authority"] is False
    assert report["financial_authority"] is False
    assert report["financial_delivery"] is False
    assert report["automatic_order_placement"] is False


def test_readiness_report_empty_dataset_is_safe_not_ready():
    report = assess_dataset_readiness(_dataset([]))
    assert report["dataset_authorized_row_count"] == 0
    assert report["model_versions"] == []
    assert report["models"] == []
    assert report["research_calibration_ready_any_model_bin"] is False
    assert report["promotion_authority"] is False
    assert report["financial_authority"] is False


def test_readiness_report_rejects_capture_policy_drift():
    with pytest.raises(WeatherCalibrationReadinessError) as raised:
        assess_dataset_readiness(_dataset([_sample(0)], capture_policy="post-hoc-policy"))
    assert raised.value.code == "READINESS_CAPTURE_POLICY_MISMATCH"


def test_readiness_report_does_not_mix_duplicate_or_wrong_model_rows_into_target_model():
    samples = [_sample(i, model="model-a") for i in range(100)]
    # Duplicate event id under another model is filtered by model identity before the
    # per-model duplicate gate; it cannot inflate model-a.
    other = _sample(0, model="model-b", probability=0.95)
    samples.append(other)
    report = assess_dataset_readiness(_dataset(samples))
    models = {row["model_version"]: row for row in report["models"]}
    assert models["model-a"]["clean_total_resolved"] == 100
    assert models["model-b"]["clean_total_resolved"] == 1
    assert models["model-b"]["research_calibration_ready_any_bin"] is False
