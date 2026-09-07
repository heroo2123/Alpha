import sqlite3

import pytest

from polymarket_scanner.models import Signal
from polymarket_scanner.settlement import selected_token_payout
from polymarket_scanner.store import Store


def _signal(*, cost: float = 0.8) -> Signal:
    return Signal(
        detector="sports_result_lag",
        confidence="ACTIONABLE",
        event_id="e1",
        market_id="m1",
        title="test",
        detail="test",
        url="https://example.com",
        edge=0.1,
        entry_cost=cost,
        theoretical_payout=1.0,
        token_ids=["yes-token"],
        metadata={"sports_mapping_version": "home_away_v2"},
    )


def test_partial_settlement_is_not_coerced_to_full_loss(tmp_path):
    store = Store(str(tmp_path / "signals.db"))
    signal_id = store.save_signal(_signal(cost=0.4))
    assert signal_id is not None

    result = store.resolve_payout(signal_id, 0.5, 100.0)
    assert result is not None
    assert result["status"] == "RESOLVED_PARTIAL"
    assert result["payout"] == 0.5
    assert result["pnl"] == pytest.approx(25.0)

    row = store.get_signal(signal_id)
    assert row is not None
    assert row["status"] == "RESOLVED_PARTIAL"
    assert row["settlement_payout"] == pytest.approx(0.5)
    assert row["pnl"] == pytest.approx(25.0)


def test_selected_token_payout_preserves_disputed_half_payout():
    row = {"token_ids": '["yes-token"]'}
    market = {
        "closed": True,
        "clobTokenIds": '["yes-token", "no-token"]',
        "outcomePrices": '["0.5", "0.5"]',
    }
    assert selected_token_payout(row, market) == pytest.approx(0.5)


def test_selected_token_payout_fails_closed_on_bad_mapping_or_open_market():
    row = {"token_ids": '["yes-token"]'}
    assert selected_token_payout(row, {
        "closed": False,
        "clobTokenIds": '["yes-token", "no-token"]',
        "outcomePrices": '["1", "0"]',
    }) is None
    assert selected_token_payout(row, {
        "closed": True,
        "clobTokenIds": '["different", "no-token"]',
        "outcomePrices": '["1", "0"]',
    }) is None


def test_manual_trade_requires_actual_execution_cost(tmp_path):
    store = Store(str(tmp_path / "signals.db"))
    signal_id = store.save_signal(_signal(cost=0.80))
    assert signal_id is not None

    with pytest.raises(ValueError, match="actual executed cost"):
        store.record_manual(signal_id, 100.0)

    # Resolve the signal first, then record the user's genuine 0.90 execution.
    store.resolve_payout(signal_id, 1.0, 100.0)
    trade = store.record_manual(signal_id, 100.0, 0.90)
    assert trade["entry_source"] == "USER_REPORTED_EXECUTION"
    assert trade["entry_cost"] == pytest.approx(0.90)
    assert trade["status"] == "WON"
    assert trade["pnl"] == pytest.approx(100.0 / 0.90 - 100.0)
    # If the stale alert quote (0.80) had been reused, this would have been $25.
    assert trade["pnl"] != pytest.approx(25.0)


def test_legacy_manual_rows_are_excluded_from_valid_manual_stats(tmp_path):
    db = str(tmp_path / "signals.db")
    store = Store(db)
    signal_id = store.save_signal(_signal())
    assert signal_id is not None

    with sqlite3.connect(db) as c:
        c.execute(
            """
            INSERT INTO manual_trades(signal_id,stake,entry_cost,status,pnl,created_at)
            VALUES(?,?,?,?,?,?)
            """,
            (signal_id, 50.0, 0.8, "WON", 12.5, "2026-09-07T00:00:00+00:00"),
        )

    stats = store.manual_stats()
    assert stats["total"] == 0
    assert stats["pnl"] == 0.0
    assert stats["legacy_excluded"] == 1


def test_synthetic_structural_settlement_is_disabled(tmp_path):
    store = Store(str(tmp_path / "signals.db"))
    signal_id = store.save_signal(_signal())
    assert signal_id is not None
    with pytest.raises(RuntimeError, match="quote snapshots are not executions|synthetic immediate settlement"):
        store.settle_immediate(signal_id, 100.0)
