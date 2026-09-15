from __future__ import annotations

import asyncio
import importlib.util
from pathlib import Path

import pytest

from polymarket_scanner.weather_only_live_paper_corrective import (
    WeatherLivePaperCorrectiveService,
)
from polymarket_scanner.weather_only_live_paper_final import FinalWeatherLivePaperService
from polymarket_scanner.weather_only_live_paper_three_layer_validation import (
    THREE_LAYER_31D_CAPTURE_ROW_BOUND,
    THREE_LAYER_ATTEMPT_ROW_CAP,
    THREE_LAYER_CAPTURE_JSON_BYTES_CAP,
    THREE_LAYER_COLLECTION_VERSION,
    THREE_LAYER_ELIGIBILITY_SCAN_DEADLINE_SECONDS,
    THREE_LAYER_ELIGIBILITY_STATION_CAP,
    THREE_LAYER_MAX_EVENTS_PER_CYCLE,
    THREE_LAYER_SELECTION_POLICY,
    THREE_LAYER_SELECTION_UNIVERSE_CAP,
    THREE_LAYER_SOURCE_BUNDLE_DEADLINE_SECONDS,
    THREE_LAYER_STATION_METADATA_CONCURRENCY,
    THREE_LAYER_VALIDATION_RUNTIME_VERSION,
    ThreeLayerValidationWeatherLivePaperService,
)
from polymarket_scanner.weather_only_live_paper_v4 import WeatherLivePaperV4Service
from polymarket_scanner.weather_only_same_day_capture_store_compressed import (
    COMPRESSED_CAPTURE_STORE_VERSION,
    CURRENT_CAPTURE_ENCODING,
    LEGACY_CAPTURE_ENCODING,
    MAX_COMPRESSED_CAPTURE_BYTES,
    MAX_UNCOMPRESSED_CAPTURE_BYTES,
)


class AsyncCloser:
    def __init__(self) -> None:
        self.closed = 0

    async def close(self) -> None:
        self.closed += 1


class SyncCloser:
    def __init__(self, *, fail: bool = False) -> None:
        self.closed = 0
        self.fail = fail

    def close(self) -> None:
        self.closed += 1
        if self.fail:
            raise RuntimeError("CLOSE_FAIL")


def test_corrective_close_releases_wrh_even_when_wrh_close_raises(monkeypatch):
    service = object.__new__(WeatherLivePaperCorrectiveService)
    service._same_day_wrh = SyncCloser(fail=True)
    service._same_day_nws = AsyncCloser()
    service._same_day_gefs = AsyncCloser()
    service._canonical_superseded_settlement = AsyncCloser()
    service._canonical_superseded_commands = AsyncCloser()
    parent_calls = []

    async def parent_close(_self):
        parent_calls.append(True)

    monkeypatch.setattr(WeatherLivePaperV4Service, "close", parent_close)
    asyncio.run(service.close())
    assert service._same_day_wrh.closed == 1
    assert service._same_day_nws.closed == 1
    assert service._same_day_gefs.closed == 1
    assert service._canonical_superseded_settlement.closed == 1
    assert service._canonical_superseded_commands.closed == 1
    assert parent_calls == [True]


def test_three_layer_close_releases_all_superseded_sources_before_parent(monkeypatch):
    service = object.__new__(ThreeLayerValidationWeatherLivePaperService)
    service._three_layer_superseded_wrh = SyncCloser(fail=True)
    service._three_layer_superseded_nws = AsyncCloser()
    service._three_layer_superseded_gefs = AsyncCloser()
    parent_calls = []

    async def parent_close(_self):
        parent_calls.append(True)

    monkeypatch.setattr(FinalWeatherLivePaperService, "close", parent_close)
    asyncio.run(service.close())
    assert service._three_layer_superseded_wrh.closed == 1
    assert service._three_layer_superseded_nws.closed == 1
    assert service._three_layer_superseded_gefs.closed == 1
    assert parent_calls == [True]


def _verifier():
    path = Path("deploy/verify-three-layer-validation-status.py")
    spec = importlib.util.spec_from_file_location("three_layer_status_verifier", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _fresh_capture_verifier():
    path = Path("deploy/verify-three-layer-fresh-capture.py")
    spec = importlib.util.spec_from_file_location("three_layer_fresh_capture_verifier", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _valid_status():
    return {
        "release_sha": "a" * 40,
        "finished_at": 1000.0,
        "three_layer_validation_runtime_version": THREE_LAYER_VALIDATION_RUNTIME_VERSION,
        "pws_enabled": False,
        "population_alignment_certified": False,
        "same_day_delivery_enabled": False,
        "financial_delivery": False,
        "financial_authority": False,
        "automatic_order_placement": False,
        "same_day_three_layer": {
            "version": THREE_LAYER_COLLECTION_VERSION,
            "enabled": True,
            "silent_research_only": True,
            "selection_policy": THREE_LAYER_SELECTION_POLICY,
            "eligible_events_total": 4,
            "selected_event_ids": ["event-1", "event-2", "event-3", "event-4"],
            "selection_universe_cap": THREE_LAYER_SELECTION_UNIVERSE_CAP,
            "selection_universe_truncated": False,
            "selection_coverage_complete": True,
            "eligibility_station_count": 1,
            "eligibility_station_cap": THREE_LAYER_ELIGIBILITY_STATION_CAP,
            "station_metadata_concurrency": THREE_LAYER_STATION_METADATA_CONCURRENCY,
            "max_events_per_cycle": THREE_LAYER_MAX_EVENTS_PER_CYCLE,
            "source_bundle_deadline_seconds": THREE_LAYER_SOURCE_BUNDLE_DEADLINE_SECONDS,
            "eligibility_scan_deadline_seconds": THREE_LAYER_ELIGIBILITY_SCAN_DEADLINE_SECONDS,
            "theoretical_31_day_row_bound_at_full_daily_eligibility": THREE_LAYER_31D_CAPTURE_ROW_BOUND,
            "capture_json_bytes_cap": THREE_LAYER_CAPTURE_JSON_BYTES_CAP,
            "attempt_row_cap": THREE_LAYER_ATTEMPT_ROW_CAP,
            "eligible_events": 4,
            "attempted_now": 4,
            "saved_now": 4,
            "duplicates_now": 0,
            "cadence_skipped_now": 0,
            "blocked_now": 4,
            "ready_uncalibrated_now": 0,
            "capture_cadence_persisted_in_sqlite": True,
            "attempt_audit_persisted_in_sqlite": True,
            "attempt_recovery_at_startup": 0,
            "errors": [],
            "store": {
                "version": COMPRESSED_CAPTURE_STORE_VERSION,
                "max_capture_rows": THREE_LAYER_31D_CAPTURE_ROW_BOUND,
                "max_capture_json_bytes": THREE_LAYER_CAPTURE_JSON_BYTES_CAP,
                "capture_storage_encoding_current": CURRENT_CAPTURE_ENCODING,
                "legacy_capture_encoding": LEGACY_CAPTURE_ENCODING,
                "max_uncompressed_capture_bytes": MAX_UNCOMPRESSED_CAPTURE_BYTES,
                "max_compressed_capture_bytes": MAX_COMPRESSED_CAPTURE_BYTES,
                "unknown_encoding_rows": 0,
                "lossless_compression": True,
                "read_time_digest_verification": True,
                "read_time_sql_identity_verification": True,
                "read_time_single_snapshot": True,
                "attempt_capacity_admission_atomic": True,
                "bounded_decompression": True,
                "legacy_uncompressed_read_compatible": True,
                "capture_storage_bytes": 0,
                "capture_capacity_exhausted": False,
                "automatic_evidence_pruning": False,
                "included_in_validated_pnl": False,
                "same_day_delivery_enabled": False,
                "financial_authority": False,
                "attempts": {
                    "max_attempt_rows": THREE_LAYER_ATTEMPT_ROW_CAP,
                    "capacity_exhausted": False,
                    "started": 0,
                    "included_in_validated_pnl": False,
                    "same_day_delivery_enabled": False,
                    "financial_authority": False,
                },
            },
            "population_alignment_certified": False,
            "calibrated_probability": False,
            "included_in_validated_pnl": False,
            "telegram_delivery": False,
            "financial_authority": False,
        },
    }


def _verify(status):
    module = _verifier()
    return module.verify(
        status,
        release_sha="a" * 40,
        not_before=999.0,
        now=1001.0,
        max_age_seconds=600.0,
    )


def _verify_fresh(status):
    module = _fresh_capture_verifier()
    return module.verify_fresh_capture(
        status,
        release_sha="a" * 40,
        not_before=999.0,
        now=1001.0,
        max_age_seconds=600.0,
    )


def test_status_verifier_accepts_complete_bounded_silent_state():
    result = _verify(_valid_status())
    assert result["acceptance"] == "PASS_THREE_LAYER_VALIDATION_RUNTIME_STATUS"


def test_fresh_capture_verifier_accepts_actual_post_start_saved_capture():
    result = _verify_fresh(_valid_status())
    assert result["acceptance"] == "PASS_THREE_LAYER_FRESH_CAPTURE_AFTER_START"
    assert result["fresh_live_source_capture_proven"] is True
    assert result["saved_now"] == 4


def test_fresh_capture_verifier_rejects_safe_but_zero_attempt_cycle():
    status = _valid_status()
    lane = status["same_day_three_layer"]
    lane["eligible_events_total"] = 0
    lane["selected_event_ids"] = []
    lane["eligible_events"] = 0
    lane["attempted_now"] = 0
    lane["saved_now"] = 0
    lane["blocked_now"] = 0
    with pytest.raises(Exception, match="THREE_LAYER_FRESH_NO_ATTEMPT_AFTER_START"):
        _verify_fresh(status)


def test_fresh_capture_verifier_rejects_cadence_only_cycle_for_persistence():
    status = _valid_status()
    lane = status["same_day_three_layer"]
    lane["attempted_now"] = 0
    lane["saved_now"] = 0
    lane["cadence_skipped_now"] = 4
    lane["blocked_now"] = 0
    with pytest.raises(Exception, match="THREE_LAYER_FRESH_NO_ATTEMPT_AFTER_START"):
        _verify_fresh(status)


def test_fresh_capture_verifier_rejects_status_from_before_candidate_start():
    status = _valid_status()
    module = _fresh_capture_verifier()
    with pytest.raises(Exception, match="THREE_LAYER_STATUS_PREDATES_START"):
        module.verify_fresh_capture(
            status,
            release_sha="a" * 40,
            not_before=1000.5,
            now=1001.0,
            max_age_seconds=600.0,
        )


@pytest.mark.parametrize(
    ("path", "value", "code"),
    [
        (("selection_universe_truncated",), True, "THREE_LAYER_SELECTION_UNIVERSE_TRUNCATED"),
        (
            ("eligible_events_total",),
            THREE_LAYER_SELECTION_UNIVERSE_CAP + 1,
            "THREE_LAYER_ELIGIBLE_TOTAL_EXCEEDS_CAP",
        ),
        (("max_events_per_cycle",), 5, "THREE_LAYER_MAX_EVENTS_PER_CYCLE_MISMATCH"),
        (("selected_event_ids",), ["event-1"] * 4, "THREE_LAYER_SELECTED_IDS_DUPLICATE"),
        (("capture_cadence_persisted_in_sqlite",), False, "THREE_LAYER_DURABLE_CADENCE_NOT_PROVEN"),
        (("attempt_audit_persisted_in_sqlite",), False, "THREE_LAYER_DURABLE_ATTEMPT_AUDIT_NOT_PROVEN"),
        (("store", "read_time_single_snapshot"), False, "THREE_LAYER_STORE_SINGLE_SNAPSHOT_NOT_PROVEN"),
        (("store", "attempt_capacity_admission_atomic"), False, "THREE_LAYER_STORE_ATTEMPT_CAP_ATOMICITY_NOT_PROVEN"),
        (("store", "capture_capacity_exhausted"), None, "THREE_LAYER_STORE_CAPTURE_CAPACITY_NOT_HEALTHY"),
        (("store", "attempts", "capacity_exhausted"), None, "THREE_LAYER_STORE_ATTEMPT_CAPACITY_NOT_HEALTHY"),
        (("store", "attempts", "started"), 1, "THREE_LAYER_ATTEMPT_LEFT_STARTED"),
        (("store", "automatic_evidence_pruning"), True, "THREE_LAYER_STORE_PRUNING_POLICY_INVALID"),
    ],
)
def test_status_verifier_fails_closed_on_missing_or_unsafe_runtime_invariants(path, value, code):
    status = _valid_status()
    target = status["same_day_three_layer"]
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value
    with pytest.raises(Exception, match=code):
        _verify(status)


def test_status_verifier_rejects_counter_inconsistency_even_without_error_strings():
    status = _valid_status()
    status["same_day_three_layer"]["attempted_now"] = 3
    with pytest.raises(Exception, match="THREE_LAYER_ATTEMPT_ACCOUNTING_MISMATCH"):
        _verify(status)


def test_deployment_attester_and_renderer_pin_the_same_three_layer_entrypoint():
    def load(path: str, name: str):
        spec = importlib.util.spec_from_file_location(name, Path(path))
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    attester = load("deploy/attest-weather-paper-runtime.py", "weather_attester_exact")
    renderer = load("deploy/render-weather-paper-unit.py", "weather_renderer_exact")
    expected = "polymarket_scanner.weather_only_live_paper_three_layer_validation"
    assert attester.FINAL_WEATHER_MODULE == expected
    assert renderer.FINAL_WEATHER_MODULE == expected
    assert "weather_only_live_paper_three_layer_validation.py" in attester.KNOWN_WEATHER_WRITER_MARKERS


def test_status_verifier_requires_eligibility_scan_deadline_identity():
    status = _valid_status()
    status["same_day_three_layer"].pop("eligibility_scan_deadline_seconds")
    with pytest.raises(Exception, match="THREE_LAYER_ELIGIBILITY_DEADLINE_MISMATCH"):
        _verify(status)


def test_three_layer_eligibility_uses_one_frozen_utc_clock_for_all_station_dates():
    source = Path(
        "polymarket_scanner/weather_only_live_paper_three_layer_validation.py"
    ).read_text(encoding="utf-8")
    assert "eligibility_now_utc = datetime.now(tz=timezone.utc)" in source
    assert "eligibility_now_utc.astimezone(zones_by_station[station]).date()" in source
    assert "local_today = datetime.now(tz=zone).date()" not in source