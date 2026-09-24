"""Evidence-bound forecast sleeves feeding the common paper coordinator.

No transport, wallet or order entry lives here. Source adapters must archive
their inputs first; scoped protected review remains necessary even for paper.
The current uncalibrated bundle cannot produce an accepted settlement entry.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from .certification import CapabilityScope
from .event_risk import EventContext, EventRiskEngine
from .evidence import EvidenceError, EvidenceStore, ReleaseBinding, digest, finite, identity
from .model_artifacts import predict_with_bundle
from .model_registry import ActiveModelRegistry
from .paper_coordinator import Proposal
from .probability import (FINAL_EXTREME, UNRESOLVED_EXTREME, ForecastComponent,
                          ObservedConstraint, RemainingPathCoverage, target_identity)
from .rules import RuleFingerprint
from .scenario_risk import Attribution, number
from .strategy_admission import StrategyAdmission, VERSION as ADMISSION_VERSION
from .valuation import CostComponent, ValuationPolicy, settlement_entry


VERSION = 'alpha_v11_temperature_strategy_pipeline_v1'
INPUT_VERSION = 'alpha_v11_archived_temperature_input_v1'
CONDITION_VERSION = 'alpha_v11_archived_extreme_condition_v1'
COVERAGE_VERSION = 'alpha_v11_archived_remaining_coverage_v1'
SLEEVES = {'FUTURE_FORECAST', 'SAME_DAY_LATE_LOCK'}


@dataclass(frozen=True)
class EntryRequest:
    admission_id: str
    event_state_id: str
    market_id: str
    side: str
    units: str
    desired_total_units: str
    book_id: str
    expires_at: float
    valuation_policy: ValuationPolicy
    costs: tuple[CostComponent, ...]
    model_input_ids: tuple[str, ...]
    observed_input_id: str | None = None
    coverage_input_id: str | None = None

    def __post_init__(self):
        for value in (self.admission_id, self.event_state_id, self.market_id, self.book_id):
            identity(value)
        if self.side not in {'YES', 'NO'} or not 0 < number(self.units) <= number(self.desired_total_units) <= 1_000_000:
            raise EvidenceError('STRATEGY_ENTRY_TARGET_OR_SIZE')
        finite(self.expires_at)
        if (type(self.model_input_ids) is not tuple or not 1 <= len(self.model_input_ids) <= 16
                or len(set(self.model_input_ids)) != len(self.model_input_ids)):
            raise EvidenceError('STRATEGY_MODEL_INPUT_BOUND')
        if type(self.costs) is not tuple or len(self.costs) > 16 or any(not isinstance(c, CostComponent) for c in self.costs):
            raise EvidenceError('STRATEGY_COST_INPUT_BOUND')
        for value in (*self.model_input_ids, self.observed_input_id, self.coverage_input_id):
            if value is not None:
                identity(value)


def _source(store, record_id, *, event_id, kind, cutoff):
    row = store.get(record_id)
    if (row['event_id'] != event_id or row['kind'] != kind
            or row['body']['available_at'] > cutoff
            or row['body'].get('evidence_class') == 'HISTORICAL_AVAILABILITY_UNKNOWN'):
        raise EvidenceError('STRATEGY_INPUT_NOT_CAUSAL_OR_SCOPED')
    return row


def _model_inputs(store, rule, ids, cutoff, *, target):
    result = []
    for key in ids:
        row = _source(store, key, event_id=rule.payload['event_id'], kind='MODEL', cutoff=cutoff)
        body, p = row['body'], row['body']['payload']
        value = p.get('temperature_input', {})
        if (set(value) != {'version', 'model_id', 'target_sha256', 'members'}
                or value['version'] != INPUT_VERSION or not isinstance(value['members'], list)
                or value['target_sha256'] != target_identity(rule, target)
                or p.get('rule_fingerprint') != rule.sha256
                or any(p.get(k) != rule.payload[k] for k in ('station', 'target_date', 'family', 'unit'))):
            raise EvidenceError('ARCHIVED_MODEL_INPUT_SCHEMA_OR_TARGET')
        if body['issued_at'] is None:
            raise EvidenceError('MODEL_RUN_AGE_UNKNOWN')
        # Fitted parameters come exclusively from the protected bundle below.
        result.append(ForecastComponent(value['model_id'], 'BUNDLE_CONFIGURED', value['target_sha256'],
                      tuple(value['members']), 0., 1., 1., row['sha256'], body['received_at'],
                      body['available_at'], body['issued_at']))
    return tuple(result)


def _condition(store, rule, request, cutoff, components, *, available_cutoff):
    if request.observed_input_id is None or request.coverage_input_id is None:
        raise EvidenceError('SAME_DAY_EXACT_CONSTRAINT_AND_COMPLETE_COVERAGE_REQUIRED')
    observation = _source(store, request.observed_input_id, event_id=rule.payload['event_id'],
                          kind='OFFICIAL_OBSERVATION', cutoff=cutoff)
    body, p = observation['body'], observation['body']['payload']
    value = p.get('exact_extreme', {})
    if (set(value) != {'version', 'whole_degree_value', 'source_role'}
            or value['version'] != CONDITION_VERSION
            or value['source_role'] != 'EXACT_CONTRACT_OBSERVATION'
            or p.get('rule_fingerprint') != rule.sha256
            or any(p.get(k) != rule.payload[k] for k in
                   ('station', 'target_date', 'family', 'unit', 'observation_population', 'source_family'))):
        raise EvidenceError('SAME_DAY_EXACT_SOURCE_POPULATION_REQUIRED')
    # These are source-adapter outputs, not a way to certify a proxy by numeric
    # agreement. Admission requires separately reviewed OFFICIAL_EXTREME_POPULATION
    # and REMAINING_EXTREME_MODEL capabilities for this exact strategy scope.
    observed = ObservedConstraint(rule.sha256, p['station'], p['observation_population'], p['unit'], p['family'],
                 value['whole_degree_value'], body['received_at'], body['available_at'], body['revision'],
                 observation['sha256'], value['source_role'])
    row = _source(store, request.coverage_input_id, event_id=rule.payload['event_id'], kind='FEATURES', cutoff=available_cutoff)
    data = row['body']['payload']
    fields = {'version', 'rule_fingerprint', 'as_of', 'accepted_intervals', 'unresolved_intervals',
              'model_evidence_sha256', 'observation_id', 'observation_sha256'}
    if (set(data) != fields or data['version'] != COVERAGE_VERSION or data['rule_fingerprint'] != rule.sha256
            or data['observation_id'] != observation['id'] or data['observation_sha256'] != observation['sha256']
            or any(not isinstance(data[k], list) for k in
                   ('accepted_intervals', 'unresolved_intervals', 'model_evidence_sha256'))):
        raise EvidenceError('SAME_DAY_COVERAGE_SOURCE_BINDING')
    for rows in (data['accepted_intervals'], data['unresolved_intervals']):
        if len(rows) > 1000 or any(not isinstance(pair, list) or len(pair) != 2 for pair in rows):
            raise EvidenceError('SAME_DAY_COVERAGE_INTERVAL_SCHEMA')
    coverage = RemainingPathCoverage(rule.sha256, finite(data['as_of']),
                  tuple(tuple(p) for p in data['accepted_intervals']),
                  tuple(tuple(p) for p in data['unresolved_intervals']),
                  tuple(data['model_evidence_sha256']), row['sha256'])
    observed.validate(rule, cutoff)
    coverage.validate(rule, components, cutoff)
    return observed, coverage


class TemperatureStrategies:
    """Evaluate one pinned thesis; reservation is a separate account operation.

    Stable request IDs resume partial evaluation without replacing an existing
    valuation. A completed result is historical data, never refreshed authority;
    the coordinator independently revalidates any returned proposal.
    """
    def __init__(self, store: EvidenceStore):
        self.store = store

    def evaluate(self, record_id: str, request: EntryRequest) -> dict:
        identity(record_id, maximum=80)
        request_data = asdict(request)
        admission_row = self.store.get(request.admission_id)
        a = admission_row['body'].get('details', {})
        if admission_row['kind'] != 'REGISTRY' or a.get('version') != ADMISSION_VERSION:
            raise EvidenceError('STRATEGY_ADMISSION_RECORD_REQUIRED')
        original = a['request']
        scope = CapabilityScope(**original['scope'])
        if scope.strategy not in SLEEVES:
            raise EvidenceError('TEMPERATURE_STRATEGY_NOT_IMPLEMENTED')
        context = EventContext(**original['context'])
        rule, binding = RuleFingerprint(**original['rule']), ReleaseBinding(**original['binding'])
        request_sha = digest(request_data)
        try:
            prior = self.store.get(record_id)
        except EvidenceError as exc:
            if str(exc) != 'EVIDENCE_MISSING':
                raise
        else:
            if (prior['kind'] != 'MEASUREMENT' or prior['body']['details'].get('version') != VERSION
                    or prior['body']['details']['request_sha256'] != request_sha):
                raise EvidenceError('STRATEGY_REQUEST_ID_COLLISION')
            return prior
        start_id = record_id+':start'
        try:
            start = self.store.get(start_id)
        except EvidenceError as exc:
            if str(exc) != 'EVIDENCE_MISSING':
                raise
            start = self.store.audit(start_id, event_id=context.event_id, kind='MEASUREMENT',
                         details=dict(version=VERSION, request_sha256=request_sha, request=request_data,
                                      stage='EVALUATION_STARTED'), evidence_ids=(request.admission_id,))
        if start['body']['details'].get('request_sha256') != request_sha:
            raise EvidenceError('STRATEGY_REQUEST_ID_COLLISION')
        cutoff = start['body']['recorded_at']
        inference_cutoff = cutoff
        value = prediction = model = assessment = event = None
        reason, outcome, proposal, trace = None, 'GATED', None, []
        references = [request.admission_id, start_id]
        try:
            assessment = StrategyAdmission(self.store).revalidate(request.admission_id, context=context, rule=rule,
                                 binding=asdict(binding), strategies=(scope.strategy,))
            trace.append(dict(stage='SOURCE_READY', state='PASS'))
            tz = ZoneInfo(rule.payload['timezone']); target_day = date.fromisoformat(rule.payload['target_date'])
            day_start = datetime.combine(target_day, time.min, tz).timestamp()
            day_end = datetime.combine(target_day+timedelta(days=1), time.min, tz).timestamp()
            current = finite(self.store.clock())
            if (scope.strategy == 'FUTURE_FORECAST' and not cutoff <= current < day_start
                    or scope.strategy == 'SAME_DAY_LATE_LOCK' and not day_start <= cutoff <= current < day_end):
                raise EvidenceError('STRATEGY_LOCAL_CONTRACT_DAY_MISMATCH')
            if not cutoff <= current < min(request.expires_at, assessment['valid_until']):
                raise EvidenceError('STRATEGY_EVALUATION_EXPIRED')
            leased = {s['evidence_id']:s for s in original['source_leases']}
            if set(request.model_input_ids) != {k for k, s in leased.items() if s['role'] == 'MODEL'}:
                raise EvidenceError('ALL_INFERENCE_MODELS_REQUIRE_ADMISSION_LEASE')
            if scope.strategy == 'FUTURE_FORECAST' and (request.observed_input_id is not None or request.coverage_input_id is not None):
                raise EvidenceError('FUTURE_DAY_CANNOT_USE_OBSERVED_EXTREME')
            if scope.strategy == 'SAME_DAY_LATE_LOCK' and any(
                key not in leased or leased[key]['role'] != role for key, role in
                ((request.observed_input_id, 'OFFICIAL'), (request.coverage_input_id, 'FEATURES'))):
                raise EvidenceError('SAME_DAY_CONDITION_REQUIRES_ADMISSION_LEASE')
            if scope.strategy == 'SAME_DAY_LATE_LOCK':
                coverage_row = _source(self.store, request.coverage_input_id, event_id=context.event_id,
                                       kind='FEATURES', cutoff=cutoff)
                inference_cutoff = finite(coverage_row['body']['payload'].get('as_of'))
                if inference_cutoff > cutoff:
                    raise EvidenceError('COVERAGE_CUTOFF_IN_FUTURE')
            components = _model_inputs(self.store, rule, request.model_input_ids, inference_cutoff,
                                       target=FINAL_EXTREME if scope.strategy == 'FUTURE_FORECAST' else UNRESOLVED_EXTREME)
            observed, coverage = (None, None) if scope.strategy == 'FUTURE_FORECAST' else _condition(
                       self.store, rule, request, inference_cutoff, components, available_cutoff=cutoff)
            mode = 'V11_PAPER' if original['stage'] == 'PAPER' else 'V11_SHADOW'
            model = ActiveModelRegistry().pin(scope_key=scope.key, mode=mode)
            if model.state_sha256 != assessment['model_state_sha256'] or model.bundle.sha256 != binding.bundle_sha256:
                raise EvidenceError('MODEL_CHANGED_DURING_STRATEGY_INFERENCE')
            prediction = predict_with_bundle(model.bundle, rule, components, as_of=inference_cutoff,
                            max_source_age_seconds=min(leased[key]['maximum_age_seconds'] for key in request.model_input_ids),
                            observed=observed, remaining_coverage=coverage)
            refs = (*request.model_input_ids, request.observed_input_id, request.coverage_input_id, request.book_id)
            references.extend(key for key in refs if key is not None)
            value_id = record_id+':valuation'
            try:
                value_row = self.store.get(value_id)
            except EvidenceError as exc:
                if str(exc) != 'EVIDENCE_MISSING':
                    raise
                value_row = settlement_entry(self.store, value_id, rule=rule, prediction=prediction, binding=binding,
                               market_id=request.market_id, side=request.side, units=request.units, book_id=request.book_id,
                               policy=request.valuation_policy, costs=request.costs)
            value = value_row['body']['details']; references.append(value_id)
            if value['model']['prediction_sha256'] != prediction.sha256:
                raise EvidenceError('STRATEGY_PARTIAL_VALUATION_BINDING')
            trace.append(dict(stage='EVALUATED', state=value['outcome']))
            event = EventRiskEngine(self.store).revalidate(request.event_state_id)
            if event['request']['context'] != asdict(context) or event['request']['binding'] != asdict(binding):
                raise EvidenceError('STRATEGY_EVENT_STATE_BINDING')
            references.append(request.event_state_id)
            # Recheck after inference/depth work. The account transaction repeats
            # these checks and guards source/policy heads atomically.
            assessment = StrategyAdmission(self.store).revalidate(request.admission_id, context=context, rule=rule,
                                 binding=asdict(binding), strategies=(scope.strategy,))
            if not ActiveModelRegistry().revalidate(model)['passed']:
                raise EvidenceError('MODEL_CHANGED_DURING_STRATEGY_INFERENCE')
            if not event['ordinary_new_risk_research_allowed']:
                raise EvidenceError('EVENT_STATE_SUPPRESSES_TEMPERATURE_ENTRY')
            outcome, reason = value['outcome'], value['reasons'][0]
            if outcome == 'ACCEPT_RESEARCH':
                thesis = digest(dict(strategy=scope.strategy, event=context.event_id, target=value['target'],
                              bundle=binding.bundle_sha256, inputs=[c.evidence_sha256 for c in components],
                              observation=observed.evidence_sha256 if observed else None))
                proposal = asdict(Proposal(record_id+':proposal', thesis, context, rule, value_id, request.event_state_id,
                                  (Attribution(scope.strategy, '1'),), min(request.expires_at, assessment['valid_until'],
                                  event['valid_until']), request.desired_total_units, (request.admission_id,)))
        except EvidenceError as exc:
            reason, outcome, proposal = str(exc), 'GATED', None
        trace.append(dict(stage='CANDIDATE', state=outcome, reason=reason))
        details = dict(version=VERSION, strategy=scope.strategy, strategy_version=VERSION,
                   request_sha256=request_sha, request=request_data, binding=asdict(binding), scope=asdict(scope),
                   evaluation_started_at=cutoff, inference_cutoff=inference_cutoff, outcome=outcome, reason=reason,
                   prediction=prediction.payload if prediction else None, valuation=value, proposal=proposal,
                   artifact_refs=model.bundle.payload['bundle']['artifacts'] if model else None,
                   model_epoch=model.epoch if model else None, admission_id=request.admission_id,
                   funnel=trace, measurement_target=FINAL_EXTREME, executable_exit_value=None,
                   financial_authority=False, execution_status='NOT_SUBMITTED')
        # Missing input references cannot prevent the durable rejection reason.
        available = []
        for key in dict.fromkeys(references):
            try:
                self.store.get(key)
            except EvidenceError as exc:
                if str(exc) != 'EVIDENCE_MISSING':
                    raise
            else:
                available.append(key)
        return self.store.audit(record_id, event_id=context.event_id, kind='MEASUREMENT', details=details,
                                evidence_ids=tuple(available))

    def proposal(self, evaluation_id: str) -> Proposal:
        row = self.store.get(evaluation_id); details = row['body'].get('details', {})
        if (row['kind'] != 'MEASUREMENT' or details.get('version') != VERSION
                or details.get('outcome') != 'ACCEPT_RESEARCH' or details.get('proposal') is None):
            raise EvidenceError('STRATEGY_HAS_NO_ECONOMIC_PROPOSAL')
        p = dict(details['proposal'])
        p.update(context=EventContext(**p['context']), rule=RuleFingerprint(**p['rule']),
                 attribution=tuple(Attribution(**a) for a in p['attribution']), admission_ids=tuple(p['admission_ids']))
        return Proposal(**p)
