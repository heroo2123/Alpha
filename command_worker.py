from __future__ import annotations

import asyncio
import json
import logging
import os
import time

from polymarket_scanner.config import settings
from polymarket_scanner.outbox import TelegramOutbox
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
            data = {
                "ok": False, "started": None, "last_scan": None, "markets": 0, "tokens": 0,
                "stations": 0, "weather_ready_stations": 0, "weather_refreshing": False,
                "market_ws_workers": 0, "sports_ws": False, "crypto_rtds": False,
                "telegram_alert_queue": 0, "telegram_watch_dropped": 0,
                "scan_in_progress": False, "last_compute_seconds": None,
                "last_error": "scanner heartbeat is warming up", "universe_error": None,
                "telegram_alert_error": None, "sports_ws_error": None, "snapshot_at": None,
            }
        else:
            try:
                decoded = json.loads(raw)
                data = dict(decoded) if isinstance(decoded, dict) else {}
            except Exception:
                data = {}
            if not data:
                data = {
                    "ok": False, "started": None, "last_scan": None, "markets": 0, "tokens": 0,
                    "stations": 0, "weather_ready_stations": 0, "weather_refreshing": False,
                    "market_ws_workers": 0, "sports_ws": False, "crypto_rtds": False,
                    "telegram_alert_queue": 0, "telegram_watch_dropped": 0,
                    "scan_in_progress": False, "last_compute_seconds": None,
                    "last_error": "scanner heartbeat record is invalid", "universe_error": None,
                    "telegram_alert_error": None, "sports_ws_error": None, "snapshot_at": None,
                }

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


async def command_loop(tg: Telegram) -> None:
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


async def alert_delivery_loop(store: Store, tg: Telegram, outbox: TelegramOutbox) -> None:
    """Drain scanner alerts through the same process that reliably handles commands."""
    last_sent = 0.0
    while True:
        try:
            item = await asyncio.to_thread(outbox.next_due)
            if not item:
                await asyncio.sleep(0.20)
                continue

            signal_row = await asyncio.to_thread(store.get_signal, int(item["signal_id"]))
            if not signal_row:
                await asyncio.to_thread(outbox.mark_failed, int(item["id"]), "signal row missing")
                await asyncio.sleep(0.25)
                continue

            signal = outbox.signal_from_row(signal_row)
            interval = (
                settings.telegram_actionable_min_interval_seconds
                if signal.confidence == "ACTIONABLE"
                else settings.telegram_watch_min_interval_seconds
            )
            wait_for = interval - (time.monotonic() - last_sent)
            if wait_for > 0:
                await asyncio.sleep(wait_for)

            try:
                await tg.send_signal(int(item["signal_id"]), signal)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                await asyncio.to_thread(outbox.mark_failed, int(item["id"]), repr(exc))
                await asyncio.to_thread(store.set_state, "telegram_outbox_error", repr(exc))
                log.warning("persistent Telegram alert %s failed: %r", item["signal_id"], exc)
                await asyncio.sleep(0.25)
                continue

            await asyncio.to_thread(outbox.mark_sent, int(item["id"]))
            await asyncio.to_thread(store.set_state, "telegram_outbox_error", "")
            last_sent = time.monotonic()
            log.info("delivered persistent Telegram alert %s", item["signal_id"])
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.exception("Telegram outbox delivery loop failed: %r", exc)
            await asyncio.sleep(1.0)


async def main() -> None:
    """Run Telegram commands and scanner-alert delivery outside the scanner process."""
    store = Store(settings.db_path)
    outbox = TelegramOutbox(settings.db_path)
    tg = Telegram(store, alert_delivery_owner=True)
    if not tg.token_enabled:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is not configured")

    await tg.local_http.aclose()
    tg.local_http = _DatabaseHealthClient(store, tg)  # type: ignore[assignment]

    log.info("standalone Telegram worker started with SQLite scanner heartbeat + persistent alert outbox")
    command_task = asyncio.create_task(command_loop(tg))
    alert_task = asyncio.create_task(alert_delivery_loop(store, tg, outbox))
    try:
        await asyncio.gather(command_task, alert_task)
    finally:
        command_task.cancel()
        alert_task.cancel()
        await asyncio.gather(command_task, alert_task, return_exceptions=True)
        await tg.close()


if __name__ == "__main__":
    asyncio.run(main())
