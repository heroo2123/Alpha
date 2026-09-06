from __future__ import annotations

import asyncio
import logging
import os

from polymarket_scanner.config import settings
from polymarket_scanner.store import Store
from polymarket_scanner.telegram import Telegram

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(message)s",
)
log = logging.getLogger("polybot.command_worker")


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

    log.info("standalone Telegram command worker started")
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
