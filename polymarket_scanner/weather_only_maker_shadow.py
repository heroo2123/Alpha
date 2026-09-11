from __future__ import annotations

"""Fail-closed virtual maker lifecycle for weather-only research.

This module models *hypothetical* resting bids.  It never posts/cancels/authenticates
an order, never treats a book touch as a fill, and never grants financial authority.
A simulated fill requires a causal public trade print explicitly identified as a sell
aggressor.  Visible queue ahead is consumed before the virtual order receives any
simulated shares; unknown-side prints remain ambiguous instead of becoming fills.

The state machine is intentionally conservative so Stage W5 can collect queue/fill,
cancellation and markout evidence without contaminating actual-fill accounting.
"""

import hashlib
import json
import math
from dataclasses import asdict, dataclass, field, replace

from .models import Book
from .weather_only_clob import WeatherMarketParameters
from .weather_only_maker import FairValueBand, MakerBidProposal


WEATHER_MAKER_SHADOW_VERSION = "weather_maker_shadow_v1_causal_sell_print_queue_fail_closed"
RESTING = "RESTING"
PARTIALLY_SIMULATED = "PARTIALLY_SIMULATED"
SIMULATED_FILLED = "SIMULATED_FILLED"
CANCELLED = "CANCELLED"
EXPIRED = "EXPIRED"

KEEP = "KEEP"
CANCEL = "CANCEL"


class WeatherMakerShadowError(RuntimeError):
    def __init__(self, code: str):
        self.code = str(code)
        super().__init__(self.code)


def _finite(value: object, *, positive: bool = False, nonnegative: bool = False) -> float:
    if isinstance(value, bool):
        raise WeatherMakerShadowError("MAKER_NUMBER_INVALID")
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        raise WeatherMakerShadowError("MAKER_NUMBER_INVALID")
    if not math.isfinite(number):
        raise WeatherMakerShadowError("MAKER_NUMBER_INVALID")
    if positive and number <= 0.0:
        raise WeatherMakerShadowError("MAKER_NUMBER_INVALID")
    if nonnegative and number < 0.0:
        raise WeatherMakerShadowError("MAKER_NUMBER_INVALID")
    return number


def _text(value: object, code: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise WeatherMakerShadowError(code)
    return text


def _digest(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    ).hexdigest()


def market_parameter_fingerprint(parameters: WeatherMarketParameters) -> str:
    if not isinstance(parameters, WeatherMarketParameters):
        raise WeatherMakerShadowError("MAKER_MARKET_PARAMETERS_INVALID")
    return _digest({
        "condition_id": parameters.condition_id,
        "token_outcomes": parameters.token_outcomes,
        "minimum_order_size": parameters.minimum_order_size,
        "minimum_tick_size": parameters.minimum_tick_size,
        "fee_rate": parameters.fee_rate,
        "fee_exponent": parameters.fee_exponent,
        "taker_only": parameters.taker_only,
        "maker_base_fee_bps": parameters.maker_base_fee_bps,
        "taker_base_fee_bps": parameters.taker_base_fee_bps,
        "rfq_enabled": parameters.rfq_enabled,
        "taker_delay_enabled": parameters.taker_delay_enabled,
    })


def _book_level_size(book: Book, price: float) -> float:
    return sum(float(size) for level_price, size in book.bids if abs(float(level_price) - price) <= 1e-12)


def _validate_book(book: Book, token_id: str) -> None:
    if not isinstance(book, Book) or str(book.token_id) != str(token_id):
        raise WeatherMakerShadowError("MAKER_BOOK_TOKEN_MISMATCH")
    for side in (book.bids, book.asks):
        for price, size in side:
            p = _finite(price, positive=True)
            q = _finite(size, positive=True)
            if p >= 1.0 or q <= 0.0:
                raise WeatherMakerShadowError("MAKER_BOOK_LEVEL_INVALID")
    if book.best_bid is not None and book.best_ask is not None and book.best_bid >= book.best_ask:
        raise WeatherMakerShadowError("MAKER_BOOK_CROSSED")


@dataclass(frozen=True, slots=True)
class MakerShadowPolicy:
    policy_id: str
    minimum_edge_per_share: float
    max_fair_age_seconds: float
    max_order_age_seconds: float
    max_book_age_seconds: float

    def __post_init__(self):
        _text(self.policy_id, "MAKER_POLICY_ID_MISSING")
        edge = _finite(self.minimum_edge_per_share, nonnegative=True)
        fair_age = _finite(self.max_fair_age_seconds, positive=True)
        order_age = _finite(self.max_order_age_seconds, positive=True)
        book_age = _finite(self.max_book_age_seconds, positive=True)
        if edge >= 1.0 or min(fair_age, order_age, book_age) <= 0.0:
            raise WeatherMakerShadowError("MAKER_POLICY_INVALID")


@dataclass(frozen=True, slots=True)
class PublicTradePrint:
    trade_id: str
    token_id: str
    price: float
    shares: float
    received_at: float
    aggressor_side: str | None
    source: str = "CLOB_PUBLIC_TRADE"

    def __post_init__(self):
        _text(self.trade_id, "MAKER_TRADE_ID_MISSING")
        _text(self.token_id, "MAKER_TRADE_TOKEN_MISSING")
        price = _finite(self.price, positive=True)
        if price >= 1.0:
            raise WeatherMakerShadowError("MAKER_TRADE_PRICE_INVALID")
        _finite(self.shares, positive=True)
        _finite(self.received_at, nonnegative=True)
        if self.aggressor_side is not None and str(self.aggressor_side).upper() not in {"BUY", "SELL"}:
            raise WeatherMakerShadowError("MAKER_TRADE_SIDE_INVALID")


@dataclass(frozen=True, slots=True)
class VirtualMakerOrder:
    version: str
    order_id: str
    policy_id: str
    event_id: str
    market_id: str
    condition_id: str
    token_id: str
    outcome: str
    bid_price: float
    shares: float
    created_at: float
    expires_at: float
    fair_model_version: str
    fair_evidence_sha256: str
    fair_as_of: float
    contract_evidence_sha256: str
    source_generation: str
    market_parameter_sha256: str
    created_book_hash: str | None
    created_book_received_at: float
    queue_ahead_shares: float
    simulated_filled_shares: float
    status: str
    research_only: bool = field(init=False, default=True)
    actual_order_placed: bool = field(init=False, default=False)
    actual_fill_authority: bool = field(init=False, default=False)
    financial_authority: bool = field(init=False, default=False)

    @property
    def remaining_shares(self) -> float:
        return max(0.0, self.shares - self.simulated_filled_shares)

    def as_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class MakerFillSimulation:
    version: str
    order_id: str
    token_id: str
    bid_price: float
    order_shares: float
    starting_queue_ahead_shares: float
    ending_queue_ahead_shares: float
    prior_simulated_filled_shares: float
    new_simulated_fill_shares: float
    total_simulated_filled_shares: float
    remaining_order_shares: float
    eligible_sell_print_shares: float
    ambiguous_print_shares: float
    ignored_preorder_print_shares: float
    used_trade_ids: tuple[str, ...]
    last_simulated_fill_received_at: float | None
    book_touch_seen: bool
    actual_fill_authority: bool = field(init=False, default=False)
    financial_authority: bool = field(init=False, default=False)

    def as_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class MakerOrderDecision:
    action: str
    reasons: tuple[str, ...]
    evaluated_at: float
    fair_edge_per_share: float | None
    financial_authority: bool = field(init=False, default=False)

    def as_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class MakerMarkout:
    version: str
    order_id: str
    horizon_seconds: float
    filled_shares: float
    fill_price: float
    later_book_received_at: float
    best_bid: float | None
    best_ask: float | None
    midpoint: float | None
    midpoint_markout_per_share: float | None
    executable_bid_markout_per_share: float | None
    midpoint_markout_value: float | None
    executable_bid_markout_value: float | None
    simulated_fill_only: bool = field(init=False, default=True)
    financial_authority: bool = field(init=False, default=False)

    def as_dict(self) -> dict:
        return asdict(self)


def create_virtual_maker_order(
    *,
    order_id: str,
    proposal: MakerBidProposal,
    fair: FairValueBand,
    book: Book,
    parameters: WeatherMarketParameters,
    policy: MakerShadowPolicy,
    created_at: float,
    contract_evidence_sha256: str,
    source_generation: str,
) -> VirtualMakerOrder:
    if not isinstance(proposal, MakerBidProposal):
        raise WeatherMakerShadowError("MAKER_PROPOSAL_INVALID")
    if not isinstance(fair, FairValueBand):
        raise WeatherMakerShadowError("MAKER_FAIR_VALUE_INVALID")
    if not isinstance(policy, MakerShadowPolicy):
        raise WeatherMakerShadowError("MAKER_POLICY_INVALID")
    now = _finite(created_at, nonnegative=True)
    _validate_book(book, proposal.token_id)
    if proposal.financial_authority is not False:
        raise WeatherMakerShadowError("MAKER_PROPOSAL_AUTHORITY_BOUNDARY_BROKEN")
    if proposal.token_id != fair.token_id:
        raise WeatherMakerShadowError("MAKER_FAIR_TOKEN_MISMATCH")
    if proposal.model_version != fair.model_version:
        raise WeatherMakerShadowError("MAKER_FAIR_MODEL_MISMATCH")
    if proposal.condition_id != parameters.condition_id:
        raise WeatherMakerShadowError("MAKER_CONDITION_MISMATCH")
    if proposal.token_id not in {token for token, _ in parameters.token_outcomes}:
        raise WeatherMakerShadowError("MAKER_PARAMETER_TOKEN_MISMATCH")
    if abs(float(proposal.minimum_tick_size) - float(parameters.minimum_tick_size)) > 1e-12:
        raise WeatherMakerShadowError("MAKER_TICK_MISMATCH")
    if abs(float(proposal.minimum_order_size) - float(parameters.minimum_order_size)) > 1e-12:
        raise WeatherMakerShadowError("MAKER_MINIMUM_SIZE_MISMATCH")
    if not isinstance(fair.calibrated, bool):
        raise WeatherMakerShadowError("MAKER_FAIR_CALIBRATION_FLAG_INVALID")
    if now < float(fair.as_of):
        raise WeatherMakerShadowError("MAKER_FAIR_FROM_FUTURE")
    if now - float(fair.as_of) > policy.max_fair_age_seconds:
        raise WeatherMakerShadowError("MAKER_FAIR_STALE_AT_CREATION")
    if book.received_at is None:
        raise WeatherMakerShadowError("MAKER_BOOK_RECEIPT_MISSING")
    book_received = _finite(book.received_at, nonnegative=True)
    if now < book_received or now - book_received > policy.max_book_age_seconds:
        raise WeatherMakerShadowError("MAKER_BOOK_STALE_AT_CREATION")
    edge = float(fair.lower) - float(proposal.bid_price)
    if edge + 1e-12 < policy.minimum_edge_per_share:
        raise WeatherMakerShadowError("MAKER_EDGE_BELOW_POLICY")
    if float(proposal.max_shares) + 1e-12 < float(parameters.minimum_order_size):
        raise WeatherMakerShadowError("MAKER_ORDER_BELOW_MINIMUM_SIZE")
    contract_sha = _text(contract_evidence_sha256, "MAKER_CONTRACT_EVIDENCE_MISSING")
    if len(contract_sha) != 64 or any(ch not in "0123456789abcdef" for ch in contract_sha.lower()):
        raise WeatherMakerShadowError("MAKER_CONTRACT_EVIDENCE_INVALID")
    generation = _text(source_generation, "MAKER_SOURCE_GENERATION_MISSING")
    oid = _text(order_id, "MAKER_ORDER_ID_MISSING")

    queue_ahead = _book_level_size(book, float(proposal.bid_price))
    return VirtualMakerOrder(
        version=WEATHER_MAKER_SHADOW_VERSION,
        order_id=oid,
        policy_id=policy.policy_id,
        event_id=_text(proposal.event_id, "MAKER_EVENT_ID_MISSING"),
        market_id=_text(proposal.market_id, "MAKER_MARKET_ID_MISSING"),
        condition_id=_text(proposal.condition_id, "MAKER_CONDITION_ID_MISSING"),
        token_id=_text(proposal.token_id, "MAKER_TOKEN_ID_MISSING"),
        outcome=_text(proposal.outcome, "MAKER_OUTCOME_MISSING"),
        bid_price=float(proposal.bid_price),
        shares=float(proposal.max_shares),
        created_at=now,
        expires_at=now + policy.max_order_age_seconds,
        fair_model_version=fair.model_version,
        fair_evidence_sha256=_digest({
            "token_id": fair.token_id,
            "lower": fair.lower,
            "point": fair.point,
            "upper": fair.upper,
            "model_version": fair.model_version,
            "as_of": fair.as_of,
            "calibrated": fair.calibrated,
        }),
        fair_as_of=float(fair.as_of),
        contract_evidence_sha256=contract_sha.lower(),
        source_generation=generation,
        market_parameter_sha256=market_parameter_fingerprint(parameters),
        created_book_hash=book.book_hash,
        created_book_received_at=book_received,
        queue_ahead_shares=float(queue_ahead),
        simulated_filled_shares=0.0,
        status=RESTING,
    )


def simulate_public_trade_progression(
    order: VirtualMakerOrder,
    trades: list[PublicTradePrint] | tuple[PublicTradePrint, ...],
    *,
    latest_book: Book | None = None,
) -> tuple[VirtualMakerOrder, MakerFillSimulation]:
    if not isinstance(order, VirtualMakerOrder):
        raise WeatherMakerShadowError("MAKER_ORDER_INVALID")
    if order.status not in {RESTING, PARTIALLY_SIMULATED}:
        raise WeatherMakerShadowError("MAKER_ORDER_NOT_RESTING")
    if latest_book is not None:
        _validate_book(latest_book, order.token_id)

    seen: set[str] = set()
    queue = float(order.queue_ahead_shares)
    remaining = float(order.remaining_shares)
    prior = float(order.simulated_filled_shares)
    newly_filled = 0.0
    eligible = 0.0
    ambiguous = 0.0
    ignored_preorder = 0.0
    used: list[str] = []
    last_fill_at: float | None = None

    ordered = sorted(trades, key=lambda row: (float(row.received_at), str(row.trade_id)))
    for trade in ordered:
        if not isinstance(trade, PublicTradePrint):
            raise WeatherMakerShadowError("MAKER_TRADE_TYPE_INVALID")
        if trade.trade_id in seen:
            raise WeatherMakerShadowError("MAKER_DUPLICATE_TRADE_ID")
        seen.add(trade.trade_id)
        if trade.token_id != order.token_id:
            continue
        if trade.received_at <= order.created_at + 1e-12:
            ignored_preorder += float(trade.shares)
            continue
        if float(trade.price) > order.bid_price + 1e-12:
            continue
        side = str(trade.aggressor_side).upper() if trade.aggressor_side is not None else None
        if side is None:
            ambiguous += float(trade.shares)
            continue
        if side != "SELL":
            continue

        quantity = float(trade.shares)
        eligible += quantity
        if queue > 0.0:
            consumed = min(queue, quantity)
            queue -= consumed
            quantity -= consumed
        if quantity <= 1e-12 or remaining <= 1e-12:
            continue
        fill = min(remaining, quantity)
        newly_filled += fill
        remaining -= fill
        used.append(trade.trade_id)
        last_fill_at = float(trade.received_at)

    total = min(order.shares, prior + newly_filled)
    if total + 1e-12 >= order.shares:
        status = SIMULATED_FILLED
    elif total > 1e-12:
        status = PARTIALLY_SIMULATED
    else:
        status = RESTING
    updated = replace(
        order,
        queue_ahead_shares=max(0.0, queue),
        simulated_filled_shares=total,
        status=status,
    )
    touch = bool(
        latest_book is not None
        and (
            (latest_book.best_ask is not None and latest_book.best_ask <= order.bid_price + 1e-12)
            or (latest_book.last_trade_price is not None and latest_book.last_trade_price <= order.bid_price + 1e-12)
        )
    )
    simulation = MakerFillSimulation(
        version=WEATHER_MAKER_SHADOW_VERSION,
        order_id=order.order_id,
        token_id=order.token_id,
        bid_price=order.bid_price,
        order_shares=order.shares,
        starting_queue_ahead_shares=order.queue_ahead_shares,
        ending_queue_ahead_shares=max(0.0, queue),
        prior_simulated_filled_shares=prior,
        new_simulated_fill_shares=newly_filled,
        total_simulated_filled_shares=total,
        remaining_order_shares=max(0.0, order.shares - total),
        eligible_sell_print_shares=eligible,
        ambiguous_print_shares=ambiguous,
        ignored_preorder_print_shares=ignored_preorder,
        used_trade_ids=tuple(used),
        last_simulated_fill_received_at=last_fill_at,
        book_touch_seen=touch,
    )
    return updated, simulation


def evaluate_virtual_order(
    order: VirtualMakerOrder,
    *,
    fair: FairValueBand,
    book: Book,
    parameters: WeatherMarketParameters,
    policy: MakerShadowPolicy,
    evaluated_at: float,
    contract_evidence_sha256: str,
    source_generation: str,
    market_open: bool,
    accepting_orders: bool,
) -> MakerOrderDecision:
    if not isinstance(order, VirtualMakerOrder):
        raise WeatherMakerShadowError("MAKER_ORDER_INVALID")
    now = _finite(evaluated_at, nonnegative=True)
    _validate_book(book, order.token_id)
    reasons: list[str] = []
    if order.policy_id != policy.policy_id:
        reasons.append("POLICY_ID_CHANGED")
    if order.status not in {RESTING, PARTIALLY_SIMULATED}:
        reasons.append("ORDER_NOT_RESTING")
    if now >= order.expires_at - 1e-12:
        reasons.append("ORDER_EXPIRED")
    if market_open is not True or accepting_orders is not True:
        reasons.append("MARKET_NOT_OPEN_AND_ACCEPTING")
    if fair.token_id != order.token_id:
        reasons.append("FAIR_TOKEN_CHANGED")
    if fair.model_version != order.fair_model_version:
        reasons.append("FAIR_MODEL_CHANGED")
    if now < float(fair.as_of) or now - float(fair.as_of) > policy.max_fair_age_seconds:
        reasons.append("FAIR_STALE")
    edge = None
    try:
        edge = float(fair.lower) - order.bid_price
        if not math.isfinite(edge) or edge + 1e-12 < policy.minimum_edge_per_share:
            reasons.append("FAIR_EDGE_ERODED")
    except Exception:
        reasons.append("FAIR_INVALID")
    if book.received_at is None or now < float(book.received_at) or now - float(book.received_at) > policy.max_book_age_seconds:
        reasons.append("BOOK_STALE")
    if market_parameter_fingerprint(parameters) != order.market_parameter_sha256:
        reasons.append("MARKET_PARAMETERS_CHANGED")
    if str(parameters.condition_id) != order.condition_id:
        reasons.append("CONDITION_CHANGED")
    if str(contract_evidence_sha256).strip().lower() != order.contract_evidence_sha256:
        reasons.append("CONTRACT_EVIDENCE_CHANGED")
    if str(source_generation).strip() != order.source_generation:
        reasons.append("SOURCE_GENERATION_CHANGED")
    # A crossed/touched virtual bid is not counted as a fill.  It requires public
    # trade evidence or an authenticated account receipt; cancel/review it instead.
    if book.best_ask is not None and book.best_ask <= order.bid_price + 1e-12:
        reasons.append("BOOK_MARKETABLE_TOUCH_REQUIRES_FILL_RECONCILIATION")

    unique = tuple(dict.fromkeys(reasons))
    return MakerOrderDecision(
        action=CANCEL if unique else KEEP,
        reasons=unique,
        evaluated_at=now,
        fair_edge_per_share=edge,
    )


def apply_cancel_decision(order: VirtualMakerOrder, decision: MakerOrderDecision) -> VirtualMakerOrder:
    if decision.action != CANCEL:
        return order
    status = EXPIRED if "ORDER_EXPIRED" in decision.reasons else CANCELLED
    return replace(order, status=status)


def markout_simulated_fill(
    order: VirtualMakerOrder,
    simulation: MakerFillSimulation,
    later_book: Book,
    *,
    horizon_seconds: float,
    exit_fee_per_share: float = 0.0,
) -> MakerMarkout:
    if simulation.order_id != order.order_id or simulation.token_id != order.token_id:
        raise WeatherMakerShadowError("MAKER_MARKOUT_IDENTITY_MISMATCH")
    if simulation.total_simulated_filled_shares <= 0.0 or simulation.last_simulated_fill_received_at is None:
        raise WeatherMakerShadowError("MAKER_MARKOUT_NO_SIMULATED_FILL")
    _validate_book(later_book, order.token_id)
    horizon = _finite(horizon_seconds, nonnegative=True)
    fee = _finite(exit_fee_per_share, nonnegative=True)
    if later_book.received_at is None:
        raise WeatherMakerShadowError("MAKER_MARKOUT_BOOK_RECEIPT_MISSING")
    received = _finite(later_book.received_at, nonnegative=True)
    minimum_time = float(simulation.last_simulated_fill_received_at) + horizon
    if received + 1e-12 < minimum_time:
        raise WeatherMakerShadowError("MAKER_MARKOUT_HORIZON_NOT_REACHED")

    best_bid = float(later_book.best_bid) if later_book.best_bid is not None else None
    best_ask = float(later_book.best_ask) if later_book.best_ask is not None else None
    midpoint = (best_bid + best_ask) / 2.0 if best_bid is not None and best_ask is not None else None
    mid_per = midpoint - order.bid_price if midpoint is not None else None
    executable_per = best_bid - order.bid_price - fee if best_bid is not None else None
    shares = float(simulation.total_simulated_filled_shares)
    return MakerMarkout(
        version=WEATHER_MAKER_SHADOW_VERSION,
        order_id=order.order_id,
        horizon_seconds=horizon,
        filled_shares=shares,
        fill_price=order.bid_price,
        later_book_received_at=received,
        best_bid=best_bid,
        best_ask=best_ask,
        midpoint=midpoint,
        midpoint_markout_per_share=mid_per,
        executable_bid_markout_per_share=executable_per,
        midpoint_markout_value=(mid_per * shares) if mid_per is not None else None,
        executable_bid_markout_value=(executable_per * shares) if executable_per is not None else None,
    )
