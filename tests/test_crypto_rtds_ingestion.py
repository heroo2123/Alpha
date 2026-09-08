import math

from polymarket_scanner.streams import CryptoRTDS


def _msg(*, ts=1_725_000_000, price=50000.0, symbol="btc/usd", topic="crypto_prices_chainlink"):
    return {
        "topic": topic,
        "payload": {
            "symbol": symbol,
            "value": price,
            "timestamp": ts,
        },
    }


def test_malformed_or_missing_source_timestamp_is_dropped_not_replaced_with_now():
    rtds = CryptoRTDS()
    for ts in (None, "", "bad", float("nan"), float("inf"), 0, -1):
        rtds._apply(_msg(ts=ts))
    assert rtds.latest("crypto_prices_chainlink", "btc/usd") is None
    assert not rtds.history.get(("crypto_prices_chainlink", "btc/usd"))
    assert rtds.invalid_rows == 7
    assert rtds.last_valid_update_at is None


def test_nonfinite_or_nonpositive_price_is_dropped():
    rtds = CryptoRTDS()
    for price in (float("nan"), float("inf"), float("-inf"), 0, -1):
        rtds._apply(_msg(price=price))
    assert rtds.latest("crypto_prices_chainlink", "btc/usd") is None
    assert rtds.invalid_rows == 5


def test_millisecond_source_timestamp_converts_without_receipt_time_substitution():
    rtds = CryptoRTDS()
    rtds._apply(_msg(ts=1_725_000_000_123))
    tick = rtds.latest("crypto_prices_chainlink", "btc/usd")
    assert tick is not None
    assert math.isclose(tick.ts, 1_725_000_000.123, rel_tol=0, abs_tol=1e-9)
    assert tick.price == 50000.0
    assert rtds.last_valid_update_at is not None


def test_out_of_order_tick_cannot_overwrite_latest_or_break_history_order():
    rtds = CryptoRTDS()
    rtds._apply(_msg(ts=200.0, price=20.0))
    rtds._apply(_msg(ts=199.0, price=99.0))

    tick = rtds.latest("crypto_prices_chainlink", "btc/usd")
    rows = list(rtds.history[("crypto_prices_chainlink", "btc/usd")])
    assert tick is not None and tick.ts == 200.0 and tick.price == 20.0
    assert [(x.ts, x.price) for x in rows] == [(200.0, 20.0)]
    assert rtds.out_of_order_ignored == 1


def test_exact_duplicate_timestamp_and_price_does_not_duplicate_history():
    rtds = CryptoRTDS()
    rtds._apply(_msg(ts=200.0, price=20.0))
    rtds._apply(_msg(ts=200.0, price=20.0))
    rows = list(rtds.history[("crypto_prices_chainlink", "btc/usd")])
    assert [(x.ts, x.price) for x in rows] == [(200.0, 20.0)]
    assert rtds.conflicting_timestamp_rows == 0


def test_same_timestamp_conflicting_price_is_quarantined_until_later_valid_tick():
    rtds = CryptoRTDS()
    key = ("crypto_prices_chainlink", "btc/usd")
    rtds._apply(_msg(ts=200.0, price=20.0))
    rtds._apply(_msg(ts=200.0, price=21.0))

    assert rtds.latest(*key) is None
    assert list(rtds.history[key]) == []
    assert rtds.conflicting_timestamp_rows == 1

    rtds._apply(_msg(ts=201.0, price=22.0))
    tick = rtds.latest(*key)
    assert tick is not None and tick.ts == 201.0 and tick.price == 22.0
    assert [(x.ts, x.price) for x in rtds.history[key]] == [(201.0, 22.0)]


def test_nested_data_rows_and_list_messages_preserve_strict_ingestion():
    rtds = CryptoRTDS()
    rtds._apply([
        {
            "topic": "crypto_prices_chainlink",
            "payload": {"data": [
                {"symbol": "eth/usd", "value": 3000.0, "timestamp_ms": 1_725_000_000_000},
                {"symbol": "sol/usd", "value": 150.0, "timestamp": "bad"},
            ]},
        },
        _msg(symbol="xrp/usd", ts=1_725_000_001, price=0.6),
    ])
    eth = rtds.latest("crypto_prices_chainlink", "eth/usd")
    sol = rtds.latest("crypto_prices_chainlink", "sol/usd")
    xrp = rtds.latest("crypto_prices_chainlink", "xrp/usd")
    assert eth is not None and eth.ts == 1_725_000_000.0
    assert sol is None
    assert xrp is not None and xrp.ts == 1_725_000_001.0
    assert rtds.invalid_rows == 1
