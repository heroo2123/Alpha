from __future__ import annotations

"""Read-only recorder for a future weather W7 e2-micro acceptance run.

This process deliberately does *not* control the weather scanner.  An operator must
already have the exact candidate scanner running in SILENT_SHADOW mode and pass its
PID plus its atomically written runtime-report file.  The recorder then:

* verifies the report is current and carries the exact read-only runtime invariants;
* samples the scanner PID RSS/host memory and scanner process identity from /proc;
* benchmarks one exact-CLOB one-event incremental evaluation per 30-second sample;
* polls the exact WRH source at a bounded cadence and, when a first-following-date
  finality transition occurs, measures source receipt -> double-CLOB confirmed
  deterministic result-lag shadow candidate;
* reads legacy SQLite containment state in mode=ro before and after the run;
* derives rather than invents all containment counters;
* emits the final tamper-evident W7 acceptance bundle atomically.

There is no service start/stop/restart code, no Telegram sender, no order method, no
DB writer and no detector-promotion authority in this module.
"""

import argparse
import asyncio
import copy
import json
import math
import os
import re
import tempfile
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from .weather_only_acceptance import (
    MIN_DURATION_SECONDS,
    WeatherW7RunEvidence,
    WeatherW7Sample,
)
from .weather_only_acceptance_bundle import (
    WeatherW7AcceptanceBundle,
    build_weather_w7_acceptance_bundle,
    dump_weather_w7_acceptance_bundle_json,
)
from .weather_only_acceptance_containment import (
    WeatherW7ProcessIdentity,
    attest_weather_w7_read_only_surface,
    build_weather_w7_containment_manifest,
    derive_weather_w7_containment_counters,
    read_linux_process_identity,
    read_weather_w7_database_snapshot,
)
from .weather_only_acceptance_evidence import build_weather_w7_evidence_envelope
from .weather_only_acceptance_host import read_linux_proc_metrics
from .weather_only_acceptance_latency import (
    CHANGE_FINALITY_TRANSITION,
    WeatherW7SourceLatencyError,
    WeatherW7SourceUpdateLatencyMeasurement,
    build_wrh_source_update_trigger,
    measure_w7_source_update_confirmation,
)
from .weather_only_acceptance_measurements import build_weather_w7_measurement_manifest
from .weather_only_clob import WeatherCLOBClient
from .weather_only_contracts import (
    DAILY_HIGH,
    DAILY_LOW,
    SOURCE_NWS_WRH,
    CompiledWeatherEvent,
    compile_weather_event,
)
from .weather_only_discovery import WeatherOnlyDiscovery
from .weather_only_incremental import (
    WeatherIncrementalLatencyMeasurement,
    measure_weather_incremental_evaluation,
)
from .weather_only_result_lag import evaluate_wrh_official_result_lag_for_w7
from .weather_only_rules import apply_rule_authority, compile_temperature_rule_authority
from .weather_only_runtime import WEATHER_SHADOW_RUNTIME_VERSION
from .weather_only_wrh import WRHSourceError, WRHSourceSnapshot
from .weather_only_wrh_client import NWSWRHLiveClient


WEATHER_W7_RECORDER_VERSION = "weather_w7_recorder_v1_attach_only_30s_exact_clob_wrh_ro_containment"
SAMPLE_INTERVAL_SECONDS = 30.0
SAMPLE_COUNT = 91
SOURCE_POLL_EVERY_SAMPLES = 2
SOURCE_POLL_MAX_ATTEMPTS = 3
SOURCE_POLL_RETRY_DELAY_SECONDS = 0.25
MAX_RUNTIME_REPORT_AGE_SECONDS = 90.0
MAX_RUNTIME_REPORT_BYTES = 2 * 1024 * 1024
_RETRYABLE_WRH_SOURCE_CODES = frozenset({
    "WRH_LIVE_SHELL_HTTP_ERROR",
    "WRH_LIVE_VIEWER_HTTP_ERROR",
    "WRH_LIVE_API_KEY_HTTP_ERROR",
    "WRH_LIVE_BACKEND_HTTP_ERROR",
})
_SHA40_RE = re.compile(r"^[0-9a-f]{40}$")


class WeatherW7RecorderError(RuntimeError):
    def __init__(self, code: str):
        self.code = str(code)
        super().__init__(self.code)


def _finite(value: object, code: str) -> float:
    if value is None or isinstance(value, bool) or not isinstance(value, (int, float)):
        raise WeatherW7RecorderError(code)
    out = float(value)
    if not math.isfinite(out) or out < 0.0:
        raise WeatherW7RecorderError(code)
    return out


def _integer(value: object, code: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise WeatherW7RecorderError(code)
    return value


def _flag(value: object, expected: bool, code: str) -> None:
    if type(value) is not bool or value is not expected:
        raise WeatherW7RecorderError(code)


@dataclass(frozen=True, slots=True)
class WeatherW7RuntimeObservation:
    version: str
    report_started_at: float
    report_finished_at: float
    observed_at: float
    weather_event_count: int
    weather_market_count: int
    cycle_ok: bool
    exact_clob_required: bool
    financial_authority: bool = field(init=False, default=False)
    financial_delivery: bool = field(init=False, default=False)
    automatic_order_placement: bool = field(init=False, default=False)


def parse_weather_w7_runtime_report(
    payload: object,
    *,
    observed_at: float,
    max_age_seconds: float = MAX_RUNTIME_REPORT_AGE_SECONDS,
) -> WeatherW7RuntimeObservation:
    now = _finite(observed_at, "W7_RECORDER_OBSERVED_AT_INVALID")
    max_age = _finite(max_age_seconds, "W7_RECORDER_REPORT_AGE_POLICY_INVALID")
    if max_age <= 0.0 or not isinstance(payload, dict):
        raise WeatherW7RecorderError("W7_RECORDER_RUNTIME_REPORT_INVALID")
    if payload.get("version") != WEATHER_SHADOW_RUNTIME_VERSION:
        raise WeatherW7RecorderError("W7_RECORDER_RUNTIME_VERSION_MISMATCH")
    if payload.get("mode") != "SILENT_SHADOW":
        raise WeatherW7RecorderError("W7_RECORDER_RUNTIME_MODE_INVALID")
    for key, expected in (
        ("read_only", True),
        ("financial_authority", False),
        ("financial_delivery", False),
        ("automatic_order_placement", False),
        ("gamma_execution_authority", False),
        ("exact_clob_required_for_recorded_opportunities", True),
        ("market_specific_fee_schedule_required", True),
        ("v2_fd_fee_authority_required", True),
        ("final_live_recheck_required", True),
        ("forecast_probability_authority", False),
        ("source_settlement_trade_authority", False),
    ):
        _flag(payload.get(key), expected, f"W7_RECORDER_RUNTIME_INVARIANT_BROKEN:{key}")
    cycle = payload.get("cycle_ok")
    if type(cycle) is not bool:
        raise WeatherW7RecorderError("W7_RECORDER_RUNTIME_CYCLE_FLAG_INVALID")
    started = _finite(payload.get("started_at"), "W7_RECORDER_RUNTIME_TIME_INVALID")
    finished = _finite(payload.get("finished_at"), "W7_RECORDER_RUNTIME_TIME_INVALID")
    if finished < started or finished > now + 1e-9:
        raise WeatherW7RecorderError("W7_RECORDER_RUNTIME_TIME_ORDER_INVALID")
    if now - finished > max_age + 1e-9:
        raise WeatherW7RecorderError("W7_RECORDER_RUNTIME_REPORT_STALE")

    discovery = payload.get("discovery")
    if not isinstance(discovery, dict):
        raise WeatherW7RecorderError("W7_RECORDER_RUNTIME_DISCOVERY_INVALID")
    events = _integer(discovery.get("unique_event_count"), "W7_RECORDER_RUNTIME_EVENT_COUNT_INVALID")
    markets = _integer(discovery.get("unique_market_count"), "W7_RECORDER_RUNTIME_MARKET_COUNT_INVALID")
    compiler = payload.get("compiler")
    if not isinstance(compiler, dict) or compiler.get("financial_authority_events") != 0:
        raise WeatherW7RecorderError("W7_RECORDER_RUNTIME_COMPILER_AUTHORITY_INVALID")
    opportunities = payload.get("opportunities")
    if not isinstance(opportunities, list):
        raise WeatherW7RecorderError("W7_RECORDER_RUNTIME_OPPORTUNITIES_INVALID")
    for row in opportunities:
        if not isinstance(row, dict):
            raise WeatherW7RecorderError("W7_RECORDER_RUNTIME_OPPORTUNITY_INVALID")
        for key, expected in (
            ("rechecked", True),
            ("financial_authority", False),
            ("financial_delivery", False),
            ("automatic_order_placement", False),
        ):
            _flag(row.get(key), expected, f"W7_RECORDER_RUNTIME_OPPORTUNITY_INVARIANT_BROKEN:{key}")
    return WeatherW7RuntimeObservation(
        version=WEATHER_W7_RECORDER_VERSION,
        report_started_at=started,
        report_finished_at=finished,
        observed_at=now,
        weather_event_count=events,
        weather_market_count=markets,
        cycle_ok=cycle,
        exact_clob_required=True,
    )


def load_weather_w7_runtime_report(
    path: str | Path,
    *,
    observed_at: float | None = None,
) -> WeatherW7RuntimeObservation:
    report_path = Path(path)
    if report_path.is_symlink():
        raise WeatherW7RecorderError("W7_RECORDER_RUNTIME_REPORT_PATH_INVALID")
    try:
        stat = report_path.stat()
    except OSError:
        raise WeatherW7RecorderError("W7_RECORDER_RUNTIME_REPORT_PATH_INVALID") from None
    if not report_path.is_file() or stat.st_size <= 0 or stat.st_size > MAX_RUNTIME_REPORT_BYTES:
        raise WeatherW7RecorderError("W7_RECORDER_RUNTIME_REPORT_SIZE_INVALID")
    try:
        raw = report_path.read_text(encoding="utf-8")
        payload = json.loads(raw)
    except (OSError, UnicodeError, json.JSONDecodeError):
        raise WeatherW7RecorderError("W7_RECORDER_RUNTIME_REPORT_READ_INVALID") from None
    now = time.time() if observed_at is None else observed_at
    return parse_weather_w7_runtime_report(payload, observed_at=now)


def _certified_probe(event: object) -> tuple[dict, CompiledWeatherEvent] | None:
    if not isinstance(event, dict):
        return None
    raw = compile_weather_event(event)
    if raw.source_family != SOURCE_NWS_WRH or raw.family not in {DAILY_HIGH, DAILY_LOW} or raw.unit != "F":
        return None
    authority = compile_temperature_rule_authority(event, raw)
    compiled = apply_rule_authority(raw, authority)
    if (
        not compiled.shadow_supported
        or not compiled.partition_shape_complete
        or not compiled.exactly_one_outcome_proven
        or compiled.financial_authority
        or not authority.rule_semantics_proven
        or not authority.exactly_one_outcome_proven
        or authority.observation_population != "WRH_HOURLY_DATA"
        or authority.precision != "WHOLE_DEGREE_F"
        or compiled.target_date is None
        or not compiled.station_hint
        or not compiled.buckets
        or any(not bucket.trade_open or not bucket.yes_token or not bucket.no_token for bucket in compiled.buckets)
    ):
        return None
    return copy.deepcopy(event), compiled


def select_weather_w7_probe_event(
    events: object,
    *,
    event_id: str | None = None,
    today_utc: object | None = None,
) -> tuple[dict, CompiledWeatherEvent]:
    if not isinstance(events, (tuple, list)):
        raise WeatherW7RecorderError("W7_RECORDER_DISCOVERY_EVENTS_INVALID")
    wanted = None if event_id is None else str(event_id).strip()
    today = today_utc if today_utc is not None else datetime.now(timezone.utc).date()
    candidates: list[tuple[int, str, dict, CompiledWeatherEvent]] = []
    for event in events:
        certified = _certified_probe(event)
        if certified is None:
            continue
        raw_event, compiled = certified
        if wanted and compiled.event_id != wanted:
            continue
        distance = abs((compiled.target_date - today).days)
        candidates.append((distance, compiled.event_id, raw_event, compiled))
    if not candidates:
        code = "W7_RECORDER_REQUESTED_EVENT_NOT_CERTIFIED" if wanted else "W7_RECORDER_NO_CERTIFIED_WRH_PROBE_EVENT"
        raise WeatherW7RecorderError(code)
    candidates.sort(key=lambda row: (row[0], row[1]))
    _, _, event, compiled = candidates[0]
    return event, compiled


async def discover_weather_w7_probe_event(
    *,
    event_id: str | None = None,
    discovery: WeatherOnlyDiscovery | None = None,
) -> tuple[dict, CompiledWeatherEvent]:
    client = discovery or WeatherOnlyDiscovery()
    owned = discovery is None
    try:
        snapshot = await client.discover()
    finally:
        if owned:
            await client.close()
    return select_weather_w7_probe_event(snapshot.events, event_id=event_id)


def _same_process(left: WeatherW7ProcessIdentity, right: WeatherW7ProcessIdentity) -> bool:
    return (
        left.process_id == right.process_id
        and left.boot_id_sha256 == right.boot_id_sha256
        and left.start_time_ticks == right.start_time_ticks
        and left.cmdline_sha256 == right.cmdline_sha256
    )


class WeatherW7RecorderSession:
    def __init__(
        self,
        *,
        release_sha: str,
        scanner_process_id: int,
        database_path: str | Path,
        runtime_report_path: str | Path,
        probe_event: dict,
        clob: WeatherCLOBClient | None = None,
        wrh: NWSWRHLiveClient | None = None,
        proc_root: str | Path = "/proc",
        wall_clock=time.time,
    ) -> None:
        release = str(release_sha or "").strip().lower()
        if not _SHA40_RE.fullmatch(release):
            raise WeatherW7RecorderError("W7_RECORDER_RELEASE_SHA_INVALID")
        if isinstance(scanner_process_id, bool) or not isinstance(scanner_process_id, int) or scanner_process_id <= 0:
            raise WeatherW7RecorderError("W7_RECORDER_PROCESS_ID_INVALID")
        if not callable(wall_clock):
            raise WeatherW7RecorderError("W7_RECORDER_CLOCK_INVALID")
        certified = _certified_probe(probe_event)
        if certified is None:
            raise WeatherW7RecorderError("W7_RECORDER_PROBE_EVENT_NOT_CERTIFIED")
        event, compiled = certified
        self.release_sha = release
        self.scanner_process_id = scanner_process_id
        self.database_path = Path(database_path)
        self.runtime_report_path = Path(runtime_report_path)
        self.event = event
        self.compiled = compiled
        self.proc_root = Path(proc_root)
        self.wall_clock = wall_clock
        self.clob = clob or WeatherCLOBClient()
        self._owns_clob = clob is None
        self.wrh = wrh or NWSWRHLiveClient()
        self.before_database = read_weather_w7_database_snapshot(
            self.database_path,
            captured_at=self.wall_clock(),
        )
        self.before_process = read_linux_process_identity(
            scanner_process_id,
            proc_root=self.proc_root,
        )
        self.read_only_surface = attest_weather_w7_read_only_surface()
        self.samples: list[WeatherW7Sample] = []
        self.incremental_measurements: list[WeatherIncrementalLatencyMeasurement] = []
        self.source_measurements: list[WeatherW7SourceUpdateLatencyMeasurement] = []
        self.source_poll_failure_codes: list[str] = []
        self.previous_wrh_snapshot: WRHSourceSnapshot | None = None
        self._sample_index = 0

    async def close(self) -> None:
        if self._owns_clob:
            await self.clob.close()

    async def _fetch_source_snapshot(self):
        for attempt in range(SOURCE_POLL_MAX_ATTEMPTS):
            try:
                result = await asyncio.to_thread(
                    self.wrh.fetch_snapshot,
                    station=str(self.compiled.station_hint),
                    target_date=self.compiled.target_date,
                )
                return result, True
            except WRHSourceError as exc:
                retryable = exc.code in _RETRYABLE_WRH_SOURCE_CODES
                if retryable and attempt + 1 < SOURCE_POLL_MAX_ATTEMPTS:
                    await asyncio.sleep(SOURCE_POLL_RETRY_DELAY_SECONDS * (attempt + 1))
                    continue
                self.source_poll_failure_codes.append(exc.code)
                return None, False
        raise WeatherW7RecorderError("W7_RECORDER_SOURCE_RETRY_STATE_INVALID")

    async def _poll_source_update(self) -> tuple[WeatherW7SourceUpdateLatencyMeasurement | None, bool]:
        result, source_poll_ok = await self._fetch_source_snapshot()
        if not source_poll_ok:
            return None, False
        if result is None:
            raise WeatherW7RecorderError("W7_RECORDER_SOURCE_RESULT_INVALID")
        current = result.snapshot
        previous = self.previous_wrh_snapshot
        self.previous_wrh_snapshot = current
        if previous is None:
            return None, True
        try:
            trigger = build_wrh_source_update_trigger(previous, current, compiled=self.compiled)
        except WeatherW7SourceLatencyError as exc:
            if exc.code in {"W7_SOURCE_PAYLOAD_UNCHANGED", "W7_SOURCE_UPDATE_NOT_EVENT_RELEVANT"}:
                return None, True
            raise WeatherW7RecorderError(f"W7_RECORDER_SOURCE_TRIGGER_INVALID:{exc.code}") from exc
        if trigger.change_kind != CHANGE_FINALITY_TRANSITION:
            return None, True

        async def evaluator(bound_trigger):
            return await evaluate_wrh_official_result_lag_for_w7(
                bound_trigger,
                self.event,
                previous,
                current,
                clob=self.clob,
            )

        try:
            return await measure_w7_source_update_confirmation(trigger, evaluator), True
        except WeatherW7SourceLatencyError as exc:
            if exc.code == "W7_SOURCE_CANDIDATE_NOT_CONFIRMED":
                return None, True
            raise WeatherW7RecorderError(f"W7_RECORDER_SOURCE_MEASUREMENT_INVALID:{exc.code}") from exc

    async def record_sample(self, *, poll_source: bool | None = None) -> WeatherW7Sample:
        should_poll = (
            self._sample_index % SOURCE_POLL_EVERY_SAMPLES == 0
            if poll_source is None
            else bool(poll_source)
        )
        _, incremental = await measure_weather_incremental_evaluation(
            self.event,
            clob=self.clob,
        )
        source = None
        source_poll_ok = True
        if should_poll:
            source, source_poll_ok = await self._poll_source_update()

        current_process = read_linux_process_identity(
            self.scanner_process_id,
            proc_root=self.proc_root,
        )
        host = read_linux_proc_metrics(
            self.proc_root,
            process_id=self.scanner_process_id,
        )
        observed_at = self.wall_clock()
        runtime = load_weather_w7_runtime_report(
            self.runtime_report_path,
            observed_at=observed_at,
        )
        cycle_ok = runtime.cycle_ok and _same_process(self.before_process, current_process) and source_poll_ok
        sample = WeatherW7Sample(
            observed_at=observed_at,
            cycle_ok=cycle_ok,
            process_rss_bytes=host.process_rss_bytes,
            swap_used_bytes=host.swap_used_bytes,
            host_mem_available_bytes=host.host_mem_available_bytes,
            incremental_evaluation_seconds=incremental.incremental_evaluation_seconds,
            incremental_evaluation_evidence_sha256=incremental.measurement_evidence_sha256,
            source_update_confirmation_seconds=(
                None if source is None else source.source_update_confirmation_seconds
            ),
            source_update_evidence_sha256=(
                None if source is None else source.measurement_evidence_sha256
            ),
            weather_event_count=runtime.weather_event_count,
            non_weather_materialized_count=0,
            exact_clob_required_for_candidates=runtime.exact_clob_required,
            financial_authority=False,
            financial_delivery=False,
            automatic_order_placement=False,
        )
        if self.samples and sample.observed_at <= self.samples[-1].observed_at:
            raise WeatherW7RecorderError("W7_RECORDER_SAMPLE_TIME_NOT_INCREASING")
        if incremental.evaluation_finished_at > sample.observed_at + 1e-9:
            raise WeatherW7RecorderError("W7_RECORDER_INCREMENTAL_TIME_AFTER_SAMPLE")
        if source is not None and source.evaluation_finished_at > sample.observed_at + 1e-9:
            raise WeatherW7RecorderError("W7_RECORDER_SOURCE_TIME_AFTER_SAMPLE")
        self.incremental_measurements.append(incremental)
        if source is not None:
            self.source_measurements.append(source)
        self.samples.append(sample)
        self._sample_index += 1
        return sample

    def finalize(self) -> WeatherW7AcceptanceBundle:
        if not self.samples:
            raise WeatherW7RecorderError("W7_RECORDER_NO_SAMPLES")
        after_database = read_weather_w7_database_snapshot(
            self.database_path,
            captured_at=self.wall_clock(),
        )
        after_process = read_linux_process_identity(
            self.scanner_process_id,
            proc_root=self.proc_root,
        )
        containment = build_weather_w7_containment_manifest(
            before_database=self.before_database,
            after_database=after_database,
            before_process=self.before_process,
            after_process=after_process,
            read_only_surface=self.read_only_surface,
        )
        counters = derive_weather_w7_containment_counters(containment)
        run = WeatherW7RunEvidence(
            release_sha=self.release_sha,
            samples=tuple(self.samples),
            **counters,
        )
        measurements = build_weather_w7_measurement_manifest(
            incremental_measurements=tuple(self.incremental_measurements),
            source_update_measurements=tuple(self.source_measurements),
        )
        created_at = max(self.wall_clock(), self.samples[-1].observed_at)
        envelope = build_weather_w7_evidence_envelope(
            run,
            measurement_manifest=measurements,
            created_at=created_at,
        )
        return build_weather_w7_acceptance_bundle(
            w7_evidence=envelope,
            containment_manifest=containment,
        )

    async def run_frozen_window(self) -> WeatherW7AcceptanceBundle:
        start = time.monotonic()
        for index in range(SAMPLE_COUNT):
            if index:
                target = start + index * SAMPLE_INTERVAL_SECONDS
                await asyncio.sleep(max(0.0, target - time.monotonic()))
            await self.record_sample()
        return self.finalize()


def atomic_write_weather_w7_bundle(path: str | Path, bundle: WeatherW7AcceptanceBundle) -> None:
    output = Path(path)
    if output.is_symlink():
        raise WeatherW7RecorderError("W7_RECORDER_OUTPUT_PATH_INVALID")
    try:
        output.parent.mkdir(parents=True, exist_ok=True)
    except OSError:
        raise WeatherW7RecorderError("W7_RECORDER_OUTPUT_DIRECTORY_INVALID") from None
    payload = dump_weather_w7_acceptance_bundle_json(bundle) + "\n"
    temporary_name = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=output.parent,
            prefix=output.name + ".",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary_name = handle.name
            os.chmod(temporary_name, 0o600)
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_name, output)
        temporary_name = None
    except OSError:
        raise WeatherW7RecorderError("W7_RECORDER_OUTPUT_WRITE_FAILED") from None
    finally:
        if temporary_name is not None:
            try:
                os.unlink(temporary_name)
            except OSError:
                pass


async def _run_cli(args) -> int:
    event, _ = await discover_weather_w7_probe_event(event_id=args.event_id)
    session = WeatherW7RecorderSession(
        release_sha=args.release_sha,
        scanner_process_id=args.scanner_pid,
        database_path=args.database,
        runtime_report_path=args.runtime_report,
        probe_event=event,
    )
    try:
        bundle = await session.run_frozen_window()
    finally:
        await session.close()
    atomic_write_weather_w7_bundle(args.output, bundle)
    from .weather_only_acceptance_bundle import validate_weather_w7_acceptance_bundle

    report = validate_weather_w7_acceptance_bundle(bundle, expected_release_sha=args.release_sha)
    print(json.dumps(report.as_dict(), sort_keys=True, indent=2))
    return 0 if report.passed else 2


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--release-sha", required=True)
    parser.add_argument("--scanner-pid", required=True, type=int)
    parser.add_argument("--database", required=True, type=Path)
    parser.add_argument("--runtime-report", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--event-id")
    args = parser.parse_args()
    raise SystemExit(asyncio.run(_run_cli(args)))


if __name__ == "__main__":
    main()
