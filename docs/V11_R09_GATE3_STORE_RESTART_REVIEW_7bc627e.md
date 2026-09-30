# Gate 3 restart repair review — exact 7bc627e

**CHANGES_REQUIRED — original five reproductions resolved; two remaining P2 defects.**

Independent Astra/high review of Sol/high commit
`7bc627e52eda2142d9a7e5d9596185a6bb463192`, tree
`bfe712bffe47f51b691281eab08e9e12cccb7a08`, against the
[five findings](V11_R09_GATE3_STORE_RESTART_REVIEW_74bd122.md) and
[restart design](V11_R09_GATE3_STORE_RESTART_DESIGN_7b5a235.md).
Candidate files were not edited; the worktree remains clean and unmerged at
`/tmp/alpha-v11-r09-gate3-strict-offline-20260930`.

Independent evidence: **279 affected passes (5.03 s)** and **153 independent
probe passes (5.36 s)**. Three probes deliberately reproduce the two defects;
150 are repair, integrity, uncertainty, ownership or interruption controls.
Passing defect probes are evidence of rejection, not acceptance. Two expected
Python deprecation warnings identify the deliberately exercised multithreaded
fork case. [Reproducible probes](V11_R09_GATE3_STORE_RESTART_REVIEW_7bc627e_probes.py)
and [completed terminal](V11_R09_GATE3_STORE_RESTART_REVIEW_7bc627e_terminal.json)
bind the evidence. Logs: `/tmp/alpha-v11-gate3-restart-review-7bc627e/`.

## Resolved original manifestations

- R1: retained descriptor/head pins on a completely emptied same root refuse
  before metadata mutation; explicitly fresh initialization still works.
- R2: ordinary close waits for the active operation, retains exclusive ownership,
  and rejects recorder reentrancy. The additional fork case below remains open.
- R3: list prefixes are frozen before PREPARE; callback/thread mutation cannot
  change COMMIT or recovered original clocks.
- R4: admitted byte/event reserve survives PREPARE and COMMIT. Just-below refusal
  preserves a healthy store; exact/just-above transactions and reopen pass.
- R5: pinned descriptor binds original host/boot, wrong-host reopen refuses, and
  INIT-only as well as populated cross-boot stores refuse acquisition.

## P2 R6 — inherited calls block on a mutex held by a vanished parent thread

`tools/v11_r09_gate3_store_v1.py:273-281,606-607,689-691`:
`_fork_child` closes inherited descriptors and marks the instance unusable, but
`seal_with_provenance` and `read_receipt` acquire `_mutex` before checking that
state. If another parent thread owned the RLock at fork, its owning thread does
not exist in the child. The child blocks indefinitely before reaching rejection.

Two independent probes pause a parent seal in its recorder, fork from another
thread, confirm child cleanup has completed (`root_fd is None`, `_pid == -1`),
then attempt inherited read or seal. Neither responds; both require bounded
SIGKILL cleanup. The parent's lock stays held and its seal completes after the
probe releases the recorder. This is an availability/ownership-contract defect,
not an observed release of the parent's flock. The explicit design requires
inherited calls to reject; acknowledging Python's general multithreaded-fork
warning does not establish that narrower store guarantee.

Repair: reject foreign/inherited process ownership **before attempting the
instance mutex** on both public operation paths. Keep fork cleanup lock-free and
descriptor-close-only; never LOCK_UN the parent's shared description. Add bounded
read/seal rejection tests while another parent thread holds the mutex, plus
close/fork and parent-ownership controls. Do not weaken normal serialization or
callback rejection.

## P2 R7 — accepted descriptor encoding exceeds recovery's fixed read limit

`tools/v11_r09_gate3_store_v1.py:230-235,362-370,394-395`:
context strings are bounded in characters, canonical encoding uses
`ensure_ascii=True`, and initialization writes the encoded descriptor without
checking its byte length. The newly persisted original host/boot fields further
increase that encoding. Recovery unconditionally limits the descriptor to
4,096 bytes.

The independent probe supplies accepted 128-character non-BMP Unicode strings
for build, clock method and host, retaining the normal boot. Initialization
returns VALID; a complete seal returns ACKNOWLEDGED_THIS_SESSION and readable
bytes. The descriptor exceeds 4,096 bytes. Clean close/reopen with the exact
same context and retained pin fails JOURNAL_OR_IDENTITY_INVALID /
OBJECT_FILE_IDENTITY. No tampering, interruption or changed context is involved.

Repair: use one explicit descriptor byte bound consistently for initialization
and recovery. Validate the canonical descriptor **before creating metadata**;
reject oversized input without leaving partial initialization. Retain the bounded
reader. Test below/exact/above the encoded limit, Unicode expansion, maximum
accepted context, and successful seal/reopen for every accepted case. Do not
silently increase other resource caps or migrate old stores. This is a bounded
format-validation fix, not a request for new real host-attestation functionality.

## Interruption evidence and scope

The independent suite retains 53 prior non-defect controls and adds 14 repaired
finding controls, three defect reproductions, one real owning-process exec
without explicit close, 54 SIGKILL boundary cases and 28 deterministic survivor
cases. SIGKILL covers metadata creation/writes/barriers, PREPARE/object/COMMIT
writes and barriers, link/unlink, recovery writes/barriers, temporary creation,
recorder and caller-return boundaries. The separate persistence model enumerates
volatile byte/name survival around seal/recovery barriers; synced bytes, synced
names and uncertain unsynced states have separate oracles. Unresolved or torn
states preserve names/evidence and remain held. Complete surviving COMMIT retains
original clocks and UNKNOWN historical acknowledgement after recovery.

The author's expanded interruption cases also pass in the affected suite. Its
exec-named test alone only starts another process after close; the independent
exec probe supplies the missing direct CLOEXEC check. No physical power-loss,
filesystem qualification, real clock attestation, provider capture, feature
eligibility, historical runtime-use, learner or budget-completion claim follows.
The earlier accepted release resolution `6ec371e` is unchanged; no full release
rerun was needed for this isolated review.

## Handoff and state

Next: normal substantive **Sonnet/high** repair of R6/R7 in the same held worktree,
limited to the offline store, tests and handoff, followed by fresh different-model
exact-commit review. Preserve the independent reproductions; new regression tests
must assert prompt refusal and bounded initialization, not the defective outcomes.
No candidate merge, publication, provider capture, G3-L, SHADOW or learner action
is authorized. This bounded routed review is complete; no duplicate worker was
launched and no review task is still running.

Recovered main `93a7685` (49 ahead / 0 behind its local tracking ref), clean held
candidate and clean SHADOW worktree. New commissioning writes are only manager /
watchdog statuses; no qualifying forward artifact was found. FINAL-REVIEWED
master SHA-256 matches its pin. V10 demo, PAPER scanner/controller and execution
units are inactive, execution masked; protected model-authority paths absent.
Disk 4.4 GiB free, memory about 875 MiB available at inspection. No V10, AxiomTrade,
service, authority, financial or publication action. No C/J/E/A crossing:
**91/200 (45.5%), formal 1/50; NOT_READY_TO_FUND**.
