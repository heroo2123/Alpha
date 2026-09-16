from __future__ import annotations

"""Read-only weather-only universe inventory CLI.

This tool discovers configured weather tags, compiles contract families and emits
JSON evidence.  It does not open CLOB books, send Telegram messages, create orders,
or grant financial authority.
"""

import argparse
import asyncio
import json
from collections import Counter
from pathlib import Path

from .weather_only_contracts import compile_weather_event
from .weather_only_discovery import DEFAULT_TAGS, WeatherOnlyDiscovery


def inventory_report(snapshot, *, include_events: bool = False) -> dict:
    compiled = [compile_weather_event(event) for event in snapshot.events]
    family = Counter(item.family for item in compiled)
    source = Counter(item.source_family for item in compiled)
    rejection = Counter(reason for item in compiled for reason in item.rejection_reasons)
    shadow = sum(bool(item.shadow_supported) for item in compiled)
    open_buckets = sum(sum(bucket.trade_open for bucket in item.buckets) for item in compiled)
    all_buckets = sum(len(item.buckets) for item in compiled)
    shape_complete = sum(bool(item.partition_shape_complete) for item in compiled)
    financial = sum(bool(item.financial_authority) for item in compiled)

    report = {
        "mode": "WEATHER_ONLY_INVENTORY_READ_ONLY",
        "discovery": snapshot.summary(),
        "compiled": {
            "events": len(compiled),
            "buckets": all_buckets,
            "open_trade_buckets": open_buckets,
            "shadow_supported_events": shadow,
            "partition_shape_complete_events": shape_complete,
            "financial_authority_events": financial,
            "by_family": dict(sorted(family.items())),
            "by_source_family": dict(sorted(source.items())),
            "rejection_reasons": dict(sorted(rejection.items())),
        },
        "financial_delivery": False,
        "automatic_order_placement": False,
    }
    if include_events:
        report["events"] = [item.as_dict() for item in compiled]
    return report


async def _run(tags: tuple[str, ...], include_events: bool) -> dict:
    discovery = WeatherOnlyDiscovery()
    try:
        snapshot = await discovery.discover(tags)
    finally:
        await discovery.close()
    return inventory_report(snapshot, include_events=include_events)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--tags",
        default=",".join(DEFAULT_TAGS),
        help="Comma-separated Gamma tag slugs; default: daily-temperature,weather",
    )
    parser.add_argument("--details", action="store_true", help="Include per-event compiled metadata")
    parser.add_argument("--output", type=Path, help="Optional JSON output file")
    args = parser.parse_args()
    tags = tuple(dict.fromkeys(part.strip() for part in args.tags.split(",") if part.strip()))
    report = asyncio.run(_run(tags, args.details))
    payload = json.dumps(report, indent=2, sort_keys=True, default=str)
    print(payload)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(payload + "\n")


if __name__ == "__main__":
    main()
