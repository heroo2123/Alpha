from __future__ import annotations

"""All-weather PAPER runtime v5: bounded maker activation lifecycle.

V5 preserves the V4 serialized maker accounting boundary and adds two independent
fail-closed corrections:

* a maker candidate waits a bounded time for a real first-book public-WebSocket
  coverage anchor, then releases the token again if no virtual order activates; and
* the Telegram decision deadline is rechecked after the post-delivery forecast/CLOB
  rebuild so network work cannot activate an already-expired PAPER instruction.

Completed/cancelled maker orders are unsubscribed while unrelated active token
coverage is retained.  The stream idles locally when no maker token is needed.  No
real order, wallet, signing, cancellation or actual-fill authority is introduced.
"""

import argparse
import asyncio
import json
import time
from pathlib import Path

from .weather_only_live_paper import (
    DEFAULT_FORECAST_CACHE_SECONDS,
    DEFAULT_FORECAST_RAW_GAP_MIN,
    DEFAULT_INTERVAL_SECONDS,
    DEFAULT_MAX_FORECAST_EVENTS,
    _atomic_json,
)
from .weather_only_live_paper_all_signals_v4 import AllPaperWeatherLiveV4Service
from .weather_only_live_paper_all_signals_v3 import AllPaperV3Error
from .weather_only_live_paper_v2 import DEFAULT_PAPER_STAKE_USD
from .weather_only_maker_trade_stream_v3 import (
    MAKER_TRADE_STREAM_V3_VERSION,
    ProspectiveMakerTradeStreamV3,
)


ALL_PAPER_V5_RUNTIME_VERSION = (
    "weather_all_paper_signals_v5_bounded_maker_activation_subscriptions"
)
MAKER_FIRST_BOOK_COVERAGE_WAIT_SECONDS = 8.0
MAKER_FIRST_BOOK_POLL_SECONDS = 0.05


class AllPaperWeatherLiveV5Service(AllPaperWeatherLiveV4Service):
    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        old_stream = self.maker_stream
        if getattr(old_stream, "_task", None) is not None:
            raise AllPaperV3Error("MAKER_STREAM_STARTED_BEFORE_V5_REPLACEMENT")
        self.maker_stream = ProspectiveMakerTradeStreamV3()

    async def _maker_rebuild_same_proposal(self, payload: dict, event: dict):
        result = await super()._maker_rebuild_same_proposal(payload, event)
        exact = result[-1]
        expires_at = float(payload["decision_expires_at"])
        if max(time.time(), float(exact.finished_at)) >= expires_at:
            raise AllPaperV3Error("MAKER_SIGNAL_EXPIRED_DURING_POST_DELIVERY_RECHECK")
        return result

    async def _wait_for_maker_coverage(self, token_id: str):
        deadline = time.monotonic() + MAKER_FIRST_BOOK_COVERAGE_WAIT_SECONDS
        while time.monotonic() < deadline:
            coverage = self.maker_stream.coverage(token_id)
            if coverage is not None and self.maker_stream.connected:
                return coverage
            task = getattr(self.maker_stream, "_task", None)
            if task is not None and task.done():
                return None
            await asyncio.sleep(MAKER_FIRST_BOOK_POLL_SECONDS)
        return None

    async def _send_maker_candidate(self, candidate: dict) -> tuple[bool, str | None]:
        token = str(candidate["proposal"].token_id)
        await self.maker_stream.subscribe(token)
        await self.maker_stream.start()
        coverage = await self._wait_for_maker_coverage(token)
        if coverage is None:
            await self.maker_stream.unsubscribe(token)
            return False, None

        try:
            return await super()._send_maker_candidate(candidate)
        finally:
            active_tokens = await asyncio.to_thread(self.maker_store.active_token_ids)
            if token not in active_tokens:
                await self.maker_stream.unsubscribe(token)

    async def _progress_maker_orders(self, by_id: dict[str, dict]) -> list[str]:
        errors = list(await super()._progress_maker_orders(by_id))
        active_tokens = await asyncio.to_thread(self.maker_store.active_token_ids)
        try:
            await self.maker_stream.retain_only(active_tokens)
        except Exception as exc:
            errors.append(
                "MAKER_STREAM_RECONCILE:"
                f"{getattr(exc, 'code', type(exc).__name__)}"
            )
        return errors

    async def run_cycle(self) -> dict:
        status = dict(await super().run_cycle())
        stream_status = self.maker_stream.status()
        status.update(
            {
                "all_paper_v5_runtime_version": ALL_PAPER_V5_RUNTIME_VERSION,
                "maker_trade_stream_version": MAKER_TRADE_STREAM_V3_VERSION,
                "maker_first_book_coverage_wait_seconds": (
                    MAKER_FIRST_BOOK_COVERAGE_WAIT_SECONDS
                ),
                "maker_post_delivery_expiry_rechecked": True,
                "maker_subscription_lifecycle_bounded": True,
                "maker_trade_stream": stream_status,
                "financial_delivery": False,
                "financial_authority": False,
                "automatic_order_placement": False,
                "wallet_or_order_api_loaded": False,
            }
        )
        _atomic_json(self.status_path, status)
        return status


async def _main(args) -> int:
    service = AllPaperWeatherLiveV5Service(
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
            return 0 if status.get("cycle_ok") and status.get("maker_healthy") else 2
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
        "--forecast-cache-seconds",
        type=float,
        default=DEFAULT_FORECAST_CACHE_SECONDS,
    )
    parser.add_argument(
        "--forecast-raw-gap-min",
        type=float,
        default=DEFAULT_FORECAST_RAW_GAP_MIN,
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
