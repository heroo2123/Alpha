from __future__ import annotations

"""Strict public CLOB/Gamma execution evidence for weather paper v4.

The v4 contract compiler proves what a token is supposed to mean.  This module then
requires the current CLOB market-info mapping and raw book payload to agree with that
meaning, condition ID, tick/minimum-size metadata and provider time.  All operations
are public reads.  There are no order/sign/cancel methods.
"""

import asyncio
import math
import time
from dataclasses import asdict, dataclass

import httpx

from .config import settings
from .models import Book
from .weather_only_clob import (
    CLOB,
    CLOB_KEEPALIVE_EXPIRY_SECONDS,
    CLOB_MAX_CONNECTIONS,
    CLOB_MAX_KEEPALIVE_CONNECTIONS,
    CLOB_TRANSIENT_MAX_ATTEMPTS,
    WeatherCLOBError,
    WeatherMarketParameters,
    _retry_delay_seconds,
    parse_market_info,
)
from .weather_only_integrity_v4 import V4CompiledWeatherEvent


GAMMA = "https://gamma-api.polymarket.com"
WEATHER_CLOB_V4_VERSION = "weather_clob_v4_condition_label_provider_time_state_bound"
MAX_PROVIDER_BOOK_AGE_SECONDS = 30.0
MAX_PROVIDER_FUTURE_SKEW_SECONDS = 5.0
MAX_LOCAL_BATCH_AGE_SECONDS = 10.0
MAX_CONDITIONS = 1_000
MAX_TOKENS = 2_000


@dataclass(frozen=True, slots=True)
class V4BookEvidence:
    token_id: str
    condition_id: str
    outcome: str
    book: Book
    provider_timestamp: float
    received_at: float
    book_hash: str
    minimum_order_size: float
    minimum_tick_size: float

    def as_dict(self) -> dict:
        value = asdict(self)
        value["book"] = {
            "token_id": self.book.token_id,
            "bids": self.book.bids,
            "asks": self.book.asks,
            "last_trade_price": self.book.last_trade_price,
            "timestamp": self.book.timestamp,
            "received_at": self.book.received_at,
            "source": self.book.source,
            "book_hash": self.book.book_hash,
        }
        return value


@dataclass(frozen=True, slots=True)
class V4ExecutionSnapshot:
    version: str
    event_id: str
    books: dict[str, Book]
    book_evidence: dict[str, V4BookEvidence]
    parameters: dict[str, WeatherMarketParameters]
    started_at: float
    finished_at: float
    exact_clob: bool
    financial_authority: bool

    @property
    def age_seconds(self) -> float:
        return max(0.0, time.time() - self.finished_at)

    def evidence_dict(self) -> dict:
        return {
            "version": self.version,
            "event_id": self.event_id,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "parameters": {key: value.as_dict() for key, value in self.parameters.items()},
            "books": {key: value.as_dict() for key, value in self.book_evidence.items()},
            "exact_clob": self.exact_clob,
            "financial_authority": False,
        }


def _finite(value: object, code: str) -> float:
    if value is None or isinstance(value, bool):
        raise WeatherCLOBError(code)
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        raise WeatherCLOBError(code) from None
    if not math.isfinite(number):
        raise WeatherCLOBError(code)
    return number


def _provider_timestamp(value: object, *, received_at: float) -> float:
    raw = _finite(value, "BOOK_PROVIDER_TIMESTAMP_INVALID")
    # Current CLOB documentation/examples use Unix timestamps.  Production has also
    # historically exposed millisecond strings, so normalize only that explicit
    # magnitude rather than guessing arbitrary units.
    if raw >= 100_000_000_000.0:
        raw /= 1000.0
    if raw < 946684800.0:  # 2000-01-01; ancient/replayed evidence is not exact-live.
        raise WeatherCLOBError("BOOK_PROVIDER_TIMESTAMP_ANCIENT")
    age = float(received_at) - raw
    if age > MAX_PROVIDER_BOOK_AGE_SECONDS:
        raise WeatherCLOBError("BOOK_PROVIDER_TIMESTAMP_STALE")
    if age < -MAX_PROVIDER_FUTURE_SKEW_SECONDS:
        raise WeatherCLOBError("BOOK_PROVIDER_TIMESTAMP_FUTURE")
    return raw


def _levels(raw: object, name: str) -> list[tuple[float, float]]:
    if not isinstance(raw, list):
        raise WeatherCLOBError("BOOK_SIDE_INVALID")
    out: list[tuple[float, float]] = []
    for level in raw:
        if not isinstance(level, dict):
            raise WeatherCLOBError("BOOK_LEVEL_INVALID")
        price = _finite(level.get("price"), "BOOK_PRICE_INVALID")
        size = _finite(level.get("size"), "BOOK_SIZE_INVALID")
        if not (0.0 < price < 1.0) or size <= 0.0:
            raise WeatherCLOBError("BOOK_LEVEL_INVALID")
        out.append((price, size))
    return out


def parse_book_v4(
    token_id: str,
    condition_id: str,
    outcome: str,
    payload: object,
    *,
    received_at: float,
    parameters: WeatherMarketParameters,
) -> V4BookEvidence:
    if not isinstance(payload, dict):
        raise WeatherCLOBError("BOOK_ENVELOPE_INVALID")
    token = str(token_id or "").strip()
    condition = str(condition_id or "").strip()
    if str(payload.get("asset_id") or "").strip() != token:
        raise WeatherCLOBError("BOOK_TOKEN_IDENTITY_MISMATCH")
    if str(payload.get("market") or "").strip().lower() != condition.lower():
        raise WeatherCLOBError("BOOK_CONDITION_IDENTITY_MISMATCH")
    book_hash = str(payload.get("hash") or "").strip()
    if not book_hash:
        raise WeatherCLOBError("BOOK_HASH_MISSING")
    provider_ts = _provider_timestamp(payload.get("timestamp"), received_at=received_at)
    raw_min = _finite(payload.get("min_order_size"), "BOOK_MIN_ORDER_SIZE_MISSING")
    raw_tick = _finite(payload.get("tick_size"), "BOOK_TICK_SIZE_MISSING")
    if raw_min <= 0.0 or abs(raw_min - float(parameters.minimum_order_size)) > 1e-9:
        raise WeatherCLOBError("BOOK_MIN_ORDER_SIZE_MISMATCH")
    if raw_tick <= 0.0 or abs(raw_tick - float(parameters.minimum_tick_size)) > 1e-12:
        raise WeatherCLOBError("BOOK_TICK_SIZE_MISMATCH")
    bids = _levels(payload.get("bids"), "bids")
    asks = _levels(payload.get("asks"), "asks")
    if bids and asks and max(price for price, _ in bids) >= min(price for price, _ in asks):
        raise WeatherCLOBError("BOOK_CROSSED")
    last = payload.get("last_trade_price")
    last_price = None if last in (None, "") else _finite(last, "BOOK_LAST_PRICE_INVALID")
    if last_price is not None and not 0.0 <= last_price <= 1.0:
        raise WeatherCLOBError("BOOK_LAST_PRICE_INVALID")
    book = Book(
        token_id=token,
        bids=bids,
        asks=asks,
        last_trade_price=last_price,
        timestamp=str(payload.get("timestamp")),
        received_at=float(received_at),
        source="clob_exact_rest_v4",
        book_hash=book_hash,
    )
    return V4BookEvidence(
        token_id=token,
        condition_id=condition,
        outcome=str(outcome).upper(),
        book=book,
        provider_timestamp=provider_ts,
        received_at=float(received_at),
        book_hash=book_hash,
        minimum_order_size=raw_min,
        minimum_tick_size=raw_tick,
    )


class WeatherCLOBClientV4:
    def __init__(self) -> None:
        self.http = httpx.AsyncClient(
            timeout=settings.request_timeout,
            limits=httpx.Limits(
                max_connections=CLOB_MAX_CONNECTIONS,
                max_keepalive_connections=CLOB_MAX_KEEPALIVE_CONNECTIONS,
                keepalive_expiry=CLOB_KEEPALIVE_EXPIRY_SECONDS,
            ),
            headers={"User-Agent": "polymarket-weather-paper-v4/1.0"},
            trust_env=False,
        )

    async def close(self) -> None:
        await self.http.aclose()

    async def _get_market_info(self, condition_id: str) -> WeatherMarketParameters:
        response = None
        for attempt in range(CLOB_TRANSIENT_MAX_ATTEMPTS):
            try:
                response = await self.http.get(f"{CLOB}/clob-markets/{condition_id}")
            except (httpx.TimeoutException, httpx.RequestError):
                if attempt + 1 >= CLOB_TRANSIENT_MAX_ATTEMPTS:
                    raise WeatherCLOBError("CLOB_TRANSPORT") from None
                await asyncio.sleep(_retry_delay_seconds(attempt))
                continue
            break
        if response is None or response.status_code >= 400:
            raise WeatherCLOBError("CLOB_MARKET_INFO_HTTP")
        received = time.time()
        try:
            payload = response.json()
        except Exception:
            raise WeatherCLOBError("MARKET_INFO_JSON_INVALID") from None
        return parse_market_info(condition_id, payload, received_at=received)

    async def _market_infos(self, condition_ids: list[str]) -> dict[str, WeatherMarketParameters]:
        ids = list(dict.fromkeys(str(value).strip() for value in condition_ids if str(value).strip()))
        if not ids or len(ids) > MAX_CONDITIONS:
            raise WeatherCLOBError("CONDITION_CAP_OR_EMPTY")
        semaphore = asyncio.Semaphore(8)

        async def one(condition: str):
            async with semaphore:
                return condition, await self._get_market_info(condition)

        rows = await asyncio.gather(*(one(condition) for condition in ids))
        out = {condition: info for condition, info in rows}
        if set(out) != set(ids):
            raise WeatherCLOBError("MARKET_INFO_INCOMPLETE")
        return out

    async def _books(
        self,
        token_identity: dict[str, tuple[str, str]],
        parameters: dict[str, WeatherMarketParameters],
    ) -> dict[str, V4BookEvidence]:
        tokens = list(dict.fromkeys(token_identity))
        if not tokens or len(tokens) > MAX_TOKENS:
            raise WeatherCLOBError("TOKEN_CAP_OR_EMPTY")
        out: dict[str, V4BookEvidence] = {}
        for start in range(0, len(tokens), 100):
            chunk = tokens[start:start + 100]
            response = None
            for attempt in range(CLOB_TRANSIENT_MAX_ATTEMPTS):
                try:
                    response = await self.http.post(
                        f"{CLOB}/books", json=[{"token_id": token} for token in chunk]
                    )
                except (httpx.TimeoutException, httpx.RequestError):
                    if attempt + 1 >= CLOB_TRANSIENT_MAX_ATTEMPTS:
                        raise WeatherCLOBError("CLOB_TRANSPORT") from None
                    await asyncio.sleep(_retry_delay_seconds(attempt))
                    continue
                break
            if response is None or response.status_code >= 400:
                raise WeatherCLOBError("CLOB_BOOK_HTTP")
            received = time.time()
            try:
                payload = response.json()
            except Exception:
                raise WeatherCLOBError("BOOK_JSON_INVALID") from None
            if not isinstance(payload, list):
                raise WeatherCLOBError("BOOK_BATCH_ENVELOPE_INVALID")
            for raw in payload:
                if not isinstance(raw, dict):
                    raise WeatherCLOBError("BOOK_ENVELOPE_INVALID")
                token = str(raw.get("asset_id") or "").strip()
                if token not in chunk or token in out:
                    raise WeatherCLOBError("BOOK_BATCH_IDENTITY_INVALID")
                condition, outcome = token_identity[token]
                out[token] = parse_book_v4(
                    token,
                    condition,
                    outcome,
                    raw,
                    received_at=received,
                    parameters=parameters[condition],
                )
            if not set(chunk).issubset(out):
                raise WeatherCLOBError("BOOK_BATCH_INCOMPLETE")
        return out

    async def exact_event_snapshot(self, compiled: V4CompiledWeatherEvent) -> V4ExecutionSnapshot:
        started = time.time()
        condition_ids: list[str] = []
        token_identity: dict[str, tuple[str, str]] = {}
        for bucket in compiled.buckets:
            if not bucket.trade_open:
                continue
            if not bucket.condition_id or not bucket.yes_token or not bucket.no_token:
                raise WeatherCLOBError("COMPILED_BUCKET_IDENTITY_INCOMPLETE")
            condition_ids.append(bucket.condition_id)
            token_identity[str(bucket.yes_token)] = (bucket.condition_id, "YES")
            token_identity[str(bucket.no_token)] = (bucket.condition_id, "NO")
        params = await self._market_infos(condition_ids)
        # Token presence is not enough: exact token -> outcome meaning must agree.
        for token, (condition, wanted) in token_identity.items():
            mapping = {str(t): str(outcome).strip().upper() for t, outcome in params[condition].token_outcomes}
            if mapping.get(token) != wanted:
                raise WeatherCLOBError("GAMMA_CLOB_TOKEN_OUTCOME_MISMATCH")
        evidence = await self._books(token_identity, params)
        finished = time.time()
        if any(finished - item.received_at > MAX_LOCAL_BATCH_AGE_SECONDS for item in evidence.values()):
            raise WeatherCLOBError("BOOK_SNAPSHOT_STALE")
        return V4ExecutionSnapshot(
            version=WEATHER_CLOB_V4_VERSION,
            event_id=compiled.event_id,
            books={token: item.book for token, item in evidence.items()},
            book_evidence=evidence,
            parameters=params,
            started_at=started,
            finished_at=finished,
            exact_clob=True,
            financial_authority=False,
        )


class GammaMarketStateClientV4:
    """Public read-only send-time market identity/state recheck."""

    def __init__(self) -> None:
        self.http = httpx.AsyncClient(
            timeout=httpx.Timeout(10.0, connect=5.0),
            limits=httpx.Limits(max_connections=4, max_keepalive_connections=2),
            headers={"User-Agent": "polymarket-weather-paper-v4-state/1.0"},
            trust_env=False,
        )

    async def close(self) -> None:
        await self.http.aclose()

    async def market(self, market_id: str) -> dict:
        mid = str(market_id or "").strip()
        if not mid:
            raise WeatherCLOBError("GAMMA_MARKET_ID_MISSING")
        try:
            response = await self.http.get(f"{GAMMA}/markets/{mid}")
        except httpx.HTTPError:
            raise WeatherCLOBError("GAMMA_MARKET_STATE_TRANSPORT") from None
        if response.status_code >= 400:
            raise WeatherCLOBError("GAMMA_MARKET_STATE_HTTP")
        try:
            payload = response.json()
        except Exception:
            raise WeatherCLOBError("GAMMA_MARKET_STATE_JSON") from None
        if not isinstance(payload, dict):
            raise WeatherCLOBError("GAMMA_MARKET_STATE_ENVELOPE")
        return payload

    async def assert_bucket_open(self, compiled: V4CompiledWeatherEvent, market_id: str) -> dict:
        bucket = next((row for row in compiled.buckets if row.market_id == str(market_id)), None)
        if bucket is None:
            raise WeatherCLOBError("GAMMA_MARKET_NOT_IN_CONTRACT")
        payload = await self.market(bucket.market_id)
        if str(payload.get("id") or "").strip() != bucket.market_id:
            raise WeatherCLOBError("GAMMA_MARKET_IDENTITY_MISMATCH")
        if str(payload.get("conditionId") or "").strip().lower() != bucket.condition_id.lower():
            raise WeatherCLOBError("GAMMA_CONDITION_IDENTITY_MISMATCH")
        outcomes_raw = payload.get("outcomes")
        tokens_raw = payload.get("clobTokenIds")
        if isinstance(outcomes_raw, str):
            import json
            try:
                outcomes_raw = json.loads(outcomes_raw)
            except Exception:
                outcomes_raw = None
        if isinstance(tokens_raw, str):
            import json
            try:
                tokens_raw = json.loads(tokens_raw)
            except Exception:
                tokens_raw = None
        if not isinstance(outcomes_raw, list) or not isinstance(tokens_raw, list) or len(outcomes_raw) != 2 or len(tokens_raw) != 2:
            raise WeatherCLOBError("GAMMA_BINARY_MAPPING_INVALID")
        mapping = {str(outcomes_raw[i]).strip().upper(): str(tokens_raw[i]).strip() for i in range(2)}
        if mapping.get("YES") != bucket.yes_token or mapping.get("NO") != bucket.no_token:
            raise WeatherCLOBError("GAMMA_TOKEN_OUTCOME_MAPPING_CHANGED")
        if not (
            payload.get("active") is True
            and payload.get("closed") is not True
            and payload.get("acceptingOrders") is True
            and payload.get("enableOrderBook") is True
        ):
            raise WeatherCLOBError("GAMMA_MARKET_NOT_TRADE_OPEN")
        return payload
