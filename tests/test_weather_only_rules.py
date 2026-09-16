from polymarket_scanner.weather_only_contracts import compile_weather_event
from polymarket_scanner.weather_only_rules import (
    apply_rule_authority,
    compile_temperature_rule_authority,
)


def _market(mid, question):
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


def _nws_event(*, high=True, hourly=True):
    extreme = "highest" if high else "lowest"
    rules = (
        f"This market will resolve to the temperature range that contains the {extreme} temperature recorded by NOAA "
        f"at the LaGuardia Airport Station in degrees Fahrenheit on 11 Sep '26. "
        f"The resolution source for this market will be information from NOAA, specifically the {extreme} reading under "
        'the "Temp" column for all times on this day, available here: https://www.weather.gov/wrh/timeseries?site=klga '
    )
    if hourly:
        rules += 'This market will resolve off of the Hourly Data provided using the "Show Hourly Data" button. '
    rules += (
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
        "id": f"nws-{'high' if high else 'low'}",
        "slug": f"nws-{'high' if high else 'low'}",
        "title": f"{'Highest' if high else 'Lowest'} temperature in NYC on September 11?",
        "description": rules,
        "resolutionSource": "https://www.weather.gov/wrh/timeseries?site=klga",
        "markets": [
            _market("a", f"Will the {extreme} temperature be 69°F or lower?"),
            _market("b", f"Will the {extreme} temperature be 70-71°F?"),
            _market("c", f"Will the {extreme} temperature be 72°F or higher?"),
        ],
    }


def _hko_event(*, high=True):
    extreme = "highest" if high else "lowest"
    stat = "Max" if high else "Min"
    rules = (
        f"This market will resolve to the temperature range that contains the {extreme} temperature recorded by the Hong Kong "
        f"Observatory in degrees Celsius on 11 Sep '26. The resolution source for this market will be information from the "
        f"Hong Kong Observatory, specifically the \"Absolute Daily {stat} (deg. C)\" the specified date once information is "
        "finalized in the relevant \"Daily Extract\", available here: https://www.weather.gov.hk/en/cis/climat.htm "
        "In the event that there is no data for the observation date by 11:59 PM ET on the seventh day following the observation date, "
        "this market will resolve to the lowest bracket. This market will resolve once data for this date has been published on the "
        "resolution source, or by 11:59 PM ET on the seventh day following the observation date, whichever comes first. "
        "The resolution source for this market measures temperatures in Celsius to one decimal place. "
        "Any revisions to temperatures recorded after data is initially published for this market's timeframe will not be considered."
    )
    return {
        "id": f"hko-{'high' if high else 'low'}",
        "slug": f"hko-{'high' if high else 'low'}",
        "title": f"{'Highest' if high else 'Lowest'} temperature in Hong Kong on September 11?",
        "description": rules,
        "resolutionSource": "https://www.weather.gov.hk/en/cis/climat.htm",
        "markets": [
            _market("h1", f"Will the {extreme} temperature be 29°C or lower?"),
            _market("h2", f"Will the {extreme} temperature be 30°C?"),
            _market("h3", f"Will the {extreme} temperature be 31°C or higher?"),
        ],
    }


def test_current_nws_high_template_proves_exactly_one_semantics_but_not_financial_authority():
    event = _nws_event(high=True, hourly=True)
    compiled = compile_weather_event(event)
    profile = compile_temperature_rule_authority(event, compiled)
    assert profile.rule_semantics_proven is True
    assert profile.exactly_one_outcome_proven is True
    assert profile.observation_population == "WRH_HOURLY_DATA"
    assert profile.precision == "WHOLE_DEGREE_F"
    assert profile.settlement_value_adapter_ready is False
    assert profile.financial_authority is False
    upgraded = apply_rule_authority(compiled, profile)
    assert upgraded.exactly_one_outcome_proven is True
    assert upgraded.financial_authority is False


def test_current_nws_low_template_is_symmetric_rule_profile():
    event = _nws_event(high=False, hourly=True)
    compiled = compile_weather_event(event)
    profile = compile_temperature_rule_authority(event, compiled)
    assert profile.rule_semantics_proven is True
    assert profile.statistic == "DAILY_LOWEST_TEMP"
    assert profile.exactly_one_outcome_proven is True


def test_nws_london_style_all_times_is_preserved_when_no_hourly_override():
    event = _nws_event(high=True, hourly=False)
    compiled = compile_weather_event(event)
    profile = compile_temperature_rule_authority(event, compiled)
    assert profile.rule_semantics_proven is True
    assert profile.observation_population == "WRH_ALL_TIMES"


def test_nws_missing_revision_cutoff_fails_closed():
    event = _nws_event()
    event["description"] = event["description"].replace(
        "after which any alterations will not be considered.",
        "later revisions may occur.",
    )
    compiled = compile_weather_event(event)
    profile = compile_temperature_rule_authority(event, compiled)
    assert profile.rule_semantics_proven is False
    assert profile.exactly_one_outcome_proven is False
    assert "NWS_CORRECTION_RULE_UNPROVEN" in profile.rejection_reasons


def test_current_hko_high_and_low_templates_recognize_rules_but_quarantine_complete_set_semantics():
    for high in (True, False):
        event = _hko_event(high=high)
        compiled = compile_weather_event(event)
        profile = compile_temperature_rule_authority(event, compiled)
        assert profile.rule_semantics_proven is True
        assert profile.exactly_one_outcome_proven is False
        assert profile.observation_population == "HKO_DAILY_EXTRACT"
        assert profile.precision == "ONE_DECIMAL_C"
        assert profile.correction_policy == "IGNORE_REVISIONS_AFTER_INITIAL_PUBLICATION"
        assert "HKO_DECIMAL_BUCKET_MAPPING_UNPROVEN" in profile.rejection_reasons
        assert profile.settlement_value_adapter_ready is False
        assert profile.financial_authority is False
        upgraded = apply_rule_authority(compiled, profile)
        assert upgraded.exactly_one_outcome_proven is False
        assert upgraded.financial_authority is False


def test_hko_missing_one_decimal_precision_fails_closed():
    event = _hko_event()
    event["description"] = event["description"].replace("one decimal place", "published precision")
    compiled = compile_weather_event(event)
    profile = compile_temperature_rule_authority(event, compiled)
    assert profile.rule_semantics_proven is False
    assert "HKO_PRECISION_RULE_UNPROVEN" in profile.rejection_reasons


def test_shape_gap_prevents_exactly_one_claim_even_with_recognized_rules():
    event = _nws_event()
    event["markets"] = [
        _market("a", "Will the highest temperature be 69°F or lower?"),
        _market("c", "Will the highest temperature be 72°F or higher?"),
    ]
    compiled = compile_weather_event(event)
    profile = compile_temperature_rule_authority(event, compiled)
    assert compiled.partition_shape_complete is False
    assert profile.exactly_one_outcome_proven is False
    assert "BUCKET_PARTITION_SHAPE_UNPROVEN" in profile.rejection_reasons
