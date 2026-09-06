from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path

from .config import settings
from .detectors import binary_buy_both as _binary_buy_both
from .detectors import neg_risk_underround as _neg_risk_underround
from .detectors_v02 import nested_threshold_arbitrage as _nested_threshold_arbitrage, threshold
from .models import Book, Market, Signal


STRUCTURAL_DETECTORS = ("binary_buy_both", "neg_risk_underround", "nested_threshold_arb")
MIN_VISIBLE_NOTIONAL_USD = 10.0
MAX_MANUAL_LEGS = 6


def archive_legacy_structural_stats(db_path: str) -> int:
    """Remove old synthetic instant-win structural P&L from performance stats once.

    Older builds marked a structural alert WON immediately when quoted asks existed,
    which assumed every leg filled. That is not an observed trade result. Preserve the
    rows for audit/history, but exclude them from ACTIONABLE paper-performance stats.
    """
    path = Path(db_path)
    if not path.exists():
        return 0
    with sqlite3.connect(path) as c:
        c.execute("CREATE TABLE IF NOT EXISTS bot_state (key TEXT PRIMARY KEY, value TEXT)")
        marker = c.execute("SELECT value FROM bot_state WHERE key='structural_stats_v2_migrated'").fetchone()
        if marker:
            return 0
        qmarks = ",".join("?" for _ in STRUCTURAL_DETECTORS)
        rows = c.execute(
            f"SELECT id,status FROM signals WHERE detector IN ({qmarks}) AND confidence='ACTIONABLE'",
            STRUCTURAL_DETECTORS,
        ).fetchall()
        legacy_ids = [int(r[0]) for r in rows]
        if legacy_ids:
            ids = ",".join("?" for _ in legacy_ids)
            # If a manual trade inherited an immediate synthetic WIN from the signal,
            # return it to OPEN rather than claiming a realized result.
            c.execute(
                f"UPDATE manual_trades SET status='OPEN',pnl=NULL,resolved_at=NULL "
                f"WHERE signal_id IN ({ids}) AND status='WON'",
                legacy_ids,
            )
        c.execute(
            f"UPDATE signals SET confidence='LEGACY_THEORETICAL',status='LEGACY_THEORETICAL',"
            f"pnl=NULL,resolved_at=NULL WHERE detector IN ({qmarks}) AND confidence='ACTIONABLE'",
            STRUCTURAL_DETECTORS,
        )
        c.execute(
            "INSERT INTO bot_state(key,value) VALUES('structural_stats_v2_migrated',?)",
            (str(len(rows)),),
        )
        return len(rows)


def _visible_notional(signal: Signal, books: dict[str, Book]) -> tuple[float, float]:
    sizes = []
    for token in signal.token_ids:
        book = books.get(token)
        if not book or book.best_ask is None or book.best_ask_size <= 0:
            return 0.0, 0.0
        sizes.append(float(book.best_ask_size))
    common = min(sizes) if sizes else 0.0
    cost = float(signal.entry_cost or 0.0)
    return common, common * cost


def _set_execution_meta(signal: Signal, common_shares: float, notional: float) -> None:
    signal.metadata["structural_arb"] = True
    signal.metadata["immediate_settlement"] = False
    signal.metadata["paper_accounting"] = "unscored_until_actual_resolution_or_execution"
    signal.metadata["visible_common_shares"] = common_shares
    signal.metadata["max_visible_notional_usd"] = notional


def _demote(signal: Signal, reason: str, *, title: str | None = None) -> Signal:
    signal.confidence = "WATCH"
    if title:
        signal.title = title
    signal.metadata["certification_status"] = "NOT_ACTIONABLE"
    signal.metadata["certification_reason"] = reason
    signal.metadata["action_steps"] = [
        "Open the event and inspect the live Rules/order books.",
        "Treat this as a research lead only; do NOT execute it as a locked arbitrage from this alert.",
    ]
    signal.metadata["risk_note"] = reason
    return signal


def hardened_binary_buy_both(markets: list[Market], books: dict[str, Book]) -> list[Signal]:
    out = _binary_buy_both(markets, books)
    for s in out:
        common, notional = _visible_notional(s, books)
        _set_execution_meta(s, common, notional)
        s.metadata["certification_status"] = "BINARY_COMPLEMENT_VERIFIED"
        s.metadata["action_steps"] = [
            "Tap OPEN MARKET below.",
            f"Buy YES and NO using the SAME share count, no more than {common:.2f} shares at the quoted top-of-book prices.",
            "Both legs must fill; never keep only one leg.",
            "If either ask is higher or the displayed size is smaller, SKIP and wait for a fresh alert.",
        ]
        s.metadata["risk_note"] = (
            "The YES/NO payoff is complementary, but the scanner does not place orders. "
            "Paper P&L is not credited unless an actual outcome is later observed."
        )
        if notional < MIN_VISIBLE_NOTIONAL_USD:
            _demote(
                s,
                f"Only about ${notional:.2f} is visible at the quoted asks; below the ${MIN_VISIBLE_NOTIONAL_USD:.0f} manual-execution floor.",
                title="Binary underround (too little executable size)",
            )
    return out


def _event_neg_risk_id(rows: list[Market]) -> str:
    event = rows[0].raw.get("_event") or {}
    event_id = str(event.get("negRiskMarketID") or event.get("negRiskMarketId") or "").strip()
    if event_id:
        return event_id
    ids = {
        str(m.raw.get("negRiskMarketID") or m.raw.get("negRiskMarketId") or "").strip()
        for m in rows
    }
    ids.discard("")
    return next(iter(ids)) if len(ids) == 1 else ""


def _neg_risk_certification(rows: list[Market]) -> tuple[bool, str]:
    if not rows:
        return False, "No event markets were available."
    event = rows[0].raw.get("_event") or {}
    if not bool(event.get("negRisk") or event.get("enableNegRisk")):
        return False, "Parent event is not explicitly flagged as negative-risk by Gamma."
    if not _event_neg_risk_id(rows):
        return False, "No consistent neg-risk market identifier was present in the event metadata."

    raw_children = [x for x in (event.get("markets") or []) if isinstance(x, dict)]
    if not raw_children:
        return False, "Parent event did not expose its full child-market list."
    active_children = {
        str(x.get("id") or "")
        for x in raw_children
        if str(x.get("id") or "") and bool(x.get("active", True)) and not bool(x.get("closed", False))
    }
    row_ids = {m.id for m in rows}
    if active_children != row_ids:
        return False, "The scanner does not currently hold every active child market in the neg-risk event."

    for m in rows:
        labels = {x.strip().lower() for x in m.outcomes}
        if labels != {"yes", "no"} or not m.yes_token or not m.no_token:
            return False, "At least one child is not an ordinary binary YES/NO market."

    # For manual trading we demand an explicit catch-all outcome. This is deliberately
    # stricter than merely trusting negRisk=true: it prevents a missing/unlisted outcome
    # from being mistaken for a complete $1 payout basket.
    child_text = " ".join(
        f"{x.get('question') or ''} {x.get('slug') or ''}" for x in raw_children
    ).lower()
    rules = f"{event.get('description') or ''} {rows[0].description}".lower()
    has_other_child = bool(re.search(r"\bother\b", child_text))
    has_other_rule = bool(re.search(r"(?:resolve|resolves|resolved)\s+(?:to|as|in\s+favor\s+of)[^.!]{0,80}\bother\b", rules))
    if not (has_other_child and has_other_rule):
        return False, "Exhaustiveness is not explicit enough: an active 'Other' fallback plus matching rule text was not verified."
    return True, "Gamma neg-risk ID, full active child set, binary structure, and explicit Other fallback were verified."


def hardened_neg_risk_underround(markets: list[Market], books: dict[str, Book]) -> list[Signal]:
    signals = _neg_risk_underround(markets, books)
    by_event: dict[str, list[Market]] = {}
    for m in markets:
        if m.event_neg_risk:
            by_event.setdefault(m.event_id, []).append(m)

    for s in signals:
        rows = by_event.get(s.event_id, [])
        certified, reason = _neg_risk_certification(rows)
        common, notional = _visible_notional(s, books)
        _set_execution_meta(s, common, notional)
        s.metadata["certification_status"] = "NEG_RISK_CERTIFIED" if certified else "NOT_ACTIONABLE"
        s.metadata["certification_reason"] = reason
        s.metadata["action_steps"] = [
            "Tap OPEN EVENT below.",
            f"Use the SAME share count on every leg, capped at {common:.2f} shares at the quoted asks.",
            "Before starting, confirm every listed leg is still available at or below the alert price.",
            "If even one leg moves or cannot fill, SKIP the entire basket.",
        ]
        s.metadata["risk_note"] = reason

        if not certified:
            _demote(s, reason, title="Neg-risk underround (structure not fully certified)")
        elif len(s.token_ids) > MAX_MANUAL_LEGS:
            _demote(
                s,
                f"The basket needs {len(s.token_ids)} separate legs; above the {MAX_MANUAL_LEGS}-leg manual-execution limit. Price movement while clicking can destroy the edge.",
                title="Neg-risk underround (too many legs for manual execution)",
            )
        elif notional < MIN_VISIBLE_NOTIONAL_USD:
            _demote(
                s,
                f"Only about ${notional:.2f} is simultaneously visible at the quoted asks; below the ${MIN_VISIBLE_NOTIONAL_USD:.0f} manual-execution floor.",
                title="Neg-risk underround (too little executable size)",
            )
    return signals


def _norm_text(value: str) -> str:
    return " ".join(str(value or "").lower().split())


def _unit_signature(question: str) -> tuple[bool, bool, tuple[str, ...]]:
    low = question.lower()
    currencies = tuple(x for x in ("usd", "eur", "gbp", "jpy", "btc", "eth", "sol", "xrp") if re.search(rf"\b{x}\b", low))
    return "$" in question, "%" in question, currencies


def _nested_certification(a: Market, b: Market) -> tuple[bool, str]:
    if a.event_id != b.event_id:
        return False, "Markets are not in the same parent event."
    if not a.active or not b.active or a.closed or b.closed:
        return False, "One of the two markets is not active/open."
    if {x.lower() for x in a.outcomes} != {"yes", "no"} or {x.lower() for x in b.outcomes} != {"yes", "no"}:
        return False, "Both threshold contracts must be binary YES/NO markets."
    pa, pb = threshold(a.question), threshold(b.question)
    if not pa or not pb or pa[0] != pb[0] or pa[2] != pb[2]:
        return False, "Question templates do not reduce to the same monotonic threshold condition."
    if _unit_signature(a.question) != _unit_signature(b.question):
        return False, "Threshold units/currency signatures differ."
    if not a.end_date or not b.end_date or a.end_date != b.end_date:
        return False, "Market end/boundary timestamps are not identical."
    if not a.resolution_source or _norm_text(a.resolution_source) != _norm_text(b.resolution_source):
        return False, "Resolution sources are missing or differ."
    if not a.description or _norm_text(a.description) != _norm_text(b.description):
        return False, "Resolution-rule descriptions are missing or differ."
    return True, "Same event, binary structure, normalized threshold template, units, end time, source, and rules text were verified."


def hardened_nested_threshold_arbitrage(markets: list[Market], books: dict[str, Book]) -> list[Signal]:
    signals = _nested_threshold_arbitrage(markets, books)
    by_id = {m.id: m for m in markets}
    for s in signals:
        key = str(s.metadata.get("fingerprint_key") or "")
        left, _, right = key.partition(":")
        a, b = by_id.get(left), by_id.get(right)
        if not a or not b:
            certified, reason = False, "Could not recover both threshold markets from the current universe."
        else:
            certified, reason = _nested_certification(a, b)
        common, notional = _visible_notional(s, books)
        _set_execution_meta(s, common, notional)
        s.metadata["certification_status"] = "NESTED_RULES_CERTIFIED" if certified else "NOT_ACTIONABLE"
        s.metadata["certification_reason"] = reason
        if certified:
            s.metadata["risk_note"] = reason + " Both legs must still fill at the quoted prices."
            s.metadata["action_steps"] = [
                s.metadata.get("action_steps", ["Open both markets."])[0],
                s.metadata.get("action_steps", ["", "Open the second market."])[1],
                f"Use the SAME share count on both legs, capped at {common:.2f} shares at the quoted asks.",
                "If either ask is higher or either visible size is smaller, SKIP.",
            ]
        if not certified:
            _demote(s, reason, title="Logical threshold spread (rules not fully certified)")
        elif notional < MIN_VISIBLE_NOTIONAL_USD:
            _demote(
                s,
                f"Only about ${notional:.2f} is simultaneously visible at the quoted asks; below the ${MIN_VISIBLE_NOTIONAL_USD:.0f} manual-execution floor.",
                title="Logical threshold spread (too little executable size)",
            )
    return signals
