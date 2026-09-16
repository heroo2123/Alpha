from __future__ import annotations

from polymarket_scanner.weather_only_wrh_collector import CollectorTickReport
from polymarket_scanner.weather_only_wrh_collector_authority import (
    TrustedWeatherWRHProspectiveCollector,
)


class _Clock:
    def __init__(self, values):
        self.values = list(values)

    def __call__(self):
        if not self.values:
            raise AssertionError("unexpected clock read")
        return self.values.pop(0)


def _failed_upstream_report() -> CollectorTickReport:
    return CollectorTickReport(
        collector_version="lower-test",
        policy_id="lower-test",
        evaluated_station_dates=1,
        fetched_snapshots=0,
        authorized_captures=0,
        failed_captures=0,
        deferred_station_dates=0,
        fetch_errors=("KLGA:2026-09-11:FORECAST_PROVIDER_TIMEOUT",),
        financial_authority=False,
        financial_delivery=False,
        automatic_order_placement=False,
    )


def test_trusted_attempt_throttle_applies_even_when_previous_lower_attempt_failed(tmp_path):
    collector = TrustedWeatherWRHProspectiveCollector(
        db_path=tmp_path / "collector.sqlite",
        clock=_Clock([1000.0, 1001.0, 1029.999, 1030.0]),
    )
    calls = []

    def fake_tick(*, now):
        calls.append(now)
        return _failed_upstream_report()

    collector._collector.tick = fake_tick
    try:
        first = collector.tick()
        assert first.fetch_errors
        assert calls == [1000.0]

        second = collector.tick()
        assert second.collector_version == "trusted_wrapper_attempt_throttled"
        assert second.fetched_snapshots == 0
        assert second.authorized_captures == 0
        assert second.financial_authority is False
        assert calls == [1000.0]

        third = collector.tick()
        assert third.collector_version == "trusted_wrapper_attempt_throttled"
        assert calls == [1000.0]

        fourth = collector.tick()
        assert fourth.fetch_errors
        assert calls == [1000.0, 1030.0]
    finally:
        collector.close()
