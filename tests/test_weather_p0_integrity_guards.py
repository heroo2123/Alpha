from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime, timezone
from types import SimpleNamespace

import pytest

from polymarket_scanner.weather_only_contracts import compile_weather_event
from polymarket_scanner.weather_only_forecast import EnsembleExtremeDistribution
from polymarket_scanner.weather_only_p0_guards import (
    WeatherP0GuardError,
    assert_future_day_dispatch,
    semantic_envelope,
    validate_clob_snapshot,
    validate_forecast_distribution,
)
from polymarket_scanner.weather_only_rules import (
    apply_rule_authority,
    compile_temperature_rule_authority,
)


def _market(mid: str, question: str) -> dict:
    return {
        "id": mid,
        "active": True,
        "closed": False,
        "acceptingOrders": True,
        "enableOrderBook": True,
        "question": question,
        "slug": f"m-{mid}",
        "conditionId": f"condition-{mid}",
        "outcomes": '["Yes","No"]',
        "clobTokenIds": f'["{mid}-yes","{mid}-no"]',
    }


def _event(*, high: bool = True) -> dict:
    extreme = "highest" if high else "lowest"
    rules = (
        f"This market will resolve to the temperature range that contains the {extreme} temperature recorded by NOAA "
        f"at the LaGuardia Airport Station in degrees Fahrenheit on 11 Sep '26. "
        f"The resolution source for this market will be information from NOAA, specifically the {extreme} reading under "
        'the "Temp" column for all times on this day, available here: https://www.weather.gov/wrh/timeseries?site=klga '
        'This market will resolve off of the Hourly Data provided using the "Show Hourly Data" button. '
        "If NOAA data for the observation date is unavailable by 11:59 PM ET on the day following the observation date, "
        "the Weather Underground Daily Observations table will be used as the resolution source. "
        "In the event that there is no data for the observation date by 11:59 PM ET on the day following the observation date, "
        "this market will resolve to the lowest bracket. "
        "This market will resolve once the first data point for the following date has been published on the resolution source, "
        "or by 11:59 PM ET on the day following the observation date, whichever comes first. "
        "The resolution source for this market measures temperatures to whole degrees Fahrenheit. "
        "Revisions to temperatures recorded within this market's timeframe will be considered until the first datapoint for the "
        "following date has been published, after which any alterations will not be considered."
    )
    return {
        "id": "evt",
        "slug": "evt",
        "title": f"{'Highest' if high else 'Lowest'} temperature in NYC on September 11?",
        "description": rules,
        "resolutionSource": "https://www.weather.gov/wrh/timeseries?site=klga",
        "markets": [
            _market("a", f"Will the {extreme} temperature be 69°F or lower?"),
            _market("b", f"Will the {extreme} temperature be 70-71°F?"),
            _market("c", f"Will the {extreme} temperature be 72°F or higher?"),
        ],
    }


def _compiled(event: dict):
    raw = compile_weather_event(event)
    return apply_rule_authority(raw, compile_temperature_rule_authority(event, raw))


def _book(token: str, *, at: float = 1_800_000_000.0):
    return SimpleNamespace(
        token_id=token,
        best_ask=0.40,
        best_ask_size=10.0,
        received_at=at,
        timestamp=str(int(at * 1000)),
        book_hash=f"hash-{token}",
    )


def _snapshot(compiled, *, swap_labels: bool = False, at: float = 1_800_000_000.0):
    params = {}
    books = {}
    for bucket in compiled.buckets:
        outcomes = (
            ((bucket.yes_token, "No"), (bucket.no_token, "Yes"))
            if swap_labels
            else ((bucket.yes_token, "Yes"), (bucket.no_token, "No"))
        )
        params[bucket.condition_id] = SimpleNamespace(
            condition_id=bucket.condition_id,
            token_outcomes=outcomes,
            minimum_order_size=5.0,
            minimum_tick_size=0.01,
        )
        books[bucket.yes_token] = _book(bucket.yes_token, at=at)
        books[bucket.no_token] = _book(bucket.no_token, at=at)
    return SimpleNamespace(
        event_id=compiled.event_id,
        exact_clob=True,
        parameters=params,
        books=books,
        started_at=at - 0.2,
        finished_at=at,
    )


def test_normal_nws_contract_gets_independent_semantic_envelope():
    event = _event()
    compiled = _compiled(event)
    result = semantic_envelope(event, compiled)
    assert result["station"] == "KLGA"
    assert result["target_date"] == "2026-09-11"
    assert result["family"] == "daily_high_temperature"
    assert result["financial_authority"] is False


@pytest.mark.parametrize(
    "bad_question",
    [
        "Will the highest temperature be less than 70°F?",
        "Will the highest temperature be greater than 70°F?",
        "Will the highest temperature be not 70°F?",
    ],
)
def test_x01_x03_strict_or_negated_bucket_never_becomes_exact(bad_question: str):
    event = _event()
    event["markets"][1]["question"] = bad_question
    compiled = _compiled(event)
    with pytest.raises(WeatherP0GuardError, match="P0_BUCKET_GRAMMAR_UNSUPPORTED"):
        semantic_envelope(event, compiled)


def test_x04_mixed_high_low_child_rejects_even_if_global_rule_phrases_pass():
    event = _event(high=True)
    event["markets"][1]["question"] = "Will the lowest temperature be 70-71°F?"
    compiled = _compiled(event)
    assert compiled.exactly_one_outcome_proven is True  # reproduces old permissive certificate
    with pytest.raises(WeatherP0GuardError, match="P0_CHILD_STATISTIC"):
        semantic_envelope(event, compiled)


def test_x07_fake_weather_gov_path_on_untrusted_host_rejects():
    event = _event()
    trusted = "https://www.weather.gov/wrh/timeseries?site=klga"
    fake = "https://untrusted.invalid/weather.gov/wrh/timeseries?site=klga"
    event["description"] = event["description"].replace(trusted, fake)
    event["resolutionSource"] = fake
    compiled = _compiled(event)
    assert compiled.source_family == "NWS_WRH_TIMESERIES"  # old substring recognizer reproduces X07
    with pytest.raises(WeatherP0GuardError, match="P0_NWS_SOURCE_URL_UNTRUSTED"):
        semantic_envelope(event, compiled)


def test_x10_fractional_integer_lattice_is_rejected():
    event = _event()
    event["markets"] = [
        _market("a", "Will the highest temperature be 69.5°F or lower?"),
        _market("b", "Will the highest temperature be 70.5°F?"),
        _market("c", "Will the highest temperature be 71.5°F or higher?"),
    ]
    compiled = _compiled(event)
    assert compiled.partition_shape_complete is True
    with pytest.raises(WeatherP0GuardError, match="P0_WHOLE_DEGREE_LATTICE_REQUIRED"):
        semantic_envelope(event, compiled)


def test_x11_yearless_title_must_match_explicit_rule_date():
    event = _event()
    event["title"] = "Highest temperature in NYC on September 12?"
    compiled = _compiled(event)
    assert compiled.target_date == date(2026, 9, 11)
    with pytest.raises(WeatherP0GuardError, match="P0_TITLE_TARGET_DATE_CONFLICT"):
        semantic_envelope(event, compiled)


def test_x12_same_token_set_with_swapped_yes_no_meaning_rejects():
    event = _event()
    compiled = _compiled(event)
    semantic_envelope(event, compiled)
    snap = _snapshot(compiled, swap_labels=True)
    with pytest.raises(WeatherP0GuardError, match="P0_CLOB_OUTCOME_MAPPING_MISMATCH"):
        validate_clob_snapshot(compiled, snap, decision_time=1_800_000_000.0)


def test_x13_ancient_provider_book_timestamp_rejects_even_with_fresh_http_receipt():
    event = _event()
    compiled = _compiled(event)
    snap = _snapshot(compiled, at=1_800_000_000.0)
    first = next(iter(snap.books.values()))
    first.timestamp = "1000"
    with pytest.raises(WeatherP0GuardError, match="P0_BOOK_PROVIDER_TIMESTAMP_STALE"):
        validate_clob_snapshot(compiled, snap, decision_time=1_800_000_000.0)


def _distribution(compiled, *, resolved_lat: float = 40.77, resolved_lon: float = -73.87, received_at: float = 1_800_000_000.0):
    values = tuple(float(70 + (i % 3)) for i in range(31))
    return EnsembleExtremeDistribution(
        adapter="adapter",
        provider="provider",
        provider_model="model",
        station="KLGA",
        target_date=compiled.target_date,
        family=compiled.family,
        unit=compiled.unit,
        timezone="America/New_York",
        requested_latitude=40.7769,
        requested_longitude=-73.8740,
        resolved_latitude=resolved_lat,
        resolved_longitude=resolved_lon,
        member_labels=tuple(["control", *[f"member{i:02d}" for i in range(1, 31)]]),
        member_values=values,
        received_at=received_at,
        evidence_sha256="e" * 64,
        source_role="FORECAST_RESEARCH_ONLY",
        settlement_authority=False,
        calibration_label_authority=False,
        calibrated_probability=False,
        financial_authority=False,
    )


def test_x14_continent_scale_forecast_grid_mismatch_rejects():
    compiled = _compiled(_event())
    distribution = _distribution(compiled, resolved_lat=-33.9, resolved_lon=151.2)
    with pytest.raises(WeatherP0GuardError, match="P0_FORECAST_GRID_MISMATCH"):
        validate_forecast_distribution(
            compiled,
            distribution,
            timezone_name="America/New_York",
            decision_time=1_800_000_000.0,
        )


def test_x15_zero_or_ancient_forecast_receipt_rejects():
    compiled = _compiled(_event())
    distribution = _distribution(compiled, received_at=0.0)
    with pytest.raises(WeatherP0GuardError, match="P0_FORECAST_RECEIPT_STALE"):
        validate_forecast_distribution(
            compiled,
            distribution,
            timezone_name="America/New_York",
            decision_time=1_800_000_000.0,
        )


def test_x17_future_day_guard_is_rechecked_at_dispatch_boundary():
    target = date(2026, 9, 12)
    before = datetime(2026, 9, 12, 3, 59, 0, tzinfo=timezone.utc).timestamp()  # 23:59 Sep 11 NY
    expiry = assert_future_day_dispatch(
        target_date=target,
        timezone_name="America/New_York",
        now_epoch=before,
        margin_seconds=30.0,
    )
    assert expiry == datetime(2026, 9, 12, 4, 0, 0, tzinfo=timezone.utc).timestamp()
    after = datetime(2026, 9, 12, 4, 0, 1, tzinfo=timezone.utc).timestamp()
    with pytest.raises(WeatherP0GuardError, match="P0_FUTURE_DAY_DECISION_EXPIRED"):
        assert_future_day_dispatch(
            target_date=target,
            timezone_name="America/New_York",
            now_epoch=after,
            margin_seconds=0.0,
        )
