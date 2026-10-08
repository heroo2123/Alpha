"""Offline PAPER prerequisite inventory; never supplies admission or risk metrics.

An optional observation is caller supplied and therefore only descriptive. Its
archive and policy must be independently checked before any other use.
"""
from .evidence import EvidenceError, digest, finite, identity, sha
from .paper_risk_observation import VERSION as OBSERVATION_VERSION
from .valuation import ENTRY_RISKS


VERSION = 'alpha_v11_paper_prerequisites_v1'
METRICS = ('time_to_settlement_seconds', 'adverse_fills', 'recent_markout_per_share')
RISKS = tuple(sorted(ENTRY_RISKS))


def _diagnostic(observation, *, account_id, event_id, rule_fingerprint):
    if observation is None:
        return None, 'NO_ARCHIVED_DIAGNOSTIC_SUPPLIED'
    if type(observation) is not dict or len(observation) > 48:
        return None, 'DIAGNOSTIC_SHAPE_UNKNOWN'
    try:
        if (observation.get('version') != OBSERVATION_VERSION
                or observation.get('namespace') != 'V11_PAPER'
                or observation.get('account_id') != account_id
                or observation.get('event_id') != event_id
                or observation.get('rule_fingerprint') != rule_fingerprint
                or observation.get('financial_authority') is not False
                or observation.get('admission_eligible') is not False
                or observation.get('execution_status') not in {
                    'OBSERVED_SYNTHETIC_DIAGNOSTIC', 'NO_RECONCILED_RECENT_PAPER_FILLS', 'UNKNOWN'}
                or observation.get('event_metrics_adverse_fills') is not None
                or observation.get('event_metrics_recent_markout_per_share') is not None):
            return None, 'DIAGNOSTIC_SCOPE_OR_AUTHORITY_UNKNOWN'
        refs = observation.get('evidence_ids')
        if type(refs) is not list or len(refs) > 4096 or len(set(refs)) != len(refs):
            return None, 'DIAGNOSTIC_REFERENCES_UNKNOWN'
        for ref in refs:
            identity(ref)
        for key in ('policy_sha256', 'frontier_tip_sha256', 'frontier_sha256', 'replay_sha256'):
            sha(observation[key])
        observed_at = finite(observation['observed_at'])
        if observation.get('evidence_class') != 'SYNTHETIC_PAPER_DIAGNOSTIC':
            return None, 'DIAGNOSTIC_EVIDENCE_CLASS_UNKNOWN'
    except (EvidenceError, KeyError, TypeError, ValueError):
        return None, 'DIAGNOSTIC_PROVENANCE_UNKNOWN'
    return dict(version=OBSERVATION_VERSION, status=observation.get('execution_status'),
                evidence_class='SYNTHETIC_PAPER_DIAGNOSTIC_UNVERIFIED',
                observed_at=observed_at, policy_sha256=observation['policy_sha256'],
                frontier_tip_sha256=observation['frontier_tip_sha256'],
                frontier_sha256=observation['frontier_sha256'],
                replay_sha256=observation['replay_sha256'], evidence_ids=list(refs)), None


def inventory(*, account_id, event_id, rule_fingerprint, at, observation=None):
    """Describe missing inputs without deriving values or reading mutable state."""
    identity(account_id); identity(event_id); sha(rule_fingerprint)
    at = finite(at)
    diagnostic, diagnostic_reason = _diagnostic(observation, account_id=account_id,
                                                event_id=event_id, rule_fingerprint=rule_fingerprint)
    metric_reasons = {
        'time_to_settlement_seconds': 'REVIEWED_NEW_RISK_CUTOFF_POLICY_AND_CAUSAL_SOURCE_REQUIRED',
        'adverse_fills': 'EXECUTION_HEALTH_PROMOTION_CONTRACT_REQUIRED',
        'recent_markout_per_share': 'EXECUTION_HEALTH_PROMOTION_CONTRACT_REQUIRED',
    }
    metrics = {name: dict(status='UNKNOWN', value=None, reason=metric_reasons[name],
                          evidence_class='NONE_ADMISSIBLE', evidence_ids=[]) for name in METRICS}
    costs = {name: dict(status='UNKNOWN', per_share=None,
                        reason='TARGET_HORIZON_CAUSAL_BOUND_REQUIRED',
                        evidence_class='NONE_ATTESTED', evidence_ids=[]) for name in RISKS}
    body = dict(version=VERSION, namespace='V11_PAPER', account_id=account_id,
                event_id=event_id, rule_fingerprint=rule_fingerprint, observed_at=at,
                financial_authority=False, admission_eligible=False,
                engine_metrics=metrics, entry_costs=costs, complete_cost_coverage=False,
                diagnostic=diagnostic, diagnostic_reason=diagnostic_reason)
    body['manifest_sha256'] = digest(body)
    return body
