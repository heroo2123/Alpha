from types import SimpleNamespace

import pytest

from polymarket_scanner.crypto_v3 import CRYPTO_FEED_VERSION
from polymarket_scanner.feed_health import feed_progress_snapshot
from polymarket_scanner.sports_v3 import (
    SPORTS_CAUSAL_CACHE_VERSION,
    _reset_sports_causal_state_for_tests,
)
from polymarket_scanner.streams import PriceTick


@pytest.fixture(autouse=True)
def _reset_sports_causal_state():
    _reset_sports_causal_state_for_tests()
    yield
    _reset_sports_causal_state_for_tests()


def test_feed_health_separates_transport_from_valid_market_progress():
    market = SimpleNamespace(
        connected_workers=2,
        books={"a": object(), "b": object()},
        last_message_at=1000.0,
        last_valid_update_at=995.0,
        last_full_book_at=990.0,
        invalidated_books=3,
        out_of_order_ignored=4,
    )
    sports = SimpleNamespace(
        connected=True,
        last_message_at=1000.0,
        last_error=None,
        results={},
    )
    crypto = SimpleNamespace(
        connected=True,
        last_message_at=1000.0,
        latest_ticks={},
    )

    snap = feed_progress_snapshot(market, sports, crypto, now=1000.0)
    clob = snap["market_clob"]
    assert clob["last_transport_message_at"] == 1000.0
    assert clob["last_valid_book_update_age_seconds"] == 5.0
    assert clob["last_full_book_age_seconds"] == 10.0
    assert clob["cached_synchronized_books"] == 2
    assert clob["books_invalidated_total"] == 3
    assert clob["out_of_order_ignored_total"] == 4


def test_feed_health_uses_sports_source_timestamp_not_socket_heartbeat():
    market = SimpleNamespace(
        connected_workers=0, books={}, last_message_at=None,
        last_valid_update_at=None, last_full_book_at=None,
        invalidated_books=0, out_of_order_ignored=0,
    )
    sports = SimpleNamespace(
        connected=True,
        last_message_at=1000.0,
        last_error=None,
        results={
            "game": {
                "slug": "game",
                "ended": True,
                "score": "2-1",
                "updated_at": 900.0,
            }
        },
    )
    crypto = SimpleNamespace(connected=False, last_message_at=None, latest_ticks={})

    snap = feed_progress_snapshot(market, sports, crypto, now=1000.0)["sports"]
    assert snap["causal_cache_version"] == SPORTS_CAUSAL_CACHE_VERSION
    assert snap["last_transport_message_at"] == 1000.0
    assert snap["latest_source_timestamp"] == 900.0
    assert snap["latest_source_age_seconds"] == 100.0
    assert snap["payloads_with_source_time"] == 1
    assert snap["causal_tracked_slugs"] == 0
    assert snap["causal_quarantined_slugs"] == 0


def test_feed_health_counts_crypto_fresh_stale_future_and_invalid_ticks():
    market = SimpleNamespace(
        connected_workers=0, books={}, last_message_at=None,
        last_valid_update_at=None, last_full_book_at=None,
        invalidated_books=0, out_of_order_ignored=0,
    )
    sports = SimpleNamespace(connected=False, last_message_at=None, last_error=None, results={})
    crypto = SimpleNamespace(
        connected=True,
        last_message_at=1000.0,
        last_valid_update_at=998.0,
        invalid_rows=7,
        out_of_order_ignored=3,
        conflicting_timestamp_rows=2,
        latest_ticks={
            ("t", "fresh"): PriceTick("t", "fresh", 100.0, 999.0),
            ("t", "stale"): PriceTick("t", "stale", 100.0, 900.0),
            ("t", "future"): PriceTick("t", "future", 100.0, 1005.0),
            ("t", "bad"): PriceTick("t", "bad", -1.0, 999.0),
        },
    )

    snap = feed_progress_snapshot(market, sports, crypto, now=1000.0)["crypto_rtds"]
    assert snap["feed_version"] == CRYPTO_FEED_VERSION
    assert snap["fresh_tick_keys"] == 1
    assert snap["stale_tick_keys"] == 1
    assert snap["future_tick_keys"] == 1
    assert snap["invalid_tick_keys"] == 1
    assert snap["latest_source_tick_at"] == 1005.0
    assert snap["latest_source_tick_age_seconds"] == -5.0
    assert snap["last_transport_message_at"] == 1000.0
    assert snap["last_valid_update_at"] == 998.0
    assert snap["last_valid_update_age_seconds"] == 2.0
    assert snap["invalid_rows_total"] == 7
    assert snap["out_of_order_ignored_total"] == 3
    assert snap["conflicting_timestamp_rows_total"] == 2
    assert snap["valid_progress_now"] is True


def test_crypto_transport_without_valid_source_progress_is_not_healthy_progress():
    market = SimpleNamespace(
        connected_workers=0, books={}, last_message_at=None,
        last_valid_update_at=None, last_full_book_at=None,
        invalidated_books=0, out_of_order_ignored=0,
    )
    sports = SimpleNamespace(connected=False, last_message_at=None, last_error=None, results={})
    crypto = SimpleNamespace(
        connected=True,
        last_message_at=1000.0,
        last_valid_update_at=900.0,
        invalid_rows=10,
        out_of_order_ignored=5,
        conflicting_timestamp_rows=1,
        latest_ticks={
            ("t", "stale"): PriceTick("t", "stale", 100.0, 900.0),
        },
    )
    snap = feed_progress_snapshot(market, sports, crypto, now=1000.0)["crypto_rtds"]
    assert snap["connected"] is True
    assert snap["last_transport_message_at"] == 1000.0
    assert snap["fresh_tick_keys"] == 0
    assert snap["last_valid_update_age_seconds"] == 100.0
    assert snap["valid_progress_now"] is False
