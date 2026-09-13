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
    WRH_BROWSER_ORIGIN,
    WRH_BROWSER_TOKEN_IDENTIFIER,
    WRH_LIVE_CLIENT_VERSION,
    WRH_STATION_METADATA_ENDPOINT,
    WRH_SYNOPTIC_ENDPOINT,
    WRH_TIMESERIES_PAGE,
    _local_query_bounds,
)


TARGET = date(2026, 9, 10)
RECEIVED = 1789192800.0
SECRET = "publicBrowserCredential_TEST_123456789"
VIEWER_BODY = b"var InfoToGet='x&token='+mesoToken+'&obtimezone=local';"
VIEWER_SHA = hashlib.sha256(VIEWER_BODY).hexdigest()


def _shell(*, key_src: str = "/source/wrh/apiKey.js") -> str:
    return (
        '<html><head>'
        '<script src="/source/wrh/timeseries/obs.js?v202601121730"></script>'
        f'<script src="{key_src}"></script>'
        '</head><body></body></html>'
    )


def _metadata_payload(*, station: str = "KLGA", timezone: str = "America/New_York") -> dict:
    return {
        "SUMMARY": {"RESPONSE_MESSAGE": "OK"},
        "STATION": [{
            "STID": station,
            "SHORTNAME": "GLOBAL-METAR",
            "TIMEZONE": timezone,
        }],
    }


def _payload() -> dict:
    return {
        "UNITS": {"air_temp": "Fahrenheit"},
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


def _transport(
    *,
    backend_status: int = 200,
    backend_body: bytes | None = None,
    metadata_status: int = 200,
    metadata_body: bytes | None = None,
    key_body: str | None = None,
    viewer_body: bytes = VIEWER_BODY,
    require_origin: bool = False,
):
    seen = {
        "backend_query": None,
        "metadata_query": None,
        "backend_origin": None,
        "metadata_origin": None,
        "paths": [],
        "backend_received": False,
    }

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        seen["paths"].append(path)
        if str(request.url).startswith(WRH_TIMESERIES_PAGE):
            return httpx.Response(200, text=_shell(), request=request)
        if path.endswith("/source/wrh/timeseries/obs.js"):
            return httpx.Response(200, content=viewer_body, request=request)
        if path == WRH_API_KEY_SCRIPT_PATH:
            body = key_body if key_body is not None else f"var mesoToken = '{SECRET}';"
            return httpx.Response(200, text=body, request=request)
        if str(request.url).startswith(WRH_STATION_METADATA_ENDPOINT):
            seen["metadata_query"] = parse_qs(request.url.query.decode())
            seen["metadata_origin"] = request.headers.get("origin")
            if require_origin and request.headers.get("origin") != WRH_BROWSER_ORIGIN:
                return httpx.Response(403, request=request)
            content = metadata_body if metadata_body is not None else json.dumps(_metadata_payload()).encode()
            return httpx.Response(metadata_status, content=content, request=request)
        if str(request.url).startswith(WRH_SYNOPTIC_ENDPOINT):
            query = parse_qs(request.url.query.decode())
            seen["backend_query"] = query
            seen["backend_origin"] = request.headers.get("origin")
            seen["backend_received"] = True
            if require_origin and request.headers.get("origin") != WRH_BROWSER_ORIGIN:
                return httpx.Response(403, request=request)
            content = backend_body if backend_body is not None else json.dumps(_payload()).encode()
            return httpx.Response(backend_status, content=content, request=request)
        return httpx.Response(404, request=request)

    return httpx.MockTransport(handler), seen


def _client(monkeypatch, transport: httpx.MockTransport) -> NWSWRHLiveClient:
    monkeypatch.setattr(wrh_client_module, "WRH_VIEWER_SCRIPT_SHA256", VIEWER_SHA)
    http = httpx.Client(transport=transport, follow_redirects=True)
    return NWSWRHLiveClient(http_client=http)


def test_discovered_contract_is_the_actual_wrh_source_identity():
    assert WRH_API_KEY_SCRIPT_PATH == "/source/wrh/apiKey.js"
    assert WRH_BROWSER_TOKEN_IDENTIFIER == "mesoToken"
    assert WRH_BROWSER_ORIGIN == "https://www.weather.gov"


def test_live_client_uses_station_local_midnight_converted_to_utc(monkeypatch):
    transport, seen = _transport(require_origin=True)
    client = _client(monkeypatch, transport)
    result = client.fetch_snapshot(station="klga", target_date=TARGET, received_at=RECEIVED)

    meta = seen["metadata_query"]
    assert meta is not None
    assert meta["token"] == [SECRET]
    assert meta["stid"] == ["KLGA"]
    assert seen["metadata_origin"] == WRH_BROWSER_ORIGIN

    query = seen["backend_query"]
    assert query is not None
    assert query["token"] == [SECRET]
    assert query["STID"] == ["KLGA"]
    assert query["units"] == ["temp|F,speed|mph,english"]
    # Sep 10 00:00 EDT = 04:00 UTC; Sep 11 23:59 EDT = Sep 12 03:59 UTC.
    assert query["start"] == ["202609100400"]
    assert query["end"] == ["202609120359"]
    assert query["complete"] == ["1"]
    assert query["obtimezone"] == ["local"]
    assert seen["backend_origin"] == WRH_BROWSER_ORIGIN

    assert result.client_version == WRH_LIVE_CLIENT_VERSION
    assert result.station == "KLGA"
    assert result.station_timezone == "America/New_York"
    assert result.query_start_utc == "2026-09-10T04:00:00+00:00"
    assert result.query_end_utc == "2026-09-12T03:59:00+00:00"
    assert result.api_key_script_url == "https://www.weather.gov/source/wrh/apiKey.js"
    assert result.snapshot.received_at == RECEIVED
    assert result.fetched_at == RECEIVED
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
    assert result.as_dict()["backend_origin"] == WRH_BROWSER_ORIGIN
    assert SECRET not in serialized
    assert "token=" not in serialized.lower()


@pytest.mark.parametrize(
    ("timezone_name", "expected_start", "expected_end"),
    [
        ("America/New_York", "202609120400", "202609140359"),
        ("America/Los_Angeles", "202609120700", "202609140659"),
        ("Europe/London", "202609112300", "202609132259"),
        ("Europe/Berlin", "202609112200", "202609132159"),
        ("Asia/Tokyo", "202609111500", "202609131459"),
        ("Australia/Sydney", "202609111400", "202609131359"),
    ],
)
def test_six_city_local_days_are_never_approximated_by_utc_calendar_dates(
    timezone_name, expected_start, expected_end
):
    start, end = _local_query_bounds(date(2026, 9, 12), timezone_name)
    assert start.strftime("%Y%m%d%H%M") == expected_start
    assert end.strftime("%Y%m%d%H%M") == expected_end


def test_dst_23_and_25_hour_days_convert_boundaries_independently():
    spring_start, spring_end = _local_query_bounds(date(2026, 3, 29), "Europe/Berlin")
    assert spring_start.strftime("%Y%m%d%H%M") == "202603282300"
    assert spring_end.strftime("%Y%m%d%H%M") == "202603302159"
    assert (spring_end - spring_start).total_seconds() == 47 * 3600 - 60

    fall_start, fall_end = _local_query_bounds(date(2026, 10, 25), "Europe/Berlin")
    assert fall_start.strftime("%Y%m%d%H%M") == "202610242200"
    assert fall_end.strftime("%Y%m%d%H%M") == "202610262259"
    assert (fall_end - fall_start).total_seconds() == 49 * 3600 - 60


def test_munich_early_local_extreme_is_inside_enforced_utc_request(monkeypatch):
    target = date(2026, 9, 12)
    metadata = _metadata_payload(station="EDDM", timezone="Europe/Berlin")
    payload = {
        "UNITS": {"air_temp": "Fahrenheit"},
        "SUMMARY": {"RESPONSE_MESSAGE": "OK"},
        "STATION": [{
            "STID": "EDDM",
            "SHORTNAME": "GLOBAL-METAR",
            "TIMEZONE": "Europe/Berlin",
            "OBSERVATIONS": {
                "date_time": [
                    "2026-09-12T00:51:00+02:00",
                    "2026-09-12T12:51:00+02:00",
                    "2026-09-13T00:51:00+02:00",
                ],
                "air_temp_set_1": [45.0, 60.0, 52.0],
                "metar_set_1": ["EDDM ...", "EDDM ...", "EDDM ..."],
                "sea_level_pressure_set_1": [1012.0, 1010.0, 1011.0],
            },
        }],
    }
    transport, seen = _transport(
        metadata_body=json.dumps(metadata).encode(),
        backend_body=json.dumps(payload).encode(),
    )
    client = _client(monkeypatch, transport)
    result = client.fetch_snapshot(station="EDDM", target_date=target, received_at=1789279200.0)
    query = seen["backend_query"]
    assert query["start"] == ["202609112200"]
    assert query["end"] == ["202609132159"]
    assert result.snapshot.target_low_f == 45


def test_production_receipt_timestamp_is_taken_only_after_backend_response(monkeypatch):
    transport, seen = _transport(require_origin=True)
    client = _client(monkeypatch, transport)
    calls = []

    def after_backend_now():
        calls.append("now")
        assert seen["backend_received"] is True
        return RECEIVED

    monkeypatch.setattr(wrh_client_module, "_now", after_backend_now)
    result = client.fetch_snapshot(station="KLGA", target_date=TARGET)

    assert calls == ["now"]
    assert result.fetched_at == RECEIVED
    assert result.snapshot.received_at == RECEIVED


def test_tokenized_backend_http_failure_is_sanitized_and_does_not_leak_request_url(monkeypatch):
    transport, _ = _transport(backend_status=503)
    client = _client(monkeypatch, transport)
    with pytest.raises(WRHSourceError) as raised:
        client.fetch_snapshot(station="KLGA", target_date=TARGET, received_at=RECEIVED)
    assert raised.value.code == "WRH_LIVE_BACKEND_HTTP_ERROR"
    assert str(raised.value) == "WRH_LIVE_BACKEND_HTTP_ERROR"
    assert SECRET not in str(raised.value)


def test_metadata_failure_is_fail_closed_before_timeseries_request(monkeypatch):
    transport, seen = _transport(metadata_status=503)
    client = _client(monkeypatch, transport)
    with pytest.raises(WRHSourceError) as raised:
        client.fetch_snapshot(station="KLGA", target_date=TARGET, received_at=RECEIVED)
    assert raised.value.code == "WRH_LIVE_METADATA_HTTP_ERROR"
    assert seen["backend_query"] is None
    assert SECRET not in str(raised.value)


def test_mesotoken_assignment_is_required_to_be_unique_and_never_returned(monkeypatch):
    body = (
        f"var mesoToken = '{SECRET}';\n"
        "mesoToken = 'DIFFERENT_SECRET_12345678';"
    )
    transport, _ = _transport(key_body=body)
    client = _client(monkeypatch, transport)
    with pytest.raises(WRHSourceError) as raised:
        client.fetch_snapshot(station="KLGA", target_date=TARGET, received_at=RECEIVED)
    assert raised.value.code == "WRH_LIVE_BROWSER_TOKEN_DISCOVERY_FAILED"
    assert SECRET not in str(raised.value)


def test_prefixed_mesotoken_is_rejected_as_transport_contract_drift(monkeypatch):
    transport, _ = _transport(key_body=f"var mesoToken = 'token={SECRET}';")
    client = _client(monkeypatch, transport)
    with pytest.raises(WRHSourceError) as raised:
        client.fetch_snapshot(station="KLGA", target_date=TARGET, received_at=RECEIVED)
    assert raised.value.code == "WRH_LIVE_BROWSER_TOKEN_SHAPE_MISMATCH"
    assert SECRET not in str(raised.value)


def test_unrelated_token_variable_cannot_substitute_for_mesotoken(monkeypatch):
    transport, _ = _transport(key_body=f"var token = '{SECRET}';")
    client = _client(monkeypatch, transport)
    with pytest.raises(WRHSourceError) as raised:
        client.fetch_snapshot(station="KLGA", target_date=TARGET, received_at=RECEIVED)
    assert raised.value.code == "WRH_LIVE_BROWSER_TOKEN_DISCOVERY_FAILED"


def test_backend_invalid_json_fails_closed_without_exposing_token(monkeypatch):
    transport, _ = _transport(backend_body=b"not-json")
    client = _client(monkeypatch, transport)
    with pytest.raises(WRHSourceError) as raised:
        client.fetch_snapshot(station="KLGA", target_date=TARGET, received_at=RECEIVED)
    assert raised.value.code == "WRH_LIVE_BACKEND_JSON_INVALID"
    assert SECRET not in str(raised.value)


def test_unexpected_api_key_script_host_fails_before_credential_fetch(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        if str(request.url).startswith(WRH_TIMESERIES_PAGE):
            return httpx.Response(
                200,
                text=_shell(key_src="https://evil.example/source/wrh/apiKey.js"),
                request=request,
            )
        return httpx.Response(500, request=request)

    monkeypatch.setattr(wrh_client_module, "WRH_VIEWER_SCRIPT_SHA256", VIEWER_SHA)
    client = NWSWRHLiveClient(http_client=httpx.Client(transport=httpx.MockTransport(handler)))
    with pytest.raises(WRHSourceError) as raised:
        client.fetch_snapshot(station="KLGA", target_date=TARGET, received_at=RECEIVED)
    assert raised.value.code == "WRH_LIVE_API_KEY_SCRIPT_IDENTITY_INVALID"


def test_wrong_api_key_script_path_fails_discovery():
    def handler(request: httpx.Request) -> httpx.Response:
        if str(request.url).startswith(WRH_TIMESERIES_PAGE):
            return httpx.Response(200, text=_shell(key_src="/source/wrh/timeseries/apiKey.js"), request=request)
        return httpx.Response(500, request=request)

    client = NWSWRHLiveClient(http_client=httpx.Client(transport=httpx.MockTransport(handler)))
    with pytest.raises(WRHSourceError) as raised:
        client.fetch_snapshot(station="KLGA", target_date=TARGET, received_at=RECEIVED)
    assert raised.value.code == "WRH_LIVE_API_KEY_SCRIPT_DISCOVERY_FAILED"


def test_viewer_script_hash_drift_fails_before_api_token_or_backend_use(monkeypatch):
    transport, seen = _transport(viewer_body=b"drifted viewer")
    client = _client(monkeypatch, transport)
    with pytest.raises(WRHSourceError) as raised:
        client.fetch_snapshot(station="KLGA", target_date=TARGET, received_at=RECEIVED)
    assert raised.value.code == "WRH_LIVE_VIEWER_SCRIPT_SHA_MISMATCH"
    assert WRH_API_KEY_SCRIPT_PATH not in seen["paths"]


def test_pinned_viewer_must_explicitly_reference_mesotoken_query_contract(monkeypatch):
    viewer = b"var InfoToGet='x&obtimezone=local';"
    viewer_sha = hashlib.sha256(viewer).hexdigest()
    transport, seen = _transport(viewer_body=viewer)
    monkeypatch.setattr(wrh_client_module, "WRH_VIEWER_SCRIPT_SHA256", viewer_sha)
    client = NWSWRHLiveClient(http_client=httpx.Client(transport=transport, follow_redirects=True))
    with pytest.raises(WRHSourceError) as raised:
        client.fetch_snapshot(station="KLGA", target_date=TARGET, received_at=RECEIVED)
    assert raised.value.code in {
        "WRH_LIVE_VIEWER_TOKEN_IDENTIFIER_MISMATCH",
        "WRH_LIVE_VIEWER_TOKEN_QUERY_CONTRACT_MISMATCH",
    }
    assert WRH_API_KEY_SCRIPT_PATH not in seen["paths"]


@pytest.mark.parametrize("station", ["", "LGA", "TOOLONG", "K LGA"])
def test_station_identity_is_strict(station):
    client = NWSWRHLiveClient(
        http_client=httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(500)))
    )
    with pytest.raises(WRHSourceError) as raised:
        client.fetch_snapshot(station=station, target_date=TARGET, received_at=RECEIVED)
    assert raised.value.code == "WRH_LIVE_STATION_INVALID"
