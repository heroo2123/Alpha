from __future__ import annotations

"""Passive maker/inventory primitives for weather-only shadow research.

Nothing here places, cancels or authenticates an order. Proposals are conditional on
future fills and never `TRADE NOW` authority. Inventory summaries accept only explicit
confirmed fills with unique fill identities; touching a book price is not a fill.

The V2 maker-fee boundary is fail closed: a proposal is emitted only when the market
reports a zero dynamic fee or explicitly marks the positive dynamic fee as taker-only.
``minimum_order_size`` is treated only as a minimum, never as a share-size increment.
"""

import math
from dataclasses import asdict, dataclass
from decimal import Decimal, ROUND_FLOOR

from .models import Book
from .weather_only_clob import WeatherMarketParameters
from .weather_only_contracts import WeatherBucket


MAKER_MODEL_VERSION = "weather_maker_v2_fee_fail_closed_unique_fill_accounting_shadow"
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
        if not str(self.token_id or "").strip() or not str(self.model_version or "").strip():
            raise ValueError("fair-value identity fields are required")
        values = (self.lower, self.point, self.upper, self.as_of)
        if any(isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)) for value in values):
            raise ValueError("fair-value numbers must be finite")
        if not (0.0 <= float(self.lower) <= float(self.point) <= float(self.upper) <= 1.0):
            raise ValueError("invalid fair value band")
        if float(self.as_of) < 0.0:
            raise ValueError("fair-value timestamp must be nonnegative")
        if type(self.calibrated) is not bool:
            raise ValueError("calibrated must be boolean")


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
    fill_id: str | None = None

    def __post_init__(self):
        if not str(self.event_id or "").strip() or not str(self.market_id or "").strip() or not str(self.token_id or "").strip():
            raise ValueError("fill identity fields are required")
        if str(self.outcome or "").strip().lower() not in {"yes", "no"}:
            raise ValueError("fill outcome must be Yes or No")
        if not str(self.source or "").strip():
            raise ValueError("fill source is required")
        if isinstance(self.shares, bool) or not isinstance(self.shares, (int, float)) or self.shares <= 0 or not math.isfinite(float(self.shares)):
            raise ValueError("shares must be positive and finite")
        if isinstance(self.price, bool) or not isinstance(self.price, (int, float)) or not (0.0 < float(self.price) < 1.0) or not math.isfinite(float(self.price)):
            raise ValueError("price must be between zero and one")
        if isinstance(self.fee_usdc, bool) or not isinstance(self.fee_usdc, (int, float)) or self.fee_usdc < 0 or not math.isfinite(float(self.fee_usdc)):
            raise ValueError("fee must be nonnegative and finite")
        if self.fill_id is not None and not str(self.fill_id).strip():
            raise ValueError("fill_id must be nonempty when supplied")


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
    actual_fill_identity_proven: bool
    financial_authority: bool

    def as_dict(self) -> dict:
        return asdict(self)


def _floor_to_tick(value: float, tick: float) -> float:
    q = Decimal(str(value)) / Decimal(str(tick))
    units = q.quantize(Decimal("1"), rounding=ROUND_FLOOR)
    return float(units * Decimal(str(tick)))


def _maker_fee_certified_zero(parameters: WeatherMarketParameters) -> bool:
    """True only when current V2 metadata proves no platform maker fee.

    Positive dynamic fees are usable for a maker screen only when the market explicitly
    marks them taker-only. A missing/unknown taker-only flag with a positive rate is
    not silently treated as zero maker cost.
    """
    rate = float(parameters.fee_rate)
    return rate == 0.0 or (rate > 0.0 and parameters.taker_only is True)


def _finite_book(book: Book) -> bool:
    for side in (book.bids, book.asks):
        for price, size in side:
            try:
                p, q = float(price), float(size)
            except (TypeError, ValueError, OverflowError):
                return False
            if not math.isfinite(p) or not math.isfinite(q) or not (0.0 < p < 1.0) or q <= 0.0:
                return False
    return not (book.best_bid is not None and book.best_ask is not None and book.best_bid >= book.best_ask)


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
    """Propose a non-crossing research bid using the conservative fair lower bound.

    The proposal is not an order and is never a fill. ``minimum_order_size`` is only
    an eligibility floor; this function does not invent an order-size step/precision.
    A future live-order adapter must separately certify amount precision before any
    placement could be considered.
    """
    wanted = str(outcome or "").strip().lower()
    if wanted not in {"yes", "no"}:
        raise ValueError("outcome must be Yes or No")
    if not str(event_id or "").strip():
        raise ValueError("event_id is required")
    if not isinstance(fair, FairValueBand):
        raise ValueError("fair must be FairValueBand")
    if not isinstance(parameters, WeatherMarketParameters):
        raise ValueError("parameters must be WeatherMarketParameters")
    if not isinstance(book, Book) or not _finite_book(book):
        return None
    if isinstance(minimum_conditional_edge, bool):
        raise ValueError("minimum_conditional_edge must be finite and nonnegative")
    try:
        minimum_edge = float(minimum_conditional_edge)
    except (TypeError, ValueError, OverflowError):
        raise ValueError("minimum_conditional_edge must be finite and nonnegative")
    if not math.isfinite(minimum_edge) or minimum_edge < 0.0 or minimum_edge >= 1.0:
        raise ValueError("minimum_conditional_edge must be finite and nonnegative")

    token = bucket.yes_token if wanted == "yes" else bucket.no_token
    if not token or token != book.token_id or token != fair.token_id:
        return None
    if bucket.condition_id != parameters.condition_id:
        return None
    if token not in {value for value, _ in parameters.token_outcomes}:
        return None
    if fair.model_version.strip() == "":
        return None
    if not _maker_fee_certified_zero(parameters):
        return None

    tick = float(parameters.minimum_tick_size)
    minimum_size = float(parameters.minimum_order_size)
    if not math.isfinite(tick) or tick <= 0.0 or tick >= 1.0:
        return None
    if not math.isfinite(minimum_size) or minimum_size <= 0.0:
        return None
    best_bid = float(book.best_bid) if book.best_bid is not None else None
    best_ask = float(book.best_ask) if book.best_ask is not None else None
    if best_ask is None:
        return None

    value_cap = float(fair.lower) - minimum_edge
    ask_cap = best_ask - tick
    candidate = min(value_cap, ask_cap)
    if best_bid is not None:
        candidate = max(candidate, best_bid + tick)
    bid = _floor_to_tick(candidate + 1e-12, tick)
    if bid <= 0.0 or bid >= best_ask or bid > value_cap + 1e-9:
        return None

    if isinstance(max_notional, bool):
        return None
    try:
        budget = float(max_notional)
    except (TypeError, ValueError, OverflowError):
        return None
    if not math.isfinite(budget) or budget <= 0.0:
        return None
    shares = budget / bid
    if shares + 1e-12 < minimum_size:
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
        fair_lower=float(fair.lower),
        fair_point=float(fair.point),
        fair_upper=float(fair.upper),
        conditional_edge_per_share=float(fair.lower) - bid,
        current_best_bid=best_bid,
        current_best_ask=best_ask,
        minimum_tick_size=tick,
        minimum_order_size=minimum_size,
        model_version=fair.model_version,
        calibrated=fair.calibrated,
        cancel_conditions=(
            "cancel if the fair-value lower bound falls to the bid plus required edge or below",
            "cancel/reprice after a new official observation or forecast generation",
            "cancel if the contract/source compiler becomes unsupported or stale",
            "cancel if tick/minimum-size/fee market parameters change",
            "revalidate exact order-size precision before any future live placement",
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
    """Summarize confirmed YES-bucket inventory and distinguish locked from residual risk.

    Every counted fill must carry a unique ``fill_id``. This prevents replay/retry rows
    from silently inflating complete-set inventory. A required YES token paired with a
    fill labelled ``No`` fails closed rather than trusting token identity alone.
    """
    if not str(event_id or "").strip():
        raise ValueError("event_id is required")
    if type(exactly_one_outcome_proven) is not bool:
        raise ValueError("exactly_one_outcome_proven must be boolean")
    required = tuple(dict.fromkeys(str(token).strip() for token in required_yes_tokens if str(token).strip()))
    if not required:
        raise ValueError("required YES tokens are required")
    if len(required) != len(tuple(str(token).strip() for token in required_yes_tokens if str(token).strip())):
        raise ValueError("duplicate required YES token")

    grouped: dict[str, list[ConfirmedFill]] = {token: [] for token in required}
    seen_fill_ids: set[str] = set()
    for fill in fills:
        if not isinstance(fill, ConfirmedFill):
            raise ValueError("fill type invalid")
        if fill.event_id != event_id:
            continue
        if fill.token_id not in grouped:
            continue
        if str(fill.outcome).strip().lower() != "yes":
            raise ValueError("required YES token has non-YES fill outcome")
        fill_id = str(fill.fill_id or "").strip()
        if not fill_id:
            raise ValueError("unique fill_id is required for inventory accounting")
        if fill_id in seen_fill_ids:
            raise ValueError("duplicate fill_id")
        seen_fill_ids.add(fill_id)
        grouped[fill.token_id].append(fill)

    token_rows: list[TokenInventory] = []
    for token in required:
        rows = grouped[token]
        shares = sum(float(row.shares) for row in rows)
        cost = sum(float(row.shares) * float(row.price) + float(row.fee_usdc) for row in rows)
        token_rows.append(TokenInventory(
            token_id=token,
            shares=shares,
            total_cost=cost,
            average_cost=(cost / shares) if shares > 0.0 else 0.0,
        ))

    complete = min((row.shares for row in token_rows), default=0.0)
    residual = tuple((row.token_id, max(0.0, row.shares - complete)) for row in token_rows)
    if exactly_one_outcome_proven and complete > 0.0 and all(row.shares > 0.0 for row in token_rows):
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
        exactly_one_outcome_proven=exactly_one_outcome_proven,
        actual_fill_identity_proven=True,
        financial_authority=False,
    )
