from __future__ import annotations

"""Fail-closed settlement contract boundary for weather research.

This is intentionally narrow. During the P0 repair phase only an explicit bucket
unit in the market question plus an authoritative NWS WRH time-series URL is
admitted to the existing weather experiment. Other legitimate source families
(Wunderground, HKO, etc.) stay silent until they have their own versioned adapters.
"""

import re
from dataclasses import replace
from urllib.parse import parse_qs, urlparse

from .models import Market

_URL_RE = re.compile(r"https?://[^\s<>\"')]+", re.I)


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


def settlement_safe_market(market: Market) -> Market | None:
    """Return a sanitized copy safe for the current WRH experiment, else None.

    The legacy weather model reads question+description to infer units and performs
    a substring source check. Supplying only the explicit contract question and the
    already-validated authoritative URL prevents those two demonstrated P0 errors
    from contaminating new prospective weather research while the full versioned
    settlement adapters are being built.
    """
    unit = contract_unit_from_question(market.question)
    source = strict_wrh_source(market)
    if unit is None or not source["verified"]:
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
    safe.raw["weather_contract_adapter"] = "NWS_WRH_STRICT_V1"
    safe.raw["weather_contract_unit"] = unit
    safe.raw["weather_contract_station"] = source["station"]
    return safe


def settlement_safe_weather_markets(markets: list[Market]) -> list[Market]:
    """Fail closed on unsupported, ambiguous or mechanically unsafe contracts."""
    out: list[Market] = []
    for market in markets:
        safe = settlement_safe_market(market)
        if safe is not None:
            out.append(safe)
    return out
