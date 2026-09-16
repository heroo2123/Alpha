from __future__ import annotations

import asyncio
import time
from datetime import date
from types import SimpleNamespace as NS

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

    def latest_attempt_at_for_event(self, event_id):
        assert event_id == "event-1"
        return None

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
    assert result["attempt_audit_persisted_in_sqlite"] is True
    assert result["telegram_delivery"] is False
    assert result["included_in_validated_pnl"] is False
    assert result["financial_authority"] is False



class _AttemptCaptureStore:
    def __init__(self, *, latest_capture=None, latest_attempt=None):
        self.latest_capture = latest_capture
        self.latest_attempt = latest_attempt
        self.started = []
        self.finished = []

    def latest_as_of_for_event(self, _event_id):
        return self.latest_capture

    def latest_attempt_at_for_event(self, _event_id):
        return self.latest_attempt

    def start_attempt(self, **kwargs):
        self.started.append(dict(kwargs))
        return len(self.started)

    def finish_attempt(self, attempt_id, **kwargs):
        self.finished.append((attempt_id, dict(kwargs)))

    def summary(self):
        return {
            "total": 0,
            "blocked": 0,
            "ready_uncalibrated": 0,
            "included_in_validated_pnl": False,
            "same_day_delivery_enabled": False,
            "financial_authority": False,
        }


def _eligible_rows_for_same_bundle(count=2):
    compiled = NS(
        station_hint="KLGA",
        target_date=date(2026, 9, 14),
        family="DAILY_HIGH",
        unit="F",
    )
    metadata = NS(timezone="UTC", latitude=40.7769, longitude=-73.8740)
    return [
        (f"event-{index}", {}, compiled, object(), metadata)
        for index in range(count)
    ]


def test_durable_failed_attempt_time_skips_network_after_restart():
    service = object.__new__(WeatherLivePaperCorrectiveService)
    now = time.time()
    service.same_day_captures = _AttemptCaptureStore(
        latest_attempt=now - SAME_DAY_CAPTURE_COOLDOWN_SECONDS / 2.0
    )

    async def eligible(_events):
        return [("event-1", None, None, None, None)], []

    async def must_not_fetch(*_args, **_kwargs):
        raise AssertionError("failed-attempt cooldown must survive restart")

    service._same_day_eligible = eligible
    service._fetch_same_day_source_bundle = must_not_fetch
    result = asyncio.run(service._capture_same_day_research(({},)))
    assert result["attempted_now"] == 0
    assert result["cadence_skipped_now"] == 1
    assert result["errors"] == []


def test_same_bundle_source_failure_is_attempt_audited_and_not_refetched_in_cycle():
    service = object.__new__(WeatherLivePaperCorrectiveService)
    service.same_day_captures = _AttemptCaptureStore()
    service._same_day_attempt_recovery = 0
    calls = 0

    async def eligible(_events):
        return _eligible_rows_for_same_bundle(2), []

    async def fail_bundle(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        raise RuntimeError("SOURCE_FAIL")

    service._same_day_eligible = eligible
    service._fetch_same_day_source_bundle = fail_bundle
    result = asyncio.run(service._capture_same_day_research(({},)))
    assert calls == 1
    assert result["attempted_now"] == 2
    assert len(service.same_day_captures.started) == 2
    assert len(service.same_day_captures.finished) == 2
    assert all(row[1]["outcome"] == "FAILED" for row in service.same_day_captures.finished)
    assert all(row[1]["error_code"] == "RuntimeError" for row in service.same_day_captures.finished)
