from unittest.mock import patch

from polymarket_scanner.models import Book, Market
from polymarket_scanner.weather_friend import friend_style_weather_lock


def _market(question: str, token: str = "yes-token") -> Market:
    return Market(
        id="m1",
        event_id="e1",
        event_slug="highest-temperature-in-test-city",
        event_title="Highest temperature in Test City on September 7?",
        event_neg_risk=False,
        question=question,
        slug="bucket",
        condition_id="c1",
        outcomes=["Yes", "No"],
        token_ids=[token, "no-token"],
        outcome_prices=[0.94, 0.06],
        best_bid=0.93,
        best_ask=0.94,
        liquidity=1000.0,
        volume_24h=10000.0,
        active=True,
        closed=False,
        end_date=None,
        description="NOAA station RJTT https://www.weather.gov/wrh/timeseries?site=rjtt",
        resolution_source="https://www.weather.gov/wrh/timeseries?site=rjtt",
        category="weather",
        tags=["weather"],
        raw={},
    )


def _lock_info(probability: float = 0.965):
    return {
        "probability": probability,
        "observed_max": 24.0,
        "current": 23.0,
        "cooling_obs": 4,
        "observed_drop": 1.0,
        "unit": "C",
        "local_time": "2026-09-07T17:30+09:00",
        "settlement_source_url": "https://www.weather.gov/wrh/timeseries?site=rjtt",
        "forecast_provider": "NOAA/AviationWeather TAF risk forecast",
    }


def test_friend_style_watch_surfaces_strong_lock_below_actionable_ev_gate():
    m = _market("Will the highest temperature be 24C?")
    books = {"yes-token": Book("yes-token", [(0.93, 100)], [(0.94, 100)])}
    with patch("polymarket_scanner.weather_friend.lock_probability", return_value=_lock_info()):
        signals = friend_style_weather_lock([m], books, {"RJTT": [object()]})
    assert len(signals) == 1
    s = signals[0]
    assert s.detector == "weather_friend_lock"
    assert s.confidence == "WATCH"
    assert s.metadata["ask"] == 0.94
    assert s.metadata["model_edge"] < 0.025
    assert s.metadata["net_payout_left"] > 0.05


def test_friend_style_watch_does_not_duplicate_main_actionable_weather():
    m = _market("Will the highest temperature be 24C?")
    books = {"yes-token": Book("yes-token", [(0.89, 100)], [(0.90, 100)])}
    with patch("polymarket_scanner.weather_friend.lock_probability", return_value=_lock_info()):
        signals = friend_style_weather_lock([m], books, {"RJTT": [object()]})
    assert signals == []


def test_friend_style_watch_skips_price_with_too_little_remaining_payout():
    m = _market("Will the highest temperature be 24C?")
    books = {"yes-token": Book("yes-token", [(0.98, 100)], [(0.98, 100)])}
    with patch("polymarket_scanner.weather_friend.lock_probability", return_value=_lock_info()):
        signals = friend_style_weather_lock([m], books, {"RJTT": [object()]})
    assert signals == []
