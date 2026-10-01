# Gate 3 V4 slice 3 — independent exact-commit review

**CHANGES_REQUIRED.** Astra/high independently reviewed Sonnet's candidate
`8e446fd7ee74b3cba987dc6db56ce0a28ab5c959`, tree
`5662b3a263d254d46a5b414f4f9418b68338bf61`, against base
`9c3e7487119969367d0e52ecf7ccdc3d63535f64` on 2026-10-01 UTC.
This is an offline implementation verdict. Do not merge or qualify this candidate
for G3-L. No provider request, launch or SHADOW evidence is authorized.

## Scope and evidence

Reviewed the exact four-file diff: new runtime and tests plus branch-local
checkpoint/progress changes. The previously accepted ledger, budget, store and
V4 validator bytes are unchanged. Candidate worktree was clean before and after
review. The independent review did not modify it. Main started at `bc7b003`.
The provider-mapping candidate `15f054f` is separate and has no verdict here.

The acceptance basis is design sections 3–7, document SHA-256
`0b121fbc422208e2fa89e0b2f7362725cac60115ada966ed83b9d1ed15ac8e4a`, accepted
in the [design review](V11_R09_GATE3_TRANSPORT_RUNTIME_REVIEW_7e132a0.md),
and the [slice-2 carry-forward notes](V11_R09_GATE3_V4_SLICE2_REVIEW_58a465f.md).
Those notes were nonblocking for isolated ledgers; runtime obligations cannot
be waived merely by retaining their earlier P3 labels. Concrete networking,
real clock measurement, GRIB qualification and real-source evidence legitimately
remain later work. Synthetic enforcement of the runtime contract belongs here.

Independently executed in the exact candidate worktree:

- Focused runtime: **36 passed**.
- Gate 3 family: **400 passed**, two existing fork warnings, 14.44 seconds.
- [Independent probes](V11_R09_GATE3_V4_SLICE3_REVIEW_8e446fd_probes.py):
  **19 passed**, comprising **16 defect reproductions and three positive
  controls**. A passing counterexample means the defect was reproduced, not
  that the candidate meets its safety contract.
- `git diff --check 9c3e748..8e446fd`: clean.

Probe command, with no provider access:

```bash
PYTHONDONTWRITEBYTECODE=1 /home/alphaadmin/AlphaV11_Dev/venv/bin/pytest -q -p no:cacheprovider /home/alphaadmin/AlphaV11_Dev/Alpha/docs/V11_R09_GATE3_V4_SLICE3_REVIEW_8e446fd_probes.py
```

The probe file defaults to `/tmp/alpha-v11-gate3-v4-slice3-20261001` and accepts
`ALPHA_GATE3_CANDIDATE` for another exact checkout. It contains fixtures only.

## Blocking findings

All line references below refer to `tools/v11_r09_gate3_runtime.py` at the
reviewed commit, not a later repair.

### R1 — P1: computed deadline never controls transport or known closure

Lines 467–473, 515–521, 539–576, 591–605. `_dispatch_deadline` computes the
expected minimum for one sample, but only tests whether it has already elapsed.
The value is neither persisted nor passed to transport, checked during body
consumption, nor checked at closure. `Transport.dispatch(request_id)` returns
an eager `OfflineResponse`; there are no headers/read/close operations, deadline
argument, bounded-read contract or proof of cancellation/known closure.

Probes advance a consistent synthetic clock by 31 seconds during dispatch and
by 1,000 seconds in a separate case. Both return `SUCCESS`, `timely=True`;
the latter receives the body after A=1000, before D=1100. Checking D cannot
replace the body-completion bound A. A mismatched Content-Length becomes a
semantic failure, then releases the reservation/shared token without separate
closure evidence. Known failed/partial transport may settle, but ambiguity
must not be inferred closed from an ordinary returned object.

Repair the injected protocol and synthetic implementation so a fixed absolute
deadline and remaining read allowance govern every phase, status precedes
further body reads, and known close/framing is explicit. Persist closure time
and evidence before accounting release; missing/ambiguous closure holds.
Recheck immediately before the injected dispatch after persistence delays.
No real socket adapter is needed or authorized for this repair.

### R2 — P1: clock validity and session elapsed state are not enforced durably

Lines 449–473, 486–519, 599–605. `_enforce_window` checks only uncertainty and
the UTC interval. It does not validate freshness, boot, finite monotonic
measurements, original evidence or the intersection of offsets across the
session. Store validation is too late to prevent dispatch.

Stale measurement and wrong-boot probes both dispatch and reach ACCOUNTED,
then fail only at store sealing. A +100-second UTC step between two attempts
is accepted because the store creates a separate clock sequence for each.
The elapsed deadline is process memory only: with elapsed cap 1 second, a
completed request freezes deadline 11, then same-boot reopen at monotonic 12
accepts another request and moves the deadline to 13. DurableBudget has its
own elapsed setting, but this runtime neither binds it to `AbsoluteWindow`
nor derives the original first-clock deadline from durable state.

Persist the first valid clock, offset intersection, immutable policy and
elapsed deadline; verify/replay them before dispatch across restarts. Bind
clock boot/policy and manifest context to all four journals. Validate original
samples before any transport, including denial/error paths, and never widen a
fixed deadline when subsequent samples arrive.

### R3 — P1: reservation pacing is not the approved closure pacing

Lines 79–91 and 505–519. The second documented scope interpretation is
**rejected**. The design deliberately requires both reservation spacing and
next dispatch >= previous known closure + frozen interval. Reservation spacing
is insufficient under fsync/transport delay.

One probe delays only the first reserve return by three seconds: both actual
synthetic dispatches start at monotonic 13 even though reservations are spaced.
Another completes a three-second transport and immediately dispatches the next
request at the same closure time. Both pairs succeed. The existing test named
`test_actual_starts_under_two_seconds_blocked_despite_spaced_window` does not
exercise delayed reservation persistence.

Add a durable closure monotonic sample and enforce the frozen >=2-second
post-close interval before every dispatch and after restart. Amend the ledger
schema explicitly with fresh review; preserving accepted source unchanged is
not a reason to substitute a weaker rule.

### R4 — P1: denial classification and receipt time can bypass provider cooldown

Lines 363–385, 526–533. Only 401/403/429/503 trigger denial. A valid 200 with
`Retry-After: 1200` seals successfully and leaves its control domain unblocked.
The design requires any Retry-After and explicit denial to stop that domain.
Additionally the denial receipt upper bound comes from the dispatch sample.
A response arriving 20 seconds later records cooldown 1210.05 instead of
1230.05, prematurely releasing the provider restriction.

Classify bounded headers before further body reads, retain actual original
header-receipt clock/evidence and origin, and handle every Retry-After form.
Invalid/missing expiry remains unresolved; valid dates need conservative
receipt-aware parsing, never an inferred shorter cooldown. Avoid duplicate
header last-value selection. Use conservative time for resumption checks.
Raw evidence must be durably retained or have an explicit missing-evidence
cause; hashing an ephemeral header tuple does not retain its bytes.

### R5 — P1: runtime requests are not bound to the frozen plan or journal context

Lines 296–360, 433–454, 483–508, 588–595. `AttemptRequest` is freely
caller-constructed; the runtime has no verified frozen schedule/review object,
expected request order, purpose totals or prerequisite validation. Transport
gets only an ID and cannot inspect a bound immutable intent. Shape-valid digest
strings do not bind the endpoint/path/purpose/window to approved plan bytes.

A runtime constructed with manifest `9...9` over session/budget/store manifest
`a...a` completes SUCCESS. An INDEX reservation of 4 MiB also dispatches and
succeeds despite the 3 MiB INDEX ceiling. Source/mapping qualification can remain
external, but enforcing an already-validated offline plan cannot. Dependency
digest tuples are not checked before transport; RAW provenance also cannot
substitute for the required capture receipt and verified dependency graph.

Bind a validated immutable plan/context to acquisition and runtime; reject any
substituted manifest, endpoint, path, purpose, cap, range, validator, policy,
order or prerequisite before dispatch. Derive accounting from frozen requests
and actual budget events. Add the bounded capture receipt/replay composition
required by section 6 rather than treating object-witness hash presence as it.

### R6 — P1: eager overdelivery loses already-returned body bytes

Lines 535–546, 558–574. The first violating chunk is charged and poisons
budget/session correctly, but iteration stops while the returned tuple can
contain further prefetched bytes. A response tuple containing 11 + 20 bytes
against allowance 10 records only 11 in budget and closure, although 31 bytes
were returned at the current eager transport boundary.

Define delivered/read-ahead semantics explicitly in the R1 protocol repair.
Account all bytes already delivered to that boundary, including discarded and
prefetched bytes, retain any accounting uncertainty, and perform no extra read
after exhaustion. Keep the current permanent poisoning and no-ACCOUNTED rule.
Do not simply continue ordinary `consume` calls after the budget is poisoned.

### R7 — P2: report composition and reserve do not meet section 6

Lines 622–722. The 2,713 integer rows form a partition, but arbitrary caller
outcomes are trusted (`INVENTED_SUCCESS`, missing request ID accepted). There
is no derivation from durable receipts/schedule, primary precedence, failed
prerequisite propagation, per-purpose/global accounting, provider/event or
all-provider views. Absent rows all claim NOT_IN_SCHEDULE even when actual
scheduled attempts may have been lost. Refused entries are labeled ATTEMPTED.

`ReportSink` checks free space but allocates no reserve before acquisition;
the runtime does not require a sink at all. `persist_incomplete` accepts a
reason larger than the entire 16 MiB limit and writes it successfully. No
runtime/report completion record links a durable report digest to its context
or preserves a report hold after failure. Exclusive creation/fsync are useful
but do not supply that composition.

Build the required bounded report from the frozen denominator and verified
records, reserve real capacity before acquisition, bound both report paths
before allocation, and test short-write/fsync/exhaustion/restart behavior.
An attempt terminal may exist before the final report, but run completion
must remain held until actual durable report evidence exists.

### R8 — P2: resource checks omit prospective allocation and capacity admission

Lines 259–268, 455, 488, 587. ResourceSnapshot contains only free disk and
available memory and `_check_resources` compares those directly with floors.
There is no subtraction/binding of the frozen subset's worst-case journal,
object, temporary, receipt/dependency and report allocation. Request capacity
is not preflighted across all four primitives. A raw free-space comparison
cannot prove >=2 GiB/512 MiB *after* prospective allocation. The store's own
small seal-time reserve does not satisfy this distinct runtime requirement.

Bind a validated capacity plan to resource checks at startup, before each
request and each local processing batch. Refuse before dispatch when frozen
capacity cannot fit; add synthetic exact-floor and one-byte-under tests using
nonzero prospective allocation. Do not change host resources or evict data.

## Scope interpretations and safeguards that do work

The **first interpretation is accepted narrowly**: a pre-transport rejection
may persist ATTEMPT_INTENT then REFUSED without shared INTENT_OPEN when no
reservation, DNS or transport occurs. The independent control confirms no
dispatch, shared open or budget count. This never permits cancellation of an
already-open/shared/inherited hold or inferring closure after a dispatch intent.

The denial-order positive control confirms both denial records exist before
the first budget consume. An injected shared-denial write failure retains the
full reservation and open shared/session hold. This proves ordering before
accounting in the eager fixture, **not** before transport reads headers/body;
R1 addresses that missing boundary.

The current overdelivery path reuses SessionLedger's R4 logic correctly for
the first observed violation: it closes as FAILED/DENIED, never OK, and returns
without ACCOUNTED; budget/session poison prevents subsequent attempts and
survives restart in the candidate tests. R6 concerns the incomplete delivered
byte total, not an absence of poisoning. Store historical eligibility remains
false and recovered acknowledgement semantics are not weakened.

## Disposition and next step

Retain `8e446fd` unmerged. Repair these eight findings in one isolated offline
worker, with explicit schema evolution where required and no silent journal
migration. Convert these defect reproductions to desired rejection/hold tests
alongside the positive controls; the review probes themselves are immutable
evidence. Obtain a fresh different-model exact-commit review before any
integration and reconcile against newer main. The mapping candidate requires
its own separate review; this verdict neither accepts nor rejects it.

No full release suite was repeated: the earlier 5,460-pass/13-skip acceptance
remains the recorded release result. PAPER/demo/controller are inactive and
disabled; weather execution inactive/masked; protected authority paths absent.
Private FINAL-REVIEWED master matches its SHA-256 pin. V10 and AxiomTrade were
untouched. No C/J/E/A boundary crossed: **91/200 (45.5%), formal 1/50;
NOT_READY_TO_FUND. G3-L NO-GO.**
