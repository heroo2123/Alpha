"""Offline composition of fresh real-evidence intake with one Gate 3 attempt.

This entrypoint is deliberately separate from provider and launch paths. It
reads the tracked intake binding for every call, while the attempted exchange
must remain a synthetic fixture. A satisfied intake is only one runtime
prerequisite and grants no provider, capture, or execution authority.
"""
from __future__ import annotations

from pathlib import Path

from tools.v11_gate3_evidence_intake_guard import EvidenceIntakeGuard
from tools.v11_r09_gate3_launch import check
from tools.v11_r09_gate3_runtime import (
    FakeClock, FrozenPlan, GateRuntime, SyntheticTransport,
)


_REPO_DOCS = Path(__file__).resolve().parent.parent / 'docs'
_TRACKED_BINDING = _REPO_DOCS / 'V11_R09_GATE3_EVIDENCE_PREFLIGHT_PACKAGE_20261002.json'


def run_offline_real_intake_attempt(*, request, shared, session, budget, store,
                                    transport, clock, resources, window,
                                    allowed_peer_ips, manifest_sha256, plan,
                                    expected_plan_sha256, report_sink,
                                    attempt_model=None):
    """Run one synthetic attempt after a fresh read of the real intake.

    No caller-supplied guard or evidence path is accepted. Reconstructing a
    runtime or retrying through this function re-reads the tracked binding;
    an ``AttemptModelGuard`` alone can never stand in for the real intake.
    Valid intake refusals use ``GateRuntime``'s session refusal path. Intake
    read, integrity, or report-shape errors propagate before runtime creation.
    """
    check(type(plan) is FrozenPlan and plan.synthetic_fixture and
          type(transport) is SyntheticTransport and type(clock) is FakeClock,
          'OFFLINE_REAL_INTAKE_SYNTHETIC_ONLY')
    evidence_intake = EvidenceIntakeGuard.from_real_intake(
        repo_docs_dir=_REPO_DOCS, binding_path=_TRACKED_BINDING)
    runtime = GateRuntime(
        shared=shared, session=session, budget=budget, store=store,
        transport=transport, clock=clock, resources=resources, window=window,
        allowed_peer_ips=allowed_peer_ips, manifest_sha256=manifest_sha256,
        plan=plan, expected_plan_sha256=expected_plan_sha256,
        report_sink=report_sink, attempt_model=attempt_model,
        evidence_intake=evidence_intake)
    return runtime.run_attempt(request)
