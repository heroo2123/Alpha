from __future__ import annotations

"""Current public Polymarket Data API trade feed for maker PAPER research.

The current documented public endpoint is ``GET https://data-api.polymarket.com/trades``.
It is distinct from the authenticated CLOB ``GET /trades`` endpoint.  The public Data
API uses camelCase fields and offset/limit pagination; an older experimental weather
adapter incorrectly assumed a ``/v2/trades`` cursor envelope.  This module freezes the
current public contract and fails closed on schema/identity drift.

``takerOnly=true`` is always sent.  Each returned row is therefore treated as a
*taker-side* public trade row for the research queue simulator.  This is still only a
PAPER fill model: public rows never gain actual-fill, order, wallet, or financial
authority.
"""

import hashlib
import json
import math
import re
from collections.abc import Iterable
from dataclasses import asdict, dataclass, field

import httpx

from .weather_only_maker_shadow import PublicTradePrint
from .weather_only_three_layer_guarded import _bounded_async_bytes
from .weather_only_trade_feed import WeatherTradeFeedError


PUBLIC_DATA_TRADES_URL = "https://data-api.polymarket.com/trades"
PUBLIC_TRADE_FEED_VERSION = "weather_trade_feed_v4_public_data_api_offset_taker_only"
PUBLIC_TRADE_FEED_MAX_RESPONSE_BYTES = 2 * 1024 * 1024
PUBLIC_TRADE_FEED_DEADLINE_SECONDS = 12.0
PUBLIC_TRADE_FEED_MAX_PAGE_SIZE = 1000
PUBLIC_TRADE_FEED_MAX_OFFSET = 10_000
_CONDITION_RE = re.compile(r"^0x[a-fA-F0-9]{64}$")
_TX_RE = re.compile(r"^0x[a-fA-F0-9]{64}$")
_WALLET_RE = re.compile(r"^0x[a-fA-F0-9]{40}$")


def _conditions(values: Iterable[str]) -> tuple[str, ...]:
    normalized: list[str] = []
    for raw in values:
        value = str(raw or "").strip().lower()
        if not _CONDITION_RE.fullmatch(value):
            raise WeatherTradeFeedError("TRADE_FEED_CONDITION_INVALID")
        normalized.append(value)
    if not 1 <= len(normalized) <= 20:
        raise WeatherTradeFeedError("TRADE_FEED_CONDITION_COUNT_INVALID")
    if len(set(normalized)) != len(normalized):
        raise WeatherTradeFeedError("TRADE_FEED_DUPLICATE_CONDITION")
    return tuple(normalized)


def _finite(value: object, code: str, *, positive: bool = False) -> float:
    if value is None or isinstance(value, bool):
        raise WeatherTradeFeedError(code)
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        raise WeatherTradeFeedError(code) from None
    if not math.isfinite(number) or (positive and number <= 0.0):
        raise WeatherTradeFeedError(code)
    return number


def _integer(value: object, code: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise WeatherTradeFeedError(code)
    return value


def _text(value: object, code: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise WeatherTradeFeedError(code)
    return value.strip()


def _canonical(value: object) -> str:
    try:
        return json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        )
    except (TypeError, ValueError):
        raise WeatherTradeFeedError("TRADE_FEED_CANONICAL_JSON_INVALID") from None


def _trade_identity(row: dict) -> str:
    # The Data API does not expose a first-class fill id.  Bind every public field
    # needed to distinguish same-transaction rows.  A collision within one response
    # is ambiguous and therefore rejects the whole page rather than double counting.
    identity = {
        "condition_id": row["condition_id"],
        "token_id": row["token_id"],
        "transaction_hash": row["transaction_hash"],
        "proxy_wallet": row["proxy_wallet"],
        "side": row["side"],
        "size": row["size"],
        "price": row["price"],
        "timestamp": row["timestamp"],
        "outcome": row["outcome"],
        "outcome_index": row["outcome_index"],
    }
    return "data-public:" + hashlib.sha256(_canonical(identity).encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class PublicWeatherTradePage:
    version: str
    condition_ids: tuple[str, ...]
    requested_offset: int
    requested_limit: int
    next_offset: int | None
    page_size: int
    fetched_at: float
    trades: tuple[PublicTradePrint, ...]
    taker_only_requested: bool = field(init=False, default=True)
    offset_pagination: bool = field(init=False, default=True)
    public_row_identity_is_surrogate: bool = field(init=False, default=True)
    actual_fill_authority: bool = field(init=False, default=False)
    financial_authority: bool = field(init=False, default=False)

    def as_dict(self) -> dict:
        value = asdict(self)
        value["trades"] = [asdict(row) for row in self.trades]
        return value


def parse_public_data_trade_page(
    payload: object,
    *,
    condition_ids: tuple[str, ...],
    requested_offset: int,
    requested_limit: int,
    fetched_at: float,
) -> PublicWeatherTradePage:
    if not isinstance(payload, list):
        raise WeatherTradeFeedError("TRADE_FEED_RESPONSE_NOT_ARRAY")
    if len(payload) > requested_limit:
        raise WeatherTradeFeedError("TRADE_FEED_PAGE_EXCEEDS_LIMIT")
    fetched = _finite(fetched_at, "TRADE_FEED_FETCHED_AT_INVALID")
    if fetched < 0.0:
        raise WeatherTradeFeedError("TRADE_FEED_FETCHED_AT_INVALID")

    allowed = set(condition_ids)
    seen: set[str] = set()
    parsed: list[PublicTradePrint] = []
    for raw in payload:
        if not isinstance(raw, dict):
            raise WeatherTradeFeedError("TRADE_FEED_ROW_INVALID")
        condition = _text(raw.get("conditionId"), "TRADE_FEED_CONDITION_MISSING").lower()
        if condition not in allowed:
            raise WeatherTradeFeedError("TRADE_FEED_CONDITION_IDENTITY_MISMATCH")
        if not _CONDITION_RE.fullmatch(condition):
            raise WeatherTradeFeedError("TRADE_FEED_CONDITION_INVALID")
        token = _text(raw.get("asset"), "TRADE_FEED_TOKEN_MISSING")
        tx = _text(raw.get("transactionHash"), "TRADE_FEED_TRANSACTION_HASH_MISSING").lower()
        wallet = _text(raw.get("proxyWallet"), "TRADE_FEED_PROXY_WALLET_MISSING").lower()
        if not _TX_RE.fullmatch(tx):
            raise WeatherTradeFeedError("TRADE_FEED_TRANSACTION_HASH_INVALID")
        if not _WALLET_RE.fullmatch(wallet):
            raise WeatherTradeFeedError("TRADE_FEED_PROXY_WALLET_INVALID")
        side = _text(raw.get("side"), "TRADE_FEED_SIDE_MISSING").upper()
        if side not in {"BUY", "SELL"}:
            raise WeatherTradeFeedError("TRADE_FEED_SIDE_INVALID")
        size = _finite(raw.get("size"), "TRADE_FEED_SIZE_INVALID", positive=True)
        price = _finite(raw.get("price"), "TRADE_FEED_PRICE_INVALID", positive=True)
        if price >= 1.0:
            raise WeatherTradeFeedError("TRADE_FEED_PRICE_INVALID")
        timestamp = _integer(raw.get("timestamp"), "TRADE_FEED_TIMESTAMP_INVALID")
        if timestamp <= 0 or float(timestamp) > fetched + 1e-9:
            raise WeatherTradeFeedError("TRADE_FEED_TIMESTAMP_INVALID")
        outcome = _text(raw.get("outcome"), "TRADE_FEED_OUTCOME_MISSING")
        outcome_index = _integer(raw.get("outcomeIndex"), "TRADE_FEED_OUTCOME_INDEX_INVALID")

        normalized = {
            "condition_id": condition,
            "token_id": token,
            "transaction_hash": tx,
            "proxy_wallet": wallet,
            "side": side,
            "size": size,
            "price": price,
            "timestamp": timestamp,
            "outcome": outcome,
            "outcome_index": outcome_index,
        }
        trade_id = _trade_identity(normalized)
        if trade_id in seen:
            raise WeatherTradeFeedError("TRADE_FEED_SURROGATE_ID_COLLISION")
        seen.add(trade_id)
        parsed.append(
            PublicTradePrint(
                trade_id=trade_id,
                token_id=token,
                price=price,
                shares=size,
                received_at=fetched,
                # takerOnly=true is part of every request, so the returned trade side
                # is used as the aggressor side for PAPER queue progression.
                aggressor_side=side,
                source="POLYMARKET_DATA_API_PUBLIC_TAKER_ONLY",
                executed_at=float(timestamp),
            )
        )

    next_offset: int | None = None
    if len(parsed) == requested_limit:
        candidate = requested_offset + len(parsed)
        if candidate <= PUBLIC_TRADE_FEED_MAX_OFFSET:
            next_offset = candidate

    return PublicWeatherTradePage(
        version=PUBLIC_TRADE_FEED_VERSION,
        condition_ids=condition_ids,
        requested_offset=requested_offset,
        requested_limit=requested_limit,
        next_offset=next_offset,
        page_size=len(parsed),
        fetched_at=fetched,
        trades=tuple(parsed),
    )


class PublicWeatherDataAPITradeFeedClient:
    """Bounded, unauthenticated, read-only current Data API trade feed."""

    def __init__(self, *, http: httpx.AsyncClient | None = None) -> None:
        self._owns_http = http is None
        self.http = http or httpx.AsyncClient(
            timeout=httpx.Timeout(8.0, connect=4.0, read=8.0, write=5.0, pool=4.0),
            follow_redirects=False,
            trust_env=False,
            limits=httpx.Limits(max_connections=2, max_keepalive_connections=2),
            headers={
                "User-Agent": "polymarket-weather-maker-shadow-public/2.0 (+https://github.com/heroo2123/Alpha)",
                "Accept": "application/json",
                "Accept-Encoding": "identity",
            },
        )

    async def close(self) -> None:
        if self._owns_http:
            await self.http.aclose()

    async def page(
        self,
        *,
        condition_ids: tuple[str, ...],
        offset: int = 0,
        limit: int = 200,
    ) -> PublicWeatherTradePage:
        conditions = _conditions(condition_ids)
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= PUBLIC_TRADE_FEED_MAX_PAGE_SIZE:
            raise WeatherTradeFeedError("TRADE_FEED_LIMIT_INVALID")
        if isinstance(offset, bool) or not isinstance(offset, int) or not 0 <= offset <= PUBLIC_TRADE_FEED_MAX_OFFSET:
            raise WeatherTradeFeedError("TRADE_FEED_OFFSET_INVALID")

        params: dict[str, object] = {
            "market": ",".join(conditions),
            "takerOnly": "true",
            "filterType": "TOKENS",
            "filterAmount": "0.01",
            "limit": limit,
            "offset": offset,
        }
        try:
            status, raw, fetched_at = await _bounded_async_bytes(
                self.http,
                PUBLIC_DATA_TRADES_URL,
                params=params,
                headers={"Accept": "application/json"},
                max_bytes=PUBLIC_TRADE_FEED_MAX_RESPONSE_BYTES,
                total_deadline_seconds=PUBLIC_TRADE_FEED_DEADLINE_SECONDS,
                allow_same_host_redirects=False,
                redirect_code="TRADE_FEED_REDIRECT",
                encoding_code="TRADE_FEED_UNSUPPORTED_ENCODING",
                size_code="TRADE_FEED_RESPONSE_CAP",
                timeout_code="TRADE_FEED_TIMEOUT",
                transport_code="TRADE_FEED_TRANSPORT_ERROR",
            )
        except RuntimeError as exc:
            raise WeatherTradeFeedError(str(exc)) from None
        if status == 429:
            raise WeatherTradeFeedError("TRADE_FEED_RATE_LIMITED")
        if status != 200:
            raise WeatherTradeFeedError(f"TRADE_FEED_HTTP_{status}")
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeError, json.JSONDecodeError):
            raise WeatherTradeFeedError("TRADE_FEED_JSON_INVALID") from None
        return parse_public_data_trade_page(
            payload,
            condition_ids=conditions,
            requested_offset=offset,
            requested_limit=limit,
            fetched_at=fetched_at,
        )
