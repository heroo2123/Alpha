import asyncio
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

import polymarket_scanner.trade_only as trade_only
from polymarket_scanner.config import settings
from polymarket_scanner.execution_certificate import (
    EXECUTION_CERTIFICATE_TTL_SECONDS,
    EXECUTION_CERTIFICATE_VERSION,
    build_execution_certificate,
)
from polymarket_scanner.models import Book, Signal
from polymarket_scanner.trade_only import mark_trade_readiness, send_trade_now
from polymarket_scanner.weather_calibration import WEATHER_CALIBRATION_VERSION


def _binary_leg(token: str, outcome: str) -> dict:
    return {
        "market_id": "m1",
        "condition_id": "c1",
        "token_id": token,
        "question": "Test binary market?",
        "outcome": outcome,
        "ask": "0.45",
        "ask_fee_per_share": "0",
        "ask_cost_per_share": "0.45",
        "safe_limit": "0.47",
        # Deliberately malicious presentation field. send_trade_now must ignore it.
        "safe_limit_text": "0.99",
        "tick_size": "0.01",
        "minimum_order_size": "5",
        "visible_best_ask_size": "60",
        "visible_depth_to_limit": "100",
        "safe_depth_to_limit": "50.00",
        "depth_levels_to_limit": [
            {
                "price": "0.45",
                "visible_size": "60",
                "safe_size": "30.00",
                "cumulative_visible_size": "60",
                "cumulative_safe_size": "30.00",
            },
            {
                "price": "0.47",
                "visible_size": "40",
                "safe_size": "20.00",
                "cumulative_visible_size": "100",
                "cumulative_safe_size": "50.00",
            },
        ],
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


def _binary_signal() -> Signal:
    checked = datetime.now(timezone.utc)
    floor = Decimal(str(settings.actionable_min_edge))
    certificate = {
        "version": EXECUTION_CERTIFICATE_VERSION,
        "checked_at": checked.isoformat(),
        "expires_at": (checked + timedelta(seconds=EXECUTION_CERTIFICATE_TTL_SECONDS)).isoformat(),
        "ttl_seconds": EXECUTION_CERTIFICATE_TTL_SECONDS,
        "legs": [_binary_leg("yes", "Yes"), _binary_leg("no", "No")],
        "payout_reference": "1",
        "configured_edge_floor": str(floor),
        "max_bundle_cost": str(Decimal("1") - floor),
        "combined_top_cost": "0.90",
        "combined_cost": "0.94",
        "common_visible_shares": "100",
        "capacity_fraction": "0.50",
        "safe_common_shares": "50.00",
        "capacity_usd": "47.0000",
        "minimum_bundle_shares": "5",
        "minimum_bundle_notional_usd": "4.70",
        "depth_basis": "test",
        "fee_basis": "test",
        "execution_atomicity": "MANUAL_MULTI_LEG_NON_ATOMIC",
        "partial_fill_policy": "CERTIFICATE_INVALID_AFTER_ANY_PARTIAL_EXECUTION; no unwind economics certified",
    }
    return Signal(
        detector="binary_buy_both",
        confidence="ACTIONABLE",
        event_id="e1",
        market_id="m1",
        title="Binary complete-set test",
        detail="test",
        url="https://example.com",
        edge=0.01,
        entry_cost=0.99,
        theoretical_payout=1.0,
        token_ids=["yes", "no"],
        metadata={
            "certification_status": "BINARY_COMPLEMENT_VERIFIED",
            "execution_certificate": certificate,
        },
    )


class FakeTelegram:
    def __init__(self):
        self.text = None
        self.buttons = None

    def _buttons(self, _signal):
        return {"inline_keyboard": []}

    async def send_alert(self, text, buttons):
        self.text = text
        self.buttons = buttons


def test_trade_message_uses_validated_numeric_limit_not_untrusted_display_text(monkeypatch):
    monkeypatch.setitem(
        trade_only._CERTIFICATIONS,
        "binary_buy_both",
        "BINARY_COMPLEMENT_VERIFIED",
    )
    signal = _binary_signal()
    assert mark_trade_readiness(signal) is True

    tg = FakeTelegram()
    asyncio.run(send_trade_now(tg, 42, signal))
    assert tg.text is not None
    assert "Current combined ask cost: <b>0.9000</b>" in tg.text
    assert "Maximum certified combined cost: <b>0.9400</b>" in tg.text
    assert "current ask <b>0.45</b> | MAX <b>0.47</b>" in tg.text
    assert "Safe depth to MAX: 50.00 shares" in tg.text
    assert "0.99" not in tg.text
    assert "non-atomic" in tg.text
    assert "certificate no longer applies" in tg.text


def test_weather_depth_failure_clears_detector_time_economics_and_records_probability_basis():
    signal = Signal(
        detector="weather_late_lock",
        confidence="ACTIONABLE",
        event_id="e1",
        market_id="m1",
        title="weather",
        detail="weather",
        url="https://example.com",
        edge=0.074,
        entry_cost=0.925,
        theoretical_payout=1.0,
        token_ids=["yes-token"],
        metadata={
            "certification_status": "WEATHER_SEMANTICS_VERIFIED",
            "weather_calibration_version": WEATHER_CALIBRATION_VERSION,
            "weather_calibration_ready": True,
            "calibrated_probability_lower_bound": 0.94,
            "confirmed_asks": [0.80],
            "confirmed_sizes": [999.0],
            "visible_common_shares": 999.0,
            "safe_common_shares": 999.0,
            "max_visible_notional_usd": 999.0,
            "rest_confirmed_at": "stale",
        },
    )

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "mts": "0.001",
                "mos": "5",
                "t": [
                    {"t": "yes-token", "o": "Yes"},
                    {"t": "no-token", "o": "No"},
                ],
                "fd": {"r": 0, "e": 0, "to": True},
                "itode": False,
            }

    class FakeHTTP:
        async def get(self, _url):
            return FakeResponse()

    class FakePoly:
        def __init__(self):
            self.http = FakeHTTP()

        async def books(self, _tokens):
            return {"yes-token": Book("yes-token", bids=[], asks=[(0.925, 100.0)])}

    raw_market = {
        "id": "m1",
        "conditionId": "c1",
        "question": "Will today's high be in this bucket?",
        "slug": "weather-test",
        "clobTokenIds": '["yes-token", "no-token"]',
        "outcomes": '["Yes", "No"]',
    }

    with pytest.raises(ValueError, match="top-of-book limit cost is already above the edge floor"):
        asyncio.run(build_execution_certificate(signal, FakePoly(), [raw_market]))

    assert signal.edge is None
    assert signal.entry_cost is None
    for key in (
        "confirmed_asks",
        "confirmed_sizes",
        "visible_common_shares",
        "safe_common_shares",
        "max_visible_notional_usd",
        "rest_confirmed_at",
    ):
        assert key not in signal.metadata
    assert signal.metadata["trade_probability_basis"] == "WEATHER_EMPIRICAL_LOWER_BOUND"
    assert signal.metadata["trade_probability"] == 0.94
