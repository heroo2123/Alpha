from polymarket_scanner.models import Market
from polymarket_scanner.weather import market_unit, settlement_source_check
from polymarket_scanner.weather_contracts import (
    contract_unit_from_question,
    settlement_safe_market,
    strict_wrh_source,
)


def _market(question: str, source: str, description: str = "") -> Market:
    return Market(
        id="m1",
        event_id="e1",
        event_slug="chicago-high",
        event_title="Highest temperature in Chicago on September 7, 2026",
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


def test_contract_unit_comes_from_question_not_celsius_display_instructions():
    market = _market(
        "Will the highest temperature be 71 F or below?",
        "https://www.weather.gov/wrh/timeseries?site=KORD",
        "Resolution uses the Temp column. The source page can also display Celsius.",
    )
    # This reproduces the old failure mode: the legacy parser sees the rules text.
    assert market_unit(market) == "C"
    assert contract_unit_from_question(market.question) == "F"

    safe = settlement_safe_market(market)
    assert safe is not None
    assert safe.raw["weather_contract_unit"] == "F"
    assert safe.raw["weather_contract_station"] == "KORD"
    assert market_unit(safe) == "F"
    assert safe.description == ""


def test_fake_host_containing_weather_gov_path_is_rejected():
    market = _market(
        "Will the highest temperature be 71 F or below?",
        "https://untrusted.invalid/weather.gov/wrh/timeseries?site=KORD",
    )
    # Document the legacy defect and prove the strict boundary contains it.
    assert settlement_source_check(market, "KORD")["verified"] is True
    assert strict_wrh_source(market)["verified"] is False
    assert settlement_safe_market(market) is None


def test_authoritative_wrh_host_and_station_are_preserved():
    market = _market(
        "Will the highest temperature be between 72 F and 73 F?",
        "https://www.weather.gov/wrh/timeseries?site=KORD",
    )
    source = strict_wrh_source(market)
    assert source["verified"] is True
    assert source["station"] == "KORD"
    assert source["kind"] == "NOAA/NWS WRH strict_v1"


def test_missing_or_conflicting_contract_unit_fails_closed():
    no_unit = _market("Will the highest temperature be 71 or below?", "https://www.weather.gov/wrh/timeseries?site=KORD")
    conflict = _market("Will the highest temperature be 71 F / 22 C?", "https://www.weather.gov/wrh/timeseries?site=KORD")
    assert settlement_safe_market(no_unit) is None
    assert settlement_safe_market(conflict) is None


def test_multiple_distinct_wrh_sources_are_ambiguous_and_rejected():
    market = _market(
        "Will the highest temperature be 71 F or below?",
        "https://www.weather.gov/wrh/timeseries?site=KORD",
        "Fallback https://www.weather.gov/wrh/timeseries?site=KMDW",
    )
    assert strict_wrh_source(market)["verified"] is False
    assert settlement_safe_market(market) is None
