"""Independent reviewer probes for 58a465f. Run with cwd = candidate worktree.
Each probe prints OK (fail-closed / correct) or DEFECT."""
import os, sys, tempfile, math
from pathlib import Path
sys.path.insert(0, os.getcwd())
from tools.v11_r09_gate3_launch import LaunchContractError
from tools.v11_r09_gate3_ledgers import REPORT_RESERVE_BYTES as R, SessionLedger, SharedLedger

M1, M2 = 'a'*64, 'e'*64
CD, CD2, EP, G = 'c'*64, '1'*64, 'f'*64, '9'*64
H = '5'*64
RES = {}
def root():
    p = Path(tempfile.mkdtemp()); p.chmod(0o700); s = p/'r'; s.mkdir(mode=0o700); return s
def rep(name, ok, detail=''):
    RES[name] = ok; print(f'{name}: {"OK" if ok else "DEFECT"} - {detail}')
def refused(fn):
    try: fn(); return None
    except LaunchContractError as e: return str(e)
def den(**kw):
    b = {'status':'503','reason':'x','evidence_sha256':'b'*64,'evidence_missing_cause':None,
         'retry_after_seconds':30,'window_end_utc':10800.0,'receipt_upper_bound_utc':600.0}
    b.update(kw); return b
def new_shared(p, boot='A'): return SharedLedger(p, boot_id=boot, genesis_review_digest=G)
def reopen(p, head, boot='A'): return SharedLedger(p, boot_id=boot, expected_history_head=head)
def sess(p, boot='A', **kw): return SessionLedger(p, manifest_sha256=M1, boot_id=boot, **kw)
def oi(s, rid, cd=CD, now=0, m=M1, res=10):
    s.intent_open(rid, purpose='FIELD', endpoint_id=EP, control_domain_id=cd,
                  manifest_sha256=m, max_reservation_bytes=res, now_utc=now)
def close(s, rid, outcome, n=0): s.intent_closed(rid, outcome=outcome, accounting_head=H, total_delivered_bytes=n)
def disp(s, rid, res=10):
    s.attempt_intent(rid, purpose='FIELD', endpoint_id=EP, max_reservation_bytes=res)
    s.budget_reserved(rid, reserve_event_hash='b'*64)
    s.dispatch_intent(rid, measured_start_monotonic=1.0)
def tclose(s, rid, n=10, outcome='OK'):
    s.transport_closed(rid, outcome=outcome, total_delivered_bytes=n, denial_history_head=H, accounting_head=H)
def term(s, rid, outcome): s.terminal(rid, outcome=outcome, reason='x', report_reserved_bytes=R)

# ---- R1 (timing semantics), several statuses
def r1_matrix():
    for status, retry in (('503',30),('429',0),('429',None),('503',None),('401',None),('403',5),
                          ('EXPLICIT_DENIAL',None),('OTHER',7),('OTHER',None)):
        p = root()
        with new_shared(p) as s:
            oi(s,'r1')
            why = refused(lambda: s.denial_observed('r1', denial=den(status=status, retry_after_seconds=retry)))
            if why: rep(f'R1_{status}_{retry}', False, f'in-window denial refused: {why}'); continue
            b_now = s.is_blocked(CD, now_utc=600.0)
            fail_close = refused(lambda: close(s,'r1','FAILED'))
            ok_close = refused(lambda: close(s,'r1','OK'))
            close(s,'r1','DENIED'); head = s.prev
        with reopen(p, head) as s:
            b_end_minus = s.is_blocked(CD, now_utc=10799.999)
            open_before = refused(lambda: oi(s,'r2',now=10799.0))
            b_end = s.is_blocked(CD, now_utc=10800.0)
            b_far = s.is_blocked(CD, now_utc=1e12)
            perm = status in ('401','403','EXPLICIT_DENIAL') or retry is None
            ok = (b_now and b_end_minus and open_before == 'SHARED_LEDGER_CONTROL_DOMAIN_COOLDOWN'
                  and fail_close == 'SHARED_LEDGER_CLOSE_OUTCOME_DENIAL_MISMATCH'
                  and ok_close == 'SHARED_LEDGER_CLOSE_OUTCOME_DENIAL_MISMATCH'
                  and (b_end == perm) and (b_far == perm))
            rep(f'R1_{status}_{retry}', ok, f'blocked@600={b_now} @10799.999={b_end_minus} '
                f'@10800={b_end} @1e12={b_far} openBefore={open_before} failClose={fail_close}')

def r1_retry_extends():
    with new_shared(root()) as s:
        oi(s,'r1'); s.denial_observed('r1', denial=den(retry_after_seconds=20000))
        rep('R1_retry_beyond_window', s.is_blocked(CD, now_utc=20599.0) and not s.is_blocked(CD, now_utc=20600.0),
            'cooldown = receipt+retry when later than window end')

def r1_uninvolved_domain():
    with new_shared(root()) as s:
        oi(s,'r1'); s.denial_observed('r1', denial=den())
        held = refused(lambda: oi(s,'r2',cd=CD2,now=601))
        close(s,'r1','DENIED',0)
        rep('R1_global_token_held_after_denial', held == 'SHARED_LEDGER_INTENT_OPEN_HELD', str(held))
        oi(s,'r2',cd=CD2,now=601)
        rep('R1_unrelated_domain_continues_after_close', s.open_intent['request_id']=='r2')

def r1_crash_between_denial_and_close():
    p = root()
    with new_shared(p) as s:
        oi(s,'r1'); s.denial_observed('r1', denial=den(status='429', retry_after_seconds=1)); head=s.prev
    with reopen(p, head) as s:
        a = refused(lambda: close(s,'r1','DENIED'))
        b = refused(lambda: oi(s,'r2',cd=CD2,now=1e9))
        rep('R1_inherited_denied_intent_held', a=='SHARED_LEDGER_INHERITED_INTENT_HELD' and b=='SHARED_LEDGER_INTENT_OPEN_HELD'
            and s.is_blocked(CD, now_utc=10799), f'close={a} open={b}')
    with reopen(p, head) as s:  # second restart
        rep('R1_hold_persists_second_restart', s.inherited_open_request_id=='r1')

def r1_crash_before_denial():
    p = root()
    with new_shared(p) as s:
        oi(s,'r1'); head=s.prev
    with reopen(p, head, ) as s:
        a = refused(lambda: s.denial_observed('r1', denial=den()))
        b = refused(lambda: oi(s,'r2',cd=CD2))
        rep('R1_crash_before_denial_global_hold', a is not None and b=='SHARED_LEDGER_INTENT_OPEN_HELD', f'{a} {b}')

def r1_finiteness_kept():
    bad = [dict(window_end_utc=float('nan')), dict(window_end_utc=float('inf')),
           dict(receipt_upper_bound_utc=float('nan')), dict(receipt_upper_bound_utc=float('-inf')),
           dict(window_end_utc=True), dict(receipt_upper_bound_utc='600'),
           dict(retry_after_seconds=-1), dict(retry_after_seconds=float('nan')),
           dict(retry_after_seconds=float('inf')), dict(retry_after_seconds=True),
           dict(status='200'), dict(reason=''), dict(evidence_sha256=None),
           dict(evidence_missing_cause='both'), dict(extra=1)]
    with new_shared(root()) as s:
        oi(s,'r1')
        acc = [b for b in bad if refused(lambda: s.denial_observed('r1', denial=den(**b))) is None]
        rep('R1_denial_validation_intact', not acc, f'accepted bad: {acc}')
        s.denial_observed('r1', denial=den())
        dup = refused(lambda: s.denial_observed('r1', denial=den()))
        rep('R1_double_denial_refused', dup=='SHARED_LEDGER_DENIAL_ALREADY_RECORDED', str(dup))

def r1_now_validation():
    with new_shared(root()) as s:
        oi(s,'r1'); s.denial_observed('r1', denial=den()); close(s,'r1','DENIED')
        res = [refused(lambda v=v: s.is_blocked(CD, now_utc=v)) for v in (float('nan'), float('inf'), None, '1', True)]
        rep('R1_now_utc_validation', all(r=='SHARED_LEDGER_NOW_UTC' for r in res), str(res))
        res2 = refused(lambda: oi(s,'r2',now=float('nan')))
        rep('R1_nan_open_refused', res2=='SHARED_LEDGER_NOW_UTC', str(res2))

def r1_overflow():
    with new_shared(root()) as s:
        oi(s,'r1'); s.denial_observed('r1', denial=den(receipt_upper_bound_utc=1.7e308, retry_after_seconds=1.7e308))
        rep('R1_overflow_fail_closed', s.is_blocked(CD, now_utc=1e308), 'cooldown=inf blocks')

def r1_later_denial_shortens():
    # after expiry, a second denial with a smaller cooldown overwrites the record
    with new_shared(root()) as s:
        oi(s,'r1'); s.denial_observed('r1', denial=den(retry_after_seconds=100000)); close(s,'r1','DENIED')
        oi(s,'r2', now=100600); s.denial_observed('r2', denial=den(window_end_utc=0.0, receipt_upper_bound_utc=0.0, retry_after_seconds=0))
        close(s,'r2','DENIED')
        rep('NOTE_later_denial_overwrites_cooldown', s.is_blocked(CD, now_utc=50000) ,
            f'non-monotonic now 50000 blocked={s.is_blocked(CD, now_utc=50000)} (record overwritten, not max-merged)')

# ---- boot
def boot():
    p = root()
    with new_shared(p,'A') as s: head=s.prev
    with reopen(p, head, 'B') as s:
        a = refused(lambda: s.is_blocked(CD, now_utc=0)); b = refused(lambda: oi(s,'r1'))
    q = root()
    with sess(q,'A'): pass
    with sess(q,'B') as s:
        c = refused(lambda: disp(s,'r1'))
    rep('BOOT_mismatch_refuses', a==b==c=='LEDGER_BOOT_ID_MISMATCH', f'{a} {b} {c}')
    try:
        SharedLedger(root(), genesis_review_digest=G); d='accepted'
    except TypeError: d='TypeError'
    try:
        SessionLedger(root(), manifest_sha256=M1); e='accepted'
    except TypeError: e='TypeError'
    rep('BOOT_required', d==e=='TypeError', f'{d} {e}')

# ---- session
def s_success_paths():
    out = {}
    with sess(root()) as s:
        disp(s,'r1'); tclose(s,'r1'); s.accounted('r1', completion_event_hash='c'*64)
        out['accounted_success'] = refused(lambda: term(s,'r1','SUCCESS'))
        term(s,'r1','FAILED')
        disp(s,'r2'); s.denial('r2', reason='403'); tclose(s,'r2'); s.accounted('r2', completion_event_hash='c'*64)
        s.object_witnessed('r2', store_receipt_commit_hash='d'*64)
        out['denied_success'] = refused(lambda: term(s,'r2','SUCCESS'))
        out['ambiguous'] = refused(lambda: term(s,'r2','AMBIGUOUS_HELD'))
        term(s,'r2','FAILED')
        disp(s,'r4'); s.denial('r4', reason='x'); out['denial_skip_close'] = refused(lambda: term(s,'r4','FAILED'))
        tclose(s,'r4'); s.accounted('r4', completion_event_hash='c'*64); term(s,'r4','FAILED')
        disp(s,'r3')
        out['refuse_after_reserve'] = refused(lambda: s.refuse('r3', reason='x'))
        tclose(s,'r3'); s.accounted('r3', completion_event_hash='c'*64); s.object_witnessed('r3', store_receipt_commit_hash='d'*64)
        term(s,'r3','SUCCESS'); out['ok_success'] = s.attempt['outcome']
    ok = (out['accounted_success']=='SESSION_LEDGER_SUCCESS_REQUIRES_WITNESS' and
          out['denied_success']=='SESSION_LEDGER_SUCCESS_AFTER_DENIAL' and
          out['ambiguous']=='SESSION_LEDGER_TERMINAL_OUTCOME' and
          out['denial_skip_close']=='SESSION_LEDGER_BAD_TRANSITION' and
          out['refuse_after_reserve']=='SESSION_LEDGER_BAD_TRANSITION' and out['ok_success']=='SUCCESS')
    rep('SESSION_terminal_rules', ok, str(out))

def s_denial_pre_dispatch():
    with sess(root()) as s:
        s.attempt_intent('r1', purpose='FIELD', endpoint_id=EP, max_reservation_bytes=10)
        a = refused(lambda: s.denial('r1', reason='x'))
        s.budget_reserved('r1', reserve_event_hash='b'*64)
        b = refused(lambda: s.denial('r1', reason='x'))
        rep('SESSION_denial_requires_dispatch', a==b=='SESSION_LEDGER_BAD_TRANSITION', f'{a} {b}')

def s_mandatory_heads():
    with sess(root()) as s:
        disp(s,'r1')
        a = refused(lambda: s.transport_closed('r1', outcome='OK', total_delivered_bytes=1))
        b = refused(lambda: s.transport_closed('r1', outcome='OK', total_delivered_bytes=1, denial_history_head=H))
        c = refused(lambda: s.transport_closed('r1', outcome='AMBIGUOUS', total_delivered_bytes=1, denial_history_head=H, accounting_head=H))
        rep('SESSION_close_heads_mandatory', a and b and c=='SESSION_LEDGER_CLOSE_OUTCOME', f'{a} {b} {c}')
    with new_shared(root()) as s:
        oi(s,'r1')
        a = refused(lambda: s.intent_closed('r1', outcome='OK'))
        b = refused(lambda: s.intent_closed('r1', outcome='OK', accounting_head=H))
        c = refused(lambda: s.intent_closed('r1', outcome='AMBIGUOUS', accounting_head=H, total_delivered_bytes=0))
        d = refused(lambda: s.intent_closed('r1', outcome='DENIED', accounting_head=H, total_delivered_bytes=0))
        rep('SHARED_close_evidence_mandatory', a and b and c=='SHARED_LEDGER_CLOSE_OUTCOME' and d=='SHARED_LEDGER_DENIAL_NOT_OBSERVED', f'{a} {b} {c} {d}')

def s_overdelivery():
    p = root()
    with sess(p) as s:
        disp(s,'r1'); tclose(s,'r1', n=11)
        a = refused(lambda: s.accounted('r1', completion_event_hash='c'*64))
        b = refused(lambda: term(s,'r1','FAILED'))
        c = refused(lambda: s.attempt_intent('r2', purpose='FIELD', endpoint_id=EP, max_reservation_bytes=10))
    with sess(p) as s:
        d = refused(lambda: s.attempt_intent('r2', purpose='FIELD', endpoint_id=EP, max_reservation_bytes=10))
        e = refused(lambda: s.accounted('r1', completion_event_hash='c'*64))
        poisoned = s.overdelivery_poisoned
    rep('SESSION_overdelivery_fail_closed', a=='SESSION_LEDGER_OVERDELIVERY_BLOCKS_ACCOUNTED' and b=='SESSION_LEDGER_BAD_TRANSITION'
        and c is not None and d is not None and e is not None and poisoned, f'{a} {b} {c} {d} {e}')
    # exact reservation is not overdelivery
    with sess(root()) as s:
        disp(s,'r1'); tclose(s,'r1', n=10); s.accounted('r1', completion_event_hash='c'*64)
        rep('SESSION_exact_reservation_ok', not s.overdelivery_poisoned)

def shared_overdelivery():
    with new_shared(root()) as s:
        oi(s,'r1', res=10)
        why = refused(lambda: close(s,'r1','OK', n=11))
        rep('NOTE_shared_close_OK_with_overdelivered_bytes', why is not None,
            f'shared INTENT_CLOSED OK with 11 > reservation 10 -> {why or "accepted (token released)"}')

def s_restart_inherit():
    for stage in ('open','reserved','dispatched','denied','closed','accounted','witnessed'):
        p = root()
        with sess(p) as s:
            s.attempt_intent('r1', purpose='FIELD', endpoint_id=EP, max_reservation_bytes=10)
            if stage!='open': s.budget_reserved('r1', reserve_event_hash='b'*64)
            if stage not in ('open','reserved'): s.dispatch_intent('r1', measured_start_monotonic=1.0)
            if stage=='denied': s.denial('r1', reason='x')
            if stage in ('closed','accounted','witnessed'): tclose(s,'r1')
            if stage in ('accounted','witnessed'): s.accounted('r1', completion_event_hash='c'*64)
            if stage=='witnessed': s.object_witnessed('r1', store_receipt_commit_hash='d'*64)
        with sess(p) as s:
            calls = [lambda: s.refuse('r1', reason='x'), lambda: s.budget_reserved('r1', reserve_event_hash='b'*64),
                     lambda: s.dispatch_intent('r1', measured_start_monotonic=2.0), lambda: s.denial('r1', reason='x'),
                     lambda: tclose(s,'r1'), lambda: s.accounted('r1', completion_event_hash='c'*64),
                     lambda: s.object_witnessed('r1', store_receipt_commit_hash='d'*64),
                     lambda: term(s,'r1','FAILED'), lambda: term(s,'r1','SUCCESS'),
                     lambda: s.attempt_intent('r2', purpose='FIELD', endpoint_id=EP, max_reservation_bytes=10)]
            res = [refused(c) for c in calls]
            rep(f'SESSION_inherited_{stage}', all(r is not None for r in res), str(set(res)))

def s_rollback_head():
    p = root()
    with sess(p) as s:
        h0 = s.prev
        s.attempt_intent('r1', purpose='FIELD', endpoint_id=EP, max_reservation_bytes=10)
    a = refused(lambda: sess(p, expected_head=h0).close())
    rep('SESSION_expected_head_checked_when_given', a=='SESSION_LEDGER_HEAD_MISMATCH', str(a))

def shared_reopen_fresh_root_loses_history():
    rep('NOTE_cross_root', True, 'stated limitation in module docstring')

def journal_tamper():
    p = root()
    with new_shared(p) as s:
        oi(s,'r1'); s.denial_observed('r1', denial=den(status='403', retry_after_seconds=None)); close(s,'r1','DENIED'); head=s.prev
    j = p/'gate3_shared.jsonl'; data = j.read_bytes()
    lines = data.split(b'\n')
    # truncate to before denial (valid prefix) -> head mismatch
    trunc = b'\n'.join(lines[:2])+b'\n'
    j.write_bytes(trunc)
    a = refused(lambda: reopen(p, head).close())
    j.write_bytes(data.replace(b'"403"', b'"429"'))
    b = refused(lambda: reopen(p, head).close())
    rep('SHARED_rollback_and_tamper_detected', a=='SHARED_LEDGER_LINEAGE_HEAD_MISMATCH' and b=='LEDGER_HASH', f'{a} {b}')

for f in (r1_matrix, r1_retry_extends, r1_uninvolved_domain, r1_crash_between_denial_and_close, r1_crash_before_denial,
          r1_finiteness_kept, r1_now_validation, r1_overflow, r1_later_denial_shortens, boot, s_success_paths,
          s_denial_pre_dispatch, s_mandatory_heads, s_overdelivery, shared_overdelivery, s_restart_inherit,
          s_rollback_head, journal_tamper):
    try: f()
    except Exception as e: rep(f'{f.__name__}_CRASH', False, repr(e))
print('TOTAL', len(RES), 'OK', sum(RES.values()), 'NOT_OK', [k for k,v in RES.items() if not v])
