from __future__ import annotations

"""Passive weather-bid math and actual-fill complete-set accounting.

This module is intentionally execution-agnostic. A proposed limit order is never a
fill, and nominal share face value is never called profit. Only recorded buy fills
can enter inventory; only the portion covered across every semantically proven
outcome in a ``TemperaturePartitionProof`` receives a guaranteed-$1 bundle label.
"""

from dataclasses import dataclass
from math import floor, isfinite

from .models import Book
from .weather_structural import TemperaturePartitionProof

MAKER_PROPOSAL_VERSION = "weather_passive_value_bid_v1"
BUNDLE_INVENTORY_VERSION = "weather_complete_set_inventory_fifo_v1"


@dataclass(frozen=True, slots=True)
class WeatherBuyFill:
    fill_id: str
    event_id: str
    token_id: str
    shares: float
    price_per_share: float
    fee_paid: float = 0.0

    @property
    def total_cost(self) -> float:
        return self.shares * self.price_per_share + self.fee_paid

    @property
    def all_in_cost_per_share(self) -> float:
        return self.total_cost / self.shares


@dataclass(frozen=True, slots=True)
class PassiveBidProposal:
    version: str
    token_id: str
    conservative_value_lower: float
    best_bid: float | None
    best_ask: float | None
    proposed_bid: float
    hard_max_bid: float
    desired_shares: float
    fee_buffer_per_share: float
    minimum_required_edge: float
    conservative_edge_if_filled: float
    crosses_current_ask: bool
    screening_only: bool
    fill_assumed: bool


@dataclass(frozen=True, slots=True)
class CompleteSetInventory:
    version: str
    event_id: str
    required_tokens: tuple[str, ...]
    total_recorded_cost: float
    total_recorded_shares: float
    covered_bundle_shares: float
    covered_fifo_cost: float
    guaranteed_payout_on_covered_bundles: float
    locked_pnl_on_covered_bundles: float
    remainder_shares: tuple[tuple[str, float], ...]
    remainder_fifo_cost: tuple[tuple[str, float], ...]
    directional_remainder_total_shares: float
    directional_remainder_total_cost: float


def _validate_fill(fill: WeatherBuyFill) -> None:
    if not fill.fill_id or not fill.event_id or not fill.token_id:
        raise ValueError("fill identity fields are required")
    if not isfinite(fill.shares) or fill.shares <= 0:
        raise ValueError("fill shares must be positive and finite")
    if not isfinite(fill.price_per_share) or not 0 <= fill.price_per_share <= 1:
        raise ValueError("fill price must be finite within [0,1]")
    if not isfinite(fill.fee_paid) or fill.fee_paid < 0:
        raise ValueError("fill fee must be finite and non-negative")


def _round_down_tick(value: float, tick: float) -> float:
    if not isfinite(value) or not isfinite(tick) or tick <= 0:
        raise ValueError("tick and value must be finite; tick must be positive")
    # tiny epsilon prevents normal binary representation from moving an exact tick
    # down one extra level.
    return floor((value + 1e-12) / tick) * tick


def propose_passive_bid(
    book: Book,
    *,
    conservative_value_lower: float,
    desired_shares: float,
    minimum_required_edge: float = 0.05,
    fee_buffer_per_share: float = 0.0,
    tick_size: float = 0.01,
    preferred_bid: float | None = None,
) -> PassiveBidProposal | None:
    """Return a non-crossing value bid bounded by a conservative probability floor.

    ``conservative_value_lower`` must come from an independently calibrated model or
    deterministic settlement evidence. This function never manufactures that value.
    The proposal is a resting-order *screen*, not evidence of a fill or profit.
    """
    for name, value in (
        ("conservative_value_lower", conservative_value_lower),
        ("desired_shares", desired_shares),
        ("minimum_required_edge", minimum_required_edge),
        ("fee_buffer_per_share", fee_buffer_per_share),
        ("tick_size", tick_size),
    ):
        if not isfinite(float(value)):
            raise ValueError(f"{name} must be finite")
    if not 0 < conservative_value_lower <= 1:
        raise ValueError("conservative value must be within (0,1]")
    if desired_shares <= 0 or minimum_required_edge < 0 or fee_buffer_per_share < 0 or tick_size <= 0:
        raise ValueError("shares/tick must be positive and buffers non-negative")

    hard_max = _round_down_tick(
        conservative_value_lower - minimum_required_edge - fee_buffer_per_share,
        tick_size,
    )
    hard_max = min(hard_max, 1.0 - tick_size)
    if hard_max <= 0:
        return None

    best_bid = float(book.best_bid) if book.best_bid is not None else None
    best_ask = float(book.best_ask) if book.best_ask is not None else None

    non_crossing_ceiling = hard_max
    if best_ask is not None:
        non_crossing_ceiling = min(non_crossing_ceiling, _round_down_tick(best_ask - tick_size, tick_size))
    if non_crossing_ceiling <= 0:
        return None

    if preferred_bid is not None:
        if not isfinite(preferred_bid) or preferred_bid <= 0:
            raise ValueError("preferred_bid must be positive and finite")
        proposed = min(_round_down_tick(preferred_bid, tick_size), non_crossing_ceiling)
    elif best_bid is not None:
        proposed = min(_round_down_tick(best_bid + tick_size, tick_size), non_crossing_ceiling)
    else:
        proposed = non_crossing_ceiling

    if proposed <= 0:
        return None
    if best_bid is not None and preferred_bid is None and proposed <= best_bid + 1e-12:
        # The existing bid is already at/above our value ceiling; joining it does not
        # create the price-improving opportunity this helper is intended to propose.
        return None

    crosses = bool(best_ask is not None and proposed >= best_ask - 1e-12)
    if crosses:
        return None

    edge = conservative_value_lower - proposed - fee_buffer_per_share
    if edge + 1e-12 < minimum_required_edge:
        return None

    return PassiveBidProposal(
        version=MAKER_PROPOSAL_VERSION,
        token_id=book.token_id,
        conservative_value_lower=float(conservative_value_lower),
        best_bid=best_bid,
        best_ask=best_ask,
        proposed_bid=float(proposed),
        hard_max_bid=float(hard_max),
        desired_shares=float(desired_shares),
        fee_buffer_per_share=float(fee_buffer_per_share),
        minimum_required_edge=float(minimum_required_edge),
        conservative_edge_if_filled=float(edge),
        crosses_current_ask=False,
        screening_only=True,
        fill_assumed=False,
    )


def _fifo_cost_for_quantity(fills: list[WeatherBuyFill], quantity: float) -> tuple[float, float, float]:
    """Return (covered_cost, remainder_shares, remainder_cost) for one token."""
    remaining_to_cover = max(0.0, float(quantity))
    covered_cost = 0.0
    total_shares = sum(fill.shares for fill in fills)
    total_cost = sum(fill.total_cost for fill in fills)

    for fill in fills:
        if remaining_to_cover <= 1e-12:
            break
        used = min(fill.shares, remaining_to_cover)
        covered_cost += used * fill.all_in_cost_per_share
        remaining_to_cover -= used

    if remaining_to_cover > 1e-9:
        raise ValueError("requested FIFO covered quantity exceeds token inventory")
    remainder_shares = max(0.0, total_shares - quantity)
    remainder_cost = max(0.0, total_cost - covered_cost)
    return covered_cost, remainder_shares, remainder_cost


def complete_set_inventory(
    proof: TemperaturePartitionProof,
    fills: list[WeatherBuyFill],
) -> CompleteSetInventory:
    """Separate structurally covered bundle shares from directional leftovers.

    FIFO is used only as a transparent cost-basis convention. Because every covered
    unit contains one YES share from every proven exhaustive bucket, each covered
    bundle share has a deterministic $1 payout under the partition proof. Unmatched
    shares remain directional and receive no guaranteed face-value claim.
    """
    required = tuple(leg.yes_token for leg in proof.legs)
    required_set = set(required)
    if len(required_set) != len(required):
        raise ValueError("proof contains duplicate required tokens")

    by_token: dict[str, list[WeatherBuyFill]] = {token: [] for token in required}
    seen_fill_ids: set[str] = set()
    for fill in fills:
        _validate_fill(fill)
        if fill.fill_id in seen_fill_ids:
            raise ValueError("duplicate fill_id")
        seen_fill_ids.add(fill.fill_id)
        if fill.event_id != proof.event_id:
            raise ValueError("fill belongs to a different event")
        if fill.token_id not in required_set:
            raise ValueError("fill token is not a YES leg in the partition proof")
        by_token[fill.token_id].append(fill)

    totals = {token: sum(fill.shares for fill in rows) for token, rows in by_token.items()}
    covered = min(totals.values(), default=0.0)

    covered_cost = 0.0
    remainder_shares: list[tuple[str, float]] = []
    remainder_cost: list[tuple[str, float]] = []
    for token in required:
        token_covered_cost, leftover_shares, leftover_cost = _fifo_cost_for_quantity(by_token[token], covered)
        covered_cost += token_covered_cost
        remainder_shares.append((token, leftover_shares))
        remainder_cost.append((token, leftover_cost))

    total_cost = sum(fill.total_cost for fill in fills)
    total_shares = sum(fill.shares for fill in fills)
    payout = covered * float(proof.guaranteed_yes_payout_per_complete_set)
    locked_pnl = payout - covered_cost
    directional_shares = sum(value for _, value in remainder_shares)
    directional_cost = sum(value for _, value in remainder_cost)

    return CompleteSetInventory(
        version=BUNDLE_INVENTORY_VERSION,
        event_id=proof.event_id,
        required_tokens=required,
        total_recorded_cost=total_cost,
        total_recorded_shares=total_shares,
        covered_bundle_shares=covered,
        covered_fifo_cost=covered_cost,
        guaranteed_payout_on_covered_bundles=payout,
        locked_pnl_on_covered_bundles=locked_pnl,
        remainder_shares=tuple(remainder_shares),
        remainder_fifo_cost=tuple(remainder_cost),
        directional_remainder_total_shares=directional_shares,
        directional_remainder_total_cost=directional_cost,
    )


def simulate_add_fill(
    proof: TemperaturePartitionProof,
    existing_fills: list[WeatherBuyFill],
    proposed_fill: WeatherBuyFill,
) -> tuple[CompleteSetInventory, CompleteSetInventory]:
    """Return inventory before/after a hypothetical *filled* lot.

    Callers must not invoke this merely because a limit order was placed; it is for
    fill simulation or for comparison once an actual fill receipt exists.
    """
    before = complete_set_inventory(proof, existing_fills)
    after = complete_set_inventory(proof, [*existing_fills, proposed_fill])
    return before, after
