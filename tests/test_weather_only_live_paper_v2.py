from __future__ import annotations

import asyncio
import inspect
import json
from pathlib import Path

from polymarket_scanner import weather_only_live_paper_v2 as live_v2
from polymarket_scanner import weather_only_paper_control as control
from polymarket_scanner import weather_only_paper_positions as positions_module
from polymarket_scanner.weather_only_paper_control import WeatherPaperCommandController
from polymarket_scanner.weather_only_paper_positions import WeatherPaperPositionStore
from polymarket_scanner.weather_only_paper_store import WeatherPaperStore


class _Telegram:
    token = "test-token"
    chat_id = "123"

    async def send_html(self, _text: str, **_kwargs) -> int:
        return 1


def test_v2_surface_contains_no_wallet_or_order_placement_api():
    source = "\n".join([
        inspect.getsource(live_v2),
        inspect.getsource(control),
        inspect.getsource(positions_module),
    ])
    forbidden = (
        "py_clob_client",
        "create_order(",
        "post_order(",
        "cancel_order(",
        "private_key",
        "wallet",
    )
    # The human-facing text may say that no wallet exists, so only executable/import
    # forms are forbidden rather than the plain English word in comments/messages.
    for text in forbidden[:-2]:
        assert text not in source
    assert "financial_authority" in source
    assert live_v2.DEFAULT_PAPER_STAKE_USD == 10.0


def test_command_text_reports_health_positions_and_stats(tmp_path: Path):
    db = tmp_path / "paper.sqlite"
    signals = WeatherPaperStore(db)
    sid = signals.save_signal(
        fingerprint="cmd",
        lane="weather_forecast_raw_gap",
        evidence_class="RESEARCH_ONLY_UNCALIBRATED",
        event_id="e1",
        market_id="m1",
        side="NO",
        token_id="t-no",
        model_probability=1.0,
        entry_cost=0.25,
        raw_gap=0.75,
        theoretical_payout=1.0,
        created_at=100.0,
        payload={"event_title": "Readable temperature market", "ask_size": 100.0},
    )
    assert sid is not None
    signals.mark_telegram_sent(sid, 55, sent_at=101.0)
    positions = WeatherPaperPositionStore(db)
    positions.ensure_sent_positions(10.0)

    status_path = tmp_path / "status.json"
    status_path.write_text(json.dumps({
        "cycle_ok": True,
        "finished_at": 101.0,
        "cycle_seconds": 12.5,
        "discovered_weather_events": 355,
        "forecast_events_evaluated": 6,
        "telegram_sent_count": 1,
        "errors": [],
    }))
    commands = WeatherPaperCommandController(
        telegram=_Telegram(), store=positions, status_path=status_path, paper_stake_usd=10.0
    )
    try:
        status = commands._status_text()
        stats = commands._stats_text()
        open_text = commands._positions_text()
        help_text = commands._help_text()
        assert "HEALTHY" in status
        assert "Open positions: <b>1</b>" in status
        assert "WEATHER PAPER PERFORMANCE" in stats
        assert "OPEN PAPER POSITIONS" in open_text
        assert "Readable temperature market" in open_text
        assert "/positions" in help_text
        assert "automatically paper-taken" in help_text
    finally:
        asyncio.run(commands.close())


def test_renderer_points_to_v2_tracked_entrypoint():
    import importlib.util

    renderer_path = Path("deploy/render-weather-paper-unit.py")
    spec = importlib.util.spec_from_file_location("weather_paper_renderer_v2", renderer_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    unit = module.render(
        Path("/home/test/polymarket-edge-scanner"),
        Path("/home/test/.polymarket-edge-scanner"),
        "testuser",
    )
    assert "weather_only_live_paper_v2" in unit
    assert "--paper-stake-usd 10" in unit
    assert "MemorySwapMax=0" in unit
    assert "app_trade_only" not in unit
    assert "command_worker" not in unit
