from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from polymarket_scanner.models import Market
from polymarket_scanner.weather import (
    ForecastContext,
    ForecastHour,
    Observation,
    ObservationBatch,
    in_bucket,
    is_hourly_observation,
    lock_probability,
    parse_bucket,
    settlement_source_check,
)


def test_bucket_parsing():
    assert parse_bucket("Highest temp 96-97F", "F") == (96.0, 97.0)
    assert parse_bucket("100F or higher", "F") == (100.0, None)
    assert parse_bucket("23C", "C") == (23.0, 23.0)
    assert in_bucket(97, (96, 97))


def test_hourly_filters():
    assert is_hourly_observation(datetime(2026, 9, 5, 15, 51, tzinfo=timezone.utc), "KLGA")
    assert not is_hourly_observation(datetime(2026, 9, 5, 15, 30, tzinfo=timezone.utc), "KLGA")
    assert is_hourly_observation(datetime(2026, 9, 5, 15, 0, tzinfo=timezone.utc), "EGLC")


def _market(source: str) -> Market:
    return Market(
        id="m1", event_id="e1", event_slug="moscow-high", event_title="Highest temperature in Moscow on September 6, 2026",
        event_neg_risk=False, question="Will the highest temperature be 30C?", slug="30c", condition_id="c1",
        outcomes=["Yes", "No"], token_ids=["yes", "no"], outcome_prices=[0.8, 0.2], best_bid=0.79, best_ask=0.8,
        liquidity=1000, volume_24h=5000, active=True, closed=False, end_date="2026-09-06T23:59:00Z",
        description=f"Resolution uses the highest value in the Temp column at {source}", resolution_source=source,
        category="weather", tags=["weather"], raw={},
    )


def _forecast(code: int = 1) -> ForecastContext:
    tz = ZoneInfo("Europe/Moscow")
    return ForecastContext(
        station="UUWW", latitude=55.59, longitude=37.26, timezone_name="Europe/Moscow",
        fetched_at=datetime(2026, 9, 6, 15, 0, tzinfo=timezone.utc),
        hours=[
            ForecastHour(datetime(2026, 9, 6, 18, 0, tzinfo=tz), 27.0, 10, 20, code, 1015, 220, 10),
            ForecastHour(datetime(2026, 9, 6, 19, 0, tzinfo=tz), 26.0, 10, 30, 1, 1016, 225, 10),
            ForecastHour(datetime(2026, 9, 6, 20, 0, tzinfo=tz), 25.0, 10, 40, 1, 1016, 230, 10),
        ],
    )


def _observations(forecast: ForecastContext) -> ObservationBatch:
    rows = [
        Observation(datetime(2026, 9, 6, 10, 0, tzinfo=timezone.utc), 28.0, ""),
        Observation(datetime(2026, 9, 6, 11, 0, tzinfo=timezone.utc), 30.0, ""),
        Observation(datetime(2026, 9, 6, 12, 0, tzinfo=timezone.utc), 29.0, ""),
        Observation(datetime(2026, 9, 6, 13, 0, tzinfo=timezone.utc), 28.0, ""),
        Observation(datetime(2026, 9, 6, 14, 0, tzinfo=timezone.utc), 27.0, ""),
    ]
    return ObservationBatch(rows, forecast=forecast)


def test_exact_wrh_station_required():
    good = _market("https://www.weather.gov/wrh/timeseries?site=UUWW")
    wrong = _market("https://www.weather.gov/wrh/timeseries?site=UUEE")
    assert settlement_source_check(good, "UUWW")["verified"] is True
    assert settlement_source_check(wrong, "UUWW")["verified"] is False


def test_dynamic_timezone_and_remaining_day_forecast_gate():
    market = _market("https://www.weather.gov/wrh/timeseries?site=UUWW")
    now = datetime(2026, 9, 6, 15, 30, tzinfo=timezone.utc)  # 18:30 Moscow
    info = lock_probability(market, _observations(_forecast()), "UUWW", now=now)
    assert info is not None
    assert info["timezone"] == "Europe/Moscow"
    assert info["observed_max"] == 30
    assert info["forecast_remaining_max"] == 27
    assert info["forecast_margin"] == 3
    assert info["settlement_source_verified"] is True


def test_thunderstorm_forecast_blocks_actionable_lock():
    market = _market("https://www.weather.gov/wrh/timeseries?site=UUWW")
    now = datetime(2026, 9, 6, 15, 30, tzinfo=timezone.utc)
    assert lock_probability(market, _observations(_forecast(code=95)), "UUWW", now=now) is None
