from __future__ import annotations

"""Canonical weather-only corrective LIVE PAPER entrypoint.

This thin layer unifies the v4 signal and position SQLite surfaces.  It exists so the
canonical service cannot accidentally use the legacy split store at a v4 save/send
boundary.  No authenticated trading API is imported.
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
)
from .weather_only_live_paper_v2 import DEFAULT_PAPER_STAKE_USD
from .weather_only_live_paper_v4 import WeatherLivePaperV4Service
from .weather_only_paper_corrective import (
    CorrectiveSettlementEngine,
    ClearWeatherPaperCommandController,
)
from .weather_only_paper_facade import CorrectiveWeatherPaperStore


CANONICAL_CORRECTIVE_VERSION = "weather_live_paper_corrective_v4_unified_store"


class WeatherLivePaperCorrectiveService(WeatherLivePaperV4Service):
    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        # V4 constructed its corrective components around the position-only legacy
        # shape. Replace them before any cycle can run with the unified facade.
        self._canonical_superseded_settlement = self.settlement
        self._canonical_superseded_commands = self.commands
        self.positions = CorrectiveWeatherPaperStore(self.db_path)
        self.settlement = CorrectiveSettlementEngine(
            store=self.positions,
            telegram=self.telegram,
        )
        self.commands = ClearWeatherPaperCommandController(
            telegram=self.telegram,
            store=self.positions,
            status_path=self.status_path,
            paper_stake_usd=self.paper_stake_usd,
        )

    async def close(self) -> None:
        await asyncio.gather(
            self._canonical_superseded_settlement.close(),
            self._canonical_superseded_commands.close(),
            return_exceptions=True,
        )
        await super().close()

    async def run_cycle(self) -> dict:
        status = dict(await super().run_cycle())
        status["canonical_corrective_version"] = CANONICAL_CORRECTIVE_VERSION
        return status


async def _main(args) -> int:
    service = WeatherLivePaperCorrectiveService(
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
    parser.add_argument(
        "--forecast-cache-seconds", type=float, default=DEFAULT_FORECAST_CACHE_SECONDS
    )
    parser.add_argument(
        "--forecast-raw-gap-min", type=float, default=DEFAULT_FORECAST_RAW_GAP_MIN
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
