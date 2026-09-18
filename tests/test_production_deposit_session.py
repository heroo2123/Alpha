"""Offline Deposit Wallet + CLOB Session Key execution adapter tests."""
import base64
import hashlib
import json
from pathlib import Path

import pytest
from eth_abi import decode
from eth_account import Account
from eth_account.messages import encode_typed_data

from polymarket_scanner.production.chain import STANDARD_EXCHANGE, ExchangeError
from polymarket_scanner.production.exchange import (
    ExchangeDepositSession, SESSION_SIGNATURE_MAGIC, deposit_wallet_typed_order,
    order_hash, wrap_deposit_wallet_signature, wrap_session_signature,
)
from test_production_exchange import API_KEY, API_SECRET, TOKEN, NOW, Wire, context, prepare

SESSION_KEY = (2).to_bytes(32, "big")
SESSION = Account.from_key(SESSION_KEY).address.lower()
OWNER_KEY = (3).to_bytes(32, "big")
OWNER = Account.from_key(OWNER_KEY).address.lower()
# Fixed independent official-py-sdk beacon derivation for OWNER_KEY=3.
DEPOSIT = "0xd2b19ff3196703493722d81676e4b1a9b1857bc8"
VECTOR_DEPOSIT = "0x" + "33" * 20


RUNTIME_FIXTURE = json.loads((Path(__file__).parent / "fixtures/deposit-wallet-runtime-2026-09-17.json").read_text())


class SessionChain:
    fee = 200
    beacon = "0x7a18edfe055488a3128f01f563e5b479d92ffc3a"
    beacon_implementation = "0xf7f27c29e60fe6325bef8da7f93250353d2e3294"
    factory = "0x00000000000fb5c9adea0298d729a0cb3823cc07"
    factory_impl = "0x528cc05efac2b0d255e423272187efd41248abd7"
    forwarder = "0x6dd7b5ea91608c60cd4a1944432cc30ee5a6d1ca"
    session_authorized_until = NOW + 10_000
    wallet = DEPOSIT
    owner = OWNER
    pinned_impl = "0x" + "00" * 20
    proxy = "native_beacon"
    pending_owner = "0x" + "00" * 20
    def block(self, tag):
        assert tag in ("latest", "finalized")
        return {"number": 100, "hash": "0x" + "89" * 32, "timestamp": NOW}
    def confirm_block(self, block):
        assert block == self.block("latest")
    def code(self, target, *, block):
        assert block == "0x64"
        if target == self.wallet:
            return bytes.fromhex(RUNTIME_FIXTURE["proxy_runtime_prefixes"][self.proxy]
                + "00"*12 + self.factory[2:] + "00"*12 + OWNER[2:])
        return bytes.fromhex(RUNTIME_FIXTURE["contracts"][target]["runtime_hex"][2:])
    def storage(self, target, slot, *, block):
        assert block == "0x64"
        impl_slot = "0x360894a13ba1a3210667c828492db98dca3e2076cc3735a920a3ca505d382bbc"
        beacon_slot = "0xa3f0ad74e5423aebfd80d3ef4346578335a9a72aeaee59ff6cb3582b35133d50"
        assert slot in (impl_slot, beacon_slot)
        if target == self.factory and slot == impl_slot:
            value = self.factory_impl
        elif target == self.wallet and slot == beacon_slot and self.proxy == "native_beacon":
            value = self.beacon
        elif target == self.wallet and slot == impl_slot and self.proxy == "legacy_erc1967":
            value = self.forwarder
        else:
            value = "0x" + "00"*20
        return bytes(12) + bytes.fromhex(value[2:])
    def call(self, target, signature, types, values, **kwargs):
        assert kwargs.get("block") == "0x64"
        value = None
        if target in (self.factory, self.forwarder) and signature == "BEACON()": value = self.beacon
        elif target == self.beacon:
            if signature == "implementation()":
                assert kwargs["sender"] == self.wallet
                value = self.pinned_impl if self.pinned_impl != "0x"+"00"*20 else self.beacon_implementation
            elif signature == "defaultImplementation()": value = self.beacon_implementation
            elif signature == "pinnedImplementation(address)":
                assert types == ["address"] and values == [self.wallet]
                value = self.pinned_impl
        elif target == self.wallet:
            if signature == "owner()": value = self.owner
            elif signature == "factory()": value = self.factory
            elif signature == "pendingOwner()": value = self.pending_owner
            elif signature == "id()": return bytes(12) + bytes.fromhex(OWNER[2:])
            elif signature == "walletInterfaceId()":
                from eth_utils import keccak
                return keccak(b"Polymarket.DepositWallet")
        assert value is not None, (target, signature)
        return bytes(12) + bytes.fromhex(value[2:])
    def call_uint(self, target, signature, types, values, **kwargs):
        if target == STANDARD_EXCHANGE:
            return self.fee
        assert kwargs == {"block": "0x64"}
        if target == self.factory:
            assert signature == "isOperator(address)" and values == [SESSION]
            return 0
        assert target == self.wallet
        if signature == "paused()": return 0
        assert signature == "sessionSignerAuthorizedUntil(address)"
        assert types == ["address"] and values == [SESSION]
        return self.session_authorized_until
    def token_balance(self, wallet, token):
        assert wallet == self.wallet and token == TOKEN
        return 2_000_000
    def collateral_state(self, wallet):
        assert wallet == self.wallet
        return {"balance": 9_000_000,
                "allowances": {STANDARD_EXCHANGE: 20_000_000}}


def session_client(wire=None, *, clock=None, valid=NOW+10_000, exclusive=NOW+9_000, chain=None):
    if chain is None:
        chain = SessionChain()
        try:
            chain.session_authorized_until = int(valid)
        except (OverflowError, ValueError):
            pass
    return ExchangeDepositSession(private_key=SESSION_KEY, api_key=API_KEY,
        api_secret=API_SECRET, api_passphrase="fixture-passphrase",
        wallet=DEPOSIT, signer=SESSION, deposit_owner=OWNER, session_scopes=("CLOB",),
        session_valid_until=valid, session_exclusive_until=exclusive,
        transport=wire or Wire(), chain=chain, clock=clock or (lambda: NOW),
        fee_policy="ONCHAIN_BOUND")


def test_deposit_session_signs_wallet_order_without_owner_key():
    wire=Wire(context())
    exchange=session_client(wire)
    prepared=prepare(exchange)
    order=prepared["payload"]["order"]
    assert order["maker"] == DEPOSIT and order["signer"] == DEPOSIT
    assert order["signatureType"] == 3
    assert prepared["order_id"] == order_hash(order, STANDARD_EXCHANGE, signature_type=3)
    raw=bytes.fromhex(order["signature"][2:])
    assert raw[-32:] == SESSION_SIGNATURE_MAGIC
    signer_id, reserved, deposit = decode(["bytes32","bytes32","bytes"], raw[:-32])
    assert signer_id == bytes(12)+bytes.fromhex(SESSION[2:])
    assert reserved == bytes(32)
    typed=deposit_wallet_typed_order(order, STANDARD_EXCHANGE)
    inner=deposit[:65]
    recovered=Account.recover_message(encode_typed_data(full_message=typed), signature=inner).lower()
    assert recovered == SESSION
    assert deposit == wrap_deposit_wallet_signature(typed, inner)
    assert all(method == "GET" for method, *_ in wire.calls)


def test_deposit_session_balance_allowance_uses_signature_type_three():
    wire=Wire([(200,{"balance":"9000000","allowances":{STANDARD_EXCHANGE:"20000000"}})])
    result=session_client(wire).balance_allowance()
    assert result["balance"] == 9_000_000
    method,url,kwargs=wire.calls[0]
    assert method == "GET" and url.endswith("/balance-allowance")
    assert kwargs["params"] == {"asset_type":"COLLATERAL","signature_type":3}
    assert kwargs["headers"]["POLY_ADDRESS"] == SESSION


def test_session_expiry_and_exclusivity_fail_opening_closed():
    responses=[(200,{"blocked":False,"country":"KW"}),
               (200,{"apiKeys":[API_KEY]}),(200,{"closed_only":False})]
    exchange=session_client(Wire(responses), clock=lambda: NOW,
        valid=NOW+299, exclusive=NOW+200)
    result=exchange.eligibility(require_opening=False)
    assert not result["openings_allowed"]
    assert "SESSION_AUTHORIZATION_EXPIRED_OR_NEAR_EXPIRY" in result["opening_restrictions"]


def test_deposit_session_signature_matches_independent_viem_vector():
    order={"salt":123456789,"maker":VECTOR_DEPOSIT,"signer":VECTOR_DEPOSIT,
        "tokenId":str(2**150+2**90+1234),"makerAmount":"6000000","takerAmount":"15000000",
        "side":"BUY","signatureType":3,"timestamp":"1789545600123",
        "metadata":"0x"+"00"*32,"builder":"0x"+"00"*32,"expiration":"0"}
    typed=deposit_wallet_typed_order(order, STANDARD_EXCHANGE)
    assert order_hash(order, STANDARD_EXCHANGE, signature_type=3) == "0x4b845338f42234bf031b1c870fa906e871b01e19fb6a82a3f343fca87bd4ee35"
    inner=Account.from_key(SESSION_KEY).sign_message(encode_typed_data(full_message=typed)).signature
    assert "0x"+bytes(inner).hex() == "0x86b9ba4147debb681cddd99b3edc8089a9ce263c4d69bdbfc5a3b0b02cb021025f1794e64fecdff4a3862bf4050aa6dc8e950788a2fa9727f1496fc97bb824501c"
    deposit=wrap_deposit_wallet_signature(typed, bytes(inner))
    assert deposit[65:97].hex() == "3264e159346253e26a64e00b69032db0e7d32f94628de3e6eecb50304d7af3d2"
    assert deposit[97:129].hex() == "e4e1b3168b4c2cfeb05f34b4926876af6debde3e70f3f38eeefc2a7793342461"
    assert len(deposit) == 317
    assert hashlib.sha256(deposit).hexdigest() == "9925f76ee563680f2df38bc6429a64c9ca3c54b4c82f8578dc430dfb3b0fbc3d"
    session=bytes.fromhex(wrap_session_signature(deposit, SESSION)[2:])
    assert len(session) == 480
    assert hashlib.sha256(session).hexdigest() == "1b50e8d232457865dd105e22f1777b97680ae3a012f4c4144ef16d46ce462263"


def test_deposit_session_post_uses_session_l2_identity_and_wallet_maker():
    wire=Wire(context())
    exchange=session_client(wire)
    prepared=prepare(exchange)
    wire.responses.append((200,{"success":True,"orderID":prepared["order_id"],
                                "status":"matched","errorMsg":""}))
    assert exchange.submit(prepared) == "ACKNOWLEDGED"
    method,url,kwargs=wire.calls[-1]
    assert method == "POST" and url.endswith("/order")
    assert kwargs["headers"]["POLY_ADDRESS"] == SESSION
    assert kwargs["body"] == prepared["wire"].encode()
    body=__import__('json').loads(prepared["wire"])
    assert body["owner"] == API_KEY
    assert body["order"]["maker"] == DEPOSIT and body["order"]["signer"] == DEPOSIT
    assert body["order"]["signatureType"] == 3


@pytest.mark.parametrize("owner_wallet", [
    "0x29b58e89eb61dfa0497f562ada227c808ccbd61c",  # fixed UUPS vector for OWNER_KEY=3
    "0xd2b19ff3196703493722d81676e4b1a9b1857bc8",  # fixed beacon vector for OWNER_KEY=3
])
def test_declared_owner_accepts_only_pinned_derived_deposit_wallet_forms(owner_wallet):
    exchange=ExchangeDepositSession(private_key=SESSION_KEY,api_key=API_KEY,
        api_secret=API_SECRET,api_passphrase="fixture-passphrase",
        wallet=owner_wallet,signer=SESSION,deposit_owner=OWNER,session_scopes=("CLOB",),
        session_valid_until=NOW+10_000,session_exclusive_until=NOW+9_000,
        transport=Wire(),chain=SessionChain(),clock=lambda:NOW,fee_policy="ONCHAIN_BOUND")
    assert exchange.wallet == owner_wallet and exchange.deposit_owner == OWNER


def test_deposit_session_rejects_wallet_not_derived_from_declared_owner_before_network():
    wire=Wire()
    with pytest.raises(ExchangeError,match="DEPOSIT_WALLET_OWNER_BINDING_MISMATCH"):
        ExchangeDepositSession(private_key=SESSION_KEY,api_key=API_KEY,
            api_secret=API_SECRET,api_passphrase="fixture-passphrase",
            wallet="0x"+"33"*20,signer=SESSION,deposit_owner=OWNER,session_scopes=("CLOB",),
            session_valid_until=NOW+10_000,session_exclusive_until=NOW+9_000,
            transport=wire,chain=SessionChain(),clock=lambda:NOW,fee_policy="ONCHAIN_BOUND")
    assert wire.calls == []


@pytest.mark.parametrize("owner_wallet", [
    "0x6ceacd4e15953a3648cec7b68b07a36aaf0c67f6",  # official UUPS derivation for key=2
    "0x156c4a4e832d683803e463e32622717b23f8f883",  # official beacon derivation for key=2
])
def test_deposit_session_rejects_owner_eoa_before_network(owner_wallet):
    wire = Wire()
    with pytest.raises(ExchangeError, match="DEPOSIT_SESSION_OWNER_KEY_FORBIDDEN"):
        ExchangeDepositSession(private_key=SESSION_KEY, api_key=API_KEY,
            api_secret=API_SECRET, api_passphrase="fixture-passphrase",
            wallet=owner_wallet, signer=SESSION, deposit_owner=SESSION, session_scopes=("CLOB",),
            session_valid_until=NOW+10_000, session_exclusive_until=NOW+9_000,
            transport=wire, chain=SessionChain(), clock=lambda: NOW,
            fee_policy="ONCHAIN_BOUND")
    assert wire.calls == []


def test_deposit_session_credentials_file_contains_session_key_not_owner_key(tmp_path):
    import json
    path=tmp_path/"session.json"
    path.write_text(json.dumps({"private_key":SESSION_KEY.hex(),"api_key":API_KEY,
        "api_secret":API_SECRET,"api_passphrase":"fixture-passphrase"}))
    path.chmod(0o600)
    exchange=ExchangeDepositSession.from_credentials_file(path,wallet=DEPOSIT,signer=SESSION,
        deposit_owner=OWNER,session_scopes=("CLOB",),session_valid_until=NOW+10000,
        session_exclusive_until=NOW+9000,transport=Wire(),chain=SessionChain(),
        clock=lambda:NOW,fee_policy="ONCHAIN_BOUND")
    assert exchange.wallet == DEPOSIT and exchange.signer == SESSION


def activity_row(*, tx, timestamp, token=TOKEN, side="BUY"):
    return {"proxy_wallet":DEPOSIT,"timestamp":timestamp,"condition_id":"0x"+"31"*32,
        "type":"TRADE","size":1.25,"usdc_size":0.5,"transaction_hash":tx,
        "price":0.4,"token_id":token,"side":side,"outcome_index":0}


def test_account_snapshot_wallet_witness_cutoff_follows_account_enumeration(monkeypatch):
    tick=[NOW]
    exchange=session_client(Wire(),clock=lambda:tick[0])
    monkeypatch.setattr(exchange,"eligibility",lambda **kwargs: {
        "country":"KW","closed_only":False,"blocked":False,
        "openings_allowed":True,"opening_restrictions":[]})
    monkeypatch.setattr(exchange,"open_orders",lambda: (tick.__setitem__(0,tick[0]+2) or []))
    trade_calls=[]
    def trades(*,token=None,after=None,before=None):
        trade_calls.append((after,before)); tick[0]+=2 if before is None else 0; return []
    monkeypatch.setattr(exchange,"account_trades",trades)
    monkeypatch.setattr(exchange,"positions",lambda: (tick.__setitem__(0,tick[0]+2) or []))
    seen={}
    def activity(*,after=None,before=None):
        seen.update(after=after,before=before); return []
    monkeypatch.setattr(exchange,"wallet_trade_activity",activity)
    monkeypatch.setattr(exchange,"balance_allowance",lambda: {"balance":9_000_000,"allowances":{STANDARD_EXCHANGE:20_000_000}})
    snap=exchange.account_snapshot(trade_after=NOW-600)
    assert seen["before"] == NOW+6 and seen["before"] > snap["started_at"]
    assert trade_calls[-1] == (NOW-600-7*24*60*60, NOW+6)


def test_wallet_trade_activity_current_v2_cursor_contract():
    first={"data":[activity_row(tx="0x"+"71"*32,timestamp=110)],
        "pagination":{"has_more":True,"limit":1,"next_cursor":"cursor-1","offset":0}}
    second={"data":[activity_row(tx="0x"+"72"*32,timestamp=120,side="SELL")],
        "pagination":{"has_more":False,"limit":1,"next_cursor":None,"offset":0}}
    wire=Wire([(200,first),(200,second)])
    exchange=session_client(wire)
    rows=exchange.wallet_trade_activity(after=100,before=200)
    assert [row["side"] for row in rows] == ["BUY","SELL"]
    assert [row["timestamp"] for row in rows] == [110,120]
    assert wire.calls[0][2]["params"] == {"user":DEPOSIT,"type":"TRADE","limit":100,
        "sort_direction":"ASC","start":"100","end":"200"}
    assert wire.calls[1][2]["params"]["cursor"] == "cursor-1"


def test_wallet_trade_activity_rejects_wrong_wallet_and_bad_pagination():
    wrong=activity_row(tx="0x"+"73"*32,timestamp=110)
    wrong["proxy_wallet"]="0x"+"44"*20
    wire=Wire([(200,{"data":[wrong],"pagination":{"has_more":False,"next_cursor":None}})])
    with pytest.raises(ExchangeError,match="ACTIVITY_ACCOUNT_MISMATCH"):
        session_client(wire).wallet_trade_activity(after=100,before=200)

    wire=Wire([(200,{"data":[],"pagination":{"has_more":True,"next_cursor":"cursor"}})])
    with pytest.raises(ExchangeError,match="ACTIVITY_PAGINATION_INCOMPLETE"):
        session_client(wire).wallet_trade_activity(after=100,before=200)


def test_wallet_trade_activity_rejects_nontrade_or_out_of_order_rows():
    row=activity_row(tx="0x"+"74"*32,timestamp=110); row["type"]="SPLIT"
    wire=Wire([(200,{"data":[row],"pagination":{"has_more":False,"next_cursor":None}})])
    with pytest.raises(ExchangeError,match="ACTIVITY_ACCOUNT_MISMATCH"):
        session_client(wire).wallet_trade_activity(after=100,before=200)
    rows=[activity_row(tx="0x"+"75"*32,timestamp=120),
          activity_row(tx="0x"+"76"*32,timestamp=119)]
    wire=Wire([(200,{"data":rows,"pagination":{"has_more":False,"next_cursor":None}})])
    with pytest.raises(ExchangeError,match="ACTIVITY_TIME_OR_ORDER_INVALID"):
        session_client(wire).wallet_trade_activity(after=100,before=200)


def test_deposit_factory_beacon_drift_or_unknown_closes_openings():
    responses=[(200,{"blocked":False,"country":"KW"}),
               (200,{"apiKeys":[API_KEY]}),(200,{"closed_only":False})]
    chain=SessionChain(); chain.beacon="0x"+"44"*20
    exchange=ExchangeDepositSession(private_key=SESSION_KEY,api_key=API_KEY,
        api_secret=API_SECRET,api_passphrase="fixture-passphrase",wallet=DEPOSIT,signer=SESSION,
        deposit_owner=OWNER,session_scopes=("CLOB",),session_valid_until=NOW+10_000,session_exclusive_until=NOW+9_000,
        transport=Wire(responses),chain=chain,clock=lambda:NOW,fee_policy="ONCHAIN_BOUND")
    result=exchange.eligibility(require_opening=False)
    assert not result["openings_allowed"]
    assert "DEPOSIT_WALLET_FACTORY_BEACON_DRIFT" in result["opening_restrictions"]

    class UnknownBeacon(SessionChain):
        def call(self, *args, **kwargs):
            raise ExchangeError("RPC_READ_FAILED")
    responses=[(200,{"blocked":False,"country":"KW"}),
               (200,{"apiKeys":[API_KEY]}),(200,{"closed_only":False})]
    exchange=ExchangeDepositSession(private_key=SESSION_KEY,api_key=API_KEY,
        api_secret=API_SECRET,api_passphrase="fixture-passphrase",wallet=DEPOSIT,signer=SESSION,
        deposit_owner=OWNER,session_scopes=("CLOB",),session_valid_until=NOW+10_000,session_exclusive_until=NOW+9_000,
        transport=Wire(responses),chain=UnknownBeacon(),clock=lambda:NOW,fee_policy="ONCHAIN_BOUND")
    result=exchange.eligibility(require_opening=False)
    assert "RPC_READ_FAILED" in result["opening_restrictions"]



def test_onchain_session_revocation_and_stale_expiry_fail_closed():
    responses=lambda: [(200,{"blocked":False,"country":"KW"}),
                         (200,{"apiKeys":[API_KEY]}),(200,{"closed_only":False})]
    revoked=SessionChain(); revoked.session_authorized_until=0
    result=session_client(Wire(responses()), chain=revoked).eligibility(require_opening=False)
    assert not result["openings_allowed"]
    assert "SESSION_AUTHORIZATION_REVOKED_OR_MISSING" in result["opening_restrictions"]
    assert result["session_onchain_valid_until"] == 0

    stale=SessionChain(); stale.session_authorized_until=NOW+5_000
    result=session_client(Wire(responses()), chain=stale, valid=NOW+10_000).eligibility(require_opening=False)
    assert not result["openings_allowed"]
    assert "SESSION_AUTHORIZATION_ONCHAIN_BEFORE_CONFIGURED_EXPIRY" in result["opening_restrictions"]



def test_onchain_session_authorization_read_failure_fails_closed():
    class UnknownAuthorization(SessionChain):
        def call_uint(self,target,signature,types,values,**kwargs):
            if target.lower() == DEPOSIT and signature == "sessionSignerAuthorizedUntil(address)":
                raise ExchangeError("RPC_READ_FAILED")
            return super().call_uint(target,signature,types,values,**kwargs)
    responses=[(200,{"blocked":False,"country":"KW"}),
               (200,{"apiKeys":[API_KEY]}),(200,{"closed_only":False})]
    chain=UnknownAuthorization()
    exchange=session_client(Wire(responses), chain=chain)
    result=exchange.eligibility(require_opening=False)
    assert not result["openings_allowed"]
    assert "RPC_READ_FAILED" in result["opening_restrictions"]
    with pytest.raises(ExchangeError, match="SESSION_AUTHORIZATION_ONCHAIN_UNKNOWN"):
        prepare(session_client(Wire(context()), chain=chain))


def test_onchain_session_revocation_at_final_prepare_boundary_never_posts():
    chain=SessionChain(); chain.session_authorized_until=0
    wire=Wire(context())
    exchange=session_client(wire, chain=chain)
    with pytest.raises(ExchangeError, match="SESSION_AUTHORIZATION_REVOKED_OR_MISSING"):
        prepare(exchange)
    assert wire.calls == []


def test_beacon_implementation_drift_or_unknown_closes_openings():
    responses=lambda: [(200,{"blocked":False,"country":"KW"}),
                         (200,{"apiKeys":[API_KEY]}),(200,{"closed_only":False})]
    drift=SessionChain(); drift.beacon_implementation="0x"+"44"*20
    result=session_client(Wire(responses()), chain=drift).eligibility(require_opening=False)
    assert not result["openings_allowed"]
    assert "DEPOSIT_WALLET_BEACON_IMPLEMENTATION_DRIFT" in result["opening_restrictions"]

    class UnknownImplementation(SessionChain):
        def call(self,target,signature,types,values,**kwargs):
            if target.lower() == self.beacon.lower() and signature == "implementation()":
                raise ExchangeError("RPC_READ_FAILED")
            return super().call(target,signature,types,values,**kwargs)
    result=session_client(Wire(responses()), chain=UnknownImplementation()).eligibility(require_opening=False)
    assert not result["openings_allowed"]
    assert "RPC_READ_FAILED" in result["opening_restrictions"]

def test_exclusivity_enters_same_safety_window_as_session_expiry():
    responses=[(200,{"blocked":False,"country":"KW"}),
               (200,{"apiKeys":[API_KEY]}),(200,{"closed_only":False})]
    exchange=session_client(Wire(responses), clock=lambda: NOW,
        valid=NOW+10_000, exclusive=NOW+299)
    result=exchange.eligibility(require_opening=False)
    assert not result["openings_allowed"]
    assert "DEDICATED_SESSION_EXCLUSIVITY_EXPIRED_OR_NEAR_EXPIRY" in result["opening_restrictions"]


def test_prepared_wire_cannot_cross_session_safety_boundary():
    tick=[NOW]
    wire=Wire(context())
    exchange=session_client(wire,clock=lambda:tick[0],valid=NOW+10_000,exclusive=NOW+301)
    prepared=prepare(exchange)
    assert prepared["valid_until"] == NOW+1
    tick[0]=NOW+1
    with pytest.raises(ExchangeError,match="PREPARED_ORDER_EXPIRED"):
        exchange.submit(prepared)
    assert all(method == "GET" for method, *_ in wire.calls)


def test_gtd_order_must_finish_before_session_safety_boundary():
    exchange=session_client(Wire(context()), clock=lambda: NOW,
        valid=NOW+10_000, exclusive=NOW+450)
    with pytest.raises(ExchangeError, match="ORDER_OUTLIVES_SESSION_OPENING_WINDOW"):
        exchange.prepare_buy(token=TOKEN, condition="0x"+"31"*32,
            quantity="15", price="0.4", order_type="GTD",
            expiration=int(NOW)+240, fee_cap="0.02", post_only=True,
            valid_until=NOW+100)


def test_deposit_session_rejects_nonfinite_expiry_metadata():
    with pytest.raises(ExchangeError, match="DEPOSIT_SESSION_EXPIRY_INVALID"):
        session_client(valid=float("inf"), exclusive=NOW+1000)
    with pytest.raises(ExchangeError, match="DEPOSIT_SESSION_EXPIRY_INVALID"):
        session_client(valid=float("inf"), exclusive=float("inf"))
