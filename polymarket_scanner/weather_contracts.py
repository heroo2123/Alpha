from __future__ import annotations

"""Fail-closed settlement contract boundary for weather research.

This adapter is intentionally narrow. Only an explicit bucket unit in the market
question plus a dedicated NWS WRH time-series *primary* resolution source is admitted
to the current weather experiment. Wunderground, HKO, Dyacon and other legitimate
families stay silent until they have separate versioned adapters.

V3 makes source precedence explicit. A WRH-looking URL found only in free-form rules
text is never allowed to become the primary source. Any distinct/fallback source in
the rules makes this primary-only adapter unsupported until its fallback policy is
implemented. This deliberately sacrifices coverage rather than silently changing the
market's settlement authority.

The temporal boundary remains fail-closed: the market date must parse to the
station's current local date, future observations cannot enter the evidence set, and
attached forecast context must itself be fresh and causally available.
"""

import re
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlparse
from zoneinfo import ZoneInfo

from .models import Market
from .weather import (
    FORECAST_REFRESH_SECONDS,
    Observation,
    ObservationBatch,
    STATION_TZ,
    market_observation_date,
)

_URL_RE = re.compile(r"https?://[^\s<>\"')]+", re.I)
_FALLBACK_RE = re.compile(
    r"\b(?:fallback|fall\s+back|alternate(?:ly)?|secondary\s+source|"
    r"if\s+(?:the\s+)?(?:primary\s+)?source\s+(?:is\s+)?(?:unavailable|missing|down)|"
    r"wunderground|weather\s+underground|hong\s+kong\s+observatory|dyacon)\b",
    re.I,
)
WEATHER_CONTRACT_ADAPTER = "NWS_WRH_PRIMARY_ONLY_V3"
WEATHER_LATE_MODEL_VERSION = "uncalibrated_v3_contract_safe"
WEATHER_FRIEND_MODEL_VERSION = "friend_uncalibrated_v3_contract_safe"
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
    """Verify one dedicated WRH primary source and no unmodeled fallback source.

    ``resolution_source`` is the only field allowed to establish primary authority.
    Rules/description text may repeat that same URL, but cannot introduce another
    source or fallback policy. A WRH URL found only in description therefore remains
    unsupported rather than being silently promoted from fallback/reference text.
    """
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
        "kind": "NOAA/NWS WRH primary-only v3",
        "url": primary_url,
        "station": station,
        "reason": "dedicated WRH primary source with no unmodeled fallback source",
        "source_priority": "resolution_source_primary_only",
        "fallback_policy": "none_present",
    }


def settlement_safe_market(market: Market, *, now: datetime | None = None) -> Market | None:
    """Return a sanitized copy safe for the current WRH experiment, else None.

    The legacy weather model reads question+description to infer units and performs
    a substring source check. Supplying only the explicit contract question and the
    already-validated authoritative URL prevents those demonstrated P0 errors. V3
    additionally requires explicit primary-source authority and rejects unmodeled
    fallback/source-priority rules.
    """
    unit = contract_unit_from_question(market.question)
    source = strict_wrh_source(market)
    if unit is None or not source["verified"]:
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
    target_date = market_observation_date(market, local_now)
    if target_date is None or target_date != local_now.date():
        return None

    # Preserve every economic/identity field, but narrow rule inputs consumed by
    # the legacy model to the certified source. The question retains the explicit
    # bucket unit; the description cannot inject a different display unit/source.
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


def settlement_safe_weather_cache(
    weather_cache: dict[str, list],
    *,
    now: datetime | None = None,
) -> dict[str, ObservationBatch]:
    """Remove non-causal observations and invalidate stale/future forecast context.

    Future observations are never converted into apparent freshness. A forecast is
    attached only when its own fetch timestamp is timezone-aware, not future-dated,
    no older than the production forecast TTL, and belongs to the same station.
    The legacy lock model will then fail closed if too few valid observations remain
    or if no valid forecast context is attached.
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

        valid_rows: list[Observation] = []
        for row in rows:
            if not isinstance(row, Observation):
                continue
            when = _aware_utc(row.when)
            if when is None or when > future_cutoff:
                continue
            if not isinstance(row.temp_c, (int, float)):
                continue
            valid_rows.append(row)
        valid_rows.sort(key=lambda row: row.when)

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
