from __future__ import annotations

import argparse
import asyncio
import html
import json
import os
import site
import sys
import time
from pathlib import Path

from .weather_only_live_paper import (
    DEFAULT_FORECAST_CACHE_SECONDS,
    DEFAULT_FORECAST_RAW_GAP_MIN,
    DEFAULT_INTERVAL_SECONDS,
    DEFAULT_MAX_FORECAST_EVENTS,
    WeatherLivePaperError,
    _atomic_json,
    _event_link,
)
from .weather_only_live_paper_all_signals_final_v9 import (
    FINAL_ALL_PAPER_RUNTIME_V9_VERSION,
    MAX_GLOBAL_RECALL_REUSE_SECONDS,
    TELEGRAM_EDIT_ABSENT,
    FinalAllPaperWeatherLiveServiceV9,
    FinalOperatorStateTelegram,
)
from .weather_only_live_paper_all_signals_v3 import MAKER_EVIDENCE_CLASS, MAKER_LANE
from .weather_only_live_paper_v2 import DEFAULT_PAPER_STAKE_USD
from .weather_only_maker_paper_accounting_v6 import (
    CERTIFIED_QUEUE_MODEL,
    LEGACY_QUEUE_UNCERTIFIED,
    MAKER_PAPER_ACCOUNTING_V6_VERSION,
    MakerPaperAccountingStoreV6,
)
from .weather_only_operator_commands_v10 import OperatorStateCommandControllerV10
from .weather_only_operator_state_corrective_v5 import (
    OPERATOR_STATE_CORRECTIVE_V5_VERSION,
    OperatorStatePostReceiptStoreV5,
)
from .weather_only_paper_corrective import CorrectiveSettlementEngine, DeliveryUncertain
from .weather_only_paper_positions import _payload


FINAL_ALL_PAPER_RUNTIME_V10_VERSION = (
    "final_all_paper_v10_semantic_census_unified_terminalization_maker_evidence"
)
FORBIDDEN_CODE_ENV = (
    "PYTHONPATH",
    "PYTHONHOME",
    "PYTHONUSERBASE",
    "PYTHONSTARTUP",
    "PYTHONINSPECT",
    "PYTHONWARNINGS",
    "PYTHONBREAKPOINT",
    "LD_PRELOAD",
    "LD_LIBRARY_PATH",
)


class FinalAllPaperWeatherLiveServiceV10(FinalAllPaperWeatherLiveServiceV9):
    def _defer_maker_restart_terminalization(self) -> bool:
        """V10 must own human-visible maker restart invalidation."""
        return True

    def __init__(self, **kwargs) -> None:
        leaked = [name for name in FORBIDDEN_CODE_ENV if name in os.environ]
        if leaked:
            raise RuntimeError(
                "ALL_PAPER_CODE_LOADING_ENVIRONMENT_FORBIDDEN:"
                + ",".join(sorted(leaked))
            )
        if os.environ.get("PYTHONNOUSERSITE") != "1" or site.ENABLE_USER_SITE is not False:
            raise RuntimeError("ALL_PAPER_USER_SITE_NOT_DISABLED")

        super().__init__(**kwargs)
        deferred_maker = tuple(
            int(value) for value in getattr(self, "_v7_maker_orphan_signal_ids", ())
        )

        self._v10_superseded_settlement = self.settlement
        self._v10_superseded_commands = self.commands
        old_maker = self.maker_store

        self.positions = OperatorStatePostReceiptStoreV5(self.db_path)
        self._v10_recovery = self.positions.reconcile_v5_after_restart()
        self.positions.ensure_operator_sync_records()

        old_maker.close()
        self.maker_store = MakerPaperAccountingStoreV6(self.db_path)
        self.settlement = CorrectiveSettlementEngine(
            store=self.positions, telegram=self.telegram
        )
        self.commands = OperatorStateCommandControllerV10(
            telegram=self.telegram,
            store=self.positions,
            maker_store=self.maker_store,
            status_path=self.status_path,
            paper_stake_usd=self.paper_stake_usd,
        )
        self._v10_deferred_maker_restart_signal_ids = list(deferred_maker)

    async def close(self) -> None:
        await asyncio.gather(
            self.settlement.close(),
            self.commands.close(),
            self._v10_superseded_settlement.close(),
            self._v10_superseded_commands.close(),
            return_exceptions=True,
        )
        await super().close()

    async def _terminalize_delivered_signal(
        self,
        signal_id: int,
        candidate: dict,
        *,
        status: str,
        reason: str,
    ) -> None:
        """Authoritative delivered-terminal transition + immediate visible invalidation."""
        await asyncio.to_thread(
            self.positions.terminalize_delivered_signal,
            int(signal_id),
            terminal_status=str(status),
            reason=str(reason),
            decision_id=str(
                candidate.get("decision_id")
                or candidate.get("order_id")
                or candidate.get("event_id")
                or signal_id
            ),
            event_id=str(candidate.get("event_id") or ""),
            market_id=str(candidate.get("market_id") or "") or None,
            side=str(candidate.get("side") or "") or None,
        )
        sync = await self._sync_operator_messages()
        if sync.get("healthy") is not True or list(sync.get("errors") or []):
            raise WeatherLivePaperError("DELIVERED_TERMINAL_OPERATOR_SYNC_FAILED")

    def _delivered_signal_candidate(self, signal_id: int) -> dict:
        with self.positions._conn() as db:
            row = db.execute(
                "SELECT * FROM weather_paper_signals WHERE id=?", (int(signal_id),)
            ).fetchone()
        if row is None:
            raise RuntimeError("DELIVERED_TERMINAL_SIGNAL_NOT_FOUND")
        signal = dict(row)
        payload = _payload(signal.get("payload_json"))
        candidate = dict(payload)
        candidate["event_id"] = str(signal.get("event_id") or "")
        candidate["market_id"] = str(signal.get("market_id") or "") or None
        candidate["side"] = str(signal.get("side") or "") or None
        candidate.setdefault("decision_id", candidate.get("order_id") or signal.get("fingerprint"))
        return candidate

    async def _terminalize_deferred_maker_restart_receipts(self) -> None:
        pending = list(self._v10_deferred_maker_restart_signal_ids)
        for signal_id in pending:
            candidate = self._delivered_signal_candidate(signal_id)
            await self._terminalize_delivered_signal(
                signal_id,
                candidate,
                status="MAKER_NOT_ACTIVATED_RESTART_COVERAGE_LOST",
                reason="PROCESS_RESTART_LOST_PROSPECTIVE_MAKER_COVERAGE",
            )
            self._v10_deferred_maker_restart_signal_ids.remove(signal_id)

    async def send_startup(self) -> int:
        # Repair all delivered terminal messages before emitting any new startup message.
        await self._terminalize_deferred_maker_restart_receipts()
        sync = await self._sync_operator_messages()
        if sync.get("healthy") is not True or list(sync.get("errors") or []):
            raise WeatherLivePaperError("STARTUP_OPERATOR_SYNC_NOT_CONFIRMED")
        return int(await super().send_startup())

    async def _send_maker_candidate(
        self, candidate: dict
    ) -> tuple[bool, str | None]:
        """Final maker delivery path: all confirmed-receipt failures invalidate immediately."""
        proposal = candidate["proposal"]
        await self.maker_stream.subscribe(proposal.token_id)
        await self.maker_stream.start()
        coverage = self.maker_stream.coverage(proposal.token_id)
        if coverage is None or not self.maker_stream.connected:
            return False, None
        if len(self.maker_store.active_orders()) >= self.maker_policy.max_active_orders:
            return False, None

        payload = dict(
            self._maker_signal_payload(
                candidate, coverage_generation=coverage.generation
            )
        )
        payload["decision_id"] = str(payload["order_id"])
        signal_id = self.positions.save_signal(
            fingerprint=str(payload["fingerprint"]),
            lane=MAKER_LANE,
            evidence_class=MAKER_EVIDENCE_CLASS,
            event_id=str(payload["event_id"]),
            market_id=str(payload["market_id"]),
            side=str(payload["side"]),
            token_id=str(payload["token_id"]),
            model_probability=float(
                payload["fair_value_research"]["raw_member_frequency"]
            ),
            entry_cost=float(payload["bid_price"]),
            raw_gap=float(payload["conditional_edge_per_share"]),
            theoretical_payout=1.0,
            created_at=time.time(),
            payload=payload,
        )
        if signal_id is None:
            return False, None
        await asyncio.to_thread(
            self.positions.set_signal_status, signal_id, "PENDING_DELIVERY"
        )
        try:
            message_id = await self.telegram.send_html(
                self._maker_proposal_message(payload),
                url=_event_link(candidate["event"]),
                expires_at=float(payload["decision_expires_at"]),
            )
        except DeliveryUncertain as exc:
            await asyncio.to_thread(
                self.positions.set_signal_status, signal_id, "DELIVERY_UNCERTAIN"
            )
            return True, exc.code
        except WeatherLivePaperError as exc:
            await asyncio.to_thread(
                self.positions.set_signal_status, signal_id, "DELIVERY_FAILED"
            )
            return True, exc.code

        sent_at = time.time()
        self.positions.mark_telegram_sent(signal_id, int(message_id), sent_at=sent_at)
        if sent_at >= float(payload["decision_expires_at"]):
            await self._terminalize_delivered_signal(
                signal_id,
                payload,
                status="EXPIRED",
                reason="MAKER_DELIVERY_RECEIPT_AFTER_EXPIRY",
            )
            return True, "MAKER_DELIVERY_RECEIPT_AFTER_EXPIRY"

        self._maker_proposals_sent += 1
        try:
            await self._activate_maker_after_delivery(
                payload=payload,
                event=candidate["event"],
                signal_id=signal_id,
                telegram_message_id=int(message_id),
                telegram_sent_at=sent_at,
            )
        except Exception as exc:
            code = getattr(exc, "code", type(exc).__name__)
            reason = f"MAKER_ACTIVATION:{code}"
            await self._terminalize_delivered_signal(
                signal_id,
                payload,
                status="MAKER_NOT_ACTIVATED",
                reason=reason,
            )
            return True, reason
        # V7's atomic activation transaction already commits MAKER_RESTING.
        return True, None

    def _maker_settlement_message(self, payload: dict) -> str:
        order_id = str(payload.get("order_id") or "")
        evidence_class = self.maker_store.evidence_class(order_id)
        if evidence_class == CERTIFIED_QUEUE_MODEL:
            return super()._maker_settlement_message(payload)
        pnl = float(payload.get("paper_pnl") or 0.0)
        roi = payload.get("paper_roi")
        roi_text = "n/a" if roi is None else f"{100.0 * float(roi):+.1f}%"
        label = (
            LEGACY_QUEUE_UNCERTIFIED
            if evidence_class == LEGACY_QUEUE_UNCERTIFIED
            else str(evidence_class)
        )
        return "\n".join(
            [
                "🧪 <b>MAKER RESEARCH SETTLEMENT — EXCLUDED FROM VALIDATED PERFORMANCE</b>",
                f"Virtual order: <code>{html.escape(order_id)}</code>",
                f"Evidence class: <code>{html.escape(label)}</code>",
                f"Historical research P&amp;L: <b>${pnl:+.2f}</b>",
                f"Historical research ROI: <b>{html.escape(roi_text)}</b>",
                "",
                "Queue position for this historical maker experiment was not execution-certified.",
                "This result remains auditable but is excluded from validated capital, proceeds, P&amp;L and ROI.",
                "🚫 No real order or fill exists.",
            ]
        )

    async def run_cycle(self) -> dict:
        status = dict(await super().run_cycle())
        recall = dict(status.get("global_weather_recall") or {})
        semantic_complete = recall.get("weather_semantic_coverage_complete") is True
        policy = str(recall.get("weather_semantic_product_policy") or "")
        status.update(
            {
                "final_all_paper_runtime_v10_version": FINAL_ALL_PAPER_RUNTIME_V10_VERSION,
                "final_all_paper_runtime_v9_version": FINAL_ALL_PAPER_RUNTIME_V9_VERSION,
                "operator_state_corrective_v5_version": OPERATOR_STATE_CORRECTIVE_V5_VERSION,
                "maker_paper_accounting_v6_version": MAKER_PAPER_ACCOUNTING_V6_VERSION,
                "gamma_census_complete": recall.get("gamma_census_complete") is True,
                "gamma_census_completed_at": recall.get("gamma_census_completed_at"),
                "gamma_census_age_seconds": recall.get("gamma_census_age_seconds"),
                "total_active_events_scanned": int(
                    recall.get("total_active_events_scanned") or 0
                ),
                "weather_looking_events_discovered": int(
                    recall.get("weather_looking_events") or 0
                ),
                "strict_supported_weather_events": int(
                    recall.get("strict_supported_events") or 0
                ),
                "unsupported_weather_events": int(
                    recall.get("unsupported_weather_events") or 0
                ),
                "unsupported_weather_reason_counts": dict(
                    recall.get("unsupported_reason_counts") or {}
                ),
                "unsupported_weather_examples": list(
                    recall.get("unsupported_examples") or []
                ),
                "weather_semantic_coverage_complete": semantic_complete,
                "weather_semantic_coverage_status": recall.get(
                    "weather_semantic_coverage_status"
                ),
                "weather_semantic_product_policy": policy,
                "global_weather_coverage_complete": semantic_complete,
                "global_weather_recall_complete": recall.get("gamma_census_complete")
                is True,
                "delivered_terminalization_common_primitive": True,
                "maker_restart_terminalization_common_primitive": True,
                "maker_restart_terminalization_pending": len(
                    self._v10_deferred_maker_restart_signal_ids
                ),
                "code_loading_environment_isolated": True,
                "python_user_site_disabled": True,
                "runtime_sys_path": list(sys.path),
                "runtime_python_executable": sys.executable,
                "runtime_prefix": sys.prefix,
                "runtime_base_prefix": sys.base_prefix,
                "maker_legacy_queue_pnl_excluded": True,
                "financial_authority": False,
                "automatic_order_placement": False,
                "wallet_or_order_api_loaded": False,
            }
        )
        if policy != "STRICT_SUPPORTED_SUBSET":
            status["cycle_ok"] = False
            status["operator_all_lanes_healthy"] = False
            status.setdefault("errors", []).append("WEATHER_SEMANTIC_POLICY_INVALID")
        if self._v10_deferred_maker_restart_signal_ids:
            status["cycle_ok"] = False
            status["operator_all_lanes_healthy"] = False
            status.setdefault("errors", []).append(
                "MAKER_RESTART_TERMINALIZATION_PENDING"
            )
        _atomic_json(self.status_path, status)
        return status


async def _main(args) -> int:
    service = FinalAllPaperWeatherLiveServiceV10(
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
    parser = argparse.ArgumentParser()
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
