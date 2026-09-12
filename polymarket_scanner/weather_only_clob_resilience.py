from __future__ import annotations

"""Bounded transient transport resilience for the weather-only read-only CLOB client.

The underlying CLOB client remains the parsing and identity authority. This adapter
retries only failures already classified as transport/timeout errors. HTTP status,
JSON, identity, stale-book and semantic failures remain single-attempt fail-closed
outcomes. Installation replaces only the implementations of two existing read-only
methods; it does not add, remove or rename any WeatherCLOBClient method.
"""

import asyncio

from . import weather_only_clob as _clob


CLOB_TRANSIENT_MAX_ATTEMPTS = 3
CLOB_TRANSIENT_RETRY_DELAY_SECONDS = 0.25
_RETRYABLE_CODES = frozenset({"CLOB_TIMEOUT", "CLOB_TRANSPORT"})

_ORIGINAL_MARKET_INFO = _clob.WeatherCLOBClient.market_info
_ORIGINAL_BOOKS = _clob.WeatherCLOBClient.books
_INSTALLED = False


async def _retry_transient(operation):
    for attempt in range(CLOB_TRANSIENT_MAX_ATTEMPTS):
        try:
            return await operation()
        except _clob.WeatherCLOBError as exc:
            if exc.code not in _RETRYABLE_CODES:
                raise
            if attempt + 1 >= CLOB_TRANSIENT_MAX_ATTEMPTS:
                raise
            delay = float(getattr(
                _clob,
                "CLOB_TRANSIENT_RETRY_DELAY_SECONDS",
                CLOB_TRANSIENT_RETRY_DELAY_SECONDS,
            ))
            if delay > 0.0:
                await asyncio.sleep(delay * (attempt + 1))
    raise RuntimeError("unreachable CLOB retry state")


async def _market_info_with_transient_retry(self, condition_id: str):
    async def operation():
        return await _ORIGINAL_MARKET_INFO(self, condition_id)

    return await _retry_transient(operation)


async def _books_with_transient_retry(self, token_ids: list[str]):
    async def operation():
        return await _ORIGINAL_BOOKS(self, token_ids)

    return await _retry_transient(operation)


def install_weather_clob_transport_resilience() -> None:
    """Patch only existing read-only methods while preserving exact class surface."""
    global _INSTALLED
    _clob.CLOB_TRANSIENT_MAX_ATTEMPTS = CLOB_TRANSIENT_MAX_ATTEMPTS
    _clob.CLOB_TRANSIENT_RETRY_DELAY_SECONDS = CLOB_TRANSIENT_RETRY_DELAY_SECONDS
    if _INSTALLED:
        return
    _clob.WeatherCLOBClient.market_info = _market_info_with_transient_retry
    _clob.WeatherCLOBClient.books = _books_with_transient_retry
    _INSTALLED = True
