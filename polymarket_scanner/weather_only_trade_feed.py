from __future__ import annotations

"""Strict read-only Polymarket Data API v2 trade adapter for weather maker shadow.

Polymarket documents ``GET /v2/trades?taker_only=true`` as serving each fill once on
its taker side.  That makes ``side`` suitable as aggressor direction for research fill
simulation.  The response does not expose a first-class fill id, so this adapter uses a
canonical row fingerprint as a *conservative surrogate identity*: if two rows in one
page collapse to the same fingerprint, the page fails closed instead of assuming they
are one fill or double-counting them.

The cursor is part of the evidence page and must be persisted by the caller.  This
module never posts/cancels orders, never authenticates to the CLOB trading API and
never grants actual-fill or financial authority.
"""

import hashlib
import json
import math
import re
import time
from dataclasses import asdict, dataclass, field

import httpx

from .weather_only_maker_shadow import PublicTradePrint


WEATHER_TRADE_FEED_VERSION = "weather_trade_feed_v1_data_api_v2_taker_once_cursor_fail_closed"
DATA_API_V2_TRADES_URL = "https://data-api.polymarket.com/v2/trades"
MAX_PAGE_SIZE = 1000
_CONDITION_RE = re.compile(r"^0x[a-fA-F0-9]{64}$")
_TX_RE = re.compile(r"^0x[a-fA-F0-9]{64}$")
_WALLET_RE = re.compile(r"^0x[a-fA-F0-9]{40}$")


class WeatherTradeFeedError(RuntimeError):
    def __init__(self, code: str):
        self.code = str(code)
        super().__init__(self.code)


def _finite(value: object, *, positive: bool = False, nonnegative: bool = False) -> float:
    if isinstance(value, bool):
        raise WeatherTradeFeedError("TRADE_FEED_NUMBER_INVALID")
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        raise WeatherTradeFeedError("TRADE_FEED_NUMBER_INVALID") from None
    if not math.isfinite(number):
        raise WeatherTradeFeedError("TRADE_FEED_NUMBER_INVALID")
    if positive and number <= 0.0:
        raise WeatherTradeFeedError("TRADE_FEED_NUMBER_INVALID")
    if nonnegative and number < 0.0:
        raise WeatherTradeFeedError("TRADE_FEED_NUMBER_INVALID")
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
        return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False)
    except (TypeError, ValueError):
        raise WeatherTradeFeedError("TRADE_FEED_CANONICAL_JSON_INVALID") from None


def _row_identity(row: dict) -> str:
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
    return "data-v2:" + hashlib.sha256(_canonical(identity).encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class WeatherTradeFeedPage:
    version: str
    condition_ids: tuple[str, ...]
    requested_cursor: str | None
    next_cursor: str | None
    has_more: bool
    page_size: int
    fetched_at: float
    trades: tuple[PublicTradePrint, ...]
    exact_taker_side_semantics: bool = field(init=False, default=True)
    public_row_identity_is_surrogate: bool = field(init=False, default=True)
    actual_fill_authority: bool = field(init=False, default=False)
    financial_authority: bool = field(init=False, default=False)

    def as_dict(self) -> dict:
        value = asdict(self)
        value["trades"] = [asdict(row) for row in self.trades]
        return value


def parse_data_api_v2_trade_page(
    payload: object,
    *,
    condition_ids: tuple[str, ...],
    requested_cursor: str | None,
    fetched_at: float,
) -> WeatherTradeFeedPage:
    if not isinstance(payload, dict):
        raise WeatherTradeFeedError("TRADE_FEED_RESPONSE_NOT_OBJECT")
    rows = payload.get("data")
    pagination = payload.get("pagination")
    if not isinstance(rows, list) or not isinstance(pagination, dict):
        raise WeatherTradeFeedError("TRADE_FEED_ENVELOPE_INVALID")
    has_more = pagination.get("has_more")
    if type(has_more) is not bool:
        raise WeatherTradeFeedError("TRADE_FEED_HAS_MORE_INVALID")
    next_cursor_raw = pagination.get("next_cursor")
    if next_cursor_raw is not None and (not isinstance(next_cursor_raw, str) or not next_cursor_raw.strip()):
        raise WeatherTradeFeedError("TRADE_FEED_NEXT_CURSOR_INVALID")
    next_cursor = next_cursor_raw.strip() if isinstance(next_cursor_raw, str) else None
    if has_more and next_cursor is None:
        raise WeatherTradeFeedError("TRADE_FEED_CURSOR_MISSING_WITH_MORE")
    if not has_more and next_cursor is not None:
        raise WeatherTradeFeedError("TRADE_FEED_CURSOR_PRESENT_WITHOUT_MORE")
    if requested_cursor is not None and next_cursor == requested_cursor:
        raise WeatherTradeFeedError("TRADE_FEED_CURSOR_DID_NOT_ADVANCE")

    allowed_conditions = set(condition_ids)
    if not allowed_conditions:
        raise WeatherTradeFeedError("TRADE_FEED_CONDITION_SET_EMPTY")
    seen_ids: set[str] = set()
    parsed: list[PublicTradePrint] = []
    fetched = _finite(fetched_at, nonnegative=True)

    for raw in rows:
        if not isinstance(raw, dict):
            raise WeatherTradeFeedError("TRADE_FEED_ROW_INVALID")
        condition = _text(raw.get("condition_id"), "TRADE_FEED_CONDITION_MISSING")
        if condition not in allowed_conditions:
            raise WeatherTradeFeedError("TRADE_FEED_CONDITION_IDENTITY_MISMATCH")
        token = _text(raw.get("token_id"), "TRADE_FEED_TOKEN_MISSING")
        tx = _text(raw.get("transaction_hash"), "TRADE_FEED_TRANSACTION_HASH_MISSING")
        wallet = _text(raw.get("proxy_wallet"), "TRADE_FEED_PROXY_WALLET_MISSING")
        if not _TX_RE.fullmatch(tx):
            raise WeatherTradeFeedError("TRADE_FEED_TRANSACTION_HASH_INVALID")
        if not _WALLET_RE.fullmatch(wallet):
            raise WeatherTradeFeedError("TRADE_FEED_PROXY_WALLET_INVALID")
        side = _text(raw.get("side"), "TRADE_FEED_SIDE_MISSING").upper()
        if side not in {"BUY", "SELL"}:
            raise WeatherTradeFeedError("TRADE_FEED_SIDE_INVALID")
        price = _finite(raw.get("price"), positive=True)
        size = _finite(raw.get("size"), positive=True)
        if price >= 1.0:
            raise WeatherTradeFeedError("TRADE_FEED_PRICE_INVALID")
        timestamp = _integer(raw.get("timestamp"), "TRADE_FEED_TIMESTAMP_INVALID")
        if timestamp <= 0:
            raise WeatherTradeFeedError("TRADE_FEED_TIMESTAMP_INVALID")
        outcome = _text(raw.get("outcome"), "TRADE_FEED_OUTCOME_MISSING")
        outcome_index = _integer(raw.get("outcome_index"), "TRADE_FEED_OUTCOME_INDEX_INVALID")

        normalized = {
            "condition_id": condition,
            "token_id": token,
            "transaction_hash": tx.lower(),
            "proxy_wallet": wallet.lower(),
            "side": side,
            "size": size,
            "price": price,
            "timestamp": timestamp,
            "outcome": outcome,
            "outcome_index": outcome_index,
        }
        trade_id = _row_identity(normalized)
        if trade_id in seen_ids:
            # The public row contract lacks a first-class fill id. Identical canonical
            # rows could be a duplicated response or distinct same-shape fills; either
            # interpretation is unsafe for queue consumption, so reject the page.
            raise WeatherTradeFeedError("TRADE_FEED_SURROGATE_ID_COLLISION")
        seen_ids.add(trade_id)
        parsed.append(PublicTradePrint(
            trade_id=trade_id,
            token_id=token,
            price=price,
            shares=size,
            received_at=fetched,
            aggressor_side=side,
            source="POLYMARKET_DATA_API_V2_TAKER_ONLY",
            executed_at=float(timestamp),
        ))

    return WeatherTradeFeedPage(
        version=WEATHER_TRADE_FEED_VERSION,
        condition_ids=condition_ids,
        requested_cursor=requested_cursor,
        next_cursor=next_cursor,
        has_more=has_more,
        page_size=len(parsed),
        fetched_at=fetched,
        trades=tuple(parsed),
    )


class WeatherDataAPITradeFeedClient:
    """Bounded read-only v2 market-trade feed.

    A bearer token is accepted only as an in-memory argument/header. It is never
    included in exceptions, page evidence or logs produced by this module.
    """

    def __init__(
        self,
        bearer_token: str,
        *,
        timeout_seconds: float = 10.0,
        http: httpx.AsyncClient | None = None,
    ):
        token = _text(bearer_token, "TRADE_FEED_BEARER_TOKEN_MISSING")
        timeout = _finite(timeout_seconds, positive=True)
        self._token = token
        self._owns_http = http is None
        self.http = http or httpx.AsyncClient(timeout=timeout)

    async def close(self) -> None:
        if self._owns_http:
            await self.http.aclose()

    async def page(
        self,
        *,
        condition_ids: tuple[str, ...],
        cursor: str | None = None,
        limit: int = 200,
    ) -> WeatherTradeFeedPage:
        if not isinstance(condition_ids, tuple) or not 1 <= len(condition_ids) <= 20:
            raise WeatherTradeFeedError("TRADE_FEED_CONDITION_COUNT_INVALID")
        normalized: list[str] = []
        for condition in condition_ids:
            value = _text(condition, "TRADE_FEED_CONDITION_MISSING")
            if not _CONDITION_RE.fullmatch(value):
                raise WeatherTradeFeedError("TRADE_FEED_CONDITION_INVALID")
            normalized.append(value.lower())
        if len(set(normalized)) != len(normalized):
            raise WeatherTradeFeedError("TRADE_FEED_DUPLICATE_CONDITION")
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= MAX_PAGE_SIZE:
            raise WeatherTradeFeedError("TRADE_FEED_LIMIT_INVALID")
        if cursor is not None and (not isinstance(cursor, str) or not cursor.strip()):
            raise WeatherTradeFeedError("TRADE_FEED_CURSOR_INVALID")

        params: dict[str, object] = {
            "condition": ",".join(normalized),
            "taker_only": "true",
            "filter_type": "TOKENS",
            "filter_amount": "0.01",
        }
        requested_cursor = cursor.strip() if isinstance(cursor, str) else None
        if requested_cursor is None:
            params["limit"] = limit
        else:
            params["cursor"] = requested_cursor

        fetched_at = time.time()
        try:
            response = await self.http.get(
                DATA_API_V2_TRADES_URL,
                params=params,
                headers={"Authorization": f"Bearer {self._token}"},
            )
        except httpx.HTTPError:
            raise WeatherTradeFeedError("TRADE_FEED_TRANSPORT_ERROR") from None
        if response.status_code == 429:
            raise WeatherTradeFeedError("TRADE_FEED_RATE_LIMITED")
        if response.status_code != 200:
            raise WeatherTradeFeedError(f"TRADE_FEED_HTTP_{response.status_code}")
        try:
            payload = response.json()
        except ValueError:
            raise WeatherTradeFeedError("TRADE_FEED_JSON_INVALID") from None
        return parse_data_api_v2_trade_page(
            payload,
            condition_ids=tuple(normalized),
            requested_cursor=requested_cursor,
            fetched_at=fetched_at,
        )
