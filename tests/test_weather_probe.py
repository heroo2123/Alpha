from __future__ import annotations

import asyncio

from polymarket_scanner.models import Book
from polymarket_scanner.polymarket import PolymarketClient
from polymarket_scanner.weather_probe import run_probe


def rules() -> str:
    return (
        "The target date is September 11, 2026. "
        "If NOAA is unavailable by 11:59 PM ET on September 12, 2026, Weather Underground Daily Observations will be used. "
        "The result becomes final when the first datapoint for the following day is published. "
        "Revisions count until before the first following-day datapoint. "
        "If no data is available by the deadline, the lowest bracket wins."
    )


def child(mid: str, question: str) -> dict:
    return {
        "id": mid,
        "question": question,
        "slug": f"m-{mid}",
        "conditionId": f"c-{mid}",
        "active": True,
        "closed": False,
        "acceptingOrders": True,
        "enableOrderBook": True,
        "outcomes": '["Yes","No"]',
        "clobTokenIds": f'["{mid}-yes","{mid}-no"]',
        "outcomePrices": '["0.5","0.5"]',
        "description": rules(),
        "resolutionSource": "https://www.weather.gov/wrh/timeseries?site=ZBAA",
        "endDate": "2026-09-12T23:59:00Z",
    }


def test_probe_uses_weather_tag_keyset_materializes_contracts_and_screens_proven_set(monkeypatch):
    event = {
        "id": "e1",
        "slug": "temp-event",
        "title": "Highest temperature in Beijing on September 11?",
        "active": True,
        "closed": False,
        "category": "Weather",
        "description": rules(),
        "resolutionSource": "https://www.weather.gov/wrh/timeseries?site=ZBAA",
        "markets": [
            child("1", "25°C or below"),
            child("2", "26°C"),
            child("3", "27°C or higher"),
        ],
    }

    client = PolymarketClient()
    calls = []

    async def page(after_cursor: str | None, *, tag_slug: str | None = None):
        calls.append((after_cursor, tag_slug))
        return [event], None

    async def books(tokens):
        asks = {"1-yes": 0.10, "2-yes": 0.20, "3-yes": 0.20}
        return {token: Book(token, [], [(asks[token], 5.0)]) for token in tokens}

    monkeypatch.setattr(client, "_event_keyset_page", page)
    monkeypatch.setattr(client, "books", books)

    try:
        report = asyncio.run(run_probe(client=client, page_ceiling=3, fetch_books=True))
    finally:
        asyncio.run(client.close())

    assert calls == [(None, "weather")]
    assert report["read_only"] is True
    assert report["financial_delivery"] is False
    assert report["automatic_order_placement"] is False
    assert report["catalog"]["event_count"] == 1
    assert report["catalog"]["market_count"] == 3
    assert report["materialized_market_count"] == 3
    assert report["supported_contract_counts"]["DAILY_TEMP_NWS_WRH_WITH_WU_FALLBACK_V1"] == 3
    assert report["temperature_partition_proof_count"] == 1
    assert report["temperature_partition_token_count"] == 3
    assert report["complete_set_screen_count"] == 1
    screen = report["complete_set_screens"][0]
    assert screen["raw_ask_cost_per_bundle"] == 0.5
    assert screen["estimated_locked_spread_per_bundle"] > 0.45
    assert screen["screening_only"] is True
    assert screen["requires_exact_fee_authority"] is True


def test_probe_can_run_catalog_only_without_requesting_clob_books(monkeypatch):
    event = {
        "id": "e2",
        "slug": "rain-event",
        "title": "Where will it rain on September 11?",
        "active": True,
        "closed": False,
        "category": "Weather",
        "markets": [{
            **child("9", "New York, NY on September 11?"),
            "description": (
                "This market covers September 11, 2026. Measurable precipitation is 0.01 inches or more in the NWS Daily Climate Report (CLI). "
                "A report of trace (T) does not qualify. The climate day for KNYC is midnight to midnight local standard time. "
                "The cutoff is 2:00 PM ET the following day. If multiple versions are issued, the last version published before the cutoff governs. "
                "If no figure has been published by then, this market will resolve to No."
            ),
            "resolutionSource": "https://forecast.weather.gov/product.php?site=NWS&product=CLI&issuedby=NYC",
        }],
    }
    client = PolymarketClient()

    async def page(after_cursor: str | None, *, tag_slug: str | None = None):
        return [event], None

    async def forbidden_books(tokens):
        raise AssertionError("books must not be called in --no-books equivalent mode")

    monkeypatch.setattr(client, "_event_keyset_page", page)
    monkeypatch.setattr(client, "books", forbidden_books)
    try:
        report = asyncio.run(run_probe(client=client, page_ceiling=3, fetch_books=False))
    finally:
        asyncio.run(client.close())

    assert report["catalog"]["event_count"] == 1
    assert report["complete_set_screen_count"] == 0
    assert report["temperature_partition_token_count"] == 0
