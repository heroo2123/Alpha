from __future__ import annotations

import asyncio
import json
from pathlib import Path

import httpx
import pytest

from polymarket_scanner.weather_only_discovery import (
    SEMANTIC_FAILURE_CATEGORIES,
    WeatherOnlyDiscovery,
)
from polymarket_scanner.weather_only_live_paper import WeatherLivePaperError
from polymarket_scanner.weather_only_live_paper_all_signals_final_v10 import (
    FinalAllPaperWeatherLiveServiceV10,
)
from polymarket_scanner.weather_only_live_paper_all_signals_final_v9 import (
    TELEGRAM_EDIT_ABSENT,
    TELEGRAM_EDIT_APPLIED,
    FinalOperatorStateTelegram,
)
from polymarket_scanner.weather_only_maker_paper_accounting import MakerPaperAccountingStore
from polymarket_scanner.weather_only_maker_paper_accounting_v6 import (
    LEGACY_QUEUE_UNCERTIFIED,
    MakerPaperAccountingStoreV6,
)
from polymarket_scanner.weather_only_maker_shadow import (
    CANCELLED,
    WEATHER_MAKER_SHADOW_VERSION,
    VirtualMakerOrder,
)
from polymarket_scanner.weather_only_operator_commands_v10 import (
    OperatorStateCommandControllerV10,
)
from polymarket_scanner.weather_only_operator_state_corrective import (
    OPERATOR_SYNC_APPLIED,
    OPERATOR_SYNC_FAILED,
    OPERATOR_SYNC_PENDING,
    RETRYABLE_AFTER_VISIBLE_INVALIDATION,
)
from polymarket_scanner.weather_only_operator_state_corrective_v5 import (
    OperatorStatePostReceiptStoreV5,
)
from polymarket_scanner.weather_only_paper_post_receipt import PAPER_EXECUTION_PROTOCOL_V5


TERMINAL_STATUSES = (
    "POST_RECEIPT_NOT_ACTIONABLE",
    "ACTIONABILITY_UNPROVEN",
    "PAPER_ACCOUNTING_ERROR",
    "EXPIRED",
    "MAKER_NOT_ACTIVATED",
    "MAKER_NOT_ACTIVATED_RESTART_COVERAGE_LOST",
)


class _EditTelegram:
    def __init__(self, outcome: str = TELEGRAM_EDIT_APPLIED, error: Exception | None = None):
        self.outcome = outcome
        self.error = error
        self.calls: list[tuple[int, str]] = []

    async def edit_html(self, message_id: int, text: str) -> str:
        self.calls.append((int(message_id), str(text)))
        if self.error is not None:
            raise self.error
        return self.outcome


class _Response:
    def __init__(self, status_code: int, payload: dict):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        return self._payload


class _LostThenNotModifiedHTTP:
    def __init__(self):
        self.calls = 0

    async def post(self, *_args, **_kwargs):
        self.calls += 1
        if self.calls == 1:
            raise httpx.ReadTimeout("ambiguous after remote receipt")
        return _Response(
            400,
            {"ok": False, "description": "Bad Request: message is not modified"},
        )


class _DeletedHTTP:
    async def post(self, *_args, **_kwargs):
        return _Response(
            400,
            {"ok": False, "description": "Bad Request: message to edit not found"},
        )


def _signal(store: OperatorStatePostReceiptStoreV5, *, suffix: str, protocol: bool = False) -> tuple[int, str, dict]:
    fingerprint = f"fp-{suffix}"
    payload = {
        "decision_id": f"decision-{suffix}",
        "event_title": f"Fixture {suffix}",
    }
    if protocol:
        payload["paper_execution_protocol_version"] = PAPER_EXECUTION_PROTOCOL_V5
    signal_id = store.save_signal(
        fingerprint=fingerprint,
        lane="fixture",
        evidence_class="FIXTURE",
        event_id=f"event-{suffix}",
        market_id=f"market-{suffix}",
        side="YES",
        token_id=f"token-{suffix}",
        entry_cost=0.40,
        raw_gap=0.10,
        theoretical_payout=1.0,
        created_at=100.0,
        payload=payload,
    )
    assert signal_id is not None
    store.mark_telegram_sent(int(signal_id), 1000 + int(signal_id), sent_at=101.0)
    store.set_signal_status(int(signal_id), "POST_RECEIPT_RECHECK")
    candidate = {
        **payload,
        "event_id": f"event-{suffix}",
        "market_id": f"market-{suffix}",
        "side": "YES",
    }
    return int(signal_id), fingerprint, candidate


def _service(store: OperatorStatePostReceiptStoreV5, telegram) -> FinalAllPaperWeatherLiveServiceV10:
    service = object.__new__(FinalAllPaperWeatherLiveServiceV10)
    service.positions = store
    service.telegram = telegram
    return service


def _row(store: OperatorStatePostReceiptStoreV5, signal_id: int) -> tuple[dict, dict | None, int]:
    with store._conn() as db:
        signal = dict(db.execute("SELECT * FROM weather_paper_signals WHERE id=?", (signal_id,)).fetchone())
        sync_raw = db.execute("SELECT * FROM weather_paper_operator_sync WHERE signal_id=?", (signal_id,)).fetchone()
        sync = None if sync_raw is None else dict(sync_raw)
        guards = int(db.execute("SELECT COUNT(*) FROM weather_paper_retry_guard").fetchone()[0])
    return signal, sync, guards


def test_unknown_weather_station_is_counted_but_never_promoted_when_gamma_is_complete():
    event = {
        "id": "unknown-1",
        "title": "Highest temperature in Unknownville on September 16?",
        "description": "Whole degrees Fahrenheit using the official local station.",
        "resolutionSource": "https://www.weather.gov/wrh/timeseries?site=KZZZ",
        "markets": [
            {"id": "m1", "question": "Will the highest temperature in Unknownville be 70°F or higher on September 16?"}
        ],
    }
    assert WeatherOnlyDiscovery._weather_looking_candidate(event) is True
    category, detail = WeatherOnlyDiscovery._semantic_classification(event)
    assert category in SEMANTIC_FAILURE_CATEGORIES
    assert category != "SUPPORTED"
    assert detail


@pytest.mark.parametrize("terminal_status", TERMINAL_STATUSES)
def test_every_delivered_terminal_status_attempts_visible_edit_before_return_and_preserves_identity(
    tmp_path: Path, terminal_status: str
):
    store = OperatorStatePostReceiptStoreV5(tmp_path / "paper.sqlite")
    signal_id, fingerprint, candidate = _signal(store, suffix=terminal_status.lower())
    telegram = _EditTelegram()
    service = _service(store, telegram)

    asyncio.run(
        service._terminalize_delivered_signal(
            signal_id,
            candidate,
            status=terminal_status,
            reason=f"fixture:{terminal_status}",
        )
    )

    signal, sync, guards = _row(store, signal_id)
    assert telegram.calls and telegram.calls[0][0] == signal["telegram_message_id"]
    assert signal["status"] == terminal_status
    assert signal["fingerprint"] == fingerprint
    assert sync is not None and sync["state"] == OPERATOR_SYNC_APPLIED
    if terminal_status in RETRYABLE_AFTER_VISIBLE_INVALIDATION:
        assert guards == 1
        assert int(sync["fingerprint_released"]) == 1
    else:
        assert guards == 0
        assert int(sync["fingerprint_released"]) == 0


def test_lost_edit_response_then_message_not_modified_is_idempotent_applied(monkeypatch, tmp_path: Path):
    async def _no_sleep(_seconds):
        return None

    monkeypatch.setattr(
        "polymarket_scanner.weather_only_live_paper_all_signals_final_v9.asyncio.sleep",
        _no_sleep,
    )
    telegram = object.__new__(FinalOperatorStateTelegram)
    telegram.token = "fixture"
    telegram.chat_id = "fixture"
    telegram.http = _LostThenNotModifiedHTTP()

    store = OperatorStatePostReceiptStoreV5(tmp_path / "paper.sqlite")
    signal_id, fingerprint, candidate = _signal(store, suffix="lost-response")
    service = _service(store, telegram)
    asyncio.run(
        service._terminalize_delivered_signal(
            signal_id,
            candidate,
            status="POST_RECEIPT_NOT_ACTIONABLE",
            reason="fixture-lost-response",
        )
    )
    signal, sync, guards = _row(store, signal_id)
    assert telegram.http.calls == 2
    assert signal["fingerprint"] == fingerprint
    assert sync is not None and sync["state"] == OPERATOR_SYNC_APPLIED
    assert guards == 1


def test_deleted_original_message_is_terminal_absence_and_releases_retry(tmp_path: Path):
    telegram = object.__new__(FinalOperatorStateTelegram)
    telegram.token = "fixture"
    telegram.chat_id = "fixture"
    telegram.http = _DeletedHTTP()
    store = OperatorStatePostReceiptStoreV5(tmp_path / "paper.sqlite")
    signal_id, fingerprint, candidate = _signal(store, suffix="deleted")
    service = _service(store, telegram)

    asyncio.run(
        service._terminalize_delivered_signal(
            signal_id,
            candidate,
            status="EXPIRED",
            reason="fixture-deleted",
        )
    )
    signal, sync, guards = _row(store, signal_id)
    assert signal["fingerprint"] == fingerprint
    assert sync is not None and sync["state"] == OPERATOR_SYNC_APPLIED
    assert guards == 1
    with store._conn() as db:
        absent = db.execute(
            "SELECT telegram_message_id FROM weather_paper_operator_absent WHERE signal_id=?",
            (signal_id,),
        ).fetchone()
    assert absent is not None


def test_explicit_edit_failure_is_durable_failed_and_does_not_release_retry(tmp_path: Path):
    store = OperatorStatePostReceiptStoreV5(tmp_path / "paper.sqlite")
    signal_id, fingerprint, candidate = _signal(store, suffix="edit-failed")
    telegram = _EditTelegram(error=WeatherLivePaperError("FIXTURE_EDIT_FAILED"))
    service = _service(store, telegram)

    with pytest.raises(WeatherLivePaperError, match="DELIVERED_TERMINAL_OPERATOR_SYNC_FAILED"):
        asyncio.run(
            service._terminalize_delivered_signal(
                signal_id,
                candidate,
                status="PAPER_ACCOUNTING_ERROR",
                reason="fixture-edit-failed",
            )
        )
    signal, sync, guards = _row(store, signal_id)
    assert telegram.calls
    assert signal["status"] == "PAPER_ACCOUNTING_ERROR"
    assert signal["fingerprint"] == fingerprint
    assert sync is not None and sync["state"] == OPERATOR_SYNC_FAILED
    assert int(sync["fingerprint_released"]) == 0
    assert guards == 0


def test_crash_after_atomic_terminal_commit_before_edit_is_repaired_on_restart(tmp_path: Path):
    db_path = tmp_path / "paper.sqlite"
    store = OperatorStatePostReceiptStoreV5(db_path)
    signal_id, fingerprint, candidate = _signal(store, suffix="crash-before-edit")
    store.terminalize_delivered_signal(
        signal_id,
        terminal_status="POST_RECEIPT_NOT_ACTIONABLE",
        reason="fixture-crash",
        decision_id=candidate["decision_id"],
        event_id=candidate["event_id"],
        market_id=candidate["market_id"],
        side=candidate["side"],
        recorded_at=102.0,
    )
    signal, sync, guards = _row(store, signal_id)
    assert signal["fingerprint"] == fingerprint
    assert sync is not None and sync["state"] == OPERATOR_SYNC_PENDING
    assert guards == 0

    restarted = OperatorStatePostReceiptStoreV5(db_path)
    telegram = _EditTelegram()
    service = _service(restarted, telegram)
    summary = asyncio.run(service._sync_operator_messages())
    signal, sync, guards = _row(restarted, signal_id)
    assert summary["healthy"] is True
    assert telegram.calls
    assert signal["fingerprint"] == fingerprint
    assert sync is not None and sync["state"] == OPERATOR_SYNC_APPLIED
    assert guards == 1


def test_v5_restart_reconciliation_uses_common_terminalization_without_rewriting_fingerprint(tmp_path: Path):
    store = OperatorStatePostReceiptStoreV5(tmp_path / "paper.sqlite")
    signal_id, fingerprint, _candidate = _signal(store, suffix="restart-v5", protocol=True)
    result = store.reconcile_v5_after_restart()
    signal, sync, guards = _row(store, signal_id)
    assert result["actionability_unproven_after_restart"] == 1
    assert signal["status"] == "ACTIONABILITY_UNPROVEN"
    assert signal["fingerprint"] == fingerprint
    assert sync is not None and sync["state"] == OPERATOR_SYNC_PENDING
    assert guards == 0


def _legacy_order() -> VirtualMakerOrder:
    return VirtualMakerOrder(
        version=WEATHER_MAKER_SHADOW_VERSION,
        order_id="legacy-maker-1",
        policy_id="legacy-policy",
        event_id="event-1",
        market_id="market-1",
        condition_id="condition-1",
        token_id="token-1",
        outcome="YES",
        bid_price=0.40,
        shares=10.0,
        created_at=100.0,
        expires_at=200.0,
        fair_model_version="legacy-model",
        fair_evidence_sha256="a" * 64,
        fair_as_of=99.0,
        contract_evidence_sha256="b" * 64,
        source_generation="legacy-public-trades",
        market_parameter_sha256="c" * 64,
        created_book_hash="book-1",
        created_book_received_at=99.5,
        queue_ahead_shares=0.0,
        simulated_filled_shares=10.0,
        processed_trade_ids=("trade-1",),
        status=CANCELLED,
    )


def test_real_old_maker_sqlite_migrates_non_destructively_and_excludes_legacy_pnl(tmp_path: Path):
    db_path = tmp_path / "maker.sqlite"
    old = MakerPaperAccountingStore(db_path)
    order = _legacy_order()
    old.save_new_order(order, recorded_at=100.0)
    before = old.record_settlement(
        order,
        payout_per_share=1.0,
        evidence={"source": "legacy-fixture"},
        settled_at=210.0,
    )
    assert before["simulated_capital_used"] == pytest.approx(4.0)
    assert before["paper_proceeds"] == pytest.approx(10.0)
    assert before["paper_pnl"] == pytest.approx(6.0)
    with old._db_lock:
        event_count_before = int(old.db.execute("SELECT COUNT(*) FROM weather_maker_shadow_events").fetchone()[0])
        order_count_before = int(old.db.execute("SELECT COUNT(*) FROM weather_maker_shadow_orders").fetchone()[0])
    old.close()

    migrated = MakerPaperAccountingStoreV6(db_path)
    assert migrated.evidence_class(order.order_id) == LEGACY_QUEUE_UNCERTIFIED
    after = migrated.settlement(order.order_id)
    assert after == before
    grouped = migrated.maker_performance_by_evidence()
    assert grouped["validated"]["capital"] == 0.0
    assert grouped["validated"]["proceeds"] == 0.0
    assert grouped["validated"]["pnl"] == 0.0
    assert grouped["validated"]["roi"] is None
    assert grouped["legacy_excluded"]["capital"] == pytest.approx(4.0)
    assert grouped["legacy_excluded"]["proceeds"] == pytest.approx(10.0)
    assert grouped["legacy_excluded"]["pnl"] == pytest.approx(6.0)
    with migrated._db_lock:
        assert int(migrated.db.execute("SELECT COUNT(*) FROM weather_maker_shadow_events").fetchone()[0]) == event_count_before
        assert int(migrated.db.execute("SELECT COUNT(*) FROM weather_maker_shadow_orders").fetchone()[0]) == order_count_before
        assert int(migrated.db.execute("SELECT COUNT(*) FROM weather_maker_evidence_classification").fetchone()[0]) == 1
    migrated.close()

    reopened = MakerPaperAccountingStoreV6(db_path)
    with reopened._db_lock:
        assert int(reopened.db.execute("SELECT COUNT(*) FROM weather_maker_evidence_classification").fetchone()[0]) == 1
        assert int(reopened.db.execute("SELECT COUNT(*) FROM weather_maker_shadow_events").fetchone()[0]) == event_count_before
    reopened.close()


def test_stats_and_settlement_message_label_legacy_maker_pnl_as_excluded(tmp_path: Path):
    db_path = tmp_path / "maker.sqlite"
    old = MakerPaperAccountingStore(db_path)
    order = _legacy_order()
    old.save_new_order(order, recorded_at=100.0)
    payload = old.record_settlement(
        order,
        payout_per_share=1.0,
        evidence={"source": "legacy-fixture"},
        settled_at=210.0,
    )
    old.close()
    maker = MakerPaperAccountingStoreV6(db_path)

    controller = object.__new__(OperatorStateCommandControllerV10)
    controller.maker_store = maker
    perf = controller._maker_performance()
    assert perf["pnl"] == 0.0
    assert perf["capital"] == 0.0
    assert perf["legacy_excluded"]["pnl"] == pytest.approx(6.0)

    service = object.__new__(FinalAllPaperWeatherLiveServiceV10)
    service.maker_store = maker
    message = service._maker_settlement_message(payload)
    assert "EXCLUDED FROM VALIDATED PERFORMANCE" in message
    assert LEGACY_QUEUE_UNCERTIFIED in message
    maker.close()


def test_effective_maker_restart_dispatch_terminalizes_and_edits_actual_receipt(tmp_path):
    store = OperatorStatePostReceiptStoreV5(tmp_path / "paper.sqlite")
    signal_id, fingerprint, _candidate = _signal(store, suffix="maker-restart")
    telegram = _EditTelegram()
    service = _service(store, telegram)
    service._v10_deferred_maker_restart_signal_ids = [signal_id]
    assert service._defer_maker_restart_terminalization() is True
    asyncio.run(service._terminalize_deferred_maker_restart_receipts())
    signal, sync, _guards = _row(store, signal_id)
    assert signal["status"] == "MAKER_NOT_ACTIVATED_RESTART_COVERAGE_LOST"
    assert signal["fingerprint"] == fingerprint
    assert sync["state"] == OPERATOR_SYNC_APPLIED
    assert [call[0] for call in telegram.calls] == [signal["telegram_message_id"]]
    assert service._v10_deferred_maker_restart_signal_ids == []


@pytest.mark.parametrize("expired,status", [(True, "EXPIRED"), (False, "MAKER_NOT_ACTIVATED")])
def test_effective_maker_sender_edits_confirmed_receipt_on_expiry_or_activation_failure(tmp_path, expired, status):
    from types import SimpleNamespace as NS
    import time

    store = OperatorStatePostReceiptStoreV5(tmp_path / "maker-receipt.sqlite")
    telegram = _EditTelegram()
    async def send(*_args, **_kwargs):
        return 4321
    async def noop(*_args, **_kwargs):
        return None
    async def failed_activation(**_kwargs):
        raise RuntimeError("fixture activation failed after acknowledged delivery")
    telegram.send_html = send
    service = _service(store, telegram)
    service.maker_stream = NS(subscribe=noop, start=noop, connected=True, coverage=lambda _token: NS(generation="g1"))
    service.maker_store = NS(active_orders=lambda: [])
    service.maker_policy = NS(max_active_orders=1)
    service._maker_proposals_sent = 0
    payload = {"fingerprint": "maker-fingerprint", "order_id": "maker-order", "event_id": "e1",
        "market_id": "m1", "side": "YES", "token_id": "t1", "fair_value_research": {"raw_member_frequency": .8},
        "bid_price": .4, "conditional_edge_per_share": .4, "decision_expires_at": time.time()+(-1 if expired else 30)}
    service._maker_signal_payload = lambda *_args, **_kwargs: dict(payload)
    service._maker_proposal_message = lambda _payload: "fixture message"
    service._activate_maker_after_delivery = failed_activation
    delivered, error = asyncio.run(service._send_maker_candidate({"proposal": NS(token_id="t1"), "event": {"slug": "fixture"}}))
    assert delivered is True and error
    signal, sync, _guards = _row(store, 1)
    assert signal["status"] == status
    assert signal["telegram_message_id"] == 4321
    assert signal["fingerprint"] == "maker-fingerprint"
    assert sync["state"] == OPERATOR_SYNC_APPLIED
    assert [call[0] for call in telegram.calls] == [4321]
