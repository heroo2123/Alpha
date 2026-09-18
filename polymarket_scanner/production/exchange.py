"""Narrow production CLOB adapters: direct EOA or Deposit Session, BUY, CTF Exchange V2.

There is no wallet/API-key provisioning, allowance setup, hidden retry, or
client-order-id assumption. The execution engine must commit its intent and
signed-wire digest before calling submit exactly once. Uncertain submissions
are reconciled by the precomputed EIP-712 order hash, never blindly retried.
"""
from __future__ import annotations

import base64
import binascii
from decimal import Decimal
from datetime import datetime
import hashlib
import hmac
import json
import math
import os
from pathlib import Path
import re
import secrets
import stat
import time

from .chain import (CHAIN_ID, NEG_RISK_EXCHANGE, STANDARD_EXCHANGE, ZERO32,
                    ChainReader, ExchangeError, SubmissionNotAttempted, JSONTransport, address, hash32,
                    keccak, micros, number, uint)
from .fees import fee_requirement, make_fee_evidence, validate_policy
from .config import SESSION_OPENING_SAFETY_SECONDS

CLOB = "https://clob.polymarket.com"
GAMMA = "https://gamma-api.polymarket.com"
DATA = "https://data-api.polymarket.com"
GEOBLOCK = "https://polymarket.com/api/geoblock"
TICKS = frozenset(map(Decimal, ("0.1", "0.01", "0.005", "0.0025", "0.001", "0.0001")))
MAX_PAGES = 100
MAX_ITEMS = 10_000
MAX_PREPARED_AGE = 5
MAX_BOOK_AGE = 15
MAX_ACCOUNT_SNAPSHOT_AGE = 15
WALLET_ACTIVITY_LOOKBACK_SECONDS = 7 * 24 * 60 * 60
MINIMUM_SIZE_POLICY = "REQUIRE_BOTH_SHARES_AND_BUY_NOTIONAL"
ORDER_FIELDS = (
    ("salt", "uint256"), ("maker", "address"), ("signer", "address"),
    ("tokenId", "uint256"), ("makerAmount", "uint256"), ("takerAmount", "uint256"),
    ("side", "uint8"), ("signatureType", "uint8"), ("timestamp", "uint256"),
    ("metadata", "bytes32"), ("builder", "bytes32"),
)
ORDER_TYPE = "Order(uint256 salt,address maker,address signer,uint256 tokenId,uint256 makerAmount,uint256 takerAmount,uint8 side,uint8 signatureType,uint256 timestamp,bytes32 metadata,bytes32 builder)"
EIP712_DOMAIN_TYPE = "EIP712Domain(string name,string version,uint256 chainId,address verifyingContract)"
SESSION_SIGNATURE_MAGIC = bytes.fromhex("6492649264926492649264926492649264926492649264926492649264926492")

# Pinned from the independently reviewed official Polymarket py-sdk production
# environment at commit 579bb2e56be9cc5d152546985870ee6ad795ec52.
# These constants are used only to reject a Deposit Wallet OWNER EOA from the
# restricted Session Key adapter; they do not grant or prove Session authority.
DEPOSIT_WALLET_FACTORY = "0x00000000000fb5c9adea0298d729a0cb3823cc07"
# Historical UUPS implementation used only for deterministic legacy wallet derivation.
DEPOSIT_WALLET_IMPLEMENTATION = "0x58ca52ebe0dadfdf531cde7062e76746de4db1eb"
DEPOSIT_WALLET_BEACON = "0x7a18edfe055488a3128f01f563e5b479d92ffc3a"
# Current beacon implementation independently observed and source-reviewed on 2026-09-18.
# A change is a protocol/security boundary requiring explicit re-review before openings.
DEPOSIT_WALLET_BEACON_IMPLEMENTATION = "0xf7f27c29e60fe6325bef8da7f93250353d2e3294"
_ERC1967_CONST1 = bytes.fromhex("cc3735a920a3ca505d382bbc545af43d6000803e6038573d6000fd5b3d6000f3")
_ERC1967_CONST2 = bytes.fromhex("5155f3363d3d373d3d363d7f360894a13ba1a3210667c828492db98dca3e2076")
_ERC1967_BEACON_CONST1 = bytes.fromhex("b3582b35133d50545afa5036515af43d6000803e604d573d6000fd5b3d6000f3")
_ERC1967_BEACON_CONST2 = bytes.fromhex("1b60e01b36527fa3f0ad74e5423aebfd80d3ef4346578335a9a72aeaee59ff6c")
_ERC1967_BEACON_CONST3 = bytes.fromhex("60195155f3363d3d373d3d363d602036600436635c60da")


def _deposit_wallet_owner_forms(owner: str) -> dict[str, str]:
    """Derive pinned production Deposit Wallet addresses for an OWNER EOA.

    Reject both the historical UUPS and current beacon forms. This is a local
    owner-key exclusion check, not evidence that any other EOA is authorized as
    a Session Key. Active scope/expiry still require trusted owner-side evidence.
    """
    try:
        from eth_abi import encode
        owner = address(owner)
        factory = address(DEPOSIT_WALLET_FACTORY)
        wallet_id = bytes.fromhex(owner[2:]).rjust(32, b"\0")
        args = encode(["address", "bytes32"], [factory, wallet_id])
        salt = keccak(args)

        def create2(init_code_hash: bytes) -> str:
            raw = b"\xff" + bytes.fromhex(factory[2:]) + salt + init_code_hash
            return "0x" + keccak(raw)[12:].hex()

        prefix = (0x61003D3D8160233D3973 + (len(args) << 56)).to_bytes(10, "big")
        uups_hash = keccak(prefix + bytes.fromhex(DEPOSIT_WALLET_IMPLEMENTATION[2:])
                           + bytes.fromhex("6009") + _ERC1967_CONST2 + _ERC1967_CONST1 + args)
        beacon_prefix = (0x6100523D8160233D3973 + (len(args) << 56)).to_bytes(10, "big")
        beacon_hash = keccak(beacon_prefix + bytes.fromhex(DEPOSIT_WALLET_BEACON[2:])
                             + _ERC1967_BEACON_CONST3 + _ERC1967_BEACON_CONST2
                             + _ERC1967_BEACON_CONST1 + args)
        return {"UUPS_BEACON_FORWARDER": address(create2(uups_hash)),
                "NATIVE_BEACON": address(create2(beacon_hash))}
    except ExchangeError:
        raise
    except (ImportError, TypeError, ValueError):
        raise ExchangeError("DEPOSIT_WALLET_OWNER_DERIVATION_FAILED") from None


def _deposit_wallet_owner_addresses(owner: str) -> frozenset[str]:
    return frozenset(_deposit_wallet_owner_forms(owner).values())


def canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def validate_buy_minimum(snapshot: dict, quantity, price) -> None:
    """Conservative supported subset while official minimum units are ambiguous.

    We require both shares and submitted BUY notional to reach the API's
    returned numeric minimum. This is our policy, not a claim that the venue
    enforces both units, nor a constraint on later partial-fill quantities.
    Post-only maker orders use the same submission-size checks as taker orders.
    """
    if not isinstance(snapshot, dict) or snapshot.get("minimum_size_policy") != MINIMUM_SIZE_POLICY:
        raise ExchangeError("MINIMUM_SIZE_POLICY_MISSING_OR_UNSUPPORTED")
    minimum = number(snapshot.get("min_order_size"), positive=True)
    notional = number(snapshot.get("minimum_buy_notional"), positive=True)
    if minimum != notional:
        raise ExchangeError("MINIMUM_SIZE_POLICY_EVIDENCE_MISMATCH")
    quantity, price = number(quantity, positive=True), number(price, positive=True)
    if price >= 1:
        raise ExchangeError("INVALID_BUY_PRICE")
    if quantity < minimum:
        raise ExchangeError("ORDER_SIZE_INVALID")
    if quantity * price < notional:
        raise ExchangeError("BUY_NOTIONAL_BELOW_CONSERVATIVE_MINIMUM")


def _fee_diagnostics(info: dict, market: dict, *, token: str, condition: str, observed_at) -> dict:
    """Bounded public-only evidence; no arbitrary body, header or account data."""
    def scalar(value):
        if value is None or type(value) is bool:
            return value
        if type(value) is int and -(2**256) < value < 2**256:
            return value
        if type(value) is float and math.isfinite(value):
            return value
        if isinstance(value, str) and len(value) <= 128:
            return value
        return "INVALID_PUBLIC_FIELD"

    def fields(value, names):
        if not isinstance(value, dict):
            return None
        return {name: scalar(value[name]) for name in names if name in value}

    clob = fields(info, ("mbf", "tbf", "oas", "mos", "mts"))
    clob["fd"] = fields(info.get("fd"), ("r", "e", "to"))
    gamma = fields(market, ("conditionId", "feesEnabled", "feeType"))
    gamma["feeSchedule"] = fields(market.get("feeSchedule"), ("rate", "exponent", "takerOnly", "rebateRate"))
    return {"observed_at": observed_at, "token": token, "condition": condition,
            "clob_url": CLOB + "/clob-markets/" + condition, "clob": clob,
            "gamma_url": GAMMA + "/markets?condition_ids=" + condition, "gamma": gamma}


def _array(value: object) -> list:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (ValueError, UnicodeError):
            raise ExchangeError("INVALID_ARRAY") from None
    if not isinstance(value, list):
        raise ExchangeError("INVALID_ARRAY")
    return value


def _token(value: object) -> str:
    token = uint(value)
    # SDK 0.10 uses this reserved-bit namespace for the newer V3 exchange.
    if token & (((1 << 64) - 1) << 40) == 0:
        raise ExchangeError("UNSUPPORTED_PROTOCOL_POSITION_ID")
    return str(token)


def _epoch(value: object, *, expiration=False) -> int:
    if expiration and value in (None, "", "0", 0):
        return 0
    if isinstance(value, str) and "T" in value:
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                raise ValueError
            return uint(int(parsed.timestamp()))
        except ValueError:
            raise ExchangeError("INVALID_ORDER_TIMESTAMP") from None
    return uint(value)


def typed_order(order: dict, exchange: str, *, signature_type: int = 0) -> dict:
    exchange = address(exchange)
    if exchange not in (STANDARD_EXCHANGE, NEG_RISK_EXCHANGE):
        raise ExchangeError("UNSUPPORTED_EXCHANGE")
    if type(signature_type) is not int or signature_type not in {0, 3}:
        raise ExchangeError("UNSUPPORTED_SIGNATURE_TYPE")
    try:
        message = {name: order[name] for name, _ in ORDER_FIELDS}
        for name, kind in ORDER_FIELDS:
            if kind.startswith("uint"):
                message[name] = uint(0 if name == "side" and order[name] == "BUY" else order[name])
            elif kind == "address":
                message[name] = address(order[name])
            else:
                message[name] = hash32(order[name])
    except (KeyError, TypeError):
        raise ExchangeError("INVALID_SIGNED_ORDER") from None
    if (message["side"] != 0 or message["signatureType"] != signature_type
        or message["maker"] != message["signer"] or message["metadata"] != ZERO32
        or message["builder"] != ZERO32):
        raise ExchangeError("UNSUPPORTED_SIGNED_ORDER")
    _token(message["tokenId"])
    return {"types": {
        "EIP712Domain": [{"name": n, "type": t} for n, t in (
            ("name", "string"), ("version", "string"), ("chainId", "uint256"),
            ("verifyingContract", "address"))],
        "Order": [{"name": n, "type": t} for n, t in ORDER_FIELDS]},
        "primaryType": "Order", "domain": {"name": "Polymarket CTF Exchange",
            "version": "2", "chainId": CHAIN_ID, "verifyingContract": exchange},
        "message": message}


def deposit_wallet_typed_order(order: dict, exchange: str) -> dict:
    base = typed_order(order, exchange, signature_type=3)
    types = dict(base["types"])
    types["TypedDataSign"] = [
        {"name": "contents", "type": "Order"}, {"name": "name", "type": "string"},
        {"name": "version", "type": "string"}, {"name": "chainId", "type": "uint256"},
        {"name": "verifyingContract", "type": "address"}, {"name": "salt", "type": "bytes32"},
    ]
    wallet = base["message"]["maker"]
    return {"types": types, "primaryType": "TypedDataSign", "domain": base["domain"],
            "message": {"contents": base["message"], "name": "DepositWallet", "version": "1",
                        "chainId": CHAIN_ID, "verifyingContract": wallet, "salt": ZERO32}}


def wrap_deposit_wallet_signature(typed_data: dict, inner_signature: bytes) -> bytes:
    try:
        from eth_abi import encode
        if not isinstance(inner_signature, (bytes, bytearray)) or len(inner_signature) != 65:
            raise ValueError
        order = typed_data["message"]["contents"]
        domain = typed_data["domain"]
        app_domain_separator = keccak(encode(
            ["bytes32", "bytes32", "bytes32", "uint256", "address"],
            [keccak(EIP712_DOMAIN_TYPE.encode()), keccak(str(domain["name"]).encode()),
             keccak(str(domain["version"]).encode()), uint(domain["chainId"]), address(domain["verifyingContract"])],
        ))
        values = [bytes.fromhex(str(order[name])[2:]) if kind == "bytes32" else order[name]
                  for name, kind in ORDER_FIELDS]
        contents_hash = keccak(encode(
            ["bytes32", *[kind for _, kind in ORDER_FIELDS]],
            [keccak(ORDER_TYPE.encode()), *values],
        ))
        type_bytes = ORDER_TYPE.encode()
        if len(type_bytes) > 65535:
            raise ValueError
        return bytes(inner_signature) + app_domain_separator + contents_hash + type_bytes + len(type_bytes).to_bytes(2, "big")
    except ExchangeError:
        raise
    except (ImportError, KeyError, TypeError, ValueError):
        raise ExchangeError("DEPOSIT_WALLET_SIGNATURE_WRAP_FAILED") from None


def wrap_session_signature(deposit_signature: bytes, session_signer: str) -> str:
    try:
        from eth_abi import encode
        signer_id = bytes(12) + bytes.fromhex(address(session_signer)[2:])
        payload = encode(["bytes32", "bytes32", "bytes"], [signer_id, bytes(32), bytes(deposit_signature)])
        return "0x" + (payload + SESSION_SIGNATURE_MAGIC).hex()
    except (ImportError, TypeError, ValueError):
        raise ExchangeError("SESSION_SIGNATURE_WRAP_FAILED") from None


def order_hash(order: dict, exchange: str, *, signature_type: int = 0) -> str:
    try:
        from eth_account.messages import encode_typed_data
        encoded = encode_typed_data(full_message=typed_order(order, exchange, signature_type=signature_type))
        return "0x" + keccak(b"\x19" + encoded.version + encoded.header + encoded.body).hex()
    except ExchangeError:
        raise
    except ImportError:
        raise ExchangeError("EXECUTION_DEPENDENCIES_MISSING") from None
    except Exception:
        raise ExchangeError("ORDER_HASH_FAILED") from None


class PublicMarketReader:
    """The identical credential-free market/fee preflight used by live signing.

    Constructing this reader cannot access an account, signer or authenticated
    API. It is also the implementation exercised by the public readiness probe.
    """
    def __init__(self, *, fee_policy: str, transport, chain, clock=time.time):
        self.fee_policy = validate_policy(fee_policy)
        self.transport, self.chain, self.clock = transport, chain, clock
        self.last_fee_diagnostics = None

    def _get(self, path: str, *, params=None, base=CLOB):
        status, value = self.transport.request("GET", base + path, params=params)
        if status != 200:
            raise ExchangeError("PUBLIC_READ_FAILED")
        return value

    def market_snapshot(self, token: str, condition: str) -> dict:
        started = self.clock()
        self.last_fee_diagnostics = None
        token, condition = _token(token), hash32(condition)
        gamma = self._get("/markets", params={"condition_ids": condition, "limit": 2}, base=GAMMA)
        if not isinstance(gamma, list) or len(gamma) != 1 or not isinstance(gamma[0], dict):
            raise ExchangeError("MARKET_IDENTITY_UNKNOWN")
        market = gamma[0]
        if hash32(market.get("conditionId")) != condition or token not in [str(uint(t)) for t in _array(market.get("clobTokenIds"))]:
            raise ExchangeError("MARKET_TOKEN_MISMATCH")
        if any(market.get(k) is not True for k in ("active", "acceptingOrders", "enableOrderBook")) or market.get("closed") is not False:
            raise ExchangeError("MARKET_NOT_TRADABLE")
        info = self._get("/clob-markets/" + condition)
        if isinstance(info, dict):
            self.last_fee_diagnostics = _fee_diagnostics(info, market, token=token,
                                                        condition=condition, observed_at=self.clock())
        book = self._get("/book", params={"token_id": token})
        if not isinstance(info, dict) or not isinstance(book, dict):
            raise ExchangeError("MARKET_CONTEXT_INVALID")
        tokens = info.get("t")
        if not isinstance(tokens, list) or token not in [str(uint(t.get("t"))) for t in tokens if isinstance(t, dict)]:
            raise ExchangeError("MARKET_TOKEN_MISMATCH")
        if str(uint(book.get("asset_id"))) != token or hash32(book.get("market")) != condition:
            raise ExchangeError("BOOK_IDENTITY_MISMATCH")
        neg_risk = book.get("neg_risk")
        if type(neg_risk) is not bool or ("nr" in info and info["nr"] is not neg_risk):
            raise ExchangeError("EXCHANGE_TYPE_UNKNOWN")
        tick, minimum = number(book.get("tick_size"), positive=True), number(book.get("min_order_size"), positive=True)
        if tick not in TICKS or tick != number(info.get("mts")) or minimum != number(info.get("mos")):
            raise ExchangeError("MARKET_CONSTRAINT_MISMATCH")
        stamp = number(book.get("timestamp")) / Decimal(1000)
        if not -2 <= Decimal(str(self.clock())) - stamp <= MAX_BOOK_AGE:
            raise ExchangeError("BOOK_STALE")
        levels = {}
        for side in ("bids", "asks"):
            raw = book.get(side)
            if not isinstance(raw, list) or len(raw) > 2000:
                raise ExchangeError("BOOK_DEPTH_INVALID")
            levels[side] = []
            for level in raw:
                if not isinstance(level, dict):
                    raise ExchangeError("BOOK_LEVEL_INVALID")
                p, q = number(level.get("price"), positive=True), number(level.get("size"), positive=True)
                if p >= 1 or p % tick:
                    raise ExchangeError("BOOK_LEVEL_INVALID")
                levels[side].append({"price": str(p), "size": str(q)})
        fee = info.get("fd")
        if not isinstance(fee, dict) or type(fee.get("to")) is not bool:
            raise ExchangeError("FEE_EVIDENCE_MISSING")
        rate, exponent = number(fee.get("r")), number(fee.get("e"))
        if rate > 1 or exponent > 4:
            raise ExchangeError("FEE_CURVE_UNSUPPORTED")
        exchange = NEG_RISK_EXCHANGE if neg_risk else STANDARD_EXCHANGE
        block = self.chain.block("latest")
        maximum_fee = self.chain.call_uint(exchange, "getMaxFeeRate()", [], [], block=hex(block["number"]))
        if not -2 <= Decimal(str(self.clock())) - stamp <= MAX_BOOK_AGE:
            raise ExchangeError("BOOK_STALE_DURING_PREFLIGHT")
        if not 0 <= self.clock() - started <= MAX_BOOK_AGE:
            raise ExchangeError("FEE_EVIDENCE_STALE_DURING_PREFLIGHT")
        # The worker uses the best level; complete depth remains available to
        # validate the signed limit immediately before preparing the wire.
        for side, price_key, size_key, reverse in (("asks", "ask", "ask_size", False), ("bids", "bid", "bid_size", True)):
            ordered = sorted(levels[side], key=lambda x: Decimal(x["price"]), reverse=reverse)
            best = ordered[0]["price"] if ordered else None
            levels[price_key] = best
            levels[size_key] = str(sum((Decimal(x["size"]) for x in ordered if x["price"] == best), Decimal(0)))
        observed = self.clock()
        evidence = make_fee_evidence(self.fee_policy, token=token, condition=condition, exchange=exchange,
            observed_at=observed, fd=fee, max_fee_bps=maximum_fee, max_fee_block=block,
            maker_base_fee_bps=info.get("mbf"), taker_base_fee_bps=info.get("tbf"))
        result = {"token": token, "condition": condition, "book": levels, "fee_rate": str(rate),
                "fee_exponent": str(exponent), "taker_only_fee": fee["to"], "tick_size": str(tick),
                "min_order_size": str(minimum), "neg_risk": neg_risk, "exchange": exchange,
                "minimum_buy_notional": str(minimum), "minimum_size_policy": MINIMUM_SIZE_POLICY,
                "max_fee_bps": maximum_fee, "received_at": observed,
                "fee_policy": self.fee_policy, "fee_evidence": evidence,
                "minimum_order_age_seconds": uint(info["oas"]) if "oas" in info else None,
                "book_timestamp": float(stamp), "market": market}
        fee_requirement(result, Decimal("0.5"), False, expected_policy=self.fee_policy)
        return result

class ExchangeEOA:
    wallet_type = "EOA"
    signature_type = 0
    order_visibility = "DEDICATED_EOA"

    def _init_authenticated(self, *, private_key, api_key: str, api_secret: str, api_passphrase: str,
                            wallet: str, signer: str, transport: JSONTransport | None = None,
                            chain: ChainReader | None = None, rpc_url: str | None = None, clock=time.time,
                            fee_policy: str | None = None):
        self.fee_policy = validate_policy(fee_policy)
        self.wallet, self.signer = address(wallet), address(signer)
        if not all(isinstance(x, str) and 0 < len(x) <= 512 for x in (api_key, api_secret, api_passphrase)):
            raise ExchangeError("CREDENTIAL_CONFIGURATION_INVALID")
        try:
            from eth_account import Account
            self._account = Account.from_key(private_key)
            self._secret = base64.b64decode(api_secret + "=" * (-len(api_secret) % 4), altchars=b"-_", validate=True)
        except ImportError:
            raise ExchangeError("EXECUTION_DEPENDENCIES_MISSING") from None
        except (ValueError, TypeError, binascii.Error):
            raise ExchangeError("CREDENTIAL_CONFIGURATION_INVALID") from None
        if not self._secret or address(self._account.address) != self.signer:
            raise ExchangeError("CREDENTIAL_ACCOUNT_MISMATCH")
        self._api_key, self._passphrase = api_key, api_passphrase
        self.transport, self.clock = transport or JSONTransport(), clock
        self.chain = chain or ChainReader(transport=self.transport, clock=clock,
                                         **({"rpc_url": rpc_url} if rpc_url is not None else {}))

    def __init__(self, *, private_key, api_key: str, api_secret: str, api_passphrase: str,
                 wallet: str, signer: str, transport: JSONTransport | None = None,
                 chain: ChainReader | None = None, rpc_url: str | None = None, clock=time.time,
                 fee_policy: str | None = None):
        self._init_authenticated(private_key=private_key, api_key=api_key, api_secret=api_secret,
            api_passphrase=api_passphrase, wallet=wallet, signer=signer, transport=transport,
            chain=chain, rpc_url=rpc_url, clock=clock, fee_policy=fee_policy)
        if self.wallet != self.signer:
            raise ExchangeError("ONLY_EXPLICIT_EOA_SUPPORTED")

    @classmethod
    def from_credentials_file(cls, path: Path, *, wallet: str, signer: str, **kwargs):
        """Read a private regular file without following a final-component symlink."""
        try:
            fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
            with os.fdopen(fd, "r") as stream:
                info = os.fstat(stream.fileno())
                if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077 or info.st_uid not in (os.getuid(), 0):
                    raise ExchangeError("CREDENTIAL_FILE_PERMISSIONS")
                content = stream.read(16_385)
            if len(content) > 16_384:
                raise ExchangeError("CREDENTIAL_FILE_TOO_LARGE")
            raw = json.loads(content)
            fields = {"private_key", "api_key", "api_secret", "api_passphrase"}
            if not isinstance(raw, dict) or set(raw) != fields:
                raise ExchangeError("CREDENTIAL_FILE_SCHEMA")
            return cls(**raw, wallet=wallet, signer=signer, **kwargs)
        except ExchangeError:
            raise
        except (OSError, ValueError, TypeError):
            raise ExchangeError("CREDENTIAL_FILE_READ_FAILED") from None

    def close(self):
        self.transport.close()

    def _headers(self, method: str, path: str, body: bytes | None = None) -> dict:
        stamp = str(int(self.clock()))
        message = (stamp + method + path).encode() + (body or b"")
        signature = base64.urlsafe_b64encode(hmac.new(self._secret, message, hashlib.sha256).digest()).decode()
        return {"POLY_ADDRESS": self.signer, "POLY_API_KEY": self._api_key,
                "POLY_PASSPHRASE": self._passphrase, "POLY_SIGNATURE": signature,
                "POLY_TIMESTAMP": stamp, "Content-Type": "application/json"}

    def _get(self, path: str, *, params=None, authenticated: bool = False, base=CLOB):
        status, value = self.transport.request("GET", base + path, params=params,
            headers=self._headers("GET", path) if authenticated else None)
        if status != 200:
            raise ExchangeError("AUTHENTICATED_READ_FAILED" if authenticated else "PUBLIC_READ_FAILED")
        return value

    def eligibility(self, *, require_opening: bool = True) -> dict:
        """Opening restrictions do not themselves revoke successful GET access.

        Read/cancel endpoints still enforce venue authentication and geography;
        this adapter never retries them through another host or identity.
        """
        restrictions, country, blocked = [], None, None
        try:
            status, geo = self.transport.request("GET", GEOBLOCK)
            if status != 200 or not isinstance(geo, dict) or type(geo.get("blocked")) is not bool or not isinstance(geo.get("country"), str) or not re.fullmatch(r"[A-Z]{2}", geo["country"]):
                restrictions.append("GEOGRAPHIC_ELIGIBILITY_UNKNOWN")
            else:
                country, blocked = geo["country"], geo["blocked"]
                if blocked:
                    restrictions.append("GEOGRAPHIC_TRADING_RESTRICTION")
        except ExchangeError:
            restrictions.append("GEOGRAPHIC_ELIGIBILITY_UNKNOWN")
        if restrictions and require_opening:
            raise ExchangeError(restrictions[0])
        keys = self._get("/auth/api-keys", authenticated=True)
        if not isinstance(keys, dict) or not isinstance(keys.get("apiKeys"), list) or self._api_key not in keys["apiKeys"]:
            raise ExchangeError("CREDENTIAL_ACCOUNT_MISMATCH")
        closed = self._get("/auth/ban-status/closed-only", authenticated=True)
        if not isinstance(closed, dict) or type(closed.get("closed_only")) is not bool:
            raise ExchangeError("ACCOUNT_ELIGIBILITY_UNKNOWN")
        if closed["closed_only"]:
            restrictions.append("ACCOUNT_CLOSE_ONLY")
        if restrictions and require_opening:
            raise ExchangeError(restrictions[0])
        return {"country": country, "closed_only": closed["closed_only"], "blocked": blocked,
                "openings_allowed": not restrictions, "opening_restrictions": restrictions}

    def balance_allowance(self, token: str | None = None) -> dict:
        params = {"asset_type": "COLLATERAL" if token is None else "CONDITIONAL", "signature_type": self.signature_type}
        if token is not None:
            params["token_id"] = _token(token)
        raw = self._get("/balance-allowance", params=params, authenticated=True)
        if not isinstance(raw, dict) or not isinstance(raw.get("allowances"), dict):
            raise ExchangeError("BALANCE_ALLOWANCE_INVALID")
        return {"balance": uint(raw.get("balance")),
                "allowances": {address(key): uint(value) for key, value in raw["allowances"].items()}}

    def _clob_pages(self, path: str, *, params: dict | None = None) -> list[dict]:
        params, items, seen, ids = dict(params or {}), [], set(), set()
        for _ in range(MAX_PAGES):
            raw = self._get(path, params=params, authenticated=True)
            if not isinstance(raw, dict) or not isinstance(raw.get("data"), list):
                raise ExchangeError("ACCOUNT_PAGE_INVALID")
            for item in raw["data"]:
                if not isinstance(item, dict) or not isinstance(item.get("id"), str) or not item["id"]:
                    raise ExchangeError("ACCOUNT_ITEM_INVALID")
                if item["id"] in ids:
                    raise ExchangeError("ACCOUNT_PAGINATION_DUPLICATE")
                ids.add(item["id"])
                items.append(item)
            if len(items) > MAX_ITEMS:
                raise ExchangeError("ACCOUNT_RECONCILIATION_CAP")
            cursor = raw.get("next_cursor")
            if cursor in ("LTE=", "", None):
                # Presence is mandatory: omitted continuation is not completion.
                if "next_cursor" not in raw:
                    raise ExchangeError("ACCOUNT_PAGINATION_INCOMPLETE")
                return items
            if not isinstance(cursor, str) or len(cursor) > 1024 or cursor in seen or not raw["data"]:
                raise ExchangeError("ACCOUNT_PAGINATION_INCOMPLETE")
            seen.add(cursor)
            params["next_cursor"] = cursor
        raise ExchangeError("ACCOUNT_RECONCILIATION_CAP")

    def _normalize_order(self, raw: object) -> dict:
        if not isinstance(raw, dict) or address(raw.get("maker_address")) != self.wallet or raw.get("owner") != self._api_key:
            raise ExchangeError("ORDER_ACCOUNT_MISMATCH")
        side, status = raw.get("side"), raw.get("status")
        if side not in ("BUY", "SELL") or status not in ("LIVE", "MATCHED", "CANCELED", "CANCELLED", "CANCELED_MARKET_RESOLVED", "INVALID", "EXPIRED", "DELAYED", "UNMATCHED"):
            raise ExchangeError("ORDER_STATUS_UNKNOWN")
        size, matched = micros(raw.get("original_size")), micros(raw.get("size_matched"))
        if size <= 0 or matched > size:
            raise ExchangeError("ORDER_QUANTITY_INVALID")
        price = number(raw.get("price"), positive=True)
        if price >= 1:
            raise ExchangeError("ORDER_PRICE_INVALID")
        return {"id": hash32(raw.get("id")), "token": _token(raw.get("asset_id")),
                "condition": hash32(raw.get("market")), "side": side,
                "status": "CANCELED" if status == "CANCELED_MARKET_RESOLVED" else status,
                "exchange_status": status,
                "matched": matched, "quantity": size, "price": str(price),
                "expires": _epoch(raw.get("expiration"), expiration=True), "created": _epoch(raw.get("created_at")),
                "trade_ids": [str(x) for x in _array(raw.get("associate_trades", []))]}

    def get_order(self, order_id: str) -> dict | None:
        order_id = hash32(order_id)
        path = "/data/order/" + order_id
        status, raw = self.transport.request("GET", CLOB + path, headers=self._headers("GET", path))
        if status == 404:
            return None  # Missing is UNKNOWN, never proof that POST was rejected.
        if status != 200:
            raise ExchangeError("ORDER_READ_FAILED")
        result = self._normalize_order(raw)
        if result["id"] != order_id:
            raise ExchangeError("ORDER_ID_MISMATCH")
        return result

    def open_orders(self) -> list[dict]:
        return [self._normalize_order(x) for x in self._clob_pages("/data/orders")]

    def account_trades(self, *, token: str | None = None, after: int | None = None,
                       before: int | None = None) -> list[dict]:
        """Complete bounded match-time window; bounds are not an indexer cursor.

        Official GET /data/trades accepts decimal Unix-second after/before.
        Callers overlap adjacent windows and re-audit history because a late
        update can retain an old match_time. No snapshot isolation is implied.
        """
        params = {"maker_address": self.wallet}
        if token is not None:
            params["asset_id"] = _token(token)
        if after is not None:
            after = uint(after)
            params["after"] = str(after)
        if before is not None:
            before = uint(before)
            params["before"] = str(before)
        if after is not None and before is not None and before <= after:
            raise ExchangeError("INVALID_TRADE_TIME_WINDOW")
        rows = self._clob_pages("/data/trades", params=params)
        result = []
        for raw in rows:
            status = raw.get("status", "")
            if isinstance(status, str) and status.startswith("TRADE_STATUS_"):
                status = status[len("TRADE_STATUS_"):]
            if status not in {"MATCHED", "MATCHED_NOT_BROADCASTED", "MINED", "CONFIRMED", "RETRYING", "FAILED"} or raw.get("trader_side") not in {"TAKER", "MAKER"}:
                raise ExchangeError("TRADE_STATUS_UNKNOWN")
            if not isinstance(raw.get("maker_orders"), list):
                raise ExchangeError("TRADE_MAKERS_INVALID")
            taker_id = hash32(raw.get("taker_order_id"))
            condition = hash32(raw.get("market"))
            token_id = _token(raw.get("asset_id"))
            trade_size = micros(raw.get("size"))
            if trade_size <= 0:
                raise ExchangeError("TRADE_QUANTITY_INVALID")
            number(raw.get("price"), positive=True)
            owned = []
            if raw["trader_side"] == "TAKER":
                if raw.get("owner") != self._api_key or address(raw.get("maker_address")) != self.wallet:
                    raise ExchangeError("TRADE_ACCOUNT_MISMATCH")
                owned.append({"id": taker_id, "token": token_id, "side": raw.get("side"),
                              "matched": trade_size})
            else:
                for maker in raw["maker_orders"]:
                    if not isinstance(maker, dict):
                        raise ExchangeError("TRADE_MAKERS_INVALID")
                    wallet = address(maker.get("maker_address"))
                    if wallet != self.wallet:
                        continue
                    if maker.get("owner") != self._api_key:
                        raise ExchangeError("TRADE_ACCOUNT_MISMATCH")
                    matched = micros(maker.get("matched_amount"))
                    maker_price = number(maker.get("price"), positive=True)
                    if matched <= 0 or maker_price >= 1:
                        raise ExchangeError("TRADE_MAKERS_INVALID")
                    owned.append({"id": hash32(maker.get("order_id")), "token": _token(maker.get("asset_id")),
                                  "side": maker.get("side"), "matched": matched})
            if (not owned or any(item["side"] not in {"BUY", "SELL"} for item in owned)
                or len({item["id"] for item in owned}) != len(owned)
                or {item["token"] for item in owned} != {token_id}
                or len({item["side"] for item in owned}) != 1):
                raise ExchangeError("TRADE_ACCOUNT_MISMATCH")
            transaction_hash = hash32(raw.get("transaction_hash")) if status == "CONFIRMED" else None
            matched_at, updated_at = _epoch(raw.get("match_time")), _epoch(raw.get("last_update"))
            # Allow equality: endpoint inclusivity is not an idempotency or
            # completeness guarantee. Adjacent caller windows must overlap.
            if (after is not None and matched_at < after) or (before is not None and matched_at > before):
                raise ExchangeError("TRADE_OUTSIDE_REQUESTED_WINDOW")
            result.append({"id": raw["id"], "condition": condition, "status": status,
                           "transaction_hash": transaction_hash, "token": token_id,
                           "side": owned[0]["side"], "wallet_quantity": sum(item["matched"] for item in owned),
                           "matched_at": matched_at, "updated_at": updated_at,
                           "owned_order_ids": [item["id"] for item in owned], "owned_orders": owned})
        return result

    def wallet_trade_activity(self, *, after: int | None = None, before: int | None = None) -> list[dict]:
        """Public wallet-wide confirmed TRADE witness for session isolation checks.

        This feed never creates fills or accounting entries. Deposit-session
        reconciliation uses it only to detect wallet trades that the active
        session's authenticated CLOB history cannot account for.
        """
        params = {"user": self.wallet, "type": "TRADE", "limit": 100,
                  "sort_direction": "ASC"}
        if after is not None:
            after = uint(after); params["start"] = str(after)
        else:
            params["start"] = "1"
        if before is not None:
            before = uint(before); params["end"] = str(before)
        if after is not None and before is not None and before < after:
            raise ExchangeError("INVALID_ACTIVITY_TIME_WINDOW")
        result, seen, previous = [], set(), None
        for _ in range(MAX_PAGES):
            raw = self._get("/v2/activity", params=params, base=DATA)
            if not isinstance(raw, dict) or not isinstance(raw.get("data"), list):
                raise ExchangeError("ACTIVITY_PAGE_INVALID")
            for row in raw["data"]:
                if (not isinstance(row, dict) or address(row.get("proxy_wallet")) != self.wallet
                    or row.get("type") != "TRADE"):
                    raise ExchangeError("ACTIVITY_ACCOUNT_MISMATCH")
                timestamp = _epoch(row.get("timestamp"))
                if ((after is not None and timestamp < after)
                    or (before is not None and timestamp > before)
                    or (previous is not None and timestamp < previous)):
                    raise ExchangeError("ACTIVITY_TIME_OR_ORDER_INVALID")
                previous = timestamp
                token = _token(row.get("token_id")); side = row.get("side")
                if side not in {"BUY", "SELL"}:
                    raise ExchangeError("ACTIVITY_TRADE_INVALID")
                quantity, price = micros(row.get("size")), number(row.get("price"), positive=True)
                if quantity <= 0 or price >= 1:
                    raise ExchangeError("ACTIVITY_TRADE_INVALID")
                result.append({"transaction_hash": hash32(row.get("transaction_hash")),
                    "condition": hash32(row.get("condition_id")), "token": token, "side": side,
                    "quantity": quantity, "price": str(price), "timestamp": timestamp})
            if len(result) > MAX_ITEMS:
                raise ExchangeError("ACTIVITY_CAP")
            page = raw.get("pagination")
            if not isinstance(page, dict) or type(page.get("has_more")) is not bool or "next_cursor" not in page:
                raise ExchangeError("ACTIVITY_PAGINATION_INCOMPLETE")
            cursor = page["next_cursor"]
            if page["has_more"] is False and cursor is None:
                return result
            if (not page["has_more"] or not isinstance(cursor, str) or not cursor or len(cursor) > 2048
                or cursor in seen or not raw["data"]):
                raise ExchangeError("ACTIVITY_PAGINATION_INCOMPLETE")
            seen.add(cursor); params["cursor"] = cursor
        raise ExchangeError("ACTIVITY_CAP")

    def positions(self) -> list[dict]:
        # Explicit OPEN includes resolved-but-unredeemed holdings. Remove the
        # documented default 0.1-share dust floor for account reconciliation.
        params, result, seen = {"user": self.wallet, "limit": 100, "status": "OPEN", "include_archived": "true",
                                "filter_type": "TOKENS", "filter_amount": 0}, [], set()
        for _ in range(MAX_PAGES):
            raw = self._get("/v2/positions", params=params, base=DATA)
            if not isinstance(raw, dict) or not isinstance(raw.get("data"), list):
                raise ExchangeError("POSITIONS_PAGE_INVALID")
            for row in raw["data"]:
                if not isinstance(row, dict) or address(row.get("proxy_wallet")) != self.wallet:
                    raise ExchangeError("POSITION_ACCOUNT_MISMATCH")
                balance = micros(row.get("current_size"))
                if not balance:
                    continue
                token = _token(row.get("token_id"))
                if any(p["token"] == token for p in result):
                    raise ExchangeError("POSITION_DUPLICATE")
                result.append({"token": token, "quantity": self.chain.token_balance(self.wallet, token),
                    "indexed_quantity": balance, "condition": hash32(row.get("condition_id"))})
            if len(result) > MAX_ITEMS:
                raise ExchangeError("POSITIONS_CAP")
            page = raw.get("pagination")
            if not isinstance(page, dict) or type(page.get("has_more")) is not bool or "next_cursor" not in page:
                raise ExchangeError("POSITIONS_PAGINATION_INCOMPLETE")
            cursor = page["next_cursor"]
            if page["has_more"] is False and cursor is None:
                return result
            if not page["has_more"] or not isinstance(cursor, str) or not cursor or len(cursor) > 2048 or cursor in seen or not raw["data"]:
                raise ExchangeError("POSITIONS_PAGINATION_INCOMPLETE")
            seen.add(cursor)
            params["cursor"] = cursor
        raise ExchangeError("POSITIONS_CAP")

    def token_balance(self, token: str) -> int:
        return self.chain.token_balance(self.wallet, _token(token))

    def account_snapshot(self, *, trade_after: int | None = None) -> dict:
        started = self.clock()
        eligibility = self.eligibility(require_opening=False)
        orders, trades, positions = self.open_orders(), self.account_trades(after=trade_after), self.positions()
        activity, activity_session_trades, activity_after, activity_before = [], [], None, None
        if self.wallet_type == "DEPOSIT_WALLET":
            activity_after = 1 if trade_after is None else max(1, uint(trade_after) - WALLET_ACTIVITY_LOOKBACK_SECONDS)
            # Use one common cutoff immediately before the session/public witness
            # comparison. Bounding at snapshot start would unnecessarily hide
            # wallet activity that occurred during earlier account enumeration.
            activity_before = int(self.clock())
            activity_session_trades = self.account_trades(after=activity_after, before=activity_before)
            activity = self.wallet_trade_activity(after=activity_after, before=activity_before)
        # Read spendable collateral last; long account enumerations must not
        # masquerade as fresh balance/risk evidence merely by stamping the end.
        api, chain = self.balance_allowance(), self.chain.collateral_state(self.wallet)
        if not 0 <= self.clock() - started <= MAX_ACCOUNT_SNAPSHOT_AGE:
            raise ExchangeError("ACCOUNT_SNAPSHOT_STALE")
        return {"wallet": self.wallet, "signer": self.signer, "wallet_type": self.wallet_type,
                "deposit_owner": getattr(self, "deposit_owner", None),
                "signature_type": self.signature_type, "order_visibility": self.order_visibility,
                "eligibility": eligibility, "openings_allowed": eligibility["openings_allowed"],
                "opening_restrictions": eligibility["opening_restrictions"],
                "balance": min(api["balance"], chain["balance"]),
                "allowances": {key: min(api["allowances"].get(key, 0), chain["allowances"].get(key, 0))
                               for key in (STANDARD_EXCHANGE, NEG_RISK_EXCHANGE)},
                "open_orders": orders, "trades": trades,
                "trade_after": trade_after,
                "trade_watermark": max((trade["matched_at"] for trade in trades), default=trade_after or 0),
                "wallet_activity": activity, "wallet_activity_session_trades": activity_session_trades,
                "wallet_activity_after": activity_after,
                "wallet_activity_before": activity_before,
                "positions": positions, "observed_at": self.clock(), "started_at": started}

    def market_snapshot(self, token: str, condition: str) -> dict:
        return PublicMarketReader(fee_policy=self.fee_policy, transport=self.transport,
                                  chain=self.chain, clock=self.clock).market_snapshot(token, condition)

    def _sign_order(self, order: dict, exchange: str) -> str:
        try:
            from eth_account.messages import encode_typed_data
            signed = self._account.sign_message(encode_typed_data(
                full_message=typed_order(order, exchange, signature_type=self.signature_type)))
            return "0x" + bytes(signed.signature).hex()
        except ExchangeError:
            raise
        except Exception:
            raise ExchangeError("ORDER_SIGNING_FAILED") from None

    def _order_hash(self, order: dict, exchange: str) -> str:
        return order_hash(order, exchange, signature_type=self.signature_type)

    def prepare_buy(self, *, token: str, condition: str, quantity, price,
                    order_type: str, fee_cap, expiration: int = 0,
                    valid_until: float | None = None, post_only: bool | None = None) -> dict:
        if order_type not in ("FOK", "FAK", "GTD"):
            raise ExchangeError("UNSUPPORTED_ORDER_TYPE")
        if post_only is not None and (type(post_only) is not bool or post_only != (order_type == "GTD")):
            raise ExchangeError("UNSUPPORTED_POST_ONLY_CONFIGURATION")
        expiration = uint(expiration)
        now = self.clock()
        if order_type == "GTD":
            if expiration < int(now) + 180:
                raise ExchangeError("GTD_EXPIRATION_TOO_SOON")
        elif expiration != 0:
            raise ExchangeError("IMMEDIATE_ORDER_EXPIRATION_MUST_BE_ZERO")
        snapshot = self.market_snapshot(token, condition)
        quantity, price, cap = number(quantity, positive=True), number(price, positive=True), number(fee_cap)
        tick = Decimal(snapshot["tick_size"])
        if not tick <= price <= 1 - tick or price % tick:
            raise ExchangeError("PRICE_OFF_TICK_GRID")
        if quantity % Decimal("0.01"):
            raise ExchangeError("ORDER_SIZE_INVALID")
        validate_buy_minimum(snapshot, quantity, price)
        bound = fee_requirement(snapshot, price, order_type == "GTD", expected_policy=self.fee_policy)
        if bound > cap:
            raise ExchangeError("EXCHANGE_FEE_BOUND_EXCEEDS_OPERATOR_CAP")
        asks = [(Decimal(x["price"]), Decimal(x["size"])) for x in snapshot["book"]["asks"]]
        if order_type == "GTD":
            if any(p <= price for p, _ in asks):
                raise ExchangeError("POST_ONLY_WOULD_CROSS")
        elif sum((q for p, q in asks if p <= price), Decimal(0)) < quantity:
            raise ExchangeError("EXECUTABLE_LIQUIDITY_INSUFFICIENT")
        created = self.clock()
        deadline = min(float(number(valid_until)) if valid_until is not None else created + MAX_PREPARED_AGE,
                       created + MAX_PREPARED_AGE)
        if deadline <= created:
            raise ExchangeError("THESIS_EXPIRED")
        order = {"salt": secrets.randbits(53), "maker": self.wallet, "signer": self.wallet,
                 "tokenId": snapshot["token"], "makerAmount": str(micros(quantity * price)),
                 "takerAmount": str(micros(quantity)), "side": "BUY", "signatureType": self.signature_type,
                 "timestamp": str(int(created * 1000)), "metadata": ZERO32, "builder": ZERO32,
                 "expiration": str(expiration)}
        order["signature"] = self._sign_order(order, snapshot["exchange"])
        payload = {"deferExec": False, "order": order, "orderType": order_type,
                   "owner": self._api_key, "postOnly": order_type == "GTD"}
        wire = canonical(payload)
        return {"order_id": self._order_hash(order, snapshot["exchange"]), "payload": payload,
                "wire": wire, "wire_hash": hashlib.sha256(wire.encode()).hexdigest(),
                "exchange": snapshot["exchange"], "condition": snapshot["condition"],
                "token": snapshot["token"], "quantity": str(quantity), "price": str(price),
                "fee_cap": str(cap), "fee_bound": str(bound), "prepared_at": created,
                "fee_policy": self.fee_policy, "fee_evidence": snapshot["fee_evidence"],
                "valid_until": deadline, "market": snapshot}

    def _submission_opening_restriction(self) -> str | None:
        return None

    def submit(self, prepared: dict, *, before_post=None) -> str:
        """One HTTP attempt. Engine must have durably armed this exact wire hash."""
        try:
            wire, payload = prepared["wire"], prepared["payload"]
            if not isinstance(wire, str) or canonical(payload) != wire or hashlib.sha256(wire.encode()).hexdigest() != prepared["wire_hash"]:
                raise ExchangeError("PREPARED_WIRE_MUTATED")
            if payload.get("owner") != self._api_key or address(payload["order"]["maker"]) != self.wallet or self._order_hash(payload["order"], prepared["exchange"]) != prepared["order_id"]:
                raise ExchangeError("PREPARED_IDENTITY_MISMATCH")
            if not prepared["prepared_at"] <= self.clock() < prepared["valid_until"] or self.clock() - prepared["prepared_at"] > MAX_PREPARED_AGE:
                raise ExchangeError("PREPARED_ORDER_EXPIRED")
            if prepared["fee_policy"] != self.fee_policy or prepared["fee_evidence"] != prepared["market"]["fee_evidence"]:
                raise ExchangeError("PREPARED_FEE_EVIDENCE_MUTATED")
            requirement = fee_requirement(prepared["market"], prepared["price"], payload["postOnly"],
                                          expected_policy=self.fee_policy)
            if requirement > number(prepared["fee_cap"]):
                raise ExchangeError("EXCHANGE_FEE_BOUND_EXCEEDS_OPERATOR_CAP")
            if not -2 <= self.clock() - prepared["market"]["book_timestamp"] <= MAX_BOOK_AGE:
                raise ExchangeError("BOOK_STALE_BEFORE_SUBMISSION")
            # A queued worker may run after the engine's last network read or
            # journal commit. Deposit authorization is checked in this worker,
            # then ALL local authority and quote deadlines are checked again.
            restriction = self._submission_opening_restriction()
            if restriction:
                raise ExchangeError(restriction)
            body = wire.encode()
            headers = self._headers("POST", "/order", body)
            if before_post is not None:
                before_post()
            now = self.clock()
            if not prepared["prepared_at"] <= now < prepared["valid_until"] or now - prepared["prepared_at"] > MAX_PREPARED_AGE:
                raise ExchangeError("PREPARED_ORDER_EXPIRED")
            if not -2 <= now - prepared["market"]["book_timestamp"] <= MAX_BOOK_AGE:
                raise ExchangeError("BOOK_STALE_BEFORE_SUBMISSION")
        except SubmissionNotAttempted:
            raise
        except ExchangeError as exc:
            raise SubmissionNotAttempted(exc.code) from None
        except (KeyError, TypeError, ValueError):
            raise SubmissionNotAttempted("PREPARED_ORDER_INVALID") from None
        except Exception:
            raise SubmissionNotAttempted("LOCAL_SUBMISSION_VALIDATION_UNAVAILABLE") from None
        # No await, network preflight, signing or journal lock lies between the
        # final local guard and this one transport invocation. Failure from this
        # point onward is UNKNOWN, including an exception before a response.
        try:
            status, result = self.transport.request("POST", CLOB + "/order", body=body,
                                                    headers=headers)
        except Exception:
            return "UNKNOWN"
        if status == 200 and isinstance(result, dict):
            accepted = result.get("success") is True and result.get("errorMsg") == "" and result.get("orderID") == prepared["order_id"] and result.get("status") in ("live", "matched", "delayed")
            if accepted:
                return "ACKNOWLEDGED"
            # An unexpected status or contradictory ID is never proof of reject.
            if result.get("success") is False and result.get("orderID") in ("", None) and result.get("status") in ("", "unmatched") and isinstance(result.get("errorMsg"), str) and result["errorMsg"]:
                return "REJECTED"
        # Auth / rate limit / server / invalid response paths retain UNKNOWN.
        return "UNKNOWN"

    def cancel_order(self, order_id: str) -> dict:
        order_id = hash32(order_id)
        body = canonical({"orderID": order_id}).encode()
        try:
            status, result = self.transport.request("DELETE", CLOB + "/order", body=body,
                                                    headers=self._headers("DELETE", "/order", body))
        except ExchangeError:
            return {"requested": True, "confirmed": False, "unknown": True}
        if status != 200 or not isinstance(result, dict) or not isinstance(result.get("canceled"), list) or not isinstance(result.get("not_canceled"), dict):
            return {"requested": True, "confirmed": False, "unknown": True}
        return {"requested": True, "confirmed": order_id in result["canceled"] and order_id not in result["not_canceled"],
                "unknown": False}

    def confirmed_fills(self, order_id: str, token: str, neg_risk: bool, *,
                        trades: list[dict] | None = None,
                        recorded_fills: list[dict] | None = None) -> list[dict]:
        order_id, token = hash32(order_id), _token(token)
        if type(neg_risk) is not bool:
            raise ExchangeError("EXCHANGE_TYPE_UNKNOWN")
        trades = self.account_trades(token=token) if trades is None else trades
        exchange = NEG_RISK_EXCHANGE if neg_risk else STANDARD_EXCHANGE
        transactions = set()
        recorded = {}
        if recorded_fills is not None:
            if not isinstance(recorded_fills, list) or len(recorded_fills) > MAX_ITEMS:
                raise ExchangeError("RECORDED_FILL_AUDIT_CAP")
            for item in recorded_fills:
                if not isinstance(item, dict) or item.get("order_id") != order_id or item.get("token") != token:
                    raise ExchangeError("RECORDED_FILL_IDENTITY_MISMATCH")
                transaction = hash32(item.get("transaction_hash"))
                index = uint(item.get("log_index"))
                identity = f"{CHAIN_ID}:{transaction}:{index}"
                if item.get("id") != identity or identity in recorded:
                    raise ExchangeError("RECORDED_FILL_IDENTITY_MISMATCH")
                hash32(item.get("block_hash"))
                for key in ("quantity", "cost", "fee"):
                    uint(item.get(key))
                recorded[identity] = item
                transactions.add(transaction)
        for trade in trades:
            ours = False
            owned = trade.get("owned_orders")
            if not isinstance(owned, list) or not owned:
                raise ExchangeError("TRADE_ACCOUNT_PROOF_MISSING")
            for item in owned:
                if not isinstance(item, dict):
                    raise ExchangeError("TRADE_ACCOUNT_PROOF_MISSING")
                if hash32(item.get("id")) == order_id:
                    if _token(item.get("token")) != token or item.get("side") != "BUY":
                        raise ExchangeError("FILL_ORDER_IDENTITY_MISMATCH")
                    ours = True
            if ours and trade.get("status") == "CONFIRMED":
                transactions.add(hash32(trade.get("transaction_hash")))
        result = {}
        for tx in sorted(transactions):
            fills = self.chain.fills(tx, order_id=order_id, wallet=self.wallet, token=token, exchange=exchange)
            if not fills:
                if any(item["transaction_hash"] == tx for item in recorded.values()):
                    raise ExchangeError("RECORDED_FILL_PROOF_MISSING")
                raise ExchangeError("CONFIRMED_TRADE_CHAIN_PROOF_PENDING")
            for fill in fills:
                if fill["id"] in result and result[fill["id"]] != fill:
                    raise ExchangeError("CONFIRMED_FILL_MUTATED")
                result[fill["id"]] = fill
        for identity, previous in recorded.items():
            if identity not in result:
                raise ExchangeError("RECORDED_FILL_PROOF_MISSING")
            if any(previous[key] != result[identity][key] for key in ("order_id", "token", "quantity", "cost", "fee", "transaction_hash", "block_hash", "log_index")):
                raise ExchangeError("RECORDED_FILL_PROOF_CHANGED")
        return list(result.values())

    def settlement(self, condition: str, outcome_index: int, *, token: str,
                   neg_risk: bool = False) -> dict | None:
        return self.chain.settlement(condition, outcome_index, token=_token(token), neg_risk=neg_risk)

    def redemption_receipt(self, transaction_hash: str, condition: str) -> list[dict]:
        return self.chain.redemption_receipt(transaction_hash, wallet=self.wallet, condition=condition)


class ExchangeDepositSession(ExchangeEOA):
    """Deposit Wallet adapter using a CLOB-only Session Key.

    The owner/Builder keys never enter this process. Authenticated CLOB reads are
    scoped to the configured Session Key, while wallet positions and on-chain
    balances are checked against the Deposit Wallet address.
    """
    wallet_type = "DEPOSIT_WALLET"
    signature_type = 3
    order_visibility = "SESSION_SIGNER_ONLY"

    def __init__(self, *, private_key, api_key: str, api_secret: str, api_passphrase: str,
                 wallet: str, signer: str, deposit_owner: str, session_scopes, session_valid_until,
                 session_exclusive_until, transport: JSONTransport | None = None,
                 chain: ChainReader | None = None, rpc_url: str | None = None, clock=time.time,
                 fee_policy: str | None = None):
        self._init_authenticated(private_key=private_key, api_key=api_key, api_secret=api_secret,
            api_passphrase=api_passphrase, wallet=wallet, signer=signer, transport=transport,
            chain=chain, rpc_url=rpc_url, clock=clock, fee_policy=fee_policy)
        self.deposit_owner = address(deposit_owner)
        if self.signer == self.deposit_owner or self.wallet in _deposit_wallet_owner_addresses(self.signer):
            raise ExchangeError("DEPOSIT_SESSION_OWNER_KEY_FORBIDDEN")
        if self.wallet not in _deposit_wallet_owner_addresses(self.deposit_owner):
            raise ExchangeError("DEPOSIT_WALLET_OWNER_BINDING_MISMATCH")
        if self.wallet == self.signer or self.wallet == self.deposit_owner or tuple(session_scopes or ()) != ("CLOB",):
            raise ExchangeError("DEPOSIT_SESSION_CONFIGURATION_INVALID")
        try:
            self.session_valid_until = float(session_valid_until)
            self.session_exclusive_until = float(session_exclusive_until)
        except (TypeError, ValueError):
            raise ExchangeError("DEPOSIT_SESSION_EXPIRY_INVALID") from None
        if (not math.isfinite(self.session_valid_until) or not math.isfinite(self.session_exclusive_until)
            or not self.session_valid_until > 0 or not self.session_exclusive_until > 0
            or self.session_exclusive_until > self.session_valid_until):
            raise ExchangeError("DEPOSIT_SESSION_EXPIRY_INVALID")
        self.session_scopes = ("CLOB",)

    def redemption_receipt(self, transaction_hash: str, condition: str) -> list[dict]:
        from .deposit_redemption import read_deposit_redemption
        return read_deposit_redemption(self.chain, transaction_hash, wallet=self.wallet,
                                       owner=self.deposit_owner, condition=condition)

    def _onchain_session_valid_until(self, *, block: dict | None = None) -> int:
        block = self.chain.block("latest") if block is None else block
        value = self.chain.call_uint(self.wallet, "sessionSignerAuthorizedUntil(address)",
                                     ["address"], [self.signer], block=hex(block["number"]))
        if not -30 <= self.clock() - block["timestamp"] <= 180:
            raise ExchangeError("STALE_CHAIN_BLOCK")
        return value

    def _session_authorization_restriction(self, onchain_valid_until: int, *, now=None) -> str | None:
        now = self.clock() if now is None else now
        if type(onchain_valid_until) is not int or onchain_valid_until <= 0:
            return "SESSION_AUTHORIZATION_REVOKED_OR_MISSING"
        if float(onchain_valid_until) < self.session_valid_until:
            return "SESSION_AUTHORIZATION_ONCHAIN_BEFORE_CONFIGURED_EXPIRY"
        if now >= onchain_valid_until - SESSION_OPENING_SAFETY_SECONDS:
            return "SESSION_AUTHORIZATION_EXPIRED_OR_NEAR_EXPIRY"
        if now >= self.session_valid_until - SESSION_OPENING_SAFETY_SECONDS:
            return "SESSION_AUTHORIZATION_EXPIRED_OR_NEAR_EXPIRY"
        if now >= self.session_exclusive_until - SESSION_OPENING_SAFETY_SECONDS:
            return "DEDICATED_SESSION_EXCLUSIVITY_EXPIRED_OR_NEAR_EXPIRY"
        return None

    def _attested_session_authorization(self) -> dict:
        from .wallet_attestation import attest_wallet
        forms = _deposit_wallet_owner_forms(self.deposit_owner)
        expected_proxy = next(name for name, wallet in forms.items() if wallet == self.wallet)
        return attest_wallet(self.chain, self.wallet, self.deposit_owner, self.signer,
                             expected_proxy_type=expected_proxy, clock=self.clock)

    def session_opening_restriction(self) -> str | None:
        try:
            evidence = self._attested_session_authorization()
        except ExchangeError as exc:
            return exc.code
        return self._session_authorization_restriction(evidence["onchain_valid_until"])

    def _submission_opening_restriction(self) -> str | None:
        return self.session_opening_restriction()

    def eligibility(self, *, require_opening: bool = True) -> dict:
        result = super().eligibility(require_opening=False)
        restrictions = list(result["opening_restrictions"])
        evidence, onchain_valid_until = None, None
        try:
            evidence = self._attested_session_authorization()
            onchain_valid_until = evidence["onchain_valid_until"]
            session_restriction = self._session_authorization_restriction(onchain_valid_until)
            if session_restriction:
                restrictions.append(session_restriction)
        except ExchangeError as exc:
            restrictions.append(exc.code)

        now = self.clock()
        if now >= self.session_valid_until - SESSION_OPENING_SAFETY_SECONDS:
            restrictions.append("SESSION_AUTHORIZATION_EXPIRED_OR_NEAR_EXPIRY")
        if now >= self.session_exclusive_until:
            restrictions.append("DEDICATED_SESSION_EXCLUSIVITY_EXPIRED")
        elif now >= self.session_exclusive_until - SESSION_OPENING_SAFETY_SECONDS:
            restrictions.append("DEDICATED_SESSION_EXCLUSIVITY_EXPIRED_OR_NEAR_EXPIRY")
        # Keep diagnostics deterministic when two independent checks report the same boundary.
        restrictions = list(dict.fromkeys(restrictions))
        if restrictions and require_opening:
            raise ExchangeError(restrictions[0])
        return dict(result, openings_allowed=not restrictions, opening_restrictions=restrictions,
                    session_scopes=list(self.session_scopes),
                    session_valid_until=self.session_valid_until,
                    session_onchain_valid_until=onchain_valid_until,
                    wallet_attestation=evidence,
                    session_exclusive_until=self.session_exclusive_until)

    def prepare_buy(self, *, token: str, condition: str, quantity, price,
                    order_type: str, fee_cap, expiration: int = 0,
                    valid_until: float | None = None, post_only: bool | None = None) -> dict:
        try:
            onchain_valid_until = self._onchain_session_valid_until()
        except ExchangeError:
            raise ExchangeError("SESSION_AUTHORIZATION_ONCHAIN_UNKNOWN") from None
        now = self.clock()
        restriction = self._session_authorization_restriction(onchain_valid_until, now=now)
        if restriction:
            raise ExchangeError(restriction)
        cutoff = min(self.session_valid_until, float(onchain_valid_until),
                     self.session_exclusive_until) - SESSION_OPENING_SAFETY_SECONDS
        if now >= cutoff:
            raise ExchangeError("SESSION_OPENING_WINDOW_CLOSED")
        if order_type == "GTD" and uint(expiration) > int(cutoff):
            raise ExchangeError("ORDER_OUTLIVES_SESSION_OPENING_WINDOW")
        # A wire prepared just before the safety boundary must not retain the
        # generic five-second submission lifetime after opening authority closes.
        bounded_valid_until = cutoff if valid_until is None else min(float(number(valid_until)), cutoff)
        return super().prepare_buy(token=token, condition=condition, quantity=quantity, price=price,
            order_type=order_type, fee_cap=fee_cap, expiration=expiration, valid_until=bounded_valid_until,
            post_only=post_only)

    def _sign_order(self, order: dict, exchange: str) -> str:
        try:
            from eth_account.messages import encode_typed_data
            typed = deposit_wallet_typed_order(order, exchange)
            inner = self._account.sign_message(encode_typed_data(full_message=typed))
            deposit = wrap_deposit_wallet_signature(typed, bytes(inner.signature))
            return wrap_session_signature(deposit, self.signer)
        except ExchangeError:
            raise
        except Exception:
            raise ExchangeError("ORDER_SIGNING_FAILED") from None
