from __future__ import annotations

import asyncio

import httpx

from polymarket_scanner.weather_only_pws import (
    PWS_STATUS_AUTH_ERROR,
    PWS_STATUS_NO_FRESH_QC,
    PWS_STATUS_PROVIDER_ERROR,
)
from polymarket_scanner.weather_only_synoptic_pws_guarded import (
    GuardedSynopticCWOPPWSClient,
)


LAT = 40.7769
LON = -73.8740


def _summary(*, response_code, message, http_status=None):
    row = {
        "RESPONSE_CODE": response_code,
        "RESPONSE_MESSAGE": message,
        "NUMBER_OF_OBJECTS": 0,
    }
    if http_status is not None:
        row["HTTP_STATUS_CODE"] = http_status
    return {"SUMMARY": row}


def _snapshot_for(body: dict):
    async def handler(request):
        return httpx.Response(200, request=request, json=body)

    async def scenario():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            client = GuardedSynopticCWOPPWSClient(token="sentinel", http=http)
            return await client.fetch_snapshot(latitude=LAT, longitude=LON, unit="F")

    return asyncio.run(scenario())


def test_documented_code2_invalid_token_with_http_401_is_auth_failure():
    result = _snapshot_for(
        _summary(
            response_code=2,
            message="Invalid token. Be sure to use a token generated from your API Key.",
            http_status=401,
        )
    )
    assert result.status == PWS_STATUS_AUTH_ERROR


def test_code2_invalid_token_message_without_http_status_is_auth_failure():
    result = _snapshot_for(
        _summary(response_code=2, message="Invalid token supplied")
    )
    assert result.status == PWS_STATUS_AUTH_ERROR


def test_code2_http_429_is_provider_failure_not_healthy_zero_results():
    result = _snapshot_for(
        _summary(
            response_code=2,
            message="Too many requests sent. Please wait and resend.",
            http_status=429,
        )
    )
    assert result.status == PWS_STATUS_PROVIDER_ERROR


def test_true_code2_zero_results_remains_safe_empty_result():
    result = _snapshot_for(
        _summary(response_code=2, message="No stations found for this request")
    )
    assert result.status == PWS_STATUS_NO_FRESH_QC


def test_explicit_403_summary_is_auth_failure():
    result = _snapshot_for(
        _summary(response_code=403, message="Authorization error")
    )
    assert result.status == PWS_STATUS_AUTH_ERROR


def test_malformed_http_status_on_error_fails_closed():
    result = _snapshot_for(
        _summary(
            response_code=2,
            message="No stations found",
            http_status="not-a-status",
        )
    )
    assert result.status == PWS_STATUS_PROVIDER_ERROR
