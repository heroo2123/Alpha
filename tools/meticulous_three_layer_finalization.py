from __future__ import annotations

from pathlib import Path


def rep(path: str, old: str, new: str, *, expected: int = 1) -> None:
    p = Path(path)
    text = p.read_text(encoding="utf-8")
    found = text.count(old)
    if found != expected:
        raise SystemExit(
            f"{path}: expected exactly {expected} occurrences, found {found}: {old[:160]!r}"
        )
    p.write_text(text.replace(old, new, expected), encoding="utf-8")


def append(path: str, text: str) -> None:
    p = Path(path)
    original = p.read_text(encoding="utf-8")
    sentinel = text.strip().splitlines()[0]
    if sentinel in original:
        raise SystemExit(f"{path}: appended regression block already present: {sentinel}")
    p.write_text(original.rstrip() + "\n\n\n" + text.strip() + "\n", encoding="utf-8")


# ---------------------------------------------------------------------------
# 1. Deployment attestation must identify the actual three-layer entrypoint.
# ---------------------------------------------------------------------------
rep(
    "deploy/attest-weather-paper-runtime.py",
    'FINAL_WEATHER_MODULE = "polymarket_scanner.weather_only_live_paper_final"',
    'FINAL_WEATHER_MODULE = "polymarket_scanner.weather_only_live_paper_three_layer_validation"',
)
rep(
    "deploy/attest-weather-paper-runtime.py",
    '    "weather_only_live_paper_final.py",\n)',
    '    "weather_only_live_paper_final.py",\n'
    '    "weather_only_live_paper_three_layer_validation.py",\n)',
)


# ---------------------------------------------------------------------------
# 2. Crash recovery must bind a STARTED attempt to the full source identity.
# Event-id-only recovery can mis-attach a durable capture if an event is mutated or
# reused between an interrupted attempt and restart.
# ---------------------------------------------------------------------------
identity_old = "WHERE c.event_id=a.event_id AND c.as_of>=a.attempted_at"
identity_new = (
    "WHERE c.event_id=a.event_id\n"
    "                              AND c.station=a.station\n"
    "                              AND c.target_date=a.target_date\n"
    "                              AND c.family=a.family\n"
    "                              AND c.unit=a.unit\n"
    "                              AND c.as_of>=a.attempted_at"
)
rep(
    "polymarket_scanner/weather_only_same_day_capture_store.py",
    identity_old,
    identity_new,
    expected=3,
)


# ---------------------------------------------------------------------------
# 3. Bound the complete eligibility/metadata scan. The discovery layer can legally
# contain thousands of events and station metadata may retry/fallback over the network.
# The 12-event selection cap previously bounded only the *result*, not the work needed
# to discover that result. Timeout is fail-closed and explicitly visible in status.
# ---------------------------------------------------------------------------
rep(
    "polymarket_scanner/weather_only_live_paper_three_layer_validation.py",
    "THREE_LAYER_SOURCE_BUNDLE_DEADLINE_SECONDS = 35.0\nTHREE_LAYER_CURSOR_KEY",
    "THREE_LAYER_SOURCE_BUNDLE_DEADLINE_SECONDS = 35.0\n"
    "THREE_LAYER_ELIGIBILITY_SCAN_DEADLINE_SECONDS = 30.0\n"
    "THREE_LAYER_CURSOR_KEY",
)
rep(
    "polymarket_scanner/weather_only_live_paper_three_layer_validation.py",
    "        seen_event_ids: set[str] = set()\n\n        for event in events:\n"
    "            if not isinstance(event, dict):",
    "        seen_event_ids: set[str] = set()\n"
    "        loop = asyncio.get_running_loop()\n"
    "        scan_deadline = loop.time() + THREE_LAYER_ELIGIBILITY_SCAN_DEADLINE_SECONDS\n\n"
    "        def timeout_result():\n"
    "            self._three_layer_last_eligible_total = len(eligible)\n"
    "            self._three_layer_last_selected_ids = ()\n"
    "            self._three_layer_last_universe_truncated = True\n"
    "            if \"SAME_DAY_ELIGIBILITY_SCAN_TIMEOUT\" not in errors:\n"
    "                errors.append(\"SAME_DAY_ELIGIBILITY_SCAN_TIMEOUT\")\n"
    "            return [], errors\n\n"
    "        for event in events:\n"
    "            if loop.time() >= scan_deadline:\n"
    "                return timeout_result()\n"
    "            if not isinstance(event, dict):",
)
rep(
    "polymarket_scanner/weather_only_live_paper_three_layer_validation.py",
    "            try:\n"
    "                metadata = await self._station_metadata_for_compiled(compiled)\n"
    "                if metadata is None:",
    "            try:\n"
    "                remaining = scan_deadline - loop.time()\n"
    "                if remaining <= 0.0:\n"
    "                    raise TimeoutError\n"
    "                metadata = await asyncio.wait_for(\n"
    "                    self._station_metadata_for_compiled(compiled),\n"
    "                    timeout=remaining,\n"
    "                )\n"
    "                if metadata is None:",
)
rep(
    "polymarket_scanner/weather_only_live_paper_three_layer_validation.py",
    "            except (ZoneInfoNotFoundError, AttributeError, ValueError) as exc:\n"
    "                errors.append(f\"SAME_DAY_STATION:{event_id}:{type(exc).__name__}\")",
    "            except TimeoutError:\n"
    "                return timeout_result()\n"
    "            except (ZoneInfoNotFoundError, AttributeError, ValueError) as exc:\n"
    "                errors.append(f\"SAME_DAY_STATION:{event_id}:{type(exc).__name__}\")",
)
rep(
    "polymarket_scanner/weather_only_live_paper_three_layer_validation.py",
    '                "source_bundle_deadline_seconds": THREE_LAYER_SOURCE_BUNDLE_DEADLINE_SECONDS,\n'
    '                "theoretical_31_day_row_bound_at_full_daily_eligibility": (',
    '                "source_bundle_deadline_seconds": THREE_LAYER_SOURCE_BUNDLE_DEADLINE_SECONDS,\n'
    '                "eligibility_scan_deadline_seconds": THREE_LAYER_ELIGIBILITY_SCAN_DEADLINE_SECONDS,\n'
    '                "theoretical_31_day_row_bound_at_full_daily_eligibility": (',
)

# Deployment verifier must attest the new bound rather than accepting its absence.
rep(
    "deploy/verify-three-layer-validation-status.py",
    "    THREE_LAYER_MAX_EVENTS_PER_CYCLE,\n    THREE_LAYER_SELECTION_POLICY,",
    "    THREE_LAYER_MAX_EVENTS_PER_CYCLE,\n"
    "    THREE_LAYER_ELIGIBILITY_SCAN_DEADLINE_SECONDS,\n"
    "    THREE_LAYER_SELECTION_POLICY,",
)
rep(
    "deploy/verify-three-layer-validation-status.py",
    "    if lane.get(\"source_bundle_deadline_seconds\") != THREE_LAYER_SOURCE_BUNDLE_DEADLINE_SECONDS:\n"
    "        raise ThreeLayerStatusError(\"THREE_LAYER_SOURCE_DEADLINE_MISMATCH\")\n"
    "    if lane.get(\"theoretical_31_day_row_bound_at_full_daily_eligibility\")",
    "    if lane.get(\"source_bundle_deadline_seconds\") != THREE_LAYER_SOURCE_BUNDLE_DEADLINE_SECONDS:\n"
    "        raise ThreeLayerStatusError(\"THREE_LAYER_SOURCE_DEADLINE_MISMATCH\")\n"
    "    if lane.get(\"eligibility_scan_deadline_seconds\") != THREE_LAYER_ELIGIBILITY_SCAN_DEADLINE_SECONDS:\n"
    "        raise ThreeLayerStatusError(\"THREE_LAYER_ELIGIBILITY_DEADLINE_MISMATCH\")\n"
    "    if lane.get(\"theoretical_31_day_row_bound_at_full_daily_eligibility\")",
)


# ---------------------------------------------------------------------------
# Regression tests for all three findings.
# ---------------------------------------------------------------------------
append(
    "tests/test_weather_only_same_day_capture_cadence.py",
    r'''
def test_interrupted_attempt_does_not_attach_capture_from_different_full_identity(tmp_path):
    db = tmp_path / "weather-paper.sqlite"
    store = SameDayCaptureStore(db)
    capture = _capture()
    store.start_attempt(
        event_id=capture.event_id,
        station="KZZZ" if capture.station != "KZZZ" else "KYYY",
        target_date=capture.target_date,
        family=capture.family,
        unit=capture.unit,
        attempted_at=capture.as_of - 1.0,
    )
    assert store.save(capture) is not None
    assert store.reconcile_started_attempts(completed_at=capture.as_of + 1.0) == 1
    summary = store.attempt_summary()
    assert summary["saved"] == 0
    assert summary["failed"] == 1
    assert summary["started"] == 0
''',
)

append(
    "tests/test_weather_only_three_layer_validation_guarded.py",
    r'''
def test_eligibility_metadata_scan_has_one_total_deadline(monkeypatch):
    import polymarket_scanner.weather_only_live_paper_three_layer_validation as module

    service = object.__new__(ThreeLayerValidationWeatherLivePaperService)

    class Positions:
        def get_state(self, _key, default=""):
            return default
        def set_state(self, _key, _value):
            raise AssertionError("cursor must not advance on incomplete eligibility scan")

    service.positions = Positions()
    today = datetime.now(timezone.utc).date()

    monkeypatch.setattr(
        module,
        "compile_strict_temperature_event",
        lambda event: NS(
            event_id=event["id"], target_date=today, station_hint="KLGA",
            family="DAILY_HIGH", unit="F",
        ),
    )
    monkeypatch.setattr(module, "compile_temperature_rule_authority", lambda *_: object())
    monkeypatch.setattr(
        module,
        "build_same_day_contract_semantics",
        lambda *_: NS(layer1_adapter_capable=True),
    )
    monkeypatch.setattr(module, "THREE_LAYER_ELIGIBILITY_SCAN_DEADLINE_SECONDS", 0.02)

    async def slow_metadata(_compiled):
        await asyncio.sleep(1.0)
        return NS(timezone="UTC", latitude=40.0, longitude=-73.0)

    service._station_metadata_for_compiled = slow_metadata
    started = time.monotonic()
    selected, errors = asyncio.run(service._same_day_eligible(({"id": "event-1"},)))
    elapsed = time.monotonic() - started
    assert elapsed < 0.25
    assert selected == []
    assert errors == ["SAME_DAY_ELIGIBILITY_SCAN_TIMEOUT"]
    assert service._three_layer_last_universe_truncated is True
    assert service._three_layer_last_selected_ids == ()
''',
)

# Extend the deployment-finalization test without changing generic attestation defaults.
rep(
    "tests/test_weather_only_three_layer_finalization.py",
    "    THREE_LAYER_CAPTURE_JSON_BYTES_CAP,\n"
    "    THREE_LAYER_MAX_EVENTS_PER_CYCLE,",
    "    THREE_LAYER_CAPTURE_JSON_BYTES_CAP,\n"
    "    THREE_LAYER_ELIGIBILITY_SCAN_DEADLINE_SECONDS,\n"
    "    THREE_LAYER_MAX_EVENTS_PER_CYCLE,",
)
rep(
    "tests/test_weather_only_three_layer_finalization.py",
    '            "source_bundle_deadline_seconds": THREE_LAYER_SOURCE_BUNDLE_DEADLINE_SECONDS,\n'
    '            "theoretical_31_day_row_bound_at_full_daily_eligibility": THREE_LAYER_31D_CAPTURE_ROW_BOUND,',
    '            "source_bundle_deadline_seconds": THREE_LAYER_SOURCE_BUNDLE_DEADLINE_SECONDS,\n'
    '            "eligibility_scan_deadline_seconds": THREE_LAYER_ELIGIBILITY_SCAN_DEADLINE_SECONDS,\n'
    '            "theoretical_31_day_row_bound_at_full_daily_eligibility": THREE_LAYER_31D_CAPTURE_ROW_BOUND,',
)
append(
    "tests/test_weather_only_three_layer_finalization.py",
    r'''
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
''',
)
