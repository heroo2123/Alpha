from __future__ import annotations

import pytest

from polymarket_scanner.weather_only_maker_trade_stream import (
    MakerTradeStreamError,
    parse_last_trade_price_message,
)


TOKEN = "123456789"
MARKET = "0x" + "1" * 64
TX = "0x" + "abcdef" * 10 + "abcd"
NOW = 1_800_000_000.0


def _message(**updates):
    value = {
        "event_type": "last_trade_price",
        "asset_id": TOKEN,
        "market": MARKET,
        "price": "0.25",
        "size": "4.5",
        "side": "SELL",
        "timestamp": str(int((NOW - 1.0) * 1000)),
        "transaction_hash": TX,
    }
    value.update(updates)
    return value


def test_uppercase_hex_body_normalizes_to_same_valid_transaction_identity():
    lower = parse_last_trade_price_message(_message(), received_at=NOW)
    upper_body = parse_last_trade_price_message(
        _message(transaction_hash="0x" + TX[2:].upper()), received_at=NOW
    )
    assert lower is not None and upper_body is not None
    assert lower.trade_id == upper_body.trade_id


def test_uppercase_0x_market_prefix_fails_closed_before_normalization():
    with pytest.raises(MakerTradeStreamError) as exc:
        parse_last_trade_price_message(
            _message(market="0X" + MARKET[2:]), received_at=NOW
        )
    assert exc.value.code == "MAKER_STREAM_MARKET_INVALID"


@pytest.mark.parametrize(
    ("field", "value", "code"),
    [
        ("market", " " + MARKET, "MAKER_STREAM_MARKET_INVALID"),
        ("market", MARKET + " ", "MAKER_STREAM_MARKET_INVALID"),
        ("transaction_hash", " " + TX, "MAKER_STREAM_TRANSACTION_HASH_INVALID"),
        ("transaction_hash", TX + " ", "MAKER_STREAM_TRANSACTION_HASH_INVALID"),
    ],
)
def test_hex_identity_whitespace_is_not_silently_stripped(field, value, code):
    with pytest.raises(MakerTradeStreamError) as exc:
        parse_last_trade_price_message(_message(**{field: value}), received_at=NOW)
    assert exc.value.code == code
