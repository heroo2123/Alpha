"""Bounded delivery of explicit archived PAPER receipts to the common account.

This is cooperative, local reconciliation, not a venue feed, execution engine,
independent guardian, or authority to turn public trades into account fills.
Once activated, the journal fences every coordinator sharing this account.
"""
from copy import deepcopy
from dataclasses import asdict, dataclass
from decimal import InvalidOperation
import fcntl
import os
import stat
import time

from .evidence import EvidenceError, canonical, digest, finite, identity


VERSION = 'alpha_v11_paper_receipt_reconciliation_v1'


@dataclass(frozen=True)
class ReconciliationPolicy:
    version: str
    maximum_receipts: int = 32
    maximum_pending: int = 64
    maximum_seconds: float = 1.

    def __post_init__(self):
        identity(self.version)
        if (type(self.maximum_receipts) is not int or not 2 <= self.maximum_receipts <= 64
                or type(self.maximum_pending) is not int or not 1 <= self.maximum_pending <= 64
                or not .05 <= finite(self.maximum_seconds) <= 2):
            raise EvidenceError('PAPER_RECEIPT_POLICY_BOUND')


def journal_key(account_id):
    return 'paper-receipts:'+digest(identity(account_id))


def admission_heads(store, *, account_id, account_policy_sha, required_config=None):
    """Guard activation, journal progress and new receipt arrival atomically."""
    key = journal_key(account_id)
    row = store.latest(kind='RUNTIME_STATUS', event_id=key)
    heads = (('RUNTIME_STATUS', key, row['seq'] if row else 0),)
    if row is None:
        if required_config is not None: raise EvidenceError('PAPER_RECEIPTS_REQUIRE_RECONCILIATION')
        return heads, None  # Preserve unconfigured historical runs.
    d = row['body']['details']; state = d.get('state', {})
    if (d.get('version') != VERSION or d.get('account_id') != account_id
            or d.get('account_policy_sha256') != account_policy_sha
            or required_config is not None and d.get('config_sha256') != required_config):
        raise EvidenceError('PAPER_RECEIPT_ACCOUNT_POLICY_MISMATCH')
    frontier = store.paper_receipt_window(limit=0)['frontier']
    if (d.get('outcome') != 'RECONCILED' or state.get('pending') != {}
            or type(state.get('cursor')) is not int or state['cursor'] != frontier):
        raise EvidenceError('PAPER_RECEIPTS_REQUIRE_RECONCILIATION')
    return heads, frontier


class PaperReconciliation:
    def __init__(self, coordinator, policy, *, queue=None):
        if not isinstance(policy, ReconciliationPolicy) or coordinator.store.namespace != 'V11_PAPER':
            raise EvidenceError('PAPER_RECEIPT_COMPONENT_SCOPE')
        self.coordinator, self.store, self.policy = coordinator, coordinator.store, policy
        if queue is not None and queue.store is not self.store:
            raise EvidenceError('PAPER_RECEIPT_QUEUE_SCOPE')
        self.queue = queue
        self.key = journal_key(coordinator.policy.account_id)
        config = dict(policy=asdict(policy), account=coordinator.policy_sha)
        if queue is not None: config['queue'] = queue.config
        self.config = digest(config)
        if coordinator.reconciliation_config not in {None, self.config}:
            raise EvidenceError('PAPER_RECEIPT_CONFIG_CHANGED')
        coordinator.reconciliation_config = self.config

    def _head(self):
        row = self.store.latest(kind='RUNTIME_STATUS', event_id=self.key)
        if row and row['body']['details'].get('config_sha256') != self.config:
            raise EvidenceError('PAPER_RECEIPT_CONFIG_CHANGED')
        return row

    def _save(self, key, head, state, **details):
        frontier = details.pop('expected_receipt_seq', None)
        return self.store.audit(key, event_id=self.key, kind='RUNTIME_STATUS', details=dict(
            version=VERSION, config_sha256=self.config, policy=asdict(self.policy),
            account_id=self.coordinator.policy.account_id, account_policy_sha256=self.coordinator.policy_sha,
            state=state, financial_authority=False, forward_or_live_acceptance=False, **details),
            expected_previous_seq=head['seq'] if head else 0, expected_receipt_seq=frontier)

    def _replay(self, key, request):
        try: row = self.store.get(key)
        except EvidenceError as exc:
            if str(exc) == 'EVIDENCE_MISSING': return None
            raise
        if (row['kind'] != 'RUNTIME_STATUS' or row['event_id'] != self.key
                or row['body']['details'].get('config_sha256') != self.config
                or canonical(row['body']['details'].get('request')) != canonical(request)):
            raise EvidenceError('PAPER_RECEIPT_REPLAY_CONFLICT')
        return row

    def step(self, command_id, *, deadline=None):
        request = dict(command_id=identity(command_id))
        key = 'paper-receipt-step:'+digest(dict(account=self.key, request=request))
        replay = self._replay(key, request)
        if replay: return replay
        end = min(time.monotonic()+self.policy.maximum_seconds,
                  finite(deadline) if deadline is not None else float('inf'))
        path = str(self.store.path)+'.'+self.key.replace(':', '-')+'.lock'
        fd = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        try:
            if not stat.S_ISREG(os.fstat(fd).st_mode): raise EvidenceError('PAPER_RECEIPT_LOCK_INVALID')
            try: fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError: raise EvidenceError('PAPER_RECEIPT_WORKER_BUSY') from None
            replay = self._replay(key, request)
            if replay: return replay
            head = self._head()
            if head is None:
                head = self._save('paper-receipt-activation:'+digest(dict(account=self.key, config=self.config)),
                    None, dict(cursor=0, pending={}, retry_cursor=''), outcome='REGISTERED_NOT_RECONCILED')
            return self._step(key, request, head, end)
        finally: os.close(fd)

    def _apply(self, row):
        b = row['body']; p = b.get('payload', {})
        # Only a well-formed explicit foreign identity may be excluded. Missing
        # identities and invalid evidence classes remain visible and unresolved.
        namespace, account = p.get('execution_namespace'), p.get('account_id')
        for value in (namespace, account): identity(value)
        if namespace != self.store.namespace or account != self.coordinator.policy.account_id:
            return dict(outcome='FOREIGN_RECEIPT', account_record_id=None)
        kind = p.get('record_type')
        if kind not in {'PAPER_FILL', 'PAPER_TERMINAL'}:
            raise EvidenceError('PAPER_RECEIPT_TYPE_UNSUPPORTED')
        # Bind the immutable receipt, not the worker attempt, so crash recovery
        # cannot deliver the same economic fill or terminal transition twice.
        command = 'paper-receipt-account:'+digest(dict(account=self.key, receipt=row['sha256']))
        result = (self.coordinator.record_fill(command, row['id']) if kind == 'PAPER_FILL'
                  else self.coordinator.reconcile_terminal(command, row['id']))
        details = dict(outcome='RECONCILED_RECEIPT', account_record_id=result['id'])
        if self.queue is not None:
            routed = self.queue.reconciled_account_change('paper-receipt-event:'+digest([self.config,row['sha256']]),
                account_record_id=result['id'], account_id=self.coordinator.policy.account_id,
                account_policy_sha=self.coordinator.policy_sha)
            details['event_queue_record_id'] = routed['id']
        return details

    def _step(self, key, request, head, end):
        state = deepcopy(head['body']['details']['state'])
        outcomes = []; attempted = 0

        def attempt(row):
            nonlocal attempted
            attempted += 1
            account = self.coordinator._head()
            seq = account['seq'] if account else 0
            item = dict(receipt_id=row['id'], receipt_sha256=row['sha256'], receipt_seq=row['seq'])
            try:
                result = self._apply(row)
                state['pending'].pop(row['id'], None)
                outcomes.append(dict(item, **result))
                return True
            except (EvidenceError, KeyError, TypeError, ValueError, InvalidOperation) as exc:
                reason = str(exc) if isinstance(exc, EvidenceError) else 'PAPER_RECEIPT_MALFORMED'
                item.update(outcome='PENDING_RECEIPT', reason=reason, attempted_account_seq=seq)
                outcomes.append(item)
                if row['id'] not in state['pending'] and len(state['pending']) >= self.policy.maximum_pending:
                    return False
                state['pending'][row['id']] = item
                return True

        pending = sorted(state['pending'])
        pending = [k for k in pending if k > state['retry_cursor']]+[k for k in pending if k <= state['retry_cursor']]
        for receipt_id in pending[:self.policy.maximum_receipts//2]:
            if time.monotonic() >= end: break
            old = state['pending'][receipt_id]
            account = self.coordinator._head()
            if (old['attempted_account_seq'] != (account['seq'] if account else 0)
                    or old['reason'] in {'CLOCK_REGRESSION', 'AUDIT_CLOCK_REGRESSION', 'AUDIT_STATE_CHANGED',
                                         'AUDIT_GUARDED_STATE_CHANGED', 'TRIGGER_STATE_BYTES_BOUND',
                                         'ARCHIVE_DISK_HEADROOM', 'ARCHIVE_BYTES_LIMIT', 'ARCHIVE_RECORD_LIMIT'}):
                row = self.store.get(receipt_id)
                if row['sha256'] != old['receipt_sha256']: raise EvidenceError('PAPER_RECEIPT_IDENTITY_CHANGED')
                attempt(row)
            state['retry_cursor'] = receipt_id

        frontier = None
        if time.monotonic() < end:
            window = self.store.paper_receipt_window(after_seq=state['cursor'],
                limit=self.policy.maximum_receipts-attempted, deadline=end)
            frontier = window['frontier']
            for row in window['records']:
                if time.monotonic() >= end or not attempt(row): break
                state['cursor'] = row['seq']
        ready = frontier is not None and state['cursor'] == frontier and not state['pending']
        account = self.coordinator._head()
        return self._save(key, head, state, request=request,
            outcome='RECONCILED' if ready else 'RECONCILIATION_REQUIRED', receipt_outcomes=outcomes,
            receipt_frontier=frontier, pending_count=len(state['pending']), attempted=attempted,
            account_record_id=account['id'] if account else None, budget_exhausted=time.monotonic() >= end,
            expected_receipt_seq=frontier if ready else None)
