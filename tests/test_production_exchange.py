"""Offline fixtures only: no funded account, remote signing, orders or Telegram.

The deterministic private key below belongs only to a mathematical test vector.
Every transport, receipt and chain balance is isolated; no live HTTP is allowed.
"""
import base64
from copy import deepcopy
from decimal import Decimal
import hashlib
import hmac
import json

import httpx
import pytest
from eth_account import Account
from eth_account.messages import encode_typed_data

from polymarket_scanner.production.chain import (
    CTF, PUSD, USDCE, STANDARD_EXCHANGE, NEG_RISK_EXCHANGE, ZERO32,
    ChainReader, ExchangeError, JSONTransport, abi, calldata, decode_fills,
    decode_redemptions, keccak,
)
from polymarket_scanner.production.exchange import (
    CLOB, DATA, GAMMA, GEOBLOCK, ExchangeEOA, PublicMarketReader, canonical, order_hash, typed_order, validate_buy_minimum,
)

NOW = 1_789_545_600
KEY = (1).to_bytes(32, "big")
WALLET = Account.from_key(KEY).address.lower()
OTHER = "0x" + "22" * 20
TOKEN = str(2**150 + 2**90 + 1234)
CONDITION = "0x" + "31" * 32
OID = "0x" + "45" * 32
TX = "0x" + "67" * 32
BLOCK = "0x" + "89" * 32
API_KEY = "fixture-api-id"
API_SECRET = base64.urlsafe_b64encode(b"isolated-test-hmac-key").decode()


class Wire:
    def __init__(self, responses=()):
        self.responses = list(responses)
        self.calls = []

    def request(self, method, url, **kwargs):
        self.calls.append((method, url, deepcopy(kwargs)))
        if not self.responses:
            raise AssertionError("Unexpected network request in offline fixture")
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response

    def close(self):
        pass


class Chain:
    fee = 200

    def block(self, tag):
        assert tag in ("latest", "finalized")
        return {"number": 100, "hash": BLOCK, "timestamp": NOW}

    def call_uint(self, exchange, signature, types, values, **kwargs):
        assert exchange in (STANDARD_EXCHANGE, NEG_RISK_EXCHANGE)
        assert signature == "getMaxFeeRate()" and types == [] and values == []
        assert kwargs == {"block": "0x64"}
        return self.fee

    def token_balance(self, wallet, token):
        assert wallet == WALLET and token == TOKEN
        return 2_000_000

    def collateral_state(self, wallet):
        assert wallet == WALLET
        return {"balance": 9_000_000, "allowances": {STANDARD_EXCHANGE: 20_000_000, NEG_RISK_EXCHANGE: 1_000_000}}


def client(wire=None, chain=None, clock=None, fee_policy="ONCHAIN_BOUND"):
    return ExchangeEOA(private_key=KEY, api_key=API_KEY, api_secret=API_SECRET,
                       api_passphrase="fixture-passphrase", wallet=WALLET, signer=WALLET,
                       transport=wire or Wire(), chain=chain or Chain(), clock=clock or (lambda: NOW), fee_policy=fee_policy)


def context():
    return [
        (200, [{"conditionId": CONDITION, "clobTokenIds": json.dumps([TOKEN, str(int(TOKEN)+1)]),
                "active": True, "closed": False, "acceptingOrders": True, "enableOrderBook": True}]),
        (200, {"t": [{"t": TOKEN}], "nr": False, "mts": ".01", "mos": "5", "mbf": 0, "tbf": 0,
               "fd": {"r": ".05", "e": "1", "to": True}}),
        (200, {"asset_id": TOKEN, "market": CONDITION, "neg_risk": False,
               "tick_size": ".01", "min_order_size": "5", "timestamp": str(NOW*1000),
               "bids": [{"price": ".38", "size": "30"}],
               "asks": [{"price": ".42", "size": "10"}, {"price": ".40", "size": "20"}]}),
    ]


def prepare(exchange, **kwargs):
    values = dict(token=TOKEN, condition=CONDITION, quantity="15", price=".40",
                  order_type="FAK", fee_cap=".02")
    values.update(kwargs)
    return exchange.prepare_buy(**values)


def raw_order(**kwargs):
    value = {"id": OID, "maker_address": WALLET, "owner": API_KEY, "side": "BUY", "status": "LIVE",
             "original_size": "5", "size_matched": "2", "price": ".4", "asset_id": TOKEN,
             "market": CONDITION, "expiration": "0", "created_at": NOW, "associate_trades": []}
    value.update(kwargs)
    return value


def trade(**kwargs):
    value = {"id": "fixture-trade", "market": CONDITION, "asset_id": TOKEN,
             "owner": API_KEY, "maker_address": WALLET, "taker_order_id": OID,
             "side": "BUY", "trader_side": "TAKER", "price": ".4", "size": "2",
             "status": "CONFIRMED", "transaction_hash": TX, "maker_orders": [],
             "match_time": str(NOW-2), "last_update": str(NOW)}
    value.update(kwargs)
    return value


def page(items, cursor="LTE="):
    return (200, {"data": items, "next_cursor": cursor})


def topic_address(value):
    return "0x" + "00" * 12 + value[2:]


def event_log(signature, topics, data, index=0, contract=STANDARD_EXCHANGE):
    return {"address": contract, "topics": ["0x" + keccak(signature.encode()).hex(), *topics],
            "data": "0x" + data.hex(), "logIndex": hex(index), "transactionHash": TX,
            "blockHash": BLOCK, "blockNumber": "0x64", "removed": False}


def fill_log(*, quantity=2_000_000, cost=800_000, fee=12_345, index=0, wallet=WALLET, token=TOKEN, side=0, exchange=STANDARD_EXCHANGE):
    return event_log("OrderFilled(bytes32,address,address,uint8,uint256,uint256,uint256,uint256,bytes32,bytes32)",
        [OID, topic_address(wallet), topic_address(OTHER)],
        abi(["uint8", "uint256", "uint256", "uint256", "uint256", "bytes32", "bytes32"],
            [side, int(token), cost, quantity, fee, bytes(32), bytes(32)]), index, exchange)


def receipt(logs):
    return {"transactionHash": TX, "blockHash": BLOCK, "blockNumber": "0x64", "status": "0x1", "logs": logs}


def test_sign_only_deterministic_hash_matches_solidity_eip712_and_no_post():
    wire = Wire(context())
    exchange = client(wire)
    prepared = prepare(exchange)
    order = prepared["payload"]["order"]
    message = typed_order(order, STANDARD_EXCHANGE)
    values = message["message"]
    fields = message["types"]["Order"]
    typehash = bytes.fromhex("bb86318a2138f5fa8ae32fbe8e659f8fcf13cc6ae4014a707893055433818589")
    domain = keccak(abi(["bytes32", "bytes32", "bytes32", "uint256", "address"],
        [keccak(b"EIP712Domain(string name,string version,uint256 chainId,address verifyingContract)"),
         keccak(b"Polymarket CTF Exchange"), keccak(b"2"), 137, STANDARD_EXCHANGE]))
    encoded_values = [bytes.fromhex(values[x["name"]][2:]) if x["type"] == "bytes32" else values[x["name"]] for x in fields]
    body = keccak(abi(["bytes32", *[x["type"] for x in fields]], [typehash, *encoded_values]))
    assert prepared["order_id"] == "0x" + keccak(b"\x19\x01" + domain + body).hex()
    assert Account.recover_message(encode_typed_data(full_message=message), signature=order["signature"]).lower() == WALLET
    assert all(method == "GET" for method, *_ in wire.calls)
    assert order["makerAmount"] == "6000000" and order["takerAmount"] == "15000000"
    assert prepared["fee_bound"] == "0.008"
    assert prepared["wire"] == canonical(prepared["payload"])
    assert order_hash(dict(order, expiration="9999999999"), STANDARD_EXCHANGE) == prepared["order_id"]
    assert hashlib.sha256(canonical(dict(prepared["payload"], postOnly=True)).encode()).hexdigest() != prepared["wire_hash"]


@pytest.mark.parametrize("response", [ExchangeError("TRANSPORT_FAILURE"), (503, {}), (400, {"error": "fixture"}), (200, {"success": True, "orderID": OID, "status": "unmatched", "errorMsg": ""})])
def test_submission_unknown_never_retries_or_provisions(response):
    wire = Wire(context() + [response])
    exchange = client(wire)
    prepared = prepare(exchange)
    assert exchange.submit(prepared) == "UNKNOWN"
    writes = [call for call in wire.calls if call[0] != "GET"]
    assert len(writes) == 1 and writes[0][1] == CLOB + "/order"
    assert writes[0][2]["body"] == prepared["wire"].encode()


def test_accepted_response_lost_recovers_by_precomputed_hash_without_second_post():
    wire = Wire(context() + [ExchangeError("RESPONSE_NOT_JSON")])
    exchange = client(wire)
    prepared = prepare(exchange)
    assert exchange.submit(prepared) == "UNKNOWN"
    wire.responses.append((200, raw_order(id=prepared["order_id"], original_size=prepared["quantity"])))
    recovered = exchange.get_order(prepared["order_id"])
    assert recovered["matched"] == 2_000_000
    assert [x[0] for x in wire.calls].count("POST") == 1


@pytest.mark.parametrize("status", ["live", "matched", "delayed"])
def test_acknowledgement_does_not_invent_fills(status):
    wire = Wire(context())
    exchange = client(wire)
    prepared = prepare(exchange)
    wire.responses.append((200, {"success": True, "orderID": prepared["order_id"], "status": status, "errorMsg": ""}))
    assert exchange.submit(prepared) == "ACKNOWLEDGED"
    assert not {"fills", "quantity_filled", "pnl"} & set(prepared)


def test_explicit_rejection_and_exact_hmac_wire():
    wire = Wire(context() + [(200, {"success": False, "orderID": "", "status": "", "errorMsg": "insufficient fixture balance"})])
    exchange = client(wire)
    prepared = prepare(exchange)
    assert exchange.submit(prepared) == "REJECTED"
    request = wire.calls[-1][2]
    mac = hmac.new(base64.urlsafe_b64decode(API_SECRET), (str(NOW) + "POST/order").encode() + request["body"], hashlib.sha256).digest()
    assert request["headers"]["POLY_SIGNATURE"] == base64.urlsafe_b64encode(mac).decode()


def test_expired_or_modified_wire_cannot_submit():
    tick = [NOW]
    wire = Wire(context())
    exchange = client(wire, clock=lambda: tick[0])
    prepared = prepare(exchange)
    tick[0] += 6
    with pytest.raises(ExchangeError, match="PREPARED_ORDER_EXPIRED"):
        exchange.submit(prepared)
    tick[0] = NOW
    prepared["payload"]["order"]["expiration"] = "9000000000"
    with pytest.raises(ExchangeError, match="PREPARED_WIRE_MUTATED"):
        exchange.submit(prepared)
    assert len(wire.calls) == 3


@pytest.mark.parametrize("changed,code", [
    ({"price": ".405"}, "PRICE_OFF_TICK_GRID"), ({"quantity": "4"}, "ORDER_SIZE_INVALID"),
    ({"quantity": "40"}, "EXECUTABLE_LIQUIDITY_INSUFFICIENT"), ({"fee_cap": ".001"}, "EXCHANGE_FEE_BOUND"),
    ({"post_only": True}, "UNSUPPORTED_POST_ONLY"), ({"order_type": "GTC"}, "UNSUPPORTED_ORDER_TYPE"),
    ({"valid_until": NOW}, "THESIS_EXPIRED"), ({"valid_until": float("nan")}, "INVALID_NUMBER"),
])
def test_presigning_price_liquidity_fee_and_expiry_limits(changed, code):
    wire = Wire(context())
    with pytest.raises(ExchangeError, match=code):
        prepare(client(wire), **changed)
    assert all(call[0] == "GET" for call in wire.calls)


def test_maker_gtd_requires_non_crossing_limit_and_exchange_expiry_buffer():
    wire = Wire(context())
    prepared = prepare(client(wire), order_type="GTD", price=".39", expiration=NOW+180, post_only=True)
    assert prepared["payload"]["postOnly"] is True
    with pytest.raises(ExchangeError, match="GTD_EXPIRATION_TOO_SOON"):
        prepare(client(), order_type="GTD", expiration=NOW+179)
    with pytest.raises(ExchangeError, match="POST_ONLY_WOULD_CROSS"):
        prepare(client(Wire(context())), order_type="GTD", expiration=NOW+180)


@pytest.mark.parametrize("change,code", [
    (lambda x: x[0][1][0].update(conditionId=OID), "MARKET_TOKEN_MISMATCH"),
    (lambda x: x[0][1][0].update(closed=True), "MARKET_NOT_TRADABLE"),
    (lambda x: x[2][1].update(timestamp=str((NOW-16)*1000)), "BOOK_STALE"),
    (lambda x: x[1][1].pop("fd"), "FEE_EVIDENCE_MISSING"),
    (lambda x: x[1][1].update(mts=".001"), "MARKET_CONSTRAINT_MISMATCH"),
    (lambda x: x[2][1].update(asset_id=str(int(TOKEN)+3)), "BOOK_IDENTITY_MISMATCH"),
])
def test_market_evidence_identity_freshness_no_zero_fee_fallback(change, code):
    responses = context()
    change(responses)
    with pytest.raises(ExchangeError, match=code):
        client(Wire(responses)).market_snapshot(TOKEN, CONDITION)


def test_best_level_normalized_and_new_protocol_rejected():
    snap = client(Wire(context())).market_snapshot(TOKEN, CONDITION)
    assert snap["book"]["ask"] == "0.40" and snap["book"]["ask_size"] == "20"
    assert snap["received_at"] == NOW
    with pytest.raises(ExchangeError, match="UNSUPPORTED_PROTOCOL_POSITION_ID"):
        client().market_snapshot("1234", CONDITION)


def test_slow_onchain_preflight_cannot_make_old_book_look_fresh():
    tick = [NOW]
    chain = Chain()
    def slow_fee(*args, **kwargs):
        tick[0] += 16
        return 200
    chain.call_uint = slow_fee
    with pytest.raises(ExchangeError, match="BOOK_STALE_DURING_PREFLIGHT"):
        client(Wire(context()), chain=chain, clock=lambda: tick[0]).market_snapshot(TOKEN, CONDITION)


def test_trade_after_cursor_filters_match_time_and_keeps_stable_update_time():
    wire = Wire([page([trade(match_time="2026-09-16T08:00:00Z", last_update="2026-09-16T08:00:05Z")])])
    rows = client(wire).account_trades(after=NOW-600, token=TOKEN)
    assert wire.calls[0][2]["params"] == {"maker_address": WALLET, "asset_id": TOKEN, "after": str(NOW-600)}
    assert rows[0]["matched_at"] == NOW
    assert rows[0]["updated_at"] == rows[0]["matched_at"] + 5


def test_historical_trade_window_retains_both_bounds_on_every_page():
    wire = Wire([page([trade(match_time=str(NOW-500))], "older-page"),
                 page([trade(id="second", match_time=str(NOW-450))])])
    rows = client(wire).account_trades(after=NOW-600, before=NOW-300, token=TOKEN)
    assert len(rows) == 2
    for method, url, request in wire.calls:
        assert method == "GET" and url == CLOB + "/data/trades"
        assert request["params"]["before"] == str(NOW-300)
        assert request["params"]["after"] == str(NOW-600)
        assert request["params"]["asset_id"] == TOKEN
    assert wire.calls[1][2]["params"]["next_cursor"] == "older-page"


@pytest.mark.parametrize("bounds,code", [
    ({"before": True}, "INVALID_UINT"), ({"before": -1}, "INVALID_UINT"),
    ({"before": 1.5}, "INVALID_UINT"), ({"after": 10, "before": 10}, "INVALID_TRADE_TIME_WINDOW"),
    ({"after": 11, "before": 10}, "INVALID_TRADE_TIME_WINDOW"),
])
def test_invalid_historical_trade_bounds_make_no_request(bounds, code):
    wire = Wire()
    with pytest.raises(ExchangeError, match=code):
        client(wire).account_trades(**bounds)
    assert wire.calls == []


def test_before_only_window_and_out_of_window_response():
    wire = Wire([page([])])
    assert client(wire).account_trades(before=NOW) == []
    assert "after" not in wire.calls[0][2]["params"]
    with pytest.raises(ExchangeError, match="TRADE_OUTSIDE_REQUESTED_WINDOW"):
        client(Wire([page([trade()])])).account_trades(before=NOW-10)


def test_long_account_enumeration_is_not_stamped_fresh(monkeypatch):
    tick = [NOW]
    exchange = client(clock=lambda: tick[0])
    monkeypatch.setattr(exchange, "eligibility", lambda **kw: {"openings_allowed": True, "opening_restrictions": []})
    monkeypatch.setattr(exchange, "open_orders", lambda: [])
    monkeypatch.setattr(exchange, "account_trades", lambda **kw: [])
    monkeypatch.setattr(exchange, "positions", lambda: [])
    def late_balance():
        tick[0] += 16
        return {"balance": 1000, "allowances": {}}
    monkeypatch.setattr(exchange, "balance_allowance", late_balance)
    with pytest.raises(ExchangeError, match="ACCOUNT_SNAPSHOT_STALE"):
        exchange.account_snapshot()


@pytest.mark.parametrize("value,code", [(0, "UNBOUNDED_EXCHANGE_FEE"), (10000, "UNBOUNDED_EXCHANGE_FEE"), (200, None)])
def test_onchain_fee_zero_is_unlimited_never_free(monkeypatch, value, code):
    chain = ChainReader(transport=Wire(), clock=lambda: NOW)
    monkeypatch.setattr(chain, "block", lambda tag: {"number": 123})
    monkeypatch.setattr(chain, "call_uint", lambda *a, **kw: value)
    if code:
        with pytest.raises(ExchangeError, match=code):
            chain.max_fee_bps(STANDARD_EXCHANGE)
    else:
        assert chain.max_fee_bps(STANDARD_EXCHANGE) == value


def test_credential_account_mismatch_and_secure_file(tmp_path):
    with pytest.raises(ExchangeError, match="CREDENTIAL_ACCOUNT_MISMATCH"):
        ExchangeEOA(private_key=KEY, api_key=API_KEY, api_secret=API_SECRET, api_passphrase="fixture", wallet=OTHER, signer=OTHER, fee_policy="ONCHAIN_BOUND")
    with pytest.raises(ExchangeError, match="ONLY_EXPLICIT_EOA_SUPPORTED"):
        ExchangeEOA(private_key=KEY, api_key=API_KEY, api_secret=API_SECRET, api_passphrase="fixture", wallet=OTHER, signer=WALLET, fee_policy="ONCHAIN_BOUND")
    path = tmp_path / "fixture.json"
    path.write_text(json.dumps({"private_key": KEY.hex(), "api_key": API_KEY, "api_secret": API_SECRET, "api_passphrase": "fixture"}))
    path.chmod(0o644)
    with pytest.raises(ExchangeError, match="CREDENTIAL_FILE_PERMISSIONS"):
        ExchangeEOA.from_credentials_file(path, wallet=WALLET, signer=WALLET)
    path.chmod(0o600)
    assert ExchangeEOA.from_credentials_file(path, wallet=WALLET, signer=WALLET, transport=Wire(), chain=Chain(), fee_policy="ONCHAIN_BOUND").wallet == WALLET
    link = tmp_path / "linked.json"
    link.symlink_to(path)
    with pytest.raises(ExchangeError, match="CREDENTIAL_FILE_READ_FAILED"):
        ExchangeEOA.from_credentials_file(link, wallet=WALLET, signer=WALLET)


@pytest.mark.parametrize("responses,code", [
    ([(200, {"blocked": True, "country": "US"})], "GEOGRAPHIC_TRADING_RESTRICTION"),
    ([(200, {"blocked": False, "country": "GB"}), (200, {"apiKeys": ["different"]})], "CREDENTIAL_ACCOUNT_MISMATCH"),
    ([(200, {"blocked": False, "country": "GB"}), (200, {"apiKeys": [API_KEY]}), (200, {"closed_only": True})], "ACCOUNT_CLOSE_ONLY"),
])
def test_eligibility_failures_never_create_credentials(responses, code):
    wire = Wire(responses)
    with pytest.raises(ExchangeError, match=code):
        client(wire).eligibility()
    assert all(x[0] == "GET" for x in wire.calls)


def test_account_pagination_complete_and_balances_use_chain_lower_bound():
    positions = {"data": [{"proxy_wallet": WALLET, "current_size": "2", "token_id": TOKEN, "condition_id": CONDITION}],
                 "pagination": {"has_more": False, "next_cursor": None}}
    wire = Wire([(200, {"blocked": False, "country": "GB"}), (200, {"apiKeys": [API_KEY]}), (200, {"closed_only": False}),
                 page([raw_order()], "second"), page([], "LTE="), page([trade()]), (200, positions),
                 (200, {"balance": "10000000", "allowances": {STANDARD_EXCHANGE: "30000000", NEG_RISK_EXCHANGE: "500000"}})])
    snapshot = client(wire).account_snapshot()
    assert snapshot["balance"] == 9_000_000
    assert snapshot["allowances"][NEG_RISK_EXCHANGE] == 500_000
    assert snapshot["positions"][0]["quantity"] == 2_000_000
    assert snapshot["open_orders"][0]["matched"] == 2_000_000
    assert wire.calls[4][2]["params"]["next_cursor"] == "second"
    assert snapshot["trade_watermark"] == NOW-2
    assert snapshot["openings_allowed"] is True
    assert all(method == "GET" for method, *_ in wire.calls)


@pytest.mark.parametrize("geo,closed,reason", [
    ((200, {"blocked": False, "country": "PT"}), True, "ACCOUNT_CLOSE_ONLY"),
    ((200, {"blocked": True, "country": "US"}), False, "GEOGRAPHIC_TRADING_RESTRICTION"),
    (ExchangeError("TRANSPORT_FAILURE"), False, "GEOGRAPHIC_ELIGIBILITY_UNKNOWN"),
])
def test_opening_restrictions_preserve_authorized_reconciliation_reads(geo, closed, reason):
    wire = Wire([geo, (200, {"apiKeys": [API_KEY]}), (200, {"closed_only": closed}),
                 page([raw_order(status="CANCELED")]), page([trade()]),
                 (200, {"data": [], "pagination": {"has_more": False, "next_cursor": None}}),
                 (200, {"balance": "5000000", "allowances": {STANDARD_EXCHANGE: "9000000"}})])
    snapshot = client(wire).account_snapshot()
    assert snapshot["openings_allowed"] is False
    assert reason in snapshot["opening_restrictions"]
    assert snapshot["trades"][0]["status"] == "CONFIRMED"
    assert snapshot["open_orders"][0]["status"] == "CANCELED"
    assert snapshot["balance"] == 5_000_000
    assert all(method == "GET" for method, *_ in wire.calls)


def test_read_permission_denial_is_not_bypassed_during_restrictions():
    wire = Wire([(200, {"blocked": True, "country": "US"}), (403, {"error": "denied"})])
    with pytest.raises(ExchangeError, match="AUTHENTICATED_READ_FAILED"):
        client(wire).account_snapshot()
    assert len(wire.calls) == 2 and all(method == "GET" for method, *_ in wire.calls)


@pytest.mark.parametrize("responses,code", [
    ([page([raw_order()], "a"), page([], "a")], "ACCOUNT_PAGINATION_INCOMPLETE"),
    ([page([raw_order()], "a"), page([raw_order()])], "ACCOUNT_PAGINATION_DUPLICATE"),
    ([(200, {"data": []})], "ACCOUNT_PAGINATION_INCOMPLETE"),
])
def test_incomplete_account_pages_fail_closed(responses, code):
    with pytest.raises(ExchangeError, match=code):
        client(Wire(responses)).open_orders()


def test_position_pagination_envelope_and_wrong_account():
    bad = {"data": [], "next_cursor": None}
    with pytest.raises(ExchangeError, match="POSITIONS_PAGINATION_INCOMPLETE"):
        client(Wire([(200, bad)])).positions()


def test_position_walk_explicitly_includes_open_archived_and_dust():
    wire = Wire([(200, {"data": [], "pagination": {"has_more": False, "next_cursor": None}})])
    assert client(wire).positions() == []
    params = wire.calls[0][2]["params"]
    assert params["status"] == "OPEN" and params["include_archived"] == "true"
    assert params["filter_type"] == "TOKENS" and params["filter_amount"] == 0
    bad = {"data": [{"proxy_wallet": OTHER, "current_size": "1"}], "pagination": {"has_more": False, "next_cursor": None}}
    with pytest.raises(ExchangeError, match="POSITION_ACCOUNT_MISMATCH"):
        client(Wire([(200, bad)])).positions()


def test_order_absence_not_rejection_and_cancel_request_not_fill_finality():
    exchange = client(Wire([(404, {}), (200, {"canceled": [OID], "not_canceled": {}}), ExchangeError("TRANSPORT_FAILURE")]))
    assert exchange.get_order(OID) is None
    assert exchange.cancel_order(OID) == {"requested": True, "confirmed": True, "unknown": False}
    assert exchange.cancel_order(OID) == {"requested": True, "confirmed": False, "unknown": True}


def test_market_resolution_cancels_only_remainder_and_retains_matched_quantity():
    exchange = client(Wire([(200, raw_order(status="CANCELED_MARKET_RESOLVED"))]))
    order = exchange.get_order(OID)
    assert order["status"] == "CANCELED" and order["exchange_status"] == "CANCELED_MARKET_RESOLVED"
    assert order["matched"] == 2_000_000 and order["quantity"] == 5_000_000


def test_production_factory_accepts_explicit_readonly_rpc(tmp_path):
    path = tmp_path / "fixture.json"
    path.write_text(json.dumps({"private_key": KEY.hex(), "api_key": API_KEY, "api_secret": API_SECRET, "api_passphrase": "fixture"}))
    path.chmod(0o600)
    exchange = ExchangeEOA.from_credentials_file(path, wallet=WALLET, signer=WALLET,
        rpc_url="https://rpc.invalid/operator", transport=Wire(), fee_policy="ONCHAIN_BOUND")
    assert exchange.chain.rpc_url == "https://rpc.invalid/operator"


def test_actual_partial_buy_fill_accounts_collateral_fee_without_reducing_shares():
    proof = receipt([fill_log(), fill_log(quantity=1_000_000, cost=390_000, fee=6_000, index=1)])
    fills = decode_fills(proof, order_id=OID, wallet=WALLET, token=TOKEN, exchange=STANDARD_EXCHANGE)
    assert sum(x["quantity"] for x in fills) == 3_000_000
    assert sum(x["cost"] + x["fee"] for x in fills) == 1_208_345
    assert len({x["id"] for x in fills}) == 2
    assert set(fills[0]) == {"id", "order_id", "token", "quantity", "cost", "fee", "transaction_hash", "block_hash", "log_index"}


@pytest.mark.parametrize("kwargs,code", [({"wallet": OTHER}, "FILL_ACCOUNT_MISMATCH"), ({"token": str(int(TOKEN)+1)}, "FILL_IDENTITY_MISMATCH"), ({"side": 1}, "FILL_IDENTITY_MISMATCH")])
def test_wrong_account_token_and_side_not_credited(kwargs, code):
    with pytest.raises(ExchangeError, match=code):
        decode_fills(receipt([fill_log(**kwargs)]), order_id=OID, wallet=WALLET, token=TOKEN, exchange=STANDARD_EXCHANGE)
    assert decode_fills(receipt([fill_log(exchange=OTHER)]), order_id=OID, wallet=WALLET, token=TOKEN, exchange=STANDARD_EXCHANGE) == []


def test_confirmed_api_trades_require_canonical_receipt_and_deduplicate(monkeypatch):
    chain = Chain()
    calls = []
    def fills(tx, **kwargs):
        calls.append(tx)
        return decode_fills(receipt([fill_log()]), **kwargs)
    chain.fills = fills
    exchange = client(chain=chain)
    normalized = client(Wire([page([trade(), trade(id="other"), trade(id="third", status="MINED")])])).account_trades()
    assert len(exchange.confirmed_fills(OID, TOKEN, False, trades=normalized)) == 1
    assert calls == [TX]
    chain.fills = lambda *a, **kw: []
    with pytest.raises(ExchangeError, match="CONFIRMED_TRADE_CHAIN_PROOF_PENDING"):
        exchange.confirmed_fills(OID, TOKEN, False, trades=normalized)


@pytest.mark.parametrize("mode,code", [
    ("missing_receipt", "RECORDED_FILL_PROOF_MISSING"),
    ("unfinalized", "RECORDED_FILL_PROOF_MISSING"),
    ("missing_log", "RECORDED_FILL_PROOF_MISSING"),
    ("reorg", "RECORDED_FILL_PROOF_CHANGED"),
    ("changed_fee", "RECORDED_FILL_PROOF_CHANGED"),
    ("noncanonical", "RECEIPT_NONCANONICAL"),
])
def test_recorded_fill_proofs_are_audited_even_when_trade_api_omits_them(monkeypatch, mode, code):
    old = decode_fills(receipt([fill_log()]), order_id=OID, wallet=WALLET, token=TOKEN, exchange=STANDARD_EXCHANGE)
    immutable = deepcopy(old)
    proof = receipt([fill_log(fee=12_346 if mode == "changed_fee" else 12_345)])
    if mode == "missing_log":
        proof["logs"] = []
    if mode == "reorg":
        proof["blockHash"] = OID
        proof["logs"][0]["blockHash"] = OID
    chain = ChainReader(transport=Wire(), clock=lambda: NOW)
    monkeypatch.setattr(chain, "block", lambda: {"number": 99 if mode == "unfinalized" else 200})
    def rpc(method, args):
        if method == "eth_getTransactionReceipt":
            return None if mode == "missing_receipt" else proof
        return {"hash": OID if mode == "noncanonical" else proof["blockHash"], "number": "0x64"}
    monkeypatch.setattr(chain, "rpc", rpc)
    with pytest.raises(ExchangeError, match=code):
        client(chain=chain).confirmed_fills(OID, TOKEN, False, trades=[], recorded_fills=old)
    assert old == immutable


def test_recorded_fill_audit_recovers_same_proof_without_trade_indexer(monkeypatch):
    old = decode_fills(receipt([fill_log()]), order_id=OID, wallet=WALLET, token=TOKEN, exchange=STANDARD_EXCHANGE)
    chain = Chain()
    chain.fills = lambda *a, **kw: deepcopy(old)
    assert client(chain=chain).confirmed_fills(OID, TOKEN, False, trades=[], recorded_fills=old) == old


def test_account_trade_census_distinguishes_our_maker_legs_and_external_sells():
    maker = {"order_id": OID, "asset_id": TOKEN, "maker_address": WALLET, "owner": API_KEY, "side": "BUY"}
    unrelated = dict(maker, order_id=TX, maker_address=OTHER, owner="unrelated")
    rows = client(Wire([page([trade(trader_side="MAKER", maker_orders=[maker, unrelated]), trade(id="sell", side="SELL")])])).account_trades()
    assert rows[0]["owned_order_ids"] == [OID]
    assert rows[1]["owned_orders"][0]["side"] == "SELL"
    assert API_KEY not in json.dumps(rows)
    with pytest.raises(ExchangeError, match="FILL_ORDER_IDENTITY_MISMATCH"):
        client().confirmed_fills(OID, TOKEN, False, trades=[rows[1]])


@pytest.mark.parametrize("alter,code", [
    (lambda r: r.update(status="0x0"), "CHAIN_TRANSACTION_FAILED"),
    (lambda r: r["logs"][0].update(removed=True), "RECEIPT_LOGS_INVALID"),
    (lambda r: r["logs"].append(deepcopy(r["logs"][0])), "DUPLICATE_RECEIPT_LOG"),
    (lambda r: r["logs"][0].update(blockHash=OID), "RECEIPT_LOG_IDENTITY_MISMATCH"),
])
def test_failed_or_inconsistent_receipts_rejected(monkeypatch, alter, code):
    chain = ChainReader(transport=Wire(), clock=lambda: NOW)
    proof = receipt([fill_log()])
    alter(proof)
    monkeypatch.setattr(chain, "block", lambda: {"number": 200})
    monkeypatch.setattr(chain, "rpc", lambda method, params: proof if method == "eth_getTransactionReceipt" else {"hash": BLOCK, "number": "0x64"})
    with pytest.raises(ExchangeError, match=code):
        chain.confirmed_receipt(TX)


def test_unfinalized_and_reorganized_transactions_not_credited(monkeypatch):
    chain = ChainReader(transport=Wire(), clock=lambda: NOW)
    monkeypatch.setattr(chain, "block", lambda: {"number": 99})
    monkeypatch.setattr(chain, "rpc", lambda method, params: receipt([fill_log()]) if method == "eth_getTransactionReceipt" else {"hash": OID, "number": "0x64"})
    assert chain.fills(TX, order_id=OID, wallet=WALLET, token=TOKEN, exchange=STANDARD_EXCHANGE) == []
    monkeypatch.setattr(chain, "block", lambda: {"number": 200})
    with pytest.raises(ExchangeError, match="RECEIPT_NONCANONICAL"):
        chain.confirmed_receipt(TX)


def test_resolved_payout_bound_to_token_is_claimable_not_cash(monkeypatch):
    chain = ChainReader(transport=Wire(), clock=lambda: NOW)
    monkeypatch.setattr(chain, "block", lambda: {"number": 200, "hash": BLOCK, "timestamp": NOW})
    monkeypatch.setattr(chain, "call", lambda target, signature, *a, **kw: bytes.fromhex("00"*12 + USDCE[2:]) if signature == "getCtfCollateral()" else bytes.fromhex(CONDITION[2:]))
    def call_uint(target, signature, types, values, **kwargs):
        return {"getPositionId(address,bytes32)": int(TOKEN), "getOutcomeSlotCount(bytes32)": 2,
                "payoutDenominator(bytes32)": 1, "payoutNumerators(bytes32,uint256)": int(values[-1] == 0)}[signature]
    monkeypatch.setattr(chain, "call_uint", call_uint)
    resolution = chain.settlement(CONDITION, 0, token=TOKEN)
    assert resolution["payout"] == "1" and resolution["proof"]["cash_received"] is False
    with pytest.raises(ExchangeError, match="SETTLEMENT_TOKEN_MISMATCH"):
        chain.settlement(CONDITION, 0, token=str(int(TOKEN)+1))


def redemption_logs():
    burn = event_log("TransferSingle(address,address,address,uint256,uint256)",
        [topic_address(WALLET), topic_address(WALLET), topic_address("0x" + "00"*20)],
        abi(["uint256", "uint256"], [int(TOKEN), 2_000_000]), 0, CTF)
    payout = event_log("PayoutRedemption(address,address,bytes32,bytes32,uint256[],uint256)",
        [topic_address(WALLET), topic_address(USDCE), ZERO32],
        abi(["bytes32", "uint256[]", "uint256"], [bytes.fromhex(CONDITION[2:]), [1], 2_000_000]), 1, CTF)
    return [burn, payout]


def test_redemption_requires_token_burn_and_preserves_usdce_asset():
    logs = redemption_logs()
    result = decode_redemptions(receipt(logs), wallet=WALLET, condition=CONDITION, tokens={1: TOKEN})
    assert result[0]["burns"] == [{"token": TOKEN, "quantity": 2_000_000}]
    assert result[0]["proceeds"] == 2_000_000
    assert result[0]["proof"]["asset"] == USDCE and result[0]["proof"]["pusd_credit"] is False
    with pytest.raises(ExchangeError, match="REDEMPTION_BURN_PROOF_MISSING"):
        decode_redemptions(receipt(logs[1:]), wallet=WALLET, condition=CONDITION, tokens={1: TOKEN})
    logs[0]["logIndex"] = "0x2"
    with pytest.raises(ExchangeError, match="INVALID_REDEMPTION_BURN"):
        decode_redemptions(receipt(logs), wallet=WALLET, condition=CONDITION, tokens={1: TOKEN})


def test_redemption_rpc_readback_requires_direct_wallet_transaction(monkeypatch):
    chain = ChainReader(transport=Wire(), clock=lambda: NOW)
    proof = dict(receipt(redemption_logs()), gasUsed=hex(73_521), effectiveGasPrice=hex(31_234_567_890), cumulativeGasUsed=hex(999_000))
    proof.update({"from": WALLET, "to": CTF})
    monkeypatch.setattr(chain, "confirmed_receipt", lambda _: proof)
    transaction = {"hash": TX, "from": WALLET, "to": CTF, "blockHash": BLOCK, "blockNumber": "0x64", "input": calldata("redeemPositions(address,bytes32,bytes32,uint256[])",
        ["address", "bytes32", "bytes32", "uint256[]"], [USDCE, bytes(32), bytes.fromhex(CONDITION[2:]), [1]])}
    monkeypatch.setattr(chain, "rpc", lambda *a: transaction)
    monkeypatch.setattr(chain, "call", lambda *a, **kw: bytes.fromhex(CONDITION[2:]))
    monkeypatch.setattr(chain, "call_uint", lambda *a, **kw: int(TOKEN))
    result = chain.redemption_receipt(TX, wallet=WALLET, condition=CONDITION)
    assert result[0]["proceeds"] == 2_000_000
    gas = result[0]["proof"]["native_gas"]
    assert gas == {"id": f"137:{TX}:gas", "asset": "POL", "chain_id": 137,
                   "amount_wei": 73_521 * 31_234_567_890, "gas_used": 73_521,
                   "effective_gas_price_wei": 31_234_567_890, "payer": WALLET,
                   "transaction_hash": TX, "scope": "TRANSACTION_TOTAL"}
    assert chain.redemption_receipt(TX, wallet=WALLET, condition=CONDITION)[0]["proof"]["native_gas"] == gas
    proof["from"] = OTHER
    with pytest.raises(ExchangeError, match="REDEMPTION_RECEIPT_PAYER_MISMATCH"):
        chain.redemption_receipt(TX, wallet=WALLET, condition=CONDITION)
    proof["from"] = WALLET
    proof.pop("effectiveGasPrice")
    with pytest.raises(ExchangeError, match="INVALID_RPC_INTEGER"):
        chain.redemption_receipt(TX, wallet=WALLET, condition=CONDITION)
    proof["effectiveGasPrice"] = hex(31_234_567_890)
    transaction["blockHash"] = OID
    with pytest.raises(ExchangeError, match="REDEMPTION_TRANSACTION_BLOCK_MISMATCH"):
        chain.redemption_receipt(TX, wallet=WALLET, condition=CONDITION)
    transaction["blockHash"] = BLOCK
    transaction["to"] = OTHER
    with pytest.raises(ExchangeError, match="UNSUPPORTED_REDEMPTION_TRANSACTION"):
        chain.redemption_receipt(TX, wallet=WALLET, condition=CONDITION)


def test_multiple_redemption_events_cannot_duplicate_transaction_gas():
    logs = redemption_logs()
    extra = deepcopy(logs[-1])
    extra["logIndex"] = "0x2"
    with pytest.raises(ExchangeError, match="REDEMPTION_IDENTITY_MISMATCH"):
        decode_redemptions(receipt([*logs, extra]), wallet=WALLET, condition=CONDITION, tokens={1: TOKEN})


def test_transport_errors_do_not_echo_secrets_or_retry():
    calls = []
    def handler(request):
        calls.append(request)
        raise httpx.ConnectError("private fixture secret should be redacted", request=request)
    transport = JSONTransport(httpx.Client(transport=httpx.MockTransport(handler), trust_env=False))
    with pytest.raises(ExchangeError) as error:
        transport.request("POST", CLOB + "/order", body=b"fixture")
    assert str(error.value) == "TRANSPORT_FAILURE" and len(calls) == 1


def test_rpc_mutations_invalid_chain_and_stale_blocks_refused(monkeypatch):
    chain = ChainReader(transport=Wire(), clock=lambda: NOW)
    with pytest.raises(ExchangeError, match="RPC_MUTATION_FORBIDDEN"):
        chain.rpc("eth_sendRawTransaction", ["fixture"])
    monkeypatch.setattr(chain, "rpc", lambda *a: "0x1")
    with pytest.raises(ExchangeError, match="RPC_CHAIN_MISMATCH"):
        chain.verify_chain()
    monkeypatch.setattr(chain, "rpc", lambda method, args: "0x89" if method == "eth_chainId" else {"number": "0x64", "hash": BLOCK, "timestamp": hex(NOW-181)})
    with pytest.raises(ExchangeError, match="STALE_CHAIN_BLOCK"):
        chain.block()


@pytest.mark.parametrize("order_type,price,extra", [
    ("FAK", ".40", {}), ("FOK", ".40", {}),
    ("GTD", ".39", {"post_only": True, "expiration": NOW+180}),
])
def test_buy_share_minimum_does_not_prove_conservative_notional_minimum(order_type, price, extra):
    wire = Wire(context())
    with pytest.raises(ExchangeError, match="BUY_NOTIONAL_BELOW_CONSERVATIVE_MINIMUM"):
        prepare(client(wire), quantity="5", price=price, order_type=order_type, **extra)
    assert all(call[0] == "GET" for call in wire.calls)


@pytest.mark.parametrize("quantity,price,order_type,extra", [
    ("12.50", ".40", "FAK", {}),
    ("12.83", ".39", "GTD", {"post_only": True, "expiration": NOW+180}),
])
def test_both_conservative_minima_pass_at_valid_submission_boundaries(quantity, price, order_type, extra):
    adapter = client(Wire(context()))
    order = prepare(adapter, quantity=quantity, price=price, order_type=order_type, **extra)
    assert Decimal(order["quantity"]) >= 5
    assert Decimal(order["quantity"])*Decimal(order["price"]) >= 5
    assert order["market"]["minimum_size_policy"] == "REQUIRE_BOTH_SHARES_AND_BUY_NOTIONAL"
    assert order["market"]["minimum_buy_notional"] == order["market"]["min_order_size"] == "5"


@pytest.mark.parametrize("quantity,price,order_type,extra", [
    ("12.49", ".40", "FAK", {}),
    ("12.82", ".39", "GTD", {"post_only": True, "expiration": NOW+180}),
])
def test_conservative_notional_minimum_is_not_rounded_up_to_pass(quantity, price, order_type, extra):
    with pytest.raises(ExchangeError, match="BUY_NOTIONAL_BELOW_CONSERVATIVE_MINIMUM"):
        prepare(client(Wire(context())), quantity=quantity, price=price, order_type=order_type, **extra)


def test_minimum_policy_metadata_cannot_disagree_or_default_away():
    sample = client(Wire(context())).market_snapshot(TOKEN, CONDITION)
    validate_buy_minimum(sample, "12.5", ".4")
    sample["minimum_buy_notional"] = "1"
    with pytest.raises(ExchangeError, match="MINIMUM_SIZE_POLICY_EVIDENCE_MISMATCH"):
        validate_buy_minimum(sample, "12.5", ".4")
    sample.pop("minimum_size_policy")
    with pytest.raises(ExchangeError, match="MINIMUM_SIZE_POLICY_MISSING_OR_UNSUPPORTED"):
        validate_buy_minimum(sample, "12.5", ".4")
