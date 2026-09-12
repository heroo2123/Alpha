from __future__ import annotations

import importlib.util
import inspect
from pathlib import Path

from polymarket_scanner import weather_only_live_paper as live


def _render_unit(app: Path, config: Path, user: str) -> str:
    path = Path(__file__).resolve().parents[1] / "deploy" / "render-weather-paper-unit.py"
    spec = importlib.util.spec_from_file_location("render_weather_paper_unit", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.render(app, config, user)


def test_live_paper_module_has_no_authenticated_trading_or_legacy_delivery_imports():
    source = inspect.getsource(live)
    forbidden = (
        "from .polymarket import",
        "import app_trade_only",
        "command_worker_trade_only",
        "py_clob_client",
        "create_order(",
        "post_order(",
        "cancel_order(",
    )
    for text in forbidden:
        assert text not in source
    assert live.MODE == "LIVE_PAPER_RESEARCH"
    assert live.DEFAULT_FORECAST_RAW_GAP_MIN == 0.08


def test_paper_fingerprint_dedupes_inside_bucket_and_rotates_after_bucket():
    identity = {"lane": "x", "event_id": "e", "side": "YES"}
    assert live._fingerprint(identity, bucket_seconds=900, at=1000.0) == live._fingerprint(
        identity, bucket_seconds=900, at=1001.0
    )
    assert live._fingerprint(identity, bucket_seconds=900, at=1000.0) != live._fingerprint(
        identity, bucket_seconds=900, at=1900.0
    )


def test_weather_paper_unit_is_dedicated_and_has_no_order_service(tmp_path):
    app = Path("/home/test/polymarket-edge-scanner")
    config = Path("/home/test/.polymarket-edge-scanner")
    unit = _render_unit(app, config, "testuser")
    assert "weather_only_live_paper" in unit
    assert f"EnvironmentFile={config}/weather-paper.env" in unit
    assert "bot.env" not in unit
    assert "app_trade_only" not in unit
    assert "command_worker" not in unit
    assert "MemorySwapMax=0" in unit
    assert "CapabilityBoundingSet=" in unit
    assert "ReadWritePaths=/var/lib/polymarket-weather-paper" in unit
