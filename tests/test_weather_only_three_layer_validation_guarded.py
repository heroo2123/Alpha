from __future__ import annotations

import asyncio
import json
import time
from datetime import date, datetime, timezone
from types import SimpleNamespace as NS
from urllib.parse import parse_qs

import httpx
import pytest

from polymarket_scanner.weather_only_gefs_hourly import (
    GEFS_HOURLY_CELL_SELECTION,
    GEFS_HOURLY_PROVIDER_MODEL,
    GEFS_HOURLY_TEMPORAL_RESOLUTION,
    GEFS_HOURLY_TIMEFORMAT,
    GEFSHourlyError,
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
    start = datetime(2026, 9, 14, 0, 0, tzinfo=timezone.utc).timestamp()
    hourly = {"time": [int(start + hour * 3600) for hour in range(24)]}
    units = {"time": GEFS_HOURLY_TIMEFORMAT}
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


def _run_guarded_gefs(payload: dict, seen_urls: list[str] | None = None):
    async def handler(request: httpx.Request):
        if seen_urls is not None:
            seen_urls.append(str(request.url))
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
    assert result.provider_model == GEFS_HOURLY_PROVIDER_MODEL
    assert result.query_cell_selection == GEFS_HOURLY_CELL_SELECTION
    assert result.query_temporal_resolution == GEFS_HOURLY_TEMPORAL_RESOLUTION
    assert result.query_timeformat == GEFS_HOURLY_TIMEFORMAT
    assert result.calibrated_probability is False
    assert result.financial_authority is False


def test_guarded_gefs_request_exactly_pins_model_temporal_resolution_and_cell_policy():
    seen: list[str] = []
    _run_guarded_gefs(
        _gefs_payload(resolved_latitude=40.05, resolved_longitude=-73.04), seen
    )
    assert len(seen) == 1
    query = parse_qs(httpx.URL(seen[0]).query.decode())
    assert query["models"] == [GEFS_HOURLY_PROVIDER_MODEL]
    assert query["temporal_resolution"] == [GEFS_HOURLY_TEMPORAL_RESOLUTION]
    assert query["timeformat"] == [GEFS_HOURLY_TIMEFORMAT]
    assert query["cell_selection"] == [GEFS_HOURLY_CELL_SELECTION]
    assert query["hourly"] == ["temperature_2m"]


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
    assert THREE_LAYER_SELECTION_UNIVERSE_CAP == 32
    assert THREE_LAYER_31D_CAPTURE_ROW_BOUND == THREE_LAYER_SELECTION_UNIVERSE_CAP * 24 * 31


def _bundle_fixture(service):
    compiled = NS(station_hint="KAAA", target_date=date(2026, 9, 14), unit="F")
    metadata = NS(latitude=40.0, longitude=-73.0, timezone="UTC")
    return compiled, metadata


def test_complete_source_bundle_has_a_hard_total_deadline(monkeypatch):
    import polymarket_scanner.weather_only_live_paper_three_layer_validation as module

    service = object.__new__(ThreeLayerValidationWeatherLivePaperService)

    class WRH:
        def fetch_snapshot(self, **_kwargs):
            return "wrh"

    class SlowNWS:
        async def fetch_snapshot(self, **_kwargs):
            await asyncio.sleep(0.2)
            return "nws"

    class SlowGEFS:
        async def target_day(self, **_kwargs):
            await asyncio.sleep(0.2)
            return "gefs"

    service._same_day_wrh = WRH()
    service._same_day_nws = SlowNWS()
    service._same_day_gefs = SlowGEFS()
    compiled, metadata = _bundle_fixture(service)
    monkeypatch.setattr(module, "THREE_LAYER_SOURCE_BUNDLE_DEADLINE_SECONDS", 0.02)
    with pytest.raises(TimeoutError):
        asyncio.run(service._fetch_same_day_source_bundle(compiled, metadata))


def test_fast_source_failure_waits_for_bounded_siblings_before_propagating():
    service = object.__new__(ThreeLayerValidationWeatherLivePaperService)
    settled = {"nws": False, "gefs": False}

    class FailingWRH:
        def fetch_snapshot(self, **_kwargs):
            raise RuntimeError("WRH_FAIL")

    class NWS:
        async def fetch_snapshot(self, **_kwargs):
            await asyncio.sleep(0.02)
            settled["nws"] = True
            return "nws"

    class GEFS:
        async def target_day(self, **_kwargs):
            await asyncio.sleep(0.02)
            settled["gefs"] = True
            return "gefs"

    service._same_day_wrh = FailingWRH()
    service._same_day_nws = NWS()
    service._same_day_gefs = GEFS()
    compiled, metadata = _bundle_fixture(service)
    with pytest.raises(RuntimeError, match="WRH_FAIL"):
        asyncio.run(service._fetch_same_day_source_bundle(compiled, metadata))
    assert settled == {"nws": True, "gefs": True}


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



def test_selection_universe_over_cap_fails_closed_in_source_not_partial_first_twelve():
    from pathlib import Path
    import polymarket_scanner.weather_only_live_paper_three_layer_validation as runtime_module

    runtime = Path(runtime_module.__file__).read_text(encoding="utf-8")
    assert "SAME_DAY_SELECTION_UNIVERSE_CAP_EXCEEDED" in runtime
    assert "universe = eligible[:THREE_LAYER_SELECTION_UNIVERSE_CAP]" not in runtime



def test_more_than_selection_cap_fails_closed_without_partial_sampling(monkeypatch):
    import polymarket_scanner.weather_only_live_paper_three_layer_validation as module

    service = object.__new__(ThreeLayerValidationWeatherLivePaperService)

    class Positions:
        def __init__(self):
            self.set_calls = []
        def get_state(self, _key, default=""):
            return default
        def set_state(self, key, value):
            self.set_calls.append((key, value))

    service.positions = Positions()

    today = datetime.now(timezone.utc).date()

    def compile_event(event):
        return NS(
            event_id=event["id"],
            target_date=today,
            station_hint="KLGA",
            family="DAILY_HIGH",
            unit="F",
        )

    monkeypatch.setattr(module, "compile_strict_temperature_event", compile_event)
    monkeypatch.setattr(module, "compile_temperature_rule_authority", lambda *_: object())
    monkeypatch.setattr(
        module,
        "build_same_day_contract_semantics",
        lambda *_: NS(layer1_adapter_capable=True),
    )

    async def metadata(_compiled):
        return NS(station="KLGA", timezone="UTC", latitude=40.7769, longitude=-73.8740)

    service._station_metadata_for_compiled = metadata
    over_cap = THREE_LAYER_SELECTION_UNIVERSE_CAP + 1
    events = tuple({"id": f"event-{index:02d}"} for index in range(over_cap))
    selected, errors = asyncio.run(service._same_day_eligible(events))
    assert selected == []
    assert errors == [
        f"SAME_DAY_SELECTION_UNIVERSE_CAP_EXCEEDED:{over_cap}>{THREE_LAYER_SELECTION_UNIVERSE_CAP}"
    ]
    assert service._three_layer_last_universe_truncated is True
    assert service._three_layer_last_selected_ids == ()
    assert service.positions.set_calls == []


def test_eligibility_metadata_scan_has_one_total_deadline(monkeypatch):
    import polymarket_scanner.weather_only_live_paper_three_layer_validation as module

    service = object.__new__(ThreeLayerValidationWeatherLivePaperService)

    class Positions:
        def get_state(self, _key, default=""):
            return default
        def set_state(self, _key, _value):
            raise AssertionError("cursor must not advance on incomplete eligibility scan")

    service.positions = Positions()
    today = datetime.now(timezone.utc).date()

    monkeypatch.setattr(
        module,
        "compile_strict_temperature_event",
        lambda event: NS(
            event_id=event["id"], target_date=today, station_hint="KLGA",
            family="DAILY_HIGH", unit="F",
        ),
    )
    monkeypatch.setattr(module, "compile_temperature_rule_authority", lambda *_: object())
    monkeypatch.setattr(
        module,
        "build_same_day_contract_semantics",
        lambda *_: NS(layer1_adapter_capable=True),
    )
    monkeypatch.setattr(module, "THREE_LAYER_ELIGIBILITY_SCAN_DEADLINE_SECONDS", 0.02)

    async def slow_metadata(_compiled):
        await asyncio.sleep(1.0)
        return NS(timezone="UTC", latitude=40.0, longitude=-73.0)

    service._station_metadata_for_compiled = slow_metadata
    started = time.monotonic()
    selected, errors = asyncio.run(service._same_day_eligible(({"id": "event-1"},)))
    elapsed = time.monotonic() - started
    assert elapsed < 0.25
    assert selected == []
    assert errors == ["SAME_DAY_ELIGIBILITY_SCAN_TIMEOUT"]
    assert service._three_layer_last_universe_truncated is True
    assert service._three_layer_last_selected_ids == ()


def test_guarded_station_metadata_rejects_compressed_body_before_iteration():
    from polymarket_scanner.weather_only_three_layer_guarded import (
        GuardedSameDayStationMetadataClient,
    )
    from polymarket_scanner.weather_only_station_metadata import WeatherStationMetadataError

    stream = TrackingAsyncStream([b"compressed-station-bomb"])

    async def handler(request: httpx.Request):
        return httpx.Response(
            200,
            headers={"Content-Encoding": "gzip"},
            stream=stream,
            request=request,
        )

    async def scenario():
        client = GuardedSameDayStationMetadataClient()
        await client.http.aclose()
        client.http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        try:
            with pytest.raises(WeatherStationMetadataError, match="STATION_METADATA_UNSUPPORTED_ENCODING"):
                await client.station("KLGA")
        finally:
            await client.close()

    asyncio.run(scenario())
    assert stream.iterated is False
    assert stream.yielded_bytes == 0


def test_guarded_station_metadata_never_follows_nws_cross_host_redirect():
    from polymarket_scanner.weather_only_three_layer_guarded import (
        GuardedSameDayStationMetadataClient,
    )
    from polymarket_scanner.weather_only_station_metadata import WeatherStationMetadataError

    seen: list[str] = []

    async def handler(request: httpx.Request):
        seen.append(str(request.url))
        return httpx.Response(
            302,
            headers={"Location": "https://evil.example/station"},
            request=request,
        )

    async def scenario():
        client = GuardedSameDayStationMetadataClient()
        await client.http.aclose()
        client.http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        try:
            with pytest.raises(WeatherStationMetadataError, match="STATION_METADATA_REDIRECT"):
                await client.station("KLGA")
        finally:
            await client.close()

    asyncio.run(scenario())
    assert len(seen) == 1
    assert "evil.example" not in seen[0]


def test_token_bearing_metadata_backend_redirect_is_rejected_without_second_request(monkeypatch):
    import polymarket_scanner.weather_only_three_layer_guarded as module

    client = module.GuardedSameDayStationMetadataClient()
    seen: list[str] = []

    async def handler(request: httpx.Request):
        seen.append(str(request.url))
        return httpx.Response(
            302,
            headers={"Location": "https://evil.example/steal"},
            request=request,
        )

    async def token():
        return "secret-sentinel"

    async def scenario():
        from polymarket_scanner.weather_only_wrh_station_metadata import WRHStationMetadataError
        await client.http.aclose()
        client.http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        monkeypatch.setattr(client, "_wrh_material", token)
        try:
            with pytest.raises(WRHStationMetadataError, match="WRH_STATION_METADATA_BACKEND_REDIRECT"):
                await client._wrh_station("KLGA")
        finally:
            await client.close()

    asyncio.run(scenario())
    assert len(seen) == 1
    assert "evil.example" not in seen[0]


def test_same_day_wrapper_has_dedicated_guarded_station_metadata_client():
    from pathlib import Path
    import polymarket_scanner.weather_only_live_paper_three_layer_validation as module

    source = Path(module.__file__).read_text(encoding="utf-8")
    assert "GuardedSameDayStationMetadataClient" in source
    assert "self._three_layer_station_client.station(station_id)" in source
    assert "self.station_client = guarded_station" not in source


def test_guarded_station_metadata_retries_transient_http_status_and_preserves_final_code(monkeypatch):
    from polymarket_scanner.weather_only_three_layer_guarded import (
        GuardedSameDayStationMetadataClient,
    )
    from polymarket_scanner.weather_only_station_metadata import WeatherStationMetadataError

    async def scenario_success():
        client = GuardedSameDayStationMetadataClient()
        calls = 0
        sentinel = object()

        async def fake_nws(_station):
            nonlocal calls
            calls += 1
            if calls < 3:
                raise WeatherStationMetadataError("STATION_METADATA_RETRYABLE_HTTP_STATUS")
            return sentinel

        monkeypatch.setattr(client, "_nws_once", fake_nws)
        monkeypatch.setattr("asyncio.sleep", lambda *_args, **_kwargs: asyncio.sleep(0))
        try:
            # Avoid monkeypatching asyncio.sleep recursively: the retries use zero by
            # temporarily setting the module retry count path through real scheduling.
            pass
        finally:
            await client.close()
        return calls, sentinel

    # Test retry policy without replacing asyncio.sleep globally.
    async def success():
        client = GuardedSameDayStationMetadataClient()
        calls = 0
        sentinel = object()
        async def fake_nws(_station):
            nonlocal calls
            calls += 1
            if calls < 3:
                raise WeatherStationMetadataError("STATION_METADATA_RETRYABLE_HTTP_STATUS")
            return sentinel
        client._nws_once = fake_nws
        import polymarket_scanner.weather_only_three_layer_guarded as module
        old_sleep = module.asyncio.sleep
        async def no_sleep(_delay):
            return None
        module.asyncio.sleep = no_sleep
        try:
            result = await client.station("KLGA")
        finally:
            module.asyncio.sleep = old_sleep
            await client.close()
        assert result is sentinel
        assert calls == 3

    asyncio.run(success())

    async def exhausted():
        client = GuardedSameDayStationMetadataClient()
        async def fake_nws(_station):
            raise WeatherStationMetadataError("STATION_METADATA_RETRYABLE_HTTP_STATUS")
        client._nws_once = fake_nws
        import polymarket_scanner.weather_only_three_layer_guarded as module
        old_sleep = module.asyncio.sleep
        async def no_sleep(_delay):
            return None
        module.asyncio.sleep = no_sleep
        try:
            with pytest.raises(WeatherStationMetadataError) as raised:
                await client.station("KLGA")
            assert raised.value.code == "STATION_METADATA_HTTP_STATUS"
        finally:
            module.asyncio.sleep = old_sleep
            await client.close()

    asyncio.run(exhausted())