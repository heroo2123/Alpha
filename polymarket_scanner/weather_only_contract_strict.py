from __future__ import annotations

"""Fail-closed semantic admission for the weather paper experiment.

The broad inventory compiler is discovery-only.  This live-paper gate accepts a much
smaller contract language: one exact highest/lowest-temperature question template,
known bucket forms, one coherent recurring rule text/source identity, exact station,
and an integer partition.  Unsupported wording is rejected rather than reinterpreted.
"""

import hashlib
import json
import math
import re
from datetime import date
from urllib.parse import parse_qs, urlparse

from .weather_only_contracts import DAILY_HIGH, DAILY_LOW, compile_weather_event
from .weather_only_rules import apply_rule_authority, compile_temperature_rule_authority


STRICT_CONTRACT_VERSION = "weather_contract_strict_v5_complete_rule_grammar_fail_closed"


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

# Gamma currently uses ``description``/``resolutionSource`` for these contracts, but a
# strict live-paper boundary must not silently ignore another settlement-text alias if
# one appears in an event/market object.  Any populated alias is treated as operative
# evidence and therefore must agree exactly with the other populated copies.
_OPERATIVE_RULE_FIELDS = (
    "description",
    "rules",
    "resolutionRules",
    "resolution_rules",
    "resolutionCriteria",
    "resolution_criteria",
    "settlementRules",
    "settlement_rules",
    "settlementCriteria",
    "settlement_criteria",
)
_OPERATIVE_SOURCE_FIELDS = (
    "resolutionSource",
    "resolution_source",
    "settlementSource",
    "settlement_source",
)


def _norm(value: object) -> str:
    text = str(value or "")
    text = text.replace("“", '"').replace("”", '"').replace("’", "'").replace("‘", "'")
    return re.sub(r"\s+", " ", text).strip().lower()


def _operative_values(container: dict, fields: tuple[str, ...], *, code: str) -> list[str]:
    values: list[str] = []
    for field in fields:
        if field not in container:
            continue
        raw = container.get(field)
        if raw is None or raw == "":
            continue
        if not isinstance(raw, str):
            raise StrictWeatherContractError(code)
        value = _norm(raw)
        if value:
            values.append(value)
    return values


def _all_text_parts(event: dict) -> list[str]:
    parts = [str(event.get("title") or "")]
    parts.extend(
        _operative_values(
            event,
            _OPERATIVE_RULE_FIELDS,
            code="STRICT_OPERATIVE_RULE_FIELD_INVALID",
        )
    )
    parts.extend(
        _operative_values(
            event,
            _OPERATIVE_SOURCE_FIELDS,
            code="STRICT_OPERATIVE_SOURCE_FIELD_INVALID",
        )
    )
    for row in event.get("markets") or []:
        if isinstance(row, dict):
            parts.append(str(row.get("question") or ""))
            parts.extend(
                _operative_values(
                    row,
                    _OPERATIVE_RULE_FIELDS,
                    code="STRICT_OPERATIVE_RULE_FIELD_INVALID",
                )
            )
            parts.extend(
                _operative_values(
                    row,
                    _OPERATIVE_SOURCE_FIELDS,
                    code="STRICT_OPERATIVE_SOURCE_FIELD_INVALID",
                )
            )
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
    """Accept only a completely consumed recurring Polymarket question template."""
    text = " ".join(str(question or "").strip().split())
    statistic = "highest" if family == DAILY_HIGH else "lowest" if family == DAILY_LOW else None
    if statistic is None:
        return False
    number = r"-?\d+(?:\.0+)?"
    unit = r"(?:°\s*[FC]|\s+degrees?\s+[FC]|\s*[FC])"
    bucket = rf"(?:{number}\s*{unit}\s+or\s+lower|{number}\s*{unit}\s+or\s+higher|{number}\s*(?:-|–|to)\s*{number}\s*{unit}|{number}\s*{unit})"
    return re.fullmatch(
        rf"Will\s+the\s+{statistic}\s+temperature\s+be\s+{bucket}\?",
        text,
        re.I,
    ) is not None


def _supported_nws_rule_structure(operative_rules: str, compiled) -> bool:
    """Whitelist the complete supported recurring NWS rule grammar.

    Required-phrase detection is not enough for settlement meaning: a contradictory
    suffix can preserve every expected phrase.  This recognizer therefore consumes
    the *entire* operative text.  Anything outside the two explicitly supported NWS
    recurring templates is rejected.  False negatives are intentional at this
    financial boundary; a newly worded contract must be reviewed and added as a new
    versioned grammar rather than guessed at runtime.
    """
    if compiled.target_date is None or not compiled.station_hint or compiled.unit not in {"F", "C"}:
        return False
    if compiled.family not in {DAILY_HIGH, DAILY_LOW}:
        return False

    text = _norm(operative_rules)
    statistic = "highest" if compiled.family == DAILY_HIGH else "lowest"
    unit_word = "fahrenheit" if compiled.unit == "F" else "celsius"
    target = compiled.target_date
    day = rf"0?{target.day}"
    month = re.escape(target.strftime("%b").lower())
    year = f"{target.year % 100:02d}"
    station = re.escape(str(compiled.station_hint).lower())
    source_url = rf"https://(?:www\.)?weather\.gov/wrh/timeseries\?site={station}"

    public_template = re.compile(
        rf"^this market resolves to the range containing the {statistic} temperature on "
        rf"{day} {month} '{year}, in degrees {unit_word}\. "
        rf"the source is noaa, the {statistic} reading under the \"temp\" column for all times on this day\. "
        rf"{source_url} "
        rf"the source measures temperatures to whole degrees {unit_word}\. "
        rf"if noaa data is unavailable by 11:59 pm et on the day following the observation date, "
        rf"the weather underground daily observations table is used\. "
        rf"if there is no data, this market resolves to the lowest bracket\. "
        rf"resolution occurs once the first data point for the following date is published, "
        rf"or at the deadline, whichever comes first\. "
        rf"revisions are considered until the first data ?point for the following date, "
        rf"after which any alterations will not be considered\.?$"
    )

    compact_template = re.compile(
        rf"^observation date {day} {month} '{year}, in whole degrees {unit_word}\. "
        rf"the market resolves using the {statistic} reading in the \"temp\" column across all times on this day\. "
        rf"on wrh select hourly data and show hourly data\. "
        rf"if wrh is unavailable, use the weather underground daily observations table by "
        rf"11:59 pm et on the day following the observation date\. "
        rf"if there is no data, the market resolves to the lowest bracket\. "
        rf"revisions are accepted until the first data point for the following date, "
        rf"whichever comes first, after which any alterations will not be considered\.?$"
    )

    return public_template.fullmatch(text) is not None or compact_template.fullmatch(text) is not None


def _coherent_rule_identity(event: dict, compiled) -> dict:
    """Require one complete supported operative rule/source meaning."""
    markets = [row for row in (event.get("markets") or []) if isinstance(row, dict)]
    containers = [event, *markets]

    descriptions: list[str] = []
    for container in containers:
        descriptions.extend(
            _operative_values(
                container,
                _OPERATIVE_RULE_FIELDS,
                code="STRICT_OPERATIVE_RULE_FIELD_INVALID",
            )
        )
    if not descriptions or len(set(descriptions)) != 1:
        raise StrictWeatherContractError("STRICT_OPERATIVE_RULE_TEXT_CONFLICT")
    operative_rules = descriptions[0]

    sources: list[str] = []
    for container in containers:
        sources.extend(
            _operative_values(
                container,
                _OPERATIVE_SOURCE_FIELDS,
                code="STRICT_OPERATIVE_SOURCE_FIELD_INVALID",
            )
        )
    if sources and len(set(sources)) != 1:
        raise StrictWeatherContractError("STRICT_OPERATIVE_SOURCE_CONFLICT")
    operative_source = sources[0] if sources else ""

    # This is the authority boundary.  Do not fall back to keyword blacklists or
    # required-phrase matching: the whole text must be a supported grammar.
    if not _supported_nws_rule_structure(operative_rules, compiled):
        raise StrictWeatherContractError("STRICT_OPERATIVE_RULE_STRUCTURE_UNSUPPORTED")

    questions = tuple(_norm(row.get("question")) for row in markets)
    return {
        "version": STRICT_CONTRACT_VERSION,
        "operative_rules": operative_rules,
        "operative_source": operative_source,
        "questions": questions,
    }


def strict_contract_identity(event: dict, compiled=None) -> dict:
    """Return immutable semantic preimage/digest for cache, dispatch and audit."""
    if not isinstance(event, dict):
        raise StrictWeatherContractError("STRICT_EVENT_INVALID")
    if compiled is None:
        compiled = compile_weather_event(event)
    identity = _coherent_rule_identity(event, compiled)
    payload = {
        **identity,
        "event_id": str(compiled.event_id),
        "family": str(compiled.family),
        "unit": str(compiled.unit or ""),
        "target_date": compiled.target_date.isoformat() if compiled.target_date else None,
        "station": str(compiled.station_hint or "").upper(),
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return {**payload, "sha256": hashlib.sha256(encoded.encode("utf-8")).hexdigest()}


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

    # Prove the entire operative grammar before phrase-based authority promotion.
    _coherent_rule_identity(event, raw)

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
    # Ensure the exact semantic preimage remains valid after authority application.
    strict_contract_identity(event, compiled)
    return compiled
