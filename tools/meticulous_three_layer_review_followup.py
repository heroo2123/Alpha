from __future__ import annotations

from pathlib import Path


def rep(path: str, old: str, new: str, count: int = 1) -> None:
    p = Path(path)
    text = p.read_text(encoding="utf-8")
    found = text.count(old)
    if found < count:
        raise SystemExit(
            f"{path}: expected at least {count} occurrences, found {found}: {old[:100]!r}"
        )
    p.write_text(text.replace(old, new, count), encoding="utf-8")


# Object.__new__ test fixtures and partially constructed instances should not crash
# only while formatting non-authoritative diagnostics.
rep(
    "polymarket_scanner/weather_only_live_paper_corrective.py",
    '            "attempt_recovery_at_startup": int(self._same_day_attempt_recovery),',
    '            "attempt_recovery_at_startup": int(\n'
    '                getattr(self, "_same_day_attempt_recovery", 0)\n'
    '            ),',
)

# Update the pre-existing restart fixture to the durable-attempt store contract.
rep(
    "tests/test_weather_only_live_paper_corrective_runtime.py",
    "    def summary(self):\n",
    "    def latest_attempt_at_for_event(self, event_id):\n"
    "        assert event_id == \"event-1\"\n"
    "        return None\n\n"
    "    def summary(self):\n",
)
rep(
    "tests/test_weather_only_live_paper_corrective_runtime.py",
    "    service._same_day_last_attempt = {}\n",
    "",
)
rep(
    "tests/test_weather_only_live_paper_corrective_runtime.py",
    '    assert result["capture_cadence_persisted_in_sqlite"] is True\n',
    '    assert result["capture_cadence_persisted_in_sqlite"] is True\n'
    '    assert result["attempt_audit_persisted_in_sqlite"] is True\n',
)

# Add a focused fake audit store for failure/cadence integration tests.
p = Path("tests/test_weather_only_live_paper_corrective_runtime.py")
text = p.read_text(encoding="utf-8")
text = text.replace("import time\n", "import time\nfrom datetime import date\nfrom types import SimpleNamespace as NS\n")
text += '''\n\n
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
'''
p.write_text(text, encoding="utf-8")

# Crash after capture save but before attempt finalization must reconcile as SAVED,
# not be mislabeled as a source failure.
p = Path("tests/test_weather_only_same_day_capture_cadence.py")
p.write_text(
    p.read_text(encoding="utf-8")
    + '''\n\n
def test_started_attempt_with_durable_capture_reconciles_as_saved(tmp_path):
    db = tmp_path / "weather-paper.sqlite"
    store = SameDayCaptureStore(db)
    capture = _capture()
    attempt = store.start_attempt(
        event_id=capture.event_id,
        station=capture.station,
        target_date=capture.target_date,
        family=capture.family,
        unit=capture.unit,
        attempted_at=capture.as_of - 1.0,
    )
    assert store.save(capture) is not None
    assert store.reconcile_started_attempts(completed_at=capture.as_of + 1.0) == 1
    summary = store.attempt_summary()
    assert summary["started"] == 0
    assert summary["saved"] == 1
    assert summary["failed"] == 0
''',
    encoding="utf-8",
)

# Behavioral regression: >12 eligible same-day contracts is not silently reduced to
# the first 12. The research lane must collect none and surface the capacity condition.
p = Path("tests/test_weather_only_three_layer_validation_guarded.py")
text = p.read_text(encoding="utf-8")
text = text.replace("from datetime import date, datetime, timezone\n", "from datetime import date, datetime, timezone\n")
text += '''\n\n
def test_more_than_selection_cap_fails_closed_without_partial_sampling(monkeypatch):
    import polymarket_scanner.weather_only_live_paper_three_layer_validation as module

    service = object.__new__(ThreeLayerValidationWeatherLivePaperService)

    class Positions:
        def __init__(self):
            self.set_calls = []
        def get_state(self, _key, default=""):
            return default
        def set_state(self, key, value):
            self.set_calls.append((key, value))

    service.positions = Positions()

    today = datetime.now(timezone.utc).date()

    def compile_event(event):
        return NS(
            event_id=event["id"],
            target_date=today,
            station_hint="KLGA",
            family="DAILY_HIGH",
            unit="F",
        )

    monkeypatch.setattr(module, "compile_strict_temperature_event", compile_event)
    monkeypatch.setattr(module, "compile_temperature_rule_authority", lambda *_: object())
    monkeypatch.setattr(
        module,
        "build_same_day_contract_semantics",
        lambda *_: NS(layer1_adapter_capable=True),
    )

    async def metadata(_compiled):
        return NS(timezone="UTC", latitude=40.7769, longitude=-73.8740)

    service._station_metadata_for_compiled = metadata
    events = tuple({"id": f"event-{index:02d}"} for index in range(13))
    selected, errors = asyncio.run(service._same_day_eligible(events))
    assert selected == []
    assert errors == ["SAME_DAY_SELECTION_UNIVERSE_CAP_EXCEEDED:13>12"]
    assert service._three_layer_last_universe_truncated is True
    assert service._three_layer_last_selected_ids == ()
    assert service.positions.set_calls == []
'''
p.write_text(text, encoding="utf-8")

print("meticulous followup patch applied")
