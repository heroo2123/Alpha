from __future__ import annotations

from polymarket_scanner.models import Market
from polymarket_scanner.weather_rule_tree import DAILY_TEMP_ADAPTER, parse_daily_temperature_contract


def _market(description: str) -> Market:
    return Market(
        id="4361511",
        event_id="987828",
        event_slug="lowest-temperature-in-seattle-on-september-10",
        event_title="Lowest temperature in Seattle on September 10?",
        event_neg_risk=False,
        question="Will the lowest temperature in Seattle be between 56-57°F on September 10?",
        slug="lowest-temperature-seattle-56-57f",
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
        end_date="2026-09-10T12:00:00Z",
        description=description,
        resolution_source="https://www.weather.gov/wrh/timeseries?site=ksea",
        category="Weather",
    )


def _live_rules(revision_sentence: str) -> str:
    return (
        "This market will resolve to the temperature range that contains the lowest temperature recorded by NOAA "
        "at the Seattle-Tacoma International Airport Station in degrees Fahrenheit on 10 Sep '26. "
        "The resolution source is https://www.weather.gov/wrh/timeseries?site=ksea. "
        "If NOAA data for the observation date is unavailable by 11:59 PM ET on the day following the observation date, "
        "the Weather Underground Daily Observations table will be used as the resolution source. "
        "In the event that there is no data for the observation date by 11:59 PM ET on the day following the observation date, "
        "this market will resolve to the lowest bracket. "
        "This market will resolve once the first data point for the following date has been published on the resolution source, "
        "or by 11:59 PM ET on the day following the observation date, whichever comes first. "
        + revision_sentence
    )


def test_live_polymarket_revision_word_order_is_supported_without_weakening_boundary():
    market = _market(_live_rules(
        "Revisions to temperatures recorded within this market's timeframe will be considered until the first datapoint "
        "for the following date has been published, after which any alterations will not be considered."
    ))

    result = parse_daily_temperature_contract(market)

    assert result.supported is True
    assert result.adapter_version == DAILY_TEMP_ADAPTER
    assert result.failure_code is None
    assert result.contract is not None
    assert result.contract.primary.station == "KSEA"
    assert result.contract.revisions_before_finalization_count is True


def test_revision_clause_without_following_day_datapoint_boundary_still_fails_closed():
    market = _market(_live_rules(
        "Revisions to temperatures may be considered until the market resolves."
    ))

    result = parse_daily_temperature_contract(market)

    assert result.supported is False
    assert result.failure_code == "REVISION_RULE"
