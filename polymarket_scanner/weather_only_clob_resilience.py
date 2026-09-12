from __future__ import annotations

"""Bounded transient transport resilience for the weather-only read-only CLOB client.

The underlying CLOB client remains the parsing and identity authority.  This adapter
retries only failures already classified as transport/timeout errors.  HTTP status,
JSON, identity, stale-book and semantic failures remain single-attempt fail-closed
outcomes.  The CLOB surface is read-only (market metadata and exact books); no order
method exists in this client.
"""

import asyncio

from . import weather_only_clob as _clob


CLOB_TRANSIENT_MAX_ATTEMPTS = 3
CLOB_TRANSIENT_RETRY_DELAY_SECONDS = 0.25
_RETRYABLE_CODES = frozenset({"CLOB_TIMEOUT", "CLOB_TRANSPORT"})

_BaseWeatherCLOBClient = _clob.WeatherCLOBClient


class ResilientWeatherCLOBClient(_BaseWeatherCLOBClient):
    async def _retry_transient(self, operation):
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

    async def market_info(self, condition_id: str):
        async def operation():
            return await _BaseWeatherCLOBClient.market_info(self, condition_id)

        return await self._retry_transient(operation)

    async def books(self, token_ids: list[str]):
        async def operation():
            return await _BaseWeatherCLOBClient.books(self, token_ids)

        return await self._retry_transient(operation)


def install_weather_clob_transport_resilience() -> None:
    """Install one package-wide weather CLOB class symbol before consumers import it."""
    _clob.CLOB_TRANSIENT_MAX_ATTEMPTS = CLOB_TRANSIENT_MAX_ATTEMPTS
    _clob.CLOB_TRANSIENT_RETRY_DELAY_SECONDS = CLOB_TRANSIENT_RETRY_DELAY_SECONDS
    if _clob.WeatherCLOBClient is not ResilientWeatherCLOBClient:
        _clob.WeatherCLOBClient = ResilientWeatherCLOBClient
