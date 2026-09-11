from __future__ import annotations

from dataclasses import replace

import pytest

from polymarket_scanner.weather_only_acceptance import (
    MAX_PROCESS_RSS_BYTES,
    MIN_HOST_MEM_AVAILABLE_BYTES,
    WEATHER_W7_POLICY_ID,
    WeatherW7AcceptanceError,
    WeatherW7Policy,
    WeatherW7RunEvidence,
    WeatherW7Sample,
    evaluate_weather_w7_acceptance,
)


SHA = "a" * 40
START = 1_800_000_000.0


def _sample(index: int, **overrides) -> WeatherW7Sample:
    values = dict(
        observed_at=START + index * 30.0,
        cycle_ok=True,
        process_rss_bytes=200 * 1024 * 1024,
        swap_used_bytes=0,
        host_mem_available_bytes=200 * 1024 * 1024,
        incremental_evaluation_seconds=1.0,
        source_update_confirmation_seconds=4.0 if index == 45 else None,
        weather_event_count=350,
        non_weather_materialized_count=0,
        exact_clob_required_for_candidates=True,
        financial_authority=False,
        financial_delivery=False,
        automatic_order_placement=False,
    )
    values.update(overrides)
    return WeatherW7Sample(**values)


def _evidence(*, samples=None, **overrides) -> WeatherW7RunEvidence:
    values = dict(
        release_sha=SHA,
        samples=tuple(_sample(i) for i in range(91)) if samples is None else tuple(samples),
        telegram_outbox_before=436,
        telegram_outbox_after=436,
        detector_promotions=0,
        order_attempts=0,
        actual_orders_placed=0,
        actual_fills_recorded=0,
        service_restart_count=0,
    )
    values.update(overrides)
    return WeatherW7RunEvidence(**values)


def test_frozen_w7_policy_is_deterministic_and_research_only_acceptance_can_pass():
    policy = WeatherW7Policy()
    assert policy.policy_id == WEATHER_W7_POLICY_ID
    assert len(policy.policy_sha256) == 64
    assert policy.policy_sha256 == WeatherW7Policy().policy_sha256
    report = evaluate_weather_w7_acceptance(_evidence(), policy=policy)
    assert report.passed is True
    assert report.sample_count == 91
    assert report.duration_seconds == pytest.approx(2700.0)
    assert report.max_sample_gap_seconds == pytest.approx(30.0)
    assert report.source_update_sample_count == 1
    assert report.max_source_update_confirmation_seconds == pytest.approx(4.0)
    assert report.financial_authority is False
    assert report.financial_delivery is False
    assert report.detector_promotion_authority is False
    assert report.automatic_order_placement is False


def test_policy_thresholds_cannot_be_relaxed_by_caller():
    with pytest.raises(WeatherW7AcceptanceError) as raised:
        WeatherW7Policy(max_process_rss_bytes=MAX_PROCESS_RSS_BYTES + 1)
    assert raised.value.code == "W7_RESOURCE_POLICY_DRIFT"


def test_duration_sample_gap_and_source_update_evidence_are_mandatory():
    short = tuple(_sample(i) for i in range(80))
    report = evaluate_weather_w7_acceptance(_evidence(samples=short))
    assert report.passed is False
    assert any(reason.startswith("DURATION_BELOW_MIN") for reason in report.reasons)

    gapped = list(_evidence().samples)
    gapped[50] = replace(gapped[50], observed_at=gapped[49].observed_at + 61.0)
    for index in range(51, len(gapped)):
        gapped[index] = replace(gapped[index], observed_at=gapped[index - 1].observed_at + 30.0)
    gap_report = evaluate_weather_w7_acceptance(_evidence(samples=gapped))
    assert gap_report.passed is False
    assert any(reason.startswith("SAMPLE_GAP_EXCEEDED") for reason in gap_report.reasons)

    no_updates = tuple(replace(_sample(i), source_update_confirmation_seconds=None) for i in range(91))
    no_update_report = evaluate_weather_w7_acceptance(_evidence(samples=no_updates))
    assert no_update_report.passed is False
    assert "SOURCE_UPDATE_SAMPLE_COUNT_BELOW_MIN:0" in no_update_report.reasons


def test_resource_and_latency_breaches_fail_acceptance():
    samples = list(_evidence().samples)
    samples[10] = replace(samples[10], process_rss_bytes=MAX_PROCESS_RSS_BYTES + 1)
    samples[20] = replace(samples[20], swap_used_bytes=4096)
    samples[30] = replace(samples[30], host_mem_available_bytes=MIN_HOST_MEM_AVAILABLE_BYTES - 1)
    samples[40] = replace(samples[40], incremental_evaluation_seconds=2.01)
    samples[45] = replace(samples[45], source_update_confirmation_seconds=5.01)
    report = evaluate_weather_w7_acceptance(_evidence(samples=samples))
    assert report.passed is False
    assert any(reason.startswith("PROCESS_RSS_EXCEEDED") for reason in report.reasons)
    assert any(reason.startswith("SWAP_USED") for reason in report.reasons)
    assert any(reason.startswith("HOST_MEM_AVAILABLE_BELOW_MIN") for reason in report.reasons)
    assert any(reason.startswith("INCREMENTAL_EVAL_LATENCY_EXCEEDED") for reason in report.reasons)
    assert any(reason.startswith("SOURCE_UPDATE_LATENCY_EXCEEDED") for reason in report.reasons)


def test_containment_is_absolute_even_if_performance_is_green():
    samples = list(_evidence().samples)
    samples[5] = replace(samples[5], financial_delivery=True)
    report = evaluate_weather_w7_acceptance(
        _evidence(
            samples=samples,
            telegram_outbox_after=437,
            detector_promotions=1,
            order_attempts=1,
            actual_orders_placed=1,
            actual_fills_recorded=1,
            service_restart_count=1,
        )
    )
    assert report.passed is False
    assert "FINANCIAL_DELIVERY_ENABLED:5" in report.reasons
    assert "TELEGRAM_OUTBOX_CHANGED" in report.reasons
    assert "DETECTOR_PROMOTIONS:1" in report.reasons
    assert "REAL_ORDER_OR_FILL_ACTIVITY_DETECTED" in report.reasons
    assert "SERVICE_RESTARTS:1" in report.reasons
    assert report.financial_authority is False


def test_non_weather_materialization_or_lost_exact_clob_requirement_fails_closed():
    samples = list(_evidence().samples)
    samples[2] = replace(samples[2], non_weather_materialized_count=1)
    samples[3] = replace(samples[3], exact_clob_required_for_candidates=False)
    report = evaluate_weather_w7_acceptance(_evidence(samples=samples))
    assert report.passed is False
    assert "NON_WEATHER_MATERIALIZED:2:1" in report.reasons
    assert "EXACT_CLOB_INVARIANT_LOST:3" in report.reasons


def test_boolean_and_numeric_coercion_are_rejected():
    with pytest.raises(WeatherW7AcceptanceError):
        _sample(0, cycle_ok=1)
    with pytest.raises(WeatherW7AcceptanceError):
        _sample(0, process_rss_bytes=True)
    with pytest.raises(WeatherW7AcceptanceError):
        _sample(0, incremental_evaluation_seconds=float("nan"))
