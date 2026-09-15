from __future__ import annotations

"""Final operator-state corrective wrapper for the all-weather PAPER runtime.

This layer closes the human-actionability gap created by the intentionally causal
Telegram -> post-receipt validation protocol. If a delivered alert later fails final
validation, the original Telegram message is edited to an unmistakable INVALIDATED
state before its dedupe key may be released for a bounded retry.

It also makes the maker proposal wording match the final queue-uncertified policy and
fails closed if an implicit working-directory .env could alter exact-SHA runtime
configuration.

No authenticated trading, wallet, signing, order or cancellation capability is added.
"""

import argparse
import asyncio
import html
import json
from pathlib import Path

from .weather_only_live_paper import (
    DEFAULT_FORECAST_CACHE_SECONDS,
    DEFAULT_FORECAST_RAW_GAP_MIN,
    DEFAULT_INTERVAL_SECONDS,
    DEFAULT_MAX_FORECAST_EVENTS,
    _atomic_json,
)
from .weather_only_live_paper_all_signals_final_v4 import FinalAllPaperWeatherLiveServiceV4
from .weather_only_live_paper_v2 import DEFAULT_PAPER_STAKE_USD
from .weather_only_operator_state_corrective import (
    OPERATOR_STATE_CORRECTIVE_VERSION,
    TERMINAL_VISIBLE_STATUSES,
    OperatorStateCommandController,
    OperatorStatePostReceiptStore,
    OperatorStateTelegram,
)
from .weather_only_paper_corrective import CorrectiveSettlementEngine


FINAL_ALL_PAPER_RUNTIME_V5_VERSION = (
    "weather_all_paper_final_v8_operator_visible_terminal_state"
)
OPERATOR_SYNC_MIN_SIGNAL_ID_KEY = "operator_sync_min_signal_id_v1"


class FinalAllPaperWeatherLiveServiceV5(FinalAllPaperWeatherLiveServiceV4):
    def __init__(self, **kwargs) -> None:
        candidates = {Path.cwd() / ".env", Path(__file__).resolve().parents[1] / ".env"}
        if any(path.exists() for path in candidates):
            raise RuntimeError("ALL_PAPER_IMPLICIT_DOTENV_FORBIDDEN")

        super().__init__(**kwargs)

        self._operator_superseded_telegram = self.telegram
        self._operator_superseded_settlement = self.settlement
        self._operator_superseded_commands = self.commands

        self.telegram = OperatorStateTelegram(
            token=self._operator_superseded_telegram.token,
            chat_id=self._operator_superseded_telegram.chat_id,
        )
        self.positions = OperatorStatePostReceiptStore(self.db_path)
        self._operator_recovery = self.positions.reconcile_v5_after_restart()
        self.settlement = CorrectiveSettlementEngine(
            store=self.positions, telegram=self.telegram
        )
        self.commands = OperatorStateCommandController(
            telegram=self.telegram,
            store=self.positions,
            maker_store=self.maker_store,
            status_path=self.status_path,
            paper_stake_usd=self.paper_stake_usd,
        )

        marker = self.positions.get_state(OPERATOR_SYNC_MIN_SIGNAL_ID_KEY, "")
        if marker:
            self._operator_min_signal_id = int(marker)
        else:
            with self.positions._conn() as db:
                row = db.execute(
                    "SELECT COALESCE(MAX(id),0)+1 FROM weather_paper_signals"
                ).fetchone()
            self._operator_min_signal_id = int(row[0])
            self.positions.set_state(
                OPERATOR_SYNC_MIN_SIGNAL_ID_KEY, self._operator_min_signal_id
            )

    async def close(self) -> None:
        await asyncio.gather(
            self._operator_superseded_settlement.close(),
            self._operator_superseded_commands.close(),
            self._operator_superseded_telegram.close(),
            return_exceptions=True,
        )
        await super().close()

    async def send_startup(self) -> int:
        release = self.release_sha()
        return await self.telegram.send_html(
            "\n".join(
                [
                    "🟢 <b>WEATHER ALL-PAPER BOT ONLINE — OPERATOR-SYNC CORRECTIVE</b>",
                    "",
                    "Future-day GEFS-gap PAPER alerts: <b>ON — uncalibrated</b>",
                    "Same-day late-lock PAPER alerts: <b>ON — fresh WRH+NWS+GEFS31 after receipt</b>",
                    "Official-extreme source-shock PAPER alerts: <b>ON — revision-sensitive</b>",
                    "Structural underround alerts: <b>ON — theoretical only, excluded from validated P&amp;L</b>",
                    "Maker bid proposals: <b>ON — observation/research only</b>",
                    "Maker simulated fills/P&amp;L: <b>OFF — public queue position is not certified</b>",
                    "Result-lag: <b>GATED — exact WRH cutoff state not proven</b>",
                    "",
                    "A delivered directional alert is not final until its post-receipt validation succeeds.",
                    "If that final validation fails, the original Telegram alert is edited to ⛔ INVALIDATED — DO NOT ACT.",
                    "Only after that edit is confirmed may the same evidence receive a bounded later retry.",
                    "🚫 <b>NO REAL ORDERS / NO WALLET / NO SIGNING AUTHORITY</b>",
                    f"Release: <code>{html.escape(release[:12])}</code>",
                ]
            )
        )

    @staticmethod
    def _maker_proposal_message(payload: dict) -> str:
        meta = payload["fair_value_research"]
        best_bid = payload.get("current_best_bid")
        best_ask = payload.get("current_best_ask")
        return "\n".join(
            [
                "📌 <b>WEATHER PAPER MAKER PROPOSAL — QUEUE UNCERTIFIED</b>",
                f"<b>{html.escape(str(payload['event_title']))}</b>",
                f"Side: <b>{html.escape(str(payload['side']))}</b>",
                "",
                f"Hypothetical resting bid: <b>${float(payload['bid_price']):.4f}</b>",
                f"Pre-delivery best bid / ask: <b>{'n/a' if best_bid is None else f'${float(best_bid):.4f}'}</b> / <b>{'n/a' if best_ask is None else f'${float(best_ask):.4f}'}</b>",
                f"Maximum PAPER notional: <b>${float(payload['max_notional']):.2f}</b>",
                f"Observed same-price size before delivery: <b>{float(payload['pre_delivery_queue_at_bid']):.2f} shares</b>",
                "",
                f"Raw GEFS support: <b>{100.0 * float(meta['raw_probability']):.1f}%</b>",
                f"Conditional research edge/share: <b>${float(payload['conditional_edge_per_share']):.4f}</b>",
                "",
                "⚠️ <b>RESEARCH PROPOSAL ONLY.</b> Public trade data cannot certify the true resting queue position.",
                "SELL trade prints are observed for diagnostics only; this runtime does NOT convert them into simulated maker fills or maker P&amp;L.",
                "No real order is placed.",
            ]
        )

    def _operator_candidate_signal_ids(self) -> list[int]:
        placeholders = ",".join("?" for _ in TERMINAL_VISIBLE_STATUSES)
        query = (
            "SELECT id FROM weather_paper_signals WHERE id>=? "
            f"AND telegram_message_id IS NOT NULL AND status IN ({placeholders}) "
            "ORDER BY id"
        )
        params = [self._operator_min_signal_id, *sorted(TERMINAL_VISIBLE_STATUSES)]
        with self.positions._conn() as db:
            return [int(row[0]) for row in db.execute(query, params)]

    async def _sync_operator_messages(self) -> dict:
        signal_ids = await asyncio.to_thread(self._operator_candidate_signal_ids)
        if signal_ids:
            await asyncio.to_thread(
                self.positions.ensure_operator_sync_records,
                signal_ids=signal_ids,
            )

        errors: list[str] = []
        rows = await asyncio.to_thread(self.positions.pending_operator_sync, 100)
        for row in rows:
            signal_id = int(row["signal_id"])
            try:
                await self.telegram.edit_html(
                    int(row["telegram_message_id"]),
                    str(row["message_text"]),
                )
            except Exception as exc:
                code = getattr(exc, "code", type(exc).__name__)
                await asyncio.to_thread(
                    self.positions.mark_operator_sync_failed,
                    signal_id,
                    str(code),
                )
                errors.append(f"OPERATOR_SYNC:{signal_id}:{code}")
                continue
            await asyncio.to_thread(
                self.positions.mark_operator_sync_applied, signal_id
            )

        summary = await asyncio.to_thread(self.positions.operator_sync_summary)
        summary["errors"] = errors
        return summary

    async def _mark_v5_not_actionable(
        self, signal_id: int, candidate: dict, reason: str
    ) -> None:
        await super()._mark_v5_not_actionable(signal_id, candidate, reason)
        await self._sync_operator_messages()

    async def run_cycle(self) -> dict:
        pre_sync = await self._sync_operator_messages()
        status = dict(await super().run_cycle())
        post_sync = await self._sync_operator_messages()

        healthy = bool(pre_sync.get("healthy")) and bool(post_sync.get("healthy"))
        errors = list(pre_sync.get("errors") or []) + list(post_sync.get("errors") or [])
        status.update(
            {
                "final_all_paper_runtime_v5_version": FINAL_ALL_PAPER_RUNTIME_V5_VERSION,
                "operator_state_corrective_version": OPERATOR_STATE_CORRECTIVE_VERSION,
                "operator_visible_invalidation_required": True,
                "operator_invalidation_transport": "IDEMPOTENT_EDIT_MESSAGE_TEXT",
                "operator_retry_release_requires_visible_invalidation": True,
                "operator_retry_cooldown_seconds": 180.0,
                "operator_retry_max_per_evidence": 3,
                "implicit_dotenv_forbidden": True,
                "maker_queue_certified": False,
                "maker_simulated_fill_accounting_enabled": False,
                "operator_message_sync": post_sync,
                "operator_message_sync_healthy": healthy,
                "operator_message_sync_errors": errors,
                "financial_delivery": False,
                "financial_authority": False,
                "automatic_order_placement": False,
                "wallet_or_order_api_loaded": False,
            }
        )
        if not healthy:
            status["cycle_ok"] = False
            status["operator_all_lanes_healthy"] = False
        _atomic_json(self.status_path, status)
        return status


async def _main(args) -> int:
    service = FinalAllPaperWeatherLiveServiceV5(
        db_path=args.db,
        status_path=args.status,
        release_file=args.release_file,
        interval_seconds=args.interval_seconds,
        forecast_cache_seconds=args.forecast_cache_seconds,
        forecast_raw_gap_min=args.forecast_raw_gap_min,
        max_forecast_events=args.max_forecast_events,
        paper_stake_usd=args.paper_stake_usd,
    )
    try:
        if args.once:
            await service.send_startup()
            status = await service.run_cycle()
            print(json.dumps(status, sort_keys=True, indent=2))
            return 0 if status.get("operator_all_lanes_healthy") is True else 2
        await service.loop()
        return 0
    finally:
        await service.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--status", type=Path, required=True)
    parser.add_argument("--release-file", type=Path, required=True)
    parser.add_argument("--interval-seconds", type=float, default=DEFAULT_INTERVAL_SECONDS)
    parser.add_argument(
        "--forecast-cache-seconds", type=float, default=DEFAULT_FORECAST_CACHE_SECONDS
    )
    parser.add_argument(
        "--forecast-raw-gap-min", type=float, default=DEFAULT_FORECAST_RAW_GAP_MIN
    )
    parser.add_argument(
        "--max-forecast-events", type=int, default=DEFAULT_MAX_FORECAST_EVENTS
    )
    parser.add_argument(
        "--paper-stake-usd", type=float, default=DEFAULT_PAPER_STAKE_USD
    )
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    raise SystemExit(asyncio.run(_main(args)))


if __name__ == "__main__":
    main()
