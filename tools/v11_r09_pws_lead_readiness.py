"""PAPER V11 READY requirement 9 evidence-sufficiency readiness probe.

Requirement 9 ("PWS_OBSERVATION_LEAD causal replay/shadow path; PWS never
settlement authority; coordinator conflict/netting",
`docs/V11_SHADOW_READINESS_20261004.md`) stays PARTIAL because the current
live KATL Shadow plan has never included a PWS source: the Oct 5-7 forward
harvest found zero `PWS_OBSERVATION` records of any kind in any retained
ledger (`docs/V11_FORWARD_EVIDENCE_HARVEST_20261007.md` sec. 2.4). That is a
live collection-plan omission (the deployed `census_worker.CensusPlan` for
KATL is never constructed with `pws=PWSQualityPlan(...)`), not a missing code
path, and that live plan construction lives entirely outside this repository
(on the deployment host) -- out of scope for this isolated, nonfinancial
worktree, which makes no service or provider-configuration change.

Every piece of the actual PWS sleeve already exists and is independently
reviewed/tested in this repository: `weather_sources.madis_request` /
`parse_madis_xml` / `normalize_weather_capture` (capture + normalize),
`census_worker.CensusPlan.pws` + `pws_runtime.PWSQualityWorker` (collection +
QC), `pws_lead.PWSObservationLead` (paired observation-only research), and
`pws_admission.PWSPreconfirmation` (the revocable join of a separately
reviewed NEXT_OBSERVATION lead champion to a separately reviewed
FINAL_CONTRACT_PAYOUT champion -- see `docs/V11_PWS_OBSERVATION_LEAD.md`).
What was missing was a reviewed, independent reader that checks, against an
arbitrary evidence store, that this sleeve's three standing invariants
actually hold -- rather than trusting a status file's prose. This module is
that reader. It grants no execution, order, model, or settlement authority.

The three invariants checked here, each by calling the real, unmodified
reviewed code rather than re-deriving a second copy of its logic:

1. The named `PWSPreconfirmation` pin still independently revalidates right
   now (`pws_admission.PWSPreconfirmation.revalidate`), proving the paired
   observation/payout join is not merely historically recorded but currently
   fresh under its own stricter sensor-policy expiry.
2. That same assessment is structurally incapable of granting PWS settlement
   authority: `valuation_type == 'SETTLEMENT'` (driven by the separately
   reviewed payout champion, never the observation-lead prediction),
   `executable_exit_proceeds is None`, `strategy_eligibility ==
   'SEPARATE_ECONOMICS_STILL_REQUIRED'`, `financial_authority is False`, and
   the observation and payout bundles are provably distinct artifacts.
3. A PWS-attributed proposal lacking its paired preconfirmation is refused,
   live, right now, by the real, unmodified `PaperCoordinator._preconfirmation`
   -- not a historical unit-test assertion elsewhere -- demonstrating the
   coordinator-level conflict/netting boundary this requirement also names.

A fourth check reuses `tools.v11_r08_scenario_reservation_readiness` (not a
re-derivation of its risk/scenario arithmetic) to confirm a genuine,
PWS-attributed, zero-authority paper reservation is actually persisted, so
"PWS observed and netted as a lead" is evidence of an actual reservation, not
only of passing invariant checks in isolation.

This module opens no socket, imports no HTTP/provider client, runs no
subprocess, and writes nothing. Every argument is an explicit, caller-supplied
reference into evidence the caller already holds; nothing here is measured,
guessed, or constructed. Against today's retained Oct 5-7 ledgers (zero PWS
records) this probe reports `PWS_LEAD_SHADOW_PATH_NOT_DEMONSTRATED`; it exists
so that the day the live deployment's collection plan actually adds a PWS
source, that evidence is recognized rather than argued about.
"""
from __future__ import annotations

from dataclasses import dataclass

from polymarket_scanner.v11.evidence import EvidenceError
from polymarket_scanner.v11.paper_coordinator import PaperCoordinator
from polymarket_scanner.v11.pws_admission import PWSPreconfirmation, STRATEGY as PWS_STRATEGY
from tools.v11_r08_scenario_reservation_readiness import (
    OUTCOME_DEMONSTRATED as RESERVATION_DEMONSTRATED,
    evaluate_scenario_reservation_readiness,
    genuine_reserved_intents,
)

SCHEMA = "R09_PWS_LEAD_READINESS_V1"

OUTCOME_NOT_DEMONSTRATED = "PWS_LEAD_SHADOW_PATH_NOT_DEMONSTRATED"
OUTCOME_DEMONSTRATED = "PWS_OBSERVED_AND_NETTED_AS_LEAD_ONLY_DEMONSTRATED"
ALLOWED_OUTCOMES = (OUTCOME_NOT_DEMONSTRATED, OUTCOME_DEMONSTRATED)

_UNPAIRED_REFUSAL_REASON = "PWS_SEPARATE_OBSERVATION_AND_ECONOMICS_PIN_REQUIRED"


@dataclass(frozen=True)
class PWSLeadReadiness:
    schema: str
    outcome: str
    preconfirmation_revalidates: bool
    pws_never_settlement_authority: bool
    unpaired_pws_proposal_refused: bool
    genuine_pws_reservation_present: bool
    financial_authority: bool
    reasons: tuple

    def __post_init__(self) -> None:
        if type(self.schema) is not str or self.schema != SCHEMA:
            raise ValueError(f"not the fixed schema: {self.schema!r}")
        if self.outcome not in ALLOWED_OUTCOMES:
            raise ValueError(f"not an allowed outcome: {self.outcome!r}")
        for flag_name in (
            "preconfirmation_revalidates", "pws_never_settlement_authority",
            "unpaired_pws_proposal_refused", "genuine_pws_reservation_present",
        ):
            if type(getattr(self, flag_name)) is not bool:
                raise ValueError(f"{flag_name} must be an exact bool")
        if type(self.financial_authority) is not bool or self.financial_authority:
            raise ValueError("financial_authority must be exactly False")
        if type(self.reasons) is not tuple:
            raise ValueError("reasons must be a tuple")
        clean = (
            self.preconfirmation_revalidates and self.pws_never_settlement_authority
            and self.unpaired_pws_proposal_refused and self.genuine_pws_reservation_present
        )
        if self.outcome == OUTCOME_DEMONSTRATED and (self.reasons or not clean):
            raise ValueError("DEMONSTRATED requires all four invariants clean and no reasons")
        if self.outcome == OUTCOME_NOT_DEMONSTRATED and not self.reasons:
            raise ValueError("NOT_DEMONSTRATED outcome requires a non-empty reason")

    def to_dict(self) -> dict:
        return {
            "schema": self.schema,
            "outcome": self.outcome,
            "preconfirmation_revalidates": self.preconfirmation_revalidates,
            "pws_never_settlement_authority": self.pws_never_settlement_authority,
            "unpaired_pws_proposal_refused": self.unpaired_pws_proposal_refused,
            "genuine_pws_reservation_present": self.genuine_pws_reservation_present,
            "financial_authority": self.financial_authority,
            "reasons": sorted(self.reasons),
        }


def _qualifying_pws_intent(coordinator: PaperCoordinator, *, preconfirmation_id: str, context, rule,
                           binding: dict, payout_admission_ids: tuple):
    """The one genuinely-reserved intent this exact revalidated pair backs.

    Only a provenance-verified reservation (``genuine_reserved_intents``, R08)
    that itself names ``preconfirmation_id`` and shares the caller's exact
    event/context/binding/admissions qualifies. An unused but currently-valid
    pair, or a reservation bound to a *different* preconfirmation, must never
    be substituted in -- fresh unused evidence cannot rescue a different,
    expired reservation's pair.
    """
    for intent in genuine_reserved_intents(coordinator):
        if (any(a["strategy"] == PWS_STRATEGY for a in intent["attribution"])
                and intent.get("preconfirmation_id") == preconfirmation_id
                and intent.get("event_id") == context.event_id
                and intent.get("binding") == binding
                and tuple(intent.get("admission_ids") or ()) == tuple(payout_admission_ids)):
            return intent
    return None


def evaluate_pws_lead_readiness(
    coordinator: PaperCoordinator, *, preconfirmation_id: str, context, rule, binding: dict,
    payout_admission_ids: tuple,
) -> PWSLeadReadiness:
    """Check the three standing PWS-sleeve invariants plus a genuine reservation.

    ``coordinator`` must already be a real ``PaperCoordinator`` bound to the
    store under probe. ``context``/``rule``/``binding``/``payout_admission_ids``
    are the exact, explicit arguments the caller's own real
    ``PWSPreconfirmation`` pin was built against -- never inferred here.
    """
    if type(coordinator) is not PaperCoordinator:
        raise EvidenceError("R09_TYPED_COORDINATOR_REQUIRED")
    reasons: list = []

    try:
        assessment = PWSPreconfirmation(coordinator.store).revalidate(
            preconfirmation_id, context=context, rule=rule, binding=binding,
            payout_admission_ids=payout_admission_ids)
        preconfirmation_revalidates = True
    except EvidenceError as exc:
        assessment = None
        preconfirmation_revalidates = False
        reasons.append("PRECONFIRMATION_DOES_NOT_REVALIDATE:" + str(exc))

    pws_never_settlement_authority = False
    if assessment is not None:
        pws_never_settlement_authority = (
            assessment.get("valuation_type") == "SETTLEMENT"
            and assessment.get("executable_exit_proceeds") is None
            and assessment.get("strategy_eligibility") == "SEPARATE_ECONOMICS_STILL_REQUIRED"
            and assessment.get("financial_authority") is False
            and assessment.get("observation_bundle_sha256") != assessment.get("payout_bundle_sha256")
        )
        if not pws_never_settlement_authority:
            reasons.append("PWS_SETTLEMENT_AUTHORITY_INVARIANT_NOT_CONFIRMED")

    try:
        coordinator._preconfirmation(
            None, strategies=(PWS_STRATEGY,), context=context, rule=rule, binding=binding, admissions=())
        unpaired_pws_proposal_refused = False
        reasons.append("UNPAIRED_PWS_PROPOSAL_WAS_NOT_REFUSED")
    except EvidenceError as exc:
        unpaired_pws_proposal_refused = str(exc) == _UNPAIRED_REFUSAL_REASON
        if not unpaired_pws_proposal_refused:
            reasons.append("UNEXPECTED_UNPAIRED_REFUSAL_REASON:" + str(exc))

    reservation = evaluate_scenario_reservation_readiness(coordinator)
    qualifying_intent = _qualifying_pws_intent(coordinator, preconfirmation_id=preconfirmation_id, context=context,
                                                rule=rule, binding=binding, payout_admission_ids=payout_admission_ids)
    genuine_pws_reservation_present = reservation.outcome == RESERVATION_DEMONSTRATED and qualifying_intent is not None
    if not genuine_pws_reservation_present:
        if reservation.outcome != RESERVATION_DEMONSTRATED:
            reasons.extend("RESERVATION:" + reason for reason in reservation.reasons)
        if qualifying_intent is None:
            reasons.append("NO_PWS_ATTRIBUTED_RESERVED_INTENT_FOR_THIS_PRECONFIRMATION")

    outcome = OUTCOME_NOT_DEMONSTRATED if reasons else OUTCOME_DEMONSTRATED
    return PWSLeadReadiness(
        schema=SCHEMA,
        outcome=outcome,
        preconfirmation_revalidates=preconfirmation_revalidates,
        pws_never_settlement_authority=pws_never_settlement_authority,
        unpaired_pws_proposal_refused=unpaired_pws_proposal_refused,
        genuine_pws_reservation_present=genuine_pws_reservation_present,
        financial_authority=False,
        reasons=tuple(sorted(set(reasons))),
    )
