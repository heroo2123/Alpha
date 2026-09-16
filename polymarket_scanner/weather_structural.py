from __future__ import annotations

"""Weather-only structural proofs and price screens.

The functions here deliberately separate *semantic proof* from *execution*. A
complete-set price screen is not a TRADE NOW certificate: exact current CLOB books,
fee authority, market-open state and send-time revalidation remain mandatory later.
"""

import json
from dataclasses import dataclass
from math import isfinite

from .models import Book, Market
from .polymarket import taker_fee_per_share
from .weather import parse_bucket
from .weather_rule_tree import DailyTemperatureContract, parse_daily_temperature_contract

TEMPERATURE_PARTITION_PROOF_VERSION = "daily_temperature_integer_partition_v1"
COMPLETE_SET_SCREEN_VERSION = "weather_complete_set_screen_v1"


@dataclass(frozen=True, slots=True)
class TemperatureBucketLeg:
    market_id: str
    market_slug: str
    question: str
    yes_token: str
    lower: int | None
    upper: int | None


@dataclass(frozen=True, slots=True)
class TemperaturePartitionProof:
    proof_version: str
    event_id: str
    event_slug: str
    contract: DailyTemperatureContract
    legs: tuple[TemperatureBucketLeg, ...]
    complete_event_market_ids: tuple[str, ...]
    guaranteed_yes_payout_per_complete_set: float


@dataclass(frozen=True, slots=True)
class CompleteSetPriceScreen:
    screen_version: str
    proof_version: str
    event_id: str
    event_slug: str
    asks: tuple[tuple[str, str, float, float], ...]
    raw_ask_cost_per_bundle: float
    estimated_fee_per_bundle: float
    estimated_total_cost_per_bundle: float
    guaranteed_payout_per_bundle: float
    estimated_locked_spread_per_bundle: float
    common_best_ask_shares: float
    estimated_locked_spread_at_common_size: float
    screening_only: bool
    requires_exact_fee_authority: bool


def _json_list(value: object) -> list:
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except Exception:
            return []
        return parsed if isinstance(parsed, list) else []
    return []


def _flag(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return False


def _integer_bound(value: float | None) -> int | None:
    if value is None:
        return None
    if not isfinite(float(value)) or int(value) != float(value):
        raise ValueError("temperature bucket boundary is not an integer")
    return int(value)


def _strict_binary_yes_token(raw_market: dict) -> str | None:
    outcomes = [str(x).strip().lower() for x in _json_list(raw_market.get("outcomes"))]
    tokens = [str(x).strip() for x in _json_list(raw_market.get("clobTokenIds"))]
    if len(outcomes) != 2 or sorted(outcomes) != ["no", "yes"]:
        return None
    if len(tokens) != 2 or len(set(tokens)) != 2 or not all(tokens):
        return None
    try:
        return tokens[outcomes.index("yes")]
    except (ValueError, IndexError):
        return None


def prove_daily_temperature_partition(event: dict, markets: list[Market]) -> TemperaturePartitionProof | None:
    """Prove a complete integer-temperature partition for one live event.

    The raw Gamma parent is mandatory because a detector-selected subset cannot
    prove that no omitted settlement bucket exists. Every parent child must be open,
    order-book enabled and present in ``markets``. Every child must share the exact
    same parsed temperature rule tree. Numeric buckets then must cover all integer
    settlement values once and only once, with lower/upper tails.
    """
    if not isinstance(event, dict) or not markets:
        return None
    event_id = str(event.get("id") or "").strip()
    event_slug = str(event.get("slug") or "").strip()
    if not event_id or not event_slug:
        return None

    children = [row for row in (event.get("markets") or []) if isinstance(row, dict)]
    if len(children) < 3:
        return None

    raw_by_id: dict[str, dict] = {}
    raw_yes: dict[str, str] = {}
    for child in children:
        mid = str(child.get("id") or "").strip()
        if not mid or mid in raw_by_id:
            return None
        if not _flag(child.get("active", True)) or _flag(child.get("closed", False)):
            return None
        if not _flag(child.get("acceptingOrders")) or not _flag(child.get("enableOrderBook")):
            return None
        yes = _strict_binary_yes_token(child)
        if not yes:
            return None
        raw_by_id[mid] = child
        raw_yes[mid] = yes

    materialized_by_id = {market.id: market for market in markets}
    if len(materialized_by_id) != len(markets) or set(materialized_by_id) != set(raw_by_id):
        return None
    if any(market.event_id != event_id or market.event_slug != event_slug for market in markets):
        return None

    # Unknown cancellation/refund semantics would break the certain-$1 partition
    # claim. Current supported daily-temperature rule trees resolve no-data to the
    # lowest bucket instead, so these terms should not be present in supported rules.
    rule_text = " ".join(
        [str(event.get("description") or ""), str(event.get("resolutionSource") or "")]
        + [str(row.get("description") or "") for row in children]
    ).lower()
    if any(term in rule_text for term in ("refund", "refunded", "voided", "market will be void")):
        return None

    parsed: list[tuple[Market, DailyTemperatureContract]] = []
    for market in markets:
        result = parse_daily_temperature_contract(market)
        if not result.supported or not isinstance(result.contract, DailyTemperatureContract):
            return None
        if market.yes_token != raw_yes.get(market.id):
            return None
        parsed.append((market, result.contract))

    contract = parsed[0][1]
    if any(candidate != contract for _, candidate in parsed[1:]):
        return None

    legs: list[TemperatureBucketLeg] = []
    try:
        for market, _ in parsed:
            lower_raw, upper_raw = parse_bucket(market.question, contract.unit)
            if lower_raw is None and upper_raw is None:
                return None
            lower = _integer_bound(lower_raw)
            upper = _integer_bound(upper_raw)
            if lower is not None and upper is not None and lower > upper:
                return None
            yes_token = market.yes_token
            if not yes_token:
                return None
            legs.append(TemperatureBucketLeg(
                market_id=market.id,
                market_slug=market.slug,
                question=market.question,
                yes_token=yes_token,
                lower=lower,
                upper=upper,
            ))
    except (TypeError, ValueError):
        return None

    def key(leg: TemperatureBucketLeg):
        return (float("-inf") if leg.lower is None else leg.lower,
                float("inf") if leg.upper is None else leg.upper,
                leg.market_id)

    legs.sort(key=key)
    if legs[0].lower is not None or legs[-1].upper is not None:
        return None

    for index, leg in enumerate(legs):
        if index > 0 and leg.lower is None:
            return None
        if index < len(legs) - 1 and leg.upper is None:
            return None
        if index:
            previous = legs[index - 1]
            if previous.upper is None or leg.lower is None:
                return None
            if leg.lower != previous.upper + 1:
                return None

    # Token identity must be one-to-one across bucket YES legs; otherwise one order
    # could accidentally be counted as multiple exhaustive outcomes.
    if len({leg.yes_token for leg in legs}) != len(legs):
        return None

    return TemperaturePartitionProof(
        proof_version=TEMPERATURE_PARTITION_PROOF_VERSION,
        event_id=event_id,
        event_slug=event_slug,
        contract=contract,
        legs=tuple(legs),
        complete_event_market_ids=tuple(sorted(raw_by_id)),
        guaranteed_yes_payout_per_complete_set=1.0,
    )


def screen_complete_set_underround(
    proof: TemperaturePartitionProof,
    books: dict[str, Book],
    *,
    minimum_spread_per_bundle: float = 0.01,
    fee_rate_estimate: float = 0.07,
) -> CompleteSetPriceScreen | None:
    """Screen best asks for a complete-set underround.

    This uses the repository's conservative fee-curve estimate and therefore stays
    screening-only. A future send-time execution certificate must replace estimated
    fees with current token fee authority and re-fetch exact books.
    """
    if minimum_spread_per_bundle < 0 or fee_rate_estimate < 0:
        raise ValueError("spread and fee rate must be non-negative")

    quotes: list[tuple[str, str, float, float]] = []
    raw_cost = 0.0
    estimated_fees = 0.0
    common_size: float | None = None

    for leg in proof.legs:
        book = books.get(leg.yes_token)
        if book is None or book.best_ask is None or book.best_ask_size <= 0:
            return None
        ask = float(book.best_ask)
        size = float(book.best_ask_size)
        if not (0 < ask < 1) or not isfinite(ask) or not isfinite(size) or size <= 0:
            return None
        fee = taker_fee_per_share(ask, fee_rate_estimate)
        raw_cost += ask
        estimated_fees += fee
        common_size = size if common_size is None else min(common_size, size)
        quotes.append((leg.market_id, leg.yes_token, ask, size))

    total = raw_cost + estimated_fees
    payout = float(proof.guaranteed_yes_payout_per_complete_set)
    spread = payout - total
    if spread + 1e-12 < minimum_spread_per_bundle:
        return None
    common = float(common_size or 0.0)
    if common <= 0:
        return None

    return CompleteSetPriceScreen(
        screen_version=COMPLETE_SET_SCREEN_VERSION,
        proof_version=proof.proof_version,
        event_id=proof.event_id,
        event_slug=proof.event_slug,
        asks=tuple(quotes),
        raw_ask_cost_per_bundle=raw_cost,
        estimated_fee_per_bundle=estimated_fees,
        estimated_total_cost_per_bundle=total,
        guaranteed_payout_per_bundle=payout,
        estimated_locked_spread_per_bundle=spread,
        common_best_ask_shares=common,
        estimated_locked_spread_at_common_size=spread * common,
        screening_only=True,
        requires_exact_fee_authority=True,
    )
