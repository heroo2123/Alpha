from __future__ import annotations

"""Operator commands for the all-weather PAPER research runtime.

Unlike the legacy canonical controller, this facade never hardcodes strategy lanes as
OFF. It reports the final runtime status dynamically, keeps V5 post-receipt results
separate from historical V4 frozen-quote results, and keeps both separate from the
prospective maker-order experiment.
"""

import html
import math
import time

from .weather_only_paper_corrective import (
    ClearWeatherPaperCommandController,
    PAPER_EXECUTION_PROTOCOL_V4,
    STATUS_MAX_AGE_SECONDS,
)
from .weather_only_paper_post_receipt import PAPER_EXECUTION_PROTOCOL_V5


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

    def _protocol_performance(self, protocol: str) -> tuple[dict, list[dict]]:
        """Read one execution experiment without combining incompatible fill semantics."""
        with self.store._conn() as db:
            row = db.execute(
                """
                SELECT
                    COUNT(*) AS total,
                    SUM(CASE WHEN status='OPEN' THEN 1 ELSE 0 END) AS open_n,
                    SUM(CASE WHEN status='NO_FILL' THEN 1 ELSE 0 END) AS no_fill,
                    SUM(CASE WHEN status='WON' THEN 1 ELSE 0 END) AS won,
                    SUM(CASE WHEN status='LOST' THEN 1 ELSE 0 END) AS lost,
                    SUM(CASE WHEN status='RESOLVED_PARTIAL' THEN 1 ELSE 0 END) AS partial,
                    SUM(CASE WHEN status IN ('WON','LOST','RESOLVED_PARTIAL') THEN capital_used ELSE 0 END) AS resolved_capital,
                    SUM(CASE WHEN status IN ('WON','LOST','RESOLVED_PARTIAL') THEN proceeds ELSE 0 END) AS resolved_proceeds,
                    SUM(CASE WHEN status IN ('WON','LOST','RESOLVED_PARTIAL') THEN pnl ELSE 0 END) AS pnl
                FROM weather_paper_positions
                WHERE validation_state='VALIDATED' AND execution_protocol=?
                """,
                (str(protocol),),
            ).fetchone()
            lanes = [
                dict(value)
                for value in db.execute(
                    """
                    SELECT lane,COUNT(*) AS total,
                           SUM(CASE WHEN status='OPEN' THEN 1 ELSE 0 END) AS open_n,
                           SUM(CASE WHEN status='NO_FILL' THEN 1 ELSE 0 END) AS no_fill,
                           SUM(CASE WHEN status='WON' THEN 1 ELSE 0 END) AS won,
                           SUM(CASE WHEN status='LOST' THEN 1 ELSE 0 END) AS lost,
                           SUM(CASE WHEN status='RESOLVED_PARTIAL' THEN 1 ELSE 0 END) AS partial,
                           SUM(CASE WHEN status IN ('WON','LOST','RESOLVED_PARTIAL') THEN capital_used ELSE 0 END) AS resolved_capital,
                           SUM(CASE WHEN status IN ('WON','LOST','RESOLVED_PARTIAL') THEN pnl ELSE 0 END) AS pnl
                    FROM weather_paper_positions
                    WHERE validation_state='VALIDATED' AND execution_protocol=?
                    GROUP BY lane ORDER BY lane
                    """,
                    (str(protocol),),
                )
            ]
        data = dict(row) if row else {}
        won = int(data.get("won") or 0)
        lost = int(data.get("lost") or 0)
        partial = int(data.get("partial") or 0)
        capital = float(data.get("resolved_capital") or 0.0)
        pnl = float(data.get("pnl") or 0.0)
        stats = {
            "total": int(data.get("total") or 0),
            "open": int(data.get("open_n") or 0),
            "no_fill": int(data.get("no_fill") or 0),
            "resolved": won + lost + partial,
            "won": won,
            "lost": lost,
            "partial": partial,
            "resolved_capital": capital,
            "resolved_proceeds": float(data.get("resolved_proceeds") or 0.0),
            "pnl": pnl,
            "resolved_roi": pnl / capital if capital > 0.0 else None,
        }
        for lane in lanes:
            lane_capital = float(lane.get("resolved_capital") or 0.0)
            lane_pnl = float(lane.get("pnl") or 0.0)
            lane["resolved_roi"] = lane_pnl / lane_capital if lane_capital > 0.0 else None
        return stats, lanes

    def _status_text(self) -> str:
        status = self._read_status()
        v5, _ = self._protocol_performance(PAPER_EXECUTION_PROTOCOL_V5)
        v4, _ = self._protocol_performance(PAPER_EXECUTION_PROTOCOL_V4)
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
            f"V5 prospective positions: <b>{int(v5['total'])}</b> | open <b>{int(v5['open'])}</b> | resolved <b>{int(v5['resolved'])}</b>",
            f"Legacy V4 historical positions kept separate: <b>{int(v4['total'])}</b>",
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
        v5, lanes = self._protocol_performance(PAPER_EXECUTION_PROTOCOL_V5)
        v4, _legacy_lanes = self._protocol_performance(PAPER_EXECUTION_PROTOCOL_V4)
        maker = self._maker_performance()
        lines = [
            "📊 <b>ALL-WEATHER PAPER PERFORMANCE</b>",
            "",
            "<b>V5 PROSPECTIVE TAKER / STRUCTURAL — POST-RECEIPT EXECUTABLE LEDGER</b>",
            f"Open: <b>{int(v5['open'])}</b> | Resolved: <b>{int(v5['resolved'])}</b> | No-fill: <b>{int(v5['no_fill'])}</b>",
            f"Results: <b>{int(v5['won'])}W / {int(v5['lost'])}L / {int(v5['partial'])} partial</b>",
            f"Resolved capital: <b>${float(v5['resolved_capital']):.2f}</b>",
            f"Resolved proceeds: <b>${float(v5['resolved_proceeds']):.2f}</b>",
            f"Net P&amp;L: <b>${float(v5['pnl']):+.2f}</b>",
            f"Resolved ROI: <b>{self._pct(v5.get('resolved_roi'))}</b>",
        ]
        if lanes:
            lines.extend(["", "<b>By V5 taker / structural lane</b>"])
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
                "<b>LEGACY V4 — HISTORICAL FROZEN-QUOTE EXPERIMENT (NOT COMBINED)</b>",
                f"Positions: <b>{int(v4['total'])}</b> | resolved: <b>{int(v4['resolved'])}</b>",
                f"Historical V4 P&amp;L: <b>${float(v4['pnl']):+.2f}</b> | ROI: <b>{self._pct(v4.get('resolved_roi'))}</b>",
                "",
                "<b>PROSPECTIVE MAKER — SEPARATE QUEUE/FILL EXPERIMENT</b>",
                f"Settled maker orders with simulated fills: <b>{int(maker['settled'])}</b>",
                f"Simulated maker capital: <b>${float(maker['capital']):.2f}</b>",
                f"Maker proceeds: <b>${float(maker['proceeds']):.2f}</b>",
                f"Maker P&amp;L: <b>${float(maker['pnl']):+.2f}</b>",
                f"Maker ROI: <b>{self._pct(maker.get('roi'))}</b>",
                "",
                "Future-day, same-day, source-shock and maker model evidence is explicitly research-grade / uncalibrated where labelled.",
                "V4 historical fills, V5 post-receipt fills and maker queue fills are separate experiments and are never summed into one headline return.",
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
