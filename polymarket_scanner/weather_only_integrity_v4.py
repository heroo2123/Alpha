from __future__ import annotations

"""Fail-closed contract identity and semantic compiler for weather paper v4.

This module exists because a believable market/forecast/quote pipeline is useless if
it refers to the wrong proposition.  It deliberately supports only the current
NOAA/NWS WRH whole-degree daily high/low contract family.  Unknown grammar,
conflicting child semantics, untrusted source URLs, ambiguous dates, fractional
whole-degree bucket lattices, duplicate identities and projection conflicts reject.

The result grants no financial authority.  Multi-child complete-set arbitrage is
not promoted by v4 even when this compiler proves the displayed weather partition;
the live v4 structural lane is limited to complementary YES+NO tokens of one exact
binary condition.
"""

import copy
import hashlib
import json
import re
import time
from dataclasses import asdict, dataclass
from datetime import date
from urllib.parse import parse_qs, urlparse

from .weather_only_contracts import (
    DAILY_HIGH,
    DAILY_LOW,
    SOURCE_NWS_WRH,
    WeatherBucket,
)
from .weather_only_discovery import (
    DEFAULT_TAGS,
    MAX_EVENTS,
    MAX_MARKETS,
    MAX_PAGES_PER_TAG,
    WeatherDiscoveryError,
    WeatherDiscoverySnapshot,
    WeatherOnlyDiscovery,
)


WEATHER_INTEGRITY_V4_VERSION = "weather_integrity_v4_strict_semantics_identity_20260913"
SUPPORTED_AUTHORITY_HOSTS = {"weather.gov", "www.weather.gov"}
SUPPORTED_AUTHORITY_PATH = "/wrh/timeseries"
_MONTHS = {
    "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6,
    "july": 7, "august": 8, "september": 9, "october": 10, "november": 11, "december": 12,
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "jun": 6, "jul": 7, "aug": 8,
    "sep": 9, "sept": 9, "oct": 10, "nov": 11, "dec": 12,
}
_MONTH = "(?:January|February|March|April|May|June|July|August|September|October|November|December|Jan|Feb|Mar|Apr|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)"
_FULL_MONTH_FIRST = re.compile(
    rf"\b({_MONTH})[a-z]*\s+(\d{{1,2}})(?:st|nd|rd|th)?(?:,)?\s+(20\d{{2}})\b", re.I
)
_FULL_DAY_FIRST = re.compile(
    rf"\b(\d{{1,2}})(?:st|nd|rd|th)?\s+({_MONTH})[a-z]*\s+(?:'(\d{{2}})|(20\d{{2}}))\b", re.I
)
_YEARLESS_MONTH_FIRST = re.compile(
    rf"\b({_MONTH})[a-z]*\s+(\d{{1,2}})(?:st|nd|rd|th)?\b(?!\s*,?\s*20\d{{2}})", re.I
)
_YEARLESS_DAY_FIRST = re.compile(
    rf"\b(\d{{1,2}})(?:st|nd|rd|th)?\s+({_MONTH})[a-z]*\b(?!\s+(?:'\d{{2}}|20\d{{2}}))", re.I
)
_URL_RE = re.compile(r"https?://[^\s<>\"')]+", re.I)
_STATION_RE = re.compile(r"^[A-Z0-9]{4}$")


class WeatherIntegrityV4Error(RuntimeError):
    def __init__(self, code: str):
        self.code = str(code)
        super().__init__(self.code)


def _flag(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return bool(value)
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return False


def _json_list(value: object) -> list:
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        try:
            decoded = json.loads(value)
        except Exception:
            return []
        return decoded if isinstance(decoded, list) else []
    return []


def _norm(value: object) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip().lower()


def _canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False)


def _sha(value: object) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _rows(event: dict) -> list[dict]:
    return [row for row in (event.get("markets") or []) if isinstance(row, dict)]


def _full_text(event: dict) -> str:
    parts: list[object] = [event.get("title"), event.get("description"), event.get("resolutionSource")]
    for row in _rows(event):
        parts.extend((row.get("question"), row.get("description"), row.get("resolutionSource")))
    return " ".join(str(part or "") for part in parts)


def _family_from_title(title: str) -> str | None:
    high = bool(re.search(r"\bhighest\s+temperature\b", title, re.I))
    low = bool(re.search(r"\blowest\s+temperature\b", title, re.I))
    if high == low:
        return None
    return DAILY_HIGH if high else DAILY_LOW


def _date_value(year: int, month_name: str, day: int) -> date | None:
    month = _MONTHS.get(month_name.lower())
    if month is None:
        return None
    try:
        return date(int(year), int(month), int(day))
    except ValueError:
        return None


def _full_dates(text: str) -> set[date]:
    found: set[date] = set()
    for match in _FULL_MONTH_FIRST.finditer(text):
        value = _date_value(int(match.group(3)), match.group(1), int(match.group(2)))
        if value is not None:
            found.add(value)
    for match in _FULL_DAY_FIRST.finditer(text):
        year = int(match.group(4)) if match.group(4) else 2000 + int(match.group(3))
        value = _date_value(year, match.group(2), int(match.group(1)))
        if value is not None:
            found.add(value)
    return found


def _yearless_month_days(text: str) -> set[tuple[int, int]]:
    found: set[tuple[int, int]] = set()
    for match in _YEARLESS_MONTH_FIRST.finditer(text):
        month = _MONTHS.get(match.group(1).lower())
        if month is not None:
            found.add((month, int(match.group(2))))
    for match in _YEARLESS_DAY_FIRST.finditer(text):
        month = _MONTHS.get(match.group(2).lower())
        if month is not None:
            found.add((month, int(match.group(1))))
    return found


def _unit_tokens(text: str) -> set[str]:
    units: set[str] = set()
    if re.search(r"(?:°\s*F\b|\bFahrenheit\b|\bdegrees?\s+F\b)", text, re.I):
        units.add("F")
    if re.search(r"(?:°\s*C\b|\bCelsius\b|\bdegrees?\s+C\b)", text, re.I):
        units.add("C")
    return units


def _extract_urls(text: str) -> tuple[str, ...]:
    out: list[str] = []
    for raw in _URL_RE.findall(text):
        value = raw.strip().rstrip(".,;")
        if value and value not in out:
            out.append(value)
    return tuple(out)


def _wrh_site_from_url(raw: str) -> tuple[str | None, str | None]:
    """Return (station, error) for an apparent WRH authority URL."""
    try:
        parsed = urlparse(raw)
    except Exception:
        return None, "SOURCE_URL_INVALID"
    host = (parsed.hostname or "").lower().rstrip(".")
    path = parsed.path.rstrip("/").lower()
    apparent = path == SUPPORTED_AUTHORITY_PATH or "weather.gov/wrh/timeseries" in raw.lower()
    if not apparent:
        return None, None
    if parsed.scheme.lower() != "https" or host not in SUPPORTED_AUTHORITY_HOSTS or path != SUPPORTED_AUTHORITY_PATH:
        return None, "UNTRUSTED_WRH_URL"
    query = parse_qs(parsed.query, keep_blank_values=True)
    site_values = [value.strip().upper() for key, values in query.items() if key.lower() == "site" for value in values]
    if len(site_values) != 1 or not _STATION_RE.fullmatch(site_values[0]):
        return None, "WRH_STATION_QUERY_INVALID"
    return site_values[0], None


def _strict_bucket_bounds(question: str, unit: str) -> tuple[float | None, float | None] | None:
    """Parse only explicit temperature propositions on a whole-degree lattice."""
    q = str(question or "").replace("–", "-").replace("—", "-")
    if re.search(r"\bnot\b|\bexcept\b", q, re.I):
        return None
    u = re.escape(unit)
    num = r"(-?\d+(?:\.\d+)?)"

    def integer(raw: str) -> int | None:
        try:
            value = float(raw)
        except (TypeError, ValueError):
            return None
        if not value.is_integer():
            return None
        return int(value)

    # Explicit inclusive finite range.
    match = re.search(rf"{num}\s*(?:-|\bto\b)\s*{num}\s*°?\s*{u}\b", q, re.I)
    if match:
        lo, hi = integer(match.group(1)), integer(match.group(2))
        return (float(lo), float(hi)) if lo is not None and hi is not None and lo <= hi else None

    # Inclusive tails, both common suffix and prefix wording.
    match = re.search(rf"{num}\s*°?\s*{u}\b\s*(?:or\s*)?(?:lower|below|less)\b", q, re.I)
    if match:
        value = integer(match.group(1))
        return (None, float(value)) if value is not None else None
    match = re.search(rf"{num}\s*°?\s*{u}\b\s*(?:or\s*)?(?:higher|above|more)\b", q, re.I)
    if match:
        value = integer(match.group(1))
        return (float(value), None) if value is not None else None
    match = re.search(rf"(?:at\s+most|no\s+more\s+than|<=|≤)\s*{num}\s*°?\s*{u}\b", q, re.I)
    if match:
        value = integer(match.group(1))
        return (None, float(value)) if value is not None else None
    match = re.search(rf"(?:at\s+least|no\s+less\s+than|>=|≥)\s*{num}\s*°?\s*{u}\b", q, re.I)
    if match:
        value = integer(match.group(1))
        return (float(value), None) if value is not None else None

    # Strict inequalities are converted only because this v4 profile explicitly
    # requires whole-degree settlement.
    match = re.search(rf"(?:less\s+than|below|<)\s*{num}\s*°?\s*{u}\b", q, re.I)
    if match:
        value = integer(match.group(1))
        return (None, float(value - 1)) if value is not None else None
    match = re.search(rf"(?:greater\s+than|higher\s+than|above|>)\s*{num}\s*°?\s*{u}\b", q, re.I)
    if match:
        value = integer(match.group(1))
        return (float(value + 1), None) if value is not None else None

    # Bare single number is exact only when no comparison/range language survives.
    if re.search(r"\b(?:less|lower|below|more|higher|above|greater|between|range|at\s+least|at\s+most)\b|[<>≤≥]", q, re.I):
        return None
    values = re.findall(rf"{num}\s*°?\s*{u}\b", q, re.I)
    if len(values) == 1:
        value = integer(values[0])
        return (float(value), float(value)) if value is not None else None
    return None


def _partition_exact(buckets: tuple[WeatherBucket, ...]) -> bool:
    if len(buckets) < 2:
        return False
    if any(bucket.lower is None and bucket.upper is None for bucket in buckets):
        return False
    if sum(bucket.lower is None for bucket in buckets) != 1 or sum(bucket.upper is None for bucket in buckets) != 1:
        return False
    ordered = sorted(buckets, key=lambda row: float("-inf") if row.lower is None else float(row.lower))
    if ordered[0].lower is not None or ordered[-1].upper is not None:
        return False
    for bucket in ordered:
        for value in (bucket.lower, bucket.upper):
            if value is not None and not float(value).is_integer():
                return False
        if bucket.lower is not None and bucket.upper is not None and bucket.lower > bucket.upper:
            return False
    for left, right in zip(ordered, ordered[1:]):
        if left.upper is None or right.lower is None or int(left.upper) + 1 != int(right.lower):
            return False
    return True


def _ordered_binary_identity(row: dict) -> tuple[tuple[str, str], ...] | None:
    outcomes = [str(value).strip() for value in _json_list(row.get("outcomes"))]
    tokens = [str(value).strip() for value in _json_list(row.get("clobTokenIds"))]
    if len(outcomes) != 2 or len(tokens) != 2 or len(set(tokens)) != 2 or any(not value for value in tokens):
        return None
    normalized = [value.lower() for value in outcomes]
    if sorted(normalized) != ["no", "yes"]:
        return None
    return tuple((tokens[index], normalized[index]) for index in range(2))


@dataclass(frozen=True, slots=True)
class V4CompiledWeatherEvent:
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
    authority_host: str | None
    observation_population: str | None
    precision: str | None
    fallback_policy: str | None
    finality_policy: str | None
    correction_policy: str | None
    semantic_digest: str

    def as_dict(self) -> dict:
        value = asdict(self)
        value["target_date"] = self.target_date.isoformat() if self.target_date else None
        return value


def compile_weather_event_v4(event: dict) -> V4CompiledWeatherEvent:
    if not isinstance(event, dict):
        raise WeatherIntegrityV4Error("EVENT_NOT_OBJECT")
    reasons: list[str] = []
    event_id = str(event.get("id") or "").strip()
    title = str(event.get("title") or "").strip()
    if not event_id:
        reasons.append("EVENT_ID_MISSING")
    family = _family_from_title(title)
    if family is None:
        reasons.append("FAMILY_UNRESOLVED_OR_CONFLICT")
        family = "unsupported"
    wanted_word = "highest" if family == DAILY_HIGH else "lowest" if family == DAILY_LOW else None
    opposite_word = "lowest" if wanted_word == "highest" else "highest" if wanted_word == "lowest" else None

    text = _full_text(event)
    explicit_dates = _full_dates(text)
    target: date | None = next(iter(explicit_dates)) if len(explicit_dates) == 1 else None
    if target is None:
        reasons.append("TARGET_DATE_UNRESOLVED_OR_CONFLICT")
    if target is not None:
        title_days = _yearless_month_days(title)
        if title_days and title_days != {(target.month, target.day)}:
            reasons.append("TITLE_RULE_DATE_CONFLICT")
        for row in _rows(event):
            child_dates = _full_dates(" ".join(str(row.get(key) or "") for key in ("question", "description", "resolutionSource")))
            if child_dates and child_dates != {target}:
                reasons.append("CHILD_TARGET_DATE_CONFLICT")

    # Bucket questions determine the unit actually being bought.  Every child must
    # state one common unit and settlement prose must not contradict it.
    question_units: set[str] = set()
    for row in _rows(event):
        units = _unit_tokens(str(row.get("question") or ""))
        if len(units) != 1:
            reasons.append("CHILD_UNIT_MISSING_OR_CONFLICT")
        question_units.update(units)
    unit = next(iter(question_units)) if len(question_units) == 1 else None
    if unit is None:
        reasons.append("UNIT_UNRESOLVED_OR_CONFLICT")
    declared_units: set[str] = set()
    settlement_text = " ".join(str(value or "") for value in [event.get("description"), *[row.get("description") for row in _rows(event)]])
    if re.search(r"\bwhole\s+degrees?\s+fahrenheit\b", settlement_text, re.I):
        declared_units.add("F")
    if re.search(r"\bwhole\s+degrees?\s+celsius\b", settlement_text, re.I):
        declared_units.add("C")
    if unit is None or declared_units != {unit}:
        reasons.append("WHOLE_DEGREE_PRECISION_UNPROVEN_OR_CONFLICT")

    # Exact authority URL/station proof.  Substring lookalikes never count.
    urls = _extract_urls(text)
    stations: set[str] = set()
    wrh_urls: list[str] = []
    for raw in urls:
        station, error = _wrh_site_from_url(raw)
        if error:
            reasons.append(error)
        if station:
            stations.add(station)
            wrh_urls.append(raw)
    if len(stations) != 1:
        reasons.append("WRH_STATION_UNRESOLVED_OR_CONFLICT")
    station_hint = next(iter(stations)) if len(stations) == 1 else None
    if not wrh_urls:
        reasons.append("NWS_WRH_AUTHORITY_URL_MISSING")

    # Child-level contradictions cannot be hidden by matching phrases elsewhere.
    for row in _rows(event):
        child_text = " ".join(str(row.get(key) or "") for key in ("question", "description", "resolutionSource"))
        if opposite_word and re.search(rf"\b{opposite_word}\s+(?:temperature|reading)\b", child_text, re.I):
            reasons.append("CHILD_STATISTIC_CONFLICT")
        child_units = _unit_tokens(str(row.get("question") or ""))
        if unit and child_units and child_units != {unit}:
            reasons.append("CHILD_UNIT_CONFLICT")
        child_sites: set[str] = set()
        for raw in _extract_urls(child_text):
            child_site, error = _wrh_site_from_url(raw)
            if error:
                reasons.append(error)
            if child_site:
                child_sites.add(child_site)
        if station_hint and child_sites and child_sites != {station_hint}:
            reasons.append("CHILD_STATION_CONFLICT")

    normalized_rules = _norm(settlement_text + " " + str(event.get("resolutionSource") or ""))
    if "obsolete" in normalized_rules or "superseded" in normalized_rules:
        reasons.append("RULE_TEXT_OBSOLETE_OR_SUPERSEDED")
    if wanted_word and f"{wanted_word} reading" not in normalized_rules:
        reasons.append("STATISTIC_RULE_UNPROVEN")
    hourly = "hourly data" in normalized_rules and "show hourly data" in normalized_rules
    if hourly:
        population = "WRH_HOURLY_DATA"
    elif '"temp" column' in normalized_rules and "all times on this day" in normalized_rules:
        population = "WRH_ALL_TIMES"
    else:
        population = None
        reasons.append("OBSERVATION_POPULATION_UNPROVEN")
    if not all(phrase in normalized_rules for phrase in (
        "weather underground daily observations table",
        "11:59 pm et",
        "day following the observation date",
    )):
        reasons.append("FALLBACK_POLICY_UNPROVEN")
    if "no data" not in normalized_rules or "lowest bracket" not in normalized_rules:
        reasons.append("NO_DATA_POLICY_UNPROVEN")
    if not all(phrase in normalized_rules for phrase in (
        "first data point for the following date",
        "whichever comes first",
    )):
        reasons.append("FINALITY_POLICY_UNPROVEN")
    if (
        "revisions" not in normalized_rules
        or "after which any alterations will not be considered" not in normalized_rules
        or not (
            "first datapoint for the following date" in normalized_rules
            or "first data point for the following date" in normalized_rules
        )
    ):
        reasons.append("CORRECTION_POLICY_UNPROVEN")

    buckets: list[WeatherBucket] = []
    seen_market: set[str] = set()
    seen_condition: set[str] = set()
    seen_token: set[str] = set()
    for row in _rows(event):
        mid = str(row.get("id") or "").strip()
        condition = str(row.get("conditionId") or "").strip()
        question = str(row.get("question") or "").strip()
        identity = _ordered_binary_identity(row)
        if not mid or mid in seen_market:
            reasons.append("MARKET_ID_MISSING_OR_DUPLICATE")
        if not condition or condition in seen_condition:
            reasons.append("CONDITION_ID_MISSING_OR_DUPLICATE")
        if identity is None:
            reasons.append("BINARY_TOKEN_OUTCOME_IDENTITY_INVALID")
            yes_token = no_token = None
        else:
            mapping = {label: token for token, label in identity}
            yes_token, no_token = mapping.get("yes"), mapping.get("no")
            for token, _label in identity:
                if token in seen_token:
                    reasons.append("TOKEN_ID_DUPLICATE_ACROSS_EVENT")
                seen_token.add(token)
        bounds = _strict_bucket_bounds(question, unit) if unit else None
        if bounds is None:
            reasons.append("BUCKET_GRAMMAR_UNSUPPORTED_OR_AMBIGUOUS")
            lower = upper = None
        else:
            lower, upper = bounds
        if wanted_word and opposite_word and re.search(rf"\b{opposite_word}\s+temperature\b", question, re.I):
            reasons.append("CHILD_STATISTIC_CONFLICT")
        trade_open = bool(
            _flag(row.get("active", True))
            and not _flag(row.get("closed", False))
            and _flag(row.get("acceptingOrders"))
            and _flag(row.get("enableOrderBook"))
            and condition and yes_token and no_token
        )
        buckets.append(WeatherBucket(
            market_id=mid,
            condition_id=condition,
            question=question,
            slug=str(row.get("slug") or ""),
            lower=lower,
            upper=upper,
            unit=unit,
            yes_token=yes_token,
            no_token=no_token,
            trade_open=trade_open,
        ))
        if mid:
            seen_market.add(mid)
        if condition:
            seen_condition.add(condition)

    bucket_tuple = tuple(buckets)
    partition = _partition_exact(bucket_tuple)
    if not partition:
        reasons.append("BUCKET_PARTITION_NOT_EXACT_WHOLE_DEGREE")
    if not bucket_tuple or not all(bucket.trade_open for bucket in bucket_tuple):
        reasons.append("ALL_BUCKETS_NOT_CURRENTLY_TRADE_OPEN")

    unique_reasons = tuple(dict.fromkeys(reasons))
    semantics_proven = not unique_reasons
    digest_payload = {
        "version": WEATHER_INTEGRITY_V4_VERSION,
        "event_id": event_id,
        "title": title,
        "family": family,
        "target_date": target.isoformat() if target else None,
        "unit": unit,
        "station": station_hint,
        "authority": SOURCE_NWS_WRH if wrh_urls else None,
        "authority_urls": sorted(wrh_urls),
        "observation_population": population,
        "precision": f"WHOLE_DEGREE_{unit}" if unit and declared_units == {unit} else None,
        "buckets": [bucket.as_dict() for bucket in bucket_tuple],
        "rejections": unique_reasons,
    }
    return V4CompiledWeatherEvent(
        compiler_version=WEATHER_INTEGRITY_V4_VERSION,
        event_id=event_id,
        event_slug=str(event.get("slug") or ""),
        title=title,
        family=family,
        target_date=target,
        unit=unit,
        source_family=SOURCE_NWS_WRH if wrh_urls else "UNSUPPORTED",
        source_urls=tuple(wrh_urls),
        station_hint=station_hint,
        buckets=bucket_tuple,
        partition_shape_complete=partition,
        exactly_one_outcome_proven=semantics_proven and partition,
        shadow_supported=semantics_proven and partition,
        financial_authority=False,
        rejection_reasons=unique_reasons,
        authority_host="weather.gov" if wrh_urls else None,
        observation_population=population,
        precision=f"WHOLE_DEGREE_{unit}" if unit and declared_units == {unit} else None,
        fallback_policy="WEATHER_UNDERGROUND_IF_WRH_UNAVAILABLE_BY_NEXT_DAY_2359_ET" if "FALLBACK_POLICY_UNPROVEN" not in unique_reasons else None,
        finality_policy="FIRST_FOLLOWING_DATE_DATAPOINT_OR_NEXT_DAY_2359_ET" if "FINALITY_POLICY_UNPROVEN" not in unique_reasons else None,
        correction_policy="ACCEPT_REVISIONS_UNTIL_FIRST_FOLLOWING_DATE_DATAPOINT" if "CORRECTION_POLICY_UNPROVEN" not in unique_reasons else None,
        semantic_digest=_sha(digest_payload),
    )


def require_certified_v4(event: dict) -> V4CompiledWeatherEvent:
    compiled = compile_weather_event_v4(event)
    if not compiled.shadow_supported or compiled.rejection_reasons:
        raise WeatherIntegrityV4Error("CONTRACT_SEMANTICS_UNPROVEN:" + ",".join(compiled.rejection_reasons[:8]))
    return compiled


def _identity_list(value: object) -> tuple[str, ...]:
    return tuple(str(item) for item in _json_list(value))


def _strict_market_identity(row: dict) -> tuple:
    return (
        str(row.get("question") or "").strip(),
        str(row.get("conditionId") or "").strip(),
        _identity_list(row.get("outcomes")),
        _identity_list(row.get("clobTokenIds")),
        str(row.get("slug") or "").strip(),
    )


def _nonempty_conflict(left: object, right: object) -> bool:
    if isinstance(left, (dict, list)) or isinstance(right, (dict, list)):
        return left not in (None, {}, []) and right not in (None, {}, []) and left != right
    a, b = str(left or "").strip(), str(right or "").strip()
    return bool(a and b and a != b)


def merge_event_v4(existing: dict, incoming: dict) -> dict:
    """Merge overlapping tag projections only when all material identities agree."""
    if str(existing.get("id") or "") != str(incoming.get("id") or ""):
        raise WeatherDiscoveryError("DUPLICATE_EVENT_IDENTITY_CONFLICT")
    for key in ("slug", "title", "description", "resolutionSource"):
        if _nonempty_conflict(existing.get(key), incoming.get(key)):
            raise WeatherDiscoveryError("DUPLICATE_EVENT_SEMANTIC_CONFLICT")
    merged = copy.deepcopy(existing)
    for key, value in incoming.items():
        if key == "markets":
            continue
        if key not in merged or merged.get(key) in (None, "", [], {}):
            merged[key] = copy.deepcopy(value)

    children: dict[str, dict] = {}
    order: list[str] = []
    for source in (existing.get("markets") or [], incoming.get("markets") or []):
        for row in source:
            if not isinstance(row, dict):
                continue
            mid = str(row.get("id") or "").strip()
            if not mid:
                raise WeatherDiscoveryError("MARKET_ID_MISSING")
            old = children.get(mid)
            if old is None:
                children[mid] = copy.deepcopy(row)
                order.append(mid)
                continue
            if _strict_market_identity(old) != _strict_market_identity(row):
                raise WeatherDiscoveryError("DUPLICATE_MARKET_IDENTITY_CONFLICT")
            for key in ("description", "resolutionSource", "active", "closed", "acceptingOrders", "enableOrderBook"):
                if _nonempty_conflict(old.get(key), row.get(key)):
                    raise WeatherDiscoveryError("DUPLICATE_MARKET_STATE_OR_SEMANTIC_CONFLICT")
            for key, value in row.items():
                if key not in old or old.get(key) in (None, "", [], {}):
                    old[key] = copy.deepcopy(value)
    merged["markets"] = [children[mid] for mid in order]
    return merged


class WeatherOnlyDiscoveryV4:
    """Weather-tag discovery that rejects conflicting projections instead of merging open."""

    def __init__(self, base: WeatherOnlyDiscovery | None = None) -> None:
        self.base = base or WeatherOnlyDiscovery()
        self._owns_base = base is None

    async def close(self) -> None:
        if self._owns_base:
            await self.base.close()

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
                events, next_cursor = await self.base._keyset_page(tag, cursor)
                pages += 1
                if pages > MAX_PAGES_PER_TAG:
                    raise WeatherDiscoveryError("TAG_PAGE_CAP")
                raw_event_hits += len(events)
                for event in events:
                    eid = str(event.get("id") or "").strip()
                    if not eid:
                        raise WeatherDiscoveryError("EVENT_ID_MISSING")
                    for row in event.get("markets") or []:
                        if not isinstance(row, dict):
                            continue
                        mid = str(row.get("id") or "").strip()
                        if not mid:
                            raise WeatherDiscoveryError("MARKET_ID_MISSING")
                        owner = market_owner.get(mid)
                        if owner is not None and owner != eid:
                            raise WeatherDiscoveryError("MARKET_PARENT_CONFLICT")
                        market_owner[mid] = eid
                    if eid in by_id:
                        duplicate_event_hits += 1
                        by_id[eid] = merge_event_v4(by_id[eid], event)
                    else:
                        by_id[eid] = copy.deepcopy(event)
                        order.append(eid)
                    if len(by_id) > MAX_EVENTS:
                        raise WeatherDiscoveryError("EVENT_CAP")
                    if len(market_owner) > MAX_MARKETS:
                        raise WeatherDiscoveryError("MARKET_CAP")
                if next_cursor is None:
                    break
                if next_cursor == cursor or next_cursor in seen_cursors:
                    raise WeatherDiscoveryError("CURSOR_REPEAT")
                seen_cursors.add(next_cursor)
                cursor = next_cursor
            pages_by_tag[tag] = pages
        finished = time.time()
        return WeatherDiscoverySnapshot(
            version=WEATHER_INTEGRITY_V4_VERSION + ":discovery",
            tags=cleaned,
            events=tuple(by_id[eid] for eid in order),
            pages_by_tag=pages_by_tag,
            raw_event_hits=raw_event_hits,
            duplicate_event_hits=duplicate_event_hits,
            unique_event_count=len(by_id),
            unique_market_count=len(market_owner),
            started_at=started,
            finished_at=finished,
        )
