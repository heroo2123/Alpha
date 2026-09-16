"""Account eligibility changes revoke reported opening authority immediately."""
import asyncio
from copy import deepcopy
import time
import pytest

from test_production_lifecycle import harness


def test_new_close_only_snapshot_revokes_existing_opening_authority(harness):
    cfg, signal, store, ledger, exchange, weather, engine = harness
    original = exchange.account_snapshot
    exchange.account_snapshot = lambda **kwargs: dict(original(**kwargs), openings_allowed=False)
    assert engine.authority()
    with pytest.raises(RuntimeError, match="ACCOUNT_OPENINGS_RESTRICTED"):
        asyncio.run(engine.execute(signal))
    assert not exchange.posts
    assert not engine.authority()


def test_maker_server_expiry_does_not_extend_operator_maximum(harness, monkeypatch):
    cfg, signal, store, ledger, exchange, weather, engine = harness
    now = time.time()
    monkeypatch.setattr(time, "time", lambda: now)
    maker = deepcopy(signal)
    maker.update(id="maker-max-rest", key="maker-max-rest", strategy="MAKER")
    store.save(maker)
    store.begin_send(maker["id"])
    store.receipt(maker["id"], 2)
    weather.value = maker
    exchange.ask = "0.41"
    asyncio.run(engine.execute(maker))
    assert exchange.posts
    assert exchange.prepared["expiration"] <= int(now) + cfg.risk.max_maker_rest_seconds


def test_preflight_rejects_close_only_account_while_reconciliation_remains_available(harness, monkeypatch):
    cfg, signal, store, ledger, exchange, weather, engine = harness
    from polymarket_scanner.production import __main__ as cli
    from polymarket_scanner.production import exchange as exchange_module
    from polymarket_scanner.production import weather as weather_module
    from polymarket_scanner.production.config import ConfigurationError
    original = exchange.account_snapshot
    exchange.account_snapshot = lambda **kwargs: dict(original(**kwargs), openings_allowed=False)

    async def close_weather():
        pass

    weather.close = close_weather
    exchange.close = lambda: None
    monkeypatch.setattr(cli, "clean_startup", lambda: None)
    monkeypatch.setattr(cli.ProductionConfig, "load", classmethod(lambda cls, path: cfg))
    monkeypatch.setattr(weather_module, "WeatherPipeline", lambda: weather)
    monkeypatch.setattr(exchange_module.ExchangeEOA, "from_credentials_file", staticmethod(lambda *args, **kwargs: exchange))
    asyncio.run(engine.reconcile())
    assert engine.reconciled  # Read/recovery capability is independent of opening.
    assert not engine.authority()
    args = cli.parser().parse_args(["preflight", "--config", str(cfg.status_path.parent / "unused.json")])
    with pytest.raises(ConfigurationError, match="PREFLIGHT_ACCOUNT_OPENINGS_RESTRICTED"):
        asyncio.run(cli.run(args))
    assert exchange.posts == []
