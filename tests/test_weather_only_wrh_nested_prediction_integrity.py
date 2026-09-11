from __future__ import annotations

import json
import sqlite3
from dataclasses import replace
from datetime import date

from polymarket_scanner.weather_only_calibration_capture import (
    PROSPECTIVE_CALIBRATION_CAPTURE_VERSION,
    PROSPECTIVE_RULE_EVIDENCE_VERSION,
    FrozenRuleBucket,
    ProspectiveNWSRuleEvidence,
    ProspectiveWeatherCalibrationCapture,
    _capture_digest_payload,
    _hash_payload,
    _rule_digest_payload,
)
from polymarket_scanner.weather_only_contracts import DAILY_HIGH, SOURCE_NWS_WRH
from polymarket_scanner.weather_only_forecast import (
    FORECAST_ADAPTER_VERSION,
    OPEN_METEO_GEFS_MODEL,
    SUPPORTED_QUANTIZATION,
)
from polymarket_scanner.weather_only_predictions import (
    PROSPECTIVE_PREDICTION_VERSION,
    SELECTION_ENGINE_VERSION,
    ProspectiveBucketPrediction,
    _prediction_digest,
)
from polymarket_scanner.weather_only_wrh_collector import CAPTURE_FAILED, CAPTURE_PENDING
from polymarket_scanner.weather_only_wrh_collector_authority import (
    TrustedWeatherWRHProspectiveCollector,
)


TARGET = date(2026, 9, 11)
CAPTURED = 1789140000.0


class _Clock:
    def __init__(self, values):
        self.values = list(values)

    def __call__(self):
        if not self.values:
            raise AssertionError("unexpected clock read")
        return self.values.pop(0)


class _NoFetchClient:
    def __init__(self):
        self.calls = []

    def fetch_snapshot(self, *, station, target_date):
        self.calls.append((station, target_date))
        raise AssertionError("integrity preflight must contain tampering before source fetch")


def _capture() -> ProspectiveWeatherCalibrationCapture:
    prediction_shell = ProspectiveBucketPrediction(
        evidence_version=PROSPECTIVE_PREDICTION_VERSION,
        selector_version=SELECTION_ENGINE_VERSION,
        selection_policy_id="prospective-test-v1",
        model_version="weather-test-model-v1",
        event_id="event-1",
        market_id="market-1",
        condition_id="condition-1",
        station="KLGA",
        target_date=TARGET,
        family=DAILY_HIGH,
        unit="F",
        provider_model=OPEN_METEO_GEFS_MODEL,
        forecast_adapter=FORECAST_ADAPTER_VERSION,
        source_evidence_sha256="1" * 64,
        forecast_snapshot_sha256="2" * 64,
        mapping_policy_id="map-test-v1",
        quantization=SUPPORTED_QUANTIZATION,
        included_control=True,
        member_count=31,
        raw_predicted_probability=0.5,
        captured_at=CAPTURED,
        prediction_evidence_sha256="0" * 64,
    )
    prediction = replace(
        prediction_shell,
        prediction_evidence_sha256=_prediction_digest(prediction_shell),
    )

    rule_shell = ProspectiveNWSRuleEvidence(
        evidence_version=PROSPECTIVE_RULE_EVIDENCE_VERSION,
        event_id="event-1",
        station="KLGA",
        target_date=TARGET,
        family=DAILY_HIGH,
        unit="F",
        source_family=SOURCE_NWS_WRH,
        compiler_version="compiler-test-v1",
        rule_authority_version="rule-authority-test-v1",
        rule_profile="profile-test-v1",
        observation_population="WRH_HOURLY_DATA",
        precision="WHOLE_DEGREE_F",
        fallback_policy="WEATHER_UNDERGROUND_IF_WRH_UNAVAILABLE_BY_NEXT_DAY_2359_ET",
        finality_policy="FIRST_FOLLOWING_DATE_DATAPOINT_OR_NEXT_DAY_2359_ET",
        correction_policy="ACCEPT_REVISIONS_UNTIL_FIRST_FOLLOWING_DATE_DATAPOINT",
        no_data_outcome="LOWEST_BRACKET",
        bucket_partition=(FrozenRuleBucket(
            market_id="market-1",
            condition_id="condition-1",
            lower=None,
            upper=None,
            unit="F",
        ),),
        source_rules_sha256="3" * 64,
        captured_at=CAPTURED,
        rule_evidence_sha256="0" * 64,
    )
    rule = replace(
        rule_shell,
        rule_evidence_sha256=_hash_payload(_rule_digest_payload(rule_shell)),
    )

    capture_shell = ProspectiveWeatherCalibrationCapture(
        capture_version=PROSPECTIVE_CALIBRATION_CAPTURE_VERSION,
        prediction=prediction,
        rule_evidence=rule,
        captured_at=CAPTURED,
        capture_evidence_sha256="0" * 64,
    )
    return replace(
        capture_shell,
        capture_evidence_sha256=_hash_payload(_capture_digest_payload(capture_shell)),
    )


def test_real_nested_prediction_field_tamper_is_terminal_before_live_fetch(tmp_path):
    db_path = tmp_path / "collector.sqlite"
    capture = _capture()
    client = _NoFetchClient()
    collector = TrustedWeatherWRHProspectiveCollector(
        db_path=db_path,
        client=client,
        clock=_Clock([CAPTURED + 10.0, CAPTURED + 20.0]),
    )
    try:
        assert collector.register_capture(capture) == CAPTURE_PENDING
        with sqlite3.connect(db_path) as db:
            row = db.execute(
                "SELECT capture_json FROM wrh_collector_captures WHERE capture_evidence_sha256 = ?",
                (capture.capture_evidence_sha256,),
            ).fetchone()
            payload = json.loads(row[0])
            payload["prediction"]["raw_predicted_probability"] = 0.9
            db.execute(
                "UPDATE wrh_collector_captures SET capture_json = ? WHERE capture_evidence_sha256 = ?",
                (json.dumps(payload, sort_keys=True, separators=(",", ":")), capture.capture_evidence_sha256),
            )

        report = collector.tick()
        status = collector.diagnostic_status()[0]
        assert report.authorized_captures == 0
        assert report.failed_captures == 1
        assert client.calls == []
        assert status["status"] == CAPTURE_FAILED
        assert "CALIBRATION_PREDICTION_DIGEST_MISMATCH" in status["failure_code"]
        assert status["authorized_evidence_present"] is False
        assert status["financial_authority"] is False
    finally:
        collector.close()
