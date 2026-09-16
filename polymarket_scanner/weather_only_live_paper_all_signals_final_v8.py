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
    "final_all_paper_v8_historical_terminal_sync_network_env_global_recall"
)
FORBIDDEN_NETWORK_ENVIRONMENT = (
    "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY",
    "http_proxy", "https_proxy", "all_proxy", "no_proxy",
    "SSL_CERT_FILE", "SSL_CERT_DIR",
    "PYTHONPATH", "PYTHONHOME", "PYTHONUSERBASE", "PYTHONSTARTUP",
    "PYTHONINSPECT", "PYTHONWARNINGS", "PYTHONBREAKPOINT",
    "LD_PRELOAD", "LD_LIBRARY_PATH",
)


def assert_network_environment_isolated() -> None:
    present = [name for name in FORBIDDEN_NETWORK_ENVIRONMENT if name in os.environ]
    if present:
        raise RuntimeError(
            "ALL_PAPER_NETWORK_ENVIRONMENT_OVERRIDE_FORBIDDEN:" + ",".join(sorted(present))
        )
    if os.environ.get("PYTHONNOUSERSITE") != "1":
        raise RuntimeError("ALL_PAPER_PYTHONNOUSERSITE_NOT_ASSERTED")


class FinalAllPaperWeatherLiveServiceV8(FinalAllPaperWeatherLiveServiceV7):
    def __init__(self, **kwargs) -> None:
        # This happens before inherited clients are constructed, so default HTTPX
        # trust_env behavior has no proxy/CA environment to consume.  Discovery also
        # uses trust_env=False and the unit/attester independently enforce absence.
        assert_network_environment_isolated()
        super().__init__(**kwargs)
        self._historical_operator_sync_created = int(self.positions.ensure_operator_sync_records())

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

    def _global_recall_status(self) -> dict:
        discovery = getattr(self.runtime, "discovery", None)
        method = getattr(discovery, "global_recall_status", None)
        if not callable(method):
            return {"complete": False, "cache_hit": False, "pages": 0, "scanned_events": 0, "retained_events": 0}
        value = method()
        return dict(value) if isinstance(value, dict) else {"complete": False}

    async def send_startup(self) -> int:
        self._historical_operator_sync_created += int(self.positions.ensure_operator_sync_records())
        if self._historical_operator_sync_missing() != 0:
            raise RuntimeError("HISTORICAL_TERMINAL_OPERATOR_SYNC_INCOMPLETE")
        message_id = await super().send_startup()
        if self._historical_operator_sync_missing() != 0:
            raise RuntimeError("HISTORICAL_TERMINAL_OPERATOR_SYNC_INCOMPLETE")
        return int(message_id)

    async def run_cycle(self) -> dict:
        self._historical_operator_sync_created += int(self.positions.ensure_operator_sync_records())
        status = dict(await super().run_cycle())
        missing = self._historical_operator_sync_missing()
        recall = self._global_recall_status()
        recall_complete = recall.get("complete") is True
        status.update(
            {
                "final_all_paper_runtime_v8_version": FINAL_ALL_PAPER_RUNTIME_V8_VERSION,
                "final_all_paper_runtime_v7_version": FINAL_ALL_PAPER_RUNTIME_V7_VERSION,
                "historical_terminal_operator_sync_backfill_required": True,
                "historical_terminal_operator_sync_created": int(self._historical_operator_sync_created),
                "historical_terminal_operator_sync_missing": int(missing),
                "historical_terminal_operator_sync_complete": missing == 0,
                "network_environment_isolated": True,
                "network_environment_absent_before_http_client_construction": True,
                "global_weather_recall_required": True,
                "global_weather_recall_complete": bool(recall_complete),
                "global_weather_recall": recall,
                "financial_delivery": False,
                "financial_authority": False,
                "automatic_order_placement": False,
                "wallet_or_order_api_loaded": False,
            }
        )
        if missing != 0 or not recall_complete:
            status["cycle_ok"] = False
            status["operator_all_lanes_healthy"] = False
            errors = list(status.get("errors") or [])
            if missing != 0:
                errors.append("HISTORICAL_TERMINAL_OPERATOR_SYNC_INCOMPLETE")
            if not recall_complete:
                errors.append("GLOBAL_WEATHER_RECALL_INCOMPLETE")
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
    parser.add_argument("--forecast-cache-seconds", type=float, default=DEFAULT_FORECAST_CACHE_SECONDS)
    parser.add_argument("--forecast-raw-gap-min", type=float, default=DEFAULT_FORECAST_RAW_GAP_MIN)
    parser.add_argument("--max-forecast-events", type=int, default=DEFAULT_MAX_FORECAST_EVENTS)
    parser.add_argument("--paper-stake-usd", type=float, default=DEFAULT_PAPER_STAKE_USD)
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    raise SystemExit(asyncio.run(_main(args)))


if __name__ == "__main__":
    main()
