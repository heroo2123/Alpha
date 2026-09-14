from __future__ import annotations

from pathlib import Path


def rep(path: str, old: str, new: str, *, expected: int = 1) -> None:
    p = Path(path)
    text = p.read_text(encoding="utf-8")
    found = text.count(old)
    if found != expected:
        raise SystemExit(
            f"{path}: expected exactly {expected} occurrences, found {found}: {old[:180]!r}"
        )
    p.write_text(text.replace(old, new, expected), encoding="utf-8")


def append(path: str, text: str) -> None:
    p = Path(path)
    original = p.read_text(encoding="utf-8")
    sentinel = text.strip().splitlines()[0]
    if sentinel in original:
        raise SystemExit(f"{path}: regression block already present: {sentinel}")
    p.write_text(original.rstrip() + "\n\n\n" + text.strip() + "\n", encoding="utf-8")


# ---------------------------------------------------------------------------
# Guard the same-day station-metadata path too. The three primary sources were raw-
# byte bounded, but station identity still passed through ordinary httpx .get(), which
# decompresses the full body before the existing post-read size check. On the e2-micro
# that left a compression-bomb / proxy / redirect gap before source collection began.
# Keep this new transport scoped to same-day eligibility so reviewed future-day model
# semantics and its existing metadata client remain unchanged.
# ---------------------------------------------------------------------------
rep(
    "polymarket_scanner/weather_only_three_layer_guarded.py",
    "from .weather_only_nws_near_term import (\n"
    "    MAX_RESPONSE_BYTES as NWS_BASE_MAX_RESPONSE_BYTES,\n"
    "    NWSNearTermError,\n"
    "    NWSNearTermGridClient,\n"
    ")\n"
    "from .weather_only_wrh_client import NWSWRHLiveClient\n",
    "from .weather_only_nws_near_term import (\n"
    "    MAX_RESPONSE_BYTES as NWS_BASE_MAX_RESPONSE_BYTES,\n"
    "    NWSNearTermError,\n"
    "    NWSNearTermGridClient,\n"
    ")\n"
    "from .weather_only_station_metadata import (\n"
    "    MAX_RESPONSE_BYTES as STATION_METADATA_MAX_RESPONSE_BYTES,\n"
    "    MAX_RETRIES as STATION_METADATA_MAX_RETRIES,\n"
    "    NWS_STATION_ENDPOINT,\n"
    "    WeatherStationMetadataError,\n"
    "    parse_nws_station_metadata,\n"
    ")\n"
    "from .weather_only_wrh import WRH_VIEWER_SCRIPT_SHA256, WRHSourceError\n"
    "from .weather_only_wrh_client import (\n"
    "    NWSWRHLiveClient,\n"
    "    WRH_BROWSER_ORIGIN,\n"
    "    WRH_TIMESERIES_PAGE,\n"
    "    _discover_api_key_script,\n"
    "    _discover_viewer_script,\n"
    "    _extract_browser_token,\n"
    "    _verify_viewer_credential_contract,\n"
    ")\n"
    "from .weather_only_wrh_station_metadata import (\n"
    "    MAX_RESPONSE_BYTES as WRH_STATION_METADATA_MAX_RESPONSE_BYTES,\n"
    "    WRH_STATION_METADATA_ENDPOINT,\n"
    "    WRHStationMetadataError,\n"
    "    parse_wrh_synoptic_station_metadata,\n"
    ")\n",
)
rep(
    "polymarket_scanner/weather_only_three_layer_guarded.py",
    "NWS_SNAPSHOT_DEADLINE_SECONDS = 20.0\nGEFS_TOTAL_RESPONSE_DEADLINE_SECONDS",
    "NWS_SNAPSHOT_DEADLINE_SECONDS = 20.0\n"
    "STATION_METADATA_REQUEST_DEADLINE_SECONDS = 12.0\n"
    "STATION_METADATA_FALLBACK_DEADLINE_SECONDS = 20.0\n"
    "GEFS_TOTAL_RESPONSE_DEADLINE_SECONDS",
)

station_guard = r'''

async def _bounded_async_bytes(
    http: httpx.AsyncClient,
    url: str,
    *,
    params: dict | None,
    headers: dict[str, str] | None,
    max_bytes: int,
    total_deadline_seconds: float,
    allow_same_host_redirects: bool,
    redirect_code: str,
    encoding_code: str,
    size_code: str,
    timeout_code: str,
    transport_code: str,
) -> tuple[int, bytes, float]:
    """Bound an identity-encoded GET before decoding; never expose request URLs."""
    current = str(url)
    current_params = params
    request_headers = dict(headers or {})
    request_headers["Accept-Encoding"] = "identity"
    try:
        async with asyncio.timeout(total_deadline_seconds):
            for _redirect_index in range(4):
                async with http.stream(
                    "GET",
                    current,
                    params=current_params,
                    headers=request_headers,
                    follow_redirects=False,
                ) as response:
                    if response.is_redirect:
                        location = response.headers.get("location")
                        if not location or not allow_same_host_redirects:
                            raise RuntimeError(redirect_code)
                        nxt = urljoin(str(response.request.url), location)
                        before = urlparse(str(response.request.url))
                        after = urlparse(nxt)
                        if (
                            after.scheme != "https"
                            or not before.hostname
                            or after.hostname != before.hostname
                        ):
                            raise RuntimeError(redirect_code)
                        current = nxt
                        current_params = None
                        continue

                    # Error bodies are irrelevant and may themselves be hostile. Return
                    # the status without consuming them so callers can apply retry/
                    # fallback policy without allocating provider-controlled content.
                    if response.status_code < 200 or response.status_code >= 300:
                        return int(response.status_code), b"", time.time()
                    if not _encoding_is_identity(response.headers) or not _transfer_encoding_supported(
                        response.headers
                    ):
                        raise RuntimeError(encoding_code)
                    length = _content_length(response.headers, code=size_code)
                    if length is not None and length > max_bytes:
                        raise RuntimeError(size_code)
                    if response.is_stream_consumed:
                        raw = bytes(response.content)
                        if len(raw) > max_bytes:
                            raise RuntimeError(size_code)
                    else:
                        payload = bytearray()
                        async for chunk in response.aiter_raw():
                            if len(payload) + len(chunk) > max_bytes:
                                raise RuntimeError(size_code)
                            payload.extend(chunk)
                        raw = bytes(payload)
                    return int(response.status_code), raw, time.time()
            raise RuntimeError(redirect_code)
    except TimeoutError:
        raise RuntimeError(timeout_code) from None
    except httpx.TimeoutException:
        raise RuntimeError(timeout_code) from None
    except httpx.RequestError:
        raise RuntimeError(transport_code) from None


def _strict_utf8(raw: bytes, code: str) -> str:
    try:
        return raw.decode("utf-8")
    except UnicodeError:
        raise RuntimeError(code) from None


def _strict_json_dict(raw: bytes, code: str) -> dict:
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError):
        raise RuntimeError(code) from None
    if not isinstance(value, dict):
        raise RuntimeError(code)
    return value


class GuardedSameDayStationMetadataClient:
    """Location identity only, with bounded NWS and WRH/Synoptic fallback reads."""

    def __init__(self) -> None:
        self.http = httpx.AsyncClient(
            timeout=httpx.Timeout(8.0, connect=4.0, read=8.0, write=5.0, pool=4.0),
            follow_redirects=False,
            trust_env=False,
            limits=httpx.Limits(max_connections=2, max_keepalive_connections=2),
            headers={
                "User-Agent": "polymarket-weather-three-layer-station-guarded/1.0 (+https://github.com/heroo2123/Alpha)",
                "Accept": "*/*",
                "Accept-Encoding": "identity",
            },
        )

    async def close(self) -> None:
        await self.http.aclose()

    @staticmethod
    def _station(value: object) -> str:
        station = str(value or "").strip().upper()
        if len(station) != 4 or not station.isalnum():
            raise WeatherStationMetadataError("STATION_METADATA_STATION_INVALID")
        return station

    async def _nws_once(self, station: str):
        try:
            status, raw, received = await _bounded_async_bytes(
                self.http,
                NWS_STATION_ENDPOINT.format(station=station),
                params=None,
                headers={"Accept": "application/geo+json"},
                max_bytes=STATION_METADATA_MAX_RESPONSE_BYTES,
                total_deadline_seconds=STATION_METADATA_REQUEST_DEADLINE_SECONDS,
                allow_same_host_redirects=False,
                redirect_code="STATION_METADATA_REDIRECT",
                encoding_code="STATION_METADATA_UNSUPPORTED_ENCODING",
                size_code="STATION_METADATA_RESPONSE_CAP",
                timeout_code="STATION_METADATA_TIMEOUT",
                transport_code="STATION_METADATA_TRANSPORT",
            )
        except RuntimeError as exc:
            raise WeatherStationMetadataError(str(exc)) from None
        if status in {404, 410}:
            return None
        if status >= 400:
            raise WeatherStationMetadataError("STATION_METADATA_HTTP_STATUS")
        try:
            payload = _strict_json_dict(raw, "STATION_METADATA_JSON_INVALID")
        except RuntimeError as exc:
            raise WeatherStationMetadataError(str(exc)) from None
        return parse_nws_station_metadata(
            payload,
            requested_station=station,
            received_at=received,
        )

    async def _wrh_material(self) -> str:
        try:
            status, shell_raw, _ = await _bounded_async_bytes(
                self.http,
                WRH_TIMESERIES_PAGE,
                params={"site": "KLGA", "hourly": "true", "obs": "tabular"},
                headers=None,
                max_bytes=WRH_STATION_METADATA_MAX_RESPONSE_BYTES,
                total_deadline_seconds=STATION_METADATA_REQUEST_DEADLINE_SECONDS,
                allow_same_host_redirects=True,
                redirect_code="WRH_STATION_METADATA_SHELL_REDIRECT",
                encoding_code="WRH_STATION_METADATA_SHELL_ENCODING",
                size_code="WRH_STATION_METADATA_SHELL_RESPONSE_CAP",
                timeout_code="WRH_STATION_METADATA_SHELL_TIMEOUT",
                transport_code="WRH_STATION_METADATA_SHELL_TRANSPORT",
            )
            if status >= 400:
                raise RuntimeError("WRH_STATION_METADATA_SHELL_HTTP_ERROR")
            shell_text = _strict_utf8(shell_raw, "WRH_STATION_METADATA_SHELL_TEXT_INVALID")
            viewer_url = _discover_viewer_script(shell_text)
            key_url = _discover_api_key_script(shell_text)

            status, viewer_raw, _ = await _bounded_async_bytes(
                self.http,
                viewer_url,
                params=None,
                headers=None,
                max_bytes=WRH_STATION_METADATA_MAX_RESPONSE_BYTES,
                total_deadline_seconds=STATION_METADATA_REQUEST_DEADLINE_SECONDS,
                allow_same_host_redirects=True,
                redirect_code="WRH_STATION_METADATA_VIEWER_REDIRECT",
                encoding_code="WRH_STATION_METADATA_VIEWER_ENCODING",
                size_code="WRH_STATION_METADATA_VIEWER_RESPONSE_CAP",
                timeout_code="WRH_STATION_METADATA_VIEWER_TIMEOUT",
                transport_code="WRH_STATION_METADATA_VIEWER_TRANSPORT",
            )
            if status >= 400:
                raise RuntimeError("WRH_STATION_METADATA_VIEWER_HTTP_ERROR")
            if __import__("hashlib").sha256(viewer_raw).hexdigest() != WRH_VIEWER_SCRIPT_SHA256:
                raise RuntimeError("WRH_STATION_METADATA_VIEWER_SHA_MISMATCH")
            viewer_text = _strict_utf8(viewer_raw, "WRH_STATION_METADATA_VIEWER_TEXT_INVALID")
            _verify_viewer_credential_contract(viewer_text)

            status, key_raw, _ = await _bounded_async_bytes(
                self.http,
                key_url,
                params=None,
                headers=None,
                max_bytes=WRH_STATION_METADATA_MAX_RESPONSE_BYTES,
                total_deadline_seconds=STATION_METADATA_REQUEST_DEADLINE_SECONDS,
                allow_same_host_redirects=True,
                redirect_code="WRH_STATION_METADATA_KEY_REDIRECT",
                encoding_code="WRH_STATION_METADATA_KEY_ENCODING",
                size_code="WRH_STATION_METADATA_KEY_RESPONSE_CAP",
                timeout_code="WRH_STATION_METADATA_KEY_TIMEOUT",
                transport_code="WRH_STATION_METADATA_KEY_TRANSPORT",
            )
            if status >= 400:
                raise RuntimeError("WRH_STATION_METADATA_KEY_HTTP_ERROR")
            key_text = _strict_utf8(key_raw, "WRH_STATION_METADATA_KEY_TEXT_INVALID")
            return _extract_browser_token(key_text)
        except WRHSourceError as exc:
            raise WRHStationMetadataError(f"WRH_STATION_METADATA_BROWSER:{exc.code}") from None
        except RuntimeError as exc:
            raise WRHStationMetadataError(str(exc)) from None

    async def _wrh_station(self, station: str):
        try:
            async with asyncio.timeout(STATION_METADATA_FALLBACK_DEADLINE_SECONDS):
                last_status = 0
                for attempt in range(2):
                    token = await self._wrh_material()
                    try:
                        status, raw, received = await _bounded_async_bytes(
                            self.http,
                            WRH_STATION_METADATA_ENDPOINT,
                            params={"stid": station, "complete": 1, "token": token},
                            headers={"Origin": WRH_BROWSER_ORIGIN},
                            max_bytes=WRH_STATION_METADATA_MAX_RESPONSE_BYTES,
                            total_deadline_seconds=STATION_METADATA_REQUEST_DEADLINE_SECONDS,
                            # A token-bearing request is never redirected, even to the
                            # same host. The ephemeral credential cannot be forwarded.
                            allow_same_host_redirects=False,
                            redirect_code="WRH_STATION_METADATA_BACKEND_REDIRECT",
                            encoding_code="WRH_STATION_METADATA_BACKEND_ENCODING",
                            size_code="WRH_STATION_METADATA_BACKEND_RESPONSE_CAP",
                            timeout_code="WRH_STATION_METADATA_BACKEND_TIMEOUT",
                            transport_code="WRH_STATION_METADATA_BACKEND_TRANSPORT",
                        )
                    finally:
                        token = ""
                    last_status = status
                    if 200 <= status < 300:
                        try:
                            payload = _strict_json_dict(raw, "WRH_STATION_METADATA_JSON_INVALID")
                        except RuntimeError as exc:
                            raise WRHStationMetadataError(str(exc)) from None
                        return parse_wrh_synoptic_station_metadata(
                            payload,
                            requested_station=station,
                            received_at=received,
                        )
                    # One authentication-like failure may reflect a just-rotated
                    # browser credential. Re-discover once; all other statuses fail.
                    if status not in {401, 403} or attempt == 1:
                        break
                raise WRHStationMetadataError(
                    f"WRH_STATION_METADATA_BACKEND_HTTP_STATUS_{last_status}"
                )
        except TimeoutError:
            raise WRHStationMetadataError("WRH_STATION_METADATA_TOTAL_TIMEOUT") from None

    async def station(self, station: str):
        station_id = self._station(station)
        for attempt in range(STATION_METADATA_MAX_RETRIES):
            try:
                result = await self._nws_once(station_id)
            except WeatherStationMetadataError as exc:
                if exc.code not in {"STATION_METADATA_TIMEOUT", "STATION_METADATA_TRANSPORT"}:
                    raise
                if attempt + 1 >= STATION_METADATA_MAX_RETRIES:
                    raise
                await asyncio.sleep(0.5 * (2 ** attempt))
                continue
            if result is not None:
                return result
            return await self._wrh_station(station_id)
        raise WeatherStationMetadataError("STATION_METADATA_RETRY_EXHAUSTED")
'''
rep(
    "polymarket_scanner/weather_only_three_layer_guarded.py",
    "\ndef _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:\n",
    station_guard + "\n\ndef _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:\n",
)

# ---------------------------------------------------------------------------
# Wire the guarded metadata client only into the same-day selector and use one frozen
# UTC instant for every timezone conversion, so crossing local midnight during a slow
# scan cannot make event eligibility depend on iteration order.
# ---------------------------------------------------------------------------
rep(
    "polymarket_scanner/weather_only_live_paper_three_layer_validation.py",
    "from datetime import datetime\n",
    "from datetime import datetime, timezone\n",
)
rep(
    "polymarket_scanner/weather_only_live_paper_three_layer_validation.py",
    "from .weather_only_live_paper_final import FinalWeatherLivePaperService\n",
    "from .weather_only_live_paper_corrective import (\n"
    "    STATION_METADATA_CACHE_MAX_ENTRIES,\n"
    "    STATION_METADATA_CACHE_TTL_SECONDS,\n"
    ")\n"
    "from .weather_only_live_paper_final import FinalWeatherLivePaperService\n",
)
rep(
    "polymarket_scanner/weather_only_live_paper_three_layer_validation.py",
    "from .weather_only_three_layer_guarded import (\n"
    "    GuardedNWSNearTermGridClient,\n",
    "from .weather_only_three_layer_guarded import (\n"
    "    GuardedNWSNearTermGridClient,\n"
    "    GuardedSameDayStationMetadataClient,\n",
)
rep(
    "polymarket_scanner/weather_only_live_paper_three_layer_validation.py",
    "        guarded_wrh = GuardedNWSWRHLiveClient()\n"
    "        guarded_nws = GuardedNWSNearTermGridClient()\n"
    "        guarded_gefs = GuardedOpenMeteoGEFSHourlyClient()\n",
    "        guarded_wrh = GuardedNWSWRHLiveClient()\n"
    "        guarded_nws = GuardedNWSNearTermGridClient()\n"
    "        guarded_gefs = GuardedOpenMeteoGEFSHourlyClient()\n"
    "        guarded_station = GuardedSameDayStationMetadataClient()\n",
)
rep(
    "polymarket_scanner/weather_only_live_paper_three_layer_validation.py",
    "        self._same_day_wrh = guarded_wrh\n"
    "        self._same_day_nws = guarded_nws\n"
    "        self._same_day_gefs = guarded_gefs\n",
    "        self._same_day_wrh = guarded_wrh\n"
    "        self._same_day_nws = guarded_nws\n"
    "        self._same_day_gefs = guarded_gefs\n"
    "        self._three_layer_station_client = guarded_station\n",
)
rep(
    "polymarket_scanner/weather_only_live_paper_three_layer_validation.py",
    "        await asyncio.gather(\n"
    "            self._three_layer_superseded_nws.close(),\n"
    "            self._three_layer_superseded_gefs.close(),\n"
    "            return_exceptions=True,\n"
    "        )\n"
    "        await super().close()\n\n"
    "    async def _same_day_eligible",
    "        await asyncio.gather(\n"
    "            self._three_layer_station_client.close(),\n"
    "            self._three_layer_superseded_nws.close(),\n"
    "            self._three_layer_superseded_gefs.close(),\n"
    "            return_exceptions=True,\n"
    "        )\n"
    "        await super().close()\n\n"
    "    async def _station_metadata_for_compiled(self, compiled):\n"
    "        station_id = str(compiled.station_hint or \"\").strip().upper()\n"
    "        if not station_id:\n"
    "            return None\n"
    "        now = self._station_cache_now()\n"
    "        cached = self._bounded_station_metadata.get(station_id)\n"
    "        if cached is not None:\n"
    "            age = now - float(cached[0])\n"
    "            if 0.0 <= age <= STATION_METADATA_CACHE_TTL_SECONDS:\n"
    "                self._bounded_station_metadata.move_to_end(station_id)\n"
    "                return cached[1]\n"
    "            self._bounded_station_metadata.pop(station_id, None)\n"
    "        metadata = await self._three_layer_station_client.station(station_id)\n"
    "        self._bounded_station_metadata[station_id] = (now, metadata)\n"
    "        self._bounded_station_metadata.move_to_end(station_id)\n"
    "        while len(self._bounded_station_metadata) > STATION_METADATA_CACHE_MAX_ENTRIES:\n"
    "            self._bounded_station_metadata.popitem(last=False)\n"
    "        return metadata\n\n"
    "    async def _same_day_eligible",
)
rep(
    "polymarket_scanner/weather_only_live_paper_three_layer_validation.py",
    "        loop = asyncio.get_running_loop()\n"
    "        scan_deadline = loop.time() + THREE_LAYER_ELIGIBILITY_SCAN_DEADLINE_SECONDS\n",
    "        loop = asyncio.get_running_loop()\n"
    "        scan_deadline = loop.time() + THREE_LAYER_ELIGIBILITY_SCAN_DEADLINE_SECONDS\n"
    "        eligibility_now_utc = datetime.now(tz=timezone.utc)\n",
)
rep(
    "polymarket_scanner/weather_only_live_paper_three_layer_validation.py",
    "                local_today = datetime.now(tz=zone).date()\n",
    "                local_today = eligibility_now_utc.astimezone(zone).date()\n",
)

# ---------------------------------------------------------------------------
# Adversarial transport and wrapper regressions.
# ---------------------------------------------------------------------------
append(
    "tests/test_weather_only_three_layer_validation_guarded.py",
    r'''
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
        await client.http.aclose()
        client.http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        monkeypatch.setattr(client, "_wrh_material", token)
        try:
            with pytest.raises(Exception, match="WRH_STATION_METADATA_BACKEND_REDIRECT"):
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
''',
)

append(
    "tests/test_weather_only_three_layer_finalization.py",
    r'''
def test_three_layer_eligibility_uses_one_frozen_utc_clock_for_all_station_dates():
    source = Path(
        "polymarket_scanner/weather_only_live_paper_three_layer_validation.py"
    ).read_text(encoding="utf-8")
    assert "eligibility_now_utc = datetime.now(tz=timezone.utc)" in source
    assert "eligibility_now_utc.astimezone(zone).date()" in source
    assert "local_today = datetime.now(tz=zone).date()" not in source
''',
)
