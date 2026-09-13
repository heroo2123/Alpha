from __future__ import annotations

"""Exact read-only CLOB authority for the weather-only scanner.

A snapshot is accepted only when every requested condition and token is represented
exactly once, token-to-outcome meaning agrees with the compiler, provider book time
is plausible/fresh, local receipts are fresh, and V2 market parameters are explicit.
No order-posting/signing/cancel method exists here.
"""

import asyncio
import math
import time
from dataclasses import asdict, dataclass
from decimal import Decimal, ROUND_CEILING

import httpx

from .config import settings
from .models import Book
from .weather_only_contracts import CompiledWeatherEvent


CLOB = "https://clob.polymarket.com"
WEATHER_CLOB_VERSION = "weather_clob_v4_token_meaning_provider_freshness_read_only"
MAX_BOOK_AGE_SECONDS = 10.0
MAX_PROVIDER_BOOK_AGE_SECONDS = 30.0
MAX_PROVIDER_FUTURE_SKEW_SECONDS = 5.0
MAX_CONDITIONS = 1_000
MAX_TOKENS = 2_000
CLOB_TRANSIENT_MAX_ATTEMPTS = 3
CLOB_TRANSIENT_RETRY_DELAY_SECONDS = 0.25
CLOB_MAX_CONNECTIONS = 12
CLOB_MAX_KEEPALIVE_CONNECTIONS = 12
CLOB_KEEPALIVE_EXPIRY_SECONDS = 120.0


class WeatherCLOBError(RuntimeError):
    def __init__(self, code: str):
        self.code = str(code)
        super().__init__(self.code)


@dataclass(frozen=True, slots=True)
class WeatherMarketParameters:
    condition_id: str
    token_outcomes: tuple[tuple[str, str], ...]
    minimum_order_size: float
    minimum_tick_size: float
    fee_rate: float
    fee_exponent: int
    taker_only: bool | None
    maker_base_fee_bps: int
    taker_base_fee_bps: int
    rfq_enabled: bool | None
    taker_delay_enabled: bool | None
    received_at: float

    def as_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class WeatherExecutionSnapshot:
    version: str
    event_id: str
    books: dict[str, Book]
    parameters: dict[str, WeatherMarketParameters]
    started_at: float
    finished_at: float
    exact_clob: bool
    financial_authority: bool

    @property
    def age_seconds(self) -> float:
        return max(0.0, time.time() - self.finished_at)


def conservative_taker_fee_per_share(price: float, fee_rate: float, fee_exponent: int) -> float:
    p = Decimal(str(price))
    rate = Decimal(str(fee_rate))
    if not (Decimal("0") < p < Decimal("1")) or rate < 0:
        raise ValueError("invalid price or fee rate")
    if isinstance(fee_exponent, bool):
        raise ValueError("invalid fee exponent")
    try:
        exponent = int(fee_exponent)
    except (TypeError, ValueError, OverflowError):
        raise ValueError("invalid fee exponent")
    if exponent < 0 or Decimal(str(fee_exponent)) != Decimal(exponent):
        raise ValueError("invalid fee exponent")
    if rate == 0:
        return 0.0
    raw = rate * (p * (Decimal("1") - p)) ** exponent
    return float(raw.quantize(Decimal("0.00001"), rounding=ROUND_CEILING))


def _finite_number(value: object, *, minimum: float = 0.0, maximum: float | None = None) -> float:
    if isinstance(value, bool):
        raise WeatherCLOBError("MARKET_INFO_NUMBER_INVALID")
    try:
        out = float(value)
    except (TypeError, ValueError):
        raise WeatherCLOBError("MARKET_INFO_NUMBER_INVALID")
    if not math.isfinite(out) or out < minimum or (maximum is not None and out > maximum):
        raise WeatherCLOBError("MARKET_INFO_NUMBER_INVALID")
    return out


def _integer(value: object) -> int:
    if isinstance(value, bool):
        raise WeatherCLOBError("MARKET_INFO_INTEGER_INVALID")
    try:
        numeric = float(value)
    except (TypeError, ValueError, OverflowError):
        raise WeatherCLOBError("MARKET_INFO_INTEGER_INVALID")
    if not math.isfinite(numeric) or numeric < 0 or not numeric.is_integer():
        raise WeatherCLOBError("MARKET_INFO_INTEGER_INVALID")
    return int(numeric)


def _retry_delay_seconds(attempt: int) -> float:
    delay = float(CLOB_TRANSIENT_RETRY_DELAY_SECONDS)
    if not math.isfinite(delay) or delay < 0.0:
        raise WeatherCLOBError("CLOB_RETRY_POLICY_INVALID")
    return delay * (attempt + 1)


def _provider_timestamp_seconds(value: object) -> float:
    raw = str(value or "").strip()
    if not raw:
        raise WeatherCLOBError("BOOK_PROVIDER_TIMESTAMP_MISSING")
    try:
        numeric = float(raw)
    except (TypeError, ValueError, OverflowError):
        raise WeatherCLOBError("BOOK_PROVIDER_TIMESTAMP_INVALID")
    if not math.isfinite(numeric) or numeric <= 0.0:
        raise WeatherCLOBError("BOOK_PROVIDER_TIMESTAMP_INVALID")
    # CLOB book timestamps are commonly milliseconds.  Also tolerate seconds and
    # higher precision epoch forms while normalizing to seconds.
    while numeric > 10_000_000_000.0:
        numeric /= 1000.0
    if numeric < 1_500_000_000.0:
        raise WeatherCLOBError("BOOK_PROVIDER_TIMESTAMP_IMPLAUSIBLE")
    return numeric


def parse_market_info(condition_id: str, payload: object, *, received_at: float) -> WeatherMarketParameters:
    if not isinstance(payload, dict):
        raise WeatherCLOBError("MARKET_INFO_ENVELOPE_INVALID")
    tokens_raw = payload.get("t")
    if not isinstance(tokens_raw, list) or len(tokens_raw) != 2:
        raise WeatherCLOBError("MARKET_INFO_BINARY_TOKENS_INVALID")
    tokens: list[tuple[str, str]] = []
    seen: set[str] = set()
    for row in tokens_raw:
        if not isinstance(row, dict):
            raise WeatherCLOBError("MARKET_INFO_BINARY_TOKENS_INVALID")
        token = str(row.get("t") or "").strip()
        outcome = str(row.get("o") or "").strip()
        if not token or not outcome or token in seen:
            raise WeatherCLOBError("MARKET_INFO_BINARY_TOKENS_INVALID")
        seen.add(token)
        tokens.append((token, outcome))
    if {outcome.lower() for _, outcome in tokens} != {"yes", "no"}:
        raise WeatherCLOBError("MARKET_INFO_BINARY_TOKENS_INVALID")

    fd = payload.get("fd")
    if fd is None:
        fee_rate = 0.0
        exponent = 0
        taker_only = None
    else:
        if not isinstance(fd, dict) or "r" not in fd:
            raise WeatherCLOBError("MARKET_INFO_FEE_DETAILS_INVALID")
        fee_rate = _finite_number(fd.get("r"), minimum=0.0, maximum=1.0)
        if "e" in fd:
            exponent = _integer(fd.get("e"))
        elif fee_rate == 0.0:
            exponent = 0
        else:
            raise WeatherCLOBError("MARKET_INFO_FEE_EXPONENT_MISSING")
        taker_only = fd.get("to") if type(fd.get("to")) is bool else None

    return WeatherMarketParameters(
        condition_id=str(condition_id),
        token_outcomes=tuple(tokens),
        minimum_order_size=_finite_number(payload.get("mos"), minimum=0.0),
        minimum_tick_size=_finite_number(payload.get("mts"), minimum=0.000001, maximum=1.0),
        fee_rate=fee_rate,
        fee_exponent=exponent,
        taker_only=taker_only,
        maker_base_fee_bps=_integer(payload.get("mbf", 0)),
        taker_base_fee_bps=_integer(payload.get("tbf", 0)),
        rfq_enabled=payload.get("rfqe") if type(payload.get("rfqe")) is bool else None,
        taker_delay_enabled=payload.get("itode") if type(payload.get("itode")) is bool else None,
        received_at=float(received_at),
    )


def parse_book(token_id: str, payload: object, *, received_at: float) -> Book:
    if not isinstance(payload, dict):
        raise WeatherCLOBError("BOOK_ENVELOPE_INVALID")
    asset = str(payload.get("asset_id") or "").strip()
    if asset != str(token_id):
        raise WeatherCLOBError("BOOK_TOKEN_IDENTITY_MISMATCH")

    def side(name: str) -> list[tuple[float, float]]:
        raw = payload.get(name)
        if not isinstance(raw, list):
            raise WeatherCLOBError("BOOK_SIDE_INVALID")
        out: list[tuple[float, float]] = []
        for level in raw:
            if not isinstance(level, dict):
                raise WeatherCLOBError("BOOK_LEVEL_INVALID")
            price = _finite_number(level.get("price"), minimum=0.000001, maximum=0.999999)
            size = _finite_number(level.get("size"), minimum=0.000001)
            out.append((price, size))
        return out

    bids, asks = side("bids"), side("asks")
    if bids and asks and max(p for p, _ in bids) >= min(p for p, _ in asks):
        raise WeatherCLOBError("BOOK_CROSSED")
    last = payload.get("last_trade_price")
    last_price = None if last in (None, "") else _finite_number(last, minimum=0.0, maximum=1.0)
    provider_raw = str(payload.get("timestamp") or "") or None
    if provider_raw is not None:
        provider_ts = _provider_timestamp_seconds(provider_raw)
        receipt = float(received_at)
        if provider_ts - receipt > MAX_PROVIDER_FUTURE_SKEW_SECONDS:
            raise WeatherCLOBError("BOOK_PROVIDER_TIMESTAMP_FUTURE")
        if receipt - provider_ts > MAX_PROVIDER_BOOK_AGE_SECONDS:
            raise WeatherCLOBError("BOOK_PROVIDER_TIMESTAMP_STALE")
    return Book(
        token_id=str(token_id),
        bids=bids,
        asks=asks,
        last_trade_price=last_price,
        timestamp=provider_raw,
        received_at=float(received_at),
        source="clob_exact_rest_v2",
        book_hash=str(payload.get("hash") or "") or None,
    )


class WeatherCLOBClient:
    def __init__(self) -> None:
        self.http = httpx.AsyncClient(
            timeout=settings.request_timeout,
            limits=httpx.Limits(
                max_connections=CLOB_MAX_CONNECTIONS,
                max_keepalive_connections=CLOB_MAX_KEEPALIVE_CONNECTIONS,
                keepalive_expiry=CLOB_KEEPALIVE_EXPIRY_SECONDS,
            ),
            headers={"User-Agent": "polymarket-weather-only-scanner/0.1 (+github)"},
        )

    async def close(self) -> None:
        await self.http.aclose()

    async def market_info(self, condition_id: str) -> WeatherMarketParameters:
        condition = str(condition_id or "").strip()
        if not condition:
            raise WeatherCLOBError("CONDITION_ID_MISSING")
        response = None
        for attempt in range(CLOB_TRANSIENT_MAX_ATTEMPTS):
            code = None
            try:
                response = await self.http.get(f"{CLOB}/clob-markets/{condition}")
            except httpx.TimeoutException:
                code = "CLOB_TIMEOUT"
            except httpx.RequestError:
                code = "CLOB_TRANSPORT"
            if code is None:
                break
            if attempt + 1 >= CLOB_TRANSIENT_MAX_ATTEMPTS:
                raise WeatherCLOBError(code)
            delay = _retry_delay_seconds(attempt)
            if delay > 0.0:
                await asyncio.sleep(delay)
        if response is None:
            raise WeatherCLOBError("CLOB_RETRY_STATE_INVALID")
        if response.status_code == 404:
            raise WeatherCLOBError("CLOB_MARKET_NOT_FOUND")
        if response.status_code >= 400:
            raise WeatherCLOBError("CLOB_HTTP_STATUS")
        received = time.time()
        try:
            payload = response.json()
        except Exception:
            raise WeatherCLOBError("MARKET_INFO_JSON_INVALID")
        return parse_market_info(condition, payload, received_at=received)

    async def market_infos(self, condition_ids: list[str]) -> dict[str, WeatherMarketParameters]:
        ids = list(dict.fromkeys(str(value).strip() for value in condition_ids if str(value).strip()))
        if len(ids) > MAX_CONDITIONS:
            raise WeatherCLOBError("CONDITION_CAP")
        sem = asyncio.Semaphore(8)

        async def one(condition: str):
            async with sem:
                return condition, await self.market_info(condition)

        rows = await asyncio.gather(*(one(condition) for condition in ids), return_exceptions=True)
        out: dict[str, WeatherMarketParameters] = {}
        for row in rows:
            if isinstance(row, Exception):
                raise row
            condition, info = row
            out[condition] = info
        if set(out) != set(ids):
            raise WeatherCLOBError("MARKET_INFO_INCOMPLETE")
        return out

    async def books(self, token_ids: list[str]) -> dict[str, Book]:
        ids = list(dict.fromkeys(str(value).strip() for value in token_ids if str(value).strip()))
        if len(ids) > MAX_TOKENS:
            raise WeatherCLOBError("TOKEN_CAP")
        if not ids:
            return {}
        out: dict[str, Book] = {}
        for start in range(0, len(ids), 100):
            chunk = ids[start:start + 100]
            response = None
            for attempt in range(CLOB_TRANSIENT_MAX_ATTEMPTS):
                code = None
                try:
                    response = await self.http.post(f"{CLOB}/books", json=[{"token_id": token} for token in chunk])
                except httpx.TimeoutException:
                    code = "CLOB_TIMEOUT"
                except httpx.RequestError:
                    code = "CLOB_TRANSPORT"
                if code is None:
                    break
                if attempt + 1 >= CLOB_TRANSIENT_MAX_ATTEMPTS:
                    raise WeatherCLOBError(code)
                delay = _retry_delay_seconds(attempt)
                if delay > 0.0:
                    await asyncio.sleep(delay)
            if response is None:
                raise WeatherCLOBError("CLOB_RETRY_STATE_INVALID")
            if response.status_code >= 400:
                raise WeatherCLOBError("CLOB_HTTP_STATUS")
            received = time.time()
            try:
                payload = response.json()
            except Exception:
                raise WeatherCLOBError("BOOK_JSON_INVALID")
            if not isinstance(payload, list):
                raise WeatherCLOBError("BOOK_BATCH_ENVELOPE_INVALID")
            for raw in payload:
                if not isinstance(raw, dict):
                    raise WeatherCLOBError("BOOK_ENVELOPE_INVALID")
                token = str(raw.get("asset_id") or "").strip()
                if token not in chunk or token in out:
                    raise WeatherCLOBError("BOOK_BATCH_IDENTITY_INVALID")
                out[token] = parse_book(token, raw, received_at=received)
            if not set(chunk).issubset(out):
                raise WeatherCLOBError("BOOK_BATCH_INCOMPLETE")
        return out

    async def exact_event_snapshot(self, compiled: CompiledWeatherEvent) -> WeatherExecutionSnapshot:
        started = time.time()
        conditions: list[str] = []
        tokens: list[str] = []
        expected_by_condition: dict[str, dict[str, str]] = {}
        for bucket in compiled.buckets:
            if not bucket.trade_open:
                continue
            if not bucket.condition_id or not bucket.yes_token or not bucket.no_token:
                raise WeatherCLOBError("COMPILED_BUCKET_IDENTITY_INCOMPLETE")
            if bucket.condition_id in expected_by_condition:
                raise WeatherCLOBError("COMPILED_CONDITION_DUPLICATE")
            conditions.append(bucket.condition_id)
            tokens.extend((bucket.yes_token, bucket.no_token))
            expected_by_condition[bucket.condition_id] = {
                bucket.yes_token: "yes",
                bucket.no_token: "no",
            }
        if not conditions:
            raise WeatherCLOBError("NO_OPEN_BUCKETS")
        if len(tokens) != len(set(tokens)):
            raise WeatherCLOBError("COMPILED_TOKEN_DUPLICATE")

        infos, books = await asyncio.gather(self.market_infos(conditions), self.books(tokens))
        for condition, expected in expected_by_condition.items():
            actual = {token: str(outcome).strip().lower() for token, outcome in infos[condition].token_outcomes}
            if actual != expected:
                raise WeatherCLOBError("GAMMA_CLOB_TOKEN_OUTCOME_MISMATCH")
        if set(books) != set(tokens):
            raise WeatherCLOBError("BOOK_BATCH_INCOMPLETE")
        finished = time.time()
        for book in books.values():
            if book.received_at is None or finished - book.received_at > MAX_BOOK_AGE_SECONDS:
                raise WeatherCLOBError("BOOK_SNAPSHOT_STALE")
            if not book.timestamp:
                raise WeatherCLOBError("BOOK_PROVIDER_TIMESTAMP_MISSING")
            provider_ts = _provider_timestamp_seconds(book.timestamp)
            if provider_ts - finished > MAX_PROVIDER_FUTURE_SKEW_SECONDS:
                raise WeatherCLOBError("BOOK_PROVIDER_TIMESTAMP_FUTURE")
            if finished - provider_ts > MAX_PROVIDER_BOOK_AGE_SECONDS:
                raise WeatherCLOBError("BOOK_PROVIDER_TIMESTAMP_STALE")
        return WeatherExecutionSnapshot(
            version=WEATHER_CLOB_VERSION,
            event_id=compiled.event_id,
            books=books,
            parameters=infos,
            started_at=started,
            finished_at=finished,
            exact_clob=True,
            financial_authority=False,
        )
