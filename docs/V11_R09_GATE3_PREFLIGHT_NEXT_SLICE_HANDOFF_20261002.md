# Gate 3 next slice: bounded offline attempt model

Astra/high architecture and acceptance handoff, 2026-10-02. Base:
`86e8784ce4bc626e1075ff7cf8dd135127f3cdc4`. **Proposed implementation scope only;
no executable preflight PASS or request authority.** Implement the synthetic
reservation/transport-event/state model below in a new isolated candidate,
then obtain different-model exact-commit review. Do not implement real dispatch.

## Basis and frozen boundary

Read and retain these controlling sources:

- [Public P1 protocol](V11_R09_GATE3_EVIDENCE_PREFLIGHT_PROTOCOL_20261002.md),
  especially sections 2–7, and [public binding metadata](V11_R09_GATE3_EVIDENCE_PREFLIGHT_PACKAGE_20261002.json).
- Accepted Sol/high design review of `27513d2`/tree `fcf1346d`:
  `/tmp/alpha-v11-preflight-review-27513d2-final.review.md` and its adjacent
  `.verdict.json`/`.terminal.json`. Verdict `PASS_IN_SCOPE_DESIGN_BLOCKED_PACKAGE`;
  original process exit 0. Report SHA-256
  `b6d47f324a55e08ba27b38b9a78d9ae95ffb830fa0def3f76208d09fae884933`;
  verdict `8a97f168e1a958f4bf66c2a59c8ea7c6e3939eca609160d3607082896063a8a0`.
  Acceptance is also recorded in [engineering progress](V11_ENGINEERING_PROGRESS.md).
- [Integrated checker](../tools/v11_gate3_evidence_preflight_checker.py), its
  [tests](../tests/test_v11_gate3_evidence_preflight_checker.py), and latest
  [92024e9 review](V11_R09_GATE3_PREFLIGHT_CHECKER_REVIEW_92024e9.md),
  [verdict](V11_R09_GATE3_PREFLIGHT_CHECKER_REVIEW_92024e9.verdict.json) and
  [original terminal](V11_R09_GATE3_PREFLIGHT_CHECKER_REVIEW_92024e9.terminal.json).
  Reviewed tree `361b5aab75bf115d9c5d60abf26c95822e3bb36f`; PASS_IN_SCOPE is
  checker-only. Retained validation: 472 tests, 11,692 checker probes, actual
  package refused with 22 blockers. These are prior results, not reruns here.

Both report/verdict seals and the public protocol binding were checked locally
for this handoff. Private paths in metadata were not dereferenced. Public binding
identifies package SHA-256 `c7d1420fa4e539810f181c244d57b260ced5dc075b0eb9a11351be5f345e2ab8`
(11,480 bytes) and history `fcf4c751a9d591c09e397afd6633efa8a4a6f70312d037d6b64c619579d55636`
(4,133 bytes); this is not a new verification of private contents.

The frozen campaign is `alpha-v11-evidence-preflight-20261002`, request
`p1-gefs-2026100200-c00-f024-index`, October 2 00Z control member/f024,
10:00 inclusive–13:30 exclusive UTC. At the local observation
`2026-10-02T13:18:53Z`, expiry had not yet occurred; remaining wall time did not
satisfy any blocker. The accepted design report calls the window expired even
though its terminal is 10:16 UTC: use the explicit window and measured interval,
not that narrative, to decide expiry. At/after 13:30 it necessarily refuses.
Never use a synthetic interior timestamp as current dispatch evidence.

The retained 22 reasons comprise the binding's 12 null prerequisite references
(including accepted design review, still null in the old bytes), plus
`GEFS_LINEAGE_UNRESOLVED`, `GEFS_STATUS_NOT_ADMISSIBLE`, `UNRESOLVED_GEFS_SCOPE`,
`NULL_COMPLETE_LINEAGE_REVIEW`, `NULL_SHARED_HISTORY_HEAD`,
`NULL_UNRESOLVED_ATTEMPT_RECONCILIATION`, `MISSING_EXECUTION_REVIEW`,
`MISSING_LIVE_LEDGER`, `MISSING_STORAGE_PERSISTENCE_REVIEW`, and
`NO_PHYSICAL_STORAGE_RESERVATION`. Expiry or bad observations can add reasons;
22 is the retained review baseline, not a promise about a current rerun.

All September 30 503/503/429 holds remain: 07:48:13.707996Z,
07:54:39.435641Z, 07:55:15.376499Z. IFS/AIFS and all ECMWF origins remain held;
missing Retry-After/expiry stays unknown. GEFS access, endpoint mapping, complete
lineage and relationship to shared provider/host controls remain unqualified.
No hostname/model/bucket/worker change, elapsed time, success or empty cooldown
file releases a hold. No failover. **G3-L NO-GO; 77 missing PRE_REVIEW identities;
zero of 2,713 slots; 91/200, formal 1/50; NOT_READY_TO_FUND.** A2/A3 UNQUALIFIED,
A4 OPEN, A8 UNQUALIFIED; no G3-E, Brain, SHADOW or financial credit.

## Smallest useful implementation

The checker validates supplied bytes/observations and a replay list. It does not
implement reservation ownership, eager denial retention, delivered-byte charging,
closure settlement or crash recovery. Those transitions are the next safe gap.
No external evidence is needed to test a model of them. Native index semantics,
real resolver/TLS/framing, physical persistence and resource enforcement remain
separate prerequisites; do not pretend synthetic acknowledgements qualify them.

Add only these files in the future implementation candidate (names are reserved
by this plan, not existing links):

| File | Interface and responsibility |
| --- | --- |
| `tools/v11_gate3_preflight_attempt_model.py` | Frozen builtin-only data types; `admit_synthetic(inputs, checkpoint) -> ModelState`; `step(state, event) -> Transition`; `recover_synthetic(snapshot_raw, checkpoint) -> Transition`; `result(state) -> ModelResult`; `run_synthetic(inputs, checkpoint, events: tuple) -> ModelResult` validates a whole script before reducing it. Pure functions, no I/O. |
| `tests/v11_gate3_preflight_synthetic_cases.py` | Explicitly generated synthetic package/history/protocol/binding bytes, clocks, checkpoints and event tuples. Immutable public denial descriptors from the retained verdict only; no private file loading or existing real-fixture helper calls. |
| `tests/test_v11_gate3_preflight_attempt_model.py` | Deterministic transition, fault, budget and malformed-input probes P01–P12 below. |
| `docs/V11_R09_GATE3_PREFLIGHT_ATTEMPT_MODEL_ACCEPTANCE.md` | Exact candidate identity, interface/limit inventory, probe results and limitations; public/synthetic artifact hashes only. |

Keep the checker, V4/capture launchers, existing runtime/store/collector and all
safety gates unchanged. No CLI, installed adapter, callbacks, socket/HTTP client,
provider SDK, resolver call, credentials/environment discovery, filesystem store,
subprocess, decoder or production import path. A synthetic transport is a finite
tuple of data events; it never has `send`, `connect` or a user-supplied callable.
No V10/AxiomTrade, root authority, service, main integration or score edits.

`SyntheticInputs` carries the four exact checker byte arguments, its existing
`ClockObservation`, `ResourceObservation`, `StateLedger`, synthetic review-terminal
mapping, and explicit `mode=SYNTHETIC_ONLY`. Invoke
`check_evidence_preflight_package` directly; never accept a caller's cached
`CheckResult` as admission. Even its satisfied result is
`CHECKER_SCHEMA_AND_POLICY_SATISFIED_NOT_EXECUTABLE`. Require all fabricated
prerequisite paths to be `synthetic://`, and bind their bytes plus frozen intent
into the model snapshot fingerprint. The retained verdict’s `retained_denials` supplies the complete public metadata
needed for the checker’s canonical denial digests; preserve those descriptors,
without opening their referenced raw sources. They are not fabricated rights. The synthetic marker is a type/scope
boundary, not an authenticity or security credential.

`Checkpoint` contains synthetic campaign/pilot IDs, externally supplied expected
history head, prior used/outstanding attempts/bytes/time, known holds, unfinished
intents, lock owner and last start time. No implicit empty/default genesis.
A fresh test genesis is explicitly labelled synthetic; missing/mismatched head,
unknown lineage or unfinished attempt refuses. Never open paths from any input.

State contains phase, exact fingerprint, sequence/head, owner, cumulative and
outstanding reservations, delivered totals, denial set, receipt references,
phase clock intervals and reasons. Store tuples/bytes and defensively copy at
admission; no mutable aliases. Events have a closed tagged schema with exact
builtin types: `LOCKS`, `INTENT_ACK`, `RESERVE_ACK`, `START`, `STATUS`, `HEADERS`,
`BODY`, `CLOSE_ACK`, `ACCOUNT_ACK`, `SEAL_ACK`, `FAULT`. Each carries sequence,
owner and expected head; applicable clock observations are explicit. ACKs mean
only simulated durability. Unknown keys/tags, bool-as-int, nonfinite/negative
counters, wrong types, duplicate sequence and out-of-order events refuse safely.

Bound admission before copying/decoding: each raw input <=1 MiB, snapshot <=1 MiB,
script <=256 events and <=4 MiB aggregate payload, BODY chunks <=64 KiB, counters
<=2^63-1 with checked additions. Validate a script before stepping it; never use
an unbounded generator. Direct `step` calls enforce the same per-event and
cumulative bounds, so callers cannot bypass the script validator. Apply the protocol's stricter per-artifact bounds too.
Retain at most the body ceiling plus one delivered chunk, then stop; count the
whole overdelivery chunk before poisoning. Oversized scripts are invalid model
inputs rejected before reservation, not simulated network deliveries. This
bounded event API is not evidence that a future wire adapter bounds read-ahead.

## Transition and accounting contract

| State/event | Required transition and refusal behavior |
| --- | --- |
| Admission | Checker refusal -> terminal `REFUSED_BEFORE_DISPATCH`, zero starts/reservations; preserve reasons. Synthetic satisfaction -> `ADMITTED`, never a dispatch capability. |
| `ADMITTED` + locks/intent | Require shared restriction, stage/session/budget/store ownership and unchanged heads. `INTENT_ACK` -> `INTENT_RECORDED`; uncertainty during intent persistence blocks recovery. |
| `INTENT_RECORDED` + reservation | `RESERVE_ACK` -> `RESERVED` only after full attempt, body, time and report/storage reservation are acknowledged. No start before both durable-model acknowledgements. |
| `RESERVED` + start | `START` -> `STARTED`, at most once, after repeated clock/resource/head/owner checks. Charge attempt even if scripted DNS/TLS subsequently fails. Reservation already prevents replay before START. |
| `STARTED` + status/headers/body | `RECEIVING`; 401/403/429/503, explicit denial or Retry-After eagerly appends a hold before any later body event. Retain original status/header bytes. No second attempt, redirect or alternate peer. |
| Close/account/seal | Close first, retain exact totals and closure/accounting refs, then account. Release unused body/time only after evidenced clean closure and accounting; report reservation survives until seal. Seal returns terminal retained outcome. |
| Fault/recovery | Clock failure, timeout, lost owner/head, journal-full, fsync/write failure, crash or missing closure -> `UNCERTAIN_HELD`; retain full unsettled ceilings/time, known bytes and eager holds. Overdelivery also poisons campaign. No restart budget reset. |

For intent-persistence ambiguity, conservatively retain the full reservation and
block; a proven pre-intent validation refusal alone consumes nothing. Recovery
of any incomplete intent is held, never resumed. A sealed outcome may be read
idempotently but cannot START again. After terminal state, all events except
read-only result retrieval refuse without reducing charges/holds. Duplicate
account/seal never refunds twice. No resumption/hold-clearing event exists here.

Attempt <=1 and entity bytes <=3,145,728 for P1; campaign <=8 attempts,
33,554,432 bytes, 120 seconds summed stage time. Reserve 60 stage seconds before
START, enforce 30 seconds START-through-close and 60 admission-through-seal;
one global in flight, minimum start spacing 2 seconds. DNS/TLS failures consume attempts; all delivered error-body and read-ahead
entity bytes count. Body totals are not wire/TLS totals. For every budget dimension,
`used + outstanding + new reservation <= ceiling`; settlement moves reservation
to measured usage atomically. Unknown completion retains the full ceiling;
known delivered excess is recorded in full even above it. Preflight consumed
and outstanding allowance also counts against linked capture's 3,600 attempts /
1,073,741,824 body bytes. Remaining campaign allowance grants no extra P1 request
or later stage. Hold all later work after ambiguity/poisoning.

Successful synthetic framing requires 200, unique case-insensitive headers,
<=4,096 total header bytes, <=32 fields, name <=64/value <=1,024 bytes, identity
encoding, no Transfer-Encoding, one ASCII decimal Content-Length 1..3,145,728,
and exactly that many delivered bytes. Synthetic valid content type is
`text/plain`; other types refuse in this narrow model. Retain ETag bytes exactly,
including weak/missing values, solely as index diagnostics. Short/extra body,
malformed framing or non-denial unexpected status yields `RETAINED_INVALID` on
proven closure; denial yields `DENIED_HELD`. Closure uncertainty takes precedence
as `UNCERTAIN_HELD`, preserving the denial separately.

A well-framed sealed response yields only `RETAINED_UNQUALIFIED`. **No index
parser in this slice**: parser/parse-result refs and parse clock are absent with
`NOT_IMPLEMENTED_IN_THIS_SLICE`; decode absent with
`NOT_PERFORMED_BYTES_ONLY`. Native ASCII/run/row/offset grammar and exact selected
row/range require a later independently reviewed parser with qualified semantics.
No inferred last-row length, field request, chained GET, semantic pin or capture.

`ModelResult` uses separate closed schema `ALPHA_V11_PREFLIGHT_SYNTHETIC_MODEL_V1`
with `synthetic=true`, protocol outcome, attempt state, fingerprint, before/after
history heads, counters, holds, reasons and modeled receipt/clock/seal refs.
Refs are null plus an explicit absence reason or bounded synthetic hash/length/
relative-path descriptors (reject absolute, traversal and symlink-like inputs;
no path is resolved). It is not an `ALPHA_V11_PREFLIGHT_RESULT_V1` real receipt.
Every result fixes execution/provider/capture authority false, qualification
credit 0, eligibility `DISCOVERY_ONLY_NOT_G3E`; reject promotion/extra keys even
under Python `-O`. No READY/CAPTURED/QUALIFIED/G3L_PASS outcome.

## Gates modeled, never satisfied physically

Apply interval checks at START, body, closure and seal: dispatch lower bound
>=10:00, all applicable upper bounds <13:30 UTC; uncertainty <=1 second,
calibration age <=60 seconds at START, consistent monotonic/UTC offsets and
same host/boot. Use the checker's strict timestamp/outward-rounding policy;
independent acceptance oracle uses rational arithmetic, not candidate helpers.
No HTTP Date, service-active flag or fresh time on recovered bytes qualifies.

Model the physical reservation of 64 MiB split 8/8/8/16/8/16 MiB for raw/temp,
session/accounting, history, reports, clocks/headers/receipts, margin; count
immutable imports separately. Require >=2 GiB free disk (target >=3 GiB) and
>=512 MiB MemAvailable after all remaining reservations, including conservative
128 MiB working memory. Descriptor <=4 KiB; each journal <=8 MiB / 2,048 records /
64 KiB per record. RSS <=128 MiB, address space <=256 MiB, CPU <=10 s,
diagnostics <=1 MiB, watchdog wall <=60 s remain required real enforcement.
Synthetic failures must preserve parent reservation and model child kill/reap.

Real uid/mode/device/inode, descriptor-relative no-symlink custody, exclusive
locks, atomic seal, fsync/restart persistence, independently retained history
checkpoints, physical allocation, dependency startup and measured clock method
remain UNQUALIFIED. A snapshot, hash chain or fake ACK is insufficient. Future
wire work additionally needs reviewed bounded resolver (<=16 public answers),
one deterministic peer/connect, actual peer and original-host TLS/trust checks,
no retry/redirect/proxy/ambient credential path and separate wire/buffer limits.
None of those operations is implemented or authorized by this slice.

## Exact acceptance probes and sequence

Implement in order: (1) closed types/admission/bounds; (2) transition reducer and
budget conservation; (3) bounded framing accumulator and eager holds; (4) snapshot
recovery/results; (5) probes below and acceptance record. Stop for independent
review on an unspecifiable invariant; do not expand to real storage or transport.
Each numbered probe must become a named `test_pNN_*` group with explicit expected
outcome, reasons, charges, holds and start count, including its positive control.

| ID | Input/change and exact oracle |
| --- | --- |
| P01 | Synthetic checker-satisfied fixture, safe interior clocks; locks -> intent -> reservation -> START -> 200/text/plain/Content-Length 3 -> `b'abc'` -> close/account/seal. Exactly one modeled start, used attempts 1/body 3, unsettled body 0, measured elapsed charged, final `RETAINED_UNQUALIFIED`; all authority false. |
| P02 | For every null prerequisite and each of the ten other retained blockers, construct synthetic failures; no START/reservation. Combine all 22 with frozen interior clock and assert exact reason set from public retained verdict, without private reads. Change path/run/date/limit, add unknown/duplicate keys, change raw hash/length, omit review/terminal or use design-only verdict: refuse before START. |
| P03 | FIELD, ECMWF, HEAD, Range/query/body, credential header, redirect, retry, second request, alternate peer, output promotion: refuse; no additional START. Falsified `CheckResult`, executable schema or changed immutable fingerprint cannot bypass admission. |
| P04 | Inject crash/write/fsync failure before/after each intent, reservation, denial, close, account and seal ACK. No start before both intent/reservation ACKs; uncertainty never refunds; eager denial survives. Proven close/account permits only one settlement; recovery never resumes unfinished work. |
| P05 | Reuse campaign/request across changed worker/root/host, reset ledger, stale/missing external head, missing genesis, changed checkpoint, competing lock owner or second in-flight intent: refuse. Replay sealed result is read-only; replay account/seal cannot double-refund. |
| P06 | Boundary tables at cap-1/cap/cap+1 for attempts, body, stage/campaign time and combined pilot budgets; count existing used AND outstanding. DNS/TLS fault consumes reserved attempt; incomplete body without proven closure retains 3,145,728 bytes and 60 s. Two starts 1,999,999/2,000,000/2,000,001 microseconds apart test spacing; P1 still never starts twice. |
| P07 | Denials 401/403/429/503 and explicit denial/Retry-After, each followed by body failure/crash; retain hold before body and count delivered error bytes. Missing, finite and malformed Retry-After never permits same-stage/window retry. Original three denial identities cannot be removed/edited; later synthetic success cannot clear them. |
| P08 | Header byte/count/name/value limits at -1/exact/+1; mixed-case duplicates; missing/duplicate/nondecimal/zero/too-large Content-Length; Transfer-Encoding; compressed body; non-200; body short/exact/+1 and cap-crossing final 64 KiB chunk. Invalid frames cannot return retained success; excess counted fully, campaign poisoned, no refund. Header split/chunk split variants produce identical totals/outcome. |
| P09 | Both frozen UTC edges at -1/0/+1 microsecond; uncertainty 0/tiny positive/0.3/1/>1; calibration 59/60/>60; malformed minute offsets, second/fractional offsets, Unicode digits, overflow, NaN/bool, UTC step/boot change and monotonic reversal. Safe interior controls retain; boundary/clock failures refuse or hold after reservation. Repeat at body/seal, deadlines 30/60 seconds and +1 microsecond. |
| P10 | Physical reservation 64 MiB-1/exact, disk 2 GiB-1/exact, memory 512 MiB-1/exact after remaining reservations; journal/record/descriptor and CPU/RSS/address/wall/diagnostic limits exact/+1. Insufficient input refuses before START, injected later breach holds with report reservation preserved; 3 GiB target is not a substituted hard floor. |
| P11 | Enumerate every state/event pair, every valid trace prefix and one duplicate/reordered/unknown event; deserialize tampered/truncated/oversized snapshots. Bound scripts/payload/counters at exact/+1; mutable alias changes do not change admitted state. Deterministic replay and no malformed-input exceptions; violations cannot erase charges/holds. |
| P12 | Import and run under socket/DNS, subprocess, credential/private-path read and filesystem-write denial guards; zero forbidden attempts. Repeat result/promotion/type tests under `-O`. All input/output evidence stays public/synthetic; no private fixture tests collected or executed. |

Run only the new suite with the existing interpreter, bytecode and plugin
autoload disabled, `--noconftest --capture=sys -p no:cacheprovider`; no dependency
install or full release run. Place denial guards before candidate import and use
an explicit public-source read allowlist. Do not run the existing full checker
suite or retained review probe scripts here: some load private package/evidence.
For checker compatibility, call its pure API only with the new synthetic inputs.
The reviewer must independently implement budget/clock oracles and adverse traces,
not merely repeat author assertions. Acceptance requires all P01–P12 green,
zero forbidden operations/exceptions/false success, stable bounded output and
unchanged checker/protocol/binding/safety files. A test PASS is model-only.

## Independent review and future package handoff

Freeze a clean candidate commit/tree and parent; record exact changed file hashes,
public source hashes, Python identity, commands, probe counts, failures and logs.
A **Sol/high reviewer** is the planned different-model reviewer for this Astra/high
handoff; if implementation author is Sol, use Astra/high for its exact-commit
review instead. Record actual model IDs/effort; do not substitute a same-model
role label. This document commissions no worker and claims no independent review
of itself or future code.

Reviewer scope: public protocol/design/checker reviews, new code and synthetic
fixtures only. Do not read/copy/transmit private package/provider evidence. Bind
report and machine verdict to exact commit/tree, parent, input hashes, P01–P12
results, findings and limitations. Allowed verdicts: `PASS_IN_SCOPE_OFFLINE_MODEL`
or `CHANGES_REQUIRED`, always non-executable. The outer runner must retain actual
original process exit, initial/final Git identities/cleanliness, timestamps and
report/verdict/last-output hashes. A written PASS without completed original
exit-0 terminal, timeout, dirty inputs or hash mismatch is incomplete; preserve
failed terminals. This task's runner records its own actual exit/Git identity;
no author-written terminal may stand in for it. No merge/integration is requested.

After review, qualified parser semantics, genuine lineage/access/owner-original
reconciliation, physical store/clock/resource proofs and a separately scoped real
transport/build review remain prerequisites. A later date/run/window/package must
be a separately versioned proposal: retain old protocol/binding/package/history
bytes and terminals, freeze new exact intent/limits and references, carry forward
all holds/unfinished intents/cumulative campaign and linked-pilot debits, and
obtain fresh independent exact-byte review of protocol, implementation/build,
package and prerequisite evidence with detached execution envelope. Do not edit
checker V1 frozen constants as a shortcut or mint a fresh budget by renaming the
campaign. Even filling the accepted design reference changes package bytes and
requires review. Original owner-directive reconciliation remains required.
Only a separately authorized and genuinely completed `EXECUTABLE_PREFLIGHT_PASS`
for that future exact package could permit its listed requests. This handoff,
a model PASS and the accepted checker provide no such authority.

Author validation for this documentation-only change: whitespace and relative-link
checks, P01–P12 coverage/source-binding assertions and unchanged tracked-source
checks passed locally. No implementation tests, private-input checks or executable
preflight were run; the future acceptance suite above remains to be implemented.
