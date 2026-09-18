"""Read-only verification of one owner-signed Deposit Wallet redemption.

Supported: one factory batch, one standard/negative collateral-adapter redeem,
no other calls. Exact calldata, reviewed code, CTF burns/payout, pUSD mint/wrap
and wallet execution events must agree. No signing, send, approve or retry API.
See docs/DEPOSIT_REDEMPTION_POLICY.md and captured public receipt fixtures.
"""
from __future__ import annotations

import re
import time

from .chain import (CHAIN_ID, CTF, PUSD, USDCE, ZERO32, ChainReader, ExchangeError,
                    abi, address, hash32, hexuint, keccak, uint)
from .wallet_attestation import (FACTORY, IMPLEMENTATION_SLOT, ZERO, abi_address,
                                 attest_wallet_identity)

STANDARD = "0xada100db00ca00073811820692005400218fce1f"
NEGATIVE = "0xada2005600dec949baf300f4c6120000bdb6eaab"
LEGACY = "0xd91e80cf2e7be2e162c6513ced06f1dd0da35296"
WCOL = "0x3a3bd7bb9528e159577f7c2e685cc81a765002e2"
PUSD_IMPLEMENTATION = "0xce84e053301a82937f90ee2c2c1889cab1db25de"
VAULT = "0xc417fd8e9661c0d2120b64a04bb3278c17e99db1"
NATIVE_GAS = "0x0000000000000000000000000000000000001010"
PROXY_TYPES = ["(address,uint256,uint256,(address,uint256,bytes)[])[]", "bytes[]"]
PROXY_SIGNATURE = "proxy((address,uint256,uint256,(address,uint256,bytes)[])[],bytes[])"
REDEEM_SIGNATURE = "redeemPositions(address,bytes32,bytes32,uint256[])"
REDEEM_TYPES = ["address", "bytes32", "bytes32", "uint256[]"]

CODE_HASHES = {
    STANDARD: "93b965351d01c1a128821ac79fc98a18105daefb46bda0d1e5b52306d713aa4f",
    NEGATIVE: "3b892c7c2f80e7af69f28faf72a51c2d793f6b79b96011bdf0a1996319fcbe5b",
    LEGACY: "10798bfdebdc3b8727171551b1287ee4c87b486045ed51a6ddc94e34f66560a1",
    WCOL: "99c62168488983e6ac023c62a6dca53acc7e8e902849fb72a9b08f29545dc474",
    PUSD: "aaa52c8cc8a0e3fd27ce756cc6b4e70c51423e9b597b11f32d3e49f8b1fc890d",
    PUSD_IMPLEMENTATION: "740b9ebbb47b33a28e47c999b330fe79f878b7e0f1f7e7e09b8a0928ef4e1cb0",
}


def require(value, code="DEPOSIT_REDEMPTION_PROOF_MISMATCH"):
    if not value:
        raise ExchangeError(code)


def raw_bytes(value, *, limit=131072):
    require(isinstance(value, str) and len(value) <= 2 + 2 * limit
            and re.fullmatch(r"0x(?:[0-9a-fA-F]{2})*", value) is not None,
            "DEPOSIT_REDEMPTION_ENCODING_INVALID")
    return bytes.fromhex(value[2:])


def decode_exact(types, data):
    from eth_abi import decode
    try:
        values = decode(types, data, strict=True)
        require(abi(types, values) == data, "DEPOSIT_REDEMPTION_ENCODING_INVALID")
        return values
    except Exception:
        raise ExchangeError("DEPOSIT_REDEMPTION_ENCODING_INVALID") from None


def owner_batch(outer, *, wallet, owner, condition, timestamp):
    from eth_account import Account
    from eth_account.messages import encode_typed_data
    require(outer[:4] == keccak(PROXY_SIGNATURE.encode())[:4], "UNSUPPORTED_DEPOSIT_REDEMPTION_ROUTE")
    batches, signatures = decode_exact(PROXY_TYPES, outer[4:])
    require(len(batches) == len(signatures) == 1, "UNSUPPORTED_DEPOSIT_REDEMPTION_BATCH")
    target_wallet, nonce, deadline, calls = batches[0]
    require(address(target_wallet) == wallet and deadline >= timestamp and len(calls) == 1)
    adapter, value, data = calls[0]
    require(adapter in (STANDARD, NEGATIVE) and value == 0
            and data[:4] == keccak(REDEEM_SIGNATURE.encode())[:4],
            "UNSUPPORTED_DEPOSIT_REDEMPTION_ROUTE")
    collateral, parent, key, sets = decode_exact(REDEEM_TYPES, data[4:])
    require(address(collateral) == PUSD and parent == bytes(32)
            and "0x" + key.hex() == condition and sets == (1, 2))
    require(len(signatures[0]) == 65, "DEPOSIT_REDEMPTION_REQUIRES_OWNER_SIGNATURE")
    fields = lambda rows: [{"name": n, "type": t} for n, t in rows]
    typed = {"types": {
        "EIP712Domain": fields((("name", "string"), ("version", "string"),
            ("chainId", "uint256"), ("verifyingContract", "address"))),
        "Batch": fields((("wallet", "address"), ("nonce", "uint256"),
            ("deadline", "uint256"), ("calls", "Call[]"))),
        "Call": fields((("target", "address"), ("value", "uint256"), ("data", "bytes")))},
        "primaryType": "Batch", "domain": {"name": "DepositWallet", "version": "1",
            "chainId": CHAIN_ID, "verifyingContract": wallet},
        "message": {"wallet": wallet, "nonce": nonce, "deadline": deadline,
            "calls": [{"target": adapter, "value": 0, "data": data}]}}
    try:
        recovered = Account.recover_message(encode_typed_data(full_message=typed),
                                            signature=signatures[0])
        require(address(recovered) == owner, "DEPOSIT_REDEMPTION_OWNER_SIGNATURE_MISMATCH")
    except Exception:
        raise ExchangeError("DEPOSIT_REDEMPTION_OWNER_SIGNATURE_MISMATCH") from None
    return adapter, nonce, data


def event(contract, signature, topics, types=(), values=()):
    return (contract, ("0x" + keccak(signature.encode()).hex(), *topics), abi(list(types), list(values)))


def topic_address(value):
    return "0x" + "00" * 12 + address(value)[2:]


def event_shape(log):
    require(isinstance(log, dict) and isinstance(log.get("topics"), list))
    require(1 <= len(log["topics"]) <= 4)
    return address(log.get("address")), tuple(hash32(x) for x in log["topics"]), raw_bytes(log.get("data"))


def expected_events(wallet, condition, adapter, nonce, data, tokens, amounts, payout):
    """Exact ordered transcript for the supported source-reviewed single call."""
    negative = adapter == NEGATIVE
    redeemer, collateral = (LEGACY, WCOL) if negative else (adapter, USDCE)
    key, ids = bytes.fromhex(condition[2:]), [int(x) for x in tokens]
    def transfer(asset, sender, receiver, value):
        return event(asset, "Transfer(address,address,uint256)",
                     [topic_address(sender), topic_address(receiver)], ["uint256"], [value])
    def batch(operator, sender, receiver):
        return event(CTF, "TransferBatch(address,address,address,uint256[],uint256[])",
            [topic_address(operator), topic_address(sender), topic_address(receiver)],
            ["uint256[]", "uint256[]"], [ids, amounts])
    result = [batch(adapter, wallet, adapter)]
    if negative:
        result.append(batch(LEGACY, adapter, LEGACY))
    for token, amount in zip(ids, amounts):
        if amount:
            result.append(event(CTF, "TransferSingle(address,address,address,uint256,uint256)",
                [topic_address(redeemer), topic_address(redeemer), topic_address(ZERO)],
                ["uint256", "uint256"], [token, amount]))
    if payout:
        result.append(transfer(collateral, CTF, redeemer, payout))
    result.append(event(CTF, "PayoutRedemption(address,address,bytes32,bytes32,uint256[],uint256)",
        [topic_address(redeemer), topic_address(collateral), ZERO32],
        ["bytes32", "uint256[]", "uint256"], [key, [1, 2], payout]))
    if negative:
        if payout:
            result += [transfer(WCOL, LEGACY, ZERO, payout), transfer(USDCE, WCOL, adapter, payout)]
        result.append(event(LEGACY, "PayoutRedemption(address,bytes32,uint256[],uint256)",
            [topic_address(adapter), condition], ["uint256[]", "uint256"], [amounts, payout]))
    result += [transfer(USDCE, adapter, PUSD, payout), transfer(PUSD, ZERO, wallet, payout),
               transfer(USDCE, PUSD, VAULT, payout)]
    result.append(event(PUSD, "Wrapped(address,address,address,uint256)",
        [topic_address(adapter), topic_address(USDCE), topic_address(wallet)], ["uint256"], [payout]))
    result.append(event(adapter, "PositionsRedeemed(address,bytes32,uint256[],uint256)",
        [topic_address(wallet), condition], ["uint256[]", "uint256"], [amounts, payout]))
    result.append(event(wallet, "Execution(address,uint256,bytes,bytes)",
        [topic_address(adapter)], ["uint256", "bytes", "bytes"], [0, data, b""]))
    result.append(event(wallet, "BatchExecuted(uint256)", ["0x" + nonce.to_bytes(32, "big").hex()]))
    return result


def adapter_evidence(chain, adapter, at):
    selected = [adapter, PUSD, PUSD_IMPLEMENTATION] + ([LEGACY, WCOL] if adapter == NEGATIVE else [])
    for target in selected:
        require(keccak(chain.code(target, block=at)).hex() == CODE_HASHES[target],
                "DEPOSIT_REDEMPTION_CODE_DRIFT")
    require(abi_address(chain.storage(PUSD, IMPLEMENTATION_SLOT, block=at)) == PUSD_IMPLEMENTATION,
            "DEPOSIT_REDEMPTION_COLLATERAL_IMPLEMENTATION_DRIFT")
    def bound(target, signature, expected):
        require(abi_address(chain.call(target, signature, [], [], block=at)) == expected,
                "DEPOSIT_REDEMPTION_CONTRACT_BINDING_MISMATCH")
    bound(adapter, "CONDITIONAL_TOKENS()", CTF)
    bound(adapter, "COLLATERAL_TOKEN()", PUSD)
    bound(adapter, "USDCE()", USDCE)
    bound(PUSD, "VAULT()", VAULT)
    bound(PUSD, "USDCE()", USDCE)
    if adapter == NEGATIVE:
        bound(adapter, "NEG_RISK_ADAPTER()", LEGACY)
        bound(adapter, "WRAPPED_COLLATERAL()", WCOL)
        bound(LEGACY, "ctf()", CTF)
        bound(LEGACY, "col()", USDCE)
        bound(LEGACY, "wcol()", WCOL)
        bound(WCOL, "underlying()", USDCE)
        bound(WCOL, "owner()", LEGACY)
    return dict(implementation=PUSD_IMPLEMENTATION, vault=VAULT,
                runtime_hashes={target: CODE_HASHES[target] for target in selected})


def _verify(chain, transaction_hash, wallet, owner, condition):
    from .exchange import _deposit_wallet_owner_forms
    wallet, owner, condition = address(wallet), address(owner), hash32(condition)
    txid = hash32(transaction_hash)
    receipt = chain.confirmed_receipt(txid)
    if receipt is None:
        return []
    tx = chain.rpc("eth_getTransactionByHash", [txid])
    require(isinstance(tx, dict) and hash32(tx.get("hash")) == txid
            and address(tx.get("to")) == FACTORY and hexuint(tx.get("value")) == 0,
            "UNSUPPORTED_DEPOSIT_REDEMPTION_TRANSACTION")
    require(hash32(tx.get("blockHash")) == hash32(receipt.get("blockHash"))
            and hexuint(tx.get("blockNumber")) == hexuint(receipt.get("blockNumber"))
            and address(receipt.get("from")) == address(tx.get("from"))
            and address(receipt.get("to")) == FACTORY,
            "DEPOSIT_REDEMPTION_TRANSACTION_BLOCK_MISMATCH")
    height = hexuint(receipt["blockNumber"])
    raw_block = chain.rpc("eth_getBlockByNumber", [hex(height), False])
    require(isinstance(raw_block, dict) and hash32(raw_block.get("hash")) == receipt["blockHash"]
            and hexuint(raw_block.get("number")) == height, "RECEIPT_NONCANONICAL")
    block = {"number": height, "hash": receipt["blockHash"],
             "timestamp": hexuint(raw_block.get("timestamp"))}
    adapter, nonce, data = owner_batch(raw_bytes(tx.get("input")), wallet=wallet,
        owner=owner, condition=condition, timestamp=block["timestamp"])
    forms = _deposit_wallet_owner_forms(owner)
    matching = [kind for kind, target in forms.items() if target == wallet]
    require(len(matching) == 1, "DEPOSIT_REDEMPTION_WALLET_DERIVATION_MISMATCH")
    identity = attest_wallet_identity(chain, wallet, owner, expected_proxy_type=matching[0], block=block)
    at = hex(height)
    contracts = adapter_evidence(chain, adapter, at)
    require(chain.call_uint(FACTORY, "isOperator(address)", ["address"],
                           [address(tx["from"])], block=at) == 1,
            "DEPOSIT_REDEMPTION_FACTORY_OPERATOR_UNVERIFIED")
    key = bytes.fromhex(condition[2:])
    collateral = WCOL if adapter == NEGATIVE else USDCE
    tokens = []
    for index_set in (1, 2):
        collection = chain.call(CTF, "getCollectionId(bytes32,bytes32,uint256)",
            ["bytes32", "bytes32", "uint256"], [bytes(32), key, index_set], block=at)
        require(len(collection) == 32, "INVALID_COLLECTION_ID")
        token = chain.call_uint(CTF, "getPositionId(address,bytes32)",
                               ["address", "bytes32"], [collateral, collection], block=at)
        require(type(token) is int and 0 < token < 2**256, "SETTLEMENT_TOKEN_MISMATCH")
        tokens.append(str(token))
    require(tokens[0] != tokens[1], "SETTLEMENT_TOKEN_MISMATCH")
    require(chain.call_uint(CTF, "getOutcomeSlotCount(bytes32)", ["bytes32"], [key], block=at) == 2,
            "UNSUPPORTED_CONDITION")
    denominator = chain.call_uint(CTF, "payoutDenominator(bytes32)", ["bytes32"], [key], block=at)
    numerators = [chain.call_uint(CTF, "payoutNumerators(bytes32,uint256)",
        ["bytes32", "uint256"], [key, i], block=at) for i in (0, 1)]
    require(denominator > 0 and sum(numerators) == denominator, "INVALID_PAYOUT_VECTOR")
    logs = receipt["logs"]
    indices = [hexuint(log.get("logIndex")) for log in logs]
    require(indices == sorted(set(indices)), "DEPOSIT_REDEMPTION_LOG_ORDER_INVALID")
    shapes = [event_shape(log) for log in logs]
    redeemed_topic = "0x" + keccak(b"PositionsRedeemed(address,bytes32,uint256[],uint256)").hex()
    matches = [(log, shape) for log, shape in zip(logs, shapes)
               if shape[0] == adapter and shape[1][0] == redeemed_topic]
    require(len(matches) == 1, "DEPOSIT_REDEMPTION_EVENT_MISSING_OR_AMBIGUOUS")
    redeemed_log, redeemed_shape = matches[0]
    amounts, payout = decode_exact(["uint256[]", "uint256"], redeemed_shape[2])
    require(len(amounts) == 2 and any(amounts), "DEPOSIT_REDEMPTION_BURN_PROOF_MISSING")
    expected_payout = sum(q * n // denominator for q, n in zip(amounts, numerators))
    require(payout == expected_payout, "DEPOSIT_REDEMPTION_PAYOUT_OR_RESIDUE_MISMATCH")
    expected = expected_events(wallet, condition, adapter, nonce, data, tokens, amounts, payout)
    require([shape for shape in shapes if shape[0] != NATIVE_GAS] == expected,
            "DEPOSIT_REDEMPTION_TRANSFER_TRANSCRIPT_MISMATCH")
    gas_used, gas_price = hexuint(receipt.get("gasUsed")), hexuint(receipt.get("effectiveGasPrice"))
    require(gas_used > 0, "REDEMPTION_GAS_PROOF_INVALID")
    burns = [{"token": token, "quantity": amount}
             for token, amount in zip(tokens, amounts) if amount]
    index = hexuint(redeemed_log["logIndex"])
    proof = {"source": "DEPOSIT_ADAPTER_REDEMPTION_V1", "asset": PUSD,
        "wallet": wallet, "owner": owner, "adapter": adapter, "block_hash": block["hash"],
        "block_number": height, "burns": burns, "index_sets": [1, 2], "pusd_credit": True,
        "wallet_identity": identity, "contracts": contracts,
        "payout_numerators": numerators, "payout_denominator": denominator,
        "transaction_gas": {"payer": address(tx["from"]), "asset": "POL",
            "amount_wei": uint(gas_used * gas_price), "scope": "TRANSACTION_TOTAL",
            "charged_to_wallet": False},
        "spendable_balance_inferred": False}
    # The factory operator pays transaction gas, not the Deposit Wallet. Do not
    # insert a fabricated wallet debit into execution_native_gas.
    require(address(tx["from"]) != wallet, "DEPOSIT_REDEMPTION_GAS_PAYER_INVALID")
    chain.confirm_receipt_block(receipt)  # LAST RPC, after code, token and payout reads.
    return [{"id": f"{CHAIN_ID}:{txid}:{index}", "transaction_hash": txid,
             "log_index": index, "condition_id": condition, "proceeds": uint(payout),
             "burns": burns, "proof": proof}]


def read_deposit_redemption(chain: ChainReader, transaction_hash: str, *, wallet: str,
                            owner: str, condition: str) -> list[dict]:
    started = time.monotonic()
    try:
        result = _verify(chain, transaction_hash, wallet, owner, condition)
        require(time.monotonic() - started <= 60, "DEPOSIT_REDEMPTION_VERIFICATION_TOO_SLOW")
        return result
    except ExchangeError:
        raise
    except Exception:
        raise ExchangeError("DEPOSIT_REDEMPTION_VERIFICATION_FAILED") from None
