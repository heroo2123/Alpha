from __future__ import annotations

import pytest

from polymarket_scanner.weather_only_live_paper_all_signals_final_v6 import (
    FinalAllPaperWeatherLiveServiceV6,
)
from polymarket_scanner.weather_only_operator_state_corrective import (
    OPERATOR_SYNC_APPLIED,
    OPERATOR_SYNC_PENDING,
    OperatorStateCommandController,
    OperatorStatePostReceiptStore,
    OperatorStateTelegram,
)
from polymarket_scanner.weather_only_operator_state_corrective_v2 import (
    OperatorStatePostReceiptStoreV2,
)
from polymarket_scanner.weather_only_paper_positions import WeatherPaperPositionError
from polymarket_scanner.weather_only_paper_post_receipt import PAPER_EXECUTION_PROTOCOL_V5


NOW = 1_900_000_000.0


def _payload(decision_id: str = "decision-1") -> dict:
    return {
        "paper_execution_protocol_version": PAPER_EXECUTION_PROTOCOL_V5,
        "decision_id": decision_id,
        "decision_expires_at": NOW + 300.0,
        "event_title": "Test weather market",
    }


def _delivered(store, *, fingerprint: str = "fp-1", lane: str = "weather_forecast_raw_gap") -> int:
    sid = store.save_signal(
        fingerprint=fingerprint,
        lane=lane,
        evidence_class="TEST",
        event_id="event-1",
        market_id="market-1",
        side="YES",
        token_id="token-1",
        model_probability=0.8,
        entry_cost=0.6,
        raw_gap=0.2,
        theoretical_payout=1.0,
        created_at=NOW - 1.0,
        payload=_payload(),
    )
    assert sid is not None
    store.set_signal_status(sid, "PENDING_DELIVERY")
    store.mark_telegram_sent(sid, 777, sent_at=NOW)
    store.set_signal_status(sid, "POST_RECEIPT_RECHECK")
    return sid


def test_terminal_invalidation_is_visible_before_dedupe_is_released(tmp_path):
    store = OperatorStatePostReceiptStore(tmp_path / "paper.sqlite")
    sid = _delivered(store)
    store.mark_post_receipt_not_actionable(
        sid,
        decision_id="decision-1",
        event_id="event-1",
        market_id="market-1",
        side="YES",
        reason="FINAL_QUOTE_MOVED",
        recorded_at=NOW + 1.0,
    )

    pending = store.pending_operator_sync()
    assert len(pending) == 1
    assert pending[0]["state"] == OPERATOR_SYNC_PENDING
    assert "INVALIDATED" in pending[0]["message_text"]
    assert "DO NOT ACT" in pending[0]["message_text"]

    assert store.save_signal(
        fingerprint="fp-1",
        lane="weather_forecast_raw_gap",
        evidence_class="TEST",
        event_id="event-1",
        market_id="market-1",
        side="YES",
        token_id="token-1",
        model_probability=0.8,
        entry_cost=0.6,
        raw_gap=0.2,
        theoretical_payout=1.0,
        payload=_payload("decision-2"),
    ) is None

    store.mark_operator_sync_applied(sid)
    with store._conn() as db:
        signal = db.execute(
            "SELECT fingerprint FROM weather_paper_signals WHERE id=?", (sid,)
        ).fetchone()
        sync = db.execute(
            "SELECT state,fingerprint_released FROM weather_paper_operator_sync WHERE signal_id=?",
            (sid,),
        ).fetchone()
        guard = db.execute(
            "SELECT invalidations,next_retry_at FROM weather_paper_retry_guard WHERE base_fingerprint='fp-1'"
        ).fetchone()
    assert str(signal["fingerprint"]).startswith("terminal:")
    assert sync["state"] == OPERATOR_SYNC_APPLIED
    assert sync["fingerprint_released"] == 1
    assert guard["invalidations"] == 1
    assert float(guard["next_retry_at"]) > 0.0

    assert store.save_signal(
        fingerprint="fp-1",
        lane="weather_forecast_raw_gap",
        evidence_class="TEST",
        event_id="event-1",
        market_id="market-1",
        side="YES",
        token_id="token-1",
        model_probability=0.8,
        entry_cost=0.6,
        raw_gap=0.2,
        theoretical_payout=1.0,
        payload=_payload("decision-2"),
    ) is None

    with store._conn() as db:
        db.execute(
            "UPDATE weather_paper_retry_guard SET next_retry_at=0 WHERE base_fingerprint='fp-1'"
        )
    retry = store.save_signal(
        fingerprint="fp-1",
        lane="weather_forecast_raw_gap",
        evidence_class="TEST",
        event_id="event-1",
        market_id="market-1",
        side="YES",
        token_id="token-1",
        model_probability=0.8,
        entry_cost=0.6,
        raw_gap=0.2,
        theoretical_payout=1.0,
        payload=_payload("decision-2"),
    )
    assert retry is not None
    assert retry != sid


def test_terminal_identity_mismatch_is_rejected_without_changing_signal(tmp_path):
    store = OperatorStatePostReceiptStore(tmp_path / "paper.sqlite")
    sid = _delivered(store)
    with pytest.raises(WeatherPaperPositionError) as exc:
        store.mark_post_receipt_not_actionable(
            sid,
            decision_id="decision-1",
            event_id="event-1",
            market_id="WRONG",
            side="YES",
            reason="TEST",
        )
    assert exc.value.code == "V5_TERMINAL_MARKET_IDENTITY_MISMATCH"
    with store._conn() as db:
        row = db.execute(
            "SELECT status FROM weather_paper_signals WHERE id=?", (sid,)
        ).fetchone()
        count = db.execute(
            "SELECT COUNT(*) FROM weather_paper_operator_sync"
        ).fetchone()[0]
    assert row["status"] == "POST_RECEIPT_RECHECK"
    assert count == 0


class _FakeMaker:
    def summary(self):
        return {"active_orders": 0}

    def orders(self):
        return []

    def active_orders(self):
        return []


class _FakeTelegram:
    token = "token"
    chat_id = "chat"


@pytest.mark.asyncio
async def test_recent_command_exposes_terminal_status_reason_and_unsynced_edit(tmp_path):
    store = OperatorStatePostReceiptStore(tmp_path / "paper.sqlite")
    sid = _delivered(store)
    store.mark_post_receipt_not_actionable(
        sid,
        decision_id="decision-1",
        event_id="event-1",
        market_id="market-1",
        side="YES",
        reason="POST_RECEIPT_QUOTE_GONE",
    )
    controller = OperatorStateCommandController(
        telegram=_FakeTelegram(),
        store=store,
        maker_store=_FakeMaker(),
        status_path=tmp_path / "status.json",
        paper_stake_usd=10.0,
    )
    try:
        text = controller._recent_text()
    finally:
        await controller.close()
    assert "INVALIDATED" in text
    assert "POST_RECEIPT_NOT_ACTIONABLE" in text
    assert "POST_RECEIPT_QUOTE_GONE" in text
    assert "Telegram invalidation is not yet confirmed" in text


class _Response:
    def __init__(self, status_code: int, body: dict):
        self.status_code = status_code
        self._body = body

    def json(self):
        return self._body


class _Http:
    def __init__(self, responses):
        self.responses = list(responses)

    async def post(self, *args, **kwargs):
        return self.responses.pop(0)

    async def aclose(self):
        return None


@pytest.mark.asyncio
async def test_edit_is_idempotent_when_telegram_reports_message_not_modified():
    telegram = OperatorStateTelegram(token="token", chat_id="chat")
    await telegram.http.aclose()
    telegram.http = _Http(
        [
            _Response(
                400,
                {
                    "ok": False,
                    "description": "Bad Request: message is not modified",
                },
            )
        ]
    )
    await telegram.edit_html(123, "⛔ INVALIDATED — DO NOT ACT")
    await telegram.close()


def test_source_shock_retry_guard_uses_final_episode_fingerprint(tmp_path):
    store = OperatorStatePostReceiptStoreV2(tmp_path / "paper.sqlite")
    payload = {
        **_payload(),
        "previous_official_extreme": 91.0,
        "new_official_extreme": 93.0,
        "latest_official_observed_at": NOW,
    }
    sid = store.save_signal(
        fingerprint="caller-placeholder",
        lane="weather_official_extreme_new_exclusion",
        evidence_class="TEST",
        event_id="event-1",
        market_id="market-1",
        side="NO",
        token_id="token-1",
        model_probability=1.0,
        entry_cost=0.8,
        raw_gap=0.2,
        theoretical_payout=1.0,
        payload=payload,
    )
    assert sid is not None
    final_fp = store._final_fingerprint(
        {
            "fingerprint": "caller-placeholder",
            "lane": "weather_official_extreme_new_exclusion",
            "event_id": "event-1",
            "market_id": "market-1",
            "side": "NO",
            "token_id": "token-1",
            "payload": payload,
        }
    )
    with store._conn() as db:
        row = db.execute(
            "SELECT fingerprint FROM weather_paper_signals WHERE id=?", (sid,)
        ).fetchone()
    assert row["fingerprint"] == final_fp


def test_final_runtime_rejects_implicit_dotenv_before_initializing_services(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text("REQUEST_TIMEOUT=1\n", encoding="utf-8")
    with pytest.raises(RuntimeError, match="ALL_PAPER_IMPLICIT_DOTENV_FORBIDDEN"):
        FinalAllPaperWeatherLiveServiceV6()


def test_final_maker_message_matches_queue_uncertified_policy():
    payload = {
        "event_title": "Maker test",
        "side": "YES",
        "bid_price": 0.40,
        "current_best_bid": 0.39,
        "current_best_ask": 0.42,
        "max_notional": 10.0,
        "pre_delivery_queue_at_bid": 25.0,
        "conditional_edge_per_share": 0.05,
        "fair_value_research": {"raw_probability": 0.55},
    }
    text = FinalAllPaperWeatherLiveServiceV6._maker_proposal_message(payload)
    assert "QUEUE UNCERTIFIED" in text
    assert "does NOT convert them into simulated maker fills" in text
    assert "No real order is placed" in text
