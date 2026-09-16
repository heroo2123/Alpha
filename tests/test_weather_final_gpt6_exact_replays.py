from __future__ import annotations

import asyncio
import copy
import json
import os
import sqlite3
import subprocess
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import patch

import httpx
import pytest

from polymarket_scanner.weather_only_clob import WeatherCLOBClient
from polymarket_scanner.weather_only_contract_strict import (
    StrictWeatherContractError,
    compile_strict_temperature_event,
)
from polymarket_scanner.weather_only_contracts import DAILY_HIGH
from polymarket_scanner.weather_only_discovery import _merge_event
from polymarket_scanner.weather_only_forecast import parse_open_meteo_gefs_daily_extreme
from polymarket_scanner.weather_only_live_paper_final import FinalWeatherLivePaperService
from polymarket_scanner.weather_only_paper_corrective import CorrectiveSettlementEngine
from polymarket_scanner.weather_only_paper_positions import WeatherPaperPositionStore
from polymarket_scanner.weather_only_paper_recovery import CrashSafeWeatherPaperStore
from polymarket_scanner.weather_only_paper_store import WeatherPaperStore
from test_host_authority_production_boundary import host, cutover


ROOT = Path(__file__).resolve().parents[1]
NOW = datetime(2026, 9, 13, 12, tzinfo=timezone.utc).timestamp()
RELEASE = "f" * 40


class SimulatedCrash(BaseException):
    pass


class FakeTelegram:
    def __init__(self) -> None:
        self.messages: list[str] = []
        self.crash = False

    async def send_html(self, text: str, **_kwargs) -> int:
        self.messages.append(text)
        if self.crash:
            raise SimulatedCrash("remote accepted; process died before durable receipt")
        return len(self.messages)


def _event(
    *,
    family: str = "high",
    target: date = date(2026, 9, 14),
    station: str = "KLGA",
    unit: str = "F",
    eid: str = "event-1",
    labels: list[str] | None = None,
) -> dict:
    extreme = "highest" if family == "high" else "lowest"
    unit_word = "Fahrenheit" if unit == "F" else "Celsius"
    source = f"https://www.weather.gov/wrh/timeseries?site={station}"
    rules = (
        f"This market resolves to the range containing the {extreme} temperature on {target.strftime('%d %b')} '{target.year % 100:02d}, "
        f"in degrees {unit_word}. The source is NOAA, the {extreme} reading under the \"Temp\" column for all times on this day. "
        f"{source} The source measures temperatures to whole degrees {unit_word}. "
        "If NOAA data is unavailable by 11:59 PM ET on the day following the observation date, "
        "the Weather Underground Daily Observations table is used. If there is no data, this market resolves to the lowest bracket. "
        "Resolution occurs once the first data point for the following date is published, or at the deadline, whichever comes first. "
        "Revisions are considered until the first datapoint for the following date, after which any alterations will not be considered."
    )
    if labels is None:
        labels = [f"69°{unit} or lower", f"70-71°{unit}", f"72°{unit} or higher"]
    markets = []
    for index, label in enumerate(labels):
        mid = f"m{index}"
        markets.append(
            {
                "id": mid,
                "conditionId": f"c{index}",
                "question": f"Will the {extreme} temperature be {label}?",
                "active": True,
                "closed": False,
                "acceptingOrders": True,
                "enableOrderBook": True,
                "outcomes": ["Yes", "No"],
                "clobTokenIds": [f"t{index}y", f"t{index}n"],
                "slug": mid,
            }
        )
    return {
        "id": eid,
        "slug": eid,
        "title": f"{extreme.title()} temperature on {target.strftime('%B')} {target.day}?",
        "description": rules,
        "resolutionSource": source,
        "markets": markets,
    }


def _distribution(compiled, *, value: float, received: float):
    variable = "temperature_2m_max" if compiled.family == DAILY_HIGH else "temperature_2m_min"
    keys = [variable] + [f"{variable}_member{i:02d}" for i in range(1, 31)]
    payload = {
        "latitude": 40.78,
        "longitude": -73.88,
        "timezone": "America/New_York",
        "daily": {"time": [compiled.target_date.isoformat()], **{key: [value] for key in keys}},
        "daily_units": {"time": "iso8601", **{key: f"°{compiled.unit}" for key in keys}},
    }
    return parse_open_meteo_gefs_daily_extreme(
        payload,
        station=compiled.station_hint,
        target_date=compiled.target_date,
        family=compiled.family,
        unit=compiled.unit,
        timezone="America/New_York",
        requested_latitude=40.78,
        requested_longitude=-73.88,
        received_at=received,
    )


def _info_json(bucket, *, minimum: float = 1.0) -> dict:
    return {
        "t": [
            {"t": bucket.yes_token, "o": "Yes"},
            {"t": bucket.no_token, "o": "No"},
        ],
        "mos": minimum,
        "mts": 0.01,
        "fd": {"r": 0.05, "e": 1, "to": True},
    }


def _book_json(token: str, *, ask: float, size: float, timestamp: str) -> dict:
    return {
        "asset_id": token,
        "market": "synthetic-condition",
        "timestamp": timestamp,
        "hash": "synthetic-book",
        "bids": [{"price": str(ask / 2), "size": "100"}],
        "asks": [{"price": str(ask), "size": str(size)}],
    }


async def _snapshot(compiled, *, clock: float, ask: float = 0.20, size: float = 100.0):
    async def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            condition_id = request.url.path.rsplit("/", 1)[-1]
            bucket = next(row for row in compiled.buckets if row.condition_id == condition_id)
            return httpx.Response(200, json=_info_json(bucket))
        tokens = [row["token_id"] for row in json.loads(request.content)]
        return httpx.Response(
            200,
            json=[
                _book_json(
                    token,
                    ask=ask if token.endswith("y") else 0.99,
                    size=size,
                    timestamp=str(int(clock * 1000)),
                )
                for token in tokens
            ],
        )

    client = object.__new__(WeatherCLOBClient)
    client.http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        return await client.exact_event_snapshot(compiled)
    finally:
        await client.close()


def _service(
    event: dict,
    clock: list[float],
    tmp_path: Path,
    *,
    telegram: FakeTelegram | None = None,
    db_path: Path | None = None,
    forecast_value: float = 70.0,
):
    compiled = compile_strict_temperature_event(event)
    service = object.__new__(FinalWeatherLivePaperService)
    service.db_path = db_path or (tmp_path / "paper.sqlite")
    service.positions = CrashSafeWeatherPaperStore(service.db_path)
    service.telegram = telegram or FakeTelegram()
    service._now_epoch = lambda: clock[0]
    service.release_sha = lambda: RELEASE

    async def metadata(_compiled):
        return NS(
            station=_compiled.station_hint,
            timezone="America/New_York",
            latitude=40.78,
            longitude=-73.88,
        )

    service._station_metadata_for_compiled = metadata
    service._forecast_cache = {}
    service._forecast_distribution_by_sha = {}
    service._strict_rule_sha_by_event = {}
    service.forecast_cache_seconds = 900.0
    service.forecast_raw_gap_min = 0.08
    service.max_forecast_events = 6
    service.paper_stake_usd = 10.0
    service._v4_eligible_evaluated_this_cycle = 0
    service._forecast_same_day_suppressed_total = 0
    service._v4_dispatch_skipped_total = 0
    service._v4_structural_suppressed_total = 0

    async def daily_extreme(**_kwargs):
        return _distribution(compiled, value=forecast_value, received=clock[0])

    async def exact_snapshot(current_compiled):
        return await _snapshot(current_compiled, clock=clock[0])

    async def market_by_id(market_id: str):
        market = next(row for row in event["markets"] if str(row.get("id")) == str(market_id))
        return copy.deepcopy(market)

    service.forecast_client = NS(daily_extreme=daily_extreme)
    service.runtime = NS(clob=NS(exact_event_snapshot=exact_snapshot))
    service.settlement = NS(gamma=NS(market_by_id=market_by_id))
    return service


async def _flow(event: dict, tmp_path: Path, **kwargs):
    clock = [NOW]
    service = _service(event, clock, tmp_path, **kwargs)
    with patch("time.time", lambda: clock[0]):
        candidate = await service._forecast_candidate(event, None)
        result = await service._save_and_send_forecast(candidate, event) if candidate else (False, None)
    return service, candidate, result


@pytest.mark.parametrize("phrase", ["higher than 70°F", "at most 70°F", "<70°F"])
def test_gpt6_f02_f04_comparator_mutations_are_rejected_before_any_candidate(tmp_path: Path, phrase: str):
    event = _event(labels=["69°F or lower", "70°F", "71°F or higher"])
    event["markets"][1]["question"] = f"Will the highest temperature be {phrase}?"
    with pytest.raises(StrictWeatherContractError) as raised:
        compile_strict_temperature_event(event)
    assert raised.value.code == "STRICT_BUCKET_GRAMMAR_UNSUPPORTED"
    shell = object.__new__(FinalWeatherLivePaperService)
    shell.positions = NS(get_state=lambda *_args: "")
    shell.max_forecast_events = 6
    assert shell._certified_forecast_events([event]) == []


@pytest.mark.parametrize(
    "suffix",
    [
        " However, if NOAA is unavailable, use the highest bracket instead of the lowest bracket.",
        " The preceding NOAA rules are obsolete. This market resolves from Weather Underground only.",
    ],
)
def test_gpt6_f07_f08_conflicting_child_authority_is_rejected(tmp_path: Path, suffix: str):
    event = _event()
    event["markets"][1]["description"] = suffix
    with pytest.raises(StrictWeatherContractError):
        compile_strict_temperature_event(event)
    shell = object.__new__(FinalWeatherLivePaperService)
    shell.positions = NS(get_state=lambda *_args: "")
    shell.max_forecast_events = 6
    assert shell._certified_forecast_events([event]) == []


def test_gpt6_f09_later_closed_projection_blocks_delivery_even_with_fresh_book(tmp_path: Path):
    first = _event()
    latest = copy.deepcopy(first)
    for market in latest["markets"]:
        market.update(active=False, closed=True, acceptingOrders=False, enableOrderBook=False)
    merged = _merge_event(first, latest)
    assert all(market["closed"] is True for market in merged["markets"])

    shell = object.__new__(FinalWeatherLivePaperService)
    shell.positions = NS(get_state=lambda *_args: "")
    shell.max_forecast_events = 6
    assert shell._certified_forecast_events([merged]) == []

    clock = [NOW]
    service = _service(first, clock, tmp_path)
    with patch("time.time", lambda: clock[0]):
        candidate = asyncio.run(service._forecast_candidate(first, None))
    assert candidate is not None

    async def closed_market_by_id(market_id: str):
        market = next(row for row in merged["markets"] if str(row.get("id")) == str(market_id))
        return copy.deepcopy(market)

    service.settlement = NS(gamma=NS(market_by_id=closed_market_by_id))
    with patch("time.time", lambda: clock[0]):
        result = asyncio.run(service._save_and_send_forecast(candidate, first))
    assert result == (False, None)
    assert service.telegram.messages == []
    assert service.positions.stats()["open"] == 0


def test_gpt6_f10_twenty_four_same_day_events_cannot_hide_next_future_event(tmp_path: Path):
    events = [_event(target=date(2026, 9, 13), eid=f"a{i:02d}") for i in range(24)]
    events.append(_event(target=date(2026, 9, 14), eid="z-future"))
    clock = [NOW]
    service = _service(events[-1], clock, tmp_path)
    first = service._certified_forecast_events(events)
    assert [event["id"] for event, _compiled in first] == [f"a{i:02d}" for i in range(24)]
    with patch("time.time", lambda: clock[0]):
        for event, compiled in first:
            assert asyncio.run(service._forecast_candidate(event, compiled)) is None
    assert service.positions.get_state("v4_forecast_cursor") == "a23"
    second = service._certified_forecast_events(events)
    assert second[0][0]["id"] == "z-future"


def test_gpt6_f12_crash_after_remote_acceptance_becomes_uncertain_and_suppresses_duplicate(tmp_path: Path):
    event = _event()
    clock = [NOW]
    telegram = FakeTelegram()
    service = _service(event, clock, tmp_path, telegram=telegram)
    with patch("time.time", lambda: clock[0]):
        candidate = asyncio.run(service._forecast_candidate(event, None))
        assert candidate is not None
        telegram.crash = True
        with pytest.raises(SimulatedCrash):
            asyncio.run(service._save_and_send_forecast(candidate, event))

    restarted = _service(
        event,
        clock,
        tmp_path,
        telegram=telegram,
        db_path=service.db_path,
    )
    recovery = restarted.positions.reconcile_crash_states()
    assert recovery["delivery_uncertain_recovered"] == 1
    assert restarted.positions.stats()["telegram_delivery_uncertain"] == 1
    assert restarted.positions.stats()["station_day_uncertain_reservations"] == 1

    clock[0] += 1.0
    telegram.crash = False
    with patch("time.time", lambda: clock[0]):
        candidate2 = asyncio.run(restarted._forecast_candidate(event, None))
        assert candidate2 is not None
        result2 = asyncio.run(restarted._save_and_send_forecast(candidate2, event))
    assert result2 == (False, None)
    assert len(telegram.messages) == 1
    assert restarted.positions.stats()["open"] == 0


def test_gpt6_f70_crash_during_settlement_notification_is_recovered_as_uncertain(tmp_path: Path):
    service, _candidate, _result = asyncio.run(_flow(_event(), tmp_path))
    position = service.positions.recent_positions()[0]
    service.positions.resolve_position(int(position["id"]), 1.0, {"synthetic_final": True})
    service.telegram.crash = True
    engine = object.__new__(CorrectiveSettlementEngine)
    engine.store = service.positions
    engine.telegram = service.telegram
    with pytest.raises(SimulatedCrash):
        asyncio.run(engine._notify_resolved())

    restarted = CrashSafeWeatherPaperStore(service.db_path)
    recovery = restarted.reconcile_crash_states()
    assert recovery["settlement_notification_uncertain_recovered"] == 1
    assert restarted.stats()["settlement_notification_uncertain"] == 1
    assert restarted.resolved_pending_notification() == []


def test_gpt6_f33_retired_backup_shell_refuses_legacy_db_without_mutation(tmp_path: Path):
    db_path = tmp_path / "legacy.sqlite"
    WeatherPaperStore(db_path)
    WeatherPaperPositionStore(db_path)

    app = tmp_path / "app"
    (app / ".venv" / "bin").mkdir(parents=True)
    os.symlink(sys.executable, app / ".venv" / "bin" / "python")
    os.symlink(ROOT / "polymarket_scanner", app / "polymarket_scanner", target_is_directory=True)
    config = tmp_path / "config"
    config.mkdir()
    (config / "weather-paper-release.sha").write_text(RELEASE + "\n", encoding="utf-8")
    backups = tmp_path / "backups"
    unrelated = tmp_path / "unrelated"
    unrelated.mkdir()
    before = db_path.read_bytes()

    env = dict(os.environ)
    env.pop("PYTHONPATH", None)
    env.update(
        {
            "ALPHA_WEATHER_APP_DIR": str(app),
            "ALPHA_CONFIG_DIR": str(config),
            "WEATHER_PAPER_DB_PATH": str(db_path),
            "WEATHER_PAPER_BACKUP_DIR": str(backups),
        }
    )
    result = subprocess.run(
        ["bash", str(ROOT / "deploy" / "pre-release-weather-paper-backup.sh")],
        cwd=unrelated,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    assert result.returncode == 40, result.stderr
    assert "production-host-control.sh" in result.stderr
    assert not backups.exists() and db_path.read_bytes() == before


def test_gpt6_f33_canonical_host_backup_preserves_legacy_wal_from_unrelated_cwd(host, monkeypatch):
    # Preserve the former F33 contract (legacy schema and unrelated CWD), using
    # the production cutover owner. All host/service operations are fixture-local.
    host.db.chmod(0o600)
    WeatherPaperStore(host.db)
    WeatherPaperPositionStore(host.db)
    writer = sqlite3.connect(host.db)
    try:
        assert writer.execute("PRAGMA journal_mode=WAL").fetchone() == ("wal",)
        writer.execute("PRAGMA wal_autocheckpoint=0")
        writer.execute("INSERT INTO signal_history(body) VALUES ('legacy committed WAL row')")
        writer.commit()
        assert Path(str(host.db) + "-wal").stat().st_size > 0
        before = host.m._logical_db_digest(host.db)
        account_before = host.account.read_bytes()
        unrelated = host.root / "unrelated"
        unrelated.mkdir()
        monkeypatch.chdir(unrelated)
        generation = cutover(host)
        directory, manifest = host.m._load_generation(generation, host.policy, require_root=False)
        backup = directory / "predecessor.sqlite3"
        assert manifest["predecessor_db_sha256"] == host.m.sha256_file(backup)
        assert manifest["predecessor_db_logical_sha256"] == before == host.m._logical_db_digest(backup)
        with sqlite3.connect(backup) as restored:
            assert restored.execute("PRAGMA quick_check").fetchall() == [("ok",)]
            assert restored.execute("SELECT body FROM signal_history ORDER BY id").fetchall() == [
                ("predecessor",), ("legacy committed WAL row",)]
            assert restored.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name").fetchall() == writer.execute(
                "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name").fetchall()
        assert host.m._logical_db_digest(host.db) == before
        assert host.account.read_bytes() == account_before
    finally:
        writer.close()


def test_gpt6_f35_attestation_cli_imports_from_clean_unrelated_cwd(tmp_path: Path):
    env = dict(os.environ)
    env.pop("PYTHONPATH", None)
    result = subprocess.run(
        [sys.executable, str(ROOT / "deploy" / "attest-weather-paper-runtime.py"), "--help"],
        cwd=tmp_path,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "ModuleNotFoundError" not in result.stderr


def test_gpt6_b6_isolation_component_rejects_enabled_legacy_service(tmp_path: Path):
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    systemctl = fake_bin / "systemctl"
    systemctl.write_text(
        "#!/usr/bin/env bash\n"
        "if [[ \"$1\" == \"is-active\" ]]; then exit 3; fi\n"
        "if [[ \"$1\" == \"is-enabled\" && \"$3\" == \"polymarket-edge-scanner.service\" ]]; then exit 0; fi\n"
        "exit 1\n",
        encoding="utf-8",
    )
    systemctl.chmod(0o755)
    env = dict(os.environ)
    env["PATH"] = str(fake_bin) + os.pathsep + env.get("PATH", "")
    result = subprocess.run(
        ["bash", str(ROOT / "deploy" / "check-weather-paper-service-isolation.sh"), "--require-disabled"],
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    assert result.returncode != 0
    assert "enabled for persistence" in result.stderr
