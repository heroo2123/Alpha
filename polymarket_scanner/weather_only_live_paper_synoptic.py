from __future__ import annotations

"""Final guarded weather PAPER runtime with silent Synoptic/CWOP PWS diagnostics.

The inherited future-day PAPER engine is unchanged. This wrapper swaps only the
optional same-day PWS diagnostic provider after the guarded final service has been
constructed. PWS remains downstream of official WRH+NWS+GEFS capture and has no
settlement, probability, delivery, validated-P&L, order-placement, or financial
authority.
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
from .weather_only_live_paper_final import FinalWeatherLivePaperService
from .weather_only_live_paper_v2 import DEFAULT_PAPER_STAKE_USD
from .weather_only_synoptic_pws import SYNOPTIC_CWOP_NETWORK_ID, SynopticCWOPPWSClient


SYNOPTIC_PWS_RUNTIME_VERSION = "weather_live_paper_synoptic_cwop_v1_silent_diagnostic"


class SynopticFinalWeatherLivePaperService(FinalWeatherLivePaperService):
    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        # The inherited corrective constructor creates the previously reviewed
        # Weather Company client but performs no network I/O. Replace it immediately
        # with the Synoptic/CWOP provider and keep the superseded client solely so its
        # owned HTTP pool can be closed deterministically at shutdown.
        old_pws = self._same_day_pws
        try:
            self._same_day_pws = SynopticCWOPPWSClient()
        except BaseException:
            self._same_day_pws = old_pws
            raise
        self._superseded_weather_company_pws = old_pws

    async def close(self) -> None:
        old = getattr(self, "_superseded_weather_company_pws", None)
        if old is not None:
            try:
                await old.close()
            except Exception:
                pass
        await super().close()

    async def run_cycle(self) -> dict:
        status = dict(await super().run_cycle())
        status.update(
            {
                "synoptic_pws_runtime_version": SYNOPTIC_PWS_RUNTIME_VERSION,
                "pws_provider": "SYNOPTIC_CWOP",
                "pws_network_id": SYNOPTIC_CWOP_NETWORK_ID,
                "pws_configured": bool(self._same_day_pws.token),
                "pws_predictive_only": True,
                "pws_may_replace_official_observation": False,
                "pws_may_reweight_probability": False,
                "same_day_delivery_enabled": False,
                "financial_authority": False,
                "automatic_order_placement": False,
            }
        )
        _atomic_json(self.status_path, status)
        return status


async def _main(args) -> int:
    service = SynopticFinalWeatherLivePaperService(
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
    parser.add_argument("--max-forecast-events", type=int, default=DEFAULT_MAX_FORECAST_EVENTS)
    parser.add_argument("--paper-stake-usd", type=float, default=DEFAULT_PAPER_STAKE_USD)
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    raise SystemExit(asyncio.run(_main(args)))


if __name__ == "__main__":
    main()
