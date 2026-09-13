from __future__ import annotations

import json
import sqlite3
from datetime import date, datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from polymarket_scanner.weather_only_calibration_capture import capture_prospective_weather_calibration_candidate
from polymarket_scanner.weather_only_calibration_horizon import (
    build_capture_horizon_evidence,
    persist_capture_horizon_evidence,
)
from polymarket_scanner.weather_only_calibration_reader import (
    WeatherCalibrationReaderError,
    read_reconstructed_calibration_dataset,
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
from polymarket_scanner.weather_only_wrh_collector import CAPTURE_FAILED, WeatherWRHProspectiveCollector


TARGET = date(2026, 9, 11)
END = date(2026, 9, 12)
ZONE = ZoneInfo("America/New_York")
FOLLOWING = datetime.fromisoformat("2026-09-12T00:51:00-04:00").timestamp()
CAPTURED = datetime(2026, 9, 10, 17, 5, tzinfo=ZONE).timestamp()
STATION_METADATA_EVIDENCE_SHA256 = "2" * 64
R26_FAILURE = "SETTLEMENT:SETTLEMENT_EXACT_CUTOFF_STATE_UNPROVEN"


def _rules() -> str:
    return (
        'This market resolves to the range containing the highest reading in the "Temp" column from all times on this day '
        'in Hourly Data after selecting Show Hourly Data at the listed NOAA station, in whole degrees Fahrenheit, on 11 Sep \'26. '
        'The resolution source is https://www.weather.gov/wrh/timeseries?site=KLGA. '
        'If NOAA data for the observation date is unavailable by 11:59 PM ET on the day following the observation date, '
        'the Weather Underground Daily Observations table will be used as the resolution source. '
        'In the event that there is no data for the observation date by 11:59 PM ET on the day following the observation date, '
        'this market will resolve to the lowest bracket. '
        'This market will resolve once the first data point for the following date has been published on the resolution source, '
        'or by 11:59 PM ET on the day following the observation date, whichever comes first. '
        "Revisions to temperatures recorded within this market's timeframe will be considered until the first data point for the "
        'following date has been published, after which any alterations will not be considered.'
    )


def _event() -> dict:
    def market(mid: str, cid: str, question: str) -> dict:
        return {
            "id": mid,
            "conditionId": cid,
            "question": question,
            "description": _rules(),
            "resolutionSource": "https://www.weather.gov/wrh/timeseries?site=KLGA",
            "outcomes": ["Yes", "No"],
            "clobTokenIds": [f"yes-{mid}", f"no-{mid}"],
            "active": True,
            "closed": False,
            "acceptingOrders": True,
            "enableOrderBook": True,
        }

    return {
        "id": "event-reader",
        "slug": "event-reader",
        "title": "Highest temperature in NYC on September 11?",
        "description": _rules(),
        "resolutionSource": "https://www.weather.gov/wrh/timeseries?site=KLGA",
        "markets": [
            market("market-low", "condition-low", "Will the highest temperature in NYC be 79°F or lower on September 11?"),
            market("market-mid", "condition-mid", "Will the highest temperature in NYC be between 80-82°F on September 11?"),
            market("market-high", "condition-high", "Will the highest temperature in NYC be 83°F or higher on September 11?"),
        ],
    }


def _frequency(mid: str, cid: str, lower, upper, hits: int) -> BucketEnsembleFrequency:
    p = hits / 31
    return BucketEnsembleFrequency(
        market_id=mid,
        condition_id=cid,
        yes_token=f"yes-{mid}",
        no_token=f"no-{mid}",
        lower=lower,
        upper=upper,
        member_hits=hits,
        member_count=31,
        raw_member_frequency=p,
        raw_no_frequency=1.0 - p,
        mapping_policy_id="reader-map-v1",
        calibrated=False,
        financial_authority=False,
    )


def _capture():
    forecast = EnsembleBucketForecast(
        adapter=FORECAST_ADAPTER_VERSION,
        event_id="event-reader",
        station="KLGA",
        target_date=TARGET,
        family=DAILY_HIGH,
        unit="F",
        provider_model=OPEN_METEO_GEFS_MODEL,
        source_evidence_sha256="1" * 64,
        mapping_policy_id="reader-map-v1",
        quantization=SUPPORTED_QUANTIZATION,
        included_control=True,
        member_count=31,
        bucket_frequencies=(
            _frequency("market-low", "condition-low", None, 79.0, 7),
            _frequency("market-mid", "condition-mid", 80.0, 82.0, 18),
            _frequency("market-high", "condition-high", 83.0, None, 6),
        ),
        probability_sum=1.0,
        calibrated=False,
        settlement_authority=False,
        financial_authority=False,
    )
    return capture_prospective_weather_calibration_candidate(
        _event(),
        forecast,
        selection_policy=ProspectiveSelectionPolicy("reader-selection-v1"),
        captured_at=CAPTURED,
    )


def _payload(include_following: bool) -> dict:
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
        "UNITS": {"air_temp": "Fahrenheit"},
        "SUMMARY": {"RESPONSE_MESSAGE": "OK"},
        "STATION": [{
            "STID": "KLGA",
            "SHORTNAME": "GLOBAL-METAR",
            "TIMEZONE": "America/New_York",
            "OBSERVATIONS": observations,
        }],
    }


def _snapshot(include_following: bool, received_at: float):
    return parse_synoptic_wrh_hourly_snapshot(
        _payload(include_following),
        station="KLGA",
        target_date=TARGET,
        query_start_date=TARGET,
        query_end_date=END,
        received_at=received_at,
    )


class _SequenceClient:
    def __init__(self, snapshots):
        self.snapshots = list(snapshots)

    def fetch_snapshot(self, *, station, target_date):
        if not self.snapshots:
            raise AssertionError("unexpected fetch")
        return SimpleNamespace(snapshot=self.snapshots.pop(0))


def _r26_failed_db(path):
    """Create the honest current state: polling evidence is terminally unproven."""
    capture = _capture()
    collector = WeatherWRHProspectiveCollector(
        db_path=path,
        client=_SequenceClient([
            _snapshot(False, FOLLOWING - 30),
            _snapshot(True, FOLLOWING + 20),
        ]),
    )
    try:
        collector.register_capture(capture, registered_at=CAPTURED + 10)
        collector.tick(now=FOLLOWING - 30)
        report = collector.tick(now=FOLLOWING + 20)
        assert report.authorized_captures == 0
        assert report.failed_captures == 1
        record = next(row for row in collector.records() if row["capture_evidence_sha256"] == capture.capture_evidence_sha256)
        assert record["status"] == CAPTURE_FAILED
        assert record["failure_code"] == R26_FAILURE
    finally:
        collector.close()

    horizon = build_capture_horizon_evidence(
        capture,
        station_timezone="America/New_York",
        station_metadata_evidence_sha256=STATION_METADATA_EVIDENCE_SHA256,
    )
    with sqlite3.connect(path) as db:
        persist_capture_horizon_evidence(db, horizon, created_at=CAPTURED + 11)
        db.commit()
    return capture


def _authorized_db(path):
    """Seed a legacy AUTHORIZED row solely to test reader rejection/integrity.

    R26 deliberately makes this state impossible through current code.  Marking the
    failed row AUTHORIZED simulates a database created by the older over-strong
    authority model.  The strict reader must never accept it as current truth.
    """
    capture = _r26_failed_db(path)
    with sqlite3.connect(path) as db:
        db.execute(
            """
            UPDATE wrh_collector_captures
            SET status = 'AUTHORIZED',
                settlement_evidence_json = ?,
                authorized_json = ?
            WHERE capture_evidence_sha256 = ?
            """,
            (
                json.dumps({"legacy_polling_authority": True}, sort_keys=True, separators=(",", ":")),
                json.dumps({"sample": {"final_payout": 1.0}}, sort_keys=True, separators=(",", ":")),
                capture.capture_evidence_sha256,
            ),
        )
        db.commit()
    return capture


def test_current_polling_capture_is_not_an_authorized_calibration_row(tmp_path):
    db_path = tmp_path / "calibration.sqlite"
    _r26_failed_db(db_path)
    report = read_reconstructed_calibration_dataset(db_path)
    assert report["read_only_database"] is True
    assert report["source_recomputed"] is True
    assert report["stored_authorized_json_used_as_authority"] is False
    assert report["financial_authority"] is False
    assert report["authorized_row_count"] == 0
    assert report["reconstructed_record_count"] == 0
    assert report["records"] == []
    assert report["status_counts"].get(CAPTURE_FAILED) == 1


def test_reader_rejects_legacy_polling_authorization_instead_of_trusting_stored_json(tmp_path):
    db_path = tmp_path / "legacy-calibration.sqlite"
    _authorized_db(db_path)
    with pytest.raises(WeatherCalibrationReaderError) as raised:
        read_reconstructed_calibration_dataset(db_path)
    assert raised.value.code == "READER_NO_VALID_FINALITY_PAIR"
