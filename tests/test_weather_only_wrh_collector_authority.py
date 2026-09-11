from __future__ import annotations

import inspect

import pytest

from polymarket_scanner.weather_only_wrh_collector_authority import (
    TRUSTED_WRH_COLLECTOR_AUTHORITY_VERSION,
    TrustedWeatherWRHCollectorClockError,
    TrustedWeatherWRHProspectiveCollector,
)


class _Clock:
    def __init__(self, values):
        self.values = list(values)

    def __call__(self):
        if not self.values:
            raise AssertionError("unexpected clock read")
        return self.values.pop(0)


def test_authority_entrypoint_exposes_no_caller_timestamp_parameters():
    register = inspect.signature(TrustedWeatherWRHProspectiveCollector.register_capture)
    tick = inspect.signature(TrustedWeatherWRHProspectiveCollector.tick)
    assert tuple(register.parameters) == ("self", "capture")
    assert tuple(tick.parameters) == ("self",)


def test_trusted_empty_tick_uses_owned_clock_and_preserves_authority_boundary(tmp_path):
    collector = TrustedWeatherWRHProspectiveCollector(
        db_path=tmp_path / "collector.sqlite",
        clock=_Clock([1234.5]),
    )
    try:
        report = collector.tick()
        assert collector.authority_version == TRUSTED_WRH_COLLECTOR_AUTHORITY_VERSION
        assert report.evaluated_station_dates == 0
        assert report.financial_authority is False
        assert report.financial_delivery is False
        assert report.automatic_order_placement is False
        assert collector.financial_authority is False
        assert collector.financial_delivery is False
        assert collector.automatic_order_placement is False
    finally:
        collector.close()


def test_trusted_clock_regression_fails_closed_before_collection(tmp_path):
    collector = TrustedWeatherWRHProspectiveCollector(
        db_path=tmp_path / "collector.sqlite",
        clock=_Clock([2000.0, 1999.0]),
    )
    try:
        collector.tick()
        with pytest.raises(TrustedWeatherWRHCollectorClockError) as raised:
            collector.tick()
        assert raised.value.code == "TRUSTED_COLLECTOR_CLOCK_REGRESSION"
    finally:
        collector.close()


@pytest.mark.parametrize("value", [True, float("nan"), float("inf"), -1.0, "123"])
def test_invalid_trusted_clock_values_fail_closed(tmp_path, value):
    collector = TrustedWeatherWRHProspectiveCollector(
        db_path=tmp_path / f"collector-{repr(value)}.sqlite",
        clock=_Clock([value]),
    )
    try:
        with pytest.raises(TrustedWeatherWRHCollectorClockError) as raised:
            collector.tick()
        assert raised.value.code == "TRUSTED_COLLECTOR_CLOCK_INVALID"
    finally:
        collector.close()


def test_callers_cannot_smuggle_registration_or_tick_time_keywords(tmp_path):
    collector = TrustedWeatherWRHProspectiveCollector(
        db_path=tmp_path / "collector.sqlite",
        clock=_Clock([1000.0]),
    )
    try:
        with pytest.raises(TypeError):
            collector.register_capture(None, registered_at=1.0)  # type: ignore[call-arg]
        with pytest.raises(TypeError):
            collector.tick(now=1.0)  # type: ignore[call-arg]
    finally:
        collector.close()
