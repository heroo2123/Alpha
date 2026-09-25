"""Finite independent Linux PAPER guardian. No services, transport or credentials.

Run as a sibling of the candidate, never from its event loop. Same-UID trusted
process independence is not authenticated cancel-only custody. The current
shared SQLite writer/disk remains a common failure domain and fails closed.
"""
from dataclasses import asdict
import ctypes
import fcntl
import json
import os
from pathlib import Path
import resource
import signal
import sqlite3
import stat
import subprocess
import sys
import time
import uuid

from .evidence import EvidenceError, EvidenceStore, Limits, canonical, digest, finite, identity
from .event_risk import EventRiskEngine
from .guardian_lease import (VERSION, GuardianPolicy, config_digest, journal_key,
                             process_identity, validate_details)
from .paper_cancellation import CancellationPolicy, PaperCancellation, managed_opening
from .paper_coordinator import PaperAccountPolicy, PaperCoordinator, UNRESOLVED, cancel_identity
from .runtime_health import (KEY as HEALTH_KEY, VERSION as HEALTH_VERSION, HealthPolicy, admission_heads, host_stamp,
                             read_health_snapshot, _worker_key)
from .scenario_risk import CorrelationMap, StationMembership, ScenarioLimits


MAX_CONFIG_BYTES = 32768


class GuardianDeadline(BaseException):
    """Terminal alarm; deliberately not caught by per-intent failure handlers."""


class GuardianObservationChanged(EvidenceError):
    """The decision's health snapshot changed before publication; no cancel trigger."""


def validate_health_observation(store,observed,*,worker,health_config,account_id):
    try:
        _validate_health_observation(store,observed,worker=worker,health_config=health_config,account_id=account_id)
    except (KeyError,TypeError,ValueError,IndexError,OverflowError):
        raise EvidenceError('GUARDIAN_HEALTH_MALFORMED') from None


def _validate_health_observation(store,observed,*,worker,health_config,account_id):
    if observed.get('error'):raise EvidenceError(observed['error'])
    if process_identity(worker['pid'])!=worker:raise EvidenceError('GUARDIAN_WORKER_IDENTITY_CHANGED')
    row=observed['row']
    if row is None:raise EvidenceError('GUARDIAN_HEALTH_MISSING')
    d=row['body']['details'];now=host_stamp(store);stamp=d.get('stamp',{})
    if (d.get('version')!=HEALTH_VERSION or d.get('config_sha256')!=health_config or d.get('account_id')!=account_id):
        raise EvidenceError('GUARDIAN_HEALTH_IDENTITY_CHANGED')
    policy=HealthPolicy(**dict(d['policy'],workers=tuple(d['policy']['workers'])))
    if (type(d['sources']) is not list or not 1<=len(d['sources'])<=32
            or digest(dict(policy=d['policy'],account_id=d['account_id'],scopes=d['scopes'],
                           sources=[s['requirement'] for s in d['sources']]))!=health_config):
        raise EvidenceError('GUARDIAN_HEALTH_CONFIG_MISMATCH')
    if (type(d['global_reasons']) is not list or any(type(r) is not str for r in d['global_reasons'])
            or type(d['workers']) is not list or tuple(w['worker'] for w in d['workers'])!=policy.workers
            or d.get('financial_authority') is not False or d.get('independent_guardian_commissioned') is not False):
        raise EvidenceError('GUARDIAN_HEALTH_MALFORMED')
    for value in (stamp['wall'],stamp['monotonic'],d['valid_until'],d['monotonic_valid_until']):finite(value)
    high=observed['archive_high']
    if high is not None and now['wall']<high:raise EvidenceError('GUARDIAN_ARCHIVE_CLOCK_AHEAD')
    if (d['global_reasons'] or now['boot_id']!=stamp['boot_id']
            or not stamp['wall']<=now['wall']<d['valid_until']
            or not stamp['monotonic']<=now['monotonic']<d['monotonic_valid_until']
            or not 0<=now['wall']-stamp['wall']<policy.maximum_sample_age_seconds
            or not 0<=now['monotonic']-stamp['monotonic']<policy.maximum_sample_age_seconds
            or abs((now['wall']-stamp['wall'])-(now['monotonic']-stamp['monotonic']))>d['policy']['maximum_wall_step_seconds']):
        raise EvidenceError('GUARDIAN_CLOCK_OR_HEALTH_GATED')
    for entry in d['workers']:
        heartbeat=observed['workers'][entry['worker']]
        if heartbeat is None or heartbeat['id']!=entry['record_id']:raise EvidenceError('GUARDIAN_HEARTBEAT_CHANGED')
        h=heartbeat['body']['details']
        if (h.get('version')!=HEALTH_VERSION or h.get('worker')!=entry['worker']
                or h.get('financial_authority') is not False
                or h.get('request')!=dict(action='HEARTBEAT',worker=entry['worker'],generation=identity(h['generation']))):
            raise EvidenceError('GUARDIAN_HEARTBEAT_MALFORMED')
        finite(h['stamp']['wall']);finite(h['stamp']['monotonic'])
        if (h.get('config_sha256')!=health_config or h['stamp']['boot_id']!=now['boot_id']
                or not 0<=now['wall']-h['stamp']['wall']<d['policy']['heartbeat_age_seconds']
                or not 0<=now['monotonic']-h['stamp']['monotonic']<d['policy']['heartbeat_age_seconds']):
            raise EvidenceError('GUARDIAN_WORKER_HEARTBEAT_EXPIRED')


def check_ready_transaction(store,db,details,heads):
    """Refresh the pinned observation's time/process checks after the write lock.

    This applies only to a READY decision carrying an observed health head. Raw
    safety cancellation, pending recovery and explicit fixture leases are untouched.
    """
    pins={(kind,event):seq for kind,event,seq in heads}
    if ('RUNTIME_STATUS',HEALTH_KEY) not in pins:return
    def pinned(event):
        seq=pins.get(('RUNTIME_STATUS',event),0)
        return store._decode(db.execute('SELECT * FROM v11_records WHERE seq=?',(seq,)).fetchone()) if seq else None
    try:
        row=pinned(HEALTH_KEY)
        entries=row['body']['details']['workers'] if row else []
        if type(entries) is not list or not 1<=len(entries)<=8:raise EvidenceError('GUARDIAN_WORKER_SCHEMA')
        observed=dict(row=row,workers={w['worker']:pinned(_worker_key(w['worker'])) for w in entries},
            archive_high=db.execute('SELECT MAX(recorded_at) FROM v11_records').fetchone()[0])
        validate_health_observation(store,observed,worker=details['worker'],health_config=details['health_config'],
            account_id=details['account_id'])
        for key in ('process','broker_process'):
            if key in details and process_identity(details[key]['pid'])!=details[key]:
                raise EvidenceError('GUARDIAN_PROCESS_CHANGED_BEFORE_READY')
    except (EvidenceError,KeyError,TypeError,ValueError,IndexError,OverflowError):
        raise GuardianObservationChanged('GUARDIAN_HEALTH_EXPIRED_BEFORE_READY') from None


def _deadline(*_):
    raise GuardianDeadline()


def process_limits():
    """Trusted child bootstrap: hard limits and no privilege gain.

    No seccomp/network firewall or separate-UID security is claimed. This module
    imports no collector/exchange client and offers no arbitrary callback/command.
    """
    os.umask(0o077)
    for key, value in ((resource.RLIMIT_CORE, 0), (resource.RLIMIT_NOFILE, 64),
                       (resource.RLIMIT_AS, 512*1024**2), (resource.RLIMIT_FSIZE, 256*1024**2),
                       (resource.RLIMIT_CPU, 30)):
        _, old_hard = resource.getrlimit(key)
        bound = value if old_hard == resource.RLIM_INFINITY else min(old_hard, value)
        resource.setrlimit(key, (bound, bound))
    libc = ctypes.CDLL(None, use_errno=True)
    if libc.prctl(38, 1, 0, 0, 0) != 0 or libc.prctl(39, 0, 0, 0, 0) != 1:
        raise EvidenceError('GUARDIAN_NO_NEW_PRIVILEGES_UNAVAILABLE')


def launch_guardian(config, *, cycles):
    """Explicit local sibling launch with a fixed module and clean environment.

    Invoke from the local supervisor, not a candidate job. Caller owns wait/stop
    and drains the one-line result. This grants no service/deployment authority.
    """
    payload = canonical(dict(config=config, cycles=cycles)).encode()
    if len(payload) > MAX_CONFIG_BYTES: raise EvidenceError('GUARDIAN_CONFIG_BYTES_BOUND')
    process = subprocess.Popen([sys.executable, '-s', '-E', '-m', 'polymarket_scanner.v11.paper_guardian'],
        cwd=Path(__file__).resolve().parents[2], env={'PATH':'/usr/bin:/bin','LANG':'C.UTF-8'},
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        close_fds=True, start_new_session=True)
    try:
        process.stdin.write(payload)
        process.stdin.close(); process.stdin = None
    except BaseException:
        process.kill(); process.communicate(timeout=5)
        raise
    return process


def configuration(coordinator, *, policy, health_config, worker):
    """Nonsecret typed launch description. Persist outside Git if saved locally."""
    config_digest(policy=policy, account_policy_sha=coordinator.policy_sha, health_config=health_config,
                  worker=worker, archive_limits=coordinator.store.limits)
    return dict(version=VERSION, database=str(coordinator.store.path), archive_limits=asdict(coordinator.store.limits),
        account=asdict(coordinator.policy), correlation=asdict(coordinator.correlation), limits=asdict(coordinator.limits),
        guardian=asdict(policy), health_config=health_config, worker=worker)


def restore(raw):
    if (type(raw) is not dict or set(raw) != {'version','database','archive_limits','account','correlation',
                                             'limits','guardian','health_config','worker'} or raw['version'] != VERSION):
        raise EvidenceError('GUARDIAN_CONFIG_SCHEMA')
    policy = GuardianPolicy(**raw['guardian'])
    account = PaperAccountPolicy(**raw['account'])
    corr = dict(raw['correlation'])
    corr['memberships'] = tuple(StationMembership(**dict(m, **{k:tuple(m[k]) for k in
        ('weather_groups','source_groups','model_groups')})) for m in corr['memberships'])
    correlation = CorrelationMap(**corr); limits = ScenarioLimits(**raw['limits'])
    # Never initialize an empty archive or modify a foreign/V10 database.
    path = Path(raw['database'])
    if not path.is_file(): raise EvidenceError('GUARDIAN_EXISTING_PAPER_ARCHIVE_REQUIRED')
    store = EvidenceStore(path, 'V11_PAPER', limits=Limits(**raw['archive_limits']))
    coordinator = PaperCoordinator(store, policy=account, correlation=correlation, limits=limits)
    return PaperGuardian(coordinator, policy=policy, health_config=raw['health_config'], worker=raw['worker'])


class PaperGuardian:
    def __init__(self, coordinator, *, policy, health_config, worker):
        if coordinator.store.namespace != 'V11_PAPER': raise EvidenceError('GUARDIAN_PAPER_ONLY')
        self.coordinator, self.store, self.policy = coordinator, coordinator.store, policy
        self.config = config_digest(policy=policy, account_policy_sha=coordinator.policy_sha, health_config=health_config,
                                    worker=worker, archive_limits=self.store.limits)
        self.health_config, self.worker = health_config, dict(worker)
        self.process = process_identity(os.getpid())
        if self.process == self.worker: raise EvidenceError('GUARDIAN_SEPARATE_PROCESS_REQUIRED')
        self.key = journal_key(coordinator.policy.account_id)
        self.generation = uuid.uuid4().hex
        self.cancellation = PaperCancellation(coordinator, CancellationPolicy('independent-paper-guardian-v1', policy.maximum_intents, policy.maximum_intents))

    def _head(self):
        row = self.store.latest(kind='RUNTIME_STATUS', event_id=self.key)
        if row:
            validate_details(self.key, row['body']['details'])
            if row['body']['details']['config_sha256'] != self.config:
                raise EvidenceError('GUARDIAN_CONFIG_CHANGED_REVIEW_REQUIRED')
        return row

    def _save(self, head, *, status, reasons, cursor, intents=None, snapshot=None, pending_trigger=None, record_id=None,
              observed_heads=None):
        details = dict(version=VERSION, config_sha256=self.config, account_id=self.coordinator.policy.account_id,
            account_policy_sha256=self.coordinator.policy_sha, health_config=self.health_config, policy=asdict(self.policy),
            worker=self.worker, process=self.process, stamp=host_stamp(self.store), generation=self.generation,
            status=status, reasons=reasons, cursor=cursor, cancel_intents=intents or {},
            account_snapshot_id=snapshot['id'] if snapshot else None, pending_trigger=pending_trigger,
            archive_limits=asdict(self.store.limits),
            financial_authority=False, independent_guardian_commissioned=False)
        if hasattr(self, 'broker_process'):
            details['broker_process'] = self.broker_process
        validate_details(self.key, details)
        try:
            return self.store.safety_audit(record_id or 'guardian:'+uuid.uuid4().hex, event_id=self.key, kind='RUNTIME_STATUS', details=details,
                evidence_ids=(snapshot['id'],) if snapshot else (), expected_previous_seq=head['seq'] if head else 0,
                expected_heads=observed_heads or ())
        except EvidenceError as exc:
            if observed_heads is not None and str(exc)=='AUDIT_GUARDED_STATE_CHANGED':
                raise GuardianObservationChanged('GUARDIAN_HEALTH_PUBLICATION_CHANGED') from None
            raise

    def _health(self):
        """Observe the worker's health; never impersonate its heartbeat/sample writer."""
        observed=read_health_snapshot(self.store)
        self._observed_health_heads=observed['heads']
        validate_health_observation(self.store,observed,worker=self.worker,health_config=self.health_config,
            account_id=self.coordinator.policy.account_id)

    def _cycle(self):
        # A changing publication is a reason to reread, not a cancellation cause.
        # Exactly two bounded attempts; neither can publish a stale trigger/READY.
        for _ in range(2):
            try:return self._cycle_attempt()
            except GuardianObservationChanged:pass
        head=self._head();d=head['body']['details']
        return self._save(head,status='GATED',reasons=['GUARDIAN_HEALTH_PUBLICATION_CHANGED'],cursor=d['cursor'],
            intents=d['cancel_intents'],pending_trigger=d['pending_trigger'],
            snapshot=self.store.get(d['account_snapshot_id']) if d['pending_trigger'] else None)

    def _cycle_attempt(self):
        previous = self._head(); cursor = previous['body']['details']['cursor'] if previous else ''
        saved = previous['body']['details'] if previous else {}
        # Withdraw the preceding lease before any work which could block or fail.
        head = self._save(previous, status='GATED', reasons=['GUARDIAN_CYCLE_IN_PROGRESS'], cursor=cursor,
            intents=saved.get('cancel_intents'), pending_trigger=saved.get('pending_trigger'),
            snapshot=self.store.get(saved['account_snapshot_id']) if saved.get('pending_trigger') else None)
        if saved.get('pending_trigger'):
            return self._resume(head)  # A recovered health sample cannot revoke durable cancellation.
        failures = []
        self._observed_health_heads=()
        try: self._health()
        except EvidenceError as exc: failures.append(str(exc))
        snapshot = self.coordinator._head(); account = self.coordinator._state(snapshot)
        retained = sorted(pid for pid,i in account['intents'].items()
                          if i['status'] in UNRESOLVED and not i.get('cancel_requested') and managed_opening(i))
        order = [p for p in retained if p > cursor]+[p for p in retained if p <= cursor]
        targets = {}
        for pid in order[:self.policy.maximum_intents]:
            cursor = pid; intent = account['intents'][pid]
            bad = bool(failures)
            if not bad:
                try:
                    admission_heads(self.store, account_id=self.coordinator.policy.account_id, event_id=intent['event_id'],
                                    strategies=tuple(a['strategy'] for a in intent['attribution']))
                    check = self.cancellation.check_admission('guardian-admission:'+uuid.uuid4().hex, intent_id=pid)
                    bad = not check['body']['details']['passed']
                    event = EventRiskEngine(self.store).revalidate(intent['event_state_id'])
                    bad = bad or event['cancellation_status'] == 'REQUESTED_NOT_CONFIRMED'
                except (EvidenceError,KeyError,TypeError,ValueError,IndexError,OverflowError):
                    bad = True
            if bad: targets[pid] = cancel_identity(intent)
        if targets:
            # Refresh the account pin after read-only checks; changed economics
            # will be refused again by the cancellation planner/transaction.
            snapshot = self.coordinator._head()
            key = 'guardian:'+uuid.uuid4().hex
            head = self._save(head, status='GATED', reasons=failures or ['GUARDIAN_RESTING_ADMISSION_GATED'],
                              cursor=cursor, intents=targets, snapshot=snapshot, pending_trigger=key, record_id=key,
                              observed_heads=self._observed_health_heads)
            return self._resume(head)
        # A failed/incomplete delivery never grants a success lease. Retained
        # requests and reservations are observed afresh on restart, without fills.
        return self._save(head, status='GATED' if failures else 'READY', reasons=failures, cursor=cursor,
                          observed_heads=self._observed_health_heads)

    def _resume(self, head):
        saved = head['body']['details']; trigger = saved['pending_trigger']
        plan = self.cancellation.plan('guardian-plan:'+digest(trigger), trigger_id=trigger)
        self.cancellation.advance('guardian-delivery:'+uuid.uuid4().hex, plan_id=plan['id'])
        account = self.coordinator._state(self.coordinator._head())
        for pid, signature in saved['cancel_intents'].items():
            intent = account['intents'].get(pid)
            if intent is None or cancel_identity(intent) != signature:
                raise EvidenceError('GUARDIAN_PENDING_INTENT_IDENTITY_CHANGED')
            if intent['status'] in UNRESOLVED and not intent.get('cancel_requested'):
                return head
        return self._save(head, status='GATED', reasons=['GUARDIAN_CANCEL_REQUESTS_DELIVERED_NOT_CONFIRMED'],
                          cursor=saved['cursor'])

    def run(self, *, cycles):
        if type(cycles) is not int or not 1 <= cycles <= 200 or cycles*self.policy.interval_seconds > 60:
            raise EvidenceError('GUARDIAN_FINITE_RUN_BOUND')
        fd = os.open(str(self.store.path)+'.guardian.lock', os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW, 0o600)
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077 or info.st_uid != os.getuid():
                raise EvidenceError('GUARDIAN_LOCK_CUSTODY')
            try: fcntl.flock(fd, fcntl.LOCK_EX|fcntl.LOCK_NB)
            except BlockingIOError: raise EvidenceError('GUARDIAN_ALREADY_RUNNING') from None
            end = time.monotonic()+60
            for _ in range(cycles):
                if time.monotonic() >= end: raise EvidenceError('GUARDIAN_WALL_TIME_BOUND')
                self._cycle()
                time.sleep(self.policy.interval_seconds)
            head = self._head()
            saved = head['body']['details']
            self._save(head, status='STOPPED', reasons=['GUARDIAN_FINITE_RUN_ENDED'], cursor=saved['cursor'],
                intents=saved['cancel_intents'], pending_trigger=saved['pending_trigger'],
                snapshot=self.store.get(saved['account_snapshot_id']) if saved['pending_trigger'] else None)
        finally: os.close(fd)


def main():
    """One bounded JSON stdin request, no operation dispatch or credential fields."""
    try:
        process_limits()
        signal.signal(signal.SIGALRM, _deadline)
        signal.setitimer(signal.ITIMER_REAL, 65.)
        raw = sys.stdin.buffer.read(MAX_CONFIG_BYTES+1)
        if len(raw) > MAX_CONFIG_BYTES: raise EvidenceError('GUARDIAN_CONFIG_BYTES_BOUND')
        request = json.loads(raw)
        if type(request) is not dict or set(request) != {'config','cycles'}:
            raise EvidenceError('GUARDIAN_REQUEST_SCHEMA')
        canonical(request)  # Refuses secret-shaped input fields without logging them.
        restore(request['config']).run(cycles=request['cycles'])
    except (GuardianDeadline, EvidenceError, OSError, sqlite3.Error, ValueError, TypeError, KeyError, MemoryError, RecursionError):
        print('{"outcome":"FAILED_CLOSED","financial_authority":false}')
        return 2
    print('{"outcome":"FINITE_RUN_ENDED_GATED","financial_authority":false}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
