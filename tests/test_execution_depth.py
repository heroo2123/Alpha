from decimal import Decimal

import pytest

from polymarket_scanner.execution_depth import (
    build_safe_ask_ladder,
    plan_equal_share_bundle_limits,
)


def unit_cost(price: Decimal) -> Decimal:
    # Simple monotonic fee stand-in; execution-certificate tests cover the real V2 fee curve.
    return price + Decimal("0.001")


def ladder(rows):
    return build_safe_ask_ladder(
        rows,
        tick=Decimal("0.01"),
        capacity_fraction=Decimal("0.50"),
        unit_cost=unit_cost,
    )


def test_ladder_aggregates_duplicate_levels_and_haircuts_each_level():
    result = ladder([
        ("0.40", "4"),
        ("0.40", "6"),
        ("0.42", "10"),
    ])
    assert len(result) == 2
    assert result[0]["visible_size"] == Decimal("10")
    assert result[0]["safe_size"] == Decimal("5.00")
    assert result[1]["cumulative_visible_size"] == Decimal("20")
    assert result[1]["cumulative_safe_size"] == Decimal("10.00")


def test_ladder_rejects_non_tick_aligned_price():
    with pytest.raises(ValueError, match="tick aligned"):
        ladder([("0.405", "10")])


def test_equal_share_plan_expands_bottleneck_depth_with_worst_case_limits():
    a = ladder([("0.40", "10"), ("0.42", "20")])
    b = ladder([("0.50", "30")])
    plan = plan_equal_share_bundle_limits(
        [a, b],
        max_bundle_cost=Decimal("0.93"),
        minimum_bundle_shares=Decimal("5"),
    )
    # Leg A top level provides only 5 safe shares after haircut, so the planner can
    # move its cap to 0.42 and expose 15 safe shares, matching leg B's 15.
    assert plan["safe_common_shares"] == Decimal("15.00")
    assert plan["legs"][0]["price"] == Decimal("0.42")
    assert plan["legs"][1]["price"] == Decimal("0.50")
    assert plan["combined_limit_cost"] == Decimal("0.922")


def test_plan_refuses_depth_expansion_that_spends_edge_floor():
    a = ladder([("0.40", "10"), ("0.44", "50")])
    b = ladder([("0.50", "50")])
    plan = plan_equal_share_bundle_limits(
        [a, b],
        max_bundle_cost=Decimal("0.92"),
        minimum_bundle_shares=Decimal("5"),
    )
    # Top caps cost .902 and are valid; raising A to .44 would cost .942, so the
    # certified capacity remains at the top-level 5 safe shares.
    assert plan["safe_common_shares"] == Decimal("5.00")
    assert plan["legs"][0]["price"] == Decimal("0.40")


def test_plan_fails_when_top_limits_already_break_edge_floor():
    a = ladder([("0.50", "100")])
    b = ladder([("0.50", "100")])
    with pytest.raises(ValueError, match="above the edge floor"):
        plan_equal_share_bundle_limits(
            [a, b],
            max_bundle_cost=Decimal("0.99"),
            minimum_bundle_shares=Decimal("5"),
        )


def test_plan_fails_if_haircut_depth_cannot_reach_minimum_bundle():
    a = ladder([("0.40", "4")])  # 2 safe shares
    b = ladder([("0.50", "100")])
    with pytest.raises(ValueError, match="cannot satisfy"):
        plan_equal_share_bundle_limits(
            [a, b],
            max_bundle_cost=Decimal("0.95"),
            minimum_bundle_shares=Decimal("5"),
        )


def test_unique_actionable_capacity_is_common_equal_share_not_sum_of_legs():
    a = ladder([("0.20", "40")])
    b = ladder([("0.30", "20")])
    c = ladder([("0.40", "60")])
    plan = plan_equal_share_bundle_limits(
        [a, b, c],
        max_bundle_cost=Decimal("0.95"),
        minimum_bundle_shares=Decimal("5"),
    )
    assert plan["safe_common_shares"] == Decimal("10.00")
    assert plan["visible_common_shares"] == Decimal("20")
