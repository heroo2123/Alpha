from __future__ import annotations

import asyncio
import json
import logging
import sqlite3
from types import SimpleNamespace

import httpx

import polymarket_scanner.weather_only_pws as pws_module
from polymarket_scanner.safe_logging import install_secret_safe_logging
from polymarket_scanner.weather_only_pws import (
    PWS_STATUS_NO_FRESH_QC,
    WeatherCompanyPWSClient,
)
from polymarket_scanner.weather_only_pws_store import SameDayPWSDiagnosticStore
from polymarket_scanner.weather_only_same_day_capture_store import SameDayCaptureStore
from test_weather_only_same_day_capture import _capture


LAT = 40.7769
LON = -73.8740


def _near():
    return {
        "location": {
            "stationId": ["PWS1"],
            "latitude": [LAT],
            "longitude": [LON],
            "qcStatus": [1],
            "updateTimeUtc": ["2026-09-14T15:00:00Z"],
        }
    }


def test_899_second_observation_ageing_to_901_is_dropped_not_raised(monkeypatch):
    base = 2_000_000_000.0
    values = iter((base, base, base + 2.0))
    monkeypatch.setattr(pws_module, "time", SimpleNamespace(time=lambda: next(values)))
    observed = int(base - 899.0)

    # Make the ISO representation exactly match the synthetic epoch.
    from datetime import datetime, timezone
    iso = datetime.fromtimestamp(observed, tz=timezone.utc).isoformat().replace("+00:00", "Z")

    async def handler(request):
        if request.url.path.endswith("/near"):
            return httpx.Response(200, request=request, json=_near())
        body = {
            "observations": [
                {
                    "stationID": "PWS1",
                    "epoch": observed,
                    "obsTimeUtc": iso,
                    "lat": LAT,
                    "lon": LON,
                    "qcStatus": 1,
                    "imperial": {"temp": 80.0},
                }
            ]
        }
        return httpx.Response(200, request=request, json=body)

    async def scenario():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            result = await WeatherCompanyPWSClient(api_key="secret", http=http).fetch_snapshot(
                latitude=LAT,
                longitude=LON,
                unit="F",
            )
            assert result.status == PWS_STATUS_NO_FRESH_QC
            assert result.observations == ()
            assert any(a.outcome == "REJECT_FINAL_TEMPORAL_WINDOW" for a in result.attempts)

    asyncio.run(scenario())


def test_rejection_audit_does_not_leak_previous_station_coordinates():
    now = int(pws_module.time.time())
    from datetime import datetime, timezone
    iso = datetime.fromtimestamp(now, tz=timezone.utc).isoformat().replace("+00:00", "Z")

    nearby = {
        "location": {
            "stationId": ["PWS1", "PWS2"],
            "latitude": [LAT, LAT + 0.001],
            "longitude": [LON, LON],
            "qcStatus": [1, 1],
            "updateTimeUtc": [iso, iso],
        }
    }

    async def handler(request):
        if request.url.path.endswith("/near"):
            return httpx.Response(200, request=request, json=nearby)
        station_id = request.url.params["stationId"]
        if station_id == "PWS1":
            return httpx.Response(
                200,
                request=request,
                json={
                    "observations": [{
                        "stationID": "PWS1",
                        "epoch": now,
                        "obsTimeUtc": iso,
                        "lat": LAT,
                        "lon": LON,
                        "qcStatus": 1,
                        "imperial": {"temp": 80.0},
                    }]
                },
            )
        return httpx.Response(
            200,
            request=request,
            json={
                "observations": [{
                    "stationID": "PWS2",
                    "epoch": now,
                    "obsTimeUtc": iso,
                    "lat": "not-a-latitude",
                    "lon": LON,
                    "qcStatus": 1,
                    "imperial": {"temp": 81.0},
                }]
            },
        )

    async def scenario():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            result = await WeatherCompanyPWSClient(api_key="secret", http=http).fetch_snapshot(
                latitude=LAT, longitude=LON, unit="F"
            )
        rejected = next(a for a in result.attempts if a.station_id == "PWS2")
        assert rejected.outcome == "PWS_LATITUDE_INVALID"
        assert rejected.observation_latitude is None
        assert rejected.observation_longitude is None
        assert rejected.identity_location_delta_km is None

    asyncio.run(scenario())


def test_real_httpx_info_request_log_redacts_explicit_api_key(caplog):
    key = "gpt6-sentinel-pws-key-not-in-env"
    install_secret_safe_logging()
    logger = logging.getLogger("httpx")
    prior = logger.level
    logger.setLevel(logging.INFO)

    async def handler(request):
        return httpx.Response(401, request=request, json={"error": "unauthorized"})

    async def scenario():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            with caplog.at_level(logging.INFO, logger="httpx"):
                await WeatherCompanyPWSClient(api_key=key, http=http).fetch_snapshot(
                    latitude=LAT,
                    longitude=LON,
                    unit="F",
                )

    try:
        asyncio.run(scenario())
        rendered = "\n".join(record.getMessage() for record in caplog.records)
        assert key not in rendered
        assert "<redacted-api-key>" in rendered
    finally:
        logger.setLevel(prior)


def test_capture_insert_rolls_back_if_atomic_pws_link_insert_conflicts(tmp_path):
    db_path = tmp_path / "weather-paper.sqlite"
    capture_store = SameDayCaptureStore(db_path)
    pws_store = SameDayPWSDiagnosticStore(db_path)
    capture = _capture()

    # Pre-seed only the link so the second statement in save() conflicts after the
    # capture INSERT has already run. The transaction must roll the capture back.
    with sqlite3.connect(db_path) as db:
        db.execute(
            """
            INSERT INTO weather_same_day_pws_capture_links(
                capture_sha256,event_id,target_date,capture_as_of,state,
                diagnostic_sha256,failure_code,started_at,completed_at,
                included_in_validated_pnl,same_day_delivery_enabled,financial_authority
            ) VALUES(?,?,?,?, 'INTERRUPTED',NULL,'synthetic-conflict',?, ?,0,0,0)
            """,
            (
                capture.capture_sha256,
                capture.event_id,
                capture.target_date,
                float(capture.as_of),
                float(capture.as_of),
                float(capture.as_of),
            ),
        )

    assert capture_store.save(capture) is None
    assert capture_store.capture_json(capture.capture_sha256) is None
    link = pws_store.link_for_capture(capture.capture_sha256)
    assert link is not None
    assert link["state"] == "INTERRUPTED"
