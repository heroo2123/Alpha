from __future__ import annotations

"""Bounded always-on foundation for the weather-only scanner.

This runtime intentionally stops one layer before financial authority.  It performs
small weather-tag discovery, compiles contract/rule semantics, cheaply pre-screens
semantically certified complete temperature partitions with *exact CLOB books*, and
only then spends market-info requests on the most promising events.  Any surviving
structural opportunity is re-fetched once more before it is recorded.

There is deliberately no Telegram sender, order posting, database mutation, or
forecast-probability promotion in this module.  Source settlement/forecast adapters,
calibration, execution certificates and final financial delivery remain separate
future gates.
"""

import argparse
import asyncio
import json
import os
import resource
import time
from collections import Counter
from dataclasses import asdict
from pathlib import Path

from .weather_only_clob import (
    MAX_TOKENS,
    WeatherCLOBClient,
    WeatherCLOBError,
    WeatherExecutionSnapshot,
)
from .weather_only_contracts import (
    DAILY_HIGH,
    DAILY_LOW,
    CompiledWeatherEvent,
    compile_weather_event,
)
from .weather_only_discovery import DEFAULT_TAGS, WeatherDiscoveryError, WeatherOnlyDiscovery
from .weather_only_rules import apply_rule_authority, compile_temperature_rule_authority
from .weather_only_structural import complete_bucket_underround


WEATHER_SHADOW_RUNTIME_VERSION = "weather_only_shadow_runtime_v1_bounded_exact_recheck"
DEFAULT_LOOP_INTERVAL_SECONDS = 300.0
MIN_LOOP_INTERVAL_SECONDS = 60.0
MAX_PRESCREEN_TOKENS = 5_000
MAX_EXACT_EVENTS_PER_CYCLE = 24
PRESCREEN_MAX_RAW_ASK_COST = 1.01
MIN_SHADOW_PROFIT_PER_SET = 0.001


class WeatherShadowRuntimeError(RuntimeError):
    def __init__(self, code: str):
        self.code = str(code)
        super().__init__(self.code)


def _rss_bytes() -> int:
    return int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss) * 1024


def _event_id(event: dict) -> str:
    return str(event.get("id") or "").strip()


def _chunks(values: list[str], size: int) -> list[list[str]]:
    return [values[start:start + size] for start in range(0, len(values), size)]


def _fee_rate_map(snapshot: WeatherExecutionSnapshot) -> tuple[dict[str, float] | None, str | None]:
    """Return only fee models this runtime can conservatively certify.

    A zero fee rate is exact regardless of any exponent metadata.  Non-zero fee
    schedules are intentionally withheld until the weather-only fee calculator is
    explicitly certified against current CLOB semantics.  This prevents the shadow
    runtime from inheriting a stale/general-bot fee assumption merely because market
    info exposed a numeric ``fee_rate``.
    """
    rates: dict[str, float] = {}
    for condition, params in snapshot.parameters.items():
        if params.fee_rate != 0.0:
            return None, "NONZERO_FEE_MODEL_NOT_CERTIFIED"
        rates[condition] = 0.0
    return rates, None


def _open_complete_partition(compiled: CompiledWeatherEvent) -> bool:
    return bool(
        compiled.family in {DAILY_HIGH, DAILY_LOW}
        and compiled.partition_shape_complete
        and compiled.exactly_one_outcome_proven
        and compiled.buckets
        and all(bucket.trade_open and bucket.yes_token for bucket in compiled.buckets)
    )


def _serialize_opportunity(opportunity, *, raw_prescreen_cost: float, rechecked: bool) -> dict:
    value = opportunity.as_dict()
    value.update({
        "raw_prescreen_cost": raw_prescreen_cost,
        "rechecked": bool(rechecked),
        "financial_authority": False,
        "financial_delivery": False,
        "automatic_order_placement": False,
    })
    return value


class WeatherOnlyShadowRuntime:
    def __init__(
        self,
        *,
        discovery: WeatherOnlyDiscovery | None = None,
        clob: WeatherCLOBClient | None = None,
        max_exact_events_per_cycle: int = MAX_EXACT_EVENTS_PER_CYCLE,
        prescreen_max_raw_ask_cost: float = PRESCREEN_MAX_RAW_ASK_COST,
        min_shadow_profit_per_set: float = MIN_SHADOW_PROFIT_PER_SET,
    ) -> None:
        if max_exact_events_per_cycle <= 0 or max_exact_events_per_cycle > 100:
            raise ValueError("max_exact_events_per_cycle must be in 1..100")
        if not (0.0 < prescreen_max_raw_ask_cost <= 1.25):
            raise ValueError("invalid prescreen_max_raw_ask_cost")
        if min_shadow_profit_per_set < 0.0:
            raise ValueError("min_shadow_profit_per_set must be non-negative")
        self.discovery = discovery or WeatherOnlyDiscovery()
        self.clob = clob or WeatherCLOBClient()
        self._owns_discovery = discovery is None
        self._owns_clob = clob is None
        self.max_exact_events_per_cycle = int(max_exact_events_per_cycle)
        self.prescreen_max_raw_ask_cost = float(prescreen_max_raw_ask_cost)
        self.min_shadow_profit_per_set = float(min_shadow_profit_per_set)

    async def close(self) -> None:
        if self._owns_discovery:
            await self.discovery.close()
        if self._owns_clob:
            await self.clob.close()

    async def _prescreen_books(self, token_ids: list[str]) -> dict[str, object]:
        unique = list(dict.fromkeys(str(value).strip() for value in token_ids if str(value).strip()))
        if len(unique) > MAX_PRESCREEN_TOKENS:
            raise WeatherShadowRuntimeError("PRESCREEN_TOKEN_CAP")
        books: dict[str, object] = {}
        for chunk in _chunks(unique, MAX_TOKENS):
            rows = await self.clob.books(chunk)
            for token, book in rows.items():
                if token in books:
                    raise WeatherShadowRuntimeError("PRESCREEN_BOOK_DUPLICATE")
                books[token] = book
        if set(books) != set(unique):
            raise WeatherShadowRuntimeError("PRESCREEN_BOOK_INCOMPLETE")
        return books

    async def run_cycle(self, tags: tuple[str, ...] = DEFAULT_TAGS) -> dict:
        started_wall = time.time()
        started = time.monotonic()
        timings: dict[str, float] = {}
        errors: list[str] = []
        clob_failures = Counter()
        compiler_rejections = Counter()
        rule_rejections = Counter()
        family_counts = Counter()
        source_counts = Counter()

        report: dict = {
            "version": WEATHER_SHADOW_RUNTIME_VERSION,
            "mode": "SILENT_SHADOW",
            "read_only": True,
            "financial_authority": False,
            "financial_delivery": False,
            "automatic_order_placement": False,
            "gamma_execution_authority": False,
            "exact_clob_required_for_recorded_opportunities": True,
            "final_live_recheck_required": True,
            "forecast_probability_authority": False,
            "source_settlement_trade_authority": False,
            "cycle_ok": False,
            "started_at": started_wall,
        }

        try:
            stage = time.monotonic()
            snapshot = await self.discovery.discover(tags)
            timings["discovery_seconds"] = time.monotonic() - stage
            report["discovery"] = snapshot.summary()
        except WeatherDiscoveryError as exc:
            errors.append(f"DISCOVERY:{exc.code}")
            report.update({
                "errors": errors,
                "opportunities": [],
                "timings_seconds": {"total": round(time.monotonic() - started, 6)},
                "process_peak_rss_bytes": _rss_bytes(),
                "finished_at": time.time(),
            })
            return report

        stage = time.monotonic()
        certified_by_id: dict[str, CompiledWeatherEvent] = {}
        raw_by_id: dict[str, dict] = {}
        semantic_proven = 0
        open_complete = 0
        for event in snapshot.events:
            eid = _event_id(event)
            if eid:
                raw_by_id[eid] = event
            compiled = compile_weather_event(event)
            family_counts[compiled.family] += 1
            source_counts[compiled.source_family] += 1
            for reason in compiled.rejection_reasons:
                compiler_rejections[str(reason)] += 1
            if compiled.family not in {DAILY_HIGH, DAILY_LOW}:
                continue
            authority = compile_temperature_rule_authority(event, compiled)
            for reason in authority.rejection_reasons:
                rule_rejections[str(reason)] += 1
            certified = apply_rule_authority(compiled, authority)
            if certified.exactly_one_outcome_proven:
                semantic_proven += 1
            if _open_complete_partition(certified):
                open_complete += 1
                certified_by_id[certified.event_id] = certified
        timings["compile_rules_seconds"] = time.monotonic() - stage

        report["compiler"] = {
            "family_counts": dict(sorted(family_counts.items())),
            "source_counts": dict(sorted(source_counts.items())),
            "compiler_rejection_counts": dict(sorted(compiler_rejections.items())),
            "rule_rejection_counts": dict(sorted(rule_rejections.items())),
            "temperature_semantic_proven_events": semantic_proven,
            "open_complete_partition_events": open_complete,
            "financial_authority_events": 0,
        }

        yes_tokens = [
            str(bucket.yes_token)
            for compiled in certified_by_id.values()
            for bucket in compiled.buckets
            if bucket.yes_token
        ]
        report["prescreen"] = {
            "candidate_event_count": len(certified_by_id),
            "yes_token_count": len(set(yes_tokens)),
            "token_cap": MAX_PRESCREEN_TOKENS,
            "raw_cost_threshold": self.prescreen_max_raw_ask_cost,
        }

        if not yes_tokens:
            report.update({
                "cycle_ok": True,
                "errors": errors,
                "clob_failure_counts": {},
                "exact_events_attempted": 0,
                "opportunities": [],
                "timings_seconds": {
                    **{key: round(value, 6) for key, value in timings.items()},
                    "total": round(time.monotonic() - started, 6),
                },
                "process_peak_rss_bytes": _rss_bytes(),
                "finished_at": time.time(),
            })
            return report

        try:
            stage = time.monotonic()
            prescreen_books = await self._prescreen_books(yes_tokens)
            timings["exact_book_prescreen_seconds"] = time.monotonic() - stage
        except (WeatherCLOBError, WeatherShadowRuntimeError) as exc:
            code = getattr(exc, "code", type(exc).__name__)
            errors.append(f"PRESCREEN_CLOB:{code}")
            report.update({
                "errors": errors,
                "clob_failure_counts": {str(code): 1},
                "exact_events_attempted": 0,
                "opportunities": [],
                "timings_seconds": {
                    **{key: round(value, 6) for key, value in timings.items()},
                    "total": round(time.monotonic() - started, 6),
                },
                "process_peak_rss_bytes": _rss_bytes(),
                "finished_at": time.time(),
            })
            return report

        ranked: list[tuple[float, str]] = []
        missing_ask_events = 0
        for event_id, compiled in certified_by_id.items():
            asks: list[float] = []
            for bucket in compiled.buckets:
                book = prescreen_books.get(str(bucket.yes_token))
                best = getattr(book, "best_ask", None)
                if best is None:
                    asks = []
                    break
                asks.append(float(best))
            if not asks:
                missing_ask_events += 1
                continue
            raw_cost = sum(asks)
            if raw_cost <= self.prescreen_max_raw_ask_cost:
                ranked.append((raw_cost, event_id))
        ranked.sort(key=lambda row: (row[0], row[1]))
        selected = ranked[: self.max_exact_events_per_cycle]
        report["prescreen"].update({
            "missing_best_ask_events": missing_ask_events,
            "threshold_match_events": len(ranked),
            "selected_exact_events": len(selected),
            "selected_event_ids": [event_id for _, event_id in selected],
        })

        opportunities: list[dict] = []
        exact_attempted = 0
        stage = time.monotonic()
        for raw_cost, event_id in selected:
            compiled = certified_by_id[event_id]
            exact_attempted += 1
            try:
                exact = await self.clob.exact_event_snapshot(compiled)
                rates, fee_error = _fee_rate_map(exact)
                if rates is None:
                    clob_failures[str(fee_error)] += 1
                    continue
                first = complete_bucket_underround(
                    compiled,
                    exact.books,
                    rates,
                    min_profit_per_set=self.min_shadow_profit_per_set,
                )
                if first is None:
                    continue

                # Full-bot lesson carried forward: candidate evidence expires.  Do
                # not record even a shadow opportunity from the first certificate;
                # fetch identity, fee metadata and books again and recompute.
                recheck = await self.clob.exact_event_snapshot(compiled)
                recheck_rates, fee_error = _fee_rate_map(recheck)
                if recheck_rates is None:
                    clob_failures[str(fee_error)] += 1
                    continue
                confirmed = complete_bucket_underround(
                    compiled,
                    recheck.books,
                    recheck_rates,
                    min_profit_per_set=self.min_shadow_profit_per_set,
                )
                if confirmed is None:
                    clob_failures["RECHECK_NO_LONGER_PROFITABLE"] += 1
                    continue
                opportunities.append(_serialize_opportunity(
                    confirmed,
                    raw_prescreen_cost=raw_cost,
                    rechecked=True,
                ))
            except WeatherCLOBError as exc:
                clob_failures[exc.code] += 1
                continue
        timings["exact_candidate_rechecks_seconds"] = time.monotonic() - stage

        report.update({
            "cycle_ok": not errors,
            "errors": errors,
            "clob_failure_counts": dict(sorted(clob_failures.items())),
            "exact_events_attempted": exact_attempted,
            "opportunity_count": len(opportunities),
            "opportunities": opportunities,
            "timings_seconds": {
                **{key: round(value, 6) for key, value in timings.items()},
                "total": round(time.monotonic() - started, 6),
            },
            "process_peak_rss_bytes": _rss_bytes(),
            "finished_at": time.time(),
        })
        return report


async def _run_once(args) -> dict:
    runtime = WeatherOnlyShadowRuntime(
        max_exact_events_per_cycle=args.max_exact_events,
        prescreen_max_raw_ask_cost=args.prescreen_max_raw_cost,
        min_shadow_profit_per_set=args.min_shadow_profit,
    )
    try:
        return await runtime.run_cycle(tuple(args.tag))
    finally:
        await runtime.close()


def _atomic_write(path: Path, payload: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(payload + "\n")
    os.replace(temporary, path)


async def _main_async(args) -> int:
    interval = max(MIN_LOOP_INTERVAL_SECONDS, float(args.interval_seconds))
    while True:
        report = await _run_once(args)
        payload = json.dumps(report, indent=2, sort_keys=True)
        if args.output:
            _atomic_write(args.output, payload)
        print(payload, flush=True)
        if not args.loop:
            return 0 if report.get("cycle_ok") else 2
        elapsed = max(0.0, float(report.get("finished_at", time.time())) - float(report.get("started_at", time.time())))
        await asyncio.sleep(max(0.0, interval - elapsed))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--loop", action="store_true", help="repeat in silent-shadow mode; default is one cycle")
    parser.add_argument("--interval-seconds", type=float, default=DEFAULT_LOOP_INTERVAL_SECONDS)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--tag", action="append", default=list(DEFAULT_TAGS))
    parser.add_argument("--max-exact-events", type=int, default=MAX_EXACT_EVENTS_PER_CYCLE)
    parser.add_argument("--prescreen-max-raw-cost", type=float, default=PRESCREEN_MAX_RAW_ASK_COST)
    parser.add_argument("--min-shadow-profit", type=float, default=MIN_SHADOW_PROFIT_PER_SET)
    args = parser.parse_args()
    raise SystemExit(asyncio.run(_main_async(args)))


if __name__ == "__main__":
    main()
