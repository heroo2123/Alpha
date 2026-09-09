"""Invalidate pre-quarantine research claims; never rewrite user fill accounting."""
import json
import sqlite3

from .hardening import RULE_QUARANTINE_VERSION


def quarantine_structural_history(db_path: str) -> int:
    with sqlite3.connect(db_path, timeout=5) as db:
        if db.execute("SELECT 1 FROM bot_state WHERE key=?", (RULE_QUARANTINE_VERSION,)).fetchone():
            return 0
        count = 0
        cursor = db.execute("SELECT id,metadata FROM signals WHERE detector IN ('nested_threshold_arb','neg_risk_underround')")
        while rows := cursor.fetchmany(200):
            for signal_id, metadata in rows:
                try:
                    meta = json.loads(metadata or "{}")
                except (ValueError, TypeError):
                    meta = {}
                if not isinstance(meta, dict):
                    meta = {}
                meta["prior_certification_status"] = meta.get("certification_status")
                meta.update(certification_status="NOT_ACTIONABLE", semantic_evidence_valid=False,
                            rule_quarantine_version=RULE_QUARANTINE_VERSION, trade_ready=False)
                if isinstance(meta.get("payoff_proof"), dict):
                    meta["payoff_proof"].pop("minimum_bundle_payout", None)
                    meta["payoff_proof"]["validated_for_contract_resolution"] = False
                db.execute("UPDATE signals SET metadata=?,confidence='LEGACY_THEORETICAL',status='LEGACY_THEORETICAL',"
                           "edge=NULL,theoretical_payout=NULL,pnl=NULL WHERE id=?", (json.dumps(meta), signal_id))
                count += 1
        db.execute("INSERT INTO bot_state(key,value) VALUES (?,?)", (RULE_QUARANTINE_VERSION, str(count)))
        return count
