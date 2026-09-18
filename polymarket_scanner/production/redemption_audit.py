"""Bounded read-only revalidation of previously imported cash receipts.

A fresh engine epoch requires every historical import to be checked again.
Each reconciliation checks at most two receipts; incomplete startup scans and
stale/missing/changed proof close openings. Original records are never rewritten.
"""
from __future__ import annotations

import json
import time
import uuid

from .config import canonical, digest

REDEMPTION_AUDIT_TTL = 600
REDEMPTION_AUDIT_BATCH = 2


class RedemptionAuditor:
    def __init__(self, ledger, exchange):
        self.ledger, self.exchange = ledger, exchange
        self.epoch = uuid.uuid4().hex
        ledger.set_state("redemption_audit_epoch", self.epoch)

    def ready(self):
        now = time.time()
        with self.ledger.connect() as db:
            current = db.execute("SELECT value FROM execution_state WHERE key='redemption_audit_epoch'").fetchone()
            if not current or current[0] != self.epoch:
                return False
            bad = db.execute("""SELECT 1 FROM execution_redemptions r
                LEFT JOIN execution_redemption_validation v ON v.id=r.id
                WHERE v.id IS NULL OR v.epoch!=? OR v.status!='VERIFIED'
                   OR v.checked>? OR v.checked<=? LIMIT 1""",
                (self.epoch, now, now-REDEMPTION_AUDIT_TTL)).fetchone()
        return bad is None

    async def check_batch(self, call):
        with self.ledger.connect() as db:
            rows = [dict(r) for r in db.execute("""SELECT r.*
                FROM execution_redemptions r
                LEFT JOIN execution_redemption_validation v ON v.id=r.id
                ORDER BY CASE WHEN v.epoch=? THEN 1 ELSE 0 END,
                    COALESCE(v.checked,0),r.id LIMIT ?""",
                (self.epoch, REDEMPTION_AUDIT_BATCH))]
        for previous in rows:
            state, reason = "UNAVAILABLE", "REDEMPTION_PROOF_UNAVAILABLE"
            expected = json.loads(previous["proof"])
            try:
                records = await call(self.exchange.redemption_receipt,
                    previous["transaction_hash"], previous["condition_id"])
                if not isinstance(records, list) or len(records) > 2:
                    raise ValueError("unsupported receipt response")
                matches = [r for r in records if isinstance(r, dict) and r.get("id") == previous["id"]]
                if len(matches) == 1:
                    found = matches[0]
                    exact = all(type(found.get(k)) is type(previous[k]) and found.get(k) == previous[k] for k in
                        ("id", "transaction_hash", "log_index", "condition_id", "proceeds"))
                    exact = exact and len(records) == 1 and canonical(found.get("proof")) == canonical(expected)
                    exact = exact and canonical(found.get("burns")) == canonical(expected.get("burns"))
                    state, reason = ("VERIFIED", None) if exact else ("CHANGED", "REDEMPTION_PROOF_CHANGED")
                elif records:
                    state, reason = "CHANGED", "REDEMPTION_PROOF_CHANGED"
            except Exception:
                state, reason = "UNAVAILABLE", "REDEMPTION_PROOF_READ_FAILED"
            with self.ledger.transaction() as db:
                prior = db.execute("SELECT status FROM execution_redemption_validation WHERE id=?",
                                   (previous["id"],)).fetchone()
                db.execute("INSERT OR REPLACE INTO execution_redemption_validation VALUES(?,?,?,?,?,?)",
                    (previous["id"], self.epoch, time.time(), state, reason, digest(expected)))
                if state == "CHANGED":
                    db.execute("INSERT OR IGNORE INTO execution_state VALUES('fault','REDEMPTION_PROOF_CHANGED')")
                if not prior or prior[0] != state:
                    self.ledger.audit(db, "REDEMPTION_PROOF_STATE", previous["id"],
                                      {"status": state, "reason": reason})
        return self.ready()
