from __future__ import annotations

import asyncio

import pytest

from polymarket_scanner.models import Book
from polymarket_scanner.weather_only_acceptance_latency import (
    CHANGE_FINALITY_TRANSITION,
    build_wrh_source_update_trigger,
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


def _execution(compiled, *, winner_ask: float = 0.92, started: float = AFTER_RECEIVED + 0.10, finished: float = AFTER_RECEIVED + 0.50):
    books = {}
    parameters = {}
    winner = compiled.buckets[-1]
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


class _FakeCLOB:
    def __init__(self, snapshots):
        self.snapshots = list(snapshots)
        self.calls = 0

    async def exact_event_snapshot(self, compiled):
        self.calls += 1
        return self.snapshots.pop(0)


def _inputs():
    event, compiled = _event_and_compiled()
    before = _snapshot(_drop_following(_payload()), BEFORE_RECEIVED)
    after = _snapshot(_payload(), AFTER_RECEIVED)
    first = _execution(compiled)
    second = _execution(compiled, started=AFTER_RECEIVED + 0.60, finished=AFTER_RECEIVED + 1.00)
    return event, compiled, before, after, first, second


def test_polling_bracket_cannot_start_deterministic_result_lag_lane():
    event, _compiled, before, after, first, second = _inputs()
    clob = _FakeCLOB((first, second))
    with pytest.raises(WeatherResultLagError) as raised:
        asyncio.run(evaluate_wrh_official_result_lag(event, before, after, clob=clob))
    assert raised.value.code == "RESULT_LAG_FINALITY_AUTHORITY_INVALID"
    assert clob.calls == 0


def test_w7_finality_trigger_is_not_a_candidate_without_exact_publication_state():
    event, compiled, before, after, first, second = _inputs()
    trigger = build_wrh_source_update_trigger(before, after, compiled=compiled)
    assert trigger.change_kind == CHANGE_FINALITY_TRANSITION
    clob = _FakeCLOB((first, second))
    result = asyncio.run(evaluate_wrh_official_result_lag_for_w7(
        trigger,
        event,
        before,
        after,
        clob=clob,
    ))
    # An uncertain source bracket is an ordinary no-candidate result for W7, not a
    # source-health failure and not a reason to spend any execution request.
    assert result is None
    assert clob.calls == 0


def test_nonfinal_target_update_cannot_use_result_lag_lane_as_fake_source_latency_candidate():
    event, compiled, before, _after, first, second = _inputs()
    import copy

    changed_payload = _drop_following(_payload())
    changed_payload["STATION"][0]["OBSERVATIONS"]["air_temp_set_1"][2] = 83.4
    target_update = _snapshot(changed_payload, AFTER_RECEIVED)
    trigger = build_wrh_source_update_trigger(before, target_update, compiled=compiled)
    assert trigger.change_kind != CHANGE_FINALITY_TRANSITION
    clob = _FakeCLOB((first, second))
    result = asyncio.run(evaluate_wrh_official_result_lag_for_w7(
        trigger,
        event,
        before,
        target_update,
        clob=clob,
    ))
    assert result is None
    assert clob.calls == 0


def test_shadow_policy_is_frozen_and_cannot_be_relaxed_for_acceptance():
    policy = WeatherResultLagShadowPolicy()
    assert len(policy.policy_sha256) == 64
    with pytest.raises(WeatherResultLagError) as edge:
        WeatherResultLagShadowPolicy(min_edge_per_share=0.0)
    assert edge.value.code == "RESULT_LAG_POLICY_DRIFT"
    with pytest.raises(WeatherResultLagError):
        WeatherResultLagShadowPolicy(min_visible_shares=True)
