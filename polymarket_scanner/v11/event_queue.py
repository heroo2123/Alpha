"""Bounded durable source-to-event routing for one nonfinancial worker.

Enqueue uses account-local SQLite CAS. An OS lock serializes evaluation across
processes, including restart; persisted claims alone are not mutual exclusion.
Loss of update coverage requests a full census. No networking or order API.
"""
from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
from dataclasses import asdict, dataclass
import fcntl
import os

from .evidence import EvidenceError, EvidenceStore, canonical, digest, finite, identity


VERSION = 'alpha_v11_event_queue_v1'
KEY = 'v11-event-queue-state'
KINDS = {'BOOK', 'TRADE', 'OFFICIAL_OBSERVATION', 'PWS_OBSERVATION', 'MODEL', 'SCHEDULED_RELEASE'}
PRIORITY = {'SCHEDULED_RELEASE':0, 'OFFICIAL_OBSERVATION':1, 'MODEL':2,
            'PWS_OBSERVATION':3, 'TRADE':4, 'BOOK':5}


@dataclass(frozen=True)
class EventRoute:
    event_id: str
    station: str
    target_date: str
    family: str
    rule_fingerprint: str
    tokens: tuple[str, ...]
    valid_until: float
    required_source_kinds: tuple[str, ...]

    def __post_init__(self):
        from datetime import date
        from .evidence import sha
        for value in (self.event_id, self.station, self.target_date, self.family):
            identity(value)
        try:
            date.fromisoformat(self.target_date)
        except ValueError:
            raise EvidenceError('ROUTE_DATE_INVALID') from None
        sha(self.rule_fingerprint)
        finite(self.valid_until)
        if self.family not in {'daily_high_temperature', 'daily_low_temperature'}:
            raise EvidenceError('ROUTE_FAMILY_INVALID')
        if type(self.tokens) is not tuple or not 1 <= len(self.tokens) <= 64 or len(set(self.tokens)) != len(self.tokens):
            raise EvidenceError('ROUTE_TOKEN_BOUND')
        for token in self.tokens:
            identity(token)
        if (type(self.required_source_kinds) is not tuple
                or len(set(self.required_source_kinds)) != len(self.required_source_kinds)
                or not set(self.required_source_kinds) <= {'MODEL', 'OFFICIAL_OBSERVATION', 'PWS_OBSERVATION'}
                or len(self.tokens)+len(self.required_source_kinds)+1 > 64):
            raise EvidenceError('ROUTE_CENSUS_SOURCE_BOUND')


@dataclass(frozen=True)
class TriggerPolicy:
    version: str
    max_pending_events: int
    max_station_fanout: int
    max_sources_per_event: int
    max_source_channels: int
    max_pending_age_seconds: float
    max_work_seconds: float
    max_state_bytes: int
    max_rule_age_seconds: float
    source_age_seconds: tuple[tuple[str, float], ...]
    pws_station_age_seconds: tuple[tuple[str, float], ...]

    def __post_init__(self):
        identity(self.version)
        for name, maximum in (('max_pending_events', 32), ('max_station_fanout', 16),
                              ('max_sources_per_event', 32), ('max_source_channels', 256),
                              ('max_state_bytes', 512*1024)):
            value = getattr(self, name)
            if type(value) is not int or not 1 <= value <= maximum:
                raise EvidenceError('TRIGGER_RESOURCE_BOUND')
        if (not 0 < finite(self.max_pending_age_seconds) <= 3600 or not 0 < finite(self.max_work_seconds) <= 60
                or not 0 < finite(self.max_rule_age_seconds) <= 86400):
            raise EvidenceError('TRIGGER_TIME_BOUND')
        if (type(self.source_age_seconds) is not tuple or dict(self.source_age_seconds).keys() != KINDS
                or len(self.source_age_seconds) != len(KINDS)):
            raise EvidenceError('TRIGGER_SOURCE_AGE_POLICY')
        if (type(self.pws_station_age_seconds) is not tuple or len(self.pws_station_age_seconds) > 32
                or len(dict(self.pws_station_age_seconds)) != len(self.pws_station_age_seconds)):
            raise EvidenceError('TRIGGER_PWS_STATION_POLICY')
        for key, value in (*self.source_age_seconds, *self.pws_station_age_seconds):
            identity(key)
            if not 0 < finite(value) <= 86400:
                raise EvidenceError('TRIGGER_SOURCE_AGE_POLICY')


class EventQueue:
    def __init__(self, store: EvidenceStore, *, routes: tuple[EventRoute, ...], policy: TriggerPolicy):
        if (type(routes) is not tuple or not 1 <= len(routes) <= 32
                or any(not isinstance(r, EventRoute) for r in routes)
                or len({r.event_id for r in routes}) != len(routes)):
            raise EvidenceError('TRIGGER_ROUTE_BOUND')
        self.store, self.policy = store, policy
        self.routes = {r.event_id:r for r in routes}
        # Scope changes need an explicit new census/reconfiguration procedure.
        # A process restart must not silently forget pending work or losses.
        self.config = digest(dict(routes=[asdict(r) for r in sorted(routes, key=lambda r:r.event_id)], policy=asdict(policy)))
        self._worker = None

    def _read(self):
        row = self.store.latest(kind='RUNTIME_STATUS', event_id=KEY)
        if row:
            value = row['body']['details']
            if value.get('version') != VERSION or value.get('config_sha256') != self.config:
                raise EvidenceError('TRIGGER_CONFIGURATION_CHANGED_REQUIRES_RECONCILIATION')
            state=deepcopy(value['state'])
            state.setdefault('evaluations', {})  # Older local queues must reevaluate, never inherit eligibility.
            return row, state
        return None, dict(pending={}, active=None, channels={}, needs_census={}, evaluations={}, metrics={
            'received':0, 'unmapped':0, 'duplicate':0, 'out_of_order':0, 'coalesced':0,
            'expired':0, 'overflow':0, 'failed':0, 'completed':0, 'abandoned':0})

    def _replay(self, record_id, request):
        try:
            row = self.store.get(record_id)
        except EvidenceError as exc:
            if str(exc) != 'EVIDENCE_MISSING':
                raise
            return None
        d = row['body'].get('details', {})
        if (row['kind'] != 'RUNTIME_STATUS' or row['event_id'] != KEY
                or d.get('version') != VERSION or d.get('config_sha256') != self.config
                or d.get('request') != request):
            raise EvidenceError('TRIGGER_COMMAND_ID_COLLISION')
        return row

    def _commit(self, record_id, request, row, state, result, refs=(), heads=()):
        if len(canonical(state).encode()) > self.policy.max_state_bytes:
            raise EvidenceError('TRIGGER_STATE_BYTES_BOUND')
        return self.store.audit(record_id, event_id=KEY, kind='RUNTIME_STATUS',
                    details=dict(version=VERSION, config_sha256=self.config, request=request, state=state,
                                 result=result, financial_authority=False), evidence_ids=tuple(dict.fromkeys(refs)),
                    expected_previous_seq=row['seq'] if row else 0, expected_heads=heads)

    def snapshot(self):
        return self._read()[1]

    def _expire(self, state, now):
        for event, item in list(state['pending'].items()):
            if not item['first_received_at'] <= now < item['expires_at']:
                state['pending'].pop(event)
                state['metrics']['expired'] += 1
                state['needs_census'][event] = 'PENDING_UPDATE_EXPIRED'

    def _enqueue(self, state, route, notice, now):
        event = route.event_id
        item = state['pending'].get(event)
        if item is None:
            if len(state['pending']) >= self.policy.max_pending_events:
                state['metrics']['overflow'] += 1
                state['needs_census'][event] = 'EVENT_QUEUE_OVERFLOW'
                return False
            item = dict(event_id=event, station=route.station, rule_fingerprint=route.rule_fingerprint,
                        first_received_at=notice['received_at'], expires_at=min(route.valid_until,
                        now+self.policy.max_pending_age_seconds), priority=notice['priority'], sources={})
            state['pending'][event] = item
        elif notice['channel'] not in item['sources'] and len(item['sources']) >= self.policy.max_sources_per_event:
            state['metrics']['overflow'] += 1
            state['needs_census'][event] = 'EVENT_SOURCE_FANIN_OVERFLOW'
            return False
        else:
            state['metrics']['coalesced'] += 1
        item['sources'][notice['channel']] = notice
        item['priority'] = min(item['priority'], notice['priority'])
        # Frequent changes cannot keep an old pending event alive indefinitely.
        item['expires_at'] = min(item['expires_at'], notice['valid_until'])
        return True

    def publish(self, record_id: str, *, kind: str, evidence_id: str) -> dict:
        if kind not in KINDS:
            raise EvidenceError('TRIGGER_KIND_INVALID')
        request = dict(action='PUBLISH', kind=kind, evidence_id=identity(evidence_id))
        prior = self._replay(record_id, request)
        if prior:
            return prior
        source = self.store.get(evidence_id); body = source['body']
        expected = 'SOURCE_SCHEDULE' if kind == 'SCHEDULED_RELEASE' else kind
        if source['kind'] != expected or body.get('evidence_class') == 'HISTORICAL_AVAILABILITY_UNKNOWN':
            raise EvidenceError('TRIGGER_SOURCE_KIND_OR_AVAILABILITY')
        row, state = self._read(); now = finite(self.store.clock()); self._expire(state, now)
        state['metrics']['received'] += 1
        p = body['details'] if kind == 'SCHEDULED_RELEASE' else body['payload']
        station = p.get('station', p.get('settlement_station_context'))
        candidates = [r for r in self.routes.values() if r.valid_until > now]
        if kind in {'BOOK', 'TRADE'}:
            candidates = [r for r in candidates if p.get('token_id') in r.tokens and source['event_id'] == r.event_id]
        else:
            candidates = [r for r in candidates if r.station == station]
        if kind == 'MODEL':
            candidates = [r for r in candidates if p.get('target_date') == r.target_date and p.get('family') == r.family]
        if kind == 'SCHEDULED_RELEASE':
            if p.get('version') != 'alpha_v11_release_schedule_v1':
                raise EvidenceError('RELEASE_SCHEDULE_REQUIRED')
            due = finite(p.get('reevaluate_at'))
            expected_release = finite(p.get('expected_release_at'))
            schedule_expiry = finite(p.get('valid_until'))
            if not body['available_at'] <= due <= expected_release < schedule_expiry or now < due:
                raise EvidenceError('RELEASE_SCHEDULE_NOT_DUE')
            observed = due
        elif kind == 'MODEL':
            observed = body['issued_at']
        elif kind in {'OFFICIAL_OBSERVATION', 'PWS_OBSERVATION'}:
            observed = body['observed_at']
            if observed is None and isinstance(p.get('observations'), list) and 0 < len(p['observations']) <= 400:
                observed = max(finite(x['observed_at']) for x in p['observations'])
            if kind == 'PWS_OBSERVATION':
                observed = p.get('as_of')
        else:
            observed = body['observed_at']
        age = dict(self.policy.source_age_seconds)[kind]
        if kind == 'PWS_OBSERVATION':
            station_age = dict(self.policy.pws_station_age_seconds).get(station)
            if station_age is None:
                raise EvidenceError('PWS_STATION_TTL_UNCONFIGURED')
            age = min(age, station_age)
        reason = None
        if (observed is None or not 0 <= now-finite(observed) < age
                or kind != 'SCHEDULED_RELEASE' and not 0 <= now-body['available_at'] < age
                or kind == 'SCHEDULED_RELEASE' and now >= schedule_expiry):
            reason = 'SOURCE_STALE_OR_AGE_UNKNOWN'
        if kind == 'PWS_OBSERVATION':
            sensor_ages = p.get('observation_age_seconds')
            if (body['provider'] != 'ALPHA_PWS_QC' or p.get('health') != 'HEALTHY'
                    or not isinstance(sensor_ages, list) or not 1 <= len(sensor_ages) <= 400
                    or observed is None or any(finite(a)+now-observed >= age for a in sensor_ages)):
                reason = 'PWS_FRESH_QC_REQUIRED'
        if kind == 'BOOK' and p.get('stream_healthy') is not True:
            reason = 'BOOK_STREAM_UNSYNCHRONIZED'
        if not candidates:
            state['metrics']['unmapped'] += 1
            reason = reason or 'NO_AFFECTED_REGISTERED_EVENT'
        if len(candidates) > self.policy.max_station_fanout:
            reason = 'STATION_FANOUT_BOUND_REQUIRES_CENSUS'
        provider = body.get('provider', 'SCHEDULE')
        source_identity = body.get('source_identity', source['event_id'])
        channel = digest([kind, provider, source_identity, station,
                          [p.get('target_date'), p.get('family')] if kind == 'MODEL' else p.get('token_id')])
        signature = digest({k:body.get(k) for k in ('provider', 'source_identity', 'revision', 'observed_at',
                                                   'issued_at', 'published_at', 'payload', 'details')})
        previous = state['channels'].get(channel)
        if previous and source['seq'] <= previous['seq']:
            state['metrics']['out_of_order'] += 1
            reason = reason or 'SOURCE_RECEIPT_ALREADY_PROCESSED'
        elif previous and previous['signature'] == signature:
            state['metrics']['duplicate'] += 1
            reason = reason or 'IDENTICAL_SOURCE_UPDATE'
            state['channels'][channel] = dict(seq=source['seq'], signature=signature)
        elif previous is None and len(state['channels']) >= self.policy.max_source_channels:
            reason = 'SOURCE_CHANNEL_BOUND_REQUIRES_CENSUS'
        elif reason is None:
            state['channels'][channel] = dict(seq=source['seq'], signature=signature)
        accepted = []
        if reason is None:
            notice = dict(kind=kind, evidence_id=evidence_id, evidence_sha256=source['sha256'], channel=channel,
                          received_at=now if kind == 'SCHEDULED_RELEASE' else body['available_at'],
                          valid_until=min(observed+age, schedule_expiry if kind == 'SCHEDULED_RELEASE' else body['available_at']+age),
                          priority=PRIORITY[kind])
            for route in sorted(candidates, key=lambda r:r.event_id):
                if self._enqueue(state, route, notice, now):
                    accepted.append(route.event_id)
        elif reason not in {'IDENTICAL_SOURCE_UPDATE', 'SOURCE_RECEIPT_ALREADY_PROCESSED', 'NO_AFFECTED_REGISTERED_EVENT'}:
            state['metrics']['failed'] += 1
            for route in candidates:
                state['needs_census'][route.event_id] = reason
        result = dict(outcome='QUEUED' if accepted else 'NO_WORK', reason=reason or 'AFFECTED_EVENTS_ONLY',
                      affected_events=accepted, received_at=body['available_at'], routed_at=now,
                      receipt_to_route_seconds=now-body['available_at'])
        return self._commit(record_id, request, row, state, result, (evidence_id,))

    def stream_gap(self, record_id: str, *, event_id: str, reason: str) -> dict:
        if event_id not in self.routes:
            raise EvidenceError('UNREGISTERED_EVENT')
        request = dict(action='STREAM_GAP', event_id=event_id, reason=identity(reason))
        prior = self._replay(record_id, request)
        if prior:
            return prior
        row, state = self._read()
        state['needs_census'][event_id] = 'STREAM_GAP:'+reason
        return self._commit(record_id, request, row, state, dict(outcome='FULL_CENSUS_REQUIRED'))

    @contextmanager
    def work(self, record_id: str, *, exclude_events: tuple[str, ...] = ()):
        """Hold the real process lock through evaluation; do not sleep in a poll.

        One worker is deliberately stricter than per-event serialization. New
        updates can enqueue while it runs. A crashed claim is explicit census
        work, never silently treated as a completed decision or external action.
        """
        identity(record_id, maximum=100)
        if type(exclude_events) is not tuple or len(exclude_events) > len(self.routes) or not set(exclude_events) <= self.routes.keys():
            raise EvidenceError('TRIGGER_EXCLUSION_SCOPE')
        if self._worker is not None:
            raise EvidenceError('TRIGGER_WORKER_ALREADY_RUNNING')
        fd = os.open(self.store.path.with_name(self.store.path.name+'.events.lock'),
                     os.O_CREAT | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
        try:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise EvidenceError('TRIGGER_WORKER_ALREADY_RUNNING') from None
            request = dict(action='CLAIM')
            if exclude_events: request['exclude_events'] = list(exclude_events)
            if self._replay(record_id, request):
                raise EvidenceError('CLAIM_ID_ALREADY_USED_READ_DURABLE_RESULT')
            row, state = self._read(); now = finite(self.store.clock()); self._expire(state, now)
            if state['active']:
                lost = state['active']
                state['needs_census'][lost['event_id']] = 'ABANDONED_EVALUATION_REQUIRES_CENSUS'
                state['metrics']['abandoned'] += 1
                state['active'] = None
            ordered = sorted((p for p in state['pending'].values() if p['event_id'] not in exclude_events),
                             key=lambda p:(p['priority'], p['first_received_at'], p['event_id']))
            census = sorted(e for e in state['needs_census'] if self.routes[e].valid_until > now and e not in exclude_events)
            event = ordered[0]['event_id'] if ordered else next(iter(census), None)
            if event is None:
                self._commit(record_id, request, row, state, dict(outcome='IDLE'))
                yield None
                return
            pending = state['pending'].pop(event, None)
            watched = {(kind,event) for kind in ('BOOK','TRADE','MODEL','OFFICIAL_OBSERVATION','PWS_OBSERVATION','RULE_STATE')}
            for notice in pending['sources'].values() if pending else ():
                source = self.store.get(notice['evidence_id'])
                watched.add((source['kind'],source['event_id']))
            heads=[]
            for kind, event_key in sorted(watched):
                head=self.store.latest(kind=kind,event_id=event_key)
                heads.append((kind,event_key,head['seq'] if head else 0))
            claim = dict(claim_id=record_id, event_id=event, claimed_at=now, deadline=now+self.policy.max_work_seconds,
                         sources=list(pending['sources'].values()) if pending else [],
                         requires_full_census=event in state['needs_census'],
                         census_reason=state['needs_census'].get(event),
                         source_to_work_seconds=now-pending['first_received_at'] if pending else None,
                         source_heads=heads, requires_result_after=record_id, coverage_valid_until=None)
            state['active'] = claim
            self._commit(record_id, request, row, state, dict(outcome='CLAIMED', claim=claim),
                         tuple(n['evidence_id'] for n in claim['sources']), tuple(heads))
            self._worker = record_id
            yield deepcopy(claim)
        finally:
            self._worker = None
            os.close(fd)

    def schedule_census(self, record_id: str) -> dict:
        """Periodic reconciliation never clears an existing loss/fault finding."""
        request = dict(action='SCHEDULE_PERIODIC_CENSUS')
        prior = self._replay(record_id, request)
        if prior: return prior
        row, state = self._read(); now = finite(self.store.clock())
        events = sorted(e for e, route in self.routes.items() if route.valid_until > now)
        for event in events:
            state['needs_census'].setdefault(event, 'PERIODIC_FULL_CENSUS_DUE')
            state['evaluations'].pop(event, None)
        return self._commit(record_id, request, row, state, dict(outcome='CENSUS_SCHEDULED', events=events))

    def finish(self, record_id: str, *, claim_id: str, result_ids: tuple[str, ...]) -> dict:
        if self._worker != claim_id:
            raise EvidenceError('FINISH_REQUIRES_HELD_WORKER_LOCK')
        if type(result_ids) is not tuple or not 1 <= len(result_ids) <= 16 or len(set(result_ids)) != len(result_ids):
            raise EvidenceError('TRIGGER_RESULT_BOUND')
        request = dict(action='FINISH', claim_id=claim_id, result_ids=list(result_ids))
        prior = self._replay(record_id, request)
        if prior:
            return prior
        row, state = self._read(); active = state['active']; now = finite(self.store.clock())
        if not active or active['claim_id'] != claim_id:
            raise EvidenceError('TRIGGER_CLAIM_FENCE_CHANGED')
        for key in result_ids:
            output = self.store.get(key)
            if (output['event_id'] != active['event_id'] or output['kind'] not in {'MEASUREMENT', 'DECISION', 'RUNTIME_STATUS'}
                    or output['seq'] <= self.store.get(active['requires_result_after'])['seq']
                    or not active['claimed_at'] <= output['body']['recorded_at'] <= now):
                raise EvidenceError('TRIGGER_RESULT_NOT_FROM_CLAIMED_EVALUATION')
        reason = None
        if not active['claimed_at'] <= now < active['deadline']:
            reason = 'WORK_BUDGET_EXCEEDED_OR_CLOCK_REGRESSED'
        elif active['event_id'] in state['needs_census']:
            reason = 'FULL_CENSUS_STILL_REQUIRED'
        elif active['event_id'] in state['pending']:
            reason = 'NEW_SOURCE_UPDATE_REQUIRES_REEVALUATION'
        elif any(now >= s['valid_until'] for s in active['sources']):
            reason = 'SOURCE_EXPIRED_DURING_EVALUATION'
        elif active['coverage_valid_until'] is not None and now >= active['coverage_valid_until']:
            reason = 'CENSUS_COVERAGE_EXPIRED_DURING_EVALUATION'
        if reason is None:
            for kind,event_key,seq in active['source_heads']:
                head=self.store.latest(kind=kind,event_id=event_key)
                if (head['seq'] if head else 0) != seq:
                    reason='ARCHIVED_SOURCE_CHANGED_DURING_EVALUATION'
                    break
        if reason:
            state['metrics']['failed'] += 1
            state['evaluations'].pop(active['event_id'], None)
            if reason != 'NEW_SOURCE_UPDATE_REQUIRES_REEVALUATION':
                state['needs_census'][active['event_id']] = reason
        else:
            state['metrics']['completed'] += 1
            bounds=[active['deadline'], *(s['valid_until'] for s in active['sources'])]
            if active['coverage_valid_until'] is not None:
                bounds.append(active['coverage_valid_until'])
            state['evaluations'][active['event_id']]=dict(completion_id=record_id, result_ids=list(result_ids),
                         source_heads=active['source_heads'], valid_until=min(bounds), completed_at=now)
        state['active'] = None
        return self._commit(record_id, request, row, state, dict(outcome='STALE_RESEARCH_RESULT' if reason else 'RESEARCH_EVALUATED',
                    reason=reason, evaluation_seconds=now-active['claimed_at'], submission_ready_latency=None,
                    downstream_revalidation_required=True), result_ids,
                    tuple(tuple(h) for h in active['source_heads']) if reason is None else ())

    def complete_census(self, record_id: str, *, claim_id: str, book_ids: tuple[str, ...],
                        source_ids: tuple[str, ...], rule_state_id: str) -> dict:
        """Clear update-loss state only while the worker holds serialization.

        This checks newly archived full-book coverage and source availability,
        not a source's truth, strategy eligibility or exchange reconnect protocol.
        The latter remain dedicated admission/transport requirements.
        """
        if self._worker != claim_id:
            raise EvidenceError('CENSUS_REQUIRES_HELD_WORKER_LOCK')
        if (type(book_ids) is not tuple or type(source_ids) is not tuple or not 1 <= len(book_ids) <= 64
                or not 0 <= len(source_ids) <= 16 or len(book_ids)+len(source_ids)+1 > 64
                or len(set(book_ids+source_ids+(rule_state_id,))) != len(book_ids)+len(source_ids)+1):
            raise EvidenceError('CENSUS_INPUT_BOUND')
        request = dict(action='CENSUS', claim_id=claim_id, book_ids=list(book_ids), source_ids=list(source_ids),
                       rule_state_id=rule_state_id)
        prior = self._replay(record_id, request)
        if prior:
            return prior
        row, state = self._read(); active = state['active']; now = finite(self.store.clock())
        if not active or active['claim_id'] != claim_id or not active['claimed_at'] <= now < active['deadline']:
            raise EvidenceError('CENSUS_CLAIM_EXPIRED_OR_CHANGED')
        route = self.routes[active['event_id']]
        if now >= route.valid_until:
            raise EvidenceError('CENSUS_ROUTE_EXPIRED')
        heads=[]
        for kind in ('BOOK','RULE_STATE','TRADE','MODEL','OFFICIAL_OBSERVATION','PWS_OBSERVATION'):
            head=self.store.latest(kind=kind,event_id=route.event_id)
            heads.append((kind,route.event_id,head['seq'] if head else 0))
        tokens, expiries = set(), [route.valid_until]
        for key in book_ids:
            source = self.store.get(key); body = source['body']; p = body.get('payload', {})
            if (source['kind'] != 'BOOK' or source['event_id'] != route.event_id
                    or source['seq'] <= self.store.get(claim_id)['seq']
                    or body['evidence_class'] == 'HISTORICAL_AVAILABILITY_UNKNOWN'
                    or p.get('snapshot_type') != 'FULL' or p.get('stream_healthy') is not True
                    or p.get('rule_fingerprint') != route.rule_fingerprint
                    or p.get('token_id') not in route.tokens or p.get('token_id') in tokens
                    or body['observed_at'] is None or not 0 <= now-body['observed_at'] < dict(self.policy.source_age_seconds)['BOOK']):
                raise EvidenceError('CENSUS_NEW_FULL_BOOK_REQUIRED')
            latest = self.store.latest_source(kind='BOOK', event_id=route.event_id, provider=body['provider'], source_identity=body['source_identity'])
            if latest['id'] != key:
                raise EvidenceError('CENSUS_BOOK_SUPERSEDED')
            tokens.add(p['token_id'])
            expiries.extend([body['observed_at']+dict(self.policy.source_age_seconds)['BOOK'],
                             body['available_at']+dict(self.policy.source_age_seconds)['BOOK']])
        if tokens != set(route.tokens):
            raise EvidenceError('CENSUS_ALL_EVENT_TOKENS_REQUIRED')
        covered_sources = set()
        for key in source_ids:
            source = self.store.get(key); body = source['body']
            if (source['event_id'] != route.event_id or source['kind'] not in KINDS-{'BOOK', 'TRADE', 'SCHEDULED_RELEASE'}
                    or body['evidence_class'] == 'HISTORICAL_AVAILABILITY_UNKNOWN'
                    or source['seq'] <= self.store.get(claim_id)['seq']):
                raise EvidenceError('CENSUS_NEW_SOURCE_EVIDENCE_REQUIRED')
            payload = body['payload']
            if payload.get('station', payload.get('settlement_station_context')) != route.station:
                raise EvidenceError('CENSUS_SOURCE_STATION_MISMATCH')
            observed = body['issued_at'] if source['kind'] == 'MODEL' else body['observed_at']
            if source['kind'] == 'MODEL' and (payload.get('target_date') != route.target_date or payload.get('family') != route.family):
                raise EvidenceError('CENSUS_MODEL_TARGET_MISMATCH')
            if source['kind'] == 'OFFICIAL_OBSERVATION' and observed is None:
                readings = payload.get('observations', [])
                if not isinstance(readings, list) or not 1 <= len(readings) <= 400:
                    raise EvidenceError('CENSUS_OFFICIAL_READING_REQUIRED')
                observed = max(finite(x['observed_at']) for x in readings)
            age = dict(self.policy.source_age_seconds)[source['kind']]
            if source['kind'] == 'PWS_OBSERVATION':
                observed = payload.get('as_of')
                age = min(age, dict(self.policy.pws_station_age_seconds).get(route.station, 0))
                sensor_ages = payload.get('observation_age_seconds')
                if (body['provider'] != 'ALPHA_PWS_QC' or payload.get('health') != 'HEALTHY'
                        or not isinstance(sensor_ages, list) or not 1 <= len(sensor_ages) <= 400
                        or observed is None or any(finite(a)+now-observed >= age for a in sensor_ages)):
                    raise EvidenceError('CENSUS_PWS_FRESH_QC_REQUIRED')
            if observed is None or not 0 <= now-finite(observed) < age or not 0 <= now-body['available_at'] < age:
                raise EvidenceError('CENSUS_SOURCE_NOT_FRESH')
            latest = self.store.latest_source(kind=source['kind'], event_id=route.event_id, provider=body['provider'], source_identity=body['source_identity'])
            if latest['id'] != key:
                raise EvidenceError('CENSUS_SOURCE_SUPERSEDED')
            expiries.extend([observed+age,body['available_at']+age])
            covered_sources.add(source['kind'])
        if not set(route.required_source_kinds) <= covered_sources:
            raise EvidenceError('CENSUS_REQUIRED_SOURCE_COVERAGE_MISSING')
        rules = self.store.get(rule_state_id)
        d = rules['body'].get('details', {})
        if (rules['kind'] != 'RULE_STATE' or rules['event_id'] != route.event_id
                or d.get('fingerprint') != route.rule_fingerprint or d.get('quarantined') is not False
                or not 0 <= now-rules['body']['recorded_at'] < self.policy.max_rule_age_seconds):
            raise EvidenceError('CENSUS_CURRENT_STABLE_RULE_REQUIRED')
        if self.store.latest(kind='RULE_STATE', event_id=route.event_id)['id'] != rule_state_id:
            raise EvidenceError('CENSUS_RULE_SUPERSEDED')
        expiries.append(rules['body']['recorded_at']+self.policy.max_rule_age_seconds)
        state['needs_census'].pop(route.event_id, None)
        state['active']['requires_full_census'] = False
        state['active']['source_heads'] = heads
        state['active']['sources'] = []
        state['active']['coverage_valid_until'] = min(expiries)
        state['active']['requires_result_after'] = record_id
        # New arrival notifications are retained; completing the census cannot
        # erase a later queued observation or grant authority to old decisions.
        details = dict(version=VERSION, config_sha256=self.config, request=request, state=state,
                       result=dict(outcome='CENSUS_SOURCE_COVERAGE_ONLY', financial_authority=False))
        return self.store.audit(record_id, event_id=KEY, kind='RUNTIME_STATUS', details=details,
                    evidence_ids=book_ids+source_ids+(rule_state_id,), expected_previous_seq=row['seq'],
                    expected_heads=tuple(heads))


def admission_heads(store: EvidenceStore, *, event_id: str, valuation_id: str) -> dict:
    """Data gate for paper reservation and its submission-state transition.

    Offline tests/periodic evaluators may have no queue installed. Absence is
    itself guarded atomically, so a queue appearing with faults during admission
    cannot be ignored. Once present, its current event evaluation is mandatory.
    This does not commission any financially active runtime.
    """
    row=store.latest(kind='RUNTIME_STATUS',event_id=KEY)
    heads=[('RUNTIME_STATUS',KEY,row['seq'] if row else 0)]
    if row is None:
        return dict(heads=heads, completion_id=None, valid_until=None, financial_authority=False)
    details=row['body']['details']
    if details.get('version') != VERSION:
        raise EvidenceError('EVENT_QUEUE_STATE_VERSION_UNKNOWN')
    state=details['state']
    if event_id in state['needs_census']:
        raise EvidenceError('EVENT_QUEUE_REQUIRES_CENSUS')
    if event_id in state['pending'] or state['active'] and state['active']['event_id']==event_id:
        raise EvidenceError('EVENT_QUEUE_REEVALUATION_PENDING')
    value=state.get('evaluations',{}).get(event_id)
    now=finite(store.clock())
    if value is None or not value['completed_at'] <= now < value['valid_until']:
        raise EvidenceError('EVENT_QUEUE_CURRENT_EVALUATION_REQUIRED')
    matched=False
    for key in value['result_ids']:
        output=store.get(key)
        if output['event_id'] != event_id:
            raise EvidenceError('EVENT_QUEUE_EVALUATION_SCOPE_INVALID')
        p=output['body'].get('details',{}).get('proposal')
        matched |= key==valuation_id or isinstance(p,dict) and p.get('valuation_id')==valuation_id
    if not matched:
        raise EvidenceError('EVENT_QUEUE_VALUATION_NOT_EVALUATED')
    for kind,event_key,seq in value['source_heads']:
        head=store.latest(kind=kind,event_id=event_key)
        if (head['seq'] if head else 0) != seq:
            raise EvidenceError('EVENT_QUEUE_SOURCE_CHANGED_REEVALUATE')
        heads.append((kind,event_key,seq))
    return dict(heads=heads, completion_id=value['completion_id'], valid_until=value['valid_until'], financial_authority=False)
