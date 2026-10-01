"""Reviewer probes for Gate 3 V4 slice-2 candidate 39b80fa
(tools/v11_r09_gate3_ledgers.py). Synthetic, offline, disposable tmp roots
only. Run with the candidate worktree on PYTHONPATH, e.g.

  cd /home/alphaadmin/AlphaV11_Gate3V4Slice2/Alpha && \
    python <this file>

Each probe prints PROBE <name>: DEFECT_REPRODUCED or NOT_REPRODUCED.
A DEFECT_REPRODUCED line means the candidate accepted a sequence that
docs/V11_R09_GATE3_TRANSPORT_RUNTIME_DESIGN.md sections 3-6 forbid.
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
CD, EP = 'c' * 64, 'f' * 64
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


def open_shared(path, manifest=M1, **kw):
    return SharedLedger(path, manifest_sha256=manifest, genesis_reviewed=True, **kw)


# P1: recording a denial is fused with INTENT_CLOSED, so a denial recorded
# "immediately when headers/status make it known" (section 3) releases the
# single global token before transport closure/settlement (sections 4-5).
def p1():
    with open_shared(root()) as s:
        s.intent_open('r1', purpose='FIELD', endpoint_id=EP, control_domain_id=CD,
                      max_reservation_bytes=10, now_utc=0)
        s.intent_closed('r1', outcome='DENIED', denial=denial())  # headers only
        try:
            s.intent_open('r2', purpose='INDEX', endpoint_id=EP,
                          control_domain_id='d' * 64, max_reservation_bytes=10, now_utc=0)
            report('P1_denial_releases_global_token', True,
                   'second intent opened on unrelated domain while denied transport '
                   'was never closed/settled')
        except LaunchContractError as exc:
            report('P1_denial_releases_global_token', False, str(exc))


# P2: shared AMBIGUOUS close releases the global token and is not a hold.
def p2():
    path = root()
    with open_shared(path) as s:
        s.intent_open('r1', purpose='FIELD', endpoint_id=EP, control_domain_id=CD,
                      max_reservation_bytes=10, now_utc=0)
        s.intent_closed('r1', outcome='AMBIGUOUS')
    with open_shared(path) as s:
        try:
            s.intent_open('r2', purpose='FIELD', endpoint_id=EP, control_domain_id=CD,
                          max_reservation_bytes=10, now_utc=0)
            report('P2_shared_ambiguous_close_not_held', True,
                   'after AMBIGUOUS close and restart, same control domain reopened')
        except LaunchContractError as exc:
            report('P2_shared_ambiguous_close_not_held', False, str(exc))


def session_to_accounted(s, rid):
    s.attempt_intent(rid, purpose='FIELD', endpoint_id=EP, max_reservation_bytes=10)
    s.budget_reserved(rid, reserve_event_hash='b' * 64)
    s.dispatch_intent(rid, measured_start_monotonic=1.0)
    s.transport_closed(rid, outcome='OK', total_delivered_bytes=10)
    s.accounted(rid, completion_event_hash='c' * 64)


# P3: session terminal AMBIGUOUS_HELD ends the attempt and admits the next one.
def p3():
    path = root()
    with SessionLedger(path, manifest_sha256=M1) as s:
        session_to_accounted(s, 'r1')
        s.terminal('r1', outcome='AMBIGUOUS_HELD', reason='x',
                   report_reserved_bytes=REPORT_RESERVE_BYTES)
    with SessionLedger(path, manifest_sha256=M1) as s:
        try:
            s.attempt_intent('r2', purpose='FIELD', endpoint_id=EP, max_reservation_bytes=10)
            report('P3_session_ambiguous_held_not_held', True,
                   'new attempt admitted after AMBIGUOUS_HELD terminal + restart')
        except LaunchContractError as exc:
            report('P3_session_ambiguous_held_not_held', False, str(exc))


# P4: session SUCCESS terminal without OBJECT_WITNESSED (section 6: ACCOUNTED
# without a store receipt proves accounting only).
def p4():
    with SessionLedger(root(), manifest_sha256=M1) as s:
        session_to_accounted(s, 'r1')
        try:
            s.terminal('r1', outcome='SUCCESS', reason='x',
                       report_reserved_bytes=REPORT_RESERVE_BYTES)
            report('P4_success_without_store_receipt', True,
                   'SUCCESS accepted from ACCOUNTED with no store receipt')
        except LaunchContractError as exc:
            report('P4_success_without_store_receipt', False, str(exc))


# P5: session denial after DISPATCHED is terminal: skips TRANSPORT_CLOSED and
# ACCOUNTED, and on restart nothing is inherited although the budget
# reservation was never settled.
def p5():
    path = root()
    with SessionLedger(path, manifest_sha256=M1) as s:
        s.attempt_intent('r1', purpose='FIELD', endpoint_id=EP, max_reservation_bytes=10)
        s.budget_reserved('r1', reserve_event_hash='b' * 64)
        s.dispatch_intent('r1', measured_start_monotonic=1.0)
        s.denial('r1', reason='403 seen in headers')
    with SessionLedger(path, manifest_sha256=M1) as s:
        try:
            s.attempt_intent('r2', purpose='FIELD', endpoint_id=EP, max_reservation_bytes=10)
            report('P5_session_denial_skips_close_and_accounting', True,
                   f'inherited={s.inherited_request_id!r}; next attempt admitted with '
                   'r1 never transport-closed or accounted')
        except LaunchContractError as exc:
            report('P5_session_denial_skips_close_and_accounting', False, str(exc))


# P6: boot identity is recorded but never compared on reopen (section 5:
# cross-boot acquisition is refused; section 3: bind host/boot).
def p6():
    path = root()
    with SessionLedger(path, manifest_sha256=M1, boot_id='boot-A'):
        pass
    try:
        with SessionLedger(path, manifest_sha256=M1, boot_id='boot-B') as s:
            s.attempt_intent('r1', purpose='FIELD', endpoint_id=EP, max_reservation_bytes=10)
        report('P6_cross_boot_reopen_accepted', True,
               'session created on boot-A accepted new attempt on boot-B')
    except LaunchContractError as exc:
        report('P6_cross_boot_reopen_accepted', False, str(exc))


# P7: NaN clock bypasses an active control-domain cooldown.
def p7():
    with open_shared(root()) as s:
        s.intent_open('r1', purpose='FIELD', endpoint_id=EP, control_domain_id=CD,
                      max_reservation_bytes=10, now_utc=0)
        s.intent_closed('r1', outcome='DENIED', denial=denial(retry_after_seconds=10 ** 6))
        blocked_now = s.is_blocked(CD, now_utc=0)
        try:
            s.intent_open('r2', purpose='FIELD', endpoint_id=EP, control_domain_id=CD,
                          max_reservation_bytes=10, now_utc=float('nan'))
            report('P7_nan_clock_bypasses_cooldown', blocked_now,
                   f'blocked at t=0 is {blocked_now}; intent opened with now_utc=nan')
        except (LaunchContractError, TypeError) as exc:
            report('P7_nan_clock_bypasses_cooldown', False, str(exc))


# P8: shared root lineage is caller-asserted: a different manifest is refused
# on the existing root (so the root is not shared across jobs), but a fresh
# directory with genesis_reviewed=True reopens the denied control domain.
def p8():
    path = root()
    with open_shared(path) as s:
        s.intent_open('r1', purpose='FIELD', endpoint_id=EP, control_domain_id=CD,
                      max_reservation_bytes=10, now_utc=0)
        s.intent_closed('r1', outcome='DENIED', denial=denial(status='403',
                                                              retry_after_seconds=None))
    try:
        open_shared(path, manifest=M2).close()
        shared_across_jobs = True
    except LaunchContractError:
        shared_across_jobs = False
    with open_shared(root(), manifest=M2) as s2:
        s2.intent_open('r1', purpose='FIELD', endpoint_id=EP, control_domain_id=CD,
                       max_reservation_bytes=10, now_utc=0)
    report('P8_denial_lineage_caller_asserted', not shared_across_jobs,
           f'existing root usable by a second manifest: {shared_across_jobs}; fresh '
           'genesis_reviewed=True root opened the 403-denied control domain')


# P9: delivered bytes above the attempt's own reservation are accepted and
# the attempt can still reach SUCCESS (section 4: overdelivery halts).
def p9():
    with SessionLedger(root(), manifest_sha256=M1) as s:
        s.attempt_intent('r1', purpose='FIELD', endpoint_id=EP, max_reservation_bytes=10)
        s.budget_reserved('r1', reserve_event_hash='b' * 64)
        s.dispatch_intent('r1', measured_start_monotonic=1.0)
        try:
            s.transport_closed('r1', outcome='OK', total_delivered_bytes=11)
            s.accounted('r1', completion_event_hash='c' * 64)
            s.object_witnessed('r1', store_receipt_commit_hash='d' * 64)
            s.terminal('r1', outcome='SUCCESS', reason='x',
                       report_reserved_bytes=REPORT_RESERVE_BYTES)
            report('P9_overdelivery_reaches_success', True,
                   '11 delivered bytes on a 10-byte reservation reached SUCCESS')
        except LaunchContractError as exc:
            report('P9_overdelivery_reaches_success', False, str(exc))


for probe in (p1, p2, p3, p4, p5, p6, p7, p8, p9):
    probe()
print('SUMMARY', sum(RESULTS.values()), 'of', len(RESULTS), 'defects reproduced')
