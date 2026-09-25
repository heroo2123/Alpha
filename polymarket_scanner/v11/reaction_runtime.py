"""Bounded receipt-reaction and inventory evaluation in the PAPER event worker.

These adapters join existing protected engines. They neither collect sources nor
approve models, infer fills, or change account/service authority. Every resulting
proposal still waits for exact queue completion and common-account revalidation.
"""
from base64 import urlsafe_b64encode
from dataclasses import dataclass, replace

from .certification import CapabilityScope
from .evidence import EvidenceError, ReleaseBinding, digest, identity
from .event_risk import EventContext
from .model_registry import ActiveModelRegistry
from .paper_runtime import Evaluation, VERSION as RUNTIME_VERSION, request_adapter_config, check_request_adapter
from .position_management import ExitRequest, PositionManager
from .pws_admission import PWSPreconfirmation
from .pws_lead import LeadPolicy, PWSObservationLead
from .rules import RuleFingerprint
from .runtime_health import admission_heads
from .source_release import SourceRelease, STRATEGIES as RELEASE_STRATEGIES
from .strategy_admission import VERSION as ADMISSION_VERSION
from .strategy_pipeline import EntryRequest, TemperatureStrategies


@dataclass(frozen=True)
class PWSLeadRequest:
    entry: EntryRequest
    observation_admission_id: str
    without_pws_model_ids: tuple[str, ...]
    policy: LeadPolicy

    def __post_init__(self):
        identity(self.observation_admission_id)
        if (not isinstance(self.entry, EntryRequest) or not isinstance(self.policy, LeadPolicy)
                or self.entry.preconfirmation_id is not None or self.entry.source_release_id is not None):
            raise EvidenceError('RUNTIME_PWS_UNJOINED_ENTRY_REQUIRED')
        if (type(self.without_pws_model_ids) is not tuple or not 1 <= len(self.without_pws_model_ids) <= 16
                or len(set(self.without_pws_model_ids)) != len(self.without_pws_model_ids)):
            raise EvidenceError('RUNTIME_PWS_ABLATION_BOUND')
        for key in self.without_pws_model_ids: identity(key)


@dataclass(frozen=True)
class SourceReleaseRequest:
    entry: EntryRequest
    previous_official_id: str
    current_official_id: str
    schedule_id: str | None = None

    def __post_init__(self):
        for key in (self.previous_official_id, self.current_official_id, self.schedule_id):
            if key is not None: identity(key)
        if (not isinstance(self.entry, EntryRequest) or self.entry.source_release_id is not None
                or self.entry.preconfirmation_id is not None):
            raise EvidenceError('RUNTIME_RELEASE_UNJOINED_ENTRY_REQUIRED')


def _admission(store, claim, key):
    row = store.get(key); d = row['body'].get('details', {})
    if row['kind'] != 'REGISTRY' or d.get('version') != ADMISSION_VERSION:
        raise EvidenceError('STRATEGY_ADMISSION_RECORD_REQUIRED')
    original = d['request']; context, scope = original['context'], original['scope']
    if context['event_id'] != claim['event_id']:
        raise EvidenceError('RUNTIME_EVALUATION_EVENT_MISMATCH')
    admission_heads(store, account_id=context['account_id'], event_id=context['event_id'], strategies=(scope['strategy'],))
    return original


def _evaluate(adapter, claim, prefix, request_type, engine, prepare):
    check_request_adapter(adapter)
    requests = adapter.requests_for_event(claim)
    if type(requests) is not tuple or not 1 <= len(requests) <= 6 or any(not isinstance(r, request_type) for r in requests):
        raise EvidenceError('RUNTIME_REACTION_REQUEST_BOUND')
    outputs, proposals = [], []
    for index, request in enumerate(requests):
        # Preserve all SHA-256 bits inside the exit engine's existing 60-char cap.
        key = 'react:'+urlsafe_b64encode(bytes.fromhex(digest([prefix, index]))).decode().rstrip('=')
        try:
            prepared = prepare(claim, key, request)
            row = engine.evaluate(key, prepared)
            if row['event_id'] != claim['event_id']:
                raise EvidenceError('RUNTIME_EVALUATION_EVENT_MISMATCH')
        except (EvidenceError, TimeoutError) as exc:
            row = adapter.store.audit(key, event_id=claim['event_id'], kind='RUNTIME_STATUS', details=dict(
                version=RUNTIME_VERSION, outcome='GATED', strategy_adapter=type(adapter).__name__,
                reason=str(exc) if isinstance(exc, EvidenceError) else 'ADAPTER_TIMEOUT', financial_authority=False))
        outputs.append(row['id'])
        if row['body']['details'].get('proposal') is not None:
            proposals.append(engine.proposal(row['id']))
    return Evaluation(tuple(outputs), tuple(proposals))


class PWSLeadEventAdapter:
    def __init__(self, store, requests_for_event):
        self.store, self.requests_for_event = store, requests_for_event
        self.config = request_adapter_config(type(self).__name__,requests_for_event)

    def _prepare(self, claim, key, request):
        payout = _admission(self.store, claim, request.entry.admission_id)
        observation = _admission(self.store, claim, request.observation_admission_id)
        if (any(p['scope']['strategy'] != 'PWS_OBSERVATION_LEAD' for p in (payout, observation))
                or payout['context'] != observation['context'] or payout['rule'] != observation['rule']):
            raise EvidenceError('RUNTIME_PWS_PAIRED_SCOPE_REQUIRED')
        leases = observation['source_leases']
        sources = {role:tuple(s['evidence_id'] for s in leases if s['role'] == role) for role in ('OFFICIAL', 'PWS', 'MODEL')}
        if len(sources['OFFICIAL']) != 1 or len(sources['PWS']) != 1 or not 1 <= len(sources['MODEL']) <= 16:
            raise EvidenceError('RUNTIME_PWS_EXACT_SOURCE_PAIR_REQUIRED')
        model = ActiveModelRegistry().pin(scope_key=CapabilityScope(**observation['scope']).key,
                    mode='V11_PAPER' if observation['stage'] == 'PAPER' else 'V11_SHADOW')
        # A paired feature ablation uses the same frozen observation model. No
        # separate unreviewed bundle or observation probability becomes payout.
        lead = PWSObservationLead(self.store).observe(key+':lead', rule=RuleFingerprint(**observation['rule']),
            binding=ReleaseBinding(**observation['binding']), policy=request.policy,
            official_id=sources['OFFICIAL'][0], pws_id=sources['PWS'][0], model_ids=sources['MODEL'],
            without_pws_model_ids=request.without_pws_model_ids, bundle=model.bundle, without_pws_bundle=model.bundle)
        # Capture the paired observation target before payout/economic filtering.
        # Dataset failure cannot turn observation probabilities into authority or
        # prevent unrelated economic evaluation and cooperative cancellation.
        capture_status_id=key+':observation-learning-status'
        from .learning_capture import _get
        if _get(self.store,capture_status_id) is None:
            from .target_learning import capture_observation_pair
            try:
                captured=capture_observation_pair(self.store,key+':observation-learning',lead_id=lead['id'],
                    context=EventContext(**observation['context']),bundle=model.bundle,without_pws_bundle=model.bundle)
                status=dict(status='PAIRED_OBSERVATIONS_CAPTURED_LABELS_PENDING',capture_id=captured['id'])
            except EvidenceError as exc:
                status=dict(status='DATASET_CAPTURE_GATED',capture_id=None,reason=str(exc))
            self.store.audit(capture_status_id,event_id=claim['event_id'],kind='MEASUREMENT',evidence_ids=(lead['id'],),
                details=dict(version='alpha_v11_observation_learning_status_v1',**status,financial_authority=False))
        paired = PWSPreconfirmation(self.store).pin(key+':pair', lead_id=lead['id'],
            observation_admission_id=request.observation_admission_id, payout_admission_id=request.entry.admission_id)
        return replace(request.entry, preconfirmation_id=paired['id'])

    def evaluate(self, claim, prefix):
        return _evaluate(self, claim, prefix, PWSLeadRequest, TemperatureStrategies(self.store), self._prepare)


class SourceReleaseEventAdapter:
    def __init__(self, store, requests_for_event):
        self.store, self.requests_for_event = store, requests_for_event
        self.config = request_adapter_config(type(self).__name__,requests_for_event)

    def _prepare(self, claim, key, request):
        original = _admission(self.store, claim, request.entry.admission_id)
        if original['scope']['strategy'] not in RELEASE_STRATEGIES:
            raise EvidenceError('RELEASE_STRATEGY_SCOPE_REQUIRED')
        release = SourceRelease(self.store).pin(key+':release', admission_id=request.entry.admission_id,
            event_state_id=request.entry.event_state_id, previous_official_id=request.previous_official_id,
            current_official_id=request.current_official_id, book_id=request.entry.book_id, schedule_id=request.schedule_id)
        return replace(request.entry, source_release_id=release['id'])

    def evaluate(self, claim, prefix):
        return _evaluate(self, claim, prefix, SourceReleaseRequest, TemperatureStrategies(self.store), self._prepare)


class PositionExitEventAdapter:
    def __init__(self, coordinator, requests_for_event):
        self.coordinator, self.store = coordinator, coordinator.store
        self.requests_for_event = requests_for_event
        self.config = request_adapter_config(type(self).__name__,requests_for_event)

    def _prepare(self, claim, key, request):
        original = _admission(self.store, claim, request.admission_id)
        if original['context']['account_id'] != self.coordinator.policy.account_id:
            raise EvidenceError('RUNTIME_EXIT_ACCOUNT_MISMATCH')
        return request

    def evaluate(self, claim, prefix):
        return _evaluate(self, claim, prefix, ExitRequest, PositionManager(self.coordinator), self._prepare)
