from __future__ import annotations

from datetime import date, datetime

import pytest

from polymarket_scanner.models import Book
from polymarket_scanner.weather_maker import (
    WeatherBuyFill,
    complete_set_inventory,
    propose_passive_bid,
    simulate_add_fill,
)
from polymarket_scanner.weather_rule_tree import DailyTemperatureContract, RuleSource
from polymarket_scanner.weather_structural import (
    TemperatureBucketLeg,
    TemperaturePartitionProof,
)


def proof() -> TemperaturePartitionProof:
    contract = DailyTemperatureContract(
        adapter_version="DAILY_TEMP_NWS_WRH_WITH_WU_FALLBACK_V1",
        family="daily_high_temperature",
        target_date=date(2026, 9, 11),
        unit="C",
        precision="whole_degree",
        primary=RuleSource("NWS_WRH", "https://weather.gov/wrh/timeseries?site=ZBAA", "weather.gov", "ZBAA"),
        fallback_kind="WEATHER_UNDERGROUND_DAILY_OBSERVATIONS",
        fallback_condition="NWS_WRH_UNAVAILABLE_BY_FOLLOWING_DAY_DEADLINE",
        fallback_deadline_local=datetime(2026, 9, 12, 23, 59),
        fallback_deadline_timezone="America/New_York",
        primary_finalization="FIRST_FOLLOWING_DAY_WRH_DATAPOINT_OR_FALLBACK_DEADLINE",
        revisions_before_finalization_count=True,
        no_data_resolution="LOWEST_BRACKET",
    )
    return TemperaturePartitionProof(
        proof_version="daily_temperature_integer_partition_v1",
        event_id="event-1",
        event_slug="event",
        contract=contract,
        legs=(
            TemperatureBucketLeg("m1", "m1", "25C or below", "A", None, 25),
            TemperatureBucketLeg("m2", "m2", "26C", "B", 26, 26),
            TemperatureBucketLeg("m3", "m3", "27C or higher", "C", 27, None),
        ),
        complete_event_market_ids=("m1", "m2", "m3"),
        guaranteed_yes_payout_per_complete_set=1.0,
    )


def test_passive_bid_improves_best_bid_without_crossing_and_stays_below_value_ceiling():
    book = Book("A", bids=[(0.20, 100)], asks=[(0.50, 10)])
    bid = propose_passive_bid(
        book,
        conservative_value_lower=0.70,
        desired_shares=25,
        minimum_required_edge=0.10,
        fee_buffer_per_share=0.01,
    )
    assert bid is not None
    assert bid.proposed_bid == pytest.approx(0.21)
    assert bid.hard_max_bid == pytest.approx(0.59)
    assert bid.conservative_edge_if_filled == pytest.approx(0.48)
    assert bid.crosses_current_ask is False
    assert bid.screening_only is True
    assert bid.fill_assumed is False


def test_passive_bid_can_use_deep_preferred_vacuum_price_without_calling_it_a_fill():
    book = Book("A", bids=[(0.18, 100)], asks=[(0.55, 10)])
    bid = propose_passive_bid(
        book,
        conservative_value_lower=0.65,
        desired_shares=50,
        minimum_required_edge=0.10,
        preferred_bid=0.20,
    )
    assert bid is not None
    assert bid.proposed_bid == pytest.approx(0.20)
    assert bid.conservative_edge_if_filled == pytest.approx(0.45)
    assert bid.fill_assumed is False


def test_passive_bid_rejects_when_existing_bid_is_above_value_ceiling():
    book = Book("A", bids=[(0.62, 10)], asks=[(0.70, 10)])
    assert propose_passive_bid(
        book,
        conservative_value_lower=0.65,
        desired_shares=10,
        minimum_required_edge=0.10,
    ) is None


def test_passive_bid_never_crosses_current_ask():
    book = Book("A", bids=[(0.48, 10)], asks=[(0.49, 10)])
    bid = propose_passive_bid(
        book,
        conservative_value_lower=0.80,
        desired_shares=10,
        minimum_required_edge=0.10,
    )
    # One tick below ask is 0.48, which cannot improve the existing 0.48 bid.
    assert bid is None


def test_complete_set_inventory_only_guarantees_the_covered_portion():
    p = proof()
    fills = [
        WeatherBuyFill("a1", "event-1", "A", 10, 0.10),
        WeatherBuyFill("b1", "event-1", "B", 8, 0.20),
        WeatherBuyFill("c1", "event-1", "C", 7, 0.15),
    ]
    inv = complete_set_inventory(p, fills)

    assert inv.total_recorded_shares == pytest.approx(25)
    assert inv.total_recorded_cost == pytest.approx(3.65)
    assert inv.covered_bundle_shares == pytest.approx(7)
    assert inv.covered_fifo_cost == pytest.approx(3.15)
    assert inv.guaranteed_payout_on_covered_bundles == pytest.approx(7)
    assert inv.locked_pnl_on_covered_bundles == pytest.approx(3.85)
    assert dict(inv.remainder_shares) == pytest.approx({"A": 3, "B": 1, "C": 0})
    assert inv.directional_remainder_total_shares == pytest.approx(4)
    assert inv.directional_remainder_total_cost == pytest.approx(0.50)


def test_fifo_bundle_cost_includes_actual_fill_fees():
    p = proof()
    fills = [
        WeatherBuyFill("a1", "event-1", "A", 2, 0.10, fee_paid=0.02),
        WeatherBuyFill("b1", "event-1", "B", 2, 0.20, fee_paid=0.04),
        WeatherBuyFill("c1", "event-1", "C", 2, 0.15, fee_paid=0.00),
    ]
    inv = complete_set_inventory(p, fills)
    assert inv.covered_bundle_shares == pytest.approx(2)
    assert inv.covered_fifo_cost == pytest.approx(0.96)
    assert inv.guaranteed_payout_on_covered_bundles == pytest.approx(2)
    assert inv.locked_pnl_on_covered_bundles == pytest.approx(1.04)
    assert inv.directional_remainder_total_shares == pytest.approx(0)


def test_simulated_fill_shows_when_a_missing_leg_creates_covered_bundles():
    p = proof()
    existing = [
        WeatherBuyFill("a1", "event-1", "A", 5, 0.10),
        WeatherBuyFill("b1", "event-1", "B", 4, 0.20),
    ]
    proposed = WeatherBuyFill("c1", "event-1", "C", 3, 0.15)
    before, after = simulate_add_fill(p, existing, proposed)
    assert before.covered_bundle_shares == 0
    assert after.covered_bundle_shares == pytest.approx(3)
    assert after.guaranteed_payout_on_covered_bundles == pytest.approx(3)
    assert after.directional_remainder_total_shares == pytest.approx(3)


def test_inventory_rejects_unknown_token_and_duplicate_fill_id():
    p = proof()
    with pytest.raises(ValueError, match="not a YES leg"):
        complete_set_inventory(p, [WeatherBuyFill("x", "event-1", "UNKNOWN", 1, 0.1)])

    fills = [
        WeatherBuyFill("same", "event-1", "A", 1, 0.1),
        WeatherBuyFill("same", "event-1", "B", 1, 0.2),
    ]
    with pytest.raises(ValueError, match="duplicate fill_id"):
        complete_set_inventory(p, fills)
