from __future__ import annotations

import hashlib
import json
from datetime import date
from urllib.parse import parse_qs

import httpx
import pytest

import polymarket_scanner.weather_only_wrh_client as wrh_client_module
from polymarket_scanner.weather_only_wrh import WRHSourceError
from polymarket_scanner.weather_only_wrh_client import (
    NWSWRHLiveClient,
    WRH_API_KEY_SCRIPT_PATH,
    WRH_LIVE_CLIENT_VERSION,
    WRH_SYNOPTIC_ENDPOINT,
    WRH_TIMESERIES_PAGE,
)


TARGET = date(2026, 9, 10)
SECRET = "publicBrowserCredential_TEST_123456789"
VIEWER_BODY = b"// pinned viewer fixture"
VIEWER_SHA = hashlib.sha256(VIEWER_BODY).hexdigest()


def _shell(*, key_src: str = "/source/wrh/timeseries/apiKey.js") -> str:
    return (
        '<html><head>'
        '<script src="/source/wrh/timeseries/obs.js?v202601121730"></script>'
        f'<script src="{key_src}"></script>'
        '</head><body></body></html>'
    )


def _payload() -> dict:
    return {
        "SUMMARY": {"RESPONSE_MESSAGE": "OK"},
        "STATION": [{
            "STID": "KLGA",
            "SHORTNAME": "GLOBAL-METAR",
            "TIMEZONE": "America/New_York",
            "OBSERVATIONS": {
                "date_time": [
                    "2026-09-10T00:51:00-04:00",
                    "2026-09-10T12:51:00-04:00",
                    "2026-09-11T00:51:00-04:00",
                ],
                "air_temp_set_1": [70.4, 80.5, 75.0],
                "metar_set_1": [
                    "KLGA 100451Z AUTO ...",
                    "KLGA 101651Z AUTO ...",
                    "KLGA 110451Z AUTO ...",
                ],
                "sea_level_pressure_set_1": [1012.0, 1010.0, 1011.0],
            },
        }],
    }


def _transport(*, backend_status: int = 200, backend_body: bytes | None = None, key_body: str | None = None):
    seen = {"backend_query": None}

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if str(request.url).startswith(WRH_TIMESERIES_PAGE):
            return httpx.Response(200, text=_shell(), request=request)
        if path.endswith("/source/wrh/timeseries/obs.js"):
            return httpx.Response(200, content=VIEWER_BODY, request=request)
        if path == WRH_API_KEY_SCRIPT_PATH:
            body = key_body if key_body is not None else f"var token = 'token={SECRET}';"
            return httpx.Response(200, text=body, request=request)
        if str(request.url).startswith(WRH_SYNOPTIC_ENDPOINT):
            query = parse_qs(request.url.query.decode())
            seen["backend_query"] = query
            content = backend_body if backend_body is not None else json.dumps(_payload()).encode()
            return httpx.Response(backend_status, content=content, request=request)
        return httpx.Response(404, request=request)

    return httpx.MockTransport(handler), seen


def _client(monkeypatch, transport: httpx.MockTransport) -> NWSWRHLiveClient:
    monkeypatch.setattr(wrh_client_module, "WRH_VIEWER_SCRIPT_SHA256", VIEWER_SHA)
    http = httpx.Client(transport=transport, follow_redirects=True)
    return NWSWRHLiveClient(http_client=http)


def test_live_client_uses_ephemeral_browser_token_and_returns_only_redacted_evidence(monkeypatch):
    transport, seen = _transport()
    client = _client(monkeypatch, transport)
    result = client.fetch_snapshot(station="klga", target_date=TARGET, received_at=1234.5)

    query = seen["backend_query"]
    assert query is not None
    assert query["token"] == [SECRET]
    assert query["STID"] == ["KLGA"]
    assert query["units"] == ["temp|F,speed|mph,english"]
    assert query["start"] == ["202609100000"]
    assert query["end"] == ["202609112359"]
    assert query["complete"] == ["1"]
    assert query["obtimezone"] == ["local"]

    assert result.client_version == WRH_LIVE_CLIENT_VERSION
    assert result.station == "KLGA"
    assert result.snapshot.target_high_f == 81
    assert result.snapshot.target_low_f == 70
    assert result.snapshot.first_following_row is not None
    assert result.snapshot.calibration_label_authority is False
    assert result.snapshot.settlement_label_authority is False
    assert result.snapshot.financial_authority is False
    assert result.token_persisted is False
    assert result.financial_authority is False
    assert len(result.transport_evidence_sha256) == 64

    serialized = json.dumps(result.as_dict(), sort_keys=True)
    assert SECRET not in serialized
    assert "token=" not in serialized.lower()


def test_tokenized_backend_http_failure_is_sanitized_and_does_not_leak_request_url(monkeypatch):
    transport, _ = _transport(backend_status=503)
    client = _client(monkeypatch, transport)
    with pytest.raises(WRHSourceError) as raised:
        client.fetch_snapshot(station="KLGA", target_date=TARGET, received_at=1234.5)
    assert raised.value.code == "WRH_LIVE_BACKEND_HTTP_ERROR"
    assert str(raised.value) == "WRH_LIVE_BACKEND_HTTP_ERROR"
    assert SECRET not in str(raised.value)


def test_browser_token_is_required_to_be_unique_and_never_returned(monkeypatch):
    body = f"var token = '{SECRET}';\nvar apiKey = 'DIFFERENT_SECRET_12345678';"
    transport, _ = _transport(key_body=body)
    client = _client(monkeypatch, transport)
    with pytest.raises(WRHSourceError) as raised:
        client.fetch_snapshot(station="KLGA", target_date=TARGET, received_at=1234.5)
    assert raised.value.code == "WRH_LIVE_BROWSER_TOKEN_DISCOVERY_FAILED"
    assert SECRET not in str(raised.value)


def test_backend_invalid_json_fails_closed_without_exposing_token(monkeypatch):
    transport, _ = _transport(backend_body=b"not-json")
    client = _client(monkeypatch, transport)
    with pytest.raises(WRHSourceError) as raised:
        client.fetch_snapshot(station="KLGA", target_date=TARGET, received_at=1234.5)
    assert raised.value.code == "WRH_LIVE_BACKEND_JSON_INVALID"
    assert SECRET not in str(raised.value)


def test_unexpected_api_key_script_host_or_path_fails_before_credential_fetch(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        if str(request.url).startswith(WRH_TIMESERIES_PAGE):
            return httpx.Response(
                200,
                text=_shell(key_src="https://evil.example/apiKey.js"),
                request=request,
            )
        return httpx.Response(500, request=request)

    monkeypatch.setattr(wrh_client_module, "WRH_VIEWER_SCRIPT_SHA256", VIEWER_SHA)
    client = NWSWRHLiveClient(http_client=httpx.Client(transport=httpx.MockTransport(handler)))
    with pytest.raises(WRHSourceError) as raised:
        client.fetch_snapshot(station="KLGA", target_date=TARGET, received_at=1234.5)
    assert raised.value.code == "WRH_LIVE_API_KEY_SCRIPT_DISCOVERY_FAILED"


def test_viewer_script_hash_drift_fails_before_api_token_or_backend_use(monkeypatch):
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        if str(request.url).startswith(WRH_TIMESERIES_PAGE):
            return httpx.Response(200, text=_shell(), request=request)
        if request.url.path.endswith("/source/wrh/timeseries/obs.js"):
            return httpx.Response(200, content=b"drifted viewer", request=request)
        if request.url.path == WRH_API_KEY_SCRIPT_PATH:
            return httpx.Response(200, text=f"var token = '{SECRET}';", request=request)
        return httpx.Response(500, request=request)

    monkeypatch.setattr(wrh_client_module, "WRH_VIEWER_SCRIPT_SHA256", VIEWER_SHA)
    client = NWSWRHLiveClient(http_client=httpx.Client(transport=httpx.MockTransport(handler)))
    with pytest.raises(WRHSourceError) as raised:
        client.fetch_snapshot(station="KLGA", target_date=TARGET, received_at=1234.5)
    assert raised.value.code == "WRH_LIVE_VIEWER_SCRIPT_SHA_MISMATCH"
    assert WRH_API_KEY_SCRIPT_PATH not in calls


@pytest.mark.parametrize("station", ["", "LGA", "TOOLONG", "K LGA"])
def test_station_identity_is_strict(station):
    client = NWSWRHLiveClient(http_client=httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(500))))
    with pytest.raises(WRHSourceError) as raised:
        client.fetch_snapshot(station=station, target_date=TARGET, received_at=1.0)
    assert raised.value.code == "WRH_LIVE_STATION_INVALID"
