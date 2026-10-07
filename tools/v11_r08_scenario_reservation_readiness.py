"""PAPER V11 READY requirement 8 evidence-sufficiency readiness probe.

Requirement 8 ("event scenario-risk/correlation accounting demonstrated in
paper/shadow", `docs/V11_SHADOW_READINESS_20261004.md`) stays PARTIAL because
every live current-input strategy attempt observed so far has been gated
before an economic proposal could exist (`EVENT_REVALIDATION_EXPIRED`,
`ASSEMBLY_BOOK_IDENTITY_OR_FRESHNESS`, ...; see
`docs/V11_FORWARD_EVIDENCE_HARVEST_20261007.md` sec. 2.4/7.2). That is a
live-data freshness condition, not a missing code path: `request_assembly.py`,
`event_risk.py`, `strategy_admission.py`, `scenario_risk.py` and
`paper_coordinator.py` already implement the full admission -> event-risk ->
proposal -> scenario-reservation chain, and nothing in this repository may
legitimately relax any of those freshness/settlement/execution-health gates
just to manufacture a passing cycle.

What was actually missing is a reviewed, independent reader that can look at
an arbitrary `PaperCoordinator`-shaped evidence store (a retained Shadow/PAPER
ledger, or a future live one) and say, honestly and fail-closed, whether a
*genuine* zero-authority scenario reservation has ever been persisted there --
instead of every reviewer re-deriving that by hand from raw records. This
module is that reader. It grants no execution, order, model or promotion
authority; it only classifies evidence that a typed, caller-constructed
`PaperCoordinator` already produced through its own real, unmodified gates.

Deliberately reused, not re-derived: `PaperCoordinator._head`/`_state`/`_risk`
(so the risk/scenario numbers seen here are the *same* live recomputation
`PaperCoordinator.snapshot()` performs, never a second copy of
`event_scenarios`/`portfolio_risk` arithmetic that could silently drift from
the reviewed original) and `scenario_risk.number` (the same bounded decimal
parser every other v11 module uses for evidence-derived amounts).

This module opens no socket, imports no HTTP/provider client, runs no
subprocess, and writes nothing: the caller must already hold a constructed
`PaperCoordinator` bound to whatever `EvidenceStore` (retained-ledger copy or
live) it wants probed, with the account policy/correlation/limits it actually
wants evaluated against -- nothing here invents or guesses that configuration.
A result of `GENUINE_ZERO_AUTHORITY_SCENARIO_RESERVATION_DEMONSTRATED` confers
no execution, order, or funding authority: it states only that this specific
evidence store already contains an account-state record showing an
unresolved, accepted, positively-reserved paper intent produced by the real
`PaperCoordinator.coordinate()` gates. Today's retained Oct 5-7 Shadow/PAPER
ledgers contain no such record (zero proposals, zero reservations -- see the
harvest above), so this probe reports `NO_GENUINE_SCENARIO_RESERVATION_RECORDED`
against them; it exists so that the day a legitimate (non-stale, non-gated)
observation does produce one, that evidence is recognized rather than argued
about.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
import time

from polymarket_scanner.v11.account_replay import replay_account_command
from polymarket_scanner.v11.account_effects import VERSION as EFFECT_INPUT_VERSION, ref
from polymarket_scanner.v11.allocation import rank_candidates
from polymarket_scanner.v11.evidence import EvidenceError, canonical, digest
from polymarket_scanner.v11.certification import CapabilityScope
from polymarket_scanner.v11.event_risk import VERSION as EVENT_RISK_VERSION
from polymarket_scanner.v11.paper_coordinator import ACCOUNT_KEY, VERSION as COORDINATOR_VERSION, PaperCoordinator, UNRESOLVED
from polymarket_scanner.v11.causal_replay import ReplayPolicy
from polymarket_scanner.v11.scenario_risk import number
from polymarket_scanner.v11.source_release import VERSION as SOURCE_RELEASE_VERSION
from polymarket_scanner.v11.strategy_admission import VERSION as STRATEGY_ADMISSION_VERSION
from polymarket_scanner.v11.valuation import VERSION as EV_VERSION

SCHEMA = "R08_SCENARIO_RESERVATION_READINESS_V1"

OUTCOME_NO_RESERVATION = "NO_GENUINE_SCENARIO_RESERVATION_RECORDED"
OUTCOME_DEMONSTRATED = "GENUINE_ZERO_AUTHORITY_SCENARIO_RESERVATION_DEMONSTRATED"
ALLOWED_OUTCOMES = (OUTCOME_NO_RESERVATION, OUTCOME_DEMONSTRATED)
_REPLAY_POLICY = ReplayPolicy("r08-read-only-account-effects", maximum_seconds=5.0)


@dataclass(frozen=True)
class ScenarioReservationReadiness:
    schema: str
    outcome: str
    reserved_intent_count: int
    reserved_cash: str
    risk_accepted: bool
    financial_authority: bool
    reasons: tuple

    def __post_init__(self) -> None:
        if type(self.schema) is not str or self.schema != SCHEMA:
            raise ValueError(f"not the fixed schema: {self.schema!r}")
        if self.outcome not in ALLOWED_OUTCOMES:
            raise ValueError(f"not an allowed outcome: {self.outcome!r}")
        if type(self.risk_accepted) is not bool:
            raise ValueError("risk_accepted must be an exact bool")
        if type(self.financial_authority) is not bool or self.financial_authority:
            raise ValueError("financial_authority must be exactly False")
        if type(self.reserved_intent_count) is not int or self.reserved_intent_count < 0:
            raise ValueError("reserved_intent_count must be a non-negative int")
        if type(self.reasons) is not tuple:
            raise ValueError("reasons must be a tuple")
        if self.outcome == OUTCOME_DEMONSTRATED and (
            self.reasons or self.reserved_intent_count < 1 or not self.risk_accepted
            or Decimal(self.reserved_cash) <= 0
        ):
            raise ValueError("DEMONSTRATED requires a clean, positively-reserved, accepted snapshot")
        if self.outcome == OUTCOME_NO_RESERVATION and not self.reasons:
            raise ValueError("NO_RESERVATION outcome requires a non-empty reason")

    def to_dict(self) -> dict:
        return {
            "schema": self.schema,
            "outcome": self.outcome,
            "reserved_intent_count": self.reserved_intent_count,
            "reserved_cash": self.reserved_cash,
            "risk_accepted": self.risk_accepted,
            "financial_authority": self.financial_authority,
            "reasons": sorted(self.reasons),
        }


def _candidate_intents(state: dict) -> list:
    intents = state.get("intents")
    if type(intents) is not dict:
        return []
    candidates = []
    for intent in intents.values():
        if type(intent) is not dict:
            continue
        try:
            if intent.get("status") in UNRESOLVED and number(intent.get("units")) > number(intent.get("filled_units")):
                candidates.append(intent)
        except (EvidenceError, TypeError, ValueError):
            continue
    return candidates


def _account_history(coordinator: PaperCoordinator, head: dict | None) -> tuple:
    """Read the complete account command chain through the selected head."""
    if head is None:
        return ()
    rows = []
    after = 0
    while len(rows) < 10000:
        page = coordinator.store.records(kind="COORDINATOR_EVENT", event_id=ACCOUNT_KEY,
                                         after_seq=after, limit=1000)
        for row in page:
            if row["seq"] > head["seq"]:
                break
            rows.append(row)
            after = row["seq"]
            if row["seq"] == head["seq"]:
                return tuple(rows) if row["sha256"] == head["sha256"] else ()
        if not page or len(page) < 1000 or page[-1]["seq"] > head["seq"]:
            break
    return ()  # Bounded failure is safer than a partial history.


def _same(a, b) -> bool:
    return canonical(a) == canonical(b)


def _account_shape_valid(state: dict) -> bool:
    """Check containers before passing untrusted account evidence to _risk."""
    if type(state) is not dict:
        return False
    for key in ("rules", "contexts", "intents", "lots", "fills", "event_realized_pnl"):
        if type(state.get(key)) is not dict:
            return False
    for key in ("realized_entries", "faults"):
        if type(state.get(key)) is not list:
            return False
    if "baskets" in state and type(state["baskets"]) is not dict:
        return False
    return all(type(value) is dict for key in ("rules", "contexts", "intents", "lots")
               for value in state[key].values())


def _validated_account_head(coordinator: PaperCoordinator) -> dict | None:
    """Refuse malformed account envelopes before the coordinator indexes them.

    Keep the coordinator's own head selection as the snapshot used by callers.
    Check that selected row again in case a writer appended between the reads.
    """
    def validate(row: dict | None) -> None:
        if row is None:
            return
        body = row.get("body") if type(row) is dict else None
        details = body.get("details") if type(body) is dict else None
        state = details.get("state") if type(details) is dict else None
        if (type(details) is not dict or type(state) is not dict
                or type(details.get("policy_sha256")) is not str
                or not details["policy_sha256"]
                or type(state.get("account_id")) is not str
                or not state["account_id"]):
            raise EvidenceError("MALFORMED_ACCOUNT_EVIDENCE")
        if (details.get("version") != COORDINATOR_VERSION
                or details["policy_sha256"] != coordinator.policy_sha
                or state["account_id"] != coordinator.policy.account_id):
            raise EvidenceError("PAPER_ACCOUNT_POLICY_OR_IDENTITY_CHANGED")

    validate(coordinator.store.latest(kind="COORDINATOR_EVENT", event_id=ACCOUNT_KEY))
    try:
        head = coordinator._head()
    except (TypeError, KeyError, AttributeError) as exc:
        raise EvidenceError("MALFORMED_ACCOUNT_EVIDENCE") from exc
    validate(head)
    return head


def _effects_reproduced(coordinator: PaperCoordinator, row: dict, deadline: float) -> bool:
    """Replay the complete command, including request binding and proof effects.

    This checks numerical effects conditional on recorded preparation and
    control inputs; it does not attest those inputs or renew admission.
    """
    replay = replay_account_command(coordinator, row["id"], policy=_REPLAY_POLICY, deadline=deadline)
    return (replay.get("status") == "EFFECTS_REPRODUCED"
            and replay.get("effects_match") is True
            and replay.get("command_ref") == ref(row))


def _account_step_valid(coordinator: PaperCoordinator, before_row: dict | None,
                        before: dict, row: dict, details: dict) -> bool:
    """Bind each stored command to its predecessor and allowed state fields."""
    request, after = details["request"], details["state"]
    inputs = details.get("effect_inputs")
    if (type(inputs) is not dict or inputs.get("version") != EFFECT_INPUT_VERSION
            or inputs.get("before_ref") != ref(before_row)
            or inputs.get("before_state_sha256") != digest(before)
            or inputs.get("request_sha256") != digest(request)
            or inputs.get("financial_authority") is not False
            or inputs.get("control_flow_replayed") is not False):
        return False
    allowed = {
        "COORDINATE": {"rules", "contexts", "intents", "baskets"},
        "TRANSITION": {"intents"},
        "RECOVER": {"intents"},
        "FILL": {"intents", "cash", "lots", "fills", "event_realized_pnl", "realized_entries", "faults"},
        "TERMINAL": {"intents"},
    }[request["action"]]
    return all(before.get(key) == after.get(key) for key in set(before) | set(after) if key not in allowed)


def _historical_lineage(coordinator: PaperCoordinator, state: dict, intent: dict,
                        proposal: dict, origin_seq: int, accepted_at: float) -> bool:
    """Check persisted acceptance-time gate outputs without reapplying freshness."""
    admission_ids = intent.get("admission_ids")
    if (type(admission_ids) is not list or not 1 <= len(admission_ids) <= 4
            or any(type(key) is not str or not key for key in admission_ids)
            or len(set(admission_ids)) != len(admission_ids)):
        return False
    event_id = intent.get("event_id")
    contexts, rules = state.get("contexts"), state.get("rules")
    if type(contexts) is not dict or type(rules) is not dict or type(event_id) is not str:
        return False
    context, rule = contexts.get(event_id), rules.get(event_id)
    binding = intent.get("binding")
    attribution = intent.get("attribution")
    if (type(context) is not dict or type(rule) is not dict or type(binding) is not dict
            or type(attribution) is not list or not attribution
            or any(type(a) is not dict or type(a.get("strategy")) is not str for a in attribution)):
        return False
    strategies = [a["strategy"] for a in attribution]
    if (proposal.get("context") != context or proposal.get("rule") != rule
            or proposal.get("admission_ids") != admission_ids
            or proposal.get("attribution") != attribution
            or proposal.get("valuation_id") != intent.get("valuation_id")
            or proposal.get("event_state_id") != intent.get("event_state_id")
            or proposal.get("preconfirmation_id") != intent.get("preconfirmation_id")
            or proposal.get("source_release_id") != intent.get("source_release_id")
            or proposal.get("proposal_id") != intent.get("proposal_id")
            or proposal.get("thesis_id") != intent.get("thesis_id")
            or proposal.get("desired_total_units") != intent.get("desired_total_units")
            or intent.get("rule_fingerprint") != rule.get("sha256")
            or binding.get("rule_fingerprint") != rule.get("sha256")
            or context.get("account_id") != coordinator.policy.account_id):
        return False
    covered = set()
    for admission_id in admission_ids:
        row = coordinator.store.get(admission_id)
        details = row["body"].get("details")
        if row["seq"] >= origin_seq or row["kind"] != "REGISTRY" or type(details) is not dict or details.get("version") != STRATEGY_ADMISSION_VERSION:
            return False
        request, assessment = details.get("request"), details.get("assessment")
        if type(request) is not dict or type(assessment) is not dict or type(request.get("scope")) is not dict:
            return False
        strategy = request["scope"].get("strategy")
        if row["event_id"] != "admission:" + CapabilityScope(**request["scope"]).key:
            return False
        if (request.get("context") != context or request.get("rule") != rule
                or request.get("binding") != binding or strategy not in strategies
                or assessment.get("strategy") != strategy
                or assessment.get("financial_authority") is not False
                or type(assessment.get("heads")) is not list
                or type(assessment.get("valid_until")) not in (int, float)
                or assessment["valid_until"] <= accepted_at
                or type(assessment.get("certification")) is not dict
                or type(assessment.get("rule")) is not dict
                or type(assessment.get("source_refs")) is not list):
            return False
        covered.add(strategy)
    if covered != set(strategies):
        return False
    event_row = coordinator.store.get(intent.get("event_state_id"))
    event = event_row["body"].get("details")
    if (event_row["seq"] >= origin_seq or event_row["kind"] != "COORDINATOR_EVENT"
            or event_row["event_id"] != "event-risk:" + digest(event_id)
            or type(event) is not dict or event.get("version") != EVENT_RISK_VERSION
            or type(event.get("request")) is not dict or type(event["request"].get("binding")) is not dict
            or event["request"].get("context") != context or event["request"].get("binding") != binding
            or event.get("financial_authority") is not False
            or type(event.get("valid_until")) not in (int, float)
            or event["valid_until"] <= accepted_at
            or type(event.get("safety")) is not dict or type(event["safety"].get("flags")) is not dict):
        return False
    flags = event["safety"]["flags"]
    if (any(flags.get(k) is not False for k in ("no_new_orders", "manual_review", "quarantined"))
            or (intent.get("direction") == "BUY" and flags.get("reduce_only") is not False)):
        return False
    value_row = coordinator.store.get(intent.get("valuation_id"))
    value = value_row["body"].get("details")
    if (value_row["seq"] >= origin_seq or value_row["kind"] != "MEASUREMENT"
            or value_row["event_id"] != event_id or type(value) is not dict
            or value.get("version") != EV_VERSION or value.get("binding") != binding
            or value.get("financial_authority") is not False
            or value.get("target") != intent.get("target")
            or value.get("collateral_asset") != coordinator.policy.collateral_asset
            or type(value.get("as_of")) not in (int, float)
            or value["as_of"] > accepted_at
            or value.get("execution_status") != "NOT_SUBMITTED"
            or number(value.get("units")) != number(intent.get("units"))
            or number(intent.get("filled_units")) != 0
            or intent.get("cancel_requested") is not False):
        return False
    release_id = intent.get("source_release_id")
    directional_release = False
    if release_id is not None:
        release_row = coordinator.store.get(release_id)
        release = release_row["body"].get("details")
        if (release_row["seq"] >= origin_seq or release_row["kind"] != "REGISTRY"
                or type(release) is not dict or release.get("version") != SOURCE_RELEASE_VERSION
                or type(release.get("assessment")) is not dict):
            return False
        assessment = release["assessment"]
        if (assessment.get("context") != context or assessment.get("rule") != rule
                or assessment.get("binding") != binding
                or assessment.get("admission_id") not in admission_ids
                or assessment.get("event_state_id") != intent.get("event_state_id")
                or assessment.get("book_id") != value.get("book", {}).get("book_id")
                or assessment.get("financial_authority") is not False
                or type(assessment.get("valid_until")) not in (int, float)
                or assessment["valid_until"] <= accepted_at):
            return False
        directional_release = assessment.get("directional_event_data_eligible") is True
    if (intent.get("direction") == "BUY" and event.get("ordinary_new_risk_research_allowed") is not True
            and not directional_release):
        return False
    if intent.get("direction") == "BUY":
        if value.get("valuation_type") != "SETTLEMENT" or value.get("outcome") != "ACCEPT_RESEARCH":
            return False
        ev, costs = value.get("conservative_ev_per_share"), value.get("costs")
    elif intent.get("direction") == "SELL":
        if value.get("valuation_type") != "EXIT_COMPARISON" or value.get("outcome") != "REDUCE_RESEARCH_CANDIDATE":
            return False
        ev, costs = value.get("sale_advantage_per_share"), value.get("sale_costs")
    else:
        return False
    book = value.get("book")
    if (type(book) is not dict or type(costs) is not dict or costs.get("complete") is not True
            or value["target"].get("token_id") != intent.get("token_id")
            or number(ev) <= coordinator.policy_amount("minimum_ev_per_share")
            or number(intent.get("conservative_ev_total")) != number(ev)*number(intent["units"])):
        return False
    price, fees = number(book.get("worst_consumed_price")), number(costs.get("known_total_per_share"))
    bound = price+fees if intent["direction"] == "BUY" else price-fees
    capital = number(intent["units"])*bound if intent["direction"] == "BUY" else number(intent["units"])
    return (number(intent.get("unit_collateral_bound")) == bound
            and number(intent.get("capital_at_risk")) == capital)


def _verify_intent_provenance(coordinator: PaperCoordinator, state: dict, intent: dict,
                              history: tuple, deadline: float) -> bool:
    """Fail-closed historical check that ``intent`` has genuine originating lineage.

    Requires the first account record containing this intent to be an accepted
    COORDINATE command with the exact proposal and complete historical gate
    lineage. Later account records must preserve its fixed fields and event
    identity. This does not demand that the old gate evidence remain fresh now.
    The bounded history and malformed evidence fail closed.
    """
    if type(intent) is not dict or intent.get("financial_authority") is not False or not history:
        return False
    intent_id = intent.get("proposal_id")
    if type(intent_id) is not str or not intent_id or state.get("intents", {}).get(intent_id) != intent:
        return False
    origin = None
    origin_context = None
    origin_rule = None
    previous = None
    previous_row = None
    previous_state = coordinator._state(None)
    mutable = {"status", "filled_units", "cancel_requested"}
    try:
        for row in history:
            details = row["body"].get("details")
            if (type(details) is not dict or details.get("version") != COORDINATOR_VERSION
                    or details.get("policy_sha256") != coordinator.policy_sha
                    or type(details.get("state")) is not dict
                    or details["state"].get("account_id") != coordinator.policy.account_id
                    or details["state"].get("execution_namespace") != coordinator.store.namespace
                    or details["state"].get("financial_authority") is not False
                    or type(details["state"].get("intents")) is not dict
                    or type(details.get("request")) is not dict
                    or details["request"].get("action") not in {
                        "COORDINATE", "TRANSITION", "RECOVER", "FILL", "TERMINAL"}):
                return False
            if (not _account_shape_valid(details["state"])
                    or not _account_step_valid(coordinator, previous_row, previous_state, row, details)
                    or not _effects_reproduced(coordinator, row, deadline)):
                return False
            current = details["state"]["intents"].get(intent_id)
            if origin is None:
                if current is None:
                    previous_row = row
                    previous_state = details["state"]
                    continue
                request = details.get("request")
                results = details.get("results")
                if (type(request) is not dict or request.get("action") != "COORDINATE"
                        or type(request.get("proposals")) is not list
                        or type(results) is not list
                        or type(details.get("reserved_intent_ids")) is not list
                        or intent_id not in details["reserved_intent_ids"]
                        or details.get("execution_status") != "NOT_SUBMITTED"
                        or details.get("risk", {}).get("accepted") is not True
                        or sum(r.get("proposal_id") == intent_id and r.get("outcome") == "RESERVED_RESEARCH" for r in results if type(r) is dict) != 1
                        or type(current) is not dict or current.get("status") != "RESERVED"
                        or current.get("financial_authority") is not False):
                    return False
                proposals = [p for p in request["proposals"] if type(p) is dict and p.get("proposal_id") == intent_id]
                preparation = details["effect_inputs"].get("preparation")
                prepared = preparation.get("prepared") if type(preparation) is dict else None
                if (type(prepared) is not list or any(type(p) is not dict for p in prepared)
                        or details.get("ranking") != rank_candidates(tuple(prepared))
                        or sum(p.get("proposal_id") == intent_id and p == {
                            k: v for k, v in current.items() if k != "conservative_ev_per_capital"}
                               for p in prepared) != 1
                        or current not in details["ranking"]):
                    return False
                if len(proposals) != 1 or not _historical_lineage(coordinator, details["state"], current, proposals[0],
                                                               row["seq"], row["body"]["recorded_at"]):
                    return False
                origin = current
                origin_context = details["state"]["contexts"][current["event_id"]]
                origin_rule = details["state"]["rules"][current["event_id"]]
            else:
                if type(current) is not dict:
                    return False
                if not _same({k: v for k, v in current.items() if k not in mutable},
                             {k: v for k, v in origin.items() if k not in mutable}):
                    return False
                if (details["state"].get("contexts", {}).get(origin["event_id"]) != origin_context
                        or details["state"].get("rules", {}).get(origin["event_id"]) != origin_rule):
                    return False
                if current != previous and details.get("request", {}).get("action") not in {
                    "TRANSITION", "RECOVER", "FILL", "TERMINAL"}:
                    return False
            previous = current
            previous_row = row
            previous_state = details["state"]
        return origin is not None and previous == intent
    except (EvidenceError, TypeError, KeyError, ValueError, AttributeError, InvalidOperation):
        return False


def genuine_reserved_intents(coordinator: PaperCoordinator, *, state: dict | None = None,
                            history: tuple | None = None) -> tuple:
    """The subset of ``coordinator``'s currently-reserved intents with verified
    originating provenance (public reuse point for other readiness probes).

    Grants no execution, order, or funding authority. It narrows unresolved
    intents to those with accepted command, historical gate, and continuous
    account-state evidence in this store.
    """
    if type(coordinator) is not PaperCoordinator:
        raise EvidenceError("R08_TYPED_COORDINATOR_REQUIRED")
    if state is None or history is None:
        head = _validated_account_head(coordinator)
        state = coordinator._state(head)
        history = _account_history(coordinator, head)
    deadline = time.monotonic() + 10.0
    return tuple(
        intent for intent in _candidate_intents(state)
        if _verify_intent_provenance(coordinator, state, intent, history, deadline)
    )


def evaluate_scenario_reservation_readiness(coordinator: PaperCoordinator, *,
                                            _snapshot: tuple | None = None) -> ScenarioReservationReadiness:
    """Classify the *current* persisted state of ``coordinator``'s account.

    ``coordinator`` must already be a real ``PaperCoordinator`` the caller
    built against the exact store/policy/correlation/limits it wants probed
    (nothing here constructs, guesses, or relaxes any of those). The account
    state and risk snapshot are read via ``PaperCoordinator``'s own private
    ``_head``/``_state``/``_risk`` -- the identical path ``.snapshot()`` uses
    -- rather than re-parsing the raw audit record, so this probe can never
    disagree with the reviewed coordinator about what its own evidence means.
    Only intents whose own originating lineage independently verifies (see
    ``_verify_intent_provenance``) count as a genuine reservation; an
    account-state record whose shape was never produced by a real
    ``coordinate()`` call is rejected rather than trusted.
    """
    if type(coordinator) is not PaperCoordinator:
        raise EvidenceError("R08_TYPED_COORDINATOR_REQUIRED")
    if _snapshot is None:
        row = _validated_account_head(coordinator)
        state = coordinator._state(row)
        history = _account_history(coordinator, row)
    else:
        state, history = _snapshot
    if not _account_shape_valid(state):
        return ScenarioReservationReadiness(SCHEMA, OUTCOME_NO_RESERVATION, 0, "0", False, False,
                                            ("MALFORMED_ACCOUNT_EVIDENCE",))
    try:
        risk = coordinator._risk(state)
    except (EvidenceError, TypeError, KeyError, ValueError, AttributeError, InvalidOperation):
        return ScenarioReservationReadiness(SCHEMA, OUTCOME_NO_RESERVATION, 0, "0", False, False,
                                            ("MALFORMED_ACCOUNT_EVIDENCE",))
    reasons: list = []
    candidates = _candidate_intents(state)
    active = genuine_reserved_intents(coordinator, state=state, history=history)
    financial_authority_clean = (
        risk.get("financial_authority") is False and state.get("financial_authority") is False
    )
    if not financial_authority_clean:
        reasons.append("FINANCIAL_AUTHORITY_FLAG_UNEXPECTED")
    provenance_clean = len(active) == len(candidates)
    if not provenance_clean:
        reasons.append("RESERVED_INTENT_PROVENANCE_UNVERIFIED")
    elif not active:
        reasons.append("NO_UNRESOLVED_RESERVED_INTENT")
    verified_cash = sum((number(i["units"])-number(i["filled_units"]))*number(i["unit_collateral_bound"])
                        for i in active if i["direction"] == "BUY")
    if not risk["accepted"]:
        reasons.append("ACCOUNT_SCENARIO_RISK_NOT_ACCEPTED")
    elif active and verified_cash <= 0:
        reasons.append("NO_POSITIVE_CASH_RESERVATION")
    outcome = OUTCOME_NO_RESERVATION if reasons else OUTCOME_DEMONSTRATED
    return ScenarioReservationReadiness(
        schema=SCHEMA,
        outcome=outcome,
        reserved_intent_count=len(active),
        reserved_cash=str(verified_cash),
        risk_accepted=bool(risk["accepted"]) and provenance_clean,
        financial_authority=False,
        reasons=tuple(sorted(reasons)),
    )
