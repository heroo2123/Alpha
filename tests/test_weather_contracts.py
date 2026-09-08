from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from polymarket_scanner.models import Market
from polymarket_scanner.weather import (
    ForecastContext,
    ForecastHour,
    Observation,
    ObservationBatch,
    market_unit,
    settlement_source_check,
)
from polymarket_scanner.weather_contracts import (
    WEATHER_CONTRACT_ADAPTER,
    contract_unit_from_question,
    exact_contract_date,
    settlement_safe_market,
    settlement_safe_weather_cache,
    strict_wrh_source,
)


def _market(
    question: str,
    source: str,
    description: str = "",
    *,
    title: str = "Highest temperature in Chicago on September 7, 2026",
) -> Market:
    return Market(
        id="m1",
        event_id="e1",
        event_slug="chicago-high",
        event_title=title,
        event_neg_risk=False,
        question=question,
        slug="bucket",
        condition_id="c1",
        outcomes=["Yes", "No"],
        token_ids=["yes", "no"],
        outcome_prices=[0.9, 0.1],
        best_bid=0.89,
        best_ask=0.9,
        liquidity=1000,
        volume_24h=5000,
        active=True,
        closed=False,
        end_date="2026-09-07T23:59:00Z",
        description=description,
        resolution_source=source,
        category="weather",
        tags=["weather"],
        raw={},
    )


def _now() -> datetime:
    return datetime(2026, 9, 7, 15, 0, tzinfo=timezone.utc)


def _forecast(fetched_at: datetime) -> ForecastContext:
    tz = ZoneInfo("America/Chicago")
    return ForecastContext(
        station="KORD",
        latitude=41.97,
        longitude=-87.90,
        timezone_name="America/Chicago",
        fetched_at=fetched_at,
        hours=[ForecastHour(datetime(2026, 9, 7, 16, 0, tzinfo=tz), 24.0)],
    )


def test_contract_unit_comes_from_question_not_celsius_display_instructions():
    market = _market(
        "Will the highest temperature be 71 F or below?",
        "https://www.weather.gov/wrh/timeseries?site=KORD",
        "Resolution uses the Temp column. The source page can also display Celsius.",
    )
    assert market_unit(market) == "C"
    assert contract_unit_from_question(market.question) == "F"

    safe = settlement_safe_market(market, now=_now())
    assert safe is not None
    assert safe.raw["weather_contract_unit"] == "F"
    assert safe.raw["weather_contract_station"] == "KORD"
    assert safe.raw["weather_contract_adapter"] == WEATHER_CONTRACT_ADAPTER
    assert safe.raw["weather_contract_target_date"] == "2026-09-07"
    assert safe.raw["weather_contract_interval_kind"] == "local_calendar_day"
    assert safe.raw["weather_contract_timezone"] == "America/Chicago"
    assert safe.raw["weather_contract_source_priority"] == "resolution_source_primary_only"
    assert safe.raw["weather_contract_fallback_policy"] == "none_present"
    assert market_unit(safe) == "F"
    assert safe.description == ""


def test_fake_host_containing_weather_gov_path_is_rejected():
    market = _market(
        "Will the highest temperature be 71 F or below?",
        "https://untrusted.invalid/weather.gov/wrh/timeseries?site=KORD",
    )
    assert settlement_source_check(market, "KORD")["verified"] is True
    assert strict_wrh_source(market)["verified"] is False
    assert settlement_safe_market(market, now=_now()) is None


def test_authoritative_wrh_host_station_and_date_are_preserved():
    market = _market(
        "Will the highest temperature be between 72 F and 73 F?",
        "https://www.weather.gov/wrh/timeseries?site=KORD",
    )
    source = strict_wrh_source(market)
    assert source["verified"] is True
    assert source["station"] == "KORD"
    assert source["kind"] == "NOAA/NWS WRH primary-only v4 exact-date"
    assert source["source_priority"] == "resolution_source_primary_only"
    assert source["fallback_policy"] == "none_present"
    assert exact_contract_date(market).isoformat() == "2026-09-07"
    assert settlement_safe_market(market, now=_now()) is not None


def test_wrh_url_only_in_description_cannot_be_promoted_to_primary_source():
    market = _market(
        "Will the highest temperature be 71 F or below?",
        "",
        "Use https://www.weather.gov/wrh/timeseries?site=KORD for settlement.",
    )
    source = strict_wrh_source(market)
    assert source["verified"] is False
    assert "resolution_source" in source["reason"]
    assert settlement_safe_market(market, now=_now()) is None


def test_unsupported_primary_with_wrh_fallback_in_description_is_rejected():
    market = _market(
        "Will the highest temperature be 71 F or below?",
        "https://www.wunderground.com/history/daily/us/il/chicago/KORD",
        "Fallback https://www.weather.gov/wrh/timeseries?site=KORD",
    )
    source = strict_wrh_source(market)
    assert source["verified"] is False
    assert "primary" in source["reason"]
    assert settlement_safe_market(market, now=_now()) is None


def test_primary_wrh_with_distinct_fallback_url_is_rejected_until_policy_is_modeled():
    market = _market(
        "Will the highest temperature be 71 F or below?",
        "https://www.weather.gov/wrh/timeseries?site=KORD",
        "If the primary source is unavailable, fallback to https://www.wunderground.com/history/daily/us/il/chicago/KORD.",
    )
    source = strict_wrh_source(market)
    assert source["verified"] is False
    assert source["station"] == "KORD"
    assert "secondary/fallback" in source["reason"]
    assert settlement_safe_market(market, now=_now()) is None


def test_primary_wrh_with_named_fallback_but_no_url_is_still_rejected():
    market = _market(
        "Will the highest temperature be 71 F or below?",
        "https://www.weather.gov/wrh/timeseries?site=KORD",
        "If the primary source is unavailable, use Wunderground as fallback.",
    )
    source = strict_wrh_source(market)
    assert source["verified"] is False
    assert "fallback/secondary" in source["reason"]
    assert settlement_safe_market(market, now=_now()) is None


def test_same_primary_url_may_be_repeated_in_rules_without_changing_authority():
    url = "https://www.weather.gov/wrh/timeseries?site=KORD"
    market = _market(
        "Will the highest temperature be 71 F or below?",
        url,
        f"Primary source: {url}",
    )
    source = strict_wrh_source(market)
    assert source["verified"] is True
    assert source["url"] == url
    assert settlement_safe_market(market, now=_now()) is not None


def test_multiple_urls_in_dedicated_resolution_source_are_ambiguous_and_rejected():
    market = _market(
        "Will the highest temperature be 71 F or below?",
        "https://www.weather.gov/wrh/timeseries?site=KORD https://www.weather.gov/wrh/timeseries?site=KMDW",
    )
    source = strict_wrh_source(market)
    assert source["verified"] is False
    assert "exactly one primary URL" in source["reason"]
    assert settlement_safe_market(market, now=_now()) is None


def test_missing_or_wrong_market_date_fails_closed():
    missing = _market(
        "Will the highest temperature be 71 F or below?",
        "https://www.weather.gov/wrh/timeseries?site=KORD",
        title="Highest temperature in Chicago",
    )
    wrong = _market(
        "Will the highest temperature be 71 F or below?",
        "https://www.weather.gov/wrh/timeseries?site=KORD",
        title="Highest temperature in Chicago on September 8, 2026",
    )
    assert exact_contract_date(missing) is None
    assert settlement_safe_market(missing, now=_now()) is None
    assert settlement_safe_market(wrong, now=_now()) is None


def test_missing_year_never_defaults_to_current_local_year():
    market = _market(
        "Will the highest temperature be 71 F or below?",
        "https://www.weather.gov/wrh/timeseries?site=KORD",
        title="Highest temperature in Chicago on September 7",
    )
    assert exact_contract_date(market) is None
    assert settlement_safe_market(market, now=_now()) is None


def test_two_distinct_contract_dates_are_ambiguous_even_if_one_is_today():
    market = _market(
        "Will the highest temperature on September 8, 2026 be 71 F or below?",
        "https://www.weather.gov/wrh/timeseries?site=KORD",
        title="Highest temperature in Chicago on September 7, 2026",
    )
    assert exact_contract_date(market) is None
    assert settlement_safe_market(market, now=_now()) is None


def test_repeated_same_explicit_date_is_not_false_ambiguity():
    market = _market(
        "Will the highest temperature on September 7, 2026 be 71 F or below?",
        "https://www.weather.gov/wrh/timeseries?site=KORD",
        title="Highest temperature in Chicago on September 7, 2026",
    )
    assert exact_contract_date(market).isoformat() == "2026-09-07"
    assert settlement_safe_market(market, now=_now()) is not None


def test_invalid_calendar_date_fails_closed():
    market = _market(
        "Will the highest temperature be 71 F or below?",
        "https://www.weather.gov/wrh/timeseries?site=KORD",
        title="Highest temperature in Chicago on February 30, 2026",
    )
    assert exact_contract_date(market) is None
    assert settlement_safe_market(market, now=_now()) is None


def test_missing_or_conflicting_contract_unit_fails_closed():
    no_unit = _market("Will the highest temperature be 71 or below?", "https://www.weather.gov/wrh/timeseries?site=KORD")
    conflict = _market("Will the highest temperature be 71 F / 22 C?", "https://www.weather.gov/wrh/timeseries?site=KORD")
    assert settlement_safe_market(no_unit, now=_now()) is None
    assert settlement_safe_market(conflict, now=_now()) is None


def test_multiple_distinct_wrh_sources_are_ambiguous_and_rejected():
    market = _market(
        "Will the highest temperature be 71 F or below?",
        "https://www.weather.gov/wrh/timeseries?site=KORD",
        "Fallback https://www.weather.gov/wrh/timeseries?site=KMDW",
    )
    assert strict_wrh_source(market)["verified"] is False
    assert settlement_safe_market(market, now=_now()) is None


def test_future_observation_is_removed_instead_of_becoming_age_zero():
    now = _now()
    batch = ObservationBatch(
        [
            Observation(now - timedelta(hours=2), 20.0, "old"),
            Observation(now - timedelta(hours=1), 21.0, "current"),
            Observation(now + timedelta(minutes=30), 35.0, "future-bad"),
        ],
        forecast=_forecast(now - timedelta(minutes=2)),
    )
    clean = settlement_safe_weather_cache({"KORD": batch}, now=now)["KORD"]
    assert [row.raw for row in clean] == ["old", "current"]
    assert max(row.when for row in clean) <= now
    assert clean.forecast is not None


def test_stale_or_future_forecast_is_invalidated():
    now = _now()
    rows = [
        Observation(now - timedelta(hours=2), 20.0, "a"),
        Observation(now - timedelta(hours=1), 21.0, "b"),
        Observation(now - timedelta(minutes=5), 20.0, "c"),
    ]

    stale = ObservationBatch(rows, forecast=_forecast(now - timedelta(hours=1)))
    stale_clean = settlement_safe_weather_cache({"KORD": stale}, now=now)["KORD"]
    assert stale_clean.forecast is None

    future = ObservationBatch(rows, forecast=_forecast(now + timedelta(minutes=5)))
    future_clean = settlement_safe_weather_cache({"KORD": future}, now=now)["KORD"]
    assert future_clean.forecast is None


def test_forecast_from_wrong_station_is_invalidated():
    now = _now()
    forecast = _forecast(now - timedelta(minutes=2))
    forecast.station = "KMDW"
    batch = ObservationBatch(
        [Observation(now - timedelta(minutes=5), 20.0, "a")],
        forecast=forecast,
    )
    clean = settlement_safe_weather_cache({"KORD": batch}, now=now)["KORD"]
    assert clean.forecast is None
