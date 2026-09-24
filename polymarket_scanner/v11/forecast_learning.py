"""Explicit offline capture-to-challenger job; no runtime trainer or promotion.

Reads an exact, caller-declared cohort from the source archive. The research
journal retains the dataset recipe, original capture/label/example hashes and
all trials. The source archive and every parent object remain unchanged.
"""
from dataclasses import asdict, dataclass
import time

from .datasets import DatasetPlan, build_dataset
from .evidence import EvidenceError, digest, finite, identity
from .forecast_features import ForecastFeatureContract
from .learning_capture import VERSION, labeled_examples
from .model_artifacts import validate_provenance
from .offline_learning import run_research_fit
from .rules import RuleFingerprint


@dataclass(frozen=True)
class ForecastLabelJoin:
    capture_id: str
    label_ids: tuple[tuple[str, str], ...]
    city: str
    horizon: str
    season: str
    prior_exposure: str = 'DEVELOPMENT'

    def __post_init__(self):
        for value in (self.capture_id, self.city, self.horizon, self.season):
            identity(value)
        if (type(self.label_ids) is not tuple or not 1 <= len(self.label_ids) <= 128
                or any(type(row) is not tuple or len(row) != 2 for row in self.label_ids)):
            raise EvidenceError('FORECAST_LABEL_JOIN_SHAPE')
        for market, label in self.label_ids:
            identity(market); identity(label)
        if (len({m for m, _ in self.label_ids}) != len(self.label_ids)
                or len({key for _, key in self.label_ids}) != len(self.label_ids)
                or self.prior_exposure not in {'DEVELOPMENT', 'UNINSPECTED'}):
            raise EvidenceError('FORECAST_LABEL_JOIN_DUPLICATE_OR_EXPOSURE')


def run_forecast_fit(*, source_store, artifacts, journal, plan_record_id, joins,
                     parent_bundle_sha256, envelope, provenance, run_id, as_of,
                     monotonic=time.monotonic):
    """One bounded research request under a previously registered dataset plan.

The 128-capture / 2048-row / 10-second assembly limits are separate from the
existing learner's finite numerical budget. Actual OS isolation is still a gate.
An interrupted fitting attempt is never rerun implicitly under the same run ID.
"""
    identity(run_id, maximum=100)
    if (not journal.store.namespace.startswith('CHALLENGER:')
            or source_store.path.resolve() == journal.store.path.resolve()):
        raise EvidenceError('FORECAST_RESEARCH_SEPARATE_CHALLENGER_JOURNAL_REQUIRED')
    if (type(joins) is not tuple or not 1 <= len(joins) <= 128
            or any(not isinstance(j, ForecastLabelJoin) for j in joins)
            or len({j.capture_id for j in joins}) != len(joins)):
        raise EvidenceError('FORECAST_RESEARCH_COHORT_BOUND_OR_DUPLICATE')
    as_of = finite(as_of)
    if as_of > journal.store.clock():
        raise EvidenceError('FORECAST_RESEARCH_FUTURE_CUTOFF')
    validate_provenance(provenance)
    if (provenance['run_id'] != run_id or provenance['seed'] != envelope.seed
            or provenance['comparison_policy_sha256'] != envelope.sha256
            or provenance['created_at'] < as_of):
        raise EvidenceError('LEARNER_RUN_PROVENANCE_MISMATCH')
    registered = journal.store.get(plan_record_id)
    d = registered['body'].get('details', {})
    if registered['event_id'] != journal.event_id or d.get('action') != 'REGISTER_PLAN':
        raise EvidenceError('REGISTERED_PLAN_REQUIRED')
    plan = DatasetPlan(**d['plan'])
    if plan.sha256 != d['plan_sha256'] or plan.comparison_policy_sha256 != envelope.sha256:
        raise EvidenceError('FROZEN_LEARNER_POLICY_MISMATCH')
    pinned = artifacts.pin(parent_bundle_sha256)
    if plan.feature_schema_sha256 != pinned.payload['bundle']['feature_schema_sha256']:
        raise EvidenceError('FORECAST_RESEARCH_PARENT_SCHEMA_MISMATCH')
    request = dict(version='alpha_v11_forecast_research_request_v1', source_namespace=source_store.namespace,
        plan_record_id=plan_record_id, plan_sha256=plan.sha256, joins=[asdict(j) for j in joins],
        parent_bundle_sha256=parent_bundle_sha256, policy_sha256=envelope.sha256, provenance=provenance, as_of=as_of)
    request_sha = digest(request)
    recipe_id = run_id + ':dataset'
    try:
        saved = journal.store.get(recipe_id)
    except EvidenceError as exc:
        if str(exc) != 'EVIDENCE_MISSING':
            raise
        saved = None
    if saved is not None:
        if saved['body'].get('details', {}).get('request_sha256') != request_sha:
            raise EvidenceError('FORECAST_RESEARCH_REPLAY_CONFLICT')
        try:
            result = journal.store.get(run_id + ':result')['body']['details']
            finished = journal.store.get(run_id + ':finished')['body']['details']
        except EvidenceError as exc:
            if str(exc) != 'EVIDENCE_MISSING':
                raise
            raise EvidenceError('FORECAST_RESEARCH_INTERRUPTED_ATTEMPT_REVIEW_REQUIRED') from None
        if (finished.get('status') != 'NO_PROMOTION' or finished.get('result_sha256') != result.get('sha256')
                or digest(result['result']) != result['sha256']):
            raise EvidenceError('FORECAST_RESEARCH_RESULT_BINDING')
        return {'result': result['result'], 'sha256': result['sha256']}
    deadline = monotonic() + 10.
    examples, captures = [], []
    for join in joins:
        if monotonic() >= deadline:
            raise EvidenceError('FORECAST_RESEARCH_ASSEMBLY_TIME_BOUND')
        capture = source_store.get(join.capture_id)
        data = capture['body'].get('details', {})
        if (capture['kind'] != 'MEASUREMENT' or data.get('version') != VERSION
                or not data.get('parent_feature_contract_verified') or not data.get('complete_event_vector')
                or data.get('feature_schema_sha256') != plan.feature_schema_sha256
                or capture['body']['available_at'] > as_of):
            raise EvidenceError('FORECAST_RESEARCH_VERIFIED_CAPTURE_REQUIRED')
        rule = RuleFingerprint(**data['rule'])
        mapping = data['model_feature_mapping']
        contract = ForecastFeatureContract(tuple((model, len(names)) for model, names in mapping.items()),
                                           rule.payload['unit'], rule.payload['family'])
        contract.require_bundle(pinned)
        if mapping != contract.mapping:
            raise EvidenceError('FORECAST_RESEARCH_MEMBER_MAPPING_MISMATCH')
        # Historical predictions remain bound to their original bundle. A parent
        # with the identical declared schema is compared on those causal inputs.
        batch = labeled_examples(source_store, join.capture_id, label_ids=dict(join.label_ids), city=join.city,
                                  horizon=join.horizon, season=join.season, prior_exposure=join.prior_exposure)
        examples.extend(batch)
        if len(examples) > 2048 or monotonic() >= deadline:
            raise EvidenceError('FORECAST_RESEARCH_ASSEMBLY_BOUND')
        captures.append(dict(id=capture['id'], sha256=capture['sha256'],
                             example_sha256s=[example.sha256 for example in batch]))
    dataset = build_dataset(tuple(examples), plan, as_of=as_of)
    # Compact reproducible recipe, not a duplicate of every private raw source.
    journal.store.audit(recipe_id, event_id=journal.event_id, kind='MODEL_EVENT', details=dict(
        action='FORECAST_DATASET_ASSEMBLED', request=request, request_sha256=request_sha, captures=captures,
        dataset_sha256=dataset['sha256'], causal_watermark=dataset['manifest']['causal_watermark'],
        counts=dataset['manifest']['counts'], source_archive_mutated=False, independent_label_attestation=False,
        financial_authority=False), evidence_ids=(plan_record_id,))
    return run_research_fit(artifacts=artifacts, journal=journal, plan_record_id=plan_record_id, dataset=dataset,
        parent_bundle_sha256=parent_bundle_sha256, envelope=envelope, provenance=provenance, run_id=run_id,
        monotonic=monotonic)
