from __future__ import annotations

"""Final all-weather PAPER entrypoint.

This thin wrapper freezes the operator-facing identity above V8. Deployment gates can
therefore prove that the complete source-shock + post-receipt + maker-corrective stack
finished a cycle rather than accepting an inherited intermediate status snapshot.
"""

import argparse
import asyncio
import html
import json
from pathlib import Path

from .weather_only_live_paper import (
    DEFAULT_FORECAST_CACHE_SECONDS,
    DEFAULT_FORECAST_RAW_GAP_MIN,
    DEFAULT_INTERVAL_SECONDS,
    DEFAULT_MAX_FORECAST_EVENTS,
    _atomic_json,
)
from .weather_only_live_paper_all_signals_v8 import AllPaperWeatherLiveV8Service
from .weather_only_live_paper_v2 import DEFAULT_PAPER_STAKE_USD


FINAL_ALL_PAPER_RUNTIME_VERSION = (
    "weather_all_paper_final_v1_v8_post_receipt_source_shock_atomic_maker"
)


class FinalAllPaperWeatherLiveService(AllPaperWeatherLiveV8Service):
    async def send_startup(self) -> int:
        release = self.release_sha()
        return await self.telegram.send_html(
            "\n".join(
                [
                    "🟢 <b>WEATHER ALL-PAPER BOT ONLINE — FINAL V8 PROFILE</b>",
                    "",
                    "Future-day GEFS-gap PAPER alerts: <b>ON — uncalibrated</b>",
                    "Same-day late-lock PAPER alerts: <b>ON — uncalibrated</b>",
                    "Official-extreme source-shock PAPER alerts: <b>ON — revision-sensitive</b>",
                    "Structural underround PAPER alerts: <b>ON</b>",
                    "Prospective maker PAPER bids: <b>ON only with certified public-WS evidence</b>",
                    "Result-lag: <b>GATED — exact WRH cutoff state not proven</b>",
                    "",
                    "New taker/structural PAPER positions require a fresh exact-CLOB recheck after Telegram receipt.",
                    "Maker fills require causal public SELL-aggressor prints; book touch alone is never a fill.",
                    "🚫 <b>NO REAL ORDERS / NO WALLET / NO SIGNING AUTHORITY</b>",
                    f"Release: <code>{html.escape(release[:12])}</code>",
                ]
            )
        )

    async def run_cycle(self) -> dict:
        status = dict(await super().run_cycle())
        status.update(
            {
                "final_all_paper_runtime_version": FINAL_ALL_PAPER_RUNTIME_VERSION,
                "financial_delivery": False,
                "financial_authority": False,
                "automatic_order_placement": False,
                "wallet_or_order_api_loaded": False,
            }
        )
        _atomic_json(self.status_path, status)
        return status


async def _main(args) -> int:
    service = FinalAllPaperWeatherLiveService(
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
    parser.add_argument("--forecast-cache-seconds", type=float, default=DEFAULT_FORECAST_CACHE_SECONDS)
    parser.add_argument("--forecast-raw-gap-min", type=float, default=DEFAULT_FORECAST_RAW_GAP_MIN)
    parser.add_argument("--max-forecast-events", type=int, default=DEFAULT_MAX_FORECAST_EVENTS)
    parser.add_argument("--paper-stake-usd", type=float, default=DEFAULT_PAPER_STAKE_USD)
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    raise SystemExit(asyncio.run(_main(args)))


if __name__ == "__main__":
    main()
