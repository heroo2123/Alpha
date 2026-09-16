from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from polymarket_scanner import weather_catalog as wc
from polymarket_scanner.polymarket import UniverseIncompleteError


def market(mid: str, question: str, *, description: str = "", resolution: str = "") -> dict:
    return {
        "id": mid,
        "question": question,
        "slug": f"m-{mid}",
        "active": True,
        "closed": False,
        "acceptingOrders": True,
        "enableOrderBook": True,
        "outcomes": '["Yes","No"]',
        "clobTokenIds": f'["{mid}-yes","{mid}-no"]',
        "description": description,
        "resolutionSource": resolution,
    }


def event(eid: str, title: str, markets: list[dict], *, description: str = "", resolution: str = "") -> dict:
    return {
        "id": eid,
        "slug": f"e-{eid}",
        "title": title,
        "active": True,
        "closed": False,
        "description": description,
        "resolutionSource": resolution,
        "markets": markets,
    }


def test_daily_temperature_with_rules_fallback_is_catalogued_as_supported_candidate():
    rules = (
        "NOAA highest reading under the Temp column. "
        "https://www.weather.gov/wrh/timeseries?site=zbaa "
        "If NOAA data is unavailable, Weather Underground Daily Observations will be used."
    )
    e = event("1", "Highest temperature in Beijing on September 11?", [market("11", "32°C", description=rules)])
    row = wc.catalog_market(e, e["markets"][0])

    assert row.family == wc.FAMILY_DAILY_HIGH_TEMP
    assert row.adapter_candidate == "DAILY_TEMP_NWS_WRH_WITH_FALLBACK_RULE_TREE_V1_CANDIDATE"
    assert row.unsupported_reason is None
    assert "www.weather.gov" in row.source_hosts
    assert row.tradable is True
    assert row.exhaustive_event_candidate is False


def test_daily_rain_cli_contract_is_recognized():
    rules = (
        "This resolves Yes for measurable precipitation of 0.01 inches or more according to the "
        "NWS Daily Climate Report (CLI). Trace (T) does not qualify. "
        "https://forecast.weather.gov/product.php?site=NWS&product=CLI&issuedby=NYC"
    )
    e = event("2", "Where will it rain on September 11?", [market("21", "New York, NY", description=rules)])
    row = wc.catalog_market(e, e["markets"][0])

    assert row.family == wc.FAMILY_DAILY_RAIN
    assert row.adapter_candidate == "DAILY_RAIN_NWS_CLI_V1_CANDIDATE"
    assert row.unsupported_reason is None
    assert "forecast.weather.gov" in row.source_hosts


def test_monthly_kma_precipitation_is_source_specific_candidate():
    rules = (
        "Total precipitation in Seoul in September according to Korea Meteorological Administration. "
        "https://data.kma.go.kr/climate/RankState/selectRankStatisticsDivisionList.do"
    )
    children = [
        market("31", "<75mm", description=rules),
        market("32", "75-100mm", description=rules),
        market("33", "100-125mm", description=rules),
    ]
    e = event("3", "Precipitation in Seoul in September?", children)
    row = wc.catalog_market(e, children[0])

    assert row.family == wc.FAMILY_MONTHLY_PRECIP
    assert row.adapter_candidate == "MONTHLY_PRECIP_KMA_V1_CANDIDATE"
    assert row.unsupported_reason is None
    assert row.exhaustive_event_candidate is True


class FakeClient:
    def __init__(self, pages):
        self.pages = pages
        self.calls = []

    async def _event_keyset_page(self, after_cursor: str | None, *, tag_slug: str | None = None):
        self.calls.append((after_cursor, tag_slug))
        return self.pages[after_cursor]


def test_weather_fetch_uses_only_bounded_weather_keyset_and_stops_on_terminal_cursor(monkeypatch):
    monkeypatch.setattr(wc, "settings", SimpleNamespace(gamma_page_size=2))
    first = [
        event("1", "Highest temperature in X on September 11?", [market("11", "20°C")]),
        event("2", "Lowest temperature in Y on September 11?", [market("21", "10°C")]),
    ]
    second = [event("3", "Where will it rain on September 11?", [market("31", "X")])]
    client = FakeClient({None: (first, "c1"), "c1": (second, None)})

    events, pages = asyncio.run(wc.fetch_weather_events(client, page_ceiling=5))

    assert pages == 2
    assert [e["id"] for e in events] == ["1", "2", "3"]
    assert client.calls == [(None, "weather"), ("c1", "weather")]


def test_weather_fetch_fails_closed_if_keyset_never_exhausts(monkeypatch):
    monkeypatch.setattr(wc, "settings", SimpleNamespace(gamma_page_size=2))
    full_a = [event("1", "A", [market("11", "A")]), event("2", "B", [market("21", "B")])]
    full_b = [event("3", "C", [market("31", "C")]), event("4", "D", [market("41", "D")])]
    client = FakeClient({None: (full_a, "c1"), "c1": (full_b, "c2")})

    with pytest.raises(UniverseIncompleteError, match="did not exhaust"):
        asyncio.run(wc.fetch_weather_events(client, page_ceiling=2))


def test_weather_fetch_fails_closed_on_repeated_continuation_cursor():
    client = FakeClient({
        None: ([event("1", "A", [market("11", "A")])], "c1"),
        "c1": ([event("2", "B", [market("21", "B")])], "c1"),
    })

    with pytest.raises(UniverseIncompleteError, match="repeated a continuation cursor"):
        asyncio.run(wc.fetch_weather_events(client, page_ceiling=4))


def test_weather_fetch_fails_closed_on_duplicate_event_across_keyset_pages():
    first = event("1", "A", [market("11", "A")])
    changed = event("1", "A", [market("11", "A"), market("12", "B")])
    client = FakeClient({None: ([first], "c1"), "c1": ([changed], None)})

    with pytest.raises(UniverseIncompleteError, match="duplicate event ID"):
        asyncio.run(wc.fetch_weather_events(client, page_ceiling=4))


def test_census_separates_supported_and_unsupported_weather_families():
    temp_rules = "https://www.weather.gov/wrh/timeseries?site=kmia"
    rain_rules = (
        "measurable precipitation NWS Daily Climate Report "
        "https://forecast.weather.gov/product.php?site=NWS&product=CLI&issuedby=MIA"
    )
    events = [
        event("1", "Highest temperature in Miami on September 11?", [market("11", "90-91°F", description=temp_rules)]),
        event("2", "Where will it rain on September 11?", [market("21", "Miami, FL", description=rain_rules)]),
        event("3", "Will a hurricane form by September 30?", [market("31", "Yes")]),
    ]

    report = wc.build_census(events, pages=1)

    assert report["complete"] is True
    assert report["event_count"] == 3
    assert report["market_count"] == 3
    assert report["tradable_market_count"] == 3
    assert report["token_count"] == 6
    assert report["family_counts"][wc.FAMILY_DAILY_HIGH_TEMP] == 1
    assert report["family_counts"][wc.FAMILY_DAILY_RAIN] == 1
    assert report["family_counts"][wc.FAMILY_HURRICANE] == 1
    assert report["unsupported_market_count"] == 1
