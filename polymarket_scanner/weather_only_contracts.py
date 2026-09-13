from __future__ import annotations

"""Typed, fail-closed compiler for the weather-only scanner foundation.

The compiler inventories recurring weather contract families and bucket structure.
It never grants financial authority.  V4 tightens the proposition boundary so
unknown comparison grammar, conflicting target dates, fake source URLs, conflicting
stations and non-integral whole-degree bucket lattices fail closed rather than being
silently reinterpreted.
"""

import json
import re
from dataclasses import asdict, dataclass
from datetime import date
from urllib.parse import parse_qs, urlparse


WEATHER_ONLY_COMPILER_VERSION = "weather_only_contract_compiler_v4_strict_semantics_fail_closed"

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
_YEARLESS_MONTH_FIRST_DATE = re.compile(
    rf"\b({_MONTH})[a-z]*\s+(\d{{1,2}})(?:st|nd|rd|th)?\b(?!\s*,?\s*20\d{{2}})",
    re.I,
)
_YEARLESS_DAY_FIRST_DATE = re.compile(
    rf"\b(\d{{1,2}})(?:st|nd|rd|th)?\s+({_MONTH})[a-z]*\b(?!\s+(?:'\d{{2}}|20\d{{2}}))",
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
    parts = [event.get("title"), event.get("description"), event.get("resolutionSource")]
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


def _target_identity_texts(event: dict) -> tuple[str, ...]:
    """Texts whose month/day language describes the market proposition itself.

    Administrative/fallback deadline prose is intentionally excluded; a title or
    child question mismatch is a proposition conflict, while a later fallback date
    in rule prose is not the target weather date.
    """
    values = [str(event.get("title") or "")]
    values.extend(str(row.get("question") or "") for row in _market_rows(event))
    return tuple(values)


def exact_weather_date(event: dict) -> date | None:
    """Resolve one explicit target date and verify yearless title/question dates.

    One full date in authoritative event/rule text supplies the year.  Any month/day
    asserted by the event title or child questions must agree with that date.  This
    prevents a title saying September 12 from silently compiling rules for September
    13 while avoiding fallback/deadline dates that appear only in prose.
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
    if len(found) != 1:
        return None
    resolved = next(iter(found))

    asserted_month_days: set[tuple[int, int]] = set()
    for identity_text in _target_identity_texts(event):
        for match in _YEARLESS_MONTH_FIRST_DATE.finditer(identity_text):
            month = _MONTHS.get(match.group(1).lower())
            if month is None:
                return None
            asserted_month_days.add((month, int(match.group(2))))
        for match in _YEARLESS_DAY_FIRST_DATE.finditer(identity_text):
            month = _MONTHS.get(match.group(2).lower())
            if month is None:
                return None
            asserted_month_days.add((month, int(match.group(1))))
    if asserted_month_days and asserted_month_days != {(resolved.month, resolved.day)}:
        return None
    return resolved


def _unit_tokens(text: str) -> set[str]:
    units: set[str] = set()
    if re.search(r"(?:°\s*F\b|\bFahrenheit\b|\bdegrees?\s+F\b)", text, re.I):
        units.add("F")
    if re.search(r"(?:°\s*C\b|\bCelsius\b|\bdegrees?\s+C\b)", text, re.I):
        units.add("C")
    return units


def _declared_resolution_units(event: dict) -> set[str]:
    parts = [event.get("description")]
    parts.extend(row.get("description") for row in _market_rows(event))
    text = " ".join(str(value or "") for value in parts)
    units: set[str] = set()
    if re.search(r"\bdegrees?\s+(?:F(?:ahrenheit)?\b|fahrenheit\b)", text, re.I):
        units.add("F")
    if re.search(r"\bdegrees?\s+(?:C(?:elsius)?\b|celsius\b)", text, re.I):
        units.add("C")
    return units


def exact_weather_unit(event: dict) -> str | None:
    question_units: set[str] = set()
    for row in _market_rows(event):
        found = _unit_tokens(str(row.get("question") or ""))
        if len(found) > 1:
            return None
        question_units.update(found)
    if len(question_units) > 1:
        return None
    declared = _declared_resolution_units(event)
    if len(declared) > 1:
        return None
    if question_units and declared and question_units != declared:
        return None
    combined = question_units or declared
    return next(iter(combined)) if len(combined) == 1 else None


def source_urls(event: dict) -> tuple[str, ...]:
    out: list[str] = []
    for raw in _URL_RE.findall(_event_text(event)):
        clean = raw.strip().rstrip(".,;")
        if clean and clean not in out:
            out.append(clean)
    return tuple(out)


def _host_is(hostname: str | None, *allowed: str) -> bool:
    host = str(hostname or "").strip().lower().rstrip(".")
    return host in {value.lower() for value in allowed}


def _nws_wrh_url(raw: str) -> bool:
    try:
        parsed = urlparse(raw)
    except Exception:
        return False
    return (
        parsed.scheme.lower() == "https"
        and _host_is(parsed.hostname, "weather.gov", "www.weather.gov")
        and parsed.path.rstrip("/").lower() == "/wrh/timeseries"
    )


def source_family(urls: tuple[str, ...], text: str = "") -> str:
    parsed_urls = []
    for raw in urls:
        try:
            parsed_urls.append(urlparse(raw))
        except Exception:
            continue
    if any(_nws_wrh_url(raw) for raw in urls):
        return SOURCE_NWS_WRH
    if any(_host_is(p.hostname, "weather.gov.hk", "www.weather.gov.hk") for p in parsed_urls) or "hong kong observatory" in text.lower():
        return SOURCE_HKO
    if any(_host_is(p.hostname, "wunderground.com", "www.wunderground.com") for p in parsed_urls) or "weather underground" in text.lower():
        return SOURCE_WUNDERGROUND
    if any(_host_is(p.hostname, "metoffice.gov.uk", "www.metoffice.gov.uk") for p in parsed_urls) or "met office" in text.lower():
        return SOURCE_MET_OFFICE
    if any(_host_is(p.hostname, "weather.gc.ca", "www.weather.gc.ca") for p in parsed_urls) or "environment and climate change canada" in text.lower():
        return SOURCE_ENV_CANADA
    if any(_host_is(p.hostname, "bom.gov.au", "www.bom.gov.au") for p in parsed_urls) or "bureau of meteorology" in text.lower():
        return SOURCE_BOM
    if any(_host_is(p.hostname, "jma.go.jp", "www.jma.go.jp") for p in parsed_urls) or "japan meteorological agency" in text.lower():
        return SOURCE_JMA
    if any(_host_is(p.hostname, "cwa.gov.tw", "www.cwa.gov.tw") for p in parsed_urls) or "central weather administration" in text.lower():
        return SOURCE_CWA_TAIWAN
    return SOURCE_OTHER


def source_station(urls: tuple[str, ...], text: str = "") -> str | None:
    stations: set[str] = set()
    for raw in urls:
        if not _nws_wrh_url(raw):
            continue
        try:
            parsed = urlparse(raw)
            query = parse_qs(parsed.query)
        except Exception:
            continue
        site_values = [value for key, values in query.items() if key.lower() == "site" for value in values]
        if len(site_values) != 1:
            return None
        station = str(site_values[0]).strip().upper()
        if not re.fullmatch(r"[A-Z0-9]{4}", station):
            return None
        stations.add(station)
    if stations:
        return next(iter(stations)) if len(stations) == 1 else None
    # Non-WRH inventory-only hint.  Never sufficient for NWS rule authority.
    candidates = set(re.findall(r"\b[A-Z]{4}\b", text))
    return next(iter(candidates)) if len(candidates) == 1 else None


def _strict_integer(value: str) -> float | None:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    return numeric if numeric.is_integer() else None


def _bucket_bounds(question: str, unit: str | None) -> tuple[float | None, float | None] | None:
    """Parse a supported bucket proposition without silently weakening grammar.

    Inclusive tails/ranges/exact values are supported.  Strict less/greater is
    translated only on an integer lattice.  Negation and unknown comparison syntax
    fail closed.  A generic single-number fallback is permitted only when no
    comparison/negation words remain in the question.
    """
    q = str(question or "").replace("–", "-").replace("—", "-")
    if not unit:
        return None
    unit_re = re.escape(unit)
    number = r"(-?\d+(?:\.\d+)?)"

    if re.search(r"\b(?:not|except|excluding)\b", q, re.I):
        return None

    strict_less = re.search(rf"\b(?:less\s+than|below)\s+{number}\s*°?\s*{unit_re}\b", q, re.I)
    if strict_less and "or below" not in q.lower():
        value = _strict_integer(strict_less.group(1))
        return (None, value - 1.0) if value is not None else None
    strict_greater = re.search(rf"\b(?:greater\s+than|above)\s+{number}\s*°?\s*{unit_re}\b", q, re.I)
    if strict_greater and "or above" not in q.lower():
        value = _strict_integer(strict_greater.group(1))
        return (value + 1.0, None) if value is not None else None

    m = re.search(rf"{number}\s*-\s*{number}\s*°?\s*{unit_re}\b", q, re.I)
    if m:
        lo, hi = float(m.group(1)), float(m.group(2))
        return (lo, hi) if lo <= hi else None

    m = re.search(rf"{number}\s*°?\s*{unit_re}\b\s*(?:or\s*)?(?:higher|above|more)", q, re.I)
    if m:
        return float(m.group(1)), None

    m = re.search(rf"{number}\s*°?\s*{unit_re}\b\s*(?:or\s*)?(?:lower|below|less)", q, re.I)
    if m:
        return None, float(m.group(1))

    if re.search(r"\b(?:less|greater|below|above|under|over|higher|lower|more)\b", q, re.I):
        return None
    matches = re.findall(rf"{number}\s*°?\s*{unit_re}\b", q, re.I)
    if len(matches) == 1:
        value = float(matches[0])
        return value, value
    return None


def _token_for(outcomes: list[str], token_ids: list[str], wanted: str) -> str | None:
    if len(outcomes) != 2 or len(token_ids) != 2:
        return None
    normalized = [str(value).strip().lower() for value in outcomes]
    if sorted(normalized) != ["no", "yes"]:
        return None
    target = wanted.lower()
    index = normalized.index(target)
    token = str(token_ids[index]).strip()
    return token or None


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
    parsed = [bucket for bucket in buckets if bucket.lower is not None or bucket.upper is not None]
    if len(parsed) != len(buckets) or len(parsed) < 2:
        return False
    if sum(bucket.lower is None for bucket in parsed) != 1:
        return False
    if sum(bucket.upper is None for bucket in parsed) != 1:
        return False
    # Current live NWS profiles settle/display whole-degree buckets.  Fractional
    # endpoints are not a proved integer partition and must be certified elsewhere.
    for bucket in parsed:
        for bound in (bucket.lower, bucket.upper):
            if bound is not None and not float(bound).is_integer():
                return False
    ordered = sorted(parsed, key=lambda bucket: float("-inf") if bucket.lower is None else bucket.lower)
    if ordered[0].lower is not None or ordered[-1].upper is not None:
        return False
    for left, right in zip(ordered, ordered[1:]):
        if left.upper is None or right.lower is None:
            return False
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
        reasons.append("TARGET_DATE_UNRESOLVED_OR_CONFLICT")
    if unit is None:
        reasons.append("UNIT_UNRESOLVED_OR_CONFLICT")
    if source == SOURCE_OTHER:
        reasons.append("SOURCE_UNRECOGNIZED")
    if source == SOURCE_NWS_WRH and station is None:
        reasons.append("NWS_STATION_UNRESOLVED_OR_CONFLICT")
    if not open_binary:
        reasons.append("NO_OPEN_BINARY_BUCKETS")
    if bucket_tuple and any(bucket.lower is None and bucket.upper is None for bucket in bucket_tuple):
        reasons.append("BUCKET_PARSE_INCOMPLETE")
    if family in {DAILY_HIGH, DAILY_LOW} and not shape:
        reasons.append("BUCKET_PARTITION_SHAPE_UNPROVEN")
    market_ids = [bucket.market_id for bucket in bucket_tuple]
    condition_ids = [bucket.condition_id for bucket in bucket_tuple]
    tokens = [token for bucket in bucket_tuple for token in (bucket.yes_token, bucket.no_token) if token]
    if len(set(market_ids)) != len(market_ids) or len(set(condition_ids)) != len(condition_ids):
        reasons.append("DUPLICATE_MARKET_OR_CONDITION_ID")
    if len(set(tokens)) != len(tokens):
        reasons.append("DUPLICATE_TOKEN_ID")

    shadow_supported = bool(
        family in {DAILY_HIGH, DAILY_LOW}
        and target is not None
        and unit is not None
        and source != SOURCE_OTHER
        and (source != SOURCE_NWS_WRH or station is not None)
        and open_binary
        and not any(reason.startswith("DUPLICATE_") for reason in reasons)
    )

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
