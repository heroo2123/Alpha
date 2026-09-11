from __future__ import annotations

import asyncio
from dataclasses import replace

import pytest

from polymarket_scanner.models import Book
from polymarket_scanner.weather_only_acceptance_latency import (
    CHANGE_FINALITY_TRANSITION,
    build_wrh_source_update_trigger,
    measure_w7_source_update_confirmation,
)
from polymarket_scanner.weather_only_clob import WeatherExecutionSnapshot, WeatherMarketParameters
from polymarket_scanner.weather_only_contracts import compile_weather_event
from polymarket_scanner.weather_only_result_lag import (
    WeatherResultLagError,
    WeatherResultLagShadowPolicy,
    evaluate_wrh_official_result_lag,
    evaluate_wrh_official_result_lag_for_w7,
)
from polymarket_scanner.weather_only_rules import apply_rule_authority, compile_temperature_rule_authority
from polymarket_scanner.weather_only_wrh import parse_synoptic_wrh_hourly_snapshot
from test_weather_only_rules import _nws_event
from test_weather_only_wrh import END, START, TARGET, _payload


FOLLOWING_EPOCH = 1_789_188_660.0
BEFORE_RECEIVED = FOLLOWING_EPOCH - 60.0
AFTER_RECEIVED = FOLLOWING_EPOCH + 60.0


def _drop_following(payload: dict) -> dict:
    import copy

    value = copy.deepcopy(payload)
    observations = value["STATION"][0]["OBSERVATIONS"]
    for series in ("date_time", "air_temp_set_1", "metar_set_1", "sea_level_pressure_set_1"):
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


def _event_and_compiled(*, high=True):
    event = _nws_event(high=high, hourly=True)
    raw = compile_weather_event(event)
    authority = compile_temperature_rule_authority(event, raw)
    return event, apply_rule_authority(raw, authority)


def _execution(compiled, *, winner_ask: float, started: float, finished: float, fee_rate=0.0):
    books = {}
    parameters = {}
    winner = compiled.buckets[-1]  # fixture final high is 82F, so highest tail wins
    for bucket in compiled.buckets:
        yes_ask = winner_ask if bucket.market_id == winner.market_id else 0.20
        books[bucket.yes_token] = Book(
            token_id=bucket.yes_token,
            bids=[(max(0.01, yes_ask - 0.02), 5.0)],
            asks=[(yes_ask, 5.0)],
            received_at=finished,
            source="clob_exact_rest_v2",
        )
        books[bucket.no_token] = Book(
            token_id=bucket.no_token,
            bids=[(0.75, 5.0)],
            asks=[(0.78, 5.0)],
            received_at=finished,
            source="clob_exact_rest_v2",
        )
        parameters[bucket.condition_id] = WeatherMarketParameters(
            condition_id=bucket.condition_id,
            token_outcomes=((bucket.yes_token, "Yes"), (bucket.no_token, "No")),
            minimum_order_size=1.0,
            minimum_tick_size=0.01,
            fee_rate=fee_rate,
            fee_exponent=1 if fee_rate > 0 else 0,
            taker_only=True if fee_rate > 0 else None,
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


class _FakeCLOB:
    def __init__(self, snapshots):
        self.snapshots = list(snapshots)
        self.calls = 0

    async def exact_event_snapshot(self, compiled):
        assert compiled.exactly_one_outcome_proven is True
        self.calls += 1
        return self.snapshots.pop(0)


def _finality_inputs(*, high=True, first_ask=0.91, second_ask=0.92, fee_rate=0.0):
    event, compiled = _event_and_compiled(high=high)
    before = _snapshot(_drop_following(_payload()), BEFORE_RECEIVED)
    after = _snapshot(_payload(), AFTER_RECEIVED)
    first = _execution(
        compiled,
        winner_ask=first_ask,
        started=AFTER_RECEIVED + 0.10,
        finished=AFTER_RECEIVED + 0.50,
        fee_rate=fee_rate,
    )
    second = _execution(
        compiled,
        winner_ask=second_ask,
        started=AFTER_RECEIVED + 0.60,
        finished=AFTER_RECEIVED + 1.00,
        fee_rate=fee_rate,
    )
    return event, compiled, before, after, first, second


def test_finalized_high_maps_to_winning_tail_and_survives_double_exact_clob_recheck():
    event, compiled, before, after, first, second = _finality_inputs()
    clob = _FakeCLOB((first, second))
    candidate, confirmed = asyncio.run(evaluate_wrh_official_result_lag(
        event,
        before,
        after,
        clob=clob,
    ))
    assert clob.calls == 2
    assert confirmed == second
    assert candidate is not None
    assert candidate.finalized_value_f == 82
    assert candidate.market_id == compiled.buckets[-1].market_id
    assert candidate.token_id == compiled.buckets[-1].yes_token
    assert candidate.executable_ask == pytest.approx(0.92)
    assert candidate.total_cost_per_share == pytest.approx(0.92)
    assert candidate.deterministic_edge_per_share == pytest.approx(0.08)
    assert candidate.minimum_required_edge_per_share == pytest.approx(0.01)
    assert candidate.finality_evidence_sha256
    assert candidate.source_snapshot_sha256 == after.evidence_sha256
    assert len(candidate.first_clob_evidence_sha256) == 64
    assert len(candidate.confirmed_clob_evidence_sha256) == 64
    assert len(candidate.candidate_evidence_sha256) == 64
    assert candidate.deterministic_result is True
    assert candidate.exact_clob_rechecked is True
    assert candidate.financial_authority is False
    assert candidate.financial_delivery is False
    assert candidate.automatic_order_placement is False


def test_second_recheck_can_kill_candidate_without_manufacturing_confirmation():
    event, _compiled, before, after, first, second = _finality_inputs(second_ask=0.995)
    clob = _FakeCLOB((first, second))
    candidate, confirmed = asyncio.run(evaluate_wrh_official_result_lag(
        event,
        before,
        after,
        clob=clob,
    ))
    assert clob.calls == 2
    assert candidate is None
    assert confirmed is None


def test_first_snapshot_must_be_profitable_before_recheck_is_spent():
    event, _compiled, before, after, first, second = _finality_inputs(first_ask=0.995)
    clob = _FakeCLOB((first, second))
    candidate, confirmed = asyncio.run(evaluate_wrh_official_result_lag(
        event,
        before,
        after,
        clob=clob,
    ))
    assert clob.calls == 1
    assert candidate is None
    assert confirmed is None


def test_positive_dynamic_fee_is_included_in_deterministic_edge():
    event, _compiled, before, after, first, second = _finality_inputs(
        first_ask=0.90,
        second_ask=0.90,
        fee_rate=0.05,
    )
    candidate, _ = asyncio.run(evaluate_wrh_official_result_lag(
        event,
        before,
        after,
        clob=_FakeCLOB((first, second)),
    ))
    assert candidate is not None
    assert candidate.conservative_fee_per_share > 0.0
    assert candidate.total_cost_per_share > candidate.executable_ask
    assert candidate.deterministic_edge_per_share < 0.10


def test_causality_rule_and_execution_identity_are_fail_closed():
    event, compiled, before, after, first, second = _finality_inputs()

    early = replace(first, started_at=AFTER_RECEIVED - 0.01)
    with pytest.raises(WeatherResultLagError) as causal:
        asyncio.run(evaluate_wrh_official_result_lag(
            event, before, after, clob=_FakeCLOB((early, second))
        ))
    assert causal.value.code == "RESULT_LAG_CLOB_CAUSALITY_INVALID"

    bad_params = dict(first.parameters)
    first_condition = compiled.buckets[0].condition_id
    first_bucket = compiled.buckets[0]
    second_bucket = compiled.buckets[1]
    original = bad_params[first_condition]
    bad_params[first_condition] = replace(
        original,
        token_outcomes=((second_bucket.yes_token, "Yes"), (second_bucket.no_token, "No")),
    )
    swapped = replace(first, parameters=bad_params)
    with pytest.raises(WeatherResultLagError) as identity:
        asyncio.run(evaluate_wrh_official_result_lag(
            event, before, after, clob=_FakeCLOB((swapped, second))
        ))
    assert identity.value.code == "RESULT_LAG_CLOB_PARAMETER_TOKEN_MISMATCH"

    raw_event = _nws_event(high=True, hourly=False)
    with pytest.raises(WeatherResultLagError):
        asyncio.run(evaluate_wrh_official_result_lag(
            raw_event, before, after, clob=_FakeCLOB((first, second))
        ))


def test_w7_finality_trigger_to_result_lag_candidate_produces_real_causal_latency_sample():
    event, compiled, before, after, first, second = _finality_inputs()
    trigger = build_wrh_source_update_trigger(before, after, compiled=compiled)
    assert trigger.change_kind == CHANGE_FINALITY_TRANSITION
    clob = _FakeCLOB((first, second))

    async def evaluator(seen):
        return await evaluate_wrh_official_result_lag_for_w7(
            seen,
            event,
            before,
            after,
            clob=clob,
        )

    wall = iter((AFTER_RECEIVED + 0.05, AFTER_RECEIVED + 1.20))
    mono = iter((100.0, 101.0))
    measurement = asyncio.run(measure_w7_source_update_confirmation(
        trigger,
        evaluator,
        wall_clock=lambda: next(wall),
        monotonic_clock=lambda: next(mono),
    ))
    assert clob.calls == 2
    assert measurement.incremental_evaluation_seconds == pytest.approx(1.0)
    assert measurement.source_update_confirmation_seconds == pytest.approx(1.2)
    assert measurement.source_update_confirmation_seconds < 5.0
    assert measurement.financial_authority is False


def test_nonfinal_target_update_cannot_use_result_lag_lane_as_fake_source_latency_candidate():
    event, compiled, before, _after, first, second = _finality_inputs()
    import copy

    changed_payload = _drop_following(_payload())
    changed_payload["STATION"][0]["OBSERVATIONS"]["air_temp_set_1"][2] = 83.4
    target_update = _snapshot(changed_payload, AFTER_RECEIVED)
    trigger = build_wrh_source_update_trigger(before, target_update, compiled=compiled)
    assert trigger.change_kind != CHANGE_FINALITY_TRANSITION
    result = asyncio.run(evaluate_wrh_official_result_lag_for_w7(
        trigger,
        event,
        before,
        target_update,
        clob=_FakeCLOB((first, second)),
    ))
    assert result is None


def test_shadow_policy_is_frozen_and_cannot_be_relaxed_for_acceptance():
    policy = WeatherResultLagShadowPolicy()
    assert len(policy.policy_sha256) == 64
    with pytest.raises(WeatherResultLagError) as edge:
        WeatherResultLagShadowPolicy(min_edge_per_share=0.0)
    assert edge.value.code == "RESULT_LAG_POLICY_DRIFT"
    with pytest.raises(WeatherResultLagError):
        WeatherResultLagShadowPolicy(min_visible_shares=True)
