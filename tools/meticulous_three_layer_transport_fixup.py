from __future__ import annotations

from pathlib import Path


def rep(path: str, old: str, new: str, *, expected: int = 1) -> None:
    p = Path(path)
    text = p.read_text(encoding="utf-8")
    found = text.count(old)
    if found != expected:
        raise SystemExit(f"{path}: expected {expected}, found {found}: {old[:180]!r}")
    p.write_text(text.replace(old, new, expected), encoding="utf-8")


def append(path: str, text: str) -> None:
    p = Path(path)
    original = p.read_text(encoding="utf-8")
    sentinel = text.strip().splitlines()[0]
    if sentinel in original:
        raise SystemExit(f"{path}: block already present: {sentinel}")
    p.write_text(original.rstrip() + "\n\n\n" + text.strip() + "\n", encoding="utf-8")


# Make shutdown safe for partially initialized instances as well as the normal path.
rep(
    "polymarket_scanner/weather_only_live_paper_three_layer_validation.py",
    "        await asyncio.gather(\n"
    "            self._three_layer_station_client.close(),\n"
    "            self._three_layer_superseded_nws.close(),\n"
    "            self._three_layer_superseded_gefs.close(),\n"
    "            return_exceptions=True,\n"
    "        )\n",
    "        station_client = getattr(self, \"_three_layer_station_client\", None)\n"
    "        station_close = (\n"
    "            station_client.close() if station_client is not None else asyncio.sleep(0)\n"
    "        )\n"
    "        await asyncio.gather(\n"
    "            station_close,\n"
    "            self._three_layer_superseded_nws.close(),\n"
    "            self._three_layer_superseded_gefs.close(),\n"
    "            return_exceptions=True,\n"
    "        )\n",
)

# Use the normal import and keep transport errors inside the station-metadata error type.
rep(
    "polymarket_scanner/weather_only_three_layer_guarded.py",
    "import asyncio\nimport json\n",
    "import asyncio\nimport hashlib\nimport json\n",
)
rep(
    "polymarket_scanner/weather_only_three_layer_guarded.py",
    'if __import__("hashlib").sha256(viewer_raw).hexdigest() != WRH_VIEWER_SCRIPT_SHA256:',
    'if hashlib.sha256(viewer_raw).hexdigest() != WRH_VIEWER_SCRIPT_SHA256:',
)
rep(
    "polymarket_scanner/weather_only_three_layer_guarded.py",
    "        if status in {404, 410}:\n"
    "            return None\n"
    "        if status >= 400:\n"
    "            raise WeatherStationMetadataError(\"STATION_METADATA_HTTP_STATUS\")\n",
    "        if status in {404, 410}:\n"
    "            return None\n"
    "        if status == 429 or status >= 500:\n"
    "            raise WeatherStationMetadataError(\"STATION_METADATA_RETRYABLE_HTTP_STATUS\")\n"
    "        if status >= 400:\n"
    "            raise WeatherStationMetadataError(\"STATION_METADATA_HTTP_STATUS\")\n",
)
rep(
    "polymarket_scanner/weather_only_three_layer_guarded.py",
    "        except TimeoutError:\n"
    "            raise WRHStationMetadataError(\"WRH_STATION_METADATA_TOTAL_TIMEOUT\") from None\n\n"
    "    async def station(self, station: str):",
    "        except TimeoutError:\n"
    "            raise WRHStationMetadataError(\"WRH_STATION_METADATA_TOTAL_TIMEOUT\") from None\n"
    "        except RuntimeError as exc:\n"
    "            raise WRHStationMetadataError(str(exc)) from None\n\n"
    "    async def station(self, station: str):",
)
rep(
    "polymarket_scanner/weather_only_three_layer_guarded.py",
    "            except WeatherStationMetadataError as exc:\n"
    "                if exc.code not in {\"STATION_METADATA_TIMEOUT\", \"STATION_METADATA_TRANSPORT\"}:\n"
    "                    raise\n"
    "                if attempt + 1 >= STATION_METADATA_MAX_RETRIES:\n"
    "                    raise\n"
    "                await asyncio.sleep(0.5 * (2 ** attempt))\n",
    "            except WeatherStationMetadataError as exc:\n"
    "                retryable = exc.code in {\n"
    "                    \"STATION_METADATA_TIMEOUT\",\n"
    "                    \"STATION_METADATA_TRANSPORT\",\n"
    "                    \"STATION_METADATA_RETRYABLE_HTTP_STATUS\",\n"
    "                }\n"
    "                if not retryable:\n"
    "                    raise\n"
    "                if attempt + 1 >= STATION_METADATA_MAX_RETRIES:\n"
    "                    if exc.code == \"STATION_METADATA_RETRYABLE_HTTP_STATUS\":\n"
    "                        raise WeatherStationMetadataError(\"STATION_METADATA_HTTP_STATUS\") from None\n"
    "                    raise\n"
    "                await asyncio.sleep(0.5 * (2 ** attempt))\n",
)

# Clarify the inherited future-day lane shares this transport-only identity hardening.
rep(
    "polymarket_scanner/weather_only_live_paper_three_layer_validation.py",
    "    async def _station_metadata_for_compiled(self, compiled):\n"
    "        station_id = str(compiled.station_hint or \"\").strip().upper()\n",
    "    async def _station_metadata_for_compiled(self, compiled):\n"
    "        # This virtual hook is also used by the inherited future-day lane. Only\n"
    "        # acquisition safety changes here; parser/identity semantics are unchanged.\n"
    "        station_id = str(compiled.station_hint or \"\").strip().upper()\n",
)

# Strengthen the redirect test to require the typed fail-closed error.
rep(
    "tests/test_weather_only_three_layer_validation_guarded.py",
    "    async def scenario():\n"
    "        await client.http.aclose()\n"
    "        client.http = httpx.AsyncClient(transport=httpx.MockTransport(handler))\n"
    "        monkeypatch.setattr(client, \"_wrh_material\", token)\n"
    "        try:\n"
    "            with pytest.raises(Exception, match=\"WRH_STATION_METADATA_BACKEND_REDIRECT\"):\n"
    "                await client._wrh_station(\"KLGA\")\n",
    "    async def scenario():\n"
    "        from polymarket_scanner.weather_only_wrh_station_metadata import WRHStationMetadataError\n"
    "        await client.http.aclose()\n"
    "        client.http = httpx.AsyncClient(transport=httpx.MockTransport(handler))\n"
    "        monkeypatch.setattr(client, \"_wrh_material\", token)\n"
    "        try:\n"
    "            with pytest.raises(WRHStationMetadataError, match=\"WRH_STATION_METADATA_BACKEND_REDIRECT\"):\n"
    "                await client._wrh_station(\"KLGA\")\n",
)

append(
    "tests/test_weather_only_three_layer_validation_guarded.py",
    r'''
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
''',
)
