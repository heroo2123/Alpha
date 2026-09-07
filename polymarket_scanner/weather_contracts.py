from __future__ import annotations

"""Fail-closed settlement contract boundary for weather research.

This is intentionally narrow. During the P0 repair phase only an explicit bucket
unit in the market question plus an authoritative NWS WRH time-series URL is
admitted to the existing weather experiment. Other legitimate source families
(Wunderground, HKO, etc.) stay silent until they have their own versioned adapters.

V2 additionally makes the temporal boundary explicit: the market date must parse to
the station's current local date, future observations cannot enter the evidence set,
and attached forecast context must itself be fresh and causally available.
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
WEATHER_CONTRACT_ADAPTER = "NWS_WRH_STRICT_V2"
WEATHER_LATE_MODEL_VERSION = "uncalibrated_v2_contract_safe"
WEATHER_FRIEND_MODEL_VERSION = "friend_uncalibrated_v2_contract_safe"
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


def _wrh_url(url: str) -> tuple[str, str] | None:
    """Return (canonical_url, station) only for an authoritative WRH URL."""
    try:
        parsed = urlparse(str(url).rstrip(".,;"))
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
    return str(url).rstrip(".,;"), station


def strict_wrh_source(market: Market) -> dict:
    """Find an exact authoritative WRH source and station, never by substring."""
    # Prefer the dedicated resolution_source field, then inspect rules text. This
    # still does not prove primary/fallback precedence; ambiguous multiple station
    # URLs therefore fail closed below.
    candidates: list[tuple[str, str]] = []
    for text in (market.resolution_source or "", market.description or ""):
        for url in _URL_RE.findall(text):
            parsed = _wrh_url(url)
            if parsed and parsed not in candidates:
                candidates.append(parsed)
    stations = {station for _, station in candidates}
    if len(candidates) != 1 or len(stations) != 1:
        return {
            "verified": False,
            "kind": "unsupported/ambiguous",
            "url": "",
            "station": None,
        }
    url, station = candidates[0]
    return {
        "verified": True,
        "kind": "NOAA/NWS WRH strict_v1",
        "url": url,
        "station": station,
    }


def settlement_safe_market(market: Market, *, now: datetime | None = None) -> Market | None:
    """Return a sanitized copy safe for the current WRH experiment, else None.

    The legacy weather model reads question+description to infer units and performs
    a substring source check. Supplying only the explicit contract question and the
    already-validated authoritative URL prevents those demonstrated P0 errors. V2
    also refuses missing/ambiguous market dates and unknown station timezones.
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
    # bucket unit; the description cannot inject a different display unit.
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
