from __future__ import annotations

"""Final operator-state wrapper with source-shock retry-fingerprint alignment."""

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
from .weather_only_live_paper_all_signals_final_v5 import FinalAllPaperWeatherLiveServiceV5
from .weather_only_live_paper_v2 import DEFAULT_PAPER_STAKE_USD
from .weather_only_operator_state_corrective import OperatorStateCommandController
from .weather_only_operator_state_corrective_v2 import (
    OPERATOR_STATE_CORRECTIVE_V2_VERSION,
    OperatorStatePostReceiptStoreV2,
)
from .weather_only_paper_corrective import CorrectiveSettlementEngine


FINAL_ALL_PAPER_RUNTIME_V6_VERSION = (
    "weather_all_paper_final_v9_operator_sync_episode_retry_alignment"
)


class FinalAllPaperWeatherLiveServiceV6(FinalAllPaperWeatherLiveServiceV5):
    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self._v6_superseded_settlement = self.settlement
        self._v6_superseded_commands = self.commands
        self.positions = OperatorStatePostReceiptStoreV2(self.db_path)
        self._v6_recovery = self.positions.reconcile_v5_after_restart()
        self.settlement = CorrectiveSettlementEngine(
            store=self.positions, telegram=self.telegram
        )
        self.commands = OperatorStateCommandController(
            telegram=self.telegram,
            store=self.positions,
            maker_store=self.maker_store,
            status_path=self.status_path,
            paper_stake_usd=self.paper_stake_usd,
        )

    async def close(self) -> None:
        await asyncio.gather(
            self._v6_superseded_settlement.close(),
            self._v6_superseded_commands.close(),
            return_exceptions=True,
        )
        await super().close()

    async def run_cycle(self) -> dict:
        status = dict(await super().run_cycle())
        status.update(
            {
                "final_all_paper_runtime_v6_version": FINAL_ALL_PAPER_RUNTIME_V6_VERSION,
                "operator_state_corrective_v2_version": OPERATOR_STATE_CORRECTIVE_V2_VERSION,
                "source_shock_retry_guard_final_episode_identity": True,
                "financial_delivery": False,
                "financial_authority": False,
                "automatic_order_placement": False,
                "wallet_or_order_api_loaded": False,
            }
        )
        _atomic_json(self.status_path, status)
        return status


async def _main(args) -> int:
    service = FinalAllPaperWeatherLiveServiceV6(
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
            return 0 if status.get("operator_all_lanes_healthy") is True else 2
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
