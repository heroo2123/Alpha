"""Bounded nonfinancial commissioning of an exact typed candidate cohort.

Plans bind the assembler's actual configuration plus exact protected model and
feature identities. Preflight reads existing rule, certification and model gates;
it never installs authority. Forward qualification requires original same-ledger
admission, decision lineage and explicit complete grouped payout receipts.
"""
from __future__ import annotations

import ast
from dataclasses import asdict, dataclass
import json
from pathlib import Path
import re

from . import certification, model_registry
from .candidate_runner import CandidateRunner, KEY as CANDIDATE_KEY
from .candidate_cohort import CandidateCohort, candidate_cohort
from .certification import CapabilityScope, StationRegistry
from .evidence import EvidenceError, EvidenceStore, canonical, digest, finite, identity, sha
from .forecast_features import ForecastFeatureContract
from .forward_qualification import VERSION as FORWARD_VERSION, grouped_outcome
from .physical_inference import PhysicalFeatureContract
from ..weather_only_contracts import DAILY_HIGH, DAILY_LOW
from .gefs_sources import MODEL_ID as GEFS_MODEL_ID
from .rules import RuleFingerprint, RuleGuard

VERSION = 'alpha_v11_shadow_commission_v2'
PLAN_VERSION = 'alpha_v11_shadow_commission_plan_v2'
KEY = 'v11-shadow-commission'
SHADOW_MODE = 'V11_SHADOW'
SHADOW_STAGE = 'SHADOW'
MAX_PLAN_BYTES = 256 * 1024
STATUS_PAGE_SIZE = 256
STATUS_MAX_ROWS = 10000
FORWARD_UNAVAILABLE = 'NO_CAUSALLY_QUALIFIED_FORWARD_ADMISSION'
_NAMESPACE_RE = re.compile(r'(?:CHALLENGER|ABLATION):[a-zA-Z0-9_-]{1,64}')
_FORBIDDEN_IMPORT_PREFIXES = ('production', 'polymarket_scanner.production',
                              'http', 'requests', 'socket', 'pickle', 'subprocess',
                              'urllib', 'ftplib', 'smtplib', 'websockets')


def require_shadow_namespace(namespace):
    if not isinstance(namespace, str) or not _NAMESPACE_RE.fullmatch(namespace):
        raise EvidenceError('SHADOW_NAMESPACE_REQUIRED')
    return namespace


@dataclass(frozen=True)
class PinnedFeatureContract:
    """Exact schema/target contract for a separately protected model bundle."""
    unit: str
    family: str
    target: str
    feature_schema_sha256: str
    model_ids: tuple[str, ...]

    def __post_init__(self):
        if (self.unit not in {'C', 'F'} or self.family not in {DAILY_HIGH, DAILY_LOW}
                or self.target not in {'FINAL_CONTRACT_PAYOUT', 'NEXT_OFFICIAL_OBSERVATION'}
                or type(self.model_ids) is not tuple or not 1 <= len(self.model_ids) <= 16
                or len(set(self.model_ids)) != len(self.model_ids)):
            raise EvidenceError('SHADOW_PINNED_FEATURE_CONTRACT_INVALID')
        sha(self.feature_schema_sha256)
        for model_id in self.model_ids:
            identity(model_id)

    def require_bundle(self, pinned):
        from .model_artifacts import PinnedBundle
        if not isinstance(pinned, PinnedBundle):
            raise EvidenceError('SHADOW_PINNED_BUNDLE_REQUIRED')
        value = pinned.payload
        if (value['bundle']['target'] != self.target
                or value['bundle']['feature_schema_sha256'] != self.feature_schema_sha256
                or digest(value['components']['FEATURES']['parameters']) != self.feature_schema_sha256
                or {m['model_id'] for m in value['components']['PROBABILITY']['parameters']['models']} != set(self.model_ids)):
            raise EvidenceError('SHADOW_PINNED_FEATURE_CONTRACT_MISMATCH')
        return value


@dataclass(frozen=True)
class ShadowScopeTarget:
    scope: CapabilityScope
    unit: str
    sample_target: int
    event_id: str
    model_epoch: int
    model_state_sha256: str
    feature_contract: ForecastFeatureContract | PhysicalFeatureContract | PinnedFeatureContract

    def __post_init__(self):
        if not isinstance(self.scope, CapabilityScope):
            raise EvidenceError('SHADOW_TARGET_SCOPE_REQUIRED')
        if self.unit not in ('C', 'F'):
            raise EvidenceError('SHADOW_TARGET_UNIT_INVALID')
        if type(self.sample_target) is not int or not 1 <= self.sample_target <= 500:
            raise EvidenceError('SHADOW_TARGET_SAMPLE_BOUND')
        identity(self.event_id)
        if type(self.model_epoch) is not int or not 1 <= self.model_epoch <= 1000:
            raise EvidenceError('SHADOW_TARGET_MODEL_EPOCH_INVALID')
        sha(self.model_state_sha256)
        if (not isinstance(self.feature_contract, (ForecastFeatureContract, PhysicalFeatureContract, PinnedFeatureContract))
                or self.feature_contract.unit != self.unit):
            raise EvidenceError('SHADOW_TARGET_FEATURE_CONTRACT_REQUIRED')

    @property
    def key(self):
        return self.scope.key, self.event_id


@dataclass(frozen=True)
class ShadowCommissionPlan:
    version: str
    namespace: str
    worker_id: str
    release_git_sha: str
    created_at: float
    description: str
    targets: tuple[ShadowScopeTarget, ...]
    cohort: CandidateCohort

    def __post_init__(self):
        if self.version != PLAN_VERSION:
            raise EvidenceError('SHADOW_PLAN_VERSION_UNSUPPORTED')
        identity(self.worker_id, maximum=160)
        identity(self.description, maximum=500)
        sha(self.release_git_sha, 40)
        finite(self.created_at)
        require_shadow_namespace(self.namespace)
        if (type(self.targets) is not tuple or not 1 <= len(self.targets) <= 64
                or any(not isinstance(t, ShadowScopeTarget) for t in self.targets)):
            raise EvidenceError('SHADOW_PLAN_TARGET_BOUND')
        if len({t.key for t in self.targets}) != len(self.targets):
            raise EvidenceError('SHADOW_PLAN_DUPLICATE_SCOPE_EVENT')
        if not isinstance(self.cohort, CandidateCohort):
            raise EvidenceError('SHADOW_PLAN_COHORT_REQUIRED')
        inputs = json.loads(self.cohort.inputs_json)
        targets = {t.key: t for t in self.targets}
        actual = set()
        for s in inputs:
            scope = CapabilityScope(**s['scope'])
            rule = RuleFingerprint(**s['rule'])
            payload = rule.payload
            key = scope.key, s['context']['event_id']
            actual.add(key)
            t = targets.get(key)
            if (t is None or s['stage'] != SHADOW_STAGE
                    or s['binding']['code_commit'] != self.release_git_sha
                    or s['binding']['rule_fingerprint'] != rule.sha256
                    or payload['event_id'] != key[1]
                    or payload['station'] != scope.station
                    or s['context']['station_id'] != scope.station
                    or payload['source_family'] != scope.source_rule_family
                    or payload['unit'] != t.unit
                    or payload['family'] != t.feature_contract.family
                    or scope.family != ('HIGH' if payload['family'] == 'daily_high_temperature' else 'LOW')):
                raise EvidenceError('SHADOW_PLAN_COHORT_BINDING_MISMATCH')
            if (key[1] in self.cohort.gefs_events and scope.strategy == 'FUTURE_FORECAST'
                    and (not isinstance(t.feature_contract, ForecastFeatureContract)
                         or t.feature_contract.model_widths != ((GEFS_MODEL_ID, 31),))):
                raise EvidenceError('SHADOW_GEFS_FEATURE_CONTRACT_MISMATCH')
        if actual != set(targets):
            raise EvidenceError('SHADOW_PLAN_COHORT_BINDING_MISMATCH')

    @property
    def key(self):
        return digest(asdict(self))


def _parse_plan_json(raw):
    if len(raw) > MAX_PLAN_BYTES:
        raise EvidenceError('SHADOW_PLAN_BYTES_BOUND')
    def pairs(rows):
        out = {}
        for k, v in rows:
            if k in out:
                raise EvidenceError('SHADOW_PLAN_DUPLICATE_KEY')
            out[k] = v
        return out
    try:
        value = json.loads(raw, object_pairs_hook=pairs)
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise EvidenceError('SHADOW_PLAN_JSON_INVALID') from exc
    canonical(value)
    return value


def load_plan(path):
    path = Path(path)
    if not path.is_absolute() or '..' in path.parts:
        raise EvidenceError('SHADOW_PLAN_PATH_INVALID')
    if path.is_symlink():
        raise EvidenceError('SHADOW_PLAN_SYMLINK_REFUSED')
    with path.open('rb') as stream:
        value = _parse_plan_json(stream.read(MAX_PLAN_BYTES + 1))
    try:
        targets = []
        for t in value['targets']:
            t = dict(t)
            t['scope'] = CapabilityScope(**t['scope'])
            fc = dict(t['feature_contract'])
            if 'model_widths' in fc:
                fc['model_widths'] = tuple(tuple(row) for row in fc['model_widths'])
            if 'model_ids' in fc:
                fc['model_ids'] = tuple(fc['model_ids'])
            t['feature_contract'] = (PinnedFeatureContract(**fc) if 'feature_schema_sha256' in fc
                                     else PhysicalFeatureContract(**fc) if 'input_target' in fc
                                     else ForecastFeatureContract(**fc))
            targets.append(ShadowScopeTarget(**t))
        cohort = dict(value['cohort'])
        cohort['gefs_events'] = tuple(cohort['gefs_events'])
        return ShadowCommissionPlan(**{**value, 'targets': tuple(targets), 'cohort': CandidateCohort(**cohort)})
    except (KeyError, TypeError, ValueError) as exc:
        raise EvidenceError('SHADOW_PLAN_SCHEMA') from exc


def _no_financial_or_v10_imports() -> tuple[bool, str]:
    """Static-only guarantee. NOT a live host/systemd check.

    This worker has no root/adm/systemd-journal access to confirm the V10
    production scanner (release `ac3b722`, no V11 imports) is actually
    stopped on any given host; only an owner with host access can confirm
    that. This only proves that *this module's own source* never imports
    order/execution/wallet/production code, mirroring the existing pattern
    in `host_trust/v11-model-authority/authority.py`'s own import check.
    """
    source = Path(__file__).read_text()
    names = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            names.extend(n.name for n in node.names)
        if isinstance(node, ast.ImportFrom) and node.module:
            names.append(node.module)
            if node.level == 0 and node.module == 'polymarket_scanner':
                names.extend(node.module + '.' + name.name for name in node.names)
    bad = sorted({n for n in names if any(n == p or n.startswith(p + '.') for p in _FORBIDDEN_IMPORT_PREFIXES)})
    if bad:
        return False, 'FORBIDDEN_IMPORT:' + ','.join(bad)
    return True, 'NO_FINANCIAL_OR_V10_IMPORT_IN_THIS_MODULE_STATIC_SOURCE_CHECK_ONLY'


def preflight(plan, store, *, current_release_git_sha):
    """Read-only current rule/certification/model eligibility; never provisions state."""
    if not isinstance(plan, ShadowCommissionPlan) or not isinstance(store, EvidenceStore):
        raise EvidenceError('SHADOW_TYPED_PLAN_AND_STORE_REQUIRED')
    if store.namespace != plan.namespace:
        raise EvidenceError('SHADOW_PREFLIGHT_STORE_NAMESPACE_MISMATCH')
    sha(current_release_git_sha, 40)
    checks, identities = [], []
    def record(name, passed, reason):
        checks.append(dict(name=name, passed=bool(passed), reason=reason))
    record('release_identity', current_release_git_sha == plan.release_git_sha, 'RELEASE_GIT_SHA_MISMATCH'
           if current_release_git_sha != plan.release_git_sha else 'RELEASE_MATCHES')
    record('plan_time', plan.created_at <= finite(store.clock()), 'PLAN_INTENT_TIME_NOT_FUTURE')
    ok, reason = _no_financial_or_v10_imports()
    record('static_import_boundary', ok, reason)
    try:
        certification.protected_reviews()
    except EvidenceError as exc:
        raise EvidenceError('SHADOW_PREFLIGHT_STATION_CAPABILITY_REVIEW_UNAVAILABLE') from exc
    targets = {t.key: t for t in plan.targets}
    for s in json.loads(plan.cohort.inputs_json):
        scope = CapabilityScope(**s['scope'])
        rule = RuleFingerprint(**s['rule'])
        target = targets[scope.key, s['context']['event_id']]
        name = target.event_id + ':' + scope.key[:16]
        rule_check = RuleGuard(store).revalidate(target.event_id, rule.sha256,
                                                max_age_seconds=s['rule_max_age_seconds'])
        record(name + ':rule', rule_check['passed'], rule_check['reason'])
        review = StationRegistry(store).assess(scope, stage=SHADOW_STAGE,
            metadata_fingerprint=rule.payload['metadata_fingerprint'], rule_fingerprint=rule.sha256)
        record(name + ':certification', review['eligible'], review['reason'])
        try:
            registry = model_registry.ActiveModelRegistry()
            pin = registry.pin(scope_key=scope.key, mode=SHADOW_MODE)
        except EvidenceError as exc:
            raise EvidenceError('SHADOW_PREFLIGHT_MODEL_AUTHORITY_STATE_UNAVAILABLE') from exc
        check = registry.revalidate(pin)
        record(name + ':model_eligible', check['passed'] and pin.size_multiplier > 0,
               check['reason'] if pin.size_multiplier > 0 else 'MODEL_SIZE_OVERLAY_ZERO')
        matched = (pin.epoch == target.model_epoch and pin.state_sha256 == target.model_state_sha256
            and pin.bundle.sha256 == s['binding']['bundle_sha256']
            and pin.bundle.payload['components']['PROBABILITY']['provenance']['model_version'] == scope.model_version)
        record(name + ':model_identity', matched, 'MODEL_IDENTITY_MATCH' if matched else 'SHADOW_MODEL_IDENTITY_CHANGED')
        try:
            target.feature_contract.require_bundle(pin.bundle)
            record(name + ':features', True, 'FORECAST_FEATURE_CONTRACT_MATCH')
        except EvidenceError as exc:
            record(name + ':features', False, str(exc))
        identities.append(dict(event_id=target.event_id, scope_key=scope.key, binding=s['binding'],
            certification=review, model_epoch=pin.epoch, model_state_sha256=pin.state_sha256,
            model_bundle_sha256=pin.bundle.sha256, feature_contract=asdict(target.feature_contract)))
    return dict(version=VERSION, plan_key=plan.key, cohort_key=plan.cohort.key, namespace=store.namespace,
        passed=all(c['passed'] for c in checks), checks=checks, identities=identities,
        runner_binding_verified=False, source_readiness_or_forward_acceptance=False,
        financial_authority=False, real_orders_sent=False, promotion_authority=False)


@dataclass(frozen=True)
class ShadowLifecyclePolicy:
    version: str
    maximum_iterations: int = 1
    interval_seconds: float = 60.

    def __post_init__(self):
        identity(self.version)
        if type(self.maximum_iterations) is not int or not 1 <= self.maximum_iterations <= 500:
            raise EvidenceError('SHADOW_LIFECYCLE_ITERATION_BOUND')
        if not 0 < finite(self.interval_seconds) <= 3600:
            raise EvidenceError('SHADOW_LIFECYCLE_INTERVAL_BOUND')


class ShadowCommissionRunner:
    def __init__(self, plan, runner, *, release_git_sha, lifecycle):
        if not isinstance(plan, ShadowCommissionPlan) or type(runner) is not CandidateRunner:
            raise EvidenceError('SHADOW_RUNNER_TYPED_COMPONENTS_REQUIRED')
        if not isinstance(lifecycle, ShadowLifecyclePolicy):
            raise EvidenceError('SHADOW_RUNNER_LIFECYCLE_POLICY_REQUIRED')
        if runner.store.namespace != plan.namespace:
            raise EvidenceError('SHADOW_RUNNER_NAMESPACE_MISMATCH')
        if runner.runtime.worker_id != plan.worker_id:
            raise EvidenceError('SHADOW_RUNNER_WORKER_IDENTITY_MISMATCH')
        if candidate_cohort(runner) != plan.cohort:
            raise EvidenceError('SHADOW_RUNNER_COHORT_MISMATCH')
        self.plan, self.runner, self.store, self.lifecycle = plan, runner, runner.store, lifecycle
        self.release_git_sha = sha(release_git_sha, 40)
        self.config = digest(dict(version=VERSION, plan_key=plan.key, runner_config=runner.config,
            release_git_sha=self.release_git_sha, lifecycle=asdict(lifecycle)))

    def preflight(self):
        if (self.runner.store is not self.store or self.store.namespace != self.plan.namespace
                or self.runner.runtime.worker_id != self.plan.worker_id
                or candidate_cohort(self.runner) != self.plan.cohort):
            raise EvidenceError('SHADOW_RUNNER_COHORT_MISMATCH')
        report = preflight(self.plan, self.store, current_release_git_sha=self.release_git_sha)
        return dict(report, runner_binding_verified=True)

    def _save(self, key, **details):
        expected = dict(
            version=VERSION, plan_key=self.plan.key, config_sha256=self.config,
            cohort_key=self.plan.cohort.key, runner_config_sha256=self.runner.config,
            release_git_sha=self.release_git_sha, financial_authority=False, real_orders_sent=False,
            deployment_acceptance=False, promotion_authority=False, forward_or_live_acceptance=False,
            **details)
        try:
            existing = self.store.get(key)
        except EvidenceError as exc:
            if str(exc) != 'EVIDENCE_MISSING':
                raise
        else:
            if (existing['kind'] != 'RUNTIME_STATUS' or existing['event_id'] != KEY
                    or canonical(existing['body'].get('details')) != canonical(expected)):
                raise EvidenceError('RECORD_ID_CONFLICT')
            return existing
        head = self.store.latest(kind='RUNTIME_STATUS', event_id=KEY)
        return self.store.audit(key, event_id=KEY, kind='RUNTIME_STATUS', details=expected,
                                expected_previous_seq=head['seq'] if head else 0)

    def _freeze(self, report):
        key = 'shadow-freeze:' + self.config
        try:
            row = self.store.get(key)
        except EvidenceError as exc:
            if str(exc) != 'EVIDENCE_MISSING':
                raise
            row = self._save(key, outcome='PLAN_FROZEN', plan=asdict(self.plan),
                             preflight=report, freeze_is_forward_acceptance=False)
        if canonical(row['body']['details'].get('preflight')) != canonical(report):
            raise EvidenceError('SHADOW_FROZEN_AUTHORITY_CHANGED_REVIEW_REQUIRED')
        return self._save(key, outcome='PLAN_FROZEN', plan=asdict(self.plan),
                          preflight=report, freeze_is_forward_acceptance=False)

    async def run_once(self, run_id):
        identity(run_id, maximum=80)
        try:
            report = self.preflight()
        except EvidenceError as exc:
            report = dict(passed=False, reason=str(exc))
        if not report['passed']:
            self._save('shadow-refused:' + digest([self.config, run_id, report]),
                       outcome='PREFLIGHT_FAILED', preflight=report, run_id=run_id)
            raise EvidenceError('SHADOW_COMMISSION_PREFLIGHT_FAILED')
        try:
            freeze = self._freeze(report)
        except EvidenceError as exc:
            self._save('shadow-freeze-refused:' + digest([self.config, run_id, str(exc)]),
                outcome='PREFLIGHT_FAILED', preflight=report, reason=str(exc), run_id=run_id)
            raise
        candidate_run = 'shadow:' + digest([self.config, run_id, freeze['sha256']])
        start_id = 'shadow-start:' + digest([self.config, run_id])
        try:
            start = self.store.get(start_id)
        except EvidenceError as exc:
            if str(exc) != 'EVIDENCE_MISSING':
                raise
            start = self._save(start_id, outcome='RUN_STARTED', run_id=run_id,
                freeze_id=freeze['id'], freeze_sha256=freeze['sha256'], candidate_run_key=candidate_run)
        result = await self.runner.run(candidate_run)
        self._save('shadow-result:' + digest([self.config, run_id]), outcome='RUN_RECORDED',
            run_id=run_id, preflight=report, freeze_id=freeze['id'], freeze_sha256=freeze['sha256'],
            start_id=start['id'], start_sha256=start['sha256'], candidate_run_id=result['id'],
            candidate_run_sha256=result['sha256'], candidate_outcome=result['body']['details']['outcome'])
        return result

    async def run_bounded(self, run_id_prefix):
        identity(run_id_prefix, maximum=60)
        import asyncio
        results = []
        for i in range(self.lifecycle.maximum_iterations):
            results.append(await self.run_once(run_id_prefix + ':iteration:' + str(i)))
            if i < self.lifecycle.maximum_iterations - 1:
                await asyncio.sleep(self.lifecycle.interval_seconds)
        return results


def _history(store, *, kind, event_id, maximum_rows=STATUS_MAX_ROWS):
    """Fixed frontier, bounded pages. A bound never silently becomes a total."""
    head = store.latest(kind=kind, event_id=event_id)
    frontier = head['seq'] if head else 0
    rows, cursor = [], 0
    while cursor < frontier and len(rows) < maximum_rows:
        page = store.records(kind=kind, event_id=event_id, after_seq=cursor,
                             limit=min(STATUS_PAGE_SIZE, maximum_rows - len(rows)))
        page = [r for r in page if r['seq'] <= frontier]
        if not page:
            break
        rows.extend(page)
        cursor = page[-1]['seq']
    return rows, dict(complete=cursor == frontier, frontier_seq=frontier,
                     scanned_through_seq=cursor, rows_scanned=len(rows), maximum_rows=maximum_rows)


def _head_at(store, *, kind, event_id, through_seq):
    """Find a guarded head at a historical capture frontier with a hard scan bound."""
    cursor = count = head = 0
    while True:
        page = store.records(kind=kind, event_id=event_id, after_seq=cursor, limit=STATUS_PAGE_SIZE)
        if not page:
            return head
        for row in page:
            if row['seq'] > through_seq:
                return head
            count += 1
            if count > STATUS_MAX_ROWS:
                raise EvidenceError('FORWARD_GUARD_HISTORY_INCOMPLETE')
            head = row['seq']
        cursor = page[-1]['seq']


def _linked_outcome(plan, store, row):
    d = row['body']['details']
    try:
        freeze, start, result = (store.get(d[k]) for k in ('freeze_id', 'start_id', 'candidate_run_id'))
        f, s, c = (r['body']['details'] for r in (freeze, start, result))
        for other in (f, s):
            if any(other.get(k) != d.get(k) for k in
                   ('version', 'plan_key', 'config_sha256', 'cohort_key', 'runner_config_sha256', 'release_git_sha')):
                return None
        if (freeze['sha256'] != d['freeze_sha256'] or start['sha256'] != d['start_sha256']
                or result['sha256'] != d['candidate_run_sha256']
                or f['outcome'] != 'PLAN_FROZEN' or digest(f['plan']) != plan.key
                or f['preflight']['passed'] is not True or f['preflight'].get('runner_binding_verified') is not True
                or d['preflight'] != f['preflight']
                or s['outcome'] != 'RUN_STARTED' or s['run_id'] != d['run_id']
                or s['freeze_id'] != freeze['id'] or s['freeze_sha256'] != freeze['sha256']
                or result['id'] != 'candidate-run:' + digest(s['candidate_run_key'])
                or result['event_id'] != CANDIDATE_KEY or result['kind'] != 'RUNTIME_STATUS'
                or c['config_sha256'] != plan.cohort.runner_config_sha256
                or not freeze['seq'] < start['seq'] < result['seq'] < row['seq']
                or c['outcome'] != d['candidate_outcome']
                or c.get('all_async_jobs_drained') is not True):
            return None
        return c['outcome'] if c['outcome'] in ('BOUNDED_RUN_FINISHED', 'DEGRADED') else None
    except (EvidenceError, KeyError, TypeError):
        return None


def _qualified_admission(plan, store, target, capture_id, label_ids):
    """Recheck every original reference; a current protected pin cannot backfill one."""
    group = grouped_outcome(store, capture_id, label_ids)
    capture = store.get(capture_id)
    cd = capture['body']['details']
    ref = group['admission_ref']
    if not isinstance(ref, dict) or set(ref) != {'id', 'sha256'}:
        raise EvidenceError('FORWARD_ORIGINAL_ADMISSION_REF_REQUIRED')
    admission = store.get(ref['id'])
    ad = admission['body'].get('details', {})
    request, assessment = ad.get('request', {}), ad.get('assessment', {})
    matching = [s for s in json.loads(plan.cohort.inputs_json)
                if s['context']['event_id'] == target.event_id
                and CapabilityScope(**s['scope']).key == target.scope.key]
    if len(matching) != 1:
        raise EvidenceError('FORWARD_PLAN_TARGET_MISMATCH')
    cohort = matching[0]
    pinned_heads = assessment.get('heads')
    if (not isinstance(pinned_heads, list) or not 3 <= len(pinned_heads) <= 64
            or any(not isinstance(h, list) or len(h) != 3 or not isinstance(h[0], str)
                   or not isinstance(h[1], str) or type(h[2]) is not int or h[2] < 0
                   for h in pinned_heads)
            or len({(h[0], h[1]) for h in pinned_heads}) != len(pinned_heads)):
        raise EvidenceError('FORWARD_ADMISSION_GUARDS_INVALID')
    guarded = {(h[0], h[1]): h[2] for h in pinned_heads}
    if not {('RULE_STATE', target.event_id), ('REGISTRY', 'station:' + target.scope.station),
            ('OFFICIAL_OBSERVATION', target.event_id), ('MODEL', target.event_id)} <= guarded.keys():
        raise EvidenceError('FORWARD_ADMISSION_GUARDS_INVALID')
    for (kind, event), expected in guarded.items():
        if (_head_at(store, kind=kind, event_id=event, through_seq=admission['seq']) != expected
                or _head_at(store, kind=kind, event_id=event, through_seq=capture['seq']) != expected):
            raise EvidenceError('FORWARD_ADMISSION_GUARD_CHANGED')
    decisions = [store.get(child['decision_id']) for child in cd['rows']]
    model_leases = {lease['evidence_id'] for lease in request.get('source_leases', [])
                    if lease.get('role') == 'MODEL'}
    assessed_models = {ref['id'] for ref in assessment.get('source_refs', [])
                       if ref.get('role') == 'MODEL'}
    source_refs = assessment.get('source_refs', [])
    assessed_hashes_match = all(store.get(source['id'])['sha256'] == source['sha256']
                                for source in source_refs)
    leases_match = {(lease['evidence_id'], lease['role']) for lease in request.get('source_leases', [])} == {
        (source['id'], source['role']) for source in source_refs}
    if (admission['kind'] != 'REGISTRY' or admission['event_id'] != 'admission:' + target.scope.key
            or admission['sha256'] != ref['sha256']
            or ad.get('version') != 'alpha_v11_strategy_admission_v1'
            or request.get('context') != cd['context']
            or request.get('scope') != asdict(target.scope)
            or request.get('rule') != cd['rule'] or request['rule'] != cohort['rule']
            or request.get('binding') != cd['binding'] or request['binding'] != cohort['binding']
            or request.get('stage') != SHADOW_STAGE
            or cd['binding']['code_commit'] != plan.release_git_sha
            or cd['binding']['bundle_sha256'] != assessment.get('model_bundle_sha256')
            or cd['feature_schema_sha256'] != target.feature_contract.schema.sha256
            or assessment.get('model_epoch') != target.model_epoch
            or assessment.get('model_state_sha256') != target.model_state_sha256
            or assessment.get('certification', {}).get('eligible') is not True
            or assessment.get('rule', {}).get('passed') is not True
            or assessment.get('financial_authority') is not False
            or assessment.get('valid_until', 0) <= capture['body']['recorded_at']
            or not decisions or admission['seq'] >= min(x['seq'] for x in decisions)
            or assessment.get('valid_until', 0) <= max(x['body']['recorded_at'] for x in decisions)
            or not assessed_hashes_match or not leases_match or model_leases != assessed_models
            or model_leases != set(group['model_source_ids'])
            or admission['body'].get('financial_authority') is not False
            or not plan.created_at <= admission['body']['recorded_at'] <= capture['body']['recorded_at']
            or not admission['seq'] < capture['seq']
            or capture['event_id'] != target.event_id):
        raise EvidenceError('FORWARD_ADMISSION_PLAN_LINEAGE_MISMATCH')
    _require_protected_interval(admission, capture, decisions)
    return dict(group, admission_id=admission['id'], admission_sha256=admission['sha256'],
                plan_key=plan.key, scope_key=target.scope.key)


def _require_protected_interval(admission, capture, decisions):
    """Refuse credit until independently custodied interval evidence exists.

    Current model state records monotonic model events, but certification is a
    replaceable manifest. Neither protected reader is fenced by the SQLite
    publication transaction. An admission snapshot or a final current-state
    read cannot prove validity at every child append and durable capture.
    No untrusted ledger field is accepted as a substitute for that proof.
    """
    raise EvidenceError('FORWARD_PROTECTED_INTERVAL_UNPROVEN')


def record_forward_admission(plan, store, *, capture_id, label_ids):
    """Persist a reviewed data qualification in the original nonfinancial ledger."""
    if (not isinstance(plan, ShadowCommissionPlan) or not isinstance(store, EvidenceStore)
            or store.namespace != plan.namespace):
        raise EvidenceError('SHADOW_TYPED_PLAN_AND_STORE_REQUIRED')
    capture = store.get(capture_id)
    ref = capture['body'].get('details', {}).get('admission_ref')
    if not isinstance(ref, dict) or not isinstance(ref.get('id'), str):
        raise EvidenceError('FORWARD_ORIGINAL_ADMISSION_REF_REQUIRED')
    admission = store.get(ref['id'])
    targets = [t for t in plan.targets if t.event_id == capture['event_id']
               and admission['event_id'] == 'admission:' + t.scope.key]
    if len(targets) != 1:
        raise EvidenceError('FORWARD_PLAN_TARGET_MISMATCH')
    target = targets[0]
    qualified = _qualified_admission(plan, store, target, capture_id, label_ids)
    key = 'forward-qualification:' + digest([plan.key, capture_id])
    existing = None
    try:
        existing = store.get(key)
    except EvidenceError as exc:
        if str(exc) != 'EVIDENCE_MISSING':
            raise
    if existing is not None:
        if existing['body'].get('details', {}).get('qualification') != qualified:
            raise EvidenceError('FORWARD_QUALIFICATION_CONFLICT')
        return existing
    prior, coverage = _history(store, kind='REGISTRY', event_id='admission:' + target.scope.key)
    if not coverage['complete']:
        raise EvidenceError('FORWARD_ADMISSION_HISTORY_INCOMPLETE')
    for row in prior:
        details = row['body'].get('details', {})
        q = details.get('qualification', {}) if isinstance(details, dict) else {}
        if (isinstance(q, dict) and details.get('version') == FORWARD_VERSION
                and q.get('plan_key') == plan.key
                and (q.get('admission_id') == qualified['admission_id']
                     or q.get('grouped_outcome_id') == qualified['grouped_outcome_id'])):
            raise EvidenceError('FORWARD_DUPLICATE_ADMISSION_OR_OUTCOME')
    return store.audit(key, event_id='admission:' + target.scope.key, kind='REGISTRY',
                       details=dict(version=FORWARD_VERSION, qualification=qualified,
                                    label_ids=label_ids, financial_authority=False,
                                    forward_acceptance_pending_independent_review=True),
                       expected_previous_seq=coverage['frontier_seq'])


def evidence_status(plan, store, *, config_sha256=None, maximum_rows=STATUS_MAX_ROWS):
    if not isinstance(plan, ShadowCommissionPlan) or not isinstance(store, EvidenceStore):
        raise EvidenceError('SHADOW_TYPED_PLAN_AND_STORE_REQUIRED')
    if store.namespace != plan.namespace:
        raise EvidenceError('SHADOW_STATUS_NAMESPACE_MISMATCH')
    if type(maximum_rows) is not int or not 1 <= maximum_rows <= STATUS_MAX_ROWS:
        raise EvidenceError('SHADOW_STATUS_SCAN_BOUND')
    if config_sha256 is not None:
        sha(config_sha256)
    rows, coverage = _history(store, kind='RUNTIME_STATUS', event_id=KEY, maximum_rows=maximum_rows)
    selected = [r for r in rows if (d := r['body']['details']).get('version') == VERSION
        and d.get('plan_key') == plan.key and d.get('cohort_key') == plan.cohort.key
        and d.get('runner_config_sha256') == plan.cohort.runner_config_sha256
        and (config_sha256 is None or d.get('config_sha256') == config_sha256)]
    groups = {}
    for row in selected:
        d = row['body']['details']
        if 'run_id' not in d:
            continue
        key = d['config_sha256'], d['run_id']
        state = groups.setdefault(key, dict(refused=False, started=False, outcome=None))
        if d['outcome'] == 'PREFLIGHT_FAILED':
            state['refused'] = True
        elif d['outcome'] == 'RUN_STARTED':
            state['started'] = True
        elif d['outcome'] == 'RUN_RECORDED' and d.get('release_git_sha') == plan.release_git_sha:
            outcome = _linked_outcome(plan, store, row)
            if outcome is not None:
                state['outcome'] = outcome
    completed = sum(g['outcome'] == 'BOUNDED_RUN_FINISHED' for g in groups.values())
    degraded = sum(g['outcome'] == 'DEGRADED' for g in groups.values())
    targets = []
    for target in plan.targets:
        admissions, scan = _history(store, kind='REGISTRY', event_id='admission:' + target.scope.key,
                                     maximum_rows=maximum_rows)
        qualified_admissions, qualified_outcomes = set(), set()
        for row in admissions:
            details = row['body'].get('details', {})
            if not isinstance(details, dict) or details.get('version') != FORWARD_VERSION:
                continue
            q = details.get('qualification', {})
            if not isinstance(q, dict):
                continue
            try:
                if (q.get('plan_key') != plan.key or q.get('scope_key') != target.scope.key
                        or details.get('financial_authority') is not False
                        or row['body'].get('financial_authority') is not False
                        or row['body']['recorded_at'] > store.clock()):
                    continue
                checked = _qualified_admission(plan, store, target, q['capture_id'], details['label_ids'])
                if checked == q and max(x['knowable_at'] for x in q['labels']) <= row['body']['recorded_at']:
                    qualified_admissions.add(q['admission_id'])
                    qualified_outcomes.add(q['grouped_outcome_id'])
            except (EvidenceError, KeyError, TypeError, ValueError):
                continue
        admission_count, sample_count = len(qualified_admissions), len(qualified_outcomes)
        targets.append(dict(scope_key=target.scope.key, event_id=target.event_id, station=target.scope.station,
            family=target.scope.family, unit=target.unit, strategy=target.scope.strategy,
            sample_target=target.sample_target, qualifying_forward_sample_count=sample_count,
            forward_admission_count=admission_count,
            unqualified_admission_rows_scanned=len(admissions)-admission_count,
            admission_history_coverage=scan,
            sample_target_reached=scan['complete'] and min(admission_count, sample_count) >= target.sample_target,
            reason=None if min(admission_count, sample_count) else FORWARD_UNAVAILABLE))
    complete = coverage['complete'] and all(t['admission_history_coverage']['complete'] for t in targets)
    has_forward = any(t['forward_admission_count'] for t in targets)
    return dict(version=VERSION, plan_key=plan.key, namespace=store.namespace, as_of=finite(store.clock()),
        plan_intent_created_at=plan.created_at, forward_evidence_available=has_forward,
        reason=None if has_forward else FORWARD_UNAVAILABLE,
        history_complete=complete, totals_are_lower_bounds=not complete, run_history_coverage=coverage,
        targets=targets, attempts_recorded=len(groups), refused_attempts_recorded=sum(g['refused'] for g in groups.values()),
        incomplete_attempts_recorded=sum(g['started'] and g['outcome'] is None for g in groups.values()),
        completed_commission_runs_recorded=completed, degraded_commission_runs_recorded=degraded,
        total_commission_runs_recorded=completed + degraded, forward_commission_runs_recorded=0,
        financial_authority=False, real_orders_sent=False, promotion_claim=None, profitability_claim=None)


def _cli():
    import argparse
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest='command', required=True)
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument('--plan', required=True, type=Path)
    common.add_argument('--store', required=True, type=Path)
    common.add_argument('--namespace', required=True)
    pf = sub.add_parser('preflight', parents=[common])
    pf.add_argument('--release-git-sha', required=True)
    sub.add_parser('status', parents=[common])
    args = parser.parse_args()
    plan = load_plan(args.plan)
    store = EvidenceStore(args.store, args.namespace)
    if args.command == 'preflight':
        report = preflight(plan, store, current_release_git_sha=args.release_git_sha)
    else:
        report = evidence_status(plan, store)
    print(canonical(report))
    if args.command == 'preflight' and not report['passed']:
        raise SystemExit(1)


if __name__ == '__main__':
    _cli()
