from __future__ import annotations

"""Final all-weather PAPER entrypoint.

This thin wrapper freezes the operator-facing identity above V8. Deployment gates can
therefore prove that the complete source-shock + post-receipt + maker-corrective stack
finished a cycle rather than accepting an inherited intermediate status snapshot.
The independent-review corrective layer additionally refuses to hide inherited safety
flag drift and installs stricter V5/maker/operator persistence boundaries.
"""

import argparse
import asyncio
import html
import json
from pathlib import Path

from .weather_only_independent_review_corrective import (
    INDEPENDENT_REVIEW_CORRECTIVE_VERSION,
    IndependentReviewAllPaperCommandController,
    IndependentReviewMakerAccountingStore,
    IndependentReviewPostReceiptStore,
)
from .weather_only_live_paper import (
    DEFAULT_FORECAST_CACHE_SECONDS,
    DEFAULT_FORECAST_RAW_GAP_MIN,
    DEFAULT_INTERVAL_SECONDS,
    DEFAULT_MAX_FORECAST_EVENTS,
    _atomic_json,
)
from .weather_only_live_paper_all_signals_v8 import AllPaperWeatherLiveV8Service
from .weather_only_live_paper_v2 import DEFAULT_PAPER_STAKE_USD
from .weather_only_paper_corrective import CorrectiveSettlementEngine


FINAL_ALL_PAPER_RUNTIME_VERSION = (
    "weather_all_paper_final_v4_independent_review_strict_fail_closed"
)


class FinalAllPaperSafetyInvariantError(RuntimeError):
    def __init__(self, code: str):
        self.code = str(code)
        super().__init__(self.code)


class FinalAllPaperWeatherLiveService(AllPaperWeatherLiveV8Service):
    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)

        # Replace only the persistence/operator facades.  The reviewed V7/V8 strategy,
        # public-data, CLOB and maker-stream logic remains unchanged.
        old_maker_store = self.maker_store
        old_maker_store.close()
        self.maker_store = IndependentReviewMakerAccountingStore(self.db_path)

        self._final_review_superseded_settlement = self.settlement
        self._final_review_superseded_commands = self.commands
        self.positions = IndependentReviewPostReceiptStore(self.db_path)
        self._final_review_v5_recovery = self.positions.reconcile_v5_after_restart()
        self.settlement = CorrectiveSettlementEngine(
            store=self.positions, telegram=self.telegram
        )
        self.commands = IndependentReviewAllPaperCommandController(
            telegram=self.telegram,
            store=self.positions,
            maker_store=self.maker_store,
            status_path=self.status_path,
            paper_stake_usd=self.paper_stake_usd,
        )

    async def close(self) -> None:
        await asyncio.gather(
            self._final_review_superseded_settlement.close(),
            self._final_review_superseded_commands.close(),
            return_exceptions=True,
        )
        await super().close()

    async def send_startup(self) -> int:
        release = self.release_sha()
        return await self.telegram.send_html(
            "\n".join(
                [
                    "🟢 <b>WEATHER ALL-PAPER BOT ONLINE — FINAL REVIEW-CORRECTED PROFILE</b>",
                    "",
                    "Future-day GEFS-gap PAPER alerts: <b>ON — uncalibrated</b>",
                    "Same-day late-lock PAPER alerts: <b>ON — uncalibrated</b>",
                    "Official-extreme source-shock PAPER alerts: <b>ON — revision-sensitive</b>",
                    "Structural underround PAPER alerts: <b>ON</b>",
                    "Prospective maker PAPER bids: <b>ON only with certified public-WS evidence</b>",
                    "Result-lag: <b>GATED — exact WRH cutoff state not proven</b>",
                    "",
                    "New taker/structural PAPER positions require a fresh exact-CLOB recheck after Telegram receipt.",
                    "V5 accounting independently revalidates exact leg cost, timing and signal identity.",
                    "Maker fills require causal public SELL-aggressor prints; book touch alone is never a fill.",
                    "🚫 <b>NO REAL ORDERS / NO WALLET / NO SIGNING AUTHORITY</b>",
                    f"Release: <code>{html.escape(release[:12])}</code>",
                ]
            )
        )

    async def _mark_v5_not_actionable(
        self, signal_id: int, candidate: dict, reason: str
    ) -> None:
        """Persist terminal non-actionability and the exact reason in one transaction."""
        await asyncio.to_thread(
            self.positions.mark_post_receipt_not_actionable,
            int(signal_id),
            decision_id=str(
                candidate.get("decision_id") or candidate.get("event_id") or signal_id
            ),
            event_id=str(candidate.get("event_id") or ""),
            market_id=str(candidate.get("market_id") or "") or None,
            side=str(candidate.get("side") or "") or None,
            reason=str(reason),
        )

    def _assert_inherited_safety_boundary(self, status: dict) -> None:
        required_false = (
            "financial_delivery",
            "financial_authority",
            "automatic_order_placement",
            "wallet_or_order_api_loaded",
            "pws_enabled",
        )
        for key in required_false:
            if status.get(key) is not False:
                raise FinalAllPaperSafetyInvariantError(
                    f"FINAL_INHERITED_{key.upper()}_NOT_FALSE"
                )
        three_layer = status.get("same_day_three_layer")
        if not isinstance(three_layer, dict):
            raise FinalAllPaperSafetyInvariantError(
                "FINAL_INHERITED_THREE_LAYER_STATUS_MISSING"
            )
        if three_layer.get("financial_authority") is not False:
            raise FinalAllPaperSafetyInvariantError(
                "FINAL_INHERITED_THREE_LAYER_FINANCIAL_AUTHORITY_NOT_FALSE"
            )

    async def run_cycle(self) -> dict:
        status = dict(await super().run_cycle())
        try:
            self._assert_inherited_safety_boundary(status)
        except FinalAllPaperSafetyInvariantError as exc:
            errors = list(status.get("errors") or [])
            errors.append(exc.code)
            status.update(
                {
                    "final_all_paper_runtime_version": FINAL_ALL_PAPER_RUNTIME_VERSION,
                    "independent_review_corrective_version": (
                        INDEPENDENT_REVIEW_CORRECTIVE_VERSION
                    ),
                    "inherited_safety_boundary_verified": False,
                    "cycle_ok": False,
                    "errors": errors,
                }
            )
            # Crucially, do not overwrite the offending inherited safety value.
            _atomic_json(self.status_path, status)
            raise

        operator_all_lanes_healthy = bool(
            status.get("cycle_ok") is True
            and status.get("maker_healthy") is True
            and status.get("maker_stream_degraded") is not True
        )
        status.update(
            {
                "final_all_paper_runtime_version": FINAL_ALL_PAPER_RUNTIME_VERSION,
                "independent_review_corrective_version": INDEPENDENT_REVIEW_CORRECTIVE_VERSION,
                "inherited_safety_boundary_verified": True,
                "v5_terminal_not_actionable_audit_atomic": True,
                "v5_independent_execution_integrity_verified": True,
                "maker_activation_accounting_atomic": True,
                "maker_activation_link_identity_strict": True,
                "source_shock_episode_dedupe_revision_aware": True,
                "operator_all_lanes_healthy": operator_all_lanes_healthy,
                "financial_delivery": False,
                "financial_authority": False,
                "automatic_order_placement": False,
                "wallet_or_order_api_loaded": False,
            }
        )
        _atomic_json(self.status_path, status)
        return status


async def _main(args) -> int:
    service = FinalAllPaperWeatherLiveService(
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
    parser.add_argument("--forecast-cache-seconds", type=float, default=DEFAULT_FORECAST_CACHE_SECONDS)
    parser.add_argument("--forecast-raw-gap-min", type=float, default=DEFAULT_FORECAST_RAW_GAP_MIN)
    parser.add_argument("--max-forecast-events", type=int, default=DEFAULT_MAX_FORECAST_EVENTS)
    parser.add_argument("--paper-stake-usd", type=float, default=DEFAULT_PAPER_STAKE_USD)
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    raise SystemExit(asyncio.run(_main(args)))


if __name__ == "__main__":
    main()
