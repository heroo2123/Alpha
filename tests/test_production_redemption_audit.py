"""Historical cash proofs are rechecked; no real account or transport is used."""
import asyncio
from copy import deepcopy
import time

import pytest

from polymarket_scanner.production.chain import PUSD
from polymarket_scanner.production.engine import ExecutionEngine
from polymarket_scanner.production.redemption_audit import RedemptionAuditor, REDEMPTION_AUDIT_TTL
from polymarket_scanner.production.signals import SignalReader
from test_production_owner_account import owner_engine, Weather


def imported(path):
    cfg, sig, ledger, ex, engine = owner_engine(path)
    asyncio.run(engine.reconcile()); assert asyncio.run(engine.execute(sig))
    ex.fill(); ex.resolution = {"payout":"1", "proof":{"source":"OFFLINE_FIXTURE"}}
    asyncio.run(engine.reconcile())
    token = sig["legs"][0]["token"]; quantity = ledger.expected_balance(token)
    burns = [{"token":token, "quantity":quantity}]
    record = {"id":"137:offline-redemption:1", "transaction_hash":"0x"+"77"*32,
        "log_index":1, "condition_id":sig["legs"][0]["condition"], "proceeds":quantity,
        "burns":burns, "proof":{"source":"DEPOSIT_ADAPTER_REDEMPTION_V1", "wallet":cfg.wallet,
        "asset":PUSD, "pusd_credit":True, "burns":burns, "block_hash":"0x"+"88"*32,
        "transaction_gas":{"asset":"POL", "payer":"0x"+"66"*20,
                           "amount_wei":1, "charged_to_wallet":False}}}
    ledger.record_redemption(record); ex.balances[token] = 0
    calls = []
    def receipt(tx, condition):
        calls.append(tx); return [deepcopy(record)]
    ex.redemption_receipt = receipt
    return cfg, sig, ledger, ex, engine, record, calls


@pytest.mark.parametrize("failure", ["missing", "error", "changed", "burn_changed"])
def test_reconciliation_no_longer_ignores_imported_receipt(tmp_path, failure):
    cfg, sig, ledger, ex, engine, record, calls = imported(tmp_path)
    def invalid(tx, condition):
        calls.append(tx)
        if failure == "error":
            raise RuntimeError("untrusted transport detail")
        if failure == "missing":
            return []
        bad = deepcopy(record)
        if failure == "changed": bad["proceeds"] += 1
        else: bad["proof"]["burns"][0]["quantity"] += 1
        return [bad]
    ex.redemption_receipt = invalid
    asyncio.run(engine.reconcile(allow_exchange_mutation=False))
    summary = ledger.summary()
    assert len(calls) == 1 and not engine.account_reconciled and not engine.authority()
    assert summary["verified_redemption_proceeds_by_asset_micros"] == {}
    assert summary["recorded_redemption_proceeds_by_asset_micros"] == {PUSD:record["proceeds"]}
    assert ledger.expected_balance(record["burns"][0]["token"]) == 0
    assert len(ex.posts) == 1 and ex.cancels == []


def test_startup_receipts_require_fresh_epoch_and_unchanged_recheck(tmp_path):
    cfg, sig, ledger, ex, engine, record, calls = imported(tmp_path)
    restarted = ExecutionEngine(cfg, ledger, SignalReader(cfg.signal_db), ex, Weather(sig))
    assert not restarted.redemption_auditor.ready()
    assert ledger.summary()["unverified_redemption_count"] == 1
    asyncio.run(restarted.reconcile(allow_exchange_mutation=False))
    assert restarted.authority() and len(calls) == 1
    assert ledger.summary()["verified_redemption_proceeds_by_asset_micros"] == {PUSD:record["proceeds"]}
    assert ledger.summary()["confirmed_fill_count"] == 1


def test_cash_verification_expires_without_another_tick(tmp_path, monkeypatch):
    cfg, sig, ledger, ex, engine, record, calls = imported(tmp_path)
    now = time.time()
    monkeypatch.setattr(time, "time", lambda: now+REDEMPTION_AUDIT_TTL+1)
    assert not engine.redemption_auditor.ready()
    assert ledger.summary()["verified_redemption_proceeds_by_asset_micros"] == {}
    assert ledger.summary()["recorded_redemption_proceeds_by_asset_micros"] == {PUSD:record["proceeds"]}


def test_missing_receipt_can_recover_without_erasing_history(tmp_path):
    cfg, sig, ledger, ex, engine, record, calls = imported(tmp_path)
    original = ex.redemption_receipt; ex.redemption_receipt = lambda *a: []
    asyncio.run(engine.reconcile(allow_exchange_mutation=False))
    assert not engine.authority() and ledger.state("fault") is None
    ex.redemption_receipt = original
    asyncio.run(engine.reconcile(allow_exchange_mutation=False))
    assert engine.authority() and ledger.summary()["confirmed_fill_count"] == 1


def test_startup_scan_is_bounded_and_failure_does_not_starve_other_receipts(tmp_path):
    from test_production_deposit_redemption import verify, holding_ledger
    from deposit_redemption_fixture import ReplayChain
    chain = ReplayChain("standard"); original, = verify(chain)
    ledger = holding_ledger(tmp_path, chain, original)
    records = {}
    for i in range(3):
        record = deepcopy(original); record["id"] = f"offline:{i}"
        record["transaction_hash"] = "0x" + f"{i+1:02x}"*32
        record["proceeds"] = 1
        record["burns"][0]["quantity"] = 1
        record["proof"]["burns"][0]["quantity"] = 1
        ledger.record_redemption(record); records[record["transaction_hash"]] = record
    class Reader:
        def __init__(self): self.seen = []
        def redemption_receipt(self, tx, condition):
            self.seen.append(tx)
            return [] if tx == next(iter(records)) else [records[tx]]
    reader = Reader(); auditor = RedemptionAuditor(ledger, reader)
    async def call(fn, *args): return fn(*args)
    assert not asyncio.run(auditor.check_batch(call))
    assert len(reader.seen) == 2
    asyncio.run(auditor.check_batch(call))
    assert len(reader.seen) == 4 and set(reader.seen) == set(records)
    assert not auditor.ready()
