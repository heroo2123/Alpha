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
from polymarket_scanner.weather_only_live_paper_all_signals_v6 import (
    AllPaperWeatherLiveV6Service,
)
from polymarket_scanner.weather_only_maker import FairValueBand, MakerBidProposal
from polymarket_scanner.weather_only_maker_paper_accounting_v4 import (
    MAKER_SETTLEMENT_NOTIFY_SENDING,
    MakerPaperAccountingStoreV4,
)
from polymarket_scanner.weather_only_maker_shadow import (
    PARTIALLY_SIMULATED,
    MakerShadowPolicy,
    create_virtual_maker_order,
)
from polymarket_scanner.weather_only_maker_store import WeatherMakerStoreError


NOW = 1_800_000_000.0


def _order(order_id: str):
    token = f"YES-{order_id}"
    condition = f"condition-{order_id}"
    fair = FairValueBand(
        token_id=token,
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
        condition_id=condition,
        token_id=token,
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
        token_id=token,
        bids=[(0.40, 2.0)],
        asks=[(0.50, 4.0)],
        received_at=NOW - 0.1,
        source="exact",
        book_hash=f"hash-{order_id}",
    )
    params = WeatherMarketParameters(
        condition_id=condition,
        token_outcomes=((token, "Yes"), (f"NO-{order_id}", "No")),
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


def _settle(store: MakerPaperAccountingStoreV4, order_id: str) -> dict:
    original = store.save_new_order(_order(order_id), recorded_at=NOW)
    partial = replace(
        original,
        simulated_filled_shares=2.0,
        queue_ahead_shares=0.0,
        status=PARTIALLY_SIMULATED,
    )
    store.update_order(
        original,
        partial,
        event_type="TRADE_PROGRESS",
        payload={"new_simulated_fill_shares": 2.0, "financial_authority": False},
        recorded_at=NOW + 1.0,
    )
    return store.record_settlement(
        partial,
        payout_per_share=1.0,
        evidence={"source": "exact", "financial_authority": False},
        settled_at=NOW + 2.0,
    )


def _claim(store: MakerPaperAccountingStoreV4):
    return store.claim_pending_settlement_notifications(
        sent_event_type=MAKER_SETTLEMENT_NOTIFY_SENT,
        uncertain_event_type=MAKER_SETTLEMENT_NOTIFY_UNCERTAIN,
        limit=50,
        claimed_at=NOW + 3.0,
    )


def test_claim_is_durable_before_send_and_suppresses_restart_retry(tmp_path):
    path = tmp_path / "maker.sqlite"
    first = MakerPaperAccountingStoreV4(path)
    try:
        expected = _settle(first, "order-1")
        claimed = _claim(first)
        assert claimed == [("order-1", expected)]
        events = first.db.execute(
            "SELECT event_type FROM weather_maker_shadow_events WHERE order_id=? ORDER BY seq",
            ("order-1",),
        ).fetchall()
        assert str(events[-1][0]) == MAKER_SETTLEMENT_NOTIFY_SENDING
        assert first.audit_order("order-1")["sqlite_integrity"] == "ok"
    finally:
        first.close()

    # Simulate a process crash immediately after the durable claim and before the
    # external Telegram outcome is known.  Reopening must not claim/send it again.
    reopened = MakerPaperAccountingStoreV4(path)
    try:
        assert _claim(reopened) == []
        assert reopened.summary()["settlement_notification_claims"] == 1
        assert reopened.audit_order("order-1")["sqlite_integrity"] == "ok"
    finally:
        reopened.close()


def test_two_independent_connections_cannot_double_claim_same_settlement(tmp_path):
    path = tmp_path / "maker.sqlite"
    seed = MakerPaperAccountingStoreV4(path)
    try:
        _settle(seed, "order-race")
    finally:
        seed.close()

    left = MakerPaperAccountingStoreV4(path)
    right = MakerPaperAccountingStoreV4(path)

    async def race():
        return await asyncio.gather(
            asyncio.to_thread(_claim, left),
            asyncio.to_thread(_claim, right),
        )

    try:
        results = asyncio.run(race())
        assert sorted(len(value) for value in results) == [0, 1]
        assert sum(len(value) for value in results) == 1
        assert left.audit_order("order-race")["sqlite_integrity"] == "ok"
    finally:
        left.close()
        right.close()


def test_sent_or_uncertain_after_claim_remains_terminal(tmp_path):
    for terminal in (MAKER_SETTLEMENT_NOTIFY_SENT, MAKER_SETTLEMENT_NOTIFY_UNCERTAIN):
        path = tmp_path / f"{terminal}.sqlite"
        store = MakerPaperAccountingStoreV4(path)
        try:
            _settle(store, "order-terminal")
            assert len(_claim(store)) == 1
            store.append_research_event(
                "order-terminal",
                event_type=terminal,
                payload={"financial_authority": False},
                recorded_at=NOW + 4.0,
            )
            assert _claim(store) == []
            assert store.audit_order("order-terminal")["sqlite_integrity"] == "ok"
        finally:
            store.close()


def test_claim_batch_is_atomic_when_any_settlement_payload_is_corrupt(tmp_path):
    path = tmp_path / "maker.sqlite"
    store = MakerPaperAccountingStoreV4(path)
    try:
        _settle(store, "order-good")
        _settle(store, "order-bad")
        with store._db_lock:
            store.db.execute(
                "UPDATE weather_maker_shadow_events SET payload_json=? "
                "WHERE order_id=? AND event_type='PAPER_SETTLED'",
                ("{broken-json", "order-bad"),
            )
        with pytest.raises(WeatherMakerStoreError):
            _claim(store)
        # BEGIN IMMEDIATE + rollback means even the earlier good row was not left in
        # SENDING after the later corrupt row aborted the batch.
        sending = store.db.execute(
            "SELECT COUNT(*) FROM weather_maker_shadow_events WHERE event_type=?",
            (MAKER_SETTLEMENT_NOTIFY_SENDING,),
        ).fetchone()[0]
        assert int(sending) == 0
    finally:
        store.close()


def test_claim_rejects_nonfinite_timestamps(tmp_path):
    store = MakerPaperAccountingStoreV4(tmp_path / "maker.sqlite")
    try:
        for value in (float("nan"), float("inf"), -1.0):
            with pytest.raises(WeatherMakerStoreError) as exc:
                store.claim_pending_settlement_notifications(
                    sent_event_type=MAKER_SETTLEMENT_NOTIFY_SENT,
                    uncertain_event_type=MAKER_SETTLEMENT_NOTIFY_UNCERTAIN,
                    claimed_at=value,
                )
            assert exc.value.code == "MAKER_STORE_NOTIFICATION_CLAIM_TIME_INVALID"
    finally:
        store.close()


def test_v6_runtime_claim_method_creates_sending_barrier(tmp_path):
    store = MakerPaperAccountingStoreV4(tmp_path / "maker.sqlite")
    service = object.__new__(AllPaperWeatherLiveV6Service)
    service.maker_store = store
    try:
        _settle(store, "order-v6")
        pending = service._pending_maker_settlement_notifications()
        assert [order_id for order_id, _payload in pending] == ["order-v6"]
        assert service._pending_maker_settlement_notifications() == []
    finally:
        store.close()
