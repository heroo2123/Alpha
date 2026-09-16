from __future__ import annotations

import json
from pathlib import Path

import pytest

from polymarket_scanner.weather_only_paper_corrective import PAPER_EXECUTION_PROTOCOL_V4
from polymarket_scanner.weather_only_paper_recovery import CrashSafeWeatherPaperStore


def _payload(decision: str, *, fill_at: float = 101.0, expires_at: float = 120.0) -> dict:
    return {
        "event_title": "Expiry recovery fixture",
        "station": "KLGA",
        "target_date": "2026-09-20",
        "condition_id": "condition-1",
        "ask": 0.50,
        "fee": 0.0,
        "entry_cost": 0.50,
        "ask_size": 5.0,
        "quote_observed_at": 100.0,
        "paper_fill_at": fill_at,
        "decision_expires_at": expires_at,
        "book_hash": "book-expiry",
        "minimum_order_size": 1.0,
        "minimum_tick_size": 0.01,
        "decision_id": decision,
        "paper_target_stake_usd": 10.0,
        "paper_execution_protocol_version": PAPER_EXECUTION_PROTOCOL_V4,
    }


def _save(
    store: CrashSafeWeatherPaperStore,
    decision: str,
    *,
    fill_at: float = 101.0,
    expires_at: float = 120.0,
) -> int:
    sid = store.save_signal(
        fingerprint=f"fingerprint-{decision}",
        lane="weather_forecast_raw_gap",
        evidence_class="RESEARCH_ONLY_UNCALIBRATED_V4",
        event_id="event-expiry",
        market_id="market-expiry",
        side="YES",
        token_id="token-expiry",
        model_probability=0.8,
        entry_cost=0.5,
        raw_gap=0.3,
        theoretical_payout=1.0,
        created_at=100.5,
        payload=_payload(decision, fill_at=fill_at, expires_at=expires_at),
    )
    assert sid is not None
    return int(sid)


def _assert_uncertain_without_validated_position(
    store: CrashSafeWeatherPaperStore, signal_id: int
) -> None:
    with store._conn() as db:
        signal = db.execute(
            "SELECT status FROM weather_paper_signals WHERE id=?", (signal_id,)
        ).fetchone()
        positions = db.execute(
            "SELECT validation_state FROM weather_paper_positions WHERE signal_id=?",
            (signal_id,),
        ).fetchall()
        reservation = db.execute(
            "SELECT state FROM weather_paper_station_day_reservations WHERE signal_id=?",
            (signal_id,),
        ).fetchone()
    assert signal is not None
    assert signal["status"] == "PAPER_ACCOUNTING_UNCERTAIN"
    assert not any(row["validation_state"] == "VALIDATED" for row in positions)
    assert reservation is not None
    assert reservation["state"] == "UNCERTAIN"


def test_restart_never_promotes_receipt_exactly_at_expiry(tmp_path: Path):
    db_path = tmp_path / "receipt-equality.sqlite"
    store = CrashSafeWeatherPaperStore(db_path)
    sid = _save(store, "receipt-equality")
    store.set_signal_status(sid, "PENDING_DELIVERY")
    store.mark_telegram_sent(sid, 701, sent_at=120.0)

    restarted = CrashSafeWeatherPaperStore(db_path)
    recovery = restarted.reconcile_crash_states()

    assert recovery["acknowledged_fills_recovered"] == 0
    assert recovery["accounting_uncertain_recovered"] >= 1
    assert any("V4_DECISION_EXPIRED" in value for value in recovery["fill_errors"])
    _assert_uncertain_without_validated_position(restarted, sid)


def test_restart_never_promotes_simulated_fill_exactly_at_expiry(tmp_path: Path):
    db_path = tmp_path / "fill-equality.sqlite"
    store = CrashSafeWeatherPaperStore(db_path)
    sid = _save(store, "fill-equality", fill_at=120.0)
    store.set_signal_status(sid, "PENDING_DELIVERY")
    store.mark_telegram_sent(sid, 702, sent_at=119.0)

    restarted = CrashSafeWeatherPaperStore(db_path)
    recovery = restarted.reconcile_crash_states()

    assert recovery["acknowledged_fills_recovered"] == 0
    assert recovery["accounting_uncertain_recovered"] >= 1
    assert any("V4_DECISION_EXPIRED" in value for value in recovery["fill_errors"])
    _assert_uncertain_without_validated_position(restarted, sid)
