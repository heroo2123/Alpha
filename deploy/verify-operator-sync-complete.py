#!/usr/bin/env python3
from __future__ import annotations

"""Independent DB gate: every delivered terminal signal has operator-sync state."""

import argparse
import json
import sqlite3
from pathlib import Path

TERMINAL = (
    "POST_RECEIPT_NOT_ACTIONABLE",
    "ACTIONABILITY_UNPROVEN",
    "PAPER_ACCOUNTING_ERROR",
    "EXPIRED",
    "MAKER_NOT_ACTIVATED",
    "MAKER_NOT_ACTIVATED_RESTART_COVERAGE_LOST",
)


def verify(path: Path) -> dict:
    path = path.expanduser().resolve()
    if not path.is_file():
        raise SystemExit("OPERATOR_SYNC_DB_MISSING")
    db = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=5.0)
    db.row_factory = sqlite3.Row
    try:
        tables = {str(row[0]) for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        required = {"weather_paper_signals", "weather_paper_operator_sync"}
        if not required.issubset(tables):
            raise SystemExit("OPERATOR_SYNC_TABLES_MISSING")
        placeholders = ",".join("?" for _ in TERMINAL)
        rows = db.execute(
            "SELECT s.id,s.status,s.telegram_message_id FROM weather_paper_signals s "
            "LEFT JOIN weather_paper_operator_sync o ON o.signal_id=s.id "
            "WHERE s.telegram_message_id IS NOT NULL "
            f"AND s.status IN ({placeholders}) AND o.signal_id IS NULL ORDER BY s.id LIMIT 20",
            TERMINAL,
        ).fetchall()
        total = db.execute(
            "SELECT COUNT(*) FROM weather_paper_signals s "
            "WHERE s.telegram_message_id IS NOT NULL "
            f"AND s.status IN ({placeholders})",
            TERMINAL,
        ).fetchone()[0]
    finally:
        db.close()
    if rows:
        raise SystemExit("HISTORICAL_TERMINAL_OPERATOR_SYNC_INCOMPLETE:" + ",".join(str(row["id"]) for row in rows))
    return {
        "version": "operator_sync_db_acceptance_v1_all_historical_terminal_rows",
        "accepted": True,
        "delivered_terminal_signal_count": int(total),
        "missing_operator_sync_count": 0,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = verify(args.db)
    payload = json.dumps(result, sort_keys=True, indent=2) + "\n"
    if args.output:
        args.output.write_text(payload, encoding="utf-8")
    print(payload, end="")


if __name__ == "__main__":
    main()
