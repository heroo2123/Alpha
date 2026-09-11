from __future__ import annotations

import pytest

from polymarket_scanner.weather_only_calibration import (
    SETTLEMENT_LABEL_EVIDENCE_VERSION,
    CalibrationPolicy,
)
from polymarket_scanner.weather_only_calibration_dataset import (
    WeatherCalibrationDatasetError,
    assess_reconstructed_calibration,
    dataset_from_reader_report,
    read_calibration_dataset,
)
from polymarket_scanner.weather_only_calibration_horizon import CAPTURE_HORIZON_POLICY_ID
from polymarket_scanner.weather_only_calibration_reader import WEATHER_CALIBRATION_READER_VERSION
from polymarket_scanner.weather_only_predictions import (
    EXACT_SETTLEMENT_SOURCE_ROLE,
    NWS_WRH_EXACT_LABEL_ADAPTER,
)

from test_weather_only_calibration_reader import _authorized_db


def _fixture_policy() -> CalibrationPolicy:
    return CalibrationPolicy(
        policy_id="synthetic-one-row-policy-for-plumbing-test-only",
        probability_bins=((0.0, 1.0),),
        min_total_resolved=1,
        min_bin_resolved=1,
        min_distinct_stations=1,
        max_brier_score=1.0,
        wilson_z=1.96,
    )


def _empty_reader_report() -> dict:
    return {
        "version": WEATHER_CALIBRATION_READER_VERSION,
        "read_only_database": True,
        "source_recomputed": True,
        "horizon_recomputed": True,
        "preregistered_capture_horizon_required": True,
        "stored_authorized_json_used_as_authority": False,
        "financial_authority": False,
        "authorized_row_count": 0,
        "reconstructed_record_count": 0,
        "records": [],
    }


def test_dataset_bridge_preserves_exact_recomputed_label_and_horizon_provenance(tmp_path):
    db_path = tmp_path / "dataset.sqlite"
    _authorized_db(db_path)
    dataset = read_calibration_dataset(db_path)

    assert dataset.authorized_row_count == 1
    assert len(dataset.samples) == 1
    assert dataset.source_recomputed is True
    assert dataset.horizon_recomputed is True
    assert dataset.stored_authorized_json_used_as_authority is False
    assert dataset.financial_authority is False
    assert dataset.capture_policy_ids == (CAPTURE_HORIZON_POLICY_ID,)

    sample = dataset.samples[0]
    assert sample.label_adapter == NWS_WRH_EXACT_LABEL_ADAPTER
    assert sample.source_role == EXACT_SETTLEMENT_SOURCE_ROLE
    assert sample.evidence_version == SETTLEMENT_LABEL_EVIDENCE_VERSION
    assert sample.label_authority is True
    assert sample.settlement_state_reconstructable is True
    assert tuple(dataset.model_versions) == (sample.model_version,)


def test_dataset_can_feed_only_caller_supplied_preregistered_policy(tmp_path):
    db_path = tmp_path / "assessment.sqlite"
    _authorized_db(db_path)
    dataset = read_calibration_dataset(db_path)
    model_version = dataset.samples[0].model_version
    probability = dataset.samples[0].predicted_probability

    rebuilt, assessment = assess_reconstructed_calibration(
        db_path,
        model_version=model_version,
        target_probability=probability,
        policy=_fixture_policy(),
    )
    assert rebuilt.authorized_row_count == 1
    assert assessment.clean_total_resolved == 1
    assert assessment.clean_bin_resolved == 1
    assert assessment.distinct_stations == 1
    assert assessment.research_calibration_ready is True
    assert assessment.financial_authority is False


def test_dataset_bridge_rejects_stored_authority_shortcut_even_with_plausible_report():
    report = _empty_reader_report()
    report["stored_authorized_json_used_as_authority"] = True
    with pytest.raises(WeatherCalibrationDatasetError) as raised:
        dataset_from_reader_report(report)
    assert raised.value.code == "DATASET_STORED_AUTHORITY_SHORTCUT"


def test_dataset_bridge_rejects_reader_that_does_not_require_horizon_proof():
    report = _empty_reader_report()
    report["preregistered_capture_horizon_required"] = False
    with pytest.raises(WeatherCalibrationDatasetError) as raised:
        dataset_from_reader_report(report)
    assert raised.value.code == "DATASET_CAPTURE_HORIZON_NOT_REQUIRED"


def test_dataset_bridge_rejects_record_that_loses_recomputed_source_attestation():
    report = _empty_reader_report()
    report.update({
        "authorized_row_count": 1,
        "reconstructed_record_count": 1,
        "records": [{
            "event_id": "e1",
            "station": "KLGA",
            "model_version": "m1",
            "capture_policy_id": CAPTURE_HORIZON_POLICY_ID,
            "capture_horizon_evidence_sha256": "1" * 64,
            "predicted_probability": 0.5,
            "final_payout": 1.0,
            "label_adapter": NWS_WRH_EXACT_LABEL_ADAPTER,
            "source_role": EXACT_SETTLEMENT_SOURCE_ROLE,
            "evidence_version": SETTLEMENT_LABEL_EVIDENCE_VERSION,
            "label_authority": True,
            "settlement_state_reconstructable": True,
            "source_recomputed": False,
            "horizon_recomputed": True,
            "stored_authorized_json_used_as_authority": False,
            "calibration_label_authority": True,
            "financial_authority": False,
        }],
    })
    with pytest.raises(WeatherCalibrationDatasetError) as raised:
        dataset_from_reader_report(report)
    assert raised.value.code == "DATASET_RECORD_SOURCE_NOT_RECOMPUTED"


def test_dataset_bridge_rejects_record_that_loses_horizon_attestation():
    report = _empty_reader_report()
    report.update({
        "authorized_row_count": 1,
        "reconstructed_record_count": 1,
        "records": [{
            "event_id": "e1",
            "station": "KLGA",
            "model_version": "m1",
            "capture_policy_id": CAPTURE_HORIZON_POLICY_ID,
            "capture_horizon_evidence_sha256": "1" * 64,
            "predicted_probability": 0.5,
            "final_payout": 1.0,
            "label_adapter": NWS_WRH_EXACT_LABEL_ADAPTER,
            "source_role": EXACT_SETTLEMENT_SOURCE_ROLE,
            "evidence_version": SETTLEMENT_LABEL_EVIDENCE_VERSION,
            "label_authority": True,
            "settlement_state_reconstructable": True,
            "source_recomputed": True,
            "horizon_recomputed": False,
            "stored_authorized_json_used_as_authority": False,
            "calibration_label_authority": True,
            "financial_authority": False,
        }],
    })
    with pytest.raises(WeatherCalibrationDatasetError) as raised:
        dataset_from_reader_report(report)
    assert raised.value.code == "DATASET_RECORD_HORIZON_NOT_RECOMPUTED"


def test_assessment_refuses_untyped_policy_object(tmp_path):
    db_path = tmp_path / "typed-policy.sqlite"
    _authorized_db(db_path)
    dataset = read_calibration_dataset(db_path)
    with pytest.raises(WeatherCalibrationDatasetError) as raised:
        assess_reconstructed_calibration(
            db_path,
            model_version=dataset.samples[0].model_version,
            target_probability=dataset.samples[0].predicted_probability,
            policy={"min_total_resolved": 1},  # type: ignore[arg-type]
        )
    assert raised.value.code == "DATASET_CALIBRATION_POLICY_REQUIRED"
