from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path

from .config import settings
from .detectors import market_url
from .models import Book, Market, Signal
from .polymarket import taker_fee_per_share

SPORTS_MAPPING_VERSION = "home_away_v3_match_moneyline_only"
SPORTS_DETECTOR = "sports_result_lag_v3"

_BAD_STATUS_WORDS = re.compile(
    r"\b(?:cancelled|canceled|postponed|suspended|abandoned|void|no\s+contest|delayed)\b",
    re.I,
)
_UNSUPPORTED_MARKET_WORDS = re.compile(
    r"\b(?:spread|handicap|over|under|o/u|total|set|period|quarter|half|inning|map|round|"
    r"series|race\s+to|margin|first\s+to|game\s+\d|set\s+\d|period\s+\d)\b",
    re.I,
)
_SIGNED_LINE = re.compile(r"(?:^|[\s(])[-+]\d+(?:\.\d+)?(?:[\s)]|$)")


def quarantine_pre_v3_sports_history(db_path: str) -> int:
    """Make every legacy sports_result_lag row fail the old-valid-version audit.

    The v2 home/away fix corrected title orientation but Astra showed that spread,
    period and cancellation semantics remained unsafe. Preserve those rows and their
    recorded outcomes for forensic audit, but rewrite only the mapping-version tag so
    Store._sports_pre_fix classifies them as KNOWN_BUG_EXCLUDED. New v3 signals use a
    separate detector ID and therefore begin a clean prospective sample.
    """
    path = Path(db_path)
    if not path.exists():
        return 0
    marker_key = "sports_pre_v3_quarantined"
    changed = 0
    with sqlite3.connect(path) as c:
        c.row_factory = sqlite3.Row
        c.execute("CREATE TABLE IF NOT EXISTS bot_state (key TEXT PRIMARY KEY, value TEXT)")
        marker = c.execute("SELECT value FROM bot_state WHERE key=?", (marker_key,)).fetchone()
        if marker:
            return 0
        rows = c.execute(
            "SELECT id,metadata FROM signals WHERE detector='sports_result_lag'"
        ).fetchall()
        for row in rows:
            try:
                meta = json.loads(row["metadata"] or "{}")
            except Exception:
                meta = {}
            if not isinstance(meta, dict):
                meta = {}
            meta["sports_mapping_version"] = "PRE_V3_QUARANTINED"
            meta["sports_quarantine_reason"] = (
                "pre-v3 sports logic did not safely distinguish spreads/periods/cancellations"
            )
            c.execute(
                "UPDATE signals SET metadata=? WHERE id=?",
                (json.dumps(meta, separators=(",", ":")), int(row["id"])),
            )
            changed += 1
        c.execute(
            "INSERT INTO bot_state(key,value) VALUES(?,?)",
            (marker_key, str(changed)),
        )
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


def _terminal_feed(payload: dict) -> tuple[bool, str]:
    if payload.get("ended") is not True:
        return False, "sports feed has not explicitly ended the event"
    for key in ("cancelled", "canceled", "postponed", "suspended", "abandoned", "void"):
        if payload.get(key) is True:
            return False, f"sports feed marks event {key}"
    status_text = " ".join(
        str(payload.get(key) or "")
        for key in ("status", "state", "gameStatus", "game_status", "reason")
    )
    if _BAD_STATUS_WORDS.search(status_text):
        return False, f"sports feed terminal status is unsafe: {status_text.strip()}"
    return True, "feed explicitly ended with no cancellation/postponement marker"


def _supported_match_moneyline(m: Market) -> tuple[bool, str]:
    q = str(m.question or "")
    scope = f"{m.question} {m.event_title} {m.slug}"
    if re.search(r"\bdraw\b", q, re.I):
        return False, "draw contracts require a sport/rules-specific adapter"
    if _UNSUPPORTED_MARKET_WORDS.search(scope) or _SIGNED_LINE.search(q):
        return False, "spread/total/period/set/game/series semantics are not supported by the match-moneyline adapter"

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
    return True, "narrow match-moneyline semantics passed"


def sports_result_lag_v3(markets: list[Market], books: dict[str, Book], cache: dict[str, dict]) -> list[Signal]:
    """Fail-closed sports known-result experiment.

    Only direct match moneylines are evaluated. Spreads, totals, draws, periods,
    sets/games, series and cancellation-like states are deliberately skipped until
    they have their own rule-aware adapters. Score orientation is always HOME-AWAY
    from explicit feed team fields, never event-title order.
    """
    out: list[Signal] = []
    for m in markets:
        payload = cache.get(m.event_slug)
        if not isinstance(payload, dict):
            continue
        terminal, terminal_reason = _terminal_feed(payload)
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
                "sports_semantic_scope": "MATCH_MONEYLINE_ONLY",
                "sports_home_team": home_team,
                "sports_away_team": away_team,
                "sports_home_score": home_score,
                "sports_away_score": away_score,
                "sports_terminal_reason": terminal_reason,
                "sports_scope_reason": scope_reason,
                "experimental_resolution": True,
                "action_steps": [
                    "Research-only sports result-lag candidate during P0 containment.",
                ],
                "risk_note": (
                    "Not promoted to TRADE NOW. Only narrow match-moneyline semantics are scored; "
                    "all spreads/totals/periods/sets/games/series/cancellation cases are skipped."
                ),
                "links": [{"label": "OPEN MARKET", "url": market_url(m)}],
            },
        ))
    return out
