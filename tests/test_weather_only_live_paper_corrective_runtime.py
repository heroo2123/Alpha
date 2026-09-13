from __future__ import annotations

import asyncio
import time

from polymarket_scanner.weather_only_live_paper_corrective import (
    SAME_DAY_CAPTURE_COOLDOWN_SECONDS,
    WeatherLivePaperCorrectiveService,
    _CapturingDiscoveryProxy,
)


class _Discovery:
    marker = "delegated"

    def __init__(self):
        self.calls = 0
        self.result = object()

    async def discover(self, *args, **kwargs):
        self.calls += 1
        return self.result


class _CaptureStore:
    def __init__(self, latest):
        self.latest = latest

    def latest_as_of_for_event(self, event_id):
        assert event_id == "event-1"
        return self.latest

    def summary(self):
        return {
            "total": 1,
            "blocked": 1,
            "ready_uncalibrated": 0,
            "included_in_validated_pnl": False,
            "same_day_delivery_enabled": False,
            "financial_authority": False,
        }


def test_discovery_proxy_reuses_exact_base_cycle_generation_and_delegates_attributes():
    source = _Discovery()
    proxy = _CapturingDiscoveryProxy(source)
    returned = asyncio.run(proxy.discover("ignored"))
    assert returned is source.result
    assert proxy.last_result is source.result
    assert source.calls == 1
    assert proxy.marker == "delegated"


def test_persisted_capture_time_skips_network_acquisition_after_process_restart():
    service = object.__new__(WeatherLivePaperCorrectiveService)
    now = time.time()
    service.same_day_captures = _CaptureStore(now - SAME_DAY_CAPTURE_COOLDOWN_SECONDS / 2.0)
    service._same_day_last_attempt = {}

    async def eligible(_events):
        # Remaining tuple fields are intentionally sentinels: a correct persistent
        # cadence gate must skip before touching contract/source acquisition fields.
        return [("event-1", None, None, None, None)], []

    async def must_not_fetch(*args, **kwargs):
        raise AssertionError("network acquisition must not run inside durable cooldown")

    service._same_day_eligible = eligible
    service._fetch_same_day_source_bundle = must_not_fetch
    result = asyncio.run(service._capture_same_day_research(({},)))
    assert result["eligible_events"] == 1
    assert result["attempted_now"] == 0
    assert result["cadence_skipped_now"] == 1
    assert result["saved_now"] == 0
    assert result["capture_cadence_persisted_in_sqlite"] is True
    assert result["telegram_delivery"] is False
    assert result["included_in_validated_pnl"] is False
    assert result["financial_authority"] is False
