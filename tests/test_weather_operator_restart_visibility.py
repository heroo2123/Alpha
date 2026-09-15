from __future__ import annotations

import asyncio
from types import MethodType

import pytest

from polymarket_scanner.weather_only_independent_review_corrective_v3 import (
    IndependentReviewPostReceiptStoreV3,
)
from polymarket_scanner.weather_only_live_paper import WeatherLivePaperError
from polymarket_scanner.weather_only_live_paper_all_signals_final_v6 import (
    FinalAllPaperWeatherLiveServiceV6,
)
from polymarket_scanner.weather_only_live_paper_all_signals_final_v7 import (
    FinalAllPaperWeatherLiveServiceV7,
)
from polymarket_scanner.weather_only_operator_state_corrective import OPERATOR_SYNC_PENDING
from polymarket_scanner.weather_only_operator_state_corrective_v3 import (
    OperatorStatePostReceiptStoreV3,
)
from polymarket_scanner.weather_only_paper_post_receipt import PAPER_EXECUTION_PROTOCOL_V5

NOW = 1_900_000_000.0


def _delivered_recheck(store: OperatorStatePostReceiptStoreV3) -> int:
    sid = store.save_signal(
        fingerprint="restart-visible-fingerprint",
        lane="weather_same_day_friend_lock",
        evidence_class="TEST_RESTART_VISIBILITY",
        event_id="event-restart-visible",
        market_id="market-restart-visible",
        side="YES",
        token_id="token-restart-visible",
        model_probability=0.96,
        entry_cost=0.91,
        raw_gap=0.05,
        theoretical_payout=1.0,
        created_at=NOW - 5.0,
        payload={
            "paper_execution_protocol_version": PAPER_EXECUTION_PROTOCOL_V5,
            "decision_id": "decision-restart-visible",
            "decision_expires_at": NOW + 300.0,
            "event_title": "Restart-visible event",
            "financial_authority": False,
            "automatic_order_placement": False,
        },
    )
    assert sid is not None
    store.set_signal_status(sid, "PENDING_DELIVERY")
    store.mark_telegram_sent(sid, 8123, sent_at=NOW)
    store.set_signal_status(sid, "POST_RECEIPT_RECHECK")
    return sid


def test_v3_surfaces_alert_recovered_before_operator_generation_marker(tmp_path):
    store = OperatorStatePostReceiptStoreV3(tmp_path / "paper.sqlite")
    sid = _delivered_recheck(store)

    # Reproduce the first-deployment ordering: an older wrapper performs V5 restart
    # reconciliation before the operator-sync layer establishes/uses its generation
    # marker.  This converts the delivered signal to ACTIONABILITY_UNPROVEN without an
    # operator-sync row.
    legacy = IndependentReviewPostReceiptStoreV3.reconcile_v5_after_restart(store)
    assert legacy["actionability_unproven_after_restart"] == 1
    with store._conn() as db:
        status = db.execute(
            "SELECT status FROM weather_paper_signals WHERE id=?", (sid,)
        ).fetchone()[0]
        sync_count = db.execute(
            "SELECT COUNT(*) FROM weather_paper_operator_sync WHERE signal_id=?", (sid,)
        ).fetchone()[0]
    assert status == "ACTIONABILITY_UNPROVEN"
    assert sync_count == 0

    repaired = store.reconcile_v5_after_restart()
    assert repaired["operator_restart_unproven_candidates"] == 1
    assert repaired["operator_restart_sync_records_created"] == 1
    pending = store.pending_operator_sync()
    assert len(pending) == 1
    assert int(pending[0]["signal_id"]) == sid
    assert pending[0]["state"] == OPERATOR_SYNC_PENDING
    assert "INVALIDATED" in pending[0]["message_text"]


def test_final_startup_synchronizes_terminal_messages_before_online(monkeypatch):
    calls: list[str] = []
    service = object.__new__(FinalAllPaperWeatherLiveServiceV7)

    async def sync(_self):
        calls.append("sync")
        return {"healthy": True, "errors": []}

    async def parent_startup(_self):
        calls.append("startup")
        return 4321

    service._sync_operator_messages = MethodType(sync, service)
    monkeypatch.setattr(FinalAllPaperWeatherLiveServiceV6, "send_startup", parent_startup)

    message_id = asyncio.run(FinalAllPaperWeatherLiveServiceV7.send_startup(service))
    assert message_id == 4321
    assert calls == ["sync", "startup"]


def test_final_startup_fails_closed_when_terminal_edit_is_unconfirmed(monkeypatch):
    calls: list[str] = []
    service = object.__new__(FinalAllPaperWeatherLiveServiceV7)

    async def sync(_self):
        calls.append("sync")
        return {"healthy": False, "errors": ["OPERATOR_SYNC:1:TIMEOUT"]}

    async def parent_startup(_self):
        calls.append("startup")
        return 4321

    service._sync_operator_messages = MethodType(sync, service)
    monkeypatch.setattr(FinalAllPaperWeatherLiveServiceV6, "send_startup", parent_startup)

    with pytest.raises(WeatherLivePaperError) as exc:
        asyncio.run(FinalAllPaperWeatherLiveServiceV7.send_startup(service))
    assert exc.value.code == "ALL_PAPER_OPERATOR_SYNC_STARTUP_UNHEALTHY"
    assert calls == ["sync"]
