from __future__ import annotations

"""Fail-closed weather discovery with tagged fast path plus exhaustive Gamma recall.

The weather tags remain the cheap low-latency path, but tag membership is no longer a
recall assumption. A cached untagged active-event keyset census is exhausted naturally.
The census classifies every *weather-looking* daily-temperature event even when the
strict compiler rejects it, while only strict-supported events are merged into the
runtime discovery set. Gamma enumeration completeness and semantic coverage are
therefore separate, explicit facts.

The exhaustive census has a deliberately short five-minute reuse budget. Its actual
completion timestamp and age are exposed as first-class evidence so a caller never has
to infer freshness from a previous cycle's status.
"""

import asyncio
import copy
import json
import re
import time
from dataclasses import asdict, dataclass

import httpx

from .config import settings


GAMMA = "https://gamma-api.polymarket.com"
WEATHER_ONLY_DISCOVERY_VERSION = "weather_keyset_v5_exhaustive_semantic_census_5m"
DEFAULT_TAGS = ("daily-temperature", "weather")
PAGE_SIZE = 25
GLOBAL_PAGE_SIZE = 50
MAX_PAGE_BYTES = 16 * 1024 * 1024
MAX_PAGES_PER_TAG = 200
MAX_GLOBAL_PAGES = 5_000
MAX_GLOBAL_EVENT_HITS = 250_000
GLOBAL_CENSUS_TTL_SECONDS = 300.0
MAX_EVENTS = 5_000
MAX_MARKETS = 50_000
MAX_UNSUPPORTED_EXAMPLES = 20
SEMANTIC_POLICY = "STRICT_SUPPORTED_SUBSET"
SEMANTIC_FAILURE_CATEGORIES = frozenset(
    {
        "UNSUPPORTED_STATION",
        "UNSUPPORTED_RULE_GRAMMAR",
        "UNSUPPORTED_UNIT",
        "UNSUPPORTED_FAMILY",
        "UNSUPPORTED_BUCKET_FORM",
        "AMBIGUOUS",
        "OTHER_FAIL_CLOSED",
    }
)
_MONTH_RE = re.compile(
    r"\b(?:january|february|march|april|may|june|july|august|september|october|november|december|"
    r"jan|feb|mar|apr|jun|jul|aug|sep|sept|oct|nov|dec)\b",
    re.I,
)
_DAILY_TEMP_RE = re.compile(r"\b(?:highest|lowest|high|low)\b.{0,48}\btemperature\b", re.I)
_TEMP_UNIT_RE = re.compile(r"(?:°\s*[fc]\b|\bdegrees?\s+[fc]\b)", re.I)


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
    global_census_complete: bool = False
    global_census_cache_hit: bool = False
    global_census_pages: int = 0
    global_census_scanned_events: int = 0
    global_census_retained_events: int = 0
    global_census_completed_at: float | None = None
    global_census_age_seconds: float | None = None
    global_census_max_reuse_seconds: float = GLOBAL_CENSUS_TTL_SECONDS

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


def _merge_safety_state(target: dict, incoming: dict) -> None:
    for key in ("active", "acceptingOrders", "enableOrderBook"):
        left, right = target.get(key), incoming.get(key)
        if left is False or right is False:
            target[key] = False
        elif left is True or right is True:
            target[key] = True
    for key in ("closed", "archived"):
        left, right = target.get(key), incoming.get(key)
        if left is True or right is True:
            target[key] = True
        elif left is False or right is False:
            target[key] = False


def _merge_event(existing: dict, incoming: dict) -> dict:
    if _event_id(existing) != _event_id(incoming):
        raise WeatherDiscoveryError("DUPLICATE_EVENT_IDENTITY_CONFLICT")
    for key in ("slug", "title"):
        if _nonempty_conflict(existing.get(key), incoming.get(key)):
            raise WeatherDiscoveryError("DUPLICATE_EVENT_IDENTITY_CONFLICT")
    merged = copy.deepcopy(existing)
    for key, value in incoming.items():
        if key == "markets":
            continue
        if key not in merged or merged.get(key) in (None, "", [], {}):
            merged[key] = copy.deepcopy(value)
    _merge_safety_state(merged, incoming)
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
            for key, value in row.items():
                if key not in previous or previous.get(key) in (None, "", [], {}):
                    previous[key] = copy.deepcopy(value)
            _merge_safety_state(previous, row)
    merged["markets"] = [children[mid] for mid in order]
    return merged


class WeatherOnlyDiscovery:
    def __init__(self) -> None:
        self.http = httpx.AsyncClient(
            timeout=settings.request_timeout,
            limits=httpx.Limits(
                max_connections=4,
                max_keepalive_connections=2,
                keepalive_expiry=20.0,
            ),
            headers={"User-Agent": "polymarket-weather-only-scanner/0.1 (+github)"},
            trust_env=False,
        )
        self._global_cache_at = 0.0
        self._global_cache_events: tuple[dict, ...] = ()
        self._global_cache_pages = 0
        self._global_cache_scanned = 0
        self._global_cache_semantic = self._empty_semantic_census()
        self._last_global_recall = self._recall_payload(
            complete=False,
            cache_hit=False,
            pages=0,
            scanned=0,
            retained=0,
            completed_at=None,
            semantic=self._global_cache_semantic,
        )

    @staticmethod
    def _empty_semantic_census() -> dict:
        return {
            "weather_looking_events": 0,
            "strict_supported_events": 0,
            "unsupported_weather_events": 0,
            "unsupported_reason_counts": {},
            "unsupported_examples": [],
            "weather_semantic_coverage_complete": False,
            "weather_semantic_coverage_status": "NOT_RUN",
            "weather_semantic_product_policy": SEMANTIC_POLICY,
        }

    @staticmethod
    def _recall_payload(
        *,
        complete: bool,
        cache_hit: bool,
        pages: int,
        scanned: int,
        retained: int,
        completed_at: float | None,
        semantic: dict,
    ) -> dict:
        age = None if completed_at is None else max(0.0, time.time() - float(completed_at))
        out = {
            "complete": bool(complete),
            "cache_hit": bool(cache_hit),
            "pages": int(pages),
            "scanned_events": int(scanned),
            "retained_events": int(retained),
            "census_completed_at": completed_at,
            "age_seconds": age,
            "max_reuse_seconds": GLOBAL_CENSUS_TTL_SECONDS,
            "gamma_census_complete": bool(complete),
            "gamma_census_completed_at": completed_at,
            "gamma_census_age_seconds": age,
            "total_active_events_scanned": int(scanned),
        }
        out.update(copy.deepcopy(semantic))
        return out

    async def close(self) -> None:
        await self.http.aclose()

    def global_recall_status(self) -> dict:
        value = copy.deepcopy(self._last_global_recall)
        completed = value.get("census_completed_at")
        if isinstance(completed, (int, float)) and not isinstance(completed, bool):
            age = max(0.0, time.time() - float(completed))
            value["age_seconds"] = age
            value["gamma_census_age_seconds"] = age
        value["max_reuse_seconds"] = GLOBAL_CENSUS_TTL_SECONDS
        return value

    async def _keyset_page(
        self,
        tag: str | None,
        cursor: str | None,
        *,
        page_size: int = PAGE_SIZE,
    ) -> tuple[list[dict], str | None]:
        params: dict[str, object] = {
            "active": "true",
            "closed": "false",
            "limit": int(page_size),
        }
        if tag:
            params["tag_slug"] = tag
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
                # Preserve the fixed per-response byte ceiling, but do not make
                # catalog growth at one legal keyset cursor a permanent outage.
                # Retry the exact same cursor with fewer events.  No oversized
                # response is parsed or accepted.  If a single-event page still
                # exceeds the cap, fail closed as before.
                if int(page_size) > 1:
                    return await self._keyset_page(
                        tag,
                        cursor,
                        page_size=max(1, int(page_size) // 2),
                    )
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

    @staticmethod
    def _weather_looking_candidate(event: dict) -> bool:
        """Inclusive census predicate; never grants trading support by itself."""
        try:
            title = str(event.get("title") or "")
            markets = event.get("markets") or []
            questions = [str(row.get("question") or "") for row in markets if isinstance(row, dict)]
            texts = [title, *questions]
        except Exception:
            return False
        joined = " ".join(texts)
        tags = event.get("tags") or []
        tag_text = " ".join(str(t.get("slug") or t.get("label") or "") if isinstance(t, dict) else str(t) for t in tags).lower()
        if any(word in tag_text for word in ("weather", "temperature")):
            return True
        if "temperature" not in joined.lower():
            return False
        # Unknown temperature wording belongs in the rejection denominator too.
        # This predicate grants no semantic or trading authority.
        return True

    @staticmethod
    def _semantic_classification(event: dict) -> tuple[str, str]:
        from .weather_only_contract_strict import StrictWeatherContractError, compile_strict_temperature_event

        try:
            compile_strict_temperature_event(event)
            return "SUPPORTED", "SUPPORTED"
        except StrictWeatherContractError as exc:
            code = str(exc.code)
        except Exception:
            return "OTHER_FAIL_CLOSED", "UNEXPECTED_STRICT_COMPILER_FAILURE"
        if "AMBIG" in code:
            return "AMBIGUOUS", code
        if "STATION" in code or "SOURCE_URL" in code:
            return "UNSUPPORTED_STATION", code
        if "UNIT" in code:
            return "UNSUPPORTED_UNIT", code
        if "FAMILY" in code or "STATISTIC" in code:
            return "UNSUPPORTED_FAMILY", code
        if "BUCKET" in code or "PARTITION" in code or "CHILD_COUNT" in code:
            return "UNSUPPORTED_BUCKET_FORM", code
        if "RULE" in code or "OPERATIVE" in code or "SOURCE_CONFLICT" in code:
            return "UNSUPPORTED_RULE_GRAMMAR", code
        if "CONFLICT" in code:
            return "AMBIGUOUS", code
        return "OTHER_FAIL_CLOSED", code

    async def _global_weather_census(self) -> tuple[tuple[dict, ...], int, int, bool]:
        now = time.time()
        cache_age = now - self._global_cache_at
        if (
            self._global_cache_at > 0.0
            and self._global_cache_pages > 0
            and 0.0 <= cache_age <= GLOBAL_CENSUS_TTL_SECONDS
        ):
            self._last_global_recall = self._recall_payload(
                complete=True,
                cache_hit=True,
                pages=self._global_cache_pages,
                scanned=self._global_cache_scanned,
                retained=len(self._global_cache_events),
                completed_at=self._global_cache_at,
                semantic=self._global_cache_semantic,
            )
            return self._global_cache_events, self._global_cache_pages, self._global_cache_scanned, True

        cursor: str | None = None
        seen_cursors: set[str] = set()
        retained: list[dict] = []
        pages = 0
        scanned = 0
        weather_looking = 0
        supported = 0
        reason_counts: dict[str, int] = {}
        unsupported_examples: list[dict] = []
        semantic_ledger: list[dict] = []

        while True:
            events, next_cursor = await self._keyset_page(None, cursor, page_size=GLOBAL_PAGE_SIZE)
            pages += 1
            if pages > MAX_GLOBAL_PAGES:
                raise WeatherDiscoveryError("GLOBAL_PAGE_CAP")
            scanned += len(events)
            if scanned > MAX_GLOBAL_EVENT_HITS:
                raise WeatherDiscoveryError("GLOBAL_EVENT_CAP")
            for event in events:
                if not self._weather_looking_candidate(event):
                    continue
                weather_looking += 1
                category, detail = self._semantic_classification(event)
                semantic_ledger.append({"event_id": str(event.get("id") or ""),
                                        "classification": category, "detail": str(detail)[:220]})
                if category == "SUPPORTED":
                    supported += 1
                    retained.append(copy.deepcopy(event))
                    if len(retained) > MAX_EVENTS:
                        raise WeatherDiscoveryError("GLOBAL_STRICT_EVENT_CAP")
                    continue
                if category not in SEMANTIC_FAILURE_CATEGORIES:
                    category, detail = "OTHER_FAIL_CLOSED", f"UNSTABLE_CATEGORY:{category}:{detail}"
                reason_counts[category] = reason_counts.get(category, 0) + 1
                if len(unsupported_examples) < MAX_UNSUPPORTED_EXAMPLES:
                    unsupported_examples.append(
                        {
                            "event_id": str(event.get("id") or ""),
                            "title": str(event.get("title") or "")[:220],
                            "classification": category,
                            "detail": str(detail)[:220],
                        }
                    )
            if next_cursor is None:
                break
            if next_cursor == cursor or next_cursor in seen_cursors:
                raise WeatherDiscoveryError("GLOBAL_CURSOR_REPEAT")
            seen_cursors.add(next_cursor)
            cursor = next_cursor

        unsupported = weather_looking - supported
        semantic = {
            "weather_looking_events": weather_looking,
            "strict_supported_events": supported,
            "unsupported_weather_events": unsupported,
            "unsupported_reason_counts": dict(sorted(reason_counts.items())),
            "unsupported_examples": unsupported_examples,
            "semantic_event_ledger": semantic_ledger,
            "weather_semantic_coverage_complete": unsupported == 0,
            "weather_semantic_coverage_status": "COMPLETE" if unsupported == 0 else "PARTIAL_STRICT_SUBSET",
            "weather_semantic_product_policy": SEMANTIC_POLICY,
        }

        completed_at = time.time()
        self._global_cache_at = completed_at
        self._global_cache_events = tuple(retained)
        self._global_cache_pages = pages
        self._global_cache_scanned = scanned
        self._global_cache_semantic = semantic
        self._last_global_recall = self._recall_payload(
            complete=True,
            cache_hit=False,
            pages=pages,
            scanned=scanned,
            retained=len(retained),
            completed_at=completed_at,
            semantic=semantic,
        )
        return self._global_cache_events, pages, scanned, False

    @staticmethod
    def _ingest(
        event: dict,
        *,
        by_id: dict[str, dict],
        order: list[str],
        market_owner: dict[str, str],
    ) -> bool:
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
        duplicate = eid in by_id
        if duplicate:
            by_id[eid] = _merge_event(by_id[eid], event)
        else:
            by_id[eid] = copy.deepcopy(event)
            order.append(eid)
        if len(by_id) > MAX_EVENTS:
            raise WeatherDiscoveryError("EVENT_CAP")
        if len(market_owner) > MAX_MARKETS:
            raise WeatherDiscoveryError("MARKET_CAP")
        return duplicate

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
                    duplicate_event_hits += int(
                        self._ingest(event, by_id=by_id, order=order, market_owner=market_owner)
                    )
                if next_cursor is None:
                    break
                if next_cursor == cursor or next_cursor in seen_cursors:
                    raise WeatherDiscoveryError("CURSOR_REPEAT")
                seen_cursors.add(next_cursor)
                cursor = next_cursor
            pages_by_tag[tag] = pages

        global_events, global_pages, global_scanned, cache_hit = await self._global_weather_census()
        for event in global_events:
            duplicate_event_hits += int(
                self._ingest(event, by_id=by_id, order=order, market_owner=market_owner)
            )
        pages_by_tag["__global__"] = global_pages
        finished = time.time()
        census_completed_at = self._global_cache_at if self._global_cache_at > 0.0 else None
        census_age = None if census_completed_at is None else max(0.0, finished - census_completed_at)

        self._last_global_recall = self._recall_payload(
            complete=True,
            cache_hit=cache_hit,
            pages=global_pages,
            scanned=global_scanned,
            retained=len(global_events),
            completed_at=census_completed_at,
            semantic=self._global_cache_semantic,
        )

        return WeatherDiscoverySnapshot(
            version=WEATHER_ONLY_DISCOVERY_VERSION,
            tags=cleaned,
            events=tuple(by_id[eid] for eid in order),
            pages_by_tag=pages_by_tag,
            raw_event_hits=raw_event_hits + global_scanned,
            duplicate_event_hits=duplicate_event_hits,
            unique_event_count=len(by_id),
            unique_market_count=len(market_owner),
            started_at=started,
            finished_at=finished,
            global_census_complete=True,
            global_census_cache_hit=bool(cache_hit),
            global_census_pages=int(global_pages),
            global_census_scanned_events=int(global_scanned),
            global_census_retained_events=len(global_events),
            global_census_completed_at=census_completed_at,
            global_census_age_seconds=census_age,
            global_census_max_reuse_seconds=GLOBAL_CENSUS_TTL_SECONDS,
        )
