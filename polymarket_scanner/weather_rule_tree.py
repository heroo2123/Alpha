from __future__ import annotations

"""Versioned rule-tree parsers for weather-only contracts.

The general scanner's historical weather adapter intentionally accepted only a
primary NWS/WRH source with no fallback. Current Polymarket weather rules are more
complicated: daily-temperature contracts can name NWS/WRH as primary and Weather
Underground as a conditional fallback, while daily-rain contracts use a specific
NWS Daily Climate Report (CLI) version/cutoff rule.

This module does **not** fetch settlement data and does not produce trades. It only
turns rule text into a closed, auditable contract specification. Anything that
cannot be parsed without inventing precedence, time or source semantics fails
closed.
"""

import re
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from urllib.parse import parse_qs, urlparse

from .models import Market

DAILY_TEMP_ADAPTER = "DAILY_TEMP_NWS_WRH_WITH_WU_FALLBACK_V1"
DAILY_RAIN_ADAPTER = "DAILY_RAIN_NWS_CLI_V1"

EASTERN_TZ = "America/New_York"

_MONTHS = {
    "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6,
    "july": 7, "august": 8, "september": 9, "october": 10, "november": 11, "december": 12,
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "jun": 6, "jul": 7, "aug": 8,
    "sep": 9, "sept": 9, "oct": 10, "nov": 11, "dec": 12,
}
_MONTH_PATTERN = "|".join(sorted(_MONTHS, key=len, reverse=True))

_IDENTITY_MONTH_DAY_RE = re.compile(
    rf"\b({_MONTH_PATTERN})[a-z]*\s+(\d{{1,2}})(?:st|nd|rd|th)?\b",
    re.I,
)
_FULL_MDY_RE = re.compile(
    rf"\b({_MONTH_PATTERN})[a-z]*\s+(\d{{1,2}})(?:st|nd|rd|th)?(?:,)?\s+(20\d{{2}})\b",
    re.I,
)
_FULL_DMY_RE = re.compile(
    rf"\b(\d{{1,2}})(?:st|nd|rd|th)?\s+({_MONTH_PATTERN})[a-z]*(?:,)?\s+(20\d{{2}})\b",
    re.I,
)
_SHORT_DMY_RE = re.compile(
    rf"\b(\d{{1,2}})(?:st|nd|rd|th)?\s+({_MONTH_PATTERN})[a-z]*\s+[\'’](\d{{2}})\b",
    re.I,
)
_URL_RE = re.compile(r"https?://[^\s<>\"')]+", re.I)


@dataclass(frozen=True, slots=True)
class RuleSource:
    kind: str
    url: str
    host: str
    station: str
    issuer: str | None = None


@dataclass(frozen=True, slots=True)
class DailyTemperatureContract:
    adapter_version: str
    family: str
    target_date: date
    unit: str
    precision: str
    primary: RuleSource
    fallback_kind: str
    fallback_condition: str
    fallback_deadline_local: datetime
    fallback_deadline_timezone: str
    primary_finalization: str
    revisions_before_finalization_count: bool
    no_data_resolution: str


@dataclass(frozen=True, slots=True)
class DailyRainContract:
    adapter_version: str
    target_date: date
    city_label: str
    station: str
    primary: RuleSource
    threshold_inches: float
    trace_counts: bool
    observation_interval: str
    observation_time_basis: str
    report_cutoff_local: datetime
    report_cutoff_timezone: str
    multiple_version_rule: str
    no_figure_resolution: str


@dataclass(frozen=True, slots=True)
class ContractParseResult:
    supported: bool
    adapter_version: str | None
    contract: DailyTemperatureContract | DailyRainContract | None
    failure_code: str | None


def _clean_url(value: str) -> str:
    return str(value or "").strip().rstrip(".,;")


def _urls(*values: object) -> list[str]:
    out: list[str] = []
    for value in values:
        for raw in _URL_RE.findall(str(value or "")):
            clean = _clean_url(raw)
            if clean and clean not in out:
                out.append(clean)
    return out


def _host(url: str) -> str:
    try:
        return (urlparse(url).hostname or "").lower().rstrip(".")
    except Exception:
        return ""


def _weather_gov_host(host: str) -> bool:
    return host == "weather.gov" or host.endswith(".weather.gov")


def _date_from_parts(year: int, month_raw: str, day_raw: str) -> date | None:
    month = _MONTHS.get(month_raw.lower())
    if month is None:
        return None
    try:
        return date(year, month, int(day_raw))
    except ValueError:
        return None


def _full_dates(text: str) -> set[date]:
    out: set[date] = set()
    for match in _FULL_MDY_RE.finditer(text):
        parsed = _date_from_parts(int(match.group(3)), match.group(1), match.group(2))
        if parsed:
            out.add(parsed)
    for match in _FULL_DMY_RE.finditer(text):
        parsed = _date_from_parts(int(match.group(3)), match.group(2), match.group(1))
        if parsed:
            out.add(parsed)
    for match in _SHORT_DMY_RE.finditer(text):
        parsed = _date_from_parts(2000 + int(match.group(3)), match.group(2), match.group(1))
        if parsed:
            out.add(parsed)
    return out


def _identity_month_day(market: Market) -> tuple[int, int] | None:
    identity = f"{market.event_title or ''} {market.question or ''}"
    found: set[tuple[int, int]] = set()
    for match in _IDENTITY_MONTH_DAY_RE.finditer(identity):
        month = _MONTHS.get(match.group(1).lower())
        if month is None:
            return None
        try:
            d = date(2000, month, int(match.group(2)))
        except ValueError:
            return None
        found.add((d.month, d.day))
    return next(iter(found)) if len(found) == 1 else None


def exact_target_date(market: Market) -> date | None:
    """Resolve the one rule date matching the event identity's month/day.

    Rule text often contains the following day's fallback/finalization date too, so
    we cannot require all dates in the rules to be identical. Instead the event
    identity must name exactly one month/day, and exactly one year is allowed among
    full rule dates matching that month/day.
    """
    month_day = _identity_month_day(market)
    if month_day is None:
        return None
    rule_text = f"{market.event_title or ''} {market.question or ''} {market.description or ''}"
    matching = {d for d in _full_dates(rule_text) if (d.month, d.day) == month_day}
    if len(matching) == 1:
        return next(iter(matching))

    # Some Gamma rows carry a fully qualified market end time while the title/rules
    # state only Month Day. We use it only as a consistency anchor when its UTC date
    # is exactly the following day. This avoids silently choosing a year from "now".
    if not matching and market.end_date:
        try:
            parsed = datetime.fromisoformat(str(market.end_date).replace("Z", "+00:00"))
        except (TypeError, ValueError):
            parsed = None
        if parsed is not None:
            candidate = parsed.date() - timedelta(days=1)
            if (candidate.month, candidate.day) == month_day:
                return candidate
    return None


def _explicit_unit(market: Market) -> str | None:
    text = str(market.question or "")
    units: set[str] = set()
    if re.search(r"(?:°\s*)?F\b|\bFahrenheit\b", text, re.I):
        units.add("F")
    if re.search(r"(?:°\s*)?C\b|\bCelsius\b", text, re.I):
        units.add("C")
    return next(iter(units)) if len(units) == 1 else None


def _wrh_source(market: Market) -> RuleSource | None:
    candidates: list[RuleSource] = []
    for url in _urls(market.resolution_source, market.description):
        try:
            parsed = urlparse(url)
        except Exception:
            continue
        host = (parsed.hostname or "").lower().rstrip(".")
        if not _weather_gov_host(host) or parsed.path.rstrip("/").lower() != "/wrh/timeseries":
            continue
        query = {str(k).lower(): v for k, v in parse_qs(parsed.query, keep_blank_values=True).items()}
        sites = query.get("site") or []
        if len(sites) != 1:
            continue
        station = str(sites[0]).strip().upper()
        if not re.fullmatch(r"[A-Z0-9]{4}", station):
            continue
        candidates.append(RuleSource("NWS_WRH", url, host, station))
    unique = {(row.url, row.station): row for row in candidates}
    return next(iter(unique.values())) if len(unique) == 1 else None


def _cli_source(market: Market) -> RuleSource | None:
    candidates: list[RuleSource] = []
    for url in _urls(market.resolution_source, market.description):
        try:
            parsed = urlparse(url)
        except Exception:
            continue
        host = (parsed.hostname or "").lower().rstrip(".")
        if host != "forecast.weather.gov" or parsed.path.rstrip("/").lower() != "/product.php":
            continue
        query = {str(k).lower(): v for k, v in parse_qs(parsed.query, keep_blank_values=True).items()}
        products = [str(x).upper() for x in (query.get("product") or [])]
        issuers = [str(x).upper() for x in (query.get("issuedby") or [])]
        if products != ["CLI"] or len(issuers) != 1 or not re.fullmatch(r"[A-Z0-9]{3,4}", issuers[0]):
            continue
        candidates.append(RuleSource("NWS_CLI", url, host, "", issuer=issuers[0]))
    unique = {(row.url, row.issuer): row for row in candidates}
    return next(iter(unique.values())) if len(unique) == 1 else None


def _station_from_cli_rules(text: str) -> str | None:
    found = {m.group(1).upper() for m in re.finditer(r"\b(?:for|station)\s+([A-Z][A-Z0-9]{3})\b", text)}
    return next(iter(found)) if len(found) == 1 else None


def _temperature_family(market: Market) -> str | None:
    text = f"{market.event_title or ''} {market.question or ''}".lower()
    high = "highest temperature" in text
    low = "lowest temperature" in text
    if high == low:
        return None
    return "daily_high_temperature" if high else "daily_low_temperature"


def parse_daily_temperature_contract(market: Market) -> ContractParseResult:
    family = _temperature_family(market)
    if family is None:
        return ContractParseResult(False, None, None, "TEMP_FAMILY")

    target = exact_target_date(market)
    if target is None:
        return ContractParseResult(False, DAILY_TEMP_ADAPTER, None, "TARGET_DATE")

    unit = _explicit_unit(market)
    if unit is None:
        return ContractParseResult(False, DAILY_TEMP_ADAPTER, None, "TEMP_UNIT")

    primary = _wrh_source(market)
    if primary is None:
        return ContractParseResult(False, DAILY_TEMP_ADAPTER, None, "WRH_PRIMARY")

    text = f"{market.description or ''} {market.resolution_source or ''}".lower()

    # Current rule family: NWS is primary; Weather Underground Daily Observations is
    # used only if the primary remains unavailable by 11:59 PM ET on the next day.
    if "weather underground" not in text:
        return ContractParseResult(False, DAILY_TEMP_ADAPTER, None, "WU_FALLBACK_MISSING")
    if not re.search(r"(?:if|when).{0,120}(?:noaa|nws|primary).{0,80}(?:unavailable|not available|missing|no data)", text, re.S):
        return ContractParseResult(False, DAILY_TEMP_ADAPTER, None, "WU_FALLBACK_CONDITION")
    if not re.search(r"11:59\s*p\.?m\.?.{0,20}(?:et|eastern)", text, re.I | re.S):
        return ContractParseResult(False, DAILY_TEMP_ADAPTER, None, "WU_FALLBACK_DEADLINE")

    following_day = target + timedelta(days=1)
    full_rule_dates = _full_dates(f"{market.description or ''}")
    # If rules explicitly state a fallback/finalization date beyond the target, it
    # must be the immediate following day. We allow no explicit following date when
    # the text describes it generically as "the following day".
    later = sorted(d for d in full_rule_dates if d > target)
    if later and any(d != following_day for d in later):
        return ContractParseResult(False, DAILY_TEMP_ADAPTER, None, "FOLLOWING_DAY_CONFLICT")

    next_data_point = bool(re.search(
        r"(?:first|initial).{0,80}(?:data\s*point|datapoint|observation).{0,100}(?:following|next)\s+(?:day|date)",
        text,
        re.S,
    ))
    # Polymarket's current live rules put the boundary in either order:
    # "first following-day datapoint" or "first datapoint for the following date".
    # Both are accepted only when a revision/correction clause explicitly binds to
    # an until/before/prior cutoff at that same following-day observation boundary.
    revision_rule = bool(re.search(
        r"(?:revision|revised|correction|corrected).{0,160}(?:until|before|prior).{0,120}(?:(?:first|initial).{0,80}(?:data\s*point|datapoint|observation).{0,100}(?:following|next)[\s-]+(?:day|date)|(?:following|next)[\s-]+(?:day|date).{0,80}(?:data\s*point|datapoint|observation))",
        text,
        re.S,
    ))
    if not next_data_point:
        return ContractParseResult(False, DAILY_TEMP_ADAPTER, None, "PRIMARY_FINALIZATION")
    if not revision_rule:
        return ContractParseResult(False, DAILY_TEMP_ADAPTER, None, "REVISION_RULE")

    no_data_lowest = bool(re.search(
        r"(?:if|should).{0,160}(?:no\s+data|data.{0,40}(?:unavailable|not available)).{0,160}(?:lowest|bottom).{0,40}(?:bracket|range|bucket)",
        text,
        re.S,
    ))
    if not no_data_lowest:
        return ContractParseResult(False, DAILY_TEMP_ADAPTER, None, "NO_DATA_RULE")

    contract = DailyTemperatureContract(
        adapter_version=DAILY_TEMP_ADAPTER,
        family=family,
        target_date=target,
        unit=unit,
        precision="whole_degree",
        primary=primary,
        fallback_kind="WEATHER_UNDERGROUND_DAILY_OBSERVATIONS",
        fallback_condition="NWS_WRH_UNAVAILABLE_BY_FOLLOWING_DAY_DEADLINE",
        fallback_deadline_local=datetime.combine(following_day, time(23, 59)),
        fallback_deadline_timezone=EASTERN_TZ,
        primary_finalization="FIRST_FOLLOWING_DAY_WRH_DATAPOINT_OR_FALLBACK_DEADLINE",
        revisions_before_finalization_count=True,
        no_data_resolution="LOWEST_BRACKET",
    )
    return ContractParseResult(True, DAILY_TEMP_ADAPTER, contract, None)


def parse_daily_rain_contract(market: Market) -> ContractParseResult:
    identity = f"{market.event_title or ''} {market.question or ''}".lower()
    text = f"{market.description or ''} {market.resolution_source or ''}"
    lower = text.lower()

    if "rain" not in identity and "precipitation" not in lower:
        return ContractParseResult(False, None, None, "RAIN_FAMILY")

    target = exact_target_date(market)
    if target is None:
        return ContractParseResult(False, DAILY_RAIN_ADAPTER, None, "TARGET_DATE")

    primary = _cli_source(market)
    if primary is None:
        return ContractParseResult(False, DAILY_RAIN_ADAPTER, None, "CLI_PRIMARY")

    station = _station_from_cli_rules(text)
    if station is None:
        return ContractParseResult(False, DAILY_RAIN_ADAPTER, None, "CLI_STATION")

    # The rule must bind the station identity to the same sentence/family and the
    # CLI URL must not invent a second station identifier. issuedby is the forecast
    # office identifier, so it is intentionally not required to equal station.
    if not re.search(r"0\.01\s*(?:inch|inches|in\b)", lower):
        return ContractParseResult(False, DAILY_RAIN_ADAPTER, None, "RAIN_THRESHOLD")
    if not re.search(r"trace.{0,50}(?:does\s+not|doesn't|not).{0,40}(?:qualify|count)", lower, re.S):
        return ContractParseResult(False, DAILY_RAIN_ADAPTER, None, "TRACE_RULE")
    if not re.search(r"midnight.{0,80}midnight.{0,80}local\s+standard\s+time", lower, re.S):
        return ContractParseResult(False, DAILY_RAIN_ADAPTER, None, "CLIMATE_DAY")
    if not re.search(r"2:00\s*p\.?m\.?.{0,20}(?:et|eastern)", lower, re.S):
        return ContractParseResult(False, DAILY_RAIN_ADAPTER, None, "CLI_CUTOFF")
    if not re.search(r"(?:multiple\s+versions|more\s+than\s+one\s+version).{0,140}(?:last|latest).{0,120}(?:before|prior)", lower, re.S):
        return ContractParseResult(False, DAILY_RAIN_ADAPTER, None, "CLI_VERSION_RULE")
    if not re.search(r"(?:if|when).{0,80}no\s+(?:figure|precipitation\s+figure).{0,120}(?:published|available).{0,120}(?:resolve|resolves|resolution).{0,30}\bno\b", lower, re.S):
        return ContractParseResult(False, DAILY_RAIN_ADAPTER, None, "CLI_NO_FIGURE_RULE")

    following_day = target + timedelta(days=1)
    city_label = str(market.question or market.event_title or "").strip()
    contract = DailyRainContract(
        adapter_version=DAILY_RAIN_ADAPTER,
        target_date=target,
        city_label=city_label,
        station=station,
        primary=RuleSource(primary.kind, primary.url, primary.host, station, primary.issuer),
        threshold_inches=0.01,
        trace_counts=False,
        observation_interval="MIDNIGHT_TO_MIDNIGHT",
        observation_time_basis="LOCAL_STANDARD_TIME",
        report_cutoff_local=datetime.combine(following_day, time(14, 0)),
        report_cutoff_timezone=EASTERN_TZ,
        multiple_version_rule="LAST_VERSION_PUBLISHED_BEFORE_CUTOFF_GOVERNS",
        no_figure_resolution="NO",
    )
    return ContractParseResult(True, DAILY_RAIN_ADAPTER, contract, None)


def parse_weather_contract(market: Market) -> ContractParseResult:
    family = _temperature_family(market)
    if family is not None:
        return parse_daily_temperature_contract(market)
    identity = f"{market.event_title or ''} {market.question or ''}".lower()
    if "where will it rain" in identity or "measurable precipitation" in str(market.description or "").lower():
        return parse_daily_rain_contract(market)
    return ContractParseResult(False, None, None, "UNSUPPORTED_WEATHER_CONTRACT")
