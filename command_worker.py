from __future__ import annotations

import asyncio
import logging
import os
import time

import httpx

from polymarket_scanner.config import settings
from polymarket_scanner.store import Store
from polymarket_scanner.telegram import Telegram

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(message)s",
)
log = logging.getLogger("polybot.command_worker")


class _CachedHealthResponse:
    def __init__(self, data: dict) -> None:
        self._data = data

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return self._data


class _CachedHealthClient:
    """Drop-in replacement for Telegram.local_http.

    Telegram /status continues to use its normal formatting code, but reads the
    latest background health snapshot instead of waiting on uvicorn. This keeps
    commands responsive even when the scanner process is busy for many seconds.
    """

    def __init__(self, cache: dict, tg: Telegram) -> None:
        self.cache = cache
        self.tg = tg

    async def get(self, _url: str) -> _CachedHealthResponse:
        raw = self.cache.get("data")
        cached_at = self.cache.get("cached_at")
        if not isinstance(raw, dict) or not cached_at:
            raise RuntimeError("scanner health cache is warming up")

        data = dict(raw)
        age = max(0.0, time.time() - float(cached_at))

        # The standalone command worker owns getUpdates, so overlay its live
        # command state instead of the scanner process's intentionally-empty
        # in-process Telegram fields.
        data["telegram_last_command_poll"] = self.tg.last_command_poll_at
        data["telegram_command_error"] = self.tg.last_command_error

        # A stale snapshot is still useful, but must never be reported HEALTHY.
        if age > 20.0:
            previous = data.get("last_error")
            stale = f"scanner health heartbeat stale ({age:.0f}s old)"
            data["ok"] = False
            data["last_error"] = f"{stale}; previous: {previous}" if previous else stale

        return _CachedHealthResponse(data)

    async def aclose(self) -> None:
        return None


async def health_cache_loop(cache: dict) -> None:
    """Sample scanner health independently from Telegram command processing."""
    timeout = httpx.Timeout(1.0, connect=0.5)
    limits = httpx.Limits(max_connections=2, max_keepalive_connections=1)
    async with httpx.AsyncClient(timeout=timeout, limits=limits, trust_env=False) as client:
        while True:
            try:
                response = await client.get("http://127.0.0.1:8000/health")
                response.raise_for_status()
                data = response.json()
                if isinstance(data, dict):
                    cache["data"] = data
                    cache["cached_at"] = time.time()
                    cache["probe_error"] = None
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                # Do not block or spam Telegram. A stale cached snapshot will be
                # marked DEGRADED by _CachedHealthClient if this persists.
                cache["probe_error"] = repr(exc)
            await asyncio.sleep(2.0)


async def main() -> None:
    """Run Telegram getUpdates in a process isolated from the scanner.

    The scanner process is CPU/network heavy. Keeping commands in their own
    process guarantees that /status, /help, /stats and /took continue to be
    consumed even when scanner evaluation or market feeds are busy.
    """
    store = Store(settings.db_path)
    tg = Telegram(store)
    if not tg.token_enabled:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is not configured")

    # /status must not perform a live request to the scanner. Keep a health
    # snapshot refreshed in a separate coroutine and serve commands from cache.
    health_cache: dict = {}
    await tg.local_http.aclose()
    tg.local_http = _CachedHealthClient(health_cache, tg)  # type: ignore[assignment]
    health_task = asyncio.create_task(health_cache_loop(health_cache))

    log.info("standalone Telegram command worker started with cached scanner health")
    try:
        while True:
            try:
                await tg.poll_commands()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log.warning("Telegram command poll failed: %r", exc)
                await asyncio.sleep(1.0)
            else:
                await asyncio.sleep(0.10)
    finally:
        health_task.cancel()
        await asyncio.gather(health_task, return_exceptions=True)
        await tg.close()


if __name__ == "__main__":
    asyncio.run(main())
