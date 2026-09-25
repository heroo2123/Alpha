"""Bounded archived market/model inputs for the nonfinancial event-risk engine.

Unobserved execution quality and settlement timing remain UNKNOWN. A point REST
book never proves websocket continuity. This is a data adapter, not a reviewed
first-canary execution baseline, calibration, or independently running guardian.
"""
from dataclasses import asdict, dataclass
from decimal import Decimal
import math
from types import SimpleNamespace

from .event_risk import EventMetrics, EventPolicy, EventRiskEngine, _key
from .evidence import EvidenceError, digest, finite, identity
from .microstructure import MakerMicrostructure, MicrostructurePolicy
from .model_artifacts import predict_with_bundle
from .model_registry import ActiveModelRegistry
from .paper_coordinator import ACCOUNT_KEY
from .paper_runtime import Evaluation, VERSION as RUNTIME_VERSION
from .probability import FINAL_EXTREME, UNRESOLVED_EXTREME
from .request_assembly import RequestAssembler, TargetPlan, _single
from .runtime_health import KEY as HEALTH_KEY, admission_heads
from .scenario_risk import number
from .strategy_pipeline import _model_inputs, _condition


VERSION = 'alpha_v11_derived_event_risk_inputs_v1'


@dataclass(frozen=True)
class RiskInputPolicy:
    version: str
    microstructure: MicrostructurePolicy
    event: EventPolicy
    maximum_tokens: int = 32
    maximum_book_skew_seconds: float = 2.

    def __post_init__(self):
        identity(self.version)
        if (not isinstance(self.microstructure,MicrostructurePolicy) or not isinstance(self.event,EventPolicy)
                or type(self.maximum_tokens) is not int or not 1 <= self.maximum_tokens <= 32
                or not 0 <= finite(self.maximum_book_skew_seconds) <= 60):
            raise EvidenceError('RISK_INPUT_POLICY_BOUND')


class EventRiskInputs:
    def __init__(self, assembler, coordinator, policy):
        if (not isinstance(assembler,RequestAssembler) or not isinstance(policy,RiskInputPolicy)
                or assembler.store is not coordinator.store
                or assembler.inputs.context.account_id != coordinator.policy.account_id
                or policy.microstructure.collateral_asset != assembler.valuation_policy.collateral_asset):
            raise EvidenceError('RISK_INPUT_COMPONENT_SCOPE')
        self.assembler, self.coordinator, self.store, self.policy = assembler, coordinator, assembler.store, policy
        self.targets = tuple(TargetPlan(b['market_id'],side,'1','1',())
            for b in assembler.inputs.rule.payload['partition'] for side in ('YES','NO'))
        if len(self.targets) > policy.maximum_tokens: raise EvidenceError('RISK_INPUT_WHOLE_EVENT_TOKEN_BOUND')
        self.config = digest(self.description())

    def description(self):
        return dict(assembler=self.assembler.config,account=self.coordinator.policy_sha,policy=asdict(self.policy),
                    targets=[asdict(t) for t in self.targets])

    def _model(self, claim):
        a = self.assembler; pin, leases = a.pin(claim); now = finite(self.store.clock())
        models = tuple(s.evidence_id for s in leases if s.role == 'MODEL')
        if not models: raise EvidenceError('RISK_MODEL_INPUTS_UNAVAILABLE')
        conditioned = any(s.role == 'FEATURES' for s in leases)
        cutoff = now; request = None
        if conditioned:
            request = SimpleNamespace(observed_input_id=_single(leases,'OFFICIAL'),coverage_input_id=_single(leases,'FEATURES'),
                                      model_input_ids=models)
            cutoff = finite(self.store.get(request.coverage_input_id)['body']['payload'].get('as_of'))
            if cutoff > now: raise EvidenceError('RISK_MODEL_CUTOFF_IN_FUTURE')
        components = _model_inputs(self.store,a.inputs.rule,models,cutoff,
                                   target=UNRESOLVED_EXTREME if conditioned else FINAL_EXTREME)
        observed, coverage = _condition(self.store,a.inputs.rule,request,cutoff,components,available_cutoff=now) if conditioned else (None,None)
        registry = ActiveModelRegistry()
        model = registry.pin(scope_key=a.inputs.scope.key,mode='V11_PAPER' if a.inputs.stage=='PAPER' else 'V11_SHADOW')
        assessment = pin['body']['details']['assessment']
        if model.state_sha256 != assessment['model_state_sha256'] or model.bundle.sha256 != a.inputs.binding.bundle_sha256:
            raise EvidenceError('RISK_MODEL_CHANGED_RECOMPUTE')
        prediction = predict_with_bundle(model.bundle,a.inputs.rule,components,as_of=cutoff,
            max_source_age_seconds=min(s.maximum_age_seconds for s in leases if s.role=='MODEL'),
            observed=observed,remaining_coverage=coverage)
        if not registry.revalidate(model)['passed']: raise EvidenceError('RISK_MODEL_CHANGED_RECOMPUTE')
        # This is the frozen model's descriptive spread, not independent samples
        # or calibrated predictive accuracy. The bundle's dependence groups apply.
        dispersion = math.sqrt(finite(prediction.payload['between_model_mean_variance']))
        age = max(now-finite(self.store.get(key)['body']['issued_at']) for key in models)
        return dispersion, age, prediction.sha256, pin['id']

    def evaluate(self, claim, prefix):
        a = self.assembler; a.check(claim); event = a.inputs.context.event_id
        if digest(self.description()) != self.config: raise EvidenceError('RISK_INPUT_CONFIGURATION_CHANGED')
        key = 'risk-input:'+digest([prefix,self.config]); errors = []; books = []; features = []; refs = []
        request = dict(claim_id=claim['claim_id'],event_id=event,config_sha256=self.config)
        try: prior = self.store.get(key)
        except EvidenceError as exc:
            if str(exc) != 'EVIDENCE_MISSING': raise
        else:
            if prior['body']['details'].get('request') != request: raise EvidenceError('RISK_INPUT_REPLAY_CONFLICT')
            try: return prior, self.store.get(key+':state')
            except EvidenceError as exc:
                if str(exc) != 'EVIDENCE_MISSING': raise
                raise EvidenceError('RISK_PARTIAL_MEASUREMENT_REQUIRES_NEW_CLAIM') from None
        old = self.store.latest(kind='COORDINATOR_EVENT',event_id=_key('event-risk',event))
        heads = []
        for kind,event_key in [('BOOK',event),('MODEL',event),('OFFICIAL_OBSERVATION',event),('PWS_OBSERVATION',event),
                               ('RULE_STATE',event),('COORDINATOR_EVENT',ACCOUNT_KEY),('RUNTIME_STATUS',HEALTH_KEY)]:
            row = self.store.latest(kind=kind,event_id=event_key)
            heads.append((kind,event_key,row['seq'] if row else 0))
        clock = sources_ok = False
        health = self.store.latest(kind='RUNTIME_STATUS',event_id=HEALTH_KEY)
        if health is not None:
            clock = not health['body']['details'].get('clock_reasons', ['UNKNOWN'])
            try:
                admission_heads(self.store,account_id=a.inputs.context.account_id,event_id=event,strategies=(a.inputs.scope.strategy,))
                sources_ok = True
            except EvidenceError as exc:
                errors.append(str(exc))
                if str(exc) != 'RUNTIME_REQUIRED_SOURCE_GATED_OR_CHANGED': clock = False
            refs.append(health['id'])
        else: errors.append('RISK_RUNTIME_HEALTH_REQUIRED')
        try: leases = a.sources(a.inputs)
        except EvidenceError as exc:
            leases = (); sources_ok = False; errors.append(str(exc))
        source_ids = tuple(s.evidence_id for s in leases if s.role != 'FEATURES')
        spread = velocity = depth_loss = motion = None; sequence = False
        now = finite(self.store.clock())
        for index,target in enumerate(self.targets):
            try:
                book = a.book(target); books.append(book['id']); history = (book['id'],)
                previous = self.store.previous_source(book['id'])
                if previous and previous['body']['observed_at'] is not None and now-previous['body']['observed_at'] <= self.policy.microstructure.history_window_seconds:
                    history = (previous['id'],book['id'])
                measured = MakerMicrostructure(self.store).evaluate(key+':book:'+str(index),rule=a.inputs.rule,
                    market_id=target.market_id,side=target.side,book_ids=history,trade_ids=(),policy=self.policy.microstructure)
                refs.append(measured['id']); f = measured['body']['details']
                if f['outcome'] != 'MEASURED_RESEARCH_FEATURES': raise EvidenceError(f['reason'])
                features.append(f)
            except EvidenceError as exc: errors.append(str(exc))
        complete = len(features) == len(self.targets)
        if complete:
            spread = max(float(f['features']['spread']) for f in features)
            stamps = [self.store.get(key)['body']['observed_at'] for key in books]
            aligned = max(stamps)-min(stamps) <= self.policy.maximum_book_skew_seconds
            temporal = aligned and all(f['temporal']['status']=='MEASURED_DECLARED_SEQUENCE' for f in features)
            if temporal:
                sequence = True
                velocity = max(abs(f['temporal']['price_velocity_per_second']) for f in features)
                losses, changes = [], []
                for f in features:
                    t = f['temporal']
                    for side in ('bid','ask'):
                        delta = Decimal(t[side+'_visible_depth_delta']); current = Decimal(f['features'][side+'_depth_units'])
                        previous_depth = current-delta
                        if previous_depth <= 0: raise EvidenceError('RISK_PRIOR_DEPTH_INVALID')
                        losses.append(float(max(Decimal(0),-delta)/previous_depth))
                    previous, latest = (self.store.get(k) for k in t['contiguous_book_ids'][-2:])
                    changes.append(abs(t['price_velocity_per_second']*(latest['body']['observed_at']-previous['body']['observed_at'])))
                depth_loss, motion = max(losses), max(changes)
            else: errors.append('RISK_TEMPORAL_SEQUENCE_OR_ALIGNMENT_UNKNOWN')
        else: errors.append('RISK_WHOLE_EVENT_BOOK_COVERAGE_INCOMPLETE')
        dispersion = model_age = prediction_sha = None
        try:
            dispersion, model_age, prediction_sha, pin_id = self._model(claim); refs.append(pin_id)
        except EvidenceError as exc:
            sources_ok = False; errors.append(str(exc))
        account = self.coordinator._head(); state = self.coordinator._state(account)
        risk = self.coordinator._risk(state)
        utilization = float((number(risk['daily_realized_losses'])+number(risk['active_worst_loss']))/
                            self.coordinator.policy_amount('daily_loss_limit'))
        if account: refs.append(account['id'])
        if risk['faults'] or state['faults']: sources_ok = False; errors.append('RISK_ACCOUNT_FAULT')
        old_sources = old['body']['details']['request']['source_ids'] if old else []
        prior_by_channel = {}
        for source_id in old_sources:
            row = self.store.get(source_id); b = row['body']
            prior_by_channel[row['kind'],b['provider'],b['source_identity']] = row
        new_model = revision = routine = False
        for source_id in source_ids:
            row = self.store.get(source_id); b = row['body']; prior = prior_by_channel.get((row['kind'],b['provider'],b['source_identity']))
            if prior is None or prior['id'] == row['id']: continue
            stamp = 'issued_at' if row['kind']=='MODEL' else 'observed_at'
            left,right = prior['body'].get(stamp),b.get(stamp)
            if left is None or right is None: continue
            new_model |= row['kind']=='MODEL' and right>left
            routine |= row['kind']=='OFFICIAL_OBSERVATION' and right>left
            revision |= row['kind'] in {'MODEL','OFFICIAL_OBSERVATION'} and right==left and prior['body']['payload']!=b['payload']
        metrics = EventMetrics(now,dispersion,model_age,velocity,spread,depth_loss,motion,utilization,
            None,None,None,sources_ok,sequence,clock,new_model_run=new_model,routine_observation=routine,source_revision=revision)
        # Save a coherent source/account cut before publishing a state. Existing
        # queue completion and common-account CAS still reject subsequent races.
        measured = self.store.audit(key,event_id=event,kind='MEASUREMENT',details=dict(
            version=VERSION,config_sha256=self.config,request=request,metrics=asdict(metrics),errors=errors,
            prediction_sha256=prediction_sha,full_book_coverage=complete,
            metric_units=dict(model_disagreement='CONTRACT_TEMPERATURE_UNIT_STANDARD_DEVIATION',
                price_velocity='ABSOLUTE_PROBABILITY_PRICE_PER_SECOND',spread='PROBABILITY_PRICE',
                depth_loss='FRACTION_OF_PREVIOUS_VISIBLE_DEPTH',cross_bucket_motion='MAXIMUM_ABSOLUTE_BUCKET_PRICE_CHANGE',
                loss_utilization='COMMON_ACCOUNT_DAILY_LOSS_PLUS_OPEN_DOWNSIDE_OVER_FIXED_LIMIT'),
            unknown_inputs=['SETTLEMENT_TIMING','OWN_EXECUTION_ADVERSE_FILLS','OWN_EXECUTION_MARKOUT'],
            declared_sequence_is_not_transport_commissioning=True,calibration_status='NOT_ATTESTED',
            settlement_finality=False,financial_authority=False),evidence_ids=tuple(dict.fromkeys(refs)),expected_heads=tuple(heads))
        state = EventRiskEngine(self.store).step(key+':state',context=a.inputs.context,policy=self.policy.event,binding=a.inputs.binding,
            metrics=metrics,book_ids=tuple(books),source_ids=source_ids)
        return measured, state


class RiskAwareEventAdapter:
    def __init__(self, evaluator, inputs):
        if type(inputs) is not tuple or not 1 <= len(inputs) <= 16 or any(not isinstance(i,EventRiskInputs) for i in inputs):
            raise EvidenceError('RISK_RUNTIME_INPUT_BOUND')
        first = inputs[0]; self.store, self.coordinator = first.store, first.coordinator
        if (evaluator.store is not self.store or getattr(evaluator,'coordinator',None) not in (None,self.coordinator)
                or any(i.store is not self.store or i.coordinator is not self.coordinator for i in inputs)):
            raise EvidenceError('RISK_RUNTIME_COMPONENT_SCOPE')
        self.inputs = {i.assembler.inputs.context.event_id:i for i in inputs}
        if len(self.inputs) != len(inputs): raise EvidenceError('RISK_RUNTIME_DUPLICATE_EVENT')
        self.evaluator = evaluator
        self.config = digest(dict(inputs={e:i.config for e,i in sorted(self.inputs.items())},evaluator=evaluator.config))

    def evaluate(self, claim, prefix):
        key = 'risk-runtime:'+digest([prefix,self.config])
        try:
            if claim['event_id'] not in self.inputs: raise EvidenceError('RISK_RUNTIME_EVENT_NOT_CONFIGURED')
            self.inputs[claim['event_id']].evaluate(claim,key)
            return self.evaluator.evaluate(claim,prefix)
        except (EvidenceError,TimeoutError) as exc:
            row = self.store.audit(key,event_id=claim['event_id'],kind='RUNTIME_STATUS',details=dict(
                version=RUNTIME_VERSION,outcome='GATED',reason=str(exc) if isinstance(exc,EvidenceError) else 'RISK_INPUT_TIMEOUT',
                financial_authority=False))
            return Evaluation((row['id'],))
