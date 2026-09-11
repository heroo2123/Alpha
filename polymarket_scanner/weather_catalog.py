from __future__ import annotations

"""Bounded weather-only Gamma discovery and contract census.

This module is intentionally independent from the general production-universe walk.
It discovers only the finite Gamma ``weather`` tag using shallow offset pagination,
requires natural exhaustion before a fixed page ceiling, and reports what the branch
would need to support. It does not produce financial signals or claim that a family
classifier is a settlement-contract proof.
"""

import argparse
import asyncio
import json
import re
from collections import Counter
from dataclasses import dataclass, asdict
from pathlib import Path
from urllib.parse import urlparse

from .polymarket import PolymarketClient, UniverseIncompleteError, _json_list
from .config import settings

WEATHER_CATALOG_VERSION = "weather_catalog_v1_tag_bounded_contract_census"
WEATHER_TAG_SLUG = "weather"
WEATHER_PAGE_CEILING = 20

_URL_RE = re.compile(r"https?://[^\s<>\"')]+", re.I)
_MONTH_RE = re.compile(
    r"\b(?:january|february|march|april|may|june|july|august|september|october|november|december)\b",
    re.I,
)

FAMILY_DAILY_HIGH_TEMP = "daily_high_temperature"
FAMILY_DAILY_LOW_TEMP = "daily_low_temperature"
FAMILY_DAILY_RAIN = "daily_measurable_rain"
FAMILY_MONTHLY_PRECIP = "monthly_precipitation"
FAMILY_DROUGHT = "drought"
FAMILY_HURRICANE = "hurricane_tropical_cyclone"
FAMILY_HYDROLOGY = "river_lake_level"
FAMILY_GLOBAL_TEMP = "global_record_temperature"
FAMILY_WEATHER_OTHER = "weather_other_unsupported"


@dataclass(slots=True)
class CatalogMarket:
    event_id: str
    market_id: str
    event_slug: str
    market_slug: str
    event_title: str
    question: str
    family: str
    adapter_candidate: str
    source_hosts: list[str]
    active: bool
    closed: bool
    accepting_orders: bool
    enable_order_book: bool
    tradable: bool
    token_count: int
    outcome_count: int
    event_market_count: int
    exhaustive_event_candidate: bool
    unsupported_reason: str | None


def _flag(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return False


def _urls(*values: object) -> list[str]:
    out: list[str] = []
    for value in values:
        for raw in _URL_RE.findall(str(value or "")):
            clean = raw.rstrip(".,;")
            if clean and clean not in out:
                out.append(clean)
    return out


def source_hosts(event: dict, market: dict) -> list[str]:
    hosts: set[str] = set()
    for url in _urls(
        market.get("resolutionSource"),
        event.get("resolutionSource"),
        market.get("description"),
        event.get("description"),
    ):
        try:
            host = (urlparse(url).hostname or "").lower().rstrip(".")
        except Exception:
            host = ""
        if host:
            hosts.add(host)
    return sorted(hosts)


def classify_weather_family(event: dict, market: dict) -> str:
    title = str(event.get("title") or "")
    question = str(market.get("question") or "")
    description = str(market.get("description") or event.get("description") or "")
    category = str(market.get("category") or event.get("category") or "")
    text = f"{title} {question} {description} {category}".lower()

    if "highest temperature" in text:
        return FAMILY_DAILY_HIGH_TEMP
    if "lowest temperature" in text:
        return FAMILY_DAILY_LOW_TEMP

    if (
        "where will it rain" in title.lower()
        or (
            "measurable precipitation" in text
            and ("daily climate report" in text or "precipitation (in)" in text)
        )
    ):
        return FAMILY_DAILY_RAIN

    if "precipitation" in text and _MONTH_RE.search(f"{title} {question}"):
        return FAMILY_MONTHLY_PRECIP

    if "drought" in text or "d4 (exceptional drought)" in text:
        return FAMILY_DROUGHT

    if any(term in text for term in (
        "hurricane", "tropical cyclone", "tropical storm", "named storm", "typhoon"
    )):
        return FAMILY_HURRICANE

    if any(term in text for term in (
        "lake mead", "lake powell", "river level", "river return", "river return to normal",
        "water level", "gauge height", "streamflow", "rhine river", "danube river"
    )):
        return FAMILY_HYDROLOGY

    if any(term in text for term in (
        "hottest on record", "temperature increase", "global temperature",
        "all-time high temperature records", "hottest summer"
    )):
        return FAMILY_GLOBAL_TEMP

    return FAMILY_WEATHER_OTHER


def adapter_candidate(family: str, event: dict, market: dict, hosts: list[str]) -> tuple[str, str | None]:
    text = f"{event.get('description') or ''} {market.get('description') or ''} {event.get('resolutionSource') or ''} {market.get('resolutionSource') or ''}".lower()

    if family in {FAMILY_DAILY_HIGH_TEMP, FAMILY_DAILY_LOW_TEMP}:
        if "weather.gov/wrh/timeseries" in text:
            return "DAILY_TEMP_NWS_WRH_WITH_FALLBACK_RULE_TREE_V1_CANDIDATE", None
        return "DAILY_TEMP_SOURCE_SPECIFIC_REQUIRED", "temperature family lacks recognized NWS WRH primary source"

    if family == FAMILY_DAILY_RAIN:
        if "daily climate report" in text and ("forecast.weather.gov" in hosts or "weather.gov" in hosts):
            return "DAILY_RAIN_NWS_CLI_V1_CANDIDATE", None
        return "DAILY_RAIN_SOURCE_SPECIFIC_REQUIRED", "daily-rain family lacks recognized NWS CLI rule source"

    if family == FAMILY_MONTHLY_PRECIP:
        if "data.kma.go.kr" in hosts:
            return "MONTHLY_PRECIP_KMA_V1_CANDIDATE", None
        return "MONTHLY_PRECIP_SOURCE_SPECIFIC_REQUIRED", "monthly precipitation needs a source-specific adapter"

    if family == FAMILY_DROUGHT:
        return "DROUGHT_SOURCE_SPECIFIC_REQUIRED", "drought adapter not implemented"
    if family == FAMILY_HURRICANE:
        return "NHC_OR_RULE_SOURCE_ADAPTER_REQUIRED", "hurricane/tropical-cyclone adapter not implemented"
    if family == FAMILY_HYDROLOGY:
        return "HYDRO_GAUGE_SOURCE_ADAPTER_REQUIRED", "hydrology adapter not implemented"
    if family == FAMILY_GLOBAL_TEMP:
        return "GLOBAL_TEMP_SOURCE_ADAPTER_REQUIRED", "global/record-temperature adapter not implemented"
    return "UNSUPPORTED_WEATHER_FAMILY", "no weather contract adapter classified"


def _event_exhaustive_candidate(family: str, children: list[dict]) -> bool:
    """Return only a *candidate* flag, never a semantic arbitrage proof."""
    if len(children) < 2:
        return False
    if family not in {
        FAMILY_DAILY_HIGH_TEMP,
        FAMILY_DAILY_LOW_TEMP,
        FAMILY_MONTHLY_PRECIP,
        FAMILY_DROUGHT,
        FAMILY_HURRICANE,
        FAMILY_HYDROLOGY,
        FAMILY_GLOBAL_TEMP,
    }:
        return False
    return all(isinstance(row, dict) and str(row.get("id") or "").strip() for row in children)


def catalog_market(event: dict, market: dict) -> CatalogMarket:
    children = [row for row in (event.get("markets") or []) if isinstance(row, dict)]
    family = classify_weather_family(event, market)
    hosts = source_hosts(event, market)
    candidate, unsupported = adapter_candidate(family, event, market, hosts)

    active = _flag(market.get("active", True))
    closed = _flag(market.get("closed", False))
    accepting = _flag(market.get("acceptingOrders"))
    order_book = _flag(market.get("enableOrderBook"))
    tokens = [str(x).strip() for x in _json_list(market.get("clobTokenIds")) if str(x).strip()]
    outcomes = [str(x).strip() for x in _json_list(market.get("outcomes")) if str(x).strip()]
    tradable = bool(active and not closed and accepting and order_book and len(tokens) >= 2)

    return CatalogMarket(
        event_id=str(event.get("id") or ""),
        market_id=str(market.get("id") or ""),
        event_slug=str(event.get("slug") or ""),
        market_slug=str(market.get("slug") or ""),
        event_title=str(event.get("title") or ""),
        question=str(market.get("question") or ""),
        family=family,
        adapter_candidate=candidate,
        source_hosts=hosts,
        active=active,
        closed=closed,
        accepting_orders=accepting,
        enable_order_book=order_book,
        tradable=tradable,
        token_count=len(tokens),
        outcome_count=len(outcomes),
        event_market_count=len(children),
        exhaustive_event_candidate=_event_exhaustive_candidate(family, children),
        unsupported_reason=unsupported,
    )


async def fetch_weather_events(
    client: PolymarketClient,
    *,
    tag_slug: str = WEATHER_TAG_SLUG,
    page_ceiling: int = WEATHER_PAGE_CEILING,
) -> tuple[list[dict], int]:
    """Fetch only the bounded weather-tag catalog and prove a short terminal page.

    This deliberately does not fall back to the full active-universe keyset walk.
    If the weather tag grows beyond the fixed shallow pagination envelope, the
    census fails closed and engineering must revise discovery explicitly.
    """
    page_size = max(1, min(int(settings.gamma_page_size), 100))
    if page_ceiling <= 0:
        raise ValueError("page_ceiling must be positive")

    events_by_id: dict[str, dict] = {}
    pages = 0
    exhausted = False
    for page in range(page_ceiling):
        rows = await client._event_page(page * page_size, tag_slug=tag_slug)
        pages += 1
        if not isinstance(rows, list):
            raise UniverseIncompleteError("weather-tag page is not a list")
        for event in rows:
            if not isinstance(event, dict):
                continue
            event_id = str(event.get("id") or "").strip()
            if not event_id:
                continue
            prior = events_by_id.get(event_id)
            if prior is not None and prior != event:
                raise UniverseIncompleteError("weather-tag pagination returned conflicting duplicate event")
            events_by_id[event_id] = event
        if len(rows) < page_size:
            exhausted = True
            break

    if not exhausted:
        raise UniverseIncompleteError(
            f"weather-tag catalog did not exhaust within {page_ceiling} pages of {page_size}"
        )
    return list(events_by_id.values()), pages


def build_census(events: list[dict], *, pages: int) -> dict:
    markets_by_id: dict[str, CatalogMarket] = {}
    event_ids: set[str] = set()

    for event in events:
        event_id = str(event.get("id") or "").strip()
        if event_id:
            event_ids.add(event_id)
        for market in event.get("markets") or []:
            if not isinstance(market, dict):
                continue
            market_id = str(market.get("id") or "").strip()
            if not market_id:
                continue
            row = catalog_market(event, market)
            prior = markets_by_id.get(market_id)
            if prior is not None and prior != row:
                raise UniverseIncompleteError("weather census found conflicting duplicate market")
            markets_by_id[market_id] = row

    rows = list(markets_by_id.values())
    family_counts = Counter(row.family for row in rows)
    adapter_counts = Counter(row.adapter_candidate for row in rows)
    source_counts = Counter(host for row in rows for host in row.source_hosts)

    return {
        "version": WEATHER_CATALOG_VERSION,
        "complete": True,
        "discovery_scope": f"Gamma events tag_slug={WEATHER_TAG_SLUG}",
        "pages": pages,
        "event_count": len(event_ids),
        "market_count": len(rows),
        "tradable_market_count": sum(row.tradable for row in rows),
        "token_count": sum(row.token_count for row in rows),
        "tradable_token_count": sum(row.token_count for row in rows if row.tradable),
        "unsupported_market_count": sum(row.unsupported_reason is not None for row in rows),
        "exhaustive_event_candidate_market_count": sum(row.exhaustive_event_candidate for row in rows),
        "family_counts": dict(sorted(family_counts.items())),
        "adapter_candidate_counts": dict(sorted(adapter_counts.items())),
        "source_host_counts": dict(sorted(source_counts.items())),
        "markets": [asdict(row) for row in sorted(rows, key=lambda row: (row.family, row.event_id, row.market_id))],
    }


async def census(*, tag_slug: str = WEATHER_TAG_SLUG, page_ceiling: int = WEATHER_PAGE_CEILING) -> dict:
    client = PolymarketClient()
    try:
        events, pages = await fetch_weather_events(client, tag_slug=tag_slug, page_ceiling=page_ceiling)
        return build_census(events, pages=pages)
    finally:
        await client.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Bounded weather-only Gamma catalog census")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--summary-only", action="store_true")
    parser.add_argument("--page-ceiling", type=int, default=WEATHER_PAGE_CEILING)
    args = parser.parse_args()

    report = asyncio.run(census(page_ceiling=args.page_ceiling))
    if args.summary_only:
        report = {key: value for key, value in report.items() if key != "markets"}
    payload = json.dumps(report, indent=2, sort_keys=True)
    if args.output:
        args.output.write_text(payload + "\n")
    print(payload)


if __name__ == "__main__":
    main()
