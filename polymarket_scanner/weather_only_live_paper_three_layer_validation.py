from __future__ import annotations

"""Guarded final PAPER runtime for validating the pure three-layer same-day system.

This branch deliberately contains no PWS integration. It keeps the reviewed final
future-day PAPER engine unchanged and hardens only the silent same-day research lane:
Layer 1 exact WRH, Layer 2 NWS grid, and Layer 3 31-member hourly GEFS.

The wrapper rotates a bounded same-day event universe instead of permanently starving
events after the first four lexicographic IDs, bounds the complete source bundle,
resolves station metadata concurrently under one global deadline, and stores research
evidence losslessly with bounded compression and read-time integrity verification.
The scientific population-alignment gate remains FALSE, so same-day probabilities,
Telegram delivery, validated P&L, and financial authority remain disabled.
"""

import argparse
import asyncio
import json
from datetime import datetime, timezone
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
from .weather_only_same_day_contract import (
    SameDayContractError,
    build_same_day_contract_semantics,
)
from .weather_only_same_day_capture_store_compressed import (
    CompressedSameDayCaptureStore,
)
from .weather_only_three_layer_guarded import (
    GuardedNWSNearTermGridClient,
    GuardedSameDayStationMetadataClient,
    GuardedNWSWRHLiveClient,
    GuardedOpenMeteoGEFSHourlyClient,
)


THREE_LAYER_VALIDATION_RUNTIME_VERSION = (
    "weather_three_layer_validation_v3_event_isolation_single_snapshot"
)
THREE_LAYER_COLLECTION_VERSION = (
    "same_day_three_layer_silent_collection_v5_event_isolation"
)
THREE_LAYER_SELECTION_POLICY = "PERSISTENT_ROUND_ROBIN_ELIGIBLE_V1"
# Live census work observed roughly 22 simultaneously eligible same-day contracts.
# Keep enough headroom to avoid the old 12-market starvation failure while retaining
# a hard fail-closed research bound on the e2-micro.
THREE_LAYER_SELECTION_UNIVERSE_CAP = 32
THREE_LAYER_MAX_EVENTS_PER_CYCLE = 4
THREE_LAYER_SOURCE_BUNDLE_DEADLINE_SECONDS = 35.0
THREE_LAYER_ELIGIBILITY_SCAN_DEADLINE_SECONDS = 30.0
THREE_LAYER_STATION_METADATA_CONCURRENCY = 4
THREE_LAYER_ELIGIBILITY_STATION_CAP = 64
THREE_LAYER_EVENT_REJECTION_SAMPLE_CAP = 20
THREE_LAYER_CURSOR_KEY = "same_day_three_layer_rotation_cursor_v1"
# Every admitted event is durably throttled to at most one attempt/capture per hour by
# the inherited collector. Thus 32 * 24 * 31 is the hard theoretical 31-day bound.
THREE_LAYER_31D_CAPTURE_ROW_BOUND = THREE_LAYER_SELECTION_UNIVERSE_CAP * 24 * 31
# New evidence is compressed losslessly. 1 GiB is deliberately well above measured
# month-scale compressed projections while still bounded relative to the VM disk.
THREE_LAYER_CAPTURE_JSON_BYTES_CAP = 1024 * 1024 * 1024
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
        # Existing legacy JSON rows remain readable. New rows use bounded lossless
        # compression; exhaustion fails closed instead of pruning evidence.
        self.same_day_captures = CompressedSameDayCaptureStore(
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
        self._three_layer_last_selected_ids: tuple[str, ...] = ()
        self._three_layer_last_universe_truncated = False
        self._three_layer_last_eligibility_complete = True
        self._three_layer_last_station_count = 0
        self._three_layer_last_event_rejected_total = 0
        self._three_layer_last_event_rejection_samples: tuple[str, ...] = ()

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
        # Use a post-receipt monotonic timestamp. A slow request must not make newly
        # received metadata appear older than it really is, nor extend from pre-await.
        stored_at = self._station_cache_now()
        self._bounded_station_metadata[station_id] = (stored_at, metadata)
        self._bounded_station_metadata.move_to_end(station_id)
        while len(self._bounded_station_metadata) > STATION_METADATA_CACHE_MAX_ENTRIES:
            self._bounded_station_metadata.popitem(last=False)
        return metadata

    async def _same_day_eligible(self, events: tuple[dict, ...]) -> tuple[list[tuple], list[str]]:
        """Build the complete station-local same-day universe before selecting from it.

        External event-level malformations are isolated and rejected. Unexpected
        internal semantic failures, station-source failures, timeouts and hard-cap
        violations still fail the *whole* research universe closed. This prevents one
        broken public event from starving all valid events without hiding code defects
        or manufacturing a partial station population after an acquisition failure.
        """
        errors: list[str] = []
        seen_event_ids: set[str] = set()
        candidates: list[tuple[str, dict, object, object, str]] = []
        station_representatives: dict[str, object] = {}
        rejection_samples: list[str] = []
        rejected_total = 0
        loop = asyncio.get_running_loop()
        scan_deadline = loop.time() + THREE_LAYER_ELIGIBILITY_SCAN_DEADLINE_SECONDS
        eligibility_now_utc = datetime.now(tz=timezone.utc)

        def publish_rejections() -> None:
            self._three_layer_last_event_rejected_total = rejected_total
            self._three_layer_last_event_rejection_samples = tuple(rejection_samples)

        def reject_event(event: dict, code: str) -> None:
            nonlocal rejected_total
            rejected_total += 1
            if len(rejection_samples) < THREE_LAYER_EVENT_REJECTION_SAMPLE_CAP:
                event_id = str(event.get("id") or event.get("eventId") or "unknown")
                rejection_samples.append(f"{event_id}:{code}")

        def incomplete(code: str | None = None):
            if code and code not in errors:
                errors.append(code)
            publish_rejections()
            self._three_layer_last_eligible_total = 0
            self._three_layer_last_selected_ids = ()
            self._three_layer_last_universe_truncated = True
            self._three_layer_last_eligibility_complete = False
            return [], errors

        for event in events:
            if loop.time() >= scan_deadline:
                return incomplete("SAME_DAY_ELIGIBILITY_SCAN_TIMEOUT")
            if not isinstance(event, dict):
                continue

            # Gamma's event contract requires markets to be a collection. Reject a
            # malformed external row locally before any parser can accidentally iterate
            # a string/scalar and convert one bad public object into a universe outage.
            raw_markets = event.get("markets")
            if raw_markets is not None and not isinstance(raw_markets, (list, tuple)):
                reject_event(event, "EVENT_MARKETS_TYPE_INVALID")
                continue

            try:
                compiled = compile_strict_temperature_event(event)
            except StrictWeatherContractError:
                # Ordinary unsupported/non-weather contracts are expected discovery
                # rejects and are not collection errors.
                continue
            except (TypeError, ValueError, KeyError, AttributeError) as exc:
                reject_event(event, f"STRICT_INPUT_MALFORMED:{type(exc).__name__}")
                continue
            except Exception as exc:
                code = getattr(exc, "code", type(exc).__name__)
                event_id = str(event.get("id") or event.get("eventId") or "unknown")
                errors.append(f"SAME_DAY_COMPILE_INTERNAL:{event_id}:{code}")
                continue

            try:
                authority = compile_temperature_rule_authority(event, compiled)
            except (TypeError, ValueError, KeyError, AttributeError) as exc:
                reject_event(event, f"RULE_INPUT_MALFORMED:{type(exc).__name__}")
                continue
            except Exception as exc:
                code = getattr(exc, "code", type(exc).__name__)
                event_id = str(compiled.event_id)
                errors.append(f"SAME_DAY_RULE_INTERNAL:{event_id}:{code}")
                continue

            try:
                semantics = build_same_day_contract_semantics(compiled, authority)
            except SameDayContractError as exc:
                # A strict contract can still be intentionally ineligible for this
                # narrower same-day adapter. That is an event-local semantic reject,
                # not evidence that every other event's universe is incomplete.
                reject_event(event, exc.code)
                continue
            except Exception as exc:
                code = getattr(exc, "code", type(exc).__name__)
                event_id = str(compiled.event_id)
                errors.append(f"SAME_DAY_SEMANTICS_INTERNAL:{event_id}:{code}")
                continue

            event_id = str(compiled.event_id)
            if event_id in seen_event_ids:
                continue
            seen_event_ids.add(event_id)
            if not semantics.layer1_adapter_capable:
                reject_event(event, "LAYER1_ADAPTER_NOT_CAPABLE")
                continue
            station = str(compiled.station_hint or "").strip().upper()
            if not station:
                # build_same_day_contract_semantics already requires a station. Reaching
                # this path therefore indicates an internal invariant failure.
                errors.append(f"SAME_DAY_STATION:{event_id}:STATION_MISSING")
                continue
            candidates.append((event_id, event, compiled, semantics, station))
            station_representatives.setdefault(station, compiled)
            if len(station_representatives) > THREE_LAYER_ELIGIBILITY_STATION_CAP:
                self._three_layer_last_station_count = len(station_representatives)
                return incomplete(
                    f"SAME_DAY_ELIGIBILITY_STATION_CAP_EXCEEDED:"
                    f"{len(station_representatives)}>{THREE_LAYER_ELIGIBILITY_STATION_CAP}"
                )

        publish_rejections()
        self._three_layer_last_station_count = len(station_representatives)
        if errors:
            # Unexpected internal local-semantic failures make completeness ambiguous.
            return incomplete()
        if loop.time() >= scan_deadline:
            return incomplete("SAME_DAY_ELIGIBILITY_SCAN_TIMEOUT")

        semaphore = asyncio.Semaphore(THREE_LAYER_STATION_METADATA_CONCURRENCY)

        async def fetch_station(station: str, compiled):
            async with semaphore:
                return await self._station_metadata_for_compiled(compiled)

        stations = list(station_representatives)
        tasks = [
            asyncio.create_task(fetch_station(station, station_representatives[station]))
            for station in stations
        ]
        if tasks:
            remaining = scan_deadline - loop.time()
            if remaining <= 0.0:
                for task in tasks:
                    task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
                return incomplete("SAME_DAY_ELIGIBILITY_SCAN_TIMEOUT")
            try:
                results = await asyncio.wait_for(
                    asyncio.gather(*tasks, return_exceptions=True),
                    timeout=remaining,
                )
            except TimeoutError:
                for task in tasks:
                    task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
                return incomplete("SAME_DAY_ELIGIBILITY_SCAN_TIMEOUT")
        else:
            results = []

        metadata_by_station: dict[str, object] = {}
        zones_by_station: dict[str, ZoneInfo] = {}
        for station, result in zip(stations, results):
            if isinstance(result, BaseException):
                code = getattr(result, "code", type(result).__name__)
                errors.append(f"SAME_DAY_STATION:{station}:{code}")
                continue
            if result is None:
                errors.append(f"SAME_DAY_STATION:{station}:METADATA_UNAVAILABLE")
                continue
            try:
                result_station = str(result.station or "").strip().upper()
                if result_station != station:
                    raise ValueError("IDENTITY_MISMATCH")
                zone = ZoneInfo(str(result.timezone))
            except (ZoneInfoNotFoundError, AttributeError, ValueError) as exc:
                errors.append(f"SAME_DAY_STATION:{station}:{type(exc).__name__}")
                continue
            metadata_by_station[station] = result
            zones_by_station[station] = zone

        if errors or len(metadata_by_station) != len(stations):
            return incomplete()

        eligible: list[tuple[str, dict, object, object, object]] = []
        for event_id, event, compiled, semantics, station in candidates:
            metadata = metadata_by_station[station]
            local_today = eligibility_now_utc.astimezone(zones_by_station[station]).date()
            if compiled.target_date == local_today:
                eligible.append((event_id, event, compiled, semantics, metadata))

        eligible.sort(key=lambda item: item[0])
        self._three_layer_last_eligible_total = len(eligible)
        if len(eligible) > THREE_LAYER_SELECTION_UNIVERSE_CAP:
            return incomplete(
                f"SAME_DAY_SELECTION_UNIVERSE_CAP_EXCEEDED:{len(eligible)}>"
                f"{THREE_LAYER_SELECTION_UNIVERSE_CAP}"
            )

        self._three_layer_last_universe_truncated = False
        self._three_layer_last_eligibility_complete = True
        cursor = self.positions.get_state(THREE_LAYER_CURSOR_KEY, "")
        selected = _rotate_after_cursor(
            eligible, cursor, THREE_LAYER_MAX_EVENTS_PER_CYCLE
        )
        self._three_layer_last_selected_ids = tuple(str(item[0]) for item in selected)
        if selected:
            # Advance even when a selected source later fails. One repeatedly broken
            # station must not permanently starve every other admitted research event.
            self.positions.set_state(THREE_LAYER_CURSOR_KEY, str(selected[-1][0]))
        publish_rejections()
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
                "version": THREE_LAYER_COLLECTION_VERSION,
                "selection_policy": THREE_LAYER_SELECTION_POLICY,
                "eligible_events_total": self._three_layer_last_eligible_total,
                "selected_event_ids": list(self._three_layer_last_selected_ids),
                "selection_universe_cap": THREE_LAYER_SELECTION_UNIVERSE_CAP,
                "selection_universe_truncated": self._three_layer_last_universe_truncated,
                "selection_coverage_complete": self._three_layer_last_eligibility_complete,
                "eligibility_station_count": self._three_layer_last_station_count,
                "eligibility_station_cap": THREE_LAYER_ELIGIBILITY_STATION_CAP,
                "event_rejected_total": self._three_layer_last_event_rejected_total,
                "event_rejection_samples": list(
                    self._three_layer_last_event_rejection_samples
                ),
                "event_rejection_sample_cap": THREE_LAYER_EVENT_REJECTION_SAMPLE_CAP,
                "station_metadata_concurrency": THREE_LAYER_STATION_METADATA_CONCURRENCY,
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
