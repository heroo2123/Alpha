from __future__ import annotations

"""Final V8 corrective wrapper: historical operator migration and network isolation."""

import argparse
import asyncio
import json
import os
from pathlib import Path

from .weather_only_live_paper import (
    DEFAULT_FORECAST_CACHE_SECONDS,
    DEFAULT_FORECAST_RAW_GAP_MIN,
    DEFAULT_INTERVAL_SECONDS,
    DEFAULT_MAX_FORECAST_EVENTS,
    _atomic_json,
)
from .weather_only_live_paper_all_signals_final_v7 import (
    FINAL_ALL_PAPER_RUNTIME_V7_VERSION,
    FinalAllPaperWeatherLiveServiceV7,
)
from .weather_only_live_paper_v2 import DEFAULT_PAPER_STAKE_USD
from .weather_only_operator_state_corrective import TERMINAL_VISIBLE_STATUSES


FINAL_ALL_PAPER_RUNTIME_V8_VERSION = (
    "final_all_paper_v8_historical_terminal_sync_network_env_isolated"
)
FORBIDDEN_NETWORK_ENVIRONMENT = (
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "ALL_PROXY",
    "NO_PROXY",
    "http_proxy",
    "https_proxy",
    "all_proxy",
    "no_proxy",
    "SSL_CERT_FILE",
    "SSL_CERT_DIR",
)


def assert_network_environment_isolated() -> None:
    present = [name for name in FORBIDDEN_NETWORK_ENVIRONMENT if name in os.environ]
    if present:
        raise RuntimeError(
            "ALL_PAPER_NETWORK_ENVIRONMENT_OVERRIDE_FORBIDDEN:" + ",".join(sorted(present))
        )


class FinalAllPaperWeatherLiveServiceV8(FinalAllPaperWeatherLiveServiceV7):
    def __init__(self, **kwargs) -> None:
        # Reject host/process proxy or alternate TLS trust configuration before any
        # inherited HTTP client is constructed.  Individual certified clients also
        # use trust_env=False so this is a second independent boundary.
        assert_network_environment_isolated()
        super().__init__(**kwargs)
        # Ignore the legacy migration cutoff and materialize sync records for every
        # historical Telegram-delivered terminal state before ONLINE can be emitted.
        self._historical_operator_sync_created = int(
            self.positions.ensure_operator_sync_records()
        )

    def _historical_operator_sync_missing(self) -> int:
        placeholders = ",".join("?" for _ in TERMINAL_VISIBLE_STATUSES)
        params = tuple(sorted(TERMINAL_VISIBLE_STATUSES))
        with self.positions._conn() as db:
            row = db.execute(
                "SELECT COUNT(*) AS n FROM weather_paper_signals s "
                "LEFT JOIN weather_paper_operator_sync o ON o.signal_id=s.id "
                "WHERE s.telegram_message_id IS NOT NULL "
                f"AND s.status IN ({placeholders}) AND o.signal_id IS NULL",
                params,
            ).fetchone()
        return int(row["n"] if row is not None else 0)

    async def send_startup(self) -> int:
        # Repeat at the last moment before operator synchronization so recovery or
        # constructor-side terminalization cannot create a below-cutoff orphan.
        self._historical_operator_sync_created += int(
            self.positions.ensure_operator_sync_records()
        )
        if self._historical_operator_sync_missing() != 0:
            raise RuntimeError("HISTORICAL_TERMINAL_OPERATOR_SYNC_INCOMPLETE")
        message_id = await super().send_startup()
        if self._historical_operator_sync_missing() != 0:
            raise RuntimeError("HISTORICAL_TERMINAL_OPERATOR_SYNC_INCOMPLETE")
        return int(message_id)

    async def run_cycle(self) -> dict:
        self._historical_operator_sync_created += int(
            self.positions.ensure_operator_sync_records()
        )
        status = dict(await super().run_cycle())
        missing = self._historical_operator_sync_missing()
        status.update(
            {
                "final_all_paper_runtime_v8_version": FINAL_ALL_PAPER_RUNTIME_V8_VERSION,
                "final_all_paper_runtime_v7_version": FINAL_ALL_PAPER_RUNTIME_V7_VERSION,
                "historical_terminal_operator_sync_backfill_required": True,
                "historical_terminal_operator_sync_created": int(
                    self._historical_operator_sync_created
                ),
                "historical_terminal_operator_sync_missing": int(missing),
                "historical_terminal_operator_sync_complete": missing == 0,
                "network_environment_isolated": True,
                "http_clients_ignore_environment": True,
                "financial_delivery": False,
                "financial_authority": False,
                "automatic_order_placement": False,
                "wallet_or_order_api_loaded": False,
            }
        )
        if missing != 0:
            status["cycle_ok"] = False
            status["operator_all_lanes_healthy"] = False
            errors = list(status.get("errors") or [])
            errors.append("HISTORICAL_TERMINAL_OPERATOR_SYNC_INCOMPLETE")
            status["errors"] = errors
        _atomic_json(self.status_path, status)
        return status


async def _main(args) -> int:
    service = FinalAllPaperWeatherLiveServiceV8(
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
