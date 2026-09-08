from __future__ import annotations

import json
import math
import re
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path

from .config import settings
from .detectors import market_url
from .models import Book, Market, Signal
from .polymarket import taker_fee_per_share

SPORTS_MAPPING_VERSION = "home_away_v5_causal_cache_unqualified_match_moneyline_only"
SPORTS_DETECTOR = "sports_result_lag_v3"
SPORTS_CAUSAL_CACHE_VERSION = "source_timestamp_monotonic_v1"

_BAD_STATUS_WORDS = re.compile(
    r"\b(?:cancelled|canceled|postponed|suspended|abandoned|void|no\s+contest|delayed)\b",
    re.I,
)
_UNSUPPORTED_MARKET_WORDS = re.compile(
    r"\b(?:spread|handicap|over|under|o/u|total|set|period|quarter|half|inning|map|round|"
    r"series|race\s+to|margin|first\s+to|game\s+\d|set\s+\d|period\s+\d|"
    r"regulation|overtime|extra\s+time|shootout|penalt(?:y|ies)|draw\s+no\s+bet|dnb|"
    r"two[-\s]+way)\b",
    re.I,
)
_SIGNED_LINE = re.compile(r"(?:^|[\s(])[-+]\d+(?:\.\d+)?(?:[\s)]|$)")

_SPORTS_CAUSAL_FLOOR: dict[str, float] = {}
_SPORTS_CAUSAL_FINGERPRINT: dict[str, str] = {}
_SPORTS_CAUSAL_QUARANTINED_AT: dict[str, float] = {}


def _decode_meta(raw: object) -> dict:
    try:
        meta = json.loads(str(raw or "{}"))
    except Exception:
        meta = {}
    return meta if isinstance(meta, dict) else {}


def quarantine_pre_v3_sports_history(db_path: str) -> int:
    """Quarantine sports evidence produced before the current narrow causal scope.

    The legacy function name is retained for deployment compatibility. Migrations are
    idempotent and preserve original status, settlement payout and P&L.

    1. Legacy ``sports_result_lag`` rows from before explicit home/away mapping.
    2. Earlier ``sports_result_lag_v3`` rows before qualifier exclusions.
    3. v4 ``sports_result_lag_v3`` rows before monotonic source-time cache evidence.
    """
    path = Path(db_path)
    if not path.exists():
        return 0

    changed = 0
    with sqlite3.connect(path) as c:
        c.row_factory = sqlite3.Row
        c.execute("CREATE TABLE IF NOT EXISTS bot_state (key TEXT PRIMARY KEY, value TEXT)")

        legacy_marker = "sports_pre_v3_quarantined"
        legacy_done = c.execute("SELECT value FROM bot_state WHERE key=?", (legacy_marker,)).fetchone()
        legacy_changed = 0
        if not legacy_done:
            rows = c.execute(
                "SELECT id,metadata FROM signals WHERE detector='sports_result_lag'"
            ).fetchall()
            for row in rows:
                meta = _decode_meta(row["metadata"])
                meta["sports_mapping_version"] = "PRE_V3_QUARANTINED"
                meta["sports_quarantine_reason"] = (
                    "pre-v3 sports logic did not safely distinguish spreads/periods/cancellations"
                )
                c.execute(
                    "UPDATE signals SET metadata=? WHERE id=?",
                    (json.dumps(meta, separators=(",", ":")), int(row["id"])),
                )
                legacy_changed += 1
            c.execute(
                "INSERT INTO bot_state(key,value) VALUES(?,?)",
                (legacy_marker, str(legacy_changed)),
            )
            changed += legacy_changed

        v3_marker = "sports_pre_v4_qualified_scope_quarantined"
        v3_done = c.execute("SELECT value FROM bot_state WHERE key=?", (v3_marker,)).fetchone()
        v3_changed = 0
        if not v3_done:
            rows = c.execute(
                "SELECT id,metadata FROM signals WHERE detector='sports_result_lag_v3'"
            ).fetchall()
            for row in rows:
                meta = _decode_meta(row["metadata"])
                old_version = str(meta.get("sports_mapping_version") or "")
                if old_version in {
                    SPORTS_MAPPING_VERSION,
                    "home_away_v4_unqualified_match_moneyline_only",
                }:
                    continue
                meta["sports_original_detector"] = "sports_result_lag_v3"
                meta["sports_original_mapping_version"] = old_version
                meta["sports_mapping_version"] = "PRE_V4_V3_QUARANTINED"
                meta["sports_quarantine_reason"] = (
                    "pre-v4 sports v3 allowed regulation/overtime/shootout-qualified contracts"
                )
                c.execute(
                    "UPDATE signals SET detector='sports_result_lag', metadata=? WHERE id=?",
                    (json.dumps(meta, separators=(",", ":")), int(row["id"])),
                )
                v3_changed += 1
            c.execute(
                "INSERT INTO bot_state(key,value) VALUES(?,?)",
                (v3_marker, str(v3_changed)),
            )
            changed += v3_changed

        causal_marker = "sports_pre_v5_causal_timestamp_quarantined"
        causal_done = c.execute("SELECT value FROM bot_state WHERE key=?", (causal_marker,)).fetchone()
        causal_changed = 0
        if not causal_done:
            rows = c.execute(
                "SELECT id,metadata FROM signals WHERE detector='sports_result_lag_v3'"
            ).fetchall()
            for row in rows:
                meta = _decode_meta(row["metadata"])
                old_version = str(meta.get("sports_mapping_version") or "")
                if old_version == SPORTS_MAPPING_VERSION:
                    continue
                meta["sports_original_detector"] = "sports_result_lag_v3"
                meta["sports_original_mapping_version"] = old_version
                meta["sports_mapping_version"] = "PRE_V5_CAUSAL_TIMESTAMP_QUARANTINED"
                meta["sports_quarantine_reason"] = (
                    "pre-v5 sports evidence did not enforce monotonic source timestamps or same-time conflict quarantine"
                )
                c.execute(
                    "UPDATE signals SET detector='sports_result_lag', metadata=? WHERE id=?",
                    (json.dumps(meta, separators=(",", ":")), int(row["id"])),
                )
                causal_changed += 1
            c.execute(
                "INSERT INTO bot_state(key,value) VALUES(?,?)",
                (causal_marker, str(causal_changed)),
            )
            changed += causal_changed

    return changed


def _score(value: object) -> tuple[float, float] | None:
    parts = re.findall(r"\d+(?:\.\d+)?", str(value or ""))
    if len(parts) != 2:
        return None
    try:
        home, away = float(parts[0]), float(parts[1])
    except ValueError:
        return None
    return home, away


def _norm_team(value: object) -> str:
    return " ".join(re.sub(r"[^a-z0-9 ]+", " ", str(value or "").lower()).split())


def _team_mentioned(question: str, team: str) -> bool:
    q = _norm_team(question)
    t = _norm_team(team)
    if not q or not t:
        return False
    if re.search(rf"\b{re.escape(t)}\b", q):
        return True
    last = t.split()[-1] if t.split() else ""
    return len(last) >= 4 and re.search(rf"\b{re.escape(last)}\b", q) is not None


def _strict_binary_token(m: Market, outcome: str) -> str | None:
    if not m.active or m.closed or len(m.outcomes) != 2 or len(m.token_ids) != 2:
        return None
    labels = [str(x).strip().lower() for x in m.outcomes]
    tokens = [str(x).strip() for x in m.token_ids]
    if sorted(labels) != ["no", "yes"] or len(set(tokens)) != 2 or not all(tokens):
        return None
    wanted = outcome.strip().lower()
    if wanted not in {"yes", "no"} or labels.count(wanted) != 1:
        return None
    return tokens[labels.index(wanted)]


def _sports_source_timestamp(payload: dict) -> float | None:
    """Extract a finite causal timestamp from current sports-feed field variants."""
    for key in (
        "last_update", "lastUpdate", "updated_at", "updatedAt",
        "finished_timestamp", "finishedTimestamp",
    ):
        raw = payload.get(key)
        if raw in {None, ""}:
            continue
        if isinstance(raw, bool):
            continue
        try:
            numeric = float(raw)
        except (TypeError, ValueError, OverflowError):
            numeric = None
        if numeric is not None:
            if not math.isfinite(numeric):
                continue
            if numeric > 10_000_000_000:
                numeric /= 1000.0
            if numeric > 0:
                return numeric
        try:
            parsed = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        except (TypeError, ValueError, OverflowError):
            continue
        if parsed.tzinfo is None:
            continue
        numeric = parsed.astimezone(timezone.utc).timestamp()
        return numeric if math.isfinite(numeric) and numeric > 0 else None
    return None


def _sports_payload_fingerprint(payload: dict) -> str:
    try:
        return json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    except (TypeError, ValueError, OverflowError):
        return repr(sorted((str(k), repr(v)) for k, v in payload.items()))


def _causal_sports_payload(
    slug: str,
    payload: dict,
    *,
    now_ts: float | None = None,
) -> dict | None:
    source_ts = _sports_source_timestamp(payload)
    if source_ts is None:
        return None
    current = time.time() if now_ts is None else float(now_ts)
    age = current - source_ts
    if age < -5.0 or age > float(settings.sports_result_max_age_seconds):
        return None

    key = str(slug or "").strip()
    if not key:
        return None
    fingerprint = _sports_payload_fingerprint(payload)
    prior_ts = _SPORTS_CAUSAL_FLOOR.get(key)

    if prior_ts is None:
        _SPORTS_CAUSAL_FLOOR[key] = source_ts
        _SPORTS_CAUSAL_FINGERPRINT[key] = fingerprint
        _SPORTS_CAUSAL_QUARANTINED_AT.pop(key, None)
        return payload

    if source_ts < prior_ts - 1e-9:
        return None

    if math.isclose(source_ts, prior_ts, rel_tol=0.0, abs_tol=1e-9):
        if key in _SPORTS_CAUSAL_QUARANTINED_AT:
            return None
        if fingerprint == _SPORTS_CAUSAL_FINGERPRINT.get(key):
            return payload
        _SPORTS_CAUSAL_QUARANTINED_AT[key] = source_ts
        return None

    _SPORTS_CAUSAL_FLOOR[key] = source_ts
    _SPORTS_CAUSAL_FINGERPRINT[key] = fingerprint
    _SPORTS_CAUSAL_QUARANTINED_AT.pop(key, None)
    return payload


def _reset_sports_causal_state_for_tests() -> None:
    _SPORTS_CAUSAL_FLOOR.clear()
    _SPORTS_CAUSAL_FINGERPRINT.clear()
    _SPORTS_CAUSAL_QUARANTINED_AT.clear()


def _terminal_feed(payload: dict, *, now_ts: float | None = None) -> tuple[bool, str, float | None, float | None]:
    if payload.get("ended") is not True:
        return False, "sports feed has not explicitly ended the event", None, None
    for key in ("cancelled", "canceled", "postponed", "suspended", "abandoned", "void"):
        if payload.get(key) is True:
            return False, f"sports feed marks event {key}", None, None
    status_text = " ".join(
        str(payload.get(key) or "")
        for key in ("status", "state", "gameStatus", "game_status", "reason")
    )
    if _BAD_STATUS_WORDS.search(status_text):
        return False, f"sports feed terminal status is unsafe: {status_text.strip()}", None, None

    source_ts = _sports_source_timestamp(payload)
    if source_ts is None:
        return False, "sports terminal result lacks a parseable source timestamp", None, None
    current = time.time() if now_ts is None else float(now_ts)
    age = current - source_ts
    if age < -5.0:
        return False, "sports terminal result is future-dated", source_ts, age
    if age > float(settings.sports_result_max_age_seconds):
        return False, "sports terminal result is older than the configured result-lag window", source_ts, age
    return True, "feed explicitly ended recently with no cancellation/postponement marker", source_ts, max(0.0, age)


def _supported_match_moneyline(m: Market) -> tuple[bool, str]:
    q = str(m.question or "")
    scope = f"{m.question} {m.event_title} {m.slug}"
    if re.search(r"\bdraw\b", q, re.I):
        return False, "draw contracts require a sport/rules-specific adapter"
    if _UNSUPPORTED_MARKET_WORDS.search(scope) or _SIGNED_LINE.search(q):
        return False, (
            "spread/total/period/set/game/series or regulation/overtime/shootout-qualified "
            "semantics are not supported by the match-moneyline adapter"
        )

    raw_type = str(
        m.raw.get("sportsMarketType")
        or m.raw.get("sports_market_type")
        or m.raw.get("marketType")
        or ""
    ).strip().lower()
    if raw_type and raw_type not in {"moneyline", "money_line", "match winner", "match_winner", "winner"}:
        return False, f"explicit sports market type '{raw_type}' is not supported"
    if "win" not in q.lower():
        return False, "question is not a direct match-winner contract"
    return True, "narrow unqualified match-moneyline semantics passed"


def sports_result_lag_v3(
    markets: list[Market],
    books: dict[str, Book],
    cache: dict[str, dict],
    *,
    now_ts: float | None = None,
) -> list[Signal]:
    """Fail-closed sports known-result experiment.

    Only direct, unqualified match moneylines are evaluated. Spreads, totals, draws,
    periods, sets/games, series, regulation-only, overtime/extra-time/shootout and
    cancellation-like states are deliberately skipped until sport/rules-specific
    adapters exist. Score orientation is HOME-AWAY from explicit feed team fields.
    Source timestamps must advance monotonically for each event slug: older rows
    cannot overwrite newer evidence, and contradictory rows carrying the same source
    timestamp quarantine the slug until a strictly newer valid update arrives.
    """
    out: list[Signal] = []
    for m in markets:
        payload = cache.get(m.event_slug)
        if not isinstance(payload, dict):
            continue
        payload = _causal_sports_payload(m.event_slug, payload, now_ts=now_ts)
        if payload is None:
            continue
        terminal, terminal_reason, source_ts, source_age = _terminal_feed(payload, now_ts=now_ts)
        if not terminal:
            continue
        supported, scope_reason = _supported_match_moneyline(m)
        if not supported:
            continue

        score = _score(payload.get("score"))
        if not score:
            continue
        home_score, away_score = score
        if home_score == away_score:
            continue

        home_team = str(payload.get("homeTeam") or payload.get("home_team") or "").strip()
        away_team = str(payload.get("awayTeam") or payload.get("away_team") or "").strip()
        if not home_team or not away_team or _norm_team(home_team) == _norm_team(away_team):
            continue

        home_match = _team_mentioned(m.question, home_team)
        away_match = _team_mentioned(m.question, away_team)
        if home_match == away_match:
            continue

        if home_match:
            selected_team = home_team
            truth = home_score > away_score
        else:
            selected_team = away_team
            truth = away_score > home_score

        outcome = "YES" if truth else "NO"
        token = _strict_binary_token(m, outcome)
        if not token:
            continue
        book = books.get(token)
        if not book or book.best_ask is None or book.best_ask_size <= 0:
            continue
        ask = float(book.best_ask)
        if ask <= 0 or ask >= 1 or ask > settings.known_outcome_max_ask:
            continue
        cost = ask + taker_fee_per_share(ask)
        edge = 1.0 - cost
        if edge < settings.actionable_min_edge:
            continue

        why = (
            f"final home-away score {home_score:g}-{away_score:g}; "
            f"{selected_team} {'won' if truth else 'did not win'}"
        )
        out.append(Signal(
            detector=SPORTS_DETECTOR,
            confidence="ACTIONABLE",
            event_id=m.event_id,
            market_id=m.id,
            title="Sports match result known, market still discounted",
            detail=f"ENDED; {why}. {outcome} ask {ask:.3f}; post-fee edge {edge:.2%}.",
            url=market_url(m),
            edge=edge,
            entry_cost=cost,
            theoretical_payout=1.0,
            token_ids=[token],
            metadata={
                "trade_outcome": outcome,
                "ask": ask,
                "fingerprint_key": f"{m.id}:{outcome}:{SPORTS_MAPPING_VERSION}",
                "sports_reason": why,
                "sports_mapping_version": SPORTS_MAPPING_VERSION,
                "sports_causal_cache_version": SPORTS_CAUSAL_CACHE_VERSION,
                "sports_semantic_scope": "UNQUALIFIED_MATCH_MONEYLINE_ONLY",
                "sports_home_team": home_team,
                "sports_away_team": away_team,
                "sports_home_score": home_score,
                "sports_away_score": away_score,
                "sports_terminal_reason": terminal_reason,
                "sports_source_timestamp": source_ts,
                "sports_source_age_seconds": source_age,
                "sports_scope_reason": scope_reason,
                "experimental_resolution": True,
                "action_steps": [
                    "Research-only sports result-lag candidate during containment.",
                ],
                "risk_note": (
                    "Not promoted to TRADE NOW. Only narrow unqualified match-moneyline semantics with monotonic "
                    "source-time evidence are scored; spreads/totals/periods/sets/games/series/regulation/"
                    "overtime/shootout/cancellation cases are skipped."
                ),
                "links": [{"label": "OPEN MARKET", "url": market_url(m)}],
            },
        ))
    return out
