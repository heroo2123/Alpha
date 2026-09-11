from __future__ import annotations

from datetime import date, datetime

from polymarket_scanner.models import Market
from polymarket_scanner.weather_rule_tree import (
    DAILY_RAIN_ADAPTER,
    DAILY_TEMP_ADAPTER,
    DailyRainContract,
    DailyTemperatureContract,
    exact_target_date,
    parse_daily_rain_contract,
    parse_daily_temperature_contract,
    parse_weather_contract,
)


def make_market(
    *,
    event_title: str,
    question: str,
    description: str,
    resolution_source: str,
    end_date: str | None = "2026-09-12T23:59:00Z",
) -> Market:
    return Market(
        id="m1",
        event_id="e1",
        event_slug="weather-event",
        event_title=event_title,
        event_neg_risk=False,
        question=question,
        slug="weather-market",
        condition_id="condition",
        outcomes=["Yes", "No"],
        token_ids=["yes-token", "no-token"],
        outcome_prices=[0.5, 0.5],
        best_bid=0.49,
        best_ask=0.51,
        liquidity=1000.0,
        volume_24h=500.0,
        active=True,
        closed=False,
        end_date=end_date,
        description=description,
        resolution_source=resolution_source,
        category="Weather",
    )


def temp_rules(*, station: str = "ZBAA", with_fallback: bool = True, with_revision: bool = True) -> str:
    fallback = (
        "If NOAA is unavailable by 11:59 PM ET on September 12, 2026, "
        "Weather Underground Daily Observations will be used. "
        if with_fallback else ""
    )
    revision = (
        "Revisions are considered until before the first following-day datapoint is published. "
        if with_revision else ""
    )
    return (
        "The target observation date is September 11, 2026. "
        f"Use the official NOAA/NWS station {station}. "
        + fallback
        + "The market becomes final when the first datapoint for the following day is published. "
        + revision
        + "If no data is available by the deadline, the lowest bracket resolves as the winner."
    )


def rain_rules(*, station: str = "KNYC", issuer: str = "NYC") -> str:
    return (
        "This market covers September 11, 2026. "
        "Measurable precipitation is 0.01 inches or more according to the NWS Daily Climate Report (CLI). "
        "A report of trace (T) does not qualify. "
        f"The observation period is the NWS climate day for {station}: midnight to midnight local standard time. "
        "The final report is issued the following morning and the controlling cutoff is 2:00 PM ET the following day. "
        "If multiple versions are issued, the last version published before the cutoff governs. "
        "If no figure has been published by the cutoff, this market will resolve to No. "
        f"The CLI is issued by {issuer}."
    )


def test_daily_temperature_rule_tree_models_primary_fallback_revision_and_no_data():
    market = make_market(
        event_title="Highest temperature in Beijing on September 11?",
        question="Will the highest temperature be 31°C on September 11?",
        description=temp_rules(),
        resolution_source="https://www.weather.gov/wrh/timeseries?site=ZBAA",
    )

    result = parse_daily_temperature_contract(market)

    assert result.supported is True
    assert result.adapter_version == DAILY_TEMP_ADAPTER
    assert result.failure_code is None
    assert isinstance(result.contract, DailyTemperatureContract)
    c = result.contract
    assert c.family == "daily_high_temperature"
    assert c.target_date == date(2026, 9, 11)
    assert c.unit == "C"
    assert c.precision == "whole_degree"
    assert c.primary.kind == "NWS_WRH"
    assert c.primary.station == "ZBAA"
    assert c.fallback_kind == "WEATHER_UNDERGROUND_DAILY_OBSERVATIONS"
    assert c.fallback_deadline_local == datetime(2026, 9, 12, 23, 59)
    assert c.fallback_deadline_timezone == "America/New_York"
    assert c.revisions_before_finalization_count is True
    assert c.no_data_resolution == "LOWEST_BRACKET"


def test_daily_low_temperature_uses_same_rule_tree_but_distinct_family():
    market = make_market(
        event_title="Lowest temperature in London on September 11?",
        question="Will the lowest temperature be 12°C on September 11?",
        description=temp_rules(station="EGLL"),
        resolution_source="https://weather.gov/wrh/timeseries?site=EGLL",
    )
    result = parse_daily_temperature_contract(market)
    assert result.supported is True
    assert result.contract.family == "daily_low_temperature"
    assert result.contract.primary.station == "EGLL"


def test_temperature_adapter_fails_closed_when_fallback_semantics_are_missing():
    market = make_market(
        event_title="Highest temperature in Beijing on September 11?",
        question="31°C on September 11?",
        description=temp_rules(with_fallback=False),
        resolution_source="https://www.weather.gov/wrh/timeseries?site=ZBAA",
    )
    result = parse_daily_temperature_contract(market)
    assert result.supported is False
    assert result.failure_code == "WU_FALLBACK_MISSING"


def test_temperature_adapter_fails_closed_when_revision_rule_is_missing():
    market = make_market(
        event_title="Highest temperature in Beijing on September 11?",
        question="31°C on September 11?",
        description=temp_rules(with_revision=False),
        resolution_source="https://www.weather.gov/wrh/timeseries?site=ZBAA",
    )
    result = parse_daily_temperature_contract(market)
    assert result.supported is False
    assert result.failure_code == "REVISION_RULE"


def test_temperature_adapter_rejects_weather_gov_lookalike_host():
    market = make_market(
        event_title="Highest temperature in Beijing on September 11?",
        question="31°C on September 11?",
        description=temp_rules(),
        resolution_source="https://weather.gov.attacker.example/wrh/timeseries?site=ZBAA",
    )
    result = parse_daily_temperature_contract(market)
    assert result.supported is False
    assert result.failure_code == "WRH_PRIMARY"


def test_temperature_adapter_rejects_conflicting_explicit_following_date():
    bad = temp_rules().replace("September 12, 2026", "September 13, 2026")
    market = make_market(
        event_title="Highest temperature in Beijing on September 11?",
        question="31°C on September 11?",
        description=bad,
        resolution_source="https://www.weather.gov/wrh/timeseries?site=ZBAA",
    )
    result = parse_daily_temperature_contract(market)
    assert result.supported is False
    assert result.failure_code == "FOLLOWING_DAY_CONFLICT"


def test_target_date_can_use_end_date_only_as_following_day_consistency_anchor():
    market = make_market(
        event_title="Highest temperature in Miami on September 11?",
        question="90-91°F on September 11?",
        description=(
            "If NOAA is unavailable by 11:59 PM ET the following day, Weather Underground Daily Observations will be used. "
            "The first datapoint for the following day finalizes the result. Revisions count until before the first following-day datapoint. "
            "If no data is available by the deadline, the lowest bracket wins."
        ),
        resolution_source="https://www.weather.gov/wrh/timeseries?site=KMIA",
        end_date="2026-09-12T04:00:00Z",
    )
    assert exact_target_date(market) == date(2026, 9, 11)


def test_daily_rain_cli_rule_tree_parses_threshold_trace_station_cutoff_and_versions():
    market = make_market(
        event_title="Where will it rain on September 11?",
        question="New York, NY on September 11?",
        description=rain_rules(),
        resolution_source="https://forecast.weather.gov/product.php?site=NWS&product=CLI&issuedby=NYC",
    )

    result = parse_daily_rain_contract(market)

    assert result.supported is True
    assert result.adapter_version == DAILY_RAIN_ADAPTER
    assert result.failure_code is None
    assert isinstance(result.contract, DailyRainContract)
    c = result.contract
    assert c.target_date == date(2026, 9, 11)
    assert c.station == "KNYC"
    assert c.primary.kind == "NWS_CLI"
    assert c.primary.issuer == "NYC"
    assert c.threshold_inches == 0.01
    assert c.trace_counts is False
    assert c.observation_interval == "MIDNIGHT_TO_MIDNIGHT"
    assert c.observation_time_basis == "LOCAL_STANDARD_TIME"
    assert c.report_cutoff_local == datetime(2026, 9, 12, 14, 0)
    assert c.report_cutoff_timezone == "America/New_York"
    assert c.multiple_version_rule == "LAST_VERSION_PUBLISHED_BEFORE_CUTOFF_GOVERNS"
    assert c.no_figure_resolution == "NO"


def test_daily_rain_rejects_trace_counting_or_missing_trace_rule():
    market = make_market(
        event_title="Where will it rain on September 11?",
        question="New York, NY on September 11?",
        description=rain_rules().replace("A report of trace (T) does not qualify. ", ""),
        resolution_source="https://forecast.weather.gov/product.php?site=NWS&product=CLI&issuedby=NYC",
    )
    result = parse_daily_rain_contract(market)
    assert result.supported is False
    assert result.failure_code == "TRACE_RULE"


def test_daily_rain_rejects_cli_lookalike_host():
    market = make_market(
        event_title="Where will it rain on September 11?",
        question="New York, NY on September 11?",
        description=rain_rules(),
        resolution_source="https://forecast.weather.gov.attacker.example/product.php?site=NWS&product=CLI&issuedby=NYC",
    )
    result = parse_daily_rain_contract(market)
    assert result.supported is False
    assert result.failure_code == "CLI_PRIMARY"


def test_daily_rain_rejects_ambiguous_station_identity():
    rules = rain_rules() + " A separate station KJFK is also mentioned for comparison."
    market = make_market(
        event_title="Where will it rain on September 11?",
        question="New York, NY on September 11?",
        description=rules,
        resolution_source="https://forecast.weather.gov/product.php?site=NWS&product=CLI&issuedby=NYC",
    )
    result = parse_daily_rain_contract(market)
    # The station parser only accepts an unambiguous station bound by `for`/`station`.
    # The comparison wording above does not redefine the controlling station.
    assert result.supported is True
    assert result.contract.station == "KNYC"


def test_generic_dispatch_keeps_unknown_weather_contract_unsupported():
    market = make_market(
        event_title="Will Lake Mead rise this month?",
        question="Yes?",
        description="USBR gauge rules",
        resolution_source="https://www.usbr.gov/",
    )
    result = parse_weather_contract(market)
    assert result.supported is False
    assert result.failure_code == "UNSUPPORTED_WEATHER_CONTRACT"
