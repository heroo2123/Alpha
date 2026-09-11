from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from datetime import date, datetime

import pytest

from polymarket_scanner.weather_only_calibration_authority import (
    WRH_CALIBRATION_AUTHORITY_VERSION,
    WeatherCalibrationAuthorityError,
    authorize_wrh_calibration_sample,
)
from polymarket_scanner.weather_only_calibration_capture import (
    WRH_CALIBRATION_FINALITY_POLICY,
    WeatherCalibrationCaptureError,
    build_wrh_exact_settlement_evidence,
    capture_prospective_weather_calibration_candidate,
)
from polymarket_scanner.weather_only_contracts import DAILY_HIGH
from polymarket_scanner.weather_only_forecast import (
    FORECAST_ADAPTER_VERSION,
    OPEN_METEO_GEFS_MODEL,
    SUPPORTED_QUANTIZATION,
    BucketEnsembleFrequency,
    EnsembleBucketForecast,
)
from polymarket_scanner.weather_only_predictions import ProspectiveSelectionPolicy
from polymarket_scanner.weather_only_wrh import WRHSourceError, parse_synoptic_wrh_hourly_snapshot


TARGET = date(2026, 9, 11)
START = TARGET
END = date(2026, 9, 12)
FOLLOWING = datetime.fromisoformat("2026-09-12T00:51:00-04:00").timestamp()


def _rules() -> str:
    return (
        "This market resolves to the range containing the highest reading in the \"Temp\" column from all times on this day "
        "in Hourly Data after selecting Show Hourly Data at the listed NOAA station, in whole degrees Fahrenheit, on 11 Sep '26. "
        "The resolution source is https://www.weather.gov/wrh/timeseries?site=KLGA. "
        "If NOAA data for the observation date is unavailable by 11:59 PM ET on the day following the observation date, "
        "the Weather Underground Daily Observations table will be used as the resolution source. "
        "In the event that there is no data for the observation date by 11:59 PM ET on the day following the observation date, "
        "this market will resolve to the lowest bracket. "
        "This market will resolve once the first data point for the following date has been published on the resolution source, "
        "or by 11:59 PM ET on the day following the observation date, whichever comes first. "
        "Revisions to temperatures recorded within this market's timeframe will be considered until the first data point for the "
        "following date has been published, after which any alterations will not be considered."
    )


def _market(market_id: str, condition_id: str, question: str) -> dict:
    return {
        "id": market_id,
        "conditionId": condition_id,
        "question": question,
        "slug": f"slug-{market_id}",
        "description": _rules(),
        "resolutionSource": "https://www.weather.gov/wrh/timeseries?site=KLGA",
        "outcomes": ["Yes", "No"],
        "clobTokenIds": [f"yes-{market_id}", f"no-{market_id}"],
        "active": True,
        "closed": False,
        "acceptingOrders": True,
        "enableOrderBook": True,
    }


def _event() -> dict:
    return {
        "id": "event-nyc-high-2026-09-11",
        "slug": "highest-temperature-in-nyc-on-september-11",
        "title": "Highest temperature in NYC on September 11?",
        "description": _rules(),
        "resolutionSource": "https://www.weather.gov/wrh/timeseries?site=KLGA",
        "markets": [
            _market("market-low", "condition-low", "Will the highest temperature in NYC be 79°F or lower on September 11?"),
            _market("market-mid", "condition-mid", "Will the highest temperature in NYC be between 80-82°F on September 11?"),
            _market("market-high", "condition-high", "Will the highest temperature in NYC be 83°F or higher on September 11?"),
        ],
    }


def _frequency(market_id: str, condition_id: str, lower, upper, hits: int) -> BucketEnsembleFrequency:
    n = 31
    p = hits / n
    return BucketEnsembleFrequency(
        market_id=market_id,
        condition_id=condition_id,
        yes_token=f"yes-{market_id}",
        no_token=f"no-{market_id}",
        lower=lower,
        upper=upper,
        member_hits=hits,
        member_count=n,
        raw_member_frequency=p,
        raw_no_frequency=1.0 - p,
        mapping_policy_id="map-v1",
        calibrated=False,
        financial_authority=False,
    )


def _forecast(*, selected: str = "mid", middle_upper: float = 82.0) -> EnsembleBucketForecast:
    hits = {"low": 7, "mid": 18, "high": 6}
    if selected == "high":
        hits = {"low": 7, "mid": 6, "high": 18}
    rows = (
        _frequency("market-low", "condition-low", None, 79.0, hits["low"]),
        _frequency("market-mid", "condition-mid", 80.0, middle_upper, hits["mid"]),
        _frequency("market-high", "condition-high", 83.0, None, hits["high"]),
    )
    return EnsembleBucketForecast(
        adapter=FORECAST_ADAPTER_VERSION,
        event_id="event-nyc-high-2026-09-11",
        station="KLGA",
        target_date=TARGET,
        family=DAILY_HIGH,
        unit="F",
        provider_model=OPEN_METEO_GEFS_MODEL,
        source_evidence_sha256="1" * 64,
        mapping_policy_id="map-v1",
        quantization=SUPPORTED_QUANTIZATION,
        included_control=True,
        member_count=31,
        bucket_frequencies=rows,
        probability_sum=1.0,
        calibrated=False,
        settlement_authority=False,
        financial_authority=False,
    )


def _payload(*, include_following: bool) -> dict:
    observations = {
        "date_time": [
            "2026-09-11T00:51:00-04:00",
            "2026-09-11T12:51:00-04:00",
            "2026-09-11T13:20:00-04:00",
            "2026-09-11T23:59:00-04:00",
            "2026-09-12T00:51:00-04:00",
        ],
        "air_temp_set_1": [70.4, 80.5, 82.4, 79.4, 75.0],
        "metar_set_1": [
            "KLGA 110451Z AUTO ...",
            "KLGA 111651Z AUTO ...",
            "KLGA 111720Z SPECI ...",
            "KLGA 120359Z AUTO ...",
            "KLGA 120451Z AUTO ...",
        ],
        "sea_level_pressure_set_1": [1012.0, 1010.0, None, 1009.0, 1011.0],
    }
    if not include_following:
        for values in observations.values():
            values.pop()
    return {
        "SUMMARY": {"RESPONSE_MESSAGE": "OK"},
        "STATION": [{
            "STID": "KLGA",
            "SHORTNAME": "GLOBAL-METAR",
            "TIMEZONE": "America/New_York",
            "OBSERVATIONS": observations,
        }],
    }


def _snapshot(*, include_following: bool, received_at: float, payload: dict | None = None):
    return parse_synoptic_wrh_hourly_snapshot(
        payload if payload is not None else _payload(include_following=include_following),
        station="KLGA",
        target_date=TARGET,
        query_start_date=START,
        query_end_date=END,
        received_at=received_at,
    )


def _capture(*, selected: str = "mid"):
    return capture_prospective_weather_calibration_candidate(
        _event(),
        _forecast(selected=selected),
        selection_policy=ProspectiveSelectionPolicy("top-bucket-prospective-v1"),
        captured_at=100.0,
    )


def _evidence(capture=None):
    capture = capture or _capture()
    previous = _snapshot(include_following=False, received_at=FOLLOWING - 30)
    current = _snapshot(include_following=True, received_at=FOLLOWING + 20)
    return build_wrh_exact_settlement_evidence(capture, previous, current)


def test_strict_authority_gate_revalidates_full_lineage_and_returns_nonfinancial_sample():
    capture = _capture()
    evidence = _evidence(capture)
    authorized = authorize_wrh_calibration_sample(capture, evidence)

    assert authorized.authority_version == WRH_CALIBRATION_AUTHORITY_VERSION
    assert authorized.sample.event_id == capture.prediction.event_id
    assert authorized.sample.station == "KLGA"
    assert authorized.sample.predicted_probability == pytest.approx(18 / 31)
    assert authorized.sample.final_payout == 1.0
    assert authorized.calibration_label_authority is True
    assert authorized.financial_authority is False
    assert len(authorized.authority_evidence_sha256) == 64
    assert authorized.capture_evidence_sha256 == capture.capture_evidence_sha256
    assert authorized.label_evidence_sha256 == evidence.label.label_evidence_sha256


def test_preselected_loser_remains_zero_payout_no_retrospective_reselection():
    capture = _capture(selected="high")
    assert capture.prediction.market_id == "market-high"
    evidence = _evidence(capture)
    authorized = authorize_wrh_calibration_sample(capture, evidence)
    assert evidence.target_value_f == 82
    assert evidence.winning_market_id == "market-mid"
    assert authorized.sample.final_payout == 0.0


def test_forecast_partition_cannot_drift_from_frozen_contract_buckets():
    with pytest.raises(WeatherCalibrationCaptureError) as raised:
        capture_prospective_weather_calibration_candidate(
            _event(),
            _forecast(middle_upper=83.0),
            selection_policy=ProspectiveSelectionPolicy("top-bucket-prospective-v1"),
            captured_at=100.0,
        )
    assert raised.value.code == "CAPTURE_FORECAST_BUCKET_PARTITION_MISMATCH"


def test_prediction_tampering_is_caught_by_authority_even_when_capture_digest_string_is_unchanged():
    capture = _capture()
    evidence = _evidence(capture)
    prediction = replace(
        capture.prediction,
        raw_predicted_probability=capture.prediction.raw_predicted_probability - 0.01,
    )
    tampered_capture = replace(capture, prediction=prediction)
    with pytest.raises(WeatherCalibrationAuthorityError) as raised:
        authorize_wrh_calibration_sample(tampered_capture, evidence)
    assert raised.value.code == "AUTHORITY_PREDICTION_INVALID:CALIBRATION_PREDICTION_DIGEST_MISMATCH"


def test_finalized_wrh_state_digest_and_extreme_are_independently_revalidated():
    capture = _capture()
    evidence = _evidence(capture)

    bad_digest_state = replace(evidence.finality_state, finality_evidence_sha256="f" * 64)
    with pytest.raises(WeatherCalibrationAuthorityError) as raised:
        authorize_wrh_calibration_sample(capture, replace(evidence, finality_state=bad_digest_state))
    assert raised.value.code == "AUTHORITY_FINALITY_DIGEST_MISMATCH"

    bad_high_state = replace(evidence.finality_state, target_high_f=83)
    with pytest.raises(WeatherCalibrationAuthorityError) as raised:
        authorize_wrh_calibration_sample(capture, replace(evidence, finality_state=bad_high_state))
    assert raised.value.code == "AUTHORITY_FINALITY_HIGH_INCONSISTENT"


def test_label_payout_tampering_is_rejected_at_outer_authority_boundary():
    capture = _capture()
    evidence = _evidence(capture)
    tampered_label = replace(evidence.label, final_payout=0.0)
    with pytest.raises(WeatherCalibrationAuthorityError) as raised:
        authorize_wrh_calibration_sample(capture, replace(evidence, label=tampered_label))
    assert raised.value.code == "AUTHORITY_LABEL_PAYOUT_MISMATCH"


def test_label_digest_tampering_is_rejected_even_when_semantic_fields_still_match():
    capture = _capture()
    evidence = _evidence(capture)
    tampered_label = replace(evidence.label, label_evidence_sha256="e" * 64)
    with pytest.raises(WeatherCalibrationAuthorityError) as raised:
        authorize_wrh_calibration_sample(capture, replace(evidence, label=tampered_label))
    assert raised.value.code == "AUTHORITY_BRIDGE_DIGEST_MISMATCH"


def test_fixed_finality_policy_cannot_be_widened_after_resolution():
    capture = _capture()
    evidence = _evidence(capture)
    assert WRH_CALIBRATION_FINALITY_POLICY.max_transition_gap_seconds == 120
    assert WRH_CALIBRATION_FINALITY_POLICY.max_following_row_age_seconds == 120

    tampered = replace(evidence, max_transition_gap_seconds=999)
    with pytest.raises(WeatherCalibrationAuthorityError) as raised:
        authorize_wrh_calibration_sample(capture, tampered)
    assert raised.value.code == "AUTHORITY_FIXED_FINALITY_POLICY_MISMATCH"


def test_target_state_change_at_cutoff_never_reaches_authority_gate():
    capture = _capture()
    previous = _snapshot(include_following=False, received_at=FOLLOWING - 30)
    payload = deepcopy(_payload(include_following=True))
    payload["STATION"][0]["OBSERVATIONS"]["air_temp_set_1"][0] = 71.4
    current = _snapshot(include_following=True, received_at=FOLLOWING + 20, payload=payload)
    with pytest.raises(WRHSourceError) as raised:
        build_wrh_exact_settlement_evidence(capture, previous, current)
    assert raised.value.code == "WRH_FINALITY_TARGET_STATE_CHANGED_ACROSS_CUTOFF"


def test_authority_gate_rejects_raw_label_in_place_of_full_settlement_evidence():
    capture = _capture()
    evidence = _evidence(capture)
    with pytest.raises(WeatherCalibrationAuthorityError) as raised:
        authorize_wrh_calibration_sample(capture, evidence.label)  # type: ignore[arg-type]
    assert raised.value.code == "AUTHORITY_SETTLEMENT_TYPE_INVALID"


def test_outer_bridge_digest_tampering_is_rejected():
    capture = _capture()
    evidence = _evidence(capture)
    with pytest.raises(WeatherCalibrationAuthorityError) as raised:
        authorize_wrh_calibration_sample(capture, replace(evidence, bridge_evidence_sha256="d" * 64))
    assert raised.value.code == "AUTHORITY_BRIDGE_DIGEST_MISMATCH"
