from __future__ import annotations

import time

from .weather_only_maker_paper_accounting import MAKER_SETTLEMENT_EVENT
from .weather_only_maker_paper_accounting_v5 import MakerPaperAccountingStoreV5

LEGACY_QUEUE_UNCERTIFIED = "LEGACY_QUEUE_UNCERTIFIED"
RESEARCH_ONLY = "RESEARCH_ONLY"
CERTIFIED_QUEUE_MODEL = "CERTIFIED_QUEUE_MODEL"
MAKER_PAPER_ACCOUNTING_V6_VERSION = "weather_maker_v6_explicit_evidence_excludes_legacy_queue_pnl"


class MakerPaperAccountingStoreV6(MakerPaperAccountingStoreV5):
    """Preserve maker history but keep uncertified queue fills out of validated returns."""

    def __init__(self, path):
        super().__init__(path)
        self._evidence_migrate()

    def _evidence_migrate(self) -> None:
        with self._db_lock:
            self.db.execute(
                """CREATE TABLE IF NOT EXISTS weather_maker_evidence_classification(
                     order_id TEXT PRIMARY KEY,
                     evidence_class TEXT NOT NULL,
                     reason TEXT NOT NULL,
                     classified_at REAL NOT NULL
                   )"""
            )
            # Anything with a historical simulated fill was produced by the old
            # public-print queue assumption. It remains auditable but can never become
            # validated performance merely because settlement happens after migration.
            legacy_ids = {
                order.order_id for order in self.orders()
                if float(order.simulated_filled_shares) > 0.0
            }
            legacy_ids.update(
                str(row[0]) for row in self.db.execute(
                    "SELECT DISTINCT order_id FROM weather_maker_shadow_events WHERE event_type=?",
                    (MAKER_SETTLEMENT_EVENT,),
                ).fetchall()
            )
            for oid in sorted(legacy_ids):
                self.db.execute(
                    """INSERT INTO weather_maker_evidence_classification(
                         order_id,evidence_class,reason,classified_at
                       ) VALUES(?,?,?,?)
                       ON CONFLICT(order_id) DO UPDATE SET
                         evidence_class=excluded.evidence_class,
                         reason=excluded.reason""",
                    (
                        oid, LEGACY_QUEUE_UNCERTIFIED,
                        "Historical public-trade queue model was not execution-certified",
                        time.time(),
                    ),
                )

    def evidence_class(self, order_id: str) -> str:
        with self._db_lock:
            row = self.db.execute(
                "SELECT evidence_class FROM weather_maker_evidence_classification WHERE order_id=?",
                (str(order_id),),
            ).fetchone()
        return str(row[0]) if row else RESEARCH_ONLY

    def maker_performance_by_evidence(self) -> dict:
        buckets = {
            CERTIFIED_QUEUE_MODEL: {"settled":0,"capital":0.0,"proceeds":0.0,"pnl":0.0},
            LEGACY_QUEUE_UNCERTIFIED: {"settled":0,"capital":0.0,"proceeds":0.0,"pnl":0.0},
            RESEARCH_ONLY: {"settled":0,"capital":0.0,"proceeds":0.0,"pnl":0.0},
        }
        for order in self.orders():
            settlement = self.settlement(order.order_id)
            if not isinstance(settlement, dict):
                continue
            cls = self.evidence_class(order.order_id)
            bucket = buckets.setdefault(cls, {"settled":0,"capital":0.0,"proceeds":0.0,"pnl":0.0})
            bucket["settled"] += 1
            bucket["capital"] += float(settlement.get("simulated_capital_used") or 0.0)
            bucket["proceeds"] += float(settlement.get("paper_proceeds") or 0.0)
            bucket["pnl"] += float(settlement.get("paper_pnl") or 0.0)
        for bucket in buckets.values():
            bucket["roi"] = bucket["pnl"] / bucket["capital"] if bucket["capital"] > 0 else None
        return {
            "validated": buckets[CERTIFIED_QUEUE_MODEL],
            "legacy_excluded": buckets[LEGACY_QUEUE_UNCERTIFIED],
            "research_excluded": buckets[RESEARCH_ONLY],
            "validated_evidence_class": CERTIFIED_QUEUE_MODEL,
        }

    def summary(self) -> dict:
        out = dict(super().summary())
        out.update({
            "version": MAKER_PAPER_ACCOUNTING_V6_VERSION,
            "maker_evidence_classification": True,
            "legacy_queue_uncertified_excluded_from_validated_pnl": True,
            "performance_by_evidence": self.maker_performance_by_evidence(),
        })
        return out
