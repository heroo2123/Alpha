from __future__ import annotations

from dataclasses import replace

import pytest

from polymarket_scanner.weather_only_maker_shadow import (
    MAX_PROCESSED_TRADE_IDS,
    PARTIALLY_SIMULATED,
    PublicTradePrint,
    WeatherMakerShadowError,
    create_virtual_maker_order,
    simulate_public_trade_progression,
)
from test_weather_only_maker_shadow import CONTRACT_SHA, NOW, _book, _fair, _order, _parameters, _policy, _proposal


def test_overlapping_poll_window_is_idempotent_across_cycles():
    order = _order(shares=10.0)
    first_trade = PublicTradePrint("trade-a", "YES-1", 0.24, 3.0, NOW + 1.0, "SELL")
    first, first_sim = simulate_public_trade_progression(order, [first_trade])
    assert first.status == PARTIALLY_SIMULATED
    assert first.simulated_filled_shares == pytest.approx(3.0)
    assert first.processed_trade_ids == ("trade-a",)
    assert first_sim.new_simulated_fill_shares == pytest.approx(3.0)

    second_trade = PublicTradePrint("trade-b", "YES-1", 0.24, 2.0, NOW + 2.0, "SELL")
    second, second_sim = simulate_public_trade_progression(first, [first_trade, second_trade])
    assert second.simulated_filled_shares == pytest.approx(5.0)
    assert second.processed_trade_ids == ("trade-a", "trade-b")
    assert second_sim.replayed_trade_ids == ("trade-a",)
    assert second_sim.new_simulated_fill_shares == pytest.approx(2.0)
    assert second_sim.eligible_sell_print_shares == pytest.approx(2.0)
    assert second.actual_fill_authority is False
    assert second.financial_authority is False


def test_queue_consuming_trade_is_persisted_even_when_it_does_not_fill_order():
    order = _order(book=_book(bids=[(0.25, 6.0)]), shares=10.0)
    queue_only = PublicTradePrint("queue-only", "YES-1", 0.25, 4.0, NOW + 1.0, "SELL")
    first, sim = simulate_public_trade_progression(order, [queue_only])
    assert first.queue_ahead_shares == pytest.approx(2.0)
    assert first.simulated_filled_shares == 0.0
    assert first.processed_trade_ids == ("queue-only",)

    replayed, replay_sim = simulate_public_trade_progression(first, [queue_only])
    assert replayed.queue_ahead_shares == pytest.approx(2.0)
    assert replayed.simulated_filled_shares == 0.0
    assert replay_sim.replayed_trade_ids == ("queue-only",)
    assert replay_sim.eligible_sell_print_shares == 0.0


def test_order_creation_rejects_proposal_fair_band_drift_even_when_edge_still_looks_good():
    proposal = replace(_proposal(), fair_lower=0.34)
    with pytest.raises(WeatherMakerShadowError) as raised:
        create_virtual_maker_order(
            order_id="drift",
            proposal=proposal,
            fair=_fair(),
            book=_book(),
            parameters=_parameters(),
            policy=_policy(),
            created_at=NOW,
            contract_evidence_sha256=CONTRACT_SHA,
            source_generation="obs-generation-1",
        )
    assert raised.value.code == "MAKER_PROPOSAL_FAIR_EVIDENCE_MISMATCH"


def test_order_creation_rejects_tampered_notional_and_off_tick_bid():
    with pytest.raises(WeatherMakerShadowError) as notional:
        create_virtual_maker_order(
            order_id="notional",
            proposal=replace(_proposal(), max_notional=999.0),
            fair=_fair(),
            book=_book(),
            parameters=_parameters(),
            policy=_policy(),
            created_at=NOW,
            contract_evidence_sha256=CONTRACT_SHA,
            source_generation="obs-generation-1",
        )
    assert notional.value.code == "MAKER_PROPOSAL_NOTIONAL_MISMATCH"

    with pytest.raises(WeatherMakerShadowError) as tick:
        create_virtual_maker_order(
            order_id="tick",
            proposal=replace(_proposal(bid=0.255), max_notional=0.255 * 10.0),
            fair=_fair(),
            book=_book(),
            parameters=_parameters(),
            policy=_policy(),
            created_at=NOW,
            contract_evidence_sha256=CONTRACT_SHA,
            source_generation="obs-generation-1",
        )
    assert tick.value.code == "MAKER_BID_TICK_INVALID"


def test_trade_history_cap_and_corrupt_history_fail_closed():
    order = _order()
    capped = replace(order, processed_trade_ids=tuple(f"t-{i}" for i in range(MAX_PROCESSED_TRADE_IDS + 1)))
    with pytest.raises(WeatherMakerShadowError) as cap:
        simulate_public_trade_progression(capped, [])
    assert cap.value.code == "MAKER_TRADE_HISTORY_CAP"

    corrupt = replace(order, processed_trade_ids=("same", "same"))
    with pytest.raises(WeatherMakerShadowError) as duplicate:
        simulate_public_trade_progression(corrupt, [])
    assert duplicate.value.code == "MAKER_ORDER_TRADE_HISTORY_CORRUPT"
