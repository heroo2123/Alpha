from __future__ import annotations

"""Read-only live probe for the weather-only branch.

The probe exists to answer two engineering questions before any service deployment:
(1) how large is the current tagged weather catalog and which contracts can be parsed
without guessing; and (2) do semantically proven daily-temperature partitions show
any complete-set underround at current exact CLOB best asks?

It never writes the account database, never sends Telegram, never places orders and
never falls back to the general ~200k-market universe walk.
"""

import argparse
import asyncio
import json
import resource
import time
from collections import Counter, defaultdict
from pathlib import Path

from .models import Market
from .polymarket import PolymarketClient
from .weather_catalog import WEATHER_PAGE_CEILING, build_census, fetch_weather_events
from .weather_rule_tree import parse_weather_contract
from .weather_structural import prove_daily_temperature_partition, screen_complete_set_underround

WEATHER_PROBE_VERSION = "weather_live_probe_v1_read_only"


def _rss_bytes() -> int:
    # Linux ru_maxrss is KiB. This branch targets Linux/GCE and CI runs on Linux.
    return int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss) * 1024


def _materialize(client: PolymarketClient, events: list[dict]) -> list[Market]:
    markets: list[Market] = []
    client._append_events(markets, events, set())
    return markets


async def run_probe(
    *,
    client: PolymarketClient | None = None,
    page_ceiling: int = WEATHER_PAGE_CEILING,
    fetch_books: bool = True,
    minimum_complete_set_spread: float = 0.01,
) -> dict:
    owned = client is None
    client = client or PolymarketClient()
    started = time.monotonic()
    timings: dict[str, float] = {}

    try:
        stage = time.monotonic()
        events, pages = await fetch_weather_events(client, page_ceiling=page_ceiling)
        timings["catalog_seconds"] = time.monotonic() - stage
        census = build_census(events, pages=pages)

        stage = time.monotonic()
        markets = _materialize(client, events)
        timings["materialize_seconds"] = time.monotonic() - stage

        supported = Counter()
        failures = Counter()
        for market in markets:
            result = parse_weather_contract(market)
            if result.supported:
                supported[str(result.adapter_version)] += 1
            else:
                failures[str(result.failure_code or "UNKNOWN")] += 1

        grouped: dict[str, list[Market]] = defaultdict(list)
        raw_by_id: dict[str, dict] = {}
        for event in events:
            eid = str(event.get("id") or "")
            if eid:
                raw_by_id[eid] = event
        for market in markets:
            grouped[market.event_id].append(market)

        stage = time.monotonic()
        proofs = []
        for event_id, rows in grouped.items():
            raw = raw_by_id.get(event_id)
            if raw is None:
                continue
            proof = prove_daily_temperature_partition(raw, rows)
            if proof is not None:
                proofs.append(proof)
        timings["semantic_proof_seconds"] = time.monotonic() - stage

        screens = []
        token_count = 0
        if fetch_books and proofs:
            tokens = list(dict.fromkeys(leg.yes_token for proof in proofs for leg in proof.legs))
            token_count = len(tokens)
            stage = time.monotonic()
            books = await client.books(tokens)
            timings["clob_books_seconds"] = time.monotonic() - stage
            stage = time.monotonic()
            for proof in proofs:
                screen = screen_complete_set_underround(
                    proof,
                    books,
                    minimum_spread_per_bundle=minimum_complete_set_spread,
                )
                if screen is None:
                    continue
                screens.append({
                    "event_id": screen.event_id,
                    "event_slug": screen.event_slug,
                    "proof_version": screen.proof_version,
                    "raw_ask_cost_per_bundle": screen.raw_ask_cost_per_bundle,
                    "estimated_fee_per_bundle": screen.estimated_fee_per_bundle,
                    "estimated_total_cost_per_bundle": screen.estimated_total_cost_per_bundle,
                    "guaranteed_payout_per_bundle": screen.guaranteed_payout_per_bundle,
                    "estimated_locked_spread_per_bundle": screen.estimated_locked_spread_per_bundle,
                    "common_best_ask_shares": screen.common_best_ask_shares,
                    "estimated_locked_spread_at_common_size": screen.estimated_locked_spread_at_common_size,
                    "screening_only": True,
                    "requires_exact_fee_authority": True,
                    "legs": [
                        {"market_id": market_id, "token_id": token_id, "ask": ask, "best_ask_size": size}
                        for market_id, token_id, ask, size in screen.asks
                    ],
                })
            timings["complete_set_screen_seconds"] = time.monotonic() - stage

        elapsed = time.monotonic() - started
        return {
            "version": WEATHER_PROBE_VERSION,
            "read_only": True,
            "financial_delivery": False,
            "automatic_order_placement": False,
            "catalog": {
                key: value for key, value in census.items() if key != "markets"
            },
            "materialized_market_count": len(markets),
            "supported_contract_counts": dict(sorted(supported.items())),
            "unsupported_contract_failure_counts": dict(sorted(failures.items())),
            "temperature_partition_proof_count": len(proofs),
            "temperature_partition_token_count": token_count,
            "complete_set_screen_count": len(screens),
            "complete_set_screens": sorted(
                screens,
                key=lambda row: (-row["estimated_locked_spread_per_bundle"], row["event_id"]),
            ),
            "timings_seconds": {key: round(value, 6) for key, value in timings.items()},
            "total_seconds": round(elapsed, 6),
            "process_peak_rss_bytes": _rss_bytes(),
        }
    finally:
        if owned:
            await client.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--page-ceiling", type=int, default=WEATHER_PAGE_CEILING)
    parser.add_argument("--no-books", action="store_true", help="catalog/contract census only; skip CLOB books")
    parser.add_argument("--minimum-complete-set-spread", type=float, default=0.01)
    args = parser.parse_args()

    report = asyncio.run(run_probe(
        page_ceiling=args.page_ceiling,
        fetch_books=not args.no_books,
        minimum_complete_set_spread=args.minimum_complete_set_spread,
    ))
    payload = json.dumps(report, indent=2, sort_keys=True)
    if args.output:
        args.output.write_text(payload + "\n")
    print(payload)


if __name__ == "__main__":
    main()
