from __future__ import annotations

import html

from .weather_only_operator_state_corrective import OperatorStateCommandController


class OperatorStateCommandControllerV10(OperatorStateCommandController):
    """Final command facade: only queue-certified maker evidence may be validated P&L."""

    @staticmethod
    def _empty_performance() -> dict:
        zero = {
            "settled": 0,
            "capital": 0.0,
            "proceeds": 0.0,
            "pnl": 0.0,
            "roi": None,
        }
        return {
            **zero,
            "legacy_excluded": dict(zero),
            "research_excluded": dict(zero),
        }

    def _maker_performance(self) -> dict:
        method = getattr(self.maker_store, "maker_performance_by_evidence", None)
        if not callable(method):
            return self._empty_performance()
        grouped = method()
        validated = dict(grouped["validated"])
        validated["legacy_excluded"] = dict(grouped["legacy_excluded"])
        validated["research_excluded"] = dict(grouped["research_excluded"])
        return validated

    def _status_text(self) -> str:
        base = super()._status_text()
        summary = self.maker_store.summary()
        counts = dict(summary.get("evidence_classification_counts") or {})
        return base + "\n" + "\n".join(
            [
                "",
                "<b>MAKER EVIDENCE POLICY</b>",
                "Validated maker performance requires: <code>CERTIFIED_QUEUE_MODEL</code>.",
                f"Legacy queue-uncertified orders: <b>{int(counts.get('LEGACY_QUEUE_UNCERTIFIED') or 0)}</b>",
                f"Research-only orders: <b>{int(counts.get('RESEARCH_ONLY') or 0)}</b>",
                "Automatic queue certification: <b>OFF</b>",
            ]
        )

    def _stats_text(self) -> str:
        base = super()._stats_text()
        base = base.replace(
            "<b>PROSPECTIVE MAKER — SEPARATE QUEUE/FILL EXPERIMENT</b>",
            "<b>MAKER — VALIDATED QUEUE-CERTIFIED PERFORMANCE ONLY</b>",
        )
        base = base.replace(
            "Settled maker orders with simulated fills:",
            "Queue-certified settled maker orders:",
        )
        base = base.replace("Simulated maker capital:", "Validated maker capital:")
        base = base.replace("Maker proceeds:", "Validated maker proceeds:")
        base = base.replace("Maker P&amp;L:", "Validated maker P&amp;L:")
        base = base.replace("Maker ROI:", "Validated maker ROI:")

        maker = self._maker_performance()
        legacy = maker.get("legacy_excluded") or {}
        research = maker.get("research_excluded") or {}
        note = "\n".join(
            [
                "",
                "<b>EXCLUDED MAKER RESEARCH HISTORY</b>",
                f"LEGACY_QUEUE_UNCERTIFIED: <b>{int(legacy.get('settled') or 0)}</b> settlements | capital ${float(legacy.get('capital') or 0):.2f} | proceeds ${float(legacy.get('proceeds') or 0):.2f} | research P&amp;L ${float(legacy.get('pnl') or 0):+.2f}",
                f"RESEARCH_ONLY: <b>{int(research.get('settled') or 0)}</b> settlements | capital ${float(research.get('capital') or 0):.2f} | proceeds ${float(research.get('proceeds') or 0):.2f} | research P&amp;L ${float(research.get('pnl') or 0):+.2f}",
                "Legacy/research maker rows remain auditable but are excluded from validated capital, proceeds, P&amp;L and ROI.",
                "No current code path automatically assigns CERTIFIED_QUEUE_MODEL.",
            ]
        )
        return base + note

    def _positions_text(self) -> str:
        text = super()._positions_text()
        text = text.replace(
            "Prospective maker orders:",
            "Research-only maker proposals/orders (queue uncertified):",
        )
        return text
