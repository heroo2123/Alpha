"""Optional pre-dispatch gate binding ``GateRuntime``
(tools/v11_r09_gate3_runtime.py) to one frozen outcome of the independently
reviewed real-evidence-preflight intake
(tools/v11_gate3_evidence_preflight_real_intake.py).

That intake module reads the real retained Gate 3 evidence-preflight
package through the already-reviewed checker and reports whether it is
``satisfied``, but -- per its own acceptance review -- nothing in the
repository actually consulted that report before a collector/launch
component could attempt dispatch; it was a standalone, read-only status
reporter with no caller. This module is the smallest wiring that makes a
report from that intake a real, enforced prerequisite: optional on
``GateRuntime`` (every existing caller that omits it is completely
unaffected), but once supplied, every ``run_attempt`` call refuses before
shared intent, budget reservation, or transport dispatch unless the bound
record's own ``satisfied`` is ``True``. The refusal is recorded in the
session. It can only ever ADD a refusal on top of ``run_attempt``'s own
existing checks (prerequisites, resources, window, control-domain hold, and
any bound ``AttemptModelGuard``) -- it never replaces or loosens one. Report
authority flags must be ``False``; records have no authority fields and grant
no execution, provider, or capture authority.

No network, no subprocess, no filesystem write anywhere in this module.
"""
from __future__ import annotations

from dataclasses import dataclass

from tools.v11_gate3_evidence_preflight_checker import (
    ELIGIBILITY_LABEL, OUTCOME_REFUSED, OUTCOME_SATISFIED,
    SCHEMA as CHECKER_SCHEMA,
)
from tools.v11_gate3_evidence_preflight_real_intake import (
    SCHEMA as REAL_INTAKE_SCHEMA,
    run_real_evidence_intake,
)
from tools.v11_r09_gate3_launch import check

# Exactly the key set ``tools.v11_gate3_evidence_preflight_real_intake.
# build_report`` produces -- never a superset or subset. A report missing or
# adding a key is refused rather than partially trusted.
_REPORT_KEYS = frozenset({
    'schema', 'intake_schema', 'outcome', 'eligibility', 'refusal_reasons',
    'generated_at_utc', 'satisfied', 'execution_authority',
    'provider_authority', 'capture_authority',
})


@dataclass(frozen=True)
class EvidenceIntakeRecord:
    """Immutable, validated snapshot of one real-evidence-intake report.

    ``from_report`` validates the full report dict shape; direct construction
    enforces the record's own schema, outcome, and refusal consistency. This
    record never re-reads evidence or re-runs the checker. The caller is
    responsible for producing a fresh report before building this record;
    this module never caches or re-fetches one.
    """
    satisfied: bool
    outcome: str
    refusal_reasons: tuple
    intake_schema: str
    generated_at_utc: str

    def __post_init__(self):
        check(type(self.satisfied) is bool, 'EVIDENCE_INTAKE_RECORD_SHAPE')
        check(self.intake_schema == REAL_INTAKE_SCHEMA,
              'EVIDENCE_INTAKE_RECORD_SCHEMA')
        check(type(self.outcome) is str and bool(self.outcome),
              'EVIDENCE_INTAKE_RECORD_SHAPE')
        check(type(self.generated_at_utc) is str and bool(self.generated_at_utc),
              'EVIDENCE_INTAKE_RECORD_SHAPE')
        check(type(self.refusal_reasons) is tuple and
              all(type(r) is str and r for r in self.refusal_reasons),
              'EVIDENCE_INTAKE_RECORD_SHAPE')
        # The checker's own invariant (CheckResult.__post_init__): refusal
        # reasons are present iff the outcome is unsatisfied. A record
        # claiming both/neither is internally inconsistent and must never be
        # treated as admissible.
        check(self.satisfied == (not self.refusal_reasons),
              'EVIDENCE_INTAKE_RECORD_CONSISTENCY')
        check(self.outcome == (OUTCOME_SATISFIED if self.satisfied else OUTCOME_REFUSED),
              'EVIDENCE_INTAKE_RECORD_CONSISTENCY')

    @classmethod
    def from_report(cls, report: dict) -> 'EvidenceIntakeRecord':
        """Build from exactly the dict shape ``build_report`` produces.
        Refuses (fails closed) on any missing/extra key or on any authority
        flag that is not literally ``False`` -- a malformed, truncated or
        tampered report can never be silently admitted, and this record can
        never itself grant authority the intake module did not already
        document as forbidden."""
        check(type(report) is dict and set(report) == _REPORT_KEYS,
              'EVIDENCE_INTAKE_REPORT_SCHEMA')
        check(report['execution_authority'] is False and
              report['provider_authority'] is False and
              report['capture_authority'] is False,
              'EVIDENCE_INTAKE_REPORT_AUTHORITY_FORBIDDEN')
        check(report['schema'] == CHECKER_SCHEMA and
              report['eligibility'] == ELIGIBILITY_LABEL and
              report['outcome'] == (OUTCOME_SATISFIED if report['satisfied'] is True
                                    else OUTCOME_REFUSED),
              'EVIDENCE_INTAKE_REPORT_CONSISTENCY')
        check(type(report['refusal_reasons']) is list, 'EVIDENCE_INTAKE_REPORT_SCHEMA')
        return cls(satisfied=report['satisfied'], outcome=report['outcome'],
                    refusal_reasons=tuple(report['refusal_reasons']),
                    intake_schema=report['intake_schema'],
                    generated_at_utc=report['generated_at_utc'])


class EvidenceIntakeGuard:
    """Binds one ``GateRuntime`` to one frozen ``EvidenceIntakeRecord``.

    Stateless and deterministic by construction: the record is frozen at
    guard-construction time, so every ``require_admission`` call against the
    same guard returns the exact same verdict -- there is no retry path that
    can flip a refusal into an admission, and no dispatch this guard admits
    can ever duplicate accounting (it mutates nothing; ``run_attempt``'s own
    durable session/shared/budget state is untouched by this check).
    """

    def __init__(self, record: EvidenceIntakeRecord):
        check(type(record) is EvidenceIntakeRecord, 'EVIDENCE_INTAKE_GUARD_SHAPE')
        self._record = record

    @property
    def record(self) -> EvidenceIntakeRecord:
        return self._record

    def require_admission(self) -> None:
        check(self._record.satisfied, 'RUNTIME_EVIDENCE_INTAKE_NOT_SATISFIED')

    @classmethod
    def from_real_intake(cls, *, repo_docs_dir, binding_path) -> 'EvidenceIntakeGuard':
        """The one sanctioned way to bind a real (non-synthetic)
        ``GateRuntime`` to the actual retained evidence-preflight package:
        runs the independently reviewed, read-only
        ``run_real_evidence_intake`` fresh (never a cached or stale report)
        and wraps its own report dict. No network, no subprocess, no write;
        see that module's own docstring for the full safety boundary. Grants
        no authority beyond what that report already documents."""
        report = run_real_evidence_intake(repo_docs_dir=repo_docs_dir,
                                          binding_path=binding_path)
        return cls(EvidenceIntakeRecord.from_report(report))
