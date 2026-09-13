from __future__ import annotations

"""Fail-closed P0 integrity guards for the weather LIVE PAPER experiment.

These checks are deliberately independent of the permissive inventory compiler.  A
compiled event can be useful for discovery while still being insufficient evidence
for a Telegram claim or a simulated fill.  This module establishes the stronger
boundary required by the 2026-09-13 adversarial review:

* exact supported temperature proposition grammar;
* coherent family/date/station/source identities across every child;
* an exhaustive whole-degree bucket lattice for NWS/WRH daily extremes;
* exact YES/NO token meaning at the CLOB boundary;
* provider + local receipt freshness for every quoted book;
* station/grid proximity and as-of checks for forecast evidence; and
* station-local expiry for future-day whole-day forecast decisions.

Unknown evidence always fails closed.  Nothing here grants real-money authority.
"""

import hashlib
import json
import math
import re
from datetime import date, datetime, time as dtime, timezone
from urllib.parse import parse_qs, urlparse
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .weather_only_clob import WeatherExecutionSnapshot
from .weather_only_contracts import (
    DAILY_HIGH,
    DAILY_LOW,
    SOURCE_NWS_WRH,
    CompiledWeatherEvent,
)
from .weather_only_forecast import EnsembleExtremeDistribution


WEATHER_P0_GUARD_VERSION = "weather_p0_integrity_guards_v1_fail_closed"
MAX_BOOK_RECEIPT_AGE_SECONDS = 10.0
MAX_BOOK_PROVIDER_AGE_SECONDS = 30.0
MAX_CLOCK_FUTURE_SKEW_SECONDS = 5.0
MAX_GEFS_GRID_DISTANCE_KM = 75.0
MAX_FORECAST_RECEIPT_AGE_SECONDS = 900.0
DISPATCH_MIDNIGHT_MARGIN_SECONDS = 30.0

_URL_RE = re.compile(r"https?://[^\s<>\"')]+", re.I)
_MONTHS = {
    "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6,
    "july": 7, "august": 8, "september": 9, "october": 10, "november": 11,
    "december": 12, "jan": 1, "feb": 2, "mar": 3, "apr": 4, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "sept": 9, "oct": 10, "nov": 11,
    "dec": 12,
}
_MONTH = (
    "January|February|March|April|May|June|July|August|September|October|"
    "November|December|Jan|Feb|Mar|Apr|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec"
)
_TITLE_MONTH_DAY = re.compile(
    rf"\b({_MONTH})\s+(\d{{1,2}})(?:st|nd|rd|th)?\b", re.I
)
_TITLE_DAY_MONTH = re.compile(
    rf"\b(\d{{1,2}})(?:st|nd|rd|th)?\s+({_MONTH})\b", re.I
)


class WeatherP0GuardError(RuntimeError):
    def __init__(self, code: str):
        self.code = str(code)
        super().__init__(self.code)


def _finite(value: object, code: str) -> float:
    if value is None or isinstance(value, bool):
        raise WeatherP0GuardError(code)
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        raise WeatherP0GuardError(code) from None
    if not math.isfinite(number):
        raise WeatherP0GuardError(code)
    return number


def _market_rows(event: dict) -> list[dict]:
    rows = event.get("markets") if isinstance(event, dict) else None
    return [row for row in (rows or []) if isinstance(row, dict)]


def _urls(*values: object) -> tuple[str, ...]:
    out: list[str] = []
    for value in values:
        for raw in _URL_RE.findall(str(value or "")):
            clean = raw.strip().rstrip(".,;")
            if clean and clean not in out:
                out.append(clean)
    return tuple(out)


def _all_contract_urls(event: dict) -> tuple[str, ...]:
    values: list[object] = [
        event.get("description"),
        event.get("resolutionSource"),
    ]
    for row in _market_rows(event):
        values.extend((row.get("question"), row.get("description"), row.get("resolutionSource")))
    return _urls(*values)


def _trusted_wrh_station(raw_url: str) -> str | None:
    try:
        parsed = urlparse(str(raw_url))
    except Exception:
        return None
    if parsed.scheme.lower() != "https":
        return None
    host = (parsed.hostname or "").lower()
    if host not in {"weather.gov", "www.weather.gov"}:
        return None
    if parsed.path.rstrip("/").lower() != "/wrh/timeseries":
        return None
    query = parse_qs(parsed.query, keep_blank_values=True)
    site_values = query.get("site") or query.get("SITE")
    if not isinstance(site_values, list) or len(site_values) != 1:
        return None
    station = str(site_values[0]).strip().upper()
    if not re.fullmatch(r"[A-Z0-9]{4}", station):
        return None
    return station


def _assert_source_identity(event: dict, compiled: CompiledWeatherEvent) -> str:
    if compiled.source_family != SOURCE_NWS_WRH:
        raise WeatherP0GuardError("P0_SOURCE_PROFILE_UNSUPPORTED")
    all_urls = _all_contract_urls(event)
    stations: set[str] = set()
    for raw in all_urls:
        trusted = _trusted_wrh_station(raw)
        if trusted:
            stations.add(trusted)
            continue
        # A look-alike WRH URL is more dangerous than an unrelated fallback URL.
        lowered = raw.lower()
        if "wrh/timeseries" in lowered or "weather.gov/wrh/timeseries" in lowered:
            raise WeatherP0GuardError("P0_NWS_SOURCE_URL_UNTRUSTED")
    if len(stations) != 1:
        raise WeatherP0GuardError("P0_NWS_STATION_IDENTITY_UNPROVEN")
    station = next(iter(stations))
    if station != str(compiled.station_hint or "").strip().upper():
        raise WeatherP0GuardError("P0_NWS_STATION_CONFLICT")
    return station


def _assert_title_date(title: str, target: date) -> None:
    assertions: set[tuple[int, int]] = set()
    for match in _TITLE_MONTH_DAY.finditer(str(title or "")):
        month = _MONTHS.get(match.group(1).lower())
        if month:
            assertions.add((month, int(match.group(2))))
    for match in _TITLE_DAY_MONTH.finditer(str(title or "")):
        month = _MONTHS.get(match.group(2).lower())
        if month:
            assertions.add((month, int(match.group(1))))
    if assertions and assertions != {(target.month, target.day)}:
        raise WeatherP0GuardError("P0_TITLE_TARGET_DATE_CONFLICT")


def _assert_no_semantic_override(event: dict, family: str) -> None:
    wanted = "highest" if family == DAILY_HIGH else "lowest"
    opposite = "lowest" if family == DAILY_HIGH else "highest"
    title = str(event.get("title") or "")
    if not re.search(rf"\b{wanted}\s+temperature\b", title, re.I):
        raise WeatherP0GuardError("P0_PARENT_STATISTIC_UNPROVEN")
    if re.search(rf"\b{opposite}\s+temperature\b", title, re.I):
        raise WeatherP0GuardError("P0_PARENT_STATISTIC_CONFLICT")

    all_rule_text = [str(event.get("description") or "")]
    for row in _market_rows(event):
        question = str(row.get("question") or "")
        if not re.search(rf"\b{wanted}\s+temperature\b", question, re.I):
            raise WeatherP0GuardError("P0_CHILD_STATISTIC_UNPROVEN")
        if re.search(rf"\b{opposite}\s+temperature\b", question, re.I):
            raise WeatherP0GuardError("P0_CHILD_STATISTIC_CONFLICT")
        desc = str(row.get("description") or "")
        all_rule_text.append(desc)
        if desc and re.search(rf"\b{opposite}\s+(?:reading|temperature)\b", desc, re.I):
            raise WeatherP0GuardError("P0_CHILD_STATISTIC_CONFLICT")
        normalized = re.sub(r"\s+", " ", desc).strip().lower()
        if normalized and "no data" in normalized and "lowest bracket" not in normalized:
            raise WeatherP0GuardError("P0_CHILD_FALLBACK_CONFLICT")
        if "regardless of" in normalized:
            raise WeatherP0GuardError("P0_CHILD_RULE_OVERRIDE")

    joined = re.sub(r"\s+", " ", " ".join(all_rule_text)).strip().lower()
    if re.search(r"\bobsolete\b|\bdo(?:es)?\s+not\s+apply\b", joined):
        raise WeatherP0GuardError("P0_RULE_TEXT_NEGATED_OR_OBSOLETE")


def _parse_supported_bucket(question: str, unit: str) -> tuple[float | None, float | None]:
    q = str(question or "").replace("–", "-").replace("—", "-")
    unit_re = re.escape(str(unit))
    # Strict/complement propositions are not silently projected onto an equality bucket.
    if re.search(r"\b(?:less\s+than|greater\s+than|more\s+than|not\s+|under\s+|over\s+)\b|[<>]", q, re.I):
        raise WeatherP0GuardError("P0_BUCKET_GRAMMAR_UNSUPPORTED")

    range_match = re.search(
        rf"\bbe\s+(-?\d+(?:\.\d+)?)\s*(?:-|to)\s*(-?\d+(?:\.\d+)?)\s*°?\s*{unit_re}\b",
        q,
        re.I,
    )
    if range_match:
        lo, hi = float(range_match.group(1)), float(range_match.group(2))
        if lo > hi:
            raise WeatherP0GuardError("P0_BUCKET_RANGE_INVALID")
        return lo, hi

    low_tail = re.search(
        rf"\bbe\s+(-?\d+(?:\.\d+)?)\s*°?\s*{unit_re}\s+or\s+(?:lower|below|less)\b",
        q,
        re.I,
    )
    if low_tail:
        return None, float(low_tail.group(1))

    high_tail = re.search(
        rf"\bbe\s+(-?\d+(?:\.\d+)?)\s*°?\s*{unit_re}\s+or\s+(?:higher|above|more)\b",
        q,
        re.I,
    )
    if high_tail:
        return float(high_tail.group(1)), None

    exact = re.search(
        rf"\bbe\s+(-?\d+(?:\.\d+)?)\s*°?\s*{unit_re}\b(?!\s+or\b)",
        q,
        re.I,
    )
    if exact:
        value = float(exact.group(1))
        return value, value
    raise WeatherP0GuardError("P0_BUCKET_GRAMMAR_UNSUPPORTED")


def _same_bound(a: float | None, b: float | None) -> bool:
    if a is None or b is None:
        return a is b
    return abs(float(a) - float(b)) <= 1e-9


def _assert_bucket_partition(event: dict, compiled: CompiledWeatherEvent) -> None:
    if compiled.unit not in {"F", "C"}:
        raise WeatherP0GuardError("P0_UNIT_UNSUPPORTED")
    by_market = {str(bucket.market_id): bucket for bucket in compiled.buckets}
    if len(by_market) != len(compiled.buckets) or len(by_market) < 2:
        raise WeatherP0GuardError("P0_BUCKET_IDENTITY_DUPLICATE")

    condition_ids: set[str] = set()
    tokens: set[str] = set()
    independently_parsed: list[tuple[float | None, float | None]] = []
    rows = _market_rows(event)
    if len(rows) != len(compiled.buckets):
        raise WeatherP0GuardError("P0_BUCKET_COUNT_MISMATCH")
    for row in rows:
        market_id = str(row.get("id") or "").strip()
        bucket = by_market.get(market_id)
        if bucket is None:
            raise WeatherP0GuardError("P0_BUCKET_MARKET_ID_MISMATCH")
        parsed = _parse_supported_bucket(str(row.get("question") or ""), str(compiled.unit))
        if not _same_bound(parsed[0], bucket.lower) or not _same_bound(parsed[1], bucket.upper):
            raise WeatherP0GuardError("P0_BUCKET_PROPOSITION_MISMATCH")
        for value in parsed:
            if value is not None and not float(value).is_integer():
                raise WeatherP0GuardError("P0_WHOLE_DEGREE_LATTICE_REQUIRED")
        independently_parsed.append(parsed)

        condition = str(bucket.condition_id or "").strip()
        yes = str(bucket.yes_token or "").strip()
        no = str(bucket.no_token or "").strip()
        if not condition or not yes or not no or yes == no:
            raise WeatherP0GuardError("P0_BUCKET_TOKEN_IDENTITY_INCOMPLETE")
        if condition in condition_ids or yes in tokens or no in tokens:
            raise WeatherP0GuardError("P0_BUCKET_IDENTITY_DUPLICATE")
        condition_ids.add(condition)
        tokens.update((yes, no))

    ordered = sorted(independently_parsed, key=lambda pair: float("-inf") if pair[0] is None else pair[0])
    if sum(lo is None for lo, _ in ordered) != 1 or sum(hi is None for _, hi in ordered) != 1:
        raise WeatherP0GuardError("P0_BUCKET_PARTITION_NOT_EXHAUSTIVE")
    if ordered[0][0] is not None or ordered[-1][1] is not None:
        raise WeatherP0GuardError("P0_BUCKET_PARTITION_NOT_EXHAUSTIVE")
    for left, right in zip(ordered, ordered[1:]):
        if left[1] is None or right[0] is None:
            raise WeatherP0GuardError("P0_BUCKET_PARTITION_OVERLAP")
        if abs((float(left[1]) + 1.0) - float(right[0])) > 1e-9:
            raise WeatherP0GuardError("P0_BUCKET_PARTITION_GAP_OR_OVERLAP")


def semantic_envelope(event: dict, compiled: CompiledWeatherEvent) -> dict:
    if not isinstance(event, dict):
        raise WeatherP0GuardError("P0_EVENT_INVALID")
    if compiled.family not in {DAILY_HIGH, DAILY_LOW}:
        raise WeatherP0GuardError("P0_FAMILY_UNSUPPORTED")
    if compiled.target_date is None:
        raise WeatherP0GuardError("P0_TARGET_DATE_UNRESOLVED")
    if not compiled.exactly_one_outcome_proven or not compiled.partition_shape_complete:
        raise WeatherP0GuardError("P0_RULE_PROOF_MISSING")
    station = _assert_source_identity(event, compiled)
    _assert_title_date(str(event.get("title") or ""), compiled.target_date)
    _assert_no_semantic_override(event, compiled.family)
    _assert_bucket_partition(event, compiled)
    return {
        "version": WEATHER_P0_GUARD_VERSION,
        "event_id": str(compiled.event_id),
        "station": station,
        "target_date": compiled.target_date.isoformat(),
        "family": compiled.family,
        "unit": compiled.unit,
        "source_family": compiled.source_family,
        "buckets": [
            {
                "market_id": str(bucket.market_id),
                "condition_id": str(bucket.condition_id),
                "question": str(bucket.question),
                "lower": bucket.lower,
                "upper": bucket.upper,
                "yes_token": str(bucket.yes_token),
                "no_token": str(bucket.no_token),
            }
            for bucket in compiled.buckets
        ],
        "financial_authority": False,
    }


def semantic_digest(event: dict, compiled: CompiledWeatherEvent) -> str:
    envelope = semantic_envelope(event, compiled)
    encoded = json.dumps(envelope, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _provider_timestamp_epoch(raw: object) -> float:
    value = _finite(raw, "P0_BOOK_PROVIDER_TIMESTAMP_INVALID")
    if value <= 0.0:
        raise WeatherP0GuardError("P0_BOOK_PROVIDER_TIMESTAMP_INVALID")
    # CLOB currently emits a Unix-like numeric string.  Accept common seconds,
    # milliseconds, microseconds or nanoseconds representations, then validate the
    # resulting epoch against the independent HTTP receipt time.
    if value >= 1e17:
        value /= 1e9
    elif value >= 1e14:
        value /= 1e6
    elif value >= 1e11:
        value /= 1e3
    return value


def validate_book_freshness(book, *, decision_time: float) -> dict:
    now = _finite(decision_time, "P0_DECISION_TIME_INVALID")
    received = _finite(getattr(book, "received_at", None), "P0_BOOK_RECEIPT_MISSING")
    receipt_age = now - received
    if receipt_age < -MAX_CLOCK_FUTURE_SKEW_SECONDS or receipt_age > MAX_BOOK_RECEIPT_AGE_SECONDS:
        raise WeatherP0GuardError("P0_BOOK_RECEIPT_STALE")
    provider = _provider_timestamp_epoch(getattr(book, "timestamp", None))
    provider_age_at_receipt = received - provider
    if (
        provider_age_at_receipt < -MAX_CLOCK_FUTURE_SKEW_SECONDS
        or provider_age_at_receipt > MAX_BOOK_PROVIDER_AGE_SECONDS
    ):
        raise WeatherP0GuardError("P0_BOOK_PROVIDER_TIMESTAMP_STALE")
    return {
        "provider_timestamp": provider,
        "received_at": received,
        "receipt_age_seconds": receipt_age,
        "provider_age_at_receipt_seconds": provider_age_at_receipt,
        "book_hash": getattr(book, "book_hash", None),
    }


def validate_clob_snapshot(
    compiled: CompiledWeatherEvent,
    snapshot: WeatherExecutionSnapshot,
    *,
    decision_time: float,
) -> dict:
    if snapshot.event_id != compiled.event_id or snapshot.exact_clob is not True:
        raise WeatherP0GuardError("P0_CLOB_EVENT_IDENTITY_MISMATCH")
    conditions_seen: set[str] = set()
    tokens_seen: set[str] = set()
    books_evidence: dict[str, dict] = {}
    for bucket in compiled.buckets:
        if not bucket.trade_open:
            continue
        condition = str(bucket.condition_id or "")
        params = snapshot.parameters.get(condition)
        if params is None or str(params.condition_id) != condition:
            raise WeatherP0GuardError("P0_CLOB_CONDITION_IDENTITY_MISMATCH")
        if condition in conditions_seen:
            raise WeatherP0GuardError("P0_CLOB_CONDITION_DUPLICATE")
        conditions_seen.add(condition)
        actual = {str(token): str(outcome).strip().lower() for token, outcome in params.token_outcomes}
        expected = {str(bucket.yes_token): "yes", str(bucket.no_token): "no"}
        if actual != expected:
            raise WeatherP0GuardError("P0_CLOB_OUTCOME_MAPPING_MISMATCH")
        if float(params.minimum_order_size) <= 0.0 or float(params.minimum_tick_size) <= 0.0:
            raise WeatherP0GuardError("P0_CLOB_EXECUTION_CONSTRAINT_INVALID")
        for token in expected:
            if token in tokens_seen:
                raise WeatherP0GuardError("P0_CLOB_TOKEN_DUPLICATE")
            tokens_seen.add(token)
            book = snapshot.books.get(token)
            if book is None:
                raise WeatherP0GuardError("P0_CLOB_BOOK_MISSING")
            books_evidence[token] = validate_book_freshness(book, decision_time=decision_time)
    if set(snapshot.books) != tokens_seen:
        raise WeatherP0GuardError("P0_CLOB_BOOK_SET_MISMATCH")
    return {
        "version": WEATHER_P0_GUARD_VERSION,
        "event_id": compiled.event_id,
        "conditions": sorted(conditions_seen),
        "books": books_evidence,
        "snapshot_started_at": snapshot.started_at,
        "snapshot_finished_at": snapshot.finished_at,
        "financial_authority": False,
    }


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2.0) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2.0) ** 2
    return 6371.0088 * 2.0 * math.atan2(math.sqrt(a), math.sqrt(max(0.0, 1.0 - a)))


def validate_forecast_distribution(
    compiled: CompiledWeatherEvent,
    distribution: EnsembleExtremeDistribution,
    *,
    timezone_name: str,
    decision_time: float,
) -> dict:
    now = _finite(decision_time, "P0_DECISION_TIME_INVALID")
    if (
        distribution.station != str(compiled.station_hint or "").strip().upper()
        or distribution.target_date != compiled.target_date
        or distribution.family != compiled.family
        or distribution.unit != compiled.unit
        or distribution.timezone != str(timezone_name)
    ):
        raise WeatherP0GuardError("P0_FORECAST_SEMANTIC_IDENTITY_MISMATCH")
    receipt = _finite(distribution.received_at, "P0_FORECAST_RECEIPT_MISSING")
    receipt_age = now - receipt
    if receipt <= 0.0 or receipt_age < -MAX_CLOCK_FUTURE_SKEW_SECONDS or receipt_age > MAX_FORECAST_RECEIPT_AGE_SECONDS:
        raise WeatherP0GuardError("P0_FORECAST_RECEIPT_STALE")
    distance = _haversine_km(
        float(distribution.requested_latitude),
        float(distribution.requested_longitude),
        float(distribution.resolved_latitude),
        float(distribution.resolved_longitude),
    )
    if not math.isfinite(distance) or distance > MAX_GEFS_GRID_DISTANCE_KM:
        raise WeatherP0GuardError("P0_FORECAST_GRID_MISMATCH")
    return {
        "version": WEATHER_P0_GUARD_VERSION,
        "adapter": distribution.adapter,
        "provider_model": distribution.provider_model,
        "station": distribution.station,
        "target_date": distribution.target_date.isoformat(),
        "family": distribution.family,
        "unit": distribution.unit,
        "timezone": distribution.timezone,
        "requested_grid": [distribution.requested_latitude, distribution.requested_longitude],
        "resolved_grid": [distribution.resolved_latitude, distribution.resolved_longitude],
        "grid_distance_km": distance,
        "received_at": receipt,
        "receipt_age_seconds": receipt_age,
        "evidence_sha256": distribution.evidence_sha256,
        # The current Open-Meteo daily adapter does not expose a model initialization
        # timestamp.  Preserve that limitation explicitly rather than calling the run fresh.
        "model_run_initialization_proven": False,
        "model_run_age_seconds": None,
        "financial_authority": False,
    }


def future_day_expiry_epoch(*, target_date: date, timezone_name: str) -> float:
    try:
        zone = ZoneInfo(str(timezone_name))
    except (ZoneInfoNotFoundError, ValueError, TypeError):
        raise WeatherP0GuardError("P0_STATION_TIMEZONE_INVALID") from None
    local_midnight = datetime.combine(target_date, dtime.min, tzinfo=zone)
    return local_midnight.astimezone(timezone.utc).timestamp()


def assert_future_day_dispatch(
    *,
    target_date: date,
    timezone_name: str,
    now_epoch: float,
    margin_seconds: float = DISPATCH_MIDNIGHT_MARGIN_SECONDS,
) -> float:
    now = _finite(now_epoch, "P0_DECISION_TIME_INVALID")
    margin = _finite(margin_seconds, "P0_DISPATCH_MARGIN_INVALID")
    if margin < 0.0:
        raise WeatherP0GuardError("P0_DISPATCH_MARGIN_INVALID")
    expiry = future_day_expiry_epoch(target_date=target_date, timezone_name=timezone_name)
    if now >= expiry - margin:
        raise WeatherP0GuardError("P0_FUTURE_DAY_DECISION_EXPIRED")
    return expiry
