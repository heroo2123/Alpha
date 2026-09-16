from __future__ import annotations

import asyncio
from datetime import date, datetime, timezone
from types import SimpleNamespace

import pytest

from polymarket_scanner.weather_only_calibration_worker import (
    CAPTURE_POLICY_ID,
    STATE_FAILED,
    STATE_REGISTERED,
    WeatherCalibrationResearchWorker,
)
from polymarket_scanner.weather_only_contracts import DAILY_HIGH
from polymarket_scanner.weather_only_forecast import (
    GEFS_CONTROL_KEY_HIGH,
    GEFS_PERTURBED_MEMBERS,
    parse_open_meteo_gefs_daily_extreme,
)
from polymarket_scanner.weather_only_station_metadata import parse_nws_station_metadata
from polymarket_scanner.weather_only_wrh_collector import CAPTURE_PENDING, CollectorTickReport


TARGET = date(2026, 9, 11)
# V3 preregisters 17:00-17:15 in the settlement station's local timezone.
# KLGA is UTC-4 on 2026-09-10, so 17:05 America/New_York is 21:05 UTC.
WINDOW = datetime(2026, 9, 10, 21, 5, tzinfo=timezone.utc).timestamp()


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


def _distribution(received_at: float):
    variable = GEFS_CONTROL_KEY_HIGH
    values = [81.0] * 18 + [78.0] * 7 + [84.0] * 6
    assert len(values) == 31
    daily = {"time": [TARGET.isoformat()]}
    units = {"time": "iso8601"}
    keys = [variable] + [f"{variable}_member{i:02d}" for i in range(1, GEFS_PERTURBED_MEMBERS + 1)]
    for key, value in zip(keys, values):
        daily[key] = [value]
        units[key] = "°F"
    return parse_open_meteo_gefs_daily_extreme(
        {
            "latitude": 40.75,
            "longitude": -73.875,
            "timezone": "America/New_York",
            "daily": daily,
            "daily_units": units,
        },
        station="KLGA",
        target_date=TARGET,
        family=DAILY_HIGH,
        unit="F",
        timezone="America/New_York",
        requested_latitude=40.7794,
        requested_longitude=-73.8803,
        received_at=received_at,
    )


def _station_metadata(received_at: float):
    return parse_nws_station_metadata(
        {
            "id": "https://api.weather.gov/stations/KLGA",
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [-73.8803, 40.7794]},
            "properties": {
                "@id": "https://api.weather.gov/stations/KLGA",
                "stationIdentifier": "KLGA",
                "timeZone": "America/New_York",
            },
        },
        requested_station="KLGA",
        received_at=received_at,
    )


class _Clock:
    def __init__(self, values):
        self.values = list(values)

    def __call__(self):
        if not self.values:
            raise AssertionError("unexpected clock read")
        return self.values.pop(0)


class _Discovery:
    def __init__(self, events):
        self.events = tuple(events)
        self.calls = 0

    async def discover(self, tags):
        self.calls += 1
        return SimpleNamespace(
            events=self.events,
            summary=lambda: {
                "unique_event_count": len(self.events),
                "unique_market_count": sum(len(row.get("markets") or []) for row in self.events),
            },
        )


class _StationClient:
    def __init__(self, metadata):
        self.metadata = metadata
        self.calls = 0

    async def station(self, station):
        self.calls += 1
        assert station == "KLGA"
        return self.metadata


class _ForecastClient:
    def __init__(self, distribution):
        self.distribution = distribution
        self.calls = 0

    async def daily_extreme(self, **kwargs):
        self.calls += 1
        assert kwargs["station"] == "KLGA"
        assert kwargs["target_date"] == TARGET
        return self.distribution


class _Collector:
    def __init__(self):
        self.registered = []
        self.ticks = 0
        self.register_calls = 0

    def diagnostic_status(self):
        return [
            {
                "capture_evidence_sha256": capture.capture_evidence_sha256,
                "status": CAPTURE_PENDING,
            }
            for capture in self.registered
        ]

    def register_capture(self, capture):
        self.register_calls += 1
        self.registered.append(capture)
        return CAPTURE_PENDING

    def tick(self):
        self.ticks += 1
        return CollectorTickReport(
            collector_version="collector-test",
            policy_id="collector-test",
            evaluated_station_dates=0,
            fetched_snapshots=0,
            authorized_captures=0,
            failed_captures=0,
            deferred_station_dates=0,
            fetch_errors=(),
            financial_authority=False,
            financial_delivery=False,
            automatic_order_placement=False,
        )


class _RegistrationFailCollector(_Collector):
    def register_capture(self, capture):
        self.register_calls += 1
        raise RuntimeError("simulated registration failure")


def _event_state(worker):
    return worker.state.db.execute(
        "SELECT * FROM weather_calibration_worker_events WHERE event_id = ?",
        (_event()["id"],),
    ).fetchone()


def test_worker_freezes_exactly_one_capture_per_event_and_reuses_durable_reservation(tmp_path):
    async def scenario():
        discovery = _Discovery([_event()])
        station = _StationClient(_station_metadata(WINDOW + 0.25))
        forecast = _ForecastClient(_distribution(WINDOW + 0.5))
        collector = _Collector()
        clock = _Clock([
            WINDOW,
            WINDOW + 1.0,
            WINDOW + 2.0,
            WINDOW + 3.0,
            WINDOW + 4.0,
            WINDOW + 5.0,
            WINDOW + 60.0,
            WINDOW + 61.0,
        ])
        worker = WeatherCalibrationResearchWorker(
            db_path=tmp_path / "worker.sqlite",
            discovery=discovery,
            station_client=station,
            forecast_client=forecast,
            collector=collector,
            clock=clock,
        )
        try:
            first = await worker.run_cycle()
            assert first["capture"]["active_window_events"] == 1
            assert first["capture"]["registered_events"] == 1
            assert first["capture"]["capture_policy_id"] == CAPTURE_POLICY_ID
            assert len(collector.registered) == 1
            capture = collector.registered[0]
            assert capture.prediction.event_id == _event()["id"]
            assert capture.prediction.target_date == TARGET
            assert capture.prediction.raw_predicted_probability == pytest.approx(18 / 31)
            assert capture.financial_authority is False
            assert collector.ticks == 1

            second = await worker.run_cycle()
            assert second["capture"]["registered_events"] == 0
            assert second["capture"]["already_reserved_events"] == 1
            assert len(collector.registered) == 1
            assert collector.register_calls == 1
            assert station.calls == 1
            assert forecast.calls == 1
            assert collector.ticks == 2

            row = _event_state(worker)
            assert row["status"] == STATE_REGISTERED
            assert row["capture_policy_id"] == CAPTURE_POLICY_ID
            assert row["capture_evidence_sha256"] == capture.capture_evidence_sha256
            assert row["forecast_source_evidence_sha256"] == capture.prediction.source_evidence_sha256
            assert len(row["station_metadata_json"]) > 0
            assert len(row["distribution_json"]) > 0
            assert len(row["forecast_json"]) > 0
            assert len(row["rule_source_json"]) > 0
        finally:
            await worker.close()

    asyncio.run(scenario())


def test_worker_before_local_window_discovers_and_resolves_metadata_but_never_forecasts(tmp_path):
    async def scenario():
        # 16:00 UTC is noon at KLGA on this date, before its 17:00 local window.
        outside = datetime(2026, 9, 10, 16, 0, tzinfo=timezone.utc).timestamp()
        discovery = _Discovery([_event()])
        station = _StationClient(_station_metadata(outside))
        forecast = _ForecastClient(_distribution(outside))
        collector = _Collector()
        worker = WeatherCalibrationResearchWorker(
            db_path=tmp_path / "worker.sqlite",
            discovery=discovery,
            station_client=station,
            forecast_client=forecast,
            collector=collector,
            clock=_Clock([outside, outside + 1.0]),
        )
        try:
            report = await worker.run_cycle()
            assert report["capture"]["active_window_events"] == 0
            assert report["capture"]["before_window_events"] == 1
            assert report["capture"]["registered_events"] == 0
            assert discovery.calls == 1
            assert station.calls == 1
            assert forecast.calls == 0
            assert collector.registered == []
            assert collector.register_calls == 0
            assert collector.ticks == 1
            assert report["financial_authority"] is False
            assert report["financial_delivery"] is False
            assert report["automatic_order_placement"] is False
            assert report["telegram_delivery"] is False
        finally:
            await worker.close()

    asyncio.run(scenario())


def test_registration_failure_terminally_consumes_event_and_cannot_select_replacement_forecast(tmp_path):
    async def scenario():
        discovery = _Discovery([_event()])
        station = _StationClient(_station_metadata(WINDOW + 0.25))
        forecast = _ForecastClient(_distribution(WINDOW + 0.5))
        collector = _RegistrationFailCollector()
        worker = WeatherCalibrationResearchWorker(
            db_path=tmp_path / "worker.sqlite",
            discovery=discovery,
            station_client=station,
            forecast_client=forecast,
            collector=collector,
            clock=_Clock([
                WINDOW,
                WINDOW + 1.0,
                WINDOW + 2.0,
                WINDOW + 3.0,
                WINDOW + 4.0,
                WINDOW + 5.0,
                WINDOW + 60.0,
                WINDOW + 61.0,
            ]),
        )
        try:
            first = await worker.run_cycle()
            assert first["capture"]["reserved_events"] == 1
            assert first["capture"]["registered_events"] == 0
            assert first["capture"]["errors"]["COLLECTOR_REGISTER:RuntimeError"] == 1
            row = _event_state(worker)
            assert row["status"] == STATE_FAILED
            assert row["failure_code"] == "COLLECTOR_REGISTER:RuntimeError"

            second = await worker.run_cycle()
            assert second["capture"]["already_reserved_events"] == 1
            assert second["capture"]["registered_events"] == 0
            assert collector.register_calls == 1
            assert station.calls == 1
            assert forecast.calls == 1
        finally:
            await worker.close()

    asyncio.run(scenario())


def test_crash_after_collector_registration_reconciles_digest_without_new_forecast(tmp_path):
    async def scenario():
        db_path = tmp_path / "worker.sqlite"
        collector = _Collector()
        first_discovery = _Discovery([_event()])
        first_station = _StationClient(_station_metadata(WINDOW + 0.25))
        first_forecast = _ForecastClient(_distribution(WINDOW + 0.5))
        first = WeatherCalibrationResearchWorker(
            db_path=db_path,
            discovery=first_discovery,
            station_client=first_station,
            forecast_client=first_forecast,
            collector=collector,
            clock=_Clock([
                WINDOW,
                WINDOW + 1.0,
                WINDOW + 2.0,
                WINDOW + 3.0,
                WINDOW + 4.0,
            ]),
        )

        def crash_before_state_ack(*args, **kwargs):
            raise KeyboardInterrupt("simulated process death after collector commit")

        first.state.mark_registered = crash_before_state_ack
        try:
            with pytest.raises(KeyboardInterrupt):
                await first.run_cycle()
            row = _event_state(first)
            assert row["status"] != STATE_REGISTERED
            assert len(collector.registered) == 1
            assert collector.register_calls == 1
        finally:
            await first.close()

        second_discovery = _Discovery([_event()])
        second_station = _StationClient(_station_metadata(WINDOW + 60.0))
        second_forecast = _ForecastClient(_distribution(WINDOW + 60.0))
        second = WeatherCalibrationResearchWorker(
            db_path=db_path,
            discovery=second_discovery,
            station_client=second_station,
            forecast_client=second_forecast,
            collector=collector,
            clock=_Clock([WINDOW + 60.0, WINDOW + 61.0]),
        )
        try:
            report = await second.run_cycle()
            assert report["reconciled_reservations"] == 1
            assert report["capture"]["already_reserved_events"] == 1
            assert report["capture"]["registered_events"] == 0
            row = _event_state(second)
            assert row["status"] == STATE_REGISTERED
            assert row["registration_status"] == CAPTURE_PENDING
            assert collector.register_calls == 1
            assert first_station.calls == 1
            assert first_forecast.calls == 1
            assert second_station.calls == 0
            assert second_forecast.calls == 0
        finally:
            await second.close()

    asyncio.run(scenario())