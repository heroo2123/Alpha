from datetime import datetime, timezone

import pytest

from polymarket_scanner.execution_certificate import EXECUTION_CERTIFICATE_VERSION
from polymarket_scanner.manual_fills import (
    STRUCTURAL_ENTRY_SOURCE,
    open_structural_trades,
    record_structural_fills,
    resolve_structural_trade,
    structural_manual_stats,
)
from polymarket_scanner.models import Signal
from polymarket_scanner.settlement import exact_token_payout
from polymarket_scanner.store import Store
from polymarket_scanner.trade_only import TRADE_READY_VERSION


def _signal(*, limits=(0.47, 0.47), safe_common=50.0) -> Signal:
    legs = []
    for token, outcome, limit in zip(("yes", "no"), ("Yes", "No"), limits):
        legs.append({
            "market_id": "m1",
            "condition_id": "c1",
            "token_id": token,
            "question": "Test market?",
            "outcome": outcome,
            "safe_limit": str(limit),
            "safe_depth_to_limit": str(safe_common),
        })
    return Signal(
        detector="binary_buy_both",
        confidence="ACTIONABLE",
        event_id="e1",
        market_id="m1",
        title="binary",
        detail="binary",
        url="https://example.com",
        edge=0.06,
        entry_cost=0.94,
        theoretical_payout=1.0,
        token_ids=["yes", "no"],
        metadata={
            "trade_ready": True,
            "trade_ready_version": TRADE_READY_VERSION,
            "execution_certificate": {
                "version": EXECUTION_CERTIFICATE_VERSION,
                "safe_common_shares": str(safe_common),
                "legs": legs,
            },
        },
    )


def _fills(*, shares1=10, shares2=10, p1=0.45, p2=0.46, f1=0.10, f2=0.10):
    return [
        {"leg": 1, "shares": shares1, "avg_price": p1, "fee_usd": f1},
        {"leg": 2, "shares": shares2, "avg_price": p2, "fee_usd": f2},
    ]


def _saved(store: Store, signal: Signal | None = None) -> int:
    sid = store.save_signal(signal or _signal())
    assert sid is not None
    return sid


def test_structural_fill_ledger_derives_cost_from_every_actual_leg(tmp_path):
    store = Store(str(tmp_path / "signals.db"))
    sid = _saved(store)

    row = record_structural_fills(
        store,
        sid,
        _fills(),
        execution_at=datetime(2026, 9, 7, 20, 0, tzinfo=timezone.utc),
    )
    assert row["entry_source"] == STRUCTURAL_ENTRY_SOURCE
    assert row["shares"] == 10.0
    assert row["total_cash_cost"] == pytest.approx(9.30)
    assert row["bundle_cost"] == pytest.approx(0.93)
    assert row["within_cert_limits"] is True
    assert row["within_cert_capacity"] is True
    assert [leg["token_id"] for leg in row["legs"]] == ["yes", "no"]

    pending = open_structural_trades(store)
    assert len(pending) == 1
    assert [leg["leg_index"] for leg in pending[0]["legs"]] == [1, 2]


def test_structural_fill_ledger_requires_every_leg_once_and_equal_shares(tmp_path):
    store = Store(str(tmp_path / "signals.db"))
    sid = _saved(store)

    with pytest.raises(ValueError, match="exactly legs 1..2"):
        record_structural_fills(store, sid, [_fills()[0]])
    with pytest.raises(ValueError, match="same filled share count"):
        record_structural_fills(store, sid, _fills(shares2=9))
    with pytest.raises(ValueError, match="only once"):
        record_structural_fills(store, sid, [_fills()[0], _fills()[0]])
    assert open_structural_trades(store) == []


def test_structural_fill_identity_comes_from_persisted_certificate_not_user(tmp_path):
    store = Store(str(tmp_path / "signals.db"))
    sid = _saved(store)
    fills = _fills()
    fills[0]["token_id"] = "attacker-token"
    fills[0]["market_id"] = "attacker-market"

    row = record_structural_fills(store, sid, fills)
    assert row["legs"][0]["token_id"] == "yes"
    assert row["legs"][0]["market_id"] == "m1"


def test_nonconforming_real_fill_is_preserved_but_excluded_from_conforming_stats(tmp_path):
    store = Store(str(tmp_path / "signals.db"))
    sid = _saved(store)
    row = record_structural_fills(store, sid, _fills(p1=0.48))
    assert row["within_cert_limits"] is False

    stats = structural_manual_stats(store)
    assert stats["total_recorded"] == 1
    assert stats["conforming_total"] == 0
    assert stats["nonconforming_excluded"] == 1
    assert stats["exchange_verified"] is False


def test_capacity_overrun_is_recorded_but_not_cert_policy_evidence(tmp_path):
    store = Store(str(tmp_path / "signals.db"))
    sid = _saved(store, _signal(safe_common=5))
    row = record_structural_fills(store, sid, _fills(shares1=6, shares2=6))
    assert row["within_cert_capacity"] is False
    assert structural_manual_stats(store)["conforming_total"] == 0


def test_duplicate_structural_execution_for_same_alert_is_rejected(tmp_path):
    store = Store(str(tmp_path / "signals.db"))
    sid = _saved(store)
    record_structural_fills(store, sid, _fills())
    with pytest.raises(ValueError, match="already recorded"):
        record_structural_fills(store, sid, _fills())


def test_exact_token_payout_preserves_partial_resolution_and_fails_closed():
    market = {
        "closed": True,
        "clobTokenIds": '["yes", "no"]',
        "outcomePrices": '["0.5", "0.5"]',
    }
    assert exact_token_payout("yes", market) == 0.5
    assert exact_token_payout("no", market) == 0.5
    assert exact_token_payout("missing", market) is None
    assert exact_token_payout("yes", {**market, "closed": False}) is None


def test_structural_pnl_uses_actual_leg_cash_and_exact_token_payouts(tmp_path):
    store = Store(str(tmp_path / "signals.db"))
    sid = _saved(store)
    recorded = record_structural_fills(store, sid, _fills())

    resolved = resolve_structural_trade(
        store,
        recorded["id"],
        {"yes": 1.0, "no": 0.0},
    )
    assert resolved is not None
    assert resolved["total_payout_per_bundle"] == 1.0
    assert resolved["pnl"] == pytest.approx(0.70)
    assert open_structural_trades(store) == []

    stats = structural_manual_stats(store)
    assert stats["resolved"] == 1
    assert stats["cash_cost"] == pytest.approx(9.30)
    assert stats["pnl"] == pytest.approx(0.70)


def test_structural_settlement_requires_exact_complete_token_vector(tmp_path):
    store = Store(str(tmp_path / "signals.db"))
    sid = _saved(store)
    recorded = record_structural_fills(store, sid, _fills())
    with pytest.raises(ValueError, match="exactly match"):
        resolve_structural_trade(store, recorded["id"], {"yes": 1.0})
    assert len(open_structural_trades(store)) == 1
