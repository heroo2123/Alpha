"""Independent behavioral reproductions against the composed production runtime.

All exchange and Telegram writes are in-memory fakes. No funded account, real
credential, network notification, chain write, or production database is used.
"""
from __future__ import annotations

import asyncio
from copy import deepcopy
from decimal import Decimal
import json
import os
from pathlib import Path
from types import SimpleNamespace
import time

import pytest

from polymarket_scanner.production.chain import ExchangeError, STANDARD_EXCHANGE, NEG_RISK_EXCHANGE
from polymarket_scanner.production.io import atomic_json
from polymarket_scanner.production.service import SignalService
from polymarket_scanner.production.signals import SignalStore
from polymarket_scanner.production.telegram import Telegram

from test_production_lifecycle import harness, candidate, config


def test_zero_exchange_allowance_prevents_authority_and_submission(harness):
    cfg, signal, store, ledger, exchange, weather, engine = harness
    original = exchange.account_snapshot
    exchange.account_snapshot = lambda **kwargs: dict(original(**kwargs), allowances={STANDARD_EXCHANGE: 0, NEG_RISK_EXCHANGE: 0})
    try:
        asyncio.run(engine.reconcile())
        asyncio.run(engine.execute(signal))
    except (ExchangeError, RuntimeError):
        pass
    assert not engine.authority(), "A spendable balance does not establish exchange spend authority"
    assert exchange.posts == []


def test_close_only_readiness_does_not_suppress_emergency_order_cancellation(harness):
    cfg, signal, store, ledger, exchange, weather, engine = harness
    asyncio.run(engine.execute(signal))
    store.command(991, "operator", "/cancel_open")

    def close_only(**kwargs):
        raise ExchangeError("ACCOUNT_CLOSE_ONLY")

    exchange.account_snapshot = close_only
    asyncio.run(engine.tick())
    assert exchange.cancels == exchange.posts, "Opening eligibility must not block managing existing orders"
    assert not engine.authority()
    assert ledger.summary()["reserved_micros"] > 0, "Cancel request alone is not release proof"


@pytest.mark.parametrize("revision", [None, {"payout": "0", "proof": {"finalized_block": "b2"}}])
def test_changed_or_revoked_settlement_blocks_reopening(harness, revision):
    cfg, signal, store, ledger, exchange, weather, engine = harness
    asyncio.run(engine.execute(signal))
    exchange.fill()
    exchange.resolution = {"payout": "1", "proof": {"finalized_block": "b1"}}
    asyncio.run(engine.reconcile())
    assert ledger.summary()["settled_position_count"] == 1
    assert engine.authority()
    exchange.resolution = revision
    try:
        asyncio.run(engine.reconcile())
    except (ExchangeError, RuntimeError):
        pass
    assert not engine.authority(), "Previously booked payout must remain supported by current finality"


def test_adapter_redemption_shape_updates_real_holding_balance(tmp_path):
    # Decode authentic event ABI fixtures; a hand-authored return-value stub can
    # hide cross-component record schema mismatches.
    from test_production_exchange import (WALLET, TOKEN, CONDITION, OID, USDCE,
        fill_log, redemption_logs, receipt)
    from polymarket_scanner.production.chain import decode_fills, decode_redemptions
    from polymarket_scanner.production.ledger import ExecutionLedger
    cfg = config(tmp_path)
    ledger = ExecutionLedger(cfg.execution_db, WALLET)
    plan = {"id": "redemption-intent", "signal_id": "redemption-signal", "strategy": "DIRECTIONAL",
            "station_day": "KSEA/2026-09-17", "expires": time.time() + 120,
            "legs": [{"token": TOKEN, "condition": CONDITION, "price": "0.4", "quantity": "2", "fee_cap": "0.01"}]}
    assert ledger.reserve(plan, cfg.risk, 100_000_000)
    ledger.begin_submission(plan["id"], 0, OID, "isolated-fixture-wire", {"fixture": True})
    ledger.submission_result(OID, "ACKNOWLEDGED")
    for fill in decode_fills(receipt([fill_log()]), order_id=OID, wallet=WALLET, token=TOKEN, exchange=STANDARD_EXCHANGE):
        ledger.record_fill(fill)
    ledger.settle(TOKEN, "1", {"finalized_block": "fixture"})
    record, = decode_redemptions(receipt(redemption_logs()), wallet=WALLET, condition=CONDITION, tokens={1: TOKEN})
    assert ledger.record_redemption(record)
    assert ledger.expected_balance(TOKEN) == 0
    assert ledger.summary()["verified_redemption_proceeds_by_asset_micros"] == {USDCE: 2_000_000}
    assert not ledger.record_redemption(record), "Repeat import must not subtract tokens twice"
    contradicted = dict(record, proceeds=record["proceeds"] + 1)
    from polymarket_scanner.production.ledger import LedgerError
    try:
        ledger.record_redemption(contradicted)
    except LedgerError:
        pass
    else:
        assert ledger.state("fault"), "A changed cash amount on the same receipt is a contradiction, not idempotent replay"


class TelegramFake:
    def __init__(self):
        self.sent = []
        self.edited = []

    async def send(self, text):
        self.sent.append(text)
        return len(self.sent)

    async def invalidate(self, row):
        self.edited.append(row["id"])
        return "EDITED"

    async def updates(self, offset):
        return []


def test_fresh_signal_can_rearm_after_old_episode_ended(harness, monkeypatch):
    cfg, first, store, ledger, exchange, weather, engine = harness
    store.terminal(first["id"], "EXPIRED", "QUOTE_VALIDITY_ENDED")
    next_episode = time.time() + 301
    monkeypatch.setattr(time, "time", lambda: next_episode)
    refreshed = dict(deepcopy(first), id="new-evidence-id", created=time.time(),
                     expires=time.time() + 120, evidence_hash="fresh-source-hash")
    telegram = TelegramFake()
    service = SignalService(cfg, store, telegram, weather)
    asyncio.run(service.deliver(refreshed))
    assert len(telegram.sent) == 1, "Contract-level dedupe cannot suppress every later valid episode"
    assert store.candidate(refreshed["id"]) == refreshed
    assert len(store.recent()) == 2, "Prior immutable signal evidence must survive rearming"


def test_command_polling_precedes_expiry_invalidation_backlog(tmp_path, monkeypatch):
    clock = [2_000_000_000.0]
    monkeypatch.setattr(time, "time", lambda: clock[0])
    cfg = config(tmp_path, "LIVE_SIGNALS")
    store = SignalStore(cfg.signal_db)
    for index in range(10):
        signal = dict(candidate(), id=f"old-{index}", key=f"old-{index}", expires=clock[0] + 1)
        assert store.save(signal)
        assert store.begin_send(signal["id"])
        store.receipt(signal["id"], index + 1)
    clock[0] += 2
    command_at = clock[0]

    class BackloggedTelegram(TelegramFake):
        chat_id = "42"
        operators = {"21"}
        authenticated = Telegram.authenticated

        async def invalidate(self, row):
            clock[0] += 15  # each expired-message edit can use its real 15s timeout
            return "EDIT_UNCERTAIN"

        async def updates(self, offset):
            return [{"update_id": 22, "message": {"chat": {"id": 42}, "from": {"id": 21, "is_bot": False},
                     "date": command_at, "text": "/stop"}}]

    asyncio.run(SignalService(cfg, store, BackloggedTelegram(), None).controls())
    assert store.state("stop_opening") == "1", "Expiry backlog must not age an authorized emergency command out"


def test_preflight_status_is_readable_by_signal_component_group(harness, monkeypatch):
    cfg, signal, store, ledger, exchange, weather, engine = harness
    from polymarket_scanner.production import __main__ as cli
    from polymarket_scanner.production import exchange as exchange_module
    from polymarket_scanner.production import weather as weather_module

    async def close_weather():
        pass

    weather.close = close_weather
    exchange.close = lambda: None
    monkeypatch.setattr(cli, "clean_startup", lambda: None)
    monkeypatch.setattr(cli.ProductionConfig, "load", classmethod(lambda cls, path: cfg))
    monkeypatch.setattr(weather_module, "WeatherPipeline", lambda: weather)
    monkeypatch.setattr(exchange_module.ExchangeEOA, "from_credentials_file", staticmethod(lambda *args, **kwargs: exchange))
    args = cli.parser().parse_args(["preflight", "--config", str(cfg.status_path.parent / "unused.json")])
    asyncio.run(cli.run(args))
    path = cfg.execution_status_path
    assert path.stat().st_mode & 0o040, "Separate signal UID needs group-read access to execution status"
    assert not path.stat().st_mode & 0o007, "Shared status should not become world-readable"
    assert SignalService(cfg, store, None, weather).execution_status()["reconciled"] is True


def test_external_position_in_previously_rejected_token_is_not_ours(harness):
    cfg, signal, store, ledger, exchange, weather, engine = harness
    exchange.outcome = "REJECTED"
    asyncio.run(engine.execute(signal))
    assert ledger.summary()["confirmed_fill_count"] == 0
    exchange.balances[signal["legs"][0]["token"]] = 7_000_000
    try:
        asyncio.run(engine.reconcile())
    except (ExchangeError, RuntimeError):
        pass
    assert not engine.authority(), "Knowing a token ID is not proof we acquired the observed holding"


def test_untracked_account_trade_round_trip_blocks_dedicated_account_authority(harness):
    cfg, signal, store, ledger, exchange, weather, engine = harness
    original = exchange.account_snapshot
    unrelated_trade = {"id": "foreign-trade", "status": "CONFIRMED", "trader_side": "TAKER",
                       "taker_order_id": "unknown-foreign-order", "maker_orders": [],
                       "asset_id": "456", "side": "BUY", "size": "7", "price": "0.5"}
    exchange.account_snapshot = lambda **kwargs: dict(original(**kwargs), trades=[unrelated_trade])
    try:
        asyncio.run(engine.reconcile())
    except (ExchangeError, RuntimeError):
        pass
    assert not engine.authority(), "Historical account activity cannot disappear merely because inventory is flat"


def test_large_basket_is_never_sent_as_silently_truncated_instructions(tmp_path):
    import httpx
    from polymarket_scanner.production.telegram import signal_message
    secret_file = tmp_path / "telegram-fixture.json"
    secret_file.write_text(json.dumps({"token": "123:isolated_fixture_not_live", "chat_id": "42", "operator_user_ids": [21]}))
    secret_file.chmod(0o600)
    signal = candidate("STRUCTURAL")
    signal["legs"] = [dict(signal["legs"][0], token=str(2**250 + index)) for index in range(40)]
    message = signal_message(signal)
    requests = []

    def response(request):
        requests.append(json.loads(request.content))
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 1}})

    async def deliver():
        api = Telegram(secret_file, transport=httpx.MockTransport(response))
        try:
            return await api.send(message)
        finally:
            await api.close()

    from polymarket_scanner.production.config import ConfigurationError
    try:
        result = asyncio.run(deliver())
    except ConfigurationError as exc:
        assert str(exc) == "COMPLETE_TELEGRAM_MESSAGE_TOO_LARGE"
        result = None
    if result is None:
        assert requests == [], "Oversize instructions must be rejected before sending, not reported unknown after truncation"
    else:
        delivered = "\n".join(row["text"] for row in requests)
        assert all(leg["token"] in delivered for leg in signal["legs"]), "Every required basket leg must reach the operator"
        assert "Valid until" in delivered and "no guaranteed realized profit" in delivered


def test_activation_review_output_alias_cannot_install_activation(harness, monkeypatch):
    from polymarket_scanner.production import __main__ as cli
    from polymarket_scanner.production.config import ConfigurationError
    cfg, signal, store, ledger, exchange, weather, engine = harness
    cfg.activation_file.unlink()
    subdirectory = cfg.activation_file.parent / "review-folder"
    subdirectory.mkdir()
    alias = subdirectory / ".." / cfg.activation_file.name
    monkeypatch.setattr(cli, "clean_startup", lambda: None)
    monkeypatch.setattr(cli.ProductionConfig, "load", classmethod(lambda cls, path: cfg))
    args = cli.parser().parse_args(["activation-request", "--config", str(cfg.status_path.parent / "unused.json"), "--output", str(alias)])
    try:
        asyncio.run(cli.run(args))
    except ConfigurationError:
        pass
    assert not cfg.activation_file.exists(), "Review request must not install live authority via a path alias"
    assert not cfg.activation_requested()


def test_operator_status_rejects_other_configuration_or_account(harness):
    cfg, signal, store, ledger, exchange, weather, engine = harness
    status = engine.status()
    status["financial_authority"] = True
    status["config_sha256"] = "another-config"
    status["account"]["wallet"] = "0x" + "99" * 20
    atomic_json(cfg.execution_status_path, status, mode=0o640)
    shown = SignalService(cfg, store, None, weather).execution_status()
    assert shown.get("financial_authority") is False
    assert shown.get("reason"), "Operator needs an explicit current-configuration mismatch diagnosis"


@pytest.mark.parametrize("field,value", [("side", "SELL"), ("condition", "different-condition"), ("price", "0.5")])
def test_wrong_known_order_identity_never_releases_reservation(harness, field, value):
    cfg, signal, store, ledger, exchange, weather, engine = harness
    asyncio.run(engine.execute(signal))
    order = exchange.remote[exchange.posts[0]]
    order.update(side="BUY", condition=signal["legs"][0]["condition"], price="0.4", status="CANCELLED")
    order[field] = value
    try:
        asyncio.run(engine.reconcile())
    except (ExchangeError, RuntimeError):
        pass
    assert ledger.summary()["reserved_micros"] > 0, "Contradictory immutable order evidence cannot prove our cancellation"
    assert not engine.authority()


def test_expiry_is_rechecked_after_contended_submission_writer_lock(harness, monkeypatch):
    from contextlib import contextmanager
    from polymarket_scanner.production.ledger import LedgerError
    cfg, signal, store, ledger, exchange, weather, engine = harness
    now = time.time()
    plan = {"id": "late-intent", "signal_id": "late-signal", "strategy": "DIRECTIONAL",
            "station_day": signal["station_day"], "expires": now + 1,
            "legs": [{"token": "123", "condition": "condition1", "quantity": "2", "price": "0.4", "fee_cap": "0.004"}]}
    assert ledger.reserve(plan, cfg.risk, 100_000_000)
    original = ledger.transaction
    clock = [now]
    monkeypatch.setattr(time, "time", lambda: clock[0])

    @contextmanager
    def lock_wait():
        with original() as db:
            clock[0] = now + 2  # acquisition finished after the deadline
            yield db

    monkeypatch.setattr(ledger, "transaction", lock_wait)
    with pytest.raises(LedgerError, match="INTENT_EXPIRED_BEFORE_SUBMISSION"):
        ledger.begin_submission(plan["id"], 0, "never-posted", "wire", {"fixture": True})
    assert ledger.orders(outstanding=False) == []
    assert exchange.posts == []


def test_consumed_intent_does_not_refetch_weather_or_starve_later_page(harness):
    cfg, signal, store, ledger, exchange, weather, engine = harness
    asyncio.run(engine.execute(signal))
    initial_calls = weather.calls
    assert asyncio.run(engine.execute(signal)) is False
    assert weather.calls == initial_calls, "Consumed immutable intent needs no second evidence walk"

    # More than one pending page at once; first-page rows are deliberately still
    # ACTIVE and delivered. A durable cursor must still visit the final row.
    for index in range(30):
        fresh = dict(deepcopy(signal), id=f"signal-{index:03d}", key=f"strategy-{index:03d}")
        assert store.save(fresh)
        assert store.begin_send(fresh["id"])
        store.receipt(fresh["id"], index + 10)
    visited = []

    async def consumed_or_rejected(value):
        visited.append(value["id"])
        return False

    engine.execute = consumed_or_rejected
    asyncio.run(engine.tick())
    asyncio.run(engine.tick())
    assert "signal-029" in visited, "First-page consumed signals must not starve newer opportunities"


@pytest.mark.parametrize("scenario", ["valid", "changed-generation", "stale-status", "wrong-config", "wrong-wallet", "fault"])
def test_local_resume_requires_current_stop_generation_and_reconciliation(harness, monkeypatch, scenario):
    from polymarket_scanner.production import __main__ as cli
    from polymarket_scanner.production.config import ConfigurationError
    cfg, signal, store, ledger, exchange, weather, engine = harness
    store.command(100, "operator", "/cancel_open")
    status = engine.status()
    if scenario == "stale-status":
        status["updated_at"] -= 31
    if scenario == "wrong-config":
        status["config_sha256"] = "different-approved-config"
    if scenario == "wrong-wallet":
        status["account"]["wallet"] = "0x" + "34" * 20
    if scenario == "fault":
        status["account"]["reconciliation_fault"] = "unresolved"
    if scenario == "changed-generation":
        store.command(101, "operator", "/stop")
    atomic_json(cfg.execution_status_path, status, mode=0o640)
    monkeypatch.setattr(cli, "clean_startup", lambda: None)
    monkeypatch.setattr(cli.ProductionConfig, "load", classmethod(lambda cls, path: cfg))
    args = cli.parser().parse_args(["resume-openings", "--config", str(cfg.status_path.parent / "unused.json"), "--expected-stop-generation", "100"])
    if scenario == "valid":
        asyncio.run(cli.run(args))
        assert store.state("stop_opening") != "1" and store.state("cancel_open") != "1"
        with store.connect() as db:
            assert db.execute("SELECT COUNT(*) FROM live_audit WHERE kind='LOCAL_OPERATOR_RESUME_REQUEST'").fetchone()[0] == 1
    else:
        with pytest.raises(ConfigurationError):
            asyncio.run(cli.run(args))
        assert store.state("stop_opening") == "1" and store.state("cancel_open") == "1"


def test_account_authority_expires_if_clock_rolls_back(harness, monkeypatch):
    cfg, signal, store, ledger, exchange, weather, engine = harness
    assert engine.authority()
    reconciled_at = engine.last_reconcile
    monkeypatch.setattr(time, "time", lambda: reconciled_at - 300)
    assert not engine.authority(), "A future reconciliation timestamp cannot establish current account readiness"


def test_terminal_reconciliation_audit_is_idempotent(harness):
    cfg, signal, store, ledger, exchange, weather, engine = harness
    asyncio.run(engine.execute(signal))
    exchange.fill()
    asyncio.run(engine.reconcile())
    with ledger.connect() as db:
        initial = db.execute("SELECT COUNT(*) FROM execution_audit WHERE kind='ORDER_TERMINAL'").fetchone()[0]
    asyncio.run(engine.reconcile())
    asyncio.run(engine.reconcile())
    with ledger.connect() as db:
        final = db.execute("SELECT COUNT(*) FROM execution_audit WHERE kind='ORDER_TERMINAL'").fetchone()[0]
    assert final == initial, "No-op confirmed state must not append millions of repeated audit rows"


def test_fully_reserved_structural_basket_tolerates_delayed_chain_confirmation(harness):
    cfg, original, store, ledger, exchange, weather, engine = harness
    signal = dict(deepcopy(original), id="structural-sig", key="structural-key", strategy="STRUCTURAL")
    signal["legs"] = [dict(signal["legs"][0], token=str(123 + index), condition=f"condition{index + 1}", price="0.2", fee="0.002") for index in range(3)]
    weather.value = signal
    exchange.ask = "0.2"
    assert store.save(signal)
    assert store.begin_send(signal["id"])
    store.receipt(signal["id"], 9)
    assert asyncio.run(engine.execute(signal))
    assert len(exchange.posts) == 3, "Final chain receipt lag must not silently make supported baskets first-leg-only"
    assert len(set(exchange.posts)) == 3
    summary = ledger.summary()
    assert summary["confirmed_fill_count"] == 0 and summary["actual_cost_micros"] == 0
    assert summary["settled_position_pnl_micros"] == 0
    assert 0 < summary["reserved_micros"] <= int(cfg.risk.legging_loss * 1_000_000)
    assert summary["reserved_micros"] == sum(row["reserved"] for row in ledger.orders())
    asyncio.run(engine.execute(signal))
    assert len(exchange.posts) == 3, "Delayed receipt is never permission to repost any basket leg"


def test_oversize_signal_is_definitely_unsent_not_stuck_sending(harness):
    cfg, original, store, ledger, exchange, weather, engine = harness
    signal = dict(deepcopy(original), id="oversize-basket", key="oversize-key", strategy="STRUCTURAL")
    signal["legs"] = [dict(signal["legs"][0], token=str(2**250 + index)) for index in range(40)]
    telegram = TelegramFake()
    asyncio.run(SignalService(cfg, store, telegram, weather).deliver(signal))
    row = next(row for row in store.recent() if row["id"] == signal["id"])
    assert telegram.sent == []
    assert row["delivery"] not in {"SENDING", "DELIVERED", "UNKNOWN"}
    assert row["status"] == "INVALIDATED"
    assert "TELEGRAM" in row["reason"]


def test_json_command_reply_does_not_crash_service_after_html_expansion(harness, tmp_path):
    import httpx
    cfg, original, store, ledger, exchange, weather, engine = harness
    for index in range(20):
        signal = dict(deepcopy(original), id=f"signal-history-{index:064d}", key=f"history-{index}")
        assert store.save(signal)
    private = tmp_path / "telegram-command-fixture.json"
    private.write_text(json.dumps({"token": "123:isolated_fixture_not_live", "chat_id": "42", "operator_user_ids": [21]}))
    private.chmod(0o600)
    sent = []

    def response(request):
        if request.url.path.endswith("/getUpdates"):
            return httpx.Response(200, json={"ok": True, "result": [{"update_id": 300,
                "message": {"chat": {"id": 42}, "from": {"id": 21, "is_bot": False},
                            "date": int(time.time()), "text": "/recent"}}]})
        assert request.url.path.endswith("/sendMessage")
        sent.append(json.loads(request.content)["text"])
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 99}})

    async def controls():
        telegram = Telegram(private, transport=httpx.MockTransport(response))
        try:
            await SignalService(cfg, store, telegram, weather).commands()
        finally:
            await telegram.close()

    asyncio.run(controls())
    assert store.state("telegram_offset") == "301"
    assert sent, "Authenticated bounded report needs a delivered reply, not just an audited command"
    assert all(len(text.encode("utf-16-le")) // 2 <= 4000 for text in sent)


@pytest.mark.parametrize("failure,expected_posts", [("REJECTED", 1), ("UNKNOWN", 1), ("KNOWN_PARTIAL", 1), ("SECOND_LEG_UNKNOWN", 2)])
def test_structural_basket_stops_on_known_failure_without_fake_profit(harness, failure, expected_posts):
    cfg, original, store, ledger, exchange, weather, engine = harness
    signal = dict(deepcopy(original), id="failure-basket", key="failure-basket-key", strategy="STRUCTURAL")
    signal["legs"] = [dict(signal["legs"][0], token=str(123 + index), condition=f"condition{index + 1}", price="0.2", fee="0.002") for index in range(3)]
    weather.value = signal
    exchange.ask = "0.2"
    original_submit = exchange.submit

    def submit(prepared):
        attempt = len(exchange.posts)
        exchange.outcome = failure if failure in {"UNKNOWN", "REJECTED"} else "UNKNOWN" if failure == "SECOND_LEG_UNKNOWN" and attempt == 1 else "ACKNOWLEDGED"
        result = original_submit(prepared)
        if failure == "KNOWN_PARTIAL" and attempt == 0:
            row = exchange.remote[prepared["order_id"]]
            row["matched"] = row["quantity"] // 2
        return result

    exchange.submit = submit
    assert store.save(signal)
    assert store.begin_send(signal["id"])
    store.receipt(signal["id"], 9)
    asyncio.run(engine.execute(signal))
    assert len(exchange.posts) == expected_posts
    summary = ledger.summary()
    assert summary["confirmed_fill_count"] == 0 and summary["actual_cost_micros"] == 0
    assert summary["settled_position_pnl_micros"] == 0
    assert summary["reserved_micros"] <= int(cfg.risk.legging_loss * 1_000_000)
    if failure == "REJECTED":
        assert summary["reserved_micros"] == 0
    else:
        assert summary["reserved_micros"] > 0
    ledger.recover_after_restart()
    asyncio.run(engine.execute(signal))
    assert len(exchange.posts) == expected_posts, "Recovery cannot replay acknowledged, unknown or unsubmitted basket legs"


def test_historical_order_work_is_bounded_and_rotates_while_hot_orders_reconcile(harness):
    cfg, original, store, ledger, exchange, weather, engine = harness
    for index in range(60):
        plan = {"id": f"old-intent-{index:03d}", "signal_id": f"old-signal-{index:03d}", "strategy": "DIRECTIONAL",
                "station_day": original["station_day"], "expires": time.time() + 120,
                "legs": [dict(original["legs"][0], quantity="2", fee_cap="0.004")]}
        assert ledger.reserve(plan, cfg.risk, 100_000_000)
        oid = f"historical-order-{index:03d}"
        ledger.begin_submission(plan["id"], 0, oid, "fixture-wire", {"order": {"expiration": "0"}})
        ledger.submission_result(oid, "REJECTED")
        ledger.release_unsubmitted(plan["id"])
    asyncio.run(engine.execute(original))
    hot_order = exchange.posts[-1]
    queried = []
    original_get = exchange.get_order

    def observed_get(order_id):
        queried.append(order_id)
        return original_get(order_id)

    exchange.get_order = observed_get
    first_batch = None
    for _ in range(12):
        before = len(queried)
        asyncio.run(engine.reconcile())
        batch = queried[before:]
        assert hot_order in batch, "Hot exposure cannot wait behind historical audit work"
        assert len(batch) < 20, "One tick must not reread all sixty settled/rejected orders"
        if first_batch is None:
            first_batch = set(batch)
        assert engine.authority()
    assert len({oid for oid in queried if oid != hot_order}) == 60, "Bounded auditing needs a durable rotating cursor"
    assert set(queried) != first_batch
