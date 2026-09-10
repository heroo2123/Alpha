from __future__ import annotations

"""Weather-only structural opportunity primitives.

All functions are pure and require caller-supplied *exact CLOB* books plus a
market-specific taker fee-rate parameter.  Gamma BBO/midpoint values are not
execution authority.  These primitives create silent-shadow candidates only;
``financial_authority`` is deliberately false until the weather runtime performs
its final live recheck and the containing strategy is separately promoted.
"""

from dataclasses import asdict, dataclass

from .models import Book
from .weather_only_contracts import CompiledWeatherEvent, WeatherBucket


WEATHER_STRUCTURAL_VERSION = "weather_structural_v1_exact_books_explicit_fees_shadow"
DETERMINISTIC = "DETERMINISTIC"


def nominal_taker_fee_per_share(price: float, fee_rate: float) -> float:
    """Protocol fee curve before trade-total 5-decimal rounding.

    Polymarket currently documents ``C * feeRate * p * (1-p)``.  Runtime code must
    obtain ``fee_rate`` for the specific market/token rather than assuming the
    category default.  Rounding and any future fee revision belong to final trade
    authority, so this shadow primitive never labels itself executable authority.
    """
    p = float(price)
    rate = float(fee_rate)
    if not (0.0 < p < 1.0) or rate < 0.0:
        raise ValueError("invalid price or fee rate")
    return rate * p * (1.0 - p)


@dataclass(frozen=True, slots=True)
class WeatherStructuralOpportunity:
    version: str
    lane: str
    evidence_class: str
    event_id: str
    market_ids: tuple[str, ...]
    token_ids: tuple[str, ...]
    ask_prices: tuple[float, ...]
    fee_rates: tuple[float, ...]
    nominal_fees_per_share: tuple[float, ...]
    gross_cost_per_set: float
    locked_payout_per_set: float
    locked_profit_per_set: float
    common_best_ask_shares: float
    max_locked_profit_at_best_level: float
    contract_partition_proven: bool
    financial_authority: bool

    def as_dict(self) -> dict:
        return asdict(self)


def _ask(book: Book | None) -> tuple[float, float] | None:
    if book is None or book.best_ask is None:
        return None
    price = float(book.best_ask)
    size = float(book.best_ask_size)
    if not (0.0 < price < 1.0) or size <= 0.0:
        return None
    return price, size


def _fee_rate(condition_id: str, rates: dict[str, float]) -> float | None:
    if condition_id not in rates:
        return None
    try:
        value = float(rates[condition_id])
    except (TypeError, ValueError):
        return None
    return value if 0.0 <= value <= 1.0 else None


def _opportunity(
    *,
    lane: str,
    event_id: str,
    market_ids: list[str],
    token_ids: list[str],
    asks: list[float],
    sizes: list[float],
    fee_rates: list[float],
    contract_partition_proven: bool,
    min_profit_per_set: float,
) -> WeatherStructuralOpportunity | None:
    fees = [nominal_taker_fee_per_share(price, rate) for price, rate in zip(asks, fee_rates)]
    cost = sum(asks) + sum(fees)
    profit = 1.0 - cost
    if profit <= max(0.0, float(min_profit_per_set)):
        return None
    common = min(sizes)
    return WeatherStructuralOpportunity(
        version=WEATHER_STRUCTURAL_VERSION,
        lane=lane,
        evidence_class=DETERMINISTIC,
        event_id=event_id,
        market_ids=tuple(market_ids),
        token_ids=tuple(token_ids),
        ask_prices=tuple(asks),
        fee_rates=tuple(fee_rates),
        nominal_fees_per_share=tuple(fees),
        gross_cost_per_set=cost,
        locked_payout_per_set=1.0,
        locked_profit_per_set=profit,
        common_best_ask_shares=common,
        max_locked_profit_at_best_level=common * profit,
        contract_partition_proven=contract_partition_proven,
        financial_authority=False,
    )


def binary_pair_underround(
    compiled: CompiledWeatherEvent,
    books: dict[str, Book],
    fee_rate_by_condition: dict[str, float],
    *,
    min_profit_per_set: float = 0.0,
) -> list[WeatherStructuralOpportunity]:
    """Find YES+NO complete-set underrounds inside binary weather child markets."""
    out: list[WeatherStructuralOpportunity] = []
    for bucket in compiled.buckets:
        if not bucket.trade_open or not bucket.yes_token or not bucket.no_token:
            continue
        yes = _ask(books.get(bucket.yes_token))
        no = _ask(books.get(bucket.no_token))
        rate = _fee_rate(bucket.condition_id, fee_rate_by_condition)
        if yes is None or no is None or rate is None:
            continue
        opportunity = _opportunity(
            lane="weather_binary_pair_underround",
            event_id=compiled.event_id,
            market_ids=[bucket.market_id, bucket.market_id],
            token_ids=[bucket.yes_token, bucket.no_token],
            asks=[yes[0], no[0]],
            sizes=[yes[1], no[1]],
            fee_rates=[rate, rate],
            contract_partition_proven=True,  # Binary child semantics only.
            min_profit_per_set=min_profit_per_set,
        )
        if opportunity is not None:
            out.append(opportunity)
    return out


def complete_bucket_underround(
    compiled: CompiledWeatherEvent,
    books: dict[str, Book],
    fee_rate_by_condition: dict[str, float],
    *,
    min_profit_per_set: float = 0.0,
) -> WeatherStructuralOpportunity | None:
    """Buy one YES in every bucket only after exactly-one semantics are certified.

    ``partition_shape_complete`` proves only the labels appear contiguous.  It is
    intentionally insufficient.  A source/rule adapter must separately upgrade
    ``exactly_one_outcome_proven`` after proving precision, fallback, no-data and
    resolution semantics.  Until then this lane returns nothing.
    """
    if not compiled.partition_shape_complete or not compiled.exactly_one_outcome_proven:
        return None
    if not compiled.buckets:
        return None

    market_ids: list[str] = []
    token_ids: list[str] = []
    asks: list[float] = []
    sizes: list[float] = []
    rates: list[float] = []

    for bucket in compiled.buckets:
        if not bucket.trade_open or not bucket.yes_token:
            return None
        book = _ask(books.get(bucket.yes_token))
        rate = _fee_rate(bucket.condition_id, fee_rate_by_condition)
        if book is None or rate is None:
            return None
        market_ids.append(bucket.market_id)
        token_ids.append(bucket.yes_token)
        asks.append(book[0])
        sizes.append(book[1])
        rates.append(rate)

    return _opportunity(
        lane="weather_complete_bucket_underround",
        event_id=compiled.event_id,
        market_ids=market_ids,
        token_ids=token_ids,
        asks=asks,
        sizes=sizes,
        fee_rates=rates,
        contract_partition_proven=True,
        min_profit_per_set=min_profit_per_set,
    )
