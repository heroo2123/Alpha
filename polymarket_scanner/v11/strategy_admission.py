"""Scoped research admission joining protected review, rules and model epochs.

An admission pin is a revocable data gate, not a signal or order permission.
Official-source arrivals invalidate pre-confirmation theses before reservation
and again before a research submission-state transition.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass

from .certification import CapabilityScope, StationRegistry
from .event_risk import EventContext
from .evidence import EvidenceError, EvidenceStore, ReleaseBinding, canonical, digest, finite, identity
from .model_registry import ActiveModelRegistry
from .rules import RuleFingerprint, RuleGuard


VERSION = 'alpha_v11_strategy_admission_v1'
FAMILIES = {'daily_high_temperature': 'HIGH', 'daily_low_temperature': 'LOW'}
ROLES = {'OFFICIAL': 'OFFICIAL_OBSERVATION', 'PWS': 'PWS_OBSERVATION', 'MODEL': 'MODEL', 'FEATURES': 'FEATURES'}
NEEDED = {
    'FUTURE_FORECAST': {'MODEL'},
    'SAME_DAY_LATE_LOCK': {'MODEL', 'OFFICIAL'},
    'PWS_OBSERVATION_LEAD': {'MODEL', 'OFFICIAL', 'PWS'},
    'SOURCE_SHOCK': {'MODEL', 'OFFICIAL'},
    'RELEASE_OPPORTUNITY': {'MODEL', 'OFFICIAL'},
    'CROSS_TEMPERATURE': {'MODEL'},
    'CROSS_TEMP_RELATIVE_VALUE': {'MODEL'},
    'STRUCTURAL': set(),
    'RESULT_LAG': {'OFFICIAL'},
    'MAKER_RESEARCH': {'MODEL'},
}


@dataclass(frozen=True)
class SourceLease:
    evidence_id: str
    role: str
    maximum_age_seconds: float

    def __post_init__(self):
        identity(self.evidence_id)
        if self.role not in ROLES or not 0 < finite(self.maximum_age_seconds) <= 86400:
            raise EvidenceError('SOURCE_LEASE_ROLE_OR_AGE')


class StrategyAdmission:
    def __init__(self, store: EvidenceStore):
        self.store = store

    def _assess(self, *, context: EventContext, scope: CapabilityScope, rule: RuleFingerprint,
                binding: ReleaseBinding, stage: str, rule_max_age_seconds: float,
                source_leases: tuple[SourceLease, ...]) -> dict:
        if stage not in {'PAPER', 'SHADOW'}:
            raise EvidenceError('NONFINANCIAL_STRATEGY_STAGE_REQUIRED')
        if not 0 < finite(rule_max_age_seconds) <= 86400:
            raise EvidenceError('STRATEGY_RULE_AGE_BOUND')
        if self.store.namespace != 'V11_PAPER' and stage != 'SHADOW':
            raise EvidenceError('CHALLENGER_ABLATION_REQUIRE_SHADOW_STAGE')
        p = rule.payload
        if (context.event_id != p['event_id'] or context.station_id != p['station']
                or scope.station != p['station'] or scope.family != FAMILIES.get(p['family'])
                or scope.source_rule_family != p['source_family'] or binding.rule_fingerprint != rule.sha256):
            raise EvidenceError('STRATEGY_RULE_SCOPE_MISMATCH')
        if (type(source_leases) is not tuple or len(source_leases) > 16
                or any(not isinstance(s, SourceLease) for s in source_leases)
                or len({s.evidence_id for s in source_leases}) != len(source_leases)
                or not NEEDED[scope.strategy] <= {s.role for s in source_leases}):
            raise EvidenceError('STRATEGY_SOURCE_DEPENDENCIES_MISSING')
        rule_head = self.store.latest(kind='RULE_STATE', event_id=context.event_id)
        station_head = self.store.latest(kind='REGISTRY', event_id='station:'+scope.station)
        if not rule_head or not station_head:
            raise EvidenceError('STRATEGY_RULE_OR_STATION_EVIDENCE_MISSING')
        heads = [('RULE_STATE', context.event_id, rule_head['seq']),
                 ('REGISTRY', 'station:'+scope.station, station_head['seq'])]
        source_kinds = {ROLES[s.role] for s in source_leases} | {'OFFICIAL_OBSERVATION'}
        # Pin heads before reading individual source versions. The final CAS
        # rejects any arrival between this read and publication/reservation.
        source_heads = {kind:self.store.latest(kind=kind,event_id=context.event_id) for kind in source_kinds}
        rules = RuleGuard(self.store).revalidate(context.event_id, rule.sha256, max_age_seconds=rule_max_age_seconds)
        if not rules['passed']:
            raise EvidenceError(rules['reason'])
        certification = StationRegistry(self.store).assess(scope, stage=stage,
                         metadata_fingerprint=p['metadata_fingerprint'], rule_fingerprint=rule.sha256)
        if not certification['eligible']:
            raise EvidenceError(certification['reason'])
        mode = 'V11_PAPER' if stage == 'PAPER' else 'V11_SHADOW'
        model = ActiveModelRegistry().pin(scope_key=scope.key, mode=mode)
        check = ActiveModelRegistry().revalidate(model)
        if not check['passed'] or model.size_multiplier <= 0:
            raise EvidenceError(check['reason'] if not check['passed'] else 'MODEL_SIZE_OVERLAY_ZERO')
        if model.bundle.sha256 != binding.bundle_sha256:
            raise EvidenceError('STRATEGY_MODEL_BUNDLE_CHANGED')
        probability = model.bundle.payload['components']['PROBABILITY']
        if probability['provenance']['model_version'] != scope.model_version:
            raise EvidenceError('STRATEGY_MODEL_VERSION_SCOPE_MISMATCH')
        now = finite(self.store.clock())
        expiry = min(certification['expires_at'], rule_head['body']['recorded_at']+rule_max_age_seconds)
        source_refs = []
        for lease in source_leases:
            source = self.store.get(lease.evidence_id); body = source['body']
            if (source['event_id'] != context.event_id or source['kind'] != ROLES[lease.role]
                    or body.get('evidence_class') == 'HISTORICAL_AVAILABILITY_UNKNOWN'
                    or body['available_at'] > now):
                raise EvidenceError('STRATEGY_SOURCE_NOT_CAUSAL_OR_SCOPED')
            latest = self.store.latest_source(kind=source['kind'], event_id=context.event_id,
                                              provider=body['provider'], source_identity=body['source_identity'])
            if latest['id'] != source['id']:
                raise EvidenceError('NEW_OFFICIAL_EVIDENCE_RECOMPUTE_THESIS' if lease.role == 'OFFICIAL' else
                                    'NEW_PWS_EVIDENCE_REQUIRES_QC_RECOMPUTE' if lease.role == 'PWS' else
                                    'CURRENT_SOURCE_REVISION_REQUIRED_RECOMPUTE')
            observed = body['observed_at']
            if lease.role == 'MODEL':
                observed = body['issued_at']  # Receipt time cannot pretend to be model run time.
                from .gefs_sources import current_path_heads
                heads.extend(current_path_heads(self.store,source))
                if any(body['payload'].get(k) != p[k] for k in ('station', 'target_date', 'family', 'unit')) or body['payload'].get('rule_fingerprint') != rule.sha256:
                    raise EvidenceError('STRATEGY_FORECAST_TARGET_MISMATCH')
            if lease.role == 'OFFICIAL':
                content = body['payload']
                if content.get('settlement_station_context') == scope.station:
                    observations = content.get('observations', [])
                    if not observations or len(observations) > 400 or any(o.get('station') != scope.station for o in observations):
                        raise EvidenceError('STRATEGY_OFFICIAL_STATION_MISMATCH')
                    observed = max(finite(o['observed_at']) for o in observations)
                elif content.get('station') != scope.station:
                    raise EvidenceError('STRATEGY_OFFICIAL_STATION_MISMATCH')
            if lease.role in {'PWS', 'FEATURES'}:
                observed = body['payload'].get('as_of', observed)
            if observed is None or not 0 <= now-finite(observed) <= lease.maximum_age_seconds:
                raise EvidenceError('STRATEGY_SOURCE_STALE_OR_AGE_UNKNOWN')
            expiry = min(expiry, observed+lease.maximum_age_seconds, body['available_at']+lease.maximum_age_seconds)
            if lease.role == 'PWS':
                qc = body['payload']
                from .pws_quality import current_neighborhood_heads
                heads.extend(current_neighborhood_heads(self.store,source))
                if (body['provider'] != 'ALPHA_PWS_QC' or qc.get('health') != 'HEALTHY'
                        or qc.get('station') != scope.station
                        or qc.get('official_metadata_fingerprint') != p['metadata_fingerprint']
                        or not qc.get('observation_age_seconds')
                        or any(finite(age)+now-observed > lease.maximum_age_seconds for age in qc['observation_age_seconds'])):
                    raise EvidenceError('STRATEGY_PWS_FRESH_QC_REQUIRED')
            source_refs.append({'id': source['id'], 'sha256': source['sha256'], 'role': lease.role})
        # Observe the complete kind head, including absent official data for a
        # future-only model. A new official print/revision invalidates the pin.
        for kind in sorted(source_kinds):
            head = source_heads[kind]
            heads.append((kind, context.event_id, head['seq'] if head else 0))
            if kind == 'OFFICIAL_OBSERVATION' and NEEDED[scope.strategy] & {'OFFICIAL'}:
                official_ids = {s.evidence_id for s in source_leases if s.role == 'OFFICIAL'}
                if not head or head['id'] not in official_ids:
                    raise EvidenceError('NEW_OFFICIAL_EVIDENCE_RECOMPUTE_THESIS')
            if kind == 'PWS_OBSERVATION':
                if not head or head['id'] not in {s.evidence_id for s in source_leases if s.role == 'PWS'}:
                    raise EvidenceError('NEW_PWS_EVIDENCE_REQUIRES_QC_RECOMPUTE')
        # Derived paths/QC can share a kind head with their explicit leases.
        # One identical guard is sufficient; differing reads must never be
        # collapsed into whichever revision was observed last.
        distinct = {}
        for kind, event, seq in heads:
            key = (kind,event)
            if key in distinct and distinct[key] != seq:
                raise EvidenceError('STRATEGY_SOURCE_CHANGED_DURING_ASSESSMENT')
            distinct[key] = seq
        heads = [(kind,event,seq) for (kind,event),seq in distinct.items()]
        return dict(certification=certification, rule=rules, source_refs=source_refs, heads=heads,
                    model_epoch=model.epoch, model_state_sha256=model.state_sha256,
                    model_bundle_sha256=model.bundle.sha256, model_size_multiplier=model.size_multiplier,
                    valid_until=expiry, strategy=scope.strategy, financial_authority=False)

    def pin(self, record_id: str, *, context: EventContext, scope: CapabilityScope, rule: RuleFingerprint,
            binding: ReleaseBinding, stage: str, rule_max_age_seconds: float,
            source_leases: tuple[SourceLease, ...]) -> dict:
        request = dict(context=asdict(context), scope=asdict(scope), rule=asdict(rule), binding=asdict(binding),
                       stage=stage, rule_max_age_seconds=finite(rule_max_age_seconds),
                       source_leases=[asdict(s) for s in source_leases])
        result = self._assess(context=context, scope=scope, rule=rule, binding=binding, stage=stage,
                              rule_max_age_seconds=rule_max_age_seconds, source_leases=source_leases)
        return self.store.audit(record_id, event_id='admission:'+scope.key, kind='REGISTRY',
                                details=dict(version=VERSION, request=request, assessment=result),
                                evidence_ids=tuple(s.evidence_id for s in source_leases),
                                expected_heads=tuple(result['heads']))

    def revalidate(self, record_id: str, *, context: EventContext, rule: RuleFingerprint,
                   binding: dict, strategies: tuple[str, ...]) -> dict:
        row = self.store.get(record_id); details = row['body'].get('details', {})
        if row['kind'] != 'REGISTRY' or details.get('version') != VERSION:
            raise EvidenceError('STRATEGY_ADMISSION_PIN_REQUIRED')
        request, before = details['request'], details['assessment']
        scope = CapabilityScope(**request['scope'])
        if (request['context'] != asdict(context) or request['rule'] != asdict(rule)
                or request['binding'] != binding or scope.strategy not in strategies):
            raise EvidenceError('STRATEGY_ADMISSION_PROPOSAL_MISMATCH')
        if finite(self.store.clock()) >= before['valid_until']:
            raise EvidenceError('STRATEGY_ADMISSION_EXPIRED')
        current = self._assess(context=context, scope=scope, rule=rule, binding=ReleaseBinding(**binding),
                               stage=request['stage'], rule_max_age_seconds=request['rule_max_age_seconds'],
                               source_leases=tuple(SourceLease(**s) for s in request['source_leases']))
        for field in ('certification', 'heads', 'model_epoch', 'model_state_sha256', 'model_bundle_sha256', 'model_size_multiplier'):
            if canonical(current[field]) != canonical(before[field]):
                raise EvidenceError('STRATEGY_AUTHORITY_OR_SOURCE_CHANGED_RECOMPUTE')
        return dict(current, admission_id=record_id, admission_sha256=row['sha256'])
