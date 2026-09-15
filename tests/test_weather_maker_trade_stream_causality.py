from __future__ import annotations

import pytest

from polymarket_scanner.weather_only_maker_trade_stream import (
    MakerTradeStreamError,
    ProspectiveMakerTradeBuffer,
    parse_last_trade_price_message,
)


TOKEN = "123456789"
MARKET = "0x" + "1" * 64
TX = "0x" + "2" * 64
NOW = 1_800_000_000.0


def _message(*, timestamp: float):
    return {
        "event_type": "last_trade_price",
        "asset_id": TOKEN,
        "market": MARKET,
        "price": "0.25",
        "size": "4.5",
        "side": "SELL",
        "timestamp": str(int(timestamp * 1000)),
        "transaction_hash": TX,
    }


@pytest.mark.parametrize("ahead_seconds", [0.001, 0.050, 1.0, 1.999])
def test_any_provider_execution_time_after_receipt_fails_closed(ahead_seconds):
    with pytest.raises(MakerTradeStreamError) as exc:
        parse_last_trade_price_message(
            _message(timestamp=NOW + ahead_seconds), received_at=NOW
        )
    assert exc.value.code == "MAKER_STREAM_EVENT_FROM_FUTURE"


def test_trade_at_or_before_receipt_remains_valid_causal_evidence():
    row = parse_last_trade_price_message(
        _message(timestamp=NOW - 0.001), received_at=NOW
    )
    assert row is not None
    assert row.effective_executed_at <= row.received_at


def test_preorder_receipt_cannot_become_postorder_trade_evidence():
    # A future-dated provider timestamp is rejected before it can enter the buffer,
    # preventing a print received before order creation from later appearing causal.
    buffer = ProspectiveMakerTradeBuffer()
    generation = buffer.mark_gap()
    buffer.mark_subscribed(TOKEN, received_at=NOW - 1.0)
    with pytest.raises(MakerTradeStreamError):
        parse_last_trade_price_message(
            _message(timestamp=NOW + 0.5), received_at=NOW
        )
    assert buffer.trades_for_order(
        token_id=TOKEN,
        order_created_at=NOW + 0.1,
        required_generation=generation,
    ) == ()
