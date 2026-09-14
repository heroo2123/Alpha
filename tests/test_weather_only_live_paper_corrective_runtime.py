from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace

import polymarket_scanner.weather_only_live_paper_corrective as runtime_module
from polymarket_scanner.weather_only_live_paper_corrective import (
    SAME_DAY_CAPTURE_COOLDOWN_SECONDS,
    WeatherLivePaperCorrectiveService,
    _CapturingDiscoveryProxy,
)
from polymarket_scanner.weather_only_pws_store import SameDayPWSDiagnosticStore
from polymarket_scanner.weather_only_rules import compile_temperature_rule_authority
from polymarket_scanner.weather_only_same_day_capture_store import SameDayCaptureStore
from polymarket_scanner.weather_only_same_day_contract import build_same_day_contract_semantics
from test_weather_only_conditioned_wrh import AS_OF, _compiled, _snapshot
from test_weather_only_rules import _nws_event
from test_weather_only_same_day_capture import _gefs, _nws_snapshot, _station_metadata


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


class _PWSStore:
    def mark_interrupted_pending(self):
        return 0

    def summary(self):
        return {
            "total": 0,
            "available": 0,
            "contradictions": 0,
            "capture_links_pending": 0,
            "predictive_only": True,
            "may_replace_official_observation": False,
            "may_reweight_probability": False,
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
    service.same_day_pws = _PWSStore()
    service._same_day_last_attempt = {}

    async def eligible(_events):
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
    assert result["pws_saved_now"] == 0
    assert result["pws_predictive_only"] is True
    assert result["pws_may_replace_official_observation"] is False
    assert result["pws_may_reweight_probability"] is False
    assert result["telegram_delivery"] is False
    assert result["financial_authority"] is False


def _service_with_real_stores(tmp_path, monkeypatch):
    monkeypatch.setattr(runtime_module.time, "time", lambda: AS_OF)
    db = tmp_path / "weather-paper.sqlite"
    service = object.__new__(WeatherLivePaperCorrectiveService)
    service.same_day_captures = SameDayCaptureStore(db)
    service.same_day_pws = SameDayPWSDiagnosticStore(db)
    service._same_day_last_attempt = {}

    compiled = _compiled(high=True)
    authority = compile_temperature_rule_authority(_nws_event(high=True, hourly=True), compiled)
    semantics = build_same_day_contract_semantics(compiled, authority)
    metadata = _station_metadata()

    async def eligible(_events):
        return [(str(compiled.event_id), {}, compiled, semantics, metadata)], []

    async def official_bundle(_compiled_value, _metadata_value):
        return SimpleNamespace(snapshot=_snapshot()), _nws_snapshot(), _gefs()

    service._same_day_eligible = eligible
    service._fetch_same_day_source_bundle = official_bundle
    return service


def test_pws_exception_after_official_sources_still_persists_official_capture(tmp_path, monkeypatch):
    service = _service_with_real_stores(tmp_path, monkeypatch)

    async def failing_pws(*args, **kwargs):
        raise RuntimeError("PWS_SYNTHETIC_FAILURE")

    service._fetch_pws_after_capture = failing_pws
    result = asyncio.run(service._capture_same_day_research(({},)))

    assert result["saved_now"] == 1
    assert result["pws_saved_now"] == 0
    assert result["pws_failed_now"] == 1
    assert service.same_day_captures.summary()["total"] == 1
    recent = service.same_day_captures.recent(1)[0]
    link = service.same_day_pws.link_for_capture(recent["capture_sha256"])
    assert link is not None
    assert link["state"] == "FAILED"
    assert link["financial_authority"] == 0


def test_pws_cycle_budget_bounds_optional_diagnostic_delay(tmp_path, monkeypatch):
    service = _service_with_real_stores(tmp_path, monkeypatch)
    monkeypatch.setattr(runtime_module, "SAME_DAY_PWS_CYCLE_BUDGET_SECONDS", 0.03)

    async def hanging_pws(*args, **kwargs):
        await asyncio.sleep(1.0)
        raise AssertionError("outer PWS cycle budget should cancel this")

    service._same_day_pws = SimpleNamespace(fetch_snapshot=hanging_pws)
    # Keep durable PWS outcome store separate from the network client attribute.
    durable = service.same_day_pws
    # Runtime has historically used the same attribute name for store/client only via
    # distinct self.same_day_pws vs self._same_day_pws; restore the store correctly.
    service.same_day_pws = SameDayPWSDiagnosticStore(service.same_day_captures.path)
    service._same_day_pws = SimpleNamespace(fetch_snapshot=hanging_pws)

    started = time.monotonic()
    result = asyncio.run(service._capture_same_day_research(({},)))
    elapsed = time.monotonic() - started

    assert elapsed < 0.5
    assert result["saved_now"] == 1
    assert result["pws_failed_now"] == 1
    assert service.same_day_captures.summary()["total"] == 1
