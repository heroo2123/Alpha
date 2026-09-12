from __future__ import annotations

"""Human-readable Telegram surface for the weather live-paper service.

The underlying candidate-generation, persistence, exact-CLOB, forecast and safety
logic remains in ``weather_only_live_paper``.  This module changes only the operator
presentation and entrypoint so a Telegram alert answers, in plain language:

* what the market is asking;
* which paper side the model is suggesting;
* what that side means;
* how many GEFS members supported it;
* what one share costs and pays if correct; and
* why the alert fired.

A 31/31 ensemble vote is deliberately described as a model vote, never as 100%
real-world certainty or a calibrated probability.
"""

import argparse
import asyncio
import html
import json
from pathlib import Path

from .weather_only_forecast import GEFS_TOTAL_MEMBERS
from .weather_only_live_paper import (
    DEFAULT_FORECAST_CACHE_SECONDS,
    DEFAULT_FORECAST_RAW_GAP_MIN,
    DEFAULT_INTERVAL_SECONDS,
    DEFAULT_MAX_FORECAST_EVENTS,
    WeatherLivePaperService,
    _event_title,
)


HUMAN_TELEGRAM_VERSION = "weather_live_paper_human_telegram_v1_plain_english_votes_cost_payout"


def _market_question(event: dict | None, market_id: str) -> str | None:
    if not isinstance(event, dict):
        return None
    markets = event.get("markets")
    if not isinstance(markets, list):
        return None
    wanted = str(market_id or "").strip()
    for market in markets:
        if not isinstance(market, dict) or str(market.get("id") or "").strip() != wanted:
            continue
        question = str(market.get("question") or "").strip()
        return question or None
    return None


def _member_vote(raw_probability: float) -> tuple[int, int]:
    # The live-paper mapper currently includes all GEFS members: control + 30
    # perturbed members. Frequencies therefore live on the exact 1/31 lattice.
    total = int(GEFS_TOTAL_MEMBERS)
    votes = int(round(float(raw_probability) * total))
    return max(0, min(total, votes)), total


class HumanReadableWeatherLivePaperService(WeatherLivePaperService):
    def _forecast_message(self, candidate: dict, event: dict | None) -> str:
        event_id = str(candidate.get("event_id") or "")
        market_id = str(candidate.get("market_id") or "")
        title = _event_title(event, event_id)
        question = _market_question(event, market_id)
        side = str(candidate.get("side") or "").upper()
        bucket = str(candidate.get("bucket_label") or "")
        station = str(candidate.get("station") or "")
        target_date = str(candidate.get("target_date") or "")
        probability = float(candidate.get("raw_probability") or 0.0)
        votes, total_members = _member_vote(probability)
        ask = float(candidate.get("ask") or 0.0)
        fee = float(candidate.get("fee") or 0.0)
        cost = float(candidate.get("entry_cost") or 0.0)
        gap = float(candidate.get("raw_gap") or 0.0)
        ask_size = float(candidate.get("ask_size") or 0.0)
        win_profit = max(0.0, 1.0 - cost)

        if side == "YES":
            action = f"BUY YES — paper bet that the final temperature IS in {bucket}."
            vote_meaning = f"{votes}/{total_members} GEFS runs landed IN this bucket."
        else:
            action = f"BUY NO — paper bet that the final temperature is NOT in {bucket}."
            vote_meaning = f"{votes}/{total_members} GEFS runs landed OUTSIDE this bucket."

        lines = [
            "🔬 <b>WEATHER PAPER SIGNAL</b>",
            f"<b>{html.escape(title)}</b>",
        ]
        if question:
            lines.append(f"Market: <b>{html.escape(question)}</b>")
        lines.extend([
            "",
            f"👉 <b>PAPER SIDE: {html.escape(side)}</b>",
            html.escape(action),
            "",
            f"🌦 <b>Model vote:</b> {votes}/{total_members} members support {html.escape(side)}",
            html.escape(vote_meaning),
            f"Station/date: <b>{html.escape(station)}</b> / {html.escape(target_date)}",
            "",
            f"💵 <b>Exact live ask:</b> {100.0 * ask:.3f}¢",
            f"Fee estimate: {100.0 * fee:.3f}¢/share",
            f"<b>Total paper entry:</b> {100.0 * cost:.3f}¢ for a share that pays $1 if correct",
            f"If correct at this snapshot: gross profit/share ≈ <b>${win_profit:.4f}</b>",
            f"Visible at best ask: <b>{ask_size:.2f} shares</b>",
            "",
            f"📐 <b>Why it alerted:</b> raw GEFS vote minus exact entry cost = {100.0 * gap:+.1f} percentage points.",
            "",
            "⚠️ <b>IMPORTANT:</b> this is an UNCALIBRATED model-disagreement signal, not a proven edge.",
            f"{votes}/{total_members} model members agreeing does <b>not</b> mean a {100.0 * probability:.0f}% real-world chance.",
            "We are collecting these paper signals specifically to see whether this disagreement survives real settlement results.",
            "🚫 No order was placed.",
        ])
        return "\n".join(lines)

    def _structural_message(self, opportunity: dict, event: dict | None) -> str:
        title = _event_title(event, str(opportunity.get("event_id") or ""))
        asks = [float(x) for x in opportunity.get("ask_prices") or ()]
        fees = [float(x) for x in opportunity.get("conservative_fees_per_share") or ()]
        tokens = [str(x) for x in opportunity.get("token_ids") or ()]
        legs = []
        for index, (token, ask) in enumerate(zip(tokens, asks), 1):
            fee = fees[index - 1] if index - 1 < len(fees) else 0.0
            legs.append(
                f"{index}. paper-buy required leg @ {100.0 * ask:.2f}¢ + {100.0 * fee:.3f}¢ fee "
                f"| <code>{html.escape(token[:12])}…</code>"
            )
        total = float(opportunity.get("gross_cost_per_set") or 0.0)
        payout = float(opportunity.get("locked_payout_per_set") or 0.0)
        profit = float(opportunity.get("locked_profit_per_set") or 0.0)
        capacity = float(opportunity.get("common_best_ask_shares") or 0.0)
        max_profit = float(opportunity.get("max_locked_profit_at_best_level") or 0.0)
        return "\n".join([
            "🧪 <b>WEATHER PAPER SIGNAL — STRUCTURAL</b>",
            f"<b>{html.escape(title)}</b>",
            "",
            "👉 <b>PAPER ACTION:</b> buy every listed leg together. The pricing snapshot forms a complete payout set.",
            *legs,
            "",
            f"Total paper cost for one complete set: <b>${total:.4f}</b>",
            f"Contract payout for a complete winning set: <b>${payout:.4f}</b>",
            f"Quoted locked margin/set: <b>${profit:.4f}</b>",
            f"Top-book common capacity: <b>{capacity:.2f} sets</b>",
            f"Quoted margin across visible top level: <b>${max_profit:.2f}</b>",
            "",
            "⚠️ This is stronger than a forecast signal because it is structural price math, but it is still PAPER ONLY: all required legs would need to fill at those exact prices.",
            "🚫 No order was placed.",
        ])


async def _main(args) -> int:
    service = HumanReadableWeatherLivePaperService(
        db_path=args.db,
        status_path=args.status,
        release_file=args.release_file,
        interval_seconds=args.interval_seconds,
        forecast_cache_seconds=args.forecast_cache_seconds,
        forecast_raw_gap_min=args.forecast_raw_gap_min,
        max_forecast_events=args.max_forecast_events,
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
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    raise SystemExit(asyncio.run(_main(args)))


if __name__ == "__main__":
    main()
