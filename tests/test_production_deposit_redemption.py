"""Golden public receipts plus adversarial mutations; all RPCs are offline."""
from copy import deepcopy

import pytest

from polymarket_scanner.production import deposit_redemption as redemption
from polymarket_scanner.production.chain import ExchangeError, abi
from deposit_redemption_fixture import ReplayChain


def verify(chain, **changes):
    args = dict(wallet=chain.wallet, owner=chain.owner, condition=chain.condition)
    args.update(changes)
    return redemption.read_deposit_redemption(chain, chain.sample["transaction"]["hash"], **args)


@pytest.mark.parametrize("route,proceeds", [("standard", 12245118), ("negative", 4330704)])
def test_public_golden_owner_redemptions(route, proceeds):
    chain = ReplayChain(route)
    result = verify(chain)
    assert len(result) == 1 and result[0]["proceeds"] == proceeds
    assert result[0]["proof"]["asset"] == redemption.PUSD
    assert result[0]["proof"]["spendable_balance_inferred"] is False
    assert "native_gas" not in result[0]["proof"]
    assert result[0]["proof"]["transaction_gas"]["charged_to_wallet"] is False
    assert chain.calls[-1] == ("eth_getBlockByNumber", [chain.at, False])
    assert not any("sessionSignerAuthorizedUntil" in str(call) for call in chain.calls)


@pytest.mark.parametrize("route,position", [(r, i) for r, n in (("standard", 11), ("negative", 15)) for i in range(n)])
def test_each_required_log_cannot_be_removed(route, position):
    chain = ReplayChain(route)
    chain.sample["receipt"]["logs"].pop(position)
    with pytest.raises(ExchangeError):
        verify(chain)


@pytest.mark.parametrize("route,position", [(r, i) for r, n in (("standard", 11), ("negative", 15)) for i in range(n)])
def test_each_required_log_cannot_be_tampered(route, position):
    chain = ReplayChain(route)
    row = chain.sample["receipt"]["logs"][position]
    if row["data"] != "0x":
        row["data"] = row["data"][:-2] + ("01" if row["data"][-2:] != "01" else "02")
    else:
        row["topics"][-1] = "0x" + "ff" * 32
    with pytest.raises(ExchangeError):
        verify(chain)


@pytest.mark.parametrize("route", ["standard", "negative"])
def test_late_reorg_after_token_reads_rejected(route):
    chain = ReplayChain(route)
    chain.reorg_at_end = True
    with pytest.raises(ExchangeError, match="RECEIPT_BLOCK_CHANGED_DURING_VERIFICATION"):
        verify(chain)


@pytest.mark.parametrize("field", ["wallet", "owner", "condition"])
def test_wrong_account_or_condition_rejected(field):
    chain = ReplayChain("standard")
    with pytest.raises(ExchangeError):
        verify(chain, **{field: "0x" + "44" * (32 if field == "condition" else 20)})


@pytest.mark.parametrize("target", list(redemption.CODE_HASHES))
def test_runtime_drift_rejected(target):
    chain = ReplayChain("negative" if target in (redemption.NEGATIVE, redemption.LEGACY, redemption.WCOL) else "standard")
    chain.codes[target] += b"\x00"
    with pytest.raises(ExchangeError, match="DEPOSIT_REDEMPTION_CODE_DRIFT"):
        verify(chain)


def test_changed_collateral_implementation_rejected():
    chain = ReplayChain("standard")
    chain.slots[redemption.PUSD, redemption.IMPLEMENTATION_SLOT] = redemption.STANDARD
    with pytest.raises(ExchangeError, match="COLLATERAL_IMPLEMENTATION_DRIFT"):
        verify(chain)


def test_noncanonical_owner_batch_trailing_bytes_rejected():
    chain = ReplayChain("standard")
    chain.sample["transaction"]["input"] += "00"
    with pytest.raises(ExchangeError, match="ENCODING_INVALID"):
        verify(chain)


@pytest.mark.parametrize("route", ["standard", "negative"])
def test_losing_position_zero_payout_still_records_real_burn(route):
    chain = ReplayChain(route)
    chain.numerators = (0, 1) if route == "standard" else (1, 0)
    removed = {501} if route == "standard" else {462, 464, 465}
    rows = [row for row in chain.sample["receipt"]["logs"] if int(row["logIndex"], 16) not in removed]
    transfer = "0x" + redemption.keccak(b"Transfer(address,address,uint256)").hex()
    wrapped = "0x" + redemption.keccak(b"Wrapped(address,address,address,uint256)").hex()
    ctf_payout = "0x" + redemption.keccak(b"PayoutRedemption(address,address,bytes32,bytes32,uint256[],uint256)").hex()
    adapter_payout = "0x" + redemption.keccak(b"PositionsRedeemed(address,bytes32,uint256[],uint256)").hex()
    legacy_payout = "0x" + redemption.keccak(b"PayoutRedemption(address,bytes32,uint256[],uint256)").hex()
    from eth_abi import decode
    for row in rows:
        topic = row["topics"][0]
        if topic in (transfer, wrapped):
            row["data"] = "0x" + bytes(32).hex()
        elif topic == ctf_payout:
            key, sets, _ = decode(["bytes32", "uint256[]", "uint256"], bytes.fromhex(row["data"][2:]))
            row["data"] = "0x" + abi(["bytes32", "uint256[]", "uint256"], [key, sets, 0]).hex()
        elif topic in (adapter_payout, legacy_payout):
            amounts, _ = decode(["uint256[]", "uint256"], bytes.fromhex(row["data"][2:]))
            row["data"] = "0x" + abi(["uint256[]", "uint256"], [amounts, 0]).hex()
    chain.sample["receipt"]["logs"] = rows
    result = verify(chain)
    assert result[0]["proceeds"] == 0 and result[0]["burns"][0]["quantity"] > 0


def holding_ledger(tmp_path, chain, record):
    import time
    from decimal import Decimal
    from polymarket_scanner.production.ledger import ExecutionLedger
    from test_production_lifecycle import config
    cfg = config(tmp_path)
    ledger = ExecutionLedger(cfg.execution_db, chain.wallet)
    from test_production_deposit_session import SESSION
    ledger.bind_adapter_identity(wallet_type="DEPOSIT_WALLET", signer=SESSION,
                                 signature_type=3, deposit_owner=chain.owner)
    burn = record["burns"][0]
    plan = {"id": "receipt-fixture", "signal_id": "receipt-fixture", "strategy": "DIRECTIONAL",
        "station_day": "KSEA/2026-09-17", "expires": time.time() + 120,
        "legs": [{"token": burn["token"], "condition": chain.condition, "price": "0.1",
                  "quantity": str(Decimal(burn["quantity"]) / 1000000), "fee_cap": "0.01"}]}
    assert ledger.reserve(plan, cfg.risk, 100000000)
    ledger.begin_submission(plan["id"], 0, "fixture-order", "fixture-wire", {"order": {"expiration": "0"}})
    ledger.submission_result("fixture-order", "ACKNOWLEDGED")
    ledger.record_fill({"id": "fixture-fill", "order_id": "fixture-order", "token": burn["token"],
        "quantity": burn["quantity"], "cost": burn["quantity"] // 10, "fee": 0,
        "transaction_hash": "0x" + "11" * 32, "block_hash": "0x" + "22" * 32, "log_index": 0})
    return ledger


@pytest.mark.parametrize("route", ["standard", "negative"])
def test_public_receipt_to_ledger_burns_once_and_keeps_asset_and_gas_scope(tmp_path, route):
    chain = ReplayChain(route)
    record, = verify(chain)
    ledger = holding_ledger(tmp_path, chain, record)
    assert ledger.record_redemption(record)
    assert not ledger.record_redemption(record)
    assert ledger.expected_balance(record["burns"][0]["token"]) == 0
    summary = ledger.summary()
    assert summary["confirmed_fill_count"] == 1
    assert summary["verified_redemption_proceeds_by_asset_micros"] == {redemption.PUSD: record["proceeds"]}
    assert summary["verified_native_gas_wei"] == {}
    assert summary["redemption_transactions_without_gas_proof"] == 0


@pytest.mark.parametrize("mutation", ["wallet", "condition", "quantity", "payer", "duplicate"])
def test_wrong_ledger_attribution_is_atomic(tmp_path, mutation):
    from polymarket_scanner.production.ledger import LedgerError
    chain = ReplayChain("standard")
    record, = verify(chain)
    ledger = holding_ledger(tmp_path, chain, record)
    bad = deepcopy(record)
    if mutation == "wallet":
        bad["proof"]["wallet"] = "0x" + "44" * 20
    elif mutation == "condition":
        bad["condition_id"] = "0x" + "44" * 32
    elif mutation == "payer":
        bad["proof"]["transaction_gas"]["payer"] = chain.wallet
    elif mutation == "duplicate":
        bad["burns"].append(deepcopy(bad["burns"][0]))
    else:
        bad["burns"][0]["quantity"] += 1
    with pytest.raises(LedgerError):
        ledger.record_redemption(bad)
    assert ledger.expected_balance(record["burns"][0]["token"]) == record["burns"][0]["quantity"]
    assert ledger.summary()["verified_redemption_proceeds_by_asset_micros"] == {}
