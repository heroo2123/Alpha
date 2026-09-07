from __future__ import annotations

import asyncio

import command_worker as worker
from polymarket_scanner.telegram import Telegram
from polymarket_scanner.trade_only import is_trade_ready, send_trade_now


async def _guarded_send_signal(self: Telegram, signal_id: int, signal) -> None:
    # Old WATCH/actionable rows may still exist in the persistent outbox from prior
    # builds. During P0 containment no detector is promoted. Mark such rows as
    # SUPPRESSED through the command worker instead of pretending they were SENT.
    if not is_trade_ready(signal):
        raise worker.AlertSuppressed("not TRADE NOW eligible under current P0 containment policy")
    await send_trade_now(self, signal_id, signal)


Telegram.send_signal = _guarded_send_signal


if __name__ == "__main__":
    asyncio.run(worker.main())
