from __future__ import annotations

import hashlib
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from polymarket_scanner import weather_only_live_paper_final as final_runtime
from polymarket_scanner import weather_only_live_paper_v4 as live_v4
from polymarket_scanner.weather_only_contract_strict import (
    StrictWeatherContractError,
    compile_strict_temperature_event,
)
from polymarket_scanner.weather_only_discovery import _merge_event
from polymarket_scanner.weather_only_live_paper_corrective import WeatherLivePaperCorrectiveService
from polymarket_scanner.weather_only_live_paper_final import (
    FinalPaperInvariantError,
    FinalWeatherLivePaperService,
)
from polymarket_scanner.weather_only_paper_backup import backup_weather_paper_database
from polymarket_scanner.weather_only_paper_corrective import PAPER_EXECUTION_PROTOCOL_V4
from polymarket_scanner.weather_only_paper_positions import WeatherPaperPositionStore
from polymarket_scanner.weather_only_paper_recovery import CrashSafeWeatherPaperStore
from polymarket_scanner.weather_only_paper_store import WeatherPaperStore
from polymarket_scanner.weather_only_runtime_lease import (
    WeatherPaperRuntimeLease,
    WeatherPaperRuntimeLeaseError,
)


ROOT = Path(__file__).resolve().parents[1]
RELEASE = "a" * 40


def _market(mid: str, question: str, *, description: str = "", source: str = "") -> dict:
    return {
        "id": mid,
        "active": True,
        "closed": False,
        "acceptingOrders": True,
        "enableOrderBook": True,
        "question": question,
        "description": description,
        "resolutionSource": source,
        "slug": f"market-{mid}",
        "conditionId": f"condition-{mid}",
        "outcomes": '["Yes","No"]',
        "clobTokenIds": f'["{mid}-yes","{mid}-no"]',
    }


def _strict_event() -> dict:
    rules = (
        "Observation date 14 Sep '26, in whole degrees Celsius. "
        "The market resolves using the lowest reading in the \"Temp\" column across all times on this day. "
        "On WRH select Hourly Data and show Hourly Data. "
        "If WRH is unavailable, use the Weather Underground Daily Observations table by 11:59 PM ET on the day following the observation date. "
        "If there is no data, the market resolves to the lowest bracket. "
        "Revisions are accepted until the first data point for the following date, whichever comes first, after which any alterations will not be considered."
    )
    source = "https://www.weather.gov/wrh/timeseries?site=EDDM"
    return {
        "id": "event-1",
        "slug": "munich-low",
        "title": "Lowest temperature in Munich on September 14?",
        "description": rules,
        "resolutionSource": source,
        "markets": [
            _market("a", "Will the lowest temperature be 6°C or lower?"),
            _market("b", "Will the lowest temperature be 7-8°C?"),
            _market("c", "Will the lowest temperature be 9°C or higher?"),
        ],
    }


@pytest.mark.parametrize(
    "question",
    [
        "Will the lowest temperature be higher than 8°C?",
        "Will the lowest temperature be at most 8°C?",
        "Will the lowest temperature be <8°C?",
    ],
)
def test_b1_unsupported_comparators_never_become_equality(question: str):
    event = _strict_event()
    event["markets"][1]["question"] = question
    with pytest.raises(StrictWeatherContractError) as raised:
        compile_strict_temperature_event(event)
    assert raised.value.code == "STRICT_BUCKET_GRAMMAR_UNSUPPORTED"


def test_b1_conflicting_child_rule_or_source_never_reaches_authority():
    event = _strict_event()
    event["markets"][1]["description"] = (
        event["description"]
        + " The preceding NOAA rules are obsolete. This market resolves from Weather Underground only."
    )
    with pytest.raises(StrictWeatherContractError) as raised:
        compile_strict_temperature_event(event)
    assert raised.value.code in {
        "STRICT_OPERATIVE_RULE_TEXT_CONFLICT",
        "STRICT_OPERATIVE_RULE_OVERRIDE_UNSUPPORTED",
    }

    event = _strict_event()
    event["markets"][1]["resolutionSource"] = "https://example.invalid/other-source"
    with pytest.raises(StrictWeatherContractError) as raised:
        compile_strict_temperature_event(event)
    assert raised.value.code == "STRICT_OPERATIVE_SOURCE_CONFLICT"


def _state_event(*, open_state: bool) -> dict:
    child = {
        "id": "m1",
        "question": "same question",
        "conditionId": "condition-1",
        "clobTokenIds": '["yes","no"]',
        "active": open_state,
        "closed": not open_state,
        "acceptingOrders": open_state,
        "enableOrderBook": open_state,
    }
    return {
        "id": "e1",
        "slug": "same-event",
        "title": "same title",
        "active": open_state,
        "closed": not open_state,
        "markets": [child],
    }


@pytest.mark.parametrize("open_first", [True, False])
def test_b2_any_closed_duplicate_projection_vetoes_open_projection(open_first: bool):
    open_event = _state_event(open_state=True)
    closed_event = _state_event(open_state=False)
    first, second = (open_event, closed_event) if open_first else (closed_event, open_event)
    merged = _merge_event(first, second)
    assert merged["active"] is False
    assert merged["closed"] is True
    market = merged["markets"][0]
    assert market["active"] is False
    assert market["closed"] is True
    assert market["acceptingOrders"] is False
    assert market["enableOrderBook"] is False


def test_b2_dispatch_state_gate_requires_explicit_current_tradability():
    baseline = {
        "id": "m1",
        "conditionId": "condition-1",
        "clobTokenIds": '["yes","no"]',
        "active": True,
        "closed": False,
        "acceptingOrders": True,
        "enableOrderBook": True,
    }
    accepted = FinalWeatherLivePaperService._validate_current_market_state(
        baseline,
        market_id="m1",
        condition_id="condition-1",
        expected_tokens={"yes", "no"},
    )
    assert accepted["accepting_orders"] is True
    for mutation, code in (
        ({"closed": True}, "FINAL_MARKET_CLOSED_OR_UNKNOWN"),
        ({"active": False}, "FINAL_MARKET_NOT_ACTIVE"),
        ({"acceptingOrders": False}, "FINAL_MARKET_NOT_ACCEPTING_ORDERS"),
        ({"enableOrderBook": False}, "FINAL_MARKET_ORDERBOOK_DISABLED_OR_UNKNOWN"),
        ({"acceptingOrders": None}, "FINAL_MARKET_NOT_ACCEPTING_ORDERS"),
    ):
        row = dict(baseline)
        row.update(mutation)
        with pytest.raises(FinalPaperInvariantError) as raised:
            FinalWeatherLivePaperService._validate_current_market_state(
                row,
                market_id="m1",
                condition_id="condition-1",
                expected_tokens={"yes", "no"},
            )
        assert raised.value.code == code


def _v4_payload(decision: str, *, target_date: str = "2026-09-20") -> dict:
    return {
        "event_title": "Crash recovery fixture",
        "station": "KLGA",
        "target_date": target_date,
        "condition_id": "condition-1",
        "ask": 0.50,
        "fee": 0.0,
        "entry_cost": 0.50,
        "ask_size": 5.0,
        "quote_observed_at": 100.0,
        "paper_fill_at": 101.0,
        "decision_expires_at": 120.0,
        "book_hash": "book-1",
        "minimum_order_size": 1.0,
        "minimum_tick_size": 0.01,
        "decision_id": decision,
        "paper_target_stake_usd": 10.0,
        "paper_execution_protocol_version": PAPER_EXECUTION_PROTOCOL_V4,
    }


def _save_v4(store: CrashSafeWeatherPaperStore, decision: str, *, target_date: str = "2026-09-20") -> int:
    sid = store.save_signal(
        fingerprint=f"fingerprint-{decision}",
        lane="weather_forecast_raw_gap",
        evidence_class="RESEARCH_ONLY_UNCALIBRATED_V4",
        event_id="event-1",
        market_id="market-1",
        side="YES",
        token_id="token-yes",
        model_probability=0.8,
        entry_cost=0.5,
        raw_gap=0.3,
        theoretical_payout=1.0,
        created_at=100.5,
        payload=_v4_payload(decision, target_date=target_date),
    )
    assert sid is not None
    return int(sid)


def test_b3_restart_turns_inflight_delivery_into_visible_uncertainty_and_blocks_duplicate(tmp_path: Path):
    db_path = tmp_path / "paper.sqlite"
    store = CrashSafeWeatherPaperStore(db_path)
    sid = _save_v4(store, "d1")
    store.set_signal_status(sid, "PENDING_DELIVERY")

    restarted = CrashSafeWeatherPaperStore(db_path)
    recovery = restarted.reconcile_crash_states()
    assert recovery["delivery_uncertain_recovered"] == 1
    with restarted._conn() as db:
        row = db.execute("SELECT status FROM weather_paper_signals WHERE id=?", (sid,)).fetchone()
        count_before = int(db.execute("SELECT COUNT(*) FROM weather_paper_signals").fetchone()[0])
    assert row["status"] == "DELIVERY_UNCERTAIN"
    assert restarted.stats()["telegram_delivery_uncertain"] == 1
    assert restarted.stats()["station_day_uncertain_reservations"] == 1

    # A fresh quote/new decision for the same station/day is suppressed rather than
    # automatically resending after an ambiguous crash window.
    second = restarted.save_signal(
        fingerprint="fingerprint-d2",
        lane="weather_forecast_raw_gap",
        evidence_class="RESEARCH_ONLY_UNCALIBRATED_V4",
        event_id="event-2",
        market_id="market-2",
        side="YES",
        token_id="token-yes-2",
        model_probability=0.8,
        entry_cost=0.5,
        raw_gap=0.3,
        theoretical_payout=1.0,
        created_at=103.0,
        payload=_v4_payload("d2"),
    )
    assert second is None
    with restarted._conn() as db:
        assert int(db.execute("SELECT COUNT(*) FROM weather_paper_signals").fetchone()[0]) == count_before


def test_b3_durable_receipt_without_fill_is_recovered_once_from_frozen_inputs(tmp_path: Path):
    db_path = tmp_path / "paper.sqlite"
    store = CrashSafeWeatherPaperStore(db_path)
    sid = _save_v4(store, "ack")
    store.set_signal_status(sid, "PENDING_DELIVERY")
    store.mark_telegram_sent(sid, 77, sent_at=102.0)

    restarted = CrashSafeWeatherPaperStore(db_path)
    recovery = restarted.reconcile_crash_states()
    assert recovery["acknowledged_fills_recovered"] == 1
    with restarted._conn() as db:
        signal = db.execute("SELECT status FROM weather_paper_signals WHERE id=?", (sid,)).fetchone()
        positions = db.execute(
            "SELECT validation_state,target_stake_usd,opened_at FROM weather_paper_positions WHERE signal_id=?",
            (sid,),
        ).fetchall()
    assert signal["status"] == "ACKNOWLEDGED"
    assert len(positions) == 1
    assert positions[0]["validation_state"] == "VALIDATED"
    assert float(positions[0]["target_stake_usd"]) == 10.0
    assert float(positions[0]["opened_at"]) == 101.0
    assert restarted.reconcile_crash_states()["acknowledged_fills_recovered"] == 0


def test_b3_partial_unverified_fill_and_settlement_send_are_explicitly_uncertain(tmp_path: Path):
    # Partial position write: preserve it as unverified evidence rather than promote.
    db_path = tmp_path / "partial.sqlite"
    store = CrashSafeWeatherPaperStore(db_path)
    sid = _save_v4(store, "partial")
    store.mark_telegram_sent(sid, 88, sent_at=102.0)
    store.set_signal_status(sid, "ACKNOWLEDGED")
    WeatherPaperPositionStore.ensure_position_for_signal(store, sid, 10.0)
    restarted = CrashSafeWeatherPaperStore(db_path)
    recovery = restarted.reconcile_crash_states()
    assert recovery["accounting_uncertain_recovered"] >= 1
    with restarted._conn() as db:
        signal = db.execute("SELECT status FROM weather_paper_signals WHERE id=?", (sid,)).fetchone()
        position = db.execute(
            "SELECT validation_state FROM weather_paper_positions WHERE signal_id=?", (sid,)
        ).fetchone()
    assert signal["status"] == "PAPER_ACCOUNTING_UNCERTAIN"
    assert position["validation_state"] != "VALIDATED"

    # Settlement notification crash is a separate uncertainty category.
    db_path2 = tmp_path / "settlement.sqlite"
    store2 = CrashSafeWeatherPaperStore(db_path2)
    sid2 = _save_v4(store2, "settle", target_date="2026-09-21")
    store2.mark_telegram_sent(sid2, 99, sent_at=102.0)
    store2.set_signal_status(sid2, "ACKNOWLEDGED")
    position2 = store2.ensure_position_for_signal(sid2, 10.0)
    assert position2 is not None
    store2.set_notification_state(int(position2["id"]), "SENDING")
    restarted2 = CrashSafeWeatherPaperStore(db_path2)
    recovery2 = restarted2.reconcile_crash_states()
    assert recovery2["settlement_notification_uncertain_recovered"] == 1
    assert restarted2.stats()["settlement_notification_uncertain"] == 1


class _CursorStore:
    def __init__(self) -> None:
        self.state: dict[str, str] = {}
        self.examined: list[str] = []

    def get_state(self, key: str, default: str = "") -> str:
        return self.state.get(key, default)

    def set_state(self, key: str, value: object) -> None:
        self.state[key] = str(value)
        if key == "v4_forecast_cursor":
            self.examined.append(str(value))


def test_b4_cursor_advances_for_every_examined_ineligible_event(monkeypatch):
    service = object.__new__(FinalWeatherLivePaperService)
    service.positions = _CursorStore()
    service.max_forecast_events = 6
    service._strict_rule_sha_by_event = {}
    service._forecast_cache = {}

    def fake_compile(event):
        return SimpleNamespace(event_id=event["id"])

    monkeypatch.setattr(live_v4, "compile_strict_temperature_event", fake_compile)
    monkeypatch.setattr(final_runtime, "compile_strict_temperature_event", fake_compile)
    monkeypatch.setattr(
        final_runtime,
        "strict_contract_identity",
        lambda event, compiled: {"version": "fixture", "sha256": f"sha-{compiled.event_id}"},
    )

    async def ineligible_parent(_self, _event, _compiled):
        return None

    monkeypatch.setattr(WeatherLivePaperCorrectiveService, "_forecast_candidate", ineligible_parent)

    events = [{"id": f"a{i:02d}"} for i in range(24)] + [{"id": "z-future"}]
    first = live_v4.WeatherLivePaperV4Service._certified_forecast_events(service, events)
    assert [compiled.event_id for _, compiled in first] == [f"a{i:02d}" for i in range(24)]

    import asyncio

    for event, compiled in first:
        assert asyncio.run(service._forecast_candidate(event, compiled)) is None
    assert service.positions.state["v4_forecast_cursor"] == "a23"

    second = live_v4.WeatherLivePaperV4Service._certified_forecast_events(service, events)
    assert second[0][1].event_id == "z-future"


def test_b5_legacy_paper_database_backs_up_before_migration_without_source_change(tmp_path: Path):
    db_path = tmp_path / "legacy.sqlite"
    signals = WeatherPaperStore(db_path)
    sid = signals.save_signal(
        fingerprint="legacy-backup",
        lane="weather_forecast_raw_gap",
        evidence_class="LEGACY_RESEARCH",
        event_id="event-old",
        market_id="market-old",
        side="YES",
        token_id="old-yes",
        model_probability=0.6,
        entry_cost=0.4,
        raw_gap=0.2,
        theoretical_payout=1.0,
        created_at=10.0,
        payload={"event_title": "legacy", "ask_size": 3.0},
    )
    assert sid is not None
    signals.mark_telegram_sent(sid, 1, sent_at=11.0)
    legacy_positions = WeatherPaperPositionStore(db_path)
    legacy_positions.ensure_position_for_signal(sid, 10.0)
    with legacy_positions._conn() as db:
        db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    before = hashlib.sha256(db_path.read_bytes()).hexdigest()

    manifest = backup_weather_paper_database(
        db_path,
        tmp_path / "backups",
        release_sha=RELEASE,
    )
    after = hashlib.sha256(db_path.read_bytes()).hexdigest()
    assert manifest["source_schema_profile"] == "LEGACY_V3_PRE_MIGRATION"
    assert manifest["restore_verified"] is True
    assert before == after


def test_b5_attestation_cli_imports_from_unrelated_cwd_with_clean_pythonpath(tmp_path: Path):
    script = ROOT / "deploy" / "attest-weather-paper-runtime.py"
    env = dict(os.environ)
    env.pop("PYTHONPATH", None)
    result = subprocess.run(
        [sys.executable, str(script), "--help"],
        cwd=tmp_path,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "ModuleNotFoundError" not in result.stderr
    assert "--app-dir" in result.stdout


def test_b6_exclusive_runtime_lease_blocks_writer_that_appears_after_preflight(tmp_path: Path):
    db_path = tmp_path / "paper.sqlite"
    first = WeatherPaperRuntimeLease(db_path)
    try:
        with pytest.raises(WeatherPaperRuntimeLeaseError) as raised:
            WeatherPaperRuntimeLease(db_path)
        assert raised.value.code == "WEATHER_PAPER_RUNTIME_ALREADY_OWNED"
    finally:
        first.close()
    second = WeatherPaperRuntimeLease(db_path)
    assert second.acquired is True
    second.close()


def test_b6_release_verifier_rejects_untracked_import_surface(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.email", "test@example.com"], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.name", "test"], check=True)
    (repo / "app.py").write_text("print('tracked')\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "app.py"], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-q", "-m", "tracked"], check=True)
    sha = subprocess.check_output(["git", "-C", str(repo), "rev-parse", "HEAD"], text=True).strip()
    marker = tmp_path / "release.sha"
    marker.write_text(sha + "\n", encoding="utf-8")
    package = repo / "polymarket_scanner"
    package.mkdir()
    (package / "unexpected.py").write_text("SURPRISE = True\n", encoding="utf-8")

    result = subprocess.run(
        ["bash", str(ROOT / "deploy" / "verify-runtime-release.sh"), str(repo), str(marker)],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    assert result.returncode != 0
    assert "untracked import surface" in result.stderr
