from __future__ import annotations

from dataclasses import replace

import pytest

from polymarket_scanner.models import Book
from polymarket_scanner.weather_only_clob import WeatherMarketParameters
from polymarket_scanner.weather_only_maker import FairValueBand, MakerBidProposal
from polymarket_scanner.weather_only_maker_shadow import (
    CANCEL,
    CANCELLED,
    KEEP,
    PARTIALLY_SIMULATED,
    RESTING,
    SIMULATED_FILLED,
    MakerShadowPolicy,
    PublicTradePrint,
    WeatherMakerShadowError,
    apply_cancel_decision,
    create_virtual_maker_order,
    evaluate_virtual_order,
    markout_simulated_fill,
    market_parameter_fingerprint,
    simulate_public_trade_progression,
)


NOW = 1_800_000_000.0
CONTRACT_SHA = "a" * 64


def _policy() -> MakerShadowPolicy:
    return MakerShadowPolicy(
        policy_id="maker-shadow-policy-v1",
        minimum_edge_per_share=0.05,
        max_fair_age_seconds=30.0,
        max_order_age_seconds=300.0,
        max_book_age_seconds=10.0,
    )


def _parameters(*, tick: float = 0.01) -> WeatherMarketParameters:
    return WeatherMarketParameters(
        condition_id="cond-1",
        token_outcomes=(("YES-1", "Yes"), ("NO-1", "No")),
        minimum_order_size=5.0,
        minimum_tick_size=tick,
        fee_rate=0.0,
        fee_exponent=0,
        taker_only=None,
        maker_base_fee_bps=0,
        taker_base_fee_bps=0,
        rfq_enabled=False,
        taker_delay_enabled=False,
        received_at=NOW - 1.0,
    )


def _book(*, bids=None, asks=None, received_at: float = NOW - 0.5, last_trade=None) -> Book:
    return Book(
        token_id="YES-1",
        bids=list(bids if bids is not None else [(0.20, 7.0)]),
        asks=list(asks if asks is not None else [(0.40, 20.0)]),
        last_trade_price=last_trade,
        received_at=received_at,
        source="clob_exact_rest_v2",
        book_hash="book-hash-1",
    )


def _fair(*, lower: float = 0.35, as_of: float = NOW - 2.0, model: str = "model-v1") -> FairValueBand:
    return FairValueBand(
        token_id="YES-1",
        lower=lower,
        point=min(0.90, lower + 0.05),
        upper=min(0.99, lower + 0.10),
        model_version=model,
        as_of=as_of,
        calibrated=False,
    )


def _proposal(*, bid: float = 0.25, shares: float = 10.0) -> MakerBidProposal:
    return MakerBidProposal(
        version="weather_maker_v1_confirmed_fill_inventory_shadow",
        evidence_class="MAKER_CONDITIONAL",
        event_id="event-1",
        market_id="market-1",
        condition_id="cond-1",
        token_id="YES-1",
        outcome="Yes",
        bid_price=bid,
        max_shares=shares,
        max_notional=bid * shares,
        fair_lower=0.35,
        fair_point=0.40,
        fair_upper=0.45,
        conditional_edge_per_share=0.10,
        current_best_bid=0.20,
        current_best_ask=0.40,
        minimum_tick_size=0.01,
        minimum_order_size=5.0,
        model_version="model-v1",
        calibrated=False,
        cancel_conditions=("research",),
        financial_authority=False,
    )


def _order(*, book=None, bid: float = 0.25, shares: float = 10.0):
    return create_virtual_maker_order(
        order_id="virtual-1",
        proposal=_proposal(bid=bid, shares=shares),
        fair=_fair(),
        book=book or _book(),
        parameters=_parameters(),
        policy=_policy(),
        created_at=NOW,
        contract_evidence_sha256=CONTRACT_SHA,
        source_generation="obs-generation-1",
    )


def test_virtual_order_binds_evidence_and_visible_queue_without_claiming_real_order():
    book = _book(bids=[(0.25, 8.0), (0.20, 7.0)])
    order = _order(book=book)
    assert order.status == RESTING
    assert order.queue_ahead_shares == pytest.approx(8.0)
    assert order.remaining_shares == pytest.approx(10.0)
    assert order.actual_order_placed is False
    assert order.actual_fill_authority is False
    assert order.financial_authority is False
    assert len(order.fair_evidence_sha256) == 64
    assert order.market_parameter_sha256 == market_parameter_fingerprint(_parameters())


def test_inside_spread_virtual_bid_has_no_visible_queue_ahead():
    order = _order(book=_book(bids=[(0.20, 7.0)]))
    assert order.bid_price == pytest.approx(0.25)
    assert order.queue_ahead_shares == 0.0


def test_public_sell_prints_consume_queue_then_simulate_fill_at_resting_bid():
    order = _order(book=_book(bids=[(0.25, 6.0)]), shares=10.0)
    trades = [
        PublicTradePrint("t1", "YES-1", 0.25, 4.0, NOW + 1.0, "SELL"),
        PublicTradePrint("t2", "YES-1", 0.24, 5.0, NOW + 2.0, "SELL"),
        PublicTradePrint("t3", "YES-1", 0.25, 9.0, NOW + 3.0, "SELL"),
    ]
    updated, sim = simulate_public_trade_progression(order, trades)
    assert sim.starting_queue_ahead_shares == pytest.approx(6.0)
    assert sim.ending_queue_ahead_shares == 0.0
    assert sim.eligible_sell_print_shares == pytest.approx(18.0)
    assert sim.new_simulated_fill_shares == pytest.approx(10.0)
    assert sim.total_simulated_filled_shares == pytest.approx(10.0)
    assert sim.remaining_order_shares == 0.0
    assert sim.used_trade_ids == ("t2", "t3")
    assert sim.actual_fill_authority is False
    assert updated.status == SIMULATED_FILLED
    assert updated.simulated_filled_shares == pytest.approx(10.0)


def test_buy_and_unknown_side_prints_never_become_virtual_bid_fills():
    order = _order()
    trades = [
        PublicTradePrint("buy", "YES-1", 0.20, 20.0, NOW + 1.0, "BUY"),
        PublicTradePrint("unknown", "YES-1", 0.20, 8.0, NOW + 2.0, None),
    ]
    updated, sim = simulate_public_trade_progression(order, trades)
    assert updated.status == RESTING
    assert sim.new_simulated_fill_shares == 0.0
    assert sim.ambiguous_print_shares == pytest.approx(8.0)
    assert sim.actual_fill_authority is False


def test_book_touch_or_last_trade_alone_is_not_a_fill():
    order = _order()
    latest = _book(bids=[(0.20, 1.0)], asks=[(0.26, 1.0)], received_at=NOW + 1.0, last_trade=0.20)
    updated, sim = simulate_public_trade_progression(order, [], latest_book=latest)
    assert updated.status == RESTING
    assert sim.new_simulated_fill_shares == 0.0
    assert sim.book_touch_seen is True


def test_preorder_print_is_excluded_from_queue_or_fill_accounting():
    order = _order()
    trade = PublicTradePrint("old", "YES-1", 0.20, 12.0, NOW - 1.0, "SELL")
    updated, sim = simulate_public_trade_progression(order, [trade])
    assert updated.status == RESTING
    assert sim.ignored_preorder_print_shares == pytest.approx(12.0)
    assert sim.eligible_sell_print_shares == 0.0


def test_duplicate_trade_id_fails_closed_instead_of_double_counting():
    order = _order()
    trade = PublicTradePrint("dup", "YES-1", 0.20, 2.0, NOW + 1.0, "SELL")
    with pytest.raises(WeatherMakerShadowError) as raised:
        simulate_public_trade_progression(order, [trade, trade])
    assert raised.value.code == "MAKER_DUPLICATE_TRADE_ID"


def test_partial_fill_can_progress_across_cycles_without_reclassifying_as_actual_fill():
    order = _order(shares=10.0)
    first, sim1 = simulate_public_trade_progression(
        order,
        [PublicTradePrint("a", "YES-1", 0.24, 3.0, NOW + 1.0, "SELL")],
    )
    assert first.status == PARTIALLY_SIMULATED
    assert first.simulated_filled_shares == pytest.approx(3.0)
    second, sim2 = simulate_public_trade_progression(
        first,
        [PublicTradePrint("b", "YES-1", 0.24, 7.0, NOW + 2.0, "SELL")],
    )
    assert second.status == SIMULATED_FILLED
    assert sim2.prior_simulated_filled_shares == pytest.approx(3.0)
    assert sim2.new_simulated_fill_shares == pytest.approx(7.0)
    assert second.actual_fill_authority is False


def test_order_is_cancelled_when_fair_edge_erodes_or_source_generation_changes():
    order = _order()
    decision = evaluate_virtual_order(
        order,
        fair=_fair(lower=0.27, as_of=NOW + 1.0),
        book=_book(received_at=NOW + 1.0),
        parameters=_parameters(),
        policy=_policy(),
        evaluated_at=NOW + 2.0,
        contract_evidence_sha256=CONTRACT_SHA,
        source_generation="obs-generation-2",
        market_open=True,
        accepting_orders=True,
    )
    assert decision.action == CANCEL
    assert "FAIR_EDGE_ERODED" in decision.reasons
    assert "SOURCE_GENERATION_CHANGED" in decision.reasons
    cancelled = apply_cancel_decision(order, decision)
    assert cancelled.status == CANCELLED


def test_unchanged_fresh_evidence_keeps_virtual_order_resting():
    order = _order()
    decision = evaluate_virtual_order(
        order,
        fair=_fair(as_of=NOW + 1.0),
        book=_book(received_at=NOW + 1.0),
        parameters=_parameters(),
        policy=_policy(),
        evaluated_at=NOW + 2.0,
        contract_evidence_sha256=CONTRACT_SHA,
        source_generation="obs-generation-1",
        market_open=True,
        accepting_orders=True,
    )
    assert decision.action == KEEP
    assert decision.reasons == ()
    assert decision.financial_authority is False


def test_parameter_drift_and_contract_drift_cancel_instead_of_silently_repricing():
    order = _order()
    changed = _parameters(tick=0.005)
    decision = evaluate_virtual_order(
        order,
        fair=_fair(as_of=NOW + 1.0),
        book=_book(received_at=NOW + 1.0),
        parameters=changed,
        policy=_policy(),
        evaluated_at=NOW + 2.0,
        contract_evidence_sha256="b" * 64,
        source_generation="obs-generation-1",
        market_open=True,
        accepting_orders=True,
    )
    assert decision.action == CANCEL
    assert "MARKET_PARAMETERS_CHANGED" in decision.reasons
    assert "CONTRACT_EVIDENCE_CHANGED" in decision.reasons


def test_markout_uses_later_public_book_but_remains_simulated_research_only():
    order = _order(shares=10.0)
    filled, sim = simulate_public_trade_progression(
        order,
        [PublicTradePrint("fill", "YES-1", 0.24, 10.0, NOW + 1.0, "SELL")],
    )
    later = _book(
        bids=[(0.23, 20.0)],
        asks=[(0.27, 20.0)],
        received_at=NOW + 31.0,
    )
    markout = markout_simulated_fill(
        filled,
        sim,
        later,
        horizon_seconds=30.0,
        exit_fee_per_share=0.001,
    )
    assert markout.midpoint == pytest.approx(0.25)
    assert markout.midpoint_markout_per_share == pytest.approx(0.0)
    assert markout.executable_bid_markout_per_share == pytest.approx(-0.021)
    assert markout.simulated_fill_only is True
    assert markout.financial_authority is False


def test_markout_before_requested_horizon_fails_closed():
    order = _order(shares=5.0)
    filled, sim = simulate_public_trade_progression(
        order,
        [PublicTradePrint("fill", "YES-1", 0.24, 5.0, NOW + 1.0, "SELL")],
    )
    with pytest.raises(WeatherMakerShadowError) as raised:
        markout_simulated_fill(
            filled,
            sim,
            _book(received_at=NOW + 20.0),
            horizon_seconds=30.0,
        )
    assert raised.value.code == "MAKER_MARKOUT_HORIZON_NOT_REACHED"


def test_future_or_stale_evidence_is_rejected_at_virtual_order_creation():
    with pytest.raises(WeatherMakerShadowError) as future:
        create_virtual_maker_order(
            order_id="x",
            proposal=_proposal(),
            fair=_fair(as_of=NOW + 1.0),
            book=_book(),
            parameters=_parameters(),
            policy=_policy(),
            created_at=NOW,
            contract_evidence_sha256=CONTRACT_SHA,
            source_generation="g",
        )
    assert future.value.code == "MAKER_FAIR_FROM_FUTURE"

    with pytest.raises(WeatherMakerShadowError) as stale:
        create_virtual_maker_order(
            order_id="x",
            proposal=_proposal(),
            fair=_fair(as_of=NOW - 31.0),
            book=_book(),
            parameters=_parameters(),
            policy=_policy(),
            created_at=NOW,
            contract_evidence_sha256=CONTRACT_SHA,
            source_generation="g",
        )
    assert stale.value.code == "MAKER_FAIR_STALE_AT_CREATION"
