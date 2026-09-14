from __future__ import annotations

"""Guarded final PAPER runtime for validating the pure three-layer same-day system.

This branch deliberately contains no PWS integration. It keeps the reviewed final
future-day PAPER engine unchanged and hardens only the silent same-day research lane:
Layer 1 exact WRH, Layer 2 NWS grid, and Layer 3 31-member hourly GEFS.

The wrapper rotates a bounded same-day event universe instead of permanently starving
events after the first four lexicographic IDs, bounds the complete source bundle, and
uses the guarded source transports. The scientific population-alignment gate remains
FALSE, so same-day probabilities, Telegram delivery, validated P&L, and financial
authority remain disabled.
"""

import argparse
import asyncio
import json
from datetime import datetime, timedelta, timezone
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
from .weather_only_live_paper_corrective import (
    STATION_METADATA_CACHE_MAX_ENTRIES,
    STATION_METADATA_CACHE_TTL_SECONDS,
)
from .weather_only_live_paper_final import FinalWeatherLivePaperService
from .weather_only_live_paper_v2 import DEFAULT_PAPER_STAKE_USD
from .weather_only_rules import compile_temperature_rule_authority
from .weather_only_same_day_contract import build_same_day_contract_semantics
from .weather_only_same_day_capture_store import SameDayCaptureStore
from .weather_only_three_layer_guarded import (
    GuardedNWSNearTermGridClient,
    GuardedSameDayStationMetadataClient,
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
THREE_LAYER_ELIGIBILITY_SCAN_DEADLINE_SECONDS = 30.0
THREE_LAYER_ELIGIBILITY_CONCURRENCY = 4
THREE_LAYER_NWS_SUPPORT_CACHE_TTL_SECONDS = 21_600.0
THREE_LAYER_NWS_SUPPORT_CACHE_MAX_ENTRIES = 128
THREE_LAYER_CURSOR_KEY = "same_day_three_layer_rotation_cursor_v1"
# Each admitted event is durably throttled to one saved capture/hour by the inherited
# collector. Restricting the rotating universe to 12 therefore bounds saved rows to
# 12 * 24 * 31 in any theoretical fully-active 31-day interval.
THREE_LAYER_31D_CAPTURE_ROW_BOUND = THREE_LAYER_SELECTION_UNIVERSE_CAP * 24 * 31
THREE_LAYER_CAPTURE_JSON_BYTES_CAP = 512 * 1024 * 1024
THREE_LAYER_ATTEMPT_ROW_CAP = THREE_LAYER_31D_CAPTURE_ROW_BOUND


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
    return [
        ordered[(start + offset) % len(ordered)]
        for offset in range(min(limit, len(ordered)))
    ]


class ThreeLayerValidationWeatherLivePaperService(FinalWeatherLivePaperService):
    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)

        # Re-open the same SQLite-backed capture facade with explicit hard limits.
        # Existing rows are preserved; exhaustion fails closed instead of pruning.
        self.same_day_captures = SameDayCaptureStore(
            self.db_path,
            max_capture_rows=THREE_LAYER_31D_CAPTURE_ROW_BOUND,
            max_capture_json_bytes=THREE_LAYER_CAPTURE_JSON_BYTES_CAP,
            max_attempt_rows=THREE_LAYER_ATTEMPT_ROW_CAP,
        )

        # These constructors perform no network I/O. Keep the inherited clients alive
        # until all replacements exist, then swap atomically from the service's point
        # of view; superseded async pools are closed during service shutdown.
        guarded_wrh = GuardedNWSWRHLiveClient()
        guarded_nws = GuardedNWSNearTermGridClient()
        guarded_gefs = GuardedOpenMeteoGEFSHourlyClient()
        guarded_station = GuardedSameDayStationMetadataClient()

        self._three_layer_superseded_wrh = self._same_day_wrh
        self._three_layer_superseded_nws = self._same_day_nws
        self._three_layer_superseded_gefs = self._same_day_gefs
        self._same_day_wrh = guarded_wrh
        self._same_day_nws = guarded_nws
        self._same_day_gefs = guarded_gefs
        self._three_layer_station_client = guarded_station
        self._three_layer_last_eligible_total = 0
        self._three_layer_last_candidate_today_total = 0
        self._three_layer_last_layer2_unsupported_total = 0
        self._three_layer_last_selected_ids: tuple[str, ...] = ()
        self._three_layer_last_universe_truncated = False
        self._three_layer_last_coverage_complete = True
        self._three_layer_last_eligibility_failure_code: str | None = None
        self._three_layer_nws_support_cache: dict[tuple[float, float], tuple[float, bool]] = {}

    async def close(self) -> None:
        try:
            self._three_layer_superseded_wrh.close()
        except Exception:
            pass
        station_client = getattr(self, "_three_layer_station_client", None)
        station_close = (
            station_client.close() if station_client is not None else asyncio.sleep(0)
        )
        await asyncio.gather(
            station_close,
            self._three_layer_superseded_nws.close(),
            self._three_layer_superseded_gefs.close(),
            return_exceptions=True,
        )
        await super().close()

    async def _station_metadata_for_compiled(self, compiled):
        # This virtual hook is also used by the inherited future-day lane. Only
        # acquisition safety changes here; parser/identity semantics are unchanged.
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
        metadata = await self._three_layer_station_client.station(station_id)
        self._bounded_station_metadata[station_id] = (now, metadata)
        self._bounded_station_metadata.move_to_end(station_id)
        while len(self._bounded_station_metadata) > STATION_METADATA_CACHE_MAX_ENTRIES:
            self._bounded_station_metadata.popitem(last=False)
        return metadata

    async def _nws_point_supported_for_metadata(self, metadata) -> bool:
        latitude = float(metadata.latitude)
        longitude = float(metadata.longitude)
        key = (round(latitude, 4), round(longitude, 4))
        now = self._station_cache_now()
        cache = getattr(self, "_three_layer_nws_support_cache", None)
        if cache is None:
            cache = {}
            self._three_layer_nws_support_cache = cache
        cached = cache.get(key)
        if cached is not None:
            age = now - float(cached[0])
            if 0.0 <= age <= THREE_LAYER_NWS_SUPPORT_CACHE_TTL_SECONDS:
                value = bool(cached[1])
                # Dict insertion order gives us a tiny dependency-free bounded LRU.
                cache.pop(key, None)
                cache[key] = (now, value)
                return value
            cache.pop(key, None)

        supported = await self._same_day_nws.point_supported(
            latitude=latitude,
            longitude=longitude,
        )
        cache[key] = (now, bool(supported))
        while len(cache) > THREE_LAYER_NWS_SUPPORT_CACHE_MAX_ENTRIES:
            cache.pop(next(iter(cache)))
        return bool(supported)

    async def _same_day_eligible(self, events: tuple[dict, ...]) -> tuple[list[tuple], list[str]]:
        """Enumerate the complete same-day Layer-2-capable population or fail closed.

        Expected semantic mismatches and explicit NWS /points 404s are legitimate
        exclusions. Provider outages, malformed station metadata, unexpected parser
        failures and scan timeouts invalidate the whole selection for the cycle; they
        must never produce a biased partial research sample.
        """
        errors: list[str] = []
        seen_event_ids: set[str] = set()
        candidates: list[tuple[str, dict, object, object]] = []
        loop = asyncio.get_running_loop()
        scan_deadline = loop.time() + THREE_LAYER_ELIGIBILITY_SCAN_DEADLINE_SECONDS
        eligibility_now_utc = datetime.now(tz=timezone.utc)
        utc_today = eligibility_now_utc.date()
        plausible_local_dates = {
            utc_today - timedelta(days=1),
            utc_today,
            utc_today + timedelta(days=1),
        }

        self._three_layer_last_eligible_total = 0
        self._three_layer_last_candidate_today_total = 0
        self._three_layer_last_layer2_unsupported_total = 0
        self._three_layer_last_selected_ids = ()
        self._three_layer_last_universe_truncated = False
        self._three_layer_last_coverage_complete = True
        self._three_layer_last_eligibility_failure_code = None

        def fail_closed(code: str, *, truncated: bool = False):
            self._three_layer_last_selected_ids = ()
            self._three_layer_last_universe_truncated = bool(truncated)
            self._three_layer_last_coverage_complete = False
            self._three_layer_last_eligibility_failure_code = str(code)
            if code not in errors:
                errors.append(code)
            return [], errors

        # Phase 1 is CPU-only. The +/- one UTC-day prefilter is exhaustive for current
        # civil time zones and prevents future-dated contracts from consuming provider
        # calls merely to prove that they are not today's station-local target.
        for event in events:
            if loop.time() >= scan_deadline:
                return fail_closed("SAME_DAY_ELIGIBILITY_SCAN_TIMEOUT", truncated=True)
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
                return fail_closed(f"SAME_DAY_SEMANTICS:{event_id}:{code}")

            event_id = str(compiled.event_id)
            if event_id in seen_event_ids:
                continue
            seen_event_ids.add(event_id)
            if not semantics.layer1_adapter_capable:
                continue
            if compiled.target_date not in plausible_local_dates:
                continue
            station_id = str(compiled.station_hint or "").strip().upper()
            if not station_id:
                return fail_closed(f"SAME_DAY_STATION:{event_id}:STATION_ID_MISSING")
            candidates.append((event_id, event, compiled, semantics))

        # Phase 2 resolves each unique settlement station once. Calls are concurrent but
        # bounded, and the entire enumeration remains under one monotonic deadline.
        by_station: dict[str, list[tuple[str, dict, object, object]]] = {}
        for item in candidates:
            station_id = str(item[2].station_hint).strip().upper()
            by_station.setdefault(station_id, []).append(item)

        station_results: dict[str, object] = {}
        semaphore = asyncio.Semaphore(THREE_LAYER_ELIGIBILITY_CONCURRENCY)

        async def resolve_station(station_id: str, representative):
            async with semaphore:
                return station_id, await self._station_metadata_for_compiled(representative)

        if by_station:
            remaining = scan_deadline - loop.time()
            if remaining <= 0.0:
                return fail_closed("SAME_DAY_ELIGIBILITY_SCAN_TIMEOUT", truncated=True)
            coroutines = [
                resolve_station(station_id, rows[0][2])
                for station_id, rows in sorted(by_station.items())
            ]
            try:
                async with asyncio.timeout(remaining):
                    resolved = await asyncio.gather(*coroutines, return_exceptions=True)
            except TimeoutError:
                return fail_closed("SAME_DAY_ELIGIBILITY_SCAN_TIMEOUT", truncated=True)
            for (station_id, _rows), result in zip(sorted(by_station.items()), resolved):
                if isinstance(result, BaseException):
                    code = getattr(result, "code", type(result).__name__)
                    return fail_closed(f"SAME_DAY_STATION_PROVIDER:{station_id}:{code}")
                returned_station, metadata = result
                if returned_station != station_id or metadata is None:
                    return fail_closed(f"SAME_DAY_STATION_PROVIDER:{station_id}:METADATA_MISSING")
                station_results[station_id] = metadata

        today_candidates: list[tuple[str, dict, object, object, object]] = []
        for item in candidates:
            event_id, event, compiled, semantics = item
            station_id = str(compiled.station_hint).strip().upper()
            metadata = station_results.get(station_id)
            if metadata is None:
                return fail_closed(f"SAME_DAY_STATION_PROVIDER:{station_id}:METADATA_MISSING")
            try:
                zone = ZoneInfo(str(metadata.timezone))
                local_today = eligibility_now_utc.astimezone(zone).date()
                # Prove coordinate coercion now so invalid metadata cannot be rebranded as
                # a normal unsupported NWS point in the next phase.
                float(metadata.latitude)
                float(metadata.longitude)
            except (ZoneInfoNotFoundError, AttributeError, TypeError, ValueError, OverflowError) as exc:
                return fail_closed(f"SAME_DAY_STATION:{event_id}:{type(exc).__name__}")
            if compiled.target_date == local_today:
                today_candidates.append((event_id, event, compiled, semantics, metadata))

        self._three_layer_last_candidate_today_total = len(today_candidates)

        # Phase 3 proves Layer-2 geographic capability. An explicit NWS /points 404 is
        # the only normal exclusion. Every other failure invalidates this cycle's full
        # population enumeration rather than silently selecting the stations that happened
        # to answer. Probe each distinct coordinate once.
        by_point: dict[tuple[float, float], list[tuple[str, dict, object, object, object]]] = {}
        for item in today_candidates:
            metadata = item[4]
            point = (round(float(metadata.latitude), 4), round(float(metadata.longitude), 4))
            by_point.setdefault(point, []).append(item)

        point_support: dict[tuple[float, float], bool] = {}

        async def resolve_point(point, metadata):
            async with semaphore:
                return point, await self._nws_point_supported_for_metadata(metadata)

        if by_point:
            remaining = scan_deadline - loop.time()
            if remaining <= 0.0:
                return fail_closed("SAME_DAY_ELIGIBILITY_SCAN_TIMEOUT", truncated=True)
            ordered_points = sorted(by_point.items())
            coroutines = [resolve_point(point, rows[0][4]) for point, rows in ordered_points]
            try:
                async with asyncio.timeout(remaining):
                    resolved = await asyncio.gather(*coroutines, return_exceptions=True)
            except TimeoutError:
                return fail_closed("SAME_DAY_ELIGIBILITY_SCAN_TIMEOUT", truncated=True)
            for (point, rows), result in zip(ordered_points, resolved):
                if isinstance(result, BaseException):
                    code = getattr(result, "code", type(result).__name__)
                    station_id = str(rows[0][2].station_hint).strip().upper()
                    return fail_closed(f"SAME_DAY_LAYER2_PROVIDER:{station_id}:{code}")
                returned_point, supported = result
                if returned_point != point:
                    return fail_closed("SAME_DAY_LAYER2_PROVIDER:POINT_IDENTITY_MISMATCH")
                point_support[point] = bool(supported)

        eligible: list[tuple[str, dict, object, object, object]] = []
        unsupported = 0
        for item in today_candidates:
            metadata = item[4]
            point = (round(float(metadata.latitude), 4), round(float(metadata.longitude), 4))
            if point_support.get(point) is True:
                eligible.append(item)
            else:
                unsupported += 1

        eligible.sort(key=lambda item: item[0])
        self._three_layer_last_layer2_unsupported_total = unsupported
        self._three_layer_last_eligible_total = len(eligible)
        if len(eligible) > THREE_LAYER_SELECTION_UNIVERSE_CAP:
            self._three_layer_last_universe_truncated = True
            self._three_layer_last_selected_ids = ()
            self._three_layer_last_coverage_complete = False
            self._three_layer_last_eligibility_failure_code = "SAME_DAY_SELECTION_UNIVERSE_CAP_EXCEEDED"
            errors.append(
                f"SAME_DAY_SELECTION_UNIVERSE_CAP_EXCEEDED:{len(eligible)}>"
                f"{THREE_LAYER_SELECTION_UNIVERSE_CAP}"
            )
            return [], errors

        cursor = self.positions.get_state(THREE_LAYER_CURSOR_KEY, "")
        selected = _rotate_after_cursor(eligible, cursor, THREE_LAYER_MAX_EVENTS_PER_CYCLE)
        self._three_layer_last_selected_ids = tuple(str(item[0]) for item in selected)
        if selected:
            self.positions.set_state(THREE_LAYER_CURSOR_KEY, str(selected[-1][0]))
        return selected, errors

    async def _fetch_same_day_source_bundle(self, compiled, metadata):
        """Acquire all three sources without leaving siblings alive after one fails.

        ``asyncio.gather`` without ``return_exceptions`` propagates the first source
        failure while sibling source coroutines may continue in the background. On a
        small VM that can accumulate overlapping provider work across later events.
        Every guarded source already has a tighter individual bound than this bundle,
        so wait for all three bounded attempts, then propagate the first failure in
        fixed WRH/NWS/GEFS order.
        """
        station = str(compiled.station_hint).upper()
        latitude = float(metadata.latitude)
        longitude = float(metadata.longitude)
        sources = (
            asyncio.to_thread(
                self._same_day_wrh.fetch_snapshot,
                station=station,
                target_date=compiled.target_date,
            ),
            self._same_day_nws.fetch_snapshot(
                station=station,
                latitude=latitude,
                longitude=longitude,
            ),
            self._same_day_gefs.target_day(
                station=station,
                latitude=latitude,
                longitude=longitude,
                target_date=compiled.target_date,
                unit=str(compiled.unit),
                timezone=str(metadata.timezone),
            ),
        )
        async with asyncio.timeout(THREE_LAYER_SOURCE_BUNDLE_DEADLINE_SECONDS):
            results = await asyncio.gather(*sources, return_exceptions=True)
        for result in results:
            if isinstance(result, BaseException):
                raise result
        return tuple(results)

    async def _capture_same_day_research(self, events: tuple[dict, ...]) -> dict:
        result = dict(await super()._capture_same_day_research(events))
        result.update(
            {
                "version": "same_day_three_layer_silent_collection_v3_guarded_rotating",
                "selection_policy": THREE_LAYER_SELECTION_POLICY,
                "eligible_events_total": self._three_layer_last_eligible_total,
                "candidate_events_today_total": self._three_layer_last_candidate_today_total,
                "layer2_unsupported_events_total": self._three_layer_last_layer2_unsupported_total,
                "selected_event_ids": list(self._three_layer_last_selected_ids),
                "selection_universe_cap": THREE_LAYER_SELECTION_UNIVERSE_CAP,
                "selection_universe_truncated": self._three_layer_last_universe_truncated,
                "selection_coverage_complete": (
                    self._three_layer_last_coverage_complete
                    and not self._three_layer_last_universe_truncated
                ),
                "eligibility_failure_code": self._three_layer_last_eligibility_failure_code,
                "eligibility_concurrency": THREE_LAYER_ELIGIBILITY_CONCURRENCY,
                "max_events_per_cycle": THREE_LAYER_MAX_EVENTS_PER_CYCLE,
                "source_bundle_deadline_seconds": THREE_LAYER_SOURCE_BUNDLE_DEADLINE_SECONDS,
                "eligibility_scan_deadline_seconds": THREE_LAYER_ELIGIBILITY_SCAN_DEADLINE_SECONDS,
                "theoretical_31_day_row_bound_at_full_daily_eligibility": (
                    THREE_LAYER_31D_CAPTURE_ROW_BOUND
                ),
                "capture_json_bytes_cap": THREE_LAYER_CAPTURE_JSON_BYTES_CAP,
                "attempt_row_cap": THREE_LAYER_ATTEMPT_ROW_CAP,
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
