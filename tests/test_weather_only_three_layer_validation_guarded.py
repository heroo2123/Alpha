from __future__ import annotations

import asyncio
import json
import time
from datetime import date, datetime, timezone
from unittest.mock import patch

import httpx
import pytest

from polymarket_scanner.weather_only_gefs_hourly import GEFSHourlyError
from polymarket_scanner.weather_only_live_paper_corrective import (
    WeatherLivePaperCorrectiveService,
)
from polymarket_scanner.weather_only_live_paper_three_layer_validation import (
    THREE_LAYER_31D_CAPTURE_ROW_BOUND,
    THREE_LAYER_MAX_EVENTS_PER_CYCLE,
    THREE_LAYER_SELECTION_UNIVERSE_CAP,
    ThreeLayerValidationWeatherLivePaperService,
    _rotate_after_cursor,
)
from polymarket_scanner.weather_only_three_layer_guarded import (
    GuardedOpenMeteoGEFSHourlyClient,
    _BoundedIdentityHTTPClient,
    _bounded_async_json,
)
from polymarket_scanner.weather_only_unresolved_coverage import (
    build_unresolved_coverage_plan,
)


class TrackingAsyncStream(httpx.AsyncByteStream):
    def __init__(self, chunks: list[bytes], *, delay: float = 0.0) -> None:
        self.chunks = list(chunks)
        self.delay = float(delay)
        self.iterated = False
        self.yielded_bytes = 0

    async def __aiter__(self):
        self.iterated = True
        for chunk in self.chunks:
            if self.delay:
                await asyncio.sleep(self.delay)
            self.yielded_bytes += len(chunk)
            yield chunk

    async def aclose(self) -> None:
        return None


class TrackingSyncStream(httpx.SyncByteStream):
    def __init__(self, chunks: list[bytes]) -> None:
        self.chunks = list(chunks)
        self.iterated = False
        self.yielded_bytes = 0

    def __iter__(self):
        self.iterated = True
        for chunk in self.chunks:
            self.yielded_bytes += len(chunk)
            yield chunk

    def close(self) -> None:
        return None


def _bounded_args() -> dict:
    return {
        "params": None,
        "max_bytes": 1024,
        "total_deadline_seconds": 0.5,
        "redirect_code": "REDIRECT",
        "status_code": "STATUS",
        "encoding_code": "ENCODING",
        "size_code": "SIZE",
        "json_code": "JSON",
        "timeout_code": "TIMEOUT",
        "transport_code": "TRANSPORT",
    }


def test_async_compressed_response_is_rejected_before_decoder_or_stream_iteration():
    stream = TrackingAsyncStream([b"tiny-compressed-bomb"])

    async def handler(request: httpx.Request):
        return httpx.Response(
            200,
            headers={"Content-Encoding": "gzip"},
            stream=stream,
            request=request,
        )

    async def scenario():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            with pytest.raises(RuntimeError, match="ENCODING"):
                await _bounded_async_json(http, "https://example.test/x", **_bounded_args())

    asyncio.run(scenario())
    assert stream.iterated is False
    assert stream.yielded_bytes == 0


def test_async_oversized_content_length_rejects_before_body_iteration():
    stream = TrackingAsyncStream([b"{}"])

    async def handler(request: httpx.Request):
        return httpx.Response(
            200,
            headers={"Content-Length": "999999"},
            stream=stream,
            request=request,
        )

    async def scenario():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            with pytest.raises(RuntimeError, match="SIZE"):
                await _bounded_async_json(http, "https://example.test/x", **_bounded_args())

    asyncio.run(scenario())
    assert stream.iterated is False


def test_async_total_deadline_stops_a_trickling_body():
    stream = TrackingAsyncStream([b"{", b'"ok":', b"true}"], delay=0.04)

    async def handler(request: httpx.Request):
        return httpx.Response(200, stream=stream, request=request)

    async def scenario():
        args = _bounded_args()
        args["total_deadline_seconds"] = 0.05
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            started = time.monotonic()
            with pytest.raises(RuntimeError, match="TIMEOUT"):
                await _bounded_async_json(http, "https://example.test/x", **args)
            assert time.monotonic() - started < 0.2

    asyncio.run(scenario())


def _gefs_payload(*, resolved_latitude: float, resolved_longitude: float) -> dict:
    hourly = {"time": [f"2026-09-14T{hour:02d}:00" for hour in range(24)]}
    units = {"time": "iso8601"}
    keys = ("temperature_2m",) + tuple(
        f"temperature_2m_member{index:02d}" for index in range(1, 31)
    )
    for member, key in enumerate(keys):
        hourly[key] = [70.0 + 0.1 * member + 0.05 * hour for hour in range(24)]
        units[key] = "°F"
    return {
        "latitude": resolved_latitude,
        "longitude": resolved_longitude,
        "timezone": "UTC",
        "hourly": hourly,
        "hourly_units": units,
    }


def _run_guarded_gefs(payload: dict):
    async def handler(request: httpx.Request):
        return httpx.Response(200, request=request, content=json.dumps(payload).encode())

    async def scenario():
        client = GuardedOpenMeteoGEFSHourlyClient()
        await client.http.aclose()
        client.http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        try:
            return await client.target_day(
                station="KAAA",
                latitude=40.0,
                longitude=-73.0,
                target_date=date(2026, 9, 14),
                unit="F",
                timezone="UTC",
            )
        finally:
            await client.close()

    return asyncio.run(scenario())


def test_gefs_resolved_grid_point_must_remain_near_requested_station():
    with pytest.raises(GEFSHourlyError, match="GEFS_HOURLY_RESOLVED_LOCATION_TOO_FAR"):
        _run_guarded_gefs(_gefs_payload(resolved_latitude=0.0, resolved_longitude=0.0))


def test_gefs_nearby_resolved_grid_preserves_all_31_members():
    result = _run_guarded_gefs(
        _gefs_payload(resolved_latitude=40.05, resolved_longitude=-73.04)
    )
    assert len(result.member_labels) == 31
    assert len(result.member_series) == 31
    assert len(result.valid_times) == 24
    assert result.calibrated_probability is False
    assert result.financial_authority is False


def _sync_client_with_transport(transport: httpx.BaseTransport) -> _BoundedIdentityHTTPClient:
    client = _BoundedIdentityHTTPClient()
    client._transport = transport
    return client


def test_wrh_compressed_response_is_rejected_before_stream_iteration():
    stream = TrackingSyncStream([b"compressed"])

    def handler(request: httpx.Request):
        return httpx.Response(
            200,
            headers={"Content-Encoding": "gzip"},
            stream=stream,
            request=request,
        )

    client = _sync_client_with_transport(httpx.MockTransport(handler))
    try:
        with pytest.raises(httpx.RequestError):
            client.get("https://www.weather.gov/source/wrh/apiKey.js")
    finally:
        client.close()
    assert stream.iterated is False
    assert stream.yielded_bytes == 0


def test_wrh_redirect_can_never_carry_browser_credential_to_another_host():
    seen: list[str] = []

    def handler(request: httpx.Request):
        seen.append(str(request.url))
        return httpx.Response(
            302,
            headers={"Location": "https://evil.example/steal"},
            request=request,
        )

    client = _sync_client_with_transport(httpx.MockTransport(handler))
    try:
        response = client.get(
            "https://api.synopticdata.com/v2/stations/timeseries",
            params={"token": "secret-sentinel"},
        )
    finally:
        client.close()
    assert response.status_code == 302
    assert len(seen) == 1
    assert seen[0].startswith("https://api.synopticdata.com/")
    assert all("evil.example" not in value for value in seen)


def test_rotation_does_not_permanently_starve_events_after_first_four():
    rows = [(f"event-{index:02d}",) for index in range(10)]
    cursor = ""
    visited: list[str] = []
    for _ in range(3):
        selected = _rotate_after_cursor(rows, cursor, 4)
        visited.extend(str(item[0]) for item in selected)
        cursor = str(selected[-1][0])
    assert visited[:10] == [f"event-{index:02d}" for index in range(10)]
    assert set(visited) == {f"event-{index:02d}" for index in range(10)}


def test_rotation_recovers_deterministically_when_persisted_cursor_disappears():
    rows = [("b",), ("a",), ("c",)]
    selected = _rotate_after_cursor(rows, "deleted-event", 2)
    assert [item[0] for item in selected] == ["a", "b"]


def test_rotation_storage_bound_matches_admitted_hourly_universe():
    assert THREE_LAYER_MAX_EVENTS_PER_CYCLE == 4
    assert THREE_LAYER_SELECTION_UNIVERSE_CAP == 12
    assert THREE_LAYER_31D_CAPTURE_ROW_BOUND == 12 * 24 * 31


def test_complete_source_bundle_has_a_hard_total_deadline(monkeypatch):
    import polymarket_scanner.weather_only_live_paper_three_layer_validation as module

    service = object.__new__(ThreeLayerValidationWeatherLivePaperService)

    async def slow_parent(_self, _compiled, _metadata):
        await asyncio.sleep(0.2)
        return None

    monkeypatch.setattr(module, "THREE_LAYER_SOURCE_BUNDLE_DEADLINE_SECONDS", 0.02)
    with patch.object(
        WeatherLivePaperCorrectiveService,
        "_fetch_same_day_source_bundle",
        slow_parent,
    ):
        with pytest.raises(TimeoutError):
            asyncio.run(service._fetch_same_day_source_bundle(object(), object()))


def test_final_local_partial_hour_has_no_full_gefs_cell_and_stays_scientifically_blocked():
    target = date(2026, 9, 14)
    cutoff = datetime(2026, 9, 14, 23, 30, tzinfo=timezone.utc).timestamp()
    observations = [
        datetime(2026, 9, 14, hour, 30, tzinfo=timezone.utc).timestamp()
        for hour in range(24)
    ]
    plan = build_unresolved_coverage_plan(
        station="KAAA",
        population_id="WRH_TEST",
        timezone="UTC",
        target_date=target,
        as_of=cutoff,
        accepted_observation_times=observations,
        population_alignment_certified=False,
    )
    assert plan.near_term_segment is not None
    assert plan.near_term_segment.end == datetime(2026, 9, 15, 0, 0, tzinfo=timezone.utc).timestamp()
    assert plan.ensemble_segments == ()
    assert plan.population_alignment_certified is False
    assert plan.same_day_delivery_authority is False
    assert plan.financial_authority is False
