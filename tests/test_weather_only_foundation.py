import asyncio
from dataclasses import replace

import httpx
import pytest

from polymarket_scanner.models import Book
from polymarket_scanner.weather_only_clob import WeatherMarketParameters
from polymarket_scanner.weather_only_contracts import (
    DAILY_HIGH,
    DAILY_LOW,
    OTHER_WEATHER,
    SOURCE_HKO,
    SOURCE_NWS_WRH,
    compile_weather_event,
)
from polymarket_scanner import weather_only_discovery as discovery_module
from polymarket_scanner.weather_only_discovery import WeatherDiscoveryError, WeatherOnlyDiscovery
from polymarket_scanner.weather_only_inventory import inventory_report
from polymarket_scanner.weather_only_structural import (
    binary_pair_underround,
    complete_bucket_underround,
)


def _market(mid, question, *, yes=None, no=None, condition=None):
    yes = yes or f"{mid}-yes"
    no = no or f"{mid}-no"
    return {
        "id": mid,
        "active": True,
        "closed": False,
        "acceptingOrders": True,
        "enableOrderBook": True,
        "question": question,
        "slug": f"market-{mid}",
        "conditionId": condition or f"condition-{mid}",
        "outcomes": '["Yes","No"]',
        "clobTokenIds": f'["{yes}","{no}"]',
        "outcomePrices": '["0.5","0.5"]',
    }


def _nyc_event(markets=None):
    return {
        "id": "nyc-2026-09-11-high",
        "slug": "highest-temperature-in-nyc-on-september-11",
        "title": "Highest temperature in NYC on September 11?",
        "description": "The observation date is 11 Sep '26 and temperatures are measured in degrees Fahrenheit.",
        "resolutionSource": "https://www.weather.gov/wrh/timeseries?site=KLGA",
        "markets": markets or [
            _market("m-low", "Will the highest temperature be 69°F or lower?"),
            _market("m-mid", "Will the highest temperature be 70-71°F?"),
            _market("m-high", "Will the highest temperature be 72°F or higher?"),
        ],
    }


def test_compiler_inventory_parses_current_like_nyc_high_without_granting_authority():
    compiled = compile_weather_event(_nyc_event())
    assert compiled.family == DAILY_HIGH
    assert compiled.target_date.isoformat() == "2026-09-11"
    assert compiled.unit == "F"
    assert compiled.source_family == SOURCE_NWS_WRH
    assert compiled.station_hint == "KLGA"
    assert compiled.partition_shape_complete is True
    assert compiled.shadow_supported is True
    assert compiled.exactly_one_outcome_proven is False
    assert compiled.financial_authority is False
    assert len(compiled.buckets) == 3
    assert all(bucket.trade_open for bucket in compiled.buckets)


def test_compiler_parses_hko_low_family_and_two_digit_year():
    event = {
        "id": "hko-low",
        "slug": "lowest-temperature-hong-kong-september-11",
        "title": "Lowest temperature in Hong Kong on September 11?",
        "description": "Observation date 11 Sep '26. Absolute Daily Min is reported in degrees Celsius.",
        "resolutionSource": "https://www.weather.gov.hk/en/cis/dailyExtract.htm?y=2026&m=9",
        "markets": [
            _market("h-low", "Will the lowest temperature be 24°C or lower?"),
            _market("h-mid", "Will the lowest temperature be 25-26°C?"),
            _market("h-high", "Will the lowest temperature be 27°C or higher?"),
        ],
    }
    compiled = compile_weather_event(event)
    assert compiled.family == DAILY_LOW
    assert compiled.target_date.isoformat() == "2026-09-11"
    assert compiled.unit == "C"
    assert compiled.source_family == SOURCE_HKO
    assert compiled.shadow_supported is True
    assert compiled.financial_authority is False


def test_weather_tag_item_that_is_not_supported_meteorology_stays_unsupported():
    event = {
        "id": "pandemic",
        "title": "Will a pandemic be declared this year?",
        "description": "Weather-tagged broad category item.",
        "resolutionSource": "https://example.com",
        "markets": [_market("p1", "Will it happen?")],
    }
    compiled = compile_weather_event(event)
    assert compiled.family == OTHER_WEATHER
    assert compiled.shadow_supported is False
    assert compiled.financial_authority is False
    assert "UNSUPPORTED_FAMILY_PHASE1" in compiled.rejection_reasons


def test_conflicting_units_fail_closed_for_shadow_support():
    event = _nyc_event()
    event["description"] += " A separate clause says degrees Celsius."
    compiled = compile_weather_event(event)
    assert compiled.unit is None
    assert compiled.shadow_supported is False
    assert "UNIT_UNRESOLVED_OR_CONFLICT" in compiled.rejection_reasons


def test_discovery_queries_only_configured_tag_keyset_and_deduplicates_children():
    requests = []
    first = _nyc_event([_market("m1", "Will the highest temperature be 70°F or lower?")])
    second = _nyc_event([_market("m2", "Will the highest temperature be 71°F or higher?")])

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        tag = request.url.params.get("tag_slug")
        assert tag in {"daily-temperature", "weather"}
        payload = first if tag == "daily-temperature" else second
        return httpx.Response(200, json={"events": [payload], "next_cursor": None})

    async def run():
        client = WeatherOnlyDiscovery()
        await client.http.aclose()
        client.http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        try:
            return await client.discover(("daily-temperature", "weather"))
        finally:
            await client.close()

    snapshot = asyncio.run(run())
    assert snapshot.unique_event_count == 1
    assert snapshot.unique_market_count == 2
    assert snapshot.raw_event_hits == 2
    assert snapshot.duplicate_event_hits == 1
    assert {row["id"] for row in snapshot.events[0]["markets"]} == {"m1", "m2"}
    assert len(requests) == 2
    for request in requests:
        assert request.url.path.endswith("/events/keyset")
        assert request.url.params.get("limit") == "25"
        assert request.url.params.get("tag_slug")
        assert "offset" not in request.url.params


def test_discovery_repeated_cursor_fails_closed():
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"events": [_nyc_event()], "next_cursor": "repeat"})

    async def run():
        client = WeatherOnlyDiscovery()
        await client.http.aclose()
        client.http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        try:
            return await client.discover(("weather",))
        finally:
            await client.close()

    with pytest.raises(WeatherDiscoveryError) as raised:
        asyncio.run(run())
    assert raised.value.code == "CURSOR_REPEAT"


def test_discovery_page_bytes_cap_is_fixed_failure(monkeypatch):
    monkeypatch.setattr(discovery_module, "MAX_PAGE_BYTES", 32)

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"events": [_nyc_event()], "next_cursor": None})

    async def run():
        client = WeatherOnlyDiscovery()
        await client.http.aclose()
        client.http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        try:
            return await client.discover(("weather",))
        finally:
            await client.close()

    with pytest.raises(WeatherDiscoveryError) as raised:
        asyncio.run(run())
    assert raised.value.code == "PAGE_BYTES_CAP"


def _book(token, ask, size=10.0):
    return Book(token_id=token, bids=[], asks=[(ask, size)], received_at=1.0, source="exact_clob_test")


def _fee_parameters(compiled, *, rate=0.05, exponent=1):
    out = {}
    for bucket in compiled.buckets:
        out[bucket.condition_id] = WeatherMarketParameters(
            condition_id=bucket.condition_id,
            token_outcomes=((bucket.yes_token, "Yes"), (bucket.no_token, "No")),
            minimum_order_size=1.0,
            minimum_tick_size=0.01,
            fee_rate=rate,
            fee_exponent=exponent,
            taker_only=True if rate > 0 else None,
            maker_base_fee_bps=0,
            taker_base_fee_bps=0,
            rfq_enabled=False,
            taker_delay_enabled=False,
            received_at=1.0,
        )
    return out


def test_binary_yes_no_underround_uses_exact_books_and_explicit_fee_schedule():
    compiled = compile_weather_event(_nyc_event())
    bucket = compiled.buckets[0]
    books = {
        bucket.yes_token: _book(bucket.yes_token, 0.45, 8.0),
        bucket.no_token: _book(bucket.no_token, 0.45, 5.0),
    }
    params = _fee_parameters(compiled)
    out = binary_pair_underround(compiled, books, params)
    assert len(out) == 1
    candidate = out[0]
    assert candidate.lane == "weather_binary_pair_underround"
    assert candidate.evidence_class == "DETERMINISTIC"
    assert candidate.fee_rates == (0.05, 0.05)
    assert candidate.fee_exponents == (1, 1)
    assert candidate.locked_profit_per_set > 0
    assert candidate.common_best_ask_shares == 5.0
    assert candidate.financial_authority is False


def test_binary_underround_refuses_missing_market_specific_parameters():
    compiled = compile_weather_event(_nyc_event())
    bucket = compiled.buckets[0]
    books = {
        bucket.yes_token: _book(bucket.yes_token, 0.40),
        bucket.no_token: _book(bucket.no_token, 0.40),
    }
    assert binary_pair_underround(compiled, books, {}) == []


def test_complete_bucket_underround_requires_semantic_exactly_one_proof():
    compiled = compile_weather_event(_nyc_event())
    books = {bucket.yes_token: _book(bucket.yes_token, 0.20, 7.0) for bucket in compiled.buckets}
    params = _fee_parameters(compiled)
    assert complete_bucket_underround(compiled, books, params) is None

    certified = replace(compiled, exactly_one_outcome_proven=True)
    candidate = complete_bucket_underround(certified, books, params)
    assert candidate is not None
    assert candidate.lane == "weather_complete_bucket_underround"
    assert candidate.contract_partition_proven is True
    assert candidate.locked_profit_per_set > 0
    assert candidate.common_best_ask_shares == 7.0
    assert candidate.financial_authority is False


def test_incomplete_bucket_partition_never_gets_complete_set_candidate():
    compiled = compile_weather_event(_nyc_event([
        _market("a", "Will the highest temperature be 69°F or lower?"),
        _market("b", "Will the highest temperature be 72°F or higher?"),
    ]))
    assert compiled.partition_shape_complete is False
    forced_semantics = replace(compiled, exactly_one_outcome_proven=True)
    books = {bucket.yes_token: _book(bucket.yes_token, 0.20) for bucket in forced_semantics.buckets}
    params = _fee_parameters(forced_semantics)
    assert complete_bucket_underround(forced_semantics, books, params) is None


def test_inventory_report_never_claims_financial_authority():
    from polymarket_scanner.weather_only_discovery import WeatherDiscoverySnapshot

    snapshot = WeatherDiscoverySnapshot(
        version="test",
        tags=("weather",),
        events=(_nyc_event(),),
        pages_by_tag={"weather": 1},
        raw_event_hits=1,
        duplicate_event_hits=0,
        unique_event_count=1,
        unique_market_count=3,
        started_at=1.0,
        finished_at=2.0,
    )
    report = inventory_report(snapshot, include_events=True)
    assert report["compiled"]["financial_authority_events"] == 0
    assert report["financial_delivery"] is False
    assert report["automatic_order_placement"] is False
    assert report["events"][0]["financial_authority"] is False
