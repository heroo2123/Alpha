from __future__ import annotations

import json
import sqlite3
from datetime import date, datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from polymarket_scanner.weather_only_calibration_capture import (
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
from polymarket_scanner.weather_only_wrh import parse_synoptic_wrh_hourly_snapshot
from polymarket_scanner.weather_only_wrh_collector import (
    CAPTURE_AUTHORIZED,
    CAPTURE_FAILED,
    CAPTURE_PENDING,
    WeatherWRHCollectorError,
    WeatherWRHProspectiveCollector,
)
from polymarket_scanner.weather_only_wrh_collector_authority import (
    TrustedWeatherWRHProspectiveCollector,
)


TARGET = date(2026, 9, 11)
END = date(2026, 9, 12)
ZONE = ZoneInfo("America/New_York")
FOLLOWING = datetime.fromisoformat("2026-09-12T00:51:00-04:00").timestamp()
CAPTURED = datetime(2026, 9, 11, 23, 50, tzinfo=ZONE).timestamp()


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
    count = 31
    probability = hits / count
    return BucketEnsembleFrequency(
        market_id=market_id,
        condition_id=condition_id,
        yes_token=f"yes-{market_id}",
        no_token=f"no-{market_id}",
        lower=lower,
        upper=upper,
        member_hits=hits,
        member_count=count,
        raw_member_frequency=probability,
        raw_no_frequency=1.0 - probability,
        mapping_policy_id="map-v1",
        calibrated=False,
        financial_authority=False,
    )


def _forecast(*, selected: str = "mid") -> EnsembleBucketForecast:
    hits = {"low": 7, "mid": 18, "high": 6}
    if selected == "high":
        hits = {"low": 7, "mid": 6, "high": 18}
    rows = (
        _frequency("market-low", "condition-low", None, 79.0, hits["low"]),
        _frequency("market-mid", "condition-mid", 80.0, 82.0, hits["mid"]),
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


def _capture(*, selected: str = "mid", captured_at: float = CAPTURED):
    return capture_prospective_weather_calibration_candidate(
        _event(),
        _forecast(selected=selected),
        selection_policy=ProspectiveSelectionPolicy("top-bucket-prospective-v1"),
        captured_at=captured_at,
    )


def _payload(*, include_following: bool, target_peak: float = 82.4) -> dict:
    observations = {
        "date_time": [
            "2026-09-11T00:51:00-04:00",
            "2026-09-11T12:51:00-04:00",
            "2026-09-11T13:20:00-04:00",
            "2026-09-11T23:59:00-04:00",
            "2026-09-12T00:51:00-04:00",
        ],
        "air_temp_set_1": [70.4, 80.5, target_peak, 79.4, 75.0],
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


def _snapshot(*, include_following: bool, received_at: float, target_peak: float = 82.4):
    return parse_synoptic_wrh_hourly_snapshot(
        _payload(include_following=include_following, target_peak=target_peak),
        station="KLGA",
        target_date=TARGET,
        query_start_date=TARGET,
        query_end_date=END,
        received_at=received_at,
    )


class _SequenceClient:
    def __init__(self, snapshots):
        self.snapshots = list(snapshots)
        self.calls = []

    def fetch_snapshot(self, *, station, target_date):
        self.calls.append((station, target_date))
        if not self.snapshots:
            raise AssertionError("unexpected collector fetch")
        return SimpleNamespace(snapshot=self.snapshots.pop(0))


class _Clock:
    def __init__(self, values):
        self.values = list(values)

    def __call__(self):
        if not self.values:
            raise AssertionError("unexpected clock read")
        return self.values.pop(0)


def _record_by_digest(collector, digest: str) -> dict:
    return next(row for row in collector.records() if row["capture_evidence_sha256"] == digest)


def _trusted_status_by_digest(collector, digest: str) -> dict:
    return next(row for row in collector.diagnostic_status() if row["capture_evidence_sha256"] == digest)


def test_registration_is_fresh_idempotent_and_rejects_time_forgery(tmp_path):
    capture = _capture()
    collector = WeatherWRHProspectiveCollector(db_path=tmp_path / "collector.sqlite", client=_SequenceClient([]))
    try:
        assert collector.register_capture(capture, registered_at=CAPTURED + 30) == CAPTURE_PENDING
        assert collector.register_capture(capture, registered_at=CAPTURED + 60) == CAPTURE_PENDING
        with pytest.raises(WeatherWRHCollectorError) as stale:
            collector.register_capture(_capture(selected="high"), registered_at=CAPTURED + 121)
        assert stale.value.code == "CAPTURE_REGISTRATION_STALE"
        with pytest.raises(WeatherWRHCollectorError) as future:
            collector.register_capture(_capture(selected="high"), registered_at=CAPTURED - 1)
        assert future.value.code == "CAPTURE_REGISTERED_BEFORE_CAPTURE"
    finally:
        collector.close()


def test_two_captures_share_one_stream_and_authorize_exact_binary_labels(tmp_path):
    previous = _snapshot(include_following=False, received_at=FOLLOWING - 30)
    current = _snapshot(include_following=True, received_at=FOLLOWING + 20)
    client = _SequenceClient([previous, current])
    collector = WeatherWRHProspectiveCollector(db_path=tmp_path / "collector.sqlite", client=client)
    mid = _capture(selected="mid")
    high = _capture(selected="high")
    try:
        collector.register_capture(mid, registered_at=CAPTURED + 10)
        collector.register_capture(high, registered_at=CAPTURED + 10)

        first = collector.tick(now=FOLLOWING - 30)
        assert first.fetched_snapshots == 1
        assert first.authorized_captures == 0
        assert first.financial_authority is False

        second = collector.tick(now=FOLLOWING + 20)
        assert second.fetched_snapshots == 1
        assert second.authorized_captures == 2
        assert second.failed_captures == 0
        assert len(client.calls) == 2

        mid_record = _record_by_digest(collector, mid.capture_evidence_sha256)
        high_record = _record_by_digest(collector, high.capture_evidence_sha256)
        assert mid_record["status"] == CAPTURE_AUTHORIZED
        assert high_record["status"] == CAPTURE_AUTHORIZED
        assert mid_record["authorized"]["sample"]["final_payout"] == 1.0
        assert high_record["authorized"]["sample"]["final_payout"] == 0.0
        assert mid_record["authorized"]["financial_authority"] is False
        assert high_record["authorized"]["financial_authority"] is False
    finally:
        collector.close()


def test_following_row_without_prospective_pre_cutoff_snapshot_fails_closed(tmp_path):
    current = _snapshot(include_following=True, received_at=FOLLOWING + 20)
    collector = WeatherWRHProspectiveCollector(
        db_path=tmp_path / "collector.sqlite",
        client=_SequenceClient([current]),
    )
    capture = _capture()
    try:
        collector.register_capture(capture, registered_at=CAPTURED + 10)
        report = collector.tick(now=FOLLOWING + 20)
        record = _record_by_digest(collector, capture.capture_evidence_sha256)
        assert report.failed_captures == 1
        assert record["status"] == CAPTURE_FAILED
        assert record["failure_code"] == "FINALITY_PRE_CUTOFF_SNAPSHOT_MISSING"
        assert "authorized" not in record
    finally:
        collector.close()


def test_target_correction_across_cutoff_is_not_repaired_by_older_matching_history(tmp_path):
    older_matching = _snapshot(include_following=False, received_at=FOLLOWING - 100, target_peak=83.4)
    latest_pre = _snapshot(include_following=False, received_at=FOLLOWING - 30, target_peak=82.4)
    current = _snapshot(include_following=True, received_at=FOLLOWING + 20, target_peak=83.4)
    client = _SequenceClient([older_matching, latest_pre, current])
    collector = WeatherWRHProspectiveCollector(db_path=tmp_path / "collector.sqlite", client=client)
    capture = _capture()
    try:
        collector.register_capture(capture, registered_at=CAPTURED + 10)
        collector.tick(now=FOLLOWING - 100)
        collector.tick(now=FOLLOWING - 30)
        final = collector.tick(now=FOLLOWING + 20)
        record = _record_by_digest(collector, capture.capture_evidence_sha256)
        assert final.failed_captures == 1
        assert record["status"] == CAPTURE_FAILED
        assert record["failure_code"] == "FINALITY:WRH_FINALITY_TARGET_STATE_CHANGED_ACROSS_CUTOFF"
    finally:
        collector.close()


def test_restart_rehydrates_digest_validated_snapshot_and_completes_cutoff(tmp_path):
    db_path = tmp_path / "collector.sqlite"
    capture = _capture()
    previous = _snapshot(include_following=False, received_at=FOLLOWING - 30)
    first_client = _SequenceClient([previous])
    first = WeatherWRHProspectiveCollector(db_path=db_path, client=first_client)
    first.register_capture(capture, registered_at=CAPTURED + 10)
    first.tick(now=FOLLOWING - 30)
    first.close()

    current = _snapshot(include_following=True, received_at=FOLLOWING + 20)
    second_client = _SequenceClient([current])
    second = WeatherWRHProspectiveCollector(db_path=db_path, client=second_client)
    try:
        report = second.tick(now=FOLLOWING + 20)
        record = _record_by_digest(second, capture.capture_evidence_sha256)
        assert report.authorized_captures == 1
        assert record["status"] == CAPTURE_AUTHORIZED
        assert len(second_client.calls) == 1
    finally:
        second.close()


def test_capture_created_inside_cutoff_collection_window_is_rejected_after_timezone_bootstrap(tmp_path):
    late_captured = datetime(2026, 9, 11, 23, 56, tzinfo=ZONE).timestamp()
    capture = _capture(captured_at=late_captured)
    previous = _snapshot(include_following=False, received_at=late_captured + 10)
    collector = WeatherWRHProspectiveCollector(
        db_path=tmp_path / "collector.sqlite",
        client=_SequenceClient([previous]),
    )
    try:
        collector.register_capture(capture, registered_at=late_captured + 5)
        report = collector.tick(now=late_captured + 10)
        record = _record_by_digest(collector, capture.capture_evidence_sha256)
        assert report.failed_captures == 1
        assert record["status"] == CAPTURE_FAILED
        assert record["failure_code"] == "CAPTURE_NOT_FROZEN_BEFORE_COLLECTION_WINDOW"
    finally:
        collector.close()


def test_trusted_preflight_terminally_rejects_tampered_capture_json_before_fetch(tmp_path):
    db_path = tmp_path / "collector.sqlite"
    capture = _capture()
    client = _SequenceClient([_snapshot(include_following=False, received_at=FOLLOWING - 30)])
    collector = TrustedWeatherWRHProspectiveCollector(
        db_path=db_path,
        client=client,
        clock=_Clock([CAPTURED + 10, FOLLOWING - 30]),
    )
    try:
        collector.register_capture(capture)
        with sqlite3.connect(db_path) as db:
            row = db.execute(
                "SELECT capture_json FROM wrh_collector_captures WHERE capture_evidence_sha256 = ?",
                (capture.capture_evidence_sha256,),
            ).fetchone()
            payload = json.loads(row[0])
            payload["prediction"]["predicted_probability"] = 0.999999
            db.execute(
                "UPDATE wrh_collector_captures SET capture_json = ? WHERE capture_evidence_sha256 = ?",
                (json.dumps(payload, sort_keys=True, separators=(",", ":")), capture.capture_evidence_sha256),
            )

        report = collector.tick()
        status = _trusted_status_by_digest(collector, capture.capture_evidence_sha256)
        assert report.authorized_captures == 0
        assert report.failed_captures == 1
        assert client.calls == []
        assert status["status"] == CAPTURE_FAILED
        assert status["failure_code"].startswith("INTEGRITY_CAPTURE:")
        assert status["authorized_evidence_present"] is False
        assert status["financial_authority"] is False
    finally:
        collector.close()


def test_trusted_preflight_terminally_rejects_tampered_snapshot_after_restart(tmp_path):
    db_path = tmp_path / "collector.sqlite"
    capture = _capture()
    previous = _snapshot(include_following=False, received_at=FOLLOWING - 30)
    current = _snapshot(include_following=True, received_at=FOLLOWING + 20)
    first_client = _SequenceClient([previous])
    first = TrustedWeatherWRHProspectiveCollector(
        db_path=db_path,
        client=first_client,
        clock=_Clock([CAPTURED + 10, FOLLOWING - 30]),
    )
    first.register_capture(capture)
    first_report = first.tick()
    assert first_report.fetched_snapshots == 1
    first.close()

    with sqlite3.connect(db_path) as db:
        row = db.execute(
            "SELECT evidence_sha256, snapshot_json FROM wrh_collector_snapshots WHERE station = 'KLGA' AND target_date = ?",
            (TARGET.isoformat(),),
        ).fetchone()
        payload = json.loads(row[1])
        payload["target_high_f"] = int(payload["target_high_f"]) + 7
        db.execute(
            "UPDATE wrh_collector_snapshots SET snapshot_json = ? WHERE evidence_sha256 = ?",
            (json.dumps(payload, sort_keys=True, separators=(",", ":")), row[0]),
        )

    second_client = _SequenceClient([current])
    second = TrustedWeatherWRHProspectiveCollector(
        db_path=db_path,
        client=second_client,
        clock=_Clock([FOLLOWING + 20]),
    )
    try:
        report = second.tick()
        status = _trusted_status_by_digest(second, capture.capture_evidence_sha256)
        assert report.authorized_captures == 0
        assert report.failed_captures == 1
        assert second_client.calls == []
        assert status["status"] == CAPTURE_FAILED
        assert status["failure_code"].startswith("INTEGRITY_SNAPSHOT:")
        assert status["authorized_evidence_present"] is False
        assert status["financial_authority"] is False
    finally:
        second.close()
