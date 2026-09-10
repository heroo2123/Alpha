from __future__ import annotations

"""Passive maker/inventory primitives for weather-only shadow research.

Nothing here places, cancels or authenticates an order.  Proposals are conditional
on future fills and never `TRADE NOW` authority.  Inventory summaries accept only
explicit/confirmed fills supplied by the caller; touching a book price is not a
fill.
"""

import math
from dataclasses import asdict, dataclass
from decimal import Decimal, ROUND_FLOOR

from .models import Book
from .weather_only_clob import WeatherMarketParameters
from .weather_only_contracts import WeatherBucket


MAKER_MODEL_VERSION = "weather_maker_v1_confirmed_fill_inventory_shadow"
MAKER_CONDITIONAL = "MAKER_CONDITIONAL"


@dataclass(frozen=True, slots=True)
class FairValueBand:
    token_id: str
    lower: float
    point: float
    upper: float
    model_version: str
    as_of: float
    calibrated: bool

    def __post_init__(self):
        if not (0.0 <= self.lower <= self.point <= self.upper <= 1.0):
            raise ValueError("invalid fair value band")


@dataclass(frozen=True, slots=True)
class MakerBidProposal:
    version: str
    evidence_class: str
    event_id: str
    market_id: str
    condition_id: str
    token_id: str
    outcome: str
    bid_price: float
    max_shares: float
    max_notional: float
    fair_lower: float
    fair_point: float
    fair_upper: float
    conditional_edge_per_share: float
    current_best_bid: float | None
    current_best_ask: float | None
    minimum_tick_size: float
    minimum_order_size: float
    model_version: str
    calibrated: bool
    cancel_conditions: tuple[str, ...]
    financial_authority: bool

    def as_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class ConfirmedFill:
    event_id: str
    market_id: str
    token_id: str
    outcome: str
    shares: float
    price: float
    fee_usdc: float = 0.0
    source: str = "USER_CONFIRMED_OR_AUTHENTICATED_FILL"

    def __post_init__(self):
        if self.shares <= 0 or not math.isfinite(self.shares):
            raise ValueError("shares must be positive")
        if not (0.0 < self.price < 1.0) or not math.isfinite(self.price):
            raise ValueError("price must be between zero and one")
        if self.fee_usdc < 0 or not math.isfinite(self.fee_usdc):
            raise ValueError("fee must be nonnegative")


@dataclass(frozen=True, slots=True)
class TokenInventory:
    token_id: str
    shares: float
    total_cost: float
    average_cost: float


@dataclass(frozen=True, slots=True)
class CompleteSetInventory:
    event_id: str
    required_tokens: tuple[str, ...]
    tokens: tuple[TokenInventory, ...]
    complete_sets: float
    complete_set_cost_basis: float | None
    locked_redemption_value: float
    locked_cost_basis: float
    locked_pnl: float | None
    residual_shares: tuple[tuple[str, float], ...]
    exactly_one_outcome_proven: bool
    financial_authority: bool

    def as_dict(self) -> dict:
        return asdict(self)


def _floor_to_tick(value: float, tick: float) -> float:
    q = Decimal(str(value)) / Decimal(str(tick))
    units = q.quantize(Decimal("1"), rounding=ROUND_FLOOR)
    return float(units * Decimal(str(tick)))


def propose_maker_bid(
    *,
    event_id: str,
    bucket: WeatherBucket,
    outcome: str,
    book: Book,
    parameters: WeatherMarketParameters,
    fair: FairValueBand,
    max_notional: float,
    minimum_conditional_edge: float = 0.05,
) -> MakerBidProposal | None:
    """Propose a queueable inside-spread bid using the conservative fair lower bound.

    The proposal is useful only when a calibrated caller supplies the fair band.
    Uncalibrated bands can still be recorded in shadow, but ``financial_authority``
    remains false either way.
    """
    wanted = outcome.strip().lower()
    if wanted not in {"yes", "no"}:
        raise ValueError("outcome must be Yes or No")
    token = bucket.yes_token if wanted == "yes" else bucket.no_token
    if not token or token != book.token_id or token != fair.token_id:
        return None
    if bucket.condition_id != parameters.condition_id:
        return None
    if token not in {value for value, _ in parameters.token_outcomes}:
        return None
    tick = parameters.minimum_tick_size
    if tick <= 0:
        return None
    best_bid = book.best_bid
    best_ask = book.best_ask
    if best_ask is None:
        return None

    value_cap = fair.lower - max(0.0, minimum_conditional_edge)
    ask_cap = best_ask - tick
    candidate = min(value_cap, ask_cap)
    if best_bid is not None:
        candidate = max(candidate, best_bid + tick)
    bid = _floor_to_tick(candidate + 1e-12, tick)
    if bid <= 0 or bid >= best_ask or bid > value_cap + 1e-9:
        return None

    budget = float(max_notional)
    if not math.isfinite(budget) or budget <= 0:
        return None
    shares = math.floor((budget / bid) / max(parameters.minimum_order_size, 1e-12)) * parameters.minimum_order_size
    if shares + 1e-12 < parameters.minimum_order_size:
        return None
    notional = shares * bid

    return MakerBidProposal(
        version=MAKER_MODEL_VERSION,
        evidence_class=MAKER_CONDITIONAL,
        event_id=str(event_id),
        market_id=bucket.market_id,
        condition_id=bucket.condition_id,
        token_id=token,
        outcome="Yes" if wanted == "yes" else "No",
        bid_price=bid,
        max_shares=shares,
        max_notional=notional,
        fair_lower=fair.lower,
        fair_point=fair.point,
        fair_upper=fair.upper,
        conditional_edge_per_share=fair.lower - bid,
        current_best_bid=best_bid,
        current_best_ask=best_ask,
        minimum_tick_size=tick,
        minimum_order_size=parameters.minimum_order_size,
        model_version=fair.model_version,
        calibrated=fair.calibrated,
        cancel_conditions=(
            "cancel if the fair-value lower bound falls to the bid or below",
            "cancel if a new official observation changes the feasible bucket set",
            "cancel if the contract/source compiler becomes unsupported or stale",
            "cancel if tick/minimum-size/fee market parameters change",
        ),
        financial_authority=False,
    )


def summarize_complete_set_inventory(
    *,
    event_id: str,
    required_yes_tokens: tuple[str, ...],
    fills: list[ConfirmedFill],
    exactly_one_outcome_proven: bool,
) -> CompleteSetInventory:
    """Summarize confirmed YES-bucket inventory and distinguish locked from residual risk."""
    required = tuple(dict.fromkeys(str(token) for token in required_yes_tokens if str(token)))
    grouped: dict[str, list[ConfirmedFill]] = {token: [] for token in required}
    for fill in fills:
        if fill.event_id != event_id or fill.token_id not in grouped:
            continue
        grouped[fill.token_id].append(fill)

    token_rows: list[TokenInventory] = []
    for token in required:
        rows = grouped[token]
        shares = sum(row.shares for row in rows)
        cost = sum(row.shares * row.price + row.fee_usdc for row in rows)
        token_rows.append(TokenInventory(
            token_id=token,
            shares=shares,
            total_cost=cost,
            average_cost=(cost / shares) if shares > 0 else 0.0,
        ))

    complete = min((row.shares for row in token_rows), default=0.0)
    residual = tuple((row.token_id, max(0.0, row.shares - complete)) for row in token_rows)
    if exactly_one_outcome_proven and complete > 0 and all(row.shares > 0 for row in token_rows):
        per_set = sum(row.average_cost for row in token_rows)
        locked_cost = complete * per_set
        redemption = complete
        pnl = redemption - locked_cost
    else:
        per_set = None
        locked_cost = 0.0
        redemption = 0.0
        pnl = None

    return CompleteSetInventory(
        event_id=str(event_id),
        required_tokens=required,
        tokens=tuple(token_rows),
        complete_sets=complete if exactly_one_outcome_proven else 0.0,
        complete_set_cost_basis=per_set,
        locked_redemption_value=redemption,
        locked_cost_basis=locked_cost,
        locked_pnl=pnl,
        residual_shares=residual,
        exactly_one_outcome_proven=bool(exactly_one_outcome_proven),
        financial_authority=False,
    )
