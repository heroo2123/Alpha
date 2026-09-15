from __future__ import annotations

import asyncio
from dataclasses import replace

import pytest

from polymarket_scanner.models import Book
from polymarket_scanner.weather_only_clob import WeatherMarketParameters
from polymarket_scanner.weather_only_maker import FairValueBand, MakerBidProposal
from polymarket_scanner.weather_only_maker_paper_accounting import (
    MAKER_SETTLEMENT_EVENT,
    MakerPaperAccountingError,
    MakerPaperAccountingStore,
)
from polymarket_scanner.weather_only_maker_shadow import (
    CANCELLED,
    PARTIALLY_SIMULATED,
    RESTING,
    MakerShadowPolicy,
    create_virtual_maker_order,
)


NOW = 1_800_000_000.0


def _order(order_id="order-1"):
    fair = FairValueBand(
        token_id="YES-1",
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
        event_id="event-1",
        market_id="market-1",
        condition_id="condition-1",
        token_id="YES-1",
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
        token_id="YES-1",
        bids=[(0.40, 2.0), (0.39, 5.0)],
        asks=[(0.50, 4.0)],
        received_at=NOW - 0.1,
        source="exact",
        book_hash="hash-1",
    )
    params = WeatherMarketParameters(
        condition_id="condition-1",
        token_outcomes=(("YES-1", "Yes"), ("NO-1", "No")),
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


def test_restart_cancels_resting_order_without_erasing_identity(tmp_path):
    store = MakerPaperAccountingStore(tmp_path / "maker.sqlite")
    try:
        original = store.save_new_order(_order())
        assert original.status == RESTING
        assert store.cancel_open_after_restart(recorded_at=NOW + 10.0) == 1
        cancelled = store.load_order(original.order_id)
        assert cancelled.status == CANCELLED
        assert cancelled.simulated_filled_shares == 0.0
        audit = store.audit_order(original.order_id)
        assert audit["sqlite_integrity"] == "ok"
        assert audit["financial_authority"] is False
    finally:
        store.close()


def test_restart_cancels_partial_order_but_preserves_simulated_inventory(tmp_path):
    store = MakerPaperAccountingStore(tmp_path / "maker.sqlite")
    try:
        original = store.save_new_order(_order())
        partial = replace(
            original,
            simulated_filled_shares=3.0,
            status=PARTIALLY_SIMULATED,
            queue_ahead_shares=0.0,
        )
        store.update_order(
            original,
            partial,
            event_type="TRADE_PROGRESS",
            payload={"new_simulated_fill_shares": 3.0, "financial_authority": False},
            recorded_at=NOW + 1.0,
        )
        assert store.cancel_open_after_restart(recorded_at=NOW + 10.0) == 1
        cancelled = store.load_order(original.order_id)
        assert cancelled.status == CANCELLED
        assert cancelled.simulated_filled_shares == pytest.approx(3.0)
        assert [row.order_id for row in store.unsettled_filled_orders()] == [original.order_id]
    finally:
        store.close()


def test_real_close_reopen_restart_preserves_partial_fill_and_settlement_idempotency(tmp_path):
    path = tmp_path / "maker.sqlite"
    store = MakerPaperAccountingStore(path)
    original = store.save_new_order(_order("reopen-partial"))
    partial = replace(
        original,
        simulated_filled_shares=3.25,
        status=PARTIALLY_SIMULATED,
        queue_ahead_shares=0.0,
    )
    store.update_order(
        original,
        partial,
        event_type="TRADE_PROGRESS",
        payload={"new_simulated_fill_shares": 3.25, "financial_authority": False},
        recorded_at=NOW + 1.0,
    )
    store.close()

    restarted = MakerPaperAccountingStore(path)
    try:
        before_cancel = restarted.load_order(original.order_id)
        assert before_cancel.status == PARTIALLY_SIMULATED
        assert before_cancel.simulated_filled_shares == pytest.approx(3.25)
        assert restarted.cancel_open_after_restart(recorded_at=NOW + 10.0) == 1
        cancelled = restarted.load_order(original.order_id)
        assert cancelled.status == CANCELLED
        assert cancelled.simulated_filled_shares == pytest.approx(3.25)
        settled = restarted.record_settlement(
            cancelled,
            payout_per_share=1.0,
            evidence={
                "source": "GAMMA_CLOSED_MARKET_EXACT_TOKEN_PAYOUT",
                "token_id": cancelled.token_id,
                "payout": 1.0,
                "financial_authority": False,
            },
            settled_at=NOW + 100.0,
        )
        assert settled["simulated_filled_shares"] == pytest.approx(3.25)
        assert settled["simulated_capital_used"] == pytest.approx(1.30)
    finally:
        restarted.close()

    reopened_again = MakerPaperAccountingStore(path)
    try:
        cancelled = reopened_again.load_order(original.order_id)
        assert cancelled.status == CANCELLED
        existing = reopened_again.settlement(original.order_id)
        assert existing is not None
        again = reopened_again.record_settlement(
            cancelled,
            payout_per_share=0.0,
            evidence={"source": "must-not-overwrite", "financial_authority": False},
            settled_at=NOW + 200.0,
        )
        assert again == existing
        count = reopened_again.db.execute(
            "SELECT COUNT(*) FROM weather_maker_shadow_events WHERE order_id=? AND event_type=?",
            (cancelled.order_id, MAKER_SETTLEMENT_EVENT),
        ).fetchone()[0]
        assert count == 1
        audit = reopened_again.audit_order(cancelled.order_id)
        assert audit["sqlite_integrity"] == "ok"
    finally:
        reopened_again.close()


def test_settlement_uses_exact_simulated_fill_quantity_and_is_idempotent(tmp_path):
    store = MakerPaperAccountingStore(tmp_path / "maker.sqlite")
    try:
        original = store.save_new_order(_order())
        filled = replace(
            original,
            simulated_filled_shares=2.5,
            status=PARTIALLY_SIMULATED,
            queue_ahead_shares=0.0,
        )
        store.update_order(
            original,
            filled,
            event_type="TRADE_PROGRESS",
            payload={"new_simulated_fill_shares": 2.5, "financial_authority": False},
            recorded_at=NOW + 1.0,
        )
        evidence = {
            "source": "GAMMA_CLOSED_MARKET_EXACT_TOKEN_PAYOUT",
            "token_id": "YES-1",
            "payout": 1.0,
            "financial_authority": False,
        }
        result = store.record_settlement(
            filled,
            payout_per_share=1.0,
            evidence=evidence,
            settled_at=NOW + 100.0,
        )
        assert result["simulated_filled_shares"] == pytest.approx(2.5)
        assert result["simulated_capital_used"] == pytest.approx(1.0)
        assert result["paper_proceeds"] == pytest.approx(2.5)
        assert result["paper_pnl"] == pytest.approx(1.5)
        assert result["paper_roi"] == pytest.approx(1.5)
        again = store.record_settlement(
            filled,
            payout_per_share=1.0,
            evidence=evidence,
            settled_at=NOW + 200.0,
        )
        assert again == result
        count = store.db.execute(
            "SELECT COUNT(*) FROM weather_maker_shadow_events WHERE order_id=? AND event_type=?",
            (filled.order_id, MAKER_SETTLEMENT_EVENT),
        ).fetchone()[0]
        assert count == 1
    finally:
        store.close()


def test_zero_fill_cannot_be_settled(tmp_path):
    store = MakerPaperAccountingStore(tmp_path / "maker.sqlite")
    try:
        order = store.save_new_order(_order())
        with pytest.raises(MakerPaperAccountingError) as exc:
            store.record_settlement(
                order,
                payout_per_share=1.0,
                evidence={"financial_authority": False},
            )
        assert exc.value.code == "MAKER_ACCOUNTING_NO_SIMULATED_FILL"
    finally:
        store.close()


def test_summary_separates_active_filled_and_settled_counts(tmp_path):
    store = MakerPaperAccountingStore(tmp_path / "maker.sqlite")
    try:
        store.save_new_order(_order("resting"))
        second = store.save_new_order(_order("filled"))
        partial = replace(
            second,
            simulated_filled_shares=1.0,
            status=PARTIALLY_SIMULATED,
            queue_ahead_shares=0.0,
        )
        store.update_order(
            second,
            partial,
            event_type="TRADE_PROGRESS",
            payload={"financial_authority": False},
            recorded_at=NOW + 1.0,
        )
        store.record_settlement(
            partial,
            payout_per_share=0.0,
            evidence={"source": "exact", "financial_authority": False},
            settled_at=NOW + 2.0,
        )
        summary = store.summary()
        assert summary["orders_total"] == 2
        assert summary["active_orders"] == 2
        assert summary["simulated_filled_orders"] == 1
        assert summary["settled_orders"] == 1
        assert summary["financial_authority"] is False
    finally:
        store.close()


def test_store_supports_successive_executor_worker_handoffs(tmp_path):
    store = MakerPaperAccountingStore(tmp_path / "maker.sqlite")

    async def exercise():
        created = await asyncio.to_thread(store.save_new_order, _order("thread-hop"))
        loaded = await asyncio.to_thread(store.load_order, created.order_id)
        summary = await asyncio.to_thread(store.summary)
        audit = await asyncio.to_thread(store.audit_order, created.order_id)
        return loaded, summary, audit

    try:
        loaded, summary, audit = asyncio.run(exercise())
        assert loaded.order_id == "thread-hop"
        assert summary["orders_total"] == 1
        assert audit["sqlite_integrity"] == "ok"
    finally:
        store.close()


def test_store_serializes_concurrent_worker_calls(tmp_path):
    store = MakerPaperAccountingStore(tmp_path / "maker.sqlite")

    async def exercise():
        await asyncio.gather(
            *(
                asyncio.to_thread(store.save_new_order, _order(f"concurrent-{index}"))
                for index in range(12)
            )
        )
        summaries = await asyncio.gather(
            *(asyncio.to_thread(store.summary) for _ in range(8))
        )
        return summaries

    try:
        summaries = asyncio.run(exercise())
        assert all(row["orders_total"] == 12 for row in summaries)
        for index in range(12):
            audit = store.audit_order(f"concurrent-{index}")
            assert audit["sqlite_integrity"] == "ok"
    finally:
        store.close()
