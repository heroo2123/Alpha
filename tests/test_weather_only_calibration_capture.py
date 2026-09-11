from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from datetime import date, datetime

import pytest

from polymarket_scanner.weather_only_calibration_capture import (
    PROSPECTIVE_CALIBRATION_CAPTURE_VERSION,
    PROSPECTIVE_RULE_EVIDENCE_VERSION,
    WRH_CALIBRATION_FINALITY_POLICY,
    WRH_SETTLEMENT_BRIDGE_VERSION,
    WeatherCalibrationCaptureError,
    build_wrh_exact_settlement_evidence,
    calibration_sample_from_wrh_settlement_evidence,
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
START = date(2026, 9, 11)
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


def _event(*, reverse_markets: bool = False, rules: str | None = None) -> dict:
    markets = [
        _market("market-low", "condition-low", "Will the highest temperature in NYC be 79°F or lower on September 11?"),
        _market("market-mid", "condition-mid", "Will the highest temperature in NYC be between 80-82°F on September 11?"),
        _market("market-high", "condition-high", "Will the highest temperature in NYC be 83°F or higher on September 11?"),
    ]
    if reverse_markets:
        markets.reverse()
    description = _rules() if rules is None else rules
    for row in markets:
        row["description"] = description
    return {
        "id": "event-nyc-high-2026-09-11",
        "slug": "highest-temperature-in-nyc-on-september-11",
        "title": "Highest temperature in NYC on September 11?",
        "description": description,
        "resolutionSource": "https://www.weather.gov/wrh/timeseries?site=KLGA",
        "markets": markets,
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


def _forecast(*, winner: str = "mid", mid_bounds=(80.0, 82.0)) -> EnsembleBucketForecast:
    hits = {
        "low": 7,
        "mid": 18,
        "high": 6,
    }
    if winner == "high":
        hits = {"low": 7, "mid": 6, "high": 18}
    rows = (
        _frequency("market-low", "condition-low", None, 79.0, hits["low"]),
        _frequency("market-mid", "condition-mid", mid_bounds[0], mid_bounds[1], hits["mid"]),
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


def _selection_policy() -> ProspectiveSelectionPolicy:
    return ProspectiveSelectionPolicy("top-bucket-prospective-v1")


def _payload(*, include_following: bool = True) -> dict:
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


def _capture(*, event=None, forecast=None, captured_at: float = 100.0):
    return capture_prospective_weather_calibration_candidate(
        event or _event(),
        forecast or _forecast(),
        selection_policy=_selection_policy(),
        captured_at=captured_at,
    )


def _settlement(capture=None):
    capture = capture or _capture()
    previous = _snapshot(include_following=False, received_at=FOLLOWING - 30)
    current = _snapshot(include_following=True, received_at=FOLLOWING + 20)
    return build_wrh_exact_settlement_evidence(capture, previous, current)


def test_prospective_capture_binds_rule_source_partition_and_forecast_before_resolution():
    capture = _capture()
    assert capture.capture_version == PROSPECTIVE_CALIBRATION_CAPTURE_VERSION
    assert capture.rule_evidence.evidence_version == PROSPECTIVE_RULE_EVIDENCE_VERSION
    assert capture.rule_evidence.station == "KLGA"
    assert capture.rule_evidence.target_date == TARGET
    assert capture.rule_evidence.observation_population == "WRH_HOURLY_DATA"
    assert capture.rule_evidence.precision == "WHOLE_DEGREE_F"
    assert capture.prediction.market_id == "market-mid"
    assert tuple(bucket.market_id for bucket in capture.rule_evidence.bucket_partition) == (
        "market-high", "market-low", "market-mid"
    )
    assert len(capture.rule_evidence.source_rules_sha256) == 64
    assert len(capture.rule_evidence.rule_evidence_sha256) == 64
    assert len(capture.capture_evidence_sha256) == 64
    assert capture.financial_authority is False
    assert capture.rule_evidence.financial_authority is False
    assert capture.prediction.financial_authority is False


def test_contract_market_order_does_not_change_frozen_rule_or_capture_evidence():
    first = _capture(event=_event(reverse_markets=False))
    second = _capture(event=_event(reverse_markets=True))
    assert first.rule_evidence.source_rules_sha256 == second.rule_evidence.source_rules_sha256
    assert first.rule_evidence.rule_evidence_sha256 == second.rule_evidence.rule_evidence_sha256
    assert first.capture_evidence_sha256 == second.capture_evidence_sha256


def test_forecast_partition_must_exactly_match_prospectively_compiled_contract_partition():
    with pytest.raises(WeatherCalibrationCaptureError) as raised:
        _capture(forecast=_forecast(mid_bounds=(80.0, 83.0)))
    assert raised.value.code == "CAPTURE_FORECAST_BUCKET_PARTITION_MISMATCH"


def test_incomplete_revision_rules_cannot_be_frozen_as_exact_settlement_authority():
    bad_rules = _rules().replace(
        "Revisions to temperatures recorded within this market's timeframe will be considered until the first data point for the following date has been published, after which any alterations will not be considered.",
        "Revisions may be considered until the market resolves.",
    )
    with pytest.raises(WeatherCalibrationCaptureError) as raised:
        _capture(event=_event(rules=bad_rules))
    assert raised.value.code == "RULE_AUTHORITY_NOT_PROVEN"


def test_exact_wrh_bridge_resolves_frozen_selected_bucket_and_keeps_financial_authority_false():
    capture = _capture()
    evidence = _settlement(capture)
    assert evidence.bridge_version == WRH_SETTLEMENT_BRIDGE_VERSION
    assert evidence.target_value_f == 82
    assert evidence.winning_market_id == "market-mid"
    assert evidence.label.market_id == capture.prediction.market_id == "market-mid"
    assert evidence.label.final_payout == 1.0
    assert evidence.finality_policy_id == WRH_CALIBRATION_FINALITY_POLICY.policy_id
    assert evidence.max_transition_gap_seconds == 120
    assert evidence.max_following_row_age_seconds == 120
    assert evidence.calibration_label_authority is True
    assert evidence.financial_authority is False
    assert evidence.finality_state.financial_authority is False
    assert evidence.label.financial_authority is False
    assert len(evidence.source_evidence_sha256) == 64
    assert len(evidence.bridge_evidence_sha256) == 64

    sample = calibration_sample_from_wrh_settlement_evidence(capture, evidence)
    assert sample.event_id == capture.prediction.event_id
    assert sample.station == "KLGA"
    assert sample.final_payout == 1.0
    assert sample.predicted_probability == pytest.approx(18 / 31)


def test_exact_bridge_labels_preselected_losing_bucket_zero_without_retrospective_reselection():
    capture = _capture(forecast=_forecast(winner="high"))
    assert capture.prediction.market_id == "market-high"
    evidence = _settlement(capture)
    assert evidence.target_value_f == 82
    assert evidence.winning_market_id == "market-mid"
    assert evidence.label.market_id == "market-high"
    assert evidence.label.final_payout == 0.0
    sample = calibration_sample_from_wrh_settlement_evidence(capture, evidence)
    assert sample.final_payout == 0.0


def test_post_resolution_event_mutation_cannot_redefine_already_frozen_settlement_partition():
    event = _event()
    capture = _capture(event=event)
    # Mutate caller-owned rule data after prospective capture. Settlement does not
    # accept a fresh event payload, so this cannot alter target bucket semantics.
    event["markets"][1]["question"] = "Will the highest temperature be between 80-99°F?"
    event["description"] = "new rules after resolution"
    evidence = _settlement(capture)
    assert evidence.target_value_f == 82
    assert evidence.winning_market_id == "market-mid"


def test_target_correction_across_cutoff_is_rejected_by_bridge_not_labeled():
    capture = _capture()
    previous = _snapshot(include_following=False, received_at=FOLLOWING - 30)
    changed = deepcopy(_payload(include_following=True))
    changed["STATION"][0]["OBSERVATIONS"]["air_temp_set_1"][0] = 71.4
    current = _snapshot(include_following=True, received_at=FOLLOWING + 20, payload=changed)
    with pytest.raises(WRHSourceError) as raised:
        build_wrh_exact_settlement_evidence(capture, previous, current)
    assert raised.value.code == "WRH_FINALITY_TARGET_STATE_CHANGED_ACROSS_CUTOFF"


def test_capture_digest_and_rule_digest_tampering_fail_before_settlement():
    capture = _capture()
    tampered_capture = replace(capture, capture_evidence_sha256="f" * 64)
    with pytest.raises(WeatherCalibrationCaptureError) as raised:
        _settlement(tampered_capture)
    assert raised.value.code == "CAPTURE_EVIDENCE_DIGEST_MISMATCH"

    tampered_rule = replace(capture.rule_evidence, source_rules_sha256="e" * 64)
    tampered_capture = replace(capture, rule_evidence=tampered_rule)
    with pytest.raises(WeatherCalibrationCaptureError) as raised:
        _settlement(tampered_capture)
    assert raised.value.code == "CAPTURE_RULE_EVIDENCE_DIGEST_MISMATCH"


def test_safe_calibration_join_requires_full_bridge_evidence_not_a_raw_label_envelope():
    capture = _capture()
    evidence = _settlement(capture)
    with pytest.raises(WeatherCalibrationCaptureError) as raised:
        calibration_sample_from_wrh_settlement_evidence(capture, evidence.label)  # type: ignore[arg-type]
    assert raised.value.code == "SETTLEMENT_EVIDENCE_TYPE_INVALID"


def test_bridge_digest_detects_post_settlement_label_or_policy_tampering():
    capture = _capture()
    evidence = _settlement(capture)

    tampered_label = replace(evidence.label, final_payout=0.0)
    tampered = replace(evidence, label=tampered_label)
    with pytest.raises(WeatherCalibrationCaptureError) as raised:
        calibration_sample_from_wrh_settlement_evidence(capture, tampered)
    assert raised.value.code == "SETTLEMENT_BRIDGE_EVIDENCE_DIGEST_MISMATCH"

    tampered_policy = replace(evidence, max_transition_gap_seconds=999)
    with pytest.raises(WeatherCalibrationCaptureError) as raised:
        calibration_sample_from_wrh_settlement_evidence(capture, tampered_policy)
    assert raised.value.code == "SETTLEMENT_FIXED_FINALITY_POLICY_MISMATCH"
