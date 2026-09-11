from __future__ import annotations

import asyncio

import httpx
import pytest

from polymarket_scanner.weather_only_maker_shadow import (
    PublicTradePrint,
    WeatherMakerShadowError,
    simulate_public_trade_progression,
)
from polymarket_scanner.weather_only_trade_feed import (
    WeatherDataAPITradeFeedClient,
    WeatherTradeFeedError,
    parse_data_api_v2_trade_page,
)
from test_weather_only_maker_shadow import NOW, _order


CONDITION = "0x" + "a" * 64
TOKEN = "123456789012345678901234567890"
TX = "0x" + "b" * 64
WALLET = "0x" + "c" * 40


def _row(**overrides):
    row = {
        "condition_id": CONDITION,
        "token_id": TOKEN,
        "transaction_hash": TX,
        "proxy_wallet": WALLET,
        "side": "SELL",
        "size": 3.5,
        "price": 0.24,
        "timestamp": 1_800_000_001,
        "outcome": "Yes",
        "outcome_index": 0,
    }
    row.update(overrides)
    return row


def _payload(rows=None, *, has_more=False, next_cursor=None):
    return {
        "data": [_row()] if rows is None else rows,
        "pagination": {
            "has_more": has_more,
            "limit": 200,
            "offset": 0,
            "next_cursor": next_cursor,
        },
    }


def test_v2_taker_row_maps_side_to_aggressor_and_keeps_execution_and_receipt_clocks_separate():
    page = parse_data_api_v2_trade_page(
        _payload(),
        condition_ids=(CONDITION,),
        requested_cursor=None,
        fetched_at=1_800_000_010.0,
    )
    assert page.exact_taker_side_semantics is True
    assert page.public_row_identity_is_surrogate is True
    assert page.actual_fill_authority is False
    assert page.financial_authority is False
    assert page.page_size == 1
    trade = page.trades[0]
    assert trade.aggressor_side == "SELL"
    assert trade.executed_at == pytest.approx(1_800_000_001.0)
    assert trade.received_at == pytest.approx(1_800_000_010.0)
    assert trade.effective_executed_at == pytest.approx(1_800_000_001.0)
    assert trade.source == "POLYMARKET_DATA_API_V2_TAKER_ONLY"
    assert trade.trade_id.startswith("data-v2:")
    assert len(trade.trade_id) == len("data-v2:") + 64


def test_identical_public_rows_fail_closed_because_v2_does_not_expose_first_class_fill_id():
    row = _row()
    with pytest.raises(WeatherTradeFeedError) as raised:
        parse_data_api_v2_trade_page(
            _payload([row, dict(row)]),
            condition_ids=(CONDITION,),
            requested_cursor=None,
            fetched_at=1_800_000_010.0,
        )
    assert raised.value.code == "TRADE_FEED_SURROGATE_ID_COLLISION"


def test_condition_identity_and_cursor_progression_fail_closed():
    with pytest.raises(WeatherTradeFeedError) as wrong_condition:
        parse_data_api_v2_trade_page(
            _payload([_row(condition_id="0x" + "d" * 64)]),
            condition_ids=(CONDITION,),
            requested_cursor=None,
            fetched_at=1_800_000_010.0,
        )
    assert wrong_condition.value.code == "TRADE_FEED_CONDITION_IDENTITY_MISMATCH"

    with pytest.raises(WeatherTradeFeedError) as missing_cursor:
        parse_data_api_v2_trade_page(
            _payload(has_more=True, next_cursor=None),
            condition_ids=(CONDITION,),
            requested_cursor=None,
            fetched_at=1_800_000_010.0,
        )
    assert missing_cursor.value.code == "TRADE_FEED_CURSOR_MISSING_WITH_MORE"

    with pytest.raises(WeatherTradeFeedError) as stalled:
        parse_data_api_v2_trade_page(
            _payload(has_more=True, next_cursor="same"),
            condition_ids=(CONDITION,),
            requested_cursor="same",
            fetched_at=1_800_000_010.0,
        )
    assert stalled.value.code == "TRADE_FEED_CURSOR_DID_NOT_ADVANCE"


def test_trade_schema_rejects_bad_side_timestamp_price_wallet_and_hash():
    cases = [
        ({"side": "UNKNOWN"}, "TRADE_FEED_SIDE_INVALID"),
        ({"timestamp": True}, "TRADE_FEED_TIMESTAMP_INVALID"),
        ({"price": 1.0}, "TRADE_FEED_PRICE_INVALID"),
        ({"proxy_wallet": "wallet"}, "TRADE_FEED_PROXY_WALLET_INVALID"),
        ({"transaction_hash": "tx"}, "TRADE_FEED_TRANSACTION_HASH_INVALID"),
    ]
    for override, code in cases:
        with pytest.raises(WeatherTradeFeedError) as raised:
            parse_data_api_v2_trade_page(
                _payload([_row(**override)]),
                condition_ids=(CONDITION,),
                requested_cursor=None,
                fetched_at=1_800_000_010.0,
            )
        assert raised.value.code == code


def test_client_requests_only_taker_rows_with_cursor_or_first_page_limit_and_never_returns_secret():
    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        cursor = request.url.params.get("cursor")
        if cursor is None:
            return httpx.Response(200, json=_payload(has_more=True, next_cursor="cursor-2"))
        return httpx.Response(200, json=_payload(has_more=False, next_cursor=None))

    async def scenario():
        http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        client = WeatherDataAPITradeFeedClient("super-secret-token", http=http)
        try:
            first = await client.page(condition_ids=(CONDITION,), limit=321)
            second = await client.page(condition_ids=(CONDITION,), cursor=first.next_cursor, limit=999)
            return first, second
        finally:
            await http.aclose()

    first, second = asyncio.run(scenario())
    assert first.next_cursor == "cursor-2"
    assert second.next_cursor is None
    assert len(requests) == 2
    assert requests[0].url.params["taker_only"] == "true"
    assert requests[0].url.params["filter_type"] == "TOKENS"
    assert requests[0].url.params["filter_amount"] == "0.01"
    assert requests[0].url.params["condition"] == CONDITION
    assert requests[0].url.params["limit"] == "321"
    assert "cursor" not in requests[0].url.params
    assert requests[1].url.params["cursor"] == "cursor-2"
    assert "limit" not in requests[1].url.params
    assert requests[0].headers["Authorization"] == "Bearer super-secret-token"
    assert "super-secret-token" not in str(first.as_dict())
    assert "super-secret-token" not in str(second.as_dict())


def test_client_rate_limit_and_input_drift_fail_closed_without_secret_in_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, json={"error": "too many"})

    async def rate_limited():
        http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        client = WeatherDataAPITradeFeedClient("secret-value", http=http)
        try:
            await client.page(condition_ids=(CONDITION,))
        finally:
            await http.aclose()

    with pytest.raises(WeatherTradeFeedError) as raised:
        asyncio.run(rate_limited())
    assert raised.value.code == "TRADE_FEED_RATE_LIMITED"
    assert "secret-value" not in str(raised.value)

    client = WeatherDataAPITradeFeedClient("secret")
    try:
        with pytest.raises(WeatherTradeFeedError) as invalid:
            asyncio.run(client.page(condition_ids=("not-a-condition",)))
        assert invalid.value.code == "TRADE_FEED_CONDITION_INVALID"
    finally:
        asyncio.run(client.close())


def test_execution_time_not_fetch_time_controls_virtual_fill_causality():
    order = _order(shares=10.0)
    # Executed before the virtual bid existed, but fetched later: must never fill it.
    old = PublicTradePrint(
        trade_id="old",
        token_id="YES-1",
        price=0.24,
        shares=8.0,
        received_at=NOW + 10.0,
        aggressor_side="SELL",
        source="test",
        executed_at=NOW - 1.0,
    )
    after_old, old_sim = simulate_public_trade_progression(order, [old])
    assert after_old.simulated_filled_shares == 0.0
    assert old_sim.ignored_preorder_print_shares == pytest.approx(8.0)

    fresh = PublicTradePrint(
        trade_id="fresh",
        token_id="YES-1",
        price=0.24,
        shares=3.0,
        received_at=NOW + 10.0,
        aggressor_side="SELL",
        source="test",
        executed_at=NOW + 1.0,
    )
    after_fresh, fresh_sim = simulate_public_trade_progression(order, [fresh])
    assert after_fresh.simulated_filled_shares == pytest.approx(3.0)
    assert fresh_sim.new_simulated_fill_shares == pytest.approx(3.0)
    assert fresh_sim.last_simulated_fill_received_at == pytest.approx(NOW + 1.0)


def test_trade_execution_timestamp_cannot_be_after_collector_receipt():
    with pytest.raises(WeatherMakerShadowError) as raised:
        PublicTradePrint(
            trade_id="future",
            token_id="YES-1",
            price=0.24,
            shares=1.0,
            received_at=NOW,
            aggressor_side="SELL",
            executed_at=NOW + 1.0,
        )
    assert raised.value.code == "MAKER_TRADE_EXECUTION_AFTER_RECEIPT"
