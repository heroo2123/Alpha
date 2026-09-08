from polymarket_scanner.weather_rule_profiles import (
    WRH_DAILY_HIGH_RULE_PROFILE_VERSION,
    compile_wrh_daily_high_profile,
)


BASE = (
    'Use the highest reading under the "Temp" column for all times on this day. '
    'If NOAA is unavailable by 11:59 PM ET on the day following the observation date, '
    'use the Weather Underground Daily Observations table. '
    'If there is no data by that deadline, resolve to the lowest bracket. '
    'Resolve once the first data point for the following date is published or at the deadline, whichever comes first. '
    'Revisions are considered until the first datapoint for the following date, after which any alterations will not be considered. '
)


def test_london_style_profile_uses_all_times_population():
    profile = compile_wrh_daily_high_profile(BASE + "Source measures temperatures to whole degrees Celsius.", "C")
    assert profile is not None
    assert profile.version == WRH_DAILY_HIGH_RULE_PROFILE_VERSION
    assert profile.observation_population == "WRH_ALL_TIMES"
    assert profile.observation_column == "Temp"
    assert profile.unit == "C"
    assert profile.precision == "whole_degree"
    assert profile.fallback_source_family == "WEATHER_UNDERGROUND_DAILY_OBSERVATIONS"
    assert profile.no_data_outcome == "LOWEST_BRACKET"


def test_chicago_style_hourly_clause_overrides_default_all_times_population():
    text = BASE + 'Resolve off the Hourly Data using the "Show Hourly Data" control. Source measures temperatures to whole degrees Fahrenheit.'
    profile = compile_wrh_daily_high_profile(text, "F")
    assert profile is not None
    assert profile.observation_population == "WRH_HOURLY_DATA"
    assert profile.unit == "F"


def test_unit_precision_mismatch_fails_closed():
    assert compile_wrh_daily_high_profile(BASE + "Source measures temperatures to whole degrees Celsius.", "F") is None


def test_vague_wunderground_mention_does_not_compile_fallback_policy():
    text = (
        'Use the highest reading under the "Temp" column for all times on this day. '
        'Maybe use Weather Underground if needed. Source measures temperatures to whole degrees Celsius.'
    )
    assert compile_wrh_daily_high_profile(text, "C") is None


def test_missing_finality_or_revision_policy_fails_closed():
    text = (
        'Use the highest reading under the "Temp" column for all times on this day. '
        'If NOAA is unavailable by 11:59 PM ET on the day following the observation date, '
        'use the Weather Underground Daily Observations table. If there is no data, resolve to the lowest bracket. '
        'Source measures temperatures to whole degrees Celsius.'
    )
    assert compile_wrh_daily_high_profile(text, "C") is None


def test_missing_no_data_lowest_bracket_rule_fails_closed():
    text = BASE.replace("If there is no data by that deadline, resolve to the lowest bracket. ", "")
    assert compile_wrh_daily_high_profile(text + "Source measures temperatures to whole degrees Celsius.", "C") is None


def test_unknown_precision_fails_closed():
    assert compile_wrh_daily_high_profile(BASE + "Source reports tenths of a degree Celsius.", "C") is None
