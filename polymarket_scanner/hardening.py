from __future__ import annotations

import math
import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from .config import settings
from .detectors import binary_buy_both as _binary_buy_both
from .detectors import neg_risk_underround as _neg_risk_underround
from .detectors_v02 import nested_threshold_arbitrage as _nested_threshold_arbitrage, threshold
from .models import Book, Market, Signal


STRUCTURAL_DETECTORS = ("binary_buy_both", "neg_risk_underround", "nested_threshold_arb")
MIN_VISIBLE_NOTIONAL_USD = 10.0
MAX_MANUAL_LEGS = 6
RULE_QUARANTINE_VERSION = "structural_rule_semantics_quarantine_v1"


@dataclass(frozen=True)
class StrictBinary:
    yes_token: str
    no_token: str


@dataclass(frozen=True)
class ThresholdContract:
    comparator: str  # gt/ge/lt/le
    value: float
    template: str


def archive_legacy_structural_stats(db_path: str) -> int:
    """Remove old synthetic instant-win structural P&L from performance stats once."""
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


def _strict_binary(m: Market) -> tuple[StrictBinary | None, str]:
    """Prove token/outcome identity without Market.yes_token/no_token fallbacks.

    Structural certification must never infer YES/NO merely because a market has two
    token IDs. We require exactly two labelled outcomes, exactly two distinct token
    IDs, and an unambiguous one-to-one mapping of labels to tokens.
    """
    if not m.active or m.closed:
        return None, "market is not active/open"
    if not str(m.condition_id or "").strip():
        return None, "condition ID is missing"
    if len(m.outcomes) != 2 or len(m.token_ids) != 2:
        return None, "market does not expose exactly two outcomes and two tokens"
    labels = [str(x).strip().lower() for x in m.outcomes]
    tokens = [str(x).strip() for x in m.token_ids]
    if sorted(labels) != ["no", "yes"]:
        return None, "outcome labels are not exactly YES and NO"
    if not all(tokens) or len(set(tokens)) != 2:
        return None, "binary token IDs are missing or duplicated"
    if labels.count("yes") != 1 or labels.count("no") != 1:
        return None, "YES/NO outcome mapping is ambiguous"
    yes_token = tokens[labels.index("yes")]
    no_token = tokens[labels.index("no")]
    return StrictBinary(yes_token=yes_token, no_token=no_token), "exact YES/NO label-to-token mapping verified"


def _visible_notional(signal: Signal, books: dict[str, Book]) -> tuple[float, float]:
    sizes = []
    for token in signal.token_ids:
        book = books.get(token)
        if not book or book.best_ask is None or book.best_ask_size <= 0:
            return 0.0, 0.0
        sizes.append(float(book.best_ask_size))
    common = min(sizes) if sizes else 0.0
    try:
        cost = float(signal.entry_cost or 0.0)
    except (TypeError, ValueError, OverflowError):
        return 0.0, 0.0
    if not math.isfinite(cost) or cost <= 0:
        return 0.0, 0.0
    return common, common * cost


def _set_execution_meta(signal: Signal, common_shares: float, notional: float) -> None:
    signal.metadata["structural_arb"] = True
    signal.metadata["immediate_settlement"] = False
    signal.metadata["paper_accounting"] = "execution_unverified_no_realized_profit"
    signal.metadata["visible_common_shares"] = common_shares
    signal.metadata["max_visible_notional_usd"] = notional


def _demote(signal: Signal, reason: str, *, title: str | None = None) -> Signal:
    signal.confidence = "WATCH"
    if title:
        signal.title = title
    signal.metadata["certification_status"] = "NOT_ACTIONABLE"
    signal.metadata["certification_reason"] = reason
    signal.metadata["action_steps"] = [
        "Research only; do not execute this as a locked arbitrage.",
    ]
    signal.metadata["risk_note"] = reason
    return signal


def _quarantine_rule_claims(signals: list[Signal]) -> list[Signal]:
    """Text/metadata checks are necessary conditions, not a rules interpreter.

    Nested and Other-fallback semantics have not earned a guaranteed payoff claim.
    Keep discovery observations but withdraw economic authority and scored edges.
    """
    for signal in signals:
        meta = signal.metadata
        meta["rule_quarantine_version"] = RULE_QUARANTINE_VERSION
        meta["hypothetical_edge"] = signal.edge
        meta["hypothetical_payout"] = signal.theoretical_payout
        meta["semantic_evidence_valid"] = False
        proof = meta.get("payoff_proof", {})
        proof.pop("minimum_bundle_payout", None)
        proof["validated_for_contract_resolution"] = False
        if signal.confidence == "ACTIONABLE":
            _demote(signal, "Contract rule semantics are unverified; text matching is not a guaranteed payoff proof.")
        meta["certification_status"] = "NOT_ACTIONABLE"
        signal.edge = signal.theoretical_payout = None
    return signals


def hardened_binary_buy_both(markets: list[Market], books: dict[str, Book]) -> list[Signal]:
    out = _binary_buy_both(markets, books)
    by_id = {m.id: m for m in markets}
    for s in out:
        common, notional = _visible_notional(s, books)
        _set_execution_meta(s, common, notional)
        m = by_id.get(str(s.market_id or ""))
        strict, why = _strict_binary(m) if m else (None, "current market could not be recovered")
        expected = [strict.yes_token, strict.no_token] if strict else []
        tokens_match = bool(strict) and len(s.token_ids) == 2 and s.token_ids == expected
        if not strict or not tokens_match:
            reason = why if not strict else "purchased tokens are not the exact mapped YES + NO pair"
            _demote(s, reason, title="Binary underround (payoff identity not proven)")
            continue

        s.metadata["certification_status"] = "BINARY_COMPLEMENT_PROOF_V2"
        s.metadata["certification_reason"] = (
            "Exactly two labelled outcomes map one-to-one to the purchased YES and NO tokens. "
            "The quote is still execution-unverified and is not booked as realized profit."
        )
        s.metadata["payoff_proof"] = {
            "version": "binary_complement_v2",
            "market_id": m.id,
            "yes_token": strict.yes_token,
            "no_token": strict.no_token,
            "minimum_bundle_payout": 1.0,
        }
        s.metadata["action_steps"] = [
            "Research-only structural candidate during P0 containment.",
            "If eventually promoted, both exact YES/NO legs must fill at or below the certified prices.",
        ]
        s.metadata["risk_note"] = "Mathematical payoff identity is proven; real execution is not."
        if notional < MIN_VISIBLE_NOTIONAL_USD:
            _demote(
                s,
                f"Only about ${notional:.2f} is visible at the quoted asks; below the ${MIN_VISIBLE_NOTIONAL_USD:.0f} manual-execution floor.",
                title="Binary underround (too little executable size)",
            )
    return out


def _neg_risk_ids(rows: list[Market], raw_children: list[dict]) -> tuple[str | None, str]:
    event = rows[0].raw.get("_event") or {}
    parent = str(event.get("negRiskMarketID") or event.get("negRiskMarketId") or "").strip()
    if not parent:
        return None, "parent neg-risk market identifier is missing"
    seen = set()
    for m in rows:
        value = str(m.raw.get("negRiskMarketID") or m.raw.get("negRiskMarketId") or parent).strip()
        if not value:
            return None, "a child neg-risk identifier is missing"
        seen.add(value)
    for child in raw_children:
        value = str(child.get("negRiskMarketID") or child.get("negRiskMarketId") or parent).strip()
        if not value:
            return None, "a raw child neg-risk identifier is missing"
        seen.add(value)
    if seen != {parent}:
        return None, "neg-risk identifiers are inconsistent across parent/children"
    return parent, "consistent parent/child neg-risk identifier verified"


def _is_other_child(child: dict) -> bool:
    text = f"{child.get('question') or ''} {child.get('slug') or ''}".lower()
    return re.search(r"\bother\b", text) is not None


def _neg_risk_certification(rows: list[Market], signal: Signal) -> tuple[bool, str, dict]:
    """Fail-closed proof that the purchased YES basket covers the whole parent set.

    Polymarket's NegRiskAdapter enforces mutual exclusion, but its own contract docs
    explicitly note that an all-false outcome is economically possible if a market
    was prepared incorrectly. We therefore never infer a $1 floor merely from the
    negRisk flag/ID. The certificate additionally requires one explicit rule-linked
    Other fallback and purchases every child in the complete parent list. Closed or
    omitted children are fatal because one of them could be the sole YES winner.
    """
    proof: dict = {"version": "neg_risk_complete_parent_set_v3"}
    if not rows:
        return False, "No event markets were available.", proof
    event = rows[0].raw.get("_event") or {}
    if not bool(event.get("negRisk") or event.get("enableNegRisk")):
        return False, "Parent event is not explicitly flagged as negative-risk by Gamma.", proof
    if event.get("negRiskAugmented") is True:
        return False, (
            "Augmented neg-risk events are not payoff-certified: placeholder/Other membership can change over time."
        ), proof

    raw_children = [x for x in (event.get("markets") or []) if isinstance(x, dict)]
    if not raw_children:
        return False, "Parent event did not expose its full child-market list.", proof
    neg_id, id_reason = _neg_risk_ids(rows, raw_children)
    if not neg_id:
        return False, id_reason, proof

    full_ids: list[str] = []
    for child in raw_children:
        child_id = str(child.get("id") or "").strip()
        if not child_id:
            return False, "A parent child market ID is missing.", proof
        if type(child.get("active")) is not bool or type(child.get("closed")) is not bool:
            return False, "A parent child has incomplete active/closed state; complete-set proof fails closed.", proof
        if child.get("active") is not True or child.get("closed") is not False:
            return False, (
                "At least one parent child is inactive/closed; buying only currently open children cannot prove a $1 payout floor."
            ), proof
        full_ids.append(child_id)

    row_ids = [str(m.id) for m in rows]
    if len(full_ids) != len(set(full_ids)) or len(row_ids) != len(set(row_ids)):
        return False, "Duplicate child market IDs prevent an exhaustive-set proof.", proof
    if set(full_ids) != set(row_ids) or len(full_ids) != len(row_ids):
        return False, "The scanner does not hold exactly every child market in the complete neg-risk parent set.", proof

    strict_by_id: dict[str, StrictBinary] = {}
    for m in rows:
        strict, reason = _strict_binary(m)
        if not strict:
            return False, f"Child {m.id} is not a strict binary market: {reason}.", proof
        strict_by_id[m.id] = strict

    other_children = [x for x in raw_children if _is_other_child(x)]
    if len(other_children) != 1:
        return False, "Exactly one open 'Other' fallback child was not verified in the complete parent set.", proof
    other_id = str(other_children[0].get("id"))
    if other_id not in strict_by_id:
        return False, "The open 'Other' fallback is not present in the purchased complete child set.", proof

    expected_yes = [strict_by_id[mid].yes_token for mid in row_ids]
    if len(expected_yes) != len(set(expected_yes)):
        return False, "Purchased YES token mapping contains duplicates.", proof
    if len(signal.token_ids) != len(expected_yes) or set(signal.token_ids) != set(expected_yes):
        return False, "The basket does not purchase the exact YES token of every parent child, including Other.", proof
    other_yes = strict_by_id[other_id].yes_token
    if other_yes not in signal.token_ids:
        return False, "The Other YES token is not purchased.", proof

    rules = f"{event.get('description') or ''} {rows[0].description}".lower()
    has_other_rule = bool(
        re.search(r"(?:resolve|resolves|resolved|resolution)[^.!]{0,120}\bother\b", rules)
        or re.search(r"\bother\b[^.!]{0,120}(?:resolve|resolves|resolved|resolution)", rules)
    )
    if not has_other_rule:
        return False, "Rules do not explicitly connect the fallback resolution to Other.", proof

    proof.update({
        "neg_risk_market_id": neg_id,
        "parent_child_market_ids": sorted(full_ids),
        "purchased_yes_tokens": sorted(signal.token_ids),
        "other_market_id": other_id,
        "other_yes_token": other_yes,
        "complete_parent_child_count": len(full_ids),
        "all_parent_children_open": True,
        "neg_risk_augmented": False,
        "minimum_bundle_payout": 1.0,
    })
    return True, (
        "Parent/children share one neg-risk ID; every parent child is explicitly open, present and strict binary; "
        "the exact YES token of every parent child is purchased; and exactly one purchased Other fallback is rule-linked."
    ), proof


def hardened_neg_risk_underround(markets: list[Market], books: dict[str, Book]) -> list[Signal]:
    signals = _neg_risk_underround(markets, books)
    by_event: dict[str, list[Market]] = {}
    for m in markets:
        if m.event_neg_risk:
            by_event.setdefault(m.event_id, []).append(m)

    for s in signals:
        rows = by_event.get(s.event_id, [])
        certified, reason, proof = _neg_risk_certification(rows, s)
        common, notional = _visible_notional(s, books)
        _set_execution_meta(s, common, notional)
        s.metadata["certification_status"] = "NEG_RISK_COMPLETE_SET_PROOF_V3" if certified else "NOT_ACTIONABLE"
        s.metadata["certification_reason"] = reason
        s.metadata["payoff_proof"] = proof
        s.metadata["risk_note"] = reason

        if not certified:
            _demote(s, reason, title="Neg-risk underround (payoff completeness not proven)")
        elif len(s.token_ids) > MAX_MANUAL_LEGS:
            _demote(
                s,
                f"The basket needs {len(s.token_ids)} separate legs; above the {MAX_MANUAL_LEGS}-leg manual-execution limit.",
                title="Neg-risk underround (too many legs for manual execution)",
            )
        elif notional < MIN_VISIBLE_NOTIONAL_USD:
            _demote(
                s,
                f"Only about ${notional:.2f} is simultaneously visible at the quoted asks; below the ${MIN_VISIBLE_NOTIONAL_USD:.0f} manual-execution floor.",
                title="Neg-risk underround (too little executable size)",
            )
        else:
            s.metadata["action_steps"] = [
                "Research-only structural candidate during P0 containment.",
                "Any future promotion must revalidate every parent child and fill every purchased YES leg.",
            ]
    return _quarantine_rule_claims(signals)


def _norm_text(value: str) -> str:
    return " ".join(str(value or "").lower().split())


def _unit_signature(question: str) -> tuple[bool, bool, tuple[str, ...]]:
    low = question.lower()
    currencies = tuple(x for x in ("usd", "eur", "gbp", "jpy", "btc", "eth", "sol", "xrp") if re.search(rf"\b{x}\b", low))
    return "$" in question, "%" in question, currencies


def _threshold_contract(question: str) -> ThresholdContract | None:
    """Conservatively recover comparator semantics for nested-threshold proof.

    The legacy parser collapses > with >= and < with <=. That is sufficient for
    discovery but not certification. We therefore separately identify the exact
    comparator wording and refuse ambiguous/unsupported wording.
    """
    parsed = threshold(question)
    if not parsed:
        return None
    direction, value, template = parsed
    q = question.lower().replace("≥", " at least ").replace("≤", " at most ")
    if re.search(r"\b(?:not|never|neither|unless|except|without)\b|n['’]t\b", q):
        return None

    # We only certify one unambiguous comparator family. If conflicting phrases are
    # present, fail closed rather than guessing which number they qualify.
    ge = bool(re.search(r"\bat\s+least\b", q) or re.search(r"\bor\s+higher\b", q) or re.search(r"\bor\s+above\b", q))
    le = bool(re.search(r"\bat\s+most\b", q) or re.search(r"\bor\s+lower\b", q) or re.search(r"\bor\s+below\b", q))
    gt = bool(re.search(r"\b(?:above|over|higher\s+than|more\s+than)\b", q)) and not ge
    lt = bool(re.search(r"\b(?:below|under|lower\s+than|less\s+than)\b", q)) and not le

    matches = [name for name, present in (("ge", ge), ("le", le), ("gt", gt), ("lt", lt)) if present]
    if len(matches) != 1:
        return None
    comparator = matches[0]
    if direction == "above" and comparator not in {"gt", "ge"}:
        return None
    if direction == "below" and comparator not in {"lt", "le"}:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    if not math.isfinite(number):
        return None
    return ThresholdContract(comparator=comparator, value=number, template=str(template))


def _looser_stricter(a: Market, b: Market) -> tuple[Market, Market, ThresholdContract, ThresholdContract] | None:
    pa = _threshold_contract(a.question)
    pb = _threshold_contract(b.question)
    if not pa or not pb:
        return None
    if pa.comparator != pb.comparator or pa.template != pb.template:
        return None
    if math.isclose(pa.value, pb.value, rel_tol=1e-12, abs_tol=1e-12):
        return None

    if pa.comparator in {"gt", "ge"}:
        return (a, b, pa, pb) if pa.value < pb.value else (b, a, pb, pa)
    if pa.comparator in {"lt", "le"}:
        return (a, b, pa, pb) if pa.value > pb.value else (b, a, pb, pa)
    return None


def _nested_certification(a: Market, b: Market, signal: Signal) -> tuple[bool, str, dict]:
    proof: dict = {"version": "nested_implication_v2"}
    if a.event_id != b.event_id:
        return False, "Markets are not in the same parent event.", proof
    ba, why_a = _strict_binary(a)
    bb, why_b = _strict_binary(b)
    if not ba or not bb:
        return False, f"Both threshold markets must be strict binary contracts ({why_a}; {why_b}).", proof
    relation = _looser_stricter(a, b)
    if relation is None:
        return False, "Threshold implication could not be formally proven from unambiguous matching comparator semantics.", proof
    loose, strict, p_loose, p_strict = relation
    b_loose = ba if loose.id == a.id else bb
    b_strict = bb if strict.id == b.id else ba

    if _unit_signature(a.question) != _unit_signature(b.question):
        return False, "Threshold units/currency signatures differ.", proof
    if re.findall(r"\b(?:19|20)\d{2}\b", a.question) != re.findall(r"\b(?:19|20)\d{2}\b", b.question):
        return False, "Question years differ; parser year normalization is not rule identity.", proof
    if not a.end_date or not b.end_date or a.end_date != b.end_date:
        return False, "Market end/boundary timestamps are not identical.", proof
    if not a.resolution_source or _norm_text(a.resolution_source) != _norm_text(b.resolution_source):
        return False, "Resolution sources are missing or differ.", proof
    if not a.description or _norm_text(a.description) != _norm_text(b.description):
        return False, "Resolution-rule descriptions are missing or differ.", proof

    expected = [b_loose.yes_token, b_strict.no_token]
    if len(signal.token_ids) != 2 or signal.token_ids != expected:
        return False, "Purchased legs are not exactly looser YES plus stricter NO in proof order.", proof
    if len(set(signal.token_ids)) != 2:
        return False, "Purchased threshold tokens are duplicated.", proof

    # Formal payoff argument: strict YES => loose YES. Therefore loose NO implies
    # strict NO. The purchased pair (loose YES, strict NO) cannot both be zero.
    proof.update({
        "comparator": p_loose.comparator,
        "looser_market_id": loose.id,
        "stricter_market_id": strict.id,
        "looser_threshold": p_loose.value,
        "stricter_threshold": p_strict.value,
        "looser_yes_token": b_loose.yes_token,
        "stricter_no_token": b_strict.no_token,
        "implication": "stricter YES implies looser YES",
        "minimum_bundle_payout": 1.0,
    })
    return True, (
        "Formal monotone implication verified with identical comparator semantics, units, boundary time, source and rules; "
        "the purchased legs are exactly looser YES + stricter NO, so both cannot lose."
    ), proof


def hardened_nested_threshold_arbitrage(markets: list[Market], books: dict[str, Book]) -> list[Signal]:
    signals = _nested_threshold_arbitrage(markets, books)
    by_id = {m.id: m for m in markets}
    for s in signals:
        key = str(s.metadata.get("fingerprint_key") or "")
        left, sep, right = key.partition(":")
        a, b = by_id.get(left), by_id.get(right)
        if not sep or not a or not b:
            certified, reason, proof = False, "Could not recover both threshold markets from the current universe.", {"version": "nested_implication_v2"}
        else:
            certified, reason, proof = _nested_certification(a, b, s)
        common, notional = _visible_notional(s, books)
        _set_execution_meta(s, common, notional)
        s.metadata["certification_status"] = "NESTED_PAYOFF_PROOF_V2" if certified else "NOT_ACTIONABLE"
        s.metadata["certification_reason"] = reason
        s.metadata["payoff_proof"] = proof
        if certified:
            s.metadata["risk_note"] = reason + " Execution remains unverified."
            s.metadata["action_steps"] = [
                "Research-only structural candidate during P0 containment.",
                "Any future promotion must fill exactly the proof-bearing looser-YES and stricter-NO legs.",
            ]
        if not certified:
            _demote(s, reason, title="Logical threshold spread (payoff implication not proven)")
        elif notional < MIN_VISIBLE_NOTIONAL_USD:
            _demote(
                s,
                f"Only about ${notional:.2f} is simultaneously visible at the quoted asks; below the ${MIN_VISIBLE_NOTIONAL_USD:.0f} manual-execution floor.",
                title="Logical threshold spread (too little executable size)",
            )
    return _quarantine_rule_claims(signals)
