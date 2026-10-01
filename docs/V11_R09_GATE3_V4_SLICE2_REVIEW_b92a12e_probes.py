"""Reviewer probes for Gate 3 V4 slice-2 repair candidate b92a12e
(tools/v11_r09_gate3_ledgers.py). Synthetic, offline, disposable tmp roots
only. Run with the candidate worktree on PYTHONPATH, e.g.

  cd /home/alphaadmin/AlphaV11_Gate3V4Slice2/Alpha && \
    python <this file>

Part A ports the nine 39b80fa probes (P1-P9) to the repaired API. A
NOT_REPRODUCED line there means the original defect is closed.

Part B probes the repair itself (N1-N4). A DEFECT_REPRODUCED line means the
candidate accepts or refuses a sequence contrary to
docs/V11_R09_GATE3_TRANSPORT_RUNTIME_DESIGN.md sections 3-6.
"""
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.getcwd())

from tools.v11_r09_gate3_launch import LaunchContractError  # noqa: E402
from tools.v11_r09_gate3_ledgers import (  # noqa: E402
    REPORT_RESERVE_BYTES, SessionLedger, SharedLedger,
)

M1, M2 = 'a' * 64, 'e' * 64
CD, EP, G = 'c' * 64, 'f' * 64, '9' * 64
RESULTS = {}


def root():
    path = Path(tempfile.mkdtemp())
    path.chmod(0o700)
    sub = path / 'r'
    sub.mkdir(mode=0o700)
    return sub


def report(name, reproduced, detail):
    RESULTS[name] = reproduced
    print(f'PROBE {name}: {"DEFECT_REPRODUCED" if reproduced else "NOT_REPRODUCED"} - {detail}')


def denial(**kw):
    base = {'status': '503', 'reason': 'overloaded', 'evidence_sha256': 'b' * 64,
            'evidence_missing_cause': None, 'retry_after_seconds': 30,
            'window_end_utc': 1000.0, 'receipt_upper_bound_utc': 1001.0}
    base.update(kw)
    return base


def new_shared(path, boot='boot-A'):
    return SharedLedger(path, boot_id=boot, genesis_review_digest=G)


def reopen_shared(path, head, boot='boot-A'):
    return SharedLedger(path, boot_id=boot, expected_history_head=head)


def session(path, boot='boot-A'):
    return SessionLedger(path, manifest_sha256=M1, boot_id=boot)


def open_intent(s, rid, cd=CD, now=0):
    s.intent_open(rid, purpose='FIELD', endpoint_id=EP, control_domain_id=cd,
                  manifest_sha256=M1, max_reservation_bytes=10, now_utc=now)


def to_dispatched(s, rid, reservation=10):
    s.attempt_intent(rid, purpose='FIELD', endpoint_id=EP, max_reservation_bytes=reservation)
    s.budget_reserved(rid, reserve_event_hash='b' * 64)
    s.dispatch_intent(rid, measured_start_monotonic=1.0)


def to_accounted(s, rid, delivered=10):
    to_dispatched(s, rid)
    s.transport_closed(rid, outcome='OK', total_delivered_bytes=delivered)
    s.accounted(rid, completion_event_hash='c' * 64)


def refused(fn):
    try:
        fn()
        return None
    except LaunchContractError as exc:
        return str(exc)


# ---------------------------------------------------------------- Part A

def p1():
    with new_shared(root()) as s:
        open_intent(s, 'r1')
        s.denial_observed('r1', denial=denial())
        why = refused(lambda: open_intent(s, 'r2', cd='d' * 64))
        report('P1_denial_releases_global_token', why is None, why or 'second intent opened')


def p2():
    path = root()
    with new_shared(path) as s:
        open_intent(s, 'r1')
        why = refused(lambda: s.intent_closed('r1', outcome='AMBIGUOUS'))
        head = s.prev
    with reopen_shared(path, head) as s:
        why2 = refused(lambda: open_intent(s, 'r2'))
        report('P2_shared_ambiguous_close_not_held', why2 is None,
               f'AMBIGUOUS close -> {why}; after restart -> {why2} '
               f'(inherited={s.inherited_open_request_id!r})')


def p3():
    path = root()
    with session(path) as s:
        to_accounted(s, 'r1')
        why = refused(lambda: s.terminal('r1', outcome='AMBIGUOUS_HELD', reason='x',
                                         report_reserved_bytes=REPORT_RESERVE_BYTES))
    with session(path) as s:
        why2 = refused(lambda: s.attempt_intent('r2', purpose='FIELD', endpoint_id=EP,
                                                max_reservation_bytes=10))
        report('P3_session_ambiguous_held_not_held', why2 is None,
               f'AMBIGUOUS_HELD -> {why}; after restart -> {why2}')


def p4():
    with session(root()) as s:
        to_accounted(s, 'r1')
        why = refused(lambda: s.terminal('r1', outcome='SUCCESS', reason='x',
                                         report_reserved_bytes=REPORT_RESERVE_BYTES))
        report('P4_success_without_store_receipt', why is None, why or 'SUCCESS accepted')


def p5():
    path = root()
    with session(path) as s:
        to_dispatched(s, 'r1')
        s.denial('r1', reason='403 seen in headers')
    with session(path) as s:
        why = refused(lambda: s.attempt_intent('r2', purpose='FIELD', endpoint_id=EP,
                                               max_reservation_bytes=10))
        report('P5_session_denial_skips_close_and_accounting', why is None,
               f'{why} (inherited={s.inherited_request_id!r})')


def p6():
    path = root()
    with session(path, 'boot-A'):
        pass
    with session(path, 'boot-B') as s:
        why = refused(lambda: s.attempt_intent('r1', purpose='FIELD', endpoint_id=EP,
                                               max_reservation_bytes=10))
    spath = root()
    with new_shared(spath, 'boot-A') as s:
        head = s.prev
    with reopen_shared(spath, head, 'boot-B') as s:
        why_s = refused(lambda: open_intent(s, 'r1'))
    report('P6_cross_boot_reopen_accepted', why is None or why_s is None,
           f'session -> {why}; shared -> {why_s}')


def p7():
    with new_shared(root()) as s:
        open_intent(s, 'r1')
        s.denial_observed('r1', denial=denial(retry_after_seconds=10 ** 6))
        s.intent_closed('r1', outcome='DENIED')
        why = refused(lambda: open_intent(s, 'r2', now=float('nan')))
        report('P7_nan_clock_bypasses_cooldown', why is None, why or 'opened with nan')


def p8():
    path = root()
    with new_shared(path) as s:
        open_intent(s, 'r1')
        s.denial_observed('r1', denial=denial(status='403', retry_after_seconds=None))
        s.intent_closed('r1', outcome='DENIED')
        head = s.prev
    no_head = refused(lambda: SharedLedger(path, boot_id='boot-A').close())
    with reopen_shared(path, head) as s:
        second_job = refused(lambda: s.intent_open(
            'r2', purpose='FIELD', endpoint_id=EP, control_domain_id='d' * 64,
            manifest_sha256=M2, max_reservation_bytes=10, now_utc=0))
        still_blocked = s.is_blocked(CD, now_utc=10 ** 9)
    bool_genesis = refused(lambda: SharedLedger(root(), boot_id='boot-A',
                                                genesis_review_digest=True).close())
    ok = (no_head is not None and second_job is None and still_blocked and
          bool_genesis is not None)
    report('P8_denial_lineage_caller_asserted', not ok,
           f'reopen without head -> {no_head}; second manifest on same root -> '
           f'{second_job or "accepted"}; 403 domain still blocked: {still_blocked}; '
           f'bool genesis -> {bool_genesis}. Cross-root correlation remains the '
           'stated, out-of-scope limitation')


def p9():
    path = root()
    with session(path) as s:
        to_dispatched(s, 'r1')
        s.transport_closed('r1', outcome='OK', total_delivered_bytes=11)
        s.accounted('r1', completion_event_hash='c' * 64)
        s.object_witnessed('r1', store_receipt_commit_hash='d' * 64)
        why = refused(lambda: s.terminal('r1', outcome='SUCCESS', reason='x',
                                         report_reserved_bytes=REPORT_RESERVE_BYTES))
        s.terminal('r1', outcome='FAILED', reason='overdelivery',
                   report_reserved_bytes=REPORT_RESERVE_BYTES)
    with session(path) as s:
        why2 = refused(lambda: s.attempt_intent('r2', purpose='FIELD', endpoint_id=EP,
                                                max_reservation_bytes=10))
    report('P9_overdelivery_reaches_success', why is None or why2 is None,
           f'SUCCESS -> {why}; next attempt after restart -> {why2}')


# ---------------------------------------------------------------- Part B

# N1: section 3 stops the control domain "for the whole original window" and
# makes the finite cooldown "at least that window end". A denial is received
# during the window, so its receipt bound normally precedes the window end.
# The S7 repair's ordering check refuses exactly that record.
def n1():
    with new_shared(root()) as s:
        open_intent(s, 'r1')
        why = refused(lambda: s.denial_observed('r1', denial=denial(
            window_end_utc=10800.0, receipt_upper_bound_utc=600.0)))
        closed_failed = refused(lambda: s.intent_closed('r1', outcome='FAILED'))
        blocked = s.is_blocked(CD, now_utc=601.0)
        report('N1_in_window_denial_unrecordable', why is not None,
               f'denial received at t=600 for window ending t=10800 -> {why}; '
               f'caller can then close FAILED -> {closed_failed or "accepted"}; '
               f'domain blocked afterwards: {blocked}')


# N2: the S5 repair made a session denial a non-terminal annotation but no
# longer forces a non-success terminal; the module docstring itself says
# "(necessarily non-SUCCESS) terminal".
def n2():
    with session(root()) as s:
        to_dispatched(s, 'r1')
        s.denial('r1', reason='429 seen in headers')
        s.transport_closed('r1', outcome='OK', total_delivered_bytes=10)
        s.accounted('r1', completion_event_hash='c' * 64)
        s.object_witnessed('r1', store_receipt_commit_hash='d' * 64)
        why = refused(lambda: s.terminal('r1', outcome='SUCCESS', reason='x',
                                         report_reserved_bytes=REPORT_RESERVE_BYTES))
        report('N2_denied_attempt_reaches_success', why is None,
               why or 'denied attempt terminated SUCCESS')


# N3: section 4: shared INTENT_CLOSED binds "outcome/denial history and
# accounting head" and "cannot merely mean the caller invoked close()".
# The repaired intent_closed takes only an outcome, and accepts OK even after
# a denial was recorded on the same intent.
def n3():
    with new_shared(root()) as s:
        open_intent(s, 'r1')
        s.denial_observed('r1', denial=denial())
        why = refused(lambda: s.intent_closed('r1', outcome='OK'))
        recorded = s.events[-1]
        report('N3_intent_closed_without_closure_evidence', why is None,
               f'close OK after recorded denial -> {why or "accepted"}; '
               f'recorded event keys: {sorted(recorded)}')


# N4: section 4: "ambiguity or stream/journal violation cannot [complete]".
# After an overdelivery the session still accepts ACCOUNTED (which binds a
# budget completion-event hash) and a FAILED terminal.
def n4():
    with session(root()) as s:
        to_dispatched(s, 'r1')
        s.transport_closed('r1', outcome='OK', total_delivered_bytes=11)
        why = refused(lambda: s.accounted('r1', completion_event_hash='c' * 64))
        report('N4_overdelivery_accepts_accounted', why is None,
               why or 'ACCOUNTED accepted after overdelivery')


for probe in (p1, p2, p3, p4, p5, p6, p7, p8, p9, n1, n2, n3, n4):
    probe()
part_a = [k for k in RESULTS if k.startswith('P')]
part_b = [k for k in RESULTS if k.startswith('N')]
print('SUMMARY_A', sum(RESULTS[k] for k in part_a), 'of', len(part_a),
      'original defects still reproduced')
print('SUMMARY_B', sum(RESULTS[k] for k in part_b), 'of', len(part_b),
      'repair-introduced or remaining defects reproduced')
