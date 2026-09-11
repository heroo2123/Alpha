from __future__ import annotations

import asyncio
import time
from dataclasses import replace

from polymarket_scanner.models import Book
from polymarket_scanner.weather_only_clob import WeatherExecutionSnapshot, WeatherMarketParameters
from polymarket_scanner.weather_only_discovery import WeatherDiscoveryError, WeatherDiscoverySnapshot
from polymarket_scanner.weather_only_runtime import WeatherOnlyShadowRuntime


def _market(mid: str, question: str) -> dict:
    return {
        "id": mid,
        "active": True,
        "closed": False,
        "acceptingOrders": True,
        "enableOrderBook": True,
        "question": question,
        "slug": f"market-{mid}",
        "conditionId": f"condition-{mid}",
        "outcomes": '["Yes","No"]',
        "clobTokenIds": f'["{mid}-yes","{mid}-no"]',
        "outcomePrices": '["0.5","0.5"]',
    }


def _nws_event() -> dict:
    rules = (
        "This market will resolve to the range containing the highest reading in the \"Temp\" column across all times on this day, "
        "11 Sep '26, in whole degrees Fahrenheit. "
        "The resolution source is https://www.weather.gov/wrh/timeseries?site=KLGA. "
        "If NOAA data is unavailable by 11:59 PM ET on the day following the observation date, the Weather Underground Daily Observations table will be used. "
        "If there is no data by that deadline, the market resolves to the lowest bracket. "
        "This market will resolve once the first data point for the following date is published, or by 11:59 PM ET on the day following the observation date, whichever comes first. "
        "Revisions will be considered until the first datapoint for the following date has been published, after which any alterations will not be considered."
    )
    markets = [
        _market("low", "Will the highest temperature be 69°F or lower?"),
        _market("mid", "Will the highest temperature be 70-71°F?"),
        _market("high", "Will the highest temperature be 72°F or higher?"),
    ]
    for row in markets:
        row["description"] = rules
        row["resolutionSource"] = "https://www.weather.gov/wrh/timeseries?site=KLGA"
    return {
        "id": "event-1",
        "slug": "highest-temperature-in-nyc-on-september-11",
        "title": "Highest temperature in NYC on September 11?",
        "description": rules,
        "resolutionSource": "https://www.weather.gov/wrh/timeseries?site=KLGA",
        "markets": markets,
    }


def _snapshot(event: dict) -> WeatherDiscoverySnapshot:
    now = time.time()
    return WeatherDiscoverySnapshot(
        version="test",
        tags=("daily-temperature", "weather"),
        events=(event,),
        pages_by_tag={"daily-temperature": 1, "weather": 1},
        raw_event_hits=2,
        duplicate_event_hits=1,
        unique_event_count=1,
        unique_market_count=3,
        started_at=now - 0.1,
        finished_at=now,
    )


def _book(token: str, ask: float, size: float = 10.0) -> Book:
    return Book(
        token_id=token,
        bids=[(max(0.001, ask - 0.02), size)],
        asks=[(ask, size)],
        received_at=time.time(),
        source="test_exact_clob",
    )


def _parameters(event: dict, fee_rate: float) -> dict[str, WeatherMarketParameters]:
    now = time.time()
    out = {}
    for row in event["markets"]:
        condition = row["conditionId"]
        mid = row["id"]
        out[condition] = WeatherMarketParameters(
            condition_id=condition,
            token_outcomes=((f"{mid}-yes", "Yes"), (f"{mid}-no", "No")),
            minimum_order_size=1.0,
            minimum_tick_size=0.01,
            fee_rate=fee_rate,
            fee_exponent=None,
            taker_only=None,
            maker_base_fee_bps=0,
            taker_base_fee_bps=0,
            rfq_enabled=None,
            taker_delay_enabled=None,
            received_at=now,
        )
    return out


def _execution_snapshot(event: dict, yes_ask: float, fee_rate: float = 0.0) -> WeatherExecutionSnapshot:
    now = time.time()
    books = {}
    for row in event["markets"]:
        mid = row["id"]
        books[f"{mid}-yes"] = _book(f"{mid}-yes", yes_ask)
        books[f"{mid}-no"] = _book(f"{mid}-no", 0.8)
    return WeatherExecutionSnapshot(
        version="test",
        event_id=event["id"],
        books=books,
        parameters=_parameters(event, fee_rate),
        started_at=now - 0.01,
        finished_at=now,
        exact_clob=True,
        financial_authority=False,
    )


class FakeDiscovery:
    def __init__(self, snapshot=None, error: str | None = None):
        self.snapshot = snapshot
        self.error = error
        self.calls = 0

    async def discover(self, tags):
        self.calls += 1
        if self.error:
            raise WeatherDiscoveryError(self.error)
        return self.snapshot


class FakeCLOB:
    def __init__(self, event: dict, *, prescreen_ask: float = 0.20, exact_sequence=None):
        self.event = event
        self.prescreen_ask = prescreen_ask
        self.exact_sequence = list(exact_sequence or [])
        self.book_calls = 0
        self.exact_calls = 0

    async def books(self, token_ids):
        self.book_calls += 1
        return {token: _book(token, self.prescreen_ask) for token in token_ids}

    async def exact_event_snapshot(self, compiled):
        self.exact_calls += 1
        assert compiled.event_id == self.event["id"]
        if not self.exact_sequence:
            raise AssertionError("unexpected exact_event_snapshot call")
        return self.exact_sequence.pop(0)


async def _cycle(discovery, clob):
    runtime = WeatherOnlyShadowRuntime(discovery=discovery, clob=clob)
    try:
        return await runtime.run_cycle()
    finally:
        await runtime.close()


def test_runtime_records_only_after_second_exact_recheck_and_never_grants_financial_authority():
    event = _nws_event()
    clob = FakeCLOB(
        event,
        exact_sequence=[
            _execution_snapshot(event, 0.20, 0.0),
            _execution_snapshot(event, 0.21, 0.0),
        ],
    )
    report = asyncio.run(_cycle(FakeDiscovery(_snapshot(event)), clob))

    assert report["cycle_ok"] is True
    assert report["read_only"] is True
    assert report["financial_authority"] is False
    assert report["financial_delivery"] is False
    assert report["automatic_order_placement"] is False
    assert report["gamma_execution_authority"] is False
    assert report["opportunity_count"] == 1
    assert report["opportunities"][0]["rechecked"] is True
    assert report["opportunities"][0]["financial_authority"] is False
    assert clob.exact_calls == 2


def test_runtime_discards_candidate_when_second_exact_recheck_is_no_longer_profitable():
    event = _nws_event()
    clob = FakeCLOB(
        event,
        exact_sequence=[
            _execution_snapshot(event, 0.20, 0.0),
            _execution_snapshot(event, 0.40, 0.0),
        ],
    )
    report = asyncio.run(_cycle(FakeDiscovery(_snapshot(event)), clob))

    assert report["cycle_ok"] is True
    assert report["opportunity_count"] == 0
    assert report["clob_failure_counts"]["RECHECK_NO_LONGER_PROFITABLE"] == 1
    assert clob.exact_calls == 2


def test_runtime_refuses_nonzero_fee_model_until_weather_fee_semantics_are_certified():
    event = _nws_event()
    clob = FakeCLOB(
        event,
        exact_sequence=[_execution_snapshot(event, 0.20, 0.05)],
    )
    report = asyncio.run(_cycle(FakeDiscovery(_snapshot(event)), clob))

    assert report["cycle_ok"] is True
    assert report["opportunity_count"] == 0
    assert report["clob_failure_counts"]["NONZERO_FEE_MODEL_NOT_CERTIFIED"] == 1
    assert clob.exact_calls == 1


def test_runtime_fails_closed_on_discovery_error_without_touching_clob():
    event = _nws_event()
    clob = FakeCLOB(event)
    report = asyncio.run(_cycle(FakeDiscovery(error="CURSOR_REPEAT"), clob))

    assert report["cycle_ok"] is False
    assert report["errors"] == ["DISCOVERY:CURSOR_REPEAT"]
    assert report["opportunities"] == []
    assert clob.book_calls == 0
    assert clob.exact_calls == 0


def test_runtime_keeps_unproven_or_incomplete_partitions_out_of_clob_prescreen():
    event = _nws_event()
    event["markets"] = event["markets"][:2]
    clob = FakeCLOB(event)
    report = asyncio.run(_cycle(FakeDiscovery(_snapshot(event)), clob))

    assert report["cycle_ok"] is True
    assert report["compiler"]["open_complete_partition_events"] == 0
    assert report["prescreen"]["candidate_event_count"] == 0
    assert report["opportunities"] == []
    assert clob.book_calls == 0
    assert clob.exact_calls == 0
