"""Conservative sizing and deterministic ranking inside supplied fixed ceilings."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from decimal import Decimal, ROUND_FLOOR, localcontext

from .evidence import EvidenceError, digest, identity, sha
from .scenario_risk import number


@dataclass(frozen=True)
class SizingFactors:
    ev_quality: str
    forecast_confidence: str
    source_confidence: str
    liquidity_quality: str
    station_horizon_quality: str
    settlement_time: str
    event_state: str
    portfolio_exposure: str
    strategy_quality: str
    evidence_sha256: str

    def __post_init__(self):
        sha(self.evidence_sha256)
        for key, value in asdict(self).items():
            if key != 'evidence_sha256' and not 0 <= number(value) <= 1:
                raise EvidenceError('SIZING_MAY_ONLY_REDUCE_FIXED_CEILING')


def size_within_ceiling(*, base_units: str, approved_max_units: str, quantity_step: str,
                        factors: SizingFactors, policy_sha256: str) -> dict:
    sha(policy_sha256)
    base, ceiling, step = number(base_units), number(approved_max_units), number(quantity_step)
    if base <= 0 or ceiling <= 0 or step <= 0 or step > ceiling:
        raise EvidenceError('SIZING_QUANTITY_INVALID')
    if base > ceiling:
        return dict(outcome='SKIP', reason='BASE_SIZE_EXCEEDS_APPROVED_CAP', units='0', financial_authority=False)
    with localcontext() as context:
        context.prec = 80
        scaled = base
        for name, value in asdict(factors).items():
            if name != 'evidence_sha256':
                scaled *= number(value)
        units = (scaled/step).to_integral_value(rounding=ROUND_FLOOR)*step
    return dict(outcome='SIZED_RESEARCH' if units > 0 else 'SKIP', reason='REDUCTIONS_WITHIN_FIXED_CAP',
                units=str(units), base_units=base_units, approved_max_units=approved_max_units,
                quantity_step=quantity_step, factors=asdict(factors), policy_sha256=policy_sha256,
                hard_limits_changed=False, financial_authority=False)


def rank_candidates(candidates: tuple[dict, ...]) -> list[dict]:
    """Full-capital EV density, then absolute EV and stable identity; never Kelly.

    Existing unresolved reservations remain unavailable. Allocation performs the
    scenario/correlation check after each choice and records rejected contenders.
    """
    if type(candidates) is not tuple or len(candidates) > 64:
        raise EvidenceError('RANKING_CANDIDATE_BOUND')
    seen, ranked = set(), []
    for candidate in candidates:
        key = identity(candidate['proposal_id'])
        if key in seen:
            raise EvidenceError('RANKING_DUPLICATE_ID')
        seen.add(key)
        ev, capital = number(candidate['conservative_ev_total']), number(candidate['capital_at_risk'])
        if ev <= 0 or capital <= 0:
            raise EvidenceError('RANKING_POSITIVE_EV_AND_CAPITAL_REQUIRED')
        with localcontext() as context:
            context.prec = 80
            density = ev/capital
        ranked.append(dict(candidate, conservative_ev_per_capital=str(density)))
    return sorted(ranked, key=lambda row: (-Decimal(row['conservative_ev_per_capital']),
                                         -Decimal(row['conservative_ev_total']), row['proposal_id']))
