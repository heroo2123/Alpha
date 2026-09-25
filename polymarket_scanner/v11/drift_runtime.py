"""Finite, journaled drift safety reduction; no learner or model publisher.

An explicit reviewed cohort can reduce one paper/shadow station-strategy scope.
No successful measurement restores a scope, changes parameters or grants authority.
The shared candidate is cooperative; independent guardian commissioning is separate.
"""
from contextlib import contextmanager
from dataclasses import asdict, dataclass
import fcntl
import os
from pathlib import Path
import stat
import time

from .certification import CapabilityScope, StationRegistry, _root_custody
from .drift import DriftPolicy, measure_window
from .evidence import EvidenceError, canonical, digest, finite, identity, sha
from .forecast_learning import ForecastLabelJoin
from .learning_sources import learning_source_view
from .model_artifacts import parse_data
from .model_registry import ActiveModelRegistry
from .paper_coordinator import PaperCoordinator


VERSION = 'alpha_v11_reviewed_drift_worker_v1'
REVIEW_PATH = Path('/etc/alpha-v11/approvals/drift-policies.json')


@dataclass(frozen=True)
class DriftPlan:
    scope: CapabilityScope
    bundle_sha256: str
    policy: DriftPolicy

    def __post_init__(self):
        if (not isinstance(self.scope, CapabilityScope) or not isinstance(self.policy, DriftPolicy)
                or self.policy.minimum_events > 64):
            raise EvidenceError('DRIFT_TYPED_PLAN_REQUIRED')
        sha(self.bundle_sha256)

    @property
    def key(self): return digest(asdict(self))


def protected_reviews():
    """Fixed protected read-only path, duplicate-key rejection and bounded bytes."""
    try:
        before = _root_custody(REVIEW_PATH)
        fd = os.open(REVIEW_PATH, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(fd, 'rb') as stream:
            opened = os.fstat(stream.fileno())
            if (before.st_dev, before.st_ino) != (opened.st_dev, opened.st_ino):
                raise EvidenceError('DRIFT_REVIEW_READ_RACE')
            value = parse_data(stream.read(1024*1024+1), max_bytes=1024*1024)
        if (set(value) != {'version','reviews'} or value['version'] != 'alpha_v11_drift_reviews_v1'
                or not isinstance(value['reviews'], list) or len(value['reviews']) > 1000
                or any(not isinstance(r, dict) for r in value['reviews'])):
            raise EvidenceError('DRIFT_REVIEW_SCHEMA')
        return value
    except OSError as exc:
        raise EvidenceError('DRIFT_REVIEW_UNAVAILABLE') from exc


class DriftWorker:
    def __init__(self, coordinator, plans):
        if (not isinstance(coordinator, PaperCoordinator) or coordinator.store.namespace not in {'V11_PAPER','V11_SHADOW'}
                or type(plans) is not tuple or not 1 <= len(plans) <= 16
                or any(not isinstance(p, DriftPlan) for p in plans)
                or len({p.scope.key for p in plans}) != len(plans)):
            raise EvidenceError('DRIFT_WORKER_SCOPE_OR_BOUND')
        self.coordinator, self.store = coordinator, coordinator.store
        self.plans = {p.key:p for p in plans}
        self.key = 'drift-worker:' + digest(coordinator.policy.account_id)
        self.config = digest(dict(version=VERSION, account_id=coordinator.policy.account_id,
            namespace=self.store.namespace, plans=[asdict(p) for p in plans]))

    def _get(self, key):
        try: return self.store.get(key)
        except EvidenceError as exc:
            if str(exc) != 'EVIDENCE_MISSING': raise
        return None

    def _head(self):
        row = self.store.latest(kind='MODEL_EVENT', event_id=self.key)
        if row and row['body']['details'].get('config_sha256') != self.config:
            raise EvidenceError('DRIFT_CONFIGURATION_CHANGED_REVIEW_REQUIRED')
        return row

    @contextmanager
    def _lock(self):
        fd = os.open(self.store.path.with_name(self.store.path.name+'.drift.lock'),
                     os.O_CREAT | os.O_WRONLY | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
        try:
            if not stat.S_ISREG(os.fstat(fd).st_mode): raise EvidenceError('DRIFT_LOCK_FILE_REQUIRED')
            try: fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError: raise EvidenceError('DRIFT_ALREADY_RUNNING') from None
            yield
        finally: os.close(fd)

    def _save(self, key, head, state, *, action, evidence_ids=(), **details):
        if len(canonical(state).encode()) > 256*1024: raise EvidenceError('DRIFT_STATE_BYTES_BOUND')
        return self.store.audit(key, event_id=self.key, kind='MODEL_EVENT', details=dict(
            version=VERSION, config_sha256=self.config, action=action, state=state,
            financial_authority=False, automatic_restoration=False, calibration_acceptance=False,
            independent_guardian_commissioned=False, **details), evidence_ids=evidence_ids,
            expected_previous_seq=head['seq'] if head else 0)

    def request(self, request_id, *, plan_key, joins, as_of):
        """One pending exact cohort, explicitly submitted; no unbounded archive scan.

        Completed identities replay without renewal. A busy slot never drops work.
        Request acceptance is not approval of the policy or statistical evidence.
        """
        identity(request_id); sha(plan_key); as_of = finite(as_of)
        if (plan_key not in self.plans or as_of > self.store.clock() or type(joins) is not tuple
                or not 1 <= len(joins) <= 64 or any(not isinstance(j, ForecastLabelJoin)
                    or j.prior_exposure != 'DEVELOPMENT' for j in joins)
                or len({j.capture_id for j in joins}) != len(joins)):
            raise EvidenceError('DRIFT_REQUEST_SCOPE_COHORT_CUTOFF')
        key = 'drift-request:' + digest([self.key, request_id])
        request = dict(plan_key=plan_key, joins=[asdict(j) for j in joins], as_of=as_of)
        request_sha = digest(request)
        with self._lock():
            old = self._get(key)
            if old:
                if (old['body']['details'].get('config_sha256') != self.config
                        or old['body']['details'].get('request_sha256') != request_sha):
                    raise EvidenceError('DRIFT_REQUEST_REPLAY_CONFLICT')
                return old
            head = self._head()
            state = head['body']['details']['state'] if head else dict(active=None, last={})
            if state['active'] is not None: raise EvidenceError('DRIFT_PENDING_COHORT_MUST_FINISH')
            previous = state['last'].get(plan_key)
            cohort_sha = digest(request['joins'])
            if previous and (as_of <= previous['as_of'] or previous.get('demotion_applied')
                             and cohort_sha == previous['cohort_sha256']):
                raise EvidenceError('DRIFT_NEW_COHORT_AND_CUTOFF_REQUIRED')
            state['active'] = dict(request=request, request_id=key, request_sha256=request_sha)
            state['last'][plan_key] = dict(as_of=as_of, cohort_sha256=cohort_sha)
            return self._save(key, head, state, action='DRIFT_COHORT_REQUEST',
                outcome='QUEUED_NOT_REVIEWED', request_sha256=request_sha)

    def _review(self, plan, result):
        now = finite(self.store.clock()); request = result['request']
        matches = [r for r in protected_reviews()['reviews'] if r.get('plan_key') == plan.key
            and r.get('account_id') == self.coordinator.policy.account_id and r.get('namespace') == self.store.namespace]
        if len(matches) != 1: raise EvidenceError('DRIFT_EXACT_POLICY_REVIEW_REQUIRED')
        r = matches[0]
        fields = {'plan_key','account_id','namespace','review_id','reviewer','approved_at','expires_at',
                  'model_state_sha256','maximum_measurement_age_seconds','selection','action','financial_authority'}
        if set(r) != fields: raise EvidenceError('DRIFT_REVIEW_SCHEMA')
        identity(r['review_id']); identity(r['reviewer']); sha(r['model_state_sha256'])
        if (r['action'] != 'SAFETY_REDUCTION_ONLY' or r['financial_authority'] is not False
                or r['selection'] != result['selection'] or r['selection'] != 'EXPLICIT_CAPTURE_COHORT'
                or not finite(r['approved_at']) <= result['window']['start_inclusive'] <= request['as_of'] <= now < finite(r['expires_at'])
                or not 1 <= finite(r['maximum_measurement_age_seconds']) <= 86400
                or now-request['as_of'] > r['maximum_measurement_age_seconds']):
            raise EvidenceError('DRIFT_REVIEW_NOT_PREDECLARED_CURRENT_OR_REDUCTION_ONLY')
        registry = ActiveModelRegistry(); pin = registry.pin(scope_key=plan.scope.key, mode=self.store.namespace)
        if (pin.bundle.sha256 != plan.bundle_sha256 or pin.state_sha256 != r['model_state_sha256']
                or any(row['model_state_sha256'] != pin.state_sha256 for row in result['rows'])
                or not registry.revalidate(pin)['passed']):
            raise EvidenceError('DRIFT_CURRENT_MODEL_EPOCH_REQUIRED')
        return r

    def _labels_current(self, result):
        # An already completed measurement stays immutable. A later label revision
        # gates its reduction instead of silently replacing the saved measurement.
        now = finite(self.store.clock())
        with learning_source_view(self.store, deadline=time.monotonic()+1.) as view:
            for row in result['rows']:
                for ref in row['labels']:
                    label = view.get(ref['id']); b = label['body']
                    latest = view.latest_source(kind='LABEL', event_id=row['event_id'], provider=b['provider'],
                                               source_identity=b['source_identity'], as_of=now)
                    if latest is None or latest['sha256'] != ref['sha256']:
                        raise EvidenceError('DRIFT_LABEL_CHANGED_BEFORE_REDUCTION')

    def step(self, command_id):
        identity(command_id); key = 'drift-step:' + digest([self.key, command_id])
        with self._lock():
            old = self._get(key)
            if old:
                if old['body']['details'].get('config_sha256') != self.config:
                    raise EvidenceError('DRIFT_STEP_REPLAY_CONFLICT')
                return old
            head = self._head(); state = head['body']['details']['state'] if head else dict(active=None, last={})
            active = state['active']
            if active is None:
                return self._save(key, head, state, action='DRIFT_IDLE', outcome='NO_EXPLICIT_COHORT')
            request = active['request']; plan = self.plans[request['plan_key']]
            measurement_key = 'drift-measurement:' + digest(active['request_id'])
            measurement = self._get(measurement_key)
            if measurement is None:
                try:
                    joins = tuple(ForecastLabelJoin(**dict(j, label_ids=tuple(tuple(p) for p in j['label_ids']))) for j in request['joins'])
                    result = measure_window(self.store, scope=plan.scope, account_id=self.coordinator.policy.account_id,
                        bundle_sha256=plan.bundle_sha256, policy=plan.policy, joins=joins, as_of=request['as_of'])
                    details = dict(result=result, result_sha256=digest(result), outcome=result['outcome'])
                except (EvidenceError, KeyError, TypeError, ValueError) as exc:
                    details = dict(result=None, outcome='MEASUREMENT_GATED',
                        reason=str(exc) if isinstance(exc, EvidenceError) else 'DRIFT_MALFORMED_EVIDENCE')
                measurement = self.store.audit(measurement_key, event_id=self.key+':measurements', kind='MODEL_EVENT',
                    details=dict(version=VERSION, config_sha256=self.config, action='DRIFT_MEASUREMENT',
                        request_sha256=active['request_sha256'], financial_authority=False, **details),
                    evidence_ids=(active['request_id'],))
            d = measurement['body']['details']; result = d['result']; outcome = d['outcome']; reason = d.get('reason')
            if (d.get('config_sha256') != self.config or d.get('request_sha256') != active['request_sha256']
                    or result is not None and digest(result) != d.get('result_sha256')):
                raise EvidenceError('DRIFT_SAVED_MEASUREMENT_BINDING')
            demotion_key = 'drift-demotion:' + digest(active['request_id']); demotion = self._get(demotion_key)
            review = None
            if demotion is not None:
                if (demotion['kind'] != 'REGISTRY' or demotion['body']['details'].get('scope_key') != plan.scope.key
                        or demotion['body']['evidence'] != [dict(id=measurement['id'],sha256=measurement['sha256'])]):
                    raise EvidenceError('DRIFT_SAVED_DEMOTION_BINDING')
                outcome = 'SCOPED_SAFETY_REDUCTION_APPLIED'
                saved_review = self._get('drift-review:' + digest(active['request_id']))
                if saved_review is None: raise EvidenceError('DRIFT_SAVED_REVIEW_MISSING')
                review = saved_review['body']['details']['review']
            elif result is not None and result['outcome'] == 'DEGRADATION_CANDIDATE':
                try:
                    station = self.store.latest(kind='REGISTRY', event_id='station:'+plan.scope.station)
                    label_heads = tuple(('LABEL', event, self.store.latest(kind='LABEL',event_id=event)['seq'])
                                        for event in sorted({row['event_id'] for row in result['rows']}))
                    self._labels_current(result)
                    review = self._review(plan, result)
                    # Preserve the exact accepted review with the decision; the
                    # source measurement does not assert independent approval.
                    review_key = 'drift-review:' + digest(active['request_id'])
                    saved_review = self._get(review_key)
                    if saved_review is None:
                        saved_review = self.store.audit(review_key, event_id=self.key+':reviews', kind='MODEL_EVENT',
                            details=dict(version=VERSION, action='DRIFT_REVIEW_PIN', review=review, review_sha256=digest(review),
                                measurement_id=measurement['id'], financial_authority=False), evidence_ids=(measurement['id'],))
                    if saved_review['body']['details']['review_sha256'] != digest(review):
                        raise EvidenceError('DRIFT_REVIEW_CHANGED_DURING_RECOVERY')
                    if self._review(plan, result) != review: raise EvidenceError('DRIFT_REVIEW_CHANGED_BEFORE_REDUCTION')
                    demotion = StationRegistry(self.store).demote(demotion_key, plan.scope, state='CALIBRATION_DEGRADED',
                        reason='REVIEWED_ROLLING_QUALITY_BREACH', evidence_ids=(measurement['id'],),
                        expected_previous_seq=station['seq'] if station else 0, expected_heads=label_heads)
                    outcome = 'SCOPED_SAFETY_REDUCTION_APPLIED'
                except (EvidenceError, KeyError, TypeError, ValueError) as exc:
                    outcome = 'REDUCTION_GATED'
                    reason = str(exc) if isinstance(exc, EvidenceError) else 'DRIFT_REVIEW_MALFORMED'
            state['active'] = None
            state['last'][request['plan_key']]['demotion_applied'] = demotion is not None
            refs = (active['request_id'], measurement['id']) + ((demotion['id'],) if demotion else ())
            return self._save(key, head, state, action='DRIFT_RESULT', outcome=outcome, reason=reason,
                request_sha256=active['request_sha256'], measurement_id=measurement['id'],
                demotion_id=demotion['id'] if demotion else None, policy_review_sha256=digest(review) if review else None,
                demotion_applied=demotion is not None, evidence_ids=refs)
