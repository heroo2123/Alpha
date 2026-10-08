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

`_costs` reports declared coverage only: any numeric `CostComponent`, however
constructed by the caller, flips its covered categories out of `missing`.
That is correct for `_costs` (`DECLARED_ASSUMPTIONS_NOT_INDEPENDENT_ATTESTATION`,
never this module's claim to make), but a manifest that echoed it as this
module's own per-category `status` would let one caller-supplied component --
including a single zero-value component spanning all seven categories -- make
every category, including the six with no in-tree producer or reviewed
reserve, look source-backed/complete.

This module never promotes a caller-supplied component to source-backed,
full stop -- there is no `KNOWN` status. `CostComponent` is a public, frozen
dataclass (`valuation.py:28-56`); any caller can construct one with any
`name`, `assumption_sha256`, `priced_buy_limit`, `post_only`, and
`valid_until` they like, including values that exactly mimic the shape
`buy_fee_cost` produces. Registry membership
(`name in _COST_PRODUCER_REFERENCE`) only says an in-tree producer function
exists for that category; it says nothing about whether *this* component was
actually returned by that producer, let alone whether the producer's own
target/freshness checks ran against the caller's real target. Checking field
shape (a name, a hash, a `valid_until`) cannot establish that either, since
every one of those fields is caller-supplied and caller-forgeable. Without a
reviewed, unforgeable producer-attestation mechanism -- which does not exist
in this tree and this module does not invent -- the only sound behavior is to
never claim source-backed knowledge from a caller-supplied component, genuine
or not. `producer_in_tree`/`producer_reference` report producer *availability*
only (is there in-tree code that could, if called, produce this category's
cost) and must never be read as a claim about any supplied component's
provenance. Every declared-but-not-missing category -- single, bundled,
zero or not -- is reported `DECLARED_UNVERIFIED`: visibly covered per
`_costs`, never promoted further. `cost_summary` keeps `_costs`' own
`complete` field verbatim (declared-coverage completeness); the separate
`source_backed_complete` field is therefore always `False` -- there is no
path by which this module attests a category as source-backed.
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


def _category_status(name: str, *, missing: set, unknown: set) -> str:
    if name in unknown:
        return 'UNKNOWN_PER_SHARE'
    if name in missing:
        return 'MISSING'
    return 'DECLARED_UNVERIFIED'


def cost_manifest(costs: tuple[CostComponent, ...] = (), *, required: frozenset = ENTRY_RISKS) -> dict:
    """Per-`ENTRY_RISKS`-category producer-availability/coverage inventory.

    `required` must equal the real, imported `ENTRY_RISKS` set exactly;
    anything else (a forged/mismatched category set) is refused.

    There is no `KNOWN` status: a caller-supplied `CostComponent` can never
    be distinguished from a forgery by this module (see module docstring),
    so every declared-but-not-missing category -- single-category or
    bundled, genuine producer output or not -- is `DECLARED_UNVERIFIED`.
    `source_backed_complete` is always `False`.
    """
    if frozenset(required) != ENTRY_RISKS:
        raise EvidenceError('ENTRY_RISKS_SET_MISMATCH')
    result = _costs(costs, horizon=PAYOUT, required=ENTRY_RISKS, already=frozenset())
    missing, unknown = set(result['missing']), set(result['unknown'])
    categories = tuple(
        dict(name=name, producer_in_tree=name in _COST_PRODUCER_REFERENCE,
             producer_reference=_COST_PRODUCER_REFERENCE.get(name),
             status=_category_status(name, missing=missing, unknown=unknown))
        for name in sorted(ENTRY_RISKS)
    )
    return dict(categories=categories, cost_summary=result, source_backed_complete=False)


def prerequisite_manifest(costs: tuple[CostComponent, ...] = (), *, required: frozenset = ENTRY_RISKS) -> dict:
    """Combined seven-cost / three-metric structural inventory.

    Never a promotion: always `financial_authority=False`,
    `admission_eligible=False`, and a non-`*_DEMONSTRATED` status.
    """
    return dict(schema=SCHEMA, event_metrics=metric_manifest(), entry_risks=cost_manifest(costs, required=required),
               financial_authority=False, admission_eligible=False,
               promotion_status='STRUCTURAL_INVENTORY_NOT_A_PROMOTION')
