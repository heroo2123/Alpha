"""Offline, chat-bound import of research history and delivered alert custody.

The predecessor database remains read-only. No financial ledger is imported and
no network request occurs here. The canonical outbox performs any later edits.
"""
from __future__ import annotations

import json
from pathlib import Path
import re
import sqlite3
import time

from .config import ConfigurationError, canonical, digest
from .io import lease
from .signals import SignalStore, SignalError


def import_legacy(store: SignalStore, source: Path, *, identity: dict, expected_identity: dict):
    source = Path(source)
    if identity != expected_identity:
        raise ConfigurationError("LEGACY_TELEGRAM_IDENTITY_MISMATCH")
    if source.is_symlink() or not source.is_file() or source.resolve() == store.path.resolve() or source.samefile(store.path):
        raise ConfigurationError("LEGACY_IMPORT_REQUIRES_DISTINCT_REGULAR_SOURCE_DATABASE")
    # Caller holds the destination writer lease. Acquire the predecessor lease
    # as well, except when both paths already share that same directory lock.
    from contextlib import nullcontext
    lock = source.parent / "weather-paper-runtime.lock"
    guard = nullcontext() if lock.resolve() == (store.path.parent / lock.name).resolve() else lease(lock)
    with guard, sqlite3.connect(source.resolve().as_uri() + "?mode=ro", uri=True) as old:
        old.row_factory = sqlite3.Row
        old.execute("BEGIN")  # consistent source snapshot, including committed WAL
        tables = {row[0] for row in old.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if "weather_paper_signals" not in tables and "weather_maker_shadow_orders" not in tables:
            raise ConfigurationError("LEGACY_WEATHER_DATABASE_REQUIRED")
        store.bind_telegram(identity)
        source_id = digest({"source_path": str(source.resolve())})
        rows_imported = 0
        edits_queued = 0
        with store.transaction() as db:
            for table in sorted(tables):
                if not re.fullmatch(r"weather_[a-z0-9_]+", table):
                    continue
                # Archive every predecessor weather table, including settlements,
                # queue model certification, hashes, and original terminal audits.
                columns = list(old.execute('PRAGMA table_info("' + table + '")'))
                keys = [row[1] for row in sorted(columns, key=lambda row: row[5]) if row[5]]
                query = 'SELECT * FROM "' + table + '"'
                for index, record in enumerate(old.execute(query)):
                    raw = dict(record)
                    if any(isinstance(value, bytes) for value in raw.values()):
                        raw = {key: {"sqlite_blob_hex": value.hex()} if isinstance(value, bytes) else value for key, value in raw.items()}
                    row_id = canonical({key: raw[key] for key in keys}) if keys else str(index)
                    evidence_hash = digest(raw)
                    existing = db.execute("SELECT evidence_hash FROM live_legacy_research WHERE source_id=? AND table_name=? AND row_id=?", (source_id, table, row_id)).fetchone()
                    if existing and existing[0] != evidence_hash:
                        raise SignalError("LEGACY_SOURCE_CHANGED_AFTER_IMPORT")
                    if not existing:
                        db.execute("INSERT INTO live_legacy_research(source_id,table_name,row_id,evidence,evidence_hash) VALUES(?,?,?,?,?)", (source_id, table, row_id, canonical(raw), evidence_hash))
                        rows_imported += 1
                    if table != "weather_paper_signals" or not raw.get("telegram_message_id"):
                        continue
                    message_id = raw["telegram_message_id"]
                    if type(message_id) is not int or message_id <= 0:
                        raise SignalError("LEGACY_TELEGRAM_RECEIPT_INVALID")
                    signal_id = "legacy-" + digest({"source": source_id, "row": row_id, "receipt": identity, "message_id": message_id})
                    if db.execute("SELECT 1 FROM live_signals WHERE id=?", (signal_id,)).fetchone():
                        continue
                    sync = None
                    if "weather_paper_operator_sync" in tables:
                        value = old.execute("SELECT * FROM weather_paper_operator_sync WHERE signal_id=?", (raw["id"],)).fetchone()
                        sync = dict(value) if value else None
                    evidence = {"id": signal_id, "key": signal_id, "title": "Historical weather alert " + str(raw["id"]),
                                "strategy": str(raw.get("lane", "LEGACY")), "scope": "EXCLUDED_LEGACY_RESEARCH",
                                "legacy_signal": raw, "legacy_operator_sync": sync, "telegram_identity": identity}
                    created = float(raw.get("created_at", 0))
                    db.execute("INSERT INTO live_signals(id,dedupe_key,evidence,evidence_hash,created,expires,status,reason,delivery,message_id) VALUES(?,?,?,?,?,?,'INVALIDATED','LEGACY_ALERT_RETIRED_AT_PRODUCTION_CUTOVER','DELIVERED',?)", (signal_id, signal_id, canonical(evidence), digest(evidence), created, created, message_id))
                    if not sync or sync.get("state") != "APPLIED":
                        attempts = max(0, min(8, int(sync.get("attempts", 0)))) if sync else 0
                        state = "ESCALATED" if attempts >= 8 else "PENDING"
                        db.execute("INSERT INTO live_sync(signal_id,attempts,due,state) VALUES(?,?,?,?)", (signal_id, attempts, time.time(), state))
                        edits_queued += 1
                    store.audit(db, "LEGACY_RECEIPT_IMPORTED", signal_id, {"source": source_id, "original_evidence_hash": evidence_hash, "accounting": "EXCLUDED_LEGACY_RESEARCH"})
            result = {"source_id": source_id, "new_research_rows": rows_imported, "new_terminal_sync_rows": edits_queued, "financial_rows_imported": 0}
            store.audit(db, "LEGACY_IMPORT_COMPLETED", source_id, result)
        return result
