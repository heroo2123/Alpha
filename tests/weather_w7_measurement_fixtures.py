from __future__ import annotations

import hashlib
from dataclasses import replace

from polymarket_scanner.weather_only_acceptance import WeatherW7RunEvidence, WeatherW7Sample
from polymarket_scanner.weather_only_acceptance_latency import (
    WEATHER_W7_SOURCE_LATENCY_VERSION,
    WeatherW7SourceUpdateLatencyMeasurement,
    _measurement_digest_payload as _source_payload,
    _sha as _source_sha,
)
from polymarket_scanner.weather_only_acceptance_measurements import (
    WeatherW7MeasurementManifest,
    build_weather_w7_measurement_manifest,
)
from polymarket_scanner.weather_only_incremental import (
    WEATHER_INCREMENTAL_VERSION,
    WeatherIncrementalLatencyMeasurement,
    _measurement_payload as _incremental_payload,
    _sha as _incremental_sha,
)


def _hex(label: str) -> str:
    return hashlib.sha256(label.encode("utf-8")).hexdigest()


def build_w7_measurement_fixture(
    *,
    release_sha: str,
    start: float,
    sample_count: int = 91,
    source_index: int | None = 45,
    incremental_seconds: float = 1.0,
    source_seconds: float = 4.0,
    weather_event_count: int = 350,
    telegram_outbox_before: int = 436,
    telegram_outbox_after: int | None = None,
) -> tuple[WeatherW7RunEvidence, WeatherW7MeasurementManifest]:
    incremental_rows: list[WeatherIncrementalLatencyMeasurement] = []
    source_rows: list[WeatherW7SourceUpdateLatencyMeasurement] = []
    samples: list[WeatherW7Sample] = []

    for index in range(sample_count):
        observed_at = start + index * 30.0
        incremental_finished = observed_at - 0.10
        incremental_started = incremental_finished - incremental_seconds
        inc_shell = WeatherIncrementalLatencyMeasurement(
            version=WEATHER_INCREMENTAL_VERSION,
            receipt_evidence_sha256=_hex(f"receipt:{index}"),
            evaluation_started_at=incremental_started,
            evaluation_finished_at=incremental_finished,
            incremental_evaluation_seconds=incremental_seconds,
            measurement_evidence_sha256="0" * 64,
        )
        inc = replace(
            inc_shell,
            measurement_evidence_sha256=_incremental_sha(_incremental_payload(inc_shell)),
        )
        incremental_rows.append(inc)

        source_latency = None
        source_sha = None
        if source_index is not None and index == source_index:
            source_finished = observed_at - 0.05
            source_received = source_finished - source_seconds
            source_started = source_finished - incremental_seconds
            src_shell = WeatherW7SourceUpdateLatencyMeasurement(
                version=WEATHER_W7_SOURCE_LATENCY_VERSION,
                trigger_evidence_sha256=_hex(f"trigger:{index}"),
                candidate_confirmation_sha256=_hex(f"candidate:{index}"),
                source_received_at=source_received,
                evaluation_started_at=source_started,
                evaluation_finished_at=source_finished,
                incremental_evaluation_seconds=incremental_seconds,
                source_update_confirmation_seconds=source_seconds,
                measurement_evidence_sha256="0" * 64,
            )
            src = replace(
                src_shell,
                measurement_evidence_sha256=_source_sha(_source_payload(src_shell)),
            )
            source_rows.append(src)
            source_latency = src.source_update_confirmation_seconds
            source_sha = src.measurement_evidence_sha256

        samples.append(WeatherW7Sample(
            observed_at=observed_at,
            cycle_ok=True,
            process_rss_bytes=200 * 1024 * 1024,
            swap_used_bytes=0,
            host_mem_available_bytes=200 * 1024 * 1024,
            incremental_evaluation_seconds=inc.incremental_evaluation_seconds,
            incremental_evaluation_evidence_sha256=inc.measurement_evidence_sha256,
            source_update_confirmation_seconds=source_latency,
            source_update_evidence_sha256=source_sha,
            weather_event_count=weather_event_count,
            non_weather_materialized_count=0,
            exact_clob_required_for_candidates=True,
            financial_authority=False,
            financial_delivery=False,
            automatic_order_placement=False,
        ))

    evidence = WeatherW7RunEvidence(
        release_sha=release_sha,
        samples=tuple(samples),
        telegram_outbox_before=telegram_outbox_before,
        telegram_outbox_after=(
            telegram_outbox_before if telegram_outbox_after is None else telegram_outbox_after
        ),
        detector_promotions=0,
        order_attempts=0,
        actual_orders_placed=0,
        actual_fills_recorded=0,
        service_restart_count=0,
    )
    manifest = build_weather_w7_measurement_manifest(
        incremental_measurements=tuple(incremental_rows),
        source_update_measurements=tuple(source_rows),
    )
    return evidence, manifest
