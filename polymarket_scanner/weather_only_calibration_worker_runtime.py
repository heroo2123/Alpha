from __future__ import annotations

"""Operational health wrapper for the prospective weather calibration worker.

The evidence-capture worker deliberately reports detailed sub-operation results and a
minimal ``cycle_ok`` integrity flag. For an eventual long-running service that is not
sufficient: a process can remain alive while discovery, GEFS capture or the WRH
collector is degraded.

This wrapper does not alter discovery, forecast, capture, reservation, settlement or
calibration semantics. It adds three independent operational attestations:

* ``process_healthy`` -- core integrity/state machinery completed safely;
* ``research_collection_healthy`` -- current discovery/capture/collector operations
  did not report source/evidence failures;
* ``prospective_gap_detected`` -- there is concrete evidence that one or more
  prospective samples were missed or terminally failed.

Normal BEFORE-window states are healthy. An already-past contract is a gap signal but
not a process crash. Temporary collector fetch errors degrade collection health but do
not by themselves assert a permanent gap. No financial authority exists here.
"""

import argparse
import asyncio
import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .weather_only_calibration_worker import (
    LOOP_INTERVAL_SECONDS,
    MIN_LOOP_INTERVAL_SECONDS,
    WeatherCalibrationResearchWorker,
)


WEATHER_CALIBRATION_WORKER_RUNTIME_VERSION = "weather_calibration_worker_runtime_v1_explicit_research_health"


@dataclass(frozen=True, slots=True)
class WeatherCalibrationCycleHealth:
    process_healthy: bool
    research_collection_healthy: bool
    prospective_gap_detected: bool
    health_reasons: tuple[str, ...]
    gap_reasons: tuple[str, ...]
    financial_authority: bool = field(init=False, default=False)
    financial_delivery: bool = field(init=False, default=False)
    automatic_order_placement: bool = field(init=False, default=False)

    def as_dict(self) -> dict:
        return asdict(self)


def _positive_int(value: object) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else 0


def assess_worker_cycle_health(report: object) -> WeatherCalibrationCycleHealth:
    if not isinstance(report, dict):
        return WeatherCalibrationCycleHealth(
            process_healthy=False,
            research_collection_healthy=False,
            prospective_gap_detected=False,
            health_reasons=("WORKER_REPORT_INVALID",),
            gap_reasons=(),
        )

    health: list[str] = []
    gaps: list[str] = []

    process_healthy = report.get("cycle_ok") is True and not bool(report.get("state_reconciliation_error"))
    if not process_healthy:
        health.append("CORE_CYCLE_INTEGRITY_FAILED")

    discovery = report.get("discovery")
    if not isinstance(discovery, dict):
        health.append("DISCOVERY_REPORT_MISSING")
    elif discovery.get("attempted") is True and discovery.get("success") is False:
        health.append(str(discovery.get("error") or "DISCOVERY_FAILED"))

    capture = report.get("capture")
    if not isinstance(capture, dict):
        health.append("CAPTURE_REPORT_MISSING")
    else:
        errors = capture.get("errors")
        if isinstance(errors, dict) and errors:
            for code, count in sorted(errors.items()):
                health.append(f"CAPTURE:{code}:{count}")
            gaps.append("CAPTURE_ERROR_DURING_PROSPECTIVE_PIPELINE")
        missed = _positive_int(capture.get("missed_window_events"))
        if missed:
            gaps.append(f"MISSED_CAPTURE_WINDOW_EVENTS:{missed}")

    collector = report.get("collector")
    if not isinstance(collector, dict):
        health.append("COLLECTOR_REPORT_MISSING")
    else:
        if collector.get("error"):
            health.append(f"COLLECTOR:{collector.get('error')}")
        fetch_errors = collector.get("fetch_errors")
        if isinstance(fetch_errors, (list, tuple)) and fetch_errors:
            health.append(f"COLLECTOR_FETCH_ERRORS:{len(fetch_errors)}")
        failed_captures = _positive_int(collector.get("failed_captures"))
        if failed_captures:
            health.append(f"COLLECTOR_FAILED_CAPTURES:{failed_captures}")
            gaps.append(f"TERMINAL_COLLECTOR_FAILURES:{failed_captures}")

    state = report.get("state")
    if not isinstance(state, dict):
        health.append("STATE_REPORT_MISSING")
    else:
        status_counts = state.get("status_counts")
        if isinstance(status_counts, dict):
            failed_reservations = _positive_int(status_counts.get("FAILED"))
            if failed_reservations:
                gaps.append(f"FAILED_RESERVED_EVENTS:{failed_reservations}")

    # Preserve order while removing duplicates so output is stable and audit-friendly.
    health_reasons = tuple(dict.fromkeys(health))
    gap_reasons = tuple(dict.fromkeys(gaps))
    collection_healthy = process_healthy and not health_reasons
    return WeatherCalibrationCycleHealth(
        process_healthy=process_healthy,
        research_collection_healthy=collection_healthy,
        prospective_gap_detected=bool(gap_reasons),
        health_reasons=health_reasons,
        gap_reasons=gap_reasons,
    )


class OperationalWeatherCalibrationResearchWorker(WeatherCalibrationResearchWorker):
    """Same evidence worker with explicit operational health appended to each report."""

    async def run_cycle(self) -> dict:
        report = await super().run_cycle()
        health = assess_worker_cycle_health(report)
        report["runtime_version"] = WEATHER_CALIBRATION_WORKER_RUNTIME_VERSION
        report["operational_health"] = health.as_dict()
        report["process_healthy"] = health.process_healthy
        report["research_collection_healthy"] = health.research_collection_healthy
        report["prospective_gap_detected"] = health.prospective_gap_detected
        # Keep cycle_ok as the core integrity flag for backward compatibility. The
        # service/readiness monitor must use the explicit health fields above.
        report["financial_authority"] = False
        report["financial_delivery"] = False
        report["automatic_order_placement"] = False
        return report


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(text + "\n")
    os.replace(temporary, path)


async def _main_async(args) -> int:
    worker = OperationalWeatherCalibrationResearchWorker(db_path=args.db)
    try:
        interval = max(MIN_LOOP_INTERVAL_SECONDS, float(args.interval_seconds))
        while True:
            report = await worker.run_cycle()
            payload = json.dumps(report, indent=2, sort_keys=True)
            if args.output:
                _atomic_write(args.output, payload)
            print(payload, flush=True)
            if not args.loop:
                # A one-shot operational probe should fail if current collection is
                # degraded. A normal BEFORE-window cycle remains healthy and exits 0.
                return 0 if report.get("research_collection_healthy") is True else 2
            elapsed = max(0.0, float(report["finished_at"]) - float(report["started_at"]))
            await asyncio.sleep(max(0.0, interval - elapsed))
    finally:
        await worker.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--db",
        type=Path,
        default=Path(os.environ.get("WEATHER_CALIBRATION_DB", "data/weather-calibration.sqlite")),
    )
    parser.add_argument("--loop", action="store_true")
    parser.add_argument("--interval-seconds", type=float, default=LOOP_INTERVAL_SECONDS)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    raise SystemExit(asyncio.run(_main_async(args)))


if __name__ == "__main__":
    main()
