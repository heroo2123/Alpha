from __future__ import annotations

from pathlib import Path


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected exactly one match, found {count}")
    return text.replace(old, new, 1)


guarded_path = Path("polymarket_scanner/weather_only_three_layer_guarded.py")
guarded = guarded_path.read_text(encoding="utf-8")
guarded = replace_once(
    guarded,
    "from .weather_only_nws_near_term import (\n"
    "    MAX_RESPONSE_BYTES as NWS_BASE_MAX_RESPONSE_BYTES,\n"
    "    NWSNearTermError,\n"
    "    NWSNearTermGridClient,\n"
    ")\n",
    "from .weather_only_nws_near_term import (\n"
    "    MAX_RESPONSE_BYTES as NWS_BASE_MAX_RESPONSE_BYTES,\n"
    "    NWSNearTermError,\n"
    "    NWSNearTermGridClient,\n"
    "    _grid_url,\n"
    "    _points_url,\n"
    ")\n",
    "guarded NWS imports",
)

needle = '''    async def fetch_snapshot(self, *, station: str, latitude: float, longitude: float):\n        try:\n            async with asyncio.timeout(NWS_SNAPSHOT_DEADLINE_SECONDS):\n                return await super().fetch_snapshot(\n                    station=station,\n                    latitude=latitude,\n                    longitude=longitude,\n                )\n        except TimeoutError:\n            raise NWSNearTermError("NWS_NEAR_TERM_PROVIDER_TIMEOUT") from None\n'''
replacement = '''    async def point_supported(self, *, latitude: float, longitude: float) -> bool:\n        """Return False only for an explicit NWS /points 404.\n\n        A 404 from the documented NWS points endpoint means the coordinate is outside\n        the NWS forecast-grid population. Every provider/transport/schema failure is\n        different: it must fail closed instead of masquerading as unsupported geography\n        and silently shrinking the research population. Error bodies are never consumed.\n        """\n        try:\n            status, raw, _received = await _bounded_async_bytes(\n                self.http,\n                _points_url(latitude, longitude),\n                params=None,\n                headers={"Accept": "application/geo+json"},\n                max_bytes=NWS_BASE_MAX_RESPONSE_BYTES,\n                total_deadline_seconds=NWS_REQUEST_DEADLINE_SECONDS,\n                allow_same_host_redirects=False,\n                redirect_code="NWS_NEAR_TERM_PROVIDER_REDIRECT",\n                encoding_code="NWS_NEAR_TERM_PROVIDER_UNSUPPORTED_ENCODING",\n                size_code="NWS_NEAR_TERM_PROVIDER_RESPONSE_CAP",\n                timeout_code="NWS_NEAR_TERM_PROVIDER_TIMEOUT",\n                transport_code="NWS_NEAR_TERM_PROVIDER_TRANSPORT",\n            )\n        except RuntimeError as exc:\n            raise NWSNearTermError(str(exc)) from None\n\n        if status == 404:\n            return False\n        if status == 429 or status >= 500:\n            raise NWSNearTermError("NWS_NEAR_TERM_PROVIDER_RETRYABLE_HTTP_STATUS")\n        if status < 200 or status >= 300:\n            raise NWSNearTermError("NWS_NEAR_TERM_PROVIDER_HTTP_STATUS")\n\n        try:\n            payload = _strict_json_dict(raw, "NWS_NEAR_TERM_PROVIDER_JSON_INVALID")\n        except RuntimeError as exc:\n            raise NWSNearTermError(str(exc)) from None\n        if payload.get("type") != "Feature":\n            raise NWSNearTermError("NWS_NEAR_TERM_POINTS_ENVELOPE_INVALID")\n        properties = payload.get("properties")\n        if not isinstance(properties, dict):\n            raise NWSNearTermError("NWS_NEAR_TERM_POINTS_PROPERTIES_INVALID")\n        # Validate that a successful points response actually binds to the exact NWS\n        # grid-data host/path grammar before declaring Layer 2 geographically capable.\n        _grid_url(properties.get("forecastGridData"))\n        return True\n\n    async def fetch_snapshot(self, *, station: str, latitude: float, longitude: float):\n        try:\n            async with asyncio.timeout(NWS_SNAPSHOT_DEADLINE_SECONDS):\n                return await super().fetch_snapshot(\n                    station=station,\n                    latitude=latitude,\n                    longitude=longitude,\n                )\n        except TimeoutError:\n            raise NWSNearTermError("NWS_NEAR_TERM_PROVIDER_TIMEOUT") from None\n'''
guarded = replace_once(guarded, needle, replacement, "guarded point capability method")
guarded_path.write_text(guarded, encoding="utf-8")


runtime_path = Path("polymarket_scanner/weather_only_live_paper_three_layer_validation.py")
runtime = runtime_path.read_text(encoding="utf-8")
runtime = replace_once(
    runtime,
    "from datetime import datetime, timezone\n",
    "from datetime import datetime, timedelta, timezone\n",
    "runtime datetime import",
)
runtime = replace_once(
    runtime,
    'THREE_LAYER_ELIGIBILITY_SCAN_DEADLINE_SECONDS = 30.0\nTHREE_LAYER_CURSOR_KEY = "same_day_three_layer_rotation_cursor_v1"\n',
    'THREE_LAYER_ELIGIBILITY_SCAN_DEADLINE_SECONDS = 30.0\n'
    'THREE_LAYER_ELIGIBILITY_CONCURRENCY = 4\n'
    'THREE_LAYER_NWS_SUPPORT_CACHE_TTL_SECONDS = 21_600.0\n'
    'THREE_LAYER_NWS_SUPPORT_CACHE_MAX_ENTRIES = 128\n'
    'THREE_LAYER_CURSOR_KEY = "same_day_three_layer_rotation_cursor_v1"\n',
    "runtime eligibility constants",
)
runtime = replace_once(
    runtime,
    "        self._three_layer_last_eligible_total = 0\n"
    "        self._three_layer_last_selected_ids: tuple[str, ...] = ()\n"
    "        self._three_layer_last_universe_truncated = False\n",
    "        self._three_layer_last_eligible_total = 0\n"
    "        self._three_layer_last_candidate_today_total = 0\n"
    "        self._three_layer_last_layer2_unsupported_total = 0\n"
    "        self._three_layer_last_selected_ids: tuple[str, ...] = ()\n"
    "        self._three_layer_last_universe_truncated = False\n"
    "        self._three_layer_last_coverage_complete = True\n"
    "        self._three_layer_last_eligibility_failure_code: str | None = None\n"
    "        self._three_layer_nws_support_cache: dict[tuple[float, float], tuple[float, bool]] = {}\n",
    "runtime state fields",
)

start = runtime.index("    async def _same_day_eligible(")
end = runtime.index("    async def _fetch_same_day_source_bundle(", start)
new_method = '''    async def _nws_point_supported_for_metadata(self, metadata) -> bool:\n        latitude = float(metadata.latitude)\n        longitude = float(metadata.longitude)\n        key = (round(latitude, 4), round(longitude, 4))\n        now = self._station_cache_now()\n        cache = getattr(self, "_three_layer_nws_support_cache", None)\n        if cache is None:\n            cache = {}\n            self._three_layer_nws_support_cache = cache\n        cached = cache.get(key)\n        if cached is not None:\n            age = now - float(cached[0])\n            if 0.0 <= age <= THREE_LAYER_NWS_SUPPORT_CACHE_TTL_SECONDS:\n                value = bool(cached[1])\n                # Dict insertion order gives us a tiny dependency-free bounded LRU.\n                cache.pop(key, None)\n                cache[key] = (now, value)\n                return value\n            cache.pop(key, None)\n\n        supported = await self._same_day_nws.point_supported(\n            latitude=latitude,\n            longitude=longitude,\n        )\n        cache[key] = (now, bool(supported))\n        while len(cache) > THREE_LAYER_NWS_SUPPORT_CACHE_MAX_ENTRIES:\n            cache.pop(next(iter(cache)))\n        return bool(supported)\n\n    async def _same_day_eligible(self, events: tuple[dict, ...]) -> tuple[list[tuple], list[str]]:\n        """Enumerate the complete same-day Layer-2-capable population or fail closed.\n\n        Expected semantic mismatches and explicit NWS /points 404s are legitimate\n        exclusions. Provider outages, malformed station metadata, unexpected parser\n        failures and scan timeouts invalidate the whole selection for the cycle; they\n        must never produce a biased partial research sample.\n        """\n        errors: list[str] = []\n        seen_event_ids: set[str] = set()\n        candidates: list[tuple[str, dict, object, object]] = []\n        loop = asyncio.get_running_loop()\n        scan_deadline = loop.time() + THREE_LAYER_ELIGIBILITY_SCAN_DEADLINE_SECONDS\n        eligibility_now_utc = datetime.now(tz=timezone.utc)\n        utc_today = eligibility_now_utc.date()\n        plausible_local_dates = {\n            utc_today - timedelta(days=1),\n            utc_today,\n            utc_today + timedelta(days=1),\n        }\n\n        self._three_layer_last_eligible_total = 0\n        self._three_layer_last_candidate_today_total = 0\n        self._three_layer_last_layer2_unsupported_total = 0\n        self._three_layer_last_selected_ids = ()\n        self._three_layer_last_universe_truncated = False\n        self._three_layer_last_coverage_complete = True\n        self._three_layer_last_eligibility_failure_code = None\n\n        def fail_closed(code: str, *, truncated: bool = False):\n            self._three_layer_last_selected_ids = ()\n            self._three_layer_last_universe_truncated = bool(truncated)\n            self._three_layer_last_coverage_complete = False\n            self._three_layer_last_eligibility_failure_code = str(code)\n            if code not in errors:\n                errors.append(code)\n            return [], errors\n\n        # Phase 1 is CPU-only. The +/- one UTC-day prefilter is exhaustive for current\n        # civil time zones and prevents future-dated contracts from consuming provider\n        # calls merely to prove that they are not today's station-local target.\n        for event in events:\n            if loop.time() >= scan_deadline:\n                return fail_closed("SAME_DAY_ELIGIBILITY_SCAN_TIMEOUT", truncated=True)\n            if not isinstance(event, dict):\n                continue\n            try:\n                compiled = compile_strict_temperature_event(event)\n                authority = compile_temperature_rule_authority(event, compiled)\n                semantics = build_same_day_contract_semantics(compiled, authority)\n            except StrictWeatherContractError:\n                continue\n            except Exception as exc:\n                code = getattr(exc, "code", type(exc).__name__)\n                event_id = str(event.get("id") or event.get("eventId") or "unknown")\n                return fail_closed(f"SAME_DAY_SEMANTICS:{event_id}:{code}")\n\n            event_id = str(compiled.event_id)\n            if event_id in seen_event_ids:\n                continue\n            seen_event_ids.add(event_id)\n            if not semantics.layer1_adapter_capable:\n                continue\n            if compiled.target_date not in plausible_local_dates:\n                continue\n            station_id = str(compiled.station_hint or "").strip().upper()\n            if not station_id:\n                return fail_closed(f"SAME_DAY_STATION:{event_id}:STATION_ID_MISSING")\n            candidates.append((event_id, event, compiled, semantics))\n\n        # Phase 2 resolves each unique settlement station once. Calls are concurrent but\n        # bounded, and the entire enumeration remains under one monotonic deadline.\n        by_station: dict[str, list[tuple[str, dict, object, object]]] = {}\n        for item in candidates:\n            station_id = str(item[2].station_hint).strip().upper()\n            by_station.setdefault(station_id, []).append(item)\n\n        station_results: dict[str, object] = {}\n        semaphore = asyncio.Semaphore(THREE_LAYER_ELIGIBILITY_CONCURRENCY)\n\n        async def resolve_station(station_id: str, representative):\n            async with semaphore:\n                return station_id, await self._station_metadata_for_compiled(representative)\n\n        if by_station:\n            remaining = scan_deadline - loop.time()\n            if remaining <= 0.0:\n                return fail_closed("SAME_DAY_ELIGIBILITY_SCAN_TIMEOUT", truncated=True)\n            coroutines = [\n                resolve_station(station_id, rows[0][2])\n                for station_id, rows in sorted(by_station.items())\n            ]\n            try:\n                async with asyncio.timeout(remaining):\n                    resolved = await asyncio.gather(*coroutines, return_exceptions=True)\n            except TimeoutError:\n                return fail_closed("SAME_DAY_ELIGIBILITY_SCAN_TIMEOUT", truncated=True)\n            for (station_id, _rows), result in zip(sorted(by_station.items()), resolved):\n                if isinstance(result, BaseException):\n                    code = getattr(result, "code", type(result).__name__)\n                    return fail_closed(f"SAME_DAY_STATION_PROVIDER:{station_id}:{code}")\n                returned_station, metadata = result\n                if returned_station != station_id or metadata is None:\n                    return fail_closed(f"SAME_DAY_STATION_PROVIDER:{station_id}:METADATA_MISSING")\n                station_results[station_id] = metadata\n\n        today_candidates: list[tuple[str, dict, object, object, object]] = []\n        for item in candidates:\n            event_id, event, compiled, semantics = item\n            station_id = str(compiled.station_hint).strip().upper()\n            metadata = station_results.get(station_id)\n            if metadata is None:\n                return fail_closed(f"SAME_DAY_STATION_PROVIDER:{station_id}:METADATA_MISSING")\n            try:\n                zone = ZoneInfo(str(metadata.timezone))\n                local_today = eligibility_now_utc.astimezone(zone).date()\n                # Prove coordinate coercion now so invalid metadata cannot be rebranded as\n                # a normal unsupported NWS point in the next phase.\n                float(metadata.latitude)\n                float(metadata.longitude)\n            except (ZoneInfoNotFoundError, AttributeError, TypeError, ValueError, OverflowError) as exc:\n                return fail_closed(f"SAME_DAY_STATION:{event_id}:{type(exc).__name__}")\n            if compiled.target_date == local_today:\n                today_candidates.append((event_id, event, compiled, semantics, metadata))\n\n        self._three_layer_last_candidate_today_total = len(today_candidates)\n\n        # Phase 3 proves Layer-2 geographic capability. An explicit NWS /points 404 is\n        # the only normal exclusion. Every other failure invalidates this cycle's full\n        # population enumeration rather than silently selecting the stations that happened\n        # to answer. Probe each distinct coordinate once.\n        by_point: dict[tuple[float, float], list[tuple[str, dict, object, object, object]]] = {}\n        for item in today_candidates:\n            metadata = item[4]\n            point = (round(float(metadata.latitude), 4), round(float(metadata.longitude), 4))\n            by_point.setdefault(point, []).append(item)\n\n        point_support: dict[tuple[float, float], bool] = {}\n\n        async def resolve_point(point, metadata):\n            async with semaphore:\n                return point, await self._nws_point_supported_for_metadata(metadata)\n\n        if by_point:\n            remaining = scan_deadline - loop.time()\n            if remaining <= 0.0:\n                return fail_closed("SAME_DAY_ELIGIBILITY_SCAN_TIMEOUT", truncated=True)\n            ordered_points = sorted(by_point.items())\n            coroutines = [resolve_point(point, rows[0][4]) for point, rows in ordered_points]\n            try:\n                async with asyncio.timeout(remaining):\n                    resolved = await asyncio.gather(*coroutines, return_exceptions=True)\n            except TimeoutError:\n                return fail_closed("SAME_DAY_ELIGIBILITY_SCAN_TIMEOUT", truncated=True)\n            for (point, rows), result in zip(ordered_points, resolved):\n                if isinstance(result, BaseException):\n                    code = getattr(result, "code", type(result).__name__)\n                    station_id = str(rows[0][2].station_hint).strip().upper()\n                    return fail_closed(f"SAME_DAY_LAYER2_PROVIDER:{station_id}:{code}")\n                returned_point, supported = result\n                if returned_point != point:\n                    return fail_closed("SAME_DAY_LAYER2_PROVIDER:POINT_IDENTITY_MISMATCH")\n                point_support[point] = bool(supported)\n\n        eligible: list[tuple[str, dict, object, object, object]] = []\n        unsupported = 0\n        for item in today_candidates:\n            metadata = item[4]\n            point = (round(float(metadata.latitude), 4), round(float(metadata.longitude), 4))\n            if point_support.get(point) is True:\n                eligible.append(item)\n            else:\n                unsupported += 1\n\n        eligible.sort(key=lambda item: item[0])\n        self._three_layer_last_layer2_unsupported_total = unsupported\n        self._three_layer_last_eligible_total = len(eligible)\n        if len(eligible) > THREE_LAYER_SELECTION_UNIVERSE_CAP:\n            self._three_layer_last_universe_truncated = True\n            self._three_layer_last_selected_ids = ()\n            self._three_layer_last_coverage_complete = False\n            self._three_layer_last_eligibility_failure_code = "SAME_DAY_SELECTION_UNIVERSE_CAP_EXCEEDED"\n            errors.append(\n                f"SAME_DAY_SELECTION_UNIVERSE_CAP_EXCEEDED:{len(eligible)}>"\n                f"{THREE_LAYER_SELECTION_UNIVERSE_CAP}"\n            )\n            return [], errors\n\n        cursor = self.positions.get_state(THREE_LAYER_CURSOR_KEY, "")\n        selected = _rotate_after_cursor(eligible, cursor, THREE_LAYER_MAX_EVENTS_PER_CYCLE)\n        self._three_layer_last_selected_ids = tuple(str(item[0]) for item in selected)\n        if selected:\n            self.positions.set_state(THREE_LAYER_CURSOR_KEY, str(selected[-1][0]))\n        return selected, errors\n\n'''
runtime = runtime[:start] + new_method + runtime[end:]
runtime = replace_once(
    runtime,
    '                "eligible_events_total": self._three_layer_last_eligible_total,\n'
    '                "selected_event_ids": list(self._three_layer_last_selected_ids),\n',
    '                "eligible_events_total": self._three_layer_last_eligible_total,\n'
    '                "candidate_events_today_total": self._three_layer_last_candidate_today_total,\n'
    '                "layer2_unsupported_events_total": self._three_layer_last_layer2_unsupported_total,\n'
    '                "selected_event_ids": list(self._three_layer_last_selected_ids),\n',
    "runtime status eligibility counts",
)
runtime = replace_once(
    runtime,
    '                "selection_coverage_complete": not self._three_layer_last_universe_truncated,\n',
    '                "selection_coverage_complete": (\n'
    '                    self._three_layer_last_coverage_complete\n'
    '                    and not self._three_layer_last_universe_truncated\n'
    '                ),\n'
    '                "eligibility_failure_code": self._three_layer_last_eligibility_failure_code,\n'
    '                "eligibility_concurrency": THREE_LAYER_ELIGIBILITY_CONCURRENCY,\n',
    "runtime status coverage",
)
runtime_path.write_text(runtime, encoding="utf-8")


test_path = Path("tests/test_weather_only_three_layer_layer2_eligibility.py")
if test_path.exists():
    raise SystemExit(f"refusing to overwrite existing {test_path}")
test_path.write_text(r'''from __future__ import annotations

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
    async def metadata(compiled): return _meta(compiled.station_hint)
    async def supported(*, latitude, longitude): return latitude < 40.05
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
''', encoding="utf-8")

print("ROUND2_PATCH_APPLIED")
