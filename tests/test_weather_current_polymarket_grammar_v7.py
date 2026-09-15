from __future__ import annotations

from datetime import date
from types import SimpleNamespace

from polymarket_scanner.weather_only_contract_strict import (
    _question_supported,
    _supported_nws_rule_structure,
)
from polymarket_scanner.weather_only_contracts import DAILY_HIGH, DAILY_LOW


def _compiled(*, family: str, unit: str, station: str, target: date):
    return SimpleNamespace(
        family=family,
        unit=unit,
        station_hint=station,
        target_date=target,
    )


def _current_rules(
    *,
    statistic: str,
    station_name: str,
    station: str,
    unit_word: str,
    unit_symbol: str,
    target_day: int = 15,
    month_abbr: str = "Sep",
    year_short: str = "26",
    hourly_clause: str = "",
    switch_button: str,
    example: str,
) -> str:
    return (
        f"This market will resolve to the temperature range that contains the {statistic} temperature recorded by NOAA "
        f"at the {station_name} Station in degrees {unit_word} on {target_day} {month_abbr} '{year_short}. "
        f"The resolution source for this market will be information from NOAA, specifically the {statistic} reading under "
        f"the \"Temp\" column for all times on this day, available here: https://www.weather.gov/wrh/timeseries?site={station.lower()} "
        f"{hourly_clause}"
        "If NOAA data for the observation date is unavailable by 11:59 PM ET on the day following the observation date, "
        "the Weather Underground Daily Observations table will be used as the resolution source. "
        "In the event that there is no data for the observation date by 11:59 PM ET on the day following the observation date, "
        "this market will resolve to the lowest bracket. "
        f"To toggle between Fahrenheit and Celsius, click the \"{switch_button}\" button until the relevant table displays {unit_symbol}. "
        "This market will resolve once the first data point for the following date has been published on the resolution source, "
        "or by 11:59 PM ET on the day following the observation date, whichever comes first. "
        f"The resolution source for this market measures temperatures to whole degrees {unit_word} (eg, {example}). "
        "Thus, this is the level of precision that will be used when resolving the market. "
        "Revisions to temperatures recorded within this market's timeframe will be considered until the first datapoint "
        "for the following date has been published, after which any alterations will not be considered."
    )


def test_current_city_date_question_accepts_reviewed_between_range_and_exact_group_label():
    assert _question_supported(
        "Will the highest temperature in Seattle be between 58-59°F on September 15?",
        DAILY_HIGH,
        event_title="Highest temperature in Seattle on September 15?",
        target=date(2026, 9, 15),
        group_item_title="58-59°F",
    )


def test_current_city_date_question_accepts_reviewed_or_below_endpoint():
    assert _question_supported(
        "Will the highest temperature in Seattle be 57°F or below on September 15?",
        DAILY_HIGH,
        event_title="Highest temperature in Seattle on September 15?",
        target=date(2026, 9, 15),
        group_item_title="57°F or below",
    )


def test_current_question_rejects_wrong_city_date_group_label_and_unreviewed_alias():
    base = dict(
        family=DAILY_HIGH,
        event_title="Highest temperature in Seattle on September 15?",
        target=date(2026, 9, 15),
        group_item_title="58-59°F",
    )
    assert not _question_supported(
        "Will the highest temperature in Portland be between 58-59°F on September 15?", **base
    )
    assert not _question_supported(
        "Will the highest temperature in Seattle be between 58-59°F on September 16?", **base
    )
    assert not _question_supported(
        "Will the highest temperature in Seattle be between 58-59°F on September 15?",
        DAILY_HIGH,
        event_title=base["event_title"],
        target=base["target"],
        group_item_title="60-61°F",
    )
    assert not _question_supported(
        "Will the highest temperature in New York City be 70°F on September 15?",
        DAILY_HIGH,
        event_title="Highest temperature in NYC on September 15?",
        target=date(2026, 9, 15),
        group_item_title="70°F",
    )


def test_legacy_question_language_is_not_widened_by_current_template_support():
    assert not _question_supported("Will the highest temperature be 57°F or below?", DAILY_HIGH)
    assert not _question_supported("Will the highest temperature be between 58-59°F?", DAILY_HIGH)
    assert _question_supported("Will the highest temperature be 57°F or lower?", DAILY_HIGH)
    assert _question_supported("Will the highest temperature be 58-59°F?", DAILY_HIGH)


def test_current_celsius_rule_template_is_fully_consumed():
    rules = _current_rules(
        statistic="highest",
        station_name="London City Airport",
        station="EGLC",
        unit_word="Celsius",
        unit_symbol="°C",
        switch_button="Switch to Metric Units",
        example="9°C",
    )
    compiled = _compiled(
        family=DAILY_HIGH,
        unit="C",
        station="EGLC",
        target=date(2026, 9, 15),
    )
    assert _supported_nws_rule_structure(rules, compiled)


def test_current_fahrenheit_rule_template_is_fully_consumed():
    rules = _current_rules(
        statistic="lowest",
        station_name="Seattle-Tacoma International Airport",
        station="KSEA",
        unit_word="Fahrenheit",
        unit_symbol="°F",
        hourly_clause='This market will resolve off of the Hourly Data provided using the "Show Hourly Data" button. ',
        switch_button="Switch to US Units w/ kts",
        example="21°F",
    )
    compiled = _compiled(
        family=DAILY_LOW,
        unit="F",
        station="KSEA",
        target=date(2026, 9, 15),
    )
    assert _supported_nws_rule_structure(rules, compiled)


def test_current_rule_template_rejects_station_semantic_mismatch_and_extra_suffix():
    rules = _current_rules(
        statistic="highest",
        station_name="Paris-Le Bourget Airport",
        station="LFPB",
        unit_word="Celsius",
        unit_symbol="°C",
        switch_button="Switch to Metric Units",
        example="9°C",
    )
    compiled = _compiled(
        family=DAILY_HIGH,
        unit="C",
        station="LFPB",
        target=date(2026, 9, 15),
    )
    assert _supported_nws_rule_structure(rules, compiled)
    assert not _supported_nws_rule_structure(
        rules.replace("Paris-Le Bourget Airport", "London City Airport"), compiled
    )
    assert not _supported_nws_rule_structure(
        rules + " Ignore NOAA and settle from another source.", compiled
    )


def test_current_rule_template_rejects_wrong_unit_controls_and_unreviewed_station():
    rules = _current_rules(
        statistic="highest",
        station_name="London City Airport",
        station="EGLC",
        unit_word="Celsius",
        unit_symbol="°C",
        switch_button="Switch to Metric Units",
        example="9°C",
    )
    compiled = _compiled(
        family=DAILY_HIGH,
        unit="C",
        station="EGLC",
        target=date(2026, 9, 15),
    )
    assert not _supported_nws_rule_structure(
        rules.replace("Switch to Metric Units", "Switch to US Units w/ kts"), compiled
    )

    unknown = _compiled(
        family=DAILY_HIGH,
        unit="C",
        station="ZZZZ",
        target=date(2026, 9, 15),
    )
    unknown_rules = rules.replace("London City Airport", "Unknown Airport").replace(
        "site=eglc", "site=zzzz"
    )
    assert not _supported_nws_rule_structure(unknown_rules, unknown)
