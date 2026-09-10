from __future__ import annotations

"""Typed, fail-closed compiler for the weather-only scanner foundation.

This module is deliberately broader than the legacy WRH-only action adapter but
weaker in authority: it inventories recurring weather contract families and their
bucket structure without granting financial permission.  Source-specific finality,
rounding and fallback adapters must upgrade ``exactly_one_outcome_proven`` and
financial authority later; discovery/title similarity never does so by itself.
"""

import json
import re
from dataclasses import asdict, dataclass
from datetime import date
from urllib.parse import parse_qs, urlparse


WEATHER_ONLY_COMPILER_VERSION = "weather_only_contract_compiler_v1_inventory_no_financial_authority"

DAILY_HIGH = "daily_high_temperature"
DAILY_LOW = "daily_low_temperature"
PRECIPITATION = "precipitation"
OTHER_WEATHER = "other_weather"

SOURCE_NWS_WRH = "NWS_WRH_TIMESERIES"
SOURCE_HKO = "HONG_KONG_OBSERVATORY"
SOURCE_WUNDERGROUND = "WEATHER_UNDERGROUND"
SOURCE_MET_OFFICE = "UK_MET_OFFICE"
SOURCE_ENV_CANADA = "ENVIRONMENT_CANADA"
SOURCE_BOM = "AUSTRALIAN_BOM"
SOURCE_JMA = "JAPAN_METEOROLOGICAL_AGENCY"
SOURCE_CWA_TAIWAN = "TAIWAN_CWA"
SOURCE_OTHER = "UNRECOGNIZED"

_URL_RE = re.compile(r"https?://[^\s<>\"')]+", re.I)
_MONTHS = {
    "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6,
    "july": 7, "august": 8, "september": 9, "october": 10, "november": 11, "december": 12,
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "jun": 6, "jul": 7, "aug": 8,
    "sep": 9, "sept": 9, "oct": 10, "nov": 11, "dec": 12,
}
_MONTH = "(?:January|February|March|April|May|June|July|August|September|October|November|December|Jan|Feb|Mar|Apr|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)"
_MONTH_FIRST_DATE = re.compile(
    rf"\b({_MONTH})[a-z]*\s+(\d{{1,2}})(?:st|nd|rd|th)?(?:,)?\s+(20\d{{2}})\b",
    re.I,
)
_DAY_FIRST_DATE = re.compile(
    rf"\b(\d{{1,2}})(?:st|nd|rd|th)?\s+({_MONTH})[a-z]*\s+(?:'(\d{{2}})|(20\d{{2}}))\b",
    re.I,
)


def _flag(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return False


def _json_list(value: object) -> list:
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except Exception:
            return []
        return parsed if isinstance(parsed, list) else []
    return []


def _market_rows(event: dict) -> list[dict]:
    return [row for row in (event.get("markets") or []) if isinstance(row, dict)]


def _event_text(event: dict) -> str:
    parts = [
        event.get("title"), event.get("description"), event.get("resolutionSource"),
    ]
    for row in _market_rows(event):
        parts.extend((row.get("question"), row.get("description"), row.get("resolutionSource")))
    return " ".join(str(x or "") for x in parts)


def weather_family(event: dict) -> str:
    title = str(event.get("title") or "")
    combined = _event_text(event)
    if re.search(r"\bhighest\s+temperature\b", title, re.I):
        return DAILY_HIGH
    if re.search(r"\blowest\s+temperature\b", title, re.I):
        return DAILY_LOW
    if re.search(r"\b(?:precipitation|rainfall|rain|snowfall)\b", combined, re.I):
        return PRECIPITATION
    return OTHER_WEATHER


def _date_from_parts(year: int, month_name: str, day: int) -> date | None:
    month = _MONTHS.get(month_name.lower())
    if month is None:
        return None
    try:
        return date(year, month, day)
    except ValueError:
        return None


def exact_weather_date(event: dict) -> date | None:
    """Resolve one explicit contract date without borrowing administrative endDate.

    Current recurring market copy may put the year in rules text as ``11 Sep '26``
    even when the event title says only ``September 11``.  Both four-digit and
    explicit two-digit 20xx forms are accepted.  Conflicting dates fail closed.
    """
    found: set[date] = set()
    text = _event_text(event)
    for match in _MONTH_FIRST_DATE.finditer(text):
        resolved = _date_from_parts(int(match.group(3)), match.group(1), int(match.group(2)))
        if resolved is None:
            return None
        found.add(resolved)
    for match in _DAY_FIRST_DATE.finditer(text):
        year = int(match.group(4)) if match.group(4) else 2000 + int(match.group(3))
        resolved = _date_from_parts(year, match.group(2), int(match.group(1)))
        if resolved is None:
            return None
        found.add(resolved)
    return next(iter(found)) if len(found) == 1 else None


def _unit_tokens(text: str) -> set[str]:
    units: set[str] = set()
    if re.search(r"(?:°\s*F\b|\bFahrenheit\b|\bdegrees?\s+F\b)", text, re.I):
        units.add("F")
    if re.search(r"(?:°\s*C\b|\bCelsius\b|\bdegrees?\s+C\b)", text, re.I):
        units.add("C")
    return units


def exact_weather_unit(event: dict) -> str | None:
    found = _unit_tokens(_event_text(event))
    return next(iter(found)) if len(found) == 1 else None


def source_urls(event: dict) -> tuple[str, ...]:
    out: list[str] = []
    for raw in _URL_RE.findall(_event_text(event)):
        clean = raw.strip().rstrip(".,;")
        if clean and clean not in out:
            out.append(clean)
    return tuple(out)


def source_family(urls: tuple[str, ...], text: str = "") -> str:
    lowered = " ".join(urls).lower() + " " + text.lower()
    if "weather.gov/wrh/timeseries" in lowered:
        return SOURCE_NWS_WRH
    if "weather.gov.hk" in lowered or "hong kong observatory" in lowered:
        return SOURCE_HKO
    if "wunderground.com" in lowered or "weather underground" in lowered:
        return SOURCE_WUNDERGROUND
    if "metoffice.gov.uk" in lowered or "met office" in lowered:
        return SOURCE_MET_OFFICE
    if "weather.gc.ca" in lowered or "environment and climate change canada" in lowered:
        return SOURCE_ENV_CANADA
    if "bom.gov.au" in lowered or "bureau of meteorology" in lowered:
        return SOURCE_BOM
    if "jma.go.jp" in lowered or "japan meteorological agency" in lowered:
        return SOURCE_JMA
    if "cwa.gov.tw" in lowered or "central weather administration" in lowered:
        return SOURCE_CWA_TAIWAN
    return SOURCE_OTHER


def source_station(urls: tuple[str, ...], text: str = "") -> str | None:
    for raw in urls:
        try:
            parsed = urlparse(raw)
            query = parse_qs(parsed.query)
        except Exception:
            continue
        for key, values in query.items():
            if key.lower() == "site" and len(values) == 1:
                station = str(values[0]).strip().upper()
                if re.fullmatch(r"[A-Z0-9]{4}", station):
                    return station
    # Inventory-only hint.  This is never enough for financial source authority.
    candidates = re.findall(r"\b[A-Z]{4}\b", text)
    return candidates[0] if len(set(candidates)) == 1 else None


def _bucket_bounds(question: str, unit: str | None) -> tuple[float | None, float | None] | None:
    q = str(question or "").replace("–", "-").replace("—", "-")
    if not unit:
        return None
    unit_re = re.escape(unit)

    m = re.search(
        rf"(-?\d+(?:\.\d+)?)\s*-\s*(-?\d+(?:\.\d+)?)\s*°?\s*{unit_re}\b",
        q,
        re.I,
    )
    if m:
        lo, hi = float(m.group(1)), float(m.group(2))
        return (lo, hi) if lo <= hi else None

    m = re.search(
        rf"(-?\d+(?:\.\d+)?)\s*°?\s*{unit_re}\b\s*(?:or\s*)?(?:higher|above|more)",
        q,
        re.I,
    )
    if m:
        return float(m.group(1)), None

    m = re.search(
        rf"(-?\d+(?:\.\d+)?)\s*°?\s*{unit_re}\b\s*(?:or\s*)?(?:lower|below|less)",
        q,
        re.I,
    )
    if m:
        return None, float(m.group(1))

    matches = re.findall(rf"(-?\d+(?:\.\d+)?)\s*°?\s*{unit_re}\b", q, re.I)
    if len(matches) == 1:
        value = float(matches[0])
        return value, value
    return None


def _token_for(outcomes: list[str], token_ids: list[str], wanted: str) -> str | None:
    target = wanted.lower()
    for index, outcome in enumerate(outcomes):
        if str(outcome).strip().lower() == target and index < len(token_ids):
            token = str(token_ids[index]).strip()
            return token or None
    return None


@dataclass(frozen=True, slots=True)
class WeatherBucket:
    market_id: str
    condition_id: str
    question: str
    slug: str
    lower: float | None
    upper: float | None
    unit: str | None
    yes_token: str | None
    no_token: str | None
    trade_open: bool

    def as_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class CompiledWeatherEvent:
    compiler_version: str
    event_id: str
    event_slug: str
    title: str
    family: str
    target_date: date | None
    unit: str | None
    source_family: str
    source_urls: tuple[str, ...]
    station_hint: str | None
    buckets: tuple[WeatherBucket, ...]
    partition_shape_complete: bool
    exactly_one_outcome_proven: bool
    shadow_supported: bool
    financial_authority: bool
    rejection_reasons: tuple[str, ...]

    def as_dict(self) -> dict:
        value = asdict(self)
        value["target_date"] = self.target_date.isoformat() if self.target_date else None
        return value


def _partition_shape_complete(buckets: tuple[WeatherBucket, ...]) -> bool:
    """Prove only the displayed integer bucket shape, not settlement semantics."""
    parsed = [bucket for bucket in buckets if bucket.lower is not None or bucket.upper is not None]
    if len(parsed) != len(buckets) or len(parsed) < 2:
        return False
    if sum(bucket.lower is None for bucket in parsed) != 1:
        return False
    if sum(bucket.upper is None for bucket in parsed) != 1:
        return False

    ordered = sorted(parsed, key=lambda bucket: float("-inf") if bucket.lower is None else bucket.lower)
    if ordered[0].lower is not None or ordered[-1].upper is not None:
        return False
    for left, right in zip(ordered, ordered[1:]):
        if left.upper is None or right.lower is None:
            return False
        # Current recurring temperature bucket labels partition whole-degree values.
        # Rule/source adapters must separately prove how raw source precision maps to
        # this lattice before any exactly-one financial claim is allowed.
        if abs((left.upper + 1.0) - right.lower) > 1e-9:
            return False
    return True


def compile_weather_event(event: dict) -> CompiledWeatherEvent:
    event_id = str(event.get("id") or "").strip()
    title = str(event.get("title") or "").strip()
    family = weather_family(event)
    target = exact_weather_date(event)
    unit = exact_weather_unit(event)
    urls = source_urls(event)
    source = source_family(urls, _event_text(event))
    station = source_station(urls, _event_text(event))

    buckets: list[WeatherBucket] = []
    for row in _market_rows(event):
        outcomes = [str(x) for x in _json_list(row.get("outcomes"))]
        token_ids = [str(x) for x in _json_list(row.get("clobTokenIds"))]
        yes = _token_for(outcomes, token_ids, "yes")
        no = _token_for(outcomes, token_ids, "no")
        bounds = _bucket_bounds(str(row.get("question") or ""), unit)
        lower, upper = bounds if bounds is not None else (None, None)
        trade_open = bool(
            _flag(row.get("active", True))
            and not _flag(row.get("closed", False))
            and _flag(row.get("acceptingOrders"))
            and _flag(row.get("enableOrderBook"))
            and yes
            and no
            and str(row.get("conditionId") or "").strip()
        )
        buckets.append(WeatherBucket(
            market_id=str(row.get("id") or "").strip(),
            condition_id=str(row.get("conditionId") or "").strip(),
            question=str(row.get("question") or ""),
            slug=str(row.get("slug") or ""),
            lower=lower,
            upper=upper,
            unit=unit,
            yes_token=yes,
            no_token=no,
            trade_open=trade_open,
        ))

    bucket_tuple = tuple(buckets)
    shape = _partition_shape_complete(bucket_tuple)
    open_binary = [bucket for bucket in bucket_tuple if bucket.trade_open]
    reasons: list[str] = []
    if family not in {DAILY_HIGH, DAILY_LOW}:
        reasons.append("UNSUPPORTED_FAMILY_PHASE1")
    if target is None:
        reasons.append("TARGET_DATE_UNRESOLVED")
    if unit is None:
        reasons.append("UNIT_UNRESOLVED_OR_CONFLICT")
    if source == SOURCE_OTHER:
        reasons.append("SOURCE_UNRECOGNIZED")
    if not open_binary:
        reasons.append("NO_OPEN_BINARY_BUCKETS")
    if bucket_tuple and any(bucket.lower is None and bucket.upper is None for bucket in bucket_tuple):
        reasons.append("BUCKET_PARSE_INCOMPLETE")
    if family in {DAILY_HIGH, DAILY_LOW} and not shape:
        reasons.append("BUCKET_PARTITION_SHAPE_UNPROVEN")

    shadow_supported = bool(
        family in {DAILY_HIGH, DAILY_LOW}
        and target is not None
        and unit is not None
        and source != SOURCE_OTHER
        and open_binary
    )

    # Foundation invariant: classification never grants financial authority.
    return CompiledWeatherEvent(
        compiler_version=WEATHER_ONLY_COMPILER_VERSION,
        event_id=event_id,
        event_slug=str(event.get("slug") or ""),
        title=title,
        family=family,
        target_date=target,
        unit=unit,
        source_family=source,
        source_urls=urls,
        station_hint=station,
        buckets=bucket_tuple,
        partition_shape_complete=shape,
        exactly_one_outcome_proven=False,
        shadow_supported=shadow_supported,
        financial_authority=False,
        rejection_reasons=tuple(dict.fromkeys(reasons)),
    )
