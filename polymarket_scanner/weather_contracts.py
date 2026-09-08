from __future__ import annotations

"""Fail-closed settlement contract boundary for weather research.

This adapter is intentionally narrow. Only an explicit bucket unit in the market
question plus a dedicated NWS WRH time-series *primary* resolution source is admitted
to the current weather experiment. Wunderground, HKO, Dyacon and other legitimate
families stay silent until they have separate versioned adapters.

V4 makes source precedence and the daily contract interval explicit. A WRH-looking
URL found only in free-form rules text is never allowed to become the primary source.
Any distinct/fallback source in the rules makes this primary-only adapter unsupported
until its fallback policy is implemented. The target day must be exactly one
unambiguous Month Day, YYYY date in the event/question identity; missing-year,
multiple-date and malformed intervals fail closed rather than defaulting to "today".

The temporal data boundary remains fail-closed: only observations stamped by the
current exact-identity AWC proxy adapter may enter prospective V4 research, station
identity must match the cache key, future/non-finite rows are rejected, identical
same-time duplicates collapse, conflicting same-time temperatures are discarded, and
attached forecast context must itself be fresh and causally available. AWC remains a
proxy and is never upgraded here to settlement authority.
"""

import math
import re
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from urllib.parse import parse_qs, urlparse
from zoneinfo import ZoneInfo

from .models import Market
from .weather import (
    AWC_OBSERVATION_ADAPTER,
    FORECAST_REFRESH_SECONDS,
    Observation,
    ObservationBatch,
    STATION_TZ,
)

_URL_RE = re.compile(r"https?://[^\s<>\"')]+", re.I)
_FALLBACK_RE = re.compile(
    r"\b(?:fallback|fall\s+back|alternate(?:ly)?|secondary\s+source|"
    r"if\s+(?:the\s+)?(?:primary\s+)?source\s+(?:is\s+)?(?:unavailable|missing|down)|"
    r"wunderground|weather\s+underground|hong\s+kong\s+observatory|dyacon)\b",
    re.I,
)
_MONTHS = {
    "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6,
    "july": 7, "august": 8, "september": 9, "october": 10, "november": 11, "december": 12,
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "jun": 6, "jul": 7, "aug": 8,
    "sep": 9, "sept": 9, "oct": 10, "nov": 11, "dec": 12,
}
_DATE_RE = re.compile(
    r"\b(January|February|March|April|May|June|July|August|September|October|November|December|"
    r"Jan|Feb|Mar|Apr|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)[a-z]*\s+"
    r"(\d{1,2})(?:st|nd|rd|th)?(?:,)?\s+(20\d{2})\b",
    re.I,
)
WEATHER_CONTRACT_ADAPTER = "NWS_WRH_PRIMARY_ONLY_V4_EXACT_DATE"
WEATHER_LATE_MODEL_VERSION = "uncalibrated_v4_contract_safe"
WEATHER_FRIEND_MODEL_VERSION = "friend_uncalibrated_v4_contract_safe"
MAX_CLOCK_SKEW_SECONDS = 5.0


def contract_unit_from_question(question: str) -> str | None:
    """Return the bucket's explicit F/C unit from the question, or fail closed."""
    text = str(question or "")
    found: set[str] = set()
    if re.search(r"(?:°\s*)?F\b|\bFahrenheit\b", text, re.I):
        found.add("F")
    if re.search(r"(?:°\s*)?C\b|\bCelsius\b", text, re.I):
        found.add("C")
    return next(iter(found)) if len(found) == 1 else None


def exact_contract_date(market: Market) -> date | None:
    """Compile exactly one explicit four-digit calendar date from contract identity.

    Administrative endDate is deliberately ignored: it need not equal the weather
    observation interval. Repeating the same date in title and question is allowed;
    two distinct dates, a missing year or an invalid calendar date are not.
    """
    text = f"{market.event_title or ''} {market.question or ''}"
    found: set[date] = set()
    for match in _DATE_RE.finditer(text):
        month = _MONTHS.get(match.group(1).lower())
        if month is None:
            return None
        try:
            found.add(date(int(match.group(3)), month, int(match.group(2))))
        except ValueError:
            return None
    return next(iter(found)) if len(found) == 1 else None


def _clean_url(url: str) -> str:
    return str(url or "").strip().rstrip(".,;")


def _urls(text: object) -> list[str]:
    out: list[str] = []
    for raw in _URL_RE.findall(str(text or "")):
        clean = _clean_url(raw)
        if clean and clean not in out:
            out.append(clean)
    return out


def _wrh_url(url: str) -> tuple[str, str] | None:
    """Return (canonical_url, station) only for an authoritative WRH URL."""
    clean = _clean_url(url)
    try:
        parsed = urlparse(clean)
    except Exception:
        return None
    host = (parsed.hostname or "").lower().rstrip(".")
    if not (host == "weather.gov" or host.endswith(".weather.gov")):
        return None
    if parsed.scheme.lower() not in {"https", "http"}:
        return None
    if parsed.path.rstrip("/").lower() != "/wrh/timeseries":
        return None
    query = parse_qs(parsed.query, keep_blank_values=True)
    sites = query.get("site") or query.get("SITE") or []
    if len(sites) != 1:
        return None
    station = str(sites[0]).strip().upper()
    if not re.fullmatch(r"[A-Z0-9]{4}", station):
        return None
    return clean, station


def _source_rejection(reason: str, *, url: str = "", station: str | None = None) -> dict:
    return {
        "verified": False,
        "kind": "unsupported/ambiguous",
        "url": url,
        "station": station,
        "reason": reason,
        "source_priority": "resolution_source_primary_only",
        "fallback_policy": "unmodeled",
    }


def strict_wrh_source(market: Market) -> dict:
    """Verify one dedicated WRH primary source and no unmodeled fallback source."""
    primary_urls = _urls(market.resolution_source)
    if len(primary_urls) != 1:
        return _source_rejection("resolution_source must contain exactly one primary URL")

    parsed_primary = _wrh_url(primary_urls[0])
    if parsed_primary is None:
        return _source_rejection("dedicated primary resolution source is not a supported NWS WRH URL")
    primary_url, station = parsed_primary

    description = str(market.description or "")
    description_urls = _urls(description)
    distinct_rule_urls = [url for url in description_urls if url != primary_url]
    if distinct_rule_urls:
        return _source_rejection(
            "rules contain a distinct secondary/fallback URL whose precedence is not modeled",
            url=primary_url,
            station=station,
        )
    if _FALLBACK_RE.search(description):
        return _source_rejection(
            "rules describe a fallback/secondary weather source whose policy is not modeled",
            url=primary_url,
            station=station,
        )

    return {
        "verified": True,
        "kind": "NOAA/NWS WRH primary-only v4 exact-date",
        "url": primary_url,
        "station": station,
        "reason": "dedicated WRH primary source with no unmodeled fallback source",
        "source_priority": "resolution_source_primary_only",
        "fallback_policy": "none_present",
    }


def settlement_safe_market(market: Market, *, now: datetime | None = None) -> Market | None:
    """Return a sanitized copy safe for the current WRH experiment, else None."""
    unit = contract_unit_from_question(market.question)
    source = strict_wrh_source(market)
    target_date = exact_contract_date(market)
    if unit is None or not source["verified"] or target_date is None:
        return None

    station = str(source.get("station") or "").upper()
    tz_name = STATION_TZ.get(station)
    if not tz_name:
        return None
    try:
        tz = ZoneInfo(tz_name)
    except Exception:
        return None

    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        return None
    local_now = current.astimezone(tz)
    if target_date != local_now.date():
        return None

    # Preserve economic/identity fields but narrow legacy rule inputs to the
    # certified primary. The question keeps the explicit bucket unit and date.
    safe = replace(
        market,
        description="",
        resolution_source=str(source["url"]),
        raw=dict(market.raw),
    )
    safe.raw["weather_contract_adapter"] = WEATHER_CONTRACT_ADAPTER
    safe.raw["weather_contract_unit"] = unit
    safe.raw["weather_contract_station"] = station
    safe.raw["weather_contract_target_date"] = target_date.isoformat()
    safe.raw["weather_contract_interval_kind"] = "local_calendar_day"
    safe.raw["weather_contract_timezone"] = tz_name
    safe.raw["weather_contract_source_priority"] = source["source_priority"]
    safe.raw["weather_contract_fallback_policy"] = source["fallback_policy"]
    return safe


def settlement_safe_weather_markets(markets: list[Market], *, now: datetime | None = None) -> list[Market]:
    """Fail closed on unsupported, ambiguous or mechanically unsafe contracts."""
    out: list[Market] = []
    for market in markets:
        safe = settlement_safe_market(market, now=now)
        if safe is not None:
            out.append(safe)
    return out


def _aware_utc(value: datetime) -> datetime | None:
    if not isinstance(value, datetime) or value.tzinfo is None:
        return None
    try:
        return value.astimezone(timezone.utc)
    except Exception:
        return None


def _pick_identical_duplicate(rows: list[Observation]) -> Observation:
    """Choose a stable representative when same-time duplicates agree on Temp."""
    # Prefer the most recently received lineage when available. This does not confer
    # settlement authority; it merely preserves the latest audit metadata for an
    # otherwise identical proxy temperature.
    def receipt_key(row: Observation) -> float:
        received = _aware_utc(getattr(row, "receipt_time", None))
        return received.timestamp() if received is not None else float("-inf")

    return max(rows, key=receipt_key)


def settlement_safe_weather_cache(
    weather_cache: dict[str, list],
    *,
    now: datetime | None = None,
) -> dict[str, ObservationBatch]:
    """Normalize only causal, exact-lineage AWC proxy observations.

    Prospective V4 evidence accepts no anonymous/legacy Observation rows. Duplicate
    rows at one meteorological timestamp are harmless only when they agree exactly on
    temperature; conflicting values are excluded because choosing one would invent a
    correction/precedence rule that is not certified against WRH settlement history.
    """
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        return {}
    current_utc = current.astimezone(timezone.utc)
    future_cutoff = current_utc + timedelta(seconds=MAX_CLOCK_SKEW_SECONDS)

    out: dict[str, ObservationBatch] = {}
    for station_raw, rows in weather_cache.items():
        station = str(station_raw or "").strip().upper()
        if not station or not isinstance(rows, list):
            continue

        grouped: dict[datetime, list[Observation]] = {}
        for row in rows:
            if not isinstance(row, Observation):
                continue
            if str(getattr(row, "station_id", "") or "").strip().upper() != station:
                continue
            if str(getattr(row, "source_adapter", "") or "") != AWC_OBSERVATION_ADAPTER:
                continue
            when = _aware_utc(row.when)
            if when is None or when > future_cutoff:
                continue
            if not isinstance(row.temp_c, (int, float)):
                continue
            temp = float(row.temp_c)
            if not math.isfinite(temp):
                continue
            grouped.setdefault(when, []).append(row)

        valid_rows: list[Observation] = []
        for when in sorted(grouped):
            candidates = grouped[when]
            temperatures = {float(row.temp_c) for row in candidates}
            if len(temperatures) != 1:
                # Same event time with conflicting temperatures may represent a
                # correction or feed inconsistency; without WRH parity we choose none.
                continue
            valid_rows.append(_pick_identical_duplicate(candidates))

        forecast = getattr(rows, "forecast", None)
        if forecast is not None:
            fetched = _aware_utc(getattr(forecast, "fetched_at", None))
            station_match = str(getattr(forecast, "station", "") or "").strip().upper() == station
            if fetched is None or not station_match:
                forecast = None
            else:
                age = (current_utc - fetched).total_seconds()
                if age < -MAX_CLOCK_SKEW_SECONDS or age > FORECAST_REFRESH_SECONDS:
                    forecast = None

        out[station] = ObservationBatch(valid_rows, forecast=forecast)
    return out
