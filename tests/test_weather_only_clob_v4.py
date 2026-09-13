from __future__ import annotations

import time

import pytest

from polymarket_scanner.weather_only_clob import WeatherCLOBError, parse_market_info
from polymarket_scanner.weather_only_clob_v4 import parse_book_v4


def _params():
    return parse_market_info(
        "0xcondition",
        {
            "t": [{"t": "yes-token", "o": "Yes"}, {"t": "no-token", "o": "No"}],
            "mos": 5,
            "mts": 0.01,
            "mbf": 0,
            "tbf": 0,
            "rfqe": True,
            "itode": False,
            "fd": {"r": 0.0, "e": 0, "to": True},
        },
        received_at=time.time(),
    )


def _book(**overrides):
    now = time.time()
    value = {
        "market": "0xcondition",
        "asset_id": "yes-token",
        "timestamp": str(int(now * 1000)),
        "hash": "book-hash",
        "bids": [{"price": "0.40", "size": "10"}],
        "asks": [{"price": "0.45", "size": "10"}],
        "last_trade_price": "0.44",
        "min_order_size": "5",
        "tick_size": "0.01",
    }
    value.update(overrides)
    return value


def test_v4_book_requires_exact_condition_identity():
    with pytest.raises(WeatherCLOBError, match="BOOK_CONDITION_IDENTITY_MISMATCH"):
        parse_book_v4(
            "yes-token", "0xcondition", "YES", _book(market="0xwrong"),
            received_at=time.time(), parameters=_params(),
        )


def test_v4_book_rejects_ancient_provider_timestamp():
    with pytest.raises(WeatherCLOBError, match="BOOK_PROVIDER_TIMESTAMP_ANCIENT"):
        parse_book_v4(
            "yes-token", "0xcondition", "YES", _book(timestamp="1000"),
            received_at=time.time(), parameters=_params(),
        )


def test_v4_book_rejects_stale_provider_timestamp():
    with pytest.raises(WeatherCLOBError, match="BOOK_PROVIDER_TIMESTAMP_STALE"):
        parse_book_v4(
            "yes-token", "0xcondition", "YES",
            _book(timestamp=str(int((time.time() - 120) * 1000))),
            received_at=time.time(), parameters=_params(),
        )


def test_v4_book_rejects_min_order_mismatch():
    with pytest.raises(WeatherCLOBError, match="BOOK_MIN_ORDER_SIZE_MISMATCH"):
        parse_book_v4(
            "yes-token", "0xcondition", "YES", _book(min_order_size="1"),
            received_at=time.time(), parameters=_params(),
        )


def test_v4_book_rejects_tick_mismatch():
    with pytest.raises(WeatherCLOBError, match="BOOK_TICK_SIZE_MISMATCH"):
        parse_book_v4(
            "yes-token", "0xcondition", "YES", _book(tick_size="0.001"),
            received_at=time.time(), parameters=_params(),
        )


def test_v4_book_accepts_ms_timestamp_and_preserves_hash():
    evidence = parse_book_v4(
        "yes-token", "0xcondition", "YES", _book(),
        received_at=time.time(), parameters=_params(),
    )
    assert evidence.condition_id == "0xcondition"
    assert evidence.outcome == "YES"
    assert evidence.book_hash == "book-hash"
    assert evidence.minimum_order_size == 5.0
