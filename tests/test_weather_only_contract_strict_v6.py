from __future__ import annotations

from datetime import date
from types import SimpleNamespace as NS

import pytest

from polymarket_scanner.weather_only_contract_strict import (
    STRICT_CONTRACT_VERSION,
    StrictWeatherContractError,
    _canonical_place,
    _current_title_identity,
    _question_identity,
    _supported_nws_rule_structure,
)
from polymarket_scanner.weather_only_contracts import DAILY_HIGH, DAILY_LOW


def _compiled(*, family=DAILY_HIGH, unit="C", station="LFPB"):
    return NS(
        target_date=date(2026, 9, 15),
        station_hint=station,
        unit=unit,
        family=family,
    )


def _current_rules(*, family=DAILY_HIGH, unit="C", station="LFPB", label="Paris-Le Bourget Airport Station", hourly=False):
    stat = "highest" if family == DAILY_HIGH else "lowest"
    unit_word = "Fahrenheit" if unit == "F" else "Celsius"
    toggle = (
        'To toggle between Fahrenheit and Celsius, click the "Switch to US Units w/ kts" button until the relevant table displays °F.'
        if unit == "F"
        else 'To toggle between Fahrenheit and Celsius, click the "Switch to Metric Units" button until the relevant table displays °C.'
    )
    example = "21°F" if unit == "F" else "9°C"
    hourly_text = (
        ' This market will resolve off of the Hourly Data provided using the "Show Hourly Data" button.'
        if hourly
        else ""
    )
    return (
        f"This market will resolve to the temperature range that contains the {stat} temperature recorded by NOAA at the {label} in degrees {unit_word} on 15 Sep '26. "
        f'The resolution source for this market will be information from NOAA, specifically the {stat} reading under the "Temp" column for all times on this day, available here: https://www.weather.gov/wrh/timeseries?site={station.lower()}'
        f"{hourly_text} "
        "If NOAA data for the observation date is unavailable by 11:59 PM ET on the day following the observation date, the Weather Underground Daily Observations table will be used as the resolution source. "
        "In the event that there is no data for the observation date by 11:59 PM ET on the day following the observation date, this market will resolve to the lowest bracket. "
        f"{toggle} "
        "This market will resolve once the first data point for the following date has been published on the resolution source, or by 11:59 PM ET on the day following the observation date, whichever comes first. "
        f"The resolution source for this market measures temperatures to whole degrees {unit_word} (eg, {example}). Thus, this is the level of precision that will be used when resolving the market. "
        "Revisions to temperatures recorded within this market's timeframe will be considered until the first datapoint for the following date has been published, after which any alterations will not be considered."
    )


def test_strict_contract_version_marks_current_recurring_v6_boundary():
    assert STRICT_CONTRACT_VERSION == "weather_contract_strict_v6_current_recurring_rule_grammar_fail_closed"


def test_current_question_grammar_consumes_location_bucket_and_date():
    low = _question_identity(
        "Will the lowest temperature in London be 12°C or below on September 15?",
        DAILY_LOW,
        unit="C",
    )
    band = _question_identity(
        "Will the lowest temperature in New York City be between 58-59°F on September 15?",
        DAILY_LOW,
        unit="F",
    )
    assert low == {"grammar": "current", "place": "london", "month": 9, "day": 15}
    assert band == {"grammar": "current", "place": "new york city", "month": 9, "day": 15}


def test_current_question_rejects_unit_or_statistic_drift():
    assert _question_identity(
        "Will the highest temperature in Paris be 22°F or higher on September 15?",
        DAILY_HIGH,
        unit="C",
    ) is None
    assert _question_identity(
        "Will the lowest temperature in Paris be 22°C or higher on September 15?",
        DAILY_HIGH,
        unit="C",
    ) is None


def test_legacy_question_grammar_remains_supported_but_identified_separately():
    assert _question_identity("Will the highest temperature be 77°F or higher?", DAILY_HIGH, unit="F") == {
        "grammar": "legacy",
        "place": None,
        "month": None,
        "day": None,
    }


def test_only_reviewed_nyc_place_alias_is_canonicalized():
    assert _canonical_place("NYC") == "new york city"
    assert _canonical_place("New York City") == "new york city"
    assert _canonical_place("Paris") == "paris"
    assert _canonical_place("New York") != _canonical_place("New York City")


def test_current_title_requires_exact_family_date_and_returns_canonical_place():
    event = {"title": "Lowest temperature in NYC on September 15?"}
    assert _current_title_identity(event, DAILY_LOW, date(2026, 9, 15)) == "new york city"
    with pytest.raises(StrictWeatherContractError, match="STRICT_TITLE_DATE_MISMATCH"):
        _current_title_identity(event, DAILY_LOW, date(2026, 9, 14))


def test_current_celsius_rule_is_completely_consumed():
    compiled = _compiled()
    assert _supported_nws_rule_structure(_current_rules(), compiled) is True


def test_current_fahrenheit_rule_with_exact_hourly_paragraph_is_completely_consumed():
    compiled = _compiled(family=DAILY_LOW, unit="F", station="KLGA")
    text = _current_rules(
        family=DAILY_LOW,
        unit="F",
        station="KLGA",
        label="LaGuardia Airport Station",
        hourly=True,
    )
    assert _supported_nws_rule_structure(text, compiled) is True


@pytest.mark.parametrize(
    "label",
    [
        "Ben Gurion International Airport",
        "Amsterdam Airport Schiphol Station",
        "Dallas Love Field Station",
        "Buckley Space Force Base Station",
        "Adolfo Suárez Madrid-Barajas Airport Station",
    ],
)
def test_current_rule_allows_reviewed_bounded_station_label_shapes(label):
    assert _supported_nws_rule_structure(_current_rules(label=label), _compiled()) is True


def test_current_rule_rejects_wrong_fahrenheit_toggle():
    compiled = _compiled(family=DAILY_LOW, unit="F", station="KLGA")
    text = _current_rules(family=DAILY_LOW, unit="F", station="KLGA", hourly=True)
    text = text.replace("Switch to US Units w/ kts", "Switch to Metric Units")
    assert _supported_nws_rule_structure(text, compiled) is False


def test_current_rule_rejects_fallback_deadline_drift():
    text = _current_rules().replace("11:59 PM ET", "11:58 PM ET", 1)
    assert _supported_nws_rule_structure(text, _compiled()) is False


def test_current_rule_rejects_wrong_machine_station_even_if_human_label_looks_valid():
    text = _current_rules().replace("site=lfpb", "site=klga")
    assert _supported_nws_rule_structure(text, _compiled()) is False


def test_current_rule_rejects_contradictory_or_unreviewed_suffix():
    assert _supported_nws_rule_structure(
        _current_rules() + " However, the exchange may use another source.",
        _compiled(),
    ) is False


def test_current_rule_rejects_unbounded_or_non_station_human_label():
    bad = _current_rules(label="X" * 121 + " Station")
    assert _supported_nws_rule_structure(bad, _compiled()) is False
    bad = _current_rules(label="Central Weather Observatory")
    assert _supported_nws_rule_structure(bad, _compiled()) is False
