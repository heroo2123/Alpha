from __future__ import annotations

import copy
from datetime import date

import pytest

from polymarket_scanner.weather_only_discovery import WeatherDiscoveryError
from polymarket_scanner.weather_only_integrity_v4 import (
    _strict_bucket_bounds,
    compile_weather_event_v4,
    merge_event_v4,
)


def _event(*, family="high", target=date(2026, 9, 12), station="KLGA", unit="F", labels=None):
    extreme = "highest" if family == "high" else "lowest"
    unit_word = "Fahrenheit" if unit == "F" else "Celsius"
    source = f"https://www.weather.gov/wrh/timeseries?site={station}"
    rules = (
        f"This market resolves to the range containing the {extreme} temperature on "
        f"{target.strftime('%d %b')} '{target.year % 100:02d}, in degrees {unit_word}. "
        f"The source is NOAA, the {extreme} reading under the \"Temp\" column for all times on this day. "
        f"{source} The source measures temperatures to whole degrees {unit_word}. "
        "If NOAA data is unavailable by 11:59 PM ET on the day following the observation date, "
        "the Weather Underground Daily Observations table is used. If there is no data, this market "
        "resolves to the lowest bracket. Resolution occurs once the first data point for the following "
        "date is published, or at the deadline, whichever comes first. Revisions are considered until "
        "the first datapoint for the following date, after which any alterations will not be considered."
    )
    if labels is None:
        labels = [f"69°{unit} or lower", f"70-71°{unit}", f"72°{unit} or higher"]
    markets = []
    for i, label in enumerate(labels):
        markets.append({
            "id": f"m{i}",
            "conditionId": f"c{i}",
            "question": f"Will the {extreme} temperature be {label}?",
            "active": True,
            "closed": False,
            "acceptingOrders": True,
            "enableOrderBook": True,
            "outcomes": ["Yes", "No"],
            "clobTokenIds": [f"t{i}y", f"t{i}n"],
            "slug": f"m{i}",
        })
    return {
        "id": "event-1",
        "slug": "event-1",
        "title": f"{extreme.title()} temperature on {target.strftime('%B')} {target.day}?",
        "description": rules,
        "resolutionSource": source,
        "markets": markets,
    }


def test_v4_normal_nws_partition_certifies():
    compiled = compile_weather_event_v4(_event())
    assert compiled.rejection_reasons == ()
    assert compiled.shadow_supported is True
    assert compiled.exactly_one_outcome_proven is True
    assert compiled.station_hint == "KLGA"
    assert compiled.target_date == date(2026, 9, 12)


@pytest.mark.parametrize(
    ("question", "expected"),
    [
        ("Will the lowest temperature be less than 7°C?", (None, 6.0)),
        ("Will the lowest temperature be greater than 7°C?", (8.0, None)),
        ("Will the lowest temperature be 7°C or lower?", (None, 7.0)),
        ("Will the lowest temperature be 7°C or higher?", (7.0, None)),
        ("Will the lowest temperature be exactly 7°C?", (7.0, 7.0)),
        ("Will the lowest temperature be 8-9°C?", (8.0, 9.0)),
    ],
)
def test_v4_bucket_grammar_preserves_comparison(question, expected):
    assert _strict_bucket_bounds(question, "C") == expected


def test_v4_negated_bucket_rejects_instead_of_becoming_exact():
    assert _strict_bucket_bounds("Will the lowest temperature be not 7°C?", "C") is None


def test_v4_fractional_whole_degree_partition_rejects():
    compiled = compile_weather_event_v4(_event(labels=["69.5°F or lower", "70.5°F", "71.5°F or higher"]))
    assert compiled.shadow_supported is False
    assert "BUCKET_GRAMMAR_UNSUPPORTED_OR_AMBIGUOUS" in compiled.rejection_reasons


def test_v4_mixed_high_low_child_rejects():
    value = _event(family="high")
    value["markets"][1]["question"] = "Will the lowest temperature be 70-71°F?"
    compiled = compile_weather_event_v4(value)
    assert compiled.shadow_supported is False
    assert "CHILD_STATISTIC_CONFLICT" in compiled.rejection_reasons


def test_v4_fake_weather_gov_substring_host_rejects():
    value = _event()
    fake = "https://untrusted.invalid/weather.gov/wrh/timeseries?site=KLGA"
    value["description"] = value["description"].replace(value["resolutionSource"], fake)
    value["resolutionSource"] = fake
    compiled = compile_weather_event_v4(value)
    assert compiled.shadow_supported is False
    assert "UNTRUSTED_WRH_URL" in compiled.rejection_reasons


def test_v4_conflicting_child_station_rejects():
    value = _event()
    value["markets"][1]["resolutionSource"] = "https://www.weather.gov/wrh/timeseries?site=KDFW"
    compiled = compile_weather_event_v4(value)
    assert compiled.shadow_supported is False
    assert (
        "WRH_STATION_UNRESOLVED_OR_CONFLICT" in compiled.rejection_reasons
        or "CHILD_STATION_CONFLICT" in compiled.rejection_reasons
    )


def test_v4_yearless_title_conflicting_explicit_rule_date_rejects():
    value = _event(target=date(2026, 9, 13))
    value["title"] = "Highest temperature on September 12?"
    compiled = compile_weather_event_v4(value)
    assert compiled.shadow_supported is False
    assert "TITLE_RULE_DATE_CONFLICT" in compiled.rejection_reasons


def test_v4_obsolete_rule_text_cannot_certify_by_phrase_presence():
    value = _event()
    value["description"] = "OBSOLETE: " + value["description"]
    compiled = compile_weather_event_v4(value)
    assert compiled.shadow_supported is False
    assert "RULE_TEXT_OBSOLETE_OR_SUPERSEDED" in compiled.rejection_reasons


def test_v4_duplicate_token_across_children_rejects():
    value = _event()
    value["markets"][1]["clobTokenIds"][0] = value["markets"][0]["clobTokenIds"][0]
    compiled = compile_weather_event_v4(value)
    assert compiled.shadow_supported is False
    assert "TOKEN_ID_DUPLICATE_ACROSS_EVENT" in compiled.rejection_reasons


def test_v4_merge_rejects_swapped_outcome_labels_with_same_tokens():
    left = _event()
    right = copy.deepcopy(left)
    right["markets"][0]["outcomes"] = ["No", "Yes"]
    with pytest.raises(WeatherDiscoveryError, match="DUPLICATE_MARKET_IDENTITY_CONFLICT"):
        merge_event_v4(left, right)


def test_v4_merge_rejects_open_closed_projection_conflict():
    left = _event()
    right = copy.deepcopy(left)
    right["markets"][0]["closed"] = True
    right["markets"][0]["acceptingOrders"] = False
    with pytest.raises(WeatherDiscoveryError, match="DUPLICATE_MARKET_STATE_OR_SEMANTIC_CONFLICT"):
        merge_event_v4(left, right)
