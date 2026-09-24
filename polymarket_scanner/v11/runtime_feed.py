"""Bounded durable receipt delivery from the existing archive into EventQueue.

No refetch, re-dating, model execution, orders, fills or historical-availability
upgrade. A crash between queue delivery and cursor commit replays the same ID.
"""
from copy import deepcopy
from dataclasses import asdict, dataclass
import fcntl
import os
import time

from .evidence import EvidenceError, canonical, digest, finite, identity


VERSION = 'alpha_v11_runtime_feed_v1'
KEY = 'v11-runtime-receipt-feed'
CHANNELS = ('OFFICIAL_OBSERVATION','PWS_OBSERVATION','MODEL','BOOK','TRADE','SOURCE_SCHEDULE')


@dataclass(frozen=True)
class FeedPolicy:
    version: str
    maximum_reads: int = 48
    maximum_schedules: int = 32
    maximum_seconds: float = 3.

    def __post_init__(self):
        identity(self.version)
        if (type(self.maximum_reads) is not int or not 1 <= self.maximum_reads <= 128
                or type(self.maximum_schedules) is not int or not 1 <= self.maximum_schedules <= 64
                or not 0 < finite(self.maximum_seconds) <= 10):
            raise EvidenceError('RECEIPT_FEED_POLICY_BOUND')


class EvidenceFeed:
    def __init__(self, queue, policy):
        if not isinstance(policy, FeedPolicy): raise EvidenceError('RECEIPT_FEED_POLICY_REQUIRED')
        self.queue, self.store, self.policy = queue, queue.store, policy
        self.config = digest(dict(queue=queue.config, policy=asdict(policy), namespace=self.store.namespace))

    def _head(self):
        row = self.store.latest(kind='RUNTIME_STATUS', event_id=KEY)
        if row and row['body']['details'].get('config_sha256') != self.config:
            raise EvidenceError('RECEIPT_FEED_CONFIG_CHANGED_REVIEW_REQUIRED')
        return row

    def _save(self, key, state, **result):
        head = self._head()
        return self.store.audit(key, event_id=KEY, kind='RUNTIME_STATUS', details=dict(
            version=VERSION, config_sha256=self.config, state=state, result=result,
            financial_authority=False, original_receipts_preserved=True), expected_previous_seq=head['seq'] if head else 0)

    @staticmethod
    def _classification(row):
        b = row['body']; kind = row['kind']; p = b.get('payload', {})
        if kind == 'SOURCE_SCHEDULE':
            return 'SCHEDULED_RELEASE' if b.get('details', {}).get('version') == 'alpha_v11_release_schedule_v1' else 'NOT_RELEASE_SCHEDULE'
        if b.get('evidence_class') == 'HISTORICAL_AVAILABILITY_UNKNOWN': return 'HISTORICAL_AVAILABILITY_UNKNOWN'
        if p.get('source_time_status') == 'NOT_YET_NORMALIZED': return 'RAW_NORMALIZATION_REQUIRED'
        if kind == 'PWS_OBSERVATION' and (b.get('provider') != 'ALPHA_PWS_QC' or p.get('health') != 'HEALTHY'):
            return 'PWS_QC_REQUIRED'
        if kind == 'TRADE' and p.get('record_type') in {'PAPER_FILL','PAPER_TERMINAL'}:
            return 'ACCOUNT_RECEIPT_NOT_PUBLIC_MARKET_TRADE'
        return kind

    def drain(self, command_id):
        identity(command_id, maximum=100)
        final = 'feed:'+digest(command_id)
        try: prior = self.store.get(final)
        except EvidenceError as exc:
            if str(exc) != 'EVIDENCE_MISSING': raise
        else:
            if prior['body']['details'].get('config_sha256') != self.config: raise EvidenceError('RECEIPT_FEED_REPLAY_CONFIG')
            return prior
        fd = os.open(self.store.path.with_name(self.store.path.name+'.feed.lock'),os.O_CREAT|os.O_WRONLY|os.O_NOFOLLOW,0o600)
        try:
            try: fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
            except BlockingIOError: raise EvidenceError('RECEIPT_FEED_ALREADY_RUNNING') from None
            return self._drain(command_id,final)
        finally: os.close(fd)

    def _drain(self, command_id, final):
        deadline = time.monotonic()+self.policy.maximum_seconds
        head = self._head()
        state = deepcopy(head['body']['details']['state']) if head else dict(
            cursors={k:0 for k in CHANNELS}, rotation=0, pending_schedules={},
            delivered=0, skipped=0, retry_attempts=0, deferred=0, attempt=0)
        state['attempt'] += 1; prefix = 'feed-step:'+digest([command_id,state['attempt']]); step = 0; outcomes=[]
        def save(**result):
            nonlocal step
            step += 1; self._save(prefix+':'+str(step), state, **result)
        save(outcome='IN_PROGRESS')
        # Waiting schedules retain receipt identity and never become observations.
        for source_id in list(state['pending_schedules']):
            if time.monotonic() >= deadline: break
            schedule = state['pending_schedules'][source_id]; now = finite(self.store.clock())
            if now < schedule['due']: continue
            try:
                result=self.queue.publish('feed-route:'+digest(source_id),kind='SCHEDULED_RELEASE',evidence_id=source_id)
            except EvidenceError as exc:
                outcomes.append(dict(id=source_id,outcome='DEFERRED',reason=str(exc)));state['retry_attempts']+=1
                save(outcome='SCHEDULE_RETRY_PENDING');continue
            state['pending_schedules'].pop(source_id);state['delivered']+=1
            outcomes.append(dict(id=source_id,outcome='ROUTED',queue_record_id=result['id']))
            save(outcome='SCHEDULE_ROUTED')
        for _ in range(self.policy.maximum_reads):
            if time.monotonic() >= deadline: break
            kind=CHANNELS[state['rotation']];state['rotation']=(state['rotation']+1)%len(CHANNELS)
            rows=self.store.records(kind=kind,after_seq=state['cursors'][kind],limit=1)
            if not rows: continue
            source=rows[0];classification=self._classification(source);source_id=source['id']
            if classification=='SCHEDULED_RELEASE':
                if len(state['pending_schedules'])>=self.policy.maximum_schedules:
                    state['deferred']+=1;outcomes.append(dict(id=source_id,outcome='DEFERRED',reason='SCHEDULE_CAPACITY'))
                    save(outcome='SCHEDULE_CAPACITY');continue  # Do not advance past unretained work.
                try:
                    d=source['body']['details'];due=finite(d['reevaluate_at']);expiry=finite(d['valid_until'])
                    if not source['body']['available_at']<=due<=finite(d['expected_release_at'])<expiry:
                        raise EvidenceError('RELEASE_SCHEDULE_CHRONOLOGY')
                except (EvidenceError,KeyError,TypeError):
                    state['skipped']+=1;outcomes.append(dict(id=source_id,outcome='SKIPPED',reason='INVALID_RELEASE_SCHEDULE'))
                else:
                    state['pending_schedules'][source_id]=dict(due=due,valid_until=expiry)
                    outcomes.append(dict(id=source_id,outcome='SCHEDULE_RETAINED'))
            elif classification in CHANNELS:
                try:
                    result=self.queue.publish('feed-route:'+digest(source_id),kind=kind,evidence_id=source_id)
                except EvidenceError as exc:
                    state['retry_attempts']+=1;outcomes.append(dict(id=source_id,outcome='DEFERRED',reason=str(exc)))
                    save(outcome='QUEUE_RETRY_PENDING');continue
                state['delivered']+=1;outcomes.append(dict(id=source_id,outcome='ROUTED',queue_record_id=result['id']))
            else:
                state['skipped']+=1;outcomes.append(dict(id=source_id,outcome='SKIPPED',reason=classification))
            state['cursors'][kind]=source['seq'];save(outcome='CURSOR_ADVANCED')
        return self._save(final,state,outcome='DRAIN_BOUNDED',outcomes=outcomes,
                          budget_exhausted=time.monotonic()>=deadline, remaining_sources_may_exist=True)
