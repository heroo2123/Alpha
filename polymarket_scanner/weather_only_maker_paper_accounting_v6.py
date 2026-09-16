from __future__ import annotations

import json
import math
import time

from .weather_only_maker_paper_accounting import MAKER_SETTLEMENT_EVENT
from .weather_only_maker_paper_accounting_v5 import MakerPaperAccountingStoreV5
from .weather_only_maker_store import WeatherMakerStoreError


LEGACY_QUEUE_UNCERTIFIED = "LEGACY_QUEUE_UNCERTIFIED"
RESEARCH_ONLY = "RESEARCH_ONLY"
CERTIFIED_QUEUE_MODEL = "CERTIFIED_QUEUE_MODEL"
MAKER_EVIDENCE_CLASSES = frozenset(
    {CERTIFIED_QUEUE_MODEL, LEGACY_QUEUE_UNCERTIFIED, RESEARCH_ONLY}
)
MAKER_PAPER_ACCOUNTING_V6_VERSION = (
    "weather_maker_v6_additive_evidence_migration_excludes_legacy_queue_performance"
)
LEGACY_QUEUE_EVENT_TYPES = frozenset(
    {
        "TRADE_PROGRESS",
        MAKER_SETTLEMENT_EVENT,
        "FILL_TELEGRAM_SENT",
        "FILL_TELEGRAM_FAILED",
        "FILL_TELEGRAM_UNCERTAIN",
    }
)


class MakerPaperAccountingStoreV6(MakerPaperAccountingStoreV5):
    """Evidence-classified maker history; no old queue simulation is validated P&L."""

    def __init__(self, path):
        super().__init__(path)
        self._evidence_migrate()

    @staticmethod
    def _simulated_filled_shares(state_json: object) -> float:
        try:
            raw = json.loads(str(state_json))
        except json.JSONDecodeError:
            raise WeatherMakerStoreError("MAKER_EVIDENCE_STATE_JSON_INVALID") from None
        if not isinstance(raw, dict):
            raise WeatherMakerStoreError("MAKER_EVIDENCE_STATE_JSON_INVALID")
        value = raw.get("simulated_filled_shares", 0.0)
        if isinstance(value, bool):
            raise WeatherMakerStoreError("MAKER_EVIDENCE_FILL_INVALID")
        try:
            number = float(value)
        except (TypeError, ValueError, OverflowError):
            raise WeatherMakerStoreError("MAKER_EVIDENCE_FILL_INVALID") from None
        if not math.isfinite(number) or number < 0.0:
            raise WeatherMakerStoreError("MAKER_EVIDENCE_FILL_INVALID")
        return number

    def _legacy_queue_evidence(self, order_id: str, state_json: object) -> bool:
        if self._simulated_filled_shares(state_json) > 0.0:
            return True
        rows = self.db.execute(
            "SELECT DISTINCT event_type FROM weather_maker_shadow_events WHERE order_id=?",
            (str(order_id),),
        ).fetchall()
        event_types = {str(row[0] or "").upper() for row in rows}
        return bool(event_types & LEGACY_QUEUE_EVENT_TYPES)

    def _evidence_migrate(self) -> None:
        """Add classification only; never rewrite historical order/event evidence."""
        with self._db_lock:
            try:
                self.db.execute("BEGIN IMMEDIATE")
                self.db.execute(
                    """
                    CREATE TABLE IF NOT EXISTS weather_maker_evidence_classification(
                        order_id TEXT PRIMARY KEY,
                        evidence_class TEXT NOT NULL,
                        reason TEXT NOT NULL,
                        classified_at REAL NOT NULL,
                        CHECK(evidence_class IN (
                            'CERTIFIED_QUEUE_MODEL',
                            'LEGACY_QUEUE_UNCERTIFIED',
                            'RESEARCH_ONLY'
                        ))
                    )
                    """
                )
                rows = self.db.execute(
                    "SELECT order_id,state_json FROM weather_maker_shadow_orders ORDER BY order_id"
                ).fetchall()
                now = time.time()
                for row in rows:
                    order_id = str(row["order_id"])
                    legacy = self._legacy_queue_evidence(order_id, row["state_json"])
                    evidence_class = (
                        LEGACY_QUEUE_UNCERTIFIED if legacy else RESEARCH_ONLY
                    )
                    reason = (
                        "Historical public-trade queue simulation was not execution-certified"
                        if legacy
                        else "Maker proposal/order retained for research without certified queue execution"
                    )
                    # Idempotent and non-destructive: a future independently certified
                    # classification can never be silently overwritten by migration.
                    self.db.execute(
                        """
                        INSERT OR IGNORE INTO weather_maker_evidence_classification(
                            order_id,evidence_class,reason,classified_at
                        ) VALUES(?,?,?,?)
                        """,
                        (order_id, evidence_class, reason, now),
                    )
                self.db.execute("COMMIT")
            except Exception:
                if self.db.in_transaction:
                    self.db.execute("ROLLBACK")
                raise

    def evidence_class(self, order_id: str) -> str:
        with self._db_lock:
            row = self.db.execute(
                "SELECT evidence_class FROM weather_maker_evidence_classification WHERE order_id=?",
                (str(order_id),),
            ).fetchone()
            if row is None:
                return RESEARCH_ONLY
            value = str(row[0])
            if value not in MAKER_EVIDENCE_CLASSES:
                raise WeatherMakerStoreError("MAKER_EVIDENCE_CLASS_INVALID")
            return value

    def evidence_classification(self, order_id: str) -> dict:
        with self._db_lock:
            row = self.db.execute(
                """
                SELECT order_id,evidence_class,reason,classified_at
                FROM weather_maker_evidence_classification WHERE order_id=?
                """,
                (str(order_id),),
            ).fetchone()
        if row is None:
            return {
                "order_id": str(order_id),
                "evidence_class": RESEARCH_ONLY,
                "reason": "Unclassified order defaults fail-closed to research only",
                "classified_at": None,
            }
        return dict(row)

    def evidence_classification_counts(self) -> dict[str, int]:
        with self._db_lock:
            rows = self.db.execute(
                """
                SELECT evidence_class,COUNT(*) AS n
                FROM weather_maker_evidence_classification
                GROUP BY evidence_class ORDER BY evidence_class
                """
            ).fetchall()
        out = {key: 0 for key in sorted(MAKER_EVIDENCE_CLASSES)}
        for row in rows:
            key = str(row["evidence_class"])
            if key not in MAKER_EVIDENCE_CLASSES:
                raise WeatherMakerStoreError("MAKER_EVIDENCE_CLASS_INVALID")
            out[key] = int(row["n"])
        return out

    def maker_performance_by_evidence(self) -> dict:
        buckets = {
            key: {
                "settled": 0,
                "capital": 0.0,
                "proceeds": 0.0,
                "pnl": 0.0,
            }
            for key in MAKER_EVIDENCE_CLASSES
        }
        for order in self.orders():
            settlement = self.settlement(order.order_id)
            if not isinstance(settlement, dict):
                continue
            evidence_class = self.evidence_class(order.order_id)
            bucket = buckets[evidence_class]
            bucket["settled"] += 1
            bucket["capital"] += float(
                settlement.get("simulated_capital_used") or 0.0
            )
            bucket["proceeds"] += float(settlement.get("paper_proceeds") or 0.0)
            bucket["pnl"] += float(settlement.get("paper_pnl") or 0.0)
        for bucket in buckets.values():
            bucket["roi"] = (
                bucket["pnl"] / bucket["capital"]
                if bucket["capital"] > 0.0
                else None
            )
        return {
            "validated": dict(buckets[CERTIFIED_QUEUE_MODEL]),
            "legacy_excluded": dict(buckets[LEGACY_QUEUE_UNCERTIFIED]),
            "research_excluded": dict(buckets[RESEARCH_ONLY]),
            "validated_evidence_class": CERTIFIED_QUEUE_MODEL,
            "legacy_evidence_class": LEGACY_QUEUE_UNCERTIFIED,
            "no_automatic_queue_certification": True,
        }

    def summary(self) -> dict:
        out = dict(super().summary())
        out.update(
            {
                "version": MAKER_PAPER_ACCOUNTING_V6_VERSION,
                "maker_evidence_classification": True,
                "evidence_classification_counts": self.evidence_classification_counts(),
                "legacy_queue_uncertified_excluded_from_validated_pnl": True,
                "certified_queue_model_automatic_assignment": False,
                "performance_by_evidence": self.maker_performance_by_evidence(),
            }
        )
        return out
