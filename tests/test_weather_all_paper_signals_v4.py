from __future__ import annotations

import asyncio
from dataclasses import replace

import pytest

from polymarket_scanner.models import Book
from polymarket_scanner.weather_only_clob import WeatherMarketParameters
from polymarket_scanner.weather_only_live_paper_all_signals_v3 import (
    MAKER_SETTLEMENT_NOTIFY_SENT,
    MAKER_SETTLEMENT_NOTIFY_UNCERTAIN,
)
from polymarket_scanner.weather_only_live_paper_all_signals_v4 import (
    AllPaperWeatherLiveV4Service,
)
from polymarket_scanner.weather_only_maker import FairValueBand, MakerBidProposal
from polymarket_scanner.weather_only_maker_paper_accounting import MAKER_SETTLEMENT_EVENT
from polymarket_scanner.weather_only_maker_paper_accounting_v3 import (
    MakerPaperAccountingStoreV3,
)
from polymarket_scanner.weather_only_maker_shadow import (
    PARTIALLY_SIMULATED,
    MakerShadowPolicy,
    create_virtual_maker_order,
)
from polymarket_scanner.weather_only_maker_store import WeatherMakerStoreError


NOW = 1_800_000_000.0


def _order(order_id: str):
    fair = FairValueBand(
        token_id=f"YES-{order_id}",
        lower=0.60,
        point=0.65,
        upper=0.70,
        model_version="uncalibrated-test",
        as_of=NOW - 1.0,
        calibrated=False,
    )
    proposal = MakerBidProposal(
        version="weather_maker_v2_fee_fail_closed_unique_fill_accounting_shadow",
        evidence_class="MAKER_CONDITIONAL",
        event_id=f"event-{order_id}",
        market_id=f"market-{order_id}",
        condition_id=f"condition-{order_id}",
        token_id=f"YES-{order_id}",
        outcome="Yes",
        bid_price=0.40,
        max_shares=10.0,
        max_notional=4.0,
        fair_lower=0.60,
        fair_point=0.65,
        fair_upper=0.70,
        conditional_edge_per_share=0.20,
        current_best_bid=0.39,
        current_best_ask=0.50,
        minimum_tick_size=0.01,
        minimum_order_size=1.0,
        model_version=fair.model_version,
        calibrated=False,
        cancel_conditions=("research",),
        financial_authority=False,
    )
    book = Book(
        token_id=f"YES-{order_id}",
        bids=[(0.40, 2.0), (0.39, 5.0)],
        asks=[(0.50, 4.0)],
        received_at=NOW - 0.1,
        source="exact",
        book_hash=f"hash-{order_id}",
    )
    params = WeatherMarketParameters(
        condition_id=f"condition-{order_id}",
        token_outcomes=((f"YES-{order_id}", "Yes"), (f"NO-{order_id}", "No")),
        minimum_order_size=1.0,
        minimum_tick_size=0.01,
        fee_rate=0.0,
        fee_exponent=0,
        taker_only=None,
        maker_base_fee_bps=0,
        taker_base_fee_bps=0,
        rfq_enabled=False,
        taker_delay_enabled=False,
        received_at=NOW - 0.1,
    )
    policy = MakerShadowPolicy(
        policy_id="policy-1",
        minimum_edge_per_share=0.05,
        max_fair_age_seconds=30.0,
        max_order_age_seconds=300.0,
        max_book_age_seconds=10.0,
    )
    return create_virtual_maker_order(
        order_id=order_id,
        proposal=proposal,
        fair=fair,
        book=book,
        parameters=params,
        policy=policy,
        created_at=NOW,
        contract_evidence_sha256="a" * 64,
        source_generation="forecast-sha|ws_generation=1",
    )


def _settled(store: MakerPaperAccountingStoreV3, order_id: str) -> None:
    original = store.save_new_order(_order(order_id))
    partial = replace(
        original,
        simulated_filled_shares=2.0,
        status=PARTIALLY_SIMULATED,
        queue_ahead_shares=0.0,
    )
    store.update_order(
        original,
        partial,
        event_type="TRADE_PROGRESS",
        payload={"new_simulated_fill_shares": 2.0, "financial_authority": False},
        recorded_at=NOW + 1.0,
    )
    store.record_settlement(
        partial,
        payout_per_share=1.0,
        evidence={"source": "exact", "financial_authority": False},
        settled_at=NOW + 2.0,
    )


def test_locked_pending_query_returns_unnotified_settlement_then_suppresses_sent(tmp_path):
    store = MakerPaperAccountingStoreV3(tmp_path / "maker.sqlite")
    try:
        _settled(store, "order-1")
        pending = store.pending_notification_payloads(
            source_event_type=MAKER_SETTLEMENT_EVENT,
            terminal_event_types=(
                MAKER_SETTLEMENT_NOTIFY_SENT,
                MAKER_SETTLEMENT_NOTIFY_UNCERTAIN,
            ),
        )
        assert len(pending) == 1
        assert pending[0][0] == "order-1"
        assert pending[0][1]["simulated_filled_shares"] == pytest.approx(2.0)

        store.append_research_event(
            "order-1",
            event_type=MAKER_SETTLEMENT_NOTIFY_SENT,
            payload={"telegram_message_id": 123, "financial_authority": False},
            recorded_at=NOW + 3.0,
        )
        assert store.pending_notification_payloads(
            source_event_type=MAKER_SETTLEMENT_EVENT,
            terminal_event_types=(
                MAKER_SETTLEMENT_NOTIFY_SENT,
                MAKER_SETTLEMENT_NOTIFY_UNCERTAIN,
            ),
        ) == []
        assert store.audit_order("order-1")["sqlite_integrity"] == "ok"
    finally:
        store.close()


def test_v4_runtime_uses_locked_store_api_for_pending_notifications(tmp_path):
    store = MakerPaperAccountingStoreV3(tmp_path / "maker.sqlite")
    service = object.__new__(AllPaperWeatherLiveV4Service)
    service.maker_store = store
    try:
        _settled(store, "order-v4")
        pending = service._pending_maker_settlement_notifications()
        assert [order_id for order_id, _payload in pending] == ["order-v4"]
    finally:
        store.close()


def test_pending_query_serializes_against_concurrent_writer_threads(tmp_path):
    store = MakerPaperAccountingStoreV3(tmp_path / "maker.sqlite")

    async def exercise():
        await asyncio.gather(
            *(asyncio.to_thread(_settled, store, f"order-{index}") for index in range(16))
        )
        snapshots = await asyncio.gather(
            *(
                asyncio.to_thread(
                    store.pending_notification_payloads,
                    source_event_type=MAKER_SETTLEMENT_EVENT,
                    terminal_event_types=(
                        MAKER_SETTLEMENT_NOTIFY_SENT,
                        MAKER_SETTLEMENT_NOTIFY_UNCERTAIN,
                    ),
                    limit=50,
                )
                for _ in range(20)
            )
        )
        return snapshots

    try:
        snapshots = asyncio.run(exercise())
        assert all(len(snapshot) == 16 for snapshot in snapshots)
        assert store.summary()["orders_total"] == 16
        for index in range(16):
            assert store.audit_order(f"order-{index}")["sqlite_integrity"] == "ok"
    finally:
        store.close()


def test_pending_query_fails_closed_on_corrupt_persisted_json(tmp_path):
    store = MakerPaperAccountingStoreV3(tmp_path / "maker.sqlite")
    try:
        _settled(store, "order-corrupt")
        with store._db_lock:
            store.db.execute(
                "UPDATE weather_maker_shadow_events SET payload_json=? "
                "WHERE order_id=? AND event_type=?",
                ("{not-json", "order-corrupt", MAKER_SETTLEMENT_EVENT),
            )
        with pytest.raises(WeatherMakerStoreError) as exc:
            store.pending_notification_payloads(
                source_event_type=MAKER_SETTLEMENT_EVENT,
                terminal_event_types=(
                    MAKER_SETTLEMENT_NOTIFY_SENT,
                    MAKER_SETTLEMENT_NOTIFY_UNCERTAIN,
                ),
            )
        assert exc.value.code == "MAKER_STORE_EVENT_PAYLOAD_JSON_INVALID"
    finally:
        store.close()


def test_pending_query_rejects_ambiguous_or_unbounded_inputs(tmp_path):
    store = MakerPaperAccountingStoreV3(tmp_path / "maker.sqlite")
    try:
        with pytest.raises(WeatherMakerStoreError):
            store.pending_notification_payloads(
                source_event_type="",
                terminal_event_types=(MAKER_SETTLEMENT_NOTIFY_SENT,),
            )
        with pytest.raises(WeatherMakerStoreError):
            store.pending_notification_payloads(
                source_event_type=MAKER_SETTLEMENT_EVENT,
                terminal_event_types=(
                    MAKER_SETTLEMENT_NOTIFY_SENT,
                    MAKER_SETTLEMENT_NOTIFY_SENT,
                ),
            )
        with pytest.raises(WeatherMakerStoreError):
            store.pending_notification_payloads(
                source_event_type=MAKER_SETTLEMENT_EVENT,
                terminal_event_types=(MAKER_SETTLEMENT_NOTIFY_SENT,),
                limit=501,
            )
    finally:
        store.close()
