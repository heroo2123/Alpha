"""Real adapter + engine + SQLite; every HTTP/chain effect is an isolated fixture."""
import asyncio
from dataclasses import replace
from decimal import Decimal
import json
import time

import pytest

from polymarket_scanner.production.chain import decode_fills
from polymarket_scanner.production.engine import ExecutionEngine
from polymarket_scanner.production.ledger import ExecutionLedger
from polymarket_scanner.production.signals import SignalStore, SignalReader
from test_production_lifecycle import config, candidate, Weather
from test_production_exchange import (Chain, Wire, client, context, raw_order, trade, page, receipt, fill_log,
    WALLET, TOKEN, CONDITION, CLOB, GAMMA, DATA, GEOBLOCK, API_KEY, STANDARD_EXCHANGE, NEG_RISK_EXCHANGE, NOW, order_hash, ExchangeError)


class ContractChain(Chain):
    def __init__(self):
        self.order = None

    def collateral_state(self, wallet):
        value = super().collateral_state(wallet)
        if self.order:
            value["balance"] -= int(self.order["makerAmount"]) + 12345
        return value

    def token_balance(self, wallet, token):
        assert wallet == WALLET and token == TOKEN
        return int(self.order["takerAmount"]) if self.order else 0

    def fills(self, transaction, *, order_id, wallet, token, exchange):
        log = fill_log(quantity=int(self.order["takerAmount"]), cost=int(self.order["makerAmount"]))
        log["topics"][1] = order_id
        return decode_fills(receipt([log]), order_id=order_id, wallet=wallet, token=token, exchange=exchange)

    def settlement(self, *args, **kwargs):
        return None


class ContractWire(Wire):
    def __init__(self, chain, rejected):
        super().__init__()
        self.chain, self.rejected, self.order_id = chain, rejected, None

    def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        if method == "POST":
            assert url == CLOB + "/order", "No other financial API may be called"
            payload = json.loads(kwargs["body"])
            if self.rejected:
                return 200, {"success": False, "orderID": "", "status": "", "errorMsg": "fixture rejection"}
            self.chain.order = payload["order"]
            self.order_id = order_hash(self.chain.order, STANDARD_EXCHANGE)
            raise ExchangeError("FIXTURE_RESPONSE_LOST_AFTER_ACCEPTANCE", uncertain=True)
        assert method == "GET"
        if url == GEOBLOCK:
            return 200, {"blocked": False, "country": "PT"}
        if url == CLOB + "/auth/api-keys":
            return 200, {"apiKeys": [API_KEY]}
        if url == CLOB + "/auth/ban-status/closed-only":
            return 200, {"closed_only": False}
        if url == CLOB + "/data/orders":
            return page([])
        if url == CLOB + "/data/trades":
            rows = [] if not self.chain.order else [trade(taker_order_id=self.order_id, size=str(Decimal(self.chain.order["takerAmount"])/1000000), match_time=str(NOW))]
            return page(rows)
        if url.startswith(CLOB + "/data/order/"):
            if not self.chain.order:
                return 404, {}
            size = str(Decimal(self.chain.order["takerAmount"])/1000000)
            return 200, raw_order(id=self.order_id, original_size=size, size_matched=size, status="MATCHED")
        if url == DATA + "/v2/positions":
            rows = [] if not self.chain.order else [{"proxy_wallet": WALLET, "current_size": str(Decimal(self.chain.order["takerAmount"])/1000000), "token_id": TOKEN, "condition_id": CONDITION}]
            return 200, {"data": rows, "pagination": {"has_more": False, "next_cursor": None}}
        if url == CLOB + "/balance-allowance":
            return 200, {"balance": "10000000", "allowances": {STANDARD_EXCHANGE: "20000000", NEG_RISK_EXCHANGE: "1000000"}}
        if url == GAMMA + "/markets":
            return context()[0]
        if url == CLOB + "/clob-markets/" + CONDITION:
            return context()[1]
        if url == CLOB + "/book":
            return context()[2]
        raise AssertionError("Unspecified network endpoint in isolated fixture")


@pytest.mark.parametrize("rejected", [False, True])
def test_real_adapter_intent_submission_restart_and_chain_fill_accounting(tmp_path, monkeypatch, rejected):
    monkeypatch.setattr(time, "time", lambda: NOW)
    cfg = replace(config(tmp_path), wallet=WALLET, signer=WALLET)
    cfg.activation_file.write_text(json.dumps({"action": "ACTIVATE_LIVE_EXECUTION", "wallet": WALLET, "config_sha256": cfg.config_sha256}))
    signal = candidate()
    signal["legs"][0].update(token=TOKEN, condition=CONDITION)
    store = SignalStore(cfg.signal_db)
    store.save(signal)
    store.begin_send(signal["id"])
    store.receipt(signal["id"], 1)
    ledger = ExecutionLedger(cfg.execution_db, WALLET)
    chain = ContractChain()
    wire = ContractWire(chain, rejected)
    exchange = client(wire, chain)
    engine = ExecutionEngine(cfg, ledger, SignalReader(cfg.signal_db), exchange, Weather(signal))
    asyncio.run(engine.reconcile())
    assert engine.authority()
    assert asyncio.run(engine.execute(signal))
    assert ledger.summary()["confirmed_fill_count"] == 0
    if not rejected:
        assert ledger.orders()[0]["status"] == "UNKNOWN"
        assert not engine.authority()
    ledger.recover_after_restart()
    asyncio.run(engine.reconcile())
    asyncio.run(engine.execute(signal))
    assert len([row for row in wire.calls if row[0] == "POST"]) == 1
    result = ledger.summary()
    assert result["confirmed_fill_count"] == (0 if rejected else 1)
    assert result["actual_fees_micros"] == (0 if rejected else 12345)
    assert result["reserved_micros"] == 0
    assert result["legacy_paper_included"] is False
    store.close()
