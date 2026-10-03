# Gate 3 V5 full-cohort execution contract — 2026-10-03

**DOCUMENTATION CANDIDATE / PROPOSED_BLOCKED. No executable V5, launch approval,
provider qualification, or new acceptance credit.** This specifies a separately
versioned implementation target. It resolves contradictory *computations* by
replacing them explicitly; it does not assert that the selected real cohort fits.
If authentic native sizes, successful decode bounds, or held resources cannot
satisfy the equations below, the full cohort remains blocked.

Baseline commit `910b841471050b637c90dddd8443d214a131995d`, tree
`9e0a53db646f3aa7e7ec1c8c1548bfefb2660674`. Only this document and its two JSON
companions are candidate changes. The [source manifest](V11_GATE3_V5_FULL_COHORT_EXECUTION_CONTRACT_20261003.sources.json)
pins public Git blobs, SHA-256 and byte lengths at that baseline; the
[verification companion](V11_GATE3_V5_FULL_COHORT_EXECUTION_CONTRACT_20261003.verification.json)
contains arithmetic witnesses, the closed future test matrix and author checks.
No source pin is independent authentication. No private evidence or protected
master was read, and no provider, service, resource allocation or financial
operation was performed.

## 1. Precedence, preservation and exact amendment surface

Read this with the merged [evidence-role contract](V11_GATE3_V5_EVIDENCE_ROLE_CONTRACT_20261003.md)
(candidate `1c0e2d0e39dab15bc420292be35e5c611f082f67`, tree
`36ab1d102c26ce26a72c323b6cfd379deac733a8`) and reviewed
[feasibility dossier](V11_GATE3_V5_FEASIBILITY_20261003.md)
(candidate `87e89402e12bcb48fcfae22caa97438b02868d5b`, tree
`4254e8c69446179eb92fe9a86c108c689affa5e9`). Public checkpoint entries at lines
20651–20669 record their different-model PASS_IN_SCOPE and merges; their
original candidate-status text remains historical. Their documentation reviews
do not accept this successor. Baseline replay of their immutable source pins
must use their own Git baselines, never repin advancing status files.

The prior contract §§2–5 and H1–H6 remain normative except the following closed
execution-profile changes to resource representations and their projections.
All role, source, transport, event, identity and acceptance semantics survive.
No call to V4 with patched constants, no implicit V5 fallback and no auto-migration.

| Predicate / evidence | Classification and proposed disposition |
| --- | --- |
| 2,713 original slots; 8,139 role cells; full native trajectories | Safety/acceptance invariant: unchanged, including unsuccessful and unattempted rows. |
| One window; 3,600 attempts; 1 GiB received; 10,800 elapsed seconds including local work; one in flight; >=2 s start spacing; <=30 s/request | Safety invariants: all global and unchanged. Segments never reset them. |
| Full frozen purpose/provider cap per request; 2/4 MiB FIELD, 3 MiB INDEX and 4 MiB other maxima; 1 GiB range-offset bound | Retained invariants. Exact-range charging instead of full-cap reservation is **not** this proposal. Smaller pre-frozen provider caps were already legal. |
| `N*d+(N-1)*s+P+Z` | Accidental additive scheduler bound; replace only through §3's reviewed scheduler and proof, preserving spacing, deadlines and P/Z floors. |
| `2*N+A` simultaneous objects and `4*N+1` store events | Accidental V4 estimates, not evidence identities. Replace with §4's allocation lifetimes and reachable transactions; retain 4,096 physical objects and 10,000 global store events. |
| `20*N` session records; every prospective record charged 64 KiB; per-body-chunk durable budget append | Implementation bounds/representation. Replace with §5's typed transition/serialization budgets; preserve every causal transition, durable intent, refusal and debit. Keep journal bytes/event maxima. |
| 32 chunks × 64 KiB succeeds only through 2 MiB | Accidental read-loop ceiling. Replace with §6's bounded short-read stream, with no larger body or memory allowance. |
| Generic `ECMWFRequest` IFS six-hour subset | Adapter scope, not native protocol. Leave that adapter unchanged; qualify separate three-hour integration (§7). |
| 256 direct dependencies; graph/artifact/parser limits; host floors; clocks; rights; restrictions; A1–A8, G3-L and G3-E | Safety/acceptance invariants: unchanged and independently checked, never divided by segment count. |

This is an explicit successor to §6's obligation to pass **both** inconsistent
legacy estimates: calculate and report both legacy results as diagnostics, but
only an independently accepted `G3_V5_FULL_COHORT_COST_1` implementation can use
the replacement admission equations. Until then their failures still block.
Deleting those checks from existing consumers is forbidden.

The future manifest identity is `R09_GATE3_LAUNCH_MANIFEST_V5_FC1`; projection,
export, custody, review and trusted-pin schema literals receive the corresponding
`_FC1` suffix. The existing V5-only consumer must refuse these versions. Add exactly
one manifest/projection field `execution_profile`, copied in full into export;
its schema is `G3_V5_FULL_COHORT_EXECUTION_1`. Its exact keys are:
`schema, cost_model, scheduler_review, store_review, ledger_review,
stream_review, native_review, allocation_plan, record_plan, timing_plan`.
`cost_model` is the literal above; the next five values are the prior contract's
Ref type; the last three are Refs to canonical closed plans below. Bind their
complete bytes, dependency/build closure, hashes and lengths into runtime
context, review, custody and external trusted pins. Unknown versions/keys refuse.
All existing V5 request/role/event fields, seven export inputs, protocol history
and supplemental pins remain. H1 requires explicit equivalence adjudication for
all V4-named obligations; a schema suffix earns no review credit.

## 2. Denominator, bytes and a truthful feasibility claim

Let F=2,713, e in {1,2}, H the frozen real overhead request count and N=F+H.
Enumeration is GEFS 31×25=775, IFS 51×25=1,275, AIFS 51×13=663, provider order
GEFS/IFS/AIFS, ascending member then native hour 0..72. All F FIELD requests must
be present for full-cohort admission; every failure still leaves F report rows.
Events share captures: e*F links and 3*e provider expansions, with original
requested/trial keys. Event success is not feature eligibility.

For each request i, r_i is its **full frozen purpose/provider cap**, not its
selected length. For FIELD, `0 < end-start+1 <= r_i` and offsets remain within
the inherited 1 GiB limit. All arithmetic uses checked integers <=2^63−1:

```
B = sum_i r_i
  = 775*cG + 1275*cI + 663*cA + sum_overhead r_i
0 < cG <= 2 MiB; 0 < cI,cA <= 4 MiB
N <= 3600; B + G_rx <= 1 GiB
```

`G_rx` is a separately held, one-time global abort-overdelivery allowance (§6),
not transferable FIELD capacity. This is stricter than the inherited B<=1 GiB.
No cap change, unused-reservation reuse, retry, replacement member, forecast-body
substitution from offline storage, second window or HTTP compression is allowed.
Authentic exact selected-run ranges and frozen provider-wide maxima must support
all F sizes before review. This proposal does not invent those data.

Full upper caps give **9,753,853,952 B**, so still refuse. With no overhead and no
abort allowance, a common cap has maximum floor(1 GiB/2713)=395,776 B. This is a
necessary arithmetic bound, not a native-size assertion. Historical
1,469,234,173 B is an estimate using historical maxima, not current minimum
demand or qualification. The proposal retains that estimate as a diagnostic.
If verified current full-cap reservations exceed 1 GiB, no local segmentation
can solve it while preserving this gate: refuse before dispatch.

A deliberately synthetic capacity witness uses H=0, all three caps 256 KiB,
G_rx=64 KiB: B=711,196,672 B and B+G_rx=711,262,208 B. It assumes every synthetic
message fits that cap, all roles are synthetically presealed and no real provider
exists. It proves that the equations do not categorically reject F; it does not
prove that a real GEFS/IFS/AIFS cohort can meet those assumptions. Overhead must
be counted from actual accepted capabilities; the conditional 851-object example
in the dossier is not a source contract. Two confirmations per such object would
yield N=4,415 and refuse, regardless of segment layout.

## 3. Exact scheduler and local-work bound

All durations below are integer milliseconds. UTC/monotonic readings retain the
existing <=1 s uncertainty policy, same-boot custody, wall-jump detection and
whole-interval validity. Let 1,000<=d_i<=30,000, in whole seconds and equal to the manifest's
frozen request deadline, be a total transport lifetime bound from
start through cancellation, socket close and abort lag; s>=2,000. A qualified
scheduler must start i+1 only after both i is closed and start_i+s, plus required
local controls. No speculative concurrency. P includes all nontransport local
wall work exactly once: setup, parsing/hashing/import verification, durable
reservation, clock calls, fsync, raw seal, native full-grid decode, graph checks,
replay and snapshots. Work performed before the acquisition accounting origin
cannot be silently credited as in-window work. If reverified in-window, cost it.

Let P>=max(60,000,1000*F), Z>=60,000 for finalization/report/emergency persistence.
J is the sum of bounded scheduling/timer dispatch delays not included in d/P/Z;
C is the worst-case clock conversion/uncertainty guard. No zero J/C without a
qualified bound. The conservative recurrence and its admission bound are:

```
t_(i+1) <= t_i + max(s,d_i) + p_i + j_i
T_bound = sum_(i=1..N-1) max(s,d_i) + d_N + P + Z + J + C
T_bound <= 10,800,000
```

Here p_i includes local work between starts, initialization and tail work enter
P separately, and all p_i sum to at most P. Proof: actual previous-request
lifetime plus its local work is at most d_i+p_i;
`max(s,d_i+p_i) <= max(s,d_i)+p_i`. Thus this formula never assumes local CPU,
fsync or decode overlaps the network. Admission also checks the independent
absolute acquisition-end and decision/target-day cutoffs with uncertainty.
For this profile all processing/finalization must fit the three-hour bound;
the 17:00–18:00 gap is not extra budget. Runtime checks remaining worst-case
work before every start and stops if the reviewed schedule can no longer fit.
A bound violation closes/poisons the attempt; it never moves an absolute cutoff.

For H=0, uniform d=1 s and ideal J=C=0, the structural floor is 8,198 s
(8,199 at d=2), replacing the impossible additive 10,910 s. Those zero-delay
figures are arithmetic diagnostics only. The synthetic witness sets d=2 s,
P=3,500 s, Z=60 s, J=590 s, C=10 s: **9,586 s**, with 1,214 s slack. P must bound
successful full decode and failure paths, not merely a timeout that aborts every
field. Sequential default A7 20 s ×1,938=38,760 s cannot pass; neither stopwatch
averages nor RAW seals certify a smaller successful decode profile.

`timing_plan` is exactly `{schema:G3_V5_TIMING_PLAN_1,deadline_ms,spacing_ms,
local_wall_ms,local_cpu_ms,finalization_ms,jitter_ms,clock_guard_ms,phase_bounds}`.
The deadline array has N entries in request order; phase_bounds is an ordered
array of `{id,owner,wall_ms,cpu_ms,review:Ref}`. IDs are unique, owner is exactly
LOCAL|FINALIZATION|JITTER|CLOCK_GUARD; nonnegative bounds sum exactly to the named
plan totals, and LOCAL CPU<=LOCAL wall. Deadline work is separately covered by
stream_review. Local and finalization work must exhaust the implementation's
call graph including bounded recovery; missing costs refuse BUDGET. Derived
worst-case totals, not caller-supplied totals alone, control admission.

## 4. Physical objects, lossless segmentation and allocation lifetimes

Use a new store version. Never reinterpret a V1 store or change its hashes.
Retain PREPARE before writing, bytes fsync, directory/link durability, original
clock prefix plus post-durability seal clock, COMMIT and authenticated external
head. Preserve provenance, all original clock bytes, dependency identities and
historical-versus-current acknowledgement semantics. A journal receipt is a
logical record, not a second forecast body. A V5 RAW provenance record may bind
its three external role dependencies explicitly; V1's RAW/no-dependencies
predicate cannot simply be reused. Review this incompatibility independently.

Let I be all pre-frozen physical imported proof/support chunks and their catalog
objects; A the aggregate/event manifests; D_o the number of decoded-output
objects. Count imports already present, not just newly copied ones. No assumed
cross-request RAW deduplication is used for capacity. Let O_old cover any other
existing occupied names. With exactly one store transaction/temp active globally:

```
O_final = O_old + I + N + A + D_o
O_peak = O_final + 1 <= 4096
S_add = 3*(I_new + N_remaining + A_new + D_new) + 4
S_existing + S_add <= 10000
```

The third event per object conservatively reserves a failure/recovery annotation;
PREPARE and COMMIT are separate. Four additional events reserve initialization,
context/closure and one reopen audit. At most one acquisition reopen is allowed
by this profile; further opens are read-only diagnosis and cannot append. Any
inherited unfinished transaction/intent blocks progress without completing it.
Failure reports have separately reserved space; no repeated recovery appends
can consume an unbounded tail. A process failure can invalidate completion of
this cohort, never justify a retry or a fresh counter.

For event FIELD dependencies let `a(n)=1+sum w_k` where w_0=n,
w_(k+1)=ceil(w_k/256), adding each w_(k+1) only while w_k>256; the leading 1 is
the event root. For F=2713, a(F)=12 and A>=12*e. Additional feature/proof/catalog
nodes must be added, never hidden in this minimum. With two events the trees
have 5,448 edges (5,426 FIELD edges +22 child-to-root edges). Direct dependencies
including supplemental external/prerequisite edges remain <=256. Recompute the
combined 16,384 graph-edge and 8,192 external-edge counters from the full closure;
packing never reduces semantic edge counts. Three external edges/FIELD consume
8,139, leaving only 53 external edges for every other use; this can still block.

Only **pre-frozen supporting artifact bytes** may be segmented. Raw forecast
messages stay whole, one complete GRIB per FIELD, with no repacking or slicing
for acceptance. For each supporting artifact of length L<=1 GiB, use
`k=ceil(L/(4 MiB))` consecutive chunks, last chunk short; all preceding chunks
are exactly 4 MiB. Its catalog entry is exactly
`{artifact:Ref,parts:[{ordinal,offset,length,sha256}]}` with ordinals 0..k-1,
offset=ordinal*4 MiB, positive lengths and exact coverage [0,L). Preserve the
original whole-artifact hash, media type, source receipt and custody. Chunks are
transport/storage representation, never new publisher evidence. Catalog schema
is `G3_V5_ARTIFACT_SEGMENTS_1`, with exactly `{schema,entries}`; entries are in
first semantic use order. Chunk/catalog objects all count in I and O_peak.
Catalog bytes obey 4 MiB/object; if a catalog cannot fit, refuse (no recursive
catalog extension in FC1). Physical representation links also count in the full
graph; they never replace original scope/use/provenance edges.

Hash and parse by bounded streaming over consecutive chunks, <=1 MiB total
stream buffer, while enforcing original artifact depth/node/string/array limits.
Do not concatenate a 1 GiB artifact in memory. Reject gaps, overlap, reordering,
extra tail, partial codepoints, unexpected EOF, cycles, compression and aliases.
An accepted parser must reproduce exactly the unsegmented canonical bytes and
semantics and verify both each chunk and whole digest before use. If the parser
cannot operate within the bound, refuse; segmentation does not qualify it.
Original authorized source storage retained alongside imports is charged too.
No deletion/compaction of original evidence, no archive-as-proof shortcut.

No combination of the illustrative counts below is a constructed all-gates
fixture. In particular, 8,139 external role edges plus 5,448 event edges leave
only 2,797 of 16,384 graph edges for proof internals, selections, source records
and representation links. Counting repeated scope/use edges remains mandatory.
Actual graph expansion may independently refuse, even if the storage arithmetic
fits. FC23 must establish the coupled closure rather than assuming it exists.

The synthetic witness uses O_old=0, I=64, A=24, D_o=2, N=2713, all imports new:
O_final=2,803, O_peak=2,804, S_add=8,413. I=64 is a fixture packing budget,
**not** a claim that the production proof closure fits. The closure must satisfy
artifact counts, edges and bytes simultaneously. Unmet proof/clock/graph terms
cannot be assigned zero to reproduce the witness.

`allocation_plan` is exactly `{schema:G3_V5_ALLOCATION_PLAN_1,objects,
allocations,closure:Ref}`. `objects` is an ordered array of
`{id,kind,bytes,existing}` with kind RAW|IMPORT|CATALOG|AGGREGATE|DECODED,
unique ID, boolean existing, bounded bytes; the one active TEMP is derived.
`allocations` is an ordered array of `{id,domain,bytes,first_phase,last_phase,
review:Ref}` for disk and memory allocations; domain is an authenticated domain
identity, phase indices are in the reviewed lifecycle. Include journals,
source copies, temp, two snapshots when used, decoder child/parent, imports,
indexes, report and emergency reserve. Derive the count and byte equations from
this exact list and qualified ownership/lifetime evidence. Max over phases is
permitted only within the same allocation domain with proved non-overlap.

## 5. Journal/event budgets without deleting evidence

Keep each of budget/session/shared-denial/store journals <=64 MiB, each serialized
record <=64 KiB, and event maxima respectively 131,072/32,768/32,768/10,000.
Segments of a journal, if used for read I/O, remain one logical chain with one
head, sequence and cumulative byte/event counter. No rotation/reinitialization,
truncation, pruning, receipt omission or per-segment allowance. FC1 needs no
journal segmentation to meet its witness. Existing occupancy always subtracts
from the same caps, including shared history from other manifests.

Replace generic worst-case record multiplication with a closed, build-bound
serializer/state-machine cost certificate. For each journal j and legal record
type t, let c(j,t) be the maximum **framed serialized** length (including newline,
escapes, integer width, hashes, clocks and raw evidence), n(j,t) its remaining
reachable multiplicity. Then:

```
E_j_existing + sum_t n(j,t) <= event_limit_j
J_j_existing + sum_t n(j,t)*c(j,t) + tail_j <= 64 MiB
0 < c(j,t) <= 64 KiB
```

No average length, Python object size, post-hoc sample or maximum successful
path only. Prove all failure branches, maximum scalar/array lengths and one
reopen against the exact schema. Enforce the bound *before* append with capacity
held for downstream denial, close, accounting and refusal. Refuse before
network when no certificate can cover the next request. Unknown/oversize
records never truncate clocks/headers; retain a bounded missing-evidence reason
and refuse without further dispatch. Production qualification additionally
requires the accepted clock/transport producer to fit the reviewed record profile. Oversize original evidence already received is
retained in the charged bounded emergency/report area where possible, with its
exact missing-evidence cause otherwise; no partial receipt can become SUCCESS.
Raw denial and conservative debit survive independently of receipt acceptance.

New budget state machine has at most four records/request:
RESERVE (durable full r_i and attempt debit before dispatch),
KNOWN_ACCOUNT (actual received total after confirmed close, or violation total),
TERMINAL, and one conservative failure annotation. No per-read fsync is required:
while open, full reservation remains irreversibly charged on crash/uncertainty.
The volatile received counter advances before interpretation and raw writes.
Release unused capacity only after durable known close/accounting; frozen plan
reservations still cannot be reallocated. An overdelivery holds G_rx, charges all
observed bytes, poisons the session and precludes further starts. A crash before
KNOWN_ACCOUNT retains the full request reservation **plus G_rx**; authenticated
recovery cannot lower an inherited uncertain debit. Retain uncertain status,
never relabel that upper bound as measured bytes. This preserves conservative
safety, with less fine-grained chunk history; transport/raw capture still retains
original headers/body and original receipt clocks required by acceptance.

New session state machine has at most twelve records/request. In order: INTENT,
BUDGET_RESERVED, DEADLINE_FIXED, DISPATCH_INTENT, optional DENIAL,
TRANSPORT_CLOSED, ACCOUNTED, OBJECT_WITNESSED, CAPTURE_RECEIPT, TERMINAL;
one CLOCK_CHECK and one FAILURE_ANNOTATION are reserved. Failure may skip
unreached phases, never reorder close/account/terminal or assert witnessed raw
bytes. Repeated clock checks are local computations, not repeated state changes;
their required original evidence stays in the bounded receipt/cost certificate.
Denial remains a nonterminal durable annotation immediately upon observation.
All original reasons and terminal precedence survive. Twelve is a new proved
transition bound, not a smaller constant substituted into the V4 runtime.
Shared ledger retains at most four records/request: intent-open, denial,
restriction-unresolved, intent-close; absence of a denial does not clear history.
Each journal reserves four additional lifecycle/reopen records.

The witness chooses budget/session/denial records <=1,024/2,048/4,096 B;
store PREPARE/COMMIT <=8,192 B each, optional annotation <=1,024 B, four fixed
records <=1,024 B, and a 16 MiB store emergency/report tail. These are **tighter
synthetic serialization profiles**, not proven bounds on existing serializers:

```
E_budget = 4*N+4;     J_budget = (4*N+4)*1024
E_session = 12*N+4;   J_session = (12*N+4)*2048
E_denial = 4*N+4;     J_denial = (4*N+4)*4096
E_store = 3*O_final+4
J_store = O_final*(8192+8192+1024) + 4*1024 + 16 MiB
```

For N=2713,O_final=2803: E=10,856/32,560/10,856/8,413;
J=11,116,544/66,682,880/44,466,176/65,575,936 B. All pass individually on empty
synthetic roots. Session headroom is only 208 records and 425,984 B; at these
bounds H<=17, and existing history may reduce that further. A 3,600-request
profile does not automatically fit this journal profile. A 64 KiB clock, receipt
or denial record cannot be silently substituted for the selected tighter bound.
Large fixed evidence (including 256 hashes per aggregate) may use immutable,
precharged object content referenced by digest, but its semantic dependencies
and original bytes remain accessible and counted. For aggregate dependencies,
FC1 PREPARE replaces only the inline `dependencies` array with
`{dependency_count,dependency_sequence_sha256}`; the ordered full sequence is
inside that same immutable aggregate body, covered by its object hash. Validate
all prior typed commits before PREPARE and verify the sequence hash/count on
replay before recognizing the object. RAW dependencies stay inline. This avoids
duplicating up to 256 hashes in the PREPARE record, without erasing any edge or
introducing a new recursive evidence format. All other provenance/clock fields
remain present. The reviewed serializer must prove this indirection and forbid
dangling/pending references; the unchanged V1 serializer cannot supply it.

`record_plan` is exactly `{schema:G3_V5_RECORD_PLAN_1,journals}`; journals is the
ordered budget/session/denial/store array. Each entry has exactly
`{kind,existing_events,existing_bytes,tail_bytes,types}`; types is the closed
build-enrolled record-type order, each `{type,max_bytes,max_remaining,review:Ref}`.
Derive multiplicities from the authenticated existing head, full frozen remaining
schedule and legal state machine, never from caller estimates. The cost review
binds exact serializer code plus all raw evidence schemas. Failure of any byte,
count or actual serialization check refuses the whole execution profile.

## 6. Streaming, physical capacity and restarted admission

Replace 32 callback chunks with bounded reads: at most 64 KiB per read, never
more than the remaining cap plus a bounded EOF/overdelivery check. Freeze a
per-request read-call limit k_i satisfying
`ceil(r_i/65536) <= k_i <= min(r_i+1,65536)`; include it in stream_review's exact
request-ordered cost certificate. Positive short reads each consume a call;
empty EOF ends the stream, and a non-EOF zero-progress result refuses. A 4 MiB
message needs 64 full reads and is representable; arbitrary one-byte reads may
hit k_i and refuse. No minimum short-read size is assumed in CPU/time bounds.
The worst successful or abort path has <=k_i calls, <=r_i+G_rx observed body bytes,
and bounded hash/parse/write work charged to d/P. Body success still requires
exact single 206 range, If-Range, Content-Range total/length, identity encoding,
complete native message, expected section/grid/member/run and no extra bytes.

G_rx=64 KiB in the witness bounds **all** outstanding eager application-visible
body delivery at abort globally. The future adapter must prove an enforced
maximum including internal queues/prefetch; a generator that can return arbitrary
bytes is not qualified. Record every eager/failed/partial byte before rejecting.
One overdelivery stops the whole session. If this receive bound cannot be proved,
refuse admission rather than assuming a read(size) call restricts returned bytes.
No body-read batching may hide attempts, bytes or immediate provider denial.

Let allocations in §4 describe bytes held over phases. For each filesystem/
quota/backing domain x:
`Q_x = max_phase sum(bytes of live allocations in x)`; include block rounding,
metadata/inodes and all source/import copies. For each memory/cgroup ancestor y:
`M_y = max_phase sum(live parent+child+buffer+parser+snapshot+state allocations)`.
No sum/max of V4 and runtime estimates substitutes for this lifetime proof.
Safe fallback where overlap is unproved is the sum of all candidate lifetimes.
Reserve raw bodies B, one temp <=max(r_i,4 MiB), retained imported chunk/catalog
bytes, A/D outputs, all four journals including reserved tails, dedicated report
>=16 MiB and emergency persistence, decoded working bounds and all new metadata.
If separate allocations back the store tail and report, charge both; aliasing
credit requires an exact reviewed allocation identity and exclusive lifetime.

Physical admission retains the reservation design's equations per qualified
domain: `free-U-G >= disk_floor>=2 GiB`, and
`headroom-P-O-G >= memory_floor>=512 MiB`, plus every effective ancestor limit.
Here U/P are the actual held candidate allocations Q/M, O other private demand,
and G a proved bound on foreign consumption through use. Unknown ancestor,
quota/backing identity, G, held reservation, decoder headroom or custody refuses.
No sparse-file, quota value, address-space cap, swap or historical probe counts
as reservation. Read checks before every start, decode and sealing step retain
use-time qualification and expiry. No current host feasibility claim is made.

Reopen reads stream the original chains and immutable objects with the same
parser/graph/memory limits, validates prior externally retained heads and binds
boot/manifest/cohort/plan/denial lineage. Capacity uses **existing + remaining**,
never a fresh-root witness. Recompute every count and debit, retain all old
original times, and refuse torn records, aliases, missing artifacts, altered
inventory, unknown restriction expiry and inherited unfinished intents. No
automatic repair, truncate, replayed dispatch or new clock to backdate evidence.

## 7. Native IFS qualification and unchanged acceptance gates

Qualify the existing G3-I `NativeECMWFThreeHourRequest` semantics through a new,
separate native index/URL/identity/range/A7 bridge. Do not edit the generic
six-hour live adapter. IFS uses every 0:3:72 point for all 51 members, including
the 612 points the generic subset would omit. AIFS retains 0:6:72. IFS control
uses oper/fc; perturbed file uses enfo/ef but member rows use pf. AIFS retains `enfo/cf|pf`. Normalize a missing control number only under the independently
accepted cf/fc parser; never normalize a missing perturbed member.

Preserve unique native selector, release/run/grid/2 m instantaneous temperature
in K, K−273.15 only, full GRIB framing and full-grid decode/re-encode checks where
required, nearest extraction <=50 km, no interpolation/member copying or daily
extrema substitution. Use batches of <=51 unique members and <=16 MiB selected
ranges, partitioned in member order when necessary, but every member still has
one separate FIELD single-range request. Existing parser 16 GiB offsets do not
relax the V5 1 GiB offset limit. G3-I shape plus A7's separate request view is
useful source code, not a qualified end-to-end native path. GEFS CGI HTTP 200/
subregion/64 KiB decoding is not a qualified full-native range-206 implementation.
No endpoint, descriptor, index association or rights enrollment is supplied here.

All 79 identity rows in the prior verification companion's G3-L preservation
map carry forward literally, with stage/scope/disposition unchanged and zero
new credit. All H1–H6 holds, 77 missing identities, A1 observer evidence and
independently qualified A2–A8 remain. Preserve frozen 00Z latest-complete-ready
runs, <=86,400 s age/no fallback, station/date/1–2 HIGH/LOW cohort, official
metadata before acquisition, preregistration<=review<start, causal original
receipts/seals, uncertainty/expiry, feature-seal upper<=decision lower and target
local-day boundaries with pinned tzdata. Preserve all reasons/partitions and
event×provider eligibility; RAW success alone never becomes native/feature credit.

Keep lawfully retained proof rights/lineage, authenticated interpretation and
scope for all 8,139 cells. Sealed offline roles create no HTTP status or attempt;
scheduled confirmations must be earlier real charged requests confirming frozen
facts, with no failure-to-offline switch. Unknown publisher/time/rights refuses.
No credentials, redirects, retries, mirror or origin/client/run reset; retain
401/403, 429/503, Retry-After, unresolved ECMWF history and cumulative denial
heads across origins/restarts. Producer, different-model reviewer and external
custodian remain independently authenticated, with exact commit/tree/build/
input/projection/review/terminal pins and fresh revocation/anti-rollback state.
No candidate self-pin closes that channel. G3-L, authorized capture and G3-E
actual-byte replay precede any later eligible use. No SHADOW, funding or C/J/E/A
crossing follows from documentation or synthetic capacity PASS.

## 8. Closed synthetic matrix and ordered implementation plan

The companion lists exactly FC01–FC24. Every future case has one positive control,
a finite enumerated mutation set, exact expected refusal stage and owning
boundary. No random-provider fixture or accepted-production output. Run all in
normal and optimized Python with network, private-path, protected-root, service
and credential tripwires. Mutation tests change one fact at a time; crash cases
cover each specified durable boundary, including lost final acknowledgement.
A runtime refusal never deletes original rows/debits/reasons. These are future
acceptance obligations, **not executed tests** in this documentation change.

Implementation must proceed in this order, one independently reviewed exact
candidate per step; changed dependencies reopen downstream review:

1. Accept this execution amendment in scope only; freeze source pins, complete
   gate-preservation map and FC matrix. Obtain explicit H1 version/equivalence
   decision. No production-capability enrollment or operational approval.
2. Implement pure FC1 schemas, typed plans, formula derivation and projections;
   deterministic bounds and closure checking, strict legacy refusals. FC01–06,
   FC19–24. Preserve both legacy diagnostic calculations.
3. Implement the separate lossless support-artifact reader/store and lifecycle
   accounting. Prove typed PREPARE/COMMIT clock/provenance equivalence, bounded
   aggregation, temporary lifetime, graph counts and crash custody. FC07–10,
   FC16–18. Existing stores remain unreadable as FC1 without separate reviewed
   translation; no live migration is part of this plan.
4. Implement bounded ledger serializers and conservative reserve/known-account
   transition machines; prove every failure path and occupancy count. FC11–14,
   FC16–18. Reject profiles whose original clocks/headers cannot fit without loss.
5. Implement isolated synthetic start-to-start scheduler and streaming adapter;
   inject worst-case delays, short/eager reads, fsync/abort failures and clock
   jumps. Prove successful-path costs and stop behavior. FC05–06, FC14–18.
6. Qualify native IFS/AIFS index/identity/range/decoder integration and separate
   GEFS full-field semantics against lawfully retained authorized fixtures.
   FC02, FC15, FC19. Keep rights/release/control-domain enrollment separate;
   public code comments do not qualify a real release.
7. Compose validator/export/resource/store/runtime/restart/report/A8 seams and
   G3-L equivalence mapping; run **all FC01–FC24**, inherited V01–V16 and relevant
   existing refusal/restart/clock/resource/A2–A8 regressions on exact build bytes.
   Independently review host/cost/clock producer contracts and genuine successful
   decode bounds. Synthetic composition grants no launch authority.
8. Only a separately authorized future package can supply authentic selected-run
   sizes, all proof roles, enrolled provider/native semantics, external trust,
   actual held resources/clocks and A2–A8/G3-L reviews before a fresh window.
   Recompute all equations and full closure on that package. Any infeasibility
   remains BLOCKED; do not shrink F or roll dates. After authorized capture,
   G3-E actual-byte replay and all existing acceptance gates still apply.

## 9. Author verification and handoff

Author checks cover public Git source bindings, unchanged historical proposal/
dossier blobs, denominator/cadence arithmetic, old contradictions, new structural
witnesses and boundary/+1 inequalities, complete inherited identity-map equality,
companion JSON closure, local links and whitespace. The source reader uses
`git show <full-baseline>:<fixed-public-path>` without executing project code;
advancing working-tree status files do not alter an immutable baseline replay.
The author verification passed 67 checks in normal Python and 67 in optimized
Python, including companion reproduction; all local links resolved. The unchanged
predecessor checker passed 32/32 in each mode on its historical public baseline.
Whitespace and documentation-only diff checks passed. No operational tests,
benchmark, actual native decode, provider request or host probe occurred. The companion distinguishes these performed checks from the
future FC matrix. Require different-model exact commit/tree review before any
integration; this author commits only the isolated clean candidate.

Status remains **91/200, formal 1/50; 77 missing identities; G3-L NO-GO;
NOT_READY_TO_FUND; V5 PROPOSED_BLOCKED**. The earlier dated proposal is expired;
this document selects no new window. Report the candidate commit/tree externally
so this document need not contain a self-referential commit hash.
