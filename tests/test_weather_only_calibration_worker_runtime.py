from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from polymarket_scanner.weather_only_calibration_worker_runtime import (
    OperationalWeatherCalibrationResearchWorker,
    assess_worker_cycle_health,
)

from test_weather_only_calibration_worker import (
    WINDOW,
    _Clock,
    _Collector,
    _Discovery,
    _ForecastClient,
    _RegistrationFailCollector,
    _StationClient,
    _distribution,
    _event,
    _station_metadata,
)


def _healthy_report() -> dict:
    return {
        "cycle_ok": True,
        "discovery": {"attempted": True, "success": True, "cached_event_count": 1},
        "capture": {
            "errors": {},
            "missed_window_events": 0,
            "before_window_events": 1,
            "active_window_events": 0,
        },
        "collector": {
            "failed_captures": 0,
            "fetch_errors": [],
            "financial_authority": False,
            "financial_delivery": False,
            "automatic_order_placement": False,
        },
        "state": {"status_counts": {}},
    }


def test_normal_before_window_cycle_is_operationally_healthy():
    health = assess_worker_cycle_health(_healthy_report())
    assert health.process_healthy is True
    assert health.research_collection_healthy is True
    assert health.prospective_gap_detected is False
    assert health.health_reasons == ()
    assert health.gap_reasons == ()
    assert health.financial_authority is False


def test_missed_window_is_gap_signal_not_false_process_outage():
    report = _healthy_report()
    report["capture"]["missed_window_events"] = 2
    report["capture"]["before_window_events"] = 0
    health = assess_worker_cycle_health(report)
    assert health.process_healthy is True
    assert health.research_collection_healthy is True
    assert health.prospective_gap_detected is True
    assert "MISSED_CAPTURE_WINDOW_EVENTS:2" in health.gap_reasons


def test_capture_error_marks_collection_unhealthy_and_gap_detected():
    report = _healthy_report()
    report["capture"]["errors"] = {"COLLECTOR_REGISTER:RuntimeError": 1}
    report["state"]["status_counts"] = {"FAILED": 1}
    health = assess_worker_cycle_health(report)
    assert health.process_healthy is True
    assert health.research_collection_healthy is False
    assert health.prospective_gap_detected is True
    assert "CAPTURE:COLLECTOR_REGISTER:RuntimeError:1" in health.health_reasons
    assert "CAPTURE_ERROR_DURING_PROSPECTIVE_PIPELINE" in health.gap_reasons
    assert "FAILED_RESERVED_EVENTS:1" in health.gap_reasons


def test_temporary_collector_fetch_error_degrades_health_without_claiming_permanent_gap():
    report = _healthy_report()
    report["collector"]["fetch_errors"] = ["KLGA:SOURCE_TIMEOUT"]
    health = assess_worker_cycle_health(report)
    assert health.process_healthy is True
    assert health.research_collection_healthy is False
    assert health.prospective_gap_detected is False
    assert "COLLECTOR_FETCH_ERRORS:1" in health.health_reasons


def test_state_reconciliation_failure_marks_process_and_collection_unhealthy():
    report = _healthy_report()
    report["cycle_ok"] = False
    report["state_reconciliation_error"] = "SQLITE_BUSY"
    health = assess_worker_cycle_health(report)
    assert health.process_healthy is False
    assert health.research_collection_healthy is False
    assert "CORE_CYCLE_INTEGRITY_FAILED" in health.health_reasons


def test_operational_wrapper_before_window_is_healthy_and_never_fetches_forecast(tmp_path):
    async def scenario():
        outside = datetime(2026, 9, 10, 16, 0, tzinfo=timezone.utc).timestamp()
        discovery = _Discovery([_event()])
        station = _StationClient(_station_metadata(outside))
        forecast = _ForecastClient(_distribution(outside))
        collector = _Collector()
        worker = OperationalWeatherCalibrationResearchWorker(
            db_path=tmp_path / "worker.sqlite",
            discovery=discovery,
            station_client=station,
            forecast_client=forecast,
            collector=collector,
            clock=_Clock([outside, outside + 1.0]),
        )
        try:
            report = await worker.run_cycle()
            assert report["cycle_ok"] is True
            assert report["process_healthy"] is True
            assert report["research_collection_healthy"] is True
            assert report["prospective_gap_detected"] is False
            assert report["capture"]["before_window_events"] == 1
            assert forecast.calls == 0
            assert report["financial_authority"] is False
            assert report["financial_delivery"] is False
            assert report["automatic_order_placement"] is False
        finally:
            await worker.close()

    asyncio.run(scenario())


def test_operational_wrapper_registration_failure_is_visible_as_unhealthy_gap(tmp_path):
    async def scenario():
        discovery = _Discovery([_event()])
        station = _StationClient(_station_metadata(WINDOW + 0.25))
        forecast = _ForecastClient(_distribution(WINDOW + 0.5))
        collector = _RegistrationFailCollector()
        worker = OperationalWeatherCalibrationResearchWorker(
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
            ]),
        )
        try:
            report = await worker.run_cycle()
            assert report["cycle_ok"] is True  # core integrity still completed safely
            assert report["process_healthy"] is True
            assert report["research_collection_healthy"] is False
            assert report["prospective_gap_detected"] is True
            assert report["capture"]["errors"]["COLLECTOR_REGISTER:RuntimeError"] == 1
            assert report["state"]["status_counts"]["FAILED"] == 1
        finally:
            await worker.close()

    asyncio.run(scenario())
