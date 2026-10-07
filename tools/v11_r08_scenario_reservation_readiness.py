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
from decimal import Decimal

from polymarket_scanner.v11.evidence import EvidenceError
from polymarket_scanner.v11.paper_coordinator import PaperCoordinator, UNRESOLVED
from polymarket_scanner.v11.scenario_risk import number

SCHEMA = "R08_SCENARIO_RESERVATION_READINESS_V1"

OUTCOME_NO_RESERVATION = "NO_GENUINE_SCENARIO_RESERVATION_RECORDED"
OUTCOME_DEMONSTRATED = "GENUINE_ZERO_AUTHORITY_SCENARIO_RESERVATION_DEMONSTRATED"
ALLOWED_OUTCOMES = (OUTCOME_NO_RESERVATION, OUTCOME_DEMONSTRATED)


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


def evaluate_scenario_reservation_readiness(coordinator: PaperCoordinator) -> ScenarioReservationReadiness:
    """Classify the *current* persisted state of ``coordinator``'s account.

    ``coordinator`` must already be a real ``PaperCoordinator`` the caller
    built against the exact store/policy/correlation/limits it wants probed
    (nothing here constructs, guesses, or relaxes any of those). The account
    state and risk snapshot are read via ``PaperCoordinator``'s own private
    ``_head``/``_state``/``_risk`` -- the identical path ``.snapshot()`` uses
    -- rather than re-parsing the raw audit record, so this probe can never
    disagree with the reviewed coordinator about what its own evidence means.
    """
    if type(coordinator) is not PaperCoordinator:
        raise EvidenceError("R08_TYPED_COORDINATOR_REQUIRED")
    row = coordinator._head()
    state = coordinator._state(row)
    risk = coordinator._risk(state)
    reasons: list = []
    active = [
        intent for intent in state["intents"].values()
        if intent["status"] in UNRESOLVED and number(intent["units"]) > number(intent["filled_units"])
    ]
    financial_authority_clean = (
        risk.get("financial_authority") is False and state.get("financial_authority") is False
    )
    if not financial_authority_clean:
        reasons.append("FINANCIAL_AUTHORITY_FLAG_UNEXPECTED")
    if not active:
        reasons.append("NO_UNRESOLVED_RESERVED_INTENT")
    if not risk["accepted"]:
        reasons.append("ACCOUNT_SCENARIO_RISK_NOT_ACCEPTED")
    elif Decimal(risk["reserved_cash"]) <= 0:
        reasons.append("NO_POSITIVE_CASH_RESERVATION")
    outcome = OUTCOME_NO_RESERVATION if reasons else OUTCOME_DEMONSTRATED
    return ScenarioReservationReadiness(
        schema=SCHEMA,
        outcome=outcome,
        reserved_intent_count=len(active),
        reserved_cash=risk["reserved_cash"],
        risk_accepted=bool(risk["accepted"]),
        financial_authority=False,
        reasons=tuple(sorted(reasons)),
    )
