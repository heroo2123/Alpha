"""Offline Deposit Wallet + CLOB Session Key execution adapter tests."""
import base64
import hashlib

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
DEPOSIT = "0x" + "33" * 20


class SessionChain:
    fee = 200
    def block(self, tag):
        assert tag in ("latest", "finalized")
        return {"number": 100, "hash": "0x" + "89" * 32, "timestamp": NOW}
    def call_uint(self, exchange, signature, types, values, **kwargs):
        assert exchange == STANDARD_EXCHANGE
        return self.fee
    def token_balance(self, wallet, token):
        assert wallet == DEPOSIT and token == TOKEN
        return 2_000_000
    def collateral_state(self, wallet):
        assert wallet == DEPOSIT
        return {"balance": 9_000_000,
                "allowances": {STANDARD_EXCHANGE: 20_000_000}}


def session_client(wire=None, *, clock=None, valid=NOW+10_000, exclusive=NOW+9_000):
    return ExchangeDepositSession(private_key=SESSION_KEY, api_key=API_KEY,
        api_secret=API_SECRET, api_passphrase="fixture-passphrase",
        wallet=DEPOSIT, signer=SESSION, session_scopes=("CLOB",),
        session_valid_until=valid, session_exclusive_until=exclusive,
        transport=wire or Wire(), chain=SessionChain(), clock=clock or (lambda: NOW),
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
    order={"salt":123456789,"maker":DEPOSIT,"signer":DEPOSIT,
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


def test_deposit_session_credentials_file_contains_session_key_not_owner_key(tmp_path):
    import json
    path=tmp_path/"session.json"
    path.write_text(json.dumps({"private_key":SESSION_KEY.hex(),"api_key":API_KEY,
        "api_secret":API_SECRET,"api_passphrase":"fixture-passphrase"}))
    path.chmod(0o600)
    exchange=ExchangeDepositSession.from_credentials_file(path,wallet=DEPOSIT,signer=SESSION,
        session_scopes=("CLOB",),session_valid_until=NOW+10000,
        session_exclusive_until=NOW+9000,transport=Wire(),chain=SessionChain(),
        clock=lambda:NOW,fee_policy="ONCHAIN_BOUND")
    assert exchange.wallet == DEPOSIT and exchange.signer == SESSION


def activity_row(*, tx, timestamp, token=TOKEN, side="BUY"):
    return {"proxy_wallet":DEPOSIT,"timestamp":timestamp,"condition_id":"0x"+"31"*32,
        "type":"TRADE","size":1.25,"usdc_size":0.5,"transaction_hash":tx,
        "price":0.4,"token_id":token,"side":side,"outcome_index":0}


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


def test_exclusivity_enters_same_safety_window_as_session_expiry():
    responses=[(200,{"blocked":False,"country":"KW"}),
               (200,{"apiKeys":[API_KEY]}),(200,{"closed_only":False})]
    exchange=session_client(Wire(responses), clock=lambda: NOW,
        valid=NOW+10_000, exclusive=NOW+299)
    result=exchange.eligibility(require_opening=False)
    assert not result["openings_allowed"]
    assert "DEDICATED_SESSION_EXCLUSIVITY_EXPIRED_OR_NEAR_EXPIRY" in result["opening_restrictions"]


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
