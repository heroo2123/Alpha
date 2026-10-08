"""Offline, read-only seven-cost / three-metric prerequisite manifest.

Per `/tmp/alpha-paper-r89-source-plan-20261008.report.md` sec. 5: this module
is a structural inventory only, distinct from and never calling the
integrated `tools/v11_paper_r08_r09_readiness_cli.py`. Given the already-typed
`ENTRY_RISKS` names and `CostComponent`/`_costs` machinery from
`polymarket_scanner/v11/valuation.py` (reused verbatim, never re-literaled or
re-derived), it reports, per entry-cost category, whether an in-tree
`CostComponent` producer exists and whether the caller-supplied costs leave
that category `missing` or `unknown` -- exactly as `_costs` itself would
decide. It separately reports the three `EventMetrics` fields that are
structurally unknown on main (`time_to_settlement_seconds`, `adverse_fills`,
`recent_markout_per_share`), each with a fixed, non-guessed reason string and
a reference to the real code path that leaves it `None` today.

This module touches no `EvidenceStore`, no sqlite file, no `PaperCoordinator`,
no network, and no credential -- it is pure dataclass/dict introspection over
already-imported policy objects. It never promotes anything: its output
always carries `financial_authority=False`, `admission_eligible=False`, and
a `promotion_status` that is explicitly not a `*_DEMONSTRATED` outcome. It
has no overlap with and does not regress the integrated CLI's snapshot/
race-safety surface.

Callers must pass `required=ENTRY_RISKS` (the real, imported set) explicitly
if they pass `required` at all; a forged/mismatched set (fewer or more than
the seven real categories) is refused (`ENTRY_RISKS_SET_MISMATCH`) rather than
silently truncated or padded.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from polymarket_scanner.v11.evidence import EvidenceError
from polymarket_scanner.v11.valuation import ENTRY_RISKS, PAYOUT, CostComponent, _costs

SCHEMA = 'PAPER_COST_METRIC_MANIFEST_V1'

EVENT_METRIC_FIELDS = ('time_to_settlement_seconds', 'adverse_fills', 'recent_markout_per_share')

_METRIC_REASON = {
    'time_to_settlement_seconds': 'NO_REVIEWED_SETTLEMENT_CUTOFF_POLICY',
    'adverse_fills': 'NO_REVIEWED_EXECUTION_HEALTH_PROMOTION_CONTRACT',
    'recent_markout_per_share': 'NO_REVIEWED_EXECUTION_HEALTH_PROMOTION_CONTRACT',
}

_METRIC_PRODUCER_REFERENCE = {
    'time_to_settlement_seconds': 'polymarket_scanner/v11/risk_inputs.py:189-202',
    'adverse_fills': 'polymarket_scanner/v11/paper_risk_observation.py:145-169',
    'recent_markout_per_share': 'polymarket_scanner/v11/paper_risk_observation.py:145-169',
}

_COST_PRODUCER_REFERENCE = {
    'ACQUISITION_FEES': 'polymarket_scanner/v11/valuation.py:88-108 (buy_fee_cost)',
}


def metric_manifest() -> tuple[dict, ...]:
    """The three structurally-unknown `EventMetrics` fields, never a guess."""
    return tuple(
        dict(field=field, status='UNKNOWN', reason=_METRIC_REASON[field],
             producer_reference=_METRIC_PRODUCER_REFERENCE[field])
        for field in EVENT_METRIC_FIELDS
    )


def cost_manifest(costs: tuple[CostComponent, ...] = (), *, required: frozenset = ENTRY_RISKS) -> dict:
    """Per-`ENTRY_RISKS`-category producer/coverage inventory.

    `required` must equal the real, imported `ENTRY_RISKS` set exactly;
    anything else (a forged/mismatched category set) is refused.
    """
    if frozenset(required) != ENTRY_RISKS:
        raise EvidenceError('ENTRY_RISKS_SET_MISMATCH')
    result = _costs(costs, horizon=PAYOUT, required=ENTRY_RISKS, already=frozenset())
    missing, unknown = set(result['missing']), set(result['unknown'])
    categories = tuple(
        dict(name=name, producer_in_tree=name in _COST_PRODUCER_REFERENCE,
             producer_reference=_COST_PRODUCER_REFERENCE.get(name),
             status=('UNKNOWN_PER_SHARE' if name in unknown else 'MISSING' if name in missing else 'KNOWN'))
        for name in sorted(ENTRY_RISKS)
    )
    return dict(categories=categories, cost_summary=result)


def prerequisite_manifest(costs: tuple[CostComponent, ...] = (), *, required: frozenset = ENTRY_RISKS) -> dict:
    """Combined seven-cost / three-metric structural inventory.

    Never a promotion: always `financial_authority=False`,
    `admission_eligible=False`, and a non-`*_DEMONSTRATED` status.
    """
    return dict(schema=SCHEMA, event_metrics=metric_manifest(), entry_risks=cost_manifest(costs, required=required),
               financial_authority=False, admission_eligible=False,
               promotion_status='STRUCTURAL_INVENTORY_NOT_A_PROMOTION')
