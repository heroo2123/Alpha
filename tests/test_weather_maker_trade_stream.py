from __future__ import annotations

import pytest

from polymarket_scanner.weather_only_maker_shadow import PublicTradePrint
from polymarket_scanner.weather_only_maker_trade_stream import (
    MAKER_TRADE_STREAM_MAX_PRINTS_PER_TOKEN,
    MakerTradeStreamError,
    parse_last_trade_price_message,
)
from polymarket_scanner.weather_only_maker_trade_stream_v2 import (
    ProspectiveMakerTradeBufferV2,
    ProspectiveMakerTradeStreamV2,
)


TOKEN = "123456789"
MARKET = "0x" + "1" * 64
TX = "0x" + "2" * 64
NOW = 1_800_000_000.0


def _message(**updates):
    value = {
        "event_type": "last_trade_price",
        "asset_id": TOKEN,
        "market": MARKET,
        "price": "0.25",
        "size": "4.5",
        "fee_rate_bps": "0",
        "side": "SELL",
        "timestamp": str(int((NOW - 1.0) * 1000)),
        "transaction_hash": TX,
    }
    value.update(updates)
    return value


def _trade(index: int, *, executed_at: float | None = None):
    return PublicTradePrint(
        trade_id=f"trade-{index}",
        token_id=TOKEN,
        price=0.25,
        shares=1.0,
        received_at=NOW + index,
        aggressor_side="SELL",
        source="test",
        executed_at=NOW + index if executed_at is None else executed_at,
    )


def test_parser_maps_documented_last_trade_event_to_causal_print():
    row = parse_last_trade_price_message(_message(), received_at=NOW)
    assert row is not None
    assert row.token_id == TOKEN
    assert row.price == pytest.approx(0.25)
    assert row.shares == pytest.approx(4.5)
    assert row.aggressor_side == "SELL"
    assert row.effective_executed_at == pytest.approx(NOW - 1.0)
    assert row.received_at == pytest.approx(NOW)
    assert row.trade_id.startswith("ws:")
    assert row.actual_fill_authority is False if hasattr(row, "actual_fill_authority") else True


def test_non_trade_event_is_ignored_not_reinterpreted():
    assert parse_last_trade_price_message(
        {"event_type": "book", "asset_id": TOKEN}, received_at=NOW
    ) is None


def test_future_execution_timestamp_fails_closed():
    with pytest.raises(MakerTradeStreamError) as exc:
        parse_last_trade_price_message(
            _message(timestamp=str(int((NOW + 3.0) * 1000))), received_at=NOW
        )
    assert exc.value.code == "MAKER_STREAM_EVENT_FROM_FUTURE"


def test_invalid_side_fails_closed():
    with pytest.raises(MakerTradeStreamError) as exc:
        parse_last_trade_price_message(_message(side="UNKNOWN"), received_at=NOW)
    assert exc.value.code == "MAKER_STREAM_SIDE_INVALID"


def test_first_book_anchors_coverage_and_later_books_do_not_move_it():
    buffer = ProspectiveMakerTradeBufferV2()
    buffer.mark_gap()
    first = buffer.mark_subscribed(TOKEN, received_at=NOW)
    later = buffer.mark_subscribed(TOKEN, received_at=NOW + 50.0)
    assert later.generation == first.generation
    assert later.started_at == pytest.approx(NOW)
    assert buffer.coverage(TOKEN).started_at == pytest.approx(NOW)


def test_order_cannot_predate_first_coverage_book():
    buffer = ProspectiveMakerTradeBufferV2()
    generation = buffer.mark_gap()
    buffer.mark_subscribed(TOKEN, received_at=NOW)
    with pytest.raises(MakerTradeStreamError) as exc:
        buffer.trades_for_order(
            token_id=TOKEN,
            order_created_at=NOW - 0.1,
            required_generation=generation,
        )
    assert exc.value.code == "MAKER_STREAM_ORDER_PREDATES_COVERAGE"


def test_disconnect_generation_gap_invalidates_old_order_coverage():
    buffer = ProspectiveMakerTradeBufferV2()
    generation = buffer.mark_gap()
    buffer.mark_subscribed(TOKEN, received_at=NOW)
    buffer.record(_trade(1))
    buffer.mark_gap()
    buffer.mark_subscribed(TOKEN, received_at=NOW + 2.0)
    with pytest.raises(MakerTradeStreamError) as exc:
        buffer.trades_for_order(
            token_id=TOKEN,
            order_created_at=NOW + 0.5,
            required_generation=generation,
        )
    assert exc.value.code == "MAKER_STREAM_GENERATION_GAP"


def test_duplicate_ws_trade_is_idempotent_inside_one_generation():
    buffer = ProspectiveMakerTradeBufferV2()
    generation = buffer.mark_gap()
    buffer.mark_subscribed(TOKEN, received_at=NOW)
    trade = _trade(1)
    buffer.record(trade)
    buffer.record(trade)
    rows = buffer.trades_for_order(
        token_id=TOKEN,
        order_created_at=NOW + 0.5,
        required_generation=generation,
    )
    assert [row.trade_id for row in rows] == [trade.trade_id]


def test_trade_received_before_coverage_is_never_replayed_into_later_order():
    buffer = ProspectiveMakerTradeBufferV2()
    generation = buffer.mark_gap()
    buffer.record(_trade(1))
    buffer.mark_subscribed(TOKEN, received_at=NOW + 2.0)
    rows = buffer.trades_for_order(
        token_id=TOKEN,
        order_created_at=NOW + 2.1,
        required_generation=generation,
    )
    assert rows == ()


def test_buffer_overflow_invalidates_entire_generation(monkeypatch):
    # Use the actual fixed cap; direct deque population keeps the test deterministic
    # without depending on network scheduling.
    buffer = ProspectiveMakerTradeBufferV2()
    generation = buffer.mark_gap()
    buffer.mark_subscribed(TOKEN, received_at=NOW)
    queue = buffer._prints[TOKEN]
    seen = buffer._seen[TOKEN]
    for index in range(MAKER_TRADE_STREAM_MAX_PRINTS_PER_TOKEN):
        row = _trade(index + 1)
        queue.append(row)
        seen.add(row.trade_id)
    with pytest.raises(MakerTradeStreamError) as exc:
        buffer.record(_trade(MAKER_TRADE_STREAM_MAX_PRINTS_PER_TOKEN + 10))
    assert exc.value.code == "MAKER_STREAM_PRINT_BUFFER_CAP"
    assert buffer.generation == generation + 1
    assert buffer.coverage(TOKEN) is None


def test_stream_status_never_claims_auth_or_fill_authority():
    stream = ProspectiveMakerTradeStreamV2()
    status = stream.status()
    assert status["authenticated"] is False
    assert status["actual_fill_authority"] is False
    assert status["financial_authority"] is False
    assert status["first_book_coverage_anchor"] is True
