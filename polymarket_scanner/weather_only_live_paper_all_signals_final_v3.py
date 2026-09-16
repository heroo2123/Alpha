from __future__ import annotations

"""Final narrow guard above the second adversarial corrective runtime.

V3 changes no strategy source or financial authority. It closes two residual operator
truthfulness boundaries:

* source-shock delivery is suppressed unless the complete decision TTL fits before the
  existing local-midnight safety margin; and
* structural underround Telegram messages explicitly identify the opportunity as a
  theoretical simultaneous basket that is excluded from validated PAPER P&L.
"""

import argparse
import asyncio
import html
import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from .weather_only_contract_strict import compile_strict_temperature_event
from .weather_only_live_paper import (
    DEFAULT_FORECAST_CACHE_SECONDS,
    DEFAULT_FORECAST_RAW_GAP_MIN,
    DEFAULT_INTERVAL_SECONDS,
    DEFAULT_MAX_FORECAST_EVENTS,
    _atomic_json,
    _event_title,
)
from .weather_only_live_paper_all_signals import LOCAL_MIDNIGHT_MARGIN_SECONDS
from .weather_only_live_paper_all_signals_final_v2 import (
    FinalAllPaperWeatherLiveServiceV2,
)
from .weather_only_live_paper_v2 import DEFAULT_PAPER_STAKE_USD
from .weather_only_live_paper_v4 import QUOTE_DECISION_TTL_SECONDS


FINAL_ALL_PAPER_RUNTIME_V3_VERSION = (
    "weather_all_paper_final_v6_midnight_and_structural_truth_guard"
)


class FinalAllPaperWeatherLiveServiceV3(FinalAllPaperWeatherLiveServiceV2):
    async def _source_shock_exact_recheck(
        self,
        candidate: dict,
        event: dict,
        *,
        after_time: float | None,
        decision_expires_at: float | None,
    ):
        checked = await super()._source_shock_exact_recheck(
            candidate,
            event,
            after_time=after_time,
            decision_expires_at=decision_expires_at,
        )
        if checked is None:
            return None
        fresh, exact, bucket, params = checked
        compiled = compile_strict_temperature_event(event)
        timezone_name = str(fresh.get("station_timezone") or "").strip()
        if not timezone_name:
            return None
        target = compiled.target_date
        if target is None:
            return None
        next_day = target.fromordinal(target.toordinal() + 1)
        midnight = datetime(
            next_day.year,
            next_day.month,
            next_day.day,
            tzinfo=ZoneInfo(timezone_name),
        ).timestamp()
        latest_safe_finish = midnight - LOCAL_MIDNIGHT_MARGIN_SECONDS
        # final_v2's source-shock decision TTL is the canonical V4 value. Requiring a
        # complete TTL to fit before the margin is slightly more conservative than
        # shortening the TTL and guarantees Telegram never advertises a thesis whose
        # normal actionable window crosses the contract local-day boundary.
        if (
            float(exact.finished_at) + QUOTE_DECISION_TTL_SECONDS
            > latest_safe_finish + 1e-9
        ):
            return None
        return fresh, exact, bucket, params

    def _structural_paper_message(self, fresh: dict, event: dict | None) -> str:
        asks = [float(value) for value in fresh.get("ask_prices") or ()]
        fees = [float(value) for value in fresh.get("conservative_fees_per_share") or ()]
        lines = [
            f"{idx}. ask ${ask:.4f} + fee ${fees[idx - 1]:.5f}"
            for idx, ask in enumerate(asks, 1)
        ]
        lane = str(fresh.get("lane") or "")
        if lane == "weather_binary_pair_underround":
            semantic = "Same-condition YES+NO payoff identity is structurally complete if both legs are actually obtained."
        else:
            semantic = (
                "Complete-bucket payoff identity is conditional on the certified "
                "exactly-one contract/rule semantics and on obtaining every leg."
            )
        return "\n".join(
            [
                "🧪 <b>PAPER STRUCTURAL WEATHER OPPORTUNITY — THEORETICAL ONLY</b>",
                f"<b>{html.escape(_event_title(event, str(fresh.get('event_id') or '')))}</b>",
                f"Lane: <code>{html.escape(lane)}</code>",
                "",
                *lines,
                "",
                f"Simultaneous-snapshot cost/set: <b>${float(fresh['gross_cost_per_set']):.4f}</b>",
                f"Reference payout/set: <b>${float(fresh['locked_payout_per_set']):.4f}</b>",
                f"Theoretical simultaneous spread/set: <b>${float(fresh['locked_profit_per_set']):.4f}</b>",
                f"Common displayed best-ask capacity: <b>{float(fresh['common_best_ask_shares']):.2f} sets</b>",
                "",
                html.escape(semantic),
                "⚠️ Multi-leg execution is non-atomic. Legging/partial-fill risk is not proven away.",
                "This alert is recorded for research but is EXCLUDED from validated PAPER P&amp;L.",
                "🚫 No real order was placed.",
            ]
        )

    async def run_cycle(self) -> dict:
        status = dict(await super().run_cycle())
        status.update(
            {
                "final_all_paper_runtime_v3_version": FINAL_ALL_PAPER_RUNTIME_V3_VERSION,
                "source_shock_full_ttl_before_midnight_margin_required": True,
                "structural_telegram_theoretical_only_label": True,
                "financial_delivery": False,
                "financial_authority": False,
                "automatic_order_placement": False,
                "wallet_or_order_api_loaded": False,
            }
        )
        _atomic_json(self.status_path, status)
        return status


async def _main(args) -> int:
    service = FinalAllPaperWeatherLiveServiceV3(
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
