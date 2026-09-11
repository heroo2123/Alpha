from __future__ import annotations

"""Fail-closed virtual maker lifecycle for weather-only research.

This module never places, authenticates, cancels, or assumes ownership of a real
Polymarket order.  It exists to study the passive/maker lane prospectively while
preserving the full-bot safety lessons:

* a resting quote is not a fill;
* an equal-price public trade is queue-ambiguous and is not counted as a fill;
* only a *strict* public trade-through may create a shadow fill;
* a shadow fill is never eligible for actual inventory/accounting;
* fair-value/model, market-parameter, book, and policy identities are frozen and
  revalidated on every lifecycle evaluation;
* known invalidation events are ordered causally against trade-through evidence;
* post-fill markouts are research evidence, never realized PnL.

The lifecycle is deliberately pure.  A later research runtime may persist these
records, but it must not reinterpret them as authenticated exchange receipts.
"""

import hashlib
import json
import math
from dataclasses import asdict, dataclass
from decimal import Decimal, ROUND_FLOOR

from .models import Book
from .weather_only_clob import WeatherMarketParameters
from .weather_only_maker import FairValueBand, MakerBidProposal


MAKER_LIFECYCLE_VERSION = "weather_maker_lifecycle_v1_strict_trade_through_shadow_only"
SHADOW_FILL_CLASS = "STRICT_PUBLIC_TRADE_THROUGH_SHADOW_FILL"

ACTION_KEEP = "KEEP"
ACTION_REPRICE = "REPRICE"
ACTION_CANCEL = "CANCEL"
ACTION_EXPIRE = "EXPIRE"
ACTION_SHADOW_FILL = "SHADOW_FILL"


class MakerLifecycleError(RuntimeError):
    def __init__(self, code: str):
        self.code = str(code)
        super().__init__(self.code)


def _canonical_sha(payload: object) -> str:
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _finite(value: object, *, nonnegative: bool = False) -> float:
    if isinstance(value, bool):
        raise MakerLifecycleError("MAKER_NUMBER_INVALID")
    try:
        out = float(value)
    except (TypeError, ValueError, OverflowError):
        raise MakerLifecycleError("MAKER_NUMBER_INVALID")
    if not math.isfinite(out) or (nonnegative and out < 0.0):
        raise MakerLifecycleError("MAKER_NUMBER_INVALID")
    return out


def _maker_fee_certified_zero(parameters: WeatherMarketParameters) -> bool:
    rate = float(parameters.fee_rate)
    return rate == 0.0 or (rate > 0.0 and parameters.taker_only is True)


def _book_provenance(book: Book) -> tuple[float, str, int, str]:
    if not isinstance(book, Book):
        raise MakerLifecycleError("MAKER_BOOK_TYPE_INVALID")
    received = _finite(book.received_at, nonnegative=True)
    source = str(book.source or "").strip()
    book_hash = str(book.book_hash or "").strip()
    epoch = book.source_epoch
    if not source or not book_hash or isinstance(epoch, bool) or not isinstance(epoch, int) or epoch < 0:
        raise MakerLifecycleError("MAKER_BOOK_PROVENANCE_MISSING")
    for side in (book.bids, book.asks):
        for price, size in side:
            p = _finite(price)
            q = _finite(size)
            if not 0.0 < p < 1.0 or q <= 0.0:
                raise MakerLifecycleError("MAKER_BOOK_LEVEL_INVALID")
    if book.best_bid is not None and book.best_ask is not None and book.best_bid >= book.best_ask:
        raise MakerLifecycleError("MAKER_BOOK_CROSSED")
    return received, source, epoch, book_hash


def _parameter_identity(parameters: WeatherMarketParameters) -> dict:
    if not isinstance(parameters, WeatherMarketParameters):
        raise MakerLifecycleError("MAKER_PARAMETERS_TYPE_INVALID")
    _finite(parameters.received_at, nonnegative=True)
    tick = _finite(parameters.minimum_tick_size)
    minimum_size = _finite(parameters.minimum_order_size)
    fee_rate = _finite(parameters.fee_rate, nonnegative=True)
    if not 0.0 < tick < 1.0 or minimum_size <= 0.0 or not 0.0 <= fee_rate <= 1.0:
        raise MakerLifecycleError("MAKER_PARAMETERS_INVALID")
    if isinstance(parameters.fee_exponent, bool) or not isinstance(parameters.fee_exponent, int) or parameters.fee_exponent < 0:
        raise MakerLifecycleError("MAKER_PARAMETERS_INVALID")
    condition = str(parameters.condition_id or "").strip()
    tokens = tuple((str(token).strip(), str(outcome).strip()) for token, outcome in parameters.token_outcomes)
    if not condition or len(tokens) != 2 or any(not token or not outcome for token, outcome in tokens):
        raise MakerLifecycleError("MAKER_PARAMETERS_INVALID")
    return {
        "condition_id": condition,
        "token_outcomes": tokens,
        "minimum_order_size": minimum_size,
        "minimum_tick_size": tick,
        "fee_rate": fee_rate,
        "fee_exponent": int(parameters.fee_exponent),
        "taker_only": parameters.taker_only,
        "maker_base_fee_bps": int(parameters.maker_base_fee_bps),
        "taker_base_fee_bps": int(parameters.taker_base_fee_bps),
        "rfq_enabled": parameters.rfq_enabled,
        "taker_delay_enabled": parameters.taker_delay_enabled,
    }


def _floor_to_tick(value: float, tick: float) -> float:
    units = (Decimal(str(value)) / Decimal(str(tick))).quantize(Decimal("1"), rounding=ROUND_FLOOR)
    return float(units * Decimal(str(tick)))


@dataclass(frozen=True, slots=True)
class MakerLifecyclePolicy:
    policy_id: str
    minimum_edge: float = 0.05
    max_fair_age_seconds: float = 900.0
    max_book_age_seconds: float = 10.0
    max_parameter_age_seconds: float = 30.0
    max_order_age_seconds: float = 1800.0
    reprice_min_ticks: int = 1

    def __post_init__(self):
        if not str(self.policy_id or "").strip():
            raise ValueError("policy_id is required")
        for name in (
            "minimum_edge",
            "max_fair_age_seconds",
            "max_book_age_seconds",
            "max_parameter_age_seconds",
            "max_order_age_seconds",
        ):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
                raise ValueError(f"{name} must be finite")
        if not 0.0 <= float(self.minimum_edge) < 1.0:
            raise ValueError("minimum_edge out of range")
        if any(float(getattr(self, name)) <= 0.0 for name in (
            "max_fair_age_seconds", "max_book_age_seconds", "max_parameter_age_seconds", "max_order_age_seconds"
        )):
            raise ValueError("age limits must be positive")
        if isinstance(self.reprice_min_ticks, bool) or not isinstance(self.reprice_min_ticks, int) or self.reprice_min_ticks < 1:
            raise ValueError("reprice_min_ticks must be a positive integer")

    def as_dict(self) -> dict:
        return asdict(self)

    @property
    def sha256(self) -> str:
        return _canonical_sha(self.as_dict())


@dataclass(frozen=True, slots=True)
class PublicTradePrint:
    trade_id: str
    token_id: str
    price: float
    size: float
    received_at: float
    source: str
    source_epoch: int

    def __post_init__(self):
        if not str(self.trade_id or "").strip() or not str(self.token_id or "").strip() or not str(self.source or "").strip():
            raise ValueError("trade identity is required")
        for name in ("price", "size", "received_at"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
                raise ValueError(f"{name} must be finite")
        if not 0.0 < float(self.price) < 1.0 or float(self.size) <= 0.0 or float(self.received_at) < 0.0:
            raise ValueError("trade numeric fields invalid")
        if isinstance(self.source_epoch, bool) or not isinstance(self.source_epoch, int) or self.source_epoch < 0:
            raise ValueError("source_epoch invalid")

    def as_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class VirtualMakerOrder:
    version: str
    order_id: str
    event_id: str
    market_id: str
    condition_id: str
    token_id: str
    outcome: str
    bid_price: float
    max_shares: float
    created_at: float
    fair_model_version: str
    fair_as_of: float
    opening_fair_lower: float
    opening_fair_point: float
    opening_fair_upper: float
    opening_book_received_at: float
    opening_book_source: str
    opening_book_source_epoch: int
    opening_book_hash: str
    opening_parameter_identity: dict
    proposal_sha256: str
    policy_sha256: str
    evidence_sha256: str
    actual_order: bool = False
    financial_authority: bool = False

    def as_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class ShadowMakerFill:
    version: str
    fill_id: str
    order_id: str
    event_id: str
    market_id: str
    token_id: str
    outcome: str
    shares: float
    virtual_fill_price: float
    evidence_class: str
    public_trade_id: str
    public_trade_price: float
    public_trade_size: float
    observed_at: float
    actual_fill: bool
    inventory_eligible: bool
    financial_authority: bool

    def as_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class MakerLifecycleDecision:
    version: str
    order_id: str
    action: str
    observed_at: float
    reason: str
    suggested_bid: float | None
    touch_observed: bool
    equal_price_trade_queue_ambiguous: bool
    shadow_fill: ShadowMakerFill | None
    actual_fill_authority: bool
    financial_authority: bool

    def as_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class ShadowFillMarkout:
    version: str
    shadow_fill_id: str
    token_id: str
    observed_at: float
    age_seconds: float
    best_bid: float | None
    best_ask: float | None
    midpoint: float | None
    best_bid_markout_per_share: float | None
    midpoint_markout_per_share: float | None
    best_bid_markout_notional: float | None
    midpoint_markout_notional: float | None
    realized_pnl: bool
    financial_authority: bool

    def as_dict(self) -> dict:
        return asdict(self)


def _order_payload_without_digest(order: VirtualMakerOrder) -> dict:
    payload = order.as_dict()
    payload.pop("evidence_sha256", None)
    return payload


def validate_virtual_maker_order(order: VirtualMakerOrder, policy: MakerLifecyclePolicy) -> None:
    if not isinstance(order, VirtualMakerOrder):
        raise MakerLifecycleError("MAKER_ORDER_TYPE_INVALID")
    if order.version != MAKER_LIFECYCLE_VERSION:
        raise MakerLifecycleError("MAKER_ORDER_VERSION_MISMATCH")
    if order.actual_order is not False or order.financial_authority is not False:
        raise MakerLifecycleError("MAKER_ORDER_AUTHORITY_INVALID")
    if order.policy_sha256 != policy.sha256:
        raise MakerLifecycleError("MAKER_ORDER_POLICY_MISMATCH")
    if order.evidence_sha256 != _canonical_sha(_order_payload_without_digest(order)):
        raise MakerLifecycleError("MAKER_ORDER_EVIDENCE_DIGEST_MISMATCH")


def open_virtual_maker_order(
    *,
    order_id: str,
    proposal: MakerBidProposal,
    fair: FairValueBand,
    book: Book,
    parameters: WeatherMarketParameters,
    policy: MakerLifecyclePolicy,
    now: float,
) -> VirtualMakerOrder:
    """Freeze a shadow-only resting-order hypothesis from causal exact evidence."""
    if not str(order_id or "").strip():
        raise MakerLifecycleError("MAKER_ORDER_ID_REQUIRED")
    if not isinstance(proposal, MakerBidProposal) or proposal.financial_authority is not False:
        raise MakerLifecycleError("MAKER_PROPOSAL_INVALID")
    if not isinstance(fair, FairValueBand):
        raise MakerLifecycleError("MAKER_FAIR_VALUE_INVALID")
    now_f = _finite(now, nonnegative=True)
    received, source, epoch, book_hash = _book_provenance(book)
    parameter_identity = _parameter_identity(parameters)
    parameter_received = float(parameters.received_at)

    if received > now_f + 1e-9 or now_f - received > float(policy.max_book_age_seconds):
        raise MakerLifecycleError("MAKER_OPEN_BOOK_NOT_FRESH")
    if parameter_received > now_f + 1e-9 or now_f - parameter_received > float(policy.max_parameter_age_seconds):
        raise MakerLifecycleError("MAKER_OPEN_PARAMETERS_NOT_FRESH")
    if float(fair.as_of) > now_f + 1e-9 or now_f - float(fair.as_of) > float(policy.max_fair_age_seconds):
        raise MakerLifecycleError("MAKER_OPEN_FAIR_NOT_FRESH")
    if proposal.token_id != fair.token_id or proposal.token_id != book.token_id:
        raise MakerLifecycleError("MAKER_OPEN_TOKEN_MISMATCH")
    if proposal.condition_id != parameters.condition_id:
        raise MakerLifecycleError("MAKER_OPEN_CONDITION_MISMATCH")
    if proposal.model_version != fair.model_version:
        raise MakerLifecycleError("MAKER_OPEN_MODEL_MISMATCH")
    if abs(float(proposal.fair_lower) - float(fair.lower)) > 1e-12 or abs(float(proposal.fair_point) - float(fair.point)) > 1e-12 or abs(float(proposal.fair_upper) - float(fair.upper)) > 1e-12:
        raise MakerLifecycleError("MAKER_OPEN_FAIR_PAYLOAD_MISMATCH")
    if abs(float(proposal.bid_price) + float(policy.minimum_edge) - float(fair.lower)) > 1e-9 and float(fair.lower) - float(proposal.bid_price) + 1e-12 < float(policy.minimum_edge):
        raise MakerLifecycleError("MAKER_OPEN_EDGE_INSUFFICIENT")
    if float(fair.lower) - float(proposal.bid_price) + 1e-12 < float(policy.minimum_edge):
        raise MakerLifecycleError("MAKER_OPEN_EDGE_INSUFFICIENT")
    if not _maker_fee_certified_zero(parameters):
        raise MakerLifecycleError("MAKER_OPEN_FEE_AUTHORITY_MISSING")
    if proposal.current_best_bid != book.best_bid or proposal.current_best_ask != book.best_ask:
        raise MakerLifecycleError("MAKER_OPEN_BOOK_PROPOSAL_MISMATCH")
    if proposal.max_shares <= 0.0 or proposal.bid_price <= 0.0 or proposal.bid_price >= 1.0:
        raise MakerLifecycleError("MAKER_OPEN_PROPOSAL_NUMBERS_INVALID")

    proposal_sha = _canonical_sha(proposal.as_dict())
    provisional = VirtualMakerOrder(
        version=MAKER_LIFECYCLE_VERSION,
        order_id=str(order_id),
        event_id=str(proposal.event_id),
        market_id=str(proposal.market_id),
        condition_id=str(proposal.condition_id),
        token_id=str(proposal.token_id),
        outcome=str(proposal.outcome),
        bid_price=float(proposal.bid_price),
        max_shares=float(proposal.max_shares),
        created_at=now_f,
        fair_model_version=str(fair.model_version),
        fair_as_of=float(fair.as_of),
        opening_fair_lower=float(fair.lower),
        opening_fair_point=float(fair.point),
        opening_fair_upper=float(fair.upper),
        opening_book_received_at=received,
        opening_book_source=source,
        opening_book_source_epoch=epoch,
        opening_book_hash=book_hash,
        opening_parameter_identity=parameter_identity,
        proposal_sha256=proposal_sha,
        policy_sha256=policy.sha256,
        evidence_sha256="",
        actual_order=False,
        financial_authority=False,
    )
    evidence_sha = _canonical_sha(_order_payload_without_digest(provisional))
    order = VirtualMakerOrder(**{**provisional.as_dict(), "evidence_sha256": evidence_sha})
    validate_virtual_maker_order(order, policy)
    return order


def _dedupe_trades(trades: tuple[PublicTradePrint, ...], token_id: str) -> tuple[PublicTradePrint, ...]:
    seen: dict[str, PublicTradePrint] = {}
    for trade in trades:
        if not isinstance(trade, PublicTradePrint):
            raise MakerLifecycleError("MAKER_PUBLIC_TRADE_TYPE_INVALID")
        if trade.token_id != token_id:
            continue
        prior = seen.get(trade.trade_id)
        if prior is not None and prior != trade:
            raise MakerLifecycleError("MAKER_PUBLIC_TRADE_ID_CONFLICT")
        seen[trade.trade_id] = trade
    return tuple(sorted(seen.values(), key=lambda row: (row.received_at, row.trade_id)))


def _invalidation(
    *,
    order: VirtualMakerOrder,
    fair: FairValueBand,
    parameters: WeatherMarketParameters,
    policy: MakerLifecyclePolicy,
    now: float,
) -> tuple[float | None, str | None, str | None]:
    candidates: list[tuple[float, str, str]] = []
    expiry = float(order.created_at) + float(policy.max_order_age_seconds)
    if expiry <= now + 1e-12:
        candidates.append((expiry, ACTION_EXPIRE, "ORDER_MAX_AGE_REACHED"))

    fair_invalid_at: float | None = None
    fair_reason: str | None = None
    if fair.token_id != order.token_id or fair.model_version != order.fair_model_version:
        fair_invalid_at = float(fair.as_of)
        fair_reason = "FAIR_IDENTITY_CHANGED"
    elif float(fair.lower) - float(order.bid_price) + 1e-12 < float(policy.minimum_edge):
        fair_invalid_at = float(fair.as_of)
        fair_reason = "FAIR_LOWER_BOUND_EDGE_LOST"
    elif float(fair.as_of) + float(policy.max_fair_age_seconds) <= now + 1e-12:
        fair_invalid_at = float(fair.as_of) + float(policy.max_fair_age_seconds)
        fair_reason = "FAIR_VALUE_STALE"
    if fair_invalid_at is not None:
        candidates.append((fair_invalid_at, ACTION_CANCEL, str(fair_reason)))

    parameter_identity = _parameter_identity(parameters)
    if parameter_identity != order.opening_parameter_identity:
        candidates.append((float(parameters.received_at), ACTION_CANCEL, "MARKET_PARAMETERS_CHANGED"))
    elif not _maker_fee_certified_zero(parameters):
        candidates.append((float(parameters.received_at), ACTION_CANCEL, "MAKER_FEE_AUTHORITY_LOST"))

    if not candidates:
        return None, None, None
    at, action, reason = min(candidates, key=lambda row: (row[0], 0 if row[1] == ACTION_CANCEL else 1, row[2]))
    return at, action, reason


def evaluate_virtual_maker_order(
    *,
    order: VirtualMakerOrder,
    current_book: Book,
    fair: FairValueBand,
    parameters: WeatherMarketParameters,
    public_trades: tuple[PublicTradePrint, ...] = (),
    policy: MakerLifecyclePolicy,
    now: float,
) -> MakerLifecycleDecision:
    """Evaluate one resting-order hypothesis without asserting a real fill.

    A strict public trade-through (trade price < our virtual resting bid) is the only
    fill simulation accepted here.  A trade exactly at our bid is queue-ambiguous and
    a quote merely touching/crossing our bid is only a touch observation.
    """
    validate_virtual_maker_order(order, policy)
    now_f = _finite(now, nonnegative=True)
    if now_f + 1e-9 < float(order.created_at):
        raise MakerLifecycleError("MAKER_CLOCK_REGRESSION")
    received, source, epoch, _ = _book_provenance(current_book)
    if current_book.token_id != order.token_id:
        raise MakerLifecycleError("MAKER_BOOK_TOKEN_MISMATCH")
    if source != order.opening_book_source:
        raise MakerLifecycleError("MAKER_BOOK_SOURCE_CHANGED")
    if epoch < order.opening_book_source_epoch:
        raise MakerLifecycleError("MAKER_BOOK_SOURCE_EPOCH_REGRESSION")
    if received > now_f + 1e-9 or now_f - received > float(policy.max_book_age_seconds):
        return MakerLifecycleDecision(
            MAKER_LIFECYCLE_VERSION, order.order_id, ACTION_CANCEL, now_f,
            "CURRENT_BOOK_NOT_FRESH", None, False, False, None, False, False,
        )
    if float(parameters.received_at) > now_f + 1e-9 or now_f - float(parameters.received_at) > float(policy.max_parameter_age_seconds):
        return MakerLifecycleDecision(
            MAKER_LIFECYCLE_VERSION, order.order_id, ACTION_CANCEL, now_f,
            "CURRENT_PARAMETERS_NOT_FRESH", None, False, False, None, False, False,
        )
    if float(fair.as_of) > now_f + 1e-9:
        raise MakerLifecycleError("MAKER_FAIR_FROM_FUTURE")
    if float(fair.as_of) + 1e-9 < float(order.fair_as_of):
        raise MakerLifecycleError("MAKER_FAIR_VERSION_TIME_REGRESSION")

    invalid_at, invalid_action, invalid_reason = _invalidation(
        order=order, fair=fair, parameters=parameters, policy=policy, now=now_f,
    )
    trades = _dedupe_trades(tuple(public_trades), order.token_id)
    equal_queue_ambiguous = False
    strict_candidates: list[PublicTradePrint] = []
    for trade in trades:
        if trade.received_at + 1e-9 < order.created_at or trade.received_at > now_f + 1e-9:
            continue
        if trade.source != order.opening_book_source or trade.source_epoch < order.opening_book_source_epoch:
            continue
        if invalid_at is not None and trade.received_at >= invalid_at - 1e-12:
            continue
        if trade.price < order.bid_price - 1e-12:
            strict_candidates.append(trade)
        elif abs(trade.price - order.bid_price) <= 1e-12:
            equal_queue_ambiguous = True

    if strict_candidates:
        trade = min(strict_candidates, key=lambda row: (row.received_at, row.trade_id))
        fill_id = _canonical_sha({
            "version": MAKER_LIFECYCLE_VERSION,
            "order_id": order.order_id,
            "public_trade_id": trade.trade_id,
            "evidence_class": SHADOW_FILL_CLASS,
        })
        fill = ShadowMakerFill(
            version=MAKER_LIFECYCLE_VERSION,
            fill_id=fill_id,
            order_id=order.order_id,
            event_id=order.event_id,
            market_id=order.market_id,
            token_id=order.token_id,
            outcome=order.outcome,
            shares=order.max_shares,
            virtual_fill_price=order.bid_price,
            evidence_class=SHADOW_FILL_CLASS,
            public_trade_id=trade.trade_id,
            public_trade_price=float(trade.price),
            public_trade_size=float(trade.size),
            observed_at=float(trade.received_at),
            actual_fill=False,
            inventory_eligible=False,
            financial_authority=False,
        )
        return MakerLifecycleDecision(
            MAKER_LIFECYCLE_VERSION, order.order_id, ACTION_SHADOW_FILL, now_f,
            "STRICT_PUBLIC_TRADE_THROUGH", None,
            bool(current_book.best_ask is not None and current_book.best_ask <= order.bid_price + 1e-12),
            equal_queue_ambiguous, fill, False, False,
        )

    if invalid_at is not None and invalid_at <= now_f + 1e-12:
        return MakerLifecycleDecision(
            MAKER_LIFECYCLE_VERSION, order.order_id, str(invalid_action), now_f,
            str(invalid_reason), None,
            bool(current_book.best_ask is not None and current_book.best_ask <= order.bid_price + 1e-12),
            equal_queue_ambiguous, None, False, False,
        )

    touch = bool(current_book.best_ask is not None and current_book.best_ask <= order.bid_price + 1e-12)
    if touch:
        return MakerLifecycleDecision(
            MAKER_LIFECYCLE_VERSION, order.order_id, ACTION_KEEP, now_f,
            "QUOTE_TOUCH_OR_CROSS_WITHOUT_STRICT_TRADE_THROUGH", None,
            True, equal_queue_ambiguous, None, False, False,
        )

    tick = float(parameters.minimum_tick_size)
    best_bid = float(current_book.best_bid) if current_book.best_bid is not None else None
    best_ask = float(current_book.best_ask) if current_book.best_ask is not None else None
    value_cap = float(fair.lower) - float(policy.minimum_edge)
    ask_cap = (best_ask - tick) if best_ask is not None else value_cap
    target_cap = min(value_cap, ask_cap)
    suggested: float | None = None
    if best_bid is not None:
        candidate = _floor_to_tick(min(best_bid + tick, target_cap) + 1e-12, tick)
        minimum_move = tick * int(policy.reprice_min_ticks)
        if candidate >= order.bid_price + minimum_move - 1e-12 and candidate < (best_ask if best_ask is not None else 1.0):
            suggested = candidate
    if suggested is not None:
        return MakerLifecycleDecision(
            MAKER_LIFECYCLE_VERSION, order.order_id, ACTION_REPRICE, now_f,
            "QUEUE_IMPROVEMENT_AVAILABLE_WITHIN_FROZEN_VALUE_CAP", suggested,
            False, equal_queue_ambiguous, None, False, False,
        )

    return MakerLifecycleDecision(
        MAKER_LIFECYCLE_VERSION, order.order_id, ACTION_KEEP, now_f,
        "RESTING_HYPOTHESIS_STILL_VALID", None, False, equal_queue_ambiguous,
        None, False, False,
    )


def measure_shadow_fill_markout(
    *,
    fill: ShadowMakerFill,
    book: Book,
    now: float,
) -> ShadowFillMarkout:
    """Measure later quoted markout for a shadow fill without calling it realized PnL."""
    if not isinstance(fill, ShadowMakerFill) or fill.actual_fill is not False or fill.inventory_eligible is not False or fill.financial_authority is not False:
        raise MakerLifecycleError("MAKER_MARKOUT_FILL_AUTHORITY_INVALID")
    now_f = _finite(now, nonnegative=True)
    received, _, _, _ = _book_provenance(book)
    if book.token_id != fill.token_id:
        raise MakerLifecycleError("MAKER_MARKOUT_TOKEN_MISMATCH")
    if received + 1e-9 < fill.observed_at or now_f + 1e-9 < received:
        raise MakerLifecycleError("MAKER_MARKOUT_TIME_INVALID")
    bid = float(book.best_bid) if book.best_bid is not None else None
    ask = float(book.best_ask) if book.best_ask is not None else None
    midpoint = ((bid + ask) / 2.0) if bid is not None and ask is not None else None
    bid_delta = (bid - fill.virtual_fill_price) if bid is not None else None
    mid_delta = (midpoint - fill.virtual_fill_price) if midpoint is not None else None
    return ShadowFillMarkout(
        version=MAKER_LIFECYCLE_VERSION,
        shadow_fill_id=fill.fill_id,
        token_id=fill.token_id,
        observed_at=received,
        age_seconds=received - fill.observed_at,
        best_bid=bid,
        best_ask=ask,
        midpoint=midpoint,
        best_bid_markout_per_share=bid_delta,
        midpoint_markout_per_share=mid_delta,
        best_bid_markout_notional=(bid_delta * fill.shares) if bid_delta is not None else None,
        midpoint_markout_notional=(mid_delta * fill.shares) if mid_delta is not None else None,
        realized_pnl=False,
        financial_authority=False,
    )
