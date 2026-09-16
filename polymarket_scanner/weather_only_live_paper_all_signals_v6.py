from __future__ import annotations

"""All-weather PAPER runtime v6: crash-safe maker result notification claims.

V6 preserves V5's prospective public-WebSocket maker lifecycle and V4's serialized
SQLite access while closing the remaining informational Telegram crash window.
Settlement/P&L is durable first; each result notification is then atomically claimed as
SENDING before any external send.  A claimed notification is never automatically
retried after restart because remote acceptance may have happened before a crash.

This is intentionally at-most-once delivery for maker result messages.  A message may
be missed after a crash or definite transport rejection, but it cannot be duplicated
by automatic restart recovery.  Durable settlement history remains authoritative.

No real order, wallet, signing, cancellation, actual-fill or financial authority is
introduced.
"""

import argparse
import asyncio
import json
from pathlib import Path

from .weather_only_live_paper import (
    DEFAULT_FORECAST_CACHE_SECONDS,
    DEFAULT_FORECAST_RAW_GAP_MIN,
    DEFAULT_INTERVAL_SECONDS,
    DEFAULT_MAX_FORECAST_EVENTS,
    _atomic_json,
)
from .weather_only_live_paper_all_signals_v3 import (
    MAKER_SETTLEMENT_NOTIFY_SENT,
    MAKER_SETTLEMENT_NOTIFY_UNCERTAIN,
)
from .weather_only_live_paper_all_signals_v5 import AllPaperWeatherLiveV5Service
from .weather_only_live_paper_v2 import DEFAULT_PAPER_STAKE_USD
from .weather_only_maker_paper_accounting_v4 import (
    MAKER_PAPER_ACCOUNTING_V4_VERSION,
    MAKER_SETTLEMENT_NOTIFY_SENDING,
    MakerPaperAccountingStoreV4,
)


ALL_PAPER_V6_RUNTIME_VERSION = (
    "weather_all_paper_signals_v6_at_most_once_maker_result_notification"
)


class AllPaperWeatherLiveV6Service(AllPaperWeatherLiveV5Service):
    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        # V3 already performed restart cancellation before the later wrappers replace
        # the store facade.  Reopen the same WAL database through the atomic-claim
        # facade without applying restart mutation a second time.
        old_store = self.maker_store
        old_store.close()
        self.maker_store = MakerPaperAccountingStoreV4(self.db_path)

    def _pending_maker_settlement_notifications(self) -> list[tuple[str, dict]]:
        # The inherited settlement loop calls this immediately before Telegram send.
        # Claiming here creates the durable SENDING barrier first.  If the process dies
        # at any later point, restart will not automatically send the same result again.
        return self.maker_store.claim_pending_settlement_notifications(
            sent_event_type=MAKER_SETTLEMENT_NOTIFY_SENT,
            uncertain_event_type=MAKER_SETTLEMENT_NOTIFY_UNCERTAIN,
            limit=50,
        )

    async def run_cycle(self) -> dict:
        status = dict(await super().run_cycle())
        status.update(
            {
                "all_paper_v6_runtime_version": ALL_PAPER_V6_RUNTIME_VERSION,
                "maker_paper_accounting_version": MAKER_PAPER_ACCOUNTING_V4_VERSION,
                "maker_settlement_notification_claim_event": (
                    MAKER_SETTLEMENT_NOTIFY_SENDING
                ),
                "maker_settlement_notification_retry_policy": (
                    "AT_MOST_ONCE_AFTER_DURABLE_CLAIM"
                ),
                "maker_settlement_duplicate_after_restart_guard": True,
                "financial_delivery": False,
                "financial_authority": False,
                "automatic_order_placement": False,
                "wallet_or_order_api_loaded": False,
            }
        )
        _atomic_json(self.status_path, status)
        return status


async def _main(args) -> int:
    service = AllPaperWeatherLiveV6Service(
        db_path=args.db,
        status_path=args.status,
        release_file=args.release_file,
        interval_seconds=args.interval_seconds,
        forecast_cache_seconds=args.forecast_cache_seconds,
        forecast_raw_gap_min=args.forecast_raw_gap_min,
        max_forecast_events=args.max_forecast_events,
        paper_stake_usd=args.paper_stake_usd,
    )
    try:
        if args.once:
            await service.send_startup()
            status = await service.run_cycle()
            print(json.dumps(status, sort_keys=True, indent=2))
            return 0 if status.get("cycle_ok") and status.get("maker_healthy") else 2
        await service.loop()
        return 0
    finally:
        await service.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--status", type=Path, required=True)
    parser.add_argument("--release-file", type=Path, required=True)
    parser.add_argument("--interval-seconds", type=float, default=DEFAULT_INTERVAL_SECONDS)
    parser.add_argument(
        "--forecast-cache-seconds",
        type=float,
        default=DEFAULT_FORECAST_CACHE_SECONDS,
    )
    parser.add_argument(
        "--forecast-raw-gap-min",
        type=float,
        default=DEFAULT_FORECAST_RAW_GAP_MIN,
    )
    parser.add_argument(
        "--max-forecast-events", type=int, default=DEFAULT_MAX_FORECAST_EVENTS
    )
    parser.add_argument(
        "--paper-stake-usd", type=float, default=DEFAULT_PAPER_STAKE_USD
    )
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    raise SystemExit(asyncio.run(_main(args)))


if __name__ == "__main__":
    main()
