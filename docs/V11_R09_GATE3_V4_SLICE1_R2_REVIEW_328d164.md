# Gate 3 V4 slice-one R2 exact-commit review — 328d164 — CHANGES_REQUIRED

Reviewer: Claude Opus 5.5 (different model from the Sonnet writer of
`328d164`). Candidate: `/home/alphaadmin/AlphaV11_Gate3V4Slice1/Alpha`, branch
`r09-gate3-v4-slice1-20261001`, commit `328d164f215f6e7553f7b656c34786249339846b`,
parent `f31305e`. Scope: R2 from the
[6e4c95b review](V11_R09_GATE3_V4_SLICE1_REVIEW_6e4c95b.md) against
[transport runtime design](V11_R09_GATE3_TRANSPORT_RUNTIME_DESIGN.md) section 5.

## Verdict: CHANGES_REQUIRED (R2 still open outside the capacity-cap path)

R2 requires the full reservation to survive restart "across all post-delivery
append failures". Design section 5 adds: "An attempt uncertain after process
failure holds the entire same-manifest budget ... Do not add a recovery path
that calls `complete()` on an old incomplete reservation."

`328d164` writes the `gate3.held` sentinel only when `self.failed` is false,
which covers the capacity-cap refusal correctly. Its stated reason for excluding
the `self.failed` case (the journal bytes "are not provably absent") does not
hold. When `os.write` raises having written nothing, replay is clean. The
[probe](V11_R09_GATE3_V4_SLICE1_R2_REVIEW_328d164_probes.py) reproduces two
cases on the exact commit:

- zero-byte journal write error (synthetic ENOSPC) during `consume('one', b'xyz')`:
  in-process `failed=True`, 3 uncertain bytes, no marker; after reopen
  `complete('one')` **succeeds**, reserved 10 -> 0.
- directory-mode identity fault detected by `_healthy()` inside `consume()`:
  same result after reopen.

A process crash after delivery but before journaling reaches the same state
with no failure handler at all, so no sentinel-only design can close it. A
full disk would also stop the sentinel write.

Other parts of `328d164` look correct on inspection. The capacity-cap marker is
`O_EXCL`, fsync'd and directory-fsync'd. On load it is identity- and
mode-checked, and its key is bound to the replayed `in_flight`. Corruption
fails closed. The marker is never cleared, which fails closed (a held journal
directory needs a fresh manifest/journal). R1 and R3-R6 are untouched.

## Repair candidate 599dfd1 (needs its own different-model review)

The reviewer wrote a narrow follow-up commit on top, `599dfd1`
(tree `a4d716d4`, 2 files, +51 lines). It records the reservation in flight
when a `DurableBudget` opens as `inherited_in_flight`, and
`next_read_limit()`/`complete()` refuse it with `UNCERTAIN_REQUEST_HELD`
(`reserve()` was already blocked by `in_flight`). This needs no disk write and
covers capacity, durability, identity and crash paths uniformly. The sentinel
is kept for its reason code. Every existing reopen-then-complete test already
expected rejection, so no prior assertion was loosened.

Regression `test_inherited_reservation_is_never_completed_after_any_post_delivery_failure`
(`write_raises`, `identity`, `crash`) fails 3/3 on `328d164` and passes on
`599dfd1`. Suites (`/home/alphaadmin/alpha-review-test-venv`): launch +
launch_v4 **99 passed**. Gate 3 family (+ offline_io, restart_composition,
message_sizes, collector, store_v1) **312 passed**, 2 pre-existing fork
DeprecationWarnings. `git merge-tree --write-tree 7ba0199 599dfd1` is clean,
prospective tree `8707f346`.

Because the reviewer wrote `599dfd1`, it cannot accept it. Before integration it
needs a different-model (non-Opus) exact-commit review of `f31305e..599dfd1`
(R2 as a whole), plus a re-check against newer main.
