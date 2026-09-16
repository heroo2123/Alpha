from __future__ import annotations

import sqlite3

import pytest

from polymarket_scanner.weather_only_maker_paper_accounting_v5 import MakerPaperAccountingStoreV5
from polymarket_scanner.weather_only_maker_shadow import RESTING, VirtualMakerOrder
from polymarket_scanner.weather_only_paper_post_receipt import (
    PAPER_EXECUTION_PROTOCOL_V5,
    PostReceiptWeatherPaperStore,
)

NOW = 1_800_000_000.0


def _paper_signal(store: PostReceiptWeatherPaperStore) -> int:
    sid = store.save_signal(
        fingerprint="rollback-paper", lane="weather_forecast_raw_gap", evidence_class="TEST",
        event_id="event-paper", market_id="market-paper", side="YES", token_id="token-paper",
        model_probability=0.8, entry_cost=0.51, raw_gap=0.29, theoretical_payout=1.0,
        created_at=NOW,
        payload={
            "paper_execution_protocol_version": PAPER_EXECUTION_PROTOCOL_V5,
            "decision_id": "decision-paper", "decision_expires_at": NOW + 30.0,
            "financial_authority": False, "automatic_order_placement": False,
        },
    )
    assert sid is not None
    store.set_signal_status(sid, "PENDING_DELIVERY")
    store.mark_telegram_sent(sid, 101, sent_at=NOW)
    return sid


def test_paper_admission_rolls_back_every_local_effect_on_late_failure(tmp_path):
    store = PostReceiptWeatherPaperStore(tmp_path / "paper.sqlite")
    sid = _paper_signal(store)
    with store._conn() as db:
        db.executescript("""
            CREATE TRIGGER force_decision_failure
            BEFORE INSERT ON weather_paper_decisions
            BEGIN SELECT RAISE(ABORT,'forced decision failure'); END;
        """)
    execution = {
        "paper_execution_protocol_version": PAPER_EXECUTION_PROTOCOL_V5,
        "decision_id":"decision-paper", "decision_expires_at":NOW+30.0,
        "post_receipt_recheck_started_at":NOW+1.0,
        "post_receipt_recheck_finished_at":NOW+2.0,
        "entry_cost_per_unit":0.51, "visible_units":8.0, "minimum_order_size":1.0,
        "theoretical_payout_per_unit":1.0,
        "legs":[{"market_id":"market-paper","condition_id":"condition-paper",
                 "token_id":"token-paper","side":"YES","ask":0.50,"fee":0.01,
                 "quote_observed_at":NOW+1.9,"minimum_order_size":1.0,"book_hash":"hash-paper"}],
    }
    with pytest.raises(sqlite3.IntegrityError):
        store.admit_post_receipt_position(sid, 5.0, execution)
    with store._conn() as db:
        assert db.execute("SELECT COUNT(*) FROM weather_paper_positions").fetchone()[0] == 0
        assert db.execute("SELECT COUNT(*) FROM weather_paper_capacity_usage").fetchone()[0] == 0
        row = db.execute("SELECT status,payload_json FROM weather_paper_signals WHERE id=?", (sid,)).fetchone()
        assert row["status"] == "PENDING_DELIVERY"
        assert "post_receipt_execution" not in str(row["payload_json"])


def _maker_order() -> VirtualMakerOrder:
    return VirtualMakerOrder(
        version="test", order_id="maker-rb", policy_id="policy", event_id="event-maker",
        market_id="market-maker", condition_id="condition-maker", token_id="token-maker",
        outcome="YES", bid_price=0.40, shares=5.0, created_at=NOW+1.0, expires_at=NOW+300.0,
        fair_model_version="uncalibrated", fair_evidence_sha256="a"*64, fair_as_of=NOW,
        contract_evidence_sha256="b"*64, source_generation="forecast|ws_generation=1",
        market_parameter_sha256="c"*64, created_book_hash="d"*64,
        created_book_received_at=NOW+0.9, queue_ahead_shares=1.0,
        simulated_filled_shares=0.0, processed_trade_ids=(), status=RESTING,
    )


def test_maker_activation_rolls_back_order_events_and_signal_status_together(tmp_path):
    path = tmp_path / "maker.sqlite"
    signals = PostReceiptWeatherPaperStore(path)
    sid = signals.save_signal(
        fingerprint="maker-rb", lane="weather_maker_virtual_bid", evidence_class="TEST",
        event_id="event-maker", market_id="market-maker", side="YES", token_id="token-maker",
        model_probability=0.7, entry_cost=0.40, raw_gap=0.2, theoretical_payout=1.0,
        created_at=NOW, payload={"order_id":"maker-rb","token_id":"token-maker"},
    )
    assert sid is not None
    signals.set_signal_status(sid, "PENDING_DELIVERY")
    signals.mark_telegram_sent(sid, 202, sent_at=NOW)
    maker = MakerPaperAccountingStoreV5(path)
    maker.db.executescript("""
        CREATE TRIGGER force_link_failure
        BEFORE INSERT ON weather_maker_shadow_events
        WHEN NEW.event_type='TELEGRAM_SIGNAL_LINKED'
        BEGIN SELECT RAISE(ABORT,'forced link failure'); END;
    """)
    with pytest.raises(sqlite3.IntegrityError):
        maker.activate_after_telegram(
            _maker_order(), signal_id=sid, telegram_message_id=202, telegram_sent_at=NOW,
            post_delivery_exact_finished_at=NOW+1.0, recorded_at=NOW+1.0,
        )
    assert maker.db.execute("SELECT COUNT(*) FROM weather_maker_shadow_orders").fetchone()[0] == 0
    assert maker.db.execute("SELECT COUNT(*) FROM weather_maker_shadow_events").fetchone()[0] == 0
    with signals._conn() as db:
        assert db.execute("SELECT status FROM weather_paper_signals WHERE id=?", (sid,)).fetchone()[0] == "PENDING_DELIVERY"
