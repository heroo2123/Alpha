from __future__ import annotations

"""Small, weather-tag-scoped Gamma discovery for the weather-only scanner.

Unlike the general Astra production universe, this module never performs an
untagged walk of every active Polymarket event.  It proves natural keyset exhaustion
for each configured weather tag, deduplicates overlapping tag results, and preserves
all child markets of selected events for contract/bucket compilation.

Tag membership is discovery recall evidence only.  It never grants contract or
financial authority.
"""

import asyncio
import copy
import json
import time
from dataclasses import asdict, dataclass

import httpx

from .config import settings


GAMMA = "https://gamma-api.polymarket.com"
WEATHER_ONLY_DISCOVERY_VERSION = "weather_tag_keyset_v1_complete_per_configured_tag"
DEFAULT_TAGS = ("daily-temperature", "weather")
PAGE_SIZE = 25
MAX_PAGE_BYTES = 16 * 1024 * 1024
MAX_PAGES_PER_TAG = 200
MAX_EVENTS = 5_000
MAX_MARKETS = 50_000


class WeatherDiscoveryError(RuntimeError):
    def __init__(self, code: str):
        self.code = str(code)
        super().__init__(self.code)


@dataclass(frozen=True, slots=True)
class WeatherDiscoverySnapshot:
    version: str
    tags: tuple[str, ...]
    events: tuple[dict, ...]
    pages_by_tag: dict[str, int]
    raw_event_hits: int
    duplicate_event_hits: int
    unique_event_count: int
    unique_market_count: int
    started_at: float
    finished_at: float

    def summary(self) -> dict:
        value = asdict(self)
        value.pop("events", None)
        value["elapsed_seconds"] = self.finished_at - self.started_at
        return value


def _market_id(row: object) -> str:
    return str(row.get("id") or "").strip() if isinstance(row, dict) else ""


def _event_id(event: object) -> str:
    return str(event.get("id") or "").strip() if isinstance(event, dict) else ""


def _identity_list(value: object) -> tuple[str, ...]:
    if isinstance(value, list):
        return tuple(str(x) for x in value)
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except Exception:
            return ()
        return tuple(str(x) for x in parsed) if isinstance(parsed, list) else ()
    return ()


def _market_identity(row: dict) -> tuple[str, str, tuple[str, ...]]:
    return (
        str(row.get("question") or "").strip(),
        str(row.get("conditionId") or "").strip(),
        _identity_list(row.get("clobTokenIds")),
    )


def _nonempty_conflict(left: object, right: object) -> bool:
    a, b = str(left or "").strip(), str(right or "").strip()
    return bool(a and b and a != b)


def _merge_event(existing: dict, incoming: dict) -> dict:
    if _event_id(existing) != _event_id(incoming):
        raise WeatherDiscoveryError("DUPLICATE_EVENT_IDENTITY_CONFLICT")
    for key in ("slug", "title"):
        if _nonempty_conflict(existing.get(key), incoming.get(key)):
            raise WeatherDiscoveryError("DUPLICATE_EVENT_IDENTITY_CONFLICT")

    merged = copy.deepcopy(existing)
    # Prefer a non-empty copy of parent fields that were absent on the first tag hit.
    for key, value in incoming.items():
        if key == "markets":
            continue
        if key not in merged or merged.get(key) in (None, "", [], {}):
            merged[key] = copy.deepcopy(value)

    children: dict[str, dict] = {}
    order: list[str] = []
    for source in (existing.get("markets") or [], incoming.get("markets") or []):
        for row in source:
            if not isinstance(row, dict):
                continue
            mid = _market_id(row)
            if not mid:
                raise WeatherDiscoveryError("MARKET_ID_MISSING")
            previous = children.get(mid)
            if previous is None:
                children[mid] = copy.deepcopy(row)
                order.append(mid)
                continue
            if _market_identity(previous) != _market_identity(row):
                raise WeatherDiscoveryError("DUPLICATE_MARKET_IDENTITY_CONFLICT")
            # Preserve fields that one tag projection omitted without rewriting
            # non-empty values from the first response.
            for key, value in row.items():
                if key not in previous or previous.get(key) in (None, "", [], {}):
                    previous[key] = copy.deepcopy(value)
    merged["markets"] = [children[mid] for mid in order]
    return merged


class WeatherOnlyDiscovery:
    def __init__(self) -> None:
        self.http = httpx.AsyncClient(
            timeout=settings.request_timeout,
            limits=httpx.Limits(max_connections=4, max_keepalive_connections=2, keepalive_expiry=20.0),
            headers={"User-Agent": "polymarket-weather-only-scanner/0.1 (+github)"},
        )

    async def close(self) -> None:
        await self.http.aclose()

    async def _keyset_page(self, tag: str, cursor: str | None) -> tuple[list[dict], str | None]:
        params: dict[str, object] = {
            "active": "true",
            "closed": "false",
            "limit": PAGE_SIZE,
            "tag_slug": tag,
        }
        if cursor:
            params["after_cursor"] = cursor

        for attempt in range(4):
            try:
                response = await self.http.get(f"{GAMMA}/events/keyset", params=params)
            except httpx.TimeoutException:
                if attempt == 3:
                    raise WeatherDiscoveryError("HTTP_TIMEOUT")
                await asyncio.sleep(min(4.0, 0.5 * (2 ** attempt)))
                continue
            except httpx.RequestError:
                if attempt == 3:
                    raise WeatherDiscoveryError("HTTP_TRANSPORT")
                await asyncio.sleep(min(4.0, 0.5 * (2 ** attempt)))
                continue

            if response.status_code == 429 or response.status_code >= 500:
                if attempt < 3:
                    await asyncio.sleep(min(4.0, 0.5 * (2 ** attempt)))
                    continue
                raise WeatherDiscoveryError("HTTP_STATUS")
            if response.status_code >= 400:
                raise WeatherDiscoveryError("HTTP_STATUS")
            if len(response.content) > MAX_PAGE_BYTES:
                raise WeatherDiscoveryError("PAGE_BYTES_CAP")
            try:
                payload = response.json()
            except Exception:
                raise WeatherDiscoveryError("MALFORMED_JSON")
            if not isinstance(payload, dict):
                raise WeatherDiscoveryError("MALFORMED_ENVELOPE")
            events = payload.get("events")
            if not isinstance(events, list) or any(not isinstance(event, dict) for event in events):
                raise WeatherDiscoveryError("MALFORMED_EVENTS")
            raw_cursor = payload.get("next_cursor")
            if raw_cursor is not None and not isinstance(raw_cursor, str):
                raise WeatherDiscoveryError("MALFORMED_CURSOR")
            next_cursor = raw_cursor.strip() if isinstance(raw_cursor, str) else ""
            next_cursor = next_cursor or None
            if next_cursor is not None and not events:
                raise WeatherDiscoveryError("EMPTY_PAGE_WITH_CURSOR")
            return events, next_cursor

        raise WeatherDiscoveryError("HTTP_RETRY_EXHAUSTED")

    async def discover(self, tags: tuple[str, ...] = DEFAULT_TAGS) -> WeatherDiscoverySnapshot:
        cleaned = tuple(dict.fromkeys(str(tag).strip() for tag in tags if str(tag).strip()))
        if not cleaned:
            raise WeatherDiscoveryError("NO_TAGS_CONFIGURED")

        started = time.time()
        by_id: dict[str, dict] = {}
        order: list[str] = []
        market_owner: dict[str, str] = {}
        pages_by_tag: dict[str, int] = {}
        raw_event_hits = 0
        duplicate_event_hits = 0

        for tag in cleaned:
            cursor: str | None = None
            seen_cursors: set[str] = set()
            pages = 0
            while True:
                events, next_cursor = await self._keyset_page(tag, cursor)
                pages += 1
                if pages > MAX_PAGES_PER_TAG:
                    raise WeatherDiscoveryError("TAG_PAGE_CAP")
                raw_event_hits += len(events)

                for event in events:
                    eid = _event_id(event)
                    if not eid:
                        raise WeatherDiscoveryError("EVENT_ID_MISSING")
                    for row in event.get("markets") or []:
                        if not isinstance(row, dict):
                            continue
                        mid = _market_id(row)
                        if not mid:
                            raise WeatherDiscoveryError("MARKET_ID_MISSING")
                        owner = market_owner.get(mid)
                        if owner is not None and owner != eid:
                            raise WeatherDiscoveryError("MARKET_PARENT_CONFLICT")
                        market_owner[mid] = eid

                    if eid in by_id:
                        duplicate_event_hits += 1
                        by_id[eid] = _merge_event(by_id[eid], event)
                    else:
                        by_id[eid] = copy.deepcopy(event)
                        order.append(eid)
                    if len(by_id) > MAX_EVENTS:
                        raise WeatherDiscoveryError("EVENT_CAP")
                    if len(market_owner) > MAX_MARKETS:
                        raise WeatherDiscoveryError("MARKET_CAP")

                if next_cursor is None:
                    break
                if next_cursor == cursor or next_cursor in seen_cursors:
                    raise WeatherDiscoveryError("CURSOR_REPEAT")
                seen_cursors.add(next_cursor)
                cursor = next_cursor

            pages_by_tag[tag] = pages

        finished = time.time()
        return WeatherDiscoverySnapshot(
            version=WEATHER_ONLY_DISCOVERY_VERSION,
            tags=cleaned,
            events=tuple(by_id[eid] for eid in order),
            pages_by_tag=pages_by_tag,
            raw_event_hits=raw_event_hits,
            duplicate_event_hits=duplicate_event_hits,
            unique_event_count=len(by_id),
            unique_market_count=len(market_owner),
            started_at=started,
            finished_at=finished,
        )
