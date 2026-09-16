from __future__ import annotations

from polymarket_scanner.weather_only_contracts import compile_weather_event


def _market(mid: str, question: str) -> dict:
    return {
        "id": mid,
        "active": True,
        "closed": False,
        "acceptingOrders": True,
        "enableOrderBook": True,
        "question": question,
        "slug": f"market-{mid}",
        "conditionId": f"condition-{mid}",
        "outcomes": '["Yes","No"]',
        "clobTokenIds": f'["{mid}-yes","{mid}-no"]',
    }


def _wunderground_celsius_event() -> dict:
    description = (
        "This market will resolve to the temperature range that contains the highest temperature recorded at the "
        "Jinan Yaoqiang International Airport Station in degrees Celsius on 20 May '26. "
        "The resolution source is https://www.wunderground.com/history/daily/cn/jinan/ZSJN. "
        "To toggle between Fahrenheit and Celsius, click the gear icon and switch the Temperature setting between °F and °C. "
        "The resolution source measures temperatures to whole degrees Celsius."
    )
    return {
        "id": "497034",
        "slug": "highest-temperature-in-jinan-on-may-20",
        "title": "Highest temperature in Jinan on May 20?",
        "description": description,
        "resolutionSource": "https://www.wunderground.com/history/daily/cn/jinan/ZSJN",
        "markets": [
            {**_market("low", "Will the highest temperature in Jinan be 15°C or below on May 20?"), "description": description},
            {**_market("mid", "Will the highest temperature in Jinan be 16°C on May 20?"), "description": description},
            {**_market("high", "Will the highest temperature in Jinan be 17°C or higher on May 20?"), "description": description},
        ],
    }


def test_ui_toggle_mentions_both_units_but_does_not_create_false_contract_conflict():
    compiled = compile_weather_event(_wunderground_celsius_event())

    assert compiled.unit == "C"
    assert "UNIT_UNRESOLVED_OR_CONFLICT" not in compiled.rejection_reasons
    assert all(bucket.unit == "C" for bucket in compiled.buckets)


def test_real_settlement_unit_conflict_still_fails_closed():
    event = _wunderground_celsius_event()
    event["description"] += " A separate settlement clause says temperatures are measured in degrees Fahrenheit."

    compiled = compile_weather_event(event)

    assert compiled.unit is None
    assert "UNIT_UNRESOLVED_OR_CONFLICT" in compiled.rejection_reasons


def test_mixed_bucket_identity_units_fail_closed_even_if_description_names_one_unit():
    event = _wunderground_celsius_event()
    event["markets"][1]["question"] = "Will the highest temperature in Jinan be 61°F on May 20?"

    compiled = compile_weather_event(event)

    assert compiled.unit is None
    assert "UNIT_UNRESOLVED_OR_CONFLICT" in compiled.rejection_reasons
