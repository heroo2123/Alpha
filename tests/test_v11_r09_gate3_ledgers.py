"""Synthetic, offline counterexamples for the Gate 3 V4 slice 2 ledgers
(tools/v11_r09_gate3_ledgers.py). No network calls, no real clock, no
launch/transport entrypoint.

Includes the repair regression tests for the nine defects (S1-S9)
Opus's exact-commit review of 39b80fa reproduced against
docs/V11_R09_GATE3_TRANSPORT_RUNTIME_DESIGN.md sections 3-6: see
docs/V11_R09_GATE3_V4_SLICE2_REVIEW_39b80fa.md. Each regression test below
is annotated with the exact S-id it covers and fails against the
unrepaired 39b80fa module.
"""
import os

import pytest

from tools.v11_r09_gate3_launch import LaunchContractError
from tools.v11_r09_gate3_ledgers import (
    LEDGER_MAX_BYTES, LEDGER_MAX_EVENTS, LEDGER_RECORD_MAX_BYTES,
    REPORT_RESERVE_BYTES, SessionLedger, SharedLedger,
)

MANIFEST = 'a' * 64
BOOT = 'boot-A'
GENESIS = '7' * 64


def _root(tmp_path, name='ledger'):
    root = tmp_path / name
    root.mkdir(mode=0o700)
    return root


def _denial(**overrides):
    base = {'status': '503', 'reason': 'provider overloaded',
            'evidence_sha256': 'b' * 64, 'evidence_missing_cause': None,
            'retry_after_seconds': 30, 'window_end_utc': 1000.0,
            'receipt_upper_bound_utc': 1001.0}
    base.update(overrides)
    return base


def _new_shared(path, **kw):
    kw.setdefault('boot_id', BOOT)
    kw.setdefault('genesis_review_digest', GENESIS)
    return SharedLedger(path, **kw)


def _open_intent(ledger, request_id, **kw):
    kw.setdefault('purpose', 'FIELD')
    kw.setdefault('endpoint_id', 'f' * 64)
    kw.setdefault('control_domain_id', 'd' * 64)
    kw.setdefault('manifest_sha256', MANIFEST)
    kw.setdefault('max_reservation_bytes', 10)
    kw.setdefault('now_utc', 0)
    ledger.intent_open(request_id, **kw)


def _session(path, **kw):
    kw.setdefault('manifest_sha256', MANIFEST)
    kw.setdefault('boot_id', BOOT)
    return SessionLedger(path, **kw)


# ---------------------------------------------------------------------------
# SharedLedger: genesis / identity / lineage
# ---------------------------------------------------------------------------

def test_shared_ledger_rejects_unreviewed_genesis(tmp_path):
    root = _root(tmp_path)
    with pytest.raises(LaunchContractError, match='SHARED_LEDGER_LINEAGE_UNREVIEWED'):
        SharedLedger(root, boot_id=BOOT)


def test_shared_ledger_rejects_expected_head_on_empty_root(tmp_path):
    root = _root(tmp_path)
    with pytest.raises(LaunchContractError, match='SHARED_LEDGER_LINEAGE_HEAD_MISMATCH'):
        SharedLedger(root, boot_id=BOOT, genesis_review_digest=GENESIS,
                     expected_history_head='c' * 64)


def test_shared_ledger_genesis_then_restart_identity_and_lineage(tmp_path):
    root = _root(tmp_path)
    with _new_shared(root) as ledger:
        head = ledger.prev
    with SharedLedger(root, boot_id=BOOT, expected_history_head=head) as ledger:
        assert ledger.open_intent is None and ledger.open_count == 0
    # S8: the expected history head is mandatory on every non-empty root.
    with pytest.raises(LaunchContractError, match='SHARED_LEDGER_LINEAGE_HEAD_REQUIRED'):
        SharedLedger(root, boot_id=BOOT)
    with pytest.raises(LaunchContractError, match='SHARED_LEDGER_IDENTITY_MISMATCH'):
        SharedLedger(root, boot_id=BOOT, expected_history_head=head, max_requests=5)
    with pytest.raises(LaunchContractError, match='SHARED_LEDGER_LINEAGE_HEAD_MISMATCH'):
        SharedLedger(root, boot_id=BOOT, expected_history_head='e' * 64)
    # Genesis is recorded once, at creation; never re-asserted on reopen.
    with pytest.raises(LaunchContractError, match='SHARED_LEDGER_GENESIS_ALREADY_RECORDED'):
        SharedLedger(root, boot_id=BOOT, genesis_review_digest=GENESIS,
                     expected_history_head=head)


def test_shared_ledger_rejects_bad_genesis_boot_id_and_cap(tmp_path):
    root = _root(tmp_path)
    with pytest.raises(LaunchContractError, match='SHARED_LEDGER_GENESIS_DIGEST'):
        SharedLedger(root, boot_id=BOOT, genesis_review_digest='not-hex')
    with pytest.raises(LaunchContractError, match='SHARED_LEDGER_BOOT_ID'):
        SharedLedger(root, boot_id='', genesis_review_digest=GENESIS)
    with pytest.raises(LaunchContractError, match='SHARED_LEDGER_REQUEST_CAP'):
        SharedLedger(root, boot_id=BOOT, genesis_review_digest=GENESIS, max_requests=0)


# ---------------------------------------------------------------------------
# S8 (shared root lineage): root is its own identity, not bound to a single
# caller manifest; each intent records its own manifest instead.
# ---------------------------------------------------------------------------

def test_shared_ledger_root_is_reusable_across_distinct_manifests(tmp_path):
    root = _root(tmp_path)
    with _new_shared(root) as ledger:
        _open_intent(ledger, 'req-1', manifest_sha256=MANIFEST)
        ledger.intent_closed('req-1', outcome='OK')
        head = ledger.prev
    with SharedLedger(root, boot_id=BOOT, expected_history_head=head) as ledger:
        _open_intent(ledger, 'req-2', manifest_sha256='f' * 64)
        assert ledger.open_intent['manifest_sha256'] == 'f' * 64


def test_shared_ledger_intent_open_rejects_bad_manifest_digest(tmp_path):
    root = _root(tmp_path)
    with _new_shared(root) as ledger:
        with pytest.raises(LaunchContractError, match='SHARED_LEDGER_MANIFEST_DIGEST'):
            _open_intent(ledger, 'req-1', manifest_sha256='not-hex')


# ---------------------------------------------------------------------------
# SharedLedger: intent lifecycle
# ---------------------------------------------------------------------------

def test_shared_ledger_intent_open_close_ok_roundtrip(tmp_path):
    root = _root(tmp_path)
    with _new_shared(root) as ledger:
        _open_intent(ledger, 'req-1')
        assert ledger.open_intent['request_id'] == 'req-1'
        ledger.intent_closed('req-1', outcome='OK')
        assert ledger.open_intent is None and ledger.closed_count == 1
        # Request ids are permanently retired even after a clean close.
        with pytest.raises(LaunchContractError, match='SHARED_LEDGER_REQUEST_ID_REUSE'):
            _open_intent(ledger, 'req-1')


def test_shared_ledger_rejects_overlap_and_close_without_open(tmp_path):
    root = _root(tmp_path)
    with _new_shared(root) as ledger:
        with pytest.raises(LaunchContractError, match='SHARED_LEDGER_CLOSE_WITHOUT_OPEN'):
            ledger.intent_closed('nope', outcome='OK')
        _open_intent(ledger, 'req-1')
        with pytest.raises(LaunchContractError, match='SHARED_LEDGER_INTENT_OPEN_HELD'):
            _open_intent(ledger, 'req-2')
        with pytest.raises(LaunchContractError, match='SHARED_LEDGER_CLOSE_WITHOUT_OPEN'):
            ledger.intent_closed('req-2', outcome='OK')


def test_shared_ledger_rejects_bad_request_id_purpose_and_cap(tmp_path):
    root = _root(tmp_path)
    with _new_shared(root, max_requests=1) as ledger:
        with pytest.raises(LaunchContractError, match='SHARED_LEDGER_REQUEST_ID'):
            _open_intent(ledger, 'bad id!')
        with pytest.raises(LaunchContractError, match='SHARED_LEDGER_PURPOSE'):
            _open_intent(ledger, 'req-1', purpose='NOPE')
        _open_intent(ledger, 'req-1')
        ledger.intent_closed('req-1', outcome='OK')
        with pytest.raises(LaunchContractError, match='SHARED_LEDGER_REQUEST_CAP_EXCEEDED'):
            _open_intent(ledger, 'req-2')


# ---------------------------------------------------------------------------
# SharedLedger: denial / cooldown semantics
# ---------------------------------------------------------------------------

def test_shared_ledger_explicit_denial_never_auto_resumes(tmp_path):
    root = _root(tmp_path)
    with _new_shared(root) as ledger:
        _open_intent(ledger, 'req-1')
        ledger.denial_observed('req-1',
                                denial=_denial(status='403', retry_after_seconds=None))
        ledger.intent_closed('req-1', outcome='DENIED')
        assert ledger.is_blocked('d' * 64, now_utc=0)
        assert ledger.is_blocked('d' * 64, now_utc=10 ** 12)


def test_shared_ledger_finite_cooldown_expires(tmp_path):
    root = _root(tmp_path)
    with _new_shared(root) as ledger:
        _open_intent(ledger, 'req-1')
        ledger.denial_observed('req-1', denial=_denial(
            status='503', retry_after_seconds=5, window_end_utc=100.0,
            receipt_upper_bound_utc=100.0))
        ledger.intent_closed('req-1', outcome='DENIED')
        assert ledger.is_blocked('d' * 64, now_utc=104.9)
        assert not ledger.is_blocked('d' * 64, now_utc=105.1)
        with pytest.raises(LaunchContractError,
                            match='SHARED_LEDGER_CONTROL_DOMAIN_COOLDOWN'):
            _open_intent(ledger, 'req-2', now_utc=104.9)
        _open_intent(ledger, 'req-2', now_utc=105.1)


def test_shared_ledger_denial_observed_schema_validation(tmp_path):
    root = _root(tmp_path)
    with _new_shared(root) as ledger:
        _open_intent(ledger, 'req-1')
        with pytest.raises(LaunchContractError, match='SHARED_LEDGER_DENIAL_SCHEMA'):
            ledger.denial_observed('req-1', denial={})
        ledger.intent_closed('req-1', outcome='OK')


def test_shared_ledger_denial_evidence_xor_and_bad_status_rejected(tmp_path):
    root = _root(tmp_path)
    with _new_shared(root) as ledger:
        _open_intent(ledger, 'req-1')
        with pytest.raises(LaunchContractError, match='SHARED_LEDGER_DENIAL_STATUS'):
            ledger.denial_observed('req-1', denial=_denial(status='999'))
        ledger.intent_closed('req-1', outcome='OK')
        _open_intent(ledger, 'req-2')
        with pytest.raises(LaunchContractError, match='SHARED_LEDGER_DENIAL_EVIDENCE'):
            ledger.denial_observed('req-2', denial=_denial(
                evidence_sha256='b' * 64, evidence_missing_cause='both set'))


def test_shared_ledger_denial_rejects_receipt_bound_before_window_end(tmp_path):
    # S7 (ordering half): the conservative receipt upper bound cannot
    # precede the window it is supposed to bound.
    root = _root(tmp_path)
    with _new_shared(root) as ledger:
        _open_intent(ledger, 'req-1')
        with pytest.raises(LaunchContractError, match='SHARED_LEDGER_DENIAL_ORDER'):
            ledger.denial_observed('req-1', denial=_denial(
                window_end_utc=1000.0, receipt_upper_bound_utc=999.0))


# ---------------------------------------------------------------------------
# S1: recording a denial is a separate DENIAL_OBSERVED event that blocks the
# control domain immediately but keeps the single global token held until a
# later, separate INTENT_CLOSED.
# ---------------------------------------------------------------------------

def test_shared_ledger_denial_observed_keeps_token_held_until_closed(tmp_path):
    root = _root(tmp_path)
    with _new_shared(root) as ledger:
        _open_intent(ledger, 'req-1', control_domain_id='d' * 64)
        ledger.denial_observed('req-1', denial=_denial())
        # The control domain is blocked at once...
        assert ledger.is_blocked('d' * 64, now_utc=0)
        # ...but the global token is still held: an unrelated intent is
        # refused until req-1 is actually, separately closed.
        with pytest.raises(LaunchContractError, match='SHARED_LEDGER_INTENT_OPEN_HELD'):
            _open_intent(ledger, 'req-2', control_domain_id='e' * 64)
        ledger.intent_closed('req-1', outcome='DENIED')
        assert ledger.open_intent is None
        head = ledger.prev
    with SharedLedger(root, boot_id=BOOT, expected_history_head=head) as ledger:
        assert ledger.inherited_open_request_id is None
        assert ledger.is_blocked('d' * 64, now_utc=0)


def test_shared_ledger_denial_close_requires_prior_observation(tmp_path):
    root = _root(tmp_path)
    with _new_shared(root) as ledger:
        _open_intent(ledger, 'req-1')
        with pytest.raises(LaunchContractError, match='SHARED_LEDGER_DENIAL_NOT_OBSERVED'):
            ledger.intent_closed('req-1', outcome='DENIED')
        ledger.intent_closed('req-1', outcome='OK')


def test_shared_ledger_denial_observed_rejects_double_record(tmp_path):
    root = _root(tmp_path)
    with _new_shared(root) as ledger:
        _open_intent(ledger, 'req-1')
        ledger.denial_observed('req-1', denial=_denial())
        with pytest.raises(LaunchContractError,
                            match='SHARED_LEDGER_DENIAL_ALREADY_RECORDED'):
            ledger.denial_observed('req-1', denial=_denial())


# ---------------------------------------------------------------------------
# S2: an AMBIGUOUS close is not an accepted outcome; ambiguity is held by
# simply never closing (inherited on restart like a crash), not released.
# ---------------------------------------------------------------------------

def test_shared_ledger_rejects_ambiguous_close_outcome(tmp_path):
    root = _root(tmp_path)
    with _new_shared(root) as ledger:
        _open_intent(ledger, 'req-1')
        with pytest.raises(LaunchContractError, match='SHARED_LEDGER_CLOSE_OUTCOME'):
            ledger.intent_closed('req-1', outcome='AMBIGUOUS')
        head = ledger.prev
    with SharedLedger(root, boot_id=BOOT, expected_history_head=head) as ledger:
        assert ledger.inherited_open_request_id == 'req-1'
        with pytest.raises(LaunchContractError,
                            match='SHARED_LEDGER_INHERITED_INTENT_HELD'):
            ledger.intent_closed('req-1', outcome='OK')


# ---------------------------------------------------------------------------
# S6: boot identity is compared on reopen; a mismatch refuses progression
# (never construction, so read-only inspection stays possible).
# ---------------------------------------------------------------------------

def test_shared_ledger_boot_mismatch_refuses_progression_not_construction(tmp_path):
    root = _root(tmp_path)
    with _new_shared(root, boot_id='boot-A') as ledger:
        head = ledger.prev
    with SharedLedger(root, boot_id='boot-B', expected_history_head=head) as ledger:
        assert ledger.boot_id == 'boot-B'
        with pytest.raises(LaunchContractError, match='LEDGER_BOOT_ID_MISMATCH'):
            _open_intent(ledger, 'req-1')


def test_session_ledger_boot_mismatch_refuses_progression_not_construction(tmp_path):
    root = _root(tmp_path)
    with _session(root, boot_id='boot-A'):
        pass
    with _session(root, boot_id='boot-B') as ledger:
        assert ledger.boot_id == 'boot-B'
        with pytest.raises(LaunchContractError, match='LEDGER_BOOT_ID_MISMATCH'):
            ledger.attempt_intent('req-1', purpose='FIELD', endpoint_id='f' * 64,
                                   max_reservation_bytes=10)


# ---------------------------------------------------------------------------
# S7 (clock half): a non-finite now_utc is rejected rather than silently
# evaluating an active cooldown as expired.
# ---------------------------------------------------------------------------

def test_shared_ledger_rejects_nan_now_utc(tmp_path):
    root = _root(tmp_path)
    with _new_shared(root) as ledger:
        _open_intent(ledger, 'req-1', control_domain_id='d' * 64)
        ledger.denial_observed('req-1', denial=_denial(retry_after_seconds=10 ** 6))
        ledger.intent_closed('req-1', outcome='DENIED')
        assert ledger.is_blocked('d' * 64, now_utc=0)
        with pytest.raises(LaunchContractError, match='SHARED_LEDGER_NOW_UTC'):
            ledger.is_blocked('d' * 64, now_utc=float('nan'))
        with pytest.raises(LaunchContractError, match='SHARED_LEDGER_NOW_UTC'):
            _open_intent(ledger, 'req-2', control_domain_id='d' * 64,
                         now_utc=float('nan'))


# ---------------------------------------------------------------------------
# SharedLedger: generalized R2 — inherited open intent is never resolvable
# ---------------------------------------------------------------------------

def test_shared_ledger_inherited_open_intent_held_forever(tmp_path):
    root = _root(tmp_path)
    with _new_shared(root) as ledger:
        _open_intent(ledger, 'req-1')
        head = ledger.prev
        # Crash: no intent_closed call, process simply exits.
    with SharedLedger(root, boot_id=BOOT, expected_history_head=head) as ledger:
        assert ledger.open_intent['request_id'] == 'req-1'
        assert ledger.inherited_open_request_id == 'req-1'
        with pytest.raises(LaunchContractError,
                            match='SHARED_LEDGER_INHERITED_INTENT_HELD'):
            ledger.intent_closed('req-1', outcome='OK')
        with pytest.raises(LaunchContractError,
                            match='SHARED_LEDGER_INHERITED_INTENT_HELD'):
            ledger.denial_observed('req-1', denial=_denial())
        head2 = ledger.prev
    # A second restart inherits the same still-open intent.
    with SharedLedger(root, boot_id=BOOT, expected_history_head=head2) as ledger:
        assert ledger.inherited_open_request_id == 'req-1'


# ---------------------------------------------------------------------------
# SessionLedger: lifecycle happy path and refuse/denial terminals
# ---------------------------------------------------------------------------

def test_session_ledger_full_success_lifecycle(tmp_path):
    root = _root(tmp_path)
    with _session(root) as ledger:
        ledger.attempt_intent('req-1', purpose='FIELD', endpoint_id='f' * 64,
                               max_reservation_bytes=10)
        assert ledger.attempt['state'] == 'OPEN'
        ledger.budget_reserved('req-1', reserve_event_hash='b' * 64)
        assert ledger.attempt['state'] == 'RESERVED'
        ledger.dispatch_intent('req-1', measured_start_monotonic=1.5)
        assert ledger.attempt['state'] == 'DISPATCHED'
        ledger.transport_closed('req-1', outcome='OK', total_delivered_bytes=10)
        assert ledger.attempt['state'] == 'CLOSED'
        ledger.accounted('req-1', completion_event_hash='c' * 64)
        assert ledger.attempt['state'] == 'ACCOUNTED'
        ledger.object_witnessed('req-1', store_receipt_commit_hash='d' * 64)
        assert ledger.attempt['state'] == 'WITNESSED'
        ledger.terminal('req-1', outcome='SUCCESS', reason='delivered',
                         report_reserved_bytes=REPORT_RESERVE_BYTES)
        assert ledger.attempt['state'] == 'TERMINAL'
        assert ledger.attempt['outcome'] == 'SUCCESS'
        assert ledger.completed_count == 1
    # Replay reconstructs the identical terminal state.
    with _session(root) as ledger:
        assert ledger.completed_count == 1
        assert ledger.inherited_request_id is None


def test_session_ledger_accounted_without_witness_can_reach_terminal(tmp_path):
    root = _root(tmp_path)
    with _session(root) as ledger:
        ledger.attempt_intent('req-1', purpose='FIELD', endpoint_id='f' * 64,
                               max_reservation_bytes=10)
        ledger.budget_reserved('req-1', reserve_event_hash='b' * 64)
        ledger.dispatch_intent('req-1', measured_start_monotonic=0)
        ledger.transport_closed('req-1', outcome='PARTIAL', total_delivered_bytes=3)
        ledger.accounted('req-1', completion_event_hash='c' * 64)
        # ACCOUNTED (no object witness) is itself a valid non-success
        # terminal predecessor; SUCCESS is the one outcome that needs WITNESSED.
        ledger.terminal('req-1', outcome='FAILED', reason='short',
                         report_reserved_bytes=REPORT_RESERVE_BYTES)
        assert ledger.attempt['outcome'] == 'FAILED'
        with pytest.raises(LaunchContractError, match='SESSION_LEDGER_BAD_TRANSITION'):
            ledger.terminal('req-1', outcome='FAILED', reason='twice',
                             report_reserved_bytes=REPORT_RESERVE_BYTES)


def test_session_ledger_refuse_path_and_then_new_attempt(tmp_path):
    root = _root(tmp_path)
    with _session(root) as ledger:
        ledger.attempt_intent('req-1', purpose='FIELD', endpoint_id='f' * 64,
                               max_reservation_bytes=10)
        ledger.refuse('req-1', reason='SHARED_LEDGER_CONTROL_DOMAIN_COOLDOWN')
        assert ledger.attempt['outcome'] == 'REFUSED' and ledger.completed_count == 1
        ledger.attempt_intent('req-2', purpose='INDEX', endpoint_id='f' * 64,
                               max_reservation_bytes=10)
        assert ledger.attempt['request_id'] == 'req-2'


# ---------------------------------------------------------------------------
# S4: SUCCESS is only reachable from WITNESSED (ACCOUNTED alone proves
# accounting, not a store receipt).
# ---------------------------------------------------------------------------

def test_session_ledger_success_requires_witness(tmp_path):
    root = _root(tmp_path)
    with _session(root) as ledger:
        ledger.attempt_intent('req-1', purpose='FIELD', endpoint_id='f' * 64,
                               max_reservation_bytes=10)
        ledger.budget_reserved('req-1', reserve_event_hash='b' * 64)
        ledger.dispatch_intent('req-1', measured_start_monotonic=0)
        ledger.transport_closed('req-1', outcome='OK', total_delivered_bytes=10)
        ledger.accounted('req-1', completion_event_hash='c' * 64)
        with pytest.raises(LaunchContractError,
                            match='SESSION_LEDGER_SUCCESS_REQUIRES_WITNESS'):
            ledger.terminal('req-1', outcome='SUCCESS', reason='x',
                             report_reserved_bytes=REPORT_RESERVE_BYTES)
        ledger.object_witnessed('req-1', store_receipt_commit_hash='d' * 64)
        ledger.terminal('req-1', outcome='SUCCESS', reason='x',
                         report_reserved_bytes=REPORT_RESERVE_BYTES)


# ---------------------------------------------------------------------------
# S3: AMBIGUOUS_HELD is not an accepted terminal outcome; ambiguity is held
# by never terminating (inherited on restart like a crash), not released.
# ---------------------------------------------------------------------------

def test_session_ledger_rejects_ambiguous_held_terminal(tmp_path):
    root = _root(tmp_path)
    with _session(root) as ledger:
        ledger.attempt_intent('req-1', purpose='FIELD', endpoint_id='f' * 64,
                               max_reservation_bytes=10)
        ledger.budget_reserved('req-1', reserve_event_hash='b' * 64)
        ledger.dispatch_intent('req-1', measured_start_monotonic=0)
        ledger.transport_closed('req-1', outcome='OK', total_delivered_bytes=10)
        ledger.accounted('req-1', completion_event_hash='c' * 64)
        with pytest.raises(LaunchContractError, match='SESSION_LEDGER_TERMINAL_OUTCOME'):
            ledger.terminal('req-1', outcome='AMBIGUOUS_HELD', reason='x',
                             report_reserved_bytes=REPORT_RESERVE_BYTES)
    with _session(root) as ledger:
        assert ledger.inherited_request_id == 'req-1'


# ---------------------------------------------------------------------------
# S5: a denial is a non-terminal annotation on a DISPATCHED attempt; it
# cannot be observed before dispatch, and TRANSPORT_CLOSED/ACCOUNTED are
# still required before any (necessarily non-SUCCESS) terminal.
# ---------------------------------------------------------------------------

def test_session_ledger_denial_requires_dispatch_and_still_requires_close(tmp_path):
    root = _root(tmp_path)
    with _session(root) as ledger:
        ledger.attempt_intent('req-1', purpose='FIELD', endpoint_id='f' * 64,
                               max_reservation_bytes=10)
        ledger.budget_reserved('req-1', reserve_event_hash='b' * 64)
        with pytest.raises(LaunchContractError, match='SESSION_LEDGER_BAD_TRANSITION'):
            ledger.denial('req-1', reason='too early')
        ledger.dispatch_intent('req-1', measured_start_monotonic=0)
        ledger.denial('req-1', reason='403 seen in headers')
        # Non-terminal: still DISPATCHED, still requires close + accounting.
        assert ledger.attempt['state'] == 'DISPATCHED'
        assert ledger.attempt['outcome'] is None
        with pytest.raises(LaunchContractError,
                            match='SESSION_LEDGER_DENIAL_ALREADY_RECORDED'):
            ledger.denial('req-1', reason='twice')
        ledger.transport_closed('req-1', outcome='FAILED', total_delivered_bytes=0)
        ledger.accounted('req-1', completion_event_hash='c' * 64)
        ledger.terminal('req-1', outcome='FAILED', reason='denied',
                         report_reserved_bytes=REPORT_RESERVE_BYTES)
        assert ledger.completed_count == 1


def test_session_ledger_denial_then_crash_inherits_unsettled_attempt(tmp_path):
    root = _root(tmp_path)
    with _session(root) as ledger:
        ledger.attempt_intent('req-1', purpose='FIELD', endpoint_id='f' * 64,
                               max_reservation_bytes=10)
        ledger.budget_reserved('req-1', reserve_event_hash='b' * 64)
        ledger.dispatch_intent('req-1', measured_start_monotonic=0)
        ledger.denial('req-1', reason='403 seen in headers')
        # Crash: no transport_closed/accounted/terminal call. The denied
        # request's budget reservation was never settled.
    with _session(root) as ledger:
        assert ledger.inherited_request_id == 'req-1'
        with pytest.raises(LaunchContractError,
                            match='SESSION_LEDGER_INHERITED_ATTEMPT_HELD'):
            ledger.transport_closed('req-1', outcome='FAILED', total_delivered_bytes=0)


# ---------------------------------------------------------------------------
# S9: an observed overdelivery is recorded durably, blocks that attempt's
# own terminal from ever being SUCCESS, and permanently poisons the whole
# session ledger against any further attempt (survives restart).
# ---------------------------------------------------------------------------

def test_session_ledger_overdelivery_blocks_success_and_poisons_session(tmp_path):
    root = _root(tmp_path)
    with _session(root) as ledger:
        ledger.attempt_intent('req-1', purpose='FIELD', endpoint_id='f' * 64,
                               max_reservation_bytes=10)
        ledger.budget_reserved('req-1', reserve_event_hash='b' * 64)
        ledger.dispatch_intent('req-1', measured_start_monotonic=1.0)
        ledger.transport_closed('req-1', outcome='OK', total_delivered_bytes=11)
        ledger.accounted('req-1', completion_event_hash='c' * 64)
        ledger.object_witnessed('req-1', store_receipt_commit_hash='d' * 64)
        with pytest.raises(LaunchContractError,
                            match='SESSION_LEDGER_OVERDELIVERY_BLOCKS_SUCCESS'):
            ledger.terminal('req-1', outcome='SUCCESS', reason='x',
                             report_reserved_bytes=REPORT_RESERVE_BYTES)
        # Still closeable as a non-success terminal.
        ledger.terminal('req-1', outcome='FAILED', reason='overdelivered',
                         report_reserved_bytes=REPORT_RESERVE_BYTES)
        with pytest.raises(LaunchContractError,
                            match='SESSION_LEDGER_OVERDELIVERY_POISONED'):
            ledger.attempt_intent('req-2', purpose='FIELD', endpoint_id='f' * 64,
                                   max_reservation_bytes=10)
    # The poison is durable: it survives a restart.
    with _session(root) as ledger:
        assert ledger.overdelivery_poisoned
        with pytest.raises(LaunchContractError,
                            match='SESSION_LEDGER_OVERDELIVERY_POISONED'):
            ledger.attempt_intent('req-2', purpose='FIELD', endpoint_id='f' * 64,
                                   max_reservation_bytes=10)


def test_session_ledger_report_reserve_mismatch_rejected(tmp_path):
    root = _root(tmp_path)
    with _session(root) as ledger:
        ledger.attempt_intent('req-1', purpose='FIELD', endpoint_id='f' * 64,
                               max_reservation_bytes=10)
        ledger.budget_reserved('req-1', reserve_event_hash='b' * 64)
        ledger.dispatch_intent('req-1', measured_start_monotonic=0)
        ledger.transport_closed('req-1', outcome='OK', total_delivered_bytes=1)
        ledger.accounted('req-1', completion_event_hash='c' * 64)
        with pytest.raises(LaunchContractError,
                            match='SESSION_LEDGER_REPORT_RESERVE_MISMATCH'):
            ledger.terminal('req-1', outcome='FAILED', reason='x',
                             report_reserved_bytes=REPORT_RESERVE_BYTES + 1)


def test_session_ledger_rejects_out_of_order_transitions(tmp_path):
    root = _root(tmp_path)
    with _session(root) as ledger:
        with pytest.raises(LaunchContractError, match='SESSION_LEDGER_NO_SUCH_ATTEMPT'):
            ledger.budget_reserved('req-1', reserve_event_hash='b' * 64)
        ledger.attempt_intent('req-1', purpose='FIELD', endpoint_id='f' * 64,
                               max_reservation_bytes=10)
        with pytest.raises(LaunchContractError, match='SESSION_LEDGER_BAD_TRANSITION'):
            ledger.dispatch_intent('req-1', measured_start_monotonic=0)
        with pytest.raises(LaunchContractError, match='SESSION_LEDGER_ATTEMPT_OPEN_HELD'):
            ledger.attempt_intent('req-2', purpose='FIELD', endpoint_id='f' * 64,
                                   max_reservation_bytes=10)


def test_session_ledger_request_id_reuse_and_cap(tmp_path):
    root = _root(tmp_path)
    with _session(root, max_requests=1) as ledger:
        ledger.attempt_intent('req-1', purpose='FIELD', endpoint_id='f' * 64,
                               max_reservation_bytes=10)
        ledger.refuse('req-1', reason='done')
        with pytest.raises(LaunchContractError,
                            match='SESSION_LEDGER_REQUEST_CAP_EXCEEDED'):
            ledger.attempt_intent('req-2', purpose='FIELD', endpoint_id='f' * 64,
                                   max_reservation_bytes=10)
    with _session(root, max_requests=1) as ledger:
        with pytest.raises(LaunchContractError,
                            match='SESSION_LEDGER_REQUEST_ID_REUSE|'
                                  'SESSION_LEDGER_REQUEST_CAP_EXCEEDED'):
            ledger.attempt_intent('req-1', purpose='FIELD', endpoint_id='f' * 64,
                                   max_reservation_bytes=10)


def test_session_ledger_range_and_optional_digest_validation(tmp_path):
    root = _root(tmp_path)
    with _session(root) as ledger:
        with pytest.raises(LaunchContractError, match='SESSION_LEDGER_RANGE'):
            ledger.attempt_intent('req-1', purpose='FIELD', endpoint_id='f' * 64,
                                   max_reservation_bytes=10, range_start=0)
        with pytest.raises(LaunchContractError, match='SESSION_LEDGER_RANGE'):
            ledger.attempt_intent('req-1', purpose='FIELD', endpoint_id='f' * 64,
                                   max_reservation_bytes=10, range_start=5, range_end=1)
        ledger.attempt_intent('req-1', purpose='FIELD', endpoint_id='f' * 64,
                               max_reservation_bytes=10, range_start=1, range_end=5,
                               validator_sha256='b' * 64)


# ---------------------------------------------------------------------------
# SessionLedger: identity / lineage on restart
# ---------------------------------------------------------------------------

def test_session_ledger_identity_mismatch_on_reopen(tmp_path):
    root = _root(tmp_path)
    with _session(root):
        pass
    with pytest.raises(LaunchContractError, match='SESSION_LEDGER_IDENTITY_MISMATCH'):
        _session(root, manifest_sha256='e' * 64)
    with pytest.raises(LaunchContractError, match='SESSION_LEDGER_IDENTITY_MISMATCH'):
        _session(root, max_requests=7)
    with pytest.raises(LaunchContractError, match='SESSION_LEDGER_IDENTITY_MISMATCH'):
        _session(root, report_reserve_bytes=REPORT_RESERVE_BYTES + 1)


def test_session_ledger_expected_head_mismatch(tmp_path):
    root = _root(tmp_path)
    with _session(root):
        pass
    with pytest.raises(LaunchContractError, match='SESSION_LEDGER_HEAD_MISMATCH'):
        _session(root, expected_head='f' * 64)


# ---------------------------------------------------------------------------
# SessionLedger: generalized R2 — inherited open attempt is never resolvable
# ---------------------------------------------------------------------------

def test_session_ledger_inherited_open_attempt_held_through_every_method(tmp_path):
    root = _root(tmp_path)
    with _session(root) as ledger:
        ledger.attempt_intent('req-1', purpose='FIELD', endpoint_id='f' * 64,
                               max_reservation_bytes=10)
        ledger.budget_reserved('req-1', reserve_event_hash='b' * 64)
        # Crash: process exits with the attempt RESERVED, never resolved.
    with _session(root) as ledger:
        assert ledger.inherited_request_id == 'req-1'
        for call in (
            lambda: ledger.dispatch_intent('req-1', measured_start_monotonic=0),
            lambda: ledger.denial('req-1', reason='x'),
            lambda: ledger.refuse('req-1', reason='x'),
            lambda: ledger.transport_closed('req-1', outcome='OK',
                                             total_delivered_bytes=0),
        ):
            with pytest.raises(LaunchContractError,
                                match='SESSION_LEDGER_INHERITED_ATTEMPT_HELD'):
                call()
        # A later restart still inherits the same unresolved attempt.
    with _session(root) as ledger:
        assert ledger.inherited_request_id == 'req-1'


def test_session_ledger_terminal_attempt_is_not_inherited(tmp_path):
    root = _root(tmp_path)
    with _session(root) as ledger:
        ledger.attempt_intent('req-1', purpose='FIELD', endpoint_id='f' * 64,
                               max_reservation_bytes=10)
        ledger.refuse('req-1', reason='done')
    with _session(root) as ledger:
        assert ledger.inherited_request_id is None
        ledger.attempt_intent('req-2', purpose='FIELD', endpoint_id='f' * 64,
                               max_reservation_bytes=10)


# ---------------------------------------------------------------------------
# Shared journal discipline (fail-closed on tamper/corruption/concurrency)
# ---------------------------------------------------------------------------

def test_shared_ledger_tampered_journal_fails_closed(tmp_path):
    root = _root(tmp_path)
    with _new_shared(root) as ledger:
        _open_intent(ledger, 'req-1')
    journal = root / 'gate3_shared.jsonl'
    journal.write_bytes(journal.read_bytes().replace(b'req-1', b'req-x'))
    with pytest.raises(LaunchContractError, match='LEDGER_HASH'):
        SharedLedger(root, boot_id=BOOT)


def test_session_ledger_rejects_concurrent_writer(tmp_path):
    root = _root(tmp_path)
    with _session(root):
        with pytest.raises(LaunchContractError, match='LEDGER_CONCURRENT_WRITER'):
            _session(root)


def test_ledger_rejects_relative_or_symlinked_directory(tmp_path):
    root = _root(tmp_path)
    link = tmp_path / 'link'
    link.symlink_to(root)
    with pytest.raises(LaunchContractError, match='LEDGER_PATH_SYMLINK'):
        _session(link)
    with pytest.raises(LaunchContractError, match='LEDGER_DIRECTORY'):
        _session(os.path.relpath(root))


def test_ledger_rejects_world_or_group_readable_directory(tmp_path):
    root = _root(tmp_path)
    os.chmod(root, 0o750)
    with pytest.raises(LaunchContractError, match='LEDGER_DIRECTORY_PRIVATE_MODE'):
        _session(root)


def test_ledger_rejects_event_cap_past_fixed_limit(tmp_path, monkeypatch):
    root = _root(tmp_path)
    monkeypatch.setattr('tools.v11_r09_gate3_ledgers.LEDGER_MAX_EVENTS', 1)
    with pytest.raises(LaunchContractError, match='LEDGER_EVENT_CAPACITY'):
        with _session(root) as ledger:
            ledger.attempt_intent('req-1', purpose='FIELD', endpoint_id='f' * 64,
                                   max_reservation_bytes=10)


def test_ledger_rejects_oversized_record_before_write(tmp_path, monkeypatch):
    root = _root(tmp_path)
    monkeypatch.setattr('tools.v11_r09_gate3_ledgers.LEDGER_RECORD_MAX_BYTES', 32)
    with pytest.raises(LaunchContractError, match='LEDGER_RECORD_TOO_LARGE'):
        with _session(root) as ledger:
            ledger.attempt_intent('req-1', purpose='FIELD', endpoint_id='f' * 64,
                                   max_reservation_bytes=10)


def test_ledger_write_failure_poisons_instance_but_not_the_durable_journal(
        tmp_path, monkeypatch):
    root = _root(tmp_path)
    with _session(root) as ledger:
        monkeypatch.setattr(os, 'write', lambda *_a, **_k: (_ for _ in ()).throw(
            OSError('disk full')))
        with pytest.raises(LaunchContractError, match='LEDGER_DURABILITY_UNCERTAIN'):
            ledger.attempt_intent('req-1', purpose='FIELD', endpoint_id='f' * 64,
                                   max_reservation_bytes=10)
        # The failed append never reached memory/disk, so the instance is
        # poisoned for every further call, not just the one that failed.
        with pytest.raises(LaunchContractError, match='LEDGER_DURABILITY_UNCERTAIN'):
            ledger.attempt_intent('req-2', purpose='FIELD', endpoint_id='f' * 64,
                                   max_reservation_bytes=10)
    monkeypatch.undo()
    # The init record was already fsync'd before the poisoned call; a fresh
    # open replays only that record and is not itself poisoned.
    with _session(root) as ledger:
        assert ledger.attempt is None and ledger.inherited_request_id is None


def test_ledger_rejects_hardlinked_journal_file(tmp_path):
    root = _root(tmp_path)
    with _session(root):
        pass
    os.link(root / 'gate3_session.jsonl', root / 'extra_hardlink')
    with pytest.raises(LaunchContractError, match='LEDGER_FILE_IDENTITY'):
        _session(root)


def test_ledger_rejects_torn_final_record(tmp_path):
    root = _root(tmp_path)
    with _session(root):
        pass
    journal = root / 'gate3_session.jsonl'
    data = journal.read_bytes()
    journal.write_bytes(data.rstrip(b'\n')[:-1])
    with pytest.raises(LaunchContractError, match='LEDGER_TORN_RECORD'):
        _session(root)


def test_ledger_rejects_capacity_past_fixed_byte_cap(tmp_path, monkeypatch):
    root = _root(tmp_path)
    monkeypatch.setattr('tools.v11_r09_gate3_ledgers.LEDGER_MAX_BYTES', 1)
    with pytest.raises(LaunchContractError, match='LEDGER_CAPACITY_EXCEEDED'):
        _session(root)


# ---------------------------------------------------------------------------
# Construction-time validation bounds
# ---------------------------------------------------------------------------

def test_session_ledger_rejects_report_reserve_below_floor(tmp_path):
    root = _root(tmp_path)
    with pytest.raises(LaunchContractError, match='SESSION_LEDGER_REPORT_RESERVE'):
        _session(root, report_reserve_bytes=REPORT_RESERVE_BYTES - 1)
