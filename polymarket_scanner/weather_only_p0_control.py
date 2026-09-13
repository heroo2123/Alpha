from __future__ import annotations

"""P0 paper settlement and operator-friendly Telegram commands.

Only validated prospective positions are performance evidence.  Settlement is read
from finalized Polygon CTF payout vectors, never from Gamma indicative prices.  The
command output deliberately separates signal delivery, simulated fill, open position,
resolution, no-fill and quarantine states so the operator can understand the ledger
without knowing internal database terminology.
"""

import asyncio
import html
import json
import math
import time
from pathlib import Path

from .weather_only_ctf_finality import (
    CTF_FINALITY_VERSION,
    PolygonCTFFinalityClient,
    WeatherCTFFinalityError,
)
from .weather_only_p0_positions import (
    DELIVERY_ACKNOWLEDGED,
    DELIVERY_EXPIRED,
    DELIVERY_PENDING,
    DELIVERY_SENDING,
    DELIVERY_UNCERTAIN,
    P0_COHORT_ID,
    P0WeatherPaperPositionStore,
    VALIDATION_VALIDATED,
)
from .weather_only_paper_control import WeatherPaperCommandController, _age, _pct, _price, _short


P0_SETTLEMENT_VERSION = "weather_p0_settlement_v1_finalized_ctf_fair_cursor"
P0_COMMAND_VERSION = "weather_p0_commands_v1_plain_ledger_lifecycle"
DEFAULT_SETTLEMENT_BATCH = 100


class P0WeatherPaperSettlementEngine:
    def __init__(
        self,
        *,
        store: P0WeatherPaperPositionStore,
        telegram,
        finality: PolygonCTFFinalityClient | None = None,
        batch_size: int = DEFAULT_SETTLEMENT_BATCH,
    ) -> None:
        self.store = store
        self.telegram = telegram
        self.finality = finality or PolygonCTFFinalityClient()
        self._owns_finality = finality is None
        self.batch_size = max(1, min(500, int(batch_size)))

    async def close(self) -> None:
        if self._owns_finality:
            await self.finality.close()

    def _next_open_batch(self) -> list[dict]:
        cursor = int(self.store.get_state("p0_settlement_cursor", "0") or 0)
        query = """
            SELECT p.*,s.payload_json,d.cohort_id,d.validation_status
            FROM weather_paper_positions p
            JOIN weather_paper_signals s ON s.id=p.signal_id
            JOIN weather_p0_decisions d ON d.signal_id=s.id
            WHERE p.status='OPEN'
              AND d.validation_status=?
              AND p.id>?
            ORDER BY p.id
            LIMIT ?
        """
        with self.store._conn() as db:
            rows = [dict(row) for row in db.execute(
                query, (VALIDATION_VALIDATED, cursor, self.batch_size)
            )]
            if not rows and cursor > 0:
                cursor = 0
                rows = [dict(row) for row in db.execute(
                    query, (VALIDATION_VALIDATED, cursor, self.batch_size)
                )]
        if rows:
            self.store.set_state("p0_settlement_cursor", int(rows[-1]["id"]))
        elif cursor != 0:
            self.store.set_state("p0_settlement_cursor", 0)
        return rows

    @staticmethod
    def _payload(row: dict) -> dict:
        try:
            payload = json.loads(str(row.get("payload_json") or "{}"))
        except Exception:
            return {}
        return payload if isinstance(payload, dict) else {}

    @staticmethod
    def _resolution_message(position: dict) -> str:
        status = str(position.get("status") or "")
        icon = "✅" if status == "WON" else "❌" if status == "LOST" else "🟡"
        pnl = float(position.get("pnl") or 0.0)
        capital = float(position.get("capital_used") or 0.0)
        proceeds = float(position.get("proceeds") or 0.0)
        payout = float(position.get("settlement_payout_per_unit") or 0.0)
        return "\n".join([
            f"{icon} <b>PAPER RESULT — {html.escape(status)}</b>",
            f"<b>{html.escape(_short(position.get('title'), 85))}</b>",
            f"Position #{int(position['id'])} • paper side <b>{html.escape(str(position.get('side') or ''))}</b>",
            "",
            f"Simulated money used: <b>${capital:.2f}</b>",
            f"Final payout per share: <b>${payout:.4f}</b>",
            f"Paper money returned: <b>${proceeds:.2f}</b>",
            f"Result: <b>{'+' if pnl >= 0 else ''}${pnl:.2f}</b> ({_pct(position.get('roi'))})",
            "",
            "🔐 Result source: finalized on-chain Polymarket CTF payout vector.",
            "📒 Paper simulation only — no real order was placed.",
        ])

    async def _notify_resolved(self) -> tuple[int, list[str]]:
        sent = 0
        errors: list[str] = []
        rows = await asyncio.to_thread(self.store.resolved_without_notification, 50)
        for position in rows:
            try:
                message_id = await self.telegram.send_html(self._resolution_message(position))
            except Exception as exc:
                errors.append(f"SETTLEMENT_NOTIFY:{type(exc).__name__}")
                continue
            await asyncio.to_thread(
                self.store.mark_settlement_notified, int(position["id"]), int(message_id)
            )
            sent += 1
        return sent, errors

    async def settle_once(self) -> dict:
        positions = await asyncio.to_thread(self._next_open_batch)
        checked = 0
        resolved = 0
        unresolved = 0
        errors: list[str] = []
        cache: dict[str, object] = {}
        for position in positions:
            checked += 1
            payload = self._payload(position)
            condition = str(payload.get("condition_id") or "").strip().lower()
            index_raw = payload.get("ctf_outcome_index")
            try:
                outcome_index = int(index_raw)
            except (TypeError, ValueError):
                errors.append(f"SETTLEMENT:{position.get('id')}:CTF_OUTCOME_INDEX_MISSING")
                continue
            if outcome_index not in {0, 1} or not condition:
                errors.append(f"SETTLEMENT:{position.get('id')}:CTF_IDENTITY_INVALID")
                continue
            if condition not in cache:
                try:
                    cache[condition] = await self.finality.finalized_binary_payout(condition)
                    self.store.set_state("p0_settlement_last_rpc_success_at", time.time())
                    self.store.set_state("p0_settlement_last_rpc_error", "")
                except WeatherCTFFinalityError as exc:
                    cache[condition] = exc
                    errors.append(f"SETTLEMENT:{condition}:{exc.code}")
                    self.store.set_state("p0_settlement_last_rpc_error", exc.code)
                except Exception as exc:
                    cache[condition] = exc
                    errors.append(f"SETTLEMENT:{condition}:{type(exc).__name__}")
                    self.store.set_state("p0_settlement_last_rpc_error", type(exc).__name__)
            evidence = cache[condition]
            if isinstance(evidence, BaseException):
                continue
            if evidence is None:
                unresolved += 1
                continue
            if getattr(evidence, "condition_id", "") != condition or getattr(evidence, "finalized", False) is not True:
                errors.append(f"SETTLEMENT:{position.get('id')}:CTF_FINALITY_IDENTITY_MISMATCH")
                continue
            vector = tuple(getattr(evidence, "payout_vector", ()))
            if len(vector) != 2:
                errors.append(f"SETTLEMENT:{position.get('id')}:CTF_VECTOR_INVALID")
                continue
            payout = float(vector[outcome_index])
            settlement_evidence = dict(evidence.as_dict())
            settlement_evidence.update({
                "settlement_engine": P0_SETTLEMENT_VERSION,
                "selected_outcome_index": outcome_index,
                "selected_token_id": str(position.get("token_id") or ""),
                "selected_side": str(position.get("side") or ""),
                "cohort_id": str(position.get("cohort_id") or P0_COHORT_ID),
                "financial_authority": False,
            })
            try:
                row = await asyncio.to_thread(
                    self.store.resolve_position,
                    int(position["id"]),
                    payout,
                    settlement_evidence,
                )
            except Exception as exc:
                errors.append(f"SETTLEMENT:{position.get('id')}:{type(exc).__name__}")
                continue
            if row is not None:
                resolved += 1

        notified, notify_errors = await self._notify_resolved()
        errors.extend(notify_errors)
        self.store.set_state("p0_settlement_last_cycle_at", time.time())
        self.store.set_state("p0_settlement_last_cycle_errors", json.dumps(errors[-20:]))
        return {
            "version": P0_SETTLEMENT_VERSION,
            "finality_source_version": CTF_FINALITY_VERSION,
            "open_checked": checked,
            "still_unresolved": unresolved,
            "resolved_now": resolved,
            "resolution_messages_sent": notified,
            "cursor": self.store.get_state("p0_settlement_cursor", "0"),
            "errors": errors,
            "financial_authority": False,
        }


class P0WeatherPaperCommandController(WeatherPaperCommandController):
    def __init__(self, *, expected_cycle_seconds: float = 180.0, **kwargs) -> None:
        super().__init__(**kwargs)
        self.expected_cycle_seconds = max(30.0, float(expected_cycle_seconds))

    def _fresh_health(self, status: dict) -> tuple[bool, float | None]:
        try:
            finished = float(status.get("finished_at"))
        except (TypeError, ValueError, OverflowError):
            return False, None
        if not math.isfinite(finished) or finished <= 0.0:
            return False, None
        age = max(0.0, time.time() - finished)
        max_age = max(2.5 * self.expected_cycle_seconds, 600.0)
        return bool(status.get("cycle_ok")) and age <= max_age, age

    def _status_text(self) -> str:
        status = self._read_status()
        stats = self.store.stats()
        healthy, age = self._fresh_health(status)
        command_error = str(self.store.get_state("telegram_command_last_error", "") or "")
        settlement_error = str(self.store.get_state("p0_settlement_last_rpc_error", "") or "")
        settlement_at = self.store.get_state("p0_settlement_last_cycle_at", "")
        outbox = stats.get("delivery_states") or {}
        active = int(stats.get("open") or 0)
        resolved = int(stats.get("resolved") or 0)
        icon = "🟢" if healthy and not command_error and not settlement_error else "🟠" if healthy else "🔴"
        lines = [
            f"{icon} <b>WEATHER PAPER BOT</b>",
            "",
            f"Scanner: <b>{'OK' if healthy else 'STALE / DEGRADED'}</b> • last full cycle {_age(status.get('finished_at'))}",
            f"Telegram commands: <b>{'OK' if not command_error else 'ERROR'}</b>",
            f"Final-result checker: <b>{'OK' if not settlement_error else 'ERROR'}</b> • last cycle {_age(settlement_at)}",
            "🚫 Real trading: <b>OFF</b>",
            "",
            "📒 <b>VALIDATED PAPER POSITIONS</b>",
            f"Open now: <b>{active}</b>",
            f"Finished: <b>{resolved}</b> ({int(stats.get('won') or 0)} won / {int(stats.get('lost') or 0)} lost / {int(stats.get('partial') or 0)} partial)",
            f"Could not be simulated at the quoted price: <b>{int(stats.get('no_fill') or 0)}</b>",
            f"Quarantined / excluded from performance: <b>{int(stats.get('quarantined') or 0)}</b>",
            f"Delivery uncertain: <b>{int(outbox.get(DELIVERY_UNCERTAIN, 0))}</b> • expired before send: <b>{int(outbox.get(DELIVERY_EXPIRED, 0))}</b>",
            "",
            f"Money currently tied up in open paper positions: <b>${float(stats.get('open_capital') or 0.0):.2f}</b>",
            f"Realized paper P&amp;L: <b>${float(stats.get('pnl') or 0.0):+.2f}</b>",
        ]
        errors = list(status.get("errors") or [])
        if command_error:
            errors.append(f"COMMANDS:{command_error}")
        if settlement_error:
            errors.append(f"SETTLEMENT:{settlement_error}")
        if errors:
            lines.extend(["", "⚠️ <b>Needs attention:</b> " + html.escape(", ".join(str(x) for x in errors)[:700])])
        return "\n".join(lines)

    def _stats_text(self) -> str:
        stats = self.store.stats()
        fills = int(stats.get("open") or 0) + int(stats.get("resolved") or 0)
        returned = float(stats.get("resolved_proceeds") or 0.0)
        resolved_capital = float(stats.get("resolved_capital") or 0.0)
        outbox = stats.get("delivery_states") or {}
        lines = [
            "📊 <b>PAPER RESULTS — VALIDATED COHORT ONLY</b>",
            "Old, invalid or unreconstructable signals are excluded from these performance numbers.",
            "",
            f"Paper trades actually simulated: <b>{fills}</b>",
            f"• Still open: <b>{int(stats.get('open') or 0)}</b>",
            f"• Finished: <b>{int(stats.get('resolved') or 0)}</b>",
            f"• No-fill / below minimum size: <b>{int(stats.get('no_fill') or 0)}</b>",
            f"• Quarantined: <b>{int(stats.get('quarantined') or 0)}</b>",
            f"• Telegram delivery uncertain: <b>{int(outbox.get(DELIVERY_UNCERTAIN, 0))}</b>",
            "",
            f"Results: <b>{int(stats.get('won') or 0)} WON / {int(stats.get('lost') or 0)} LOST / {int(stats.get('partial') or 0)} PARTIAL</b>",
            f"Win rate (full wins/losses only): <b>{_pct(stats.get('win_rate'))}</b>",
            "",
            "💵 <b>MONEY</b>",
            f"Total simulated capital put into valid fills so far: <b>${float(stats.get('capital_all') or 0.0):.2f}</b>",
            f"Currently open: <b>${float(stats.get('open_capital') or 0.0):.2f}</b>",
            f"Capital in finished trades: <b>${resolved_capital:.2f}</b>",
            f"Money returned by finished trades: <b>${returned:.2f}</b>",
            f"Realized P&amp;L: <b>${float(stats.get('pnl') or 0.0):+.2f}</b>",
            f"ROI on finished trades: <b>{_pct(stats.get('resolved_roi'))}</b>",
            "",
            f"Prospective validated cohort: <code>{html.escape(str(stats.get('validated_cohort') or P0_COHORT_ID))}</code>",
            "Final results are counted only after finalized on-chain CTF payouts are available.",
        ]
        return "\n".join(lines)

    def _positions_text(self) -> str:
        rows = self.store.open_positions(10)
        lines = ["📂 <b>OPEN PAPER POSITIONS</b>"]
        if not rows:
            lines.append("Nothing is currently open in the validated paper ledger.")
            return "\n".join(lines)
        lines.append(f"Showing {min(len(rows),10)} most relevant open position(s).")
        for row in rows:
            capital = float(row.get("capital_used") or 0.0)
            max_payout = float(row.get("maximum_payout") or 0.0)
            lines.extend([
                "",
                f"<b>#{int(row['id'])} • BUY {html.escape(str(row.get('side') or ''))}</b>",
                f"{html.escape(_short(row.get('title'), 82))}",
                f"Entry cost/share: <b>{_price(row.get('entry_cost_per_unit'))}</b>",
                f"Shares simulated: <b>{float(row.get('filled_units') or 0.0):.4f}</b>",
                f"Money used: <b>${capital:.2f}</b>",
                f"If this token pays $1: return <b>${max_payout:.2f}</b> • max P&amp;L <b>${max_payout-capital:+.2f}</b>",
                f"Opened: {_age(row.get('opened_at'))}",
            ])
        return "\n".join(lines)

    def _history_text(self) -> str:
        rows = self.store.recent_positions(10, resolved_only=True)
        lines = ["🏁 <b>FINISHED PAPER POSITIONS</b>"]
        if not rows:
            lines.append("No validated position has a finalized result yet.")
            return "\n".join(lines)
        for row in rows:
            capital = float(row.get("capital_used") or 0.0)
            proceeds = float(row.get("proceeds") or 0.0)
            pnl = float(row.get("pnl") or 0.0)
            icon = "✅" if row.get("status") == "WON" else "❌" if row.get("status") == "LOST" else "🟡"
            lines.extend([
                "",
                f"{icon} <b>#{int(row['id'])} {html.escape(str(row.get('status') or ''))}</b> • BUY {html.escape(str(row.get('side') or ''))}",
                html.escape(_short(row.get("title"), 78)),
                f"Used <b>${capital:.2f}</b> → returned <b>${proceeds:.2f}</b>",
                f"P&amp;L <b>{'+' if pnl >= 0 else ''}${pnl:.2f}</b> ({_pct(row.get('roi'))})",
                "Result proof: finalized Polymarket CTF payout.",
            ])
        return "\n".join(lines)

    def _recent_pipeline(self) -> list[dict]:
        with self.store._conn() as db:
            return [dict(row) for row in db.execute(
                """
                SELECT s.id,s.event_id,s.side,s.entry_cost,s.created_at,s.status AS signal_status,
                       d.validation_status,o.delivery_state,p.id AS position_id,p.status AS position_status
                FROM weather_paper_signals s
                LEFT JOIN weather_p0_decisions d ON d.signal_id=s.id
                LEFT JOIN weather_p0_delivery_outbox o ON o.signal_id=s.id
                LEFT JOIN weather_paper_positions p ON p.signal_id=s.id
                ORDER BY s.id DESC LIMIT 10
                """
            )]

    def _recent_text(self) -> str:
        rows = self._recent_pipeline()
        lines = ["🧾 <b>RECENT SIGNAL PIPELINE</b>", "This shows what happened to each alert, not just whether it was detected."]
        if not rows:
            lines.append("No signals stored yet.")
            return "\n".join(lines)
        for row in rows:
            validation = str(row.get("validation_status") or "LEGACY / UNVERIFIED")
            delivery = str(row.get("delivery_state") or "NOT IN P0 OUTBOX")
            position = str(row.get("position_status") or "NO PAPER POSITION")
            lines.extend([
                "",
                f"<b>Signal #{int(row['id'])} • {html.escape(str(row.get('side') or ''))}</b> @ {_price(row.get('entry_cost'))}",
                f"Validation: <b>{html.escape(validation)}</b>",
                f"Telegram: <b>{html.escape(delivery)}</b>",
                f"Paper position: <b>{html.escape(position)}</b>",
            ])
        return "\n".join(lines)

    @staticmethod
    def _help_text() -> str:
        return "\n".join([
            "🤖 <b>WEATHER PAPER COMMANDS</b>",
            "",
            "/status — Is the bot actually healthy right now? How many positions are open?",
            "/stats — How many valid paper trades, wins/losses, money used and P&amp;L?",
            "/positions — Exactly which validated paper positions are OPEN now?",
            "/history — Which positions finished, and how much did each win/lose?",
            "/recent — What happened to the latest signals (validated/sent/filled/quarantined)?",
            "/help — Show this explanation again.",
            "",
            "Important: a SIGNAL is an alert candidate. A POSITION exists only if the P0 validation and frozen paper-fill rules passed. Quarantined and uncertain rows never count as strategy profit/loss.",
        ])
