from datetime import datetime, timedelta, timezone
from decimal import Decimal

import polymarket_scanner.trade_only as trade_only
from polymarket_scanner.config import settings
from polymarket_scanner.execution_certificate import (
    EXECUTION_CERTIFICATE_TTL_SECONDS,
    EXECUTION_CERTIFICATE_VERSION,
)
from polymarket_scanner.models import Signal
from polymarket_scanner.trade_only import TRADE_READY_VERSION, is_trade_ready, mark_trade_readiness, promoted_detectors


def _execution_leg(token: str, outcome: str) -> dict:
    return {
        "market_id": "m1",
        "condition_id": "c1",
        "token_id": token,
        "question": "Test market?",
        "outcome": outcome,
        "ask": "0.47",
        "ask_fee_per_share": "0",
        "ask_cost_per_share": "0.47",
        "safe_limit": "0.47",
        "safe_limit_text": "0.47",
        "tick_size": "0.01",
        "minimum_order_size": "5",
        "visible_best_ask_size": "100",
        "visible_depth_to_limit": "100",
        "safe_depth_to_limit": "50.00",
        "depth_levels_to_limit": [{
            "price": "0.47",
            "visible_size": "100",
            "safe_size": "50.00",
            "cumulative_visible_size": "100",
            "cumulative_safe_size": "50.00",
        }],
        "book_timestamp": "",
        "fee_rate": "0",
        "fee_exponent": "0",
        "fee_taker_only": True,
        "raw_fee_per_share": "0",
        "fee_rounding_pad_per_share": "0",
        "fee_per_share": "0",
        "cost_per_share": "0.47",
        "url": "https://polymarket.com/market/test",
    }


def _execution_cert(*, checked_at=None, expires_at=None):
    checked = checked_at or datetime.now(timezone.utc)
    expires = expires_at or (checked + timedelta(seconds=EXECUTION_CERTIFICATE_TTL_SECONDS))
    floor = Decimal(str(settings.actionable_min_edge))
    return {
        "version": EXECUTION_CERTIFICATE_VERSION,
        "checked_at": checked.isoformat(),
        "expires_at": expires.isoformat(),
        "ttl_seconds": EXECUTION_CERTIFICATE_TTL_SECONDS,
        "legs": [
            _execution_leg("yes", "Yes"),
            _execution_leg("no", "No"),
        ],
        "payout_reference": "1",
        "configured_edge_floor": str(floor),
        "max_bundle_cost": str(Decimal("1") - floor),
        "combined_top_cost": "0.94",
        "combined_cost": "0.94",
        "common_visible_shares": "100",
        "capacity_fraction": "0.50",
        "safe_common_shares": "50.00",
        "capacity_usd": "47.0000",
        "minimum_bundle_shares": "5",
        "minimum_bundle_notional_usd": "4.70",
        "depth_basis": "CURRENT_BATCH_MULTI_LEVEL_ASK_DEPTH_WITH_50_PERCENT_PER_LEVEL_HAIRCUT_AND_WORST_CASE_LIMIT_COST",
        "fee_basis": "test",
        "execution_atomicity": "MANUAL_MULTI_LEG_NON_ATOMIC",
        "partial_fill_policy": "CERTIFICATE_INVALID_AFTER_ANY_PARTIAL_EXECUTION; no unwind economics certified",
    }


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
            "execution_certificate": _execution_cert(),
        },
    )


def _promote_binary(monkeypatch):
    monkeypatch.setitem(trade_only._CERTIFICATIONS, "binary_buy_both", "BINARY_COMPLEMENT_VERIFIED")


def test_p0_containment_promotes_no_detectors():
    assert promoted_detectors() == ()


def test_previous_structural_certificate_is_not_trade_ready_during_containment():
    s = _signal()
    assert mark_trade_readiness(s) is False
    assert is_trade_ready(s) is False
    assert s.metadata["trade_ready_version"] == TRADE_READY_VERSION
    assert "P0 containment" in s.metadata["trade_ready_reason"]


def test_watch_is_never_trade_ready():
    s = _signal(confidence="WATCH")
    assert mark_trade_readiness(s) is False
    assert is_trade_ready(s) is False


def test_unpromoted_sports_actionable_stays_silent():
    s = _signal(detector="sports_result_lag", cert="")
    s.token_ids = ["yes"]
    assert mark_trade_readiness(s) is False
    assert is_trade_ready(s) is False
    assert "not promoted" in s.metadata["trade_ready_reason"]


def test_old_trade_ready_metadata_cannot_bypass_empty_registry():
    s = _signal()
    s.metadata["trade_ready"] = True
    s.metadata["trade_ready_version"] = TRADE_READY_VERSION
    assert is_trade_ready(s) is False


def test_promoted_gate_accepts_only_fresh_exact_certificate(monkeypatch):
    _promote_binary(monkeypatch)
    s = _signal()
    assert mark_trade_readiness(s) is True
    assert is_trade_ready(s) is True
    assert s.entry_cost == 0.94
    assert abs(float(s.edge) - 0.06) < 1e-12
    assert s.metadata["safe_common_shares"] == 50.0


def test_promoted_gate_rejects_expired_execution_certificate(monkeypatch):
    _promote_binary(monkeypatch)
    s = _signal()
    checked = datetime.now(timezone.utc) - timedelta(seconds=30)
    s.metadata["execution_certificate"] = _execution_cert(
        checked_at=checked,
        expires_at=checked + timedelta(seconds=EXECUTION_CERTIFICATE_TTL_SECONDS),
    )
    assert mark_trade_readiness(s) is False
    assert is_trade_ready(s) is False
    assert "expired" in s.metadata["trade_ready_reason"]


def test_promoted_gate_rejects_nonfinite_certificate_values(monkeypatch):
    _promote_binary(monkeypatch)
    s = _signal()
    s.metadata["execution_certificate"]["legs"][0]["ask"] = "nan"
    assert mark_trade_readiness(s) is False
    assert is_trade_ready(s) is False
    assert "nonfinite" in s.metadata["trade_ready_reason"]


def test_promoted_gate_rejects_future_certificate(monkeypatch):
    _promote_binary(monkeypatch)
    s = _signal()
    checked = datetime.now(timezone.utc) + timedelta(minutes=5)
    s.metadata["execution_certificate"] = _execution_cert(
        checked_at=checked,
        expires_at=checked + timedelta(seconds=EXECUTION_CERTIFICATE_TTL_SECONDS),
    )
    assert mark_trade_readiness(s) is False
    assert is_trade_ready(s) is False
    assert "future" in s.metadata["trade_ready_reason"]


def test_promoted_gate_rejects_tampered_leg_fee(monkeypatch):
    _promote_binary(monkeypatch)
    s = _signal()
    s.metadata["execution_certificate"]["legs"][0]["fee_per_share"] = "0.01"
    assert mark_trade_readiness(s) is False
    assert "arithmetic" in s.metadata["trade_ready_reason"]
