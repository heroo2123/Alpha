from __future__ import annotations

import asyncio
import sqlite3

import pytest

from polymarket_scanner.weather_only_calibration_horizon import CAPTURE_HORIZON_POLICY_ID
from polymarket_scanner.weather_only_calibration_worker import STATE_REGISTERED
from polymarket_scanner.weather_only_calibration_worker_runtime import (
    OperationalWeatherCalibrationResearchWorker,
)
from polymarket_scanner.weather_only_wrh_collector import CollectorTickReport
from polymarket_scanner.weather_only_wrh_collector_authority import (
    TrustedWeatherWRHProspectiveCollector,
)

from test_weather_only_calibration_worker import (
    WINDOW,
    _Clock,
    _Discovery,
    _ForecastClient,
    _StationClient,
    _distribution,
    _event,
    _station_metadata,
)


class _UnusedWRHClient:
    def fetch_snapshot(self, **kwargs):
        raise AssertionError("operational horizon integration must not fetch WRH")


class _PersistingTrustedCollector(TrustedWeatherWRHProspectiveCollector):
    """Use the real trusted registration/persistence path but keep settlement idle."""

    def __init__(self, db_path):
        super().__init__(
            db_path=db_path,
            client=_UnusedWRHClient(),
            clock=lambda: WINDOW + 2.5,
        )
        self.ticks = 0

    def tick(self):
        self.ticks += 1
        return CollectorTickReport(
            collector_version="integration-idle-collector",
            policy_id=self.authority_version,
            evaluated_station_dates=0,
            fetched_snapshots=0,
            authorized_captures=0,
            failed_captures=0,
            deferred_station_dates=1,
            fetch_errors=(),
            financial_authority=False,
            financial_delivery=False,
            automatic_order_placement=False,
        )


def _horizon_rows(db_path) -> list[sqlite3.Row]:
    with sqlite3.connect(db_path) as db:
        db.row_factory = sqlite3.Row
        exists = db.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='weather_calibration_capture_horizon'"
        ).fetchone()
        if exists is None:
            return []
        return list(db.execute(
            "SELECT * FROM weather_calibration_capture_horizon ORDER BY capture_evidence_sha256"
        ).fetchall())


def _worker_row(db_path):
    with sqlite3.connect(db_path) as db:
        db.row_factory = sqlite3.Row
        return db.execute(
            "SELECT * FROM weather_calibration_worker_events WHERE event_id = ?",
            (_event()["id"],),
        ).fetchone()


def test_operational_worker_persists_horizon_attestation_from_real_registered_rows(tmp_path):
    async def scenario():
        db_path = tmp_path / "operational-horizon.sqlite"
        collector = _PersistingTrustedCollector(db_path)
        discovery = _Discovery([_event()])
        station = _StationClient(_station_metadata(WINDOW + 0.25))
        forecast = _ForecastClient(_distribution(WINDOW + 0.5))
        worker = OperationalWeatherCalibrationResearchWorker(
            db_path=db_path,
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
            ]),
        )
        try:
            report = await worker.run_cycle()
            assert report["capture"]["registered_events"] == 1
            assert report["horizon_attestation"]["eligible_registered_rows"] == 1
            assert report["horizon_attestation"]["created_horizon_attestations"] == 1
            assert report["horizon_attestation"]["errors"] == {}
            assert report["research_collection_healthy"] is True
            assert report["financial_authority"] is False

            row = _worker_row(db_path)
            assert row is not None
            assert row["status"] == STATE_REGISTERED
            assert row["capture_policy_id"] == CAPTURE_HORIZON_POLICY_ID

            horizon_rows = _horizon_rows(db_path)
            assert len(horizon_rows) == 1
            horizon = horizon_rows[0]
            assert horizon["capture_evidence_sha256"] == row["capture_evidence_sha256"]
            assert horizon["capture_policy_id"] == CAPTURE_HORIZON_POLICY_ID
            assert horizon["station"] == "KLGA"
            assert horizon["station_timezone"] == "America/New_York"
        finally:
            await worker.close()
            collector.close()

    asyncio.run(scenario())


def test_restart_reconciles_post_registration_crash_then_attests_same_frozen_capture(tmp_path):
    async def scenario():
        db_path = tmp_path / "operational-horizon-restart.sqlite"
        collector = _PersistingTrustedCollector(db_path)
        first = OperationalWeatherCalibrationResearchWorker(
            db_path=db_path,
            discovery=_Discovery([_event()]),
            station_client=_StationClient(_station_metadata(WINDOW + 0.25)),
            forecast_client=_ForecastClient(_distribution(WINDOW + 0.5)),
            collector=collector,
            clock=_Clock([
                WINDOW,
                WINDOW + 1.0,
                WINDOW + 2.0,
                WINDOW + 3.0,
            ]),
        )

        def crash_before_state_ack(*args, **kwargs):
            raise KeyboardInterrupt("simulated death after durable collector registration")

        first.state.mark_registered = crash_before_state_ack
        try:
            with pytest.raises(KeyboardInterrupt):
                await first.run_cycle()
            # The runtime never reached horizon attestation. No eligible calibration
            # proof may appear merely because collector registration committed.
            assert _horizon_rows(db_path) == []
        finally:
            await first.close()

        second_forecast = _ForecastClient(_distribution(WINDOW + 60.0))
        second = OperationalWeatherCalibrationResearchWorker(
            db_path=db_path,
            discovery=_Discovery([_event()]),
            station_client=_StationClient(_station_metadata(WINDOW + 60.0)),
            forecast_client=second_forecast,
            collector=collector,
            clock=_Clock([WINDOW + 60.0, WINDOW + 61.0]),
        )
        try:
            report = await second.run_cycle()
            assert report["reconciled_reservations"] == 1
            assert report["capture"]["already_reserved_events"] == 1
            assert report["capture"]["registered_events"] == 0
            assert second_forecast.calls == 0
            assert report["horizon_attestation"]["eligible_registered_rows"] == 1
            assert report["horizon_attestation"]["created_horizon_attestations"] == 1
            assert report["horizon_attestation"]["errors"] == {}
            assert report["research_collection_healthy"] is True

            row = _worker_row(db_path)
            assert row is not None
            assert row["status"] == STATE_REGISTERED
            horizon_rows = _horizon_rows(db_path)
            assert len(horizon_rows) == 1
            assert horizon_rows[0]["capture_evidence_sha256"] == row["capture_evidence_sha256"]
        finally:
            await second.close()
            collector.close()

    asyncio.run(scenario())
