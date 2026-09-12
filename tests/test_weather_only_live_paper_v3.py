from __future__ import annotations

import asyncio
import inspect
import json
from datetime import date, datetime, timezone
from pathlib import Path
from types import SimpleNamespace

from polymarket_scanner import weather_only_live_paper_v3 as live_v3
from polymarket_scanner.weather_only_live_paper_v2 import WeatherLivePaperV2Service
from polymarket_scanner.weather_only_paper_store import WeatherPaperStore


class _StationClient:
    async def station(self, station: str):
        assert station == "EDDM"
        return SimpleNamespace(timezone="Europe/Berlin")


class _Compiled:
    station_hint = "EDDM"

    def __init__(self, target: date):
        self.target_date = target


def _service_shell() -> live_v3.WeatherLivePaperV3Service:
    service = object.__new__(live_v3.WeatherLivePaperV3Service)
    service._station_cache = {}
    service.station_client = _StationClient()
    service._forecast_same_day_suppressed_total = 0
    service._forecast_history_quarantined_total = 0
    service._quarantine_errors = []
    # 2026-09-12 12:00 UTC = 14:00 Europe/Berlin.
    service._now_epoch = lambda: datetime(2026, 9, 12, 12, tzinfo=timezone.utc).timestamp()
    return service


def test_same_day_raw_gefs_candidate_is_suppressed(monkeypatch):
    service = _service_shell()
    called = False

    async def parent(_self, _event, _compiled):
        nonlocal called
        called = True
        return {"should": "not happen"}

    monkeypatch.setattr(WeatherLivePaperV2Service, "_forecast_candidate", parent)
    result = asyncio.run(service._forecast_candidate({}, _Compiled(date(2026, 9, 12))))
    assert result is None
    assert called is False
    assert service._forecast_same_day_suppressed_total == 1


def test_future_day_raw_gefs_candidate_remains_research_eligible(monkeypatch):
    service = _service_shell()

    async def parent(_self, _event, compiled):
        return {"target": compiled.target_date.isoformat()}

    monkeypatch.setattr(WeatherLivePaperV2Service, "_forecast_candidate", parent)
    result = asyncio.run(service._forecast_candidate({}, _Compiled(date(2026, 9, 13))))
    assert result == {"target": "2026-09-13"}
    assert service._forecast_same_day_suppressed_total == 0


def test_historical_same_day_signal_is_quarantined_out_of_pnl(tmp_path: Path):
    db_path = tmp_path / "paper.sqlite"
    signals = WeatherPaperStore(db_path)
    sent = datetime(2026, 9, 12, 12, tzinfo=timezone.utc).timestamp()

    same_day = signals.save_signal(
        fingerprint="munich-bad",
        lane="weather_forecast_raw_gap",
        evidence_class="RESEARCH_ONLY_UNCALIBRATED",
        event_id="munich-12",
        market_id="m-bad",
        side="YES",
        token_id="t-bad",
        model_probability=29 / 31,
        entry_cost=0.11,
        raw_gap=0.82,
        theoretical_payout=1.0,
        created_at=sent - 1,
        payload={
            "event_title": "Lowest temperature in Munich on September 12?",
            "station": "EDDM",
            "target_date": "2026-09-12",
            "ask_size": 100.0,
        },
    )
    future = signals.save_signal(
        fingerprint="munich-future",
        lane="weather_forecast_raw_gap",
        evidence_class="RESEARCH_ONLY_UNCALIBRATED",
        event_id="munich-13",
        market_id="m-good",
        side="YES",
        token_id="t-good",
        model_probability=20 / 31,
        entry_cost=0.25,
        raw_gap=0.39,
        theoretical_payout=1.0,
        created_at=sent - 1,
        payload={
            "event_title": "Lowest temperature in Munich on September 13?",
            "station": "EDDM",
            "target_date": "2026-09-13",
            "ask_size": 100.0,
        },
    )
    assert same_day is not None and future is not None
    signals.mark_telegram_sent(same_day, 1001, sent_at=sent)
    signals.mark_telegram_sent(future, 1002, sent_at=sent)

    positions = live_v3.GuardedWeatherPaperPositionStore(db_path)
    positions.ensure_sent_positions(10.0)
    assert positions.stats()["open"] == 2

    service = _service_shell()
    service.positions = positions
    result = asyncio.run(service.quarantine_observation_blind_history())
    assert result["quarantined_now"] == 1
    assert result["errors"] == []

    with positions._conn() as db:
        bad_signal = db.execute(
            "SELECT status FROM weather_paper_signals WHERE id=?", (same_day,)
        ).fetchone()
        good_signal = db.execute(
            "SELECT status FROM weather_paper_signals WHERE id=?", (future,)
        ).fetchone()
        bad_position = db.execute(
            "SELECT status,no_fill_reason FROM weather_paper_positions WHERE signal_id=?",
            (same_day,),
        ).fetchone()
        good_position = db.execute(
            "SELECT status FROM weather_paper_positions WHERE signal_id=?", (future,)
        ).fetchone()
    assert bad_signal["status"] == live_v3.QUARANTINED_STATUS
    assert good_signal["status"] == "OPEN"
    assert bad_position["status"] == live_v3.QUARANTINED_STATUS
    assert bad_position["no_fill_reason"] == live_v3.QUARANTINE_REASON
    assert good_position["status"] == "OPEN"

    stats = positions.stats()
    assert stats["quarantined"] == 1
    assert stats["open"] == 1
    assert stats["resolved"] == 0
    assert abs(stats["capital_all"] - 10.0) < 1e-9
    assert abs(stats["open_capital"] - 10.0) < 1e-9
    assert stats["pnl"] == 0.0


def test_v3_surface_contains_no_order_api_and_renderer_points_to_v3():
    source = inspect.getsource(live_v3)
    for forbidden in ("py_clob_client", "create_order(", "post_order(", "cancel_order(", "private_key"):
        assert forbidden not in source

    import importlib.util

    renderer_path = Path("deploy/render-weather-paper-unit.py")
    spec = importlib.util.spec_from_file_location("weather_paper_renderer_v3", renderer_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    unit = module.render(
        Path("/home/test/polymarket-edge-scanner"),
        Path("/home/test/.polymarket-edge-scanner"),
        "testuser",
    )
    assert "weather_only_live_paper_v3" in unit
    assert "--paper-stake-usd 10" in unit
    assert "MemorySwapMax=0" in unit
    assert "app_trade_only" not in unit
    assert "command_worker" not in unit
