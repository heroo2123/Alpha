from __future__ import annotations

"""Weather-only structural opportunity primitives.

All functions are pure and require caller-supplied *exact CLOB* books plus the
market-specific V2 market parameters from the same live recheck. Gamma BBO/midpoint
values and category fee defaults are not execution authority. These primitives
create silent-shadow candidates only; ``financial_authority`` remains false until a
separate final execution/delivery boundary is explicitly promoted.

For V2 platform fees, ``fd.r`` and ``fd.e`` are the executable fee schedule used by
the current official client. Legacy ``tbf``/``mbf`` metadata is retained for audit
telemetry but is not added to the V2 platform-fee formula. Builder fees are also
separate and remain outside this manual-execution shadow screen.
"""

from dataclasses import asdict, dataclass

from .models import Book
from .weather_only_clob import WeatherMarketParameters, conservative_taker_fee_per_share
from .weather_only_contracts import CompiledWeatherEvent


WEATHER_STRUCTURAL_VERSION = "weather_structural_v3_v2_fd_fee_authority_shadow"
DETERMINISTIC = "DETERMINISTIC"


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
    fee_exponents: tuple[int, ...]
    conservative_fees_per_share: tuple[float, ...]
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


def _parameters(
    condition_id: str,
    parameters_by_condition: dict[str, WeatherMarketParameters],
) -> WeatherMarketParameters | None:
    value = parameters_by_condition.get(condition_id)
    if not isinstance(value, WeatherMarketParameters):
        return None
    if value.condition_id != condition_id:
        return None
    if not (0.0 <= value.fee_rate <= 1.0) or value.fee_exponent < 0:
        return None
    # Current V2 market-info marks positive dynamic fees taker-only. If that
    # semantic changes, fail closed instead of guessing how the schedule applies.
    if value.fee_rate > 0.0 and value.taker_only is not True:
        return None
    return value


def _tokens_match(parameters: WeatherMarketParameters, wanted: tuple[str, ...]) -> bool:
    actual = {token for token, _ in parameters.token_outcomes}
    return all(token in actual for token in wanted)


def _opportunity(
    *,
    lane: str,
    event_id: str,
    market_ids: list[str],
    token_ids: list[str],
    asks: list[float],
    sizes: list[float],
    parameters: list[WeatherMarketParameters],
    contract_partition_proven: bool,
    min_profit_per_set: float,
) -> WeatherStructuralOpportunity | None:
    if not (len(asks) == len(sizes) == len(parameters) == len(token_ids) == len(market_ids)):
        return None
    fees = [
        conservative_taker_fee_per_share(price, params.fee_rate, params.fee_exponent)
        for price, params in zip(asks, parameters)
    ]
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
        fee_rates=tuple(params.fee_rate for params in parameters),
        fee_exponents=tuple(params.fee_exponent for params in parameters),
        conservative_fees_per_share=tuple(fees),
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
    parameters_by_condition: dict[str, WeatherMarketParameters],
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
        params = _parameters(bucket.condition_id, parameters_by_condition)
        if yes is None or no is None or params is None:
            continue
        if not _tokens_match(params, (bucket.yes_token, bucket.no_token)):
            continue
        opportunity = _opportunity(
            lane="weather_binary_pair_underround",
            event_id=compiled.event_id,
            market_ids=[bucket.market_id, bucket.market_id],
            token_ids=[bucket.yes_token, bucket.no_token],
            asks=[yes[0], no[0]],
            sizes=[yes[1], no[1]],
            parameters=[params, params],
            contract_partition_proven=True,
            min_profit_per_set=min_profit_per_set,
        )
        if opportunity is not None:
            out.append(opportunity)
    return out


def complete_bucket_underround(
    compiled: CompiledWeatherEvent,
    books: dict[str, Book],
    parameters_by_condition: dict[str, WeatherMarketParameters],
    *,
    min_profit_per_set: float = 0.0,
) -> WeatherStructuralOpportunity | None:
    """Buy one YES in every bucket only after exactly-one semantics are certified.

    ``partition_shape_complete`` proves only the labels appear contiguous. It is
    intentionally insufficient. A source/rule adapter must separately upgrade
    ``exactly_one_outcome_proven`` after proving precision, fallback, no-data and
    resolution semantics. Exact market parameters are also mandatory for every leg.
    """
    if not compiled.partition_shape_complete or not compiled.exactly_one_outcome_proven:
        return None
    if not compiled.buckets:
        return None

    market_ids: list[str] = []
    token_ids: list[str] = []
    asks: list[float] = []
    sizes: list[float] = []
    parameters: list[WeatherMarketParameters] = []

    for bucket in compiled.buckets:
        if not bucket.trade_open or not bucket.yes_token:
            return None
        book = _ask(books.get(bucket.yes_token))
        params = _parameters(bucket.condition_id, parameters_by_condition)
        if book is None or params is None or not _tokens_match(params, (bucket.yes_token,)):
            return None
        market_ids.append(bucket.market_id)
        token_ids.append(bucket.yes_token)
        asks.append(book[0])
        sizes.append(book[1])
        parameters.append(params)

    return _opportunity(
        lane="weather_complete_bucket_underround",
        event_id=compiled.event_id,
        market_ids=market_ids,
        token_ids=token_ids,
        asks=asks,
        sizes=sizes,
        parameters=parameters,
        contract_partition_proven=True,
        min_profit_per_set=min_profit_per_set,
    )
