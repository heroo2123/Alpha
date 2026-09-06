from __future__ import annotations

import asyncio
import json
import logging
import os
import time

from polymarket_scanner.config import settings
from polymarket_scanner.store import Store
from polymarket_scanner.telegram import Telegram

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(message)s",
)
log = logging.getLogger("polybot.command_worker")


class _DatabaseHealthResponse:
    def __init__(self, data: dict) -> None:
        self._data = data

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return self._data


class _DatabaseHealthClient:
    """Drop-in replacement for Telegram.local_http using shared SQLite state."""

    def __init__(self, store: Store, tg: Telegram) -> None:
        self.store = store
        self.tg = tg

    async def get(self, _url: str) -> _DatabaseHealthResponse:
        raw = await asyncio.to_thread(self.store.get_state, "scanner_health_snapshot", "")
        if not raw:
            raise RuntimeError("scanner heartbeat is warming up")
        try:
            data = json.loads(raw)
        except Exception as exc:
            raise RuntimeError("scanner heartbeat is invalid") from exc
        if not isinstance(data, dict):
            raise RuntimeError("scanner heartbeat is invalid")

        data = dict(data)
        data["telegram_last_command_poll"] = self.tg.last_command_poll_at
        data["telegram_command_error"] = self.tg.last_command_error

        try:
            snapshot_at = float(data.get("snapshot_at"))
        except (TypeError, ValueError):
            snapshot_at = 0.0
        age = max(0.0, time.time() - snapshot_at) if snapshot_at else float("inf")
        if age > 15.0:
            previous = data.get("last_error")
            stale = "scanner heartbeat missing timestamp" if not snapshot_at else f"scanner heartbeat stale ({age:.0f}s old)"
            data["ok"] = False
            data["last_error"] = f"{stale}; previous: {previous}" if previous else stale

        return _DatabaseHealthResponse(data)

    async def aclose(self) -> None:
        return None


async def main() -> None:
    """Run Telegram commands in a process isolated from the scanner.

    /status reads scanner health from the shared SQLite heartbeat, never from
    uvicorn. Signal bursts and detector work therefore cannot block commands.
    """
    store = Store(settings.db_path)
    tg = Telegram(store)
    if not tg.token_enabled:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is not configured")

    await tg.local_http.aclose()
    tg.local_http = _DatabaseHealthClient(store, tg)  # type: ignore[assignment]

    log.info("standalone Telegram command worker started with SQLite scanner heartbeat")
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
        await tg.close()


if __name__ == "__main__":
    asyncio.run(main())