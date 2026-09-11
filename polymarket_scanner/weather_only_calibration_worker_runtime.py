from __future__ import annotations

"""Operational health wrapper for the prospective weather calibration worker.

The evidence-capture worker deliberately reports detailed sub-operation results and a
minimal ``cycle_ok`` integrity flag. For an eventual long-running service that is not
sufficient: a process can remain alive while discovery, GEFS capture or the WRH
collector is degraded.

This wrapper does not alter discovery, forecast, capture, reservation, settlement or
calibration semantics. It adds explicit operational health, a fail-closed horizon
attestation for every successfully registered capture, and a compact digest-bound
attestation of the frozen prospective experiment/policy identity on every cycle.

A crash before horizon attestation can lose a research sample but cannot create
eligible calibration evidence. Experiment-manifest drift makes collection health fail
closed; it never grants calibrated-probability or financial authority.
"""

import argparse
import asyncio
import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .weather_only_calibration_horizon import attest_registered_worker_horizons
from .weather_only_calibration_worker import (
    LOOP_INTERVAL_SECONDS,
    MIN_LOOP_INTERVAL_SECONDS,
    WeatherCalibrationResearchWorker,
)


WEATHER_CALIBRATION_WORKER_RUNTIME_VERSION = "weather_calibration_worker_runtime_v3_health_horizon_experiment_attestation"


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

    horizon = report.get("horizon_attestation")
    if not isinstance(horizon, dict):
        health.append("HORIZON_ATTESTATION_REPORT_MISSING")
    else:
        if horizon.get("error"):
            health.append(f"HORIZON:{horizon.get('error')}")
            gaps.append("HORIZON_ATTESTATION_FAILED")
        horizon_errors = horizon.get("errors")
        if isinstance(horizon_errors, dict) and horizon_errors:
            for code, count in sorted(horizon_errors.items()):
                health.append(f"HORIZON:{code}:{count}")
            gaps.append("HORIZON_ATTESTATION_FAILED")

    experiment = report.get("experiment")
    if not isinstance(experiment, dict):
        health.append("EXPERIMENT_ATTESTATION_REPORT_MISSING")
    elif experiment.get("error"):
        health.append(f"EXPERIMENT:{experiment.get('error')}")
    else:
        if (
            not isinstance(experiment.get("manifest_sha256"), str)
            or len(experiment["manifest_sha256"]) != 64
            or not isinstance(experiment.get("statistical_policy_sha256"), str)
            or len(experiment["statistical_policy_sha256"]) != 64
            or not experiment.get("statistical_policy_id")
            or experiment.get("calibrated_probability_authority") is not False
            or experiment.get("financial_authority") is not False
        ):
            health.append("EXPERIMENT_ATTESTATION_BOUNDARY_INVALID")

    state = report.get("state")
    if not isinstance(state, dict):
        health.append("STATE_REPORT_MISSING")
    else:
        status_counts = state.get("status_counts")
        if isinstance(status_counts, dict):
            failed_reservations = _positive_int(status_counts.get("FAILED"))
            if failed_reservations:
                gaps.append(f"FAILED_RESERVED_EVENTS:{failed_reservations}")

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


def _experiment_attestation() -> dict:
    # Import lazily: the experiment manifest intentionally binds this module's version
    # constant, so importing it during module initialization would create a cycle.
    from .weather_calibration_experiment import (
        build_weather_calibration_experiment_manifest,
        validate_weather_calibration_experiment_manifest,
    )

    manifest = validate_weather_calibration_experiment_manifest(
        build_weather_calibration_experiment_manifest()
    )
    return {
        "manifest_version": manifest.manifest_version,
        "manifest_sha256": manifest.manifest_sha256,
        "capture_policy_id": manifest.capture_policy_id,
        "statistical_policy_status": manifest.statistical_policy_status,
        "statistical_policy_id": manifest.statistical_policy_id,
        "statistical_policy_sha256": manifest.statistical_policy_sha256,
        "prospective_collection_authority": manifest.prospective_collection_authority,
        "calibrated_probability_authority": False,
        "financial_authority": False,
        "financial_delivery": False,
        "automatic_order_placement": False,
    }


class OperationalWeatherCalibrationResearchWorker(WeatherCalibrationResearchWorker):
    """Same evidence worker with horizon lineage, experiment identity and health."""

    async def run_cycle(self) -> dict:
        report = await super().run_cycle()
        try:
            report["horizon_attestation"] = attest_registered_worker_horizons(
                self.state.db,
                created_at=float(report["finished_at"]),
            )
        except Exception as exc:
            code = getattr(exc, "code", type(exc).__name__)
            report["horizon_attestation"] = {
                "error": str(code),
                "financial_authority": False,
            }
        try:
            report["experiment"] = _experiment_attestation()
        except Exception as exc:
            code = getattr(exc, "code", type(exc).__name__)
            report["experiment"] = {
                "error": str(code),
                "calibrated_probability_authority": False,
                "financial_authority": False,
                "financial_delivery": False,
                "automatic_order_placement": False,
            }
        health = assess_worker_cycle_health(report)
        report["runtime_version"] = WEATHER_CALIBRATION_WORKER_RUNTIME_VERSION
        report["operational_health"] = health.as_dict()
        report["process_healthy"] = health.process_healthy
        report["research_collection_healthy"] = health.research_collection_healthy
        report["prospective_gap_detected"] = health.prospective_gap_detected
        report["calibrated_probability_authority"] = False
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
