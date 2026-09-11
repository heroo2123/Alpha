from __future__ import annotations

import asyncio
from copy import deepcopy
from dataclasses import replace

import pytest

from polymarket_scanner.models import Book
from polymarket_scanner.weather_only_acceptance_latency import (
    CHANGE_FINALITY_TRANSITION,
    CHANGE_TARGET_STATE,
    WeatherW7SourceLatencyError,
    build_w7_confirmed_shadow_candidate,
    build_wrh_source_update_trigger,
    measure_w7_source_update_confirmation,
)
from polymarket_scanner.weather_only_clob import WeatherExecutionSnapshot, WeatherMarketParameters
from polymarket_scanner.weather_only_contracts import compile_weather_event
from polymarket_scanner.weather_only_rules import apply_rule_authority, compile_temperature_rule_authority
from polymarket_scanner.weather_only_wrh import parse_synoptic_wrh_hourly_snapshot
from test_weather_only_rules import _nws_event
from test_weather_only_wrh import END, START, TARGET, _payload


BASE_RECEIVED = 1_789_160_400.0


def _compiled():
    event = _nws_event(high=True, hourly=True)
    raw = compile_weather_event(event)
    authority = compile_temperature_rule_authority(event, raw)
    return apply_rule_authority(raw, authority)


def _drop_following(payload: dict) -> dict:
    value = deepcopy(payload)
    observations = value["STATION"][0]["OBSERVATIONS"]
    for series in (
        "date_time",
        "air_temp_set_1",
        "metar_set_1",
        "sea_level_pressure_set_1",
    ):
        observations[series].pop()
    return value


def _snapshot(payload: dict, received_at: float):
    return parse_synoptic_wrh_hourly_snapshot(
        payload,
        station="KLGA",
        target_date=TARGET,
        query_start_date=START,
        query_end_date=END,
        received_at=received_at,
    )


def _execution(compiled, *, started=BASE_RECEIVED + 0.2, finished=BASE_RECEIVED + 0.8):
    books = {}
    parameters = {}
    for bucket in compiled.buckets:
        books[bucket.yes_token] = Book(
            token_id=bucket.yes_token,
            bids=[(0.20, 5.0)],
            asks=[(0.21, 5.0)],
            received_at=finished,
            source="clob_exact_rest_v2",
        )
        books[bucket.no_token] = Book(
            token_id=bucket.no_token,
            bids=[(0.78, 5.0)],
            asks=[(0.79, 5.0)],
            received_at=finished,
            source="clob_exact_rest_v2",
        )
        parameters[bucket.condition_id] = WeatherMarketParameters(
            condition_id=bucket.condition_id,
            token_outcomes=((bucket.yes_token, "Yes"), (bucket.no_token, "No")),
            minimum_order_size=1.0,
            minimum_tick_size=0.01,
            fee_rate=0.0,
            fee_exponent=0,
            taker_only=None,
            maker_base_fee_bps=0,
            taker_base_fee_bps=0,
            rfq_enabled=False,
            taker_delay_enabled=False,
            received_at=finished,
        )
    return WeatherExecutionSnapshot(
        version="weather_clob_v2_exact_books_dynamic_fee_exponent_read_only",
        event_id=compiled.event_id,
        books=books,
        parameters=parameters,
        started_at=started,
        finished_at=finished,
        exact_clob=True,
        financial_authority=False,
    )


def _target_change_trigger():
    compiled = _compiled()
    before_payload = _drop_following(_payload())
    after_payload = deepcopy(before_payload)
    after_payload["STATION"][0]["OBSERVATIONS"]["air_temp_set_1"][2] = 83.4
    before = _snapshot(before_payload, BASE_RECEIVED - 1.0)
    after = _snapshot(after_payload, BASE_RECEIVED)
    return compiled, before, after, build_wrh_source_update_trigger(before, after, compiled=compiled)


def test_target_state_change_becomes_digest_bound_source_trigger():
    compiled, before, after, trigger = _target_change_trigger()
    assert trigger.event_id == compiled.event_id
    assert trigger.station == "KLGA"
    assert trigger.target_date == TARGET
    assert trigger.change_kind == CHANGE_TARGET_STATE
    assert trigger.previous_snapshot_sha256 == before.evidence_sha256
    assert trigger.current_snapshot_sha256 == after.evidence_sha256
    assert trigger.previous_target_state_sha256 != trigger.current_target_state_sha256
    assert set(trigger.condition_ids) == {bucket.condition_id for bucket in compiled.buckets}
    assert set(trigger.token_ids) == {
        token for bucket in compiled.buckets for token in (bucket.yes_token, bucket.no_token)
    }
    assert len(trigger.trigger_evidence_sha256) == 64
    assert trigger.financial_authority is False
    assert trigger.financial_delivery is False
    assert trigger.automatic_order_placement is False


def test_first_following_row_is_material_finality_transition_even_when_target_state_is_unchanged():
    compiled = _compiled()
    before = _snapshot(_drop_following(_payload()), BASE_RECEIVED - 1.0)
    after = _snapshot(_payload(), BASE_RECEIVED)
    trigger = build_wrh_source_update_trigger(before, after, compiled=compiled)
    assert trigger.change_kind == CHANGE_FINALITY_TRANSITION
    assert trigger.previous_target_state_sha256 == trigger.current_target_state_sha256
    assert before.first_following_row is None
    assert after.first_following_row is not None


def test_unrelated_excluded_source_row_drift_cannot_masquerade_as_event_update():
    compiled = _compiled()
    before_payload = _drop_following(_payload())
    after_payload = deepcopy(before_payload)
    # This KJFK SPECI row is excluded by the pinned WRH Hourly predicate.
    after_payload["STATION"][0]["OBSERVATIONS"]["air_temp_set_1"][3] = 97.0
    before = _snapshot(before_payload, BASE_RECEIVED - 1.0)
    after = _snapshot(after_payload, BASE_RECEIVED)
    assert before.source_payload_sha256 != after.source_payload_sha256
    assert before.target_state_sha256 == after.target_state_sha256
    with pytest.raises(WeatherW7SourceLatencyError) as raised:
        build_wrh_source_update_trigger(before, after, compiled=compiled)
    assert raised.value.code == "W7_SOURCE_UPDATE_NOT_EVENT_RELEVANT"


def test_raw_compiler_inventory_without_rule_upgrade_cannot_create_w7_source_trigger():
    event = _nws_event(high=True, hourly=True)
    compiled = compile_weather_event(event)
    before_payload = _drop_following(_payload())
    after_payload = deepcopy(before_payload)
    after_payload["STATION"][0]["OBSERVATIONS"]["air_temp_set_1"][2] = 83.4
    before = _snapshot(before_payload, BASE_RECEIVED - 1.0)
    after = _snapshot(after_payload, BASE_RECEIVED)
    assert compiled.exactly_one_outcome_proven is False
    with pytest.raises(WeatherW7SourceLatencyError) as raised:
        build_wrh_source_update_trigger(before, after, compiled=compiled)
    assert raised.value.code == "W7_SOURCE_CONTRACT_RULES_UNPROVEN"


def test_candidate_confirmation_requires_current_source_binding_and_full_exact_clob_identity():
    compiled, _before, after, trigger = _target_change_trigger()
    execution = _execution(compiled)
    confirmation = build_w7_confirmed_shadow_candidate(
        trigger,
        execution,
        candidate_evidence_sha256="c" * 64,
        candidate_source_snapshot_sha256=after.evidence_sha256,
    )
    assert confirmation.candidate_confirmed is True
    assert confirmation.exact_clob is True
    assert len(confirmation.exact_clob_evidence_sha256) == 64
    assert len(confirmation.confirmation_evidence_sha256) == 64
    assert confirmation.financial_authority is False

    with pytest.raises(WeatherW7SourceLatencyError) as source_error:
        build_w7_confirmed_shadow_candidate(
            trigger,
            execution,
            candidate_evidence_sha256="c" * 64,
            candidate_source_snapshot_sha256="d" * 64,
        )
    assert source_error.value.code == "W7_SOURCE_CANDIDATE_NOT_BOUND_TO_CURRENT_SOURCE"

    missing_book = replace(execution, books=dict(list(execution.books.items())[1:]))
    with pytest.raises(WeatherW7SourceLatencyError) as book_error:
        build_w7_confirmed_shadow_candidate(
            trigger,
            missing_book,
            candidate_evidence_sha256="c" * 64,
            candidate_source_snapshot_sha256=after.evidence_sha256,
        )
    assert book_error.value.code == "W7_SOURCE_CLOB_TOKEN_SET_MISMATCH"

    missing_condition = replace(execution, parameters=dict(list(execution.parameters.items())[1:]))
    with pytest.raises(WeatherW7SourceLatencyError) as condition_error:
        build_w7_confirmed_shadow_candidate(
            trigger,
            missing_condition,
            candidate_evidence_sha256="c" * 64,
            candidate_source_snapshot_sha256=after.evidence_sha256,
        )
    assert condition_error.value.code == "W7_SOURCE_CLOB_CONDITION_SET_MISMATCH"


def test_measurement_counts_only_exact_confirmed_candidate_and_preserves_both_clock_domains():
    compiled, _before, after, trigger = _target_change_trigger()
    confirmation = build_w7_confirmed_shadow_candidate(
        trigger,
        _execution(compiled),
        candidate_evidence_sha256="c" * 64,
        candidate_source_snapshot_sha256=after.evidence_sha256,
    )

    async def evaluator(seen):
        assert seen == trigger
        return confirmation

    wall_values = iter((BASE_RECEIVED + 0.20, BASE_RECEIVED + 1.50))
    monotonic_values = iter((10.0, 11.0))
    measured = asyncio.run(measure_w7_source_update_confirmation(
        trigger,
        evaluator,
        wall_clock=lambda: next(wall_values),
        monotonic_clock=lambda: next(monotonic_values),
    ))
    assert measured.incremental_evaluation_seconds == pytest.approx(1.0)
    assert measured.source_update_confirmation_seconds == pytest.approx(1.5)
    assert measured.w7_latency_fields() == {
        "incremental_evaluation_seconds": pytest.approx(1.0),
        "source_update_confirmation_seconds": pytest.approx(1.5),
    }
    assert len(measured.measurement_evidence_sha256) == 64
    assert measured.financial_authority is False


def test_no_candidate_produces_no_w7_latency_sample():
    _compiled_event, _before, _after, trigger = _target_change_trigger()

    async def evaluator(_trigger):
        return None

    wall_values = iter((BASE_RECEIVED + 0.1, BASE_RECEIVED + 0.2))
    monotonic_values = iter((1.0, 1.1))
    with pytest.raises(WeatherW7SourceLatencyError) as raised:
        asyncio.run(measure_w7_source_update_confirmation(
            trigger,
            evaluator,
            wall_clock=lambda: next(wall_values),
            monotonic_clock=lambda: next(monotonic_values),
        ))
    assert raised.value.code == "W7_SOURCE_CANDIDATE_NOT_CONFIRMED"


def test_trigger_or_confirmation_tampering_and_clock_inconsistency_fail_closed():
    compiled, _before, after, trigger = _target_change_trigger()
    confirmation = build_w7_confirmed_shadow_candidate(
        trigger,
        _execution(compiled),
        candidate_evidence_sha256="c" * 64,
        candidate_source_snapshot_sha256=after.evidence_sha256,
    )

    tampered_trigger = replace(trigger, source_received_at=trigger.source_received_at - 1.0)
    async def good_eval(_trigger):
        return confirmation
    with pytest.raises(WeatherW7SourceLatencyError) as trigger_error:
        asyncio.run(measure_w7_source_update_confirmation(tampered_trigger, good_eval))
    assert trigger_error.value.code == "W7_SOURCE_TRIGGER_DIGEST_MISMATCH"

    tampered_confirmation = replace(confirmation, candidate_evidence_sha256="e" * 64)
    async def bad_eval(_trigger):
        return tampered_confirmation
    wall_values = iter((BASE_RECEIVED + 0.1, BASE_RECEIVED + 0.3))
    monotonic_values = iter((1.0, 1.1))
    with pytest.raises(WeatherW7SourceLatencyError) as candidate_error:
        asyncio.run(measure_w7_source_update_confirmation(
            trigger,
            bad_eval,
            wall_clock=lambda: next(wall_values),
            monotonic_clock=lambda: next(monotonic_values),
        ))
    assert candidate_error.value.code == "W7_SOURCE_CONFIRMATION_DIGEST_MISMATCH"

    async def good_eval_again(_trigger):
        return confirmation
    wall_values = iter((BASE_RECEIVED + 0.1, BASE_RECEIVED + 0.2))
    monotonic_values = iter((1.0, 1.5))
    with pytest.raises(WeatherW7SourceLatencyError) as clock_error:
        asyncio.run(measure_w7_source_update_confirmation(
            trigger,
            good_eval_again,
            wall_clock=lambda: next(wall_values),
            monotonic_clock=lambda: next(monotonic_values),
        ))
    assert clock_error.value.code == "W7_SOURCE_MEASUREMENT_CLOCK_DOMAINS_INCONSISTENT"
