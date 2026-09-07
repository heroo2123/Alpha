import sqlite3
from datetime import datetime, timedelta, timezone

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


def _manual_insert(
    db: str,
    signal_id: int,
    *,
    execution_at: str | None,
    status: str = "WON",
    pnl: float | None = 25.0,
    entry_source: str = "USER_REPORTED_EXECUTION",
) -> None:
    with sqlite3.connect(db) as c:
        c.execute(
            """
            INSERT INTO manual_trades(
                signal_id,stake,entry_cost,entry_source,execution_at,status,pnl,
                settlement_payout,created_at,resolved_at
            ) VALUES(?,?,?,?,?,?,?,?,?,?)
            """,
            (
                signal_id, 100.0, 0.8, entry_source, execution_at, status, pnl,
                1.0 if status == "WON" else None,
                execution_at or "2026-09-07T00:00:00+00:00",
                "2026-09-07T01:00:00+00:00" if status != "OPEN" else None,
            ),
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


def test_manual_trade_requires_actual_cost_and_resolves_only_after_execution(tmp_path):
    db = str(tmp_path / "signals.db")
    store = Store(db)
    signal_id = store.save_signal(_signal(cost=0.80))
    assert signal_id is not None

    with pytest.raises(ValueError, match="actual executed cost"):
        store.record_manual(signal_id, 100.0)

    trade = store.record_manual(signal_id, 100.0, 0.90)
    assert trade["entry_source"] == "USER_REPORTED_EXECUTION"
    assert trade["entry_cost"] == pytest.approx(0.90)
    assert trade["status"] == "OPEN"
    assert trade["pnl"] is None
    assert trade["settlement_payout"] is None

    with sqlite3.connect(db) as c:
        row = c.execute(
            "SELECT execution_at,status,pnl,settlement_payout,resolved_at FROM manual_trades WHERE id=?",
            (trade["id"],),
        ).fetchone()
    assert row is not None
    assert row[0]
    assert row[1] == "OPEN"
    assert row[2] is None
    assert row[3] is None
    assert row[4] is None

    store.resolve_payout(signal_id, 1.0, 100.0)
    rows = store.recent_manual(10)
    resolved = next(x for x in rows if x["id"] == trade["id"])
    assert resolved["status"] == "WON"
    assert resolved["pnl"] == pytest.approx(100.0 / 0.90 - 100.0)
    assert resolved["pnl"] != pytest.approx(25.0)

    stats = store.manual_stats()
    assert stats["total"] == 1
    assert stats["won"] == 1
    assert stats["excluded_total"] == 0


def test_manual_trade_after_known_settlement_is_rejected(tmp_path):
    store = Store(str(tmp_path / "signals.db"))
    signal_id = store.save_signal(_signal(cost=0.80))
    assert signal_id is not None

    store.resolve_payout(signal_id, 1.0, 100.0)
    with pytest.raises(ValueError, match="after the alert has settled|retrospective"):
        store.record_manual(signal_id, 100.0, 0.90)

    assert store.manual_stats()["total"] == 0


def test_duplicate_actual_execution_for_same_alert_is_rejected(tmp_path):
    store = Store(str(tmp_path / "signals.db"))
    signal_id = store.save_signal(_signal())
    assert signal_id is not None

    first = store.record_manual(signal_id, 50.0, 0.82)
    assert first["status"] == "OPEN"
    with pytest.raises(ValueError, match="already recorded"):
        store.record_manual(signal_id, 25.0, 0.81)
    assert store.manual_stats()["total"] == 1


def test_manual_partial_payout_is_applied_only_to_preexisting_execution(tmp_path):
    store = Store(str(tmp_path / "signals.db"))
    signal_id = store.save_signal(_signal())
    assert signal_id is not None
    trade = store.record_manual(signal_id, 100.0, 0.80)

    store.resolve_payout(signal_id, 0.5, 100.0)
    rows = store.recent_manual(10)
    resolved = next(x for x in rows if x["id"] == trade["id"])
    assert resolved["status"] == "RESOLVED_PARTIAL"
    assert resolved["pnl"] == pytest.approx(100.0 / 0.80 * 0.5 - 100.0)


def test_legacy_manual_rows_are_excluded_from_valid_manual_stats(tmp_path):
    db = str(tmp_path / "signals.db")
    store = Store(db)
    signal_id = store.save_signal(_signal())
    assert signal_id is not None

    _manual_insert(
        db,
        signal_id,
        execution_at=None,
        entry_source="LEGACY_ALERT_ESTIMATE",
    )

    stats = store.manual_stats()
    assert stats["total"] == 0
    assert stats["pnl"] == 0.0
    assert stats["legacy_excluded"] == 1
    assert stats["excluded_total"] == 1


def test_pre_fix_sports_manual_row_is_quarantined_without_rewriting(tmp_path):
    db = str(tmp_path / "signals.db")
    store = Store(db)
    bad = _signal()
    bad.metadata = {"sports_mapping_version": "old_title_order_bug"}
    signal_id = store.save_signal(bad)
    assert signal_id is not None
    execution_at = datetime(2026, 9, 7, 0, 0, tzinfo=timezone.utc).isoformat()
    _manual_insert(db, signal_id, execution_at=execution_at)

    stats = store.manual_stats()
    assert stats["total"] == 0
    assert stats["known_bug_excluded"] == 1
    assert stats["pnl"] == 0.0

    with sqlite3.connect(db) as c:
        row = c.execute("SELECT entry_source,status,pnl FROM manual_trades").fetchone()
    assert row == ("USER_REPORTED_EXECUTION", "WON", 25.0)


def test_historical_retrospective_manual_row_is_excluded_without_rewrite(tmp_path):
    db = str(tmp_path / "signals.db")
    store = Store(db)
    signal_id = store.save_signal(_signal())
    assert signal_id is not None
    store.resolve_payout(signal_id, 1.0, 100.0)
    signal = store.get_signal(signal_id)
    resolved_at = datetime.fromisoformat(signal["resolved_at"])
    execution_at = (resolved_at + timedelta(seconds=5)).isoformat()
    _manual_insert(db, signal_id, execution_at=execution_at)

    stats = store.manual_stats()
    assert stats["total"] == 0
    assert stats["retrospective_excluded"] == 1
    assert stats["pnl"] == 0.0


def test_manual_row_without_execution_timestamp_is_not_valid_evidence(tmp_path):
    db = str(tmp_path / "signals.db")
    store = Store(db)
    signal_id = store.save_signal(_signal())
    assert signal_id is not None
    _manual_insert(db, signal_id, execution_at=None)

    stats = store.manual_stats()
    assert stats["total"] == 0
    assert stats["timing_unknown_excluded"] == 1


def test_structural_combined_cost_row_is_separate_unverified_evidence(tmp_path):
    db = str(tmp_path / "signals.db")
    store = Store(db)
    structural = _signal()
    structural.detector = "binary_buy_both"
    structural.token_ids = ["yes", "no"]
    structural.metadata = {"fingerprint_key": "structural"}
    signal_id = store.save_signal(structural)
    assert signal_id is not None

    execution_at = datetime(2026, 9, 7, 0, 0, tzinfo=timezone.utc).isoformat()
    _manual_insert(db, signal_id, execution_at=execution_at, status="OPEN", pnl=None)
    stats = store.manual_stats()
    assert stats["total"] == 0
    assert stats["structural_unverified_excluded"] == 1
    assert stats["stake"] == 0.0


def test_synthetic_structural_settlement_is_disabled(tmp_path):
    store = Store(str(tmp_path / "signals.db"))
    signal_id = store.save_signal(_signal())
    assert signal_id is not None
    with pytest.raises(RuntimeError, match="quote snapshots are not executions|synthetic immediate settlement"):
        store.settle_immediate(signal_id, 100.0)
