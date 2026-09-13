from __future__ import annotations

"""Weather LIVE PAPER v4 corrective runtime.

This release is deliberately fail-closed.  It repairs the paper-experiment boundary
before any attempt is made to promote same-day conditioned forecasts:

* legacy/unreconstructable paper history is excluded from validated performance;
* complete-set structural alerts are disabled until their common resolution function
  is proved by a stricter compiler;
* future-day raw GEFS is revalidated at the actual dispatch boundary, including
  station-local expiry, exact YES/NO token meaning and a fresh exact CLOB quote;
* paper stake/fill assumptions are frozen with the decision, minimum order/tick and
  quote age are enforced, and one captured book cannot be consumed repeatedly;
* ambiguous Telegram transport becomes DELIVERY_UNCERTAIN rather than a blind retry;
* settlement requires an explicitly resolved/settled market and a coherent final
  binary payout vector rather than merely ``closed=True``;
* settlement scanning rotates fairly and lookup failures degrade health; and
* Telegram commands use plain operator language for open, won/lost, no-fill,
  skipped, uncertain and excluded records.

The observation-conditioned three-layer model lives in
``weather_only_conditioned_extremes``.  V4 keeps same-day directional emission OFF
until exact official-observation ingestion and remaining-hours ensemble coverage
pass their own acceptance gates.  No wallet/order/cancel API is imported here.
"""

import argparse
import asyncio
import hashlib
import html
import json
import math
import re
import sqlite3
import time
from datetime import date, datetime, time as datetime_time, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlparse
from zoneinfo import ZoneInfo

import httpx

from .config import settings
from .settlement import _json_list
from .weather_only_clob import (
    MAX_BOOK_AGE_SECONDS,
    WeatherCLOBError,
    conservative_taker_fee_per_share,
)
from .weather_only_contracts import DAILY_HIGH, DAILY_LOW, compile_weather_event
from .weather_only_forecast import (
    FORECAST_MAPPING_POLICY if False else EnsembleMappingPolicy,  # type: ignore[comparison-overlap]
)
from .weather_only_forecast import WeatherForecastError, map_ensemble_to_contract_buckets
from .weather_only_live_paper import (
    DEFAULT_FORECAST_CACHE_SECONDS,
    DEFAULT_FORECAST_RAW_GAP_MIN,
    DEFAULT_INTERVAL_SECONDS,
    DEFAULT_MAX_FORECAST_EVENTS,
    FORECAST_MAPPING_POLICY,
    MODE,
    SUMMARY_INTERVAL_SECONDS,
    PaperTelegram,
    WeatherLivePaperError,
    _atomic_json,
    _event_id,
    _event_link,
    _event_title,
)
from .weather_only_live_paper_v2 import DEFAULT_PAPER_STAKE_USD
from .weather_only_live_paper_v3 import (
    QUARANTINED_STATUS,
    GuardedWeatherPaperPositionStore,
    WeatherLivePaperV3Service,
)
from .weather_only_paper_control import PublicGammaSettlementClient, WeatherPaperCommandController
from .weather_only_paper_positions import WeatherPaperPositionError, _json, _payload
from .weather_only_rules import apply_rule_authority, compile_temperature_rule_authority
from .weather_only_station_metadata import WeatherStationMetadataError


WEATHER_LIVE_PAPER_V4_VERSION = "weather_live_paper_v4_fail_closed_corrective_protocol"
PAPER_EXECUTION_PROTOCOL_V4 = "weather_paper_execution_v4_frozen_quote_capacity"
PAPER_POSITION_VERSION_V4 = "weather_paper_position_v4_validated_frozen_execution"
PAPER_SETTLEMENT_VERSION_V4 = "weather_paper_settlement_v4_finality_vector_exact_token"
V4_LEGACY_REASON = "PRE_V4_OR_UNRECONSTRUCTABLE_HISTORY"
QUOTE_DECISION_TTL_SECONDS = 20.0
LOCAL_MIDNIGHT_MARGIN_SECONDS = 5.0
PROVIDER_BOOK_MAX_SKEW_SECONDS = 30.0
GRID_MAX_DISTANCE_KM = 100.0
STATUS_MAX_AGE_SECONDS = 600.0
SETTLEMENT_PAGE_SIZE = 200

_ALLOWED_WRH_HOSTS = {"weather.gov", "www.weather.gov"}
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


class V4InvariantError(RuntimeError):
    def __init__(self, code: str):
        self.code = str(code)
        super().__init__(self.code)


class DeliveryUncertain(WeatherLivePaperError):
    pass


def _finite(value: object, code: str) -> float:
    if value is None or isinstance(value, bool):
        raise V4InvariantError(code)
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        raise V4InvariantError(code) from None
    if not math.isfinite(number):
        raise V4InvariantError(code)
    return number


def _canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False)


def _sha(value: object) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _provider_book_epoch(raw: object) -> float:
    text = str(raw or "").strip()
    if not text:
        raise V4InvariantError("V4_BOOK_PROVIDER_TIMESTAMP_MISSING")
    try:
        value = float(text)
    except (TypeError, ValueError, OverflowError):
        raise V4InvariantError("V4_BOOK_PROVIDER_TIMESTAMP_INVALID") from None
    if not math.isfinite(value) or value <= 0:
        raise V4InvariantError("V4_BOOK_PROVIDER_TIMESTAMP_INVALID")
    # Current CLOB payloads use millisecond epochs; accept documented second epochs
    # as well, but reject arbitrary tiny counters.
    if value >= 100_000_000_000:
        value /= 1000.0
    if value < 1_000_000_000:
        raise V4InvariantError("V4_BOOK_PROVIDER_TIMESTAMP_INVALID")
    return value


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    from math import asin, cos, radians, sin, sqrt

    r = 6371.0088
    p1, p2 = radians(lat1), radians(lat2)
    dlat = radians(lat2 - lat1)
    dlon = radians(lon2 - lon1)
    a = sin(dlat / 2) ** 2 + cos(p1) * cos(p2) * sin(dlon / 2) ** 2
    return 2 * r * asin(sqrt(a))


def _title_date_matches(event: dict, target: date) -> bool:
    title = str(event.get("title") or "")
    matches = list(_TITLE_DATE_RE.finditer(title))
    if not matches:
        return False
    for match in matches:
        month = _MONTHS.get(match.group(1).lower())
        day = int(match.group(2))
        if month != target.month or day != target.day:
            return False
    return True


def _strict_source_station(event: dict, expected_station: str) -> bool:
    texts = [
        str(event.get("title") or ""),
        str(event.get("description") or ""),
        str(event.get("resolutionSource") or ""),
    ]
    for row in event.get("markets") or []:
        if isinstance(row, dict):
            texts.extend((
                str(row.get("question") or ""),
                str(row.get("description") or ""),
                str(row.get("resolutionSource") or ""),
            ))
    stations: set[str] = set()
    trusted = 0
    for raw_url in _URL_RE.findall(" ".join(texts)):
        url = raw_url.rstrip(".,;]")
        parsed = urlparse(url)
        host = (parsed.hostname or "").lower()
        path = parsed.path.rstrip("/")
        looks_like_wrh = "weather.gov/wrh/timeseries" in url.lower() or path.lower().endswith("/wrh/timeseries")
        if not looks_like_wrh:
            continue
        if parsed.scheme.lower() != "https" or host not in _ALLOWED_WRH_HOSTS or path.lower() != "/wrh/timeseries":
            return False
        sites = [str(v).strip().upper() for v in parse_qs(parsed.query).get("site", []) if str(v).strip()]
        if len(sites) != 1 or len(sites[0]) != 4 or not sites[0].isalnum():
            return False
        stations.add(sites[0])
        trusted += 1
    return trusted > 0 and stations == {str(expected_station).upper()}


def _question_semantics_supported(question: str, family: str) -> bool:
    text = " ".join(str(question or "").strip().split())
    low = text.lower()
    if any(term in low for term in ("less than", "greater than", "not ", "except ")):
        return False
    opposite = "lowest temperature" if family == DAILY_HIGH else "highest temperature"
    if opposite in low:
        return False
    # Supported recurring grammar is deliberately narrow: inclusive tails,
    # inclusive ranges and exact whole-degree buckets.
    number = r"-?\d+(?:\.0+)?"
    unit = r"(?:°\s*[FC]|\s+degrees?\s+[FC]|\s*[FC])"
    patterns = (
        rf".*\b{number}\s*{unit}\s+or\s+lower\??$",
        rf".*\b{number}\s*{unit}\s+or\s+higher\??$",
        rf".*\b{number}\s*(?:-|–|to)\s*{number}\s*{unit}\??$",
        rf".*\b(?:be\s+)?{number}\s*{unit}\??$",
    )
    return any(re.fullmatch(pattern, text, re.I) for pattern in patterns)


def _strict_partition(compiled) -> bool:
    buckets = tuple(compiled.buckets or ())
    if not buckets:
        return False
    finite_points: list[int] = []
    seen_market: set[str] = set()
    seen_condition: set[str] = set()
    seen_tokens: set[str] = set()
    for bucket in buckets:
        if not bucket.market_id or bucket.market_id in seen_market:
            return False
        if not bucket.condition_id or bucket.condition_id in seen_condition:
            return False
        if not bucket.yes_token or not bucket.no_token or bucket.yes_token == bucket.no_token:
            return False
        if bucket.yes_token in seen_tokens or bucket.no_token in seen_tokens:
            return False
        seen_market.add(bucket.market_id)
        seen_condition.add(bucket.condition_id)
        seen_tokens.update((bucket.yes_token, bucket.no_token))
        for bound in (bucket.lower, bucket.upper):
            if bound is not None:
                value = float(bound)
                if not math.isfinite(value) or not value.is_integer():
                    return False
                finite_points.append(int(value))
    if not finite_points:
        return False
    start = min(finite_points) - 3
    end = max(finite_points) + 3
    for value in range(start, end + 1):
        matches = [
            bucket for bucket in buckets
            if (bucket.lower is None or value >= bucket.lower)
            and (bucket.upper is None or value <= bucket.upper)
        ]
        if len(matches) != 1:
            return False
    return True


def strict_compile_weather_event(event: dict):
    raw = compile_weather_event(event)
    if raw.family not in {DAILY_HIGH, DAILY_LOW}:
        raise V4InvariantError("V4_CONTRACT_FAMILY_UNSUPPORTED")
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
        raise V4InvariantError("V4_CONTRACT_AUTHORITY_UNPROVEN")
    if not _title_date_matches(event, compiled.target_date):
        raise V4InvariantError("V4_CONTRACT_TITLE_DATE_MISMATCH")
    if not _strict_source_station(event, str(compiled.station_hint)):
        raise V4InvariantError("V4_CONTRACT_SOURCE_STATION_UNPROVEN")
    markets = [row for row in event.get("markets") or [] if isinstance(row, dict)]
    if len(markets) != len(compiled.buckets):
        raise V4InvariantError("V4_CONTRACT_CHILD_COUNT_MISMATCH")
    for row in markets:
        if not _question_semantics_supported(str(row.get("question") or ""), compiled.family):
            raise V4InvariantError("V4_CONTRACT_BUCKET_GRAMMAR_UNSUPPORTED")
        child_text = " ".join((str(row.get("question") or ""), str(row.get("description") or ""))).lower()
        opposite = "lowest temperature" if compiled.family == DAILY_HIGH else "highest temperature"
        if opposite in child_text:
            raise V4InvariantError("V4_CONTRACT_CHILD_STATISTIC_CONFLICT")
    if not _strict_partition(compiled):
        raise V4InvariantError("V4_CONTRACT_PARTITION_UNPROVEN")
    return compiled


def _semantic_digest(compiled, metadata) -> str:
    return _sha({
        "event_id": compiled.event_id,
        "station": str(compiled.station_hint).upper(),
        "target_date": compiled.target_date.isoformat(),
        "family": compiled.family,
        "unit": compiled.unit,
        "timezone": str(metadata.timezone),
        "latitude": float(metadata.latitude),
        "longitude": float(metadata.longitude),
        "mapping_policy": FORECAST_MAPPING_POLICY.policy_id,
        "buckets": [
            {
                "market_id": bucket.market_id,
                "condition_id": bucket.condition_id,
                "yes": bucket.yes_token,
                "no": bucket.no_token,
                "lower": bucket.lower,
                "upper": bucket.upper,
            }
            for bucket in compiled.buckets
        ],
    })


def _validate_exact_snapshot(compiled, snapshot) -> None:
    expected_tokens: set[str] = set()
    expected_condition_by_token: dict[str, str] = {}
    for bucket in compiled.buckets:
        expected_tokens.update((bucket.yes_token, bucket.no_token))
        expected_condition_by_token[bucket.yes_token] = bucket.condition_id
        expected_condition_by_token[bucket.no_token] = bucket.condition_id
        info = snapshot.parameters.get(bucket.condition_id)
        if info is None:
            raise V4InvariantError("V4_CLOB_CONDITION_MISSING")
        outcome_map = {str(token): str(outcome).strip().lower() for token, outcome in info.token_outcomes}
        if outcome_map.get(bucket.yes_token) != "yes" or outcome_map.get(bucket.no_token) != "no":
            raise V4InvariantError("V4_CLOB_OUTCOME_MEANING_MISMATCH")
    if set(snapshot.books) != expected_tokens:
        raise V4InvariantError("V4_CLOB_BOOK_SET_MISMATCH")
    now = time.time()
    for token, book in snapshot.books.items():
        if book.received_at is None:
            raise V4InvariantError("V4_BOOK_RECEIPT_MISSING")
        received = float(book.received_at)
        if now - received > MAX_BOOK_AGE_SECONDS or received - now > 2.0:
            raise V4InvariantError("V4_BOOK_RECEIPT_STALE")
        provider = _provider_book_epoch(book.timestamp)
        if abs(received - provider) > PROVIDER_BOOK_MAX_SKEW_SECONDS:
            raise V4InvariantError("V4_BOOK_PROVIDER_TIMESTAMP_STALE")
        if getattr(book, "condition_id", None) not in (None, "", expected_condition_by_token[token]):
            raise V4InvariantError("V4_BOOK_CONDITION_MISMATCH")
        if not str(book.book_hash or "").strip():
            raise V4InvariantError("V4_BOOK_HASH_MISSING")


class CorrectivePaperTelegram(PaperTelegram):
    """Paper Telegram transport that never blindly retries an ambiguous transport."""

    async def send_html(self, text: str, *, url: str | None = None, expires_at: float | None = None) -> int:
        if len(text) > 3900:
            raise WeatherLivePaperError("PAPER_TELEGRAM_MESSAGE_TOO_LONG")
        payload: dict[str, object] = {
            "chat_id": self.chat_id,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }
        if isinstance(url, str) and url.startswith("https://"):
            payload["reply_markup"] = {"inline_keyboard": [[{"text": "OPEN POLYMARKET", "url": url}]]}
        endpoint = f"https://api.telegram.org/bot{self.token}/sendMessage"
        for attempt in range(3):
            if expires_at is not None and time.time() >= float(expires_at):
                raise WeatherLivePaperError("PAPER_TELEGRAM_DECISION_EXPIRED")
            try:
                response = await self.http.post(endpoint, json=payload)
            except httpx.RequestError:
                # We cannot prove whether Telegram accepted the request before the
                # local transport failed.  Do not retry and risk a duplicate.
                raise DeliveryUncertain("PAPER_TELEGRAM_DELIVERY_UNCERTAIN") from None
            if response.status_code == 429:
                try:
                    retry_after = float((response.json().get("parameters") or {}).get("retry_after", 1.0))
                except Exception:
                    retry_after = 1.0
                if attempt >= 2:
                    raise WeatherLivePaperError("PAPER_TELEGRAM_RATE_LIMITED")
                wait = min(15.0, max(1.0, retry_after))
                if expires_at is not None and time.time() + wait >= float(expires_at):
                    raise WeatherLivePaperError("PAPER_TELEGRAM_DECISION_EXPIRED")
                await asyncio.sleep(wait)
                continue
            if response.status_code >= 500:
                # A server error is also not a sufficient exactly-once receipt.
                raise DeliveryUncertain("PAPER_TELEGRAM_SERVER_UNCERTAIN")
            if response.status_code >= 400:
                raise WeatherLivePaperError(f"PAPER_TELEGRAM_HTTP_{response.status_code}")
            try:
                body = response.json()
            except ValueError:
                raise DeliveryUncertain("PAPER_TELEGRAM_RESPONSE_UNCERTAIN") from None
            result = body.get("result") if isinstance(body, dict) and body.get("ok") is True else None
            message_id = result.get("message_id") if isinstance(result, dict) else None
            if isinstance(message_id, bool) or not isinstance(message_id, int) or message_id <= 0:
                raise DeliveryUncertain("PAPER_TELEGRAM_RECEIPT_UNCERTAIN")
            return int(message_id)
        raise WeatherLivePaperError("PAPER_TELEGRAM_RETRY_EXHAUSTED")


class CorrectiveWeatherPaperPositionStore(GuardedWeatherPaperPositionStore):
    def __init__(self, path: str | Path) -> None:
        super().__init__(path)
        self._migrate_v4()

    def _migrate_v4(self) -> None:
        additions = {
            "decision_id": "TEXT",
            "execution_protocol": "TEXT",
            "decision_expires_at": "REAL",
            "paper_fill_at": "REAL",
            "validation_state": "TEXT NOT NULL DEFAULT 'UNVERIFIED'",
            "capacity_key": "TEXT",
            "settlement_notification_state": "TEXT",
        }
        with self._conn() as db:
            existing = {str(row["name"]) for row in db.execute("PRAGMA table_info(weather_paper_positions)")}
            for name, ddl in additions.items():
                if name not in existing:
                    db.execute(f"ALTER TABLE weather_paper_positions ADD COLUMN {name} {ddl}")
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS weather_paper_capacity_usage (
                    capacity_key TEXT PRIMARY KEY,
                    visible_units REAL NOT NULL,
                    consumed_units REAL NOT NULL,
                    first_signal_id INTEGER NOT NULL,
                    last_signal_id INTEGER NOT NULL,
                    updated_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS weather_paper_decisions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    decision_id TEXT NOT NULL,
                    event_id TEXT NOT NULL,
                    market_id TEXT,
                    side TEXT,
                    outcome TEXT NOT NULL,
                    reason TEXT,
                    created_at REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_weather_paper_decision_outcome
                    ON weather_paper_decisions(outcome, created_at);
                """
            )

    def set_signal_status(self, signal_id: int, status: str) -> None:
        with self._conn() as db:
            cur = db.execute("UPDATE weather_paper_signals SET status=? WHERE id=?", (str(status), int(signal_id)))
            if cur.rowcount != 1:
                raise WeatherPaperPositionError("PAPER_SIGNAL_STATUS_NOT_FOUND")

    def record_decision(
        self,
        *,
        decision_id: str,
        event_id: str,
        market_id: str | None,
        side: str | None,
        outcome: str,
        reason: str | None = None,
    ) -> None:
        with self._conn() as db:
            db.execute(
                "INSERT INTO weather_paper_decisions(decision_id,event_id,market_id,side,outcome,reason,created_at) "
                "VALUES(?,?,?,?,?,?,?)",
                (str(decision_id), str(event_id), market_id, side, str(outcome), reason, time.time()),
            )

    def decision_counts(self) -> dict:
        with self._conn() as db:
            rows = db.execute(
                "SELECT outcome,COUNT(*) AS n FROM weather_paper_decisions GROUP BY outcome"
            ).fetchall()
        return {str(row["outcome"]): int(row["n"]) for row in rows}

    def ensure_sent_positions(self, target_stake_usd: float) -> list[dict]:
        # Recovery is intentionally limited to decisions that already froze the v4
        # protocol.  Pre-v4 Telegram receipts can never acquire a new stake/fill rule
        # retrospectively on restart.
        with self._conn() as db:
            rows = [dict(row) for row in db.execute(
                """
                SELECT s.* FROM weather_paper_signals s
                LEFT JOIN weather_paper_positions p ON p.signal_id=s.id
                WHERE s.telegram_message_id IS NOT NULL AND p.id IS NULL
                ORDER BY s.id
                """
            )]
        created: list[dict] = []
        for row in rows:
            payload = _payload(row.get("payload_json"))
            if payload.get("paper_execution_protocol_version") != PAPER_EXECUTION_PROTOCOL_V4:
                continue
            if str(row.get("status") or "") != "ACKNOWLEDGED":
                continue
            position = self.ensure_position_for_signal(int(row["id"]), target_stake_usd)
            if position is not None:
                created.append(position)
        return created

    def ensure_position_for_signal(self, signal_id: int, target_stake_usd: float) -> dict | None:
        sid = int(signal_id)
        with self._conn() as db:
            existing = db.execute("SELECT * FROM weather_paper_positions WHERE signal_id=?", (sid,)).fetchone()
            if existing:
                return self._decode_position(dict(existing))
            signal_row = db.execute("SELECT * FROM weather_paper_signals WHERE id=?", (sid,)).fetchone()
        if not signal_row:
            return None
        signal = dict(signal_row)
        payload = _payload(signal.get("payload_json"))
        if payload.get("paper_execution_protocol_version") != PAPER_EXECUTION_PROTOCOL_V4:
            return None
        if str(signal.get("status") or "") != "ACKNOWLEDGED":
            return None

        frozen_stake = _finite(payload.get("paper_target_stake_usd"), "V4_PAPER_STAKE_MISSING")
        if frozen_stake <= 0.0:
            raise WeatherPaperPositionError("V4_PAPER_STAKE_INVALID")
        spec = super()._position_spec(signal, frozen_stake)
        if spec is None or spec.get("position_kind") != "DIRECTIONAL":
            return None

        quote_at = _finite(payload.get("quote_observed_at"), "V4_QUOTE_TIME_MISSING")
        fill_at = _finite(payload.get("paper_fill_at"), "V4_FILL_TIME_MISSING")
        expires_at = _finite(payload.get("decision_expires_at"), "V4_DECISION_EXPIRY_MISSING")
        sent_at = _finite(signal.get("telegram_sent_at"), "V4_TELEGRAM_TIME_MISSING")
        if quote_at > fill_at or fill_at - quote_at > MAX_BOOK_AGE_SECONDS or fill_at > expires_at or sent_at > expires_at:
            raise WeatherPaperPositionError("V4_PAPER_FILL_EXPIRED")

        ask = _finite(payload.get("ask"), "V4_ASK_MISSING")
        tick = _finite(payload.get("minimum_tick_size"), "V4_TICK_MISSING")
        minimum = _finite(payload.get("minimum_order_size"), "V4_MIN_ORDER_MISSING")
        if ask <= 0.0 or tick <= 0.0 or minimum < 0.0:
            raise WeatherPaperPositionError("V4_EXECUTION_CONSTRAINT_INVALID")
        tick_units = ask / tick
        if abs(tick_units - round(tick_units)) > 1e-6:
            raise WeatherPaperPositionError("V4_ASK_OFF_TICK")

        decision_id = str(payload.get("decision_id") or "").strip()
        book_hash = str(payload.get("book_hash") or "").strip()
        condition_id = str(payload.get("condition_id") or "").strip()
        token_id = str(signal.get("token_id") or "").strip()
        side = str(signal.get("side") or "").strip().upper()
        if not decision_id or not book_hash or not condition_id or not token_id or side not in {"YES", "NO"}:
            raise WeatherPaperPositionError("V4_DECISION_IDENTITY_INVALID")
        capacity_key = _sha({"token": token_id, "book_hash": book_hash, "quote_at": quote_at, "ask": ask})

        visible = float(spec["visible_units"])
        cost = float(spec["entry_cost_per_unit"])
        requested = frozen_stake / cost
        with self._conn() as db:
            existing = db.execute("SELECT * FROM weather_paper_positions WHERE signal_id=?", (sid,)).fetchone()
            if existing:
                return self._decode_position(dict(existing))
            cap = db.execute(
                "SELECT visible_units,consumed_units FROM weather_paper_capacity_usage WHERE capacity_key=?",
                (capacity_key,),
            ).fetchone()
            consumed = float(cap["consumed_units"]) if cap else 0.0
            if cap is not None and abs(float(cap["visible_units"]) - visible) > 1e-9:
                raise WeatherPaperPositionError("V4_CAPACITY_IDENTITY_CONFLICT")
            available = max(0.0, visible - consumed)
            filled = min(requested, available)
            if filled <= 0.0:
                status, capital, reason = "NO_FILL", 0.0, "NO_REMAINING_CAPTURED_CAPACITY"
                filled = 0.0
            elif filled + 1e-12 < minimum:
                status, capital, reason = "NO_FILL", 0.0, "BELOW_MINIMUM_ORDER_SIZE"
                filled = 0.0
            else:
                status, capital, reason = "OPEN", filled * cost, None

            legs = [{
                "market_id": str(signal.get("market_id") or ""),
                "condition_id": condition_id,
                "token_id": token_id,
                "side": side,
            }]
            cur = db.execute(
                """
                INSERT INTO weather_paper_positions(
                    position_version,signal_id,lane,evidence_class,position_kind,event_id,
                    market_id,token_id,side,title,target_stake_usd,entry_cost_per_unit,
                    visible_units,filled_units,capital_used,maximum_payout,legs_json,
                    quote_observed_at,telegram_sent_at,opened_at,status,no_fill_reason,
                    financial_authority,automatic_order_placement,decision_id,execution_protocol,
                    decision_expires_at,paper_fill_at,validation_state,capacity_key,
                    settlement_notification_state
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,0,0,?,?,?,?,?,?,?)
                """,
                (
                    PAPER_POSITION_VERSION_V4, sid, spec["lane"], spec["evidence_class"], "DIRECTIONAL",
                    spec["event_id"], spec["market_id"], token_id, side, spec["title"], frozen_stake,
                    cost, visible, filled, capital, filled, _json(legs), quote_at, sent_at, fill_at,
                    status, reason, decision_id, PAPER_EXECUTION_PROTOCOL_V4, expires_at, fill_at,
                    "VALIDATED", capacity_key, "PENDING" if status == "OPEN" else None,
                ),
            )
            position_id = int(cur.lastrowid)
            if filled > 0.0:
                if cap is None:
                    db.execute(
                        "INSERT INTO weather_paper_capacity_usage VALUES(?,?,?,?,?,?)",
                        (capacity_key, visible, filled, sid, sid, time.time()),
                    )
                else:
                    db.execute(
                        "UPDATE weather_paper_capacity_usage SET consumed_units=?,last_signal_id=?,updated_at=? "
                        "WHERE capacity_key=?",
                        (consumed + filled, sid, time.time(), capacity_key),
                    )
            row = db.execute("SELECT * FROM weather_paper_positions WHERE id=?", (position_id,)).fetchone()
        return self._decode_position(dict(row)) if row else None

    def open_positions(self, limit: int = 200) -> list[dict]:
        count = max(1, min(1000, int(limit)))
        with self._conn() as db:
            rows = [dict(row) for row in db.execute(
                "SELECT * FROM weather_paper_positions WHERE status='OPEN' AND validation_state='VALIDATED' "
                "ORDER BY id LIMIT ?", (count,)
            )]
        return [self._decode_position(row) for row in rows]

    def open_positions_for_settlement(self, limit: int = SETTLEMENT_PAGE_SIZE) -> list[dict]:
        count = max(1, min(1000, int(limit)))
        cursor = int(self.get_state("v4_settlement_cursor", "0") or 0)
        with self._conn() as db:
            rows = [dict(row) for row in db.execute(
                "SELECT * FROM weather_paper_positions WHERE status='OPEN' AND validation_state='VALIDATED' "
                "AND id>? ORDER BY id LIMIT ?", (cursor, count)
            )]
            if len(rows) < count:
                remaining = count - len(rows)
                seen = {int(row["id"]) for row in rows}
                wrapped = [dict(row) for row in db.execute(
                    "SELECT * FROM weather_paper_positions WHERE status='OPEN' AND validation_state='VALIDATED' "
                    "AND id<=? ORDER BY id LIMIT ?", (cursor, remaining)
                )]
                rows.extend(row for row in wrapped if int(row["id"]) not in seen)
        if rows:
            self.set_state("v4_settlement_cursor", int(rows[-1]["id"]))
        return [self._decode_position(row) for row in rows]

    def recent_positions(self, limit: int = 10, *, resolved_only: bool = False) -> list[dict]:
        count = max(1, min(100, int(limit)))
        where = "AND status IN ('WON','LOST','RESOLVED_PARTIAL')" if resolved_only else ""
        with self._conn() as db:
            rows = [dict(row) for row in db.execute(
                f"SELECT * FROM weather_paper_positions WHERE validation_state='VALIDATED' {where} "
                "ORDER BY id DESC LIMIT ?", (count,)
            )]
        return [self._decode_position(row) for row in rows]

    def resolved_pending_notification(self, limit: int = 50) -> list[dict]:
        with self._conn() as db:
            rows = [dict(row) for row in db.execute(
                """
                SELECT * FROM weather_paper_positions
                WHERE validation_state='VALIDATED'
                  AND status IN ('WON','LOST','RESOLVED_PARTIAL')
                  AND COALESCE(settlement_notification_state,'PENDING')='PENDING'
                ORDER BY id LIMIT ?
                """, (max(1, min(200, int(limit))),)
            )]
        return [self._decode_position(row) for row in rows]

    def set_notification_state(self, position_id: int, state: str, message_id: int | None = None) -> None:
        with self._conn() as db:
            db.execute(
                "UPDATE weather_paper_positions SET settlement_notification_state=?, "
                "settlement_telegram_message_id=COALESCE(?,settlement_telegram_message_id) WHERE id=?",
                (str(state), message_id, int(position_id)),
            )

    def stats(self) -> dict:
        with self._conn() as db:
            row = db.execute(
                """
                SELECT
                    COUNT(*) AS all_rows,
                    SUM(CASE WHEN validation_state='VALIDATED' THEN 1 ELSE 0 END) AS validated,
                    SUM(CASE WHEN validation_state!='VALIDATED' OR validation_state IS NULL THEN 1 ELSE 0 END) AS unverified,
                    SUM(CASE WHEN validation_state='VALIDATED' AND status='OPEN' THEN 1 ELSE 0 END) AS open_n,
                    SUM(CASE WHEN validation_state='VALIDATED' AND status='NO_FILL' THEN 1 ELSE 0 END) AS no_fill,
                    SUM(CASE WHEN validation_state='VALIDATED' AND status='WON' THEN 1 ELSE 0 END) AS won,
                    SUM(CASE WHEN validation_state='VALIDATED' AND status='LOST' THEN 1 ELSE 0 END) AS lost,
                    SUM(CASE WHEN validation_state='VALIDATED' AND status='RESOLVED_PARTIAL' THEN 1 ELSE 0 END) AS partial,
                    SUM(CASE WHEN validation_state='VALIDATED' AND status='OPEN' THEN capital_used ELSE 0 END) AS open_capital,
                    SUM(CASE WHEN validation_state='VALIDATED' AND status IN ('WON','LOST','RESOLVED_PARTIAL') THEN capital_used ELSE 0 END) AS resolved_capital,
                    SUM(CASE WHEN validation_state='VALIDATED' AND status IN ('WON','LOST','RESOLVED_PARTIAL') THEN proceeds ELSE 0 END) AS resolved_proceeds,
                    SUM(CASE WHEN validation_state='VALIDATED' AND status IN ('WON','LOST','RESOLVED_PARTIAL') THEN pnl ELSE 0 END) AS pnl
                FROM weather_paper_positions
                """
            ).fetchone()
            quarantine = int(db.execute(
                "SELECT COUNT(*) FROM weather_paper_signals WHERE status='QUARANTINED'"
            ).fetchone()[0])
            uncertain = int(db.execute(
                "SELECT COUNT(*) FROM weather_paper_signals WHERE status='DELIVERY_UNCERTAIN'"
            ).fetchone()[0])
        data = dict(row) if row else {}
        won = int(data.get("won") or 0)
        lost = int(data.get("lost") or 0)
        partial = int(data.get("partial") or 0)
        resolved = won + lost + partial
        capital = float(data.get("resolved_capital") or 0.0)
        pnl = float(data.get("pnl") or 0.0)
        decisions = self.decision_counts()
        return {
            "version": PAPER_POSITION_VERSION_V4,
            "total": int(data.get("validated") or 0),
            "all_rows": int(data.get("all_rows") or 0),
            "unverified": int(data.get("unverified") or 0),
            "quarantined": quarantine,
            "delivery_uncertain": uncertain,
            "open": int(data.get("open_n") or 0),
            "no_fill": int(data.get("no_fill") or 0),
            "resolved": resolved,
            "won": won,
            "lost": lost,
            "partial": partial,
            "win_rate": won / (won + lost) if won + lost else None,
            "open_capital": float(data.get("open_capital") or 0.0),
            "resolved_capital": capital,
            "resolved_proceeds": float(data.get("resolved_proceeds") or 0.0),
            "pnl": pnl,
            "resolved_roi": pnl / capital if capital > 0 else None,
            "skipped": int(decisions.get("SKIPPED", 0)),
            "decision_counts": decisions,
            "financial_authority": False,
            "automatic_order_placement": False,
        }


def final_token_payout_v4(token_id: str, market: dict, *, expected_condition_id: str, expected_side: str) -> float | None:
    if not isinstance(market, dict) or market.get("closed") is not True:
        return None
    resolution = str(market.get("umaResolutionStatus") or market.get("uma_resolution_status") or "").strip().lower()
    if resolution not in {"resolved", "settled"}:
        return None
    condition = str(market.get("conditionId") or market.get("condition_id") or "").strip()
    if not condition or condition != str(expected_condition_id):
        raise V4InvariantError("V4_SETTLEMENT_CONDITION_MISMATCH")

    tokens = [str(value) for value in _json_list(market.get("clobTokenIds"))]
    prices_raw = _json_list(market.get("outcomePrices"))
    outcomes = [str(value).strip().lower() for value in _json_list(market.get("outcomes"))]
    if len(tokens) != 2 or len(prices_raw) != 2 or len(set(tokens)) != 2 or tokens.count(str(token_id)) != 1:
        raise V4InvariantError("V4_SETTLEMENT_TOKEN_VECTOR_INVALID")
    prices: list[float] = []
    for raw in prices_raw:
        if isinstance(raw, bool):
            raise V4InvariantError("V4_SETTLEMENT_PAYOUT_VECTOR_INVALID")
        value = _finite(raw, "V4_SETTLEMENT_PAYOUT_VECTOR_INVALID")
        if value < 0.0 or value > 1.0:
            raise V4InvariantError("V4_SETTLEMENT_PAYOUT_VECTOR_INVALID")
        prices.append(value)
    allowed = (
        (abs(prices[0] - 1.0) <= 1e-9 and abs(prices[1]) <= 1e-9)
        or (abs(prices[1] - 1.0) <= 1e-9 and abs(prices[0]) <= 1e-9)
        or (abs(prices[0] - 0.5) <= 1e-9 and abs(prices[1] - 0.5) <= 1e-9)
    )
    if not allowed or abs(sum(prices) - 1.0) > 1e-9:
        raise V4InvariantError("V4_SETTLEMENT_PAYOUT_VECTOR_INVALID")
    if outcomes:
        if len(outcomes) != 2 or set(outcomes) != {"yes", "no"}:
            raise V4InvariantError("V4_SETTLEMENT_OUTCOME_LABEL_INVALID")
        label = outcomes[tokens.index(str(token_id))]
        if label != str(expected_side).strip().lower():
            raise V4InvariantError("V4_SETTLEMENT_TOKEN_MEANING_MISMATCH")
    return prices[tokens.index(str(token_id))]


class CorrectiveSettlementEngine:
    def __init__(self, *, store: CorrectiveWeatherPaperPositionStore, telegram, gamma: PublicGammaSettlementClient | None = None) -> None:
        self.store = store
        self.telegram = telegram
        self.gamma = gamma or PublicGammaSettlementClient()
        self._owns_gamma = gamma is None

    async def close(self) -> None:
        if self._owns_gamma:
            await self.gamma.close()

    @staticmethod
    def _resolution_message(position: dict) -> str:
        status = str(position.get("status") or "")
        label = "WIN" if status == "WON" else "LOSS" if status == "LOST" else "PUSH / PARTIAL"
        icon = "✅" if status == "WON" else "❌" if status == "LOST" else "🟰"
        pnl = float(position.get("pnl") or 0.0)
        return "\n".join([
            f"{icon} <b>PAPER TRADE FINISHED — {label}</b>",
            html.escape(str(position.get("title") or ""))[:120],
            f"Position: <b>#{int(position['id'])}</b> | Side: <b>{html.escape(str(position.get('side') or ''))}</b>",
            f"Paper amount used: <b>${float(position.get('capital_used') or 0.0):.2f}</b>",
            f"Paper payout: <b>${float(position.get('proceeds') or 0.0):.2f}</b>",
            f"Result: <b>{pnl:+.2f} USD</b>",
            "",
            "📒 Simulated fill only. No real order was placed.",
        ])

    async def _notify(self) -> tuple[int, list[str]]:
        sent = 0
        errors: list[str] = []
        rows = await asyncio.to_thread(self.store.resolved_pending_notification, 50)
        for row in rows:
            pid = int(row["id"])
            await asyncio.to_thread(self.store.set_notification_state, pid, "SENDING", None)
            try:
                mid = await self.telegram.send_html(self._resolution_message(row))
            except DeliveryUncertain as exc:
                await asyncio.to_thread(self.store.set_notification_state, pid, "UNCERTAIN", None)
                errors.append(f"SETTLEMENT_NOTIFY_UNCERTAIN:{pid}:{exc.code}")
                continue
            except Exception as exc:
                await asyncio.to_thread(self.store.set_notification_state, pid, "PENDING", None)
                errors.append(f"SETTLEMENT_NOTIFY_FAILED:{pid}:{type(exc).__name__}")
                continue
            await asyncio.to_thread(self.store.set_notification_state, pid, "ACKNOWLEDGED", int(mid))
            sent += 1
        return sent, errors

    async def settle_once(self) -> dict:
        positions = await asyncio.to_thread(self.store.open_positions_for_settlement, SETTLEMENT_PAGE_SIZE)
        market_ids = sorted({
            str(leg.get("market_id") or "")
            for position in positions for leg in (position.get("legs") or [])
            if str(leg.get("market_id") or "")
        })
        semaphore = asyncio.Semaphore(6)

        async def fetch(mid: str):
            async with semaphore:
                try:
                    return mid, await self.gamma.market_by_id(mid), None
                except Exception as exc:
                    return mid, None, type(exc).__name__

        results = await asyncio.gather(*(fetch(mid) for mid in market_ids))
        markets: dict[str, dict] = {}
        errors: list[str] = []
        for mid, market, error in results:
            if error or not isinstance(market, dict):
                errors.append(f"SETTLEMENT_LOOKUP_UNAVAILABLE:{mid}:{error or 'NO_DATA'}")
            else:
                markets[mid] = market

        resolved = 0
        checked = 0
        for position in positions:
            checked += 1
            payouts: list[dict] = []
            complete = True
            for leg in position.get("legs") or []:
                mid = str(leg.get("market_id") or "")
                market = markets.get(mid)
                if market is None:
                    complete = False
                    break
                try:
                    payout = final_token_payout_v4(
                        str(leg.get("token_id") or ""), market,
                        expected_condition_id=str(leg.get("condition_id") or ""),
                        expected_side=str(leg.get("side") or ""),
                    )
                except V4InvariantError as exc:
                    errors.append(f"SETTLEMENT_INVALID:{position.get('id')}:{exc.code}")
                    complete = False
                    break
                if payout is None:
                    complete = False
                    break
                payouts.append({
                    "market_id": mid,
                    "condition_id": str(leg.get("condition_id") or ""),
                    "token_id": str(leg.get("token_id") or ""),
                    "side": str(leg.get("side") or ""),
                    "payout": float(payout),
                    "uma_resolution_status": str(market.get("umaResolutionStatus") or ""),
                })
            if not complete or not payouts:
                continue
            evidence = {
                "version": PAPER_SETTLEMENT_VERSION_V4,
                "source": "GAMMA_RESOLVED_EXACT_TOKEN_FINAL_VECTOR",
                "checked_at": time.time(),
                "legs": payouts,
                "financial_authority": False,
            }
            row = await asyncio.to_thread(
                self.store.resolve_position, int(position["id"]), sum(x["payout"] for x in payouts), evidence
            )
            if row is not None:
                resolved += 1
        notified, notification_errors = await self._notify()
        errors.extend(notification_errors)
        return {
            "version": PAPER_SETTLEMENT_VERSION_V4,
            "open_checked": checked,
            "market_ids_checked": len(market_ids),
            "resolved_now": resolved,
            "resolution_messages_sent": notified,
            "errors": errors,
        }


class ClearWeatherPaperCommandController(WeatherPaperCommandController):
    @staticmethod
    def _pct(value: object) -> str:
        try:
            number = float(value)
        except (TypeError, ValueError, OverflowError):
            return "n/a"
        return f"{100.0 * number:.1f}%" if math.isfinite(number) else "n/a"

    def _status_text(self) -> str:
        status = self._read_status()
        stats = self.store.stats()
        try:
            finished = float(status.get("finished_at") or 0.0)
        except (TypeError, ValueError, OverflowError):
            finished = 0.0
        age = time.time() - finished if finished > 0 else float("inf")
        healthy = bool(status.get("cycle_ok")) and 0.0 <= age <= STATUS_MAX_AGE_SECONDS
        icon = "🟢" if healthy else "🔴"
        lines = [
            f"{icon} <b>WEATHER PAPER BOT — {'RUNNING NORMALLY' if healthy else 'NEEDS ATTENTION'}</b>",
            f"Last full cycle: <b>{int(max(0, age)) if math.isfinite(age) else 'unknown'}s ago</b>",
            f"Open paper trades: <b>{int(stats.get('open') or 0)}</b>",
            f"Finished: <b>{int(stats.get('won') or 0)} wins / {int(stats.get('lost') or 0)} losses / {int(stats.get('partial') or 0)} push/partial</b>",
            f"Paper P&amp;L: <b>${float(stats.get('pnl') or 0.0):+.2f}</b>",
            f"Excluded old/unverified rows: <b>{int(stats.get('unverified') or 0) + int(stats.get('quarantined') or 0)}</b>",
            f"Delivery uncertain: <b>{int(stats.get('delivery_uncertain') or 0)}</b>",
            "",
            "Same-day conditioned lane: <b>OFF until observation + remaining-hours acceptance passes</b>",
            "Structural guarantee lane: <b>OFF pending strict semantic proof</b>",
            "Real orders: <b>DISABLED</b>",
        ]
        errors = status.get("errors") or []
        if errors:
            lines.append("⚠️ Last cycle issues: <code>" + html.escape(", ".join(map(str, errors))[:700]) + "</code>")
        return "\n".join(lines)

    def _stats_text(self) -> str:
        s = self.store.stats()
        validated_closed = int(s.get("resolved") or 0)
        lines = [
            "📊 <b>PAPER TRADING SUMMARY</b>",
            "",
            "<b>OPEN NOW</b>",
            f"Positions: <b>{int(s.get('open') or 0)}</b>",
            f"Paper money currently at risk: <b>${float(s.get('open_capital') or 0.0):.2f}</b>",
            "",
            "<b>FINISHED TRADES</b>",
            f"Completed: <b>{validated_closed}</b>",
            f"Wins: <b>{int(s.get('won') or 0)}</b> | Losses: <b>{int(s.get('lost') or 0)}</b> | Push/partial: <b>{int(s.get('partial') or 0)}</b>",
            f"Paper amount used in finished trades: <b>${float(s.get('resolved_capital') or 0.0):.2f}</b>",
            f"Paper payout received: <b>${float(s.get('resolved_proceeds') or 0.0):.2f}</b>",
            f"Net paper P&amp;L: <b>${float(s.get('pnl') or 0.0):+.2f}</b>",
            f"ROI on finished validated trades: <b>{self._pct(s.get('resolved_roi'))}</b>",
            "",
            "<b>NOT TRADED / NOT COUNTED</b>",
            f"No fill: <b>{int(s.get('no_fill') or 0)}</b>",
            f"Skipped at final recheck: <b>{int(s.get('skipped') or 0)}</b>",
            f"Delivery uncertain: <b>{int(s.get('delivery_uncertain') or 0)}</b>",
            f"Old/unverified/excluded: <b>{int(s.get('unverified') or 0) + int(s.get('quarantined') or 0)}</b>",
            "",
            "Only v4 VALIDATED rows are included in wins, losses, ROI and P&amp;L.",
            "📒 Everything is simulated; no real order was placed.",
        ]
        return "\n".join(lines)

    def _positions_text(self) -> str:
        rows = self.store.open_positions(10)
        lines = [f"📂 <b>OPEN PAPER POSITIONS — {len(rows)}</b>"]
        if not rows:
            return "\n".join(lines + ["No validated paper trades are open right now."])
        for row in rows:
            lines.extend([
                "",
                f"<b>#{int(row['id'])} — {html.escape(str(row.get('side') or ''))}</b>",
                html.escape(str(row.get("title") or ""))[:100],
                f"Entry cost/share: <b>${float(row.get('entry_cost_per_unit') or 0.0):.4f}</b>",
                f"Paper amount used: <b>${float(row.get('capital_used') or 0.0):.2f}</b>",
                f"Shares: <b>{float(row.get('filled_units') or 0.0):.4f}</b>",
                "Status: <b>🟡 OPEN — waiting for final market result</b>",
            ])
        if len(rows) == 10:
            lines.append("\nShowing the 10 oldest open positions.")
        return "\n".join(lines)

    def _history_text(self) -> str:
        rows = self.store.recent_positions(10, resolved_only=True)
        lines = ["🏁 <b>RECENT FINISHED PAPER TRADES</b>"]
        if not rows:
            return "\n".join(lines + ["No validated trades have finished yet."])
        for row in rows:
            status = str(row.get("status") or "")
            result = "✅ WIN" if status == "WON" else "❌ LOSS" if status == "LOST" else "🟰 PUSH/PARTIAL"
            pnl = float(row.get("pnl") or 0.0)
            lines.extend([
                "",
                f"<b>#{int(row['id'])} {result}</b>",
                html.escape(str(row.get("title") or ""))[:95],
                f"Paper amount: ${float(row.get('capital_used') or 0.0):.2f} | P&amp;L: <b>${pnl:+.2f}</b>",
            ])
        return "\n".join(lines)

    def _recent_text(self) -> str:
        rows = self.store.recent_signals(10)
        lines = ["🧾 <b>RECENT SIGNALS / WHAT HAPPENED</b>"]
        if not rows:
            return "\n".join(lines + ["No signals stored yet."])
        for row in rows:
            position = str(row.get("position_status") or "NOT TRADED")
            lines.append(
                f"#{int(row['id'])} {html.escape(str(row.get('side') or ''))} @ "
                f"${float(row.get('entry_cost') or 0.0):.4f} → <b>{html.escape(position)}</b>"
            )
        return "\n".join(lines)

    @staticmethod
    def _help_text() -> str:
        return "\n".join([
            "🤖 <b>WEATHER PAPER COMMANDS</b>",
            "/status — is the bot healthy and what is enabled?",
            "/stats — open trades, wins/losses, money used and P&amp;L",
            "/positions — exactly what paper trades are open now",
            "/history — latest finished wins/losses",
            "/recent — latest signals and whether they became trades",
            "/help — this list",
            "",
            "No manual /took command is needed. Valid delivered signals are tracked automatically.",
        ])


class WeatherLivePaperV4Service(WeatherLivePaperV3Service):
    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self.positions = CorrectiveWeatherPaperPositionStore(self.db_path)
        old_settlement = self.settlement
        old_commands = self.commands
        old_telegram = self.telegram
        self.telegram = CorrectivePaperTelegram(token=old_telegram.token, chat_id=old_telegram.chat_id)
        self.settlement = CorrectiveSettlementEngine(store=self.positions, telegram=self.telegram)
        self.commands = ClearWeatherPaperCommandController(
            telegram=self.telegram,
            store=self.positions,
            status_path=self.status_path,
            paper_stake_usd=self.paper_stake_usd,
        )
        self._v4_superseded_settlement = old_settlement
        self._v4_superseded_commands = old_commands
        self._v4_superseded_telegram = old_telegram
        self._v4_structural_suppressed_total = 0
        self._v4_dispatch_skipped_total = 0
        self._v4_last_summary_sent_at = 0.0
        self._forecast_cache = {}
        self._forecast_distribution_by_sha: dict[str, object] = {}
        # Suppress the base's mid-cycle summary; v4 emits one only after tracking and
        # settlement have completed.
        self._last_summary_sent_at = float("inf")

    async def close(self) -> None:
        await asyncio.gather(
            self._v4_superseded_settlement.close(),
            self._v4_superseded_commands.close(),
            self._v4_superseded_telegram.close(),
            return_exceptions=True,
        )
        await super().close()

    async def _save_and_send_structural(self, opportunity: dict, event: dict | None):
        self._v4_structural_suppressed_total += 1
        return False, None

    async def quarantine_observation_blind_history(self) -> dict:
        rows = await asyncio.to_thread(self._forecast_signal_rows)
        quarantined = 0
        errors: list[str] = []
        for row in rows:
            try:
                payload = _payload(row.get("payload_json"))
                if payload.get("paper_execution_protocol_version") == PAPER_EXECUTION_PROTOCOL_V4:
                    continue
                changed = await asyncio.to_thread(
                    self.positions.quarantine_signal, int(row["id"]), V4_LEGACY_REASON
                )
                if changed:
                    quarantined += 1
            except Exception as exc:
                errors.append(f"V4_HISTORY_QUARANTINE:{row.get('id')}:{type(exc).__name__}")
        self._forecast_history_quarantined_total += quarantined
        self._quarantine_errors = errors[-20:]
        return {"quarantined_now": quarantined, "errors": errors}

    async def _compile_eligible(self, event: dict):
        compiled = strict_compile_weather_event(event)
        metadata = await self._station_metadata_for_compiled(compiled)
        if metadata is None:
            raise V4InvariantError("V4_STATION_METADATA_MISSING")
        local_today = self._local_date(self._now_epoch(), str(metadata.timezone))
        if compiled.target_date <= local_today:
            raise V4InvariantError("V4_RAW_GEFS_NOT_FUTURE_LOCAL_DAY")
        if (compiled.target_date - local_today).days > 3:
            raise V4InvariantError("V4_FORECAST_HORIZON_OUT_OF_RANGE")
        return compiled, metadata

    def _certified_forecast_events(self, events) -> list[tuple[dict, object]]:
        # Strict semantics first.  Station-local eligibility is completed async in
        # _forecast_candidate, but unlike v3 we do not let same-day rows consume the
        # six-event budget here.  Rotate by event ID across cycles for fairness.
        rows: list[tuple[str, dict, object]] = []
        for event in events:
            if not isinstance(event, dict):
                continue
            try:
                compiled = strict_compile_weather_event(event)
            except V4InvariantError:
                continue
            rows.append((compiled.event_id, event, compiled))
        rows.sort(key=lambda value: value[0])
        if not rows:
            return []
        last = self.positions.get_state("v4_forecast_cursor", "")
        start = 0
        if last:
            for index, (event_id, _, _) in enumerate(rows):
                if event_id > last:
                    start = index
                    break
            else:
                start = 0
        ordered = rows[start:] + rows[:start]
        # Return more than the final budget so station-local same-day suppression
        # cannot starve eligible future events. _forecast_candidate cheaply gates
        # before network-heavy forecast/CLOB work.
        return [(event, compiled) for _, event, compiled in ordered[: max(self.max_forecast_events * 4, self.max_forecast_events)]]

    async def _mapped_forecast(self, event_id: str, compiled):
        metadata = await self._station_metadata_for_compiled(compiled)
        if metadata is None:
            raise V4InvariantError("V4_STATION_METADATA_MISSING")
        semantic = _semantic_digest(compiled, metadata)
        now_mono = time.monotonic()
        cached = self._forecast_cache.get(semantic)
        if cached is not None and 0.0 <= now_mono - float(cached[0]) <= self.forecast_cache_seconds:
            return cached[1]
        distribution = await self.forecast_client.daily_extreme(
            station=str(compiled.station_hint).upper(),
            latitude=float(metadata.latitude),
            longitude=float(metadata.longitude),
            target_date=compiled.target_date,
            family=compiled.family,
            unit=compiled.unit,
            timezone=str(metadata.timezone),
        )
        distance = _haversine_km(
            float(metadata.latitude), float(metadata.longitude),
            float(distribution.resolved_latitude), float(distribution.resolved_longitude),
        )
        if distance > GRID_MAX_DISTANCE_KM:
            raise V4InvariantError("V4_FORECAST_GRID_TOO_FAR_FROM_STATION")
        forecast = map_ensemble_to_contract_buckets(compiled, distribution, FORECAST_MAPPING_POLICY)
        self._forecast_distribution_by_sha[forecast.source_evidence_sha256] = distribution
        self._forecast_cache[semantic] = (now_mono, forecast)
        # Bound cache growth for month-scale operation.
        if len(self._forecast_cache) > 128:
            oldest = min(self._forecast_cache.items(), key=lambda item: float(item[1][0]))[0]
            self._forecast_cache.pop(oldest, None)
        if len(self._forecast_distribution_by_sha) > 256:
            for key in list(self._forecast_distribution_by_sha)[:64]:
                self._forecast_distribution_by_sha.pop(key, None)
        return forecast

    async def _forecast_candidate(self, event: dict, compiled) -> dict | None:
        try:
            compiled, metadata = await self._compile_eligible(event)
        except (V4InvariantError, WeatherStationMetadataError):
            self._forecast_same_day_suppressed_total += 1
            return None
        forecast = await self._mapped_forecast(compiled.event_id, compiled)
        exact = await self.runtime.clob.exact_event_snapshot(compiled)
        _validate_exact_snapshot(compiled, exact)
        bucket_by_market = {bucket.market_id: bucket for bucket in compiled.buckets}
        best = None
        for row in forecast.bucket_frequencies:
            bucket = bucket_by_market.get(row.market_id)
            if bucket is None:
                continue
            params = exact.parameters.get(bucket.condition_id)
            if params is None or (params.fee_rate > 0.0 and params.taker_only is not True):
                continue
            for side, token, probability in (
                ("YES", bucket.yes_token, float(row.raw_member_frequency)),
                ("NO", bucket.no_token, float(row.raw_no_frequency)),
            ):
                book = exact.books.get(token)
                if book is None or book.best_ask is None or book.best_ask_size <= 0.0:
                    continue
                ask = float(book.best_ask)
                fee = conservative_taker_fee_per_share(ask, params.fee_rate, params.fee_exponent)
                cost = ask + fee
                gap = probability - cost
                if gap < self.forecast_raw_gap_min:
                    continue
                label = (
                    f"≤ {bucket.upper:g}{compiled.unit}" if bucket.lower is None
                    else f"≥ {bucket.lower:g}{compiled.unit}" if bucket.upper is None
                    else f"{bucket.lower:g}–{bucket.upper:g}{compiled.unit}"
                )
                candidate = {
                    "lane": "weather_forecast_raw_gap",
                    "evidence_class": "RESEARCH_ONLY_UNCALIBRATED_V4",
                    "event_id": compiled.event_id,
                    "market_id": bucket.market_id,
                    "condition_id": bucket.condition_id,
                    "token_id": token,
                    "side": side,
                    "station": str(compiled.station_hint).upper(),
                    "station_timezone": str(metadata.timezone),
                    "target_date": compiled.target_date.isoformat(),
                    "family": compiled.family,
                    "unit": compiled.unit,
                    "bucket_label": label,
                    "raw_probability": probability,
                    "member_hits": int(row.member_hits if side == "YES" else row.member_count - row.member_hits),
                    "member_count": int(row.member_count),
                    "ask": ask,
                    "fee": fee,
                    "entry_cost": cost,
                    "raw_gap": gap,
                    "forecast_evidence_sha256": forecast.source_evidence_sha256,
                    "forecast_mapping_policy": forecast.mapping_policy_id,
                    "semantic_digest": _semantic_digest(compiled, metadata),
                    "forecast_run_id": forecast.source_evidence_sha256,
                    "forecast_run_age_known": False,
                    "exact_clob": True,
                    "calibrated_probability": False,
                    "execution_verified": False,
                    "financial_authority": False,
                    "automatic_order_placement": False,
                }
                if best is None or candidate["raw_gap"] > best["raw_gap"]:
                    best = candidate
        return best

    @staticmethod
    def _local_midnight_epoch(target: date, timezone_name: str) -> float:
        zone = ZoneInfo(str(timezone_name))
        return datetime.combine(target, datetime_time.min, tzinfo=zone).timestamp()

    async def _dispatch_recheck(self, candidate: dict, event: dict) -> dict | None:
        compiled, metadata = await self._compile_eligible(event)
        if _semantic_digest(compiled, metadata) != str(candidate.get("semantic_digest") or ""):
            raise V4InvariantError("V4_CONTRACT_CHANGED_BEFORE_DISPATCH")
        bucket = next((row for row in compiled.buckets if row.market_id == candidate.get("market_id")), None)
        if bucket is None:
            raise V4InvariantError("V4_MARKET_CHANGED_BEFORE_DISPATCH")
        expected_token = bucket.yes_token if candidate.get("side") == "YES" else bucket.no_token
        if expected_token != candidate.get("token_id"):
            raise V4InvariantError("V4_TOKEN_MEANING_CHANGED_BEFORE_DISPATCH")

        exact = await self.runtime.clob.exact_event_snapshot(compiled)
        _validate_exact_snapshot(compiled, exact)
        params = exact.parameters.get(bucket.condition_id)
        book = exact.books.get(str(expected_token))
        if params is None or book is None or book.best_ask is None or book.best_ask_size <= 0:
            return None
        ask = float(book.best_ask)
        fee = conservative_taker_fee_per_share(ask, params.fee_rate, params.fee_exponent)
        cost = ask + fee
        gap = float(candidate["raw_probability"]) - cost
        if gap < self.forecast_raw_gap_min:
            return None
        quote_at = float(book.received_at)
        fill_at = float(exact.finished_at)
        local_midnight = self._local_midnight_epoch(compiled.target_date, str(metadata.timezone))
        expires = min(fill_at + QUOTE_DECISION_TTL_SECONDS, local_midnight - LOCAL_MIDNIGHT_MARGIN_SECONDS)
        if time.time() >= expires:
            return None
        outcome_map = {
            str(token): str(outcome).strip().lower()
            for token, outcome in params.token_outcomes
        }
        payload = dict(candidate)
        payload.update({
            "ask": ask,
            "fee": fee,
            "entry_cost": cost,
            "raw_gap": gap,
            "ask_size": float(book.best_ask_size),
            "quote_observed_at": quote_at,
            "paper_fill_at": fill_at,
            "decision_expires_at": expires,
            "book_provider_timestamp": str(book.timestamp or ""),
            "book_hash": str(book.book_hash or ""),
            "minimum_order_size": float(params.minimum_order_size),
            "minimum_tick_size": float(params.minimum_tick_size),
            "clob_outcome_map": outcome_map,
            "paper_target_stake_usd": float(self.paper_stake_usd),
            "paper_execution_protocol_version": PAPER_EXECUTION_PROTOCOL_V4,
            "event_title": _event_title(event, str(candidate["event_id"])),
            "event_url": _event_link(event),
            "paper_mode": True,
        })
        payload["decision_id"] = _sha({
            "lane": payload["lane"],
            "event_id": payload["event_id"],
            "market_id": payload["market_id"],
            "side": payload["side"],
            "token_id": payload["token_id"],
            "semantic_digest": payload["semantic_digest"],
            "forecast_run_id": payload["forecast_run_id"],
            "book_hash": payload["book_hash"],
            "ask": payload["ask"],
            "quote_observed_at": payload["quote_observed_at"],
        })
        return payload

    def _forecast_message_v4(self, candidate: dict) -> str:
        side = html.escape(str(candidate["side"]))
        bucket = html.escape(str(candidate["bucket_label"]))
        title = html.escape(str(candidate.get("event_title") or candidate["event_id"]))
        members = int(candidate.get("member_hits") or 0)
        total = int(candidate.get("member_count") or 0)
        return "\n".join([
            "🚨 <b>PAPER WEATHER SIGNAL</b>",
            f"<b>{title}</b>",
            "",
            f"Simulated action: <b>BUY {side} — {bucket}</b>",
            f"Exact ask now: <b>${float(candidate['ask']):.4f}</b> + fee ${float(candidate['fee']):.5f}",
            f"Paper stake target: <b>${float(candidate['paper_target_stake_usd']):.2f}</b>",
            f"Visible shares at captured ask: <b>{float(candidate['ask_size']):.4f}</b>",
            "",
            f"GEFS member count for this side: <b>{members}/{total}</b> (uncalibrated)",
            f"Raw member frequency: <b>{100.0 * float(candidate['raw_probability']):.1f}%</b>",
            f"Raw frequency − executable cost: <b>{100.0 * float(candidate['raw_gap']):.1f} pts</b>",
            "",
            f"Station/date: <b>{html.escape(str(candidate['station']))} / {html.escape(str(candidate['target_date']))}</b>",
            "Quote and future-day eligibility were rechecked immediately before this message.",
            "This snapshot expires quickly; expired/uncertain delivery is excluded from paper P&amp;L.",
            "⚠️ Member frequency is model evidence, not a calibrated win probability.",
            "📒 PAPER ONLY — no real order was placed.",
            f"ID: <code>{html.escape(str(candidate['decision_id'])[:12])}</code>",
        ])

    async def _save_and_send_forecast(self, candidate: dict, event: dict) -> tuple[bool, str | None]:
        try:
            fresh = await self._dispatch_recheck(candidate, event)
        except (V4InvariantError, WeatherCLOBError, WeatherStationMetadataError) as exc:
            self._v4_dispatch_skipped_total += 1
            code = getattr(exc, "code", type(exc).__name__)
            await asyncio.to_thread(
                self.positions.record_decision,
                decision_id=str(candidate.get("forecast_evidence_sha256") or candidate.get("event_id") or "unknown"),
                event_id=str(candidate.get("event_id") or ""),
                market_id=str(candidate.get("market_id") or "") or None,
                side=str(candidate.get("side") or "") or None,
                outcome="SKIPPED",
                reason=str(code),
            )
            return False, None
        if fresh is None:
            self._v4_dispatch_skipped_total += 1
            await asyncio.to_thread(
                self.positions.record_decision,
                decision_id=str(candidate.get("forecast_evidence_sha256") or candidate.get("event_id") or "unknown"),
                event_id=str(candidate.get("event_id") or ""),
                market_id=str(candidate.get("market_id") or "") or None,
                side=str(candidate.get("side") or "") or None,
                outcome="SKIPPED",
                reason="FINAL_RECHECK_NO_LONGER_ELIGIBLE",
            )
            return False, None

        # Recheck station-local temporal eligibility once more at the persistence /
        # dispatch boundary; the quote expiry also includes a midnight safety margin.
        if time.time() >= float(fresh["decision_expires_at"]):
            return False, None
        signal_id = self.store.save_signal(
            fingerprint=str(fresh["decision_id"]),
            lane=str(fresh["lane"]),
            evidence_class=str(fresh["evidence_class"]),
            event_id=str(fresh["event_id"]),
            market_id=str(fresh["market_id"]),
            side=str(fresh["side"]),
            token_id=str(fresh["token_id"]),
            model_probability=float(fresh["raw_probability"]),
            entry_cost=float(fresh["entry_cost"]),
            raw_gap=float(fresh["raw_gap"]),
            theoretical_payout=1.0,
            created_at=float(fresh["paper_fill_at"]),
            payload=fresh,
        )
        if signal_id is None:
            return False, None
        await asyncio.to_thread(self.positions.set_signal_status, signal_id, "PENDING_DELIVERY")
        try:
            message_id = await self.telegram.send_html(
                self._forecast_message_v4(fresh),
                url=_event_link(event),
                expires_at=float(fresh["decision_expires_at"]),
            )
        except DeliveryUncertain as exc:
            await asyncio.to_thread(self.positions.set_signal_status, signal_id, "DELIVERY_UNCERTAIN")
            await asyncio.to_thread(
                self.positions.record_decision,
                decision_id=str(fresh["decision_id"]), event_id=str(fresh["event_id"]),
                market_id=str(fresh["market_id"]), side=str(fresh["side"]),
                outcome="DELIVERY_UNCERTAIN", reason=exc.code,
            )
            return True, exc.code
        except WeatherLivePaperError as exc:
            status = "EXPIRED" if "EXPIRED" in exc.code else "DELIVERY_FAILED"
            await asyncio.to_thread(self.positions.set_signal_status, signal_id, status)
            await asyncio.to_thread(
                self.positions.record_decision,
                decision_id=str(fresh["decision_id"]), event_id=str(fresh["event_id"]),
                market_id=str(fresh["market_id"]), side=str(fresh["side"]),
                outcome="SKIPPED", reason=exc.code,
            )
            return True, exc.code

        sent_at = time.time()
        self.store.mark_telegram_sent(signal_id, int(message_id), sent_at=sent_at)
        if sent_at >= float(fresh["decision_expires_at"]):
            await asyncio.to_thread(self.positions.set_signal_status, signal_id, "EXPIRED")
            await asyncio.to_thread(
                self.positions.record_decision,
                decision_id=str(fresh["decision_id"]), event_id=str(fresh["event_id"]),
                market_id=str(fresh["market_id"]), side=str(fresh["side"]),
                outcome="SKIPPED", reason="DELIVERY_RECEIPT_AFTER_EXPIRY",
            )
            # Visible message already carries an explicit expiry qualification.  Do
            # not invent a paper fill from it.
            return True, "DELIVERY_RECEIPT_AFTER_EXPIRY"
        await asyncio.to_thread(self.positions.set_signal_status, signal_id, "ACKNOWLEDGED")
        position = await asyncio.to_thread(self.positions.ensure_position_for_signal, signal_id, self.paper_stake_usd)
        await asyncio.to_thread(
            self.positions.record_decision,
            decision_id=str(fresh["decision_id"]), event_id=str(fresh["event_id"]),
            market_id=str(fresh["market_id"]), side=str(fresh["side"]),
            outcome="OPENED" if position and position.get("status") == "OPEN" else "NO_FILL",
            reason=None if position and position.get("status") == "OPEN" else str((position or {}).get("no_fill_reason") or "NO_POSITION"),
        )
        self.positions.set_state("v4_forecast_cursor", str(fresh["event_id"]))
        return True, None

    async def send_startup(self) -> int:
        release = self.release_sha()
        text = "\n".join([
            "🟢 <b>WEATHER PAPER BOT ONLINE — V4 CORRECTIVE MODE</b>",
            "",
            "Validated future-day research uses exact rechecked Polymarket books.",
            "Old/unreconstructable history is excluded from performance automatically.",
            "Structural guaranteed-basket alerts are OFF until strict semantic proof passes.",
            "Same-day conditioned alerts are OFF until official observations + near-term + remaining-hours acceptance passes.",
            "",
            f"Paper stake target: <b>${self.paper_stake_usd:.2f}</b> per valid decision, frozen before delivery.",
            "Commands: <code>/status</code> <code>/stats</code> <code>/positions</code> <code>/history</code> <code>/recent</code>",
            "🚫 <b>NO REAL ORDERS</b>",
            f"Release: <code>{html.escape(release[:12])}</code>",
        ])
        return await self.telegram.send_html(text)

    async def run_cycle(self) -> dict:
        status = await super().run_cycle()
        status = dict(status)
        position_stats = await asyncio.to_thread(self.positions.stats)
        status.update({
            "version": WEATHER_LIVE_PAPER_V4_VERSION,
            "forecast_policy": "STRICT_FUTURE_LOCAL_DAY_RAW_GEFS_V4",
            "same_day_conditioned_policy": "FOUNDATION_PRESENT_BUT_EMISSION_DISABLED_PENDING_ACCEPTANCE",
            "structural_policy": "DISABLED_PENDING_STRICT_COMMON_RESOLUTION_PROOF",
            "structural_suppressed_total": self._v4_structural_suppressed_total,
            "dispatch_skipped_total": self._v4_dispatch_skipped_total,
            "paper_position_stats": position_stats,
            "financial_delivery": False,
            "financial_authority": False,
            "automatic_order_placement": False,
            "wallet_or_order_api_loaded": False,
        })
        _atomic_json(self.status_path, status)

        finished = float(status.get("finished_at") or time.time())
        if self._v4_last_summary_sent_at == 0.0 or finished - self._v4_last_summary_sent_at >= SUMMARY_INTERVAL_SECONDS:
            s = position_stats
            text = "\n".join([
                "📡 <b>WEATHER PAPER — HOURLY SUMMARY</b>",
                f"Bot health: <b>{'OK' if status.get('cycle_ok') else 'NEEDS ATTENTION'}</b>",
                f"Open paper trades: <b>{int(s.get('open') or 0)}</b> (${float(s.get('open_capital') or 0.0):.2f} at risk)",
                f"Finished: <b>{int(s.get('won') or 0)}W / {int(s.get('lost') or 0)}L / {int(s.get('partial') or 0)} push/partial</b>",
                f"Net validated paper P&amp;L: <b>${float(s.get('pnl') or 0.0):+.2f}</b>",
                f"No-fill: <b>{int(s.get('no_fill') or 0)}</b> | skipped: <b>{int(s.get('skipped') or 0)}</b> | delivery uncertain: <b>{int(s.get('delivery_uncertain') or 0)}</b>",
                f"Old/unverified excluded: <b>{int(s.get('unverified') or 0) + int(s.get('quarantined') or 0)}</b>",
                "",
                "Use /positions for what is open now and /stats for the full paper ledger.",
                "🚫 No real orders.",
            ])
            try:
                await self.telegram.send_html(text)
                self._v4_last_summary_sent_at = finished
            except WeatherLivePaperError as exc:
                status["errors"] = list(status.get("errors") or []) + [exc.code]
                status["cycle_ok"] = False
                _atomic_json(self.status_path, status)
        return status


async def _main(args) -> int:
    service = WeatherLivePaperV4Service(
        db_path=args.db,
        status_path=args.status,
        release_file=args.release_file,
        interval_seconds=args.interval_seconds,
        forecast_cache_seconds=args.forecast_cache_seconds,
        forecast_raw_gap_min=args.forecast_raw_gap_min,
        max_forecast_events=args.max_forecast_events,
        paper_stake_usd=args.paper_stake_usd,
    )
    try:
        if args.once:
            await service.send_startup()
            status = await service.run_cycle()
            print(json.dumps(status, sort_keys=True, indent=2))
            return 0 if status.get("cycle_ok") else 2
        await service.loop()
        return 0
    finally:
        await service.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--status", type=Path, required=True)
    parser.add_argument("--release-file", type=Path, required=True)
    parser.add_argument("--interval-seconds", type=float, default=DEFAULT_INTERVAL_SECONDS)
    parser.add_argument("--forecast-cache-seconds", type=float, default=DEFAULT_FORECAST_CACHE_SECONDS)
    parser.add_argument("--forecast-raw-gap-min", type=float, default=DEFAULT_FORECAST_RAW_GAP_MIN)
    parser.add_argument("--max-forecast-events", type=int, default=DEFAULT_MAX_FORECAST_EVENTS)
    parser.add_argument("--paper-stake-usd", type=float, default=DEFAULT_PAPER_STAKE_USD)
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    raise SystemExit(asyncio.run(_main(args)))


if __name__ == "__main__":
    main()
