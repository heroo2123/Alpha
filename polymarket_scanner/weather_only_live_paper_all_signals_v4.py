from __future__ import annotations

"""All-weather PAPER runtime v4: serialized maker notification persistence.

V4 is an additive corrective wrapper over V3.  It preserves the reviewed forecast,
same-day, structural and prospective maker behavior while removing the last direct
runtime access to the maker SQLite connection.  Settlement-notification discovery now
runs only through the accounting store's RLock-protected API.

No real-money order, wallet, signing, cancellation or actual-fill authority exists in
this runtime.
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
    AllPaperWeatherLiveV3Service,
)
from .weather_only_live_paper_v2 import DEFAULT_PAPER_STAKE_USD
from .weather_only_maker_paper_accounting import MAKER_SETTLEMENT_EVENT
from .weather_only_maker_paper_accounting_v3 import (
    MAKER_PAPER_ACCOUNTING_V3_VERSION,
    MakerPaperAccountingStoreV3,
)


ALL_PAPER_V4_RUNTIME_VERSION = (
    "weather_all_paper_signals_v4_serialized_maker_notification_query"
)


class AllPaperWeatherLiveV4Service(AllPaperWeatherLiveV3Service):
    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        # V3 already applied the one-time restart coverage cancellation before any
        # live work can begin.  Reopen the same WAL database through the stricter
        # facade; do not apply the restart mutation twice.
        old_store = self.maker_store
        old_store.close()
        self.maker_store = MakerPaperAccountingStoreV3(self.db_path)

    def _pending_maker_settlement_notifications(self) -> list[tuple[str, dict]]:
        return self.maker_store.pending_notification_payloads(
            source_event_type=MAKER_SETTLEMENT_EVENT,
            terminal_event_types=(
                MAKER_SETTLEMENT_NOTIFY_SENT,
                MAKER_SETTLEMENT_NOTIFY_UNCERTAIN,
            ),
            limit=50,
        )

    async def run_cycle(self) -> dict:
        status = dict(await super().run_cycle())
        status.update(
            {
                "all_paper_v4_runtime_version": ALL_PAPER_V4_RUNTIME_VERSION,
                "maker_paper_accounting_version": MAKER_PAPER_ACCOUNTING_V3_VERSION,
                "maker_settlement_notification_query_serialized": True,
                "financial_delivery": False,
                "financial_authority": False,
                "automatic_order_placement": False,
                "wallet_or_order_api_loaded": False,
            }
        )
        _atomic_json(self.status_path, status)
        return status


async def _main(args) -> int:
    service = AllPaperWeatherLiveV4Service(
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
