"""Narrow production CLOB adapter: explicit EOA, BUY, CTF Exchange V2.

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
import os
from pathlib import Path
import re
import secrets
import stat
import time

from .chain import (CHAIN_ID, NEG_RISK_EXCHANGE, STANDARD_EXCHANGE, ZERO32,
                    ChainReader, ExchangeError, JSONTransport, address, hash32,
                    keccak, micros, number, uint)
from .fees import fee_requirement, make_fee_evidence, validate_policy

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
ORDER_FIELDS = (
    ("salt", "uint256"), ("maker", "address"), ("signer", "address"),
    ("tokenId", "uint256"), ("makerAmount", "uint256"), ("takerAmount", "uint256"),
    ("side", "uint8"), ("signatureType", "uint8"), ("timestamp", "uint256"),
    ("metadata", "bytes32"), ("builder", "bytes32"),
)


def canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


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


def typed_order(order: dict, exchange: str) -> dict:
    exchange = address(exchange)
    if exchange not in (STANDARD_EXCHANGE, NEG_RISK_EXCHANGE):
        raise ExchangeError("UNSUPPORTED_EXCHANGE")
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
    if message["side"] != 0 or message["signatureType"] != 0 or message["maker"] != message["signer"] or message["metadata"] != ZERO32 or message["builder"] != ZERO32:
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


def order_hash(order: dict, exchange: str) -> str:
    try:
        from eth_account.messages import encode_typed_data
        encoded = encode_typed_data(full_message=typed_order(order, exchange))
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

    def _get(self, path: str, *, params=None, base=CLOB):
        status, value = self.transport.request("GET", base + path, params=params)
        if status != 200:
            raise ExchangeError("PUBLIC_READ_FAILED")
        return value

    def market_snapshot(self, token: str, condition: str) -> dict:
        started = self.clock()
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
                "max_fee_bps": maximum_fee, "received_at": observed,
                "fee_policy": self.fee_policy, "fee_evidence": evidence,
                "book_timestamp": float(stamp), "market": market}
        fee_requirement(result, Decimal("0.5"), False, expected_policy=self.fee_policy)
        return result

class ExchangeEOA:
    def __init__(self, *, private_key, api_key: str, api_secret: str, api_passphrase: str,
                 wallet: str, signer: str, transport: JSONTransport | None = None,
                 chain: ChainReader | None = None, rpc_url: str | None = None, clock=time.time,
                 fee_policy: str | None = None):
        self.fee_policy = validate_policy(fee_policy)
        self.wallet, self.signer = address(wallet), address(signer)
        if self.wallet != self.signer:
            raise ExchangeError("ONLY_EXPLICIT_EOA_SUPPORTED")
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
        params = {"asset_type": "COLLATERAL" if token is None else "CONDITIONAL", "signature_type": 0}
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
            number(raw.get("size"), positive=True)
            number(raw.get("price"), positive=True)
            owned = []
            if raw["trader_side"] == "TAKER":
                if raw.get("owner") != self._api_key or address(raw.get("maker_address")) != self.wallet:
                    raise ExchangeError("TRADE_ACCOUNT_MISMATCH")
                owned.append({"id": taker_id, "token": token_id, "side": raw.get("side")})
            else:
                for maker in raw["maker_orders"]:
                    if not isinstance(maker, dict):
                        raise ExchangeError("TRADE_MAKERS_INVALID")
                    wallet = address(maker.get("maker_address"))
                    if wallet != self.wallet:
                        continue
                    if maker.get("owner") != self._api_key:
                        raise ExchangeError("TRADE_ACCOUNT_MISMATCH")
                    owned.append({"id": hash32(maker.get("order_id")), "token": _token(maker.get("asset_id")), "side": maker.get("side")})
            if not owned or any(item["side"] not in {"BUY", "SELL"} for item in owned) or len({item["id"] for item in owned}) != len(owned):
                raise ExchangeError("TRADE_ACCOUNT_MISMATCH")
            if status == "CONFIRMED":
                hash32(raw.get("transaction_hash"))
            matched_at, updated_at = _epoch(raw.get("match_time")), _epoch(raw.get("last_update"))
            # Allow equality: endpoint inclusivity is not an idempotency or
            # completeness guarantee. Adjacent caller windows must overlap.
            if (after is not None and matched_at < after) or (before is not None and matched_at > before):
                raise ExchangeError("TRADE_OUTSIDE_REQUESTED_WINDOW")
            result.append({"id": raw["id"], "condition": condition, "status": status,
                           "transaction_hash": raw.get("transaction_hash") if status == "CONFIRMED" else None,
                           "matched_at": matched_at, "updated_at": updated_at,
                           "owned_order_ids": [item["id"] for item in owned], "owned_orders": owned})
        return result

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
        # Read spendable collateral last; long account enumerations must not
        # masquerade as fresh balance/risk evidence merely by stamping the end.
        api, chain = self.balance_allowance(), self.chain.collateral_state(self.wallet)
        if not 0 <= self.clock() - started <= MAX_ACCOUNT_SNAPSHOT_AGE:
            raise ExchangeError("ACCOUNT_SNAPSHOT_STALE")
        return {"wallet": self.wallet, "signer": self.signer, "eligibility": eligibility,
                "openings_allowed": eligibility["openings_allowed"],
                "opening_restrictions": eligibility["opening_restrictions"],
                "balance": min(api["balance"], chain["balance"]),
                "allowances": {key: min(api["allowances"].get(key, 0), chain["allowances"].get(key, 0))
                               for key in (STANDARD_EXCHANGE, NEG_RISK_EXCHANGE)},
                "open_orders": orders, "trades": trades,
                "trade_after": trade_after,
                "trade_watermark": max((trade["matched_at"] for trade in trades), default=trade_after or 0),
                "positions": positions, "observed_at": self.clock(), "started_at": started}

    def market_snapshot(self, token: str, condition: str) -> dict:
        return PublicMarketReader(fee_policy=self.fee_policy, transport=self.transport,
                                  chain=self.chain, clock=self.clock).market_snapshot(token, condition)

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
        if quantity < Decimal(snapshot["min_order_size"]) or quantity % Decimal("0.01"):
            raise ExchangeError("ORDER_SIZE_INVALID")
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
        order = {"salt": secrets.randbits(53), "maker": self.wallet, "signer": self.signer,
                 "tokenId": snapshot["token"], "makerAmount": str(micros(quantity * price)),
                 "takerAmount": str(micros(quantity)), "side": "BUY", "signatureType": 0,
                 "timestamp": str(int(created * 1000)), "metadata": ZERO32, "builder": ZERO32,
                 "expiration": str(expiration)}
        try:
            from eth_account.messages import encode_typed_data
            signed = self._account.sign_message(encode_typed_data(full_message=typed_order(order, snapshot["exchange"])))
            order["signature"] = "0x" + bytes(signed.signature).hex()
        except Exception:
            raise ExchangeError("ORDER_SIGNING_FAILED") from None
        payload = {"deferExec": False, "order": order, "orderType": order_type,
                   "owner": self._api_key, "postOnly": order_type == "GTD"}
        wire = canonical(payload)
        return {"order_id": order_hash(order, snapshot["exchange"]), "payload": payload,
                "wire": wire, "wire_hash": hashlib.sha256(wire.encode()).hexdigest(),
                "exchange": snapshot["exchange"], "condition": snapshot["condition"],
                "token": snapshot["token"], "quantity": str(quantity), "price": str(price),
                "fee_cap": str(cap), "fee_bound": str(bound), "prepared_at": created,
                "fee_policy": self.fee_policy, "fee_evidence": snapshot["fee_evidence"],
                "valid_until": deadline, "market": snapshot}

    def submit(self, prepared: dict) -> str:
        """One HTTP attempt. Engine must have durably armed this exact wire hash."""
        try:
            wire, payload = prepared["wire"], prepared["payload"]
            if not isinstance(wire, str) or canonical(payload) != wire or hashlib.sha256(wire.encode()).hexdigest() != prepared["wire_hash"]:
                raise ExchangeError("PREPARED_WIRE_MUTATED")
            if payload.get("owner") != self._api_key or address(payload["order"]["maker"]) != self.wallet or order_hash(payload["order"], prepared["exchange"]) != prepared["order_id"]:
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
        except (KeyError, TypeError, ValueError):
            raise ExchangeError("PREPARED_ORDER_INVALID") from None
        # The engine performs independent weather validation, reserves capital,
        # and checks activation before this call. No hidden network preflight can
        # silently age that authority after its durable submission boundary.
        body = wire.encode()
        try:
            status, result = self.transport.request("POST", CLOB + "/order", body=body,
                                                    headers=self._headers("POST", "/order", body))
        except ExchangeError:
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
