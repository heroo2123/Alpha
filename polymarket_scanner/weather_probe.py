from __future__ import annotations

"""Read-only live probe for the weather-only branch.

The probe exists to answer three engineering questions before any service deployment:
(1) how large is the current tagged weather catalog and which contracts can be parsed
without guessing; (2) does the intended weather-only compiler/rule-authority stack
agree with the independently live-proven rule tree; and (3) do semantically proven
daily-temperature partitions show any complete-set underround at current exact CLOB
best asks?

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
from .weather_only_contracts import DAILY_HIGH, DAILY_LOW, compile_weather_event
from .weather_only_rules import compile_temperature_rule_authority
from .weather_rule_tree import parse_weather_contract
from .weather_structural import prove_daily_temperature_partition, screen_complete_set_underround

WEATHER_PROBE_VERSION = "weather_live_probe_v3_runtime_cross_certification"
FAILURE_SAMPLE_LIMIT = 3
FAILURE_SAMPLE_TEXT_LIMIT = 1800


def _rss_bytes() -> int:
    # Linux ru_maxrss is KiB. This branch targets Linux/GCE and CI runs on Linux.
    return int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss) * 1024


def _materialize(client: PolymarketClient, events: list[dict]) -> list[Market]:
    markets: list[Market] = []
    client._append_events(markets, events, set())
    return markets


def _sample_text(value: object) -> str:
    text = str(value or "").strip()
    return text[:FAILURE_SAMPLE_TEXT_LIMIT]


def _failure_sample(market: Market) -> dict:
    return {
        "event_id": market.event_id,
        "market_id": market.id,
        "event_title": _sample_text(market.event_title),
        "question": _sample_text(market.question),
        "description": _sample_text(market.description),
        "resolution_source": _sample_text(market.resolution_source),
        "end_date": _sample_text(market.end_date),
    }


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
        failure_samples: dict[str, list[dict]] = defaultdict(list)
        for market in markets:
            result = parse_weather_contract(market)
            if result.supported:
                supported[str(result.adapter_version)] += 1
            else:
                code = str(result.failure_code or "UNKNOWN")
                failures[code] += 1
                if len(failure_samples[code]) < FAILURE_SAMPLE_LIMIT:
                    failure_samples[code].append(_failure_sample(market))

        # Independently run the intended weather-only runtime compiler and rule
        # authority over the same live parent events. This prevents a stricter/newer
        # runtime path from silently drifting away from semantics already proven by
        # the diagnostic rule tree.
        stage = time.monotonic()
        foundation_family_counts = Counter()
        foundation_source_counts = Counter()
        foundation_profile_counts = Counter()
        foundation_rejections = Counter()
        foundation_shadow_supported_ids: set[str] = set()
        foundation_exactly_one_ids: set[str] = set()
        foundation_temperature_ids: set[str] = set()
        for event in events:
            compiled = compile_weather_event(event)
            foundation_family_counts[compiled.family] += 1
            foundation_source_counts[compiled.source_family] += 1
            if compiled.shadow_supported:
                foundation_shadow_supported_ids.add(compiled.event_id)
            if compiled.family not in {DAILY_HIGH, DAILY_LOW}:
                continue
            foundation_temperature_ids.add(compiled.event_id)
            authority = compile_temperature_rule_authority(event, compiled)
            foundation_profile_counts[authority.profile] += 1
            if authority.exactly_one_outcome_proven:
                foundation_exactly_one_ids.add(compiled.event_id)
            for reason in authority.rejection_reasons:
                foundation_rejections[str(reason)] += 1
        timings["foundation_cross_cert_seconds"] = time.monotonic() - stage

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
        proof_event_ids = {proof.event_id for proof in proofs}

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
            "unsupported_contract_failure_samples": {
                code: rows for code, rows in sorted(failure_samples.items())
            },
            "foundation_cross_certification": {
                "temperature_event_count": len(foundation_temperature_ids),
                "shadow_supported_event_count": len(foundation_shadow_supported_ids),
                "exactly_one_rule_proven_event_count": len(foundation_exactly_one_ids),
                "family_counts": dict(sorted(foundation_family_counts.items())),
                "source_counts": dict(sorted(foundation_source_counts.items())),
                "rule_profile_counts": dict(sorted(foundation_profile_counts.items())),
                "rule_rejection_counts": dict(sorted(foundation_rejections.items())),
                "proof_events_missing_foundation_exactly_one_count": len(proof_event_ids - foundation_exactly_one_ids),
                "proof_events_missing_foundation_exactly_one_ids": sorted(proof_event_ids - foundation_exactly_one_ids)[:50],
                "foundation_exactly_one_without_partition_proof_count": len(foundation_exactly_one_ids - proof_event_ids),
                "financial_authority": False,
            },
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
