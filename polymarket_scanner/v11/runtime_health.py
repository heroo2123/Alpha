"""Receipt-bound PAPER runtime health. No service mutation or financial authority.

Local sync status is read through timedated; unavailable status fails closed.
Synthetic probes are dependency-injected only in off-host mechanical tests.
This same-process monitor is not the independently commissioned live guardian.
"""
from copy import copy
from dataclasses import asdict, dataclass
from pathlib import Path
import re
import subprocess
import time

from .evidence import EvidenceError, canonical, digest, finite, identity


VERSION = 'alpha_v11_runtime_health_v1'
KEY = 'v11-runtime-health'


def host_stamp(store):
    try: boot = Path('/proc/sys/kernel/random/boot_id').read_text().strip()
    except OSError: boot = 'UNKNOWN'
    if not re.fullmatch(r'[0-9a-f-]{36}', boot): boot = 'UNKNOWN'
    return dict(boot_id=boot, monotonic=finite(time.monotonic()), wall=finite(store.clock()))


def local_sync_status():
    """Fixed read-only command, no sudo, shell, time setting or service action."""
    try:
        result = subprocess.run(['/usr/bin/timedatectl', 'show', '--property=NTPSynchronized', '--value'],
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            timeout=2, check=False, env={'PATH':'/usr/bin:/bin', 'LC_ALL':'C', 'SYSTEMD_PAGER':'cat'})
        raw = result.stdout
        if result.returncode != 0 or raw not in {b'yes\n', b'no\n'}:
            return dict(synchronized=None, mechanism='LOCAL_SYSTEMD_TIMEDATED', reason='SYNC_STATUS_UNAVAILABLE', offset_seconds=None)
        return dict(synchronized=raw == b'yes\n', mechanism='LOCAL_SYSTEMD_TIMEDATED',
                    reason='SYNC_STATUS_OBSERVED', offset_seconds=None)
    except (OSError, subprocess.TimeoutExpired):
        return dict(synchronized=None, mechanism='LOCAL_SYSTEMD_TIMEDATED', reason='SYNC_STATUS_UNAVAILABLE', offset_seconds=None)


@dataclass(frozen=True)
class HealthPolicy:
    version: str
    maximum_sample_age_seconds: float
    heartbeat_age_seconds: float
    maximum_wall_step_seconds: float
    recovery_samples: int
    recovery_spacing_seconds: float
    workers: tuple[str, ...]

    def __post_init__(self):
        identity(self.version)
        for v in (self.maximum_sample_age_seconds, self.heartbeat_age_seconds,
                  self.maximum_wall_step_seconds, self.recovery_spacing_seconds):
            if not 0 < finite(v) <= 300:
                raise EvidenceError('RUNTIME_HEALTH_POLICY_BOUND')
        if (type(self.recovery_samples) is not int or not 2 <= self.recovery_samples <= 20
                or type(self.workers) is not tuple or not 1 <= len(self.workers) <= 8
                or len(set(self.workers)) != len(self.workers)):
            raise EvidenceError('RUNTIME_HEALTH_POLICY_BOUND')
        for worker in self.workers: identity(worker)


@dataclass(frozen=True)
class SourceNeed:
    event_id: str
    strategy: str
    kind: str
    provider: str
    source_identity: str
    maximum_age_seconds: float

    def __post_init__(self):
        for val in (self.event_id, self.strategy, self.provider, self.source_identity): identity(val)
        if self.kind not in {'BOOK','MODEL','OFFICIAL_OBSERVATION','PWS_OBSERVATION'} or not 0 < finite(self.maximum_age_seconds) <= 86400:
            raise EvidenceError('RUNTIME_SOURCE_REQUIREMENT_INVALID')


def _worker_key(worker):
    return 'v11-worker:'+digest(worker)


def read_health_snapshot(store, *, workers=None):
    """Bounded coherent observation; callers retain exact heads for write CAS.

    Malformed health retains its head and an error, so a guardian can fence a
    failing observation before publishing cancellation. A missing health row is
    also fenced. No archive sequence guard is needed for unrelated telemetry.
    """
    def names(values):
        if (type(values) not in (tuple, list) or not 1 <= len(values) <= 8
                or any(type(v) is not str for v in values) or len(set(values)) != len(values)):
            raise EvidenceError('RUNTIME_HEALTH_WORKER_SCHEMA')
        for value in values: identity(value)
        return tuple(values)
    selected = names(workers) if workers is not None else ()
    with store._connect() as db:
        if not db.in_transaction: db.execute('BEGIN')
        raw = db.execute('SELECT * FROM v11_records WHERE kind=? AND event_id=? ORDER BY seq DESC LIMIT 1',
                         ('RUNTIME_STATUS', KEY)).fetchone()
        row = store._decode(raw) if raw is not None else None
        error = None
        if row is not None:
            d = row['body'].get('details', {})
            try:
                configured = names(d['policy']['workers'])
                declared = d['workers']
                if (type(declared) is not list or len(declared) != len(configured)
                        or any(type(w) is not dict for w in declared)
                        or tuple(w.get('worker') for w in declared) != configured):
                    raise EvidenceError('RUNTIME_HEALTH_WORKER_SCHEMA')
                if workers is None: selected = configured
            except (EvidenceError, KeyError, TypeError):
                error = 'RUNTIME_HEALTH_WORKER_SCHEMA'
                if workers is None:
                    try: selected = names(d['policy']['workers'])
                    except (EvidenceError, KeyError, TypeError): selected = ()
        heads = [('RUNTIME_STATUS', KEY, row['seq'] if row else 0)]
        observed = {}
        for worker in selected:
            raw = db.execute('SELECT * FROM v11_records WHERE kind=? AND event_id=? ORDER BY seq DESC LIMIT 1',
                             ('RUNTIME_STATUS', _worker_key(worker))).fetchone()
            h = store._decode(raw) if raw is not None else None
            observed[worker] = h
            heads.append(('RUNTIME_STATUS', _worker_key(worker), h['seq'] if h else 0))
        high = db.execute('SELECT MAX(recorded_at) FROM v11_records').fetchone()[0]
    return dict(row=row, workers=observed, archive_high=high, heads=tuple(heads), error=error)


def _source_status(store, need, now):
    try:
        return _source_status_unchecked(store, need, now)
    except (EvidenceError, KeyError, TypeError, ValueError):
        return dict(requirement=asdict(need), record_id=None, reason='REQUIRED_SOURCE_INVALID',
                    valid_until=None, source_head=None)


def _source_status_unchecked(store, need, now):
    row = store.latest_source(kind=need.kind, event_id=need.event_id, provider=need.provider,
                              source_identity=need.source_identity)
    result = dict(requirement=asdict(need), record_id=None, reason=None, valid_until=None, source_head=None)
    if row is None:
        result['reason'] = 'REQUIRED_SOURCE_MISSING'; return result
    b = row['body']; p = b['payload']; stamp = b['issued_at'] if need.kind == 'MODEL' else b['observed_at']
    result.update(record_id=row['id'], source_head=[need.kind, need.event_id,
                  store.latest(kind=need.kind, event_id=need.event_id)['seq']])
    if need.kind=='MODEL':
        from .gefs_sources import current_path_heads
        result['dependency_heads']=current_path_heads(store,row)
    if need.kind == 'PWS_OBSERVATION':
        stamp = p.get('as_of'); ages = p.get('observation_age_seconds')
        if (need.provider != 'ALPHA_PWS_QC' or p.get('health') != 'HEALTHY'
                or not isinstance(ages, list) or not 1 <= len(ages) <= 400):
            result['reason'] = 'REQUIRED_PWS_QC_UNHEALTHY'
        elif stamp is not None:
            stamp = finite(stamp)-max(finite(a) for a in ages)
            from .pws_quality import current_neighborhood_heads
            result['dependency_heads']=current_neighborhood_heads(store,row)
    if need.kind == 'OFFICIAL_OBSERVATION' and stamp is None:
        readings = p.get('observations')
        if isinstance(readings, list) and 1 <= len(readings) <= 400:
            stamp = max(finite(r['observed_at']) for r in readings)
    if b.get('evidence_class') == 'HISTORICAL_AVAILABILITY_UNKNOWN' or stamp is None:
        result['reason'] = 'SOURCE_AVAILABILITY_OR_TIME_UNKNOWN'
    elif not 0 <= now-finite(stamp) < need.maximum_age_seconds or not 0 <= now-b['available_at'] < need.maximum_age_seconds:
        result['reason'] = 'REQUIRED_SOURCE_STALE_OR_FUTURE'
    else:
        result['valid_until'] = min(stamp, b['available_at'])+need.maximum_age_seconds
    if need.kind == 'BOOK' and p.get('stream_healthy') is not True:
        result['reason'] = 'REQUIRED_BOOK_UNSYNCHRONIZED'
    return result


class RuntimeHealth:
    def __init__(self, store, policy, *, account_id, scopes, sources, sync_probe=local_sync_status):
        if not isinstance(policy, HealthPolicy): raise EvidenceError('HEALTH_POLICY_REQUIRED')
        if (type(sources) is not tuple or not 1 <= len(sources) <= 32
                or any(not isinstance(n, SourceNeed) for n in sources)
                or not isinstance(scopes, dict) or not 1 <= len(scopes) <= 16):
            raise EvidenceError('HEALTH_SCOPE_BOUND')
        for event, strategies in scopes.items():
            identity(event)
            if type(strategies) is not tuple or not 1 <= len(strategies) <= 8 or len(set(strategies)) != len(strategies):
                raise EvidenceError('HEALTH_STRATEGY_SCOPE_BOUND')
            for strategy in strategies:
                identity(strategy)
                if not any(n.event_id == event and n.strategy in {'*', strategy} for n in sources):
                    raise EvidenceError('HEALTH_SOURCE_COVERAGE_REQUIRED')
        if len(set(sources)) != len(sources) or any(n.event_id not in scopes or n.strategy not in ('*', *scopes[n.event_id]) for n in sources):
            raise EvidenceError('HEALTH_SOURCE_SCOPE_MISMATCH')
        self.store, self.policy, self.account_id = store, policy, identity(account_id)
        self.scopes, self.sources, self.sync_probe = dict(scopes), sources, sync_probe
        self.config = digest(dict(policy=asdict(policy), account_id=account_id, scopes=scopes, sources=[asdict(s) for s in sources]))

    def heartbeat(self, key, *, worker, generation):
        if worker not in self.policy.workers: raise EvidenceError('UNCONFIGURED_WORKER')
        request = dict(action='HEARTBEAT', worker=worker, generation=identity(generation))
        prior = self._replay(key, request)
        if prior: return prior
        stamp = host_stamp(self.store); head = self.store.latest(kind='RUNTIME_STATUS', event_id=_worker_key(worker))
        return self.store.safety_audit(key, event_id=_worker_key(worker), kind='RUNTIME_STATUS', details=dict(
            version=VERSION, config_sha256=self.config, request=request, stamp=stamp,
            worker=worker, generation=generation, financial_authority=False), expected_previous_seq=head['seq'] if head else 0)

    def _replay(self, key, request):
        identity(key)
        try: row = self.store.get(key)
        except EvidenceError as exc:
            if str(exc) == 'EVIDENCE_MISSING': return None
            raise
        d = row['body'].get('details', {})
        if d.get('version') != VERSION or d.get('config_sha256') != self.config or d.get('request') != request:
            raise EvidenceError('RUNTIME_HEALTH_REPLAY_CONFLICT')
        return row

    def sample(self, key):
        request = dict(action='SAMPLE')
        prior = self._replay(key, request)
        if prior: return prior
        sync = self.sync_probe()
        return self._sample(key, request=request, sync=sync)

    def _publication_replay(self, key, heartbeat_key, worker, generation):
        request = dict(action='SAMPLE', heartbeat_key=heartbeat_key, worker=worker, generation=generation)
        heartbeat = dict(action='HEARTBEAT', worker=worker, generation=generation)
        with self.store._connect() as db:
            if not db.in_transaction: db.execute('BEGIN')
            rows = {r['record_id']: self.store._decode(r) for r in db.execute(
                'SELECT * FROM v11_records WHERE record_id IN (?,?)', (key, heartbeat_key)).fetchall()}
        if not rows: return None
        if set(rows) != {key, heartbeat_key}: raise EvidenceError('RUNTIME_HEALTH_INCOMPLETE_PUBLICATION')
        for record, expected, event in ((rows[key], request, KEY), (rows[heartbeat_key], heartbeat, _worker_key(worker))):
            d = record['body'].get('details', {})
            if (record['kind'] != 'RUNTIME_STATUS' or record['event_id'] != event
                    or d.get('version') != VERSION or d.get('config_sha256') != self.config or d.get('request') != expected):
                raise EvidenceError('RUNTIME_HEALTH_REPLAY_CONFLICT')
        sample, h = rows[key], rows[heartbeat_key]
        if (sum(w.get('worker') == worker and w.get('record_id') == h['id'] for w in sample['body']['details']['workers']) != 1
                or dict(id=h['id'], sha256=h['sha256']) not in sample['body']['evidence']):
            raise EvidenceError('RUNTIME_HEALTH_PUBLICATION_BINDING')
        return sample

    def publish(self, key, *, heartbeat_key, worker, generation):
        """Atomically publish one worker heartbeat and its original health sample."""
        identity(key); identity(heartbeat_key); identity(generation)
        if key == heartbeat_key: raise EvidenceError('RUNTIME_HEALTH_PUBLICATION_KEYS')
        if worker not in self.policy.workers: raise EvidenceError('UNCONFIGURED_WORKER')
        prior = self._publication_replay(key, heartbeat_key, worker, generation)
        if prior: return prior
        sync = self.sync_probe()  # No external probe while holding SQLite's writer lock.
        with self.store.runtime_health_publication() as bound:
            monitor = copy(self); monitor.store = bound
            prior = monitor._publication_replay(key, heartbeat_key, worker, generation)
            if prior: return prior
            monitor.heartbeat(heartbeat_key, worker=worker, generation=generation)
            return monitor._sample(key, request=dict(action='SAMPLE', heartbeat_key=heartbeat_key,
                worker=worker, generation=generation), sync=sync)

    def _sample(self, key, *, request, sync):
        view = read_health_snapshot(self.store, workers=self.policy.workers)
        if view['error']: raise EvidenceError(view['error'])
        previous = view['row']
        old = previous['body']['details'] if previous else None
        if old and old.get('config_sha256') != self.config:
            raise EvidenceError('RUNTIME_HEALTH_CONFIG_CHANGED_REVIEW_REQUIRED')
        # Compare the archive observed before this stamp; a concurrent healthy
        # append must not make an earlier local timestamp look like rollback.
        stamp = host_stamp(self.store); failures = []; refs = []; heads = []
        if stamp['boot_id'] == 'UNKNOWN': failures.append('LOCAL_BOOT_ID_UNAVAILABLE')
        if (not isinstance(sync, dict) or sync.get('synchronized') is not True
                or sync.get('mechanism') not in {'LOCAL_SYSTEMD_TIMEDATED','SYNTHETIC_OFF_HOST_FIXTURE'}):
            failures.append('CLOCK_SYNC_UNVERIFIED')
        archive_high = view['archive_high']
        stable = 0; counted = None; high = max(stamp['wall'], old['wall_high_water'] if old else stamp['wall'], archive_high or 0)
        if stamp['wall'] < high: failures.append('EVIDENCE_CLOCK_HIGH_WATER_AHEAD')
        if old:
            p = old['stamp']
            if p['boot_id'] != stamp['boot_id']: failures.append('CLOCK_BOOT_CHANGED')
            elif stamp['monotonic'] < p['monotonic']: failures.append('MONOTONIC_REGRESSION')
            elif abs((stamp['wall']-p['wall'])-(stamp['monotonic']-p['monotonic'])) > self.policy.maximum_wall_step_seconds:
                failures.append('WALL_MONOTONIC_DISCONTINUITY')
            if stamp['wall'] < old['wall_high_water']: failures.append('WALL_BELOW_RECORDED_HIGH_WATER')
        if not failures:
            stable = old['stable_samples'] if old and old['stamp']['boot_id'] == stamp['boot_id'] else 0
            counted = old['last_stable_monotonic'] if stable else None
            if counted is None or stamp['monotonic']-counted >= self.policy.recovery_spacing_seconds:
                stable = min(self.policy.recovery_samples, stable+1); counted = stamp['monotonic']
            if stable < self.policy.recovery_samples: failures.append('CLOCK_RECOVERY_SAMPLES_PENDING')
        clock_reasons = list(failures)
        worker_status = []
        for worker in self.policy.workers:
            row = view['workers'][worker]; reason = 'WORKER_HEARTBEAT_MISSING'
            heads.append(('RUNTIME_STATUS', _worker_key(worker), row['seq'] if row else 0))
            if row:
                d = row['body']['details']; s = d.get('stamp', {}); refs.append(row['id'])
                reason = None if (d.get('config_sha256') == self.config and s.get('boot_id') == stamp['boot_id']
                     and 0 <= stamp['monotonic']-s.get('monotonic', -1) < self.policy.heartbeat_age_seconds
                     and 0 <= stamp['wall']-s.get('wall', -1) < self.policy.heartbeat_age_seconds) else 'WORKER_HEARTBEAT_STALE_OR_IDENTITY_CHANGED'
            if reason: failures.append(reason+':'+worker)
            worker_status.append(dict(worker=worker, reason=reason, record_id=row['id'] if row else None))
        account = self.store.latest(kind='COORDINATOR_EVENT', event_id='v11-paper-account-state')
        if account and account['body']['details'].get('state', {}).get('account_id') != self.account_id:
            failures.append('ACCOUNT_IDENTITY_MISMATCH')
        if account and account['body']['details'].get('state', {}).get('faults'):
            failures.append('ACCOUNT_RECONCILIATION_FAULT')
        sources = [_source_status(self.store, n, stamp['wall']) for n in self.sources]
        # Health-only source state is rechecked again at account/maker admission.
        refs += [s['record_id'] for s in sources if s['record_id']]
        valid = stamp['wall']+self.policy.maximum_sample_age_seconds
        details = dict(version=VERSION, config_sha256=self.config, request=request, account_id=self.account_id,
            policy=asdict(self.policy), scopes=self.scopes, stamp=stamp, sync=sync,
            wall_high_water=high, stable_samples=stable, last_stable_monotonic=counted,
        clock_reasons=clock_reasons, global_reasons=sorted(set(failures)), workers=worker_status, sources=sources,
            valid_until=valid, monotonic_valid_until=stamp['monotonic']+self.policy.maximum_sample_age_seconds,
            financial_authority=False, independent_guardian_commissioned=False)
        return self.store.safety_audit(key, event_id=KEY, kind='RUNTIME_STATUS', details=details,
            evidence_ids=tuple(dict.fromkeys(refs)), expected_previous_seq=previous['seq'] if previous else 0,
            expected_heads=tuple(heads))


def admission_heads(store, *, account_id, event_id, strategies):
    """Once installed, current health is mandatory at every paper opening CAS."""
    view = read_health_snapshot(store)
    row = view['row']; heads = list(view['heads'])
    if row is None: return tuple(heads)  # Standalone offline components; absence is fenced.
    if view['error']: raise EvidenceError(view['error'])
    d = row['body']['details']; stamp = host_stamp(store)
    if (d.get('version') != VERSION or d.get('account_id') != account_id
            or event_id not in d.get('scopes', {}) or not strategies
            or not set(strategies) <= set(d['scopes'][event_id])):
        raise EvidenceError('RUNTIME_HEALTH_SCOPE_OR_VERSION')
    if (d['global_reasons'] or stamp['boot_id'] != d['stamp']['boot_id']
            or not d['stamp']['wall'] <= stamp['wall'] < d['valid_until']
            or not d['stamp']['monotonic'] <= stamp['monotonic'] < d['monotonic_valid_until']
            or abs((stamp['wall']-d['stamp']['wall'])-(stamp['monotonic']-d['stamp']['monotonic'])) > d['policy']['maximum_wall_step_seconds']):
        raise EvidenceError('RUNTIME_CLOCK_OR_LIVENESS_GATED')
    for worker in d['workers']:
        h = view['workers'][worker['worker']]
        if not h or h['id'] != worker['record_id']: raise EvidenceError('RUNTIME_HEARTBEAT_CHANGED_RESAMPLE')
        s = h['body']['details']['stamp']
        if not 0 <= stamp['monotonic']-s['monotonic'] < d['policy']['heartbeat_age_seconds']:
            raise EvidenceError('RUNTIME_HEARTBEAT_EXPIRED')
    for source in d['sources']:
        n = SourceNeed(**source['requirement'])
        if n.event_id != event_id or n.strategy not in {'*', *strategies}: continue
        current = _source_status(store, n, stamp['wall'])
        if source['reason'] or current['reason'] or current['record_id'] != source['record_id']:
            raise EvidenceError('RUNTIME_REQUIRED_SOURCE_GATED_OR_CHANGED')
        heads.append(tuple(current['source_head']))
        heads.extend(tuple(h) for h in current.get('dependency_heads',()))
    unique = {}
    for kind, event, seq in heads:
        if (kind,event) in unique and unique[kind,event] != seq: raise EvidenceError('RUNTIME_HEALTH_HEAD_RACE')
        unique[kind,event] = seq
    return tuple((k,e,s) for (k,e),s in unique.items())


def cancellation_required(details, event_id, strategies):
    if details['global_reasons'] or event_id not in details['scopes']:
        return True
    return any(s['reason'] and s['requirement']['event_id'] == event_id
               and s['requirement']['strategy'] in {'*', *strategies} for s in details['sources'])
