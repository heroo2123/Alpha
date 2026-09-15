from __future__ import annotations

"""Operator commands for the all-weather PAPER research runtime.

Unlike the legacy canonical controller, this facade never hardcodes strategy lanes as
OFF. It reports the final runtime status dynamically and keeps taker/structural PAPER
positions separate from prospective maker-order research.
"""

import html
import math
import time

from .weather_only_paper_corrective import (
    ClearWeatherPaperCommandController,
    STATUS_MAX_AGE_SECONDS,
)


class AllPaperCommandController(ClearWeatherPaperCommandController):
    def __init__(self, *, maker_store, **kwargs) -> None:
        super().__init__(**kwargs)
        self.maker_store = maker_store

    @staticmethod
    def _on(value: object, *, suffix: str = "") -> str:
        return f"<b>ON{html.escape(suffix)}</b>" if value is True else "<b>OFF</b>"

    def _maker_performance(self) -> dict:
        capital = proceeds = pnl = 0.0
        settled = 0
        try:
            orders = self.maker_store.orders()
        except Exception:
            return {"settled": 0, "capital": 0.0, "proceeds": 0.0, "pnl": 0.0, "roi": None}
        for order in orders:
            try:
                row = self.maker_store.settlement(order.order_id)
            except Exception:
                continue
            if not isinstance(row, dict):
                continue
            settled += 1
            capital += float(row.get("simulated_capital_used") or 0.0)
            proceeds += float(row.get("paper_proceeds") or 0.0)
            pnl += float(row.get("paper_pnl") or 0.0)
        return {
            "settled": settled,
            "capital": capital,
            "proceeds": proceeds,
            "pnl": pnl,
            "roi": pnl / capital if capital > 0.0 else None,
        }

    def _status_text(self) -> str:
        status = self._read_status()
        stats = self.store.stats()
        maker = self.maker_store.summary()
        try:
            finished = float(status.get("finished_at") or 0.0)
        except (TypeError, ValueError, OverflowError):
            finished = 0.0
        age = time.time() - finished if finished > 0.0 else float("inf")
        healthy = bool(status.get("cycle_ok")) and 0.0 <= age <= STATUS_MAX_AGE_SECONDS
        icon = "🟢" if healthy else "🔴"

        lines = [
            f"{icon} <b>WEATHER ALL-PAPER BOT — {'RUNNING NORMALLY' if healthy else 'NEEDS ATTENTION'}</b>",
            f"Last full cycle: <b>{int(max(0.0, age)) if math.isfinite(age) else 'unknown'}s ago</b>",
            "",
            "<b>STRATEGY LANES</b>",
            f"Future-day GEFS gap: {self._on(status.get('paper_telegram_delivery'), suffix=' — UNCALIBRATED')}",
            f"Same-day late-lock: {self._on(status.get('same_day_paper_delivery_enabled'), suffix=' — UNCALIBRATED')}",
            f"Official-extreme source shock: {self._on(status.get('source_shock_paper_delivery_enabled'), suffix=' — REVISION-SENSITIVE')}",
            f"Structural underround: {self._on(status.get('structural_paper_delivery_enabled'))}",
            f"Prospective maker: {self._on(status.get('maker_paper_delivery_enabled'), suffix=' — UNCALIBRATED')}",
            "Result-lag: <b>GATED — exact WRH cutoff state not proven</b>",
            "",
            "<b>EXECUTION / SAFETY</b>",
            f"Post-receipt exact-CLOB admission: <b>{'ON' if status.get('post_receipt_execution_required') is True else 'NOT PROVEN'}</b>",
            f"Validated taker/structural positions: <b>{int(stats.get('total') or 0)}</b>",
            f"Active maker virtual orders: <b>{int(maker.get('active_orders') or 0)}</b>",
            f"Maker public-WS fill evidence ready: <b>{'YES' if status.get('maker_fill_evidence_ready') is True else 'NO'}</b>",
            "Real orders / wallet / signing: <b>DISABLED</b>",
            "",
            f"Financial authority flag: <b>{html.escape(str(status.get('financial_authority')))}</b>",
            f"Automatic order placement flag: <b>{html.escape(str(status.get('automatic_order_placement')))}</b>",
        ]
        errors = (
            list(status.get("errors") or [])
            + list(status.get("maker_errors") or [])
            + list(status.get("source_shock_errors") or [])
        )
        if errors:
            lines.append(
                "⚠️ Last issues: <code>" + html.escape(", ".join(map(str, errors))[:700]) + "</code>"
            )
        return "\n".join(lines)

    def _stats_text(self) -> str:
        stats = self.store.stats()
        lanes = self.store.lane_stats()
        maker = self._maker_performance()
        lines = [
            "📊 <b>ALL-WEATHER PAPER PERFORMANCE</b>",
            "",
            "<b>TAKER / STRUCTURAL — VALIDATED POSITION LEDGER</b>",
            f"Open: <b>{int(stats.get('open') or 0)}</b> | Resolved: <b>{int(stats.get('resolved') or 0)}</b> | No-fill: <b>{int(stats.get('no_fill') or 0)}</b>",
            f"Results: <b>{int(stats.get('won') or 0)}W / {int(stats.get('lost') or 0)}L / {int(stats.get('partial') or 0)} partial</b>",
            f"Resolved capital: <b>${float(stats.get('resolved_capital') or 0.0):.2f}</b>",
            f"Resolved proceeds: <b>${float(stats.get('resolved_proceeds') or 0.0):.2f}</b>",
            f"Net P&amp;L: <b>${float(stats.get('pnl') or 0.0):+.2f}</b>",
            f"Resolved ROI: <b>{self._pct(stats.get('resolved_roi'))}</b>",
        ]
        if lanes:
            lines.extend(["", "<b>By taker / structural lane</b>"])
            for row in lanes:
                lines.append(
                    f"• {html.escape(str(row.get('lane') or 'unknown'))}: "
                    f"{int(row.get('total') or 0)} positions | "
                    f"{int(row.get('won') or 0)}W/{int(row.get('lost') or 0)}L/"
                    f"{int(row.get('partial') or 0)} partial | "
                    f"P&amp;L ${float(row.get('pnl') or 0.0):+.2f}"
                )
        lines.extend(
            [
                "",
                "<b>PROSPECTIVE MAKER — SEPARATE QUEUE/FILL EXPERIMENT</b>",
                f"Settled maker orders with simulated fills: <b>{int(maker['settled'])}</b>",
                f"Simulated maker capital: <b>${float(maker['capital']):.2f}</b>",
                f"Maker proceeds: <b>${float(maker['proceeds']):.2f}</b>",
                f"Maker P&amp;L: <b>${float(maker['pnl']):+.2f}</b>",
                f"Maker ROI: <b>{self._pct(maker.get('roi'))}</b>",
                "",
                "Future-day, same-day, source-shock and maker model evidence is explicitly research-grade / uncalibrated where labelled.",
                "V4 historical fills and V5 post-receipt fills are different experiment protocols; neither rewrites the other.",
                "📒 Everything above is simulated. No real order was placed.",
            ]
        )
        return "\n".join(lines)

    def _positions_text(self) -> str:
        rows = self.store.open_positions(10)
        try:
            maker_orders = self.maker_store.active_orders()[:10]
        except Exception:
            maker_orders = []
        lines = [
            "📂 <b>OPEN PAPER EXPERIMENTS</b>",
            f"Taker/structural positions: <b>{len(rows)}</b>",
        ]
        for row in rows:
            lines.extend(
                [
                    "",
                    f"<b>Position #{int(row['id'])} — {html.escape(str(row.get('side') or ''))}</b>",
                    html.escape(str(row.get("title") or ""))[:100],
                    f"Lane: <code>{html.escape(str(row.get('lane') or ''))}</code>",
                    f"Entry cost/unit: <b>${float(row.get('entry_cost_per_unit') or 0.0):.4f}</b>",
                    f"Paper capital: <b>${float(row.get('capital_used') or 0.0):.2f}</b>",
                    f"Execution protocol: <code>{html.escape(str(row.get('execution_protocol') or 'legacy'))}</code>",
                ]
            )
        lines.extend(["", f"Prospective maker orders: <b>{len(maker_orders)}</b>"])
        for order in maker_orders:
            lines.append(
                f"• <code>{html.escape(order.order_id[:12])}</code> "
                f"{html.escape(order.outcome)} bid ${float(order.bid_price):.4f} — "
                f"{html.escape(order.status)}; simulated {float(order.simulated_filled_shares):.4f}/{float(order.shares):.4f} shares"
            )
        if not rows and not maker_orders:
            lines.append("No simulated position or active virtual maker order is open.")
        return "\n".join(lines)
