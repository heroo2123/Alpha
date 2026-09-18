"""Isolated financial worker: durable intent precedes every single submission.

Market/weather input is independently refetched by the worker. A signature never
comes from Telegram or a signal payload. Existing orders keep reconciling while
new position authority is stopped. No ambiguous submission is ever reposted.
"""
from __future__ import annotations

import asyncio
from collections import Counter
from decimal import Decimal, ROUND_FLOOR
import json
import sqlite3
import time

from .config import (ProductionConfig, ConfigurationError, decimal, digest,
                     SESSION_OPENING_SAFETY_SECONDS)
from .ledger import ExecutionLedger, LedgerError, micros, SCALE
from .fees import fee_requirement
from .chain import ExchangeError, SubmissionNotAttempted, address, hash32, uint


class ExecutionError(RuntimeError):
    pass


class ExecutionEngine:
    def __init__(self, config: ProductionConfig, ledger: ExecutionLedger, reader, exchange, weather):
        if config.mode != "LIVE_EXECUTION" or config.risk is None:
            raise ConfigurationError("EXECUTION_MODE_REQUIRED")
        self.config, self.ledger, self.reader = config, ledger, reader
        self.ledger.bind_adapter_identity(wallet_type=config.wallet_type, signer=config.signer,
                                          signature_type=config.signature_type,
                                          deposit_owner=config.deposit_owner, signer_type=config.signer_type)
        self.exchange, self.weather = exchange, weather
        from .redemption_audit import RedemptionAuditor
        self.redemption_auditor = RedemptionAuditor(ledger, exchange)
        self.reconciled = False
        self.account_reconciled = False
        self.funding_ready = False
        self.io_fault = False
        self.storage_health = None
        self.last_account = None
        self.last_reconcile = 0.0
        self.last_reconcile_monotonic = None
        self.last_error = None
        self.control = None
        self._attempt_revision = None
        self._attempt_deadline = None
        if config.operator_control:
            from .executor_control import ExecutorControl
            self.control = ExecutorControl(self)

    def authority(self) -> bool:
        if self.control:
            if self._attempt_deadline is not None and time.time() >= self._attempt_deadline:
                return False
            if self.control.reason():
                return False
            if self._attempt_revision is not None and self._attempt_revision != self.control.settings["revision"]:
                return False
        return self.base_authority()

    def base_authority(self) -> bool:
        return self.base_authority_reason() is None

    def _storage_admission_reason(self):
        from .storage_health import check_storage
        paths = {"execution": self.config.execution_db.parent,
                 "signals": self.config.signal_db.parent}
        if self.config.operator_control:
            paths["scanner"] = self.config.operator_control.scanner_db.parent
        self.storage_health = check_storage(paths)
        return self.storage_health["reason"]

    def base_authority_reason(self):
        try:
            stopped = self.reader.state("stop_opening") == "1"
        except (sqlite3.Error, OSError):
            self.last_error = "SIGNAL_STATE_UNAVAILABLE"
            return self.last_error
        if stopped: return "LEGACY_OPERATOR_STOP_REQUIRES_LOCAL_RECOVERY"
        if self.config.stop_file and self.config.stop_file.exists(): return "LOCAL_EMERGENCY_STOP_FILE"
        if self.config.is_deposit_owner:
            if time.time() >= self.config.deposit_opening_cutoff:
                return "DEDICATED_OWNER_EXCLUSIVITY_EXPIRED_OR_NEAR_EXPIRY"
        elif self.config.wallet_type == "DEPOSIT_WALLET":
            now = time.time()
            if now >= float(self.config.session_valid_until) - SESSION_OPENING_SAFETY_SECONDS:
                return "SESSION_AUTHORIZATION_EXPIRED_OR_NEAR_EXPIRY"
            if now >= float(self.config.session_exclusive_until):
                return "DEDICATED_SESSION_EXCLUSIVITY_EXPIRED"
            if now >= float(self.config.session_exclusive_until) - SESSION_OPENING_SAFETY_SECONDS:
                return "DEDICATED_SESSION_EXCLUSIVITY_EXPIRED_OR_NEAR_EXPIRY"
        storage_reason = self._storage_admission_reason()
        if storage_reason:
            return storage_reason
        if self.io_fault: return "EXECUTION_DATABASE_WRITE_FAULT"
        if self.ledger.state("fault"): return "UNRESOLVED_RECONCILIATION_FAULT"
        if not self.redemption_auditor.ready(): return "REDEMPTION_PROOF_REVALIDATION_REQUIRED"
        if not self.config.activation_requested(): return "CONFIGURATION_BOUND_ACTIVATION_MISSING_OR_INVALID"
        if not self.reconciled or not 0 <= time.time()-self.last_reconcile < 30: return "FRESH_ACCOUNT_RECONCILIATION_REQUIRED"
        if not self.last_account or self.last_account.get("openings_allowed") is not True: return "ACCOUNT_OPENING_ELIGIBILITY_NOT_READY"
        return None

    async def call(self, method, *args, **kwargs):
        return await asyncio.to_thread(method, *args, **kwargs)

    def _require_submission_authority(self, signal_id: str, expires: float):
        """Run in the submit worker immediately before its transport invocation.

        No network or writer lock follows these local checks. A request can still
        arrive after POST begins; this is not atomic exclusion of remote actors.
        """
        if not self.authority() or not self.reader.is_active(signal_id) or time.time() >= expires:
            raise SubmissionNotAttempted("LOCAL_AUTHORITY_CHANGED_BEFORE_POST")
        # Authority reads may wait for filesystem/SQLite work. Evaluate the
        # temporal grants again after those reads, using one current timestamp.
        now = time.time()
        if self._attempt_deadline is not None and now >= self._attempt_deadline:
            raise SubmissionNotAttempted("CONFIRMATION_EXPIRED_BEFORE_POST")
        if self.control:
            from .control import schedule_open
            if now >= self.control.policy.expires or not schedule_open(self.control.settings["schedule_utc"]):
                raise SubmissionNotAttempted("CONTROL_AUTHORITY_EXPIRED_BEFORE_POST")
        if self.config.wallet_type == "DEPOSIT_WALLET" and now >= self.config.deposit_opening_cutoff:
            raise SubmissionNotAttempted("SESSION_OPENING_WINDOW_CLOSED")
        if not 0 <= now - self.last_reconcile < 30:
            raise SubmissionNotAttempted("FRESH_ACCOUNT_RECONCILIATION_REQUIRED")

    def _validate_account_identity(self, account):
        try:
            valid = (isinstance(account, dict) and address(account.get("wallet")) == self.config.wallet
                     and address(account.get("signer")) == self.config.signer)
        except ExchangeError:
            valid = False
        if not valid:
            self.fail("ACCOUNT_IDENTITY_MISMATCH")

    def fail(self, code: str):
        self.reconciled = False
        self.account_reconciled = False
        self.last_error = code
        try:
            self.ledger.fault(code)
        except (OSError, sqlite3.Error):
            self.io_fault = True
        raise ExecutionError(code)

    def _validate_deposit_session_account(self, account: dict) -> None:
        if self.config.wallet_type != "DEPOSIT_WALLET":
            return
        expected_visibility = "OWNER_SIGNER_ONLY" if self.config.is_deposit_owner else "SESSION_SIGNER_ONLY"
        actual_signer_type = account.get("signer_type", "SESSION_KEY" if not self.config.is_deposit_owner else None)
        if actual_signer_type != self.config.signer_type:
            self.fail("DEPOSIT_OWNER_ACCOUNT_SIGNER_TYPE_MISMATCH" if self.config.is_deposit_owner
                      else "SESSION_ACCOUNT_VISIBILITY_MISMATCH")
        if (account.get("wallet_type") != "DEPOSIT_WALLET" or type(account.get("signature_type")) is not int
            or account.get("signature_type") != 3
            or account.get("order_visibility") != expected_visibility):
            self.fail("SESSION_ACCOUNT_VISIBILITY_MISMATCH")
        if (not isinstance(account.get("deposit_owner"), str)
            or account["deposit_owner"].lower() != self.config.deposit_owner):
            self.fail("DEPOSIT_OWNER_ACCOUNT_MISMATCH")
        activity = account.get("wallet_activity")
        session_history = account.get("wallet_activity_session_trades")
        if (not isinstance(activity, list) or not isinstance(session_history, list)
            or account.get("wallet_activity_after") is None or account.get("wallet_activity_before") is None):
            self.fail("WALLET_WIDE_ACTIVITY_WITNESS_MISSING")

        after, before = account["wallet_activity_after"], account["wallet_activity_before"]
        if (type(after) is not int or type(before) is not int or not 1 <= after < before
            or len(activity) > 10_000 or len(session_history) > 10_000):
            self.fail("WALLET_WIDE_ACTIVITY_WITNESS_INVALID")
        try:
            for rows, session in ((activity, False), (session_history, True)):
                for row in rows:
                    if not isinstance(row, dict) or row.get("side") not in {"BUY", "SELL"}:
                        raise ValueError
                    hash32(row.get("condition"))
                    token = row.get("token")
                    if not isinstance(token, str) or str(uint(token)) != token or uint(token) == 0:
                        raise ValueError
                    quantity = row.get("wallet_quantity" if session else "quantity")
                    if type(quantity) is not int or not 0 < quantity < 2**256:
                        raise ValueError
                    when = row.get("matched_at" if session else "timestamp")
                    if type(when) is not int or not after <= when <= before:
                        raise ValueError
                    status = row.get("status") if session else "CONFIRMED"
                    if status not in {"MATCHED", "MATCHED_NOT_BROADCASTED", "MINED", "CONFIRMED", "RETRYING", "FAILED"}:
                        raise ValueError
                    if status == "CONFIRMED":
                        hash32(row.get("transaction_hash"))
        except (ExchangeError, ValueError, TypeError):
            self.fail("WALLET_WIDE_ACTIVITY_WITNESS_INVALID")

        def activity_key(row):
            return (row.get("transaction_hash"), row.get("condition"), row.get("token"), row.get("side"))

        confirmed_session = [row for row in session_history if row.get("status") == "CONFIRMED"]
        if (any(type(row.get("quantity")) is not int or row["quantity"] <= 0 for row in activity)
            or any(type(row.get("wallet_quantity")) is not int or row["wallet_quantity"] <= 0
                   for row in confirmed_session)):
            self.fail("WALLET_WIDE_ACTIVITY_WITNESS_INVALID")
        public_counts = Counter(activity_key(row) for row in activity)
        session_counts = Counter(activity_key(row) for row in confirmed_session)
        public_quantity, session_quantity = Counter(), Counter()
        for row in activity:
            public_quantity[activity_key(row)] += row["quantity"]
        for row in confirmed_session:
            session_quantity[activity_key(row)] += row["wallet_quantity"]
        if any(key[0] is None or public_counts[key] > session_counts[key]
               or public_quantity[key] > session_quantity[key] for key in public_counts):
            self.fail("EXTERNAL_WALLET_TRADE_ACTIVITY")

    async def reconcile(self, *, ignore_sticky_fault=False, allow_exchange_mutation=True):
        """A failed comparison stops opening; confirmed fills remain append-only."""
        started = time.time()
        self.last_reconcile_monotonic = time.monotonic()
        self.reconciled = False
        self.account_reconciled = False
        self.funding_ready = False
        management_required = False
        redemption_ready = await self.redemption_auditor.check_batch(self.call)
        if self.ledger.audit_fill_limits_upgrade():
            if allow_exchange_mutation:
                await self.manage_existing()
            else:
                management_required = True
        hot = self.ledger.orders()
        watermark = self.ledger.state("trade_census_after")
        trade_after = int(watermark) - 300 if watermark else None
        if trade_after is not None and hot:
            trade_after = min(trade_after, int(min(row["created"] for row in hot)) - 1)
        account = await self.call(self.exchange.account_snapshot, trade_after=max(0, trade_after) if trade_after is not None else None)
        self._validate_account_identity(account)
        self._validate_deposit_session_account(account)
        minimum_funding = micros(self.config.risk.per_order)
        allowance_ready = bool(account.get("allowances") and max(account["allowances"].values()) >= minimum_funding)
        balance_ready = type(account.get("balance")) is int and account["balance"] >= minimum_funding
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
        self.funding_ready = allowance_ready and balance_ready
        unresolved = False
        if not allowance_ready:
            self.last_error = "COLLATERAL_ALLOWANCE_READINESS_FAILED"
        elif not balance_ready:
            self.last_error = "COLLATERAL_BALANCE_READINESS_FAILED"
        for order in known.values():
            plan = self.ledger.plan(order["intent_id"])
            leg = plan["legs"][order["leg"]]
            # Also check terminal orders: an indexer-delayed fill must survive a
            # cancellation race, and a previously confirmed receipt may reorganize.
            trades = account.get("trades")
            if trade_after is not None and order["created"] < trade_after:
                trades = await self.call(self.exchange.account_trades, token=order["token"], after=max(0, int(order["created"]) - 300), before=max(int(order["created"]) + 60, self.ledger.order_expiration(order["id"])) + 300)
            fills = await self.call(self.exchange.confirmed_fills, order["id"], order["token"], leg["neg_risk"], trades=trades, recorded_fills=self.ledger.order_fills(order["id"]))
            new_fill = False
            for fill in fills:
                new_fill = self.ledger.record_fill(fill) or new_fill
            if new_fill and self.ledger.state("fault"):
                if allow_exchange_mutation:
                    await self.manage_existing()
                else:
                    management_required = True
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
                self.ledger.confirm_terminal(order["id"], cancelled=True, matched=matched, exchange_status=remote["status"])
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
        unresolved = (unresolved or management_required or not redemption_ready
                      or not self.redemption_auditor.ready()
                      or bool(self.ledger.summary()["settlement_proof_unavailable_tokens"]))
        if not redemption_ready:
            self.last_error = "REDEMPTION_PROOF_REVALIDATION_REQUIRED"
        if management_required:
            self.last_error = "PREFLIGHT_ORDER_MANAGEMENT_REQUIRED"
        self.account_reconciled = not unresolved and (ignore_sticky_fault or not self.ledger.state("fault"))
        self.reconciled = self.account_reconciled and self.funding_ready
        if self.reconciled:
            self.last_error = None
        return self.status()

    async def validate_preflight_completion(self, *, allow_unfunded=False):
        """Read-only end-of-preflight gate; never an activation or order attempt.

        Reconciliation may wait on many receipts. Its opening-eligibility flag
        is an observation, not a lease that survives time or authorization drift.
        A failed completion invalidates readiness but never erases journal data.
        """
        from .executor_control import safe_reason
        try:
            ready = self.account_reconciled if allow_unfunded else self.reconciled
            if not ready:
                raise ConfigurationError("PREFLIGHT_RECONCILIATION_INCOMPLETE")
            if not self.last_account or self.last_account.get("openings_allowed") is not True:
                raise ConfigurationError("PREFLIGHT_ACCOUNT_OPENINGS_RESTRICTED")
            started, monotonic_started = time.time(), time.monotonic()
            current = await self.call(self.exchange.eligibility, require_opening=False)
            if not isinstance(current, dict) or current.get("openings_allowed") is not True:
                raise ConfigurationError("PREFLIGHT_ACCOUNT_OPENINGS_RESTRICTED")
            # Config custody is independent of activation: an unfunded account
            # is expected to have no initial financial activation at all.
            if self.config.source_path is not None:
                actual = json.loads(self.config.source_path.read_text())
                if digest(actual) != self.config.config_sha256:
                    raise ConfigurationError("PREFLIGHT_CONFIGURATION_CHANGED")
            storage_reason = self._storage_admission_reason()
            if storage_reason:
                raise ConfigurationError(storage_reason)
            now, monotonic_now = time.time(), time.monotonic()
            if (not 0 <= now - started <= 15 or not 0 <= now - self.last_reconcile < 30
                or not 0 <= monotonic_now - monotonic_started <= 15
                or self.last_reconcile_monotonic is None
                or not 0 <= monotonic_now - self.last_reconcile_monotonic < 30):
                raise ConfigurationError("PREFLIGHT_EVIDENCE_STALE")
            if self.config.wallet_type == "DEPOSIT_WALLET":
                if now >= self.config.deposit_opening_cutoff:
                    raise ConfigurationError("PREFLIGHT_SESSION_WINDOW_CLOSED")
            self.last_account = dict(self.last_account, eligibility=current,
                                     openings_allowed=True,
                                     opening_restrictions=current.get("opening_restrictions", []))
        except Exception as exc:
            self.reconciled = self.account_reconciled = False
            self.last_error = safe_reason(exc)
            if isinstance(exc, ConfigurationError):
                raise
            raise ConfigurationError("PREFLIGHT_COMPLETION_FAILED_CLOSED") from None

    def status(self):
        from .io import release_identity
        events = None
        if self.control:
            from .notifications import event_batch
            try:
                events = event_batch(self.ledger, int(self.control.reader.state("notification_cursor")))
            except (ValueError, OSError, sqlite3.Error):
                pass
        return {"mode": self.config.mode, "release": release_identity(), "financial_authority": self.authority(),
                "config_sha256": self.config.config_sha256,
                "fee_policy": self.config.fee_policy,
                "fee_limit_scope": "LOCAL_SUBMISSION_CHECK_AND_RESERVATION_NOT_SIGNED_EXCHANGE_CAP",
                "reconciled": self.reconciled, "account_reconciled": self.account_reconciled,
                "funding_ready": self.funding_ready, "reconciled_at": self.last_reconcile,
                "last_error": self.last_error, "database_write_fault": self.io_fault,
                "storage_health": self.storage_health,
                "opening_disabled_reason": (self.control.reason() if self.control else None) or
                    self.base_authority_reason(),
                "control": dict(self.control.settings, authorization_version=self.control.policy.authorization_version,
                    authorization_expires_at=self.control.policy.expires,
                    processed_seq=int(self.ledger.state("control_seq"))) if self.control else None,
                "balance_micros": self.last_account.get("balance") if self.last_account else None,
                "account_performance_scope": (
                    "BOT_CONFIRMED_OWNER_FILLS_ONLY_DEDICATED_DEPOSIT_WALLET"
                    if self.config.is_deposit_owner else
                    "BOT_CONFIRMED_SESSION_FILLS_ONLY_DEDICATED_DEPOSIT_WALLET"
                    if self.config.wallet_type == "DEPOSIT_WALLET"
                    else "BOT_CONFIRMED_BUY_FILLS_ONLY_DEDICATED_EOA"),
                "signing_authority": (
                    "DEPOSIT_WALLET_FULL_OWNER_KEY_NOT_WITHDRAWAL_RESTRICTED"
                    if self.config.is_deposit_owner else
                    "DEPOSIT_WALLET_SESSION_KEY_OWNER_KEY_OFF_HOST"
                    if self.config.wallet_type == "DEPOSIT_WALLET"
                    else "EOA_FULL_KEY_APPLICATION_BUY_ONLY_NOT_WITHDRAWAL_RESTRICTED"),
                "wallet_type": self.config.wallet_type, "signature_type": self.config.signature_type,
                "session_scopes": list(self.config.session_scopes),
                "session_valid_until": self.config.session_valid_until,
                "session_exclusive_until": self.config.session_exclusive_until,
                "deposit_owner": self.config.deposit_owner, "signer_type": self.config.signer_type,
                "wallet_exclusive_until": self.config.wallet_exclusive_until,
                "account": self.ledger.summary(), "notification_batch": events, "updated_at": time.time()}

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
                    if fee_requirement(market, Decimal(order["limit_price"]), post_only=True,
                                       expected_policy=self.config.fee_policy) > Decimal(order["fee_cap"]):
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
        required_fee = fee_requirement(snap, price, post_only=maker, expected_policy=self.config.fee_policy)
        if required_fee > risk.max_fee_per_share:
            raise ExecutionError("EXECUTABLE_FEE_LIMIT")
        # Published fees are mutable venue policy. Reserve the operator's entire
        # allowance, never label the observed schedule as an exchange-enforced cap.
        fee = risk.max_fee_per_share if self.config.fee_policy == "EXCHANGE_PUBLISHED_SCHEDULE" else required_fee
        return dict(leg, price=str(price), fee_cap=str(fee), observed_fee_requirement=str(required_fee),
                    fee_policy=self.config.fee_policy, fee_evidence=snap["fee_evidence"],
                    available=str(available), min_size=str(minimum), tick=str(tick), neg_risk=snap["neg_risk"], exchange=snap["exchange"])

    async def execute(self, signal: dict) -> bool:
        if self.control:
            if not self.authority():
                return False
            self._attempt_deadline = self.control.claim(signal)
            if self._attempt_deadline is None:
                return False
            self._attempt_revision = self.control.settings["revision"]
        try:
            return await self._execute(signal)
        finally:
            self._attempt_revision = None
            self._attempt_deadline = None

    async def _execute(self, signal: dict) -> bool:
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
        # Public interfaces use ambiguous share/notional minimum terminology.
        # Our supported subset must meet both, including passive maker orders.
        if any(quantity * Decimal(x["price"]) < Decimal(x["min_size"]) for x in legs):
            raise ExecutionError("BUY_NOTIONAL_BELOW_CONSERVATIVE_MINIMUM")
        for leg in legs:
            leg["quantity"] = str(quantity)
        expires = min(signal["expires"], fresh["expires"], time.time() + 20)
        plan = {"id": digest({"wallet": self.config.wallet, "signal": signal["id"]}), "signal_id": signal["id"], "signal": signal,
                "strategy": fresh["strategy"], "station_day": fresh["station_day"], "expires": expires, "legs": legs}
        if self.control:
            plan["control_revision"] = self._attempt_revision
            plan["authorization_version"] = self.control.policy.authorization_version
        trade_after = max(0, int(self.ledger.state("trade_census_after") or self.last_reconcile) - 300)
        outstanding = self.ledger.orders()
        if outstanding:
            trade_after = min(trade_after, max(0, int(min(row["created"] for row in outstanding)) - 1))
        account = await self.call(self.exchange.account_snapshot, trade_after=trade_after)
        self._validate_account_identity(account)
        # Repeat the complete Deposit-wallet isolation witness on the final account
        # snapshot. An external/manual trade appearing after periodic reconciliation
        # must block this submission, not merely the next reconciliation cycle.
        self._validate_deposit_session_account(account)
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
                if self.config.wallet_type == "DEPOSIT_WALLET":
                    try:
                        guard = (self.exchange.owner_opening_restriction if self.config.is_deposit_owner
                                 else self.exchange.session_opening_restriction)
                        session_restriction = await self.call(guard)
                    except Exception:
                        session_restriction = ("DEPOSIT_OWNER_AUTHORITY_UNKNOWN" if self.config.is_deposit_owner
                                               else "SESSION_AUTHORIZATION_ONCHAIN_UNKNOWN")
                    if session_restriction:
                        self.reconciled = False
                        self.last_error = session_restriction
                        raise ExecutionError(session_restriction)
                if not self.authority() or not self.reader.is_active(signal["id"]):
                    break
                self.ledger.begin_submission(plan["id"], index, prepared["order_id"], prepared["wire_hash"], prepared["payload"], prepared["fee_evidence"])
                if not self.authority() or not self.reader.is_active(signal["id"]) or time.time() >= plan["expires"]:
                    self.ledger.abort_before_post(prepared["order_id"])
                    break
                try:
                    outcome = await self.call(self.exchange.submit, prepared,
                        before_post=lambda: self._require_submission_authority(signal["id"], plan["expires"]))
                except SubmissionNotAttempted as exc:
                    # The transport has not been invoked. Preserve the consumed
                    # intent/confirmation, but do not invent an UNKNOWN order.
                    self.ledger.abort_before_post(prepared["order_id"])
                    self.last_error = exc.code
                    self.reconciled = False
                    break
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
                    new_fill = False
                    for fill in fills:
                        new_fill = self.ledger.record_fill(fill) or new_fill
                    if new_fill and self.ledger.state("fault"):
                        await self.manage_existing()
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

    async def safety_tick(self):
        """Requests/cancellations do not wait for discovery, reconciliation or Telegram."""
        if not self.control:
            return
        try:
            local_stop = "LOCAL_EMERGENCY_STOP_FILE" if self.config.stop_file and self.config.stop_file.exists() else ""
            if local_stop != self.ledger.state("observed_local_stop"):
                with self.ledger.transaction() as db:
                    db.execute("INSERT OR REPLACE INTO execution_state VALUES('observed_local_stop',?)",(local_stop,))
                    if local_stop:
                        self.ledger.audit(db,"EMERGENCY_PAUSE",self.config.wallet,{"reason":local_stop})
            self.control.process()
            for order in self.ledger.orders():
                if order["status"] == "CANCEL_REQUESTED" and self.ledger.cancellation_requested(order["id"]):
                    try:
                        await self.call(self.exchange.cancel_order, order["id"])
                    except Exception:
                        self.last_error = "CANCEL_REQUEST_UNCERTAIN"
        except (sqlite3.Error, OSError):
            self.io_fault = True
            self.reconciled = False
            raise
        except Exception:
            self.reconciled = False
            self.last_error = "CONTROL_REQUEST_FAILED_CLOSED"

    async def tick(self):
        try:
            await self.safety_tick()
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
                    except (ConfigurationError, ExecutionError, LedgerError) as exc:
                        from .executor_control import safe_reason
                        self.last_error = safe_reason(exc)
                        with self.ledger.transaction() as db:
                            if not db.execute("SELECT 1 FROM execution_audit WHERE kind='OPPORTUNITY_REJECTED' AND identity=? AND json_extract(data,'$.reason')=? LIMIT 1", (signal["id"], self.last_error)).fetchone():
                                self.ledger.audit(db, "OPPORTUNITY_REJECTED", signal["id"], {"reason": self.last_error})
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
