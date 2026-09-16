"""Fresh adversarial review of frozen c4131c64; all external writes are mocks.

These assertions state the required safety behavior and intentionally fail on
the reviewed frozen candidate. No implementation files are changed here.
"""
import asyncio
from copy import deepcopy
from dataclasses import replace
from decimal import Decimal
import json
import time

import pytest

from polymarket_scanner.production.chain import ExchangeError
from polymarket_scanner.production.config import ConfigurationError
from polymarket_scanner.production.service import SignalService
from polymarket_scanner.production.signals import SignalStore
from test_production_lifecycle import harness, config


def test_terminal_cancel_contradicted_by_live_order_revokes_authority(harness):
    cfg, signal, store, ledger, exchange, weather, engine = harness
    asyncio.run(engine.execute(signal))
    order_id = exchange.posts[0]
    exchange.remote[order_id]["status"] = "CANCELLED"
    asyncio.run(engine.reconcile())
    assert ledger.order(order_id)["status"] == "CANCELLED"
    assert ledger.summary()["reserved_micros"] == 0

    # A subsequent authenticated census and single-order read both expose live
    # remainder. A local terminal row cannot make that external exposure vanish.
    exchange.remote[order_id]["status"] = "LIVE"
    try:
        asyncio.run(engine.reconcile())
    except RuntimeError:
        pass
    assert not engine.authority(), (
        "Authenticated LIVE order was ignored: local CANCELLED row has zero "
        "reservation and is excluded from manage_existing()"
    )


def test_opening_uses_bounded_trade_window_after_initial_census(harness):
    cfg, signal, store, ledger, exchange, weather, engine = harness
    snapshot = exchange.account_snapshot
    calls = []

    def long_lived_account(*, trade_after=None):
        calls.append(trade_after)
        # Model the real adapter's MAX_ITEMS / MAX_PAGES behavior for an account
        # with >10,000 historical trades but a small live incremental window.
        if trade_after is None:
            raise ExchangeError("ACCOUNT_RECONCILIATION_CAP")
        return snapshot(trade_after=trade_after)

    exchange.account_snapshot = long_lived_account
    asyncio.run(engine.reconcile())
    assert engine.authority()
    asyncio.run(engine.tick())
    assert all(value is not None for value in calls), (
        "execute() discarded the durable cursor and requested lifetime trades"
    )
    assert exchange.posts, "A bounded, fully reconciled account should remain usable"


def test_slow_proof_walk_does_not_refresh_old_account_authority(harness, monkeypatch):
    cfg, signal, store, ledger, exchange, weather, engine = harness
    asyncio.run(engine.execute(signal))
    clock = [time.time()]
    monkeypatch.setattr(time, "time", lambda: clock[0])
    snapshot = exchange.account_snapshot
    fills = exchange.confirmed_fills

    def observed_snapshot(**kwargs):
        return dict(snapshot(**kwargs), observed_at=clock[0], started_at=clock[0])

    def slow_proof(*args, **kwargs):
        clock[0] += 40
        return fills(*args, **kwargs)

    exchange.account_snapshot = observed_snapshot
    exchange.confirmed_fills = slow_proof
    asyncio.run(engine.reconcile())
    assert not engine.authority(), (
        "End-of-reconciliation time relabelled a 40-second-old account census fresh"
    )


@pytest.mark.parametrize("new_activity", ["order", "position"])
def test_opening_refresh_cannot_ignore_new_unmanaged_account_activity(harness, new_activity):
    cfg, signal, store, ledger, exchange, weather, engine = harness
    snapshot = exchange.account_snapshot

    def changed_account(**kwargs):
        value = snapshot(**kwargs)
        if new_activity == "order":
            value["open_orders"] = [{"id": "unknown-external-order"}]
        else:
            value["positions"] = [{"token": "unmanaged-token", "quantity": 1_000_000}]
        return value

    exchange.account_snapshot = changed_account
    try:
        asyncio.run(engine.execute(signal))
    except RuntimeError:
        pass
    assert not exchange.posts, "Opening refresh saw unmanaged activity but still submitted"


def test_canonical_startup_does_not_silently_abandon_legacy_terminal_backlog(tmp_path):
    from polymarket_scanner.weather_only_operator_state_corrective_v5 import OperatorStatePostReceiptStoreV5
    from test_weather_stage2_semantic_terminal_maker import _signal

    cfg = config(tmp_path, "LIVE_SIGNALS")
    legacy = OperatorStatePostReceiptStoreV5(cfg.signal_db)
    signal_id, _, candidate = _signal(legacy, suffix="frozen-cutover")
    legacy.terminalize_delivered_signal(
        signal_id, terminal_status="EXPIRED", reason="old delivered alert",
        decision_id=candidate["decision_id"], event_id=candidate["event_id"],
        market_id=candidate["market_id"], side="YES", recorded_at=102,
    )
    assert legacy.pending_operator_sync(limit=20)
    try:
        store = SignalStore(cfg.signal_db)
    except RuntimeError as error:
        # Requiring a separately reviewed, chat-bound offline migration is safe;
        # silently claiming zero pending operator work is not.
        assert "LEGACY" in str(error).upper()
        return

    class Telegram:
        edited = []

        async def updates(self, offset):
            return []

        async def invalidate(self, row):
            self.edited.append(row)
            return "EDITED"

    telegram = Telegram()
    service = SignalService(cfg, store, telegram, None)
    try:
        store.recover()
        asyncio.run(service.controls())
        assert telegram.edited or store.summary()["operator_sync_pending_or_escalated"] > 0, (
            "Canonical startup ignored a delivered legacy terminal alert and "
            "reported zero pending operator synchronization"
        )
    finally:
        store.close()


def test_real_weather_refresh_binds_station_day_budget_to_verified_contract(tmp_path, monkeypatch):
    from polymarket_scanner.production.engine import ExecutionEngine
    from polymarket_scanner.production.ledger import ExecutionLedger
    from polymarket_scanner.production.signals import SignalReader
    from polymarket_scanner.production.weather import _sha
    from test_production_lifecycle import Exchange, WALLET
    from test_production_weather import pipeline, candidates, NOW

    monkeypatch.setattr(time, "time", lambda: NOW)
    weather, public = pipeline()
    source = next(row for row in candidates(weather, public.raw, ["DIRECTIONAL"])
                  if row["legs"][0]["token"] == "t1y")
    signal = deepcopy(source)
    signal["station_day"] = "untrusted-producer-claimed-another-station-day"
    signal["id"] = _sha({key: value for key, value in signal.items() if key != "id"})
    fresh = asyncio.run(weather.revalidate(signal))
    assert fresh["station_day"] == source["station_day"]
    assert fresh["station_day"] != signal["station_day"]

    cfg = config(tmp_path)
    cfg = replace(cfg, risk=replace(cfg.risk, per_station_day=Decimal("5")))
    cfg.activation_file.write_text(json.dumps({"action": "ACTIVATE_LIVE_EXECUTION",
        "wallet": WALLET, "config_sha256": cfg.config_sha256}))
    store = SignalStore(cfg.signal_db)
    ledger = ExecutionLedger(cfg.execution_db, WALLET)
    prior = {"id": "prior-risk", "signal_id": "prior-risk-signal", "strategy": "DIRECTIONAL",
             "station_day": source["station_day"], "expires": NOW + 120,
             "legs": [{"token": "prior-token", "condition": "prior-condition",
                       "price": "0.5", "fee_cap": "0", "quantity": "10"}]}
    assert ledger.reserve(prior, cfg.risk, 100_000_000)
    assert store.save(signal)
    assert store.begin_send(signal["id"])
    store.receipt(signal["id"], 1)
    exchange = Exchange()
    engine = ExecutionEngine(cfg, ledger, SignalReader(cfg.signal_db), exchange, weather)
    try:
        asyncio.run(engine.reconcile())
        assert engine.authority()
        try:
            asyncio.run(engine.execute(signal))
        except RuntimeError:
            pass
        assert not exchange.posts, (
            "Real weather refresh recovered the authentic station/day, but the "
            "engine booked its new order against the producer's forged label"
        )
    finally:
        store.close()


def test_native_terminal_receipt_cannot_be_edited_in_a_different_chat(tmp_path):
    import httpx
    from polymarket_scanner.production.telegram import Telegram
    from test_production_lifecycle import candidate

    cfg = config(tmp_path, "LIVE_SIGNALS")
    credentials = tmp_path / "fixture-telegram.json"
    store = SignalStore(cfg.signal_db)
    edits = []

    def transport(request):
        payload = json.loads(request.content)
        if request.url.path.endswith("/sendMessage"):
            return httpx.Response(200, json={"ok": True, "result": {"message_id": 77}})
        assert request.url.path.endswith("/editMessageText")
        edits.append(payload)
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 77}})

    def select_chat(chat_id):
        credentials.write_text(json.dumps({"token": "100:offline_fixture_only",
            "chat_id": chat_id, "operator_user_ids": [7]}))
        credentials.chmod(0o600)

    async def run():
        select_chat("101")
        first = Telegram(credentials, transport=httpx.MockTransport(transport))
        signal = candidate()
        try:
            await SignalService(cfg, store, first, None).deliver(signal)
        finally:
            await first.close()
        store.terminal(signal["id"], "EXPIRED", "restart before edit")
        select_chat("202")
        second = Telegram(credentials, transport=httpx.MockTransport(transport))
        try:
            try:
                await SignalService(cfg, store, second, None).sync()
            except (RuntimeError, ConfigurationError):
                pass  # Explicit chat-mismatch refusal is acceptable.
        finally:
            await second.close()
        assert not edits or all(item["chat_id"] == "101" for item in edits), (
            "A receipt delivered in chat 101 was edited in chat 202 and marked DONE"
        )

    try:
        asyncio.run(run())
    finally:
        store.close()


def test_partial_fill_does_not_clear_quarantined_cancel_request(harness, monkeypatch):
    cfg, signal, store, ledger, exchange, weather, engine = harness
    clock = [time.time()]
    monkeypatch.setattr(time, "time", lambda: clock[0])
    asyncio.run(engine.execute(signal))
    order_id = exchange.posts[0]
    exchange.remote[order_id]["status"] = "CANCELLED"
    asyncio.run(engine.reconcile())
    exchange.remote[order_id]["status"] = "LIVE"
    with pytest.raises(RuntimeError, match="REMOTE_TERMINAL_ORDER_REVIVED"):
        asyncio.run(engine.reconcile())
    asyncio.run(engine.manage_existing())
    assert exchange.cancels == [order_id]
    # The cancellation response is not terminal proof. A new confirmed partial
    # fill must retain cancellation of the still-live remainder.
    exchange.fill(Decimal("0.5"))
    asyncio.run(engine.reconcile(ignore_sticky_fault=True))
    clock[0] += 16
    assert clock[0] < ledger.plan(ledger.order(order_id)["intent_id"])["expires"]
    asyncio.run(engine.manage_existing())
    assert exchange.cancels == [order_id, order_id], (
        "Confirmed partial fill changed CANCEL_REQUESTED to PARTIAL and silently "
        "dropped cancellation retry of the quarantined LIVE remainder"
    )
    assert not engine.reconciled, "Quarantined live remainder cannot pass recovery"


def test_opening_refresh_checks_known_positions_missing_from_new_census(harness):
    cfg, signal, store, ledger, exchange, weather, engine = harness
    asyncio.run(engine.execute(signal))
    exchange.fill()
    asyncio.run(engine.reconcile())
    assert engine.authority()
    assert ledger.expected_balance("123") > 0
    previous_posts = list(exchange.posts)

    next_signal = deepcopy(signal)
    next_signal.update(id="next-signal", key="next-signal")
    next_signal["legs"][0]["token"] = "456"
    next_signal["legs"][0]["condition"] = "condition2"
    assert store.save(next_signal)
    assert store.begin_send(next_signal["id"])
    store.receipt(next_signal["id"], 2)
    weather.value = next_signal
    # An external token transfer can empty a holding without an authenticated
    # CLOB trade. ExchangeEOA.positions() omits zero-sized indexer rows.
    exchange.balances.clear()
    try:
        asyncio.run(engine.execute(next_signal))
    except RuntimeError:
        pass
    assert exchange.posts == previous_posts, (
        "Opening refreshed to an empty account census but ignored the missing "
        "previously reconciled holding and submitted another order"
    )


def test_partial_fill_does_not_postpone_due_cancel_retry(harness, monkeypatch):
    cfg, signal, store, ledger, exchange, weather, engine = harness
    clock = [time.time()]
    monkeypatch.setattr(time, "time", lambda: clock[0])
    asyncio.run(engine.execute(signal))
    order_id = exchange.posts[0]
    store.command(9001, "operator", "/cancel_open")
    asyncio.run(engine.manage_existing())
    assert exchange.cancels == [order_id]
    clock[0] += 10
    exchange.fill(Decimal("0.5"))
    asyncio.run(engine.reconcile())
    assert ledger.order(order_id)["status"] == "CANCEL_REQUESTED"
    clock[0] += 6
    asyncio.run(engine.manage_existing())
    assert exchange.cancels == [order_id, order_id], (
        "Partial-fill updated timestamp reset the cancellation retry clock; "
        "a continuing fill stream can suppress retries indefinitely"
    )


def test_maker_expiration_respects_operator_rest_cap(harness, monkeypatch):
    cfg, signal, store, ledger, exchange, weather, engine = harness
    now = time.time()
    monkeypatch.setattr(time, "time", lambda: now)
    signal["strategy"] = "MAKER"
    signal["legs"][0]["price"] = "0.39"
    assert asyncio.run(engine.execute(signal))
    assert exchange.posts
    assert exchange.prepared["expiration"] <= now + cfg.risk.max_maker_rest_seconds, (
        "Prepared maker expiration exceeds the operator's configured maximum rest time"
    )
