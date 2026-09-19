from __future__ import annotations

import html
import math
import time

from .weather_only_operator_state_corrective import OperatorStateCommandController
from .weather_only_paper_corrective import PAPER_EXECUTION_PROTOCOL_V4, STATUS_MAX_AGE_SECONDS
from .weather_only_paper_post_receipt import PAPER_EXECUTION_PROTOCOL_V5


class OperatorStateCommandControllerV10(OperatorStateCommandController):
    """Final command facade with segregated maker and Result-Lag research evidence."""

    def __init__(self, *, result_lag_store=None, **kwargs) -> None:
        super().__init__(**kwargs)
        self.result_lag_store = result_lag_store

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

    @staticmethod
    def _friendly_issue(code: object) -> str:
        value = str(code or "").strip()
        known = {
            "PRESCREEN_CLOB:BOOK_BATCH_INCOMPLETE": (
                "Polymarket returned an incomplete order-book batch on the last scan. "
                "The bot skipped that affected scan instead of guessing and will retry automatically."
            ),
            "GLOBAL_WEATHER_RECALL_STALE": (
                "The full Polymarket weather-market scan became stale, so new paper entries were paused."
            ),
            "GLOBAL_WEATHER_RECALL_INCOMPLETE": (
                "The full Polymarket weather-market scan did not finish, so new paper entries were paused."
            ),
        }
        if value in known:
            return known[value]
        return "Technical issue: " + html.escape(value)

    def _status_text(self) -> str:
        status = self._read_status()
        main, _lanes = self._protocol_performance(PAPER_EXECUTION_PROTOCOL_V5)
        maker = self.maker_store.summary()
        result_lag = (
            self.result_lag_store.stats()
            if self.result_lag_store is not None
            else {"open": 0, "resolved": 0}
        )
        try:
            finished = float(status.get("finished_at") or 0.0)
        except (TypeError, ValueError, OverflowError):
            finished = 0.0
        age = time.time() - finished if finished > 0.0 else float("inf")
        healthy = (
            status.get("cycle_ok") is True
            and status.get("operator_all_lanes_healthy") is True
            and 0.0 <= age <= STATUS_MAX_AGE_SECONDS
        )
        icon = "🟢" if healthy else "🔴"
        headline = "RUNNING NORMALLY" if healthy else "NEEDS ATTENTION"

        lines = [
            f"{icon} <b>PAPER WEATHER BOT — {headline}</b>",
            f"Last full scan: <b>{int(max(0.0, age)) if math.isfinite(age) else 'unknown'}s ago</b>",
            "",
            "<b>STRATEGIES</b>",
            f"🌤 Future-day forecast: {self._strategy_state(status.get('paper_telegram_delivery'))}",
            f"⏰ Same-day late-lock: {self._strategy_state(status.get('same_day_paper_delivery_enabled'))}",
            f"⚡ Official-source shock: {self._strategy_state(status.get('source_shock_paper_delivery_enabled'))}",
            f"🧩 Structural underround: {self._strategy_state(status.get('structural_paper_delivery_enabled'))}",
            f"📌 Maker research: {self._strategy_state(status.get('maker_paper_delivery_enabled'))}",
            f"🌙 End-of-day Result-Lag: {self._strategy_state(status.get('result_lag_paper_delivery_enabled'))}",
            "",
            "<b>CURRENT PAPER ACTIVITY</b>",
            (
                f"Open main paper trades: <b>{int(main.get('open') or 0)}</b> | "
                f"Finished: <b>{int(main.get('resolved') or 0)}</b>"
            ),
            f"Excluded old/invalid records: <b>{int(main.get('quarantined') or 0)}</b>",
            f"Open maker research orders: <b>{int(maker.get('active_orders') or 0)}</b>",
            f"Open Result-Lag research trades: <b>{int(result_lag.get('open') or 0)}</b>",
            "",
            "<b>SAFETY</b>",
            "Real-money trading: <b>OFF</b>",
            "Wallet/signing/order authority: <b>OFF</b>",
        ]

        errors = (
            list(status.get("errors") or [])
            + list(status.get("maker_errors") or [])
            + list(status.get("source_shock_errors") or [])
            + list(status.get("result_lag_research_errors") or [])
        )
        if errors:
            lines.extend(["", "<b>LAST SCAN ISSUE</b>"])
            for value in errors[:5]:
                lines.append("⚠️ " + self._friendly_issue(value))
        return "\n".join(lines)

    @staticmethod
    def _friendly_lane_totals(rows: list[dict], lanes: set[str]) -> dict:
        out = {
            "total": 0,
            "open": 0,
            "no_fill": 0,
            "quarantined": 0,
            "won": 0,
            "lost": 0,
            "partial": 0,
            "resolved_capital": 0.0,
            "pnl": 0.0,
        }
        for row in rows:
            if str(row.get("lane") or "") not in lanes:
                continue
            out["total"] += int(row.get("total") or 0)
            out["open"] += int(row.get("open_n") or 0)
            out["no_fill"] += int(row.get("no_fill") or 0)
            out["quarantined"] += int(row.get("quarantined") or 0)
            out["won"] += int(row.get("won") or 0)
            out["lost"] += int(row.get("lost") or 0)
            out["partial"] += int(row.get("partial") or 0)
            out["resolved_capital"] += float(row.get("resolved_capital") or 0.0)
            out["pnl"] += float(row.get("pnl") or 0.0)
        out["resolved"] = out["won"] + out["lost"] + out["partial"]
        out["roi"] = (
            out["pnl"] / out["resolved_capital"]
            if out["resolved_capital"] > 0.0
            else None
        )
        return out

    @staticmethod
    def _strategy_state(enabled: object) -> str:
        return "🟢 ON" if enabled is True else "🔴 OFF"

    def _simple_strategy_lines(self, title: str, enabled: object, stats: dict) -> list[str]:
        lines = [
            f"<b>{title}</b> — {self._strategy_state(enabled)}",
            (
                f"Open <b>{int(stats.get('open') or 0)}</b> | "
                f"Finished <b>{int(stats.get('resolved') or 0)}</b> "
                f"(<b>{int(stats.get('won') or 0)}W / "
                f"{int(stats.get('lost') or 0)}L / "
                f"{int(stats.get('partial') or 0)} partial</b>)"
            ),
            (
                f"Paper P&amp;L <b>${float(stats.get('pnl') or 0.0):+.2f}</b> | "
                f"ROI <b>{self._pct(stats.get('roi'))}</b>"
            ),
        ]
        if int(stats.get("no_fill") or 0):
            lines.append(
                f"Could not simulate a fill: <b>{int(stats.get('no_fill') or 0)}</b>"
            )
        return lines

    def _stats_text(self) -> str:
        status = self._read_status()
        main, lanes = self._protocol_performance(PAPER_EXECUTION_PROTOCOL_V5)
        old_test, _old_test_lanes = self._protocol_performance(PAPER_EXECUTION_PROTOCOL_V4)

        by_strategy = {
            "forecast": self._friendly_lane_totals(
                lanes, {"weather_forecast_raw_gap"}
            ),
            "same_day": self._friendly_lane_totals(
                lanes, {"weather_same_day_friend_lock"}
            ),
            "source_shock": self._friendly_lane_totals(
                lanes, {"weather_official_extreme_new_exclusion"}
            ),
            "structural": self._friendly_lane_totals(
                lanes,
                {
                    "weather_binary_pair_underround",
                    "weather_complete_bucket_underround",
                },
            ),
        }

        maker_summary = self.maker_store.summary()
        maker_perf = self._maker_performance()
        maker_legacy = maker_perf.get("legacy_excluded") or {}
        maker_research = maker_perf.get("research_excluded") or {}
        maker_research_settled = (
            int(maker_legacy.get("settled") or 0)
            + int(maker_research.get("settled") or 0)
        )
        maker_research_pnl = (
            float(maker_legacy.get("pnl") or 0.0)
            + float(maker_research.get("pnl") or 0.0)
        )

        result_lag = (
            self.result_lag_store.stats()
            if self.result_lag_store is not None
            else {
                "total": 0,
                "open": 0,
                "resolved": 0,
                "won": 0,
                "lost": 0,
                "partial": 0,
                "resolved_capital": 0.0,
                "pnl": 0.0,
                "roi": None,
            }
        )

        lines = [
            "📊 <b>PAPER TRADING SUMMARY</b>",
            "",
            "<b>MAIN PAPER TRADES</b>",
            (
                f"Open: <b>{int(main.get('open') or 0)}</b> | "
                f"Finished: <b>{int(main.get('resolved') or 0)}</b> | "
                f"No fill: <b>{int(main.get('no_fill') or 0)}</b>"
            ),
            (
                f"Results: <b>{int(main.get('won') or 0)}W / "
                f"{int(main.get('lost') or 0)}L / "
                f"{int(main.get('partial') or 0)} partial</b>"
            ),
            f"Paper P&amp;L: <b>${float(main.get('pnl') or 0.0):+.2f}</b>",
            f"ROI on finished trades: <b>{self._pct(main.get('resolved_roi'))}</b>",
            "",
            *self._simple_strategy_lines(
                "🌤 Future-day forecast",
                status.get("paper_telegram_delivery"),
                by_strategy["forecast"],
            ),
            "",
            *self._simple_strategy_lines(
                "⏰ Same-day late-lock",
                status.get("same_day_paper_delivery_enabled"),
                by_strategy["same_day"],
            ),
            "",
            *self._simple_strategy_lines(
                "⚡ Official-source shock",
                status.get("source_shock_paper_delivery_enabled"),
                by_strategy["source_shock"],
            ),
            "",
            *self._simple_strategy_lines(
                "🧩 Structural underround",
                status.get("structural_paper_delivery_enabled"),
                by_strategy["structural"],
            ),
            "",
            "<b>📌 Maker research</b> — "
            + self._strategy_state(status.get("maker_paper_delivery_enabled")),
            (
                f"Open virtual orders: <b>{int(maker_summary.get('active_orders') or 0)}</b> | "
                f"Research settlements: <b>{maker_research_settled}</b>"
            ),
            f"Research P&amp;L (not counted in main total): <b>${maker_research_pnl:+.2f}</b>",
            "Maker results stay separate unless the simulated fill can be proven from queue evidence.",
            "",
            "<b>🌙 End-of-day Result-Lag</b> — "
            + self._strategy_state(status.get("result_lag_paper_delivery_enabled")),
            (
                f"Open <b>{int(result_lag.get('open') or 0)}</b> | "
                f"Finished <b>{int(result_lag.get('resolved') or 0)}</b> "
                f"(<b>{int(result_lag.get('won') or 0)}W / "
                f"{int(result_lag.get('lost') or 0)}L / "
                f"{int(result_lag.get('partial') or 0)} partial</b>)"
            ),
            (
                f"Research P&amp;L (separate): "
                f"<b>${float(result_lag.get('pnl') or 0.0):+.2f}</b> | "
                f"ROI <b>{self._pct(result_lag.get('roi'))}</b>"
            ),
            "This strategy is active in PAPER but remains revision-sensitive, so its P&amp;L is kept separate.",
            "",
            "<b>NOT COUNTED IN THE MAIN RESULTS</b>",
            (
                f"Excluded old/invalid records: "
                f"<b>{int(main.get('quarantined') or 0)}</b>"
            ),
            (
                f"Old test data kept only for audit: "
                f"<b>{int(old_test.get('total') or 0)} positions</b> "
                f"(historical P&amp;L ${float(old_test.get('pnl') or 0.0):+.2f})"
            ),
            "",
            "📒 Everything here is simulated. No real order was placed.",
        ]
        return "\n".join(lines)

    def _positions_text(self) -> str:
        text = super()._positions_text()
        text = text.replace(
            "Prospective maker orders:",
            "Research-only maker proposals/orders (queue uncertified):",
        )
        if self.result_lag_store is None:
            return text
        rows = self.result_lag_store.open_positions(10)
        lines = ["", "<b>Open Result-Lag research positions (excluded):</b>"]
        if not rows:
            lines.append("None.")
        else:
            for row in rows:
                lines.append(
                    f"• #{int(row['id'])} {html.escape(str(row.get('station') or ''))} "
                    f"{html.escape(str(row.get('target_date') or ''))} "
                    f"provisional {int(row.get('provisional_value_f') or 0)}°F "
                    f"| capital $__DOLLAR__{float(row.get('capital_used') or 0.0):.2f}"
                )
        return (text + "\n" + "\n".join(lines)).replace("$__DOLLAR__", "$")
