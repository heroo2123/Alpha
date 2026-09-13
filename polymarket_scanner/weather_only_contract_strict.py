from __future__ import annotations

"""Additional fail-closed semantic admission for the weather paper experiment.

The inventory compiler remains useful for broad discovery.  This module is a stricter
*live-paper gate*: it refuses to promote a temperature event unless the title date,
WRH source/station, child statistic, supported bucket grammar, identities and integer
partition all agree.  It deliberately accepts less than the inventory compiler.
"""

import math
import re
from datetime import date
from urllib.parse import parse_qs, urlparse

from .weather_only_contracts import DAILY_HIGH, DAILY_LOW, compile_weather_event
from .weather_only_rules import apply_rule_authority, compile_temperature_rule_authority


STRICT_CONTRACT_VERSION = "weather_contract_strict_v2_pre_authority_syntax_fail_closed"


class StrictWeatherContractError(RuntimeError):
    def __init__(self, code: str):
        self.code = str(code)
        super().__init__(self.code)


_URL_RE = re.compile(r"https?://[^\s<>\"')]+", re.I)
_TITLE_DATE_RE = re.compile(
    r"\b(january|february|march|april|may|june|july|august|september|october|november|december|"
    r"jan|feb|mar|apr|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\s+(\d{1,2})(?:st|nd|rd|th)?\b",
    re.I,
)
_MONTHS = {
    "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6,
    "july": 7, "august": 8, "september": 9, "october": 10, "november": 11, "december": 12,
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "jun": 6, "jul": 7, "aug": 8,
    "sep": 9, "sept": 9, "oct": 10, "nov": 11, "dec": 12,
}
_ALLOWED_WRH_HOSTS = {"weather.gov", "www.weather.gov"}


def _all_text_parts(event: dict) -> list[str]:
    parts = [
        str(event.get("title") or ""),
        str(event.get("description") or ""),
        str(event.get("resolutionSource") or ""),
    ]
    for row in event.get("markets") or []:
        if isinstance(row, dict):
            parts.extend((
                str(row.get("question") or ""),
                str(row.get("description") or ""),
                str(row.get("resolutionSource") or ""),
            ))
    return parts


def _title_date_matches(event: dict, target: date) -> bool:
    matches = list(_TITLE_DATE_RE.finditer(str(event.get("title") or "")))
    if not matches:
        return False
    for match in matches:
        if _MONTHS.get(match.group(1).lower()) != target.month or int(match.group(2)) != target.day:
            return False
    return True


def _trusted_wrh_station(event: dict) -> str | None:
    stations: set[str] = set()
    trusted_count = 0
    for raw_url in _URL_RE.findall(" ".join(_all_text_parts(event))):
        url = raw_url.rstrip(".,;]")
        parsed = urlparse(url)
        host = (parsed.hostname or "").lower()
        path = parsed.path.rstrip("/").lower()
        looks_like_wrh = "weather.gov/wrh/timeseries" in url.lower() or path.endswith("/wrh/timeseries")
        if not looks_like_wrh:
            continue
        if parsed.scheme.lower() != "https" or host not in _ALLOWED_WRH_HOSTS or path != "/wrh/timeseries":
            raise StrictWeatherContractError("STRICT_SOURCE_URL_UNTRUSTED")
        sites = [str(value).strip().upper() for value in parse_qs(parsed.query).get("site", []) if str(value).strip()]
        if len(sites) != 1 or len(sites[0]) != 4 or not sites[0].isalnum():
            raise StrictWeatherContractError("STRICT_SOURCE_STATION_INVALID")
        stations.add(sites[0])
        trusted_count += 1
    if trusted_count == 0 or len(stations) != 1:
        return None
    return next(iter(stations))


def _question_supported(question: str, family: str) -> bool:
    text = " ".join(str(question or "").strip().split())
    low = text.lower()
    if any(term in low for term in ("less than", "greater than", "not ", "except ")):
        return False
    opposite = "lowest temperature" if family == DAILY_HIGH else "highest temperature"
    if opposite in low:
        return False
    number = r"-?\d+(?:\.0+)?"
    unit = r"(?:°\s*[FC]|\s+degrees?\s+[FC]|\s*[FC])"
    patterns = (
        rf".*\b{number}\s*{unit}\s+or\s+lower\??$",
        rf".*\b{number}\s*{unit}\s+or\s+higher\??$",
        rf".*\b{number}\s*(?:-|–|to)\s*{number}\s*{unit}\??$",
        rf".*\b(?:be\s+)?{number}\s*{unit}\??$",
    )
    return any(re.fullmatch(pattern, text, re.I) for pattern in patterns)


def _partition_is_exact(compiled) -> bool:
    buckets = tuple(compiled.buckets or ())
    if not buckets:
        return False
    markets: set[str] = set()
    conditions: set[str] = set()
    tokens: set[str] = set()
    finite_bounds: list[int] = []
    for bucket in buckets:
        if not bucket.market_id or bucket.market_id in markets:
            return False
        if not bucket.condition_id or bucket.condition_id in conditions:
            return False
        if not bucket.yes_token or not bucket.no_token or bucket.yes_token == bucket.no_token:
            return False
        if bucket.yes_token in tokens or bucket.no_token in tokens:
            return False
        markets.add(bucket.market_id)
        conditions.add(bucket.condition_id)
        tokens.update((bucket.yes_token, bucket.no_token))
        for bound in (bucket.lower, bucket.upper):
            if bound is None:
                continue
            value = float(bound)
            if not math.isfinite(value) or not value.is_integer():
                return False
            finite_bounds.append(int(value))
    if not finite_bounds:
        return False
    for value in range(min(finite_bounds) - 3, max(finite_bounds) + 4):
        matches = [
            bucket for bucket in buckets
            if (bucket.lower is None or value >= bucket.lower)
            and (bucket.upper is None or value <= bucket.upper)
        ]
        if len(matches) != 1:
            return False
    return True


def compile_strict_temperature_event(event: dict):
    if not isinstance(event, dict):
        raise StrictWeatherContractError("STRICT_EVENT_INVALID")
    raw = compile_weather_event(event)
    if raw.family not in {DAILY_HIGH, DAILY_LOW}:
        raise StrictWeatherContractError("STRICT_FAMILY_UNSUPPORTED")

    # Reject independently provable syntax/identity conflicts before asking the
    # broader rule-authority layer to promote the contract.  Otherwise malformed
    # child wording can merely collapse partition_shape_complete and be reported as
    # a generic authority failure, obscuring the exact unsafe proposition change.
    if raw.target_date is not None and not _title_date_matches(event, raw.target_date):
        raise StrictWeatherContractError("STRICT_TITLE_DATE_MISMATCH")

    source_station = _trusted_wrh_station(event)
    if source_station is None:
        raise StrictWeatherContractError("STRICT_SOURCE_STATION_MISMATCH")
    if raw.station_hint and source_station != str(raw.station_hint).upper():
        raise StrictWeatherContractError("STRICT_SOURCE_STATION_MISMATCH")

    markets = [row for row in (event.get("markets") or []) if isinstance(row, dict)]
    opposite = "lowest temperature" if raw.family == DAILY_HIGH else "highest temperature"
    for row in markets:
        if not _question_supported(str(row.get("question") or ""), raw.family):
            raise StrictWeatherContractError("STRICT_BUCKET_GRAMMAR_UNSUPPORTED")
        child_text = " ".join((str(row.get("question") or ""), str(row.get("description") or ""))).lower()
        if opposite in child_text:
            raise StrictWeatherContractError("STRICT_CHILD_STATISTIC_CONFLICT")

    authority = compile_temperature_rule_authority(event, raw)
    compiled = apply_rule_authority(raw, authority)
    if (
        not compiled.shadow_supported
        or not compiled.partition_shape_complete
        or not compiled.exactly_one_outcome_proven
        or compiled.target_date is None
        or not compiled.station_hint
        or compiled.unit not in {"C", "F"}
        or not compiled.buckets
        or compiled.financial_authority
    ):
        raise StrictWeatherContractError("STRICT_RULE_AUTHORITY_UNPROVEN")

    if source_station != str(compiled.station_hint).upper():
        raise StrictWeatherContractError("STRICT_SOURCE_STATION_MISMATCH")
    if len(markets) != len(compiled.buckets):
        raise StrictWeatherContractError("STRICT_CHILD_COUNT_MISMATCH")
    if not _partition_is_exact(compiled):
        raise StrictWeatherContractError("STRICT_PARTITION_UNPROVEN")
    return compiled
