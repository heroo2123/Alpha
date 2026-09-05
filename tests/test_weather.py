from datetime import datetime, timezone

from polymarket_scanner.weather import in_bucket, is_hourly_observation, parse_bucket


def test_bucket_parsing():
    assert parse_bucket("Highest temp 96-97F", "F") == (96.0, 97.0)
    assert parse_bucket("100F or higher", "F") == (100.0, None)
    assert parse_bucket("23C", "C") == (23.0, 23.0)
    assert in_bucket(97, (96, 97))


def test_hourly_filters():
    assert is_hourly_observation(datetime(2026, 9, 5, 15, 51, tzinfo=timezone.utc), "KLGA")
    assert not is_hourly_observation(datetime(2026, 9, 5, 15, 30, tzinfo=timezone.utc), "KLGA")
    assert is_hourly_observation(datetime(2026, 9, 5, 15, 0, tzinfo=timezone.utc), "EGLC")
