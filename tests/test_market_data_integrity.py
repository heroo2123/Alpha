import math
import time

from polymarket_scanner.models import Book
from polymarket_scanner.streams import LiveMarketStream


def _book_message(*, ts="1000", ask_size="10", bid_size="12"):
    return {
        "event_type": "book",
        "asset_id": "token",
        "timestamp": ts,
        "hash": "book-hash",
        "bids": [
            {"price": "0.40", "size": bid_size},
            {"price": "0.35", "size": "20"},
        ],
        "asks": [
            {"price": "0.60", "size": ask_size},
            {"price": "0.65", "size": "30"},
        ],
    }


def _price_change(*, ts="1001", price="0.60", size="3", side="SELL", best_bid="0.40", best_ask="0.60"):
    return {
        "event_type": "price_change",
        "timestamp": ts,
        "price_changes": [{
            "asset_id": "token",
            "price": price,
            "size": size,
            "side": side,
            "hash": f"delta-{ts}",
            "best_bid": best_bid,
            "best_ask": best_ask,
        }],
    }


def test_same_price_price_change_replaces_aggregate_size_instead_of_preserving_old_size():
    stream = LiveMarketStream()
    stream._apply(_book_message())
    assert stream.books["token"].best_ask == 0.60
    assert stream.books["token"].best_ask_size == 10.0

    stream._apply(_price_change(size="3"))
    assert stream.books["token"].best_ask == 0.60
    assert stream.books["token"].best_ask_size == 3.0
    assert dict(stream.books["token"].asks)[0.65] == 30.0


def test_price_change_zero_size_removes_level_and_advances_best_price():
    stream = LiveMarketStream()
    stream._apply(_book_message())

    stream._apply(_price_change(size="0", best_ask="0.65"))
    assert stream.books["token"].best_ask == 0.65
    assert stream.books["token"].best_ask_size == 30.0


def test_out_of_order_delta_is_ignored_without_rewinding_depth():
    stream = LiveMarketStream()
    stream._apply(_book_message(ts="2000"))
    stream._apply(_price_change(ts="2001", size="4"))
    assert stream.books["token"].best_ask_size == 4.0

    stream._apply(_price_change(ts="1999", size="99"))
    assert stream.books["token"].best_ask_size == 4.0
    assert stream.out_of_order_ignored == 1


def test_incremental_delta_before_current_epoch_snapshot_is_rejected():
    stream = LiveMarketStream()
    stream._token_owner["token"] = 7
    stream._worker_tokens[7] = {"token"}
    stream._worker_epoch[7] = 2

    stream._apply(_price_change(), worker_id=7, epoch=2)
    assert "token" not in stream.books


def test_new_worker_epoch_invalidates_pre_reconnect_book_until_resnapshot():
    stream = LiveMarketStream()
    stream._token_owner["token"] = 3
    stream._worker_tokens[3] = {"token"}
    stream._worker_epoch[3] = 1
    stream._apply(_book_message(), worker_id=3, epoch=1)
    assert "token" in stream.books

    epoch = stream._begin_worker_epoch(3)
    assert epoch == 2
    assert "token" not in stream.books
    assert stream.invalidated_books >= 1

    # Deltas from the new connection do not resurrect pre-disconnect depth.
    stream._apply(_price_change(ts="3000"), worker_id=3, epoch=2)
    assert "token" not in stream.books

    stream._apply(_book_message(ts="3001", ask_size="7"), worker_id=3, epoch=2)
    assert stream.books["token"].best_ask_size == 7.0
    assert stream.books["token"].source_epoch == 2


def test_price_change_that_contradicts_declared_best_invalidates_book():
    stream = LiveMarketStream()
    stream._apply(_book_message())

    # Changing the 0.65 level cannot make 0.55 the best ask; the declared top is
    # inconsistent with our reconstructed depth, so fail closed until next snapshot.
    stream._apply(_price_change(price="0.65", size="10", best_ask="0.55"))
    assert "token" not in stream.books
    assert stream.invalidated_books == 1


def test_best_bid_ask_never_invents_or_refreshes_depth_size():
    stream = LiveMarketStream()
    received = time.time() - 5
    stream._apply(_book_message(), received_at=received)
    before = stream.books["token"].clone()

    stream._apply({
        "event_type": "best_bid_ask",
        "asset_id": "token",
        "timestamp": "1002",
        "best_bid": "0.40",
        "best_ask": "0.60",
    }, received_at=time.time())

    after = stream.books["token"]
    assert after.best_bid_size == before.best_bid_size
    assert after.best_ask_size == before.best_ask_size
    # A price-only consistency message is not allowed to make old depth look fresh.
    assert after.received_at == received


def test_best_bid_ask_contradiction_invalidates_depth_instead_of_mutating_top_only():
    stream = LiveMarketStream()
    stream._apply(_book_message())

    stream._apply({
        "event_type": "best_bid_ask",
        "asset_id": "token",
        "timestamp": "1002",
        "best_bid": "0.40",
        "best_ask": "0.59",
    })
    assert "token" not in stream.books


def test_snapshot_returns_isolated_books_not_live_mutable_objects():
    stream = LiveMarketStream()
    stream._apply(_book_message())
    snap = stream.snapshot()
    snap["token"].asks[0] = (0.99, 999.0)

    live = stream.books["token"]
    assert live.best_ask == 0.60
    assert live.best_ask_size == 10.0


def test_book_clone_preserves_provenance_and_is_finite():
    book = Book(
        "t",
        [(0.4, 1.0)],
        [(0.6, 2.0)],
        timestamp="123",
        received_at=time.time(),
        source="test",
        source_epoch=4,
        book_hash="abc",
    )
    clone = book.clone()
    assert clone is not book
    assert clone.bids is not book.bids
    assert clone.asks is not book.asks
    assert clone.source == "test"
    assert clone.source_epoch == 4
    assert clone.book_hash == "abc"
    assert clone.received_at is not None and math.isfinite(clone.received_at)
