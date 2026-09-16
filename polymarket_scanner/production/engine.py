"""Isolated financial worker: durable intent precedes every single submission.

Market/weather input is independently refetched by the worker. A signature never
comes from Telegram or a signal payload. Existing orders keep reconciling while
new position authority is stopped. No ambiguous submission is ever reposted.
"""
from __future__ import annotations

import asyncio
from decimal import Decimal, ROUND_FLOOR
import json
import sqlite3
import time

from .config import ProductionConfig, ConfigurationError, decimal, digest
from .ledger import ExecutionLedger, LedgerError, micros, SCALE


class ExecutionError(RuntimeError):
    pass


class ExecutionEngine:
    def __init__(self, config: ProductionConfig, ledger: ExecutionLedger, reader, exchange, weather):
        if config.mode != "LIVE_EXECUTION" or config.risk is None:
            raise ConfigurationError("EXECUTION_MODE_REQUIRED")
        self.config, self.ledger, self.reader = config, ledger, reader
        self.exchange, self.weather = exchange, weather
        self.reconciled = False
        self.io_fault = False
        self.last_account = None
        self.last_reconcile = 0.0
        self.last_error = None

    def authority(self) -> bool:
        try:
            stopped = self.reader.state("stop_opening") == "1"
        except (sqlite3.Error, OSError):
            self.last_error = "SIGNAL_STATE_UNAVAILABLE"
            return False
        return bool(not self.io_fault and self.reconciled and 0 <= time.time() - self.last_reconcile < 30
                    and self.last_account and self.last_account.get("openings_allowed") is True
                    and self.config.activation_requested() and not self.ledger.state("fault")
                    and not stopped)

    async def call(self, method, *args, **kwargs):
        return await asyncio.to_thread(method, *args, **kwargs)

    def fail(self, code: str):
        self.reconciled = False
        self.last_error = code
        try:
            self.ledger.fault(code)
        except (OSError, sqlite3.Error):
            self.io_fault = True
        raise ExecutionError(code)

    async def reconcile(self, *, ignore_sticky_fault=False):
        """A failed comparison stops opening; confirmed fills remain append-only."""
        started = time.time()
        self.reconciled = False
        hot = self.ledger.orders()
        watermark = self.ledger.state("trade_census_after")
        trade_after = int(watermark) - 300 if watermark else None
        if trade_after is not None and hot:
            trade_after = min(trade_after, int(min(row["created"] for row in hot)) - 1)
        account = await self.call(self.exchange.account_snapshot, trade_after=max(0, trade_after) if trade_after is not None else None)
        if account.get("wallet", "").lower() != self.config.wallet or account.get("signer", "").lower() != self.config.signer:
            self.fail("ACCOUNT_IDENTITY_MISMATCH")
        allowance_ready = bool(account.get("allowances") and max(account["allowances"].values()) >= micros(self.config.risk.per_order))
        audit = self.ledger.historical_orders(self.ledger.state("order_audit_cursor") or "")
        if not audit:
            audit = self.ledger.historical_orders("")
        known = {r["id"]: r for r in hot + audit}
        for trade in account.get("trades", []):
            owned = trade.get("owned_order_ids")
            if not isinstance(owned, list) or not owned:
                self.fail("UNMANAGED_ACCOUNT_TRADE")
            for order_id in owned:
                row = known.get(order_id) or self.ledger.order(order_id)
                if row is None:
                    self.fail("UNMANAGED_ACCOUNT_TRADE")
                known[order_id] = row
        for external in account["open_orders"]:
            row = known.get(external["id"]) or self.ledger.order(external["id"])
            if row is None:
                self.fail("UNMANAGED_ACCOUNT_ORDER")
            if row["status"] in {"CANCELLED", "REJECTED"}:
                self.ledger.quarantine_reopened_order(row["id"])
                self.fail("REMOTE_TERMINAL_ORDER_REVIVED")
            known[external["id"]] = row
        # Poll one overlapping historical time window in addition to the live
        # window. Match-time filtering alone cannot prove that old indexer rows
        # will never arrive late. The initial full account census is retained as
        # the trust boundary; history since it is audited on a durable cursor.
        census_start = self.ledger.state("trade_census_start")
        if census_start and watermark:
            audit_start = int(self.ledger.state("trade_history_cursor") or census_start)
            if audit_start >= int(watermark):
                audit_start = int(census_start)
            audit_end = min(audit_start + 1800, int(watermark))
            historical = await self.call(self.exchange.account_trades, after=max(0, audit_start - 300), before=audit_end)
            for trade in historical:
                owned = trade.get("owned_order_ids")
                if not isinstance(owned, list) or not owned:
                    self.fail("UNMANAGED_HISTORICAL_ACCOUNT_TRADE")
                for order_id in owned:
                    row = known.get(order_id) or self.ledger.order(order_id)
                    if row is None:
                        self.fail("UNMANAGED_HISTORICAL_ACCOUNT_TRADE")
                    known[order_id] = row
            self.ledger.set_state("trade_history_cursor", str(audit_end))
        unresolved = not allowance_ready
        if not allowance_ready:
            self.last_error = "COLLATERAL_ALLOWANCE_READINESS_FAILED"
        for order in known.values():
            plan = self.ledger.plan(order["intent_id"])
            leg = plan["legs"][order["leg"]]
            # Also check terminal orders: an indexer-delayed fill must survive a
            # cancellation race, and a previously confirmed receipt may reorganize.
            trades = account.get("trades")
            if trade_after is not None and order["created"] < trade_after:
                trades = await self.call(self.exchange.account_trades, token=order["token"], after=max(0, int(order["created"]) - 300), before=max(int(order["created"]) + 60, self.ledger.order_expiration(order["id"])) + 300)
            fills = await self.call(self.exchange.confirmed_fills, order["id"], order["token"], leg["neg_risk"], trades=trades, recorded_fills=self.ledger.order_fills(order["id"]))
            for fill in fills:
                self.ledger.record_fill(fill)
            remote = await self.call(self.exchange.get_order, order["id"])
            current = self.ledger.order(order["id"])
            if remote is None:
                if current["status"] not in {"FILLED", "REJECTED", "CANCELLED"}:
                    unresolved = True
                continue
            if (remote["id"] != order["id"] or remote["token"] != order["token"] or remote["quantity"] != order["quantity"]
                or remote.get("condition") != order["condition_id"] or remote.get("side") != "BUY"
                or Decimal(str(remote.get("price", "-1"))) != Decimal(order["limit_price"])
                or remote.get("expires") != self.ledger.order_expiration(order["id"])):
                self.fail("ORDER_RECONCILIATION_IDENTITY_MISMATCH")
            if remote["status"] == "INVALID":
                self.fail("EXCHANGE_ORDER_INVALID_REQUIRES_RECOVERY")
            if current["status"] in {"CANCELLED", "REJECTED"} and remote["status"] in {"LIVE", "DELAYED", "UNMATCHED"}:
                # A terminal local assertion cannot hide newly reported exposure.
                # Keep the contradiction durable and the order in cancellation
                # management, even when this exceeds configured opening limits.
                self.ledger.quarantine_reopened_order(order["id"])
                self.fail("REMOTE_TERMINAL_ORDER_REVIVED")
            matched = remote["matched"]
            if matched < current["matched"]:
                self.fail("REMOTE_MATCHED_BELOW_CONFIRMED_FILLS")
            if matched != current["matched"]:
                unresolved = True  # reserved until confirmed fee-bearing receipts
                continue
            if remote["status"] in {"CANCELED", "CANCELLED", "EXPIRED"}:
                self.ledger.confirm_terminal(order["id"], cancelled=True, matched=matched)
            elif matched == current["quantity"]:
                self.ledger.confirm_terminal(order["id"], cancelled=False, matched=matched)
            elif current["status"] in {"UNKNOWN", "SUBMITTING"}:
                self.ledger.submission_result(order["id"], "ACKNOWLEDGED")
            elif current["status"] == "CANCEL_REQUESTED":
                unresolved = True
        # Only actual confirmed holdings are ours. A dedicated account is required;
        # manual/external transfers, sells and untracked positions block openings.
        known_tokens = {order["token"] for order in known.values()}
        own = {p["token"] for p in self.ledger.positions(held_only=True)}
        known_tokens.update(own)
        for position in account["positions"]:
            token = str(position["token"])
            if position["quantity"] and not self.ledger.token_known(token):
                self.fail("UNMANAGED_ACCOUNT_POSITION")
            if position["quantity"] != self.ledger.expected_balance(token):
                unresolved = True
                self.last_error = "POSITION_BALANCE_UNRECONCILED"
        for token in known_tokens:
            balance = await self.call(self.exchange.token_balance, token)
            expected = self.ledger.expected_balance(token)
            if balance != expected:
                unresolved = True
                self.last_error = "POSITION_BALANCE_UNRECONCILED"
        checked_settlements = set()
        for order in known.values():
            order = self.ledger.order(order["id"])
            if order["status"] not in {"FILLED", "CANCELLED"} or not order["matched"]:
                continue
            if order["token"] in checked_settlements:
                continue
            checked_settlements.add(order["token"])
            plan = self.ledger.plan(order["intent_id"])
            leg = plan["legs"][order["leg"]]
            resolution = await self.call(self.exchange.settlement, order["condition_id"], leg["outcome_index"], token=order["token"], neg_risk=leg["neg_risk"])
            if resolution:
                self.ledger.settle(order["token"], str(resolution["payout"]), resolution["proof"])
            else:
                self.ledger.dispute_missing_settlement(order["token"])
        self.last_account = account
        self.last_reconcile = min(started, float(account.get("started_at", started)), float(account.get("observed_at", started)))
        if audit:
            self.ledger.set_state("order_audit_cursor", audit[-1]["id"])
        self.ledger.set_state("trade_census_after", str(int(account.get("observed_at", time.time()))))
        if not census_start:
            self.ledger.set_state("trade_census_start", str(int(account.get("observed_at", time.time())) - 300))
        unresolved = unresolved or bool(self.ledger.summary()["settlement_proof_unavailable_tokens"])
        self.reconciled = not unresolved and (ignore_sticky_fault or not self.ledger.state("fault"))
        if self.reconciled:
            self.last_error = None
        return self.status()

    def status(self):
        return {"mode": self.config.mode, "financial_authority": self.authority(),
                "config_sha256": self.config.config_sha256,
                "reconciled": self.reconciled, "reconciled_at": self.last_reconcile,
                "last_error": self.last_error, "database_write_fault": self.io_fault,
                "account_performance_scope": "BOT_CONFIRMED_BUY_FILLS_ONLY_DEDICATED_EOA",
                "account": self.ledger.summary(), "updated_at": time.time()}

    async def manage_existing(self):
        for order in self.ledger.orders():
            plan = self.ledger.plan(order["intent_id"])
            try:
                cancel = self.reader.state("cancel_open") == "1" or not self.reader.is_active(plan["signal_id"])
            except (sqlite3.Error, OSError):
                # Lost signal custody removes new-position authority but does not
                # prevent best-effort cancellation and account reconciliation.
                cancel = True
                self.reconciled = False
                self.last_error = "SIGNAL_STATE_UNAVAILABLE_CANCEL_REQUESTED"
            cancel = cancel or order["status"] == "CANCEL_REQUESTED" or time.time() >= plan["expires"]
            if not cancel and plan["strategy"] == "MAKER":
                try:
                    await self.weather.revalidate(plan["signal"])
                    market = await self.call(self.exchange.market_snapshot, order["token"], order["condition_id"])
                    if Decimal(order["limit_price"]) * Decimal(market["max_fee_bps"]) / 10000 > Decimal(order["fee_cap"]):
                        cancel = True
                except Exception:
                    cancel = True
            if cancel and self.ledger.cancellation_requested(order["id"]):
                # A request/HTTP response alone cannot release reserved capital.
                try:
                    await self.call(self.exchange.cancel_order, order["id"])
                except Exception:
                    self.last_error = "CANCEL_REQUEST_UNCERTAIN"

    async def _checked_leg(self, leg, reference, *, maker: bool):
        snap = await self.call(self.exchange.market_snapshot, leg["token"], leg["condition"])
        tick = decimal(snap["tick_size"], "tick")
        minimum = decimal(snap["min_order_size"], "minimum")
        book = snap["book"]
        price = decimal(leg["price"], "price")
        if maker:
            if price >= decimal(book["ask"], "ask"):
                raise ExecutionError("MAKER_WOULD_CROSS")
            available = Decimal("Infinity")
        else:
            price = decimal(book["ask"], "ask")
            available = decimal(book["ask_size"], "ask_size")
        risk = self.config.risk
        if price > risk.max_price or price > decimal(reference["price"], "signal_price") + risk.max_slippage:
            raise ExecutionError("EXECUTABLE_PRICE_LIMIT")
        if price % tick:
            raise ExecutionError("OFF_TICK_PRICE")
        if time.time() - float(snap["received_at"]) > 5:
            raise ExecutionError("EXECUTABLE_SNAPSHOT_STALE")
        fee = price * decimal(snap["max_fee_bps"], "max_fee_bps") / Decimal(10000)
        if fee > risk.max_fee_per_share:
            raise ExecutionError("EXECUTABLE_FEE_LIMIT")
        return dict(leg, price=str(price), fee_cap=str(fee), available=str(available), min_size=str(minimum), tick=str(tick), neg_risk=snap["neg_risk"], exchange=snap["exchange"])

    async def execute(self, signal: dict) -> bool:
        if self.ledger.has_intent(signal["id"]):
            return False
        if not self.authority() or not self.reader.is_active(signal["id"]):
            return False
        fresh = await self.weather.revalidate(signal)
        if fresh["semantic_hash"] != signal["semantic_hash"] or fresh["strategy"] != signal["strategy"]:
            raise ExecutionError("CONTRACT_OR_STRATEGY_CHANGED")
        if signal["strategy"] not in self.config.strategies:
            raise ExecutionError("STRATEGY_NOT_CONFIGURED")
        if [(x["token"], x["condition"]) for x in fresh["legs"]] != [(x["token"], x["condition"]) for x in signal["legs"]]:
            raise ExecutionError("EXECUTION_LEGS_CHANGED")
        if time.time() >= min(signal["expires"], fresh["expires"]):
            return False
        maker = signal["strategy"] == "MAKER"
        legs = [await self._checked_leg(leg, ref, maker=maker) for leg, ref in zip(fresh["legs"], signal["legs"])]
        unit_cost = sum(Decimal(x["price"]) + Decimal(x["fee_cap"]) for x in legs)
        if signal["strategy"] == "STRUCTURAL":
            if Decimal(str(fresh["theoretical_payout"])) - unit_cost < self.config.min_structural_edge:
                raise ExecutionError("STRUCTURAL_EDGE_AFTER_FEE_BOUND")
        elif signal["strategy"] in {"SOURCE_SHOCK", "RESULT_LAG"}:
            minimum = Decimal("0.015") if signal["strategy"] == "SOURCE_SHOCK" else self.config.min_structural_edge
            if Decimal(str(fresh["theoretical_payout"])) - unit_cost < minimum:
                raise ExecutionError("INSUFFICIENT_PROVISIONAL_PAYOUT_LEFT")
        elif Decimal(str(fresh["model_frequency"])) - unit_cost < self.config.min_model_gap:
            raise ExecutionError("MODEL_GAP_AFTER_FEE_BOUND")
        risk = self.config.risk
        quantity = min(min(Decimal(x["available"]) for x in legs), min(risk.per_order / (Decimal(x["price"]) + Decimal(x["fee_cap"])) for x in legs))
        if len(legs) > 1:
            quantity = min(quantity, risk.legging_loss / unit_cost)
        # CLOB accepts at most two decimal places of share precision in this path.
        quantity = quantity.quantize(Decimal("0.01"), rounding=ROUND_FLOOR)
        if quantity <= 0 or any(quantity < Decimal(x["min_size"]) for x in legs):
            raise ExecutionError("INSUFFICIENT_LIQUIDITY_OR_MINIMUM")
        for leg in legs:
            leg["quantity"] = str(quantity)
        expires = min(signal["expires"], fresh["expires"], time.time() + 20)
        plan = {"id": digest({"wallet": self.config.wallet, "signal": signal["id"]}), "signal_id": signal["id"], "signal": signal,
                "strategy": fresh["strategy"], "station_day": fresh["station_day"], "expires": expires, "legs": legs}
        trade_after = max(0, int(self.ledger.state("trade_census_after") or self.last_reconcile) - 300)
        outstanding = self.ledger.orders()
        if outstanding:
            trade_after = min(trade_after, max(0, int(min(row["created"] for row in outstanding)) - 1))
        account = await self.call(self.exchange.account_snapshot, trade_after=trade_after)
        if account["wallet"].lower() != self.config.wallet or account["signer"].lower() != self.config.signer:
            self.fail("ACCOUNT_IDENTITY_MISMATCH")
        if account.get("openings_allowed") is not True:
            self.last_account = account
            self.reconciled = False
            raise ExecutionError("ACCOUNT_OPENINGS_RESTRICTED")
        for trade in account.get("trades", []):
            owned = trade.get("owned_order_ids")
            if not isinstance(owned, list) or not owned or any(self.ledger.order(oid) is None for oid in owned):
                self.fail("UNMANAGED_ACCOUNT_TRADE_BEFORE_SUBMISSION")
        for external in account["open_orders"]:
            local = self.ledger.order(external["id"])
            if local is None:
                self.fail("UNMANAGED_ACCOUNT_ORDER_BEFORE_SUBMISSION")
            if local["status"] in {"CANCELLED", "REJECTED"}:
                self.ledger.quarantine_reopened_order(local["id"])
                self.fail("REMOTE_TERMINAL_ORDER_REVIVED")
        for position in account["positions"]:
            if position["quantity"] != self.ledger.expected_balance(str(position["token"])):
                self.reconciled = False
                raise ExecutionError("ACCOUNT_POSITION_CHANGED_RECONCILE_BEFORE_SUBMISSION")
        listed_tokens = {str(position["token"]) for position in account["positions"]}
        for held in self.ledger.positions(held_only=True):
            if held["token"] not in listed_tokens:
                balance = await self.call(self.exchange.token_balance, held["token"])
                if balance != self.ledger.expected_balance(held["token"]):
                    self.reconciled = False
                    raise ExecutionError("ACCOUNT_POSITION_CHANGED_RECONCILE_BEFORE_SUBMISSION")
        for exchange in {leg["exchange"] for leg in legs}:
            required_allowance = sum(micros((Decimal(leg["price"]) + Decimal(leg["fee_cap"])) * quantity) for leg in legs if leg["exchange"] == exchange)
            required_allowance += self.ledger.summary()["reserved_micros"]
            if account.get("allowances", {}).get(exchange, 0) < required_allowance:
                raise ExecutionError("INSUFFICIENT_COLLATERAL_ALLOWANCE")
        if not self.authority() or not self.reader.is_active(signal["id"]):
            return False
        if not self.ledger.reserve(plan, risk, account["balance"]):
            return False
        try:
            for index, leg in enumerate(legs):
                # Revalidate per leg, including weather revisions during a basket.
                check = await self.weather.revalidate(signal)
                if check["semantic_hash"] != signal["semantic_hash"]:
                    raise ExecutionError("CONTRACT_CHANGED_DURING_SUBMISSION")
                checked = await self._checked_leg(check["legs"][index], signal["legs"][index], maker=maker)
                if Decimal(checked["price"]) > Decimal(leg["price"]) or Decimal(checked["fee_cap"]) > Decimal(leg["fee_cap"]) or Decimal(checked["available"]) < quantity:
                    raise ExecutionError("RESERVED_PLAN_NO_LONGER_EXECUTABLE")
                if not self.authority() or not self.reader.is_active(signal["id"]) or time.time() >= plan["expires"]:
                    break
                expiration = int(time.time()) + risk.max_maker_rest_seconds if maker else 0
                prepared = await self.call(self.exchange.prepare_buy, token=leg["token"], condition=leg["condition"], quantity=quantity,
                                           price=Decimal(leg["price"]), order_type="GTD" if maker else "FAK", expiration=expiration,
                                           fee_cap=Decimal(leg["fee_cap"]), post_only=maker, valid_until=plan["expires"])
                if not self.authority() or not self.reader.is_active(signal["id"]):
                    break
                self.ledger.begin_submission(plan["id"], index, prepared["order_id"], prepared["wire_hash"], prepared["payload"])
                if not self.authority() or not self.reader.is_active(signal["id"]) or time.time() >= plan["expires"]:
                    self.ledger.abort_before_post(prepared["order_id"])
                    break
                try:
                    outcome = await self.call(self.exchange.submit, prepared)
                except Exception:
                    outcome = "UNKNOWN"
                self.ledger.submission_result(prepared["order_id"], outcome)
                if outcome == "UNKNOWN":
                    self.reconciled = False
                    self.last_error = "SUBMISSION_OUTCOME_UNKNOWN"
                if outcome != "ACKNOWLEDGED":
                    break
                if index + 1 < len(legs):
                    fills = await self.call(self.exchange.confirmed_fills, prepared["order_id"], leg["token"], leg["neg_risk"])
                    for fill in fills:
                        self.ledger.record_fill(fill)
                    committed = self.ledger.order(prepared["order_id"])
                    # Full basket cost was reserved before leg one. Pending final
                    # receipts are not a profit claim and do not force a one-leg
                    # basket. Stop on known partial fill; other eligible legs can
                    # proceed within the explicitly selected full-cost policy.
                    remote = await self.call(self.exchange.get_order, prepared["order_id"])
                    if (0 < committed["matched"] < micros(quantity) or self.ledger.state("fault")
                        or remote and (remote["status"] in {"CANCELED", "CANCELLED", "EXPIRED", "INVALID"}
                                       or 0 < remote["matched"] < micros(quantity))):
                        break
            return True
        except (sqlite3.Error, OSError):
            self.io_fault = True
            self.reconciled = False
            raise
        finally:
            try:
                self.ledger.release_unsubmitted(plan["id"])
            except (sqlite3.Error, OSError):
                self.io_fault = True
                self.reconciled = False
                raise

    async def tick(self):
        try:
            await self.manage_existing()
            await self.reconcile()
            if self.authority():
                cursor = self.ledger.state("signal_cursor") or ""
                signals = self.reader.pending(after=cursor)
                if not signals:
                    signals = self.reader.pending()
                for signal in signals:
                    self.ledger.set_state("signal_cursor", signal["id"])
                    try:
                        await self.execute(signal)
                    except (ConfigurationError, ExecutionError, LedgerError):
                        self.last_error = "OPPORTUNITY_REJECTED_AT_EXECUTION"
                    if not self.authority():
                        break
        except (sqlite3.Error, OSError):
            self.io_fault = True
            self.reconciled = False
            raise
        except Exception:
            self.reconciled = False
            self.last_error = "EXECUTION_READINESS_OR_RECONCILIATION_FAILED"
        return self.status()
