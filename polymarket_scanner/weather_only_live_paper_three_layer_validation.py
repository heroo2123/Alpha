from __future__ import annotations

"""Guarded final PAPER runtime for validating the pure three-layer same-day system.

This branch deliberately contains no PWS integration.  It keeps the reviewed final
future-day PAPER engine unchanged and hardens only the silent same-day research lane:
Layer 1 exact WRH, Layer 2 NWS grid, and Layer 3 31-member hourly GEFS.

The wrapper rotates a bounded same-day event universe instead of permanently starving
events after the first four lexicographic IDs, bounds the complete source bundle, and
uses the guarded source transports.  The scientific population-alignment gate remains
FALSE, so same-day probabilities, Telegram delivery, validated P&L, and financial
authority remain disabled.
"""

import argparse
import asyncio
import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .weather_only_contract_strict import (
    StrictWeatherContractError,
    compile_strict_temperature_event,
)
from .weather_only_live_paper import (
    DEFAULT_FORECAST_CACHE_SECONDS,
    DEFAULT_FORECAST_RAW_GAP_MIN,
    DEFAULT_INTERVAL_SECONDS,
    DEFAULT_MAX_FORECAST_EVENTS,
    _atomic_json,
)
from .weather_only_live_paper_final import FinalWeatherLivePaperService
from .weather_only_live_paper_v2 import DEFAULT_PAPER_STAKE_USD
from .weather_only_rules import compile_temperature_rule_authority
from .weather_only_same_day_contract import build_same_day_contract_semantics
from .weather_only_three_layer_guarded import (
    GuardedNWSNearTermGridClient,
    GuardedNWSWRHLiveClient,
    GuardedOpenMeteoGEFSHourlyClient,
)


THREE_LAYER_VALIDATION_RUNTIME_VERSION = (
    "weather_three_layer_validation_v1_guarded_rotating_silent"
)
THREE_LAYER_SELECTION_POLICY = "PERSISTENT_ROUND_ROBIN_ELIGIBLE_V1"
THREE_LAYER_SELECTION_UNIVERSE_CAP = 12
THREE_LAYER_MAX_EVENTS_PER_CYCLE = 4
THREE_LAYER_SOURCE_BUNDLE_DEADLINE_SECONDS = 35.0
THREE_LAYER_CURSOR_KEY = "same_day_three_layer_rotation_cursor_v1"


def _rotate_after_cursor(rows: list[tuple], cursor: str, limit: int) -> list[tuple]:
    if not rows or limit <= 0:
        return []
    ordered = sorted(rows, key=lambda item: str(item[0]))
    if cursor:
        start = next(
            (index + 1 for index, item in enumerate(ordered) if str(item[0]) == cursor),
            0,
        )
    else:
        start = 0
    return [ordered[(start + offset) % len(ordered)] for offset in range(min(limit, len(ordered)))]


class ThreeLayerValidationWeatherLivePaperService(FinalWeatherLivePaperService):
    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)

        # Construct all replacements before mutating the inherited service so a
        # constructor failure cannot leave a half-swapped source set.
        guarded_wrh = GuardedNWSWRHLiveClient()
        guarded_nws = GuardedNWSNearTermGridClient()
        guarded_gefs = GuardedOpenMeteoGEFSHourlyClient()

        self._three_layer_superseded_nws = self._same_day_nws
        self._three_layer_superseded_gefs = self._same_day_gefs
        self._same_day_wrh = guarded_wrh
        self._same_day_nws = guarded_nws
        self._same_day_gefs = guarded_gefs
        self._three_layer_last_eligible_total = 0
        self._three_layer_last_selected_ids: tuple[str, ...] = ()
        self._three_layer_last_universe_truncated = False

    async def close(self) -> None:
        self._same_day_wrh.close()
        await asyncio.gather(
            self._three_layer_superseded_nws.close(),
            self._three_layer_superseded_gefs.close(),
            return_exceptions=True,
        )
        await super().close()

    async def _same_day_eligible(self, events: tuple[dict, ...]) -> tuple[list[tuple], list[str]]:
        eligible: list[tuple[str, dict, object, object, object]] = []
        errors: list[str] = []
        seen_event_ids: set[str] = set()

        for event in events:
            if not isinstance(event, dict):
                continue
            try:
                compiled = compile_strict_temperature_event(event)
                authority = compile_temperature_rule_authority(event, compiled)
                semantics = build_same_day_contract_semantics(compiled, authority)
            except StrictWeatherContractError:
                continue
            except Exception as exc:
                code = getattr(exc, "code", type(exc).__name__)
                event_id = str(event.get("id") or event.get("eventId") or "unknown")
                errors.append(f"SAME_DAY_SEMANTICS:{event_id}:{code}")
                continue

            event_id = str(compiled.event_id)
            if event_id in seen_event_ids:
                continue
            seen_event_ids.add(event_id)
            if not semantics.layer1_adapter_capable:
                continue
            try:
                metadata = await self._station_metadata_for_compiled(compiled)
                if metadata is None:
                    continue
                zone = ZoneInfo(str(metadata.timezone))
                local_today = datetime.now(tz=zone).date()
            except (ZoneInfoNotFoundError, AttributeError, ValueError) as exc:
                errors.append(f"SAME_DAY_STATION:{event_id}:{type(exc).__name__}")
                continue
            except Exception as exc:
                code = getattr(exc, "code", type(exc).__name__)
                errors.append(f"SAME_DAY_STATION:{event_id}:{code}")
                continue
            if compiled.target_date != local_today:
                continue
            eligible.append((event_id, event, compiled, semantics, metadata))

        eligible.sort(key=lambda item: item[0])
        self._three_layer_last_eligible_total = len(eligible)
        universe = eligible[:THREE_LAYER_SELECTION_UNIVERSE_CAP]
        self._three_layer_last_universe_truncated = len(eligible) > len(universe)
        cursor = self.positions.get_state(THREE_LAYER_CURSOR_KEY, "")
        selected = _rotate_after_cursor(universe, cursor, THREE_LAYER_MAX_EVENTS_PER_CYCLE)
        self._three_layer_last_selected_ids = tuple(str(item[0]) for item in selected)
        if selected:
            # Advance even when a selected source later fails. One repeatedly broken
            # station must not permanently starve every other same-day event.
            self.positions.set_state(THREE_LAYER_CURSOR_KEY, str(selected[-1][0]))
        return selected, errors

    async def _fetch_same_day_source_bundle(self, compiled, metadata):
        async with asyncio.timeout(THREE_LAYER_SOURCE_BUNDLE_DEADLINE_SECONDS):
            return await super()._fetch_same_day_source_bundle(compiled, metadata)

    async def _capture_same_day_research(self, events: tuple[dict, ...]) -> dict:
        result = dict(await super()._capture_same_day_research(events))
        result.update(
            {
                "version": "same_day_three_layer_silent_collection_v3_guarded_rotating",
                "selection_policy": THREE_LAYER_SELECTION_POLICY,
                "eligible_events_total": self._three_layer_last_eligible_total,
                "selected_event_ids": list(self._three_layer_last_selected_ids),
                "selection_universe_cap": THREE_LAYER_SELECTION_UNIVERSE_CAP,
                "selection_universe_truncated": self._three_layer_last_universe_truncated,
                "max_events_per_cycle": THREE_LAYER_MAX_EVENTS_PER_CYCLE,
                "source_bundle_deadline_seconds": THREE_LAYER_SOURCE_BUNDLE_DEADLINE_SECONDS,
                "population_alignment_certified": False,
                "calibrated_probability": False,
                "included_in_validated_pnl": False,
                "telegram_delivery": False,
                "financial_authority": False,
            }
        )
        return result

    async def run_cycle(self) -> dict:
        status = dict(await super().run_cycle())
        status.update(
            {
                "three_layer_validation_runtime_version": THREE_LAYER_VALIDATION_RUNTIME_VERSION,
                "pws_enabled": False,
                "population_alignment_certified": False,
                "same_day_delivery_enabled": False,
                "financial_delivery": False,
                "financial_authority": False,
                "automatic_order_placement": False,
            }
        )
        _atomic_json(self.status_path, status)
        return status


async def _main(args) -> int:
    service = ThreeLayerValidationWeatherLivePaperService(
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
