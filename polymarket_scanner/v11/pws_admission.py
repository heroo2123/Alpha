"""Revocable PWS pre-confirmation joins, separate from payout and order authority.

The research observation is only an input. Both model scopes and strategy
capabilities must independently pass the protected nonfinancial admission gate.
This module has no transport, account writer, model publisher or order endpoint.
"""
from dataclasses import asdict

from .certification import CapabilityScope
from .event_risk import EventContext
from .evidence import EvidenceError, EvidenceStore, ReleaseBinding, canonical, digest, finite, identity
from .model_artifacts import predict_with_bundle
from .model_registry import ActiveModelRegistry
from .probability import NEXT_OBSERVATION
from .pws_lead import VERSION as LEAD_VERSION, LeadPolicy, _lineage, _report
from .rules import RuleFingerprint
from .strategy_admission import StrategyAdmission, VERSION as ADMISSION_VERSION
from .strategy_pipeline import _model_inputs


VERSION = 'alpha_v11_pws_preconfirmation_pin_v1'
STRATEGY = 'PWS_OBSERVATION_LEAD'


def _heads(rows):
    result = {}
    for kind, event, seq in rows:
        if (kind, event) in result and result[(kind, event)] != seq:
            raise EvidenceError('PWS_PRECONFIRMATION_SOURCE_CHANGED')
        result[(kind, event)] = seq
    return tuple((k, e, s) for (k, e), s in sorted(result.items()))


class PWSPreconfirmation:
    def __init__(self, store: EvidenceStore):
        self.store = store

    def _assess(self, *, lead_id, observation_admission_id, payout_admission_id):
        lead = self.store.get(lead_id); d = lead['body'].get('details', {})
        if (lead['kind'] != 'MEASUREMENT' or d.get('version') != LEAD_VERSION
                or d.get('target') != NEXT_OBSERVATION or d.get('valuation_type') != 'OBSERVATION_ONLY'):
            raise EvidenceError('PWS_OBSERVATION_RESEARCH_REQUIRED')
        request = d['request']; rule = RuleFingerprint(**request['rule'])
        policy = LeadPolicy(**request['policy']); now = finite(self.store.clock())
        cutoff = finite(d['feature_ready_at'])
        if (cutoff != d['observation_window']['window_start'] or not cutoff <= lead['body']['recorded_at'] <= now
                or not cutoff <= now < cutoff+policy.horizon_seconds
                or d['observation_window']['window_end'] != cutoff+policy.horizon_seconds):
            raise EvidenceError('PWS_PRECONFIRMATION_WINDOW_EXPIRED_OR_CHANGED')
        admissions, originals, models = [], [], []
        for key, target in ((observation_admission_id, NEXT_OBSERVATION),
                            (payout_admission_id, 'FINAL_CONTRACT_PAYOUT')):
            row = self.store.get(key); a = row['body'].get('details', {})
            if row['kind'] != 'REGISTRY' or a.get('version') != ADMISSION_VERSION:
                raise EvidenceError('PWS_SEPARATE_STRATEGY_ADMISSION_REQUIRED')
            original = a['request']; scope = CapabilityScope(**original['scope'])
            if scope.strategy != STRATEGY or original['rule'] != asdict(rule):
                raise EvidenceError('PWS_OBSERVATION_AND_PAYOUT_SCOPE_MISMATCH')
            binding = ReleaseBinding(**original['binding'])
            result = StrategyAdmission(self.store).revalidate(key, context=EventContext(**original['context']),
                        rule=rule, binding=asdict(binding), strategies=(STRATEGY,))
            mode = 'V11_PAPER' if original['stage'] == 'PAPER' else 'V11_SHADOW'
            model = ActiveModelRegistry().pin(scope_key=scope.key, mode=mode)
            if (model.state_sha256 != result['model_state_sha256']
                    or model.bundle.sha256 != binding.bundle_sha256 or model.bundle.payload['bundle']['target'] != target):
                raise EvidenceError('PWS_SEPARATE_APPROVED_OBSERVATION_AND_PAYOUT_TARGETS_REQUIRED')
            leases = {s['evidence_id']:s for s in original['source_leases']}
            if any(key not in leases or leases[key]['role'] != role for key, role in
                   ((request['official_id'], 'OFFICIAL'), (request['pws_id'], 'PWS'))):
                raise EvidenceError('PWS_BOTH_MODELS_REQUIRE_SAME_OFFICIAL_AND_PWS_LEASES')
            admissions.append(result); originals.append(original); models.append(model)
        obs, payout = originals
        if (obs['context'] != payout['context'] or obs['stage'] != payout['stage']
                or any(obs['scope'][k] != payout['scope'][k] for k in
                       ('station', 'family', 'source_rule_family', 'strategy', 'season', 'time_of_day'))
                or any(obs['binding'][k] != payout['binding'][k] for k in
                       ('code_commit', 'code_tree', 'config_sha256', 'rule_fingerprint'))
                or obs['binding'] != request['binding']
                or {s['evidence_id'] for s in obs['source_leases'] if s['role'] == 'MODEL'} != set(request['model_ids'])):
            raise EvidenceError('PWS_MODEL_PAIR_CONTEXT_OR_RELEASE_MISMATCH')
        if request['bundle_sha256'] != models[0].bundle.sha256:
            raise EvidenceError('PWS_RESEARCH_BUNDLE_NOT_APPROVED_OBSERVATION_CHAMPION')
        heads = _heads([*admissions[0]['heads'], *admissions[1]['heads'], *d['source_heads']])
        for kind, event, seq in heads:
            head = self.store.latest(kind=kind, event_id=event)
            if (head['seq'] if head else 0) != seq:
                raise EvidenceError('PWS_PRECONFIRMATION_SOURCE_CHANGED')
        official = self.store.get(request['official_id']); _report(official, rule)
        pws = self.store.get(request['pws_id']); qc = pws['body']['payload']
        graph = _lineage(self.store, tuple(request['model_ids']), event_id=rule.payload['event_id'], cutoff=cutoff)
        if graph != d['paired_provenance']['with_pws'] or graph['pws_ids'] != [request['pws_id']]:
            raise EvidenceError('PWS_OBSERVATION_LINEAGE_MISMATCH')
        components = _model_inputs(self.store, rule, tuple(request['model_ids']), cutoff, target=NEXT_OBSERVATION)
        prediction = predict_with_bundle(models[0].bundle, rule, components, as_of=cutoff,
                                         max_source_age_seconds=policy.max_model_age_seconds)
        if prediction.payload != d['with_pws']:
            raise EvidenceError('PWS_OBSERVATION_PREDICTION_NOT_REPRODUCED')
        expiry = min(*(a['valid_until'] for a in admissions), cutoff+policy.horizon_seconds,
                     self.store.latest(kind='RULE_STATE', event_id=rule.payload['event_id'])['body']['recorded_at']+policy.max_rule_age_seconds,
                     official['body']['observed_at']+policy.max_official_age_seconds,
                     finite(qc['as_of'])+policy.max_pws_age_seconds-max(finite(a) for a in qc['observation_age_seconds']),
                     *(self.store.get(k)['body']['issued_at']+policy.max_model_age_seconds for k in request['model_ids']))
        if not now < expiry:
            raise EvidenceError('PWS_PRECONFIRMATION_SOURCE_POLICY_EXPIRED')
        for model in models:
            if not ActiveModelRegistry().revalidate(model)['passed']:
                raise EvidenceError('PWS_MODEL_CHANGED_DURING_PRECONFIRMATION')
        return dict(strategy=STRATEGY, context=payout['context'], rule=payout['rule'], binding=payout['binding'],
                    observation_admission_id=observation_admission_id, payout_admission_id=payout_admission_id,
                    lead_id=lead_id, lead_sha256=lead['sha256'], observation_prediction_sha256=prediction.sha256,
                    observation_bundle_sha256=models[0].bundle.sha256, payout_bundle_sha256=models[1].bundle.sha256,
                    model_states=[a['model_state_sha256'] for a in admissions], heads=heads, valid_until=expiry,
                    model_size_multiplier=min(a['model_size_multiplier'] for a in admissions),
                    valuation_type='SETTLEMENT', executable_exit_proceeds=None,
                    financial_authority=False, strategy_eligibility='SEPARATE_ECONOMICS_STILL_REQUIRED')

    def pin(self, record_id, *, lead_id, observation_admission_id, payout_admission_id):
        request = dict(lead_id=identity(lead_id), observation_admission_id=identity(observation_admission_id),
                       payout_admission_id=identity(payout_admission_id))
        try:
            old = self.store.get(record_id)
        except EvidenceError as exc:
            if str(exc) != 'EVIDENCE_MISSING':
                raise
        else:
            if (old['kind'] != 'REGISTRY' or old['body']['details'].get('version') != VERSION
                    or old['body']['details']['request'] != request):
                raise EvidenceError('PWS_PRECONFIRMATION_ID_COLLISION')
            return old  # Historical replay does not refresh authority or expiry.
        result = self._assess(**request)
        return self.store.audit(record_id, event_id='pws-preconfirmation:'+digest(request), kind='REGISTRY',
                 details=dict(version=VERSION, request=request, assessment=result),
                 evidence_ids=tuple(request.values()), expected_heads=result['heads'])

    def revalidate(self, record_id, *, context, rule, binding, payout_admission_ids):
        row = self.store.get(record_id); d = row['body'].get('details', {})
        if row['kind'] != 'REGISTRY' or d.get('version') != VERSION:
            raise EvidenceError('PWS_PRECONFIRMATION_PIN_REQUIRED')
        before = d['assessment']
        if (before['context'] != asdict(context) or before['rule'] != asdict(rule)
                or before['binding'] != binding or before['payout_admission_id'] not in payout_admission_ids):
            raise EvidenceError('PWS_PRECONFIRMATION_PROPOSAL_MISMATCH')
        current = self._assess(**d['request'])
        if canonical(current) != canonical(before):
            raise EvidenceError('PWS_PRECONFIRMATION_CHANGED_RECOMPUTE')
        return dict(current, preconfirmation_id=record_id, preconfirmation_sha256=row['sha256'])
