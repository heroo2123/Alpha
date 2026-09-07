from __future__ import annotations

import asyncio

import command_worker as worker
from polymarket_scanner.telegram import Telegram
from polymarket_scanner.trade_only import is_trade_ready, send_trade_now


async def _guarded_send_signal(self: Telegram, signal_id: int, signal) -> None:
    # Old WATCH/actionable rows may still exist in the persistent outbox from prior
    # builds. Treat them as delivered-without-notification unless they carry the
    # current post-confirmation TRADE NOW certification.
    if not is_trade_ready(signal):
        return
    await send_trade_now(self, signal_id, signal)


Telegram.send_signal = _guarded_send_signal


if __name__ == "__main__":
    asyncio.run(worker.main())
