# Gate 3 V5 FC1 interface amendment 1 — 2026-10-03

**PROPOSED_BLOCKED / documentation only / zero qualification credit.**
This separately versioned amendment selects three interfaces for future FC1
implementation. It accepts no implementation, production use or H1 equivalence.
Base: `480793cbe4a9832ebf1ffcd95c17d12a1bd333e5`. The original
[FC1 contract](V11_GATE3_V5_FULL_COHORT_EXECUTION_CONTRACT_20261003.md),
[verification](V11_GATE3_V5_FULL_COHORT_EXECUTION_CONTRACT_20261003.verification.json)
and [sources](V11_GATE3_V5_FULL_COHORT_EXECUTION_CONTRACT_20261003.sources.json)
remain byte-for-byte historical evidence. Apply this delta only after independent
acceptance, with that contract; this document takes precedence only at the three
interfaces below and their directly affected pins, cost certificates and cases.

The exact retained `/tmp/alpha-v11-v5-fc1-interface-adjudication.final` was read;
its digest/length and original reviewed commit are recorded in the new
[source companion](V11_GATE3_V5_FC1_INTERFACE_AMENDMENT_1_20261003.sources.json).
It held all three interfaces and approved no candidate. Its local path is
provenance, not a repository dependency or an external authentication channel.
The new [verification companion](V11_GATE3_V5_FC1_INTERFACE_AMENDMENT_1_20261003.verification.json)
contains the complete FC01–FC24 matrix, retaining unaffected cases literally.
The [document schema](V11_GATE3_V5_FC1_INTERFACE_AMENDMENT_1_20261003.schema.json)
validates these documentation companions only, not unfinished step-2 wire bytes.

## 1. Version boundary and preservation

The amendment identity is `G3_V5_FC1_INTERFACE_AMENDMENT_1` (IA1). Future execution
packages must use these replacements consistently, never mix old/new identities:

| Original proposed identity | IA1 identity |
| --- | --- |
| `R09_GATE3_LAUNCH_MANIFEST_V5_FC1` | `R09_GATE3_LAUNCH_MANIFEST_V5_FC1_IA1` |
| `G3_V5_FULL_COHORT_EXECUTION_1` | `G3_V5_FULL_COHORT_EXECUTION_1_IA1` |
| `G3_V5_FULL_COHORT_COST_1` | `G3_V5_FULL_COHORT_COST_1_IA1` |
| `G3_V5_TIMING_PLAN_1` | `G3_V5_TIMING_PLAN_1_IA1` |
| `G3_V5_RECORD_PLAN_1` | `G3_V5_RECORD_PLAN_1_IA1` |

Every FC1 projection, export, runtime context, custody, review and trusted-pin
schema appends `_IA1` to its complete FC1 identity. New session/receipt/stream
interfaces are `G3_V5_FC1_SESSION_IA1`, `G3_V5_FC1_CAPTURE_RECEIPT_IA1`,
`G3_V5_FC1_START_BOUND_IA1`, and `G3_V5_FC1_COUNTED_EOF_STREAM_IA1`.
Unchanged allocation/segment representations retain their original identities;
their enclosing IA1 plans and exact references still require renewed review.
The execution-profile key set stays unchanged. Its scheduler/ledger/stream
review Refs must enroll IA1 and bind exact schema, implementation, producer,
serializer and dependency bytes. Pin the original contract plus this amendment,
both companion pairs, and every changed build/plan through export, review and
external custody. A self-pin proves no authority. Do not repin old baselines.
Unknown identities, mixed profiles and unknown keys refuse before dispatch.
V1/V4/V5/original FC1 consumers must refuse IA1; no translation, auto-migration,
fallback or edits to their runtime checks. Step 2 must freeze exact wire schemas
only after this contract receives independent review. No step-2 bytes are changed
or asserted complete here.

All unmodified FC1 obligations remain normative, including 2,713 report rows,
8,139 role cells, full native trajectories, one global window/token, 3,600
attempts, 1 GiB total received, <=30 s/request, >=2 s actual-start spacing,
10,800 s for all work, full frozen cap before dispatch, no retry/cap reuse and
no second window. Retain original raw/header/clock evidence, denial/restriction
lineage, 401/403/429/503 and Retry-After semantics, immediate durable denial,
authenticated heads, same-boot clock intersection and absolute cutoffs.

Store PREPARE, body fsync, namespace durability, original clock prefix,
post-durability seal clock and COMMIT remain ordered and mandatory. Reopen
never manufactures historical acknowledgement. Preserve one reserved acquisition
reopen, existing-plus-remaining occupancy, physical resource reservations, graph
bounds, V1 refusal, full lossless provenance and all original reasons/partitions.
All 79 inherited G3-L map rows are copied literally with zero new credit.
H1–H6, A1–A8, G3-L/G3-E and independent external custody remain holds. No H1
equivalence is granted, no window selected and no production acceptance implied.
Status remains 91/200, formal 1/50, 77 missing identities, G3-L NO-GO,
NOT_READY_TO_FUND. No root/service/V10/Axiom/financial, funding/order/credential
change, provider/network request or private evidence transmission is authorized.

## 2. Terminal outcome before capture custody

Replace FC1 §5's session ordering and FC13's positive control with:

```
INTENT -> BUDGET_RESERVED -> DEADLINE_FIXED -> DISPATCH_INTENT
 -> [DENIAL] -> TRANSPORT_CLOSED -> ACCOUNTED -> [OBJECT_WITNESSED]
 -> TERMINAL -> CAPTURE_RECEIPT
```

CLOCK_CHECK and FAILURE_ANNOTATION remain bounded optional records. The maximum
is still twelve per request plus the original four lifecycle/reopen records.
No new normal-path record is introduced. The terminal event records the outcome;
completed capture custody is a separate derived predicate requiring exactly one
valid, durably acknowledged receipt after that terminal. Keep the global progress
latch held through receipt durability. Terminal alone cannot permit another
intent/dispatch, dependency use, eligibility or accepted reporting. Diagnostic
reports may describe incomplete custody with the original outcome and reasons.

In `G3_V5_FC1_CAPTURE_RECEIPT_IA1`, `session_terminal_head` means the actual hash
of that request's terminal event, not a preterminal hash, current journal head,
receipt hash or caller-selected digest. Verify it against replay-derived terminal
history, request/context identity and outcome. The chained receipt follows the
terminal and retains all inherited evidence/dependency/head bindings. There is
no hash cycle, provisional success, receipt rewrite or extra finalize record.
Report/dependency validation must check the terminal/receipt pair, original store
custody, denial history and relevant budget/shared heads before granting credit.

SUCCESS still requires ACCOUNTED and a valid witnessed RAW commit and cannot
follow denial or overdelivery. A known ordinary failure may omit the witness
but requires confirmed close and durable accounting before FAILED then receipt.
A predispatch REFUSED event has its own outcome head, cannot invent transport,
RAW or budget completion, and requires the same receipt-custody latch if an
intent was recorded. Refusal before opening an intent grants no attempt credit.
Ambiguous close/accounting or overdelivery remains globally held; recording a
violation total is not successful ACCOUNTED or a releasable terminal.

Crash after witness but before terminal leaves an unfinished attempt. Crash
between terminal and receipt, a torn/missing receipt, failed append/fsync, wrong
head/outcome, duplicates or unknown durability leaves custody held. Replay may
recognize retained bytes and current verified durability but cannot fabricate a
missing receipt, rewrite a terminal, assert a lost historical acknowledgement or
resume that incomplete attempt. A complete clean authenticated pair may support
one same-boot reopen only with all inherited checks; its existence does not
retroactively prove historical feature eligibility. Keep diagnostic bytes and
conservative debits even when receipt construction fails.

## 3. Actual-start pacing with a conservative upper bound

The inherited [transport design](V11_R09_GATE3_TRANSPORT_RUNTIME_DESIGN.md) §5
intentionally waits until confirmed close plus spacing: reservation and intent
samples occur before potentially delayed fsync. Calling that rule accidental
was incorrect. Replace FC1 §1's characterization and qualify §3's recurrence
as follows. Only the new independently reviewed IA1 scheduler/ledger/producer
may supersede that pause. Legacy close-plus-spacing and reservation-spacing
checks remain unchanged in their existing consumers.

Define actual start `t_i` as the first transport activity for request i, including
DNS, connect/TLS or request transmission on an existing connection, whichever
is first. No eager background activity may precede the dispatch gate. A qualified
adapter must bracket this boundary using original same-boot measured clocks:
`l_i <= t_i <= u_i`, with a proved finite `u_i-l_i <= a_i`. Sample l after all
reservation/intent persistence and immediately before authorizing transport;
sample u after the actual start has occurred, before returning control for body
processing. Delayed dispatch and scheduling within that bracket are included
in a_i. A response callback after an unbounded operation is not start evidence.
The producer must prove the ordering against its exact transport build; a field
named actual_start or a fixture's advance knowledge is insufficient.

Retain both original clock records, boot/request/context bindings, boundary
identity and a_i in `G3_V5_FC1_START_BOUND_IA1`, embedded in the already-required
TRANSPORT_CLOSED record and bound by the capture receipt. Persist it before any
next intent/dispatch. This introduces fields, not an extra per-request append.
Original clock bytes may not be replaced by hashes without retained, precharged,
authenticated evidence. Missing start evidence, missing closure, exceeded a_i,
clock reversal, empty offset intersection or unknown acknowledgement holds.
Crash before this durable close record is an unfinished attempt, never a reason
to infer a start from reservation/intent timestamps.

Immediately before actual transport permission for i+1, recheck:

```
previous transport confirmed closed; previous receipt custody complete
current lower monotonic bound >= u_i + s
current window/clock/resource/denial checks valid; one global token held
remaining worst-case work fits all unchanged cutoffs
```

Gate-to-start delay cannot make spacing unsafe, but must be bounded and charged.
Freeze the absolute request deadline D_i before DISPATCH_INTENT as in the
inherited design: min(original measured sample + frozen deadline, acquisition
end minus current offset upper bound, persisted elapsed deadline). Fsync/delayed
dispatch consume this allowance. Recheck after fsync and at transport permission;
never reset D_i from l_i/u_i or a later clock. Every DNS/TLS/header/read/EOF/abort/
close phase shares D_i. If a delay leaves insufficient reviewed successful-path
allowance, refuse; post-start failure accounts/cancels and holds as required.
A tighter clock may shorten a deadline, never extend it. All finalization still
fits FC1's three hours; the later decision gap supplies no added budget.

Let d_i bound actual transport lifetime through confirmed close, p_i the local
controls, and j_i other scheduling delay. Since `u_i <= t_i+a_i` and permission
for the next request can itself precede actual start by up to a_(i+1), use:

```
t_(i+1) <= t_i + max(s,d_i) + a_i + a_(i+1) + p_i + j_i
J = 2*A + J_other; A = sum_(i=1..N) a_i
T_bound = sum_(i=1..N-1) max(s,d_i) + d_N + P + Z + J + C
```

Two copies of A conservatively cover two distinct effects: delaying actual
dispatch after permission, and waiting to the previous start upper bound rather
than its actual start. This also covers initialization/tail endpoints. Assign
other work once; do not omit delays by calling them overlapping work. The IA1 timing
plan adds exactly `start_bound_ms`, an N-entry integer array of a_i>=0, to the
original keys. Its JITTER phase bounds contain exactly one `START_BOUND_TOTAL`
and one `DISPATCH_BOUND_TOTAL` entry, each with wall_ms=A and cpu_ms=0;
remaining JITTER entries total J_other.
A zero a_i needs a qualified exact-boundary clock producer; it is not a default.
All entries, sums and derived inequalities retain checked <=2^63-1 arithmetic.
Timing, phase and serializer proofs bind the new evidence acquisition and fsync.

The 9,586 s structural witness remains only conditional arithmetic: allocate
50 ms/request to a_i, A=135,650 ms for each of the two effects and
J_other=318,700 ms inside the unchanged 590,000 ms J. The normal d=2,000 ms,
P=3,500,000 ms, Z=60,000 ms and C=10,000 ms then still yield 9,586,000 ms and 1,214,000 ms slack. No producer or cost bound is
qualified by these numbers. The inherited close-plus-spacing calculation with
these same parameters is 15,010,000 ms and refuses. Report both; if IA1 evidence
or cost proof is unavailable, hold, never silently use the smaller equation.
Restart replays the original u_i, close, D_i, elapsed deadline and clock
intersection; it neither substitutes the reopen clock nor resets pacing/debits.

## 4. Counted bounded EOF, including the final probe

Select a conventional counted-read interface, not a full-read-with-eof result.
Replace FC1 §6's read inequality and FC16 positive control with:

```
ceil(r_i/65536) + 1 <= k_i <= min(r_i+1,65536)
4 MiB: 64 full payload calls + 1 bounded EOF call; frozen k_i = 65
```

Every read invocation consumes one slot before entry, including empty EOF,
failed/throwing reads and overdelivery probes. Check call_count<k_i before every
call; never invoke k_i+1 and never increase k_i dynamically. Positive short
reads may exhaust k_i before EOF and then refuse. No minimum short-read length
is assumed. A non-EOF zero-progress result refuses. `read(maximum_bytes,
absolute_deadline)` returns nonempty bytes, a distinct proved end-of-message
EOF sentinel, or failure; an empty bytes result is zero progress, not EOF.
No data-bearing result implicitly proves EOF.

After the expected range length has arrived (including cap equality), use a
counted probe with maximum_bytes=1. Any returned byte is overdelivery. Before
that point maximum_bytes<=min(65536,remaining expected length); eager violations
of the requested bound are counted before refusal. Premature EOF refuses.
EOF must prove framing completion and exhaustion of all already-delivered
application-visible queues; Content-Length alone does not do so. A qualified
adapter must expose all eager/failed/partial bytes and bound all outstanding
application-visible delivery by the same globally held G_rx, including delivery
while canceling/closing. It must not use an uncounted read inside close, a probe,
framing verification, prefetch or an EOF helper to escape k_i. Any body fetch
performed internally is an exposed counted read; transport buffering is bounded,
visible to accounting and costed. Bytes hidden from the caller are not free.

EOF is distinct from confirmed close. Success requires both, exact 206 range/
If-Range/Content-Range/identity encoding, full native validation and all inherited
custody. Close only acknowledges teardown and reports known outstanding bytes;
it does not consume a body or make missing EOF true. If closure cannot be proved
within D_i, hold globally. Cancellation forbids new body reads, but every late
queued/delivered byte still counts. Charge all reads, framing, queue inspection,
close and abort lag in the frozen stream/timing/CPU certificate. The 4 MiB probe
cost must be covered; the old 64-call certificate cannot be reused.

Full cap and attempt are durably debited before dispatch. Volatile received
count advances before interpretation/raw writes. Only confirmed close followed
by durable KNOWN_ACCOUNT establishes a measured total; any pre-accounting crash
or uncertainty retains full r_i plus the one-time G_rx, explicitly uncertain.
A known violation total is retained without enabling ACCOUNTED/SUCCESS/progress.
No late observation reduces an inherited uncertain debit. All observed excess
bytes are charged even on an adapter bound breach; such a breach invalidates
the safety proof, poisons the session and never gets clipped to r_i+G_rx.
Do not reuse unused cap or G_rx and never retry. Denial remains immediate and
durable, independent of receipt/EOF success. Existing legacy 32-chunk refusal
behavior is unchanged.

## 5. Cost proof, matrix and ordered review obligations

FC01/05/06/11–14/16–18/24 are updated in the companion. Every old mutation
survives unless its old k=64 wording is explicitly replaced by IA1 admission
refusal and attempted k+1 refusal. Unchanged cases and the full identity map
are copied literally. All cases remain FUTURE_NOT_RUN. The current checks
validate documentation and inherited regressions, not these future interfaces.

Receipt reordering preserves the twelve-session-record bound; embedding start
evidence changes serialized size. Reprove FC11/12 over exact IA1 serializers,
maximum original clocks/headers, failure paths, EOF/late delivery counters,
existing occupancy and one reopen. Do not assume 2,048-byte session records fit.
The old independent synthetic journal equations remain 32,560 session events /
66,682,880 B for N=2,713, with only 208 events / 425,984 B headroom. If the enlarged
records cannot satisfy the certificate, refuse the profile. No extra record,
clock truncation, uncharged evidence object or extra lifecycle tail is implicit.

Ordered implementation remains FC1 §8: (1) independently review this exact
amendment and separately adjudicate H1; (2) freeze IA1 schemas/plans/projections,
including new pins and pure derivation; (3) store bytes/custody; (4) ledger
transitions, capture latch and occupancy; (5) scheduler/start producer/counted
stream; (6) native qualification; (7) complete synthetic composition and all
inherited regressions; (8) separately authorized authentic package then G3-E.
Changed dependencies reopen every downstream review. No implementation against
unfinished step-2 bytes is part of this commit.

Reproduce local checks with `python3 tests/verify_v11_fc1_ia1_documents.py` and
`python3 -O tests/verify_v11_fc1_ia1_documents.py`. The checker uses explicit
exceptions/unittest checks, strict duplicate-key JSON and local JSON Schema;
no project implementation or provider is invoked. Run inherited offline ledger,
runtime and store restart regressions in both modes as recorded in the companion.
The author document suite passed 10/10 in each mode; the three inherited suites
passed 270/270 in each mode using the existing development pytest environment.
The optimized run warns about non-rewritten assertions; two inherited fork
deprecation warnings occur in each mode. The document checker uses no assert
statements. No package download was needed (system Python lacks pytest).
These checks do not qualify successful decode costs, actual-start producers,
stream adapters, real resources, native data or production custody.

**Integration hold:** require an independent different-model review of the exact
committed SHA/tree, all changed bytes, source bindings and validation output
before integration. This author does not perform or substitute for that review.
Report SHA/tree externally to avoid self-reference. H1–H6, A1–A8 and G3-L/G3-E
remain independently unresolved; neither a clean commit nor document PASS
changes them.
