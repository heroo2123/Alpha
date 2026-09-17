"""CLI wiring replay: authentic public receipt, local fixture journal, no network."""
import asyncio

import pytest

from polymarket_scanner.production import __main__ as cli
from polymarket_scanner.production import engine as engine_module
from polymarket_scanner.production import exchange as exchange_module
from polymarket_scanner.production import weather as weather_module
from polymarket_scanner.production.config import ProductionConfig
from polymarket_scanner.production.signals import SignalStore
from test_production_deposit_session_engine import session_config
from test_production_deposit_session import SESSION_KEY, SESSION, API_KEY, API_SECRET
from test_production_exchange import Wire
from test_production_deposit_redemption import verify, holding_ledger
from deposit_redemption_fixture import ReplayChain


@pytest.mark.parametrize("route", ["standard", "negative"])
def test_cli_import_uses_deposit_verifier_and_never_exchange_management(tmp_path, monkeypatch, route):
    chain = ReplayChain(route)
    record, = verify(chain)
    ledger = holding_ledger(tmp_path, chain, record)
    cfg = session_config(tmp_path, wallet=chain.wallet, deposit_owner=chain.owner)
    signals = SignalStore(cfg.signal_db)
    wire = Wire()
    ex = exchange_module.ExchangeDepositSession(private_key=SESSION_KEY, api_key=API_KEY,
        api_secret=API_SECRET, api_passphrase="offline-fixture", wallet=chain.wallet,
        signer=SESSION, deposit_owner=chain.owner, session_scopes=("CLOB",),
        session_valid_until=cfg.session_valid_until, session_exclusive_until=cfg.session_exclusive_until,
        transport=wire, chain=chain, fee_policy="ONCHAIN_BOUND")
    monkeypatch.setattr(ProductionConfig, "load", lambda path: cfg)
    monkeypatch.setattr(exchange_module.ExchangeDepositSession, "from_credentials_file", lambda *a, **kw: ex)
    checked = []
    async def readonly_reconcile(self, **kwargs):
        assert kwargs == {"allow_exchange_mutation": False}
        checked.append(self.ledger.expected_balance(record["burns"][0]["token"]))
        return self.status()
    monkeypatch.setattr(engine_module.ExecutionEngine, "reconcile", readonly_reconcile)
    class Weather:
        async def close(self):
            pass
    monkeypatch.setattr(weather_module, "WeatherPipeline", Weather)
    args = cli.parser().parse_args(["record-redemption", "--config", str(tmp_path / "fixture-config.json"),
        "--transaction", record["transaction_hash"], "--condition", chain.condition])
    asyncio.run(cli.run(args))
    assert checked == [0]
    assert wire.calls == []
    assert ledger.summary()["verified_redemption_proceeds_by_asset_micros"] == {redemption_asset(): record["proceeds"]}
    signals.close()


def redemption_asset():
    from polymarket_scanner.production.chain import PUSD
    return PUSD
