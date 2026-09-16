from __future__ import annotations

from copy import deepcopy
from datetime import date, datetime

import pytest

from polymarket_scanner.weather_only_calibration_authority import (
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
from polymarket_scanner.weather_only_wrh_finality import certify_wrh_first_following_transition


TARGET = date(2026, 9, 11)
START = TARGET
END = date(2026, 9, 12)
FOLLOWING = datetime.fromisoformat("2026-09-12T00:51:00-04:00").timestamp()


def _rules() -> str:
    return (
        'This market resolves to the range containing the highest reading in the "Temp" column from all times on this day '
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
        bucket_frequencies=(
            _frequency("market-low", "condition-low", None, 79.0, hits["low"]),
            _frequency("market-mid", "condition-mid", 80.0, middle_upper, hits["mid"]),
            _frequency("market-high", "condition-high", 83.0, None, hits["high"]),
        ),
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
            "KLGA 110451Z AUTO ...", "KLGA 111651Z AUTO ...",
            "KLGA 111720Z SPECI ...", "KLGA 120359Z AUTO ...",
            "KLGA 120451Z AUTO ...",
        ],
        "sea_level_pressure_set_1": [1012.0, 1010.0, None, 1009.0, 1011.0],
    }
    if not include_following:
        for values in observations.values():
            values.pop()
    return {
        "UNITS": {"air_temp": "Fahrenheit"},
        "SUMMARY": {"RESPONSE_MESSAGE": "OK"},
        "STATION": [{
            "STID": "KLGA", "SHORTNAME": "GLOBAL-METAR",
            "TIMEZONE": "America/New_York", "OBSERVATIONS": observations,
        }],
    }


def _snapshot(*, include_following: bool, received_at: float, payload: dict | None = None):
    return parse_synoptic_wrh_hourly_snapshot(
        payload if payload is not None else _payload(include_following=include_following),
        station="KLGA", target_date=TARGET, query_start_date=START,
        query_end_date=END, received_at=received_at,
    )


def _capture(*, selected: str = "mid"):
    return capture_prospective_weather_calibration_candidate(
        _event(), _forecast(selected=selected),
        selection_policy=ProspectiveSelectionPolicy("top-bucket-prospective-v1"),
        captured_at=100.0,
    )


def _poll_pair():
    return (
        _snapshot(include_following=False, received_at=FOLLOWING - 30),
        _snapshot(include_following=True, received_at=FOLLOWING + 20),
    )


def test_polling_bracket_is_rejected_before_exact_label_is_constructed():
    capture = _capture()
    previous, current = _poll_pair()
    with pytest.raises(WeatherCalibrationCaptureError) as raised:
        build_wrh_exact_settlement_evidence(capture, previous, current)
    assert raised.value.code == "SETTLEMENT_EXACT_CUTOFF_STATE_UNPROVEN"


def test_preselected_loser_is_not_scored_from_polling_only_cutoff_evidence():
    capture = _capture(selected="high")
    previous, current = _poll_pair()
    with pytest.raises(WeatherCalibrationCaptureError) as raised:
        build_wrh_exact_settlement_evidence(capture, previous, current)
    assert raised.value.code == "SETTLEMENT_EXACT_CUTOFF_STATE_UNPROVEN"


def test_forecast_partition_cannot_drift_from_frozen_contract_buckets():
    with pytest.raises(WeatherCalibrationCaptureError) as raised:
        capture_prospective_weather_calibration_candidate(
            _event(), _forecast(middle_upper=83.0),
            selection_policy=ProspectiveSelectionPolicy("top-bucket-prospective-v1"),
            captured_at=100.0,
        )
    assert raised.value.code == "CAPTURE_FORECAST_BUCKET_PARTITION_MISMATCH"


def test_fixed_polling_policy_stays_bounded_but_is_not_upgraded_to_exactness():
    assert WRH_CALIBRATION_FINALITY_POLICY.max_transition_gap_seconds == 120
    assert WRH_CALIBRATION_FINALITY_POLICY.max_following_row_age_seconds == 120
    previous, current = _poll_pair()
    state = certify_wrh_first_following_transition(
        previous,
        current,
        policy=WRH_CALIBRATION_FINALITY_POLICY,
    )
    assert state.transition_gap_seconds <= 120
    assert state.transition_bracket_observed is True
    assert state.exact_publication_state_observed is False
    assert state.calibration_label_authority is False


def test_target_state_change_at_cutoff_still_fails_before_exactness_question():
    capture = _capture()
    previous = _snapshot(include_following=False, received_at=FOLLOWING - 30)
    payload = deepcopy(_payload(include_following=True))
    payload["STATION"][0]["OBSERVATIONS"]["air_temp_set_1"][0] = 71.4
    current = _snapshot(include_following=True, received_at=FOLLOWING + 20, payload=payload)
    with pytest.raises(WRHSourceError) as raised:
        build_wrh_exact_settlement_evidence(capture, previous, current)
    assert raised.value.code == "WRH_FINALITY_TARGET_STATE_CHANGED_ACROSS_CUTOFF"


def test_authority_gate_rejects_non_settlement_evidence_type():
    capture = _capture()
    with pytest.raises(WeatherCalibrationAuthorityError) as raised:
        authorize_wrh_calibration_sample(capture, object())  # type: ignore[arg-type]
    assert raised.value.code == "AUTHORITY_SETTLEMENT_TYPE_INVALID"
