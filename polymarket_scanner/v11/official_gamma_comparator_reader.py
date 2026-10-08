"""Pure, offline diagnostic reader over retained Gamma closed-market payout
evidence for one event/rule, pinned to a caller-supplied read frontier.

Given an already-constructed `EvidenceStore`, an explicit `event_id`, a
`RuleFingerprint`, and a `through_seq` the caller already pinned (e.g. via
`store.pin_read_view(...)`), this module scans every retained `RULES`/`LABEL`
record for that event (bounded pages, never past `through_seq`), walks any
Gamma provider's payload shape for a closed-market payout touching one of the
rule's own partition markets, and cross-checks the exact market/condition/
token binding before accepting a reading. It never opens a path, never calls
a writer method on the store (`audit`, `capture`, `decision`, `safety_audit`,
`funnel`, `source_result`), never uses a wall clock, and never performs
network or provider access of any kind. It never imports or calls
`official_label_review_packet.build_review_packet`, never mints a `LABEL`,
and never infers independent WRH/WU settlement truth.

This module does not pin its own frontier -- the caller supplies `through_seq`
directly, so several readers (this one and `official_label_review_reader.py`)
can be run against the exact same pinned view without re-pinning. It is
deliberately independent of `official_label_review_reader.py`: it shares no
private helper with it and does not import it, so each module can be reviewed
on its own. It reuses `label_attestation.py`'s `GAMMA_LABEL_PROVIDER` constant,
the same provider identity `tools/v11_brain_label_attestation.py` already
trusts for a Gamma LABEL.

Gamma source lineage (finding F1). A retained `RULES` row is only walked for
a payout if its own `provider` is one of the Gamma shapes this module knows
how to parse (`_GAMMA_RULES_PROVIDERS`) and its `evidence_class` is one this
archive ever assigns to genuinely received evidence (never
`HISTORICAL_AVAILABILITY_UNKNOWN`) -- an unrecognized provider is simply not
walked, the same soft skip `_process_label_row` already used for a
mismatched LABEL provider. Any market actually found nested under a
`markets` list or an `event`/`events` wrapper must carry the caller's own
`event_id` -- a contradicting nested event id is a `TAMPERED_LINEAGE` refusal,
not a skip, because the row's own content disagrees with itself. An `events`
list is walked exactly like a single `markets` hit: every member must itself
be a validated event (no stray wrapper key, a matching `id`, a `markets`
list) and is recursed into, so a market nested under an `events` list is
bound and scored, not merely identity-checked and silently dropped. A row
whose declared `provider` is itself one of the event-shaped providers
(`GAMMA_EVENT`, `GAMMA_EVENT_LIST`) but whose market was never actually
reached through a validated event wrapper is the same refusal: the row
claims event-level provenance it never structurally proves. Finally, the
row's own `source_identity` must name either this event or this exact
market (`"event:" + event_id`, `"market:" + market_id`, or the bare
`market_id`) -- any other value contradicts the row's own content, and a
`source_identity` that itself claims `"event:" + event_id` must have
actually been reached through a validated event wrapper (`bound_event`),
or it is the same refusal: a bare market under e.g. a `response` wrapper
cannot smuggle in an event-level provenance claim its own payload never
structurally proves, regardless of which `provider` string the row declares.

LABEL source and receipt causality (finding F2). A Gamma LABEL is never
accepted as self-certifying. Its `source_identity` must name the exact
bucket market_id, its `evidence_class` must be one ever assigned to
genuinely received evidence, its `value` must be a true `int` 0 or 1 (never
a `bool`, since Python's `True == 1`), and it must carry `knowable_at` as a
finite number no later than its own `available_at` (a later self-declared
knowable time is backdating in the wrong direction and refused outright,
the same fail-closed stance `official_label_review_reader.py` takes on a
disclosure time). The label must also reference a genuine retained `RULES`
disclosure via `source_capture_id`/`source_capture_sha256`: that record must
exist, hash-match, belong to this exact event, and itself independently
disclose the identical payout value for the identical bucket through the
same provenance checks `RULES` rows get -- and it must have been retained at
or before the label (`source.seq < label.seq`, `source.available_at <=
label.available_at`), never the reverse. `knowable_at` must also be no
earlier than that cited source's own conservative disclosure time: a label
cannot claim a fact was knowable before the very source it cites for that
fact ever disclosed it, even though the label's own capture happens later.
A disagreement between the label's own `value` and its cited source's
disclosed value is the same `CONFLICTING_PAYOUT_FOR_MARKET` refusal two
disagreeing receipts for one market already raise.

Exact payout vector binding (finding F3). `_bucket_payout` never trusts a
single selected token's price alone. It requires the market's own
`clobTokenIds` to equal the bucket's committed `[yes_token, no_token]` pair
exactly (rejecting a wrong NO token, a duplicated token, or any reordering),
requires exactly two prices, requires those two prices to be finite decimals
in `[0, 1]` that sum to exactly `1` (a clean `[1, 0]`/`[0, 1]` resolution,
or a disputed fractional pair such as `[0.5, 0.5]`, which remains a hold
elsewhere, never a refusal here) -- and
requires the market's own `outcomes` to read exactly `["Yes", "No"]`, the
same order `tools/v11_brain_label_attestation.py` already requires. The
token vector alone binds *which* tokens settled; `outcomes` binds what the
first of those tokens actually means, so a market whose own outcome
labelling is reversed relative to its token order cannot be read as a YES
winner merely because its first token's price is `1`. An internally
inconsistent vector such as `[1, 1]`, or a reversed/invalid `outcomes` list,
is `MALFORMED_PAYOUT_RECORD`. Decimal input length and exponent are bounded;
classification uses a private fixed context and exact Decimal values, with
candidate values rendered as decimal strings only after classification.
JSON-encoded price arrays preserve each numeric token before Decimal parsing.
Native floats are refused because their original tokens are already lost.

Rule binding semantics (finding F4). Beyond the existing nonempty-string
shape check, `timezone` must be a real IANA zone (`ZoneInfo` must accept it),
`target_date` must be a real ISO calendar date in the exact
``compiled.target_date.isoformat()`` form `rules.fingerprint_event` itself
emits (`date.fromisoformat` alone is strictly more permissive than that one
output shape -- it also accepts e.g. a compact ``YYYYMMDD`` string no
fingerprint ever carries -- so the parsed date must also round-trip back to
the identical input string), and every bucket's `yes_token`/`no_token` pair
must be globally unique across the whole partition -- the same
token-partition invariant `rules.fingerprint_event` itself enforces at
compile time. The `(unit, statistic, precision_rounding)` triple must also be
one of the exact combinations `compile_temperature_rule_authority`
(`weather_only_rules.py`) can ever actually produce on a path that reaches
`fingerprint_event` -- a digest-consistent `RuleFingerprint` proves internal
self-consistency, not that the strict compiler ever actually emitted this
semantic triple, so this reader refuses a combination the compiler could
never have produced rather than echoing it as a diagnostic. This closed
vocabulary intentionally excludes every HKO profile triple
(`ABSOLUTE_DAILY_MAX_C`/`ABSOLUTE_DAILY_MIN_C` with `ONE_DECIMAL_C`):
`weather_only_rules._hko_profile` always appends
`HKO_DECIMAL_BUCKET_MAPPING_UNPROVEN` to its structural reasons whenever its
precision is otherwise proven, so `exactly_one_outcome_proven` is `False` for
every HKO event unconditionally, and `rules.fingerprint_event` raises
`EXACT_RULE_SEMANTICS_REQUIRED` before a `RuleFingerprint` is ever
constructed for it -- a digest-consistent HKO triple is therefore not an
attainable rule fingerprint under any input, only a self-consistent forgery,
and is refused here on that basis, not merely narrowed for convenience.
The reader also checks the committed strict contract, exact integer partition
coverage, and whether the complete supplied preimage can be reproduced by
the current strict compiler. This does not authenticate the original event.

Postdecision lookahead (finding F5). This reader has no `DECISION` record,
decision id, or decision time as an input by design -- only
`store`/`event_id`/`rule`/`through_seq`, per the readiness map's exact
phrasing -- and it still never calls `store.pin_read_view` itself, so it
never sees a row appended after the caller's own frontier. It additionally
accepts one optional, caller-supplied `decision_cutoff`: a wall-clock time
the caller truthfully attests their own decision was made at; this reader's
own read-only computation never derives it, backdates it, or infers it from
any record. When supplied, every accepted candidate's own conservative
disclosure time -- the archive's own retention time, never an earlier
self-declared `observed_at`/`issued_at`/`published_at` or `knowable_at`
claim, since such a claim could otherwise mask a genuinely late receipt --
is compared against it purely for reporting: any candidate disclosed after
the cutoff is named in `provenance["postdecision_candidate_ids"]` and
flagged with the `POSTDECISION_DISCLOSURE_PRESENT_AMONG_CANDIDATES` hold.
This is not a refusal and nothing is filtered by it -- a payout genuinely
disclosed after a decision is the ordinary, expected shape of real
settlement evidence, not a defect. It is simply surfaced rather than left
silently undetectable. When `decision_cutoff` is omitted -- including when
the scan itself is cut short by the row cap or depth limit, which proves
nothing about postdecision disclosure either -- the permanent, unmistakable
`POSTDECISION_DISCLOSURE_UNKNOWN_NO_DECISION_CUTOFF_SUPPLIED` hold documents
the exact limitation: without a decision time, this reader cannot and does
not claim to know whether any included payout was disclosed after a
decision, so the hold can never be mistaken for that assurance.

The returned `GammaComparatorDiagnostic` is permanently nonpromoting: all six
authority fields and `qualified` are frozen to literal `False`, defended by
`__post_init__` the same way `ReviewInputs`/`ReviewPacket` defend their own
authority fields. Nothing in this module clears, inspects, or depends on
`official_label_review_reader.py`'s own `GAMMA_COMPARATOR_NOT_CONSTRUCTED`
permanent hold; that hold describes the fact that no `ReviewPacket` embeds
this comparator yet, which remains true after this slice.
"""
from __future__ import annotations

import json
import math
from collections import Counter
from dataclasses import dataclass
from datetime import date
from decimal import Context, Decimal, DecimalException, InvalidOperation, localcontext
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .evidence import EvidenceError, EvidenceStore, digest, finite, identity
from .label_attestation import GAMMA_LABEL_PROVIDER
from .rules import RuleFingerprint, fingerprint_event
from ..weather_only_contract_strict import STRICT_CONTRACT_VERSION, StrictWeatherContractError, _norm
from ..weather_only_contracts import DAILY_HIGH, DAILY_LOW, _bucket_bounds


READER_VERSION = "alpha_v11_official_gamma_comparator_reader_v3"

# Five permanent declared residuals of this slice (always present, every
# call, regardless of input) plus the data-dependent conditional holds.
PERMANENT_HOLDS = (
    "GAMMA_COMPARATOR_DIAGNOSTIC_ONLY_NOT_SETTLEMENT_TRUTH",
    "PUBLISHER_RIGHTS_AND_AUTHENTICITY_UNREVIEWED",
    "ARCHIVE_NOT_INDEPENDENT_TAMPERPROOF",
    "DISCOVERY_CATALOG_NOT_SCANNED",
    "WU_FALLBACK_OUT_OF_SCOPE",
)
CONDITIONAL_HOLDS = (
    "PAYOUT_SCAN_INCOMPLETE",
    "PARTITION_BUCKET_UNRESOLVED",
    "DISPUTED_PAYOUT_PRESENT",
    "NO_WINNER_AMONG_RESOLVED_BUCKETS",
    "POSTDECISION_DISCLOSURE_UNKNOWN_NO_DECISION_CUTOFF_SUPPLIED",
    "POSTDECISION_DISCLOSURE_PRESENT_AMONG_CANDIDATES",
)
HOLDS = PERMANENT_HOLDS + CONDITIONAL_HOLDS

_SCAN_PAGE_LIMIT = 200
_SCAN_CAP = 2000
_GAMMA_WRAPPERS = ("event", "market", "response")
_GAMMA_MAX_DEPTH = 4
_MAX_PAYOUT_NUMBER_CHARS = 256
_MAX_PAYOUT_EXPONENT = 1000
# More than enough precision for two bounded coefficients at exponent -1000.
# Construct our own context so caller precision, rounding and traps are irrelevant.
_PAYOUT_CONTEXT = Context(prec=2048, Emin=-2048, Emax=2048)
_GAMMA_RULES_PROVIDERS = (
    "GAMMA_EVENT", "GAMMA_EVENT_LIST", "GAMMA_MARKET",
    "GAMMA_CLOSED_MARKET", "GAMMA_CLOSED_MARKET_EXACT_TOKEN_PAYOUT",
)
_GAMMA_EVENT_SHAPED_PROVIDERS = ("GAMMA_EVENT", "GAMMA_EVENT_LIST")
_RECEIVED_EVIDENCE_CLASSES = ("PUBLIC_OBSERVED", "SYNTHETIC")
# The exact closed vocabulary `compile_temperature_rule_authority`
# (weather_only_rules.py) can ever assign on a path that reaches
# `fingerprint_event` -- finding F4. This reader is not the compiler and does
# not re-derive these from event text; it only refuses a (unit, statistic,
# precision_rounding) triple the compiler could never have produced.
#
# HKO is deliberately absent. `_hko_profile` always appends
# "HKO_DECIMAL_BUCKET_MAPPING_UNPROVEN" to its structural reasons whenever
# "HKO_PRECISION_RULE_UNPROVEN" is not already one of its semantic reasons
# (i.e. whenever precision="ONE_DECIMAL_C" would otherwise be set), so
# exactly_one_outcome_proven is False for every HKO event with no exception,
# and rules.py's fingerprint_event (`if ... not authority.exactly_one_outcome_proven
# ... raise EXACT_RULE_SEMANTICS_REQUIRED`) refuses to construct a
# RuleFingerprint for any HKO event at all. A caller-supplied
# ("C", "ABSOLUTE_DAILY_MAX_C"/"ABSOLUTE_DAILY_MIN_C", "ONE_DECIMAL_C")
# triple is therefore not attainable from any real compiled event, however
# digest-consistent it is made to look, so it is excluded here rather than
# merely trusted.
_VALID_RULE_SEMANTIC_PROFILES = frozenset({
    ("F", "DAILY_HIGHEST_TEMP", "WHOLE_DEGREE_F"),
    ("F", "DAILY_LOWEST_TEMP", "WHOLE_DEGREE_F"),
    ("C", "DAILY_HIGHEST_TEMP", "WHOLE_DEGREE_C"),
    ("C", "DAILY_LOWEST_TEMP", "WHOLE_DEGREE_C"),
})


def _require(ok: bool, reason: str) -> None:
    if not ok:
        raise EvidenceError("GAMMA_COMPARATOR_READER_" + reason)


@dataclass(frozen=True)
class GammaComparatorDiagnostic:
    version: str
    event_id: str
    rule_fingerprint_sha256: str
    through_seq: int
    decision_cutoff: float | None
    station: str
    timezone: str
    target_date: str
    unit: str
    statistic: str
    precision_rounding: str
    partition: tuple[dict, ...]
    candidates: tuple[dict, ...]
    winner_market_id: str | None
    provenance: dict
    holds: tuple[str, ...]
    qualified: bool = False
    independent_label_attestation: bool = False
    settlement_authority: bool = False
    calibration_authority: bool = False
    financial_authority: bool = False
    automatic_promotion: bool = False

    def __post_init__(self) -> None:
        for name in ("independent_label_attestation", "settlement_authority",
                     "calibration_authority", "financial_authority",
                     "automatic_promotion", "qualified"):
            if getattr(self, name) is not False:
                raise ValueError(f"{name} must be literal False")
        if self.decision_cutoff is not None and type(self.decision_cutoff) not in (int, float):
            raise ValueError("decision_cutoff must be None or a number")
        if type(self.holds) is not tuple or not all(type(h) is str for h in self.holds):
            raise ValueError("holds must be a tuple of str")
        if not set(PERMANENT_HOLDS) <= set(self.holds):
            raise ValueError("holds must retain every permanent hold")
        if not set(self.holds) <= set(HOLDS):
            raise ValueError("holds must stay within the closed hold vocabulary")
        if type(self.partition) is not tuple or not all(type(b) is dict for b in self.partition):
            raise ValueError("partition must be a tuple of dict")
        if type(self.candidates) is not tuple or not all(type(c) is dict for c in self.candidates):
            raise ValueError("candidates must be a tuple of dict")
        market_ids = {b.get("market_id") for b in self.partition}
        if self.winner_market_id is not None and self.winner_market_id not in market_ids:
            raise ValueError("winner_market_id must be a partition market_id or None")


class _GammaScanIncomplete(Exception):
    """A payload nests a market beyond the bounded depth walk."""


def _json_list(value, *, preserve_numbers: bool = False):
    if isinstance(value, str):
        try:
            # A Gamma array can itself be a JSON string in the retained
            # payload. Decode its number tokens as their original spelling:
            # ordinary json.loads rounds them to binary floats first.
            hooks = (dict(parse_int=str, parse_float=str, parse_constant=str)
                     if preserve_numbers else {})
            value = json.loads(value, **hooks)
        except (ValueError, RecursionError):
            raise EvidenceError("GAMMA_COMPARATOR_READER_MALFORMED_PAYOUT_RECORD") from None
    if not isinstance(value, list):
        raise EvidenceError("GAMMA_COMPARATOR_READER_MALFORMED_PAYOUT_RECORD")
    return value


def _bucket_payout(bucket: dict, market: dict) -> Decimal | None:
    """Exact whole-vector payout for one bucket, or None if the market is open.

    Unlike checking a single selected token's price in isolation, this
    requires the market's own token list to equal the bucket's committed
    ``[yes_token, no_token]`` pair exactly, the two prices to be finite
    decimals in ``[0, 1]`` that sum to exactly ``1`` (a clean resolution or
    a disputed fractional pair) and the market's own ``outcomes`` to read exactly
    ``["Yes", "No"]``, so the token order is bound to what it actually means,
    not merely trusted to mean YES-first. A wrong second token, a duplicated
    token, a reversed/invalid ``outcomes`` list, or an internally
    inconsistent vector such as ``[1, 1]`` is a refusal, never a
    silently-accepted winner.
    """
    if not isinstance(market, dict) or market.get("closed") is not True:
        return None
    tokens = [str(x) for x in _json_list(market.get("clobTokenIds"))]
    prices = _json_list(market.get("outcomePrices"), preserve_numbers=True)
    outcomes = [str(x) for x in _json_list(market.get("outcomes"))]
    if (tokens != [bucket["yes_token"], bucket["no_token"]] or len(prices) != 2
            or outcomes != ["Yes", "No"]):
        raise EvidenceError("GAMMA_COMPARATOR_READER_MALFORMED_PAYOUT_RECORD")
    try:
        # Bound each preserved numeric spelling and exponent before arithmetic.
        # A float already lost its original JSON token before this reader can
        # inspect it. Even 1.0 may have originated as 1.00000000000000001.
        # Require an exact native string/int or a preserved encoded lexeme.
        if any(type(x) not in (str, int) for x in prices):
            raise InvalidOperation
        tokens = [str(x) for x in prices]
        if any(len(x) > _MAX_PAYOUT_NUMBER_CHARS for x in tokens):
            raise InvalidOperation
        values = [Decimal(x) for x in tokens]
        if any(not v.is_finite() or abs(v.as_tuple().exponent) > _MAX_PAYOUT_EXPONENT
               for v in values):
            raise InvalidOperation
        with localcontext(_PAYOUT_CONTEXT):
            if (any(v < 0 or v > 1 for v in values)
                    or values[0] + values[1] != Decimal(1)):
                raise InvalidOperation
    except (DecimalException, TypeError, ValueError, OverflowError):
        raise EvidenceError("GAMMA_COMPARATOR_READER_MALFORMED_PAYOUT_RECORD") from None
    return values[0]


def _partition_is_exact(partition: list[dict]) -> bool:
    """Check the integer coverage invariant of the strict compiler output."""
    conditions: set[str] = set()
    finite_bounds = 0
    for bucket in partition:
        condition = bucket["condition_id"]
        if condition in conditions or not all(k in bucket for k in ("lower", "upper")):
            return False
        conditions.add(condition)
        for bound in (bucket["lower"], bucket["upper"]):
            if bound is None:
                continue
            if (type(bound) not in (int, float) or abs(bound) > 2**53 - 1
                    or not math.isfinite(bound) or not float(bound).is_integer()):
                return False
            finite_bounds += 1
    if not finite_bounds:
        return False
    ordered = sorted(partition, key=lambda b: float("-inf") if b["lower"] is None else b["lower"])
    if ordered[0]["lower"] is not None or ordered[-1]["upper"] is not None:
        return False
    for index, bucket in enumerate(ordered):
        if bucket["lower"] is None and index != 0:
            return False
        if bucket["upper"] is None and index != len(ordered) - 1:
            return False
        if (bucket["lower"] is not None and bucket["upper"] is not None
                and bucket["lower"] > bucket["upper"]):
            return False
        if index and (ordered[index - 1]["upper"] is None
                      or bucket["lower"] != ordered[index - 1]["upper"] + 1):
            return False
    return True


def _recompiled_rule_matches(payload: dict, contract: dict, partition: list[dict]) -> bool:
    """Check that committed fields can produce this entire compiler output.

    This reconstructs a minimal event from the fingerprint's own committed
    rules and buckets. It checks compiler attainability, not authenticity of
    the original event or its source_event_sha256.
    """
    if (type(payload.get("title")) is not str
            or type(payload.get("metadata_fingerprint")) is not str):
        return False
    by_question = {_norm(b["question"]): b for b in partition}
    if len(by_question) != len(partition):
        return False
    markets = []
    for question in contract["questions"]:
        bucket = by_question[question]
        markets.append({"id": bucket["market_id"], "conditionId": bucket["condition_id"],
                        "question": bucket["question"], "outcomes": ["Yes", "No"],
                        "clobTokenIds": [bucket["yes_token"], bucket["no_token"]],
                        "active": True, "closed": False, "acceptingOrders": True,
                        "enableOrderBook": True})
    event = {"id": payload["event_id"], "title": payload["title"],
             "description": contract["operative_rules"],
             "resolutionSource": contract["operative_source"], "markets": markets}
    try:
        rebuilt = fingerprint_event(
            event, station_timezone=payload["timezone"],
            metadata_fingerprint=payload["metadata_fingerprint"])
        return rebuilt.sha256 == digest(payload)
    except (EvidenceError, StrictWeatherContractError, TypeError, ValueError,
            OverflowError, KeyError):
        return False


def _gamma_markets(value, event_id: str, depth: int = 0, *,
                    force_event: bool = False, bound_event: bool = False):
    """Bounded-depth, provider-agnostic walk of a Gamma RULES payload.

    Any dict reached with a ``markets`` key, or reached through the
    ``event`` wrapper, is treated as an event: it must carry no other
    wrapper key and its own ``id`` must equal ``event_id``, or the payload
    contradicts itself (`TAMPERED_LINEAGE`), not merely fails to disclose.
    An ``events`` list anywhere makes the same claim about every member, and
    each member is itself recursed into as a validated event (the same
    ``markets``-key handling above), so a market nested under an ``events``
    list is reached, bound, and yielded, not merely identity-checked. Every
    market found is yielded together with whether it was actually reached
    through such a validated event binding.
    """
    if depth > _GAMMA_MAX_DEPTH:
        raise _GammaScanIncomplete
    if isinstance(value, list):
        for item in value:
            yield from _gamma_markets(item, event_id, depth + 1,
                                       force_event=force_event, bound_event=bound_event)
        return
    if not isinstance(value, dict):
        return
    if force_event or "markets" in value:
        if "events" in value or any(key in value for key in _GAMMA_WRAPPERS):
            raise EvidenceError("GAMMA_COMPARATOR_READER_TAMPERED_LINEAGE")
        if str(value.get("id")) != event_id or not isinstance(value.get("markets"), list):
            raise EvidenceError("GAMMA_COMPARATOR_READER_TAMPERED_LINEAGE")
        yield from _gamma_markets(value["markets"], event_id, depth + 1, bound_event=True)
        return
    if any(key in value for key in _GAMMA_WRAPPERS):
        if "id" in value or "events" in value or bound_event:
            raise EvidenceError("GAMMA_COMPARATOR_READER_TAMPERED_LINEAGE")
        for key in _GAMMA_WRAPPERS:
            if key in value:
                yield from _gamma_markets(value[key], event_id, depth + 1,
                                           force_event=(key == "event"), bound_event=bound_event)
        return
    if "events" in value:
        events = value["events"]
        if not isinstance(events, list) or not events:
            raise EvidenceError("GAMMA_COMPARATOR_READER_TAMPERED_LINEAGE")
        for event in events:
            if not isinstance(event, dict):
                raise EvidenceError("GAMMA_COMPARATOR_READER_TAMPERED_LINEAGE")
            yield from _gamma_markets(event, event_id, depth + 1,
                                       force_event=True, bound_event=bound_event)
        return
    if "clobTokenIds" in value or "outcomePrices" in value:
        yield value, bound_event


def _scan(store, *, kind: str, event_id: str, through_seq: int, cap: int) -> tuple[list[dict], bool]:
    """Bounded forward page scan of one (kind, event) stream, seq-frontier filtered."""
    rows: list[dict] = []
    cursor = 0
    while True:
        page = store.records(kind=kind, event_id=event_id, after_seq=cursor, limit=_SCAN_PAGE_LIMIT)
        if not page:
            return rows, False
        cursor = page[-1]["seq"]
        rows.extend(row for row in page if row["seq"] <= through_seq)
        if len(rows) > cap:
            return rows, True
        if cursor >= through_seq or len(page) < _SCAN_PAGE_LIMIT:
            return rows, False


def _disclosure_time(body: dict) -> float:
    """The conservative (never-too-early) time this row's own payout became
    available, for decision-cutoff comparison (finding F5). The archive's own
    `recorded_at` -- the store clock's actual retention time -- is always at
    or after every self-declared `observed_at`/`issued_at`/`published_at`
    claim (`EvidenceStore.capture` refuses a self-declared time in the
    future). Using any such earlier self-declared claim here, instead of the
    actual retention time, would let a genuinely late receipt masquerade as
    an earlier one and silently defeat the postdecision-disclosure hold."""
    return body["recorded_at"]


def _process_rules_row(row: dict, partition_by_id: dict, event_id: str) -> list[dict]:
    body = row["body"]
    provider = body.get("provider")
    if provider not in _GAMMA_RULES_PROVIDERS or body.get("evidence_class") not in _RECEIVED_EVIDENCE_CLASSES:
        return []
    payload = body.get("payload")
    if not isinstance(payload, dict):
        return []
    readings = []
    for market, bound_event in _gamma_markets(payload, event_id):
        if not isinstance(market, dict):
            continue
        market_id = market.get("id")
        if market_id is None:
            continue
        bucket = partition_by_id.get(str(market_id))
        if bucket is None:
            continue
        if provider in _GAMMA_EVENT_SHAPED_PROVIDERS and not bound_event:
            raise EvidenceError("GAMMA_COMPARATOR_READER_TAMPERED_LINEAGE")
        source_identity = body.get("source_identity")
        if source_identity not in (
                "event:" + event_id, "market:" + bucket["market_id"], bucket["market_id"]):
            raise EvidenceError("GAMMA_COMPARATOR_READER_TAMPERED_LINEAGE")
        if source_identity == "event:" + event_id and not bound_event:
            # The row's own identity claims event-level provenance; its
            # payload must structurally prove that claim, not merely assert
            # it on a bare market with no event wrapper (finding F1).
            raise EvidenceError("GAMMA_COMPARATOR_READER_TAMPERED_LINEAGE")
        condition_id = market.get("conditionId", market.get("condition_id"))
        if condition_id != bucket["condition_id"]:
            raise EvidenceError("GAMMA_COMPARATOR_READER_TAMPERED_LINEAGE")
        payout = _bucket_payout(bucket, market)
        if payout is None:
            continue
        readings.append(dict(market_id=bucket["market_id"], value=payout, id=row["id"], seq=row["seq"],
                              recorded_at=_disclosure_time(body), provider=provider))
    return readings


def _label_source_reading(store, *, event_id: str, bucket: dict,
                           source_capture_id, source_capture_sha256) -> tuple[dict, dict]:
    if not isinstance(source_capture_id, str) or not source_capture_id:
        raise EvidenceError("GAMMA_COMPARATOR_READER_LABEL_SOURCE_REFERENCE_MISSING")
    try:
        source = store.get(source_capture_id)
    except EvidenceError as exc:
        if exc.args == ("EVIDENCE_MISSING",):
            raise EvidenceError("GAMMA_COMPARATOR_READER_LABEL_SOURCE_REFERENCE_MISSING") from None
        raise
    if (not isinstance(source_capture_sha256, str) or source["sha256"] != source_capture_sha256
            or source["kind"] != "RULES" or source["event_id"] != event_id):
        raise EvidenceError("GAMMA_COMPARATOR_READER_LABEL_SOURCE_REFERENCE_MISSING")
    readings = _process_rules_row(source, {bucket["market_id"]: bucket}, event_id)
    if len(readings) != 1:
        raise EvidenceError("GAMMA_COMPARATOR_READER_LABEL_SOURCE_REFERENCE_MISSING")
    return source, readings[0]


def _process_label_row(row: dict, partition_by_id: dict, store, event_id: str) -> list[dict]:
    body = row["body"]
    if body.get("provider") != GAMMA_LABEL_PROVIDER or body.get("evidence_class") not in _RECEIVED_EVIDENCE_CLASSES:
        return []
    payload = body.get("payload")
    if not isinstance(payload, dict):
        return []
    target_identity = payload.get("target_identity")
    if not isinstance(target_identity, dict):
        return []
    market_id = target_identity.get("market_id")
    bucket = partition_by_id.get(market_id)
    if bucket is None:
        return []
    if set(target_identity) != {"market_id", "condition_id", "token_id", "side"} or target_identity.get("side") != "YES":
        raise EvidenceError("GAMMA_COMPARATOR_READER_MALFORMED_PAYOUT_RECORD")
    if (target_identity.get("condition_id") != bucket["condition_id"]
            or target_identity.get("token_id") != bucket["yes_token"]):
        raise EvidenceError("GAMMA_COMPARATOR_READER_TAMPERED_LINEAGE")
    if body.get("source_identity") != market_id:
        raise EvidenceError("GAMMA_COMPARATOR_READER_TAMPERED_LINEAGE")
    value = payload.get("value")
    if type(value) is not int or value not in (0, 1):
        raise EvidenceError("GAMMA_COMPARATOR_READER_MALFORMED_PAYOUT_RECORD")
    knowable_at = payload.get("knowable_at")
    if type(knowable_at) not in (int, float):
        raise EvidenceError("GAMMA_COMPARATOR_READER_MALFORMED_PAYOUT_RECORD")
    if knowable_at > body["available_at"]:
        raise EvidenceError("GAMMA_COMPARATOR_READER_TAMPERED_LINEAGE")
    source, source_reading = _label_source_reading(
        store, event_id=event_id, bucket=bucket,
        source_capture_id=payload.get("source_capture_id"),
        source_capture_sha256=payload.get("source_capture_sha256"))
    if source["seq"] >= row["seq"] or source["body"]["available_at"] > body["available_at"]:
        raise EvidenceError("GAMMA_COMPARATOR_READER_TAMPERED_LINEAGE")
    if knowable_at < source_reading["recorded_at"]:
        # A fact cannot have been knowable before the very source the label
        # cites for it ever disclosed it -- a self-declared earlier
        # `knowable_at` cannot outrun its own cited causal source (F2).
        raise EvidenceError("GAMMA_COMPARATOR_READER_TAMPERED_LINEAGE")
    if source_reading["value"] != Decimal(value):
        raise EvidenceError("GAMMA_COMPARATOR_READER_CONFLICTING_PAYOUT_FOR_MARKET")
    return [dict(market_id=bucket["market_id"], value=Decimal(value), id=row["id"], seq=row["seq"],
                 recorded_at=_disclosure_time(body), provider=body.get("provider"))]


def read_gamma_comparator(*, store: EvidenceStore, event_id: str, rule: RuleFingerprint,
                           through_seq: int, decision_cutoff: float | None = None) -> GammaComparatorDiagnostic:
    """Map retained Gamma payout evidence for one event/rule into a plain,
    permanently nonpromoting comparator diagnostic, or raise a typed refusal.

    ``store`` is an already-constructed `EvidenceStore` (or any object with
    its `get`/`records` read methods); this function never opens a path and
    never calls `store.pin_read_view` itself -- ``through_seq`` is the
    frontier the caller already pinned, so this reader never sees a row
    appended after it. ``decision_cutoff``, if supplied, is the caller's own
    truthfully-attested decision time, used only to flag (never filter)
    whether any accepted candidate was disclosed after it; see the module
    docstring's "Postdecision lookahead" section.
    """
    identity(event_id)
    _require(isinstance(rule, RuleFingerprint), "RULE_TYPE_INVALID")
    _require(type(through_seq) is int and through_seq >= 0, "THROUGH_SEQ_INVALID")
    if decision_cutoff is not None:
        try:
            decision_cutoff = finite(decision_cutoff)
        except EvidenceError:
            raise EvidenceError("GAMMA_COMPARATOR_READER_DECISION_CUTOFF_INVALID") from None

    rule_payload = rule.payload  # keeps bare RULE_FINGERPRINT_INTEGRITY on digest mismatch.
    _require(isinstance(rule_payload, dict), "RULE_PREIMAGE_INVALID")
    _require(rule_payload.get("event_id") == event_id, "RULE_EVENT_MISMATCH")

    partition = rule_payload.get("partition")
    _require(isinstance(partition, list) and len(partition) >= 2, "RULE_PARTITION_INVALID")
    _require(all(isinstance(b, dict) and all(isinstance(b.get(k), str) and b[k]
                 for k in ("market_id", "condition_id", "yes_token", "no_token"))
                 for b in partition), "RULE_PARTITION_INVALID")
    partition_by_id = {b["market_id"]: b for b in partition}
    _require(len(partition_by_id) == len(partition), "RULE_PARTITION_INVALID")

    seen_tokens: set[str] = set()
    for bucket in partition:
        yes_token, no_token = bucket["yes_token"], bucket["no_token"]
        _require(yes_token != no_token and yes_token not in seen_tokens and no_token not in seen_tokens,
                 "RULE_PARTITION_INVALID")
        seen_tokens.update((yes_token, no_token))

    for field in ("station", "timezone", "target_date", "unit", "statistic", "precision_rounding"):
        _require(isinstance(rule_payload.get(field), str) and bool(rule_payload[field]), "RULE_BINDING_FIELDS_INVALID")
    try:
        ZoneInfo(rule_payload["timezone"])
    except (ZoneInfoNotFoundError, TypeError, ValueError):
        raise EvidenceError("GAMMA_COMPARATOR_READER_RULE_BINDING_FIELDS_INVALID") from None
    try:
        parsed_target_date = date.fromisoformat(rule_payload["target_date"])
    except ValueError:
        raise EvidenceError("GAMMA_COMPARATOR_READER_RULE_BINDING_FIELDS_INVALID") from None
    # `date.fromisoformat` alone accepts strings `fingerprint_event` never
    # emits (e.g. a compact "YYYYMMDD" form) -- `rules.py` always writes
    # `compiled.target_date.isoformat()`, so the parsed date must round-trip
    # back to the identical input string, not merely parse (finding F4).
    if parsed_target_date.isoformat() != rule_payload["target_date"]:
        raise EvidenceError("GAMMA_COMPARATOR_READER_RULE_BINDING_FIELDS_INVALID")
    # A digest-consistent RuleFingerprint proves internal self-consistency,
    # not that the strict compiler ever produced this semantic triple
    # (finding F4) -- refuse any (unit, statistic, precision_rounding) the
    # compiler could never emit, rather than echoing it as a diagnostic.
    _require((rule_payload["unit"], rule_payload["statistic"], rule_payload["precision_rounding"])
              in _VALID_RULE_SEMANTIC_PROFILES, "RULE_BINDING_FIELDS_INVALID")
    # The compiler derives the statistic from family and writes each bucket's
    # compiled unit alongside the parent unit. A rehashed preimage cannot
    # change either relationship while claiming to be that compiler's output.
    _require((rule_payload.get("family"), rule_payload["statistic"]) in (
        (DAILY_HIGH, "DAILY_HIGHEST_TEMP"), (DAILY_LOW, "DAILY_LOWEST_TEMP")),
        "RULE_BINDING_FIELDS_INVALID")
    _require(all(b.get("unit") == rule_payload["unit"] for b in partition),
             "RULE_BINDING_FIELDS_INVALID")

    # The strict compiler commits these fields together. The outer rule hash
    # alone can be recomputed after changing the diagnostic fields; the inner
    # contract hash and cross-field relationships must also remain intact.
    contract = rule_payload.get("strict_contract")
    contract_fields = {"version", "operative_rules", "operative_source", "questions",
                       "location", "event_id", "family", "unit", "target_date",
                       "station", "sha256"}
    _require(type(contract) is dict and set(contract) == contract_fields,
             "RULE_BINDING_FIELDS_INVALID")
    _require(contract["version"] == STRICT_CONTRACT_VERSION
             and type(contract["operative_rules"]) is str and bool(contract["operative_rules"])
             and type(contract["operative_source"]) is str
             and type(contract["questions"]) is list
             and all(type(q) is str for q in contract["questions"])
             and type(contract["sha256"]) is str,
             "RULE_BINDING_FIELDS_INVALID")
    _require(digest({k: v for k, v in contract.items() if k != "sha256"}) == contract["sha256"],
             "RULE_BINDING_FIELDS_INVALID")
    for parent, committed in (("event_id", "event_id"), ("family", "family"),
                              ("unit", "unit"), ("target_date", "target_date"),
                              ("station", "station"), ("primary_source", "operative_source"),
                              ("city", "location")):
        _require(rule_payload.get(parent) == contract[committed], "RULE_BINDING_FIELDS_INVALID")
    _require(len(contract["questions"]) == len(partition)
             and all(type(b.get("question")) is str for b in partition)
             and Counter(contract["questions"]) == Counter(_norm(b["question"]) for b in partition),
             "RULE_BINDING_FIELDS_INVALID")
    _require(_partition_is_exact(partition), "RULE_PARTITION_INVALID")
    _require(all(_bucket_bounds(b["question"], b["unit"]) == (b["lower"], b["upper"])
                 for b in partition), "RULE_BINDING_FIELDS_INVALID")
    _require(_recompiled_rule_matches(rule_payload, contract, partition),
             "RULE_BINDING_FIELDS_INVALID")

    sorted_partition = tuple(dict(b) for b in sorted(partition, key=lambda b: b["market_id"]))

    def diagnostic(*, candidates, winner_market_id, provenance, holds):
        return GammaComparatorDiagnostic(
            version=READER_VERSION, event_id=event_id, rule_fingerprint_sha256=rule.sha256,
            through_seq=through_seq, decision_cutoff=decision_cutoff,
            station=rule_payload["station"], timezone=rule_payload["timezone"],
            target_date=rule_payload["target_date"], unit=rule_payload["unit"],
            statistic=rule_payload["statistic"], precision_rounding=rule_payload["precision_rounding"],
            partition=sorted_partition, candidates=candidates, winner_market_id=winner_market_id,
            provenance=provenance, holds=tuple(holds))

    holds: list[str] = list(PERMANENT_HOLDS)

    def hold(code: str) -> None:
        if code not in holds:
            holds.append(code)

    rules_rows, rules_truncated = _scan(store, kind="RULES", event_id=event_id,
                                         through_seq=through_seq, cap=_SCAN_CAP)
    label_rows, label_truncated = _scan(store, kind="LABEL", event_id=event_id,
                                         through_seq=through_seq, cap=_SCAN_CAP)
    provenance = {"scanned_rules_rows": len(rules_rows), "scanned_label_rows": len(label_rows)}

    if rules_truncated or label_truncated:
        # Never return a partial candidate tuple: an incomplete scan cannot
        # prove completeness, so this fails closed to "no candidate" rather
        # than one that could be mistaken for the full retained set.
        hold("PAYOUT_SCAN_INCOMPLETE")
        if decision_cutoff is None:
            # A truncated scan proves nothing about postdecision disclosure
            # either -- this permanent limitation hold must not be dropped
            # just because the scan itself was cut short (finding F5).
            hold("POSTDECISION_DISCLOSURE_UNKNOWN_NO_DECISION_CUTOFF_SUPPLIED")
        return diagnostic(candidates=(), winner_market_id=None, provenance=provenance, holds=holds)

    readings_by_market: dict[str, list[dict]] = {mid: [] for mid in partition_by_id}
    all_candidates: list[dict] = []
    try:
        for row in rules_rows:
            for reading in _process_rules_row(row, partition_by_id, event_id):
                readings_by_market[reading["market_id"]].append(reading)
                all_candidates.append(reading)
        for row in label_rows:
            for reading in _process_label_row(row, partition_by_id, store, event_id):
                readings_by_market[reading["market_id"]].append(reading)
                all_candidates.append(reading)
    except _GammaScanIncomplete:
        hold("PAYOUT_SCAN_INCOMPLETE")
        if decision_cutoff is None:
            hold("POSTDECISION_DISCLOSURE_UNKNOWN_NO_DECISION_CUTOFF_SUPPLIED")
        return diagnostic(candidates=(), winner_market_id=None, provenance=provenance, holds=holds)

    resolved: dict[str, Decimal] = {}
    for market_id, readings in readings_by_market.items():
        if not readings:
            continue
        values = {r["value"] for r in readings}
        _require(len(values) == 1, "CONFLICTING_PAYOUT_FOR_MARKET")
        resolved[market_id] = values.pop()

    winners = [mid for mid, value in resolved.items() if value == 1]
    _require(len(winners) <= 1, "AMBIGUOUS_WINNER")
    winner_market_id = winners[0] if winners else None

    if len(resolved) < len(partition_by_id):
        hold("PARTITION_BUCKET_UNRESOLVED")
    if any(value not in (0, 1) for value in resolved.values()):
        hold("DISPUTED_PAYOUT_PRESENT")
    if winner_market_id is None and resolved:
        hold("NO_WINNER_AMONG_RESOLVED_BUCKETS")

    if decision_cutoff is None:
        hold("POSTDECISION_DISCLOSURE_UNKNOWN_NO_DECISION_CUTOFF_SUPPLIED")
    else:
        postdecision_ids = tuple(sorted(c["id"] for c in all_candidates if c["recorded_at"] > decision_cutoff))
        if postdecision_ids:
            hold("POSTDECISION_DISCLOSURE_PRESENT_AMONG_CANDIDATES")
            provenance["postdecision_candidate_ids"] = postdecision_ids

    provenance["resolved_market_ids"] = tuple(sorted(resolved))
    all_candidates.sort(key=lambda r: r["seq"])

    # Expose the exact decimal spelling; a near-one value must never be
    # rendered as 1.0 even when a float could not represent the difference.
    for candidate in all_candidates:
        with localcontext(_PAYOUT_CONTEXT):
            candidate["value"] = str(candidate["value"])
    return diagnostic(candidates=tuple(all_candidates), winner_market_id=winner_market_id,
                       provenance=provenance, holds=holds)
