from __future__ import annotations

import asyncio
import json
import time

from polymarket_scanner.weather_only_paper_commands_canonical import (
    CanonicalWeatherPaperCommandController,
)


class _Telegram:
    token = "test-token"
    chat_id = "123"


class _Store:
    def stats(self):
        return {
            "open": 2,
            "won": 3,
            "lost": 1,
            "partial": 1,
            "pnl": 12.34,
            "delivery_uncertain": 0,
        }


def test_status_plainly_separates_live_paper_ledger_from_three_layer_research(tmp_path):
    status_path = tmp_path / "status.json"
    status_path.write_text(
        json.dumps(
            {
                "cycle_ok": True,
                "finished_at": time.time(),
                "same_day_three_layer": {
                    "enabled": True,
                    "silent_research_only": True,
                    "errors": [],
                    "store": {
                        "total": 15,
                        "blocked": 14,
                        "ready_uncalibrated": 1,
                    },
                },
                "same_day_delivery_enabled": False,
                "errors": [],
            }
        ),
        encoding="utf-8",
    )
    controller = CanonicalWeatherPaperCommandController(
        telegram=_Telegram(),
        store=_Store(),
        status_path=status_path,
        paper_stake_usd=5.0,
    )
    try:
        text = controller._status_text()
        assert "PAPER TRADING" in text
        assert "Open: <b>2</b>" in text
        assert "3 wins / 1 losses / 1 push/partial" in text
        assert "Three-layer same-day research: <b>ON — silent only</b>" in text
        assert "15 captures; 14 blocked; 1 research-ready" in text
        assert "Same-day Telegram trade alerts: <b>OFF</b>" in text
        assert "Same-day research probability: <b>UNCALIBRATED</b>" in text
        assert "not</b> counted as paper trades or P&amp;L" in text
        assert "OFF pending observation/remaining-hours acceptance" not in text
        assert "Real orders: <b>DISABLED</b>" in text
    finally:
        asyncio.run(controller.close())
