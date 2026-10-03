# Gate 3 fresh-readiness boundary design and implementation handoff

Status: PROPOSED_OFFLINE_DESIGN; not an executable protocol amendment.
Author: Astra/high. Code/protocol baseline: `b8493f0fc2aa174bcdc21d0b91e43230fe1a2855`.
Scope: public repository protocol and code only. No private package, retained
private weather evidence, or held repair candidate bytes were read for this design.
The separate repairs `976217d` and `741c6ae` remain untouched and unmerged.

The next implementation is a bounded, pure proposal validator. It cannot grant
provider rights, qualify observations, construct an executable package, dispatch,
clear a hold, reserve resources, or grant G3-L/G3-E/SHADOW admission. All output
paths carry `execution_authority=false`, `capture_eligibility=false`,
`qualification_credit=0`, and `g3l=NO_GO`. A structurally valid proposal has
status `PROPOSAL_VALID_NOT_EXECUTABLE`, never READY or PASS. Invalid inputs yield
`PROPOSAL_REFUSED` with bounded reasons and no partial accepted proposal.

## 1. Existing authority and the three open questions

The source manifest beside this document binds the exact files used. Relevant
contracts are:

- Evidence-preflight protocol sections 1, 4 and 5: frozen October 2 window
  10:00–13:30 UTC; one P1 attempt, 30-second attempt / 60-second stage;
  uncertainty <=1 second and calibration age <=60 seconds; 64 MiB physical
  reservation; 128 MiB RSS; post-reservation 2 GiB disk / 512 MiB memory floors.
- Collection protocol sections 3–5: capture 14:00–17:00 UTC on the UTC date
  preceding the target local date; 18:00 decision before local-day start;
  00Z cycle, maximum run age 86,400 seconds, prospective review and all causal
  bounds. This is a different window and admission path from evidence preflight.
- `v11_gate3_evidence_preflight_checker.py`: frozen P1 scalars/window/limits;
  `ClockObservation` and `ResourceObservation` are structural inputs. A source
  string or a structurally satisfied checker result is not provenance.
- `v11_r09_gate3_g3l_prep.py`: fixed capture schedule and bounded slot planning;
  current resource argument validation establishes nonnegative integers, not
  plausible host measurements or enforceable reservation.
- `v11_gate3_preflight_attempt_model.py`: offline monotonic/host/boot event
  composition and accounting. It does not acquire trustworthy clock observations.

No source authorizes a generic rolling window, a hardware capacity inferred
from arbitrary integers, or a caller-issued clock trust assertion. The design
below closes those *proposal* ambiguities without changing frozen enforcement.

## 2. Window policy: explicit proposals, never roll-forward

Use separate named modes `P1_PREFLIGHT_PROPOSAL` and `CAPTURE_PLAN_PROPOSAL`.
Neither mode accepts a user-supplied policy object that can widen bounds. Put
versioned constants in the new module. Reports carry an externally computed
`declared_build_ref` (source SHA-256 and byte length) as unqualified metadata.
A pure validator cannot attest its own executing bytes; a separately reviewed
runner must bind actual loaded module/build bytes before any real admission.
Do not embed a purported self-hash or trust a caller declaration as that binding.
Require caller-supplied evaluation UTC; do not default to the host clock.
A caller timestamp supports scenario evaluation only, not freshness attestation.

For P1 proposals require explicit `run_utc`, `start_utc`, `end_utc` and
`evaluation_utc`. The proposed policy is:

1. All times are canonical UTC `YYYY-MM-DDTHH:MM:SS[.ffffff]Z`, with at most
   six fractional digits; reject leap seconds, naive/offset values, noncanonical
   fractions, invalid dates, overflow and silent normalization. Normalize neither
   the proposal nor its hashes. Integer microseconds drive comparisons.
2. `evaluation_utc < start_utc < end_utc`. The proposal stage budget must fit:
   `60 seconds < end-start <= 12,600 seconds`. The 12,600 upper bound inherits
   the frozen P1 window's width, not its execution authority; the strict lower bound
   rejects the execution-infeasible 60-second equality. It leaves nominal room
   for a stage budget, but does not prove enough uncertainty/drift margin.
3. `end_utc - evaluation_utc <= 86,400 seconds`. This **new conservative planning
   horizon** bounds the whole proposal to the next day; it is not the capture
   run-age rule and is not asserted to be a provider retention/release policy.
   Equality is allowed. Dates farther out are refused, never silently advanced.
4. `run_utc` is exactly 00:00:00Z on the same UTC calendar date as `start_utc`;
   `run_utc <= evaluation_utc`. New speculative runs are not labeled available.
   These are **proposed scope restrictions**, not evidence that the index exists
   or that its requested path is lawful. No path is generated in this slice.
5. A date, time or width change creates a new proposal digest. Keep the original
   campaign accounting identity and restriction lineage; a proposed new date
   cannot reset either. The frozen October 2 checker stays unchanged and must
   still refuse changed dates. A future executable checker/protocol/package is
   a separate reviewed change.

Capture mode continues to derive the existing 14:00/17:00/18:00 schedule and
00Z run from the explicit target date. Do not apply P1's 3.5-hour window or
invent a new capture cadence. The same *proposed* next-day horizon may bound
planning, but cannot replace maximum run age, local-day/DST rules, prerequisite
receipt/review cutoffs or exact-manifest review. Refer to the existing planner
for the slot denominator; never shrink the denominator when resources are low.

Executable qualification, outside this slice, must additionally use genuinely
reviewed observations: start lower bound >= approved start; all required upper
bounds strictly < approved end. For the full 60-second stage reservation,
require `dispatch_upper_utc + 60 seconds + qualified_drift_margin < end_utc`,
where the nonnegative drift margin comes from the independently qualified clock
method. Unknown margin refuses admission; equality refuses. This conservative
fit predicate does not replace actual per-event deadline enforcement. Original
review must have completed before the required cutoff. Passing proposal arithmetic cannot
satisfy any of these real-time obligations.

## 3. Resource magnitude and accounting

Separate representational bounds, cross-field plausibility, and genuine resource
qualification. No scalar proves all three.

The new parser accepts exact built-in integers only, `0 <= n <= 2**63-1` for
byte magnitudes. Reject bools, floats, strings, subclasses and larger values
before arithmetic or formatting. The upper bound is a **new implementation
representation ceiling**, not an assertion of host capacity. Checked sums and
products exceeding it refuse; do not clamp, saturate, wrap or treat overflow
as unlimited headroom. IDs and counts have their own small bounds.

A planning snapshot supplies disk total and disk available-to-process on the
selected filesystem, host memory total and MemAvailable, host/boot identifiers,
filesystem device identity, observation UTC and monotonic time. Require
`available_disk <= total_disk`, `MemAvailable <= total_memory`, and an exact
match to the selected store's filesystem identity. These checks only establish
internal plausibility; all fields can still be fabricated.

Require a bounded reservation view with distinct IDs and states:

- `MATERIALIZED_BEFORE_SNAPSHOT`: disk capacity actually consumed before the
  snapshot; it is already reflected in available bytes. Do not subtract twice.
- `OUTSTANDING_NOT_IN_SNAPSHOT`: maximum remaining commitments, including crash
  or uncertain holds, with no evidence of release. Subtract in full.
- Unknown timing/state/ownership/overlap, duplicate IDs, missing consumers or
  unavailable shared reservation evidence means `RESERVATION_VIEW_UNQUALIFIED`.
  Reject partial accounting; do not infer zero reservations from an empty file.

Post-reservation disk is the observed available bytes minus outstanding disk
commitments and the new, not-yet-materialized 64 MiB P1 reservation. The memory
calculation subtracts the remaining commitments plus a conservative 128 MiB
working reservation for P1; never reclaim an active process's measured RSS as
new headroom. A materialized reservation can avoid a second debit only when
its complete current worst-case obligation is demonstrably covered. Existing
unused capacity is not two independent reservations. Reject negative results.

Require post-reservation disk >=2,147,483,648 and memory >=536,870,912 bytes;
report disk below 3,221,225,472 as target shortfall without pretending the target
is the existing hard floor. Keep owner specialist concurrency policy separate
from the 512 MiB execution floor. Larger measurements never enlarge request,
body, time, CPU, output or reservation limits. Capture retains its own reviewed
resource calculation, including report/decoder overhead and combined campaign
accounting; P1 resources cannot stand in for capture resources.

Propose snapshot age <=60 seconds when evaluating actual local observations,
with age derived on the same host/boot monotonic timeline and no future sample.
This is a **new proposal freshness cap**, not an already accepted resource gate.
Scenario-only snapshots are always unqualified. Even a fresh real snapshot is
not reservation/enforcement evidence: storage descriptor/custody, physically
allocated reservation, quota/watchdog and before-dispatch checks remain separate
reviewed prerequisites. No allocation or filesystem scan occurs in this slice.

## 4. Clock provenance: qualification cannot be self-issued

Introduce a new sidecar observation dossier; do not enlarge or reinterpret the
existing `ClockObservation` as trusted. A canonical, closed, bounded record has:

- host identity, boot identity, recorder source/build digest, method version,
  retained raw calibration reference and retained sample reference;
- per sample: event kind, sequence, monotonic microseconds, UTC microseconds,
  uncertainty microseconds, calibration identity and original calibration
  monotonic microseconds;
- a detached reference to independent method/custody review, including exact
  reviewed recorder/dependencies, source method, host/boot scope, raw evidence
  bindings, validity domain, report and completed terminal hashes.

Each reference binds SHA-256 and byte length to explicitly supplied immutable
bytes. No path/URL dereference is allowed. Hash matches establish byte identity,
not an independent trusted source. Review-envelope shape, `PASS` text,
`LOCAL_AUTHORIZED_ONLY`, `monotonic_consistent=true`, service-active, HTTP Date,
file mtime, a fresh copy or a hash chain cannot qualify the source. The planner
may report `STRUCTURALLY_LINKED_UNQUALIFIED` for complete supplied bytes but
must retain `clock_qualification=false`, even for synthetic positive fixtures.

A later real admission consumer must resolve reviewer identity and exact verdict
against its independently accepted review records, never a signer/key/trust list
supplied by the dossier. The recorder and raw calibration method must already
be independently qualified; if no such source exists, CLOCK_UNQUALIFIED persists.
This proposes no root installation, new trust service, NTP access or credential.

Use exact integer microseconds. Enforce uncertainty 0..1,000,000 inclusive and
calibration age 0..60,000,000 inclusive, computed from the same host/boot
monotonic record, never trusted from a caller's age field. Derived offsets are
signed; validate their representation separately from nonnegative resource bytes.
All sample references must retain original times; replay does not refresh age.

For a method with a reviewed drift envelope, project the original calibration
UTC interval using monotonic elapsed time plus that envelope; each sample must
be consistent with the original anchor and all retained applicable constraints.
Adjacent-pair overlap alone permits accumulated drift and is insufficient.
Missing/unsupported drift model refuses qualification; do not invent zero drift.
Detect host/boot changes, monotonic reversal, future calibration, nonfinite values,
clock steps and excessive uncertainty/age. No rebase to a new calibration within
an attempt unless a separately reviewed transition proves continuity. At live
admission, use the conservative qualified interval, not a narrower self-reported
sample interval, for dispatch/body/parse/seal bounds. Any clock failure preserves
bytes, accounting and holds; it never grants a refund or rewrites original time.

## 5. Bounded implementation slice and acceptance tests

Implement only `tools/v11_gate3_readiness_boundaries.py` and focused tests plus a
handoff. Consume explicit byte strings and exact built-in objects; no filesystem,
network, provider imports, subprocess, wall-clock reads or existing held branch
imports. Parser ceilings proposed for this slice: aggregate input 1 MiB, nesting
8, nodes 4,096, array items 64, strings 4,096 UTF-8 bytes, reference bytes within
the same aggregate cap, at most 64 reasons and output <=64 KiB. Bound inputs
before hashing/traversal; iterative depth checks before JSON decoding or catch
recursion failures without propagating them. Reject duplicate/unknown keys,
non-JSON values, subclasses/custom Mapping/iterator methods and string/key bombs.
Do not invoke arbitrary repr/equality hooks while generating reasons. Input
collections cannot smuggle unbounded traversal through false lengths.

Keep a versioned closed schema and explicit refusal codes for syntax, time,
horizon, representation overflow, resource inconsistency, incomplete reservation
view and clock linkage. Complete syntactic consistency may yield proposal-valid
status with unresolved qualification blockers; never clear a blocker by omitting
its field. Output is deterministic, detached from later caller mutation, and
contains all fixed false authority flags on every serializable result path.

Focused tests must include:

1. Refuse width 60 s and below; accept nominal width 60 s + 1 microsecond
   only as a proposal. Test 12,600 s / 86,400 s equalities and one-microsecond
   neighbors, and full-stage conservative fit equality refusal;
   start==evaluation, inverted interval, expired window, UTC day boundaries,
   00Z mismatch, future run, leap/naive/offset/precision and datetime overflow.
2. Exact disk/memory floor and one-byte deficit, target-only shortfall,
   signed-64 ceiling and overflow sum, bool/subclass/10,000-digit integers,
   available>total, wrong filesystem, stale/future/cross-boot snapshot.
3. Double-counted materialized reservation, hidden outstanding commitment,
   duplicate/missing reservation IDs and uncertain crash state. No refund from
   date changes, favorable snapshots or guessed prior release.
4. Forged source/status/review fields, absent/tampered raw bytes, matching hashes
   with unqualified origin, boot drift, monotonic reversal, caller-age mismatch,
   1-second/60-second edges, adjacent-compatible cumulative clock drift, copied
   observations and original-anchor failure. All still lack qualification.
5. Forged or missing declared build binding cannot attest executing source.
   Empty/oversized/deep/duplicate-key JSON, hostile mapping/subclasses, bounded
   output/refusal path, repeat replay and caller mutation. Patch socket,
   subprocess and file-opening entry points to raise if the validator calls them.
6. Assert false authority/G3-L/eligibility and zero credit on every result; retain
   frozen-checker refusal of shifted dates. Run plain and Python `-O` modes.

Independent exact-commit review by a different model must approve the proposed
policy and implementation scope before integration as an offline tool. Review
must identify the new horizon/resource freshness/representation constants as
proposals, not infer them from unrelated accepted limits. No design or tool PASS
can become EXECUTABLE_PREFLIGHT_PASS. Actual fresh package/protocol, trusted
observations, restriction/access/path evidence, resource enforcement and detached
exact execution review remain distinct future work.

This task ends with a reviewable implementation handoff, not code readiness or
an accepted new clock policy. The two held repairs still require their own
permitted exact review and reconciliation. Score remains **91/200, formal 1/50;
G3-L NO-GO; NOT_READY_TO_FUND**. No request or forward SHADOW occurred.
