from __future__ import annotations

from pathlib import Path


def rep(path: str, old: str, new: str, count: int = 1) -> None:
    p = Path(path)
    text = p.read_text(encoding="utf-8")
    found = text.count(old)
    if found < count:
        raise SystemExit(
            f"{path}: expected at least {count} occurrences, found {found}: {old[:120]!r}"
        )
    p.write_text(text.replace(old, new, count), encoding="utf-8")


# Resource hygiene: the base corrective runtime owns a synchronous WRH client too.
# Closing only NWS/GEFS leaves that transport pool open for direct corrective/final
# runtimes. One failing close must never prevent the remaining async pools and parent
# runtime from being released.
rep(
    "polymarket_scanner/weather_only_live_paper_corrective.py",
    "    async def close(self) -> None:\n"
    "        await asyncio.gather(\n"
    "            self._canonical_superseded_settlement.close(),\n"
    "            self._canonical_superseded_commands.close(),\n"
    "            self._same_day_nws.close(),\n"
    "            self._same_day_gefs.close(),\n"
    "            return_exceptions=True,\n"
    "        )\n"
    "        await super().close()",
    "    async def close(self) -> None:\n"
    "        try:\n"
    "            self._same_day_wrh.close()\n"
    "        except Exception:\n"
    "            pass\n"
    "        await asyncio.gather(\n"
    "            self._canonical_superseded_settlement.close(),\n"
    "            self._canonical_superseded_commands.close(),\n"
    "            self._same_day_nws.close(),\n"
    "            self._same_day_gefs.close(),\n"
    "            return_exceptions=True,\n"
    "        )\n"
    "        await super().close()",
)

# The three-layer wrapper replaces all three inherited clients. Preserve and close the
# inherited WRH client as well; then let the parent chain close the active guarded set.
rep(
    "polymarket_scanner/weather_only_live_paper_three_layer_validation.py",
    "        self._three_layer_superseded_nws = self._same_day_nws\n"
    "        self._three_layer_superseded_gefs = self._same_day_gefs\n"
    "        self._same_day_wrh = guarded_wrh",
    "        self._three_layer_superseded_wrh = self._same_day_wrh\n"
    "        self._three_layer_superseded_nws = self._same_day_nws\n"
    "        self._three_layer_superseded_gefs = self._same_day_gefs\n"
    "        self._same_day_wrh = guarded_wrh",
)
rep(
    "polymarket_scanner/weather_only_live_paper_three_layer_validation.py",
    "    async def close(self) -> None:\n"
    "        try:\n"
    "            self._same_day_wrh.close()\n"
    "        except Exception:\n"
    "            pass\n"
    "        await asyncio.gather(\n"
    "            self._three_layer_superseded_nws.close(),\n"
    "            self._three_layer_superseded_gefs.close(),\n"
    "            return_exceptions=True,\n"
    "        )\n"
    "        await super().close()",
    "    async def close(self) -> None:\n"
    "        try:\n"
    "            self._three_layer_superseded_wrh.close()\n"
    "        except Exception:\n"
    "            pass\n"
    "        await asyncio.gather(\n"
    "            self._three_layer_superseded_nws.close(),\n"
    "            self._three_layer_superseded_gefs.close(),\n"
    "            return_exceptions=True,\n"
    "        )\n"
    "        await super().close()",
)

# Deployment acceptance must prove the bounded selector/audit/store invariants rather
# than accepting missing or weakly typed fields.
rep(
    "deploy/verify-three-layer-validation-status.py",
    "    THREE_LAYER_CAPTURE_JSON_BYTES_CAP,\n"
    "    THREE_LAYER_ATTEMPT_ROW_CAP,\n"
    "    THREE_LAYER_SELECTION_POLICY,",
    "    THREE_LAYER_CAPTURE_JSON_BYTES_CAP,\n"
    "    THREE_LAYER_ATTEMPT_ROW_CAP,\n"
    "    THREE_LAYER_MAX_EVENTS_PER_CYCLE,\n"
    "    THREE_LAYER_SELECTION_POLICY,",
)
rep(
    "deploy/verify-three-layer-validation-status.py",
    "def _require_false(payload: dict, key: str, code: str) -> None:\n"
    "    if payload.get(key) is not False:\n"
    "        raise ThreeLayerStatusError(code)\n\n\n"
    "def verify(",
    "def _require_false(payload: dict, key: str, code: str) -> None:\n"
    "    if payload.get(key) is not False:\n"
    "        raise ThreeLayerStatusError(code)\n\n\n"
    "def _nonnegative_int(value: object, code: str) -> int:\n"
    "    if isinstance(value, bool) or not isinstance(value, int) or value < 0:\n"
    "        raise ThreeLayerStatusError(code)\n"
    "    return value\n\n\n"
    "def verify(",
)
old_block = '''    if lane.get("selection_universe_cap") != THREE_LAYER_SELECTION_UNIVERSE_CAP:
        raise ThreeLayerStatusError("THREE_LAYER_SELECTION_CAP_MISMATCH")
    if lane.get("selection_coverage_complete") is not True:
        raise ThreeLayerStatusError("THREE_LAYER_SELECTION_UNIVERSE_TRUNCATED")
    if lane.get("source_bundle_deadline_seconds") != THREE_LAYER_SOURCE_BUNDLE_DEADLINE_SECONDS:
        raise ThreeLayerStatusError("THREE_LAYER_SOURCE_DEADLINE_MISMATCH")
'''
new_block = '''    if lane.get("selection_universe_cap") != THREE_LAYER_SELECTION_UNIVERSE_CAP:
        raise ThreeLayerStatusError("THREE_LAYER_SELECTION_CAP_MISMATCH")
    if lane.get("selection_universe_truncated") is not False:
        raise ThreeLayerStatusError("THREE_LAYER_SELECTION_UNIVERSE_TRUNCATED")
    if lane.get("selection_coverage_complete") is not True:
        raise ThreeLayerStatusError("THREE_LAYER_SELECTION_UNIVERSE_TRUNCATED")
    eligible_total = _nonnegative_int(
        lane.get("eligible_events_total"), "THREE_LAYER_ELIGIBLE_TOTAL_INVALID"
    )
    if eligible_total > THREE_LAYER_SELECTION_UNIVERSE_CAP:
        raise ThreeLayerStatusError("THREE_LAYER_ELIGIBLE_TOTAL_EXCEEDS_CAP")
    if lane.get("max_events_per_cycle") != THREE_LAYER_MAX_EVENTS_PER_CYCLE:
        raise ThreeLayerStatusError("THREE_LAYER_MAX_EVENTS_PER_CYCLE_MISMATCH")
    selected_ids = lane.get("selected_event_ids")
    if not isinstance(selected_ids, list) or any(
        not isinstance(value, str) or not value.strip() for value in selected_ids
    ):
        raise ThreeLayerStatusError("THREE_LAYER_SELECTED_IDS_INVALID")
    if len(selected_ids) != len(set(selected_ids)):
        raise ThreeLayerStatusError("THREE_LAYER_SELECTED_IDS_DUPLICATE")
    if len(selected_ids) > THREE_LAYER_MAX_EVENTS_PER_CYCLE or len(selected_ids) > eligible_total:
        raise ThreeLayerStatusError("THREE_LAYER_SELECTED_IDS_COUNT_INVALID")
    selected_count = _nonnegative_int(
        lane.get("eligible_events"), "THREE_LAYER_SELECTED_EVENT_COUNT_INVALID"
    )
    if selected_count != len(selected_ids):
        raise ThreeLayerStatusError("THREE_LAYER_SELECTED_EVENT_COUNT_MISMATCH")
    if lane.get("source_bundle_deadline_seconds") != THREE_LAYER_SOURCE_BUNDLE_DEADLINE_SECONDS:
        raise ThreeLayerStatusError("THREE_LAYER_SOURCE_DEADLINE_MISMATCH")
'''
rep("deploy/verify-three-layer-validation-status.py", old_block, new_block)
old_store = '''    attempts = store.get("attempts")
    if not isinstance(attempts, dict) or attempts.get("max_attempt_rows") != THREE_LAYER_ATTEMPT_ROW_CAP:
        raise ThreeLayerStatusError("THREE_LAYER_STORE_ATTEMPT_CAP_MISMATCH")
    if store.get("capture_capacity_exhausted") is True or attempts.get("capacity_exhausted") is True:
        raise ThreeLayerStatusError("THREE_LAYER_STORE_CAPACITY_EXHAUSTED")
    if list(lane.get("errors") or []):
        raise ThreeLayerStatusError("THREE_LAYER_SOURCE_ERRORS_PRESENT")
'''
new_store = '''    attempts = store.get("attempts")
    if not isinstance(attempts, dict) or attempts.get("max_attempt_rows") != THREE_LAYER_ATTEMPT_ROW_CAP:
        raise ThreeLayerStatusError("THREE_LAYER_STORE_ATTEMPT_CAP_MISMATCH")
    if store.get("capture_capacity_exhausted") is not False:
        raise ThreeLayerStatusError("THREE_LAYER_STORE_CAPTURE_CAPACITY_NOT_HEALTHY")
    if attempts.get("capacity_exhausted") is not False:
        raise ThreeLayerStatusError("THREE_LAYER_STORE_ATTEMPT_CAPACITY_NOT_HEALTHY")
    if store.get("automatic_evidence_pruning") is not False:
        raise ThreeLayerStatusError("THREE_LAYER_STORE_PRUNING_POLICY_INVALID")
    _require_false(store, "included_in_validated_pnl", "THREE_LAYER_STORE_PNL_NOT_FALSE")
    _require_false(store, "same_day_delivery_enabled", "THREE_LAYER_STORE_DELIVERY_NOT_FALSE")
    _require_false(store, "financial_authority", "THREE_LAYER_STORE_FINANCIAL_NOT_FALSE")
    _require_false(attempts, "included_in_validated_pnl", "THREE_LAYER_ATTEMPT_PNL_NOT_FALSE")
    _require_false(attempts, "same_day_delivery_enabled", "THREE_LAYER_ATTEMPT_DELIVERY_NOT_FALSE")
    _require_false(attempts, "financial_authority", "THREE_LAYER_ATTEMPT_FINANCIAL_NOT_FALSE")
    if _nonnegative_int(attempts.get("started"), "THREE_LAYER_ATTEMPT_STARTED_INVALID") != 0:
        raise ThreeLayerStatusError("THREE_LAYER_ATTEMPT_LEFT_STARTED")
    if lane.get("capture_cadence_persisted_in_sqlite") is not True:
        raise ThreeLayerStatusError("THREE_LAYER_DURABLE_CADENCE_NOT_PROVEN")
    if lane.get("attempt_audit_persisted_in_sqlite") is not True:
        raise ThreeLayerStatusError("THREE_LAYER_DURABLE_ATTEMPT_AUDIT_NOT_PROVEN")
    _nonnegative_int(
        lane.get("attempt_recovery_at_startup"), "THREE_LAYER_ATTEMPT_RECOVERY_INVALID"
    )
    errors = lane.get("errors")
    if not isinstance(errors, list):
        raise ThreeLayerStatusError("THREE_LAYER_ERRORS_TYPE_INVALID")
    if errors:
        raise ThreeLayerStatusError("THREE_LAYER_SOURCE_ERRORS_PRESENT")

    attempted = _nonnegative_int(lane.get("attempted_now"), "THREE_LAYER_ATTEMPTED_NOW_INVALID")
    saved = _nonnegative_int(lane.get("saved_now"), "THREE_LAYER_SAVED_NOW_INVALID")
    duplicates = _nonnegative_int(lane.get("duplicates_now"), "THREE_LAYER_DUPLICATES_NOW_INVALID")
    cadence_skipped = _nonnegative_int(
        lane.get("cadence_skipped_now"), "THREE_LAYER_CADENCE_SKIPPED_INVALID"
    )
    blocked = _nonnegative_int(lane.get("blocked_now"), "THREE_LAYER_BLOCKED_NOW_INVALID")
    ready = _nonnegative_int(
        lane.get("ready_uncalibrated_now"), "THREE_LAYER_READY_NOW_INVALID"
    )
    if attempted != saved + duplicates:
        raise ThreeLayerStatusError("THREE_LAYER_ATTEMPT_ACCOUNTING_MISMATCH")
    if blocked + ready != saved + duplicates:
        raise ThreeLayerStatusError("THREE_LAYER_CAPTURE_STATUS_ACCOUNTING_MISMATCH")
    if selected_count != attempted + cadence_skipped:
        raise ThreeLayerStatusError("THREE_LAYER_SELECTION_ACCOUNTING_MISMATCH")
'''
rep("deploy/verify-three-layer-validation-status.py", old_store, new_store)

# Behavioral cleanup and verifier regressions.
Path("tests/test_weather_only_three_layer_finalization.py").write_text(
    '''from __future__ import annotations

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
    THREE_LAYER_MAX_EVENTS_PER_CYCLE,
    THREE_LAYER_SELECTION_POLICY,
    THREE_LAYER_SELECTION_UNIVERSE_CAP,
    THREE_LAYER_SOURCE_BUNDLE_DEADLINE_SECONDS,
    THREE_LAYER_VALIDATION_RUNTIME_VERSION,
    ThreeLayerValidationWeatherLivePaperService,
)
from polymarket_scanner.weather_only_live_paper_v4 import WeatherLivePaperV4Service


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
            "version": "same_day_three_layer_silent_collection_v3_guarded_rotating",
            "enabled": True,
            "silent_research_only": True,
            "selection_policy": THREE_LAYER_SELECTION_POLICY,
            "eligible_events_total": 4,
            "selected_event_ids": ["event-1", "event-2", "event-3", "event-4"],
            "selection_universe_cap": THREE_LAYER_SELECTION_UNIVERSE_CAP,
            "selection_universe_truncated": False,
            "selection_coverage_complete": True,
            "max_events_per_cycle": THREE_LAYER_MAX_EVENTS_PER_CYCLE,
            "source_bundle_deadline_seconds": THREE_LAYER_SOURCE_BUNDLE_DEADLINE_SECONDS,
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
                "max_capture_rows": THREE_LAYER_31D_CAPTURE_ROW_BOUND,
                "max_capture_json_bytes": THREE_LAYER_CAPTURE_JSON_BYTES_CAP,
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


def test_status_verifier_accepts_complete_bounded_silent_state():
    result = _verify(_valid_status())
    assert result["acceptance"] == "PASS_THREE_LAYER_VALIDATION_RUNTIME_STATUS"


@pytest.mark.parametrize(
    ("path", "value", "code"),
    [
        (("selection_universe_truncated",), True, "THREE_LAYER_SELECTION_UNIVERSE_TRUNCATED"),
        (("eligible_events_total",), 13, "THREE_LAYER_ELIGIBLE_TOTAL_EXCEEDS_CAP"),
        (("max_events_per_cycle",), 5, "THREE_LAYER_MAX_EVENTS_PER_CYCLE_MISMATCH"),
        (("selected_event_ids",), ["event-1"] * 4, "THREE_LAYER_SELECTED_IDS_DUPLICATE"),
        (("capture_cadence_persisted_in_sqlite",), False, "THREE_LAYER_DURABLE_CADENCE_NOT_PROVEN"),
        (("attempt_audit_persisted_in_sqlite",), False, "THREE_LAYER_DURABLE_ATTEMPT_AUDIT_NOT_PROVEN"),
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
''',
    encoding="utf-8",
)

print("meticulous three-layer finalization patch applied")
