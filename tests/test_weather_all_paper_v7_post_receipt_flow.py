from __future__ import annotations

import asyncio

from polymarket_scanner.weather_only_live_paper_all_signals_v7 import (
    AllPaperWeatherLiveV7Service,
)
from polymarket_scanner.weather_only_live_paper_v4 import V4InvariantError


class _Telegram:
    async def send_html(self, text, *, url=None, expires_at=None):
        return 321


class _Positions:
    def __init__(self):
        self.statuses = []
        self.receipts = []
        self.admissions = []
        self.signals = []

    def save_signal(self, **kwargs):
        self.signals.append(kwargs)
        return 7

    def set_signal_status(self, signal_id, status):
        self.statuses.append((signal_id, status))

    def mark_telegram_sent(self, signal_id, message_id, sent_at=None):
        self.receipts.append((signal_id, message_id, sent_at))

    def admit_post_receipt_position(self, signal_id, stake, execution):
        self.admissions.append((signal_id, stake, dict(execution)))
        return {"id": 9, "status": "OPEN"}

    def record_decision(self, **kwargs):
        pass


def _service():
    service = object.__new__(AllPaperWeatherLiveV7Service)
    service.positions = _Positions()
    service.telegram = _Telegram()
    service.paper_stake_usd = 10.0
    service._v4_dispatch_skipped_total = 0
    return service


def _candidate():
    return {
        "lane": "weather_forecast_raw_gap",
        "evidence_class": "TEST",
        "event_id": "event-1",
        "market_id": "market-1",
        "side": "YES",
        "token_id": "token-1",
        "raw_probability": 0.8,
        "entry_cost": 0.50,
        "raw_gap": 0.30,
        "decision_id": "decision-1",
        "decision_expires_at": 9_999_999_999.0,
        "forecast_evidence_sha256": "a" * 64,
        "semantic_digest": "b" * 64,
        "paper_fill_at": 100.0,
    }


def test_forecast_position_uses_post_receipt_execution_not_pre_send_quote(monkeypatch):
    service = _service()
    candidate = _candidate()

    async def dispatch(_candidate, _event):
        return dict(candidate)

    async def post(_candidate, _event, *, telegram_sent_at):
        assert telegram_sent_at > 0.0
        return {
            "paper_execution_protocol_version": "weather_paper_execution_v5_post_receipt_exact_clob",
            "decision_id": "decision-1",
            "decision_expires_at": candidate["decision_expires_at"],
            "post_receipt_recheck_started_at": telegram_sent_at + 0.01,
            "post_receipt_recheck_finished_at": telegram_sent_at + 0.02,
            "entry_cost_per_unit": 0.70,
            "visible_units": 4.0,
            "minimum_order_size": 1.0,
            "theoretical_payout_per_unit": 1.0,
            "legs": [{
                "market_id": "market-1", "condition_id": "condition-1",
                "token_id": "token-1", "side": "YES", "ask": 0.69, "fee": 0.01,
                "quote_observed_at": telegram_sent_at + 0.015,
                "minimum_order_size": 1.0, "book_hash": "post-receipt-hash",
            }],
        }

    service._dispatch_recheck = dispatch
    service._forecast_post_receipt_execution = post
    service._forecast_message_v4 = lambda value: "message"
    service._record_skip = lambda *args, **kwargs: None
    monkeypatch.setattr(
        "polymarket_scanner.weather_only_live_paper_all_signals_v7.time.time",
        lambda: 200.0,
    )

    created, error = asyncio.run(service._save_and_send_forecast(candidate, {}))
    assert created is True and error is None
    assert service.positions.signals[0]["entry_cost"] == 0.50
    assert service.positions.admissions[0][2]["entry_cost_per_unit"] == 0.70
    assert service.positions.admissions[0][2]["legs"][0]["book_hash"] == "post-receipt-hash"
    assert (7, "POST_RECEIPT_RECHECK") in service.positions.statuses


def test_forecast_post_receipt_loss_of_edge_never_creates_position(monkeypatch):
    service = _service()
    candidate = _candidate()

    async def dispatch(_candidate, _event):
        return dict(candidate)

    async def post(_candidate, _event, *, telegram_sent_at):
        return None

    service._dispatch_recheck = dispatch
    service._forecast_post_receipt_execution = post
    service._forecast_message_v4 = lambda value: "message"

    async def no_op(*args, **kwargs):
        return None

    service._record_skip = no_op
    monkeypatch.setattr(
        "polymarket_scanner.weather_only_live_paper_all_signals_v7.time.time",
        lambda: 200.0,
    )

    created, error = asyncio.run(service._save_and_send_forecast(candidate, {}))
    assert created is True and error is None
    assert service.positions.admissions == []
    assert (7, "POST_RECEIPT_NOT_ACTIONABLE") in service.positions.statuses


def test_forecast_post_receipt_invariant_failure_fails_closed(monkeypatch):
    service = _service()
    candidate = _candidate()

    async def dispatch(_candidate, _event):
        return dict(candidate)

    async def post(_candidate, _event, *, telegram_sent_at):
        raise V4InvariantError("V5_TOKEN_MEANING_CHANGED_AFTER_DELIVERY")

    service._dispatch_recheck = dispatch
    service._forecast_post_receipt_execution = post
    service._forecast_message_v4 = lambda value: "message"

    async def no_op(*args, **kwargs):
        return None

    service._record_skip = no_op
    monkeypatch.setattr(
        "polymarket_scanner.weather_only_live_paper_all_signals_v7.time.time",
        lambda: 200.0,
    )

    created, error = asyncio.run(service._save_and_send_forecast(candidate, {}))
    assert created is True
    assert error == "V5_TOKEN_MEANING_CHANGED_AFTER_DELIVERY"
    assert service.positions.admissions == []
    assert (7, "POST_RECEIPT_NOT_ACTIONABLE") in service.positions.statuses
