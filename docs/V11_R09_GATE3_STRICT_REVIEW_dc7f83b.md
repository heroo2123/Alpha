# Independent G3-I strict offline review — dc7f83b

**CHANGES_REQUIRED; review COMPLETED. Do not merge this candidate.**
Astra/high independently reviewed Sol/high's exact commit
`dc7f83bf8076747742dc983f3be3238de24845e0`, tree
`5e440cb96aa0b3c7d9a906e93c36c6ddb3ccb1fd`, in
`/tmp/alpha-v11-r09-gate3-strict-offline-20260930` on 2026-09-30.
The candidate was clean before and after review. No candidate code was edited.
This review covers the offline strict manifest validator and durable budget
extension against the independently accepted launch-contract addendum. It
neither authorizes G3-L/network capture nor accepts transport/decoder/clock
integration, which does not exist in this candidate.

## Evidence and scope

- Independently reran `tests/test_v11_r09_gate3_launch.py` and
  `tests/test_v11_r09_gate3_collector.py` using
  `/home/alphaadmin/AlphaV11_Dev/venv/bin/python`: **62 passed in 0.89 s**.
- Added independent synthetic adversarial probes outside the candidate:
  **12 passed in 0.71 s**. Ten tests deliberately reproduce acceptance or
  persistence defects; passing these tests is evidence of defects, not a
  release PASS. The other two validate actual abrupt-process-exit recovery
  and real Git SHA-256 code-object resolution.
- Reproducer and complete concise logs are preserved in
  [review evidence](review-evidence/g3i-dc7f83b/test_adversarial.py),
  [adversarial log](review-evidence/g3i-dc7f83b/adversarial.log), and
  [focused log](review-evidence/g3i-dc7f83b/focused.log). Run the reproducer
  with the project venv's `python -m pytest -q` against this unchanged
  worktree. It imports the author's synthetic fixture and monkeypatches
  protocol pins only for those synthetic manifests; the production pin test
  in the 62-test run checks the actual repository constants. No real dossier,
  private launch manifest, remote service or network request was fabricated.
- An initial probe expected budget-exceeded on reopening the fsync-corrupted
  journal; actual replay rejected the earlier duplicated sequence instead.
  The probe expectation was corrected to `JOURNAL_SEQUENCE`; the accounting
  bypass before reopen reproduced in both runs. Initial result: 11 passed,
  1 failed assertion; final result above.

All implementation line references below refer to
`tools/v11_r09_gate3_launch.py` at the exact reviewed commit.

## P2-1 — Persistence failures leave the budget object usable

Lines 570–578 append and fsync before updating memory, but never latch a
failed state on write/fsync failure. At a one-byte budget, reserve one byte,
then inject an fsync error in `consume('one', b'x')`. The byte was delivered
and the record was written, yet `received` remains zero. After catching the
error, `complete('one')` succeeds and releases the reservation; a second
one-byte request, receipt and completion also succeed. Two bytes have been
received against a one-byte cap. Reopen then rejects `JOURNAL_SEQUENCE`,
but this is too late to prevent the extra request. A separate short-write
probe leaves a torn record and still permits another reservation in the same
object. These failures require no hostile caller or network: ordinary I/O
failure plus error handling is sufficient.

Repair: permanently poison that instance on any append/durability failure;
reject all subsequent reservation, read, consume and complete operations.
Preserve uncertain reservations, known delivered-byte accounting and an
explicit uncertainty outcome where durability cannot be established. Do not
silently repair/truncate the journal or release uncertain charges. Test
short writes and failures of write/fsync at reserve, chunk, violation and
completion boundaries, including restart behavior.

## P2-2 — Existing journal/lock files bypass file-isolation checks

Lines 514–526 use `O_NOFOLLOW`, but do not validate opened lock/journal files'
regular-file type, uid, private mode or link count. Unlike `_resolve_refs`,
they accept a hardlinked existing journal. The probe links an unrelated empty
synthetic file into `gate3.jsonl`; constructing the budget appends the init
record to that unrelated file. No real protected file was used. Parent-path
symlink handling and constructor fd cleanup also need to be made consistent
with the storage contract before reuse by integration.

Repair: validate both opened files with descriptor-based checks before writes,
including regular file, owner, 0600 mode and single link; bind the canonical
private directory identity and reject ancestor symlink/path escape cases.
Ensure every failed construction closes all descriptors. Keep the lock and
journal inode identities stable; add hardlink, wrong-mode/type and failure
cleanup tests. No cleanup of unrelated files is authorized.

## P2-3 — Native inventory accepts wrong cycle times and JSON types

Lines 363–374 check only the UTC hour, not minutes/seconds. All three runs at
**00:59:59** pass with a freshly computed 2,713-slot list/digest. This is not
the frozen 00Z native run. Python equality also accepts `[False]` for allowed
cycles and booleans in native member/hour positions. The boolean-slot probe
passes despite the manifest's actual canonical slot-list hash differing from
`slot_inventory_sha256`: lines 419–420 hash reconstructed `expected`, not the
provided list after strict type validation.

Repair: reconstruct exact run candidates with Gate 2's conservative decision
bound and require full epoch equality, including minute/second zero. Validate
all slot/policy scalar types explicitly (bool is not int); hash the exact
validated canonical inventory. Preserve provider order, cardinalities
775/1,275/663, native cadences, and no run fallback. Add negative tests for
nonzero seconds/minutes, bool/float aliases and actual-list digest mismatch.

## P2-4 — Schedule cannot bind requests to objects or prove feasibility

Lines 434–454 give overhead requests no provider, origin, object, range or
cache identity. One INDEX/OBJECT_ID/METADATA trio can serve GEFS slot 0,
IFS slot 775 and AIFS slot 2050, and the validator accepts it. Consequently
unique index/object costs and correct prerequisites cannot be verified.
Non-field prerequisites accept nonexistent references and wrong types.
Sources/network also accept empty path templates (lines 351–354/386–388),
so this is not a fully constrained request plan.

Separately, lines 464–465 multiply request count by deadline while ignoring
start pacing. Four requests with a one-second deadline and **10,801 seconds
between starts** pass a 10,800-second window. They cannot fit even with
instantaneous responses. These are offline planning defects independently
of the future transport's runtime checks.

Repair: encode explicit provider/origin/object/index/cache/request identities
and range bindings; validate every prerequisite as an earlier compatible
request, allowing sharing only for the same proven object. Reject empty or
unconstrained paths. Calculate conservative serial time using both pacing
and request deadlines, plus the bounded processing/finalization plan. Recheck
all-purpose byte reservations and report capacity without raising any cap.
A schedule schema change remains offline and needs fresh exact-commit review.

## P2-5 — Pinned timezone bytes do not govern date validation

Lines 275–286 load the ambient `ZoneInfo(cohort['timezone'])`; `tzdata` is
only an opaque hashed object reference. The probe changes timezone to
America/New_York and adjusts local-day bounds by five hours while preserving
a referenced object containing only `synthetic artifact only`. Validation
passes, so it has not checked DST/fractional-offset semantics against pinned
tzdata as required by the addendum. Hosts with differing timezone databases
could evaluate the same manifest differently.

Repair: resolve the referenced zone data and compute from its validated bytes,
or prove an explicit version/file hash match to the exact ambient data used;
reject mismatches and test DST and fractional offsets. More generally, the
current resolver proves artifact bytes/length/privacy, not their meaning:
review reports/terminals, source identity, release/access evidence, buckets,
clock evidence and dependency locks require typed semantics or explicit,
separately reviewed binding before any completeness/G3-L claim. This review
does not treat arbitrary correctly hashed documents as genuine evidence.

## Controls that work and limits of acceptance

The original/addendum repository pins match. Real SHA-1 OIDs and source-byte
checks work in the existing suite; the independent SHA-256 repository probe
confirms `_validate_code` handles that object format. This does not imply the
whole protocol, which pins existing SHA-1 history, can be validated in an
unrelated SHA-256 repository. The fixed ordinary integer inventory has 2,713
slots, shortened inventory is rejected, canonical JSON rejects duplicates,
and explicit authority flags remain false. A valid return is a digest and
there is no transport or launch entrypoint.

An actual subprocess exited with `os._exit(17)` after durable reserve/chunk.
Reopening retained one received byte and the two-byte uncertain reservation;
a new request was refused. Changing boot identity was also refused. Existing
concurrency, exhausted-budget and over-allowance receipt controls pass under
successful journal I/O. These controls do not resolve the five findings.
No broad/full release rerun was warranted after these localized blockers.

## Disposition and next action

Keep `dc7f83b` unmerged and preserve its clean worktree. Route normal substantive
offline repair to Sonnet/high in that same worktree, with the five findings and
this reproducer as the acceptance checklist. Convert defect reproductions to
negative regression tests and obtain a new independent exact-commit review
before local integration. Do not self-approve repaired executable bytes.
The existing separate Brain-readiness worker remains active; do not duplicate
or overwrite its untracked implementation/tests.

Main was clean at `6578610` before this review record, 26 ahead/0 behind its
local upstream tracking ref (no remote refresh or push). The publication hold
remains. GEFS SHADOW worktree is clean at `15e99bd`; newer commissioning files
are watchdog/terminal-manager status only, not qualifying forward evidence.
PAPER scanner/controller are inactive, V11 execution masked, V10 demo inactive
and disabled. Protected authority directories are absent. The FINAL-REVIEWED
master hash still matches `a0e16d9bd7344c943a54a16a53c6757662363d93642f6e5cb7953cd047659b4a`.
Inspection showed 4.5 GiB free disk and about 905 MiB available memory. No
service, authority, credential, financial, AxiomTrade or publication action.
**91/200 (45.5%), formal 1/50; NOT_READY_TO_FUND**, unchanged.
