"""Durable execution journal. Acknowledgements and signal receipts are not fills."""
from __future__ import annotations

from contextlib import contextmanager
from decimal import Decimal, ROUND_CEILING
import json
import os
from pathlib import Path
import sqlite3
import time

from .config import RiskLimits, canonical, decimal, digest

SCALE = Decimal(1_000_000)
UNRESOLVED = ("RESERVED", "SUBMITTING", "UNKNOWN", "ACKNOWLEDGED", "PARTIAL", "CANCEL_REQUESTED")


class LedgerError(RuntimeError):
    pass


def micros(value: object) -> int:
    return int((decimal(value, "amount", positive=False) * SCALE).to_integral_value(rounding=ROUND_CEILING))


class ExecutionLedger:
    def __init__(self, path: Path, wallet: str):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.path.is_symlink():
            raise LedgerError("EXECUTION_DB_SYMLINK")
        self.wallet = wallet.lower()
        with self.connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS execution_identity(wallet TEXT PRIMARY KEY);
                CREATE TABLE IF NOT EXISTS execution_state(key TEXT PRIMARY KEY,value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS execution_intents(
                    id TEXT PRIMARY KEY, signal_id TEXT NOT NULL UNIQUE, wallet TEXT NOT NULL,
                    strategy TEXT NOT NULL, station_day TEXT NOT NULL, plan TEXT NOT NULL,
                    plan_hash TEXT NOT NULL, created REAL NOT NULL, expires REAL NOT NULL,
                    status TEXT NOT NULL, reserved INTEGER NOT NULL CHECK(reserved>=0));
                CREATE TABLE IF NOT EXISTS execution_orders(
                    id TEXT PRIMARY KEY, intent_id TEXT NOT NULL REFERENCES execution_intents(id),
                    leg INTEGER NOT NULL, token TEXT NOT NULL, condition_id TEXT NOT NULL,
                    quantity INTEGER NOT NULL, limit_price TEXT NOT NULL, fee_cap TEXT NOT NULL,
                    status TEXT NOT NULL, reserved INTEGER NOT NULL CHECK(reserved>=0),
                    submission_hash TEXT NOT NULL, created REAL NOT NULL, updated REAL NOT NULL,
                    matched INTEGER NOT NULL DEFAULT 0, cancellation_confirmed INTEGER NOT NULL DEFAULT 0,
                    UNIQUE(intent_id,leg));
                CREATE TABLE IF NOT EXISTS execution_fills(
                    id TEXT PRIMARY KEY, order_id TEXT NOT NULL REFERENCES execution_orders(id),
                    token TEXT NOT NULL, quantity INTEGER NOT NULL CHECK(quantity>0),
                    cost INTEGER NOT NULL CHECK(cost>=0), fee INTEGER NOT NULL CHECK(fee>=0),
                    transaction_hash TEXT NOT NULL, block_hash TEXT NOT NULL,
                    log_index INTEGER NOT NULL, recorded REAL NOT NULL,
                    UNIQUE(transaction_hash,log_index));
                CREATE TABLE IF NOT EXISTS execution_settlements(
                    token TEXT PRIMARY KEY, payout TEXT NOT NULL, quantity INTEGER NOT NULL,
                    claim_value INTEGER NOT NULL, cost INTEGER NOT NULL, fees INTEGER NOT NULL,
                    proof TEXT NOT NULL, recorded REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS execution_redemptions(
                    id TEXT PRIMARY KEY, transaction_hash TEXT NOT NULL, log_index INTEGER NOT NULL,
                    condition_id TEXT NOT NULL, proceeds INTEGER NOT NULL CHECK(proceeds>=0),
                    proof TEXT NOT NULL, recorded REAL NOT NULL,
                    UNIQUE(transaction_hash,log_index));
                CREATE TABLE IF NOT EXISTS execution_burns(
                    redemption_id TEXT NOT NULL REFERENCES execution_redemptions(id), token TEXT NOT NULL,
                    quantity INTEGER NOT NULL CHECK(quantity>0), PRIMARY KEY(redemption_id,token));
                CREATE TABLE IF NOT EXISTS execution_native_gas(
                    id TEXT PRIMARY KEY, transaction_hash TEXT NOT NULL UNIQUE,
                    asset TEXT NOT NULL, amount_wei TEXT NOT NULL, proof TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS execution_audit(
                    seq INTEGER PRIMARY KEY, kind TEXT NOT NULL, identity TEXT NOT NULL,
                    data TEXT NOT NULL, created REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS execution_submissions(
                    order_id TEXT PRIMARY KEY REFERENCES execution_orders(id),
                    signed_payload TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS execution_orders_status ON execution_orders(status,id);
                CREATE INDEX IF NOT EXISTS execution_orders_token ON execution_orders(token);
                CREATE INDEX IF NOT EXISTS execution_fills_token ON execution_fills(token);
                CREATE INDEX IF NOT EXISTS execution_fills_order ON execution_fills(order_id);
                CREATE INDEX IF NOT EXISTS execution_burns_token ON execution_burns(token);
            """)
        os.chmod(self.path, 0o600)
        with self.transaction() as db:
            identities = [r[0] for r in db.execute("SELECT wallet FROM execution_identity")]
            if identities and identities != [self.wallet]:
                raise LedgerError("EXECUTION_DB_ACCOUNT_MISMATCH")
            db.execute("INSERT OR IGNORE INTO execution_identity VALUES(?)", (self.wallet,))
            for row in db.execute("SELECT id,proof FROM execution_redemptions"):
                for burn in json.loads(row["proof"])["burns"]:
                    db.execute("INSERT OR IGNORE INTO execution_burns VALUES(?,?,?)", (row["id"], str(burn["token"]), int(burn["quantity"])))
            # A process died between durable submit intent and durable response.
            self.audit(db, "STARTUP", self.wallet, {})

    def bind_adapter_identity(self, *, wallet_type: str, signer: str, signature_type: int) -> None:
        """Bind a financial journal to one signer model; never silently rotate it.

        Session-scoped CLOB history means changing the Session Key can hide the
        previous signer's orders/trades. Expiry renewal for the same signer is
        allowed because expiry is configuration authority, not journal identity.
        """
        identity = canonical({"wallet": self.wallet, "wallet_type": wallet_type,
                              "signer": signer.lower(), "signature_type": signature_type})
        with self.transaction() as db:
            row = db.execute("SELECT value FROM execution_state WHERE key='adapter_identity'").fetchone()
            if row and row[0] != identity:
                raise LedgerError("EXECUTION_ADAPTER_IDENTITY_MISMATCH")
            if row is None and (wallet_type != "EOA" or signer.lower() != self.wallet or signature_type != 0):
                # Releases before Deposit-session support could only create EOA
                # journals. Never reinterpret populated legacy state as session
                # history merely because the wallet address is unchanged.
                tables = ("execution_intents", "execution_orders", "execution_fills",
                          "execution_settlements", "execution_redemptions")
                populated = any(db.execute(f"SELECT 1 FROM {table} LIMIT 1").fetchone() for table in tables)
                prior_state = db.execute("SELECT 1 FROM execution_state WHERE key!='adapter_identity' LIMIT 1").fetchone()
                if populated or prior_state:
                    raise LedgerError("LEGACY_EOA_JOURNAL_ADAPTER_MIGRATION_REQUIRED")
            db.execute("INSERT OR IGNORE INTO execution_state VALUES('adapter_identity',?)", (identity,))

    def recover_after_restart(self):
        """Caller must hold the exclusive worker lease before recovery."""
        with self.transaction() as db:
            for row in db.execute("SELECT id FROM execution_orders WHERE status='SUBMITTING'").fetchall():
                self.audit(db, "SUBMISSION_UNKNOWN", row[0], {})
            db.execute("UPDATE execution_orders SET status='UNKNOWN' WHERE status='SUBMITTING'")
            db.execute("UPDATE execution_intents SET reserved=(SELECT COALESCE(SUM(o.reserved),0) FROM execution_orders o WHERE o.intent_id=execution_intents.id),status='RECOVERED'")
            self.audit(db, "CRASH_RECOVERY", self.wallet, {})

    def audit_fill_limits_upgrade(self) -> bool:
        """Once per policy version, audit fills written by earlier implementations.

        The completion marker, faults and cancellation queue share one commit.
        Keeping the marker after explicit operator recovery acknowledges the old
        evidence; duplicate receipt walks must not repeatedly resurrect it.
        New fills are always checked separately by record_fill.
        """
        if self.state("fill_limit_audit_version") == "1":
            return False
        breached = 0
        with self.transaction() as db:
            done = db.execute("SELECT value FROM execution_state WHERE key='fill_limit_audit_version'").fetchone()
            if done and done[0] == "1":
                return False
            rows = db.execute("""SELECT o.id,o.quantity,o.limit_price,o.fee_cap,
                SUM(f.quantity) AS filled,SUM(f.cost) AS cost,SUM(f.fee) AS fee
                FROM execution_orders o JOIN execution_fills f ON f.order_id=o.id
                GROUP BY o.id ORDER BY o.id""")
            for row in rows:
                code = None
                if row["filled"] > row["quantity"]:
                    code = "ACTUAL_QUANTITY_LIMIT_BREACH"
                if Decimal(row["cost"]) > Decimal(row["filled"]) * Decimal(row["limit_price"]) + 1:
                    code = "ACTUAL_PRICE_LIMIT_BREACH"
                if Decimal(row["fee"]) > Decimal(row["filled"]) * Decimal(row["fee_cap"]) + 1:
                    code = "ACTUAL_FEE_LIMIT_BREACH"
                if code:
                    breached += 1
                    db.execute("INSERT OR IGNORE INTO execution_state VALUES('fault',?)", (code,))
                    self.audit(db, "FILL_LIMIT_UPGRADE_BREACH", row["id"],
                               {"version": 1, "code": code, "quantity": row["filled"], "cost": row["cost"], "fee": row["fee"]})
            if breached:
                db.execute("UPDATE execution_orders SET status='CANCEL_REQUESTED',updated=? WHERE status IN ('SUBMITTING','UNKNOWN','ACKNOWLEDGED','PARTIAL')", (time.time() - 16,))
            db.execute("INSERT OR REPLACE INTO execution_state VALUES('fill_limit_audit_version','1')")
            self.audit(db, "FILL_LIMIT_UPGRADE_COMPLETE", self.wallet, {"version": 1, "breached_orders": breached})
        return bool(breached)

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=10, isolation_level=None)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("PRAGMA synchronous=FULL")
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("PRAGMA busy_timeout=10000")
        try:
            yield db
        finally:
            db.close()

    @contextmanager
    def transaction(self):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                yield db
                db.execute("COMMIT")
            except BaseException:
                db.execute("ROLLBACK")
                raise

    @staticmethod
    def audit(db, kind: str, identity: str, data: dict):
        db.execute("INSERT INTO execution_audit(kind,identity,data,created) VALUES(?,?,?,?)",
                   (kind, identity, canonical(data), time.time()))

    def fault(self, code: str):
        # Only controlled codes are passed here; exception bodies are never persisted.
        with self.transaction() as db:
            db.execute("INSERT OR REPLACE INTO execution_state VALUES('fault',?)", (code,))
            self.audit(db, "RECONCILIATION_FAULT", self.wallet, {"code": code})

    def state(self, key: str) -> str | None:
        with self.connect() as db:
            row = db.execute("SELECT value FROM execution_state WHERE key=?", (key,)).fetchone()
        return row[0] if row else None

    def reserve(self, plan: dict, limits: RiskLimits, available: int) -> bool:
        now = time.time()
        if not isinstance(plan.get("legs"), list) or not 1 <= len(plan["legs"]) <= 64:
            raise LedgerError("INTENT_LEGS_INVALID")
        for leg in plan["legs"]:
            if decimal(leg["price"], "price") > limits.max_price or decimal(leg["fee_cap"], "fee_cap", positive=False) > limits.max_fee_per_share:
                raise LedgerError("INTENT_PRICE_OR_FEE_LIMIT")
            decimal(leg["quantity"], "quantity")
        if now >= float(plan["expires"]):
            raise LedgerError("INTENT_EXPIRED")
        costs = [micros((Decimal(leg["price"]) + Decimal(leg["fee_cap"])) * Decimal(leg["quantity"])) for leg in plan["legs"]]
        total = sum(costs)
        if total <= 0 or any(c > micros(limits.per_order) for c in costs):
            raise LedgerError("PER_ORDER_LIMIT")
        if plan["strategy"] == "STRUCTURAL" and total > micros(limits.legging_loss):
            raise LedgerError("BASKET_FULL_COST_EXCEEDS_LEGGING_LOSS_LIMIT")
        with self.transaction() as db:
            if db.execute("SELECT 1 FROM execution_intents WHERE signal_id=?", (plan["signal_id"],)).fetchone():
                return False
            if db.execute("SELECT 1 FROM execution_state WHERE key='fault'").fetchone():
                raise LedgerError("RECONCILIATION_FAULT_ACTIVE")
            if db.execute("SELECT 1 FROM execution_orders WHERE status IN ('SUBMITTING','UNKNOWN','CANCEL_REQUESTED')").fetchone():
                raise LedgerError("UNRESOLVED_ORDER_AUTHORITY")
            reserved = db.execute("SELECT COALESCE(SUM(reserved),0) FROM execution_intents").fetchone()[0]
            spent = db.execute("SELECT COALESCE(SUM(cost+fee),0) FROM execution_fills f WHERE NOT EXISTS (SELECT 1 FROM execution_settlements s WHERE s.token=f.token)").fetchone()[0]
            losses = db.execute("SELECT COALESCE(SUM(MAX(0,cost+fees-claim_value)),0) FROM execution_settlements").fetchone()[0]
            daily = db.execute("SELECT COALESCE(SUM(MAX(0,cost+fees-claim_value)),0) FROM execution_settlements WHERE recorded>=?", (int(now // 86400) * 86400,)).fetchone()[0]
            open_count = db.execute("SELECT COUNT(*) FROM execution_orders WHERE status IN ('SUBMITTING','UNKNOWN','ACKNOWLEDGED','PARTIAL','CANCEL_REQUESTED')").fetchone()[0]
            for waiting in db.execute("SELECT id,plan FROM execution_intents WHERE status='RESERVED'").fetchall():
                submitted = db.execute("SELECT COUNT(*) FROM execution_orders WHERE intent_id=?", (waiting["id"],)).fetchone()[0]
                open_count += len(json.loads(waiting["plan"])["legs"]) - submitted
            station = db.execute("SELECT COALESCE(SUM(reserved),0) FROM execution_intents WHERE station_day=?", (plan["station_day"],)).fetchone()[0]
            station += db.execute("SELECT COALESCE(SUM(f.cost+f.fee),0) FROM execution_fills f JOIN execution_orders o ON o.id=f.order_id JOIN execution_intents i ON i.id=o.intent_id WHERE i.station_day=? AND NOT EXISTS (SELECT 1 FROM execution_settlements s WHERE s.token=f.token)", (plan["station_day"],)).fetchone()[0]
            if total + reserved > available:
                raise LedgerError("INSUFFICIENT_UNRESERVED_BALANCE")
            if total + reserved + spent + losses > micros(limits.capital):
                raise LedgerError("CAPITAL_LIMIT")
            if total + reserved + spent + losses > micros(limits.max_loss):
                raise LedgerError("TOTAL_WORST_CASE_LOSS_LIMIT")
            if total + reserved + spent + daily > micros(limits.daily_loss):
                raise LedgerError("DAILY_WORST_CASE_LOSS_LIMIT")
            if station + total > micros(limits.per_station_day):
                raise LedgerError("STATION_DAY_LIMIT")
            if open_count + len(plan["legs"]) > limits.max_open_orders:
                raise LedgerError("OPEN_ORDER_LIMIT")
            held = {row[0] for row in db.execute("SELECT token FROM execution_fills GROUP BY token HAVING SUM(quantity)>COALESCE((SELECT SUM(b.quantity) FROM execution_burns b WHERE b.token=execution_fills.token),0)")}
            held.update(row[0] for row in db.execute("SELECT token FROM execution_orders WHERE reserved>0"))
            held.update(leg["token"] for leg in plan["legs"])
            if len(held) > limits.max_positions:
                raise LedgerError("OPEN_POSITION_LIMIT")
            db.execute("INSERT INTO execution_intents VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                       (plan["id"], plan["signal_id"], self.wallet, plan["strategy"], plan["station_day"],
                        canonical(plan), digest(plan), now, plan["expires"], "RESERVED", total))
            self.audit(db, "CAPITAL_RESERVED", plan["id"], {"amount": total})
        return True

    def begin_submission(self, intent_id: str, leg_index: int, order_id: str, wire_hash: str, payload: dict, fee_evidence: dict | None = None):
        """This must COMMIT before a single financial POST is attempted."""
        now = time.time()
        with self.transaction() as db:
            now = time.time()  # re-read after acquiring a potentially contended writer lock
            intent = db.execute("SELECT * FROM execution_intents WHERE id=?", (intent_id,)).fetchone()
            if not intent or now >= intent["expires"] or intent["status"] != "RESERVED":
                raise LedgerError("INTENT_EXPIRED_BEFORE_SUBMISSION")
            plan = json.loads(intent["plan"])
            if digest(plan) != intent["plan_hash"]:
                raise LedgerError("INTENT_EVIDENCE_MUTATED")
            leg = plan["legs"][leg_index]
            amount = micros((Decimal(leg["price"]) + Decimal(leg["fee_cap"])) * Decimal(leg["quantity"]))
            assigned = db.execute("SELECT COALESCE(SUM(reserved),0) FROM execution_orders WHERE intent_id=?", (intent_id,)).fetchone()[0]
            if amount + assigned > intent["reserved"] or db.execute("SELECT 1 FROM execution_state WHERE key='fault'").fetchone():
                raise LedgerError("SUBMISSION_RESERVATION_OR_FAULT")
            db.execute("INSERT INTO execution_orders(id,intent_id,leg,token,condition_id,quantity,limit_price,fee_cap,status,reserved,submission_hash,created,updated) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                       (order_id, intent_id, leg_index, leg["token"], leg["condition"], micros(leg["quantity"]), leg["price"], leg["fee_cap"], "SUBMITTING", amount, wire_hash, now, now))
            db.execute("INSERT INTO execution_submissions VALUES(?,?)", (order_id, canonical(payload)))
            self.audit(db, "SUBMISSION_ARMED", order_id, {"intent": intent_id, "leg": leg_index, "wire_hash": wire_hash,
                       "fee_evidence": fee_evidence})

    def submission_result(self, order_id: str, outcome: str):
        if outcome not in {"ACKNOWLEDGED", "UNKNOWN", "REJECTED"}:
            raise LedgerError("INVALID_SUBMISSION_RESULT")
        with self.transaction() as db:
            row = db.execute("SELECT * FROM execution_orders WHERE id=?", (order_id,)).fetchone()
            if row is None or row["status"] not in {"SUBMITTING", "UNKNOWN"}:
                raise LedgerError("SUBMISSION_STATE_CONFLICT")
            db.execute("UPDATE execution_orders SET status=?,updated=? WHERE id=?", (outcome, time.time(), order_id))
            if outcome == "REJECTED":
                self._release_order(db, row)
            self.audit(db, "SUBMISSION_" + outcome, order_id, {})

    def abort_before_post(self, order_id: str):
        """Only the submitting process may call this before invoking transport."""
        with self.transaction() as db:
            row = db.execute("SELECT * FROM execution_orders WHERE id=?", (order_id,)).fetchone()
            if not row or row["status"] != "SUBMITTING":
                raise LedgerError("LOCAL_ABORT_STATE_CONFLICT")
            db.execute("UPDATE execution_orders SET status='REJECTED',updated=? WHERE id=?", (time.time(), order_id))
            self._release_order(db, row)
            self.audit(db, "LOCAL_ABORT_NO_FINANCIAL_POST", order_id, {})

    def quarantine_reopened_order(self, order_id: str):
        """Retain contradicted exchange exposure; never treat it as a new intent."""
        with self.transaction() as db:
            row = db.execute("SELECT * FROM execution_orders WHERE id=?", (order_id,)).fetchone()
            if not row:
                raise LedgerError("REOPENED_ORDER_NOT_FOUND")
            remaining = max(0, row["quantity"] - row["matched"])
            reserve = max(row["reserved"], micros(Decimal(remaining) / SCALE * (Decimal(row["limit_price"]) + Decimal(row["fee_cap"]))))
            db.execute("UPDATE execution_intents SET reserved=reserved+? WHERE id=?", (reserve - row["reserved"], row["intent_id"]))
            db.execute("UPDATE execution_orders SET status='CANCEL_REQUESTED',reserved=?,cancellation_confirmed=0,updated=? WHERE id=?", (reserve, time.time() - 16, order_id))
            db.execute("INSERT OR REPLACE INTO execution_state VALUES('fault','REMOTE_TERMINAL_ORDER_REVIVED')")
            self.audit(db, "TERMINAL_ORDER_QUARANTINED", order_id, {"previous_status": row["status"], "reserved": reserve})

    @staticmethod
    def _release_order(db, row):
        db.execute("UPDATE execution_intents SET reserved=reserved-? WHERE id=?", (row["reserved"], row["intent_id"]))
        db.execute("UPDATE execution_orders SET reserved=0 WHERE id=?", (row["id"],))

    def release_unsubmitted(self, intent_id: str):
        with self.transaction() as db:
            db.execute("UPDATE execution_intents SET reserved=(SELECT COALESCE(SUM(reserved),0) FROM execution_orders WHERE intent_id=?) WHERE id=?", (intent_id, intent_id))
            db.execute("UPDATE execution_intents SET status='SUBMISSION_CLOSED' WHERE id=?", (intent_id,))
            self.audit(db, "UNSUBMITTED_CAPITAL_RELEASED", intent_id, {})

    def orders(self, *, outstanding: bool = True) -> list[dict]:
        with self.connect() as db:
            where = "WHERE status IN ('SUBMITTING','UNKNOWN','ACKNOWLEDGED','PARTIAL','CANCEL_REQUESTED')" if outstanding else ""
            return [dict(r) for r in db.execute("SELECT * FROM execution_orders " + where + " ORDER BY created,id")]

    def has_intent(self, signal_id: str) -> bool:
        with self.connect() as db:
            return db.execute("SELECT 1 FROM execution_intents WHERE signal_id=?", (signal_id,)).fetchone() is not None

    def order(self, order_id: str):
        with self.connect() as db:
            row = db.execute("SELECT * FROM execution_orders WHERE id=?", (order_id,)).fetchone()
            return dict(row) if row else None

    def order_expiration(self, order_id: str):
        with self.connect() as db:
            row = db.execute("SELECT signed_payload FROM execution_submissions WHERE order_id=?", (order_id,)).fetchone()
        if not row:
            raise LedgerError("SIGNED_SUBMISSION_MISSING")
        return int(json.loads(row[0])["order"]["expiration"])

    def historical_orders(self, after: str, limit=5) -> list[dict]:
        with self.connect() as db:
            return [dict(row) for row in db.execute("SELECT * FROM execution_orders WHERE id>? AND status IN ('FILLED','REJECTED','CANCELLED') ORDER BY id LIMIT ?", (after, limit))]

    def token_known(self, token: str) -> bool:
        with self.connect() as db:
            return bool(db.execute("SELECT 1 FROM execution_orders WHERE token=? LIMIT 1", (token,)).fetchone())

    def order_fills(self, order_id: str):
        with self.connect() as db:
            return [dict(row) for row in db.execute("SELECT * FROM execution_fills WHERE order_id=? ORDER BY transaction_hash,log_index", (order_id,))]

    def set_state(self, key: str, value: str):
        with self.transaction() as db:
            db.execute("INSERT OR REPLACE INTO execution_state VALUES(?,?)", (key, value))

    def plan(self, intent_id: str) -> dict:
        with self.connect() as db:
            row = db.execute("SELECT plan,plan_hash FROM execution_intents WHERE id=?", (intent_id,)).fetchone()
        if not row or digest(json.loads(row[0])) != row[1]:
            raise LedgerError("INTENT_EVIDENCE_MUTATED")
        return json.loads(row[0])

    def clear_fault(self, expected: str):
        with self.transaction() as db:
            row = db.execute("SELECT value FROM execution_state WHERE key='fault'").fetchone()
            if not row or row[0] != expected:
                raise LedgerError("FAULT_CHANGED_DURING_RECOVERY")
            db.execute("DELETE FROM execution_state WHERE key='fault'")
            self.audit(db, "OPERATOR_RECONCILIATION_RECOVERY", self.wallet, {"previous_fault": expected})

    def record_redemption(self, record: dict):
        with self.transaction() as db:
            old = db.execute("SELECT * FROM execution_redemptions WHERE id=?", (record["id"],)).fetchone()
            if old:
                if old["proof"] != canonical(record["proof"]) or any(old[key] != record[key] for key in ("transaction_hash", "log_index", "condition_id", "proceeds")):
                    raise LedgerError("REDEMPTION_PROOF_MUTATED")
                return False
            if type(record["proceeds"]) is not int or record["proceeds"] < 0 or not record["proof"].get("asset") or not record["proof"].get("burns"):
                raise LedgerError("REDEMPTION_PROOF_INCOMPLETE")
            db.execute("INSERT INTO execution_redemptions VALUES(?,?,?,?,?,?,?)", (record["id"], record["transaction_hash"], record["log_index"], record["condition_id"], record["proceeds"], canonical(record["proof"]), time.time()))
            gas = record["proof"].get("native_gas")
            if gas:
                if (gas.get("asset") != "POL" or gas.get("chain_id") != 137 or gas.get("payer") != self.wallet
                    or gas.get("transaction_hash") != record["transaction_hash"] or type(gas.get("amount_wei")) is not int
                    or gas["amount_wei"] < 0):
                    raise LedgerError("REDEMPTION_GAS_PROOF_INVALID")
                previous = db.execute("SELECT proof FROM execution_native_gas WHERE id=?", (gas["id"],)).fetchone()
                if previous and previous[0] != canonical(gas):
                    raise LedgerError("REDEMPTION_GAS_PROOF_MUTATED")
                db.execute("INSERT OR IGNORE INTO execution_native_gas VALUES(?,?,?,?,?)", (gas["id"], gas["transaction_hash"], gas["asset"], str(gas["amount_wei"]), canonical(gas)))
            for burn in record["proof"]["burns"]:
                token, quantity = str(burn["token"]), int(burn["quantity"])
                acquired = db.execute("SELECT COALESCE(SUM(quantity),0) FROM execution_fills WHERE token=?", (token,)).fetchone()[0]
                prior = db.execute("SELECT COALESCE(SUM(quantity),0) FROM execution_burns WHERE token=?", (token,)).fetchone()[0]
                if quantity <= 0 or prior + quantity > acquired:
                    raise LedgerError("REDEMPTION_BURN_EXCEEDS_RECONCILED_HOLDING")
                db.execute("INSERT INTO execution_burns VALUES(?,?,?)", (record["id"], token, quantity))
            self.audit(db, "VERIFIED_REDEMPTION", record["id"], {"proceeds": record["proceeds"], "asset": record["proof"]["asset"]})
        return True

    def expected_balance(self, token: str) -> int:
        with self.connect() as db:
            quantity = db.execute("SELECT COALESCE(SUM(quantity),0) FROM execution_fills WHERE token=?", (token,)).fetchone()[0]
            quantity -= db.execute("SELECT COALESCE(SUM(quantity),0) FROM execution_burns WHERE token=?", (token,)).fetchone()[0]
            return quantity

    def record_fill(self, fill: dict):
        """Only independently validated confirmed chain events enter this method."""
        with self.transaction() as db:
            order = db.execute("SELECT * FROM execution_orders WHERE id=?", (fill["order_id"],)).fetchone()
            if not order or order["token"] != fill["token"]:
                raise LedgerError("FILL_IDENTITY_MISMATCH")
            if db.execute("SELECT 1 FROM execution_fills WHERE id=?", (fill["id"],)).fetchone():
                existing = dict(db.execute("SELECT * FROM execution_fills WHERE id=?", (fill["id"],)).fetchone())
                if any(existing[k] != fill[k] for k in ("order_id", "token", "quantity", "cost", "fee", "transaction_hash", "block_hash", "log_index")):
                    raise LedgerError("CONFIRMED_FILL_MUTATED")
                return False
            quantity, cost, fee = (fill[k] for k in ("quantity", "cost", "fee"))
            if any(type(v) is not int for v in (quantity, cost, fee)) or quantity <= 0 or cost < 0 or fee < 0:
                raise LedgerError("FILL_AMOUNTS_INVALID")
            previous = db.execute("SELECT COALESCE(SUM(quantity),0),COALESCE(SUM(cost),0),COALESCE(SUM(fee),0) FROM execution_fills WHERE order_id=?", (order["id"],)).fetchone()
            cumulative = previous[0] + quantity
            breach = None
            if cumulative > order["quantity"]:
                breach = "ACTUAL_QUANTITY_LIMIT_BREACH"
            if (Decimal(cost) > Decimal(quantity) * Decimal(order["limit_price"]) + 1
                or Decimal(previous[1] + cost) > Decimal(cumulative) * Decimal(order["limit_price"]) + 1):
                breach = "ACTUAL_PRICE_LIMIT_BREACH"
            # Record real fills even when a fee breached expectations; retain a fault.
            if (Decimal(fee) > Decimal(quantity) * Decimal(order["fee_cap"]) + 1
                or Decimal(previous[2] + fee) > Decimal(cumulative) * Decimal(order["fee_cap"]) + 1):
                breach = "ACTUAL_FEE_LIMIT_BREACH"
            if breach:
                db.execute("INSERT OR REPLACE INTO execution_state VALUES('fault',?)", (breach,))
            db.execute("INSERT INTO execution_fills VALUES(?,?,?,?,?,?,?,?,?,?)", tuple(fill[k] for k in ("id", "order_id", "token", "quantity", "cost", "fee", "transaction_hash", "block_hash", "log_index")) + (time.time(),))
            remaining = max(0, order["quantity"] - cumulative)
            new_reserved = micros(Decimal(remaining) / SCALE * (Decimal(order["limit_price"]) + Decimal(order["fee_cap"])))
            # A cancel confirmation may have released the unfilled portion already.
            new_reserved = min(order["reserved"], new_reserved)
            db.execute("UPDATE execution_intents SET reserved=reserved-? WHERE id=?", (order["reserved"] - new_reserved, order["intent_id"]))
            status = ("FILLED" if remaining == 0 else "CANCELLED" if order["cancellation_confirmed"]
                      else "CANCEL_REQUESTED" if order["status"] == "CANCEL_REQUESTED" else "PARTIAL")
            # Cancellation retry cadence measures the last request, not unrelated
            # fill arrivals; repeated partial fills must not postpone stopping it.
            updated = order["updated"] if status == "CANCEL_REQUESTED" else time.time()
            db.execute("UPDATE execution_orders SET matched=?,reserved=?,status=?,updated=? WHERE id=?", (cumulative, new_reserved, status, updated, order["id"]))
            self.audit(db, "CONFIRMED_FILL", fill["id"], {"order": order["id"], "quantity": quantity, "cost": cost, "fee": fee, "matched": cumulative, "ordered": order["quantity"]})
            if breach:
                # The fill, fault and cancellation work commit together. A crash
                # cannot preserve a breached fee while losing the stop request.
                # Existing cancellation retries retain their original cadence.
                db.execute("UPDATE execution_orders SET status='CANCEL_REQUESTED',updated=? WHERE status IN ('SUBMITTING','UNKNOWN','ACKNOWLEDGED','PARTIAL')", (time.time() - 16,))
                self.audit(db, "ACTUAL_LIMIT_BREACH_CANCEL_QUEUED", fill["id"], {"fault": breach})
            settled = db.execute("SELECT payout FROM execution_settlements WHERE token=?", (fill["token"],)).fetchone()
            if settled:
                totals = db.execute("SELECT SUM(quantity),SUM(cost),SUM(fee) FROM execution_fills WHERE token=?", (fill["token"],)).fetchone()
                db.execute("UPDATE execution_settlements SET quantity=?,claim_value=?,cost=?,fees=? WHERE token=?", (totals[0], int(Decimal(totals[0])*Decimal(settled[0])), totals[1], totals[2], fill["token"]))
                self.audit(db, "SETTLEMENT_LATE_FILL_RECONCILED", fill["id"], {})
        return True

    def cancellation_requested(self, order_id: str):
        with self.transaction() as db:
            row = db.execute("SELECT status,updated FROM execution_orders WHERE id=?", (order_id,)).fetchone()
            if row and row[0] in {"FILLED", "REJECTED", "CANCELLED"}:
                return False
            if not row:
                raise LedgerError("ORDER_NOT_FOUND")
            if row["status"] == "CANCEL_REQUESTED" and 0 <= time.time() - row["updated"] < 15:
                return False
            db.execute("UPDATE execution_orders SET status='CANCEL_REQUESTED',updated=? WHERE id=?", (time.time(), order_id))
            self.audit(db, "CANCEL_REQUESTED", order_id, {})
        return True

    def confirm_terminal(self, order_id: str, *, cancelled: bool, matched: int, exchange_status=None):
        with self.transaction() as db:
            row = db.execute("SELECT * FROM execution_orders WHERE id=?", (order_id,)).fetchone()
            if not row or matched != row["matched"]:
                raise LedgerError("TERMINAL_UNRECONCILED_FILLS")
            if matched != row["quantity"] and not cancelled:
                raise LedgerError("TERMINAL_STATUS_UNPROVEN")
            target = "FILLED" if matched == row["quantity"] else "CANCELLED"
            if row["status"] == target and row["reserved"] == 0 and row["cancellation_confirmed"] == int(cancelled):
                return
            db.execute("UPDATE execution_orders SET status=?,cancellation_confirmed=?,updated=? WHERE id=?",
                       (target, int(cancelled), time.time(), order_id))
            self._release_order(db, row)
            self.audit(db, "ORDER_TERMINAL", order_id, {"cancelled": cancelled, "matched": matched, "exchange_status": exchange_status})

    def positions(self, *, held_only=False) -> list[dict]:
        with self.connect() as db:
            where = "WHERE quantity>0" if held_only else ""
            return [dict(r) for r in db.execute("SELECT * FROM (SELECT token,SUM(quantity)-COALESCE((SELECT SUM(b.quantity) FROM execution_burns b WHERE b.token=execution_fills.token),0) quantity,SUM(quantity) acquired_quantity,SUM(cost) cost,SUM(fee) fees FROM execution_fills GROUP BY token) " + where)]

    def settle(self, token: str, payout: str, proof: dict):
        value = decimal(payout, "payout", positive=False)
        if value > 1:
            raise LedgerError("PAYOUT_INVALID")
        with self.transaction() as db:
            row = db.execute("SELECT SUM(quantity),SUM(cost),SUM(fee) FROM execution_fills WHERE token=?", (token,)).fetchone()
            if row[0] is None:
                raise LedgerError("SETTLEMENT_WITHOUT_ACTUAL_FILLS")
            old = db.execute("SELECT payout,proof FROM execution_settlements WHERE token=?", (token,)).fetchone()
            if old:
                if Decimal(old[0]) != value:
                    db.execute("INSERT OR REPLACE INTO execution_state VALUES('fault','SETTLEMENT_PAYOUT_CHANGED')")
                    db.execute("UPDATE execution_settlements SET payout=?,claim_value=?,proof=?,recorded=? WHERE token=?", (str(value), int(Decimal(row[0]) * value), canonical(proof), time.time(), token))
                    self.audit(db, "SETTLEMENT_CHAIN_CORRECTION", token, {"previous_payout": old[0], "current_payout": str(value), "previous_proof": json.loads(old[1]), "current_proof": proof})
                db.execute("DELETE FROM execution_state WHERE key=?", ("settlement_unverified:" + token,))
                return False
            if db.execute("SELECT 1 FROM execution_orders WHERE token=? AND reserved>0", (token,)).fetchone():
                raise LedgerError("SETTLEMENT_WITH_OUTSTANDING_ORDER")
            claim = int(Decimal(row[0]) * value)
            db.execute("INSERT INTO execution_settlements VALUES(?,?,?,?,?,?,?,?)", (token, str(value), *row[:1], claim, row[1], row[2], canonical(proof), time.time()))
            self.audit(db, "ACTUAL_POSITION_SETTLED_CLAIMABLE", token, {"claim_value": claim, "cash_redemption_confirmed": False})
        return True

    def dispute_missing_settlement(self, token: str):
        with self.transaction() as db:
            if db.execute("SELECT 1 FROM execution_settlements WHERE token=?", (token,)).fetchone():
                db.execute("INSERT OR REPLACE INTO execution_state VALUES('fault','SETTLEMENT_FINALITY_DISAPPEARED')")
                db.execute("INSERT OR REPLACE INTO execution_state VALUES(?, '1')", ("settlement_unverified:" + token,))
                self.audit(db, "SETTLEMENT_PROOF_UNAVAILABLE", token, {})

    def summary(self) -> dict:
        with self.connect() as db:
            fills = db.execute("SELECT COUNT(*),COALESCE(SUM(cost),0),COALESCE(SUM(fee),0) FROM execution_fills").fetchone()
            outcomes = db.execute("SELECT COUNT(*),COALESCE(SUM(claim_value-cost-fees),0) FROM execution_settlements").fetchone()
            disputed = [r[0].split(":",1)[1] for r in db.execute("SELECT key FROM execution_state WHERE key LIKE 'settlement_unverified:%'")]
            reserved = db.execute("SELECT COALESCE(SUM(reserved),0) FROM execution_intents").fetchone()[0]
            redemptions = {}
            for row in db.execute("SELECT proceeds,proof FROM execution_redemptions"):
                asset = json.loads(row[1])["asset"]
                redemptions[asset] = redemptions.get(asset, 0) + row[0]
            native_gas = {}
            for row in db.execute("SELECT asset,amount_wei FROM execution_native_gas"):
                native_gas[row[0]] = native_gas.get(row[0], 0) + int(row[1])
            gas_unknown = db.execute("SELECT COUNT(DISTINCT transaction_hash) FROM execution_redemptions WHERE json_extract(proof,'$.native_gas') IS NULL").fetchone()[0]
            recent_orders = [dict(r) for r in db.execute("SELECT id,token,status,quantity,matched,limit_price,fee_cap,reserved FROM execution_orders ORDER BY updated DESC,id LIMIT 20")]
            for order in recent_orders:
                terminal=db.execute("SELECT json_extract(data,'$.exchange_status') FROM execution_audit WHERE kind='ORDER_TERMINAL' AND identity=? ORDER BY seq DESC LIMIT 1",(order["id"],)).fetchone()
                order["exchange_terminal_status"] = terminal[0] if terminal and terminal[0]=="EXPIRED" else None
            recent_fills = [dict(r) for r in db.execute("SELECT id,order_id,token,quantity,cost,fee FROM execution_fills ORDER BY recorded DESC,id LIMIT 20")]
            claims = [dict(r) for r in db.execute("SELECT token,payout,MAX(0,quantity-COALESCE((SELECT SUM(quantity) FROM execution_burns b WHERE b.token=s.token),0)) AS held_quantity FROM execution_settlements s ORDER BY recorded DESC LIMIT 1000")]
        for claim in claims:
            claim["unredeemed_value_micros"] = int(Decimal(claim["payout"]) * claim["held_quantity"]) if claim["token"] not in disputed else None
        return {"wallet": self.wallet, "confirmed_fill_count": fills[0],
                "recent_orders": recent_orders, "recent_fills": recent_fills, "claimable_positions": claims, "actual_cost_micros": fills[1],
                "actual_fees_micros": fills[2], "settled_position_count": outcomes[0],
                "settled_position_pnl_micros": None if disputed else outcomes[1], "settlement_proof_unavailable_tokens": disputed,
                "verified_redemption_proceeds_by_asset_micros": redemptions,
                "verified_native_gas_wei": {key: str(value) for key, value in native_gas.items()},
                "redemption_transactions_without_gas_proof": gas_unknown,
                "reserved_micros": reserved, "outstanding_orders": self.orders(), "positions": self.positions(held_only=True),
                "reconciliation_fault": self.state("fault"), "signal_receipt_implies_trade": False,
                "legacy_paper_included": False}
