from __future__ import annotations

"""Second all-PAPER promotion layer: exact structural evidence + crash-safe expiry.

The first all-signal layer adds same-day late-lock and reviewed structural PAPER
alerts.  This wrapper hardens the structural accounting boundary before promotion:
exact CLOB book hashes/timestamps are persisted with every basket and restart
recovery cannot turn an expired Telegram receipt/captured quote into a simulated
position.

Real-money authority remains impossible here.
"""

import argparse
import asyncio
import json
import math
from pathlib import Path

from .weather_only_clob import WeatherCLOBError
from .weather_only_contract_strict import StrictWeatherContractError, compile_strict_temperature_event
from .weather_only_live_paper import (
    DEFAULT_FORECAST_CACHE_SECONDS,
    DEFAULT_FORECAST_RAW_GAP_MIN,
    DEFAULT_INTERVAL_SECONDS,
    DEFAULT_MAX_FORECAST_EVENTS,
)
from .weather_only_live_paper_all_signals import (
    AllPaperWeatherLiveService,
    AllSignalsCrashSafeWeatherPaperStore,
)
from .weather_only_live_paper_v2 import DEFAULT_PAPER_STAKE_USD
from .weather_only_live_paper_v4 import V4InvariantError, _validate_exact_snapshot
from .weather_only_paper_commands_canonical import CanonicalWeatherPaperCommandController
from .weather_only_paper_corrective import CorrectiveSettlementEngine
from .weather_only_paper_positions import WeatherPaperPositionError, _payload
from .weather_only_structural import binary_pair_underround, complete_bucket_underround


ALL_PAPER_RUNTIME_V2_VERSION = "weather_all_paper_signals_v2_structural_exact_evidence_expiry"
_STRUCTURAL_LANES = {
    "weather_binary_pair_underround",
    "weather_complete_bucket_underround",
}


class AllSignalsV2CrashSafeStore(AllSignalsCrashSafeWeatherPaperStore):
    def ensure_position_for_signal(self, signal_id: int, target_stake_usd: float) -> dict | None:
        signal = self._load_signal(int(signal_id))
        if signal is None:
            return None
        lane = str(signal.get("lane") or "").strip()
        if lane in _STRUCTURAL_LANES and str(signal.get("status") or "") == "ACKNOWLEDGED":
            payload = _payload(signal.get("payload_json"))
            try:
                sent_at = float(signal.get("telegram_sent_at"))
                fill_at = float(payload.get("paper_fill_at"))
                expires_at = float(payload.get("decision_expires_at"))
            except (TypeError, ValueError, OverflowError):
                raise WeatherPaperPositionError("ALL_PAPER_STRUCTURAL_EXPIRY_EVIDENCE_MISSING") from None
            if not all(math.isfinite(value) and value >= 0.0 for value in (sent_at, fill_at, expires_at)):
                raise WeatherPaperPositionError("ALL_PAPER_STRUCTURAL_EXPIRY_EVIDENCE_INVALID")
            if sent_at >= expires_at or fill_at >= expires_at:
                raise WeatherPaperPositionError("ALL_PAPER_STRUCTURAL_DECISION_EXPIRED")
        return super().ensure_position_for_signal(int(signal_id), target_stake_usd)


class AllPaperWeatherLiveServiceV2(AllPaperWeatherLiveService):
    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self._all_paper_v2_superseded_settlement = self.settlement
        self._all_paper_v2_superseded_commands = self.commands
        self.positions = AllSignalsV2CrashSafeStore(self.db_path)
        self.settlement = CorrectiveSettlementEngine(store=self.positions, telegram=self.telegram)
        self.commands = CanonicalWeatherPaperCommandController(
            telegram=self.telegram,
            store=self.positions,
            status_path=self.status_path,
            paper_stake_usd=self.paper_stake_usd,
        )

    async def close(self) -> None:
        await asyncio.gather(
            self._all_paper_v2_superseded_settlement.close(),
            self._all_paper_v2_superseded_commands.close(),
            return_exceptions=True,
        )
        await super().close()

    async def _recheck_structural(self, opportunity: dict, event: dict | None) -> dict | None:
        if not isinstance(event, dict):
            return None
        try:
            compiled = compile_strict_temperature_event(event)
            exact = await self.runtime.clob.exact_event_snapshot(compiled)
            _validate_exact_snapshot(compiled, exact)
        except (StrictWeatherContractError, WeatherCLOBError, V4InvariantError):
            raise

        lane = str(opportunity.get("lane") or "")
        if lane == "weather_binary_pair_underround":
            rows = binary_pair_underround(compiled, exact.books, exact.parameters)
        elif lane == "weather_complete_bucket_underround":
            candidate = complete_bucket_underround(compiled, exact.books, exact.parameters)
            rows = [] if candidate is None else [candidate]
        else:
            return None

        wanted = tuple(str(value) for value in (opportunity.get("token_ids") or ()))
        for row in rows:
            value = row.as_dict()
            tokens = tuple(str(token) for token in value.get("token_ids") or ())
            if tokens != wanted:
                continue
            book_evidence: list[dict] = []
            for token in tokens:
                book = exact.books.get(token)
                if book is None or book.received_at is None:
                    raise V4InvariantError("ALL_PAPER_STRUCTURAL_BOOK_EVIDENCE_MISSING")
                book_evidence.append({
                    "token_id": token,
                    "best_ask": float(book.best_ask) if book.best_ask is not None else None,
                    "best_ask_size": float(book.best_ask_size),
                    "provider_timestamp": str(book.timestamp or ""),
                    "received_at": float(book.received_at),
                    "book_hash": str(book.book_hash or ""),
                })
            value.update({
                "exact_clob_started_at": float(exact.started_at),
                "exact_clob_finished_at": float(exact.finished_at),
                "exact_clob_book_evidence": book_evidence,
                "exact_clob_event_id": str(exact.event_id),
            })
            return value
        return None

    async def run_cycle(self) -> dict:
        status = dict(await super().run_cycle())
        status.update({
            "all_paper_runtime_v2_version": ALL_PAPER_RUNTIME_V2_VERSION,
            "structural_exact_book_evidence_persisted": True,
            "structural_crash_recovery_expiry_guard": True,
            "result_lag_paper_delivery_enabled": False,
            "result_lag_block_reason": "EXACT_WRH_CUTOFF_STATE_NOT_PROVEN",
            "maker_paper_delivery_enabled": False,
            "maker_block_reason": "CONSERVATIVE_VALUE_POLICY_AND_PUBLIC_TRADE_FEED_NOT_CERTIFIED",
            "financial_delivery": False,
            "financial_authority": False,
            "automatic_order_placement": False,
            "wallet_or_order_api_loaded": False,
        })
        from .weather_only_live_paper import _atomic_json
        _atomic_json(self.status_path, status)
        return status


# Explicit compatibility exports for the V3 development layer.  These aliases are
# identity-only: they do not create another implementation or alter V2 behavior.
# Keeping them here makes the inter-layer contract explicit and testable while the
# canonical V2 names above remain unchanged for existing deployments/tests.
ALL_PAPER_V2_RUNTIME_VERSION = ALL_PAPER_RUNTIME_V2_VERSION
AllPaperWeatherLiveV2Service = AllPaperWeatherLiveServiceV2


async def _main(args) -> int:
    service = AllPaperWeatherLiveServiceV2(
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
            return 0 if status.get("cycle_ok") else 2
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
