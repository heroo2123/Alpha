"""Executor-owned effects of bounded requests; no Telegram or signer imports."""
from dataclasses import replace
from decimal import Decimal
import json
import re
import sqlite3
import time

from .config import ConfigurationError, RiskLimits, canonical, digest
from .signals import SignalError
from .control import ControlError, ControlReader, changed_settings, initial_settings, schedule_open, validate_request


def safe_reason(exc):
    value = str(exc)
    return value if re.fullmatch(r"[A-Z][A-Z0-9_:]{0,120}", value) else "VALIDATION_FAILED"


class ExecutorControl:
    def __init__(self, engine):
        self.engine, self.base, self.ledger = engine, engine.config, engine.ledger
        self.policy = self.base.operator_control
        self.reader = ControlReader(self.policy)
        with self.ledger.connect() as db:
            db.executescript("""
            CREATE TABLE IF NOT EXISTS execution_controls(id TEXT PRIMARY KEY,seq INTEGER NOT NULL,
                result TEXT NOT NULL,created REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS execution_confirmations(id TEXT PRIMARY KEY,signal_id TEXT NOT NULL,
                signal_hash TEXT NOT NULL,revision INTEGER NOT NULL,expires REAL NOT NULL,state TEXT NOT NULL);
            """)
        identity = canonical(self.policy.identity(self.base))
        with self.ledger.transaction() as db:
            previous = self.ledger.state("control_identity")
            if previous and previous != identity:
                raise ControlError("EXECUTOR_CONTROL_IDENTITY_CHANGED_REVIEWED_MIGRATION_REQUIRED")
            db.execute("INSERT OR IGNORE INTO execution_state VALUES('control_identity',?)", (identity,))
            db.execute("INSERT OR IGNORE INTO execution_state VALUES('control_settings',?)", (canonical(initial_settings(self.base)),))
            db.execute("INSERT OR IGNORE INTO execution_state VALUES('control_seq','0')")
            db.execute("INSERT OR IGNORE INTO execution_state VALUES('control_epoch','0')")
            # A claimed attempt is never automatically retried after a crash.
            db.execute("UPDATE execution_confirmations SET state='INTERRUPTED' WHERE state='CLAIMED'")
        self.settings = json.loads(self.ledger.state("control_settings"))
        self.apply()

    def apply(self):
        s = self.settings
        self.engine.config = replace(self.base, risk=RiskLimits.parse(s["risk"]),
            strategies=frozenset(s["strategies"]), fee_policy=s["fee_policy"],
            min_model_gap=Decimal(s["min_model_gap"]), min_structural_edge=Decimal(s["min_structural_edge"]))
        self.engine.exchange.fee_policy = s["fee_policy"]

    def reason(self):
        try:
            if self.reader.state("identity", "") != canonical(self.policy.identity(self.base)):
                return "CONTROL_IDENTITY_UNAVAILABLE_OR_CHANGED"
            if self.reader.latest() != int(self.ledger.state("control_seq")):
                return "CONTROL_REQUEST_PENDING_OR_HISTORY_REGRESSED"
            if int(self.reader.state("safety_epoch")) != int(self.ledger.state("control_epoch")):
                return "IMMEDIATE_OPERATOR_PAUSE"
        except (ValueError, TypeError, OSError, sqlite3.Error):
            return "CONTROL_STATE_UNAVAILABLE"
        if time.time() >= self.policy.expires:
            return "CONTROL_AUTHORIZATION_EXPIRED"
        if self.settings["experience"] == "SIGNALS":
            return "SIGNALS_ONLY"
        if self.settings["paused"]:
            return "OPERATOR_PAUSED"
        if not schedule_open(self.settings["schedule_utc"]):
            return "OUTSIDE_APPROVED_UTC_SCHEDULE"
        return None

    def validate(self, row):
        return validate_request(row, self.base, self.settings,
            int(self.ledger.state("control_epoch")), int(self.ledger.state("control_update") or "-1"))

    def process(self):
        """No network waits. Effects, receipts, cancel intents and audit are atomic."""
        after = int(self.ledger.state("control_seq"))
        if self.reader.state("identity", "") != canonical(self.policy.identity(self.base)) or self.reader.latest() < after:
            raise ControlError("CONTROL_CUSTODY_CHANGED")
        for row in self.reader.requests(after):
            settings = dict(self.settings)
            epoch = int(self.ledger.state("control_epoch"))
            body = None
            cancel = False
            permit = None
            try:
                body = self.validate(row)
                op, data = body["operation"], body["data"]
                if op in {"PAUSE", "CANCEL", "RESUME"}:
                    if data:
                        raise ControlError("CONTROL_DATA_INVALID")
                    if op == "RESUME":
                        if settings["experience"] == "SIGNALS" or not self.engine.base_authority():
                            raise ControlError("EXISTING_ACTIVATION_AND_RECONCILIATION_REQUIRED")
                        settings["paused"] = False
                    else:
                        settings["paused"] = True
                        epoch = max(epoch,body["safety_epoch"]+1)
                        cancel = op == "CANCEL"
                    settings["revision"] += 1
                elif op == "SET":
                    if set(data) != {"key", "value"} or not isinstance(data["key"], str):
                        raise ControlError("CONTROL_SETTING_INVALID")
                    settings, grows = changed_settings(self.base, settings, data["key"], data["value"])
                    if grows and not self.engine.base_authority():
                        raise ControlError("EXISTING_ACTIVATION_AND_RECONCILIATION_REQUIRED")
                    # No old resting order survives a settings revision unchecked.
                    cancel = True
                elif op == "CONFIRM_TRADE":
                    if set(data) != {"signal_id", "signal_hash"} or any(not isinstance(x, str) or not x or len(x)>128 for x in data.values()):
                        raise ControlError("CONFIRMATION_IDENTITY_INVALID")
                    if settings["experience"] != "CONFIRM" or settings["paused"] or not self.engine.base_authority():
                        raise ControlError("CONFIRMATION_MODE_NOT_READY")
                    signal = self.engine.reader.candidate(data["signal_id"])
                    if digest(signal) != data["signal_hash"] or not self.engine.reader.is_active(signal["id"]) or time.time() >= signal["expires"]:
                        raise ControlError("CONFIRMATION_SIGNAL_CHANGED_OR_EXPIRED")
                    permit = (body["id"], signal["id"], data["signal_hash"], settings["revision"], min(body["expires"], signal["expires"]))
                else:
                    raise ControlError("CONTROL_OPERATION_UNSUPPORTED")
                if op not in {"PAUSE", "CANCEL"} and time.time() >= self.policy.expires:
                    raise ControlError("CONTROL_AUTHORIZATION_EXPIRED")
                result = "APPLIED"
            except (ConfigurationError, SignalError, ValueError, TypeError, KeyError) as exc:
                settings = dict(self.settings, paused=True, revision=self.settings["revision"] + 1)
                result = safe_reason(exc)
                permit, cancel = None, False
                epoch = int(self.reader.state("safety_epoch"))
                # Invalid requests cannot consume a later safety epoch or resume.
            with self.ledger.transaction() as db:
                db.execute("INSERT INTO execution_controls VALUES(?,?,?,?)", (row["action_id"], row["seq"], result, time.time()))
                for key, value in {"control_settings": canonical(settings), "control_seq": str(row["seq"]), "control_epoch": str(epoch)}.items():
                    db.execute("INSERT OR REPLACE INTO execution_state VALUES(?,?)", (key, value))
                if body:
                    db.execute("INSERT OR REPLACE INTO execution_state VALUES('control_update',?)", (str(body["update_id"]),))
                if permit:
                    db.execute("INSERT INTO execution_confirmations VALUES(?,?,?,?,?,'READY')", permit)
                if cancel:
                    for order in db.execute("SELECT id FROM execution_orders WHERE status NOT IN ('FILLED','CANCELLED','REJECTED')").fetchall():
                        db.execute("UPDATE execution_orders SET status='CANCEL_REQUESTED',updated=0 WHERE id=?", (order[0],))
                        self.ledger.audit(db, "CANCEL_REQUESTED", order[0], {"reason": "OPERATOR_CONTROL_REVISION"})
                self.ledger.audit(db, "OPERATOR_CONTROL", row["action_id"], {"result": result, "operation": body["operation"] if body else "INVALID", "revision": settings["revision"], "paused": settings["paused"]})
            self.settings = settings
            self.apply()

    def claim(self, signal):
        if self.settings["experience"] == "AUTOMATIC":
            return signal["expires"]
        if self.settings["experience"] != "CONFIRM":
            return None
        with self.ledger.transaction() as db:
            row = db.execute("SELECT id,expires FROM execution_confirmations WHERE signal_id=? AND signal_hash=? AND revision=? AND expires>? AND state='READY' ORDER BY expires LIMIT 1",
                (signal["id"], digest(signal), self.settings["revision"], time.time())).fetchone()
            if not row:
                return None
            # Multiple buttons authorizing the same immutable signal are one attempt.
            db.execute("UPDATE execution_confirmations SET state='CLAIMED' WHERE signal_id=? AND state='READY'", (signal["id"],))
            self.ledger.audit(db, "CONFIRMATION_ATTEMPT_CLAIMED", row[0], {"signal_id": signal["id"]})
            return row["expires"]


def rotate_authorization(config, ledger, expected_identity):
    """Local operator action after independent approval; never reachable by chat.

    Keeps the financial journal, faults and old receipts. The newly provisioned
    control database must be empty. Open orders must first reach reconciliation.
    """
    if not config.operator_control or not config.activation_requested():
        raise ControlError("NEW_CONFIGURATION_ACTIVATION_REQUIRED")
    previous = ledger.state("control_identity")
    if not previous or digest(json.loads(previous)) != expected_identity:
        raise ControlError("EXPECTED_CONTROL_IDENTITY_MISMATCH")
    reader = ControlReader(config.operator_control)
    identity = canonical(config.operator_control.identity(config))
    if reader.state("identity", "") != identity or reader.latest() != 0 or reader.state("safety_epoch") != "0":
        raise ControlError("FRESH_PREPROVISIONED_CONTROL_DATABASE_REQUIRED")
    with ledger.transaction() as db:
        if db.execute("SELECT 1 FROM execution_orders WHERE status NOT IN ('FILLED','REJECTED','CANCELLED')").fetchone():
            raise ControlError("CONTROL_ROTATION_REQUIRES_RECONCILED_TERMINAL_ORDERS")
        if db.execute("SELECT 1 FROM execution_intents WHERE reserved>0").fetchone():
            raise ControlError("CONTROL_ROTATION_HAS_RESERVED_CAPITAL")
        db.execute("UPDATE execution_confirmations SET state='REVOKED' WHERE state='READY'")
        values={"control_identity":identity,"control_settings":canonical(initial_settings(config)),"control_seq":"0","control_epoch":"0","control_update":"-1"}
        for key,value in values.items():
            db.execute("INSERT OR REPLACE INTO execution_state VALUES(?,?)",(key,value))
        ledger.audit(db,"LOCAL_CONTROL_AUTHORIZATION_ROTATED",config.wallet,{"previous_identity":json.loads(previous),"new_identity":json.loads(identity),"opening_state":"PAUSED_SIGNALS"})
