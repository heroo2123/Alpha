"""Finite offline forecast learning worker with evidence triggers and backoff.

This worker is never attached to the candidate runtime. It has no model-pointer,
service, credential, order or cancellation API. OS isolation remains a separate
commissioning gate; these limits are not a post-completion funding wait.
"""
from copy import deepcopy
from dataclasses import asdict, dataclass
import fcntl
import os
import stat

from .evidence import EvidenceError, digest, finite, identity
from .forecast_learning import ForecastLabelJoin, run_forecast_fit
from .learning_capture import VERSION as CAPTURE_VERSION
from .learning_sources import learning_source_view
from .rules import RuleFingerprint


VERSION = 'alpha_v11_forecast_learning_worker_v1'


@dataclass(frozen=True)
class LearningTriggerPolicy:
    version: str
    minimum_new_city_days: int
    minimum_interval_seconds: float
    maximum_daily_attempts: int

    def __post_init__(self):
        identity(self.version)
        if (type(self.minimum_new_city_days) is not int or not 2 <= self.minimum_new_city_days <= 128
                or not 60 <= finite(self.minimum_interval_seconds) <= 604800
                or type(self.maximum_daily_attempts) is not int or not 1 <= self.maximum_daily_attempts <= 24):
            raise EvidenceError('LEARNING_TRIGGER_POLICY_BOUND')


class ForecastLearningWorker:
    def __init__(self, *, source_store, artifacts, journal, policy):
        if (not isinstance(policy, LearningTriggerPolicy) or not journal.store.namespace.startswith('CHALLENGER:')
                or source_store.path.resolve() == journal.store.path.resolve()):
            raise EvidenceError('LEARNING_WORKER_SEPARATE_RESEARCH_SCOPE_REQUIRED')
        self.source, self.artifacts, self.journal, self.policy = source_store, artifacts, journal, policy
        self.store = journal.store
        self.key = 'learning-worker:' + digest(journal.event_id)
        self.config = digest(dict(version=VERSION, policy=asdict(policy), program=journal.event_id,
                                  source_namespace=source_store.namespace, research_namespace=self.store.namespace))

    def _get(self, key):
        try: return self.store.get(key)
        except EvidenceError as exc:
            if str(exc) != 'EVIDENCE_MISSING': raise
        return None

    def _head(self):
        row = self.store.latest(kind='MODEL_EVENT', event_id=self.key)
        if row and row['body']['details'].get('config_sha256') != self.config:
            raise EvidenceError('LEARNING_WORKER_POLICY_CHANGED_REVIEW_REQUIRED')
        return row

    def _save(self, key, head, state, *, request_sha, outcome, **details):
        return self.store.audit(key, event_id=self.key, kind='MODEL_EVENT', details=dict(
            version=VERSION, config_sha256=self.config, action='FORECAST_LEARNING_JOB',
            request_sha256=request_sha, state=state, outcome=outcome,
            financial_authority=False, automatic_promotion=False, os_isolation_verified=False, **details),
            expected_previous_seq=head['seq'] if head else 0)

    def _groups(self, joins, as_of):
        groups, receipts = set(), []
        with learning_source_view(self.source) as view:
            for join in joins:
                row = view.get(join.capture_id); d = row['body'].get('details', {})
                if (row['kind'] != 'MEASUREMENT' or d.get('version') != CAPTURE_VERSION
                        or not d.get('complete_event_vector') or not d.get('parent_feature_contract_verified')
                        or row['body']['available_at'] > as_of or d['context']['city_id'] != join.city):
                    raise EvidenceError('LEARNING_TRIGGER_CAPTURE_INVALID')
                rule = RuleFingerprint(**d['rule']).payload
                targets = {r['target_identity']['market_id']:r['target_identity'] for r in d['rows']}
                if set(dict(join.label_ids)) != set(targets):
                    raise EvidenceError('LEARNING_TRIGGER_COMPLETE_LABEL_SET_REQUIRED')
                context = dict(station=rule['station'], city=join.city, local_date=rule['target_date'],
                    target='FINAL_CONTRACT_PAYOUT', rule_fingerprint=d['binding']['rule_fingerprint'])
                labels = []
                for market, key in join.label_ids:
                    label = view.get(key); b = label['body']; p = b.get('payload', {})
                    if (label['kind'] != 'LABEL' or label['event_id'] != row['event_id']
                            or b['available_at'] > as_of or finite(p.get('knowable_at')) > b['available_at']
                            or b.get('evidence_class') not in {'PUBLIC_OBSERVED','SYNTHETIC'}
                            or p.get('context') != context or p.get('target_identity') != targets[market]
                            or p.get('evidence_type') not in {'EXACT_SOURCE_LABEL','SYNTHETIC'}):
                        raise EvidenceError('LEARNING_TRIGGER_EXACT_AVAILABLE_LABEL_REQUIRED')
                    labels.append(dict(id=key, sha256=label['sha256']))
                groups.add(join.city+':'+rule['target_date'])
                receipts.append(dict(capture_id=row['id'], capture_sha256=row['sha256'], labels=labels))
        return groups, digest(receipts)

    def _recover(self, head, state):
        active = state['active']; run = active['run_id']
        result, finish, failed = (self._get(run+suffix) for suffix in (':result',':finished',':failed'))
        if result or failed:
            recipe = self._get(run+':dataset')
            request = recipe['body']['details']['request'] if recipe else {}
            keys = ('plan_record_id','joins','parent_bundle_sha256','policy_sha256','provenance','as_of')
            if (not recipe or digest(request) != recipe['body']['details']['request_sha256']
                    or digest({key:request.get(key) for key in keys}) != active['fit_identity']):
                raise EvidenceError('LEARNING_WORKER_RESEARCH_RECIPE_BINDING')
        if result and finish:
            r = result['body']['details']; f = finish['body']['details']
            if (f.get('status') != 'NO_PROMOTION' or f.get('result_sha256') != r.get('sha256')
                    or digest(r['result']) != r['sha256'] or r['result'].get('financial_authority') is not False
                    or r['result'].get('status') != 'NO_PROMOTION'
                    or r['result'].get('parent_bundle_sha256') != active['parent_bundle_sha256']
                    or r['result'].get('dataset_sha256',recipe['body']['details']['dataset_sha256'])
                       != recipe['body']['details']['dataset_sha256']):
                raise EvidenceError('LEARNING_WORKER_RESULT_BINDING')
            if r['result']['candidate_bundle_sha256'] is not None:
                self.artifacts.pin(r['result']['candidate_bundle_sha256'])
            state['used_city_days'] = sorted(set(state['used_city_days']) | set(active['city_days']))
            state['active'] = None
            return self._save(active['command_key'], head, state, request_sha=active['request_sha256'],
                outcome='RESEARCH_COMPLETED_NO_PROMOTION', run_id=run, result_sha256=r['sha256'],
                candidate_bundle_sha256=r['result']['candidate_bundle_sha256'])
        if failed:
            if failed['body'].get('details', {}).get('status') != 'FAILED':
                raise EvidenceError('LEARNING_WORKER_FAILURE_BINDING')
            state['active'] = None
            return self._save(active['command_key'], head, state, request_sha=active['request_sha256'],
                outcome='RESEARCH_FAILED_PARENT_UNCHANGED', run_id=run)
        # No automatic rerun after an uncertain worker/fit interruption.
        interrupted = active['command_key']+':interrupted'
        return self._get(interrupted) or self._save(interrupted, head, state,
            request_sha=active['request_sha256'], outcome='INTERRUPTED_FIT_REVIEW_REQUIRED', run_id=run)

    def step(self, command_id, *, plan_record_id, joins, parent_bundle_sha256, envelope, provenance, run_id, as_of):
        """At most one bounded fit, outside all decision/execution processes."""
        identity(command_id, maximum=100); identity(run_id, maximum=100)
        if (type(joins) is not tuple or not 1 <= len(joins) <= 128
                or any(not isinstance(j, ForecastLabelJoin) for j in joins)
                or len({j.capture_id for j in joins}) != len(joins)):
            raise EvidenceError('LEARNING_TRIGGER_COHORT_BOUND')
        as_of = finite(as_of)
        request_sha = digest(dict(config=self.config, plan_record_id=plan_record_id,
            joins=[asdict(j) for j in joins], parent_bundle_sha256=parent_bundle_sha256,
            envelope=asdict(envelope), provenance=provenance, run_id=run_id, as_of=as_of))
        key = 'learning-job:' + digest(command_id)
        fd = os.open(self.store.path.with_name(self.store.path.name+'.learning.lock'),
                     os.O_CREAT|os.O_WRONLY|os.O_NOFOLLOW, 0o600)
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077:
                raise EvidenceError('LEARNING_WORKER_LOCK_CUSTODY')
            try: fcntl.flock(fd, fcntl.LOCK_EX|fcntl.LOCK_NB)
            except BlockingIOError: raise EvidenceError('LEARNING_WORKER_ALREADY_RUNNING') from None
            head = self._head()
            old = self._get(key)
            if old:
                if old['body']['details'].get('request_sha256') != request_sha:
                    raise EvidenceError('LEARNING_WORKER_REPLAY_CONFLICT')
                return old
            state = deepcopy(head['body']['details']['state']) if head else dict(
                active=None, last_attempt_at=None, attempt_day=None, day_attempts=0, used_city_days=[])
            now = finite(self.store.clock())
            if as_of > now or head and now < head['body']['recorded_at']:
                raise EvidenceError('LEARNING_WORKER_CLOCK_OR_CUTOFF')
            if state['active'] is not None:
                return self._recover(head, state)
            def gated(reason, **extra):
                return self._save(key, head, state, request_sha=request_sha, outcome=reason, **extra)
            if state['last_attempt_at'] is not None and now-state['last_attempt_at'] < self.policy.minimum_interval_seconds:
                return gated('DEFERRED_LEARNING_BACKOFF')
            day = int(now//86400)
            if state['attempt_day'] == day and state['day_attempts'] >= self.policy.maximum_daily_attempts:
                return gated('DEFERRED_LEARNING_DAILY_BUDGET')
            groups, cohort_sha = self._groups(joins, as_of)
            new = groups-set(state['used_city_days'])
            if len(new) < self.policy.minimum_new_city_days:
                return gated('DEFERRED_NEW_RESOLVED_EVIDENCE', new_city_days=len(new))
            if len(groups | set(state['used_city_days'])) > 4096:
                return gated('LEARNING_GROUP_HISTORY_BOUND_REVIEW_REQUIRED')
            if any(self._get(run_id+suffix) for suffix in (':dataset',':result',':finished',':failed',':selection')):
                return gated('LEARNING_RUN_ID_ALREADY_USED')
            fit_identity=digest(dict(plan_record_id=plan_record_id,joins=[asdict(j) for j in joins],
                parent_bundle_sha256=parent_bundle_sha256,policy_sha256=envelope.sha256,provenance=provenance,as_of=as_of))
            state.update(last_attempt_at=now, attempt_day=day,
                day_attempts=state['day_attempts']+1 if state['attempt_day']==day else 1,
                active=dict(run_id=run_id, command_key=key, request_sha256=request_sha,
                    parent_bundle_sha256=parent_bundle_sha256, city_days=sorted(groups), cohort_sha256=cohort_sha,
                    fit_identity=fit_identity))
            reserved = self._save(key+':reserved', head, state, request_sha=request_sha,
                outcome='RESEARCH_ATTEMPT_RESERVED', new_city_days=len(new), trigger='NEW_TO_THIS_PROGRAM_COHORT')
            # Reservation precedes fitting. Incomplete fits leave this active
            # record for honest recovery; they cannot trigger a second worker.
            run_forecast_fit(source_store=self.source, artifacts=self.artifacts, journal=self.journal,
                plan_record_id=plan_record_id, joins=joins, parent_bundle_sha256=parent_bundle_sha256,
                envelope=envelope, provenance=provenance, run_id=run_id, as_of=as_of)
            return self._recover(reserved, state)
        finally:
            os.close(fd)
