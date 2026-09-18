"""CLI wiring replay: authentic public receipt, local fixture journal, no network."""
import asyncio
import os

import pytest

from polymarket_scanner.production import __main__ as cli
from polymarket_scanner.production import engine as engine_module
from polymarket_scanner.production import exchange as exchange_module
from polymarket_scanner.production import weather as weather_module
from polymarket_scanner.production.config import ConfigurationError, ProductionConfig
from polymarket_scanner.production.signals import SignalStore
from test_production_deposit_session_engine import session_config
from test_production_deposit_session import SESSION_KEY, SESSION, API_KEY, API_SECRET
from test_production_exchange import Wire
from test_production_deposit_redemption import verify, holding_ledger
from deposit_redemption_fixture import ReplayChain


# CI's Python setup exports LD_LIBRARY_PATH. A CLI wiring fixture models the
# isolated production launcher, not that developer/runner environment. Keep the
# real startup guard active; the negative cases below verify its full denylist.
FORBIDDEN_STARTUP_ENV = frozenset({
    "PYTHONPATH", "PYTHONHOME", "PYTHONUSERBASE", "PYTHONSTARTUP",
    "LD_PRELOAD", "LD_LIBRARY_PATH", "SSL_CERT_FILE", "SSL_CERT_DIR",
    "REQUESTS_CA_BUNDLE", "CURL_CA_BUNDLE", "HTTP_PROXY", "HTTPS_PROXY",
    "ALL_PROXY", "NO_PROXY",
})


@pytest.fixture
def isolated_startup_env(monkeypatch):
    for name in tuple(os.environ):
        if name.upper() in FORBIDDEN_STARTUP_ENV:
            monkeypatch.delenv(name)
    monkeypatch.setenv("ALPHA_DISABLE_DOTENV", "1")
    previous_umask = os.umask(0o077)
    try:
        yield
    finally:
        os.umask(previous_umask)


@pytest.mark.parametrize("route", ["standard", "negative"])
def test_cli_import_uses_deposit_verifier_and_never_exchange_management(tmp_path, monkeypatch, route, isolated_startup_env):
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


@pytest.mark.parametrize("name", sorted(FORBIDDEN_STARTUP_ENV))
@pytest.mark.parametrize("lowercase", [False, True])
def test_real_startup_guard_still_rejects_forbidden_environment(
        isolated_startup_env, monkeypatch, name, lowercase):
    monkeypatch.setenv(name.lower() if lowercase else name, "isolated-fixture-value")
    with pytest.raises(ConfigurationError, match="CODE_LOADING_OR_TRANSPORT_ENVIRONMENT_FORBIDDEN"):
        cli.clean_startup()
