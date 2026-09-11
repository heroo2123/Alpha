from __future__ import annotations

import asyncio
from dataclasses import replace

import pytest

from polymarket_scanner.models import Book
from polymarket_scanner.weather_only_clob import WeatherExecutionSnapshot, WeatherMarketParameters
from polymarket_scanner.weather_only_contracts import compile_weather_event
from polymarket_scanner.weather_only_incremental import (
    WeatherIncrementalError,
    evaluate_weather_event_incrementally,
    measure_weather_incremental_evaluation,
)
from polymarket_scanner.weather_only_rules import apply_rule_authority, compile_temperature_rule_authority
from test_weather_only_rules import _nws_event


STARTED = 1_800_000_000.0
FINISHED = STARTED + 0.4


def _compiled(event=None):
    event = event or _nws_event(high=True, hourly=True)
    raw = compile_weather_event(event)
    return apply_rule_authority(raw, compile_temperature_rule_authority(event, raw))


def _snapshot(compiled, *, yes_ask=0.60, no_ask=0.60):
    books = {}
    parameters = {}
    for bucket in compiled.buckets:
        books[bucket.yes_token] = Book(
            token_id=bucket.yes_token,
            bids=[(max(0.01, yes_ask - 0.02), 10.0)],
            asks=[(yes_ask, 10.0)],
            received_at=FINISHED,
            source="clob_exact_rest_v2",
        )
        books[bucket.no_token] = Book(
            token_id=bucket.no_token,
            bids=[(max(0.01, no_ask - 0.02), 10.0)],
            asks=[(no_ask, 10.0)],
            received_at=FINISHED,
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
            received_at=FINISHED,
        )
    return WeatherExecutionSnapshot(
        version="weather_clob_v2_exact_books_dynamic_fee_exponent_read_only",
        event_id=compiled.event_id,
        books=books,
        parameters=parameters,
        started_at=STARTED,
        finished_at=FINISHED,
        exact_clob=True,
        financial_authority=False,
    )


class _FakeCLOB:
    def __init__(self, snapshot):
        self.snapshot = snapshot
        self.calls = 0

    async def exact_event_snapshot(self, compiled):
        assert compiled.event_id == self.snapshot.event_id
        self.calls += 1
        return self.snapshot


def test_zero_candidate_event_is_still_valid_incremental_timing_evidence():
    event = _nws_event(high=True, hourly=True)
    compiled = _compiled(event)
    clob = _FakeCLOB(_snapshot(compiled, yes_ask=0.60, no_ask=0.60))
    receipt = asyncio.run(evaluate_weather_event_incrementally(event, clob=clob))
    assert clob.calls == 1
    assert receipt.event_id == compiled.event_id
    assert receipt.structural_opportunity_count == 0
    assert receipt.structural_opportunity_sha256 == ()
    assert len(receipt.compiled_evidence_sha256) == 64
    assert len(receipt.exact_clob_evidence_sha256) == 64
    assert len(receipt.receipt_evidence_sha256) == 64
    assert receipt.exact_clob is True
    assert receipt.research_only is True
    assert receipt.financial_authority is False


def test_incremental_measurement_uses_monotonic_elapsed_and_is_hash_bound():
    event = _nws_event(high=True, hourly=True)
    compiled = _compiled(event)
    clob = _FakeCLOB(_snapshot(compiled))
    mono = iter((10.0, 10.8))
    wall = iter((STARTED, STARTED + 0.9))
    receipt, measured = asyncio.run(measure_weather_incremental_evaluation(
        event,
        clob=clob,
        monotonic_clock=lambda: next(mono),
        wall_clock=lambda: next(wall),
    ))
    assert measured.incremental_evaluation_seconds == pytest.approx(0.8)
    assert measured.evaluation_started_at == pytest.approx(STARTED)
    assert measured.evaluation_finished_at == pytest.approx(STARTED + 0.9)
    assert measured.receipt_evidence_sha256 == receipt.receipt_evidence_sha256
    assert len(measured.measurement_evidence_sha256) == 64
    assert measured.financial_authority is False
    assert measured.financial_delivery is False
    assert measured.automatic_order_placement is False


def test_structural_opportunities_are_digest_only_research_evidence_not_authority():
    event = _nws_event(high=True, hourly=True)
    compiled = _compiled(event)
    receipt = asyncio.run(evaluate_weather_event_incrementally(
        event,
        clob=_FakeCLOB(_snapshot(compiled, yes_ask=0.40, no_ask=0.40)),
    ))
    assert receipt.structural_opportunity_count >= 1
    assert len(receipt.structural_opportunity_sha256) == receipt.structural_opportunity_count
    assert all(len(value) == 64 for value in receipt.structural_opportunity_sha256)
    assert receipt.financial_authority is False


def test_incomplete_or_crosswired_exact_clob_fixture_fails_closed():
    event = _nws_event(high=True, hourly=True)
    compiled = _compiled(event)
    snapshot = _snapshot(compiled)

    missing = replace(snapshot, books=dict(list(snapshot.books.items())[1:]))
    with pytest.raises(WeatherIncrementalError) as missing_error:
        asyncio.run(evaluate_weather_event_incrementally(event, clob=_FakeCLOB(missing)))
    assert missing_error.value.code == "INCREMENTAL_CLOB_COVERAGE_INVALID"

    first_bucket, second_bucket = compiled.buckets[:2]
    params = dict(snapshot.parameters)
    params[first_bucket.condition_id] = replace(
        params[first_bucket.condition_id],
        token_outcomes=((second_bucket.yes_token, "Yes"), (second_bucket.no_token, "No")),
    )
    crosswired = replace(snapshot, parameters=params)
    with pytest.raises(WeatherIncrementalError) as identity_error:
        asyncio.run(evaluate_weather_event_incrementally(event, clob=_FakeCLOB(crosswired)))
    assert identity_error.value.code == "INCREMENTAL_CLOB_PARAMETER_TOKEN_MISMATCH"


def test_clock_regression_and_bool_clock_fail_closed():
    event = _nws_event(high=True, hourly=True)
    compiled = _compiled(event)

    mono = iter((10.0, 9.0))
    wall = iter((STARTED, STARTED + 1.0))
    with pytest.raises(WeatherIncrementalError) as regression:
        asyncio.run(measure_weather_incremental_evaluation(
            event,
            clob=_FakeCLOB(_snapshot(compiled)),
            monotonic_clock=lambda: next(mono),
            wall_clock=lambda: next(wall),
        ))
    assert regression.value.code == "INCREMENTAL_CLOCK_REGRESSION"

    with pytest.raises(WeatherIncrementalError):
        asyncio.run(measure_weather_incremental_evaluation(
            event,
            clob=_FakeCLOB(_snapshot(compiled)),
            monotonic_clock=lambda: True,
            wall_clock=lambda: STARTED,
        ))
