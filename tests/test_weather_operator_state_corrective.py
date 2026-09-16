from __future__ import annotations

import asyncio
import json

import pytest

from polymarket_scanner.weather_only_live_paper_all_signals_final_v5 import (
    FinalAllPaperWeatherLiveServiceV5,
)
from polymarket_scanner.weather_only_live_paper_all_signals_final_v7 import (
    assert_attested_all_paper_configuration,
)
from polymarket_scanner.weather_only_operator_state_corrective import (
    OPERATOR_SYNC_APPLIED,
    OPERATOR_SYNC_PENDING,
    OperatorStateTelegram,
)
from polymarket_scanner.weather_only_operator_state_corrective_v4 import (
    OperatorStatePostReceiptStoreV4,
)
from polymarket_scanner.weather_only_paper_positions import WeatherPaperPositionError
from polymarket_scanner.weather_only_paper_post_receipt import PAPER_EXECUTION_PROTOCOL_V5

NOW = 1_900_000_000.0


def _save_signal(store: OperatorStatePostReceiptStoreV4, name: str, *, lane: str = "weather_same_day_friend_lock", fingerprint: str | None = None) -> tuple[int, str]:
    fp = fingerprint or f"operator-fp-{name}"
    payload = {
        "paper_execution_protocol_version": PAPER_EXECUTION_PROTOCOL_V5,
        "decision_id": f"decision-{name}",
        "decision_expires_at": NOW + 300.0,
        "event_title": f"Event {name}",
        "financial_authority": False,
        "automatic_order_placement": False,
    }
    if lane == "weather_official_extreme_new_exclusion":
        payload.update(
            {
                "previous_official_extreme": 90.0,
                "new_official_extreme": 91.0,
                "latest_official_observed_at": NOW - 5.0,
            }
        )
    sid = store.save_signal(
        fingerprint=fp,
        lane=lane,
        evidence_class="TEST_OPERATOR_STATE",
        event_id=f"event-{name}",
        market_id=f"market-{name}",
        side="NO" if lane == "weather_official_extreme_new_exclusion" else "YES",
        token_id=f"token-{name}",
        model_probability=0.96,
        entry_cost=0.91,
        raw_gap=0.05,
        theoretical_payout=1.0,
        created_at=NOW - 1.0,
        payload=payload,
    )
    assert sid is not None
    with store._conn() as db:
        actual_fp = str(
            db.execute("SELECT fingerprint FROM weather_paper_signals WHERE id=?", (sid,)).fetchone()[0]
        )
    store.set_signal_status(sid, "PENDING_DELIVERY")
    store.mark_telegram_sent(sid, 7000 + sid, sent_at=NOW)
    store.set_signal_status(sid, "POST_RECEIPT_RECHECK")
    return sid, actual_fp


def _status(store: OperatorStatePostReceiptStoreV4, sid: int) -> str:
    with store._conn() as db:
        return str(db.execute("SELECT status FROM weather_paper_signals WHERE id=?", (sid,)).fetchone()[0])


def _invalidate(store: OperatorStatePostReceiptStoreV4, sid: int, name: str, *, lane: str = "weather_same_day_friend_lock") -> None:
    store.mark_post_receipt_not_actionable(
        sid,
        decision_id=f"decision-{name}",
        event_id=f"event-{name}",
        market_id=f"market-{name}",
        side="NO" if lane == "weather_official_extreme_new_exclusion" else "YES",
        reason="POST_RECEIPT_TEST_INVALIDATED",
        recorded_at=NOW + 1.0,
    )


def test_terminal_identity_and_prestate_fail_closed_without_mutation(tmp_path):
    store = OperatorStatePostReceiptStoreV4(tmp_path / "paper.sqlite")
    sid, _ = _save_signal(store, "identity")

    with pytest.raises(WeatherPaperPositionError, match="V5_TERMINAL_EVENT_IDENTITY_MISMATCH"):
        store.mark_post_receipt_not_actionable(
            sid,
            decision_id="decision-identity",
            event_id="wrong-event",
            market_id="market-identity",
            side="YES",
            reason="BAD_CALLER",
        )
    assert _status(store, sid) == "POST_RECEIPT_RECHECK"

    store.set_signal_status(sid, "PENDING_DELIVERY")
    with pytest.raises(WeatherPaperPositionError, match="V5_TERMINAL_PRESTATE_INVALID"):
        _invalidate(store, sid, "identity")
    assert _status(store, sid) == "PENDING_DELIVERY"
    with store._conn() as db:
        assert db.execute(
            "SELECT COUNT(*) FROM weather_paper_decisions WHERE decision_id='decision-identity'"
        ).fetchone()[0] == 0


def test_visible_invalidation_releases_retry_without_mutating_original_fingerprint(tmp_path):
    store = OperatorStatePostReceiptStoreV4(tmp_path / "paper.sqlite")
    sid, base = _save_signal(store, "retry")
    _invalidate(store, sid, "retry")

    pending = store.pending_operator_sync()
    assert len(pending) == 1
    assert pending[0]["state"] == OPERATOR_SYNC_PENDING
    assert "INVALIDATED" in pending[0]["message_text"]
    with store._conn() as db:
        assert db.execute(
            "SELECT fingerprint FROM weather_paper_signals WHERE id=?", (sid,)
        ).fetchone()[0] == base

    # Before the visible edit is confirmed, identical evidence is still deduped.
    assert store.save_signal(
        fingerprint=base,
        lane="weather_same_day_friend_lock",
        evidence_class="TEST_OPERATOR_STATE",
        event_id="event-retry",
        market_id="market-retry",
        side="YES",
        token_id="token-retry",
        model_probability=0.96,
        entry_cost=0.91,
        raw_gap=0.05,
        theoretical_payout=1.0,
        payload={
            "paper_execution_protocol_version": PAPER_EXECUTION_PROTOCOL_V5,
            "decision_id": "decision-retry-duplicate",
            "decision_expires_at": NOW + 600.0,
        },
    ) is None

    store.mark_operator_sync_applied(sid)
    with store._conn() as db:
        sync = db.execute(
            "SELECT state,fingerprint_released FROM weather_paper_operator_sync WHERE signal_id=?",
            (sid,),
        ).fetchone()
        assert tuple(sync) == (OPERATOR_SYNC_APPLIED, 1)
        # The original evidence identity is immutable. V3 must never tombstone it.
        assert str(
            db.execute("SELECT fingerprint FROM weather_paper_signals WHERE id=?", (sid,)).fetchone()[0]
        ) == base
        guard = db.execute(
            "SELECT invalidations,next_retry_at FROM weather_paper_retry_guard WHERE base_fingerprint=?",
            (base,),
        ).fetchone()
        assert int(guard[0]) == 1
        assert float(guard[1]) > 0.0

    # Cooldown blocks the derived retry identity immediately after invalidation.
    assert store.save_signal(
        fingerprint=base,
        lane="weather_same_day_friend_lock",
        evidence_class="TEST_OPERATOR_STATE",
        event_id="event-retry",
        market_id="market-retry",
        side="YES",
        token_id="token-retry",
        model_probability=0.96,
        entry_cost=0.91,
        raw_gap=0.05,
        theoretical_payout=1.0,
        payload={
            "paper_execution_protocol_version": PAPER_EXECUTION_PROTOCOL_V5,
            "decision_id": "decision-retry-later",
            "decision_expires_at": NOW + 600.0,
        },
    ) is None

    # Once the cooldown is explicitly expired, the retry is stored under a derived
    # identity while the original row remains untouched and auditably attributable.
    with store._conn() as db:
        db.execute(
            "UPDATE weather_paper_retry_guard SET next_retry_at=0 WHERE base_fingerprint=?",
            (base,),
        )
    retry_id = store.save_signal(
        fingerprint=base,
        lane="weather_same_day_friend_lock",
        evidence_class="TEST_OPERATOR_STATE",
        event_id="event-retry",
        market_id="market-retry",
        side="YES",
        token_id="token-retry",
        model_probability=0.96,
        entry_cost=0.91,
        raw_gap=0.05,
        theoretical_payout=1.0,
        payload={
            "paper_execution_protocol_version": PAPER_EXECUTION_PROTOCOL_V5,
            "decision_id": "decision-retry-after-cooldown",
            "decision_expires_at": NOW + 600.0,
        },
    )
    assert retry_id is not None and retry_id != sid
    with store._conn() as db:
        original = db.execute(
            "SELECT fingerprint FROM weather_paper_signals WHERE id=?", (sid,)
        ).fetchone()
        retry = db.execute(
            "SELECT fingerprint,payload_json FROM weather_paper_signals WHERE id=?", (retry_id,)
        ).fetchone()
    assert str(original[0]) == base
    assert str(retry[0]).startswith("retry:")
    assert str(retry[0]) != base
    retry_payload = json.loads(str(retry[1]))
    assert retry_payload["operator_retry_base_fingerprint"] == base
    assert retry_payload["operator_retry_generation"] == 2


def test_source_shock_retry_guard_uses_final_episode_fingerprint(tmp_path):
    store = OperatorStatePostReceiptStoreV4(tmp_path / "paper.sqlite")
    lane = "weather_official_extreme_new_exclusion"
    sid, final_fp = _save_signal(store, "shock", lane=lane, fingerprint="caller-fingerprint-a")
    assert final_fp != "caller-fingerprint-a"
    _invalidate(store, sid, "shock", lane=lane)
    store.mark_operator_sync_applied(sid)

    duplicate = store.save_signal(
        fingerprint="caller-fingerprint-b",
        lane=lane,
        evidence_class="TEST_OPERATOR_STATE",
        event_id="event-shock",
        market_id="market-shock",
        side="NO",
        token_id="token-shock",
        model_probability=0.96,
        entry_cost=0.91,
        raw_gap=0.05,
        theoretical_payout=1.0,
        payload={
            "paper_execution_protocol_version": PAPER_EXECUTION_PROTOCOL_V5,
            "decision_id": "decision-shock-2",
            "decision_expires_at": NOW + 600.0,
            "previous_official_extreme": 90.0,
            "new_official_extreme": 91.0,
            "latest_official_observed_at": NOW - 5.0,
        },
    )
    assert duplicate is None
    with store._conn() as db:
        assert db.execute(
            "SELECT COUNT(*) FROM weather_paper_retry_guard WHERE base_fingerprint=?",
            (final_fp,),
        ).fetchone()[0] == 1
        # Source-shock original episode evidence is immutable too.
        assert db.execute(
            "SELECT fingerprint FROM weather_paper_signals WHERE id=?", (sid,)
        ).fetchone()[0] == final_fp


def test_edit_message_not_modified_is_idempotent_success():
    class Response:
        status_code = 400
        def json(self):
            return {"ok": False, "description": "Bad Request: message is not modified"}

    class HTTP:
        async def post(self, *args, **kwargs):
            return Response()
        async def aclose(self):
            return None

    async def scenario() -> None:
        telegram = OperatorStateTelegram(token="test", chat_id="123")
        await telegram.http.aclose()
        telegram.http = HTTP()
        await telegram.edit_html(1234, "same text")
        await telegram.close()

    asyncio.run(scenario())


def test_maker_message_truthfully_disables_fill_simulation():
    text = FinalAllPaperWeatherLiveServiceV5._maker_proposal_message(
        {
            "event_title": "Test weather event",
            "side": "YES",
            "bid_price": 0.8,
            "current_best_bid": 0.79,
            "current_best_ask": 0.82,
            "max_notional": 10.0,
            "pre_delivery_queue_at_bid": 100.0,
            "conditional_edge_per_share": 0.03,
            "fair_value_research": {"raw_probability": 0.9},
        }
    )
    assert "QUEUE UNCERTIFIED" in text
    assert "does NOT convert" in text
    assert "simulated maker fills or maker P&amp;L" in text
    assert "create simulated shares" not in text


def test_config_guard_requires_explicit_dotenv_disable_and_default_nontelegram_settings(monkeypatch):
    monkeypatch.delenv("ALPHA_DISABLE_DOTENV", raising=False)
    with pytest.raises(RuntimeError, match="ALL_PAPER_DOTENV_DISABLE_NOT_ASSERTED"):
        assert_attested_all_paper_configuration()

    monkeypatch.setenv("ALPHA_DISABLE_DOTENV", "1")
    assert_attested_all_paper_configuration()

    from polymarket_scanner.weather_only_live_paper_all_signals_final_v7 import settings
    monkeypatch.setattr(settings, "request_timeout", settings.request_timeout + 1.0)
    with pytest.raises(RuntimeError, match="ALL_PAPER_IMPLICIT_SETTINGS_OVERRIDE:request_timeout"):
        assert_attested_all_paper_configuration()
