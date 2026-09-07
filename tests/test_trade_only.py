from polymarket_scanner.models import Signal
from polymarket_scanner.trade_only import TRADE_READY_VERSION, is_trade_ready, mark_trade_readiness


def _signal(detector="binary_buy_both", confidence="ACTIONABLE", cert="BINARY_COMPLEMENT_VERIFIED"):
    return Signal(
        detector=detector,
        confidence=confidence,
        event_id="e1",
        market_id="m1",
        title="test",
        detail="test",
        url="https://example.com",
        edge=0.04,
        entry_cost=0.96,
        theoretical_payout=1.0,
        token_ids=["yes", "no"],
        metadata={
            "certification_status": cert,
            "rest_confirmed_at": "2026-09-07T00:00:00+00:00",
            "confirmed_asks": [0.48, 0.47],
            "confirmed_sizes": [100.0, 100.0],
            "visible_common_shares": 100.0,
            "max_visible_notional_usd": 96.0,
        },
    )


def test_certified_rest_confirmed_structural_signal_is_trade_ready():
    s = _signal()
    assert mark_trade_readiness(s) is True
    assert is_trade_ready(s) is True
    assert s.metadata["trade_ready_version"] == TRADE_READY_VERSION


def test_watch_is_never_trade_ready():
    s = _signal(confidence="WATCH")
    assert mark_trade_readiness(s) is False
    assert is_trade_ready(s) is False


def test_unpromoted_sports_actionable_stays_silent():
    s = _signal(detector="sports_result_lag", cert="")
    s.token_ids = ["winner"]
    s.metadata["confirmed_asks"] = [0.90]
    s.metadata["confirmed_sizes"] = [100.0]
    s.entry_cost = 0.905
    s.edge = 0.095
    assert mark_trade_readiness(s) is False
    assert is_trade_ready(s) is False
    assert "not yet promoted" in s.metadata["trade_ready_reason"]


def test_missing_rest_confirmation_is_silent():
    s = _signal()
    s.metadata.pop("rest_confirmed_at")
    assert mark_trade_readiness(s) is False
    assert is_trade_ready(s) is False


def test_insufficient_visible_capacity_is_silent():
    s = _signal()
    s.metadata["max_visible_notional_usd"] = 2.0
    assert mark_trade_readiness(s) is False
    assert is_trade_ready(s) is False
