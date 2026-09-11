from __future__ import annotations

"""Gamma-only coverage census for prospective weather calibration.

This diagnostic measures the current active NWS/WRH daily-temperature universe
against the exact compiler/rule/capture gates used by the prospective research
worker.  It deliberately performs no CLOB requests, no forecast requests, no WRH
source requests, no database writes and no delivery/order actions.

The census is useful for separating engineering coverage from empirical calibration:
Fahrenheit events that pass the frozen worker gates are presently collectable;
Celsius events may be structurally understood but remain excluded from exact WRH
calibration until a metric WRH transport/display/finality adapter is independently
certified.
"""

import argparse
import asyncio
import json
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import date
from pathlib import Path

from .weather_only_contracts import DAILY_HIGH, DAILY_LOW, SOURCE_NWS_WRH, compile_weather_event
from .weather_only_discovery import DEFAULT_TAGS, WeatherOnlyDiscovery
from .weather_only_rules import apply_rule_authority, compile_temperature_rule_authority


WEATHER_CALIBRATION_CENSUS_VERSION = "weather_calibration_census_v1_gamma_only_worker_gate_mirror"
SAMPLE_LIMIT = 20
NEAR_TERM_DAYS = 7


@dataclass(frozen=True, slots=True)
class CalibrationCoverageSample:
    event_id: str
    title: str
    family: str
    unit: str | None
    station: str | None
    target_date: str | None
    rule_profile: str
    rule_semantics_proven: bool
    exactly_one_outcome_proven: bool
    partition_shape_complete: bool
    shadow_supported: bool
    worker_capture_eligible: bool
    financial_authority: bool = False

    def as_dict(self) -> dict:
        return asdict(self)


def _sample(event: dict, compiled, authority, *, worker_capture_eligible: bool) -> CalibrationCoverageSample:
    return CalibrationCoverageSample(
        event_id=str(compiled.event_id),
        title=str(event.get("title") or "")[:300],
        family=str(compiled.family),
        unit=compiled.unit,
        station=str(compiled.station_hint) if compiled.station_hint else None,
        target_date=compiled.target_date.isoformat() if compiled.target_date else None,
        rule_profile=str(authority.profile),
        rule_semantics_proven=bool(authority.rule_semantics_proven),
        exactly_one_outcome_proven=bool(authority.exactly_one_outcome_proven),
        partition_shape_complete=bool(compiled.partition_shape_complete),
        shadow_supported=bool(compiled.shadow_supported),
        worker_capture_eligible=bool(worker_capture_eligible),
    )


def _worker_gate(compiled, authority) -> bool:
    if (
        not compiled.event_id
        or compiled.source_family != SOURCE_NWS_WRH
        or compiled.family not in {DAILY_HIGH, DAILY_LOW}
        or compiled.unit != "F"
        or compiled.target_date is None
        or not compiled.station_hint
        or not compiled.partition_shape_complete
        or not compiled.shadow_supported
        or not authority.rule_semantics_proven
        or not authority.exactly_one_outcome_proven
    ):
        return False
    certified = apply_rule_authority(compiled, authority)
    return bool(certified.exactly_one_outcome_proven and not certified.financial_authority)


async def run_census(
    *,
    discovery: WeatherOnlyDiscovery | None = None,
    as_of_date: date | None = None,
) -> dict:
    owned = discovery is None
    discovery = discovery or WeatherOnlyDiscovery()
    as_of = as_of_date or date.today()
    try:
        snapshot = await discovery.discover(DEFAULT_TAGS)
        units = Counter()
        families = Counter()
        profiles = Counter()
        compiler_rejections = Counter()
        rule_rejections = Counter()
        stations = Counter()
        nws_temperature_events = 0
        rule_proven_events = 0
        exactly_one_events = 0
        worker_capture_eligible = 0
        future_worker_capture_eligible = 0
        near_term_worker_capture_eligible = 0
        celsius_structural_proven = 0
        celsius_future_structural_proven = 0
        samples: list[CalibrationCoverageSample] = []

        for event in snapshot.events:
            compiled = compile_weather_event(event)
            for reason in compiled.rejection_reasons:
                compiler_rejections[str(reason)] += 1
            if compiled.source_family != SOURCE_NWS_WRH or compiled.family not in {DAILY_HIGH, DAILY_LOW}:
                continue
            nws_temperature_events += 1
            families[str(compiled.family)] += 1
            units[str(compiled.unit or "UNRESOLVED")] += 1
            if compiled.station_hint:
                stations[str(compiled.station_hint).upper()] += 1

            authority = compile_temperature_rule_authority(event, compiled)
            profiles[str(authority.profile)] += 1
            for reason in authority.rejection_reasons:
                rule_rejections[str(reason)] += 1
            if authority.rule_semantics_proven:
                rule_proven_events += 1
            if authority.exactly_one_outcome_proven:
                exactly_one_events += 1

            worker_ok = _worker_gate(compiled, authority)
            if worker_ok:
                worker_capture_eligible += 1
                if compiled.target_date is not None and compiled.target_date >= as_of:
                    future_worker_capture_eligible += 1
                    delta = (compiled.target_date - as_of).days
                    if 0 <= delta <= NEAR_TERM_DAYS:
                        near_term_worker_capture_eligible += 1

            celsius_ok = bool(
                compiled.unit == "C"
                and compiled.partition_shape_complete
                and compiled.shadow_supported
                and authority.rule_semantics_proven
                and authority.exactly_one_outcome_proven
            )
            if celsius_ok:
                celsius_structural_proven += 1
                if compiled.target_date is not None and compiled.target_date >= as_of:
                    celsius_future_structural_proven += 1

            if len(samples) < SAMPLE_LIMIT and (
                worker_ok
                or celsius_ok
                or (compiled.target_date is not None and compiled.target_date >= as_of)
            ):
                samples.append(_sample(
                    event,
                    compiled,
                    authority,
                    worker_capture_eligible=worker_ok,
                ))

        return {
            "version": WEATHER_CALIBRATION_CENSUS_VERSION,
            "as_of_date": as_of.isoformat(),
            "read_only": True,
            "gamma_only": True,
            "database_mutation": False,
            "forecast_requests": False,
            "wrh_source_requests": False,
            "clob_requests": False,
            "financial_authority": False,
            "financial_delivery": False,
            "automatic_order_placement": False,
            "discovery": snapshot.summary(),
            "nws_temperature_event_count": nws_temperature_events,
            "unit_counts": dict(sorted(units.items())),
            "family_counts": dict(sorted(families.items())),
            "rule_profile_counts": dict(sorted(profiles.items())),
            "compiler_rejection_counts": dict(sorted(compiler_rejections.items())),
            "rule_rejection_counts": dict(sorted(rule_rejections.items())),
            "rule_semantics_proven_event_count": rule_proven_events,
            "exactly_one_rule_proven_event_count": exactly_one_events,
            "worker_capture_eligible_event_count": worker_capture_eligible,
            "future_worker_capture_eligible_event_count": future_worker_capture_eligible,
            "near_term_worker_capture_eligible_event_count": near_term_worker_capture_eligible,
            "celsius_structural_proven_event_count": celsius_structural_proven,
            "celsius_future_structural_proven_event_count": celsius_future_structural_proven,
            "unique_station_count": len(stations),
            "station_event_counts": dict(sorted(stations.items())),
            "samples": [row.as_dict() for row in samples],
        }
    finally:
        if owned:
            await discovery.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = asyncio.run(run_census())
    payload = json.dumps(report, indent=2, sort_keys=True)
    if args.output:
        args.output.write_text(payload + "\n")
    print(payload)


if __name__ == "__main__":
    main()
