from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace as NS

import httpx
import pytest

import polymarket_scanner.weather_only_live_paper_three_layer_validation as runtime_module
from polymarket_scanner.weather_only_nws_near_term import NWSNearTermError
from polymarket_scanner.weather_only_three_layer_guarded import GuardedNWSNearTermGridClient


class _Positions:
    def __init__(self):
        self.state = {}
    def get_state(self, key, default=""):
        return self.state.get(key, default)
    def set_state(self, key, value):
        self.state[key] = value


def _install_semantics(monkeypatch, target_date):
    def compile_event(event):
        return NS(
            event_id=str(event["id"]),
            target_date=event.get("target_date", target_date),
            station_hint=event.get("station", "KLGA"),
            family="DAILY_HIGH",
            unit="F",
        )
    monkeypatch.setattr(runtime_module, "compile_strict_temperature_event", compile_event)
    monkeypatch.setattr(runtime_module, "compile_temperature_rule_authority", lambda *_: object())
    monkeypatch.setattr(
        runtime_module,
        "build_same_day_contract_semantics",
        lambda *_: NS(layer1_adapter_capable=True),
    )


def _service(metadata_func, point_func):
    service = object.__new__(runtime_module.ThreeLayerValidationWeatherLivePaperService)
    service.positions = _Positions()
    service._three_layer_nws_support_cache = {}
    service._station_cache_now = lambda: 1000.0
    service._three_layer_last_eligible_total = 0
    service._three_layer_last_candidate_today_total = 0
    service._three_layer_last_layer2_unsupported_total = 0
    service._three_layer_last_selected_ids = ()
    service._three_layer_last_universe_truncated = False
    service._three_layer_last_coverage_complete = True
    service._three_layer_last_eligibility_failure_code = None
    service._station_metadata_for_compiled = metadata_func
    service._same_day_nws = NS(point_supported=point_func)
    return service


def _meta(station="KLGA"):
    offset = sum(ord(ch) for ch in station) % 100
    return NS(timezone="UTC", latitude=40.0 + offset / 1000.0, longitude=-73.0)


def test_nws_capability_404_is_normal_unsupported_and_error_body_is_not_consumed():
    class ExplodingStream(httpx.AsyncByteStream):
        iterated = False
        async def __aiter__(self):
            self.iterated = True
            raise AssertionError("404 error body must never be consumed")
            yield b"x"
        async def aclose(self):
            return None

    stream = ExplodingStream()
    async def handler(request):
        return httpx.Response(404, request=request, stream=stream)

    async def scenario():
        client = GuardedNWSNearTermGridClient()
        await client.http.aclose()
        client.http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        try:
            assert await client.point_supported(latitude=51.47, longitude=-0.46) is False
        finally:
            await client.close()
    asyncio.run(scenario())
    assert stream.iterated is False


@pytest.mark.parametrize("status", [429, 500, 503])
def test_nws_capability_retryable_provider_status_never_becomes_unsupported(status):
    async def handler(request):
        return httpx.Response(status, request=request, content=b"hostile body")
    async def scenario():
        client = GuardedNWSNearTermGridClient()
        await client.http.aclose()
        client.http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        try:
            with pytest.raises(NWSNearTermError, match="NWS_NEAR_TERM_PROVIDER_RETRYABLE_HTTP_STATUS"):
                await client.point_supported(latitude=40.77, longitude=-73.87)
        finally:
            await client.close()
    asyncio.run(scenario())


def test_nws_capability_success_requires_strict_feature_and_grid_url():
    payload = {"type":"Feature","properties":{"forecastGridData":"https://api.weather.gov/gridpoints/OKX/33,37"}}
    async def handler(request):
        return httpx.Response(200, request=request, content=json.dumps(payload).encode())
    async def scenario():
        client = GuardedNWSNearTermGridClient()
        await client.http.aclose()
        client.http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        try:
            assert await client.point_supported(latitude=40.77, longitude=-73.87) is True
        finally:
            await client.close()
    asyncio.run(scenario())


def test_nws_capability_malformed_success_fails_closed():
    async def handler(request):
        return httpx.Response(200, request=request, json={"type":"Feature","properties":{"forecastGridData":"https://evil.example/gridpoints/OKX/1,2"}})
    async def scenario():
        client = GuardedNWSNearTermGridClient()
        await client.http.aclose()
        client.http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        try:
            with pytest.raises(NWSNearTermError, match="NWS_NEAR_TERM_GRID_URL_INVALID"):
                await client.point_supported(latitude=40.77, longitude=-73.87)
        finally:
            await client.close()
    asyncio.run(scenario())


def test_explicit_layer2_unsupported_station_is_excluded_without_poisoning_coverage(monkeypatch):
    today = datetime.now(timezone.utc).date()
    _install_semantics(monkeypatch, today)
    async def metadata(compiled):
        if compiled.station_hint == "KAAA":
            return NS(timezone="UTC", latitude=40.0, longitude=-73.0)
        return NS(timezone="UTC", latitude=51.47, longitude=-0.46)
    async def supported(*, latitude, longitude): return latitude < 50.0
    service = _service(metadata, supported)
    rows = ({"id":"a","station":"KAAA"},{"id":"b","station":"KZZZ"})
    selected, errors = asyncio.run(service._same_day_eligible(rows))
    assert errors == []
    assert [x[0] for x in selected] == ["a"]
    assert service._three_layer_last_candidate_today_total == 2
    assert service._three_layer_last_layer2_unsupported_total == 1
    assert service._three_layer_last_coverage_complete is True


def test_station_provider_failure_suppresses_entire_selection(monkeypatch):
    today = datetime.now(timezone.utc).date()
    _install_semantics(monkeypatch, today)
    async def metadata(compiled):
        if compiled.station_hint == "KBBB":
            raise RuntimeError("station-down")
        return _meta(compiled.station_hint)
    async def supported(**_kwargs): return True
    service = _service(metadata, supported)
    selected, errors = asyncio.run(service._same_day_eligible(({"id":"a","station":"KAAA"},{"id":"b","station":"KBBB"})))
    assert selected == []
    assert service._three_layer_last_coverage_complete is False
    assert any("SAME_DAY_STATION_PROVIDER:KBBB" in value for value in errors)


def test_nws_provider_failure_suppresses_entire_selection(monkeypatch):
    today = datetime.now(timezone.utc).date()
    _install_semantics(monkeypatch, today)
    async def metadata(compiled): return _meta(compiled.station_hint)
    async def supported(*, latitude, longitude):
        if latitude > 40.05:
            raise NWSNearTermError("NWS_NEAR_TERM_PROVIDER_RETRYABLE_HTTP_STATUS")
        return True
    service = _service(metadata, supported)
    selected, errors = asyncio.run(service._same_day_eligible(({"id":"a","station":"KAAA"},{"id":"b","station":"KZZZ"})))
    assert selected == []
    assert service._three_layer_last_coverage_complete is False
    assert any("SAME_DAY_LAYER2_PROVIDER" in value for value in errors)


def test_far_future_contracts_do_not_consume_station_network_calls(monkeypatch):
    today = datetime.now(timezone.utc).date()
    _install_semantics(monkeypatch, today)
    calls = []
    async def metadata(compiled):
        calls.append(compiled.event_id)
        return _meta(compiled.station_hint)
    async def supported(**_kwargs): return True
    service = _service(metadata, supported)
    selected, errors = asyncio.run(service._same_day_eligible(({"id":"far","target_date":today + timedelta(days=10)},)))
    assert selected == []
    assert errors == []
    assert calls == []
    assert service._three_layer_last_coverage_complete is True


def test_unique_station_metadata_is_resolved_once_for_multiple_events(monkeypatch):
    today = datetime.now(timezone.utc).date()
    _install_semantics(monkeypatch, today)
    calls = 0
    async def metadata(compiled):
        nonlocal calls
        calls += 1
        return _meta(compiled.station_hint)
    async def supported(**_kwargs): return True
    service = _service(metadata, supported)
    selected, errors = asyncio.run(service._same_day_eligible(({"id":"a","station":"KLGA"},{"id":"b","station":"KLGA"})))
    assert errors == []
    assert len(selected) == 2
    assert calls == 1


def test_station_resolution_is_bounded_concurrent_not_serial(monkeypatch):
    today = datetime.now(timezone.utc).date()
    _install_semantics(monkeypatch, today)
    async def metadata(compiled):
        await asyncio.sleep(0.04)
        return _meta(compiled.station_hint)
    async def supported(**_kwargs): return True
    service = _service(metadata, supported)
    monkeypatch.setattr(runtime_module, "THREE_LAYER_ELIGIBILITY_SCAN_DEADLINE_SECONDS", 0.14)
    rows = tuple({"id":f"e{i}","station":f"K{i:03d}"} for i in range(8))
    selected, errors = asyncio.run(service._same_day_eligible(rows))
    assert not any("SAME_DAY_ELIGIBILITY_SCAN_TIMEOUT" in value for value in errors)
    assert service._three_layer_last_coverage_complete is True
    assert len(selected) == runtime_module.THREE_LAYER_MAX_EVENTS_PER_CYCLE


def test_support_probe_is_deduplicated_by_exact_nws_coordinate(monkeypatch):
    today = datetime.now(timezone.utc).date()
    _install_semantics(monkeypatch, today)
    async def metadata(_compiled): return NS(timezone="UTC", latitude=40.12344, longitude=-73.98764)
    calls = 0
    async def supported(**_kwargs):
        nonlocal calls
        calls += 1
        return True
    service = _service(metadata, supported)
    selected, errors = asyncio.run(service._same_day_eligible(({"id":"a","station":"KAAA"},{"id":"b","station":"KBBB"})))
    assert errors == []
    assert len(selected) == 2
    assert calls == 1
