"""Synthetic, offline counterexamples for the Gate 3 V4 slice 2 ledgers
(tools/v11_r09_gate3_ledgers.py). No network calls, no real clock, no
launch/transport entrypoint.
"""
import os

import pytest

from tools.v11_r09_gate3_launch import LaunchContractError
from tools.v11_r09_gate3_ledgers import (
    LEDGER_MAX_BYTES, LEDGER_MAX_EVENTS, LEDGER_RECORD_MAX_BYTES,
    REPORT_RESERVE_BYTES, SessionLedger, SharedLedger,
)

MANIFEST = 'a' * 64


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


# ---------------------------------------------------------------------------
# SharedLedger: genesis / identity / lineage
# ---------------------------------------------------------------------------

def test_shared_ledger_rejects_unreviewed_genesis(tmp_path):
    root = _root(tmp_path)
    with pytest.raises(LaunchContractError, match='SHARED_LEDGER_LINEAGE_UNREVIEWED'):
        SharedLedger(root, manifest_sha256=MANIFEST)


def test_shared_ledger_rejects_expected_head_on_empty_root(tmp_path):
    root = _root(tmp_path)
    with pytest.raises(LaunchContractError, match='SHARED_LEDGER_LINEAGE_HEAD_MISMATCH'):
        SharedLedger(root, manifest_sha256=MANIFEST, genesis_reviewed=True,
                     expected_history_head='c' * 64)


def test_shared_ledger_genesis_then_restart_identity_and_lineage(tmp_path):
    root = _root(tmp_path)
    with SharedLedger(root, manifest_sha256=MANIFEST, genesis_reviewed=True) as ledger:
        head = ledger.prev
    with SharedLedger(root, manifest_sha256=MANIFEST,
                       expected_history_head=head) as ledger:
        assert ledger.open_intent is None and ledger.open_count == 0
    with pytest.raises(LaunchContractError, match='SHARED_LEDGER_IDENTITY_MISMATCH'):
        SharedLedger(root, manifest_sha256='d' * 64)
    with pytest.raises(LaunchContractError, match='SHARED_LEDGER_IDENTITY_MISMATCH'):
        SharedLedger(root, manifest_sha256=MANIFEST, max_requests=5)
    with pytest.raises(LaunchContractError, match='SHARED_LEDGER_LINEAGE_HEAD_MISMATCH'):
        SharedLedger(root, manifest_sha256=MANIFEST, expected_history_head='e' * 64)


# ---------------------------------------------------------------------------
# SharedLedger: intent lifecycle
# ---------------------------------------------------------------------------

def test_shared_ledger_intent_open_close_ok_roundtrip(tmp_path):
    root = _root(tmp_path)
    with SharedLedger(root, manifest_sha256=MANIFEST, genesis_reviewed=True) as ledger:
        ledger.intent_open('req-1', purpose='FIELD', endpoint_id='f' * 64,
                            control_domain_id='d' * 64, max_reservation_bytes=10,
                            now_utc=0)
        assert ledger.open_intent['request_id'] == 'req-1'
        ledger.intent_closed('req-1', outcome='OK')
        assert ledger.open_intent is None and ledger.closed_count == 1
        # Request ids are permanently retired even after a clean close.
        with pytest.raises(LaunchContractError, match='SHARED_LEDGER_REQUEST_ID_REUSE'):
            ledger.intent_open('req-1', purpose='FIELD', endpoint_id='f' * 64,
                                control_domain_id='d' * 64, max_reservation_bytes=10,
                                now_utc=0)


def test_shared_ledger_rejects_overlap_and_close_without_open(tmp_path):
    root = _root(tmp_path)
    with SharedLedger(root, manifest_sha256=MANIFEST, genesis_reviewed=True) as ledger:
        with pytest.raises(LaunchContractError, match='SHARED_LEDGER_CLOSE_WITHOUT_OPEN'):
            ledger.intent_closed('nope', outcome='OK')
        ledger.intent_open('req-1', purpose='FIELD', endpoint_id='f' * 64,
                            control_domain_id='d' * 64, max_reservation_bytes=10,
                            now_utc=0)
        with pytest.raises(LaunchContractError, match='SHARED_LEDGER_INTENT_OPEN_HELD'):
            ledger.intent_open('req-2', purpose='FIELD', endpoint_id='f' * 64,
                                control_domain_id='d' * 64, max_reservation_bytes=10,
                                now_utc=0)
        with pytest.raises(LaunchContractError, match='SHARED_LEDGER_CLOSE_WITHOUT_OPEN'):
            ledger.intent_closed('req-2', outcome='OK')


def test_shared_ledger_rejects_bad_request_id_purpose_and_cap(tmp_path):
    root = _root(tmp_path)
    with SharedLedger(root, manifest_sha256=MANIFEST, genesis_reviewed=True,
                       max_requests=1) as ledger:
        with pytest.raises(LaunchContractError, match='SHARED_LEDGER_REQUEST_ID'):
            ledger.intent_open('bad id!', purpose='FIELD', endpoint_id='f' * 64,
                                control_domain_id='d' * 64, max_reservation_bytes=10,
                                now_utc=0)
        with pytest.raises(LaunchContractError, match='SHARED_LEDGER_PURPOSE'):
            ledger.intent_open('req-1', purpose='NOPE', endpoint_id='f' * 64,
                                control_domain_id='d' * 64, max_reservation_bytes=10,
                                now_utc=0)
        ledger.intent_open('req-1', purpose='FIELD', endpoint_id='f' * 64,
                            control_domain_id='d' * 64, max_reservation_bytes=10,
                            now_utc=0)
        ledger.intent_closed('req-1', outcome='OK')
        with pytest.raises(LaunchContractError, match='SHARED_LEDGER_REQUEST_CAP_EXCEEDED'):
            ledger.intent_open('req-2', purpose='FIELD', endpoint_id='f' * 64,
                                control_domain_id='d' * 64, max_reservation_bytes=10,
                                now_utc=0)


# ---------------------------------------------------------------------------
# SharedLedger: denial / cooldown semantics
# ---------------------------------------------------------------------------

def test_shared_ledger_explicit_denial_never_auto_resumes(tmp_path):
    root = _root(tmp_path)
    with SharedLedger(root, manifest_sha256=MANIFEST, genesis_reviewed=True) as ledger:
        ledger.intent_open('req-1', purpose='FIELD', endpoint_id='f' * 64,
                            control_domain_id='d' * 64, max_reservation_bytes=10,
                            now_utc=0)
        ledger.intent_closed('req-1', outcome='DENIED',
                              denial=_denial(status='403', retry_after_seconds=None))
        assert ledger.is_blocked('d' * 64, now_utc=0)
        assert ledger.is_blocked('d' * 64, now_utc=10 ** 12)


def test_shared_ledger_finite_cooldown_expires(tmp_path):
    root = _root(tmp_path)
    with SharedLedger(root, manifest_sha256=MANIFEST, genesis_reviewed=True) as ledger:
        ledger.intent_open('req-1', purpose='FIELD', endpoint_id='f' * 64,
                            control_domain_id='d' * 64, max_reservation_bytes=10,
                            now_utc=0)
        ledger.intent_closed('req-1', outcome='DENIED', denial=_denial(
            status='503', retry_after_seconds=5, window_end_utc=100.0,
            receipt_upper_bound_utc=100.0))
        assert ledger.is_blocked('d' * 64, now_utc=104.9)
        assert not ledger.is_blocked('d' * 64, now_utc=105.1)
        with pytest.raises(LaunchContractError,
                            match='SHARED_LEDGER_CONTROL_DOMAIN_COOLDOWN'):
            ledger.intent_open('req-2', purpose='FIELD', endpoint_id='f' * 64,
                                control_domain_id='d' * 64, max_reservation_bytes=10,
                                now_utc=104.9)
        ledger.intent_open('req-2', purpose='FIELD', endpoint_id='f' * 64,
                            control_domain_id='d' * 64, max_reservation_bytes=10,
                            now_utc=105.1)


def test_shared_ledger_denial_without_outcome_rejected_and_ok_forbids_denial(tmp_path):
    root = _root(tmp_path)
    with SharedLedger(root, manifest_sha256=MANIFEST, genesis_reviewed=True) as ledger:
        ledger.intent_open('req-1', purpose='FIELD', endpoint_id='f' * 64,
                            control_domain_id='d' * 64, max_reservation_bytes=10,
                            now_utc=0)
        with pytest.raises(LaunchContractError, match='SHARED_LEDGER_DENIAL_SCHEMA'):
            ledger.intent_closed('req-1', outcome='DENIED', denial=None)
        # A rejected close validates before appending, so req-1 is still
        # open in this same process (only an inherited-on-restart hold is
        # permanent); close it validly before continuing.
        ledger.intent_closed('req-1', outcome='OK')
        ledger.intent_open('req-2', purpose='FIELD', endpoint_id='f' * 64,
                            control_domain_id='d' * 64, max_reservation_bytes=10,
                            now_utc=0)
        with pytest.raises(LaunchContractError, match='SHARED_LEDGER_CLOSE_OUTCOME'):
            ledger.intent_closed('req-2', outcome='OK', denial=_denial())


def test_shared_ledger_denial_evidence_xor_and_bad_status_rejected(tmp_path):
    root = _root(tmp_path)
    with SharedLedger(root, manifest_sha256=MANIFEST, genesis_reviewed=True) as ledger:
        ledger.intent_open('req-1', purpose='FIELD', endpoint_id='f' * 64,
                            control_domain_id='d' * 64, max_reservation_bytes=10,
                            now_utc=0)
        with pytest.raises(LaunchContractError, match='SHARED_LEDGER_DENIAL_STATUS'):
            ledger.intent_closed('req-1', outcome='DENIED',
                                  denial=_denial(status='999'))
        ledger.intent_closed('req-1', outcome='OK')
        ledger.intent_open('req-2', purpose='FIELD', endpoint_id='f' * 64,
                            control_domain_id='d' * 64, max_reservation_bytes=10,
                            now_utc=0)
        with pytest.raises(LaunchContractError, match='SHARED_LEDGER_DENIAL_EVIDENCE'):
            ledger.intent_closed('req-2', outcome='DENIED', denial=_denial(
                evidence_sha256='b' * 64, evidence_missing_cause='both set'))


# ---------------------------------------------------------------------------
# SharedLedger: generalized R2 — inherited open intent is never resolvable
# ---------------------------------------------------------------------------

def test_shared_ledger_inherited_open_intent_held_forever(tmp_path):
    root = _root(tmp_path)
    with SharedLedger(root, manifest_sha256=MANIFEST, genesis_reviewed=True) as ledger:
        ledger.intent_open('req-1', purpose='FIELD', endpoint_id='f' * 64,
                            control_domain_id='d' * 64, max_reservation_bytes=10,
                            now_utc=0)
        # Crash: no intent_closed call, process simply exits.
    with SharedLedger(root, manifest_sha256=MANIFEST) as ledger:
        assert ledger.open_intent['request_id'] == 'req-1'
        assert ledger.inherited_open_request_id == 'req-1'
        with pytest.raises(LaunchContractError,
                            match='SHARED_LEDGER_INHERITED_INTENT_HELD'):
            ledger.intent_closed('req-1', outcome='OK')
        # A second restart inherits the same still-open intent.
    with SharedLedger(root, manifest_sha256=MANIFEST) as ledger:
        assert ledger.inherited_open_request_id == 'req-1'
        with pytest.raises(LaunchContractError,
                            match='SHARED_LEDGER_INHERITED_INTENT_HELD'):
            ledger.intent_closed('req-1', outcome='DENIED', denial=_denial())


# ---------------------------------------------------------------------------
# SessionLedger: lifecycle happy path and refuse/denial terminals
# ---------------------------------------------------------------------------

def test_session_ledger_full_success_lifecycle(tmp_path):
    root = _root(tmp_path)
    with SessionLedger(root, manifest_sha256=MANIFEST) as ledger:
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
    with SessionLedger(root, manifest_sha256=MANIFEST) as ledger:
        assert ledger.completed_count == 1
        assert ledger.inherited_request_id is None


def test_session_ledger_accounted_without_witness_can_reach_terminal(tmp_path):
    root = _root(tmp_path)
    with SessionLedger(root, manifest_sha256=MANIFEST) as ledger:
        ledger.attempt_intent('req-1', purpose='FIELD', endpoint_id='f' * 64,
                               max_reservation_bytes=10)
        ledger.budget_reserved('req-1', reserve_event_hash='b' * 64)
        ledger.dispatch_intent('req-1', measured_start_monotonic=0)
        ledger.transport_closed('req-1', outcome='PARTIAL', total_delivered_bytes=3)
        ledger.accounted('req-1', completion_event_hash='c' * 64)
        # ACCOUNTED (no object witness) is itself a valid terminal predecessor.
        ledger.terminal('req-1', outcome='FAILED', reason='short',
                         report_reserved_bytes=REPORT_RESERVE_BYTES)
        assert ledger.attempt['outcome'] == 'FAILED'
        with pytest.raises(LaunchContractError, match='SESSION_LEDGER_BAD_TRANSITION'):
            ledger.terminal('req-1', outcome='FAILED', reason='twice',
                             report_reserved_bytes=REPORT_RESERVE_BYTES)


def test_session_ledger_refuse_path_and_then_new_attempt(tmp_path):
    root = _root(tmp_path)
    with SessionLedger(root, manifest_sha256=MANIFEST) as ledger:
        ledger.attempt_intent('req-1', purpose='FIELD', endpoint_id='f' * 64,
                               max_reservation_bytes=10)
        ledger.refuse('req-1', reason='SHARED_LEDGER_CONTROL_DOMAIN_COOLDOWN')
        assert ledger.attempt['outcome'] == 'REFUSED' and ledger.completed_count == 1
        ledger.attempt_intent('req-2', purpose='INDEX', endpoint_id='f' * 64,
                               max_reservation_bytes=10)
        assert ledger.attempt['request_id'] == 'req-2'


def test_session_ledger_denial_from_reserved_state(tmp_path):
    root = _root(tmp_path)
    with SessionLedger(root, manifest_sha256=MANIFEST) as ledger:
        ledger.attempt_intent('req-1', purpose='FIELD', endpoint_id='f' * 64,
                               max_reservation_bytes=10)
        ledger.budget_reserved('req-1', reserve_event_hash='b' * 64)
        ledger.denial('req-1', reason='provider denied',
                      shared_denial_event_hash='c' * 64)
        assert ledger.attempt['outcome'] == 'DENIED'
        with pytest.raises(LaunchContractError, match='SESSION_LEDGER_BAD_TRANSITION'):
            ledger.denial('req-1', reason='twice')


def test_session_ledger_report_reserve_mismatch_rejected(tmp_path):
    root = _root(tmp_path)
    with SessionLedger(root, manifest_sha256=MANIFEST) as ledger:
        ledger.attempt_intent('req-1', purpose='FIELD', endpoint_id='f' * 64,
                               max_reservation_bytes=10)
        ledger.budget_reserved('req-1', reserve_event_hash='b' * 64)
        ledger.dispatch_intent('req-1', measured_start_monotonic=0)
        ledger.transport_closed('req-1', outcome='OK', total_delivered_bytes=1)
        ledger.accounted('req-1', completion_event_hash='c' * 64)
        with pytest.raises(LaunchContractError,
                            match='SESSION_LEDGER_REPORT_RESERVE_MISMATCH'):
            ledger.terminal('req-1', outcome='SUCCESS', reason='x',
                             report_reserved_bytes=REPORT_RESERVE_BYTES + 1)


def test_session_ledger_rejects_out_of_order_transitions(tmp_path):
    root = _root(tmp_path)
    with SessionLedger(root, manifest_sha256=MANIFEST) as ledger:
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
    with SessionLedger(root, manifest_sha256=MANIFEST, max_requests=1) as ledger:
        ledger.attempt_intent('req-1', purpose='FIELD', endpoint_id='f' * 64,
                               max_reservation_bytes=10)
        ledger.refuse('req-1', reason='done')
        with pytest.raises(LaunchContractError,
                            match='SESSION_LEDGER_REQUEST_CAP_EXCEEDED'):
            ledger.attempt_intent('req-2', purpose='FIELD', endpoint_id='f' * 64,
                                   max_reservation_bytes=10)
    with SessionLedger(root, manifest_sha256=MANIFEST, max_requests=1) as ledger:
        with pytest.raises(LaunchContractError,
                            match='SESSION_LEDGER_REQUEST_ID_REUSE|'
                                  'SESSION_LEDGER_REQUEST_CAP_EXCEEDED'):
            ledger.attempt_intent('req-1', purpose='FIELD', endpoint_id='f' * 64,
                                   max_reservation_bytes=10)


def test_session_ledger_range_and_optional_digest_validation(tmp_path):
    root = _root(tmp_path)
    with SessionLedger(root, manifest_sha256=MANIFEST) as ledger:
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
    with SessionLedger(root, manifest_sha256=MANIFEST):
        pass
    with pytest.raises(LaunchContractError, match='SESSION_LEDGER_IDENTITY_MISMATCH'):
        SessionLedger(root, manifest_sha256='e' * 64)
    with pytest.raises(LaunchContractError, match='SESSION_LEDGER_IDENTITY_MISMATCH'):
        SessionLedger(root, manifest_sha256=MANIFEST, max_requests=7)
    with pytest.raises(LaunchContractError, match='SESSION_LEDGER_IDENTITY_MISMATCH'):
        SessionLedger(root, manifest_sha256=MANIFEST,
                       report_reserve_bytes=REPORT_RESERVE_BYTES + 1)


def test_session_ledger_expected_head_mismatch(tmp_path):
    root = _root(tmp_path)
    with SessionLedger(root, manifest_sha256=MANIFEST):
        pass
    with pytest.raises(LaunchContractError, match='SESSION_LEDGER_HEAD_MISMATCH'):
        SessionLedger(root, manifest_sha256=MANIFEST, expected_head='f' * 64)


# ---------------------------------------------------------------------------
# SessionLedger: generalized R2 — inherited open attempt is never resolvable
# ---------------------------------------------------------------------------

def test_session_ledger_inherited_open_attempt_held_through_every_method(tmp_path):
    root = _root(tmp_path)
    with SessionLedger(root, manifest_sha256=MANIFEST) as ledger:
        ledger.attempt_intent('req-1', purpose='FIELD', endpoint_id='f' * 64,
                               max_reservation_bytes=10)
        ledger.budget_reserved('req-1', reserve_event_hash='b' * 64)
        # Crash: process exits with the attempt RESERVED, never resolved.
    with SessionLedger(root, manifest_sha256=MANIFEST) as ledger:
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
    with SessionLedger(root, manifest_sha256=MANIFEST) as ledger:
        assert ledger.inherited_request_id == 'req-1'


def test_session_ledger_terminal_attempt_is_not_inherited(tmp_path):
    root = _root(tmp_path)
    with SessionLedger(root, manifest_sha256=MANIFEST) as ledger:
        ledger.attempt_intent('req-1', purpose='FIELD', endpoint_id='f' * 64,
                               max_reservation_bytes=10)
        ledger.refuse('req-1', reason='done')
    with SessionLedger(root, manifest_sha256=MANIFEST) as ledger:
        assert ledger.inherited_request_id is None
        ledger.attempt_intent('req-2', purpose='FIELD', endpoint_id='f' * 64,
                               max_reservation_bytes=10)


# ---------------------------------------------------------------------------
# Shared journal discipline (fail-closed on tamper/corruption/concurrency)
# ---------------------------------------------------------------------------

def test_shared_ledger_tampered_journal_fails_closed(tmp_path):
    root = _root(tmp_path)
    with SharedLedger(root, manifest_sha256=MANIFEST, genesis_reviewed=True) as ledger:
        ledger.intent_open('req-1', purpose='FIELD', endpoint_id='f' * 64,
                            control_domain_id='d' * 64, max_reservation_bytes=10,
                            now_utc=0)
    journal = root / 'gate3_shared.jsonl'
    journal.write_bytes(journal.read_bytes().replace(b'req-1', b'req-x'))
    with pytest.raises(LaunchContractError, match='LEDGER_HASH'):
        SharedLedger(root, manifest_sha256=MANIFEST)


def test_session_ledger_rejects_concurrent_writer(tmp_path):
    root = _root(tmp_path)
    with SessionLedger(root, manifest_sha256=MANIFEST):
        with pytest.raises(LaunchContractError, match='LEDGER_CONCURRENT_WRITER'):
            SessionLedger(root, manifest_sha256=MANIFEST)


def test_ledger_rejects_relative_or_symlinked_directory(tmp_path):
    root = _root(tmp_path)
    link = tmp_path / 'link'
    link.symlink_to(root)
    with pytest.raises(LaunchContractError, match='LEDGER_PATH_SYMLINK'):
        SessionLedger(link, manifest_sha256=MANIFEST)
    with pytest.raises(LaunchContractError, match='LEDGER_DIRECTORY'):
        SessionLedger(os.path.relpath(root), manifest_sha256=MANIFEST)


def test_ledger_rejects_world_or_group_readable_directory(tmp_path):
    root = _root(tmp_path)
    os.chmod(root, 0o750)
    with pytest.raises(LaunchContractError, match='LEDGER_DIRECTORY_PRIVATE_MODE'):
        SessionLedger(root, manifest_sha256=MANIFEST)


def test_ledger_rejects_event_cap_past_fixed_limit(tmp_path, monkeypatch):
    root = _root(tmp_path)
    monkeypatch.setattr('tools.v11_r09_gate3_ledgers.LEDGER_MAX_EVENTS', 1)
    with pytest.raises(LaunchContractError, match='LEDGER_EVENT_CAPACITY'):
        with SessionLedger(root, manifest_sha256=MANIFEST) as ledger:
            ledger.attempt_intent('req-1', purpose='FIELD', endpoint_id='f' * 64,
                                   max_reservation_bytes=10)


def test_ledger_rejects_oversized_record_before_write(tmp_path, monkeypatch):
    root = _root(tmp_path)
    monkeypatch.setattr('tools.v11_r09_gate3_ledgers.LEDGER_RECORD_MAX_BYTES', 32)
    with pytest.raises(LaunchContractError, match='LEDGER_RECORD_TOO_LARGE'):
        with SessionLedger(root, manifest_sha256=MANIFEST) as ledger:
            ledger.attempt_intent('req-1', purpose='FIELD', endpoint_id='f' * 64,
                                   max_reservation_bytes=10)


def test_ledger_write_failure_poisons_instance_but_not_the_durable_journal(
        tmp_path, monkeypatch):
    root = _root(tmp_path)
    with SessionLedger(root, manifest_sha256=MANIFEST) as ledger:
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
    with SessionLedger(root, manifest_sha256=MANIFEST) as ledger:
        assert ledger.attempt is None and ledger.inherited_request_id is None


def test_ledger_rejects_hardlinked_journal_file(tmp_path):
    root = _root(tmp_path)
    with SessionLedger(root, manifest_sha256=MANIFEST):
        pass
    os.link(root / 'gate3_session.jsonl', root / 'extra_hardlink')
    with pytest.raises(LaunchContractError, match='LEDGER_FILE_IDENTITY'):
        SessionLedger(root, manifest_sha256=MANIFEST)


def test_ledger_rejects_torn_final_record(tmp_path):
    root = _root(tmp_path)
    with SessionLedger(root, manifest_sha256=MANIFEST):
        pass
    journal = root / 'gate3_session.jsonl'
    data = journal.read_bytes()
    journal.write_bytes(data.rstrip(b'\n')[:-1])
    with pytest.raises(LaunchContractError, match='LEDGER_TORN_RECORD'):
        SessionLedger(root, manifest_sha256=MANIFEST)


def test_ledger_rejects_capacity_past_fixed_byte_cap(tmp_path, monkeypatch):
    root = _root(tmp_path)
    monkeypatch.setattr('tools.v11_r09_gate3_ledgers.LEDGER_MAX_BYTES', 1)
    with pytest.raises(LaunchContractError, match='LEDGER_CAPACITY_EXCEEDED'):
        SessionLedger(root, manifest_sha256=MANIFEST)


# ---------------------------------------------------------------------------
# Construction-time validation bounds
# ---------------------------------------------------------------------------

def test_shared_ledger_rejects_bad_manifest_boot_id_and_cap(tmp_path):
    root = _root(tmp_path)
    with pytest.raises(LaunchContractError, match='SHARED_LEDGER_MANIFEST_DIGEST'):
        SharedLedger(root, manifest_sha256='not-hex', genesis_reviewed=True)
    with pytest.raises(LaunchContractError, match='SHARED_LEDGER_BOOT_ID'):
        SharedLedger(root, manifest_sha256=MANIFEST, boot_id='', genesis_reviewed=True)
    with pytest.raises(LaunchContractError, match='SHARED_LEDGER_REQUEST_CAP'):
        SharedLedger(root, manifest_sha256=MANIFEST, genesis_reviewed=True,
                     max_requests=0)


def test_session_ledger_rejects_report_reserve_below_floor(tmp_path):
    root = _root(tmp_path)
    with pytest.raises(LaunchContractError, match='SESSION_LEDGER_REPORT_RESERVE'):
        SessionLedger(root, manifest_sha256=MANIFEST,
                      report_reserve_bytes=REPORT_RESERVE_BYTES - 1)
