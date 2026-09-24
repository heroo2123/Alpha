"""Causal, immutable learning inputs and explicit temporal research partitions.

Dataset identity never attests settlement truth or grants model promotion. The
source-specific label checker, frozen comparison and reviewed registry are separate.
No credential, exchange or application configuration is imported here.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date
import json

from .evidence import EvidenceError, EvidenceStore, canonical, digest, finite, identity, sha
from .rules import history


TARGETS = {"FINAL_CONTRACT_PAYOUT", "NEXT_OFFICIAL_OBSERVATION", "EXECUTABLE_MARKOUT",
           "REAL_EXECUTION_COST", "VIRTUAL_MAKER_RESEARCH"}
SELECTIONS = {"ALL_SUPPORTED_PREDICTIONS", "SELECTED_TRADES", "REJECTED_COUNTERFACTUALS"}


def validate_target_identity(kind: str, value: dict, *, station: str, decision_at: float):
    if not isinstance(value, dict) or len(value)>8:
        raise EvidenceError('EXACT_LEARNING_TARGET_REQUIRED')
    if kind=='FINAL_CONTRACT_PAYOUT':
        if set(value)!={'market_id','condition_id','token_id','side'} or value['side'] not in {'YES','NO'}:
            raise EvidenceError('EXACT_PAYOUT_TARGET_REQUIRED')
        for v in value.values():
            identity(v)
    elif kind=='NEXT_OFFICIAL_OBSERVATION':
        if (set(value)!={'station','population','window_start','window_end'} or value['station']!=station
                or not decision_at <= finite(value['window_start']) < finite(value['window_end'])):
            raise EvidenceError('EXACT_OBSERVATION_TARGET_REQUIRED')
        identity(value['population'])
    else:
        if set(value)!={'token_id','units','horizon_seconds','measurement_class'}:
            raise EvidenceError('EXACT_EXECUTION_TARGET_REQUIRED')
        identity(value['token_id'])
        if not 0 < finite(value['units']) <= 1_000_000 or not 0 <= finite(value['horizon_seconds']) <= 86400:
            raise EvidenceError('EXECUTION_TARGET_BOUND')
        expected={'REAL_EXECUTION_COST':'RECONCILED_LIVE_EXECUTION','EXECUTABLE_MARKOUT':'DEPTH_COUNTERFACTUAL',
                  'VIRTUAL_MAKER_RESEARCH':'VIRTUAL_MAKER'}[kind]
        if value['measurement_class']!=expected:
            raise EvidenceError('EXECUTION_TARGET_CLASS_MISMATCH')


@dataclass(frozen=True)
class FeatureDefinition:
    name: str
    unit: str
    family: str
    minimum: float
    maximum: float
    missing_allowed: bool

    def __post_init__(self):
        for value in (self.name, self.unit, self.family):
            identity(value)
        if (finite(self.minimum, nonnegative=False) >= finite(self.maximum, nonnegative=False)
                or type(self.missing_allowed) is not bool):
            raise EvidenceError("FEATURE_DEFINITION_INVALID")


@dataclass(frozen=True)
class FeatureSchema:
    version: str
    features: tuple[FeatureDefinition, ...]

    def __post_init__(self):
        identity(self.version)
        if (not isinstance(self.features, tuple) or not 1 <= len(self.features) <= 128
                or any(not isinstance(f, FeatureDefinition) for f in self.features)
                or len({f.name for f in self.features}) != len(self.features)):
            raise EvidenceError("FEATURE_SCHEMA_INVALID")

    @property
    def sha256(self):
        return digest(asdict(self))

    def validate(self, values: dict):
        if not isinstance(values, dict) or set(values) != {f.name for f in self.features}:
            raise EvidenceError("FEATURE_SHAPE_MISMATCH")
        for f in self.features:
            value = values[f.name]
            if value is None:
                if not f.missing_allowed:
                    raise EvidenceError("REQUIRED_FEATURE_MISSING")
            elif not f.minimum <= finite(value, nonnegative=False) <= f.maximum:
                raise EvidenceError("FEATURE_OUT_OF_BOUNDS")


def archive_features(store: EvidenceStore, record_id: str, *, event_id: str, schema: FeatureSchema,
                     values: dict, evidence_ids: tuple[str, ...], source_versions: dict) -> dict:
    schema.validate(values)
    if not evidence_ids or len(evidence_ids) > 64 or len(set(evidence_ids)) != len(evidence_ids):
        raise EvidenceError("FEATURE_EVIDENCE_BOUND")
    if not isinstance(source_versions, dict) or not 1 <= len(source_versions) <= 32:
        raise EvidenceError("SOURCE_VERSION_BOUND")
    for k, v in source_versions.items():
        identity(k)
        identity(v)
    inputs = [store.get(key) for key in evidence_ids]
    ready = finite(store.clock())
    if any(r['kind'] not in {'BOOK', 'TRADE', 'OFFICIAL_OBSERVATION', 'PWS_OBSERVATION', 'MODEL',
                             'RULES', 'STATION_METADATA', 'FEATURES'} or r['event_id'] != event_id
           or r['body']['available_at'] > ready
           or r['body'].get('evidence_class') == 'HISTORICAL_AVAILABILITY_UNKNOWN' for r in inputs):
        raise EvidenceError("NONCAUSAL_FEATURE_INPUT")
    payload = {"feature_schema": asdict(schema), "feature_schema_sha256": schema.sha256,
               "values": values, "feature_ready_at": ready, "source_versions": source_versions,
               "dependencies": [{"id": r['id'], "sha256": r['sha256']} for r in inputs]}
    return store.capture(record_id, event_id=event_id, kind='FEATURES', provider='ALPHA_V11_FEATURES',
                         source_identity=schema.version, revision=record_id, payload=payload,
                         evidence_class='SYNTHETIC' if any(r['body']['evidence_class']=='SYNTHETIC' for r in inputs)
                         else 'PUBLIC_OBSERVED')


@dataclass(frozen=True)
class CausalExample:
    canonical_json: str
    sha256: str

    @property
    def payload(self):
        value = json.loads(self.canonical_json)
        if digest(value) != self.sha256:
            raise EvidenceError("EXAMPLE_INTEGRITY")
        return value


def build_example(store: EvidenceStore, *, decision_id: str, feature_id: str, label_id: str,
                  station: str, city: str, local_date: str, horizon: str, season: str,
                  target: str, selection: str, prior_exposure: str) -> CausalExample:
    for v in (station, city, horizon, season):
        identity(v)
    if date.fromisoformat(local_date).isoformat() != local_date:
        raise EvidenceError("LOCAL_DATE_INVALID")
    if target not in TARGETS or selection not in SELECTIONS or prior_exposure not in {'DEVELOPMENT', 'UNINSPECTED'}:
        raise EvidenceError("LEARNING_TARGET_OR_SELECTION_INVALID")
    decision, feature, label = (store.get(i) for i in (decision_id, feature_id, label_id))
    if decision['kind'] != 'DECISION' or feature['kind'] != 'FEATURES' or label['kind'] != 'LABEL':
        raise EvidenceError("EXAMPLE_RECORD_KINDS")
    if not decision['event_id'] == feature['event_id'] == label['event_id']:
        raise EvidenceError("EXAMPLE_EVENT_MISMATCH")
    d, f, lab = decision['body'], feature['body'], label['body']
    target_context=d.get('explanation',{}).get('target_identity')
    validate_target_identity(target,target_context,station=station,decision_at=d['recorded_at'])
    pinned = {r['id']: r['sha256'] for r in d['evidence']}
    if pinned.get(feature_id) != feature['sha256'] or f['available_at'] > d['feature_ready_at']:
        raise EvidenceError("FEATURE_NOT_PINNED_TO_DECISION")
    fp, lp = f['payload'], lab['payload']
    schema_data = fp.get('feature_schema', {})
    try:
        schema = FeatureSchema(schema_data['version'], tuple(FeatureDefinition(**row) for row in schema_data['features']))
    except (TypeError, KeyError):
        raise EvidenceError("FEATURE_SCHEMA_INVALID") from None
    schema.validate(fp['values'])
    if schema.sha256 != fp['feature_schema_sha256'] or finite(fp['feature_ready_at']) > f['available_at']:
        raise EvidenceError("FEATURE_SCHEMA_OR_TIME_MISMATCH")
    expected_context = {'station': station, 'city': city, 'local_date': local_date,
                        'target': target, 'rule_fingerprint': d['binding']['rule_fingerprint']}
    if lp.get('context') != expected_context:
        raise EvidenceError("LABEL_TARGET_OR_IDENTITY_MISMATCH")
    if lp.get('label_version') != lab['revision'] or lp.get('decision_target') != d['target']:
        raise EvidenceError("LABEL_VERSION_OR_DECISION_TARGET_MISMATCH")
    if lp.get('target_identity')!=target_context:
        raise EvidenceError('LABEL_EXACT_TARGET_MISMATCH')
    if finite(lp.get('knowable_at')) > lab['available_at']:
        raise EvidenceError("LABEL_AVAILABILITY_INVALID")
    if lab['available_at'] < d['recorded_at']:
        raise EvidenceError("OUTCOME_KNOWN_BEFORE_PREDICTION")
    if lp.get('evidence_type') not in {'EXACT_SOURCE_LABEL', 'OBSERVATION_LABEL', 'DEPTH_COUNTERFACTUAL',
                                        'RECONCILED_LIVE_EXECUTION', 'VIRTUAL_MAKER', 'SYNTHETIC'}:
        raise EvidenceError("LABEL_EVIDENCE_TYPE_INVALID")
    if target == 'REAL_EXECUTION_COST' and (selection != 'SELECTED_TRADES'
            or lp['evidence_type'] != 'RECONCILED_LIVE_EXECUTION' or lab['evidence_class'] != 'PUBLIC_OBSERVED'):
        raise EvidenceError("HYPOTHETICAL_IS_NOT_REAL_EXECUTION")
    if target == 'FINAL_CONTRACT_PAYOUT' and lp['evidence_type'] not in {'EXACT_SOURCE_LABEL', 'SYNTHETIC'}:
        raise EvidenceError("PROXY_IS_NOT_SETTLEMENT_LABEL")
    if target == 'NEXT_OFFICIAL_OBSERVATION' and lp['evidence_type'] not in {'OBSERVATION_LABEL', 'SYNTHETIC'}:
        raise EvidenceError("NEXT_OBSERVATION_LABEL_REQUIRED")
    value = lp.get('value')
    finite(value, nonnegative=False)
    if target == 'FINAL_CONTRACT_PAYOUT' and value not in (0, 1):
        raise EvidenceError("BINARY_PAYOUT_LABEL_REQUIRED")

    # Traverse the exact archived derivation DAG. Never substitute a latest source
    # value or follow a label into the feature graph.
    pending, seen, provenance, source_roots = [feature], set(), [], []
    while pending:
        row = pending.pop()
        if row['id'] in seen:
            continue
        if len(seen) >= 256:
            raise EvidenceError("FEATURE_PROVENANCE_BOUND")
        seen.add(row['id'])
        body = row['body']
        if (row['event_id'] != decision['event_id'] or row['kind'] == 'LABEL'
                or body['available_at'] > d['feature_ready_at']
                or body.get('evidence_class') not in {'PUBLIC_OBSERVED', 'SYNTHETIC'}):
            raise EvidenceError("NONCAUSAL_FEATURE_PROVENANCE")
        provenance.append({'id': row['id'], 'sha256': row['sha256'], 'kind': row['kind'],
                           'provider': body['provider'], 'source_identity': body['source_identity'],
                           'revision': body['revision'], 'observed_at': body['observed_at'],
                           'issued_at': body['issued_at'], 'published_at': body['published_at'],
                           'received_at': body['received_at'], 'available_at': body['available_at'],
                           'evidence_class': body['evidence_class']})
        if row['kind'] == 'FEATURES':
            refs = body['payload'].get('dependencies')
            if not isinstance(refs, list) or not 1 <= len(refs) <= 64:
                raise EvidenceError("FEATURE_DERIVATION_MISSING")
            for ref in refs:
                source = store.get(ref['id'])
                if source['sha256'] != ref['sha256'] or source['seq'] >= row['seq']:
                    raise EvidenceError("FEATURE_DERIVATION_BINDING")
                pending.append(source)
        else:
            source_roots.append(row)
    result = {'version': 'alpha_v11_causal_example_v1', 'namespace': store.namespace,
              'event_id': decision['event_id'], 'station': station, 'city': city, 'local_date': local_date,
              'city_day': city+':'+local_date, 'horizon': horizon, 'season': season, 'strategy': d['strategy'],
              'target': target, 'selection': selection, 'prior_exposure': prior_exposure,
              'target_identity': target_context, 'target_identity_sha256': digest(target_context),
              'decision_id': decision_id, 'decision_sha256': decision['sha256'], 'decision_at': d['recorded_at'],
              'feature_ready_at': d['feature_ready_at'], 'feature_id': feature_id, 'feature_sha256': feature['sha256'],
              'feature_schema_sha256': schema.sha256, 'values': fp['values'], 'source_versions': fp['source_versions'],
              'binding': d['binding'], 'provenance': sorted(provenance, key=lambda row: row['id']),
              'label_id': label_id, 'label_sha256': label['sha256'], 'label_version': lab['revision'],
              'label_available_at': max(lab['available_at'], lp['knowable_at']), 'label_value': value,
              'label_evidence_type': lp['evidence_type'], 'label_evidence_class': lab['evidence_class'],
              'label_independently_attested': False, 'financial_authority': False}
    from .learning_sources import source_derivation
    derived=source_derivation(store,source_roots,event_id=decision['event_id'],cutoff=d['feature_ready_at'])
    if derived is not None:
        result['source_derivation']=derived
    return CausalExample(canonical(result), digest(result))


@dataclass(frozen=True)
class DatasetPlan:
    plan_id: str
    target: str
    feature_schema_sha256: str
    train_start: float
    training_cutoff: float
    development_end: float
    confirmation_end: float
    comparison_policy_sha256: str

    def __post_init__(self):
        identity(self.plan_id)
        sha(self.feature_schema_sha256)
        sha(self.comparison_policy_sha256)
        if self.target not in TARGETS or not (finite(self.train_start) < finite(self.training_cutoff)
                < finite(self.development_end) < finite(self.confirmation_end)):
            raise EvidenceError("DATASET_PLAN_INVALID")

    @property
    def sha256(self):
        return digest(asdict(self))


def build_dataset(examples: tuple[CausalExample, ...], plan: DatasetPlan, *, as_of: float) -> dict:
    as_of = finite(as_of)
    if not isinstance(examples, tuple) or not 1 <= len(examples) <= 10_000:
        raise EvidenceError("DATASET_EXAMPLE_BOUND")
    partitions = {'TRAIN': [], 'DEVELOPMENT': [], 'CONFIRMATION': []}
    event_splits, group_splits, decisions = {}, {}, set()
    for example in examples:
        p = example.payload
        if p['decision_id'] in decisions:
            raise EvidenceError("DUPLICATE_DECISION_OR_LABEL_REVISION")
        decisions.add(p['decision_id'])
        if p['feature_schema_sha256'] != plan.feature_schema_sha256 or p['target'] != plan.target:
            raise EvidenceError("DATASET_SCHEMA_OR_TARGET_MISMATCH")
        at = finite(p['decision_at'])
        if not plan.train_start <= at < plan.confirmation_end or p['label_available_at'] > as_of:
            raise EvidenceError("EXAMPLE_OUTSIDE_AVAILABLE_WINDOW")
        part = 'TRAIN' if at < plan.training_cutoff else 'DEVELOPMENT' if at < plan.development_end else 'CONFIRMATION'
        if part == 'TRAIN' and p['label_available_at'] > plan.training_cutoff:
            raise EvidenceError("LABEL_UNAVAILABLE_AT_TRAINING_CUTOFF")
        if part == 'CONFIRMATION' and p['prior_exposure'] != 'UNINSPECTED':
            raise EvidenceError("INSPECTED_DATA_IS_DEVELOPMENT")
        for key, splits in ((p['event_id'], event_splits), (p['city_day'], group_splits)):
            if key in splits and splits[key] != part:
                raise EvidenceError("EVENT_OR_CITY_DAY_SPLIT_LEAKAGE")
            splits[key] = part
        partitions[part].append({'sha256': example.sha256, 'example': p})
    counts, slices = {}, {}
    for part, rows in partitions.items():
        rows.sort(key=lambda r: (r['example']['decision_at'], r['example']['decision_id']))
        counts[part] = {'examples': len(rows), 'events': len({r['example']['event_id'] for r in rows}),
                        'city_days': len({r['example']['city_day'] for r in rows}),
                        'decision_range': [rows[0]['example']['decision_at'], rows[-1]['example']['decision_at']] if rows else None}
        for r in rows:
            p=r['example']
            key=canonical([part,p['station'],p['horizon'],p['season'],p['strategy'],p['selection']])
            slices[key]=slices.get(key,0)+1
    result = {'version': 'alpha_v11_dataset_v1', 'plan': asdict(plan), 'plan_sha256': plan.sha256,
              'as_of': as_of, 'causal_watermark': max(e.payload['label_available_at'] for e in examples),
              'partitions': partitions, 'counts': counts, 'slice_counts': slices,
              'confirmation_status': 'UNINSPECTED_CLAIM_REQUIRES_REVEAL_LEDGER',
              'calibration_status': 'DATASET_NOT_CALIBRATION', 'financial_authority': False}
    return {'manifest': result, 'sha256': digest(result)}


def verify_dataset(dataset: dict) -> dict:
    try:
        manifest = dataset['manifest']
        plan = DatasetPlan(**manifest['plan'])
        examples = tuple(CausalExample(canonical(row['example']), row['sha256'])
                         for rows in manifest['partitions'].values() for row in rows)
        rebuilt = build_dataset(examples, plan, as_of=manifest['as_of'])
    except (KeyError, TypeError, ValueError):
        raise EvidenceError("DATASET_SCHEMA_INVALID") from None
    if rebuilt != dataset:
        raise EvidenceError("DATASET_INTEGRITY")
    return manifest


class ExperimentJournal:
    """All attempts and confirmation reveals append to one research program.

    This is research provenance, not a promotion mechanism. Host separation is
    required to prevent a learner from claiming a different ledger as untouched.
    """
    def __init__(self, store: EvidenceStore, program_id: str):
        self.store = store
        self.event_id = 'learning:'+identity(program_id, maximum=100)

    def _append(self, record_id, details, refs=(), *, expected_previous_seq=None):
        if expected_previous_seq is None:
            past=history(self.store,'MODEL_EVENT',self.event_id)
            expected_previous_seq=past[-1]['seq'] if past else 0
        return self.store.audit(record_id,event_id=self.event_id,kind='MODEL_EVENT',details=details,
                                evidence_ids=refs,expected_previous_seq=expected_previous_seq)

    def register(self, record_id: str, plan: DatasetPlan) -> dict:
        return self._append(record_id,{'action':'REGISTER_PLAN','plan':asdict(plan),'plan_sha256':plan.sha256,
                          'registration_timing':'PROSPECTIVE' if self.store.clock() <= plan.training_cutoff else 'RETROSPECTIVE_DEVELOPMENT_ONLY'})

    def attempt(self, record_id: str, *, plan_record_id: str, candidate_sha256: str,
                parent_champion_sha256: str, hyperparameters: dict) -> dict:
        plan=self.store.get(plan_record_id)
        if plan['event_id']!=self.event_id or plan['body'].get('details',{}).get('action')!='REGISTER_PLAN':
            raise EvidenceError("REGISTERED_PLAN_REQUIRED")
        sha(candidate_sha256)
        sha(parent_champion_sha256)
        encoded=canonical(hyperparameters)
        if len(encoded.encode())>16384:
            raise EvidenceError("HYPERPARAMETER_BOUND")
        return self._append(record_id,{'action':'ATTEMPT_STARTED','plan_record_id':plan_record_id,
                          'candidate_sha256':candidate_sha256,'parent_champion_sha256':parent_champion_sha256,
                          'hyperparameters':hyperparameters,'hyperparameters_sha256':digest(hyperparameters)},(plan_record_id,))

    def reveal_confirmation(self, record_id: str, *, attempt_id: str, dataset: dict) -> dict:
        manifest=verify_dataset(dataset)
        if manifest['as_of'] > self.store.clock():
            raise EvidenceError("DATASET_NOT_YET_AVAILABLE")
        attempt=self.store.get(attempt_id)
        a=attempt['body'].get('details',{})
        if attempt['event_id']!=self.event_id or a.get('action')!='ATTEMPT_STARTED':
            raise EvidenceError("STARTED_ATTEMPT_REQUIRED")
        plan=self.store.get(a['plan_record_id'])
        if manifest['plan_sha256']!=plan['body']['details']['plan_sha256']:
            raise EvidenceError("REGISTERED_DATASET_PLAN_MISMATCH")
        rows=manifest['partitions']['CONFIRMATION']
        if not rows:
            raise EvidenceError("CONFIRMATION_EMPTY")
        groups={r['example']['city_day'] for r in rows}
        past=history(self.store,'MODEL_EVENT',self.event_id)
        used=set()
        for r in past:
            details=r['body']['details']
            if details.get('action')=='CONFIRMATION_REVEALED':
                used.update(details['city_days'])
        repeated=bool(groups & used)
        prospective=plan['body']['details']['registration_timing']=='PROSPECTIVE'
        return self._append(record_id,{'action':'CONFIRMATION_REVEALED','attempt_id':attempt_id,
                          'dataset_sha256':dataset['sha256'],'city_days':sorted(groups),
                          'label_sha256s':sorted({r['example']['label_sha256'] for r in rows}),
                          'evidence_role':'DEVELOPMENT' if repeated or not prospective else 'FIRST_REGISTERED_CONFIRMATION',
                          'reused_confirmation':repeated,'promotion_authorized':False},(attempt_id,),
                            expected_previous_seq=past[-1]['seq'] if past else 0)

    def finish(self, record_id: str, *, attempt_id: str, status: str, reason: str, result_sha256: str | None) -> dict:
        attempt=self.store.get(attempt_id)
        if attempt['event_id']!=self.event_id or attempt['body'].get('details',{}).get('action')!='ATTEMPT_STARTED':
            raise EvidenceError("STARTED_ATTEMPT_REQUIRED")
        if status not in {'FAILED','NO_PROMOTION','PROPOSAL_ONLY'}:
            raise EvidenceError("LEARNER_CANNOT_PROMOTE")
        identity(reason)
        if result_sha256 is not None:
            sha(result_sha256)
        past=history(self.store,'MODEL_EVENT',self.event_id)
        if any(r['body']['details'].get('action')=='ATTEMPT_FINISHED' and r['body']['details'].get('attempt_id')==attempt_id
               for r in past):
            raise EvidenceError("ATTEMPT_ALREADY_FINISHED")
        return self._append(record_id,{'action':'ATTEMPT_FINISHED','attempt_id':attempt_id,'status':status,
                          'reason':reason,'result_sha256':result_sha256,'champion_unchanged':True},(attempt_id,),
                            expected_previous_seq=past[-1]['seq'] if past else 0)
