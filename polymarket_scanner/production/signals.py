"""Signal observations and operator synchronization; never an account ledger."""
from __future__ import annotations

from contextlib import contextmanager
import json
import os
from pathlib import Path
import sqlite3
import time

from .config import canonical, digest

TERMINAL = {"EXPIRED", "INVALIDATED", "SETTLED"}


class SignalError(RuntimeError):
    pass


class SignalStore:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.path.is_symlink():
            raise SignalError("SIGNAL_DB_SYMLINK")
        if self.path.exists():
            with sqlite3.connect(self.path.resolve().as_uri() + "?mode=ro", uri=True) as existing:
                if existing.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name IN ('weather_paper_signals','weather_maker_shadow_orders')").fetchone():
                    raise SignalError("LEGACY_DATABASE_IMPORT_REQUIRED:select_fresh_signal_db_and_run_import-legacy")
        with self.connect() as db:
            db.executescript("""
            CREATE TABLE IF NOT EXISTS live_signals(
              id TEXT PRIMARY KEY, dedupe_key TEXT NOT NULL UNIQUE,
              evidence TEXT NOT NULL, evidence_hash TEXT NOT NULL,
              created REAL NOT NULL, expires REAL NOT NULL,
              status TEXT NOT NULL, reason TEXT, delivery TEXT NOT NULL,
              message_id INTEGER, outcome TEXT, outcome_checked REAL NOT NULL DEFAULT 0);
            CREATE TABLE IF NOT EXISTS live_sync(
              signal_id TEXT PRIMARY KEY REFERENCES live_signals(id),
              attempts INTEGER NOT NULL DEFAULT 0, due REAL NOT NULL,
              state TEXT NOT NULL DEFAULT 'PENDING', error TEXT);
            CREATE TABLE IF NOT EXISTS live_state(key TEXT PRIMARY KEY,value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS live_audit(
              seq INTEGER PRIMARY KEY, kind TEXT NOT NULL, identity TEXT NOT NULL,
              data TEXT NOT NULL, created REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS live_commands(
              update_id INTEGER PRIMARY KEY, actor TEXT NOT NULL,
              command TEXT NOT NULL, created REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS live_legacy_research(
              source_id TEXT NOT NULL, table_name TEXT NOT NULL, row_id TEXT NOT NULL,
              evidence TEXT NOT NULL, evidence_hash TEXT NOT NULL,
              classification TEXT NOT NULL DEFAULT 'EXCLUDED_LEGACY_RESEARCH',
              PRIMARY KEY(source_id,table_name,row_id));
            CREATE TRIGGER IF NOT EXISTS immutable_legacy_evidence
              BEFORE UPDATE ON live_legacy_research
              BEGIN SELECT RAISE(ABORT,'IMMUTABLE_LEGACY_EVIDENCE'); END;
            CREATE TRIGGER IF NOT EXISTS immutable_live_evidence
              BEFORE UPDATE OF evidence,evidence_hash,id,dedupe_key,created,expires ON live_signals
              BEGIN SELECT RAISE(ABORT,'IMMUTABLE_SIGNAL_EVIDENCE'); END;
            """)
        os.chmod(self.path, 0o640)  # execution worker is a read-only member of signal group
        # Keep WAL/SHM present while the signal service runs. A distinct read-only
        # UID cannot recreate them in this non-writable directory between short
        # transactions. SQLite inherits the database's group-readable mode.
        self._anchor = sqlite3.connect(self.path, isolation_level=None)
        self._anchor.execute("PRAGMA journal_mode=WAL").fetchone()
        self._anchor.execute("SELECT COUNT(*) FROM live_state").fetchone()

    def close(self):
        self._anchor.close()

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, isolation_level=None, timeout=10)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("PRAGMA synchronous=FULL")
        db.execute("PRAGMA foreign_keys=ON")
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
    def audit(db, kind, identity, data):
        db.execute("INSERT INTO live_audit(kind,identity,data,created) VALUES(?,?,?,?)", (kind, str(identity), canonical(data), time.time()))

    def recover(self):
        with self.transaction() as db:
            db.execute("UPDATE live_signals SET delivery='UNKNOWN' WHERE delivery='SENDING'")
            self.audit(db, "STARTUP_DELIVERY_RECOVERY", "signals", {})

    def bind_telegram(self, identity: dict):
        """Bot and chat are receipt custody, independent of rotating token secrets."""
        value = canonical(identity)
        with self.transaction() as db:
            row = db.execute("SELECT value FROM live_state WHERE key='telegram_delivery_identity'").fetchone()
            if row and row[0] != value:
                raise SignalError("TELEGRAM_RECEIPT_IDENTITY_MISMATCH")
            if not row:
                if db.execute("SELECT 1 FROM live_signals WHERE delivery IN ('SENDING','DELIVERED','UNKNOWN')").fetchone():
                    raise SignalError("LEGACY_UNBOUND_TELEGRAM_RECEIPTS_REQUIRE_OPERATOR_MIGRATION")
                db.execute("INSERT INTO live_state VALUES('telegram_delivery_identity',?)", (value,))
                self.audit(db, "TELEGRAM_IDENTITY_BOUND", identity["bot_id"], identity)

    def save(self, candidate: dict) -> bool:
        if not candidate.get("id") or not candidate.get("key") or time.time() >= candidate["expires"]:
            return False
        with self.transaction() as db:
            # Stable strategy identity is not a lifetime ban on new evidence.
            # Keep one alert per five-minute episode, retaining every prior row.
            episode = digest({"key": candidate["key"], "window": int(candidate["created"] // 300)})
            if db.execute("SELECT 1 FROM live_signals WHERE status='ACTIVE' AND expires>? AND json_extract(evidence,'$.key')=?", (time.time(), candidate["key"])).fetchone():
                return False
            inserted = db.execute("INSERT OR IGNORE INTO live_signals(id,dedupe_key,evidence,evidence_hash,created,expires,status,delivery) VALUES(?,?,?,?,?,?,'ACTIVE','READY')", (candidate["id"], episode, canonical(candidate), digest(candidate), candidate["created"], candidate["expires"])).rowcount
            if inserted:
                self.audit(db, "SIGNAL_CREATED", candidate["id"], {"execution_inferred": False})
            return bool(inserted)

    def candidate(self, signal_id: str) -> dict:
        with self.connect() as db:
            row = db.execute("SELECT evidence,evidence_hash FROM live_signals WHERE id=?", (signal_id,)).fetchone()
        if not row:
            raise SignalError("SIGNAL_NOT_FOUND")
        value = json.loads(row[0])
        if digest(value) != row[1]:
            raise SignalError("SIGNAL_EVIDENCE_CORRUPTED")
        return value

    def begin_send(self, signal_id: str) -> bool:
        with self.transaction() as db:
            return bool(db.execute("UPDATE live_signals SET delivery='SENDING' WHERE id=? AND status='ACTIVE' AND delivery='READY' AND expires>?", (signal_id, time.time())).rowcount)

    def receipt(self, signal_id: str, message_id: int | None):
        with self.transaction() as db:
            row = db.execute("SELECT * FROM live_signals WHERE id=?", (signal_id,)).fetchone()
            if not row or row["delivery"] != "SENDING":
                raise SignalError("RECEIPT_STATE_CONFLICT")
            if message_id is not None and (type(message_id) is not int or message_id <= 0):
                raise SignalError("RECEIPT_ID_INVALID")
            db.execute("UPDATE live_signals SET delivery=?,message_id=? WHERE id=?", ("DELIVERED" if message_id else "UNKNOWN", message_id, signal_id))
            self.audit(db, "DELIVERY_RECEIPT" if message_id else "DELIVERY_UNKNOWN", signal_id, {"message_id": message_id, "execution_inferred": False})
            if time.time() >= row["expires"] and row["status"] == "ACTIVE":
                db.execute("UPDATE live_signals SET status='EXPIRED',reason='QUOTE_VALIDITY_ENDED' WHERE id=?", (signal_id,))
                self.audit(db, "SIGNAL_TERMINAL", signal_id, {"status": "EXPIRED", "reason": "QUOTE_VALIDITY_ENDED"})
            if message_id and (row["status"] in TERMINAL or time.time() >= row["expires"]):
                db.execute("INSERT OR REPLACE INTO live_sync(signal_id,due) VALUES(?,?)", (signal_id, time.time()))

    def terminal(self, signal_id: str, status: str, reason: str):
        if status not in TERMINAL:
            raise SignalError("INVALID_TERMINAL_STATUS")
        with self.transaction() as db:
            row = db.execute("SELECT status,message_id FROM live_signals WHERE id=?", (signal_id,)).fetchone()
            if not row:
                raise SignalError("SIGNAL_NOT_FOUND")
            if row["status"] in TERMINAL:
                return False
            db.execute("UPDATE live_signals SET status=?,reason=? WHERE id=?", (status, reason, signal_id))
            self.audit(db, "SIGNAL_TERMINAL", signal_id, {"status": status, "reason": reason, "order_cancellation_implied": False})
            if row["message_id"]:
                db.execute("INSERT OR IGNORE INTO live_sync(signal_id,due) VALUES(?,?)", (signal_id, time.time()))
            return True

    def pending_sync(self, signal_id=None):
        with self.connect() as db:
            return [dict(r) for r in db.execute("SELECT s.id,s.status,s.reason,s.message_id,q.attempts FROM live_sync q JOIN live_signals s ON s.id=q.signal_id WHERE q.state='PENDING' AND (? IS NULL OR s.id=?) AND (q.due<=? OR ? IS NOT NULL) ORDER BY q.due LIMIT 25", (signal_id, signal_id, time.time(), signal_id))]

    def sync_result(self, signal_id: str, result: str):
        with self.transaction() as db:
            row = db.execute("SELECT attempts FROM live_sync WHERE signal_id=?", (signal_id,)).fetchone()
            if not row:
                return
            attempts = row[0] + 1
            state = "DONE" if result in {"EDITED", "UNCHANGED", "DELETED"} else "ESCALATED" if attempts >= 8 else "PENDING"
            db.execute("UPDATE live_sync SET attempts=?,state=?,due=?,error=? WHERE signal_id=?", (attempts, state, time.time() + min(3600, 2 ** attempts), None if state == "DONE" else result, signal_id))
            self.audit(db, "OPERATOR_SYNC", signal_id, {"state": state, "result": result})

    def active(self):
        with self.connect() as db:
            return [dict(r) for r in db.execute("SELECT id,expires,delivery FROM live_signals WHERE status='ACTIVE' ORDER BY created LIMIT 500")]

    def outcome_pending(self):
        with self.connect() as db:
            return [dict(r) for r in db.execute("SELECT id FROM live_signals WHERE outcome IS NULL AND COALESCE(json_extract(evidence,'$.scope'),'')!='EXCLUDED_LEGACY_RESEARCH' ORDER BY outcome_checked,created LIMIT 20")]

    def outcome(self, signal_id: str, value: dict | None):
        with self.transaction() as db:
            db.execute("UPDATE live_signals SET outcome_checked=? WHERE id=?", (time.time(), signal_id))
            if value is not None:
                db.execute("UPDATE live_signals SET outcome=? WHERE id=? AND outcome IS NULL", (canonical(value), signal_id))
                self.audit(db, "OBSERVED_SIGNAL_OUTCOME", signal_id, {"value": value, "actual_trade_inferred": False})

    def state(self, key: str, default="") -> str:
        with self.connect() as db:
            row = db.execute("SELECT value FROM live_state WHERE key=?", (key,)).fetchone()
            return row[0] if row else default

    def set_state(self, key, value):
        with self.transaction() as db:
            db.execute("INSERT OR REPLACE INTO live_state VALUES(?,?)", (key, str(value)))

    def command(self, update_id: int, actor: str, command: str) -> bool:
        with self.transaction() as db:
            if not db.execute("INSERT OR IGNORE INTO live_commands VALUES(?,?,?,?)", (update_id, actor, command, time.time())).rowcount:
                return False
            if command in {"/stop", "/cancel_open"}:
                db.execute("INSERT OR REPLACE INTO live_state VALUES('stop_opening','1')")
                db.execute("INSERT OR REPLACE INTO live_state VALUES('stop_generation',?)", (str(update_id),))
                if command == "/cancel_open":
                    db.execute("INSERT OR REPLACE INTO live_state VALUES('cancel_open','1')")
            self.audit(db, "AUTHENTICATED_COMMAND", str(update_id), {"actor": actor, "command": command})
            return True

    def recent(self):
        with self.connect() as db:
            return [dict(r) for r in db.execute("SELECT id,status,reason,delivery,message_id,outcome,created,expires FROM live_signals ORDER BY created DESC LIMIT 20")]

    def summary(self):
        with self.connect() as db:
            counts = {r[0]: r[1] for r in db.execute("SELECT status,COUNT(*) FROM live_signals WHERE COALESCE(json_extract(evidence,'$.scope'),'')!='EXCLUDED_LEGACY_RESEARCH' GROUP BY status")}
            unresolved = db.execute("SELECT COUNT(*) FROM live_sync WHERE state!='DONE'").fetchone()[0]
            outcomes = db.execute("SELECT COUNT(*) FROM live_signals WHERE outcome IS NOT NULL").fetchone()[0]
            unknown = db.execute("SELECT COUNT(*) FROM live_signals WHERE delivery='UNKNOWN'").fetchone()[0]
            legacy = db.execute("SELECT COUNT(*) FROM live_legacy_research").fetchone()[0]
        return {"signal_states": counts, "observed_signal_outcomes": outcomes, "operator_sync_pending_or_escalated": unresolved, "unknown_deliveries": unknown, "actual_trades_inferred": 0, "excluded_legacy_research_rows": legacy, "simulated_performance": "SEPARATE_LEGACY_DATABASE_ONLY", "stop_opening": self.state("stop_opening") == "1", "stop_generation": self.state("stop_generation", "0")}


class SignalReader:
    """Execution component never receives a writable connection to signal history."""
    def __init__(self, path: Path):
        self.path = path

    def _query(self, query: str, params=()):
        with sqlite3.connect(self.path.as_uri() + "?mode=ro", uri=True) as db:
            db.row_factory = sqlite3.Row
            return [dict(r) for r in db.execute(query, params)]

    def state(self, key, default=""):
        rows = self._query("SELECT value FROM live_state WHERE key=?", (key,))
        return rows[0]["value"] if rows else default

    def candidate(self, signal_id):
        rows = self._query("SELECT evidence,evidence_hash,status,expires FROM live_signals WHERE id=?", (signal_id,))
        if not rows:
            raise SignalError("SIGNAL_NOT_FOUND")
        row = rows[0]
        value = json.loads(row["evidence"])
        if digest(value) != row["evidence_hash"]:
            raise SignalError("SIGNAL_EVIDENCE_CORRUPTED")
        return value

    def is_active(self, signal_id):
        rows = self._query("SELECT status,expires FROM live_signals WHERE id=?", (signal_id,))
        return bool(rows and rows[0]["status"] == "ACTIVE" and time.time() < rows[0]["expires"])

    def pending(self, *, after=""):
        return [self.candidate(r["id"]) for r in self._query("SELECT id FROM live_signals WHERE status='ACTIVE' AND delivery='DELIVERED' AND expires>? AND id>? ORDER BY id LIMIT 25", (time.time(), after))]
