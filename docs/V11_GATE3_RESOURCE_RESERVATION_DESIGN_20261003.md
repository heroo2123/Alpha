# Gate 3 host storage and resource reservation design — 2026-10-03

**PROPOSED OFFLINE DESIGN; PENDING INDEPENDENT DIFFERENT-MODEL EXACT REVIEW.**
Author: independent Codex Astra/high architecture worker, normal service mode.
Baseline commit `cf0d88fe732efab4e1c3ed69d67019771c58eb72`, tree
`5a980e6565dce448b95e65d24a0f998737d3da6a`.
Only this document is delivered. This is neither accepted admission policy nor a
resource receipt, host measurement, executable implementation, or launch approval.
**G3-L NO_GO; qualification credit 0; real-example and forward-prediction credit 0.**
No change to protocol ceilings, provider permission, host authority, V10/Axiom,
financial surfaces, or model admission is proposed by implication.

## 1. Admission answer and evidence boundary

A future launch can establish resource fit before DNS only if an exact frozen
plan passes all existing validators **and** a separately reviewed resource
custodian proves that every future allocation has bounded, exclusive backing
on the actual filesystems and memory domain for the whole reservation lifetime.
The dispatcher must verify that custody immediately before its first external
operation. The backing must survive refusal, transport closure, reporting and
crash recovery. A digest, free-space snapshot, quota declaration, advisory lock,
or successful synthetic reducer is insufficient individually or together without
that enforcement and custody evidence.

The proposed proof has three parts:

1. Deterministic offline calculation from exact plan/code/protocol bytes, with
   request/body/time, disk, memory, inode and record/event budgets kept distinct.
2. Reviewed local reservation method and custody evidence, including complete
   competing-consumer scope, followed by actual allocation/verification on the
   selected host. This future operation needs its own authority; none runs here.
3. Bound, expiring revalidation at each allocation/dispatch/decode/seal boundary,
   under the same ownership and accounting epoch, with an already reserved
   refusal path. All unrelated admission gates remain mandatory.

A finite sample cannot establish a floor *throughout* an interval when an
unbounded unrelated writer or memory consumer can run between samples. Therefore
an unqualified or incomplete host-wide consumer boundary means NO_GO, even if
all instantaneous numbers pass. Monitoring detects violations; it does not
retroactively make them safe. If existing authorized host isolation cannot bound
other consumers, future work must remain blocked or seek separately reviewed
host arrangements. This design grants no installation or service-change right.

Only relevant tracked public docs, code and synthetic-test source were inspected.
No private input, retained provider-evidence file, credential, external site or
other worktree was opened. References in inspected public documents were not
followed into those inputs. No `df`, `/proc`, cgroup, mount, clock-service or other
live resource observation was performed; no ledger/store/report constructor,
decoder, live intake, network/provider request or service operation was run.
Historical measurements quoted by the launch-readiness audit are attributed
history, never present host evidence. Source bindings are in section 10.

## 2. What the baseline actually implements

Source IDs below refer to exact-byte entries in section 10. Statements about
current behavior are confined to the inspected code, not inferred qualification.

| Surface | Existing behavior | Consequence for this design |
| --- | --- | --- |
| Frozen collection protocol and addendum [S02, S03] | 2,713 slots; bounded prespecified subset allowed without changing denominator; 3,600 requests, 1 GiB bodies, 10,800 seconds, one in flight, >=2-second spacing, <=30-second request deadline; no retry. At least 2 GiB free disk and 512 MiB available host memory. Addendum/code enforce GEFS <=2 MiB, IFS/AIFS <=4 MiB, index <=3 MiB; these are stricter than the original 16 MiB field ceiling. | Preserve all limits and full-cohort refusal accounting. Unknown sizes reserve ceilings; failed/partial requests count. Raw-size estimates cannot substitute for reservations. |
| Launch-readiness audit [S01] | Dated historical audit, explicitly NO-GO. Describes roughly 2.9 GiB free disk/885 MiB available memory in one historical snapshot and an earlier larger snapshot; explicitly denies those are launch reservations. Earlier runtime/mapping absence findings are historical. | Do not use its numbers as today's measurements or its earlier code-status inventory as baseline truth. Current baseline contains runtime and typed mapping code [S11, S13]. Its storage feasibility warning still follows from the V4 formula. |
| `validate_manifest_v4` [S11], constants [S12] | Recomputes exact ordered request/purpose totals, full denominator, field cap reservations even for shorter ranges, overhead reservations, time feasibility, fixed journal/store caps and a minimum `local_storage_quota_bytes`. Resolves referenced bytes; returns manifest digest. | A quota *number* is not allocated space, a filesystem identity, or launch authority. Receipt integration needs a reviewed schema/path; unknown keys cannot just be added to the closed V4 object. |
| `g3l_prep` [S10] | Nonlaunchable inventory/planner. `plan` reserves four requests per selected slot conservatively; `_resources` uses 64 MiB decoded reserve; `_fits` adds 32 MiB headroom. `check_inventory` treats `storage.live_disk_memory_quota_measurement` as window-scoped, requires correct window, nonfuture observation and observation no earlier than start minus 3,600 seconds. PRE_REVIEW excludes detached review outputs; FINAL requires them but grants no launch. | These planning constants and inventory freshness do not prove physical custody or dispatch freshness. No null template or complete inventory replaces detached review. |
| Fresh-window/readiness proposal [S05, S06] | Pure bounded validator. P1 allows a proposed future window >60 seconds and <=12,600 seconds, within 86,400 seconds of evaluation, same-date 00Z run. Capture derives preceding-date 00Z/14:00/17:00/18:00 and denominator 2,713; capture requires `resources=null`. P1 subtracts outstanding declarations plus 64 MiB disk/128 MiB memory, preserves 2 GiB/512 MiB floors, reports a 3 GiB disk-target shortfall. Snapshot age <=60 seconds is **proposed**, not accepted qualification. | `PROPOSAL_VALID_NOT_EXECUTABLE` still has `resource_qualification=false`, `clock_qualification=false`, `g3l=NO_GO`, zero credit. Caller-supplied `complete=true` and scope bytes cannot prove complete reservation custody. Capture currently performs no resource fit calculation. |
| P1 protocol/checker [S04, S08] | One frozen P1 attempt, 3 MiB body and 60-second stage; campaign 8 attempts/32 MiB/120 seconds, combined with linked capture's 3,600/1 GiB ceilings. 64 MiB physical disk reserve, 128 MiB working RSS, 256 MiB address space, 10 CPU seconds, 1 MiB diagnostic output, 16 MiB report, bounded journals and watchdog. `ResourceObservation` contains only post-reservation disk, post-reservation memory and physical-reservation bytes. | `_check_storage` checks exact scalar shape, persistence-review reference/live-ledger declaration, >=64 MiB and consistency, and floors. It does not acquire or prove physical allocation, memory enforcement, mount scope or custody. Its frozen dates are not renewed by the proposal validator. |
| Synthetic attempt model [S07] | `SYNTHETIC_ONLY`; staged LOCKS/INTENT_ACK/RESERVE_ACK/START/closure/accounting/seal. Reservation acknowledgment requires exactly 1 attempt, 3 MiB, 60 seconds and 16 MiB report. Uses cumulative consumed/outstanding budgets; START rechecks the three resource scalars. DNS/TLS/crash ambiguity retains holds; recovery cannot silently retry. | ACKs are test data, not fsync evidence. No parser/decode or dispatch authority. Its fixed October 2 window remains historical. The illustrative `conservative_stage_fit` helper in S06 is not wired as a qualified clock/time reservation guard in S07. |
| `CapacityPlan` / `GateRuntime` [S13] | Injected `ResourceProbe`; built-in concrete probe is fake, and the interface says real host probe is later work. Constructor and pre-attempt/pre-seal paths subtract a conservative capacity from the snapshot. Constructor derives capacity from remaining requests/events; `_check_capacity_resources` does not decrement that stored capacity after each attempt. Guards run before durable attempt opens; intent/budget reservation precede transport. Immediate dispatch checks repeat time/hold/pacing, **not a fresh resource/custody check after those writes**. | Useful fail-closed arithmetic and accounting, not proof of global physical reservations. Repeated full-capacity subtraction may conservatively overcount already consumed/materialized capacity. Do not fix that by feeding invented free-space values into the existing probe. |
| `ReportSink` [S13] | Uses private directory/held descriptor identities, no-follow files, exclusive flock, `posix_fallocate`, file+directory fsync and allocated-block check for a 16 MiB report. Reopening a sparse reserve re-establishes allocation. Writes report into reserve, truncates, fsyncs, links final name without overwrite, unlinks reserve, fsyncs. | This is actual reservation code, not whole-host qualification. Constructor tests only available space >=16 MiB, not 2 GiB **after** allocation. It and other state-creating constructors must be behind a future pre-allocation floor/custody gate. |
| Store / decoder [S14, S15] | Store seal checks actual store filesystem free bytes against object + three records + report allowance; this is not the 2 GiB floor. A7 checks scratch filesystem via `tempfile.gettempdir()`, parent serialization uses `raw.hex()`/JSON, child has AS/CPU/output bounds and watchdog. `_available_memory()` deliberately always raises `A7_HEADROOM_UNKNOWN`; visible cgroup headroom is diagnostic only. Defaults are 512 MiB child AS +128 MiB headroom. | Store, report, journals and decoder scratch may occupy different mounts. Decoder limits do not prove host-memory floor or complete ancestry; never substitute `_visible_memory_headroom` in real admission. Include parent/child overlap, scratch and report memory. |
| Intake and attempt guards [S09, S13] | `EvidenceIntakeGuard` is optional, frozen to one report; `require_admission` checks its satisfied bit, not ongoing freshness. `AttemptModelGuard` is optional and restricted to a synthetic single remaining INDEX request at its frozen P1 path. `FrozenPlan.from_validated_manifest` projects requests and binds supplemental pins/context/review. | None consumes a resource-custody receipt today. A future mandatory resource gate must add refusal and preserve these restrictions, not promote an optional satisfied report or synthetic guard into real authority. |

## 3. Proposed identities and receipt chain

This section onward specifies a **candidate contract for later review**, not new
accepted policy. Suggested schema name: `ALPHA_V11_GATE3_RESOURCE_RESERVATION_V1`.
Keep it a detached sidecar until an exact reviewed integration selects a versioned
schema. Unknown versions/keys, unresolved inputs and mismatched bytes refuse.

Use canonical UTF-8 JSON with closed schemas, sorted object keys and fixed
separators; preserve schedule array order. Reject duplicate keys, floats for
quantities, booleans-as-integers, negatives, nonfinite values and overflow. Proposed
numeric representation is nonnegative signed-64 integers, byte counts and integer
microseconds; every add, multiply, subtraction and round-up is checked. Parser
size/depth/item/output limits must be frozen before implementation review. Hash
original artifact bytes, not reconstructed prose; store SHA-256 **and byte length**.
Use version/domain-separated hashes of canonical structures for derived IDs.

Define an acyclic binding order:

1. **Resource plan core** binds mode (P1 or capture), campaign/pilot/stage IDs,
   restriction-lineage ID, full slot inventory, ordered request plan and purpose
   totals; absolute run/window/decision/expiry; all hard bounds; exact source,
   executable/dependency/decoder/allocator/probe versions, commits/trees and
   artifact hashes/sizes. Include runtime request pins, ranges, object/index/cache
   identities, allowed root map, terminal-reason precedence and calculator version.
   This core excludes receipt/manifest hashes that do not yet exist.
2. **Reservation instance** binds the core digest, custodian epoch and monotonically
   increasing allocation sequence. Its unique lease ID is not permission to reset
   an existing campaign. Bind an independently retained previous custody/head
   anchor, including an explicit reviewed genesis when none exists. New directory,
   host, PID or campaign spelling cannot erase consumed/outstanding obligations.
3. **Acquisition receipt** binds that instance, exact independently reviewed method
   and custody artifacts, pre/post observation refs, allocation extents and
   amounts, per-filesystem identities, host/boot/namespace/cgroup identities,
   held-owner identity (including process start identity), lock order and fencing
   generation, state-journal heads, and acquisition/expiry clocks. It also binds
   allocation/fsync/identity verification outcomes and full reservation inventory.
4. **Launch envelope** binds exact final manifest, core, acquisition receipt and
   reviewed runtime context with detached report/terminal and reviewer/model.
   A manifest may reference an already existing receipt; it must never require
   that receipt to hash the manifest that contains it. Recompute the manifest's
   resource-relevant projection and compare to the core exactly.
5. **Revalidation records** are append-only outputs referencing the immutable
   envelope, acquisition receipt, prior validation head and request/phase. They
   attest current custody/floors against pre-reviewed freshness predicates;
   future phase observations are not fabricated as review inputs. No mutation
   of the frozen plan or stale receipt date to claim a new window.

Every receipt carries `execution_authority=false`, `provider_authority=false`,
`capture_authority=false`, `host_authority=false`, `qualification_credit=0`.
A prospective result such as `RESOURCE_PREDICATES_SATISFIED` concerns only resource
predicates; launch still requires the separate exact G3-L decision and every
source/restriction/clock/metadata/build gate. A hash chain supplies integrity
linkage, not independent truth, exclusive ownership or rollback resistance.

## 4. Conservative arithmetic and fit predicates

### 4.1 Preserve independent request, body and time accounts

Let N be the number of actual scheduled requests, including all unique overhead,
probes and failures; B = sum of their reservation ceilings. Freeze sharing by
identity, never by a post-response success assumption. V4 currently reserves
`limits.field_bytes[provider]` for each FIELD, `max_index_bytes` for INDEX and
`metadata` for other purposes, even if a field's range is shorter. Enforce both
per-purpose totals and global totals. No anticipated successful response,
compression ratio, historical average or release of unused budget reduces B
before a durable known outcome.

For each stage/campaign/pilot account separately prove:

```
used_attempts + outstanding_attempts + new_attempts <= account_attempt_cap
used_body + outstanding_body + new_body_reservations <= account_body_cap
used_elapsed + outstanding_elapsed + new_stage_time <= account_time_cap
```

Linked preflight consumed/outstanding attempts and bytes also debit the capture
aggregate ceilings [S04]. Do not charge the same stage twice when reconciling a
parent account; receipt entries carry account and contribution identities. A
physical block reservation is not a provider-body debit or a time reservation.
No failure, uncertain closure or new window releases an old accounting hold.

V4's current serial check is:

```
N * request_deadline + (N - 1) * minimum_start_spacing
  + processing_seconds + finalization_seconds <= max_elapsed_seconds
```

It requires processing >=max(60, attempted field count) and finalization >=60.
That is a conservative scheduling expression, not measured decoder throughput.
Future resource review must bound actual critical paths: preparation, persistence,
DNS/TLS through closure, decoder startup/work/kill/reap, report and directory fsync.
Do not silently assume they fit the declared processing allowance. Unbounded
latency means the plan cannot promise completion; bounded abort/failure must still
fit reserved reporting resources. CPU limits are not wall-time bounds.

Proposed phase fit uses conservative UTC intervals and a separately qualified
clock drift/age margin: dispatch lower bound >=start, dispatch upper bound plus
all reserved remaining stage work and drift margin strictly <expiry; remaining
acquisition must fit the acquisition deadline, and complete feature seal upper
bound must be <=decision lower bound. Retain the stricter current V4 serial
check even if a proposed timeline uses the decode hour after acquisition. P1's
60-second stage and 30-second request remain separate. No clock-margin number or
new freshness policy is accepted here; unknown margin means refusal.

### 4.2 Reproduce, then supplement existing storage bounds

For V4, let g0=N; while g>1 replace g with ceil(g/256) and add it to A.
Required store objects O=2N+A. With J=64 MiB, object cap C=4 MiB,
R=storage report reserve (>=16 MiB), D=limits.decoded:

```
Q_v4 = 4*J + R + O*C + D + B
local_storage_quota_bytes >= Q_v4
O <= 4096
4*N + 1 <= 10000
8*N + 1 <= 32768
34*N + 1 <= 131072
```

The last predicates are the manifest checks, not substitutes for the runtime's
stricter prospective journal-byte/event checks. Existing occupied journal bytes,
object counts, external evidence and unresolved reservations must also fit.
Do not use sparse logical sizes or number of available bytes alone to pass inode,
record-count or quota constraints.

`CapacityPlan.for_requests` [S13] uses a different scope. For remaining requests,
K=sum(min(reservation_bytes,32)), H=K+3N budget records; E is the sum of event
root and fanout-tree nodes for the runtime's FrozenEvents; T=3(N+E) store records:

```
Q_runtime = 2*B + 2*E*4194304
  + (H + T + 20*N + 4*N + 4)*65536 + 16777216 + 4096
M_runtime = 2*max(request reservation, default=0) + 4*65536
```

This includes temporary object copies and conservative chunk/closure records;
small chunks cannot be costed as if all were 64 KiB. Runtime additionally checks
remaining journal bytes/events and object counts against existing durable usage.
It is a RAW-path capacity estimate, not a bound for a whole decoder/host.

Future review must reconcile both formulas by an allocation/lifetime table. Each
row identifies filesystem, owner, maximum bytes/inodes/records, start/end phase,
materialized portion, still-unallocated portion, physical backing and code path
that enforces the maximum. Include raw/temp/final copies, decoded outputs and
aggregates, all four journals, clock/header/receipt/descriptor data, evidence
imports, allocation metadata, refusal sentinels, custody journal, report and
finalization scratch. Include bounded parent JSON/hex copies and diagnostic files.
Bytes already present at the snapshot are occupied, not newly free headroom.

The initial conservative candidate may **sum** Q_v4 + Q_runtime plus uncovered
categories, treating overlap as intentional extra margin. This can reject a
feasible host and is not a mandate to physically allocate duplicate copies.
Reducing that sum requires an explicit reviewed overlap proof assigning each
live allocation once and showing both old bounds remain satisfied. Merely taking
max(Q_v4,Q_runtime) is insufficient when their uncovered categories differ.
Never lower accepted caps/calculations to fit a host or reuse P1's 64 MiB as a
capture reservation. No absolute capture reservation is chosen by this document.

Synthetic arithmetic, not host measurements:

| Calculation | Result | Meaning |
| --- | ---: | --- |
| N=2,713 | A=12; O=5,438 | Exceeds 4,096 objects before overhead; full capture cannot pass unchanged V4 capacity. |
| N=333 | A=3; O=669 | Store/journal/report subtotal = 3,091,202,048 bytes, **before D and B**. |
| N=333, deadline=30, spacing=2, processing=60, finalization=60 | 10,774 seconds | Arithmetic illustration only; processing=60 is invalid if field count exceeds 60 and leaves no proof of real work duration. |
| P1: no other outstanding reservations | Need >=2,214,592,512 disk bytes and >=671,088,640 available memory bytes before 64/128 MiB prospective debits | Exact bare-floor arithmetic only, without external demand, metadata overhead or enforcement qualification. |

The historical audit's rough 3.09 GB example is therefore reproducible. Neither
that example nor a current integer that exceeds it demonstrates actual host fit.

### 4.3 Per-filesystem and memory conservation

For each allocation domain f, sample user-usable space from a **held descriptor**
(`f_bavail*f_frsize`), not privileged `f_bfree`, and verify identity and quota
semantics. Define F_f as that usable availability, U_f as all remaining allocations
not already materialized in that snapshot (candidate plus other consumers), and
G_f as a reviewed upper bound on additional external consumption/measurement and
abort lag. Proposed invariant at every boundary is:

```
F_f - U_f - G_f >= floor_f
```

Never add materialized reserve bytes back to F_f. If held extents will be consumed
in place without new allocation, they are already accounted for in F_f and must
not be subtracted again as U_f. If consuming a reserve requires unlinking it and
allocating elsewhere, there is no guaranteed transfer: refuse that mechanism
unless its atomic/exclusive allocation guarantee is independently qualified.
A quota ceiling or admission ledger alone is not a reservation against outsiders.
A preallocated "floor file" occupies space; it does not establish 2 GiB *free*.

Candidate scope: put new acquisition state and disk scratch on one qualified
local persistent filesystem where possible; do not assume paths imply sameness.
For multiple disk filesystems, conservatively preserve >=2 GiB on **each** used
domain (or a stricter manifest floor). This per-domain interpretation is a
proposal requiring review; summing free bytes across mounts is never acceptable.
Quota headroom, inode availability and underlying shared pool constraints must
also admit all remaining allocations and metadata. Shared pools are accounted
once as their own constrained domain as well as enforcing each mount's floor.

For memory, H is genuine host MemAvailable with a qualified complete host view.
For every effective cgroup ancestor j let C_j be independently verified remaining
hard-limit headroom. Reserve peak simultaneous **additional** resident demand P,
other outstanding commitments O_m and bounded external/abort margin G_m:

```
H - P - O_m - G_m >= max(512 MiB, stricter manifest host floor)
C_j - P_j - O_j - G_j >= reviewed cgroup safety margin_j, for every ancestor j
```

The host floor and cgroup fit are separate predicates; swapping one for the other
is invalid. Unknown/hidden ancestors refuse. Swap is not MemAvailable and cannot
be credited as floor capacity. An AS limit is not physical-memory reservation;
RSS samples, page-touching, `memory.max`, `memory.high` or a declared 128 MiB limit
alone do not reserve host headroom against other consumers. Prove both enforcement
and complete custody. Any proposed host/cgroup mechanism needs separate review.

P covers interpreter/dependency startup, parent/child overlap, DNS/TLS buffers,
raw and hex/JSON copies, native full-grid decoder arrays, bounded journal replay,
page cache/dirty writeback, decoded output, monitor/custodian and report serialization.
Peak may use max rather than sum only where enforced serial lifetimes and child
kill/reap prove nonoverlap. The parent's refusal/report memory remains reserved
through cleanup. A tmpfs scratch allocation debits memory and its own capacity;
it cannot be counted as ordinary disk-only space or as durable evidence storage.
A7's 512+128 MiB defaults and the prep planner's 64+32 MiB are different scopes;
neither proves this P or the host floor.

## 5. Filesystem qualification, allocation and custody

The receipt's scope map covers shared denial/history root, session, budget,
object store, report, imported evidence, decoder temp/input/output, diagnostics,
custody metadata and emergency refusal storage. Relative names bind to held root
fds. Record filesystem type and identity, device/inode, mount ID and mount-namespace
identity, owner/mode, quota domain, underlying pool where relevant, host and boot.
Device/inode alone can be reused and cannot identify a mount across reboot.
Validate no symlink/hardlink/path escape, no unexpected nested mount or alias,
private ancestor traversal and identity agreement between names and held fds.
Bind current free inodes and an independently justified metadata/inode reserve.

Initial candidate method should refuse tmpfs for durable state and refuse unknown
network/overlay/CoW/thin-provisioned storage semantics. A later method may qualify
one explicitly, including backing-pool, snapshot/CoW amplification, quota and
persistence guarantees; file size and `st_blocks` alone cannot establish that.
No blanket claim that ext4 or a successful fsync proves power-loss persistence.
Bind reviewed filesystem/mount/device behavior, failure model and limits to
separately obtained evidence. This work performs no such qualification.

Proposed transaction order, to be proved by future implementation/review:

1. Parse bounded public/frozen inputs and recompute demand without acquiring
   network resources or constructing mutating state objects. Validate detached
   approval requirements and all currently available prerequisites. Any DNS
   pre-resolution by constructors, source discovery or dependency startup counts
   as dispatch and is prohibited before admission.
2. Acquire a custodian lease and all relevant reservation/accounting locks in
   one reviewed global order. Existing synthetic lock order is shared, stage,
   session, budget, store [S07]; runtime acquires shared/session/budget/store.
   Adding custody/report locks requires a deadlock and recovery review, not an
   ad hoc second order. Fence older processes and verify independently retained
   head/epoch before they can allocate or dispatch.
3. Establish complete consumer inventory for each filesystem and memory domain,
   including non-Gate-3 work and retained/uncertain reservations. A cooperative
   flock only excludes participants; it does not fence unrelated writers.
   Verify the external-demand upper bounds and effective memory hierarchy.
   Unknown scope, foreign owner, stale anchor or unbounded consumer refuses.
4. Check the full prospective floor **before any allocation**, including state
   initialization and the report constructor. Durable preparation intent uses
   separately provisioned, bounded custodian emergency capacity; without it,
   refuse before preparation. This prevents bootstrapping by first writing an
   unaccounted receipt/journal. Acquire report/refusal capacity first within the
   approved total, then allocate remaining extents and metadata/inodes through
   the qualified method. Every partial preparation is journaled.
5. Prefer consumption in the actual reserved backing, as `ReportSink` does.
   A dummy fallocated file cannot back the current store's independent temp-file
   writes automatically. Preallocating journals also conflicts with current
   EOF/hash-chain parsers unless a reviewed format separates logical used length
   from allocated extent. The future implementation must explicitly solve this
   mapping or retain separately guaranteed capacity for existing writes; do not
   silently change existing append/replay semantics.
6. Verify allocated bytes, identities, locks and ownership under held descriptors,
   fsync files and directories, then acquire post-allocation observations under
   the same scope/epoch. Recompute F-U-G, inode/quota and memory predicates.
   Receipt records exact allocation-to-plan mapping; sparse/truncated/partial
   allocation, unsupported fallocate, ENOSPC/EDQUOT or fsync uncertainty refuses.
7. Seal acquisition receipt and envelope bindings. Immediately before DNS/TLS or
   provider dispatch, revalidate custody, freshness, floors, exact next request,
   remaining counters, restrictions, source/clock approvals and dispatch deadline.
   Persist request intent and budget reservation first; recheck after these writes
   and after dispatch-intent fsync. Hold ownership through the transport boundary;
   passing data to an unguarded transport queue is already dispatch.
8. Keep one request in flight, bound reads/headers/chunks and allocations before
   they occur, and enforce closure/decoder/report limits. Revalidate before each
   decode batch, seal, report and any reservation growth. Stop new operations on
   loss of capacity or custody; close/reap using reserved resources, retain known
   bytes and denials, and persist full-denominator refusal/INCOMPLETE evidence.

The proof obligation is inductive: initial F-U-G and memory invariants hold;
every legal transition consumes only its already charged amount, and all other
consumption is bounded by custody/external-demand evidence. Transition-specific
metadata, fsync, abort and report demands are included. Then the floor remains
available between observations within that qualified failure model. If there is
only a monitor with unknown delay or unknown external growth, no such proof
exists. Arbitrary hardware failure can still prevent persistence; report that
limit and retain uncertainty rather than declaring success.

## 6. Lifetime, revalidation, expiry and recovery

Freeze a resource observation maximum age, custody lease duration, qualification
validity interval, monitoring/abort bound and clock uncertainty/drift policy in
future reviewed method bytes. The existing proposal's 60-second resource age is
a candidate upper bound, not a blanket acceptance rule. The prep inventory's
one-hour pre-window threshold cannot stand in for dispatch revalidation. Use
same-boot monotonic ages plus conservative UTC intervals; reject future samples,
clock reversal, boot changes, negative ages and missing context. Policy/build,
plan, mount, namespace, quota, cgroup, ownership, history-head or backing changes
invalidate the receipt immediately, even before its numeric expiry.

Dispatch permission expires at the earliest applicable plan/window/method/lease/
review/clock limit. Do not allocate for a request whose remaining worst-case
phase work cannot fit. Lease expiry ends dispatch eligibility; it does **not**
release storage, memory obligations, a report reserve or an unfinished intent.
Lifetime must cover setup, the complete window, worst-case close/reap/report and
recovery custody. A persistent parent/custodian retains backing across worker
exit. No receipt may promise release at 17:00 merely because acquisition ends.
If retention cost cannot be borne until explicit reconciliation, refuse upfront.

Proposed reservation states:

| State | Permitted transition / crash interpretation |
| --- | --- |
| `PLANNED_UNQUALIFIED` | No allocation/dispatch credit. Only reviewed preparation can progress. |
| `PREPARING` | Durable intent precedes allocation. Crash may have allocated some/all backing; retain claim, verify by descriptor/extent inventory on recovery. No automatic rollback/refund. |
| `HELD_VERIFIED` | Immutable acquisition receipt, backing and floors verified. A fresh bound revalidation is still needed for each operation. |
| `IN_USE` | Attempt intent/dispatch fence/counters fixed. Consumed allocations and remaining reservation are reconciled without double subtraction. Missing closure means full uncertain request/body/time hold. |
| `UNCERTAIN_HELD` | Any crash, lost lock/fencing owner, stale head, partial write, ENOSPC, clock/boot ambiguity or expired active lease. No new dispatch or retry. Preserve resource and denial obligations until evidence reconciles. |
| `FINALIZING_LOCAL_ONLY` | Transport closed and children reaped or otherwise fenced; only previously reserved reporting/recovery work. Expired windows never restart acquisition. |
| `RELEASED` | Durable independently anchored reconciliation proves no live users/writers, no uncertain intent, complete durable accounting/report or explicit INCOMPLETE disposition, and retention ownership transfer. Release only unused capacity, never delete retained evidence as a budget trick. |

Startup is read-only until it has checked the independent head, held-root identity,
all journals, immutable object hashes, dispatch/closure evidence and reservation
map. Reacquire locks in the reviewed order and verify fencing; never interpret a
missing file or an empty new directory as fresh genesis. Orphan extents stay held;
uncertain materialization is not free capacity. If uncertainty prevents a unique
used-vs-reserved classification, refuse rather than guessing or double-crediting.
A torn acquisition receipt/partial allocation yields no launchable reservation.
Known overdelivery is recorded in full, poisons the session and retains at least
max(reserved, known-delivered) accounting; never clip observed violations.

Recovery after a boot change requires a new qualified host/boot observation and
custody reconciliation linked to the old epoch; it cannot renew the expired
window or clear provider restrictions. A hash chain restored from an older copy
cannot detect its own rollback; an independent anchor is mandatory. Recovery
must not re-run `ReportSink` blindly after partial finalization: truncation before
final link, both names after link, or lost final-directory fsync are distinct
crash states. Classify under exclusive ownership without overwriting a completed
report, claiming unproven durability, or recharging/retrying a completed request.

If the report cannot be persisted, retain the hold and emit INCOMPLETE only where
qualified emergency capacity permits. No process-local success flag substitutes
for durable evidence. If even that path fails, future admission refuses on the
unreconciled intent. No filesystem cleanup, protected-data deletion or stopping
other workloads is authorized by this design.

## 7. Future consumers and deterministic refusals

No baseline validator understands this new receipt. A future reviewed integration
must make receipt validation mandatory at the real entrypoint and at the actual
transport/allocator boundary, with impossible-to-omit dependencies. Keep offline
proposal and synthetic interfaces unqualified. Candidate consumer map:

| Existing boundary | Proposed additional consumption; preserve current checks |
| --- | --- |
| `g3l_prep.check_inventory` [S10] | Resource core/receipt/custody-method/review refs populate the window-scoped storage and quota identities. Existing PRE_REVIEW/FINAL split and no-launch result remain. |
| `readiness_boundaries.validate` [S06] | A separately versioned offline projection may check receipt structure and arithmetic; it must still distinguish SCENARIO/LOCAL_DECLARED from acquired evidence and output zero authority. No added inference from capture `resources=null`. |
| `check_evidence_preflight_package` [S08] | Later reviewed adapter validates full receipt before deriving the three `ResourceObservation` scalars; rejects unqualified/mismatched context. Keep frozen P1 package/dates/limits, independent execution review and restriction reconciliation; a new window requires separately reviewed bytes. |
| Synthetic reducer / `AttemptModelGuard` [S07, S13] | Add synthetic fault cases for reservation identity and revalidation; never promote ACK data, a fabricated genesis or the existing synthetic-only guard to physical admission. |
| `validate_manifest_v4`, `FrozenPlan.from_validated_manifest`, `verify_validated_projection` [S11, S13] | Recompute core projection and quota/time requirements, bind the receipt and detached envelope through a reviewed schema extension/sidecar path. Preserve exact schedule/pins/context and every existing cap. No interpretation of a receipt digest alone as authority. |
| `GateRuntime` / `ResourceProbe` [S13] | Reviewed real probe returns authenticated context through a new bounded interface. Mandatory custody guard runs before constructors/allocations and just before dispatch after journal writes, then before decode/seal/report. Preserve existing conservative probe checks until a separately reviewed materialized-vs-outstanding adapter exists. |
| `EvidenceIntakeGuard` [S09] | Keep its additional refusal and false authority flags; frozen satisfied snapshot cannot replace fresh resource revalidation or supply custody. Do not call real intake merely to inspect design readiness. |
| `ReportSink`, store seal, A7 `run` [S13–S15] | Consume owned reservation portions on the bound actual filesystem and memory domain; revalidate allocation identity. Preserve private-root, journal, fsync and no-overwrite checks. A7 remains `A7_HEADROOM_UNKNOWN` until independent complete-hierarchy evidence and enforcement are accepted. |
| Terminal reporting / replay [S13] | Bind resource refusal reasons, receipt/phase heads and reserved/used/outstanding/released totals to the original full denominator. Resource success never marks a capture eligible. |

Proposed bounded refusal vocabulary below is not a retrofit to existing enums.
Freeze ordering/version in the new contract; report all applicable reasons plus
one stable primary reason. Suggested precedence is identity/custody, accounting,
resource fit, freshness/time, then operation failure. Preserve the original cause
and map unattempted rows into existing terminal categories only through review.

| Proposed reason family | Examples that must refuse |
| --- | --- |
| `RESOURCE_INPUT_INVALID`, `RESOURCE_BINDING_MISMATCH`, `RESOURCE_ARITHMETIC_OVERFLOW` | Noncanonical/oversized data, bool quantity, wrong core/manifest/source/size/hash, unknown version, unsafe arithmetic. |
| `RESOURCE_SCOPE_UNKNOWN`, `RESOURCE_FS_UNQUALIFIED`, `RESOURCE_MEMORY_HIERARCHY_UNKNOWN` | Unmapped scratch mount, shared underlying pool omitted, unsupported allocation semantics, hidden cgroup ancestor, unbounded external consumers. |
| `RESOURCE_CUSTODY_LOST`, `RESOURCE_HISTORY_UNRECONCILED` | No exclusive lease/fence, wrong epoch/owner/head, rollback, orphan/unfinished intent, second consumer claiming same backing. |
| `RESOURCE_RESERVATION_UNPROVEN`, `RESOURCE_RESERVATION_ACCOUNTING` | Sparse or partial backing, unsupported extent transfer, duplicate obligation, mixed snapshot/materialization order, report reserve missing, undeclared temporary copies. |
| `RESOURCE_DISK_FLOOR`, `RESOURCE_MEMORY_FLOOR`, `RESOURCE_QUOTA_OR_INODE`, `RESOURCE_JOURNAL_OR_OBJECT_CAP` | Exact floor minus one byte, insufficient parent/child memory, exhausted quota/inodes/records/events even with spare bytes. |
| `RESOURCE_STALE_OR_EXPIRED`, `RESOURCE_CONTEXT_CHANGED`, `RESOURCE_TIME_DOES_NOT_FIT` | Expired observation/lease/method, reboot/remount, changed plan/build, dispatch or cleanup outside bounds. |
| `RESOURCE_PREPARE_FAILED`, `RESOURCE_PERSISTENCE_UNCERTAIN`, `RESOURCE_LIMIT_BREACH` | Allocation/write/fsync/watchdog failure, observed overdelivery or unbounded allocation after intent. |

Before any intent or possibly external operation, a proven refusal can have zero
new attempt charge. From an ambiguous intent/dispatch boundary onward preserve
existing conservative request/body/time holds. Always retain all pre-existing
charges and restrictions. Resource refusal never grants permission to switch
origin, retry, enlarge ceilings or shrink the denominator.

## 8. Focused synthetic tests and independent review plan

Future implementation testing should use fabricated bytes, fake probes and
filesystem/syscall fault doubles, not real weather packages or host stress. Test
allocation/custody adapters in an explicitly authorized disposable environment
only after design review. No live test is requested or authorized here.

| Test group | Required counterexamples and acceptance evidence |
| --- | --- |
| Exact identities and parser bounds | Mutate one request/purpose/order/range, date, build, mount, receipt byte or length; duplicate keys/IDs, booleans, negative/overflow sums, excessive tree depth and output; all refuse without dispatch or partial admission. |
| Arithmetic cross-check | Independent implementation reproduces O, Q_v4, Q_runtime, time and P1 debits. Check zero/malformed N; fanout boundaries 1/255/256/257; object/record/journal limits; exact floor and floor-minus-one; unknown sizes and short ranges still debit full caps. |
| Materialized vs outstanding | Same reserve already reflected in post-snapshot availability vs still outstanding; partial preparation, earlier snapshot, double claim, duplicate consumer, legitimate shared backing, max-only envelope missing a category. No omission or double credit; do not mask existing runtime subtraction with fake observations. |
| Scope and custody | Same path on different mount/namespace; report/temp on different device; aliases/shared pool; quota or inode exhaustion with ample free bytes; sparse/truncated/hole-punched backing; substituted inode, symlink/hardlink; incomplete cgroup ancestry; AS/RSS declaration without enforcement; external writer exceeds declared bound. Refuse before DNS. |
| Guard order | Spy transport records DNS/TLS/open/send separately. Deny before constructor allocation; revoke lease after budget fsync and after dispatch-intent fsync; all unqualified paths have zero external calls. Kill old fenced writer; lease expiry cannot free backing under it. |
| Peak memory and time | Parent hex/JSON plus child arrays and report overlap; tmpfs double role; tiny body chunks maximize records; finalization/kill-reap at deadline, UTC uncertainty straddling end, stale calibration, boot/clock changes, delayed fsync. Unknown bound refuses, not a smaller guessed reservation. |
| Fault/recovery matrix | Inject failure before/after every allocation, intent, reserve, dispatch, closure, accounting, seal, fsync, report link and release. Recover missing/truncated/rolled-back journals, sparse reopened report, stale custody receipt and two owners. No retry/refund; preserve full uncertain ceilings and original clocks/denials. |
| Terminal evidence and no authority | Resource failures partition all 2,713 slots with stable/all reasons; failed report is INCOMPLETE/held. Expired windows finalize locally only. Every offline/proposal result retains false authority flags and zero qualification. Existing intake/attempt/source/restriction refusals remain effective. |

Existing relevant tests inspected, not executed wholesale: S16 checks readiness
floors/age/context/reservation declarations; S17 `TestP10PhysicalReservation`
explicitly limits the synthetic model to three scalar resource dimensions;
S18 checks exact prospective floors, sparse report reopen, chunk/capacity and
recovery behavior. These are useful regressions, not new host qualification.

Executed for this document: a bounded `python3` process compiled only S06 in
memory (its standard-library-only module; no CLI/import of intake or store),
then tested ten fabricated cases: exact P1 floor, disk minus one, memory minus
one, age over 60 seconds, boot mismatch, incomplete inventory, materialized
reservation, outstanding reservation, duplicate reservation ID, and capture mode
with no resource input. All ten produced expected statuses/reasons and asserted
`g3l=NO_GO`, zero credit and `execution_authority=false`. Four independent integer
checks reproduced O(2,713)=5,438, O(333)=669, the 3,091,202,048-byte subtotal and
10,774-second illustration. No host observation or provider request occurred.
The first invocation used absent `python`; rerun with `python3` passed. No full
suite, physical allocator test, decoder test or persistence qualification ran.

Review must be independent and use a different model from this Astra/high author.
It must bind the **final commit/tree and this document's actual bytes**, inspect
all cited formulas and source bindings, challenge the throughout-floor argument,
resolve schema/custody/open decisions, record executed probes or explicitly
unexecuted documentary checks, findings, verdict and completed terminal. This
writer's static checks are not self-approval. Review this exact candidate before
integration; any subsequent source/design change requires scope reconciliation
and, where bindings change, fresh exact review.

## 9. Unresolved decisions and integration barriers

1. **Custodian authority and complete scope:** which already authorized component
   can fence allocation/dispatch, retain an independent nonrollback head and bound
   every competing host consumer? No such qualification is established here.
   No new service, cgroup or authority installation is implicitly authorized.
2. **Backing mechanism and format:** in-place extents vs independently guaranteed
   pool; inode/metadata reservation; interaction with current EOF journal replay,
   content-addressed temporary writes, report truncation/restart and quota pools.
   Dummy reserves plus unlink-and-reallocate are insufficient.
3. **Memory qualification:** establish complete effective hierarchy visibility,
   peak parent/child/native/cache/refusal demand and enforceable external demand
   bounds. A7's deliberate unknown-headroom refusal remains a blocker.
4. **Envelope/schema:** select the reviewed sidecar/version and mandatory consumers;
   resolve V4/runtime envelope overlaps without weakening either; set parser
   limits and deterministic resource refusal mapping. No code change is delivered.
5. **Freshness and temporal proof:** independently qualify observation age, lease,
   monitoring/abort interval, drift and work-duration bounds, including setup and
   final fsync. Proposed 60-second snapshot age alone supplies none of these.
6. **Filesystem scope:** accept or revise the proposed 2 GiB-per-used-filesystem
   rule; select actual private roots and qualified filesystem/persistence method
   in later authorized work. No path or live storage identity is invented here.
7. **Exact launch packet and other gates:** genuine source/clock/restriction/cohort
   evidence, fresh frozen window, independent execution/G3-L review and all
   accepted protocol requirements remain separate. This public-only design does
   not inventory or judge the current private package and clears no prior hold.

**Conclusion: NO_GO, zero qualification/capture/learner/forward credit.** The
concrete candidate is a reviewed-plan calculation plus an expiring, independently
anchored reservation/custody receipt and mandatory operation guards. The receipt,
its actual measurements, the enforcement implementation and its independent
qualification do not exist as outputs of this task. Independent different-model
exact review is required before integration; this author does not self-approve.

## 10. Exact public source bindings

All entries are tracked files at the baseline commit above. SHA-256 and byte
length bind the complete raw file even when only the relevant sections/symbols
were inspected. They are source identities, not measurements or source-policy
acceptance. No retained evidence file or linked private artifact is in this list.

| ID | Public repository path | Bytes | SHA-256 |
| --- | --- | ---: | --- |
| S01 | `docs/V11_GATE3_LAUNCH_READINESS_AUDIT_20261001.md` | 17595 | `d135c7480cfb2afb48ba745dc460ae70b7f50d2bfb194a07b9480851301e855f` |
| S02 | `docs/V11_R09_GATE3_COLLECTION_PROTOCOL.md` | 20041 | `da3144c558134e7bd6a06b5c3f298740e86cee13be7c54a9932204362c269524` |
| S03 | `docs/V11_R09_GATE3_LAUNCH_CONTRACT_ADJUDICATION.md` | 17309 | `a4a2a18e83a53359e3a46cdf7cbda6031c6afec74e5d497f2cd126b1ae7b943c` |
| S04 | `docs/V11_R09_GATE3_EVIDENCE_PREFLIGHT_PROTOCOL_20261002.md` | 20274 | `ae59812fa58ec41895b87408f0ea175988ae6d20a1f4d5dabcd96a01c2dd3a68` |
| S05 | `docs/V11_GATE3_READINESS_BOUNDARY_IMPLEMENTATION_HANDOFF_20261003.md` | 17759 | `d9f4d3783a1dbdb8b96cb2bfd18ab210cb416124accbbb7eb4b94f1b35fd587c` |
| S06 | `tools/v11_gate3_readiness_boundaries.py` | 21121 | `50ba2c894f9cc20dee8349498dab2aab4a3f5b25e48680436929ba545d8e4e69` |
| S07 | `tools/v11_gate3_preflight_attempt_model.py` | 42781 | `4734e9a1386e5cb96a73fa3ba8959977f9756026d1adace2b985ba3cd388a463` |
| S08 | `tools/v11_gate3_evidence_preflight_checker.py` | 45681 | `6df49d57b6807d1b6d47075520d45c493f33550baaee1317d416e4d2d6347355` |
| S09 | `tools/v11_gate3_evidence_intake_guard.py` | 7082 | `009c45eea8aad75cc118591b8cbbe0e7065df6e25f95144d43b08b2ab3123670` |
| S10 | `tools/v11_r09_gate3_g3l_prep.py` | 31205 | `a5051a65aa219d40f5ac67acf9ca6f1e72839eb1d988ae5d08b2ea0565e89481` |
| S11 | `tools/v11_r09_gate3_launch_v4.py` | 47602 | `a2fe4d99666ef744927cfba49de03726b3fc695a98e7836dc1f3642781cbe978` |
| S12 | `tools/v11_r09_gate3_launch.py` | 57812 | `d126744f3905b65b97dd19ccddc787d4bcdd71fca88288c1504966b0fb2681b8` |
| S13 | `tools/v11_r09_gate3_runtime.py` | 120122 | `276d9779b1bcf1a6ca0b59b284d47ebd1540600c0280c57669ee6a6c589fcd3c` |
| S14 | `tools/v11_r09_gate3_store_v1.py` | 34393 | `cf65d164dba4a1937fc38a4cfa18cf686c53b248e97b15ac21d8a7d4bee0d636` |
| S15 | `tools/v11_r09_gate3_a7_decoder.py` | 17101 | `38425ed5a02cdc0067216f513ba6a2bea0de83bc40c20566297c0b071243fb20` |
| S16 | `tests/test_v11_gate3_readiness_boundaries.py` | 26263 | `6b99a704ca2b4850356573e79c922c705014fbf2e6137e30588b11d20e52f579` |
| S17 | `tests/test_v11_gate3_preflight_attempt_model.py` | 71320 | `72ad74304cd47278bd1f9d386bb69b846ec897461079a7ab49949199a39576df` |
| S18 | `tests/test_v11_r09_gate3_runtime.py` | 96474 | `8ef5c9c629dd2ca56e7527190229fedaadf9a5f0efd33c74804c8d368076e466` |

Artifact checks: all 18 source hashes/sizes were recomputed and their bytes matched
`git show` at the baseline. Existing tracked files and the initial index were
unchanged. `git diff --check` and `git diff --cached --check` passed; the staged
scope contained exactly this one new handoff. These are author checks only.
The final commit/tree identities are supplied in the worker's completion message
so this document does not introduce a self-referential commit binding.
