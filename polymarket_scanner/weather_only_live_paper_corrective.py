from __future__ import annotations

"""Canonical weather-only corrective LIVE PAPER entrypoint.

This layer unifies the v4 signal/position SQLite surfaces and adds the month-scale
operational bounds required by the adversarial review: historical quarantine resumes
from a persisted policy-bound cursor instead of rescanning the whole ledger every
cycle, and station metadata uses a TTL/LRU bound instead of growing forever.

It also runs the three-layer same-day model as a *silent research collector* only:
exact WRH observations so far, staged official NWS near-term grid evidence, and full
31-member GEFS remaining-hours paths. Captures are stored in an isolated table and
cannot create Telegram alerts, paper positions, validated P&L or financial authority.
The current WRH-to-model observation-population alignment remains uncertified, so
same-day captures normally remain BLOCKED_RESEARCH rather than manufacturing a
probability from an unproven scientific assumption.

The older full-replay same-day envelope table is preserved separately for future
scientifically accepted decisions. No authenticated trading API is imported and
same-day directional delivery remains disabled.
"""

import argparse
import asyncio
import json
import time
from collections import OrderedDict
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .weather_only_contract_strict import (
    StrictWeatherContractError,
    compile_strict_temperature_event,
)
from .weather_only_forecast import EnsembleMappingPolicy
from .weather_only_gefs_hourly import OpenMeteoGEFSHourlyClient
from .weather_only_history_bounded import BoundedForecastHistoryQuarantine
from .weather_only_live_paper import (
    DEFAULT_FORECAST_CACHE_SECONDS,
    DEFAULT_FORECAST_RAW_GAP_MIN,
    DEFAULT_INTERVAL_SECONDS,
    DEFAULT_MAX_FORECAST_EVENTS,
    _atomic_json,
)
from .weather_only_live_paper_v2 import DEFAULT_PAPER_STAKE_USD
from .weather_only_live_paper_v4 import (
    PRE_V4_QUARANTINE_REASON,
    WeatherLivePaperV4Service,
)
from .weather_only_nws_near_term import NWSNearTermGridClient
from .weather_only_paper_corrective import (
    PAPER_EXECUTION_PROTOCOL_V4,
    CorrectiveSettlementEngine,
    ClearWeatherPaperCommandController,
)
from .weather_only_paper_facade import CorrectiveWeatherPaperStore
from .weather_only_rules import compile_temperature_rule_authority
from .weather_only_same_day_capture import (
    SAME_DAY_CAPTURE_BLOCKED,
    SAME_DAY_CAPTURE_READY,
    assemble_same_day_capture,
)
from .weather_only_same_day_capture_store import SameDayCaptureStore
from .weather_only_same_day_contract import build_same_day_contract_semantics
from .weather_only_same_day_store import SameDayResearchStore
from .weather_only_wrh_client import NWSWRHLiveClient


CANONICAL_CORRECTIVE_VERSION = "weather_live_paper_corrective_v7_silent_three_layer_capture"
HISTORY_QUARANTINE_POLICY_ID = "PRE_V4_PROTOCOL_QUARANTINE_V2_BOUNDED_CURSOR"
HISTORY_QUARANTINE_BATCH_SIZE = 200
STATION_METADATA_CACHE_MAX_ENTRIES = 128
STATION_METADATA_CACHE_TTL_SECONDS = 21_600.0
DEFAULT_MAX_SAME_DAY_CAPTURE_EVENTS = 4
SAME_DAY_CAPTURE_COOLDOWN_SECONDS = 900.0
SAME_DAY_MAPPING_POLICY = EnsembleMappingPolicy(
    policy_id="SAME_DAY_THREE_LAYER_NEAREST_WHOLE_V1",
    include_control=True,
)


class _CapturingDiscoveryProxy:
    """Observe the discovery call already made by the inherited runtime.

    The base paper service already performs discovery. Re-running Gamma discovery for
    the same-day collector would waste network/CPU on e2-micro and could expose a
    different universe generation within one logical cycle. This transparent proxy
    records the exact latest discovery result while delegating every other attribute.
    """

    def __init__(self, delegate) -> None:
        self._delegate = delegate
        self.last_result = None

    async def discover(self, *args, **kwargs):
        result = await self._delegate.discover(*args, **kwargs)
        self.last_result = result
        return result

    def __getattr__(self, name: str):
        return getattr(self._delegate, name)


class WeatherLivePaperCorrectiveService(WeatherLivePaperV4Service):
    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        # V4 constructed its corrective components around the position-only legacy
        # shape. Replace them before any cycle can run with the unified facade.
        self._canonical_superseded_settlement = self.settlement
        self._canonical_superseded_commands = self.commands
        self.positions = CorrectiveWeatherPaperStore(self.db_path)
        self.same_day_research = SameDayResearchStore(self.db_path)
        self.same_day_captures = SameDayCaptureStore(self.db_path)
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

        # Reuse the exact discovery generation already consumed by the inherited
        # paper cycle instead of launching another universe walk.
        self._canonical_discovery_delegate = self.runtime.discovery
        self._capturing_discovery = _CapturingDiscoveryProxy(self.runtime.discovery)
        self.runtime.discovery = self._capturing_discovery

        # Three-layer acquisition is read-only and isolated from validated paper P&L.
        # WRH uses a synchronous secret-safe browser adapter in a worker thread.
        self._same_day_wrh = NWSWRHLiveClient()
        self._same_day_nws = NWSNearTermGridClient()
        self._same_day_gefs = OpenMeteoGEFSHourlyClient()
        self._same_day_last_attempt: dict[str, float] = {}

    async def close(self) -> None:
        await asyncio.gather(
            self._canonical_superseded_settlement.close(),
            self._canonical_superseded_commands.close(),
            self._same_day_nws.close(),
            self._same_day_gefs.close(),
            return_exceptions=True,
        )
        await super().close()

    def _station_cache_now(self) -> float:
        """Dedicated monotonic clock seam so tests never patch asyncio's global clock."""
        return time.monotonic()

    async def _station_metadata_for_compiled(self, compiled):
        """TTL/LRU station metadata cache used by every canonical forecast gate."""
        station_id = str(compiled.station_hint or "").strip().upper()
        if not station_id:
            return None
        now = self._station_cache_now()
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

    async def _same_day_eligible(self, events: tuple[dict, ...]) -> tuple[list[tuple], list[str]]:
        """Filter exact station-local target-day events before applying the event cap."""
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

        # R18: cap only after semantic + station-local date eligibility is proven.
        eligible.sort(key=lambda item: item[0])
        return eligible[:DEFAULT_MAX_SAME_DAY_CAPTURE_EVENTS], errors

    async def _fetch_same_day_source_bundle(self, compiled, metadata):
        station = str(compiled.station_hint).upper()
        latitude = float(metadata.latitude)
        longitude = float(metadata.longitude)
        target_date = compiled.target_date
        timezone_name = str(metadata.timezone)
        wrh_task = asyncio.to_thread(
            self._same_day_wrh.fetch_snapshot,
            station=station,
            target_date=target_date,
        )
        nws_task = self._same_day_nws.fetch_snapshot(
            station=station,
            latitude=latitude,
            longitude=longitude,
        )
        gefs_task = self._same_day_gefs.target_day(
            station=station,
            latitude=latitude,
            longitude=longitude,
            target_date=target_date,
            unit=str(compiled.unit),
            timezone=timezone_name,
        )
        return await asyncio.gather(wrh_task, nws_task, gefs_task)

    async def _capture_same_day_research(self, events: tuple[dict, ...]) -> dict:
        """Collect all three layers silently; never create a signal or paper position."""
        eligible, errors = await self._same_day_eligible(events)
        attempted = 0
        saved = 0
        blocked = 0
        ready = 0
        duplicates = 0
        block_reasons: dict[str, int] = {}
        now = time.time()

        # High/low contracts at the same station/date/unit can reuse the identical raw
        # source bundle within one cycle. Family-specific projection stays per event.
        bundle_cache: dict[tuple, tuple] = {}
        for event_id, _event, compiled, semantics, metadata in eligible:
            last = float(self._same_day_last_attempt.get(event_id, 0.0))
            if last > 0.0 and now - last < SAME_DAY_CAPTURE_COOLDOWN_SECONDS:
                continue
            self._same_day_last_attempt[event_id] = now
            attempted += 1
            key = (
                str(compiled.station_hint).upper(),
                compiled.target_date.isoformat(),
                str(compiled.unit),
                str(metadata.timezone),
                round(float(metadata.latitude), 6),
                round(float(metadata.longitude), 6),
            )
            try:
                if key not in bundle_cache:
                    bundle_cache[key] = await self._fetch_same_day_source_bundle(compiled, metadata)
                wrh_result, nws_snapshot, hourly_gefs = bundle_cache[key]
                # Freeze t only *after* every awaited source response exists. This is
                # the anti-lookahead boundary for O(t), Layer 2 and Layer 3.
                as_of = time.time()
                record = assemble_same_day_capture(
                    compiled=compiled,
                    contract_semantics=semantics,
                    station_metadata=metadata,
                    wrh_snapshot=wrh_result.snapshot,
                    near_term_raw_snapshot=nws_snapshot,
                    hourly_gefs=hourly_gefs,
                    as_of=as_of,
                    mapping_policy=SAME_DAY_MAPPING_POLICY,
                    population_alignment_certified=False,
                )
                row_id = await asyncio.to_thread(self.same_day_captures.save, record)
            except Exception as exc:
                code = getattr(exc, "code", type(exc).__name__)
                errors.append(f"SAME_DAY_CAPTURE:{event_id}:{code}")
                continue

            if row_id is None:
                duplicates += 1
            else:
                saved += 1
            if record.status == SAME_DAY_CAPTURE_BLOCKED:
                blocked += 1
            elif record.status == SAME_DAY_CAPTURE_READY:
                ready += 1
            for reason in record.block_reasons:
                block_reasons[reason] = block_reasons.get(reason, 0) + 1

        summary = await asyncio.to_thread(self.same_day_captures.summary)
        return {
            "version": "same_day_three_layer_silent_collection_v1",
            "enabled": True,
            "silent_research_only": True,
            "eligible_events": len(eligible),
            "attempted_now": attempted,
            "saved_now": saved,
            "duplicates_now": duplicates,
            "blocked_now": blocked,
            "ready_uncalibrated_now": ready,
            "block_reasons": block_reasons,
            "errors": errors,
            "store": summary,
            "capture_cooldown_seconds": SAME_DAY_CAPTURE_COOLDOWN_SECONDS,
            "max_events_per_cycle": DEFAULT_MAX_SAME_DAY_CAPTURE_EVENTS,
            "population_alignment_certified": False,
            "calibrated_probability": False,
            "included_in_validated_pnl": False,
            "telegram_delivery": False,
            "financial_authority": False,
        }

    async def run_cycle(self) -> dict:
        status = dict(await super().run_cycle())
        discovery = self._capturing_discovery.last_result
        events = tuple(getattr(discovery, "events", ()) or ())
        try:
            same_day_capture = await self._capture_same_day_research(events)
        except Exception as exc:
            code = getattr(exc, "code", type(exc).__name__)
            same_day_capture = {
                "version": "same_day_three_layer_silent_collection_v1",
                "enabled": True,
                "silent_research_only": True,
                "errors": [f"SAME_DAY_COLLECTOR:{code}"],
                "population_alignment_certified": False,
                "calibrated_probability": False,
                "included_in_validated_pnl": False,
                "telegram_delivery": False,
                "financial_authority": False,
            }
        replay_summary = await asyncio.to_thread(self.same_day_research.summary)
        status.update({
            "canonical_corrective_version": CANONICAL_CORRECTIVE_VERSION,
            "history_quarantine_policy_id": HISTORY_QUARANTINE_POLICY_ID,
            "history_quarantine_batch_size": HISTORY_QUARANTINE_BATCH_SIZE,
            "station_metadata_cache_entries": len(self._bounded_station_metadata),
            "station_metadata_cache_max_entries": STATION_METADATA_CACHE_MAX_ENTRIES,
            "station_metadata_cache_ttl_seconds": STATION_METADATA_CACHE_TTL_SECONDS,
            "same_day_three_layer": same_day_capture,
            "same_day_research": replay_summary,
            "same_day_delivery_enabled": False,
        })
        # The inherited cycle writes status before this silent collector runs. Rewrite
        # atomically so operator status describes the complete canonical cycle.
        _atomic_json(self.status_path, status)
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
