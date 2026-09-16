from __future__ import annotations

"""Weather LIVE PAPER v2: real alerts, automatic simulated fills and commands.

This wraps the reviewed human-readable paper signal generator without changing its
detector math. A Telegram-accepted signal is automatically entered into the isolated
paper ledger, settled from public Gamma exact-token payouts when the market closes,
and exposed through private-chat Telegram commands. No wallet/order/cancel API is
imported.
"""

import argparse
import asyncio
import html
import json
import time
from pathlib import Path

from .weather_only_live_paper import (
    DEFAULT_FORECAST_CACHE_SECONDS,
    DEFAULT_FORECAST_RAW_GAP_MIN,
    DEFAULT_INTERVAL_SECONDS,
    DEFAULT_MAX_FORECAST_EVENTS,
    _atomic_json,
)
from .weather_only_live_paper_human import HumanReadableWeatherLivePaperService
from .weather_only_paper_control import (
    WeatherPaperCommandController,
    WeatherPaperSettlementEngine,
)
from .weather_only_paper_positions import WeatherPaperPositionStore


WEATHER_LIVE_PAPER_V2_VERSION = "weather_live_paper_v2_auto_positions_commands_gamma_settlement"
DEFAULT_PAPER_STAKE_USD = 10.0


class WeatherLivePaperV2Service(HumanReadableWeatherLivePaperService):
    def __init__(self, *, paper_stake_usd: float = DEFAULT_PAPER_STAKE_USD, **kwargs) -> None:
        super().__init__(**kwargs)
        stake = float(paper_stake_usd)
        if not 0.10 <= stake <= 10_000.0:
            raise ValueError("paper_stake_usd must be in 0.10..10000")
        self.paper_stake_usd = stake
        self.positions = WeatherPaperPositionStore(self.db_path)
        self.settlement = WeatherPaperSettlementEngine(store=self.positions, telegram=self.telegram)
        self.commands = WeatherPaperCommandController(
            telegram=self.telegram,
            store=self.positions,
            status_path=self.status_path,
            paper_stake_usd=self.paper_stake_usd,
        )

    async def close(self) -> None:
        await asyncio.gather(
            self.commands.close(),
            self.settlement.close(),
            return_exceptions=True,
        )
        await super().close()

    async def send_startup(self) -> int:
        release = self.release_sha()
        text = "\n".join([
            "🟢 <b>WEATHER PAPER BOT ONLINE</b>",
            "",
            "Real Polymarket books + real weather/forecast data are live.",
            "Every Telegram signal is automatically tracked as a simulated position.",
            f"Target paper stake: <b>${self.paper_stake_usd:.2f} per signal</b>, capped by the exact visible top-of-book quantity captured with the alert.",
            "Closed markets are automatically scored from the exact token payout.",
            "",
            "Commands: <code>/status</code> <code>/stats</code> <code>/positions</code> <code>/history</code> <code>/recent</code> <code>/help</code>",
            "",
            "🚫 <b>NO REAL ORDERS</b> — no wallet/order/cancel authority exists in this service.",
            f"Release: <code>{html.escape(release[:12])}</code>",
        ])
        return await self.telegram.send_html(text)

    async def run_cycle(self) -> dict:
        status = await super().run_cycle()
        tracker_errors: list[str] = []
        try:
            created = await asyncio.to_thread(
                self.positions.ensure_sent_positions, self.paper_stake_usd
            )
        except Exception as exc:
            created = []
            tracker_errors.append(f"POSITION_BACKFILL:{type(exc).__name__}")

        try:
            settlement = await self.settlement.settle_once()
        except Exception as exc:
            settlement = {
                "open_checked": 0,
                "resolved_now": 0,
                "resolution_messages_sent": 0,
                "errors": [f"UNHANDLED:{type(exc).__name__}"],
            }
        tracker_errors.extend(str(value) for value in settlement.get("errors") or [])

        try:
            position_stats = await asyncio.to_thread(self.positions.stats)
        except Exception as exc:
            position_stats = {}
            tracker_errors.append(f"POSITION_STATS:{type(exc).__name__}")

        status = dict(status)
        status.update({
            "version": WEATHER_LIVE_PAPER_V2_VERSION,
            "paper_tracker_ok": not tracker_errors,
            "paper_target_stake_usd": self.paper_stake_usd,
            "paper_positions_created_this_cycle": len(created),
            "paper_position_stats": position_stats,
            "paper_settlement": settlement,
            "paper_command_last_poll_at": self.positions.get_state("telegram_command_last_poll_at", ""),
            "paper_command_last_error": self.positions.get_state("telegram_command_last_error", ""),
            "financial_delivery": False,
            "financial_authority": False,
            "automatic_order_placement": False,
            "wallet_or_order_api_loaded": False,
        })
        if tracker_errors:
            status["errors"] = list(status.get("errors") or []) + tracker_errors
            status["cycle_ok"] = False
        _atomic_json(self.status_path, status)
        return status

    async def loop(self) -> None:
        await self.send_startup()
        # Preserve the beginning of the live experiment: signals delivered by v1
        # before this release are converted to paper positions using their stored
        # Telegram receipt time and captured exact-book entry evidence.
        await asyncio.to_thread(self.positions.ensure_sent_positions, self.paper_stake_usd)
        command_task = asyncio.create_task(self.commands.loop())
        try:
            while True:
                started = time.monotonic()
                try:
                    await self.run_cycle()
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    status = {
                        "version": WEATHER_LIVE_PAPER_V2_VERSION,
                        "mode": "LIVE_PAPER_RESEARCH",
                        "release_sha": self.release_sha(),
                        "finished_at": time.time(),
                        "cycle_ok": False,
                        "errors": [f"UNHANDLED:{type(exc).__name__}"],
                        "paper_tracker_ok": False,
                        "financial_delivery": False,
                        "financial_authority": False,
                        "automatic_order_placement": False,
                        "wallet_or_order_api_loaded": False,
                    }
                    _atomic_json(self.status_path, status)
                elapsed = time.monotonic() - started
                await asyncio.sleep(max(0.0, self.interval_seconds - elapsed))
        finally:
            command_task.cancel()
            await asyncio.gather(command_task, return_exceptions=True)


async def _main(args) -> int:
    service = WeatherLivePaperV2Service(
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
            await asyncio.to_thread(service.positions.ensure_sent_positions, service.paper_stake_usd)
            status = await service.run_cycle()
            print(json.dumps(status, sort_keys=True, indent=2))
            return 0 if status.get("cycle_ok") else 2
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
