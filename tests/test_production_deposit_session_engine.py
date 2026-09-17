"""Execution-engine policy for the restricted Deposit Wallet session adapter."""
import asyncio
import json
import time

import pytest
from eth_account import Account

from polymarket_scanner.production.engine import ExecutionEngine, ExecutionError
from polymarket_scanner.production.ledger import ExecutionLedger
from polymarket_scanner.production.signals import SignalStore, SignalReader
from test_production_lifecycle import Exchange, Weather, candidate, config

SESSION = Account.from_key((2).to_bytes(32, "big")).address.lower()
OWNER = Account.from_key((3).to_bytes(32, "big")).address.lower()
DEPOSIT = "0x" + "33" * 20


class SessionExchange(Exchange):
    def __init__(self, *, visibility="SESSION_SIGNER_ONLY"):
        super().__init__()
        self.wallet = DEPOSIT
        self.visibility = visibility
    def account_snapshot(self, **kwargs):
        value=super().account_snapshot(**kwargs)
        value.update(wallet=DEPOSIT, signer=SESSION, wallet_type="DEPOSIT_WALLET", deposit_owner=OWNER,
                     signature_type=3, order_visibility=self.visibility, wallet_activity=[],
                     wallet_activity_session_trades=[], wallet_activity_after=1)
        return value


def session_config(tmp_path, **overrides):
    values=dict(wallet=DEPOSIT, signer=SESSION, deposit_owner=OWNER,
                wallet_type="DEPOSIT_WALLET", signature_type=3,
                session_scopes=["CLOB"], session_valid_until=time.time()+100_000,
                session_exclusive_until=time.time()+90_000)
    values.update(overrides)
    return config(tmp_path, **values)


def make_engine(tmp_path, **cfg_overrides):
    cfg=session_config(tmp_path, **cfg_overrides)
    cfg.activation_file.write_text(json.dumps({"action":"ACTIVATE_LIVE_EXECUTION",
        "wallet":DEPOSIT,"config_sha256":cfg.config_sha256}))
    sig=candidate(); store=SignalStore(cfg.signal_db)
    store.bind_telegram({"bot_id":"123","chat_id":"42"})
    store.save(sig); store.begin_send(sig["id"]); store.receipt(sig["id"],1)
    ledger=ExecutionLedger(cfg.execution_db,DEPOSIT); exchange=SessionExchange(); weather=Weather(sig)
    engine=ExecutionEngine(cfg,ledger,SignalReader(cfg.signal_db),exchange,weather)
    return cfg,sig,store,ledger,exchange,engine


def test_session_engine_reconciles_and_reports_restricted_authority(tmp_path):
    cfg,sig,store,ledger,exchange,engine=make_engine(tmp_path)
    asyncio.run(engine.reconcile())
    assert engine.reconciled and engine.authority()
    status=engine.status()
    assert status["wallet_type"] == "DEPOSIT_WALLET" and status["signature_type"] == 3
    assert status["session_scopes"] == ["CLOB"]
    assert status["deposit_owner"] == OWNER
    assert status["signing_authority"] == "DEPOSIT_WALLET_SESSION_KEY_OWNER_KEY_OFF_HOST"
    assert "DEPOSIT_WALLET" in status["account_performance_scope"]


def test_session_owner_binding_mismatch_is_sticky_fault(tmp_path):
    cfg,sig,store,ledger,exchange,engine=make_engine(tmp_path)
    original=exchange.account_snapshot
    exchange.account_snapshot=lambda **kwargs: dict(original(**kwargs),deposit_owner="0x"+"44"*20)
    with pytest.raises(ExecutionError,match="DEPOSIT_OWNER_ACCOUNT_MISMATCH"):
        asyncio.run(engine.reconcile())
    assert ledger.state("fault") == "DEPOSIT_OWNER_ACCOUNT_MISMATCH"
    assert not engine.authority()


def test_session_visibility_mismatch_is_sticky_fault(tmp_path):
    cfg,sig,store,ledger,exchange,engine=make_engine(tmp_path)
    exchange.visibility="FULL_ACCOUNT"
    with pytest.raises(ExecutionError,match="SESSION_ACCOUNT_VISIBILITY_MISMATCH"):
        asyncio.run(engine.reconcile())
    assert ledger.state("fault") == "SESSION_ACCOUNT_VISIBILITY_MISMATCH"
    assert not engine.authority()


def test_session_expiry_or_exclusivity_blocks_opening_without_erasing_state(tmp_path):
    cfg,sig,store,ledger,exchange,engine=make_engine(
        tmp_path, session_exclusive_until=time.time()-1,
        session_valid_until=time.time()+100_000)
    asyncio.run(engine.reconcile())
    assert engine.reconciled
    assert engine.base_authority_reason() == "DEDICATED_SESSION_EXCLUSIVITY_EXPIRED"
    assert not asyncio.run(engine.execute(sig))
    assert ledger.orders() == []


def test_wallet_wide_external_trade_faults_even_with_no_current_position(tmp_path):
    cfg,sig,store,ledger,exchange,engine=make_engine(tmp_path)
    original=exchange.account_snapshot
    exchange.account_snapshot=lambda **kwargs: dict(original(**kwargs), wallet_activity=[{
        "transaction_hash":"0x"+"44"*32, "condition":"0x"+"55"*32,
        "token":str(2**150+2**90+1234), "side":"BUY", "quantity":1_000_000,
        "price":"0.4", "timestamp":int(time.time())}],
        wallet_activity_session_trades=[])
    with pytest.raises(ExecutionError,match="EXTERNAL_WALLET_TRADE_ACTIVITY"):
        asyncio.run(engine.reconcile())
    assert ledger.state("fault") == "EXTERNAL_WALLET_TRADE_ACTIVITY"
    assert exchange.balances == {} and not engine.authority()


def test_wallet_activity_witness_missing_fails_closed(tmp_path):
    cfg,sig,store,ledger,exchange,engine=make_engine(tmp_path)
    original=exchange.account_snapshot
    exchange.account_snapshot=lambda **kwargs: {k:v for k,v in original(**kwargs).items()
        if not k.startswith("wallet_activity")}
    with pytest.raises(ExecutionError,match="WALLET_WIDE_ACTIVITY_WITNESS_MISSING"):
        asyncio.run(engine.reconcile())


def test_wallet_wide_activity_accounted_by_same_session_is_accepted(tmp_path):
    cfg,sig,store,ledger,exchange,engine=make_engine(tmp_path)
    original=exchange.account_snapshot
    identity={"transaction_hash":"0x"+"44"*32,"condition":"0x"+"55"*32,
        "token":str(2**150+2**90+1234),"side":"BUY"}
    exchange.account_snapshot=lambda **kwargs: dict(original(**kwargs),
        wallet_activity=[dict(identity,quantity=1_000_000,price="0.4",timestamp=int(time.time()))],
        wallet_activity_session_trades=[dict(identity,status="CONFIRMED",wallet_quantity=1_000_000)])
    asyncio.run(engine.reconcile())
    assert engine.reconciled and ledger.state("fault") is None and engine.authority()


def test_wallet_activity_quantity_excess_detects_same_tx_token_side_external_fill(tmp_path):
    cfg,sig,store,ledger,exchange,engine=make_engine(tmp_path)
    original=exchange.account_snapshot
    identity={"transaction_hash":"0x"+"66"*32,"condition":"0x"+"77"*32,
        "token":str(2**150+2**90+1234),"side":"BUY"}
    exchange.account_snapshot=lambda **kwargs: dict(original(**kwargs),
        wallet_activity=[dict(identity,quantity=2_000_000,price="0.4",timestamp=int(time.time()))],
        wallet_activity_session_trades=[dict(identity,status="CONFIRMED",wallet_quantity=1_000_000)])
    with pytest.raises(ExecutionError,match="EXTERNAL_WALLET_TRADE_ACTIVITY"):
        asyncio.run(engine.reconcile())
    assert ledger.state("fault") == "EXTERNAL_WALLET_TRADE_ACTIVITY"


def test_engine_closes_new_openings_before_exclusivity_boundary(tmp_path):
    cfg,sig,store,ledger,exchange,engine=make_engine(
        tmp_path, session_exclusive_until=time.time()+299,
        session_valid_until=time.time()+100_000)
    asyncio.run(engine.reconcile())
    assert engine.reconciled
    assert engine.base_authority_reason() == "DEDICATED_SESSION_EXCLUSIVITY_EXPIRED_OR_NEAR_EXPIRY"
    assert not engine.authority()


def test_session_signer_rotation_is_not_silent_on_existing_journal(tmp_path):
    cfg,sig,store,ledger,exchange,engine=make_engine(tmp_path)
    other=Account.from_key((6).to_bytes(32, "big")).address.lower()
    rotated=session_config(tmp_path, signer=other,
        session_valid_until=time.time()+100_000,
        session_exclusive_until=time.time()+90_000)
    with pytest.raises(Exception, match="EXECUTION_ADAPTER_IDENTITY_MISMATCH"):
        ExecutionEngine(rotated, ledger, SignalReader(cfg.signal_db), exchange, Weather(sig))


def test_cli_preflight_selects_deposit_session_adapter(tmp_path, monkeypatch):
    from polymarket_scanner.production import __main__ as cli
    from polymarket_scanner.production import exchange as exchange_module
    from polymarket_scanner.production import weather as weather_module
    cfg=session_config(tmp_path)
    sig=candidate(); exchange=SessionExchange(); weather=Weather(sig)
    exchange.account_snapshot=lambda **kwargs: dict(SessionExchange.account_snapshot(exchange, **kwargs),
        openings_allowed=True, balance=0, allowances={})
    exchange.close=lambda: None
    async def close_weather(): pass
    weather.close=close_weather
    monkeypatch.setattr(cli, "clean_startup", lambda: None)
    monkeypatch.setattr(cli.ProductionConfig, "load", classmethod(lambda cls, path: cfg))
    monkeypatch.setattr(weather_module, "WeatherPipeline", lambda: weather)
    called={}
    def deposit_factory(*args, **kwargs): called.update(kwargs); return exchange
    monkeypatch.setattr(exchange_module.ExchangeDepositSession, "from_credentials_file", staticmethod(deposit_factory))
    monkeypatch.setattr(exchange_module.ExchangeEOA, "from_credentials_file", staticmethod(lambda *a, **k: (_ for _ in ()).throw(AssertionError("EOA factory used"))))
    strict=cli.parser().parse_args(["preflight", "--config", str(tmp_path/"unused.json")])
    with pytest.raises(Exception, match="PREFLIGHT_RECONCILIATION_INCOMPLETE"):
        asyncio.run(cli.run(strict))
    args=cli.parser().parse_args(["preflight", "--config", str(tmp_path/"unused.json"), "--allow-unfunded"])
    asyncio.run(cli.run(args))
    status=json.loads(cfg.execution_status_path.read_text())
    assert status["account_reconciled"] is True and status["funding_ready"] is False
    assert status["reconciled"] is False and status["financial_authority"] is False
    assert called["wallet"] == DEPOSIT and called["signer"] == SESSION
    assert called["deposit_owner"] == OWNER
    assert called["session_scopes"] == ("CLOB",)
    assert called["session_valid_until"] == cfg.session_valid_until
    assert called["session_exclusive_until"] == cfg.session_exclusive_until


def test_same_session_signer_can_renew_expiry_without_journal_identity_change(tmp_path):
    cfg,sig,store,ledger,exchange,engine=make_engine(tmp_path)
    renewed=session_config(tmp_path, signer=SESSION,
        session_valid_until=time.time()+200_000,
        session_exclusive_until=time.time()+180_000)
    second=ExecutionEngine(renewed, ledger, SignalReader(cfg.signal_db), exchange, Weather(sig))
    assert second.config.signer == SESSION
    assert ledger.state("adapter_identity") is not None


def test_deposit_owner_rotation_is_not_silent_on_existing_journal(tmp_path):
    cfg,sig,store,ledger,exchange,engine=make_engine(tmp_path)
    other_owner=Account.from_key((5).to_bytes(32, "big")).address.lower()
    changed=session_config(tmp_path, deposit_owner=other_owner,
        session_valid_until=time.time()+100_000,
        session_exclusive_until=time.time()+90_000)
    with pytest.raises(Exception, match="EXECUTION_ADAPTER_IDENTITY_MISMATCH"):
        ExecutionEngine(changed, ledger, SignalReader(cfg.signal_db), exchange, Weather(sig))


def test_populated_legacy_journal_cannot_be_reinterpreted_as_deposit_session(tmp_path):
    legacy=ExecutionLedger(tmp_path/"legacy.db", DEPOSIT)
    legacy.set_state("trade_census_after", "123")
    with pytest.raises(Exception, match="LEGACY_EOA_JOURNAL_ADAPTER_MIGRATION_REQUIRED"):
        legacy.bind_adapter_identity(wallet_type="DEPOSIT_WALLET", signer=SESSION, signature_type=3,
                                     deposit_owner=OWNER)
    assert legacy.state("adapter_identity") is None


def test_populated_legacy_journal_can_bind_its_original_eoa_identity(tmp_path):
    wallet=Account.from_key((4).to_bytes(32, "big")).address.lower()
    legacy=ExecutionLedger(tmp_path/"legacy-eoa.db", wallet)
    legacy.set_state("trade_census_after", "123")
    legacy.bind_adapter_identity(wallet_type="EOA", signer=wallet, signature_type=0)
    assert '"wallet_type":"EOA"' in legacy.state("adapter_identity")


def test_unfunded_account_can_reconcile_without_financial_authority(tmp_path):
    cfg,sig,store,ledger,exchange,engine=make_engine(tmp_path)
    original=exchange.account_snapshot
    exchange.account_snapshot=lambda **kwargs: dict(original(**kwargs), balance=0, allowances={})
    status=asyncio.run(engine.reconcile())
    assert engine.account_reconciled is True
    assert engine.funding_ready is False and engine.reconciled is False
    assert status["last_error"] == "COLLATERAL_ALLOWANCE_READINESS_FAILED"
    assert status["financial_authority"] is False
    assert engine.base_authority_reason() == "FRESH_ACCOUNT_RECONCILIATION_REQUIRED"


def test_zero_balance_is_not_funding_ready_even_with_allowance(tmp_path):
    cfg,sig,store,ledger,exchange,engine=make_engine(tmp_path)
    original=exchange.account_snapshot
    exchange.account_snapshot=lambda **kwargs: dict(original(**kwargs), balance=0, allowances={"exchange1":100_000_000})
    status=asyncio.run(engine.reconcile())
    assert engine.account_reconciled is True
    assert engine.funding_ready is False and engine.reconciled is False
    assert status["last_error"] == "COLLATERAL_BALANCE_READINESS_FAILED"
    assert status["financial_authority"] is False


def test_preflight_reconcile_never_runs_exchange_order_management(tmp_path):
    cfg,sig,store,ledger,exchange,engine=make_engine(tmp_path)
    ledger.audit_fill_limits_upgrade=lambda: True
    async def forbidden_management():
        raise AssertionError("preflight attempted exchange order management")
    engine.manage_existing=forbidden_management
    status=asyncio.run(engine.reconcile(allow_exchange_mutation=False))
    assert engine.account_reconciled is False
    assert engine.reconciled is False and status["financial_authority"] is False
    assert status["last_error"] == "PREFLIGHT_ORDER_MANAGEMENT_REQUIRED"
