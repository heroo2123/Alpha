"""Strict data-only, content-addressed model artifacts and compatible bundles.

No pickle, dynamic imports, code paths, order endpoints, risk limits or credentials
can be supplied by an artifact. A validated hash is still not a promotion review.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import stat
import tempfile

from .datasets import FeatureDefinition, FeatureSchema, TARGETS
from .evidence import EvidenceError, canonical, digest, finite, identity, sha


ARTIFACT_VERSION = 'alpha_v11_data_artifact_v1'
BUNDLE_VERSION = 'alpha_v11_compatible_bundle_v1'
RUNTIME_CONTRACT = 'alpha_v11_numeric_inference_v1'
KINDS = {'FEATURES','PROBABILITY','CALIBRATION','EXECUTION_COST','STRATEGY_QUALITY'}
MAX_BYTES = 256*1024


def exact(value, keys, code):
    if not isinstance(value, dict) or set(value) != set(keys):
        raise EvidenceError(code)


def parse_data(raw: bytes, *, max_bytes: int = MAX_BYTES) -> dict:
    if type(max_bytes) is not int or not 0<max_bytes<=1024*1024 or not isinstance(raw, bytes) or len(raw) > max_bytes:
        raise EvidenceError('ARTIFACT_BYTES_BOUND')
    def pairs(rows):
        result = {}
        for key, value in rows:
            if key in result:
                raise EvidenceError('DUPLICATE_ARTIFACT_KEY')
            result[key] = value
        return result
    try:
        value = json.loads(raw, object_pairs_hook=pairs,
                           parse_constant=lambda _: (_ for _ in ()).throw(EvidenceError('NONFINITE_ARTIFACT')))
        canonical(value)
    except (UnicodeDecodeError, ValueError, RecursionError):
        raise EvidenceError('ARTIFACT_JSON_INVALID') from None
    if not isinstance(value, dict):
        raise EvidenceError('ARTIFACT_OBJECT_REQUIRED')
    return value


def validate_provenance(value: dict):
    exact(value, {'code_commit','code_tree','config_sha256','dataset_sha256','causal_watermark',
                  'dependency_sha256','runtime_sha256','seed','created_at','parent_bundle_sha256',
                  'run_id','model_family','model_version','training_metrics_sha256','holdout_metrics_sha256',
                  'comparison_policy_sha256','training_status'}, 'MODEL_PROVENANCE_SCHEMA')
    for key in ('code_commit','code_tree'):
        sha(value[key],40)
    for key in ('config_sha256','dependency_sha256','runtime_sha256','comparison_policy_sha256'):
        sha(value[key])
    for key in ('run_id','model_family','model_version'):
        identity(value[key])
    if type(value['seed']) is not int or not 0 <= value['seed'] <= 2**32-1:
        raise EvidenceError('MODEL_SEED_INVALID')
    created = finite(value['created_at'])
    if value['parent_bundle_sha256'] is not None:
        sha(value['parent_bundle_sha256'])
    if value['training_status']=='INITIAL_NO_FIT':
        if any(value[k] is not None for k in ('dataset_sha256','causal_watermark','training_metrics_sha256','holdout_metrics_sha256')):
            raise EvidenceError('INITIAL_MODEL_CANNOT_CLAIM_TRAINING')
    elif value['training_status']=='FITTED_NOT_CALIBRATED':
        for key in ('dataset_sha256','training_metrics_sha256'):
            sha(value[key])
        if value['holdout_metrics_sha256'] is not None:
            sha(value['holdout_metrics_sha256'])
        if finite(value['causal_watermark']) > created:
            raise EvidenceError('MODEL_PROVENANCE_LOOKAHEAD')
    else:
        raise EvidenceError('MODEL_TRAINING_STATUS_UNSUPPORTED')


def validate_artifact(value: dict) -> dict:
    exact(value, {'version','kind','target','feature_schema_sha256','parameters','provenance'}, 'ARTIFACT_SCHEMA')
    if value['version'] != ARTIFACT_VERSION or value['kind'] not in KINDS or value['target'] not in TARGETS:
        raise EvidenceError('ARTIFACT_VERSION_KIND_OR_TARGET')
    sha(value['feature_schema_sha256'])
    validate_provenance(value['provenance'])
    p, kind = value['parameters'], value['kind']
    if kind=='FEATURES':
        exact(p, {'version','features'}, 'FEATURE_ARTIFACT_SCHEMA')
        if not isinstance(p['features'],list) or not 1 <= len(p['features']) <= 128:
            raise EvidenceError('FEATURE_ARTIFACT_SHAPE')
        try:
            schema=FeatureSchema(p['version'],tuple(FeatureDefinition(**row) for row in p['features']))
        except TypeError:
            raise EvidenceError('FEATURE_ARTIFACT_FIELDS') from None
        if schema.sha256 != value['feature_schema_sha256']:
            raise EvidenceError('FEATURE_SCHEMA_DIGEST_MISMATCH')
    elif kind=='PROBABILITY':
        exact(p, {'family','models','group_weights'}, 'PROBABILITY_ARTIFACT_SCHEMA')
        if p['family']!='GAUSSIAN_MEMBER_MIXTURE' or not isinstance(p['models'],list) or not 1 <= len(p['models']) <= 16:
            raise EvidenceError('PROBABILITY_MODEL_SHAPE')
        names, groups = set(), set()
        for m in p['models']:
            exact(m, {'model_id','dependence_group','bias','kernel_sigma','within_group_weight'}, 'MODEL_PARAMETER_SCHEMA')
            identity(m['model_id'])
            identity(m['dependence_group'])
            if m['model_id'] in names:
                raise EvidenceError('DUPLICATE_MODEL')
            names.add(m['model_id'])
            groups.add(m['dependence_group'])
            if (abs(finite(m['bias'],nonnegative=False))>30 or not .01 <= finite(m['kernel_sigma']) <= 50
                    or not 0 < finite(m['within_group_weight']) <= 1):
                raise EvidenceError('MODEL_PARAMETER_BOUND')
        weights=p['group_weights']
        if not isinstance(weights,dict) or set(weights)!=groups:
            raise EvidenceError('MODEL_DEPENDENCE_GROUPS')
        if any(not 0 < finite(w) <= 1 for w in weights.values()) or not math.isclose(math.fsum(weights.values()),1,abs_tol=1e-12):
            raise EvidenceError('MODEL_GROUP_WEIGHT_BOUND')
    elif kind=='CALIBRATION':
        exact(p, {'method','status','probability_artifact_sha256'}, 'CALIBRATION_ARTIFACT_SCHEMA')
        sha(p['probability_artifact_sha256'])
        if p['method']!='VACUOUS_BOUNDS' or p['status']!='UNCALIBRATED':
            # New calibrated schemas require reviewed code plus actual evidence;
            # an artifact cannot invent its own calibrated flag or loader.
            raise EvidenceError('CALIBRATION_EVIDENCE_NOT_IMPLEMENTED')
    elif kind=='EXECUTION_COST':
        exact(p, {'method','additional_cost_per_share','evidence_class'}, 'EXECUTION_ARTIFACT_SCHEMA')
        if p['method']!='NO_EMPIRICAL_EXECUTION_MODEL' or p['additional_cost_per_share'] is not None or p['evidence_class']!='UNKNOWN':
            raise EvidenceError('EMPIRICAL_EXECUTION_EVIDENCE_REQUIRED')
    elif kind=='STRATEGY_QUALITY':
        exact(p, {'method','modifiers'}, 'QUALITY_ARTIFACT_SCHEMA')
        if p['method']!='FIXED_REDUCTION_ONLY' or not isinstance(p['modifiers'],dict) or len(p['modifiers'])>256:
            raise EvidenceError('QUALITY_ARTIFACT_SHAPE')
        for scope, modifier in p['modifiers'].items():
            sha(scope)
            if not 0 <= finite(modifier) <= 1:
                raise EvidenceError('QUALITY_CANNOT_INCREASE_PROTECTED_SIZE')
    canonical(value)
    return value


@dataclass(frozen=True)
class PinnedBundle:
    canonical_json: str
    sha256: str

    @property
    def payload(self):
        value=json.loads(self.canonical_json)
        if digest(value['bundle'])!=self.sha256:
            raise EvidenceError('PINNED_BUNDLE_INTEGRITY')
        exact(value['components'], KINDS, 'PINNED_COMPONENT_SET')
        for kind, component in value['components'].items():
            validate_artifact(component)
            if (digest(component)!=value['bundle']['artifacts'][kind] or component['kind']!=kind
                    or component['feature_schema_sha256']!=value['bundle']['feature_schema_sha256']
                    or component['target']!=value['bundle']['target']):
                raise EvidenceError('PINNED_COMPONENT_INTEGRITY')
        return value


class ArtifactStore:
    """Bounded research object writer. There is deliberately no active pointer."""
    def __init__(self, root: Path):
        self.root=Path(root)
        if not self.root.is_absolute() or '..' in self.root.parts:
            raise EvidenceError('ARTIFACT_ABSOLUTE_PATH_REQUIRED')
        for p in (self.root,*self.root.parents):
            if p.is_symlink():
                raise EvidenceError('ARTIFACT_SYMLINK_REFUSED')
        if not self.root.is_dir() or self.root.stat().st_mode & 0o077:
            raise EvidenceError('PRIVATE_ARTIFACT_DIRECTORY_REQUIRED')

    def _path(self, object_sha256):
        return self.root/(sha(object_sha256)+'.json')

    def read(self, object_sha256: str) -> dict:
        path=self._path(object_sha256)
        fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW)
        with os.fdopen(fd,'rb') as stream:
            info=os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077 or info.st_size > MAX_BYTES:
                raise EvidenceError('ARTIFACT_FILE_CUSTODY_OR_BOUND')
            raw=stream.read(MAX_BYTES+1)
        value=parse_data(raw)
        if hashlib.sha256(raw).hexdigest()!=object_sha256 or raw!=canonical(value).encode():
            raise EvidenceError('ARTIFACT_HASH_OR_CANONICAL_MISMATCH')
        return value

    def _write(self, value: dict) -> str:
        raw=canonical(value).encode()
        if len(raw)>MAX_BYTES:
            raise EvidenceError('ARTIFACT_BYTES_BOUND')
        key=hashlib.sha256(raw).hexdigest()
        path=self._path(key)
        if path.exists():
            if self.read(key)!=value:
                raise EvidenceError('ARTIFACT_EXISTING_CONFLICT')
            return key
        total, count = 0, 0
        for p in self.root.iterdir():
            count+=1
            if count>10_000:
                raise EvidenceError('ARTIFACT_COUNT_BOUND')
            if p.is_symlink() or not p.is_file():
                raise EvidenceError('FOREIGN_ARTIFACT_DIRECTORY_ENTRY')
            total+=p.stat().st_size
        if total+len(raw)>256*1024**2 or shutil.disk_usage(self.root).free<64*1024**2+len(raw):
            raise EvidenceError('ARTIFACT_DISK_BUDGET')
        fd, temporary=tempfile.mkstemp(prefix='artifact-',suffix='.pending',dir=self.root)
        try:
            with os.fdopen(fd,'wb') as stream:
                stream.write(raw)
                stream.flush()
                os.fchmod(stream.fileno(),0o400)
                os.fsync(stream.fileno())
            try:
                os.link(temporary,path,follow_symlinks=False)
            except FileExistsError:
                if self.read(key)!=value:
                    raise EvidenceError('ARTIFACT_EXISTING_CONFLICT')
            directory=os.open(self.root,os.O_DIRECTORY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        finally:
            Path(temporary).unlink(missing_ok=True)
        return key

    def put_artifact(self, value: dict) -> str:
        validate_artifact(value)
        return self._write(value)

    def put_bundle(self, *, artifacts: dict[str,str], target: str, feature_schema_sha256: str) -> str:
        value={'version':BUNDLE_VERSION,'runtime_contract':RUNTIME_CONTRACT,'target':target,
               'feature_schema_sha256':feature_schema_sha256,'artifacts':artifacts,'financial_authority':False}
        self.validate_bundle(value)
        return self._write(value)

    def validate_bundle(self, value: dict) -> dict:
        exact(value, {'version','runtime_contract','target','feature_schema_sha256','artifacts','financial_authority'}, 'BUNDLE_SCHEMA')
        if (value['version']!=BUNDLE_VERSION or value['runtime_contract']!=RUNTIME_CONTRACT
                or value['target'] not in TARGETS or value['financial_authority'] is not False):
            raise EvidenceError('BUNDLE_COMPATIBILITY_OR_AUTHORITY')
        sha(value['feature_schema_sha256'])
        exact(value['artifacts'], KINDS, 'BUNDLE_ARTIFACT_SET')
        components={}
        for kind, key in value['artifacts'].items():
            item=validate_artifact(self.read(key))
            if item['kind']!=kind or item['target']!=value['target'] or item['feature_schema_sha256']!=value['feature_schema_sha256']:
                raise EvidenceError('BUNDLE_COMPONENT_MISMATCH')
            components[kind]=item
        if components['CALIBRATION']['parameters']['probability_artifact_sha256']!=value['artifacts']['PROBABILITY']:
            raise EvidenceError('CALIBRATION_PROBABILITY_BINDING')
        return components

    def pin(self, bundle_sha256: str) -> PinnedBundle:
        bundle=self.read(bundle_sha256)
        components=self.validate_bundle(bundle)
        value={'bundle':bundle,'components':components}
        return PinnedBundle(canonical(value),bundle_sha256)


def predict_with_bundle(pinned: PinnedBundle, rule, components: tuple, *, as_of: float,
                        max_source_age_seconds: float, observed=None, remaining_coverage=None):
    """Apply only the parameters from one frozen bundle; do not mix caller fits."""
    from dataclasses import replace
    from .probability import FINAL_EXTREME, NEXT_OBSERVATION, predict_buckets
    value=pinned.payload
    target={'FINAL_CONTRACT_PAYOUT':FINAL_EXTREME,'NEXT_OFFICIAL_OBSERVATION':NEXT_OBSERVATION}.get(value['bundle']['target'])
    if target is None:
        raise EvidenceError('BUNDLE_NOT_TEMPERATURE_PREDICTION')
    params=value['components']['PROBABILITY']['parameters']
    models={row['model_id']:row for row in params['models']}
    if len(components)!=len(models) or {c.model_id for c in components}!=set(models):
        raise EvidenceError('BUNDLE_MODEL_INPUT_SET_MISMATCH')
    configured=[]
    for c in components:
        m=models[c.model_id]
        configured.append(replace(c,dependence_group=m['dependence_group'],bias=m['bias'],kernel_sigma=m['kernel_sigma'],
                                  within_group_weight=m['within_group_weight']))
    return predict_buckets(rule,tuple(configured),group_weights=tuple(sorted(params['group_weights'].items())),
                           as_of=as_of,bundle_sha256=pinned.sha256,max_source_age_seconds=max_source_age_seconds,
                           target=target,observed=observed,remaining_coverage=remaining_coverage)
