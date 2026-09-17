"""Read-only Polygon evidence for the reviewed CTF Exchange V2 contracts.

Only canonical, finalized receipts produce fills. Amounts are integer micros.
This module never submits transactions, approves spenders, or redeems positions.
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation
import itertools
import json
import re
import time
from urllib.parse import urlsplit

import httpx

CHAIN_ID = 137
PUSD = "0xc011a7e12a19f7b1f670d46f03b03f3342e82dfb"
USDCE = "0x2791bca1f2de4661ed88a30c99a7a9449aa84174"
CTF = "0x4d97dcd97ec945f40cf65f87097ace5ea0476045"
STANDARD_EXCHANGE = "0xe111180000d2663c0091e4f400237545b87b996b"
NEG_RISK_EXCHANGE = "0xe2222d279d744050d28e00520010520000310f59"
ZERO32 = "0x" + "00" * 32
SCALE = Decimal(1_000_000)
MAX_RESPONSE_BYTES = 8 * 1024 * 1024


class ExchangeError(RuntimeError):
    """Controlled codes only; remote response bodies and secrets are excluded."""

    def __init__(self, code: str, *, uncertain: bool = False):
        super().__init__(code)
        self.code = code
        self.uncertain = uncertain


class SubmissionNotAttempted(ExchangeError):
    """The adapter has not invoked its order transport; no remote intent exists.

    Only the local pre-POST boundary may raise this type. Once request() starts,
    any failure is potentially ambiguous and must retain the reservation.
    """


def address(value: object) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"0x[0-9a-fA-F]{40}", value):
        raise ExchangeError("INVALID_ADDRESS")
    return value.lower()


def hash32(value: object) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"0x[0-9a-fA-F]{64}", value):
        raise ExchangeError("INVALID_HASH")
    return value.lower()


def number(value: object, *, positive: bool = False) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (str, int, float, Decimal)):
        raise ExchangeError("INVALID_NUMBER")
    try:
        result = Decimal(str(value))
    except InvalidOperation:
        raise ExchangeError("INVALID_NUMBER") from None
    if not result.is_finite() or result < 0 or (positive and result == 0):
        raise ExchangeError("INVALID_NUMBER")
    return result


def uint(value: object) -> int:
    if type(value) is int:
        result = value
    elif isinstance(value, str) and re.fullmatch(r"[0-9]+", value):
        result = int(value)
    else:
        raise ExchangeError("INVALID_UINT")
    if not 0 <= result < 2**256:
        raise ExchangeError("INVALID_UINT")
    return result


def hexuint(value: object) -> int:
    if not isinstance(value, str) or not re.fullmatch(r"0x[0-9a-fA-F]+", value):
        raise ExchangeError("INVALID_RPC_INTEGER")
    return uint(int(value, 16))


def micros(value: object) -> int:
    result = number(value) * SCALE
    if result != result.to_integral_value():
        raise ExchangeError("NONINTEGRAL_MICROS")
    return uint(int(result))


def keccak(value: bytes) -> bytes:
    try:
        from eth_utils import keccak as implementation
    except ImportError:
        raise ExchangeError("EXECUTION_DEPENDENCIES_MISSING") from None
    return implementation(value)


def abi(types: list[str], values: list) -> bytes:
    try:
        from eth_abi import encode
        return encode(types, values)
    except ImportError:
        raise ExchangeError("EXECUTION_DEPENDENCIES_MISSING") from None
    except Exception:
        raise ExchangeError("INVALID_ABI_ARGUMENTS") from None


def calldata(signature: str, types: list[str], values: list) -> str:
    return "0x" + (keccak(signature.encode())[:4] + abi(types, values)).hex()


class JSONTransport:
    """Bounded responses, no retries, redirects, proxies, or environment CAs."""

    def __init__(self, client: httpx.Client | None = None):
        self.owned = client is None
        self.client = client or httpx.Client(
            trust_env=False, follow_redirects=False,
            timeout=httpx.Timeout(15, connect=5, pool=2),
            limits=httpx.Limits(max_connections=8, max_keepalive_connections=4),
        )

    def request(self, method: str, url: str, *, params=None, headers=None,
                body: bytes | None = None) -> tuple[int, object]:
        try:
            with self.client.stream(method, url, params=params, headers=headers, content=body) as response:
                chunks, size = [], 0
                for chunk in response.iter_bytes():
                    size += len(chunk)
                    if size > MAX_RESPONSE_BYTES:
                        raise ExchangeError("RESPONSE_TOO_LARGE", uncertain=True)
                    chunks.append(chunk)
                try:
                    payload = json.loads(b"".join(chunks))
                except (ValueError, UnicodeError):
                    raise ExchangeError("RESPONSE_NOT_JSON", uncertain=True) from None
                return response.status_code, payload
        except ExchangeError:
            raise
        except Exception:
            raise ExchangeError("TRANSPORT_FAILURE", uncertain=True) from None

    def close(self):
        if self.owned:
            self.client.close()


class ChainReader:
    """An explicit trusted Polygon RPC. Missing finalized-block support fails closed."""

    METHODS = frozenset({"eth_chainId", "eth_getBlockByNumber", "eth_getBlockByHash",
                         "eth_call", "eth_getTransactionReceipt", "eth_getTransactionByHash",
                         "eth_getCode", "eth_getStorageAt"})

    def __init__(self, *, transport: JSONTransport | None = None,
                 rpc_url: str = "https://polygon.drpc.org", clock=time.time):
        parsed = urlsplit(rpc_url)
        if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ExchangeError("INVALID_RPC_URL")
        self.transport = transport or JSONTransport()
        self.rpc_url, self.clock = rpc_url, clock
        self.ids = itertools.count(1)

    def rpc(self, method: str, params: list):
        if method not in self.METHODS:
            raise ExchangeError("RPC_MUTATION_FORBIDDEN")
        identity = next(self.ids)
        body = json.dumps({"jsonrpc": "2.0", "id": identity, "method": method,
                           "params": params}, separators=(",", ":")).encode()
        status, response = self.transport.request("POST", self.rpc_url,
            headers={"Content-Type": "application/json"}, body=body)
        if status != 200 or not isinstance(response, dict) or response.get("jsonrpc") != "2.0" or response.get("id") != identity or "error" in response or "result" not in response:
            raise ExchangeError("RPC_READ_FAILED")
        return response["result"]

    def verify_chain(self):
        if hexuint(self.rpc("eth_chainId", [])) != CHAIN_ID:
            raise ExchangeError("RPC_CHAIN_MISMATCH")

    def block(self, tag: str = "finalized") -> dict:
        self.verify_chain()
        raw = self.rpc("eth_getBlockByNumber", [tag, False])
        if not isinstance(raw, dict):
            raise ExchangeError("CHAIN_BLOCK_UNAVAILABLE")
        result = {"number": hexuint(raw.get("number")), "hash": hash32(raw.get("hash")),
                  "timestamp": hexuint(raw.get("timestamp"))}
        if not -30 <= self.clock() - result["timestamp"] <= 180:
            raise ExchangeError("STALE_CHAIN_BLOCK")
        return result

    def confirm_block(self, block: dict):
        raw = self.rpc("eth_getBlockByNumber", [hex(block["number"]), False])
        if (not isinstance(raw, dict) or hexuint(raw.get("number")) != block["number"]
            or hash32(raw.get("hash")) != block["hash"]
            or hexuint(raw.get("timestamp")) != block["timestamp"]):
            raise ExchangeError("CHAIN_ATTESTATION_BLOCK_CHANGED")
        if not -30 <= self.clock() - block["timestamp"] <= 180:
            raise ExchangeError("STALE_CHAIN_BLOCK")

    def code(self, target: str, *, block: str) -> bytes:
        raw = self.rpc("eth_getCode", [address(target), block])
        if not isinstance(raw, str) or not re.fullmatch(r"0x(?:[0-9a-fA-F]{2})+", raw):
            raise ExchangeError("CHAIN_CODE_MISSING_OR_MALFORMED")
        return bytes.fromhex(raw[2:])

    def storage(self, target: str, slot: str, *, block: str) -> bytes:
        return bytes.fromhex(hash32(self.rpc("eth_getStorageAt",
            [address(target), hash32(slot), block]))[2:])

    def call(self, target: str, signature: str, types: list[str], values: list,
             *, block: str = "latest", sender: str | None = None) -> bytes:
        call = {"to": address(target), "data": calldata(signature, types, values)}
        if sender is not None:
            call["from"] = address(sender)
        raw = self.rpc("eth_call", [call, block])
        if not isinstance(raw, str) or not re.fullmatch(r"0x(?:[0-9a-fA-F]{2})*", raw):
            raise ExchangeError("INVALID_CHAIN_CALL_RESULT")
        return bytes.fromhex(raw[2:])

    def call_uint(self, *args, **kwargs) -> int:
        raw = self.call(*args, **kwargs)
        if len(raw) != 32:
            raise ExchangeError("INVALID_CHAIN_UINT_RESULT")
        return int.from_bytes(raw, "big")

    def collateral_state(self, wallet: str) -> dict:
        block = self.block("latest")
        at, wallet = hex(block["number"]), address(wallet)
        return {"balance": self.call_uint(PUSD, "balanceOf(address)", ["address"], [wallet], block=at),
                "allowances": {exchange: self.call_uint(PUSD, "allowance(address,address)",
                    ["address", "address"], [wallet, exchange], block=at)
                    for exchange in (STANDARD_EXCHANGE, NEG_RISK_EXCHANGE)}, "block": block}

    def token_balance(self, wallet: str, token: str) -> int:
        at = hex(self.block("latest")["number"])
        return self.call_uint(CTF, "balanceOf(address,uint256)",
                              ["address", "uint256"], [address(wallet), uint(token)], block=at)

    def max_fee_bps(self, exchange: str) -> int:
        exchange = address(exchange)
        if exchange not in (STANDARD_EXCHANGE, NEG_RISK_EXCHANGE):
            raise ExchangeError("UNSUPPORTED_EXCHANGE")
        block = self.block("latest")
        bound = self.call_uint(exchange, "getMaxFeeRate()", [], [], block=hex(block["number"]))
        # V2 explicitly treats zero as unlimited, not as a zero-fee venue.
        if not 0 < bound < 10_000:
            raise ExchangeError("UNBOUNDED_EXCHANGE_FEE")
        return bound

    def confirmed_receipt(self, transaction_hash: str) -> dict | None:
        transaction_hash = hash32(transaction_hash)
        final = self.block()
        receipt = self.rpc("eth_getTransactionReceipt", [transaction_hash])
        if receipt is None:
            return None
        if not isinstance(receipt, dict) or hash32(receipt.get("transactionHash")) != transaction_hash:
            raise ExchangeError("RECEIPT_IDENTITY_MISMATCH")
        if hexuint(receipt.get("status")) != 1:
            raise ExchangeError("CHAIN_TRANSACTION_FAILED")
        height = hexuint(receipt.get("blockNumber"))
        if height > final["number"]:
            return None
        block_hash = hash32(receipt.get("blockHash"))
        canonical = self.rpc("eth_getBlockByNumber", [hex(height), False])
        if not isinstance(canonical, dict) or hash32(canonical.get("hash")) != block_hash or hexuint(canonical.get("number")) != height:
            raise ExchangeError("RECEIPT_NONCANONICAL")
        logs = receipt.get("logs")
        if not isinstance(logs, list) or len(logs) > 10_000:
            raise ExchangeError("RECEIPT_LOGS_INVALID")
        seen = set()
        for log in logs:
            if not isinstance(log, dict) or log.get("removed") is True:
                raise ExchangeError("RECEIPT_LOGS_INVALID")
            if hash32(log.get("transactionHash")) != transaction_hash or hash32(log.get("blockHash")) != block_hash or hexuint(log.get("blockNumber")) != height:
                raise ExchangeError("RECEIPT_LOG_IDENTITY_MISMATCH")
            index = hexuint(log.get("logIndex"))
            if index in seen:
                raise ExchangeError("DUPLICATE_RECEIPT_LOG")
            seen.add(index)
        return receipt

    def fills(self, transaction_hash: str, *, order_id: str, wallet: str,
              token: str, exchange: str) -> list[dict]:
        receipt = self.confirmed_receipt(transaction_hash)
        if receipt is None:
            return []
        return decode_fills(receipt, order_id=order_id, wallet=wallet,
                            token=token, exchange=exchange)

    def settlement(self, condition: str, outcome_index: int, *, token: str,
                   neg_risk: bool = False) -> dict | None:
        condition = hash32(condition)
        if type(outcome_index) is not int or outcome_index not in (0, 1) or type(neg_risk) is not bool:
            raise ExchangeError("UNSUPPORTED_OUTCOME_INDEX")
        final = self.block()
        at, key = hex(final["number"]), bytes.fromhex(condition[2:])
        exchange = NEG_RISK_EXCHANGE if neg_risk else STANDARD_EXCHANGE
        raw_collateral = self.call(exchange, "getCtfCollateral()", [], [], block=at)
        if len(raw_collateral) != 32 or raw_collateral[:12] != bytes(12):
            raise ExchangeError("INVALID_CTF_COLLATERAL")
        collateral = address("0x" + raw_collateral[-20:].hex())
        collection = self.call(CTF, "getCollectionId(bytes32,bytes32,uint256)",
            ["bytes32", "bytes32", "uint256"], [bytes(32), key, 1 << outcome_index], block=at)
        if len(collection) != 32:
            raise ExchangeError("INVALID_COLLECTION_ID")
        derived = self.call_uint(CTF, "getPositionId(address,bytes32)",
                                 ["address", "bytes32"], [collateral, collection], block=at)
        if derived != uint(token):
            raise ExchangeError("SETTLEMENT_TOKEN_MISMATCH")
        count = self.call_uint(CTF, "getOutcomeSlotCount(bytes32)", ["bytes32"], [key], block=at)
        if count != 2:
            raise ExchangeError("UNSUPPORTED_CONDITION")
        denominator = self.call_uint(CTF, "payoutDenominator(bytes32)", ["bytes32"], [key], block=at)
        if denominator == 0:
            return None
        numerators = [self.call_uint(CTF, "payoutNumerators(bytes32,uint256)",
            ["bytes32", "uint256"], [key, index], block=at) for index in (0, 1)]
        if sum(numerators) != denominator:
            raise ExchangeError("INVALID_PAYOUT_VECTOR")
        return {"payout": str(Decimal(numerators[outcome_index]) / Decimal(denominator)),
                "proof": {"source": "CTF_FINALIZED_PAYOUT", "contract": CTF,
                          "condition": condition, "token": str(derived), "outcome_index": outcome_index,
                          "numerator": numerators[outcome_index], "denominator": denominator,
                          "block": final, "cash_received": False}}

    def redemption_receipt(self, transaction_hash: str, *, wallet: str,
                           condition: str) -> list[dict]:
        receipt = self.confirmed_receipt(transaction_hash)
        if receipt is None:
            return []
        wallet, condition = address(wallet), hash32(condition)
        transaction = self.rpc("eth_getTransactionByHash", [hash32(transaction_hash)])
        if not isinstance(transaction, dict) or hash32(transaction.get("hash")) != hash32(transaction_hash) or address(transaction.get("from")) != wallet or address(transaction.get("to")) != CTF:
            raise ExchangeError("UNSUPPORTED_REDEMPTION_TRANSACTION")
        if hash32(transaction.get("blockHash")) != hash32(receipt.get("blockHash")) or hexuint(transaction.get("blockNumber")) != hexuint(receipt.get("blockNumber")):
            raise ExchangeError("REDEMPTION_TRANSACTION_BLOCK_MISMATCH")
        raw = transaction.get("input")
        try:
            from eth_abi import decode
            data = bytes.fromhex(raw[2:])
            if data[:4] != keccak(b"redeemPositions(address,bytes32,bytes32,uint256[])")[:4]:
                raise ValueError
            collateral, parent, key, sets = decode(["address", "bytes32", "bytes32", "uint256[]"], data[4:], strict=True)
            if calldata("redeemPositions(address,bytes32,bytes32,uint256[])", ["address", "bytes32", "bytes32", "uint256[]"], [collateral, parent, key, sets]) != raw.lower():
                raise ValueError
            if address(collateral) != USDCE or parent != bytes(32) or "0x" + key.hex() != condition or not sets or len(sets) > 2 or len(set(sets)) != len(sets) or any(x not in (1, 2) for x in sets):
                raise ValueError
        except Exception:
            raise ExchangeError("UNSUPPORTED_REDEMPTION_TRANSACTION") from None
        tokens = {}
        at = receipt["blockNumber"]
        for index_set in sets:
            collection = self.call(CTF, "getCollectionId(bytes32,bytes32,uint256)", ["bytes32", "bytes32", "uint256"], [bytes(32), key, index_set], block=at)
            if len(collection) != 32:
                raise ExchangeError("INVALID_COLLECTION_ID")
            tokens[index_set] = str(self.call_uint(CTF, "getPositionId(address,bytes32)", ["address", "bytes32"], [USDCE, collection], block=at))
        result = decode_redemptions(receipt, wallet=wallet, condition=condition, tokens=tokens)
        if result:
            # Native gas belongs to this sender-paid transaction, not each burn,
            # payout leg, or exchange fill. Preserve denomination without FX.
            if address(receipt.get("from")) != wallet or address(receipt.get("to")) != CTF:
                raise ExchangeError("REDEMPTION_RECEIPT_PAYER_MISMATCH")
            gas_used, gas_price = hexuint(receipt.get("gasUsed")), hexuint(receipt.get("effectiveGasPrice"))
            if gas_used == 0:
                raise ExchangeError("REDEMPTION_GAS_PROOF_INVALID")
            gas = {"id": f"{CHAIN_ID}:{hash32(transaction_hash)}:gas", "asset": "POL",
                   "chain_id": CHAIN_ID, "amount_wei": uint(gas_used * gas_price),
                   "gas_used": gas_used, "effective_gas_price_wei": gas_price,
                   "payer": wallet, "transaction_hash": hash32(transaction_hash),
                   "scope": "TRANSACTION_TOTAL"}
            # Direct CTF redeemPositions emits one payout event. The decoder
            # rejects multiple events; its two possible burns share this charge.
            result[0]["proof"]["native_gas"] = gas
        return result


def _words(data: object, count: int) -> list[int]:
    if not isinstance(data, str) or not re.fullmatch(r"0x[0-9a-fA-F]{" + str(count * 64) + "}", data):
        raise ExchangeError("INVALID_EVENT_DATA")
    return [int(data[2 + i * 64:2 + (i + 1) * 64], 16) for i in range(count)]


def _topic_address(topic: object) -> str:
    raw = hash32(topic)
    if raw[2:26] != "0" * 24:
        raise ExchangeError("INVALID_EVENT_ADDRESS")
    return address("0x" + raw[-40:])


def decode_fills(receipt: dict, *, order_id: str, wallet: str, token: str,
                 exchange: str) -> list[dict]:
    """Our BUY fills only. Caller supplies a verified finalized receipt."""
    order_id, wallet, token, exchange = hash32(order_id), address(wallet), str(uint(token)), address(exchange)
    if exchange not in (STANDARD_EXCHANGE, NEG_RISK_EXCHANGE):
        raise ExchangeError("UNSUPPORTED_EXCHANGE")
    event = "0x" + keccak(b"OrderFilled(bytes32,address,address,uint8,uint256,uint256,uint256,uint256,bytes32,bytes32)").hex()
    fills = []
    for log in receipt["logs"]:
        topics = log.get("topics")
        if address(log.get("address")) != exchange or not isinstance(topics, list) or not topics or hash32(topics[0]) != event:
            continue
        if len(topics) != 4:
            raise ExchangeError("INVALID_FILL_TOPICS")
        if hash32(topics[1]) != order_id:
            continue
        if _topic_address(topics[2]) != wallet:
            raise ExchangeError("FILL_ACCOUNT_MISMATCH")
        _topic_address(topics[3])
        side, asset, cost, quantity, fee, builder, metadata = _words(log.get("data"), 7)
        if side != 0 or str(asset) != token or quantity <= 0 or cost <= 0 or builder != 0 or metadata != 0:
            raise ExchangeError("FILL_IDENTITY_MISMATCH")
        tx, block = hash32(receipt["transactionHash"]), hash32(receipt["blockHash"])
        index = hexuint(log["logIndex"])
        fills.append({"id": f"{CHAIN_ID}:{tx}:{index}", "order_id": order_id, "token": token,
                      "quantity": quantity, "cost": cost, "fee": fee,
                      "transaction_hash": tx, "block_hash": block, "log_index": index})
    return fills


def decode_redemptions(receipt: dict, *, wallet: str, condition: str, tokens: dict[int, str]) -> list[dict]:
    """Direct CTF redemption pays USDC.e, not a spendable pUSD balance credit.

    Adapter redemptions require a separate bound transfer proof and are excluded.
    """
    wallet, condition = address(wallet), hash32(condition)
    event = "0x" + keccak(b"PayoutRedemption(address,address,bytes32,bytes32,uint256[],uint256)").hex()
    burn_event = "0x" + keccak(b"TransferSingle(address,address,address,uint256,uint256)").hex()
    result = []
    for log in receipt["logs"]:
        topics = log.get("topics")
        if address(log.get("address")) != CTF or not isinstance(topics, list) or not topics or hash32(topics[0]) != event:
            continue
        if len(topics) != 4:
            raise ExchangeError("INVALID_REDEMPTION_TOPICS")
        if _topic_address(topics[1]) != wallet:
            continue
        if _topic_address(topics[2]) != USDCE or hash32(topics[3]) != ZERO32:
            raise ExchangeError("UNSUPPORTED_REDEMPTION_ASSET")
        try:
            from eth_abi import decode
            data = bytes.fromhex(log["data"][2:])
            found, sets, payout = decode(["bytes32", "uint256[]", "uint256"], data, strict=True)
            if abi(["bytes32", "uint256[]", "uint256"], [found, sets, payout]) != data:
                raise ValueError
        except Exception:
            raise ExchangeError("INVALID_REDEMPTION_DATA") from None
        if "0x" + found.hex() != condition:
            continue
        if not sets or len(sets) > 2 or len(set(sets)) != len(sets) or any(x not in (1, 2) for x in sets):
            raise ExchangeError("UNSUPPORTED_REDEMPTION_INDEX_SETS")
        index, tx = hexuint(log["logIndex"]), hash32(receipt["transactionHash"])
        if set(sets) != set(tokens) or result:
            raise ExchangeError("REDEMPTION_IDENTITY_MISMATCH")
        expected = {str(uint(token)) for token in tokens.values()}
        burnt = {}
        for transfer in receipt["logs"]:
            ts = transfer.get("topics")
            if address(transfer.get("address")) != CTF or not isinstance(ts, list) or not ts or hash32(ts[0]) != burn_event:
                continue
            if len(ts) != 4:
                raise ExchangeError("INVALID_BURN_TOPICS")
            if _topic_address(ts[2]) != wallet or _topic_address(ts[3]) != "0x" + "00" * 20:
                continue
            _topic_address(ts[1])
            token, quantity = _words(transfer.get("data"), 2)
            if str(token) not in expected:
                continue
            if not quantity or hexuint(transfer["logIndex"]) >= index or str(token) in burnt:
                raise ExchangeError("INVALID_REDEMPTION_BURN")
            burnt[str(token)] = quantity
        if not burnt:
            raise ExchangeError("REDEMPTION_BURN_PROOF_MISSING")
        result.append({"id": f"{CHAIN_ID}:{tx}:{index}", "transaction_hash": tx,
                       "log_index": index, "condition_id": condition, "proceeds": uint(payout),
                       "burns": [{"token": token, "quantity": quantity} for token, quantity in sorted(burnt.items())],
                       "proof": {"source": "CTF_PAYOUT_REDEMPTION", "asset": USDCE,
                                 "wallet": wallet, "block_hash": hash32(receipt["blockHash"]),
                                 "burns": [{"token": token, "quantity": quantity} for token, quantity in sorted(burnt.items())],
                                 "index_sets": list(sets), "pusd_credit": False}})
    return result
