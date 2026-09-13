from __future__ import annotations

"""Plain-language operator status for the canonical weather paper runtime."""

import html
import math
import time

from .weather_only_paper_corrective import (
    STATUS_MAX_AGE_SECONDS,
    ClearWeatherPaperCommandController,
)


class CanonicalWeatherPaperCommandController(ClearWeatherPaperCommandController):
    """Keep paper-trade reporting simple while separating research from trading."""

    def _status_text(self) -> str:
        status = self._read_status()
        stats = self.store.stats()
        try:
            finished = float(status.get("finished_at") or 0.0)
        except (TypeError, ValueError, OverflowError):
            finished = 0.0
        age = time.time() - finished if finished > 0 else float("inf")
        healthy = bool(status.get("cycle_ok")) and 0.0 <= age <= STATUS_MAX_AGE_SECONDS
        icon = "🟢" if healthy else "🔴"

        same_day = status.get("same_day_three_layer")
        same_day = same_day if isinstance(same_day, dict) else {}
        store = same_day.get("store")
        store = store if isinstance(store, dict) else {}
        capture_total = int(store.get("total") or 0)
        blocked = int(store.get("blocked") or 0)
        ready = int(store.get("ready_uncalibrated") or 0)
        collector_errors = same_day.get("errors") or []

        if same_day.get("enabled") is True:
            research_line = (
                "Three-layer same-day research: <b>ON — silent only</b> "
                f"({capture_total} captures; {blocked} blocked; {ready} research-ready)"
            )
        else:
            research_line = "Three-layer same-day research: <b>NOT ACTIVE</b>"

        lines = [
            f"{icon} <b>WEATHER PAPER BOT — {'RUNNING NORMALLY' if healthy else 'NEEDS ATTENTION'}</b>",
            f"Last full cycle: <b>{int(max(0.0, age)) if math.isfinite(age) else 'unknown'}s ago</b>",
            "",
            "<b>PAPER TRADING</b>",
            f"Open: <b>{int(stats.get('open') or 0)}</b>",
            f"Finished: <b>{int(stats.get('won') or 0)} wins / {int(stats.get('lost') or 0)} losses / {int(stats.get('partial') or 0)} push/partial</b>",
            f"Net paper P&amp;L: <b>${float(stats.get('pnl') or 0.0):+.2f}</b>",
            f"Delivery uncertain: <b>{int(stats.get('delivery_uncertain') or 0)}</b>",
            "",
            "<b>RESEARCH / SAFETY</b>",
            research_line,
            "Same-day Telegram trade alerts: <b>OFF</b>",
            "Same-day research probability: <b>UNCALIBRATED</b>",
            "Structural guarantee lane: <b>OFF pending common-resolution proof</b>",
            "Real orders: <b>DISABLED</b>",
        ]
        if blocked:
            lines.append(
                "Three-layer blocked captures are <b>not</b> counted as paper trades or P&amp;L."
            )
        errors = list(status.get("errors") or []) + list(collector_errors)
        if errors:
            lines.append(
                "⚠️ Last cycle issues: <code>"
                + html.escape(", ".join(map(str, errors))[:700])
                + "</code>"
            )
        return "\n".join(lines)
