"""Bounded nonfinancial forward-SHADOW commissioning substrate.

This module adds exactly one thing on top of the existing, already-reviewed
`CandidateRunner`/`PaperRuntime`/`assemble_candidate` machinery: an explicit,
frozen "which exact scopes, how many samples, which release" commissioning
boundary, plus a fail-closed preflight gate against externally provisioned
root-owned state, plus a bounded (never-daemonized) lifecycle wrapper and a
read-only evidence/status report.

It does not build a new `CandidatePlan` factory. `candidate_assembly.py`
already is that factory; this module does not duplicate it and does not
invent markets, rules, stations or scopes. The caller supplies both a real
`CandidateRunner` (built the ordinary way) and a `ShadowCommissionPlan` file
naming the exact frozen cohort this run is authorized to touch.

Hard requirements enforced here, all fail-closed (raise, never silently
degrade or substitute a default):
  * The evidence store's namespace must be a nonfinancial research namespace
    (`CHALLENGER:...` or `ABLATION:...`) -- never `V11_PAPER`. This is the
    concrete meaning of "V11_SHADOW namespace only" in this codebase: the
    `EvidenceStore` itself has no literal `V11_SHADOW` namespace (see
    `evidence.py`'s constructor regex); `V11_SHADOW` is the nonfinancial
    model-authority *mode* string paired with `stage='SHADOW'` admissions in
    any `CHALLENGER:`/`ABLATION:` store, exactly as `strategy_admission.py`,
    `drift_runtime.py` and `model_registry.py` already enforce it.
  * Every declared scope must have its own root-owned model-authority slot
    (`/var/lib/alpha-v11/model-authority/scopes/V11_SHADOW/<scope_key>.json`,
    read only through the unmodified `model_registry.ActiveModelRegistry`)
    and the global station-capability review manifest
    (`/etc/alpha-v11/approvals/station-capabilities.json`, read only through
    the unmodified `certification.protected_reviews`) must be present and
    must name that exact scope for stage `SHADOW` in this namespace. Neither
    file exists on this host as of this writing; both checks therefore fail
    closed today, by design, until the owner installs them.
  * No wildcard `CapabilityScope` (already structurally impossible --
    `CapabilityScope.__post_init__` itself forbids `"*"`).
  * `financial_authority`/`real_orders_sent`/`promotion_authority` are always
    `False` in every durable record this module writes, and this module
    never imports order/execution/wallet/production (V10) code -- verified
    both by a runtime constant and by a static AST check callers can run
    independently (`_no_financial_or_v10_imports`), also covered by a
    dedicated regression test.

What this module deliberately does NOT do:
  * It does not add a CLI/daemon entrypoint that constructs a live
    `CandidateRunner` from scratch. `candidate_runner.py` remains finite and
    off-host with no daemon CLI; adding one here would mean either
    fabricating a real `CandidatePlan` (markets/rules/scopes the owner has
    not supplied) or building a new bespoke daemon path outside the existing
    reviewed construction route. The bounded lifecycle wrapper is a library
    API: the owner/operator constructs a real `CandidateRunner` the existing
    way (`candidate_assembly.assemble_candidate`) and hands it to
    `ShadowCommissionRunner`.
  * It does not install, write, or self-approve any root-owned path. It only
    reads `model_registry.py`/`certification.py`'s existing read-only
    surfaces, unmodified.
  * It never mutates model-authority or station-capability state and never
    grants or claims promotion, calibration, or financial authority.
"""
from __future__ import annotations

import ast
from dataclasses import asdict, dataclass
import json
from pathlib import Path
import re

from . import certification, model_registry
from .candidate_runner import CandidateRunner
from .certification import CapabilityScope
from .evidence import EvidenceError, EvidenceStore, canonical, digest, finite, identity, sha


VERSION = 'alpha_v11_shadow_commission_v1'
PLAN_VERSION = 'alpha_v11_shadow_commission_plan_v1'
KEY = 'v11-shadow-commission'
SHADOW_MODE = 'V11_SHADOW'
SHADOW_STAGE = 'SHADOW'
MAX_PLAN_BYTES = 256 * 1024
_NAMESPACE_RE = re.compile(r'(?:CHALLENGER|ABLATION):[a-zA-Z0-9_-]{1,64}')
_UNITS = ('C', 'F')
# This module and every V11 import it makes must never reach order/execution/
# wallet/production (V10) code. Checked by _no_financial_or_v10_imports and
# by tests/test_v11_shadow_commission.py's static regression.
_FORBIDDEN_IMPORT_PREFIXES = ('production', 'http', 'requests', 'socket', 'pickle', 'subprocess',
                              'urllib', 'ftplib', 'smtplib', 'websockets')


def require_shadow_namespace(namespace: str) -> str:
    if namespace == 'V11_PAPER' or not _NAMESPACE_RE.fullmatch(namespace):
        raise EvidenceError('SHADOW_NAMESPACE_REQUIRED')
    return namespace


@dataclass(frozen=True)
class ShadowScopeTarget:
    """One explicit, non-wildcard scope this frozen cohort is allowed to touch."""
    scope: CapabilityScope
    unit: str
    sample_target: int

    def __post_init__(self):
        if not isinstance(self.scope, CapabilityScope):
            raise EvidenceError('SHADOW_TARGET_SCOPE_REQUIRED')
        if self.unit not in _UNITS:
            raise EvidenceError('SHADOW_TARGET_UNIT_INVALID')
        if type(self.sample_target) is not int or not 1 <= self.sample_target <= 500:
            raise EvidenceError('SHADOW_TARGET_SAMPLE_BOUND')

    @property
    def family_unit(self) -> tuple[str, str]:
        return (self.scope.family, self.unit)


@dataclass(frozen=True)
class ShadowCommissionPlan:
    """A reviewed, frozen forward-SHADOW cohort/sample-target plan.

    This is an operator-supplied intent file, not root-protected state. It
    names *which* scopes/units/sample counts a commissioning run is allowed
    to touch; it grants no authority by itself. Actual authority (model
    bundle, station-capability review) still comes only from the separate
    root-owned planes read through `model_registry.py`/`certification.py`.
    """
    version: str
    namespace: str
    worker_id: str
    release_git_sha: str
    created_at: float
    description: str
    targets: tuple[ShadowScopeTarget, ...]

    def __post_init__(self):
        if self.version != PLAN_VERSION:
            raise EvidenceError('SHADOW_PLAN_VERSION_UNSUPPORTED')
        identity(self.worker_id, maximum=160)
        identity(self.description, maximum=500)
        sha(self.release_git_sha, 40)
        finite(self.created_at)
        require_shadow_namespace(self.namespace)
        if (type(self.targets) is not tuple or not 1 <= len(self.targets) <= 16
                or any(not isinstance(t, ShadowScopeTarget) for t in self.targets)):
            raise EvidenceError('SHADOW_PLAN_TARGET_BOUND')
        keys = [t.scope.key for t in self.targets]
        if len(set(keys)) != len(keys):
            raise EvidenceError('SHADOW_PLAN_DUPLICATE_SCOPE')
        family_units = [t.family_unit for t in self.targets]
        if len(set(family_units)) != len(family_units):
            # Two distinct (family, unit) contracts can never silently share
            # one declared target -- each HIGH/LOW x C/F combination this
            # plan names must bind its own explicit scope, matching R47's
            # four-hash HIGH/LOW x C/F candidate-bundle structure.
            raise EvidenceError('SHADOW_PLAN_DUPLICATE_FAMILY_UNIT')

    @property
    def key(self) -> str:
        return digest(asdict(self))


def _parse_plan_json(raw: bytes) -> dict:
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
    canonical(value)  # depth/shape/secret-field guard shared with the rest of V11
    return value


def _target_from_dict(value: dict) -> ShadowScopeTarget:
    required = {'scope', 'unit', 'sample_target'}
    if not isinstance(value, dict) or set(value) != required:
        raise EvidenceError('SHADOW_PLAN_TARGET_SCHEMA')
    scope_value = value['scope']
    scope_required = {'station', 'family', 'source_rule_family', 'model_version',
                       'horizon', 'strategy', 'season', 'time_of_day'}
    if not isinstance(scope_value, dict) or set(scope_value) != scope_required:
        raise EvidenceError('SHADOW_PLAN_SCOPE_SCHEMA')
    if type(value['sample_target']) is not int:
        raise EvidenceError('SHADOW_PLAN_TARGET_SCHEMA')
    return ShadowScopeTarget(CapabilityScope(**scope_value), value['unit'], value['sample_target'])


def load_plan(path: Path) -> ShadowCommissionPlan:
    """Load and strictly validate an externally supplied plan file.

    The caller (a human/owner) authors this file; nothing here invents a
    market, rule, station or scope. Unknown/missing/duplicate keys, wrong
    types, wildcards and out-of-bound counts all fail closed.
    """
    path = Path(path)
    if not path.is_absolute() or '..' in path.parts:
        raise EvidenceError('SHADOW_PLAN_PATH_INVALID')
    if path.is_symlink():
        raise EvidenceError('SHADOW_PLAN_SYMLINK_REFUSED')
    value = _parse_plan_json(path.read_bytes())
    required = {'version', 'namespace', 'worker_id', 'release_git_sha', 'created_at',
                'description', 'targets'}
    if not isinstance(value, dict) or set(value) != required:
        raise EvidenceError('SHADOW_PLAN_SCHEMA')
    if not isinstance(value['targets'], list) or not value['targets']:
        raise EvidenceError('SHADOW_PLAN_TARGET_BOUND')
    targets = tuple(_target_from_dict(t) for t in value['targets'])
    return ShadowCommissionPlan(value['version'], value['namespace'], value['worker_id'],
        value['release_git_sha'], value['created_at'], value['description'], targets)


def _station_capability_declared(manifest: dict, scope: CapabilityScope, namespace: str) -> bool:
    reviews = manifest.get('reviews', [])
    return any(r.get('scope_key') == scope.key and r.get('namespace') == namespace
               and r.get('stage') == SHADOW_STAGE for r in reviews)


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
    bad = sorted({n for n in names if any(n == p or n.startswith(p + '.') for p in _FORBIDDEN_IMPORT_PREFIXES)})
    if bad:
        return False, 'FORBIDDEN_IMPORT:' + ','.join(bad)
    return True, 'NO_FINANCIAL_OR_V10_IMPORT_IN_THIS_MODULE_STATIC_SOURCE_CHECK_ONLY'


def preflight(plan: ShadowCommissionPlan, store: EvidenceStore, *, current_release_git_sha: str) -> dict:
    """Read-only dry-run check. Starts no work, mutates no state.

    Two distinct failure modes, both fail-closed:
      * Root plane totally unavailable (no station-capability manifest, or no
        model-authority slot for a declared scope) -> this function raises
        immediately. That is the literal case on this host today, since
        neither `/etc/alpha-v11` nor `/var/lib/alpha-v11/model-authority`
        exists.
      * Root plane readable but does not (yet) name this exact scope, or the
        release identity does not match the plan -> this function returns a
        report with `passed=False` and itemized reasons; callers must refuse
        to run on that report exactly as they must refuse on a raised
        exception.
    """
    if not isinstance(plan, ShadowCommissionPlan):
        raise EvidenceError('SHADOW_PLAN_REQUIRED')
    if not isinstance(store, EvidenceStore):
        raise EvidenceError('SHADOW_STORE_REQUIRED')
    if store.namespace != plan.namespace:
        raise EvidenceError('SHADOW_PREFLIGHT_STORE_NAMESPACE_MISMATCH')
    require_shadow_namespace(store.namespace)
    sha(current_release_git_sha, 40)

    checks = []

    def record(name, passed, reason):
        checks.append(dict(name=name, passed=bool(passed), reason=reason))

    record('namespace_nonfinancial_research_only', True, 'NAMESPACE_' + store.namespace)
    release_ok = current_release_git_sha == plan.release_git_sha
    record('release_identity', release_ok, 'RELEASE_MATCHES' if release_ok else 'RELEASE_GIT_SHA_MISMATCH')
    v10_ok, v10_reason = _no_financial_or_v10_imports()
    record('v10_and_financial_route_unreachable_static_only', v10_ok, v10_reason)

    try:
        manifest = certification.protected_reviews()
    except EvidenceError as exc:
        raise EvidenceError('SHADOW_PREFLIGHT_STATION_CAPABILITY_REVIEW_UNAVAILABLE') from exc

    for target in plan.targets:
        name = 'scope:' + target.scope.key[:16] + ':' + target.unit
        declared = _station_capability_declared(manifest, target.scope, store.namespace)
        record(name + ':station_capability_review_declared', declared,
               'SCOPE_NAMED_IN_REVIEWED_MANIFEST' if declared else
               'SCOPE_NOT_YET_REVIEWED_FOR_SHADOW_STAGE_IN_THIS_NAMESPACE')
        try:
            pinned = model_registry.ActiveModelRegistry().pin(scope_key=target.scope.key, mode=SHADOW_MODE)
        except EvidenceError as exc:
            raise EvidenceError('SHADOW_PREFLIGHT_MODEL_AUTHORITY_STATE_UNAVAILABLE') from exc
        record(name + ':model_authority_pinned', True, 'model_epoch=' + str(pinned.epoch))

    bound_ok = all(1 <= t.sample_target <= 500 for t in plan.targets)
    record('sample_target_bounds', bound_ok, 'WITHIN_1_500' if bound_ok else 'OUT_OF_BOUND')

    passed = all(c['passed'] for c in checks)
    return dict(version=VERSION, plan_key=plan.key, namespace=store.namespace, passed=passed,
                financial_authority=False, real_orders_sent=False, promotion_authority=False,
                checks=checks)


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
    """Bounded nonfinancial forward-SHADOW commissioning wrapper.

    Adds nothing to the wrapped `CandidateRunner`'s own event/order/collection
    authority. Adds exactly one extra fail-closed gate (this plan's frozen
    preconditions, re-checked before every finite invocation) plus a bounded,
    idempotent multi-tick lifecycle and read-only evidence/status reporting.
    Never mutates model-authority or station-capability state, never
    promotes, never grants financial authority, never runs an unbounded loop.
    """

    def __init__(self, plan: ShadowCommissionPlan, runner: CandidateRunner, *,
                 release_git_sha: str, lifecycle: ShadowLifecyclePolicy):
        if not isinstance(plan, ShadowCommissionPlan) or not isinstance(runner, CandidateRunner):
            raise EvidenceError('SHADOW_RUNNER_TYPED_COMPONENTS_REQUIRED')
        if not isinstance(lifecycle, ShadowLifecyclePolicy):
            raise EvidenceError('SHADOW_RUNNER_LIFECYCLE_POLICY_REQUIRED')
        store = runner.store
        if store.namespace != plan.namespace:
            raise EvidenceError('SHADOW_RUNNER_NAMESPACE_MISMATCH')
        require_shadow_namespace(store.namespace)
        if runner.runtime.worker_id != plan.worker_id:
            raise EvidenceError('SHADOW_RUNNER_WORKER_IDENTITY_MISMATCH')
        self.plan, self.runner, self.store, self.lifecycle = plan, runner, store, lifecycle
        self.release_git_sha = sha(release_git_sha, 40)
        self.config = digest(dict(version=VERSION, plan_key=plan.key, runner_config=runner.config,
                                   release_git_sha=self.release_git_sha, lifecycle=asdict(lifecycle)))

    def preflight(self) -> dict:
        return preflight(self.plan, self.store, current_release_git_sha=self.release_git_sha)

    def _head(self):
        row = self.store.latest(kind='RUNTIME_STATUS', event_id=KEY)
        if row and row['body']['details'].get('config_sha256') != self.config:
            raise EvidenceError('SHADOW_COMMISSION_CONFIGURATION_CHANGED_REVIEW_REQUIRED')
        return row

    def _save(self, key, **details):
        # An ordinary CAS-guarded audit record, not store.safety_audit(): this
        # wrapper's own status trail is not one of the small, explicitly
        # allow-listed safety-tick invariants _validate_safety_append enforces
        # (paper cancel broker/guardian/candidate-liveness/etc.); it is a
        # regular append-only decision-adjacent record like certification.py's
        # StationRegistry or strategy_admission.py's StrategyAdmission use.
        head = self._head()
        return self.store.audit(key, event_id=KEY, kind='RUNTIME_STATUS', details=dict(
            version=VERSION, config_sha256=self.config, financial_authority=False,
            real_orders_sent=False, deployment_acceptance=False, promotion_authority=False,
            forward_or_live_acceptance=False, **details), expected_previous_seq=head['seq'] if head else 0)

    async def run_once(self, run_id: str) -> dict:
        """One finite, idempotent invocation. Refuses to start on a failed preflight."""
        identity(run_id, maximum=80)
        report = self.preflight()
        if not report['passed']:
            self._save('shadow-run:' + digest([run_id, 'PREFLIGHT_FAILED']),
                       outcome='PREFLIGHT_FAILED', preflight=report, run_id=run_id)
            raise EvidenceError('SHADOW_COMMISSION_PREFLIGHT_FAILED')
        result = await self.runner.run(run_id)
        self._save('shadow-run:' + digest([run_id, result['sha256']]),
                   outcome='RUN_RECORDED', preflight=report, run_id=run_id,
                   candidate_run_id=result['id'], candidate_outcome=result['body']['details']['outcome'])
        return result

    async def run_bounded(self, run_id_prefix: str) -> list[dict]:
        """Exactly `lifecycle.maximum_iterations` finite ticks; never a daemon loop.

        Each iteration derives a distinct, deterministic run_id from
        `(run_id_prefix, iteration_index)`; a crash/restart that repeats an
        iteration replays the wrapped `CandidateRunner.run()`'s own cached
        result rather than redoing or duplicating work.
        """
        identity(run_id_prefix, maximum=60)
        import asyncio
        results = []
        for i in range(self.lifecycle.maximum_iterations):
            run_id = run_id_prefix + ':iteration:' + str(i)
            results.append(await self.run_once(run_id))
            if i < self.lifecycle.maximum_iterations - 1:
                await asyncio.sleep(self.lifecycle.interval_seconds)
        return results


def evidence_status(plan: ShadowCommissionPlan, store: EvidenceStore) -> dict:
    """Read-only forward-SHADOW evidence/status report. No profitability or
    promotion claim of any kind -- both are explicitly `None` below.
    """
    if not isinstance(plan, ShadowCommissionPlan):
        raise EvidenceError('SHADOW_PLAN_REQUIRED')
    if not isinstance(store, EvidenceStore):
        raise EvidenceError('SHADOW_STORE_REQUIRED')
    if store.namespace != plan.namespace:
        raise EvidenceError('SHADOW_STATUS_NAMESPACE_MISMATCH')
    now = finite(store.clock())
    targets_report = []
    for target in plan.targets:
        admission_event = 'admission:' + target.scope.key
        limit = min(1000, max(64, target.sample_target * 4))
        pins = store.records(kind='REGISTRY', event_id=admission_event, limit=limit)
        forward_pins = [p for p in pins if p['body']['recorded_at'] > plan.created_at]
        targets_report.append(dict(
            scope_key=target.scope.key, station=target.scope.station, family=target.scope.family,
            unit=target.unit, strategy=target.scope.strategy, sample_target=target.sample_target,
            forward_admission_count=len(forward_pins),
            historical_or_pre_freeze_admission_count=len(pins) - len(forward_pins),
            sample_target_reached=len(forward_pins) >= target.sample_target))
    run_rows = store.records(kind='RUNTIME_STATUS', event_id=KEY, limit=1000)
    forward_runs = [r for r in run_rows if r['body']['recorded_at'] > plan.created_at]
    return dict(version=VERSION, plan_key=plan.key, namespace=store.namespace, as_of=now,
                frozen_at=plan.created_at,
                historical_vs_forward='ONLY_RECORDS_AFTER_frozen_at_COUNT_AS_FORWARD_SHADOW_EVIDENCE',
                targets=targets_report, total_commission_runs_recorded=len(run_rows),
                forward_commission_runs_recorded=len(forward_runs),
                financial_authority=False, real_orders_sent=False,
                promotion_claim=None, profitability_claim=None)


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
