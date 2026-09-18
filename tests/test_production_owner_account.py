"""No-Builder owner mode; fixed test keys, mocked transports, no real account."""
import asyncio
import json
import time

import pytest
from eth_account import Account
from eth_account.messages import encode_typed_data

from polymarket_scanner.production.config import ConfigurationError, OWNER_CUSTODY_ACK
from polymarket_scanner.production.chain import ExchangeError, SubmissionNotAttempted, STANDARD_EXCHANGE
from polymarket_scanner.production.exchange import deposit_wallet_typed_order, SESSION_SIGNATURE_MAGIC
from polymarket_scanner.production.owner_account import ExchangeDepositOwner
from test_production_deposit_session import OWNER_KEY, OWNER, DEPOSIT, SESSION, SessionChain
from test_production_exchange import API_KEY, API_SECRET, Wire, NOW, context, prepare
from test_production_lifecycle import config, candidate, Weather, Exchange
from polymarket_scanner.production.engine import ExecutionEngine, ExecutionError
from polymarket_scanner.production.ledger import ExecutionLedger, LedgerError
from polymarket_scanner.production.signals import SignalStore, SignalReader


def owner_config(path, **overrides):
    values = dict(wallet=DEPOSIT, signer=OWNER, deposit_owner=OWNER,
        wallet_type="DEPOSIT_WALLET", signature_type=3, signer_type="OWNER",
        owner_custody_ack=OWNER_CUSTODY_ACK, wallet_exclusive_until=time.time()+90000)
    values.update(overrides)
    return config(path, **values)


def owner_client(wire=None, *, chain=None, clock=None, exclusive=NOW+9000, **extra):
    return ExchangeDepositOwner(private_key=OWNER_KEY, api_key=API_KEY,
        api_secret=API_SECRET, api_passphrase="offline-fixture", wallet=DEPOSIT,
        signer=OWNER, deposit_owner=OWNER, owner_custody_ack=OWNER_CUSTODY_ACK,
        wallet_exclusive_until=exclusive, transport=wire or Wire(), chain=chain or SessionChain(),
        clock=clock or (lambda: NOW), fee_policy="ONCHAIN_BOUND", **extra)


@pytest.mark.parametrize("change", [
    {"owner_custody_ack": None}, {"owner_custody_ack": "CLOB_ONLY"},
    {"signer_type": "SESSION_KEY"}, {"signer_type": "AUTO"},
    {"signer": SESSION}, {"signature_type": 0},
    {"session_scopes": ["CLOB"]}, {"session_valid_until": NOW+1000},
    {"wallet_exclusive_until": True}, {"wallet_exclusive_until": float("nan")},
    {"wallet_exclusive_until": float("inf")}, {"wallet_exclusive_until": -1},
    {"builder_api_key": "not-a-real-key"}, {"withdrawal_disabled": True}])
def test_owner_mode_requires_explicit_consistent_custody_config(tmp_path, change):
    with pytest.raises(ConfigurationError):
        owner_config(tmp_path, **change)


def test_owner_config_does_not_invent_session_or_withdrawal_restriction(tmp_path):
    cfg = owner_config(tmp_path)
    assert cfg.is_deposit_owner and cfg.session_scopes == ()
    assert cfg.session_valid_until is None and cfg.session_exclusive_until is None
    assert cfg.deposit_opening_cutoff == cfg.wallet_exclusive_until - 300



def test_owner_poly1271_matches_official_rust_222143d_vector(monkeypatch):
    """Cross-check the exact public Rust test vector without any private key.

    Official rs-clob-client-v2 commit 222143d321eba97d5711a848265eb9aab3bc7ff4
    test `v2_poly1271_signing_matches_deposit_wallet_signature` uses Amoy and a
    tiny synthetic token id that Alpha deliberately rejects in production.  This
    fixture temporarily bypasses only that token-namespace guard, then proves the
    nested typed-data digest and 317-byte ERC-7739 wrapper are byte-identical.
    """
    import polymarket_scanner.production.exchange as exchange_module

    expected = bytes.fromhex(
        "a3a093c83b6c20c83355c16ce94c92e6e9fcbdeb840618cc74f6c57a42ad145b"
        "2b98db73d2c73cbf1f2b6af288566ae81960ddbc3a13921027358a8bff3be6ff1c"
        "a440cbd865bc0c6243d7a8df9a8bf48a8827b0a4abbb61c30e96d305423af148"
        "d23d42d3ad94e65d78258cecaf8dcbaddac0f73dc085040f2c12bb595dd83804"
        "4f726465722875696e743235362073616c742c61646472657373206d616b65722c"
        "61646472657373207369676e65722c75696e7432353620746f6b656e49642c75"
        "696e74323536206d616b6572416d6f756e742c75696e743235362074616b6572"
        "416d6f756e742c75696e743820736964652c75696e7438207369676e61747572"
        "65547970652c75696e743235362074696d657374616d702c6279746573333220"
        "6d657461646174612c62797465733332206275696c6465722900ba")
    inner = expected[:65]
    monkeypatch.setattr(exchange_module, "CHAIN_ID", 80002)
    monkeypatch.setattr(exchange_module, "_token", lambda value: str(int(value)))
    order = {
        "salt": 479249096354, "maker": "0x1111111111111111111111111111111111111111",
        "signer": "0x1111111111111111111111111111111111111111", "tokenId": 1234,
        "makerAmount": 100000000, "takerAmount": 50000000, "side": 0,
        "signatureType": 3, "timestamp": 1710000000000, "metadata": "0x" + "00" * 32,
        "builder": "0x" + "00" * 32, "expiration": 0}
    typed = exchange_module.deposit_wallet_typed_order(
        order, "0xE111180000d2663C0091e4f400237545B87B996B")
    recovered = Account.recover_message(encode_typed_data(full_message=typed), signature=inner)
    assert recovered.lower() == "0xf39fd6e51aad88f6f4ce6ab8827279cfffb92266"
    wrapped = exchange_module.wrap_deposit_wallet_signature(typed, inner)
    assert len(wrapped) == 317 and wrapped == expected


def test_owner_order_uses_type3_without_session_envelope_or_builder():
    wire = Wire(context()); client = owner_client(wire)
    prepared = prepare(client); order = prepared["payload"]["order"]
    assert order["maker"] == order["signer"] == DEPOSIT
    assert order["signatureType"] == 3
    raw = bytes.fromhex(order["signature"][2:])
    assert len(raw) == 317 and not raw.endswith(SESSION_SIGNATURE_MAGIC)
    recovered = Account.recover_message(encode_typed_data(
        full_message=deposit_wallet_typed_order(order, STANDARD_EXCHANGE)), signature=raw[:65])
    assert recovered.lower() == OWNER
    wire.responses.append((200, {"success": True, "orderID": prepared["order_id"],
                                  "status": "live", "errorMsg": ""}))
    assert client.submit(prepared) == "ACKNOWLEDGED"
    assert wire.calls[-1][2]["headers"]["POLY_ADDRESS"] == OWNER
    assert all("BUILDER" not in key for key in wire.calls[-1][2]["headers"])


def test_owner_eligibility_ignores_session_registry_and_checks_actual_wallet():
    chain = SessionChain(); chain.session_authorized_until = 0
    responses = [(200, {"country": "KW", "blocked": False}),
                 (200, {"apiKeys": [API_KEY]}), (200, {"closed_only": False})]
    result = owner_client(Wire(responses), chain=chain).eligibility()
    assert result["openings_allowed"] and result["signer_type"] == "OWNER"
    assert "FULL_OWNER" in result["custody"]
    chain.owner = SESSION
    assert owner_client(chain=chain).owner_opening_restriction() == "DEPOSIT_WALLET_CURRENT_OWNER_MISMATCH"


def test_owner_cutoff_and_post_guard_keep_same_fail_closed_boundary():
    clock = [NOW]
    wire = Wire(context()); client = owner_client(wire, clock=lambda: clock[0], exclusive=NOW+301)
    prepared = prepare(client)
    assert prepared["valid_until"] == NOW+1
    clock[0] += 1
    with pytest.raises(SubmissionNotAttempted):
        client.submit(prepared)
    assert all(call[0] != "POST" for call in wire.calls)
    with pytest.raises(ExchangeError, match="ORDER_OUTLIVES_OWNER_OPENING_WINDOW"):
        owner_client(exclusive=NOW+450).prepare_buy(token="1", condition="unused",
            quantity=1, price="0.4", fee_cap="0.01", order_type="GTD", expiration=NOW+240)


class OwnerExchange(Exchange):
    def __init__(self):
        super().__init__(); self.wallet = DEPOSIT
    def owner_opening_restriction(self):
        return None
    def account_snapshot(self, **kwargs):
        return dict(super().account_snapshot(**kwargs), wallet=DEPOSIT, signer=OWNER,
            deposit_owner=OWNER, wallet_type="DEPOSIT_WALLET", signer_type="OWNER",
            signature_type=3, order_visibility="OWNER_SIGNER_ONLY", wallet_activity=[],
            wallet_activity_session_trades=[], wallet_activity_after=1,
            wallet_activity_before=int(time.time()))


def owner_engine(path):
    cfg = owner_config(path); sig = candidate()
    cfg.activation_file.write_text(json.dumps({"action": "ACTIVATE_LIVE_EXECUTION",
        "wallet": DEPOSIT, "config_sha256": cfg.config_sha256}))
    cfg.activation_file.chmod(0o600)
    store = SignalStore(cfg.signal_db); store.bind_telegram({"bot_id":"123","chat_id":"42"})
    store.save(sig); store.begin_send(sig["id"]); store.receipt(sig["id"], 1)
    ledger = ExecutionLedger(cfg.execution_db, DEPOSIT); ex = OwnerExchange()
    engine = ExecutionEngine(cfg, ledger, SignalReader(cfg.signal_db), ex, Weather(sig))
    return cfg, sig, ledger, ex, engine


def test_owner_engine_uses_explicit_custody_and_normal_durable_execution(tmp_path):
    cfg, sig, ledger, ex, engine = owner_engine(tmp_path)
    asyncio.run(engine.reconcile())
    assert engine.authority()
    assert engine.status()["signing_authority"] == "DEPOSIT_WALLET_FULL_OWNER_KEY_NOT_WITHDRAWAL_RESTRICTED"
    assert "OWNER" in engine.status()["account_performance_scope"]
    assert asyncio.run(engine.execute(sig)) and len(ex.posts) == 1
    assert not asyncio.run(engine.execute(sig)) and len(ex.posts) == 1
    with pytest.raises(LedgerError, match="EXECUTION_ADAPTER_IDENTITY_MISMATCH"):
        ledger.bind_adapter_identity(wallet_type="DEPOSIT_WALLET", signer=SESSION,
            signature_type=3, deposit_owner=OWNER, signer_type="SESSION_KEY")


@pytest.mark.parametrize("mutation", ["external", "missing", "wrong_mode"])
def test_owner_final_snapshot_retains_wallet_wide_isolation(tmp_path, mutation):
    cfg, sig, ledger, ex, engine = owner_engine(tmp_path)
    asyncio.run(engine.reconcile()); original = ex.account_snapshot
    def bad_snapshot(**kwargs):
        value = original(**kwargs)
        if mutation == "external":
            value["wallet_activity"] = [{"transaction_hash":"0x"+"44"*32,
                "condition":"0x"+"55"*32, "token":"123", "side":"BUY", "quantity":1,
                "timestamp":int(time.time())}]
        elif mutation == "missing":
            value.pop("wallet_activity")
        else:
            value["signer_type"] = "SESSION_KEY"
        return value
    ex.account_snapshot = bad_snapshot
    with pytest.raises(ExecutionError):
        asyncio.run(engine.execute(sig))
    assert ex.posts == [] and ledger.state("fault") and not engine.authority()


def test_owner_unfunded_does_not_gain_financial_authority(tmp_path):
    cfg, sig, ledger, ex, engine = owner_engine(tmp_path)
    cfg.activation_file.unlink(); ex.balance = 0
    asyncio.run(engine.reconcile(allow_exchange_mutation=False))
    asyncio.run(engine.validate_preflight_completion(allow_unfunded=True))
    assert engine.account_reconciled and not engine.funding_ready and not engine.authority()


def test_cli_selects_owner_adapter_without_trying_session_or_eoa(tmp_path, monkeypatch):
    from polymarket_scanner.production import __main__ as cli
    from polymarket_scanner.production import exchange as exchanges
    from polymarket_scanner.production import weather as weather_module
    cfg = owner_config(tmp_path); ex = OwnerExchange(); ex.balance = 0
    SignalStore(cfg.signal_db).close(); ex.close = lambda: None
    monkeypatch.setattr(cli, "clean_startup", lambda: None)
    monkeypatch.setattr(cli.ProductionConfig, "load", lambda path: cfg)
    called = []
    def factory(*args, **kwargs):
        called.append(kwargs); return ex
    monkeypatch.setattr(ExchangeDepositOwner, "from_credentials_file", factory)
    def forbidden(*args, **kwargs):
        raise AssertionError("silent fallback attempted")
    monkeypatch.setattr(exchanges.ExchangeDepositSession, "from_credentials_file", forbidden)
    monkeypatch.setattr(exchanges.ExchangeEOA, "from_credentials_file", forbidden)
    class Pipeline:
        async def close(self): pass
    monkeypatch.setattr(weather_module, "WeatherPipeline", Pipeline)
    args = cli.parser().parse_args(["preflight", "--config", str(tmp_path/"unused.json"), "--allow-unfunded"])
    asyncio.run(cli.run(args))
    assert len(called) == 1 and called[0]["owner_custody_ack"] == OWNER_CUSTODY_ACK
    result = json.loads(cfg.execution_status_path.read_text())
    assert result["account_reconciled"] and not result["financial_authority"]
    assert result["signer_type"] == "OWNER" and ex.posts == [] and ex.cancels == []
