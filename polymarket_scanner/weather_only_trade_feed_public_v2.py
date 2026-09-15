from __future__ import annotations

"""Current public Polymarket Data API v2 trade feed for maker PAPER research.

Polymarket's current Data API v2 contract documents all v2 reads as public and
unauthenticated.  The older weather adapter required a bearer token; this adapter
matches the current public contract while reusing the strict row parser and causal
trade-print identity rules.

This is read-only public market data.  It has no CLOB credentials, wallet authority,
order placement, cancellation or actual-fill authority.
"""

from collections.abc import Iterable

import httpx

from .weather_only_three_layer_guarded import _bounded_async_json
from .weather_only_trade_feed import (
    DATA_API_V2_TRADES_URL,
    MAX_PAGE_SIZE,
    WeatherTradeFeedError,
    WeatherTradeFeedPage,
    parse_data_api_v2_trade_page,
)


PUBLIC_TRADE_FEED_VERSION = "weather_trade_feed_v3_public_data_api_v2_no_auth"
PUBLIC_TRADE_FEED_MAX_RESPONSE_BYTES = 2 * 1024 * 1024
PUBLIC_TRADE_FEED_DEADLINE_SECONDS = 12.0


def _conditions(values: Iterable[str]) -> tuple[str, ...]:
    normalized: list[str] = []
    for raw in values:
        value = str(raw or "").strip().lower()
        if not value.startswith("0x") or len(value) != 66:
            raise WeatherTradeFeedError("TRADE_FEED_CONDITION_INVALID")
        if any(ch not in "0123456789abcdef" for ch in value[2:]):
            raise WeatherTradeFeedError("TRADE_FEED_CONDITION_INVALID")
        normalized.append(value)
    if not 1 <= len(normalized) <= 20:
        raise WeatherTradeFeedError("TRADE_FEED_CONDITION_COUNT_INVALID")
    if len(set(normalized)) != len(normalized):
        raise WeatherTradeFeedError("TRADE_FEED_DUPLICATE_CONDITION")
    return tuple(normalized)


class PublicWeatherDataAPITradeFeedClient:
    """Bounded, unauthenticated, read-only Data API v2 trade feed."""

    def __init__(self, *, http: httpx.AsyncClient | None = None) -> None:
        self._owns_http = http is None
        self.http = http or httpx.AsyncClient(
            timeout=httpx.Timeout(8.0, connect=4.0, read=8.0, write=5.0, pool=4.0),
            follow_redirects=False,
            trust_env=False,
            limits=httpx.Limits(max_connections=2, max_keepalive_connections=2),
            headers={
                "User-Agent": "polymarket-weather-maker-shadow-public-v2/1.0 (+https://github.com/heroo2123/Alpha)",
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
        cursor: str | None = None,
        limit: int = 200,
    ) -> WeatherTradeFeedPage:
        conditions = _conditions(condition_ids)
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= MAX_PAGE_SIZE:
            raise WeatherTradeFeedError("TRADE_FEED_LIMIT_INVALID")
        requested_cursor = None
        if cursor is not None:
            if not isinstance(cursor, str) or not cursor.strip():
                raise WeatherTradeFeedError("TRADE_FEED_CURSOR_INVALID")
            requested_cursor = cursor.strip()

        params: dict[str, object] = {
            "condition": ",".join(conditions),
            "taker_only": "true",
            "filter_type": "TOKENS",
            "filter_amount": "0.01",
        }
        if requested_cursor is None:
            params["limit"] = limit
        else:
            # Data API v2 requires the same filters on every feed page.  Page size is
            # carried by the opaque cursor after the first request.
            params["cursor"] = requested_cursor

        try:
            payload, fetched_at = await _bounded_async_json(
                self.http,
                DATA_API_V2_TRADES_URL,
                params=params,
                max_bytes=PUBLIC_TRADE_FEED_MAX_RESPONSE_BYTES,
                total_deadline_seconds=PUBLIC_TRADE_FEED_DEADLINE_SECONDS,
                redirect_code="TRADE_FEED_REDIRECT",
                status_code="TRADE_FEED_HTTP_STATUS",
                encoding_code="TRADE_FEED_UNSUPPORTED_ENCODING",
                size_code="TRADE_FEED_RESPONSE_CAP",
                json_code="TRADE_FEED_JSON_INVALID",
                timeout_code="TRADE_FEED_TIMEOUT",
                transport_code="TRADE_FEED_TRANSPORT_ERROR",
            )
        except RuntimeError as exc:
            raise WeatherTradeFeedError(str(exc)) from None

        page = parse_data_api_v2_trade_page(
            payload,
            condition_ids=conditions,
            requested_cursor=requested_cursor,
            fetched_at=fetched_at,
        )
        # Keep the parser's evidence shape stable; adapter version remains available
        # separately so persisted maker state can identify the public/no-auth transport.
        return page
