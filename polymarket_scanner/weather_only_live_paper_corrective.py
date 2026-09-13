from __future__ import annotations

"""Canonical weather-only corrective LIVE PAPER entrypoint.

This layer unifies the v4 signal/position SQLite surfaces and adds the month-scale
operational bounds required by the adversarial review: historical quarantine resumes
from a persisted policy-bound cursor instead of rescanning the whole ledger every
cycle, and station metadata uses a TTL/LRU bound instead of growing forever.

No authenticated trading API is imported. Same-day directional delivery remains
disabled in the inherited v4 runtime.
"""

import argparse
import asyncio
import json
import time
from collections import OrderedDict
from pathlib import Path

from .weather_only_history_bounded import BoundedForecastHistoryQuarantine
from .weather_only_live_paper import (
    DEFAULT_FORECAST_CACHE_SECONDS,
    DEFAULT_FORECAST_RAW_GAP_MIN,
    DEFAULT_INTERVAL_SECONDS,
    DEFAULT_MAX_FORECAST_EVENTS,
)
from .weather_only_live_paper_v2 import DEFAULT_PAPER_STAKE_USD
from .weather_only_live_paper_v4 import (
    PRE_V4_QUARANTINE_REASON,
    WeatherLivePaperV4Service,
)
from .weather_only_paper_corrective import (
    PAPER_EXECUTION_PROTOCOL_V4,
    CorrectiveSettlementEngine,
    ClearWeatherPaperCommandController,
)
from .weather_only_paper_facade import CorrectiveWeatherPaperStore


CANONICAL_CORRECTIVE_VERSION = "weather_live_paper_corrective_v5_bounded_history_cache"
HISTORY_QUARANTINE_POLICY_ID = "PRE_V4_PROTOCOL_QUARANTINE_V2_BOUNDED_CURSOR"
HISTORY_QUARANTINE_BATCH_SIZE = 200
STATION_METADATA_CACHE_MAX_ENTRIES = 128
STATION_METADATA_CACHE_TTL_SECONDS = 21_600.0


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
        self._history_quarantine = BoundedForecastHistoryQuarantine(
            self.positions,
            policy_id=HISTORY_QUARANTINE_POLICY_ID,
            current_execution_protocol=PAPER_EXECUTION_PROTOCOL_V4,
            quarantine_reason=PRE_V4_QUARANTINE_REASON,
            batch_size=HISTORY_QUARANTINE_BATCH_SIZE,
        )
        self._bounded_station_metadata: OrderedDict[str, tuple[float, object]] = OrderedDict()

    async def close(self) -> None:
        await asyncio.gather(
            self._canonical_superseded_settlement.close(),
            self._canonical_superseded_commands.close(),
            return_exceptions=True,
        )
        await super().close()

    async def _station_metadata_for_compiled(self, compiled):
        """TTL/LRU station metadata cache used by every canonical forecast gate."""
        station_id = str(compiled.station_hint or "").strip().upper()
        if not station_id:
            return None
        now = time.monotonic()
        cached = self._bounded_station_metadata.get(station_id)
        if cached is not None:
            age = now - float(cached[0])
            if 0.0 <= age <= STATION_METADATA_CACHE_TTL_SECONDS:
                self._bounded_station_metadata.move_to_end(station_id)
                return cached[1]
            self._bounded_station_metadata.pop(station_id, None)

        metadata = await self.station_client.station(station_id)
        self._bounded_station_metadata[station_id] = (now, metadata)
        self._bounded_station_metadata.move_to_end(station_id)
        while len(self._bounded_station_metadata) > STATION_METADATA_CACHE_MAX_ENTRIES:
            self._bounded_station_metadata.popitem(last=False)
        return metadata

    async def quarantine_observation_blind_history(self) -> dict:
        """Perform at most one fixed historical batch and persist its progress."""
        try:
            report = await asyncio.to_thread(self._history_quarantine.run_once)
            payload = report.as_dict()
        except Exception as exc:
            code = getattr(exc, "code", type(exc).__name__)
            payload = {
                "policy_id": HISTORY_QUARANTINE_POLICY_ID,
                "cursor_before": None,
                "cursor_after": None,
                "scanned_now": 0,
                "quarantined_now": 0,
                "scan_complete_at_call_end": False,
                "errors": [f"HISTORY_QUARANTINE:{code}"],
                "bounded_work": True,
                "financial_authority": False,
            }
        quarantined = int(payload.get("quarantined_now") or 0)
        errors = [str(value) for value in payload.get("errors") or []]
        self._forecast_history_quarantined_total += quarantined
        self._quarantine_errors = errors[-20:]
        return payload

    async def run_cycle(self) -> dict:
        status = dict(await super().run_cycle())
        status.update({
            "canonical_corrective_version": CANONICAL_CORRECTIVE_VERSION,
            "history_quarantine_policy_id": HISTORY_QUARANTINE_POLICY_ID,
            "history_quarantine_batch_size": HISTORY_QUARANTINE_BATCH_SIZE,
            "station_metadata_cache_entries": len(self._bounded_station_metadata),
            "station_metadata_cache_max_entries": STATION_METADATA_CACHE_MAX_ENTRIES,
            "station_metadata_cache_ttl_seconds": STATION_METADATA_CACHE_TTL_SECONDS,
        })
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
