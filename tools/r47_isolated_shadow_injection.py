"""Isolated, explicit, research-only injection of one immutable candidate bundle.

This module stands in for `model_registry.DecisionModelPin` for research
evidence only. It never reads `/var/lib/alpha-v11/model-authority` state,
never constructs `model_registry.ApprovedArtifactReader`, never promotes,
approves, or writes any protected pointer, and financial authority is always
False and cannot be overridden by a caller -- `IsolatedResearchInjection` is
frozen and its own constructor rejects any other value. No production or
live decision-site module may import this module; that boundary is enforced
by a static regression test in
tests/test_v11_r47_real_candidate_shadow_injection.py, mirroring the existing
learner-plane import-boundary check in tests/test_v11_offline_learning.py.

Every value produced here is labeled ISOLATED_RESEARCH_INJECTION /
NOT_HOST_APPROVED / NO_PROMOTION and exists only to exercise the real,
already-reviewed prediction machinery (`model_artifacts.predict_with_bundle`)
against a caller-supplied private `ArtifactStore` bundle -- for example the
real-data research candidates recorded in
docs/V11_R47_REAL_CANDIDATE_SHADOW_EVIDENCE.md. A validated hash is still not
a promotion review: see model_artifacts.py's own module docstring.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from polymarket_scanner.v11.evidence import EvidenceError
from polymarket_scanner.v11.model_artifacts import ArtifactStore, PinnedBundle, predict_with_bundle


ISOLATED_RESEARCH_MODE = 'V11_SHADOW'
ISOLATED_RESEARCH_STATUS = 'ISOLATED_RESEARCH_INJECTION'


@dataclass(frozen=True)
class IsolatedResearchInjection:
    """A labeled, non-authoritative stand-in for a champion model pin.

    Every label field is fixed by `__post_init__`, not just by convention: a
    caller cannot construct or `dataclasses.replace` this into claiming host
    approval, promotion authority, or financial authority.
    """
    bundle: PinnedBundle
    target: str
    mode: str = ISOLATED_RESEARCH_MODE
    status: str = ISOLATED_RESEARCH_STATUS
    host_approved: bool = False
    promotion_authority: bool = False
    financial_authority: bool = False

    def __post_init__(self):
        if not isinstance(self.bundle, PinnedBundle):
            raise EvidenceError('ISOLATED_INJECTION_PINNED_BUNDLE_REQUIRED')
        payload = self.bundle.payload  # Re-verify full bundle/component integrity, not just construction-time trust.
        if payload['bundle']['financial_authority'] is not False:
            raise EvidenceError('ISOLATED_INJECTION_CANNOT_CLAIM_AUTHORITY')
        if payload['bundle']['target'] != self.target:
            raise EvidenceError('ISOLATED_INJECTION_TARGET_MISMATCH')
        if (self.mode != ISOLATED_RESEARCH_MODE or self.status != ISOLATED_RESEARCH_STATUS
                or self.host_approved is not False or self.promotion_authority is not False
                or self.financial_authority is not False):
            raise EvidenceError('ISOLATED_INJECTION_LABEL_FIXED')


def inject_isolated_research_candidate(*, private_root: Path, bundle_sha256: str) -> IsolatedResearchInjection:
    """The only constructor. `private_root` is caller-supplied and explicit:
    there is no default, no discovery, and no fallback to protected state or
    `/var/lib/alpha-v11`. Every component hash is validated by the existing
    `ArtifactStore`/`PinnedBundle` machinery before this returns.
    """
    store = ArtifactStore(Path(private_root))
    pinned = store.pin(bundle_sha256)
    return IsolatedResearchInjection(pinned, pinned.payload['bundle']['target'])


def run_isolated_shadow_prediction(injection: IsolatedResearchInjection, rule, components, *,
                                    as_of: float, max_source_age_seconds: float,
                                    observed=None, remaining_coverage=None) -> dict:
    """Exercise the real prediction machinery with an isolated candidate.

    This calls the exact `predict_with_bundle` used by every real decision
    site; it never touches protected model-authority state and never creates
    a TRADE/order/account record. The returned mapping repeats the fixed
    research-only labels so a caller cannot mistake this for a champion
    decision without inspecting the payload.
    """
    if not isinstance(injection, IsolatedResearchInjection) or injection.financial_authority is not False:
        raise EvidenceError('ISOLATED_INJECTION_CANNOT_CLAIM_AUTHORITY')
    prediction = predict_with_bundle(injection.bundle, rule, components, as_of=as_of,
        max_source_age_seconds=max_source_age_seconds, observed=observed, remaining_coverage=remaining_coverage)
    return {'status': injection.status, 'mode': injection.mode, 'host_approved': injection.host_approved,
            'promotion_authority': injection.promotion_authority, 'financial_authority': injection.financial_authority,
            'prediction': prediction.payload}
