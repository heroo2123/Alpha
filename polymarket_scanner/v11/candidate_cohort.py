"""Immutable assembly provenance for nonfinancial commissioning.

Only the typed assembler registers runners. The contract is a configuration
identity, not custody or execution authority. Runtime inputs and worker plans
are re-read before use; copying a contract onto an unrelated runner cannot bind it.
"""
from dataclasses import asdict, dataclass, is_dataclass
import json
from weakref import WeakKeyDictionary

from .evidence import EvidenceError, canonical, digest, sha


@dataclass(frozen=True)
class CandidateCohort:
    assembly_sha256: str
    runner_config_sha256: str
    graph_sha256: str
    inputs_json: str
    gefs_events: tuple[str, ...]

    def __post_init__(self):
        for value in (self.assembly_sha256, self.runner_config_sha256, self.graph_sha256):
            sha(value)
        values = json.loads(self.inputs_json)
        if not isinstance(values, list) or not values or canonical(values) != self.inputs_json:
            raise EvidenceError('CANDIDATE_COHORT_INPUTS_INVALID')
        if type(self.gefs_events) is not tuple:
            raise EvidenceError('CANDIDATE_COHORT_GEFS_INVALID')

    @property
    def key(self):
        return digest(asdict(self))


# Process-local assembly provenance, never populated from a caller's plan file.
_ASSEMBLED = WeakKeyDictionary()


def _value(value):
    if is_dataclass(value):
        return asdict(value)
    if isinstance(value, dict):
        return {str(k): _value(v) for k, v in value.items()}
    if isinstance(value, (tuple, list)):
        return [_value(v) for v in value]
    return value


def _snapshot(runner):
    from .candidate_assembly import _EventDispatch
    from .risk_inputs import RiskAwareEventAdapter
    from .request_assembly import (EntryRequestFactory, RelativeValueRequestFactory,
        ExitRequestFactory, SourceReleaseRequestFactory, PWSRequestFactory)
    from .maker_runtime import MakerRequestFactory
    from .strategy_runtime import MultiStrategyEventAdapter
    from .paper_runtime import check_request_adapter

    rt = runner.runtime
    if rt.store is not runner.store or rt.queue.store is not runner.store:
        raise EvidenceError('CANDIDATE_COHORT_GRAPH_CHANGED')
    if type(rt.evaluator) is not RiskAwareEventAdapter or type(rt.evaluator.evaluator) is not _EventDispatch:
        raise EvidenceError('CANDIDATE_TYPED_COHORT_REQUIRED')
    inputs = {}

    def assembler(a):
        if a.queue is not rt.queue or a.store is not runner.store:
            raise EvidenceError('CANDIDATE_COHORT_GRAPH_CHANGED')
        description = a.description()
        if digest(description) != a.config:
            raise EvidenceError('CANDIDATE_COHORT_GRAPH_CHANGED')
        inputs[digest(asdict(a.inputs))] = asdict(a.inputs)
        return description

    risks = {event: dict(assembler=assembler(r.assembler), config=r.config,
                         description=r.description()) for event, r in rt.evaluator.inputs.items()}
    lanes = {}
    for event, multi in rt.evaluator.evaluator.adapters.items():
        if type(multi) is not MultiStrategyEventAdapter:
            raise EvidenceError('CANDIDATE_TYPED_COHORT_REQUIRED')
        lanes[event] = []
        for name, adapter in multi.adapters:
            check_request_adapter(adapter)
            f = adapter.requests_for_event
            if type(f) in (SourceReleaseRequestFactory, PWSRequestFactory):
                base = f.entries
            elif type(f) in (EntryRequestFactory, RelativeValueRequestFactory, ExitRequestFactory, MakerRequestFactory):
                base = f
            else:
                raise EvidenceError('CANDIDATE_TYPED_COHORT_REQUIRED')
            extras = []
            for extra in (getattr(f, 'observation', None), getattr(f, 'payout_inputs', None)):
                if extra is not None:
                    inputs[digest(asdict(extra))] = asdict(extra)
                    extras.append(asdict(extra))
            factory_settings = dict(targets=_value(base.targets))
            if type(f) is PWSRequestFactory:
                factory_settings.update(policy=_value(f.policy), without=_value(f.without))
            if type(f) is RelativeValueRequestFactory:
                factory_settings.update(policy=_value(f.policy), maximum=f.maximum)
            if type(f) is ExitRequestFactory:
                factory_settings.update(hold_costs=_value(f.hold_costs),
                                        sale_costs=_value(f.sale_costs), reason=f.reason)
            if type(f) is MakerRequestFactory:
                factory_settings.update(microstructure=_value(f.microstructure),
                                        research_policy=_value(f.research.policy),
                                        research_policy_sha=f.research.policy_sha)
            lanes[event].append(dict(name=name, adapter=type(adapter).__name__, factory=type(f).__name__,
                config=f.config, assembler=assembler(base.assembler), extras=extras,
                settings=factory_settings))
    workers = {}
    for name in ('census', 'discovery', 'audits', 'observation', 'maker_telemetry',
                 'pws_quality', 'forecasts', 'gefs', 'preparations', 'drift', 'operator_commands'):
        worker = getattr(runner, name)
        if worker is not None:
            if (getattr(worker, 'store', runner.store) is not runner.store
                    or getattr(worker, 'health', rt.health) is not rt.health):
                raise EvidenceError('CANDIDATE_COHORT_GRAPH_CHANGED')
        workers[name] = None if worker is None else dict(type=type(worker).__name__,
            config=getattr(worker, 'config', None),
            **{field: _value(getattr(worker, field)) for field in
               ('plans', 'policy', 'book_policy', 'settings', 'rollover', 'event_ids') if hasattr(worker, field)})
    coordinator = rt.coordinator
    graph = dict(config=runner.config, runtime=rt.config, worker_id=rt.worker_id,
        policy=asdict(runner.policy), kinds=runner.kinds, observation_batch=_value(runner.observation_batch),
        runtime_policy=_value(rt.policy),
        coordinator=dict(policy=_value(coordinator.policy), limits=_value(coordinator.limits),
            correlation=_value(coordinator.correlation), guardian_config=_value(coordinator.guardian_config),
            reconciliation_config=_value(coordinator.reconciliation_config)),
        health=dict(policy=_value(rt.health.policy), account_id=rt.health.account_id,
            scopes=_value(rt.health.scopes), sources=_value(rt.health.sources)),
        feed_policy=_value(rt.feed.policy), audits_policy=_value(rt.audits.policy),
        cancellation_policy=_value(rt.cancellation.policy),
        reconciliation_policy=_value(rt.reconciliation.policy) if rt.reconciliation else None,
        maker_research_policy=_value(rt.maker.policy) if rt.maker else None,
        routes=_value(rt.queue.routes), queue_policy=_value(rt.queue.policy),
        risks=risks, lanes=lanes, workers=workers)
    return digest(graph), canonical([inputs[k] for k in sorted(inputs)])


def register_assembly(runner, assembly_sha256):
    # Called by assemble_candidate only, after its complete typed construction.
    graph, inputs = _snapshot(runner)
    _ASSEMBLED[runner] = CandidateCohort(assembly_sha256, runner.config, graph, inputs,
        tuple(sorted(runner.gefs.plans)) if runner.gefs is not None else ())


def candidate_cohort(runner):
    contract = _ASSEMBLED.get(runner)
    if contract is None:
        raise EvidenceError('CANDIDATE_TYPED_COHORT_REQUIRED')
    graph, inputs = _snapshot(runner)
    if (graph != contract.graph_sha256 or inputs != contract.inputs_json
            or runner.config != contract.runner_config_sha256
            or runner.assembly_sha256 != contract.assembly_sha256):
        raise EvidenceError('CANDIDATE_COHORT_GRAPH_CHANGED')
    return contract
