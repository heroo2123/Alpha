import asyncio

import pytest

from polymarket_scanner.models import Market
from polymarket_scanner.production_gamma_bbo import (
    PRODUCTION_REFRESH_TRANSPORT_VERSION,
    PRODUCTION_UNIVERSE_MAX_STALE_SECONDS,
    PRODUCTION_UNIVERSE_REFRESH_SECONDS,
    PRODUCTION_WATCHDOG_STARTUP_GRACE_SECONDS,
    ScannerOwnedProductionPolymarketClient,
)


def _market() -> Market:
    return Market(
        id="m1",
        event_id="e1",
        event_slug="event",
        event_title="Event",
        event_neg_risk=False,
        question="Will X happen?",
        slug="m1",
        condition_id="condition",
        outcomes=["Yes", "No"],
        token_ids=["yes-token", "no-token"],
        outcome_prices=[0.42, 0.58],
        best_bid=0.40,
        best_ask=0.42,
        liquidity=100.0,
        volume_24h=1000.0,
        active=True,
        closed=False,
        end_date=None,
        description="",
        resolution_source="",
        category="",
        tags=[],
        raw={},
    )


@pytest.mark.asyncio
async def test_scanner_owned_client_performs_real_fetch_on_every_outer_refresh(monkeypatch):
    client = ScannerOwnedProductionPolymarketClient()
    calls = 0

    async def fake_fetch():
        nonlocal calls
        calls += 1
        client._fetch_complete = True
        client._fetch_reason = f"fake complete {calls}"
        client._discovered_market_count = 100 + calls
        client._materialized_market_count = 1
        client._keyset_page_count = 2
        return [_market()]

    monkeypatch.setattr(client, "_fetch_active_markets", fake_fetch)

    try:
        first = await client.active_markets()
        second = await client.active_markets()

        assert len(first) == 1
        assert len(second) == 1
        assert calls == 2
        assert client._active_refresh_task is None

        status = client.universe_status()
        assert status["refresh_owner"] == "scanner_loop_only"
        assert status["refresh_transport"] == PRODUCTION_REFRESH_TRANSPORT_VERSION
        assert status["last_full_fetch_seconds"] is not None
        assert status["last_full_fetch_seconds"] >= 0.0
        assert status["discovered_market_count"] == 102
        assert status["materialized_market_count"] == 1
        assert status["keyset_pages"] == 2
        assert status["refresh_progress"] is None
        assert status["safe_for_detection"] is True
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_scanner_owned_client_does_not_hide_failed_full_refresh(monkeypatch):
    client = ScannerOwnedProductionPolymarketClient()

    async def successful_fetch():
        client._fetch_complete = True
        client._fetch_reason = "fake complete"
        client._discovered_market_count = 100
        client._materialized_market_count = 1
        client._keyset_page_count = 2
        return [_market()]

    monkeypatch.setattr(client, "_fetch_active_markets", successful_fetch)

    try:
        await client.active_markets()
        accepted_at = client._active_cache_at
        accepted_status = client.universe_status()

        async def failed_fetch():
            client._discovered_market_count = 17
            client._materialized_market_count = 0
            client._keyset_page_count = 3
            raise RuntimeError("Gamma test failure")

        monkeypatch.setattr(client, "_fetch_active_markets", failed_fetch)

        with pytest.raises(RuntimeError, match="Gamma test failure"):
            await client.active_markets()

        status = client.universe_status()
        assert client._active_refresh_task is None
        assert client._active_cache_at == accepted_at
        assert "Gamma test failure" in str(client._active_last_error)
        assert status["last_attempt_full_fetch_seconds"] is not None
        assert status["discovered_market_count"] == accepted_status["discovered_market_count"]
        assert status["materialized_market_count"] == accepted_status["materialized_market_count"]
        assert status["keyset_pages"] == accepted_status["keyset_pages"]
        assert status["safe_for_detection"] is True
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_partial_refresh_progress_never_replaces_accepted_authority(monkeypatch):
    client = ScannerOwnedProductionPolymarketClient()

    async def initial_fetch():
        client._fetch_complete = True
        client._fetch_reason = "accepted complete"
        client._discovered_market_count = 191_000
        client._materialized_market_count = 13_500
        client._keyset_page_count = 230
        return [_market()]

    monkeypatch.setattr(client, "_fetch_active_markets", initial_fetch)

    try:
        await client.active_markets()
        accepted = client.universe_status()

        entered = asyncio.Event()
        release = asyncio.Event()

        async def partial_fetch():
            client._discovered_market_count = 13_087
            client._materialized_market_count = 0
            client._keyset_page_count = 30
            entered.set()
            await release.wait()
            raise RuntimeError("stop test refresh")

        monkeypatch.setattr(client, "_fetch_active_markets", partial_fetch)
        task = asyncio.create_task(client.active_markets())
        await entered.wait()

        during = client.universe_status()
        assert during["refresh_in_progress"] is True
        assert during["discovered_market_count"] == 191_000
        assert during["materialized_market_count"] == 13_500
        assert during["keyset_pages"] == 230
        assert during["safe_for_detection"] is True

        progress = during["refresh_progress"]
        assert progress["discovered_market_count"] == 13_087
        assert progress["materialized_market_count"] == 0
        assert progress["keyset_pages"] == 30

        release.set()
        with pytest.raises(RuntimeError, match="stop test refresh"):
            await task

        after = client.universe_status()
        assert after["refresh_progress"] is None
        assert after["discovered_market_count"] == accepted["discovered_market_count"]
        assert after["materialized_market_count"] == accepted["materialized_market_count"]
        assert after["keyset_pages"] == accepted["keyset_pages"]
    finally:
        await client.close()


def test_large_universe_runtime_floors_are_conservative():
    assert PRODUCTION_UNIVERSE_REFRESH_SECONDS >= 600
    assert PRODUCTION_UNIVERSE_MAX_STALE_SECONDS >= 1800
    assert PRODUCTION_WATCHDOG_STARTUP_GRACE_SECONDS >= 900.0
