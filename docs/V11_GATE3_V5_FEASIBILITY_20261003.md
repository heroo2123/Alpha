# Gate 3 V5 full-cohort resource feasibility — 2026-10-03

**FULL COHORT BLOCKED under the retained reservation rules. Architecture findings
only; candidate pending different-model exact review.** No supported qualifying
2,713-slot architecture fits all the existing gates. This is stronger than “sizes
are unknown”: time and capacity predicates reject the complete schedule even if
every FIELD body were tiny and every evidence role were lawfully supplied offline.
It is not a proof that every future, separately reviewed architecture is impossible.

Baseline: `e73fb9b66c0cf2591c0b8017925c764aac6cff1e`, tree
`8afe7a24c6e437b0b6345a1fadc08e136797a2c1`; clean isolated branch
`gate3-v5-feasibility-20261003` at recovery. No applicable AGENTS.md was found in
the workspace/ancestor or docs/tools directories. The reviewed proposal is
`1c0e2d0e39dab15bc420292be35e5c611f082f67`, tree
`36ab1d102c26ce26a72c323b6cfd379deac733a8`. Its three proposal artifacts are present
unchanged at this baseline. The appended current entries in
[checkpoint](V11_WORK_CHECKPOINT.md#L20651),
[requirements](V11_REQUIREMENTS_MATRIX.md#L4819), and
[progress](V11_ENGINEERING_PROGRESS.md#L7552) record documentation-only acceptance,
still PROPOSED_BLOCKED. Older prepended status entries describe earlier states.
Those public records, rather than private review files, establish this dossier's
status context. **No G3-L PASS, identity/authority credit, or integration approval.**

All citations below refer to baseline bytes. The
[arithmetic companion](V11_GATE3_V5_FEASIBILITY_20261003.arithmetic.json) pins 22
public source files by SHA-256 and length and records reproducible integer results.
No provider/current release facts were obtained. Paths mentioned inside public
historical summaries were not followed; no private master or retained private
evidence was accessed.

## 1. Scope of the impossibility result

The [V5 contract §§2, 6](V11_GATE3_V5_EVIDENCE_ROLE_CONTRACT_20261003.md#L47)
retains full native coverage, schedule feasibility/processing minima and all
unchanged limits; lines 314–351 retain full-purpose-cap reservations and both
capacity estimates, while explicitly blocking the new local cost formula.
Removing mandatory overhead HTTP requests cannot remove FIELD obligations.
The [protocol](V11_R09_GATE3_COLLECTION_PROTOCOL.md#L131) fixes 2,713 distinct
messages, and lines 237–243 prohibit turning a survivor subset into a complete
multi-model example. For a complete successful cohort, let F=2,713 and let H>=0
be real non-FIELD requests; N=F+H. Give the proposal its most favorable case H=0.
Every additional request, imported proof and local operation can only add demand
under the retained formulas. Failed/crash-uncertain attempts do not buy coverage.

| Gate, with no network overhead | Exact result | Classification |
| --- | ---: | --- |
| Requests <=3,600 | 2,713; 887 remaining | Fits alone; overhead must fit too |
| FIELD full 2/4 MiB cap reservations <=1 GiB | 9,753,853,952 B = 9,302 MiB = 9.083984375 GiB | Impossible for this cap profile; 8,680,112,128 B over |
| Inherited serial bound <=10,800 s, choosing deadline=1 s | 10,910 s | Hard admission contradiction, independent of body sizes |
| Inherited store objects <=4,096 | 5,438 | Hard inherited formula contradiction |
| Inherited store events <=10,000 | 10,853 | Hard inherited formula contradiction |
| Runtime budget journal <=64 MiB, full caps | 6,222,970,880 B | Hard conservative capacity rejection |
| Runtime session events <=32,768 | 54,261 | Hard conservative capacity rejection |

“Hard” here means rejection by retained normative/computed admission bounds,
not a claim that actual small records occupy their maximum size or that a real
request necessarily lasts its deadline. V5 has no executable validator; these
are requirements it preserves and cannot simply omit. Its explicitly unresolved
replacement formula is not evidence that a cheaper store is safe.

## 2. Native FIELD and request semantics

[Launch constants](../tools/v11_r09_gate3_launch.py#L26) give:

| Provider | Native inventory, inclusive 0..72 h | FIELDs | Max-cap reserved bytes |
| --- | --- | ---: | ---: |
| GEFS | 31 members × 25 three-hour points | 775 | 1,625,292,800 |
| IFS | 51 members × 25 three-hour points | 1,275 | 5,347,737,600 |
| AIFS | 51 members × 13 six-hour points | 663 | 2,780,823,552 |

HIGH and LOW share raw captures; one or two events do not double downloads, but
two events require 5,426 FIELD links. A provider path/file is not a FIELD:
[ECMWFRequest.selectors/url](../polymarket_scanner/v11/ecmwf_sources.py#L52)
distinguishes IFS control `oper/fc`, IFS perturbed file `enfo/ef` with `pf` member
rows, and AIFS `enfo/cf|pf`. [plan_ranges](../polymarket_scanner/v11/ecmwf_sources.py#L116)
requires exact selectors, unique members and one range per selected message;
missing control `number` is normalized only for cf/fc, and duplicate or missing
matches refuse. Its <=51-member, <=16 MiB selected-range batch cap is another
bound, not permission to fetch a multi-member FIELD. Native integration must
respect it or separately review its replacement. The V5 FIELD contract requires
one exact 206 single range and complete GRIB framing. Same-file sharing saves
some index/identity work, not the 1,275/663 member-message denominator.

The [generic request constructor](../polymarket_scanner/v11/ecmwf_sources.py#L34)
rejects `step % 6 != 0`, even for IFS. That would supply only 663 IFS fields and
omit **612**, leaving 2,101 total instead of 2,713. The separate
[NativeECMWFThreeHourRequest](../tools/v11_r09_gate3_collector.py#L142) already
accepts IFS 0:3:72 and AIFS 0:6:72, but has no native index/URL/decoder integration
by itself and is not an ECMWFRequest accepted by `plan_ranges`. Do not claim a
total absence of three-hour support: [A7's preflight bridge](../tools/v11_r09_gate3_a7_decoder.py#L237)
passes exact step/source data via a separate request view, and the
[native decoder](../polymarket_scanner/v11/ecmwf_grib.py#L148) checks actual
run/member/step, 2 m instantaneous temperature and control/perturbed templates.
The unresolved item is a qualified end-to-end native index/identity/decoder path.
The [offline adapter findings](V11_BRAIN_IFS_AIFS_NATIVE_ADAPTER_FINDINGS_20261003.md#L63)
also separate this upstream work from the later real-example adapter.

GEFS's [existing adapter](../polymarket_scanner/v11/gefs_sources.py#L89) builds a
CGI subregion request and expects HTTP 200 (lines 120–137). That is not the V5
full-native single-range 206 contract. The
[adjudication lines 21–51](V11_R09_GATE3_LAUNCH_CONTRACT_ADJUDICATION.md#L21)
explicitly separates its 64 KiB/25-point decoder from the 2 MiB full-field path,
whose rights, range/index association and full-grid decoder must be qualified.
No CGI, S3, mirror or nominal public endpoint is enrolled by these findings.

Request sharing is conditional on real object identities. For illustration only,
the ECMWF path forms suggest 25×2=50 IFS and 13×2=26 AIFS objects; **if** a
separately qualified GEFS path used one object per member/hour, there would be
775+50+26=851 objects. One INDEX plus one OBJECT_ID confirmation for each would
add 1,702 requests, giving 4,415 before metadata: too many. One index per object
alone would give 3,564, leaving 36 requests. These are conditional counts, not a
provider contract, selected plan, or required lower bound. Offline proof roles
may remove those requests only with authentic pre-freeze supporting evidence.
Zero overhead is already insufficient for the complete cohort.

## 3. Bytes: distinguish a cap proof from data-dependent fit

[V4 lines 577–580, 681–707](../tools/v11_r09_gate3_launch_v4.py#L577) permit a
**lower frozen provider-wide FIELD cap** within the 2/4 MiB maxima. Every request
must still reserve that full chosen cap; its exact range must fit it; and the
sum of all scheduled reservations must be <=1 GiB. Thus the 9.08 GiB result is
not a theorem that every syntactically legal smaller cap vector exceeds 1 GiB.
For smaller caps cG,cI,cA and overhead reservation B_H, necessary conditions are:

```
775*cG + 1275*cI + 663*cA + B_H <= 1,073,741,824
0 < length(slot) <= c_provider <= applicable 2/4 MiB maximum
```

A common integer cap could be at most 395,776 B with zero overhead. Every actual
native message must meet it; that is not established here. Reserving exact index
lengths instead of the chosen full purpose cap, accepting a truncated message,
or amortizing large fields against small ones would change retained semantics.
[DurableBudget completion](../tools/v11_r09_gate3_launch.py#L949) releases unused
reservation after a durable known outcome; it does not waive the prelaunch
sum-of-reservations predicate or permit mid-window replanning. Uncertain attempts
and all received failed/partial/eager bytes remain charged.

The public [historical estimate](V11_R09_GATE3_LAUNCH_CONTRACT_ADJUDICATION.md#L53)
is 1,469,234,173 B, already 395,492,349 B over 1 GiB before overhead. It multiplies
historical provider maxima, **not** minimum future sizes or the actual selected
cohort total. The committed [size summary](../config/v11/r09_gate3_observed_message_sizes_20260930.json)
supplies no current release, original clock or rights qualification; its underlying
databases were not opened. Neither historical minima nor maxima can certify the
next run. The inherited `estimated_full_raw_bytes >= 1469234173` field is a
conservative recorded estimate, not a requirement that received bytes exceed 1 GiB.

HTTP identity encoding, exact native message framing and independent section/grid
checks remain mandatory. GRIB packing already exists inside the raw message;
[CCSDS decoding](../polymarket_scanner/v11/ecmwf_grib.py#L37) requires full-grid
decode and byte-exact re-encode integrity, despite returning a station value.
There is no supported compressed HTTP/station-only/native-message substitution.
Also the existing synthetic runtime permits <=32 body chunks and asks for <=64 KiB
per read ([runtime lines 1568, 1638–1661](../tools/v11_r09_gate3_runtime.py#L1638)):
its successful non-overdelivery path can carry at most 2 MiB in those chunks.
A 4 MiB receipt *cap* does not prove that path accepts a 4 MiB success. Actual
fields may be smaller; V5 transport integration and chunk semantics still need
review. Eager overdelivery accounting is a refusal path, not extra success capacity.

## 4. Time and processing

[V4 lines 572–576 and 711–720](../tools/v11_r09_gate3_launch_v4.py#L711) require
integer deadline d>=1, start spacing s>=2, processing P>=max(60,F), finalization
Z>=60 and `N*d + (N-1)*s + P + Z <= 10800`. With H=0 the minimum is
`2713 + 5424 + 2713 + 60 = 10910`, **110 seconds too large**. At d=30 it is
89,587 s. Every overhead request adds at least 3 s without reducing P. Even
ignoring every other gate, the minimum formula admits at most 2,685 FIELDs;
2,686 already requires 10,802 s. A faster observed socket cannot override this
worst-case admission rule. There is no integer d that fits the full cohort.

This formula conservatively adds start spacing after every request duration.
It is not a physical lower bound: with an exactly implemented start-to-start
scheduler and no unbudgeted work, a different proof might bound network time by
`(N-1)*max(d,s)+d`. At d=1/2 that plus minimum P and Z is 8,198/8,199 s. Such
a substitution needs an explicit versioned amendment, clock/fsync/scheduler and
abort-lag proof; it is **not** the inherited rule and does not resolve bytes,
storage or actual decode work. The fixed 17:00 acquisition end and 18:00 decision
do not grant a fourth elapsed hour: V5 §6 expressly retains <=10,800 s including
processing/finalization. No work can be backdated to those cutoffs.

P=2,713 s is only a floor. [A7 Bounds](../tools/v11_r09_gate3_a7_decoder.py#L48)
defaults to 15 CPU/20 wall seconds per invocation; using that timeout envelope
sequentially for the 1,938 ECMWF FIELDs alone is 38,760 wall seconds, before GEFS,
hashes/parse/native pins, parent startup, fsync or reporting. This is an allowed
worst-case envelope, not measured actual runtime or proof all fields take 20 s.
Bounds allow smaller choices, but no qualified whole-cohort successful decode
profile is supplied. The [runtime RAW seal](../tools/v11_r09_gate3_runtime.py#L1734)
is not A7 native decoding. V5 §6 also requires bounded local CPU/wall, memory,
imports, snapshots and graph traversal to be included once in local work; none
can be assumed free or overlapped across a single sequential worker.

## 5. Storage, journals and evidence graph

The [V4 calculation](../tools/v11_r09_gate3_launch_v4.py#L799) aggregates with
fanout 256: ceil(2713/256)=11, then one root, A=12. It reserves `2*N+A=5438`
objects and tests `4*N+1=10853` store events. Independent necessary limits are
N<=2,043 for objects and N<=2,499 for events. Neither changes when bodies shrink.
Its other event checks (21,705 session and 92,243 budget) fit their respective
32,768/131,072 caps; passing those does not override the tighter checks.

With J=64 MiB, object cap C=4 MiB, report R>=16 MiB and decoded reserve D>0,
V4 requires `Qv4=4*J+R+(2*N+A)*C+D+B`. At full FIELD caps and minimum R it is
**32,847,691,776 + D B**, before new V5 imports/work. This already-invalid
plan's hypothetical disk requirement must not be mistaken for an allocation.

[CapacityPlan](../tools/v11_r09_gate3_runtime.py#L287) is a second, different
estimate. With full caps, K=32N, budget records K+3N=94,955. Each full-field event
needs 12 aggregate/root nodes, so E=12 or 24 for one or two events:

```
T = 3*(N+E)
Qruntime = 2*B + 2*E*4 MiB
         + (35*N + T + 20*N + 4*N + 4)*64 KiB + 16 MiB + 4096
Mruntime = 2*max(reservation) + 4*64 KiB
```

One/two events yield T=8,175/8,211 and Qruntime=30,651,322,368/30,754,344,960 B;
RAW-only memory is 8,650,752 B, **not a native decoder/host peak**. T and runtime
raw object counts alone fit the numerical store ceilings, unlike V4's conservative
formula. The [actual runtime admission checks](../tools/v11_r09_gate3_runtime.py#L1128)
also enforce journal bytes and existing occupancy: budget 6,222,970,880 B,
session 3,556,048,896 B, denial 711,196,672 B each exceed its 64 MiB journal cap.
Session events 54,261 exceed 32,768; store journal reservation also exceeds 64 MiB.
At 32 chunks even 30 requests exceed a fresh budget journal; 29 is merely a
necessary bound for that one check. Even hypothetical one-byte reservations
give `(1+3)*2713*65536=711196672` budget-journal B, still too much.

These are conservative prospective record reservations, not actual serialized
record lengths. [The store](../tools/v11_r09_gate3_store_v1.py#L605) separately
checks objects/events/journal headroom before sealing; hardlinks/temp lifetime and
small actual JSON do not justify deleting prospective bounds. V5 §6 requires both
estimates plus new costs, not choosing whichever is smaller. The
[reservation design](V11_GATE3_RESOURCE_RESERVATION_DESIGN_20261003.md#L177)
requires a reviewed allocation/lifetime reconciliation for any overlap reduction.
Neither summing nor taking max blindly demonstrates physically held capacity.

Proof obligations remain **8,139 role cells**, not necessarily 8,139 distinct
artifacts. Exact-byte sharing can reduce stored bytes, while every scope and use
edge remains. V5 §6 caps combined referenced artifacts at 4,096, graph edges at
16,384, external edges at 8,192 and combined direct dependencies at 256 per
request/receipt; aggregation/imports/temporary copies also count against store
capacity. With three distinct direct external proof edges per FIELD, 8,139 edges
leave only 53 for other external edges. This is a conditional edge count, not a
proof that all schemas must consume precisely that many external hashes. Role,
internal graph, supplemental external and event-link counters must be distinguished
and independently recomputed from the actual closure. Per-request three-role
fanout fits 256; an event's 2,713 FIELD links need aggregation. Neither fact proves
the entire graph fits. Shared artifacts cannot erase applicability or provenance.

The <=4 GiB total hashed bytes, <=1 GiB per referenced artifact, 32 MiB manifest/
projection, 40 MiB inputs, 33 MiB consumer, node/depth/string/array bounds and 1 MiB
streaming buffer are refusal ceilings, not resource reservations. A single 1 GiB
referenced artifact also does not fit the inherited 4 MiB store object cap as-is;
its retained location, bounded verification and any reviewed representation must
be specified. No content packing, chunking or omission scheme is qualified here.
Worst-case parse/hash/replay/report memory, CPU, bytes, graph closure and restarted
journal occupancy remain unqualified; missing terms cannot be set to zero.

## 6. Host reservation is a separate blocked proof

[Runtime prospective checks](../tools/v11_r09_gate3_runtime.py#L1167) subtract
capacity before requiring the host floors. The
[public host assessment](V11_GATE3_HOST_RESOURCE_FEASIBILITY_20261003.md#L165)
reports one historical sample of 3,073,671,168 B available disk and
1,134,837,760 B available memory; it proves no reservation. Default A7's 512+128 MiB
additional conservative envelope plus the 512 MiB host floor requires
1,207,959,552 B, missing that sample by 73,121,792 B. This is not a present host
measurement or proof every possible decoder needs that peak. No new host probe,
allocation, lock, cgroup, quota, credential or privileged action was performed.

Qualified capacity needs held filesystem/mount/quota/backing identities, complete
memory ancestry, allocation lifetimes, report/emergency persistence, bounded
foreign consumption and independently retained custody through use/restart. The
[proposed conservation equations](V11_GATE3_RESOURCE_RESERVATION_DESIGN_20261003.md#L243)
are `F-U-G >= disk floor` per qualified allocation domain and
`H-P-O-G >= max(512 MiB, manifest floor)` plus every effective ancestor's fit.
Unknown G or hidden ancestors cannot be assumed zero. A quota number, fallocate
on one report, sparse reserve, AS cap or swap does not reserve host floors.
[A7._available_memory](../tools/v11_r09_gate3_a7_decoder.py#L130) still refuses
with `A7_HEADROOM_UNKNOWN`; no diagnostic helper substitution is authorized.
More disk/RAM alone would not fix the time, received-byte or fixed-count failures.

## 7. Explicit blocked disposition and evidence needed

No operational amendment is proposed as safe: available evidence supports none
that preserves **all** retained computations and completes the cohort. Retain
V5 PROPOSED_BLOCKED. This dossier/JSON schema versions the finding only; it
confers no new manifest parser, protocol permission, enrollment or trust pin.

| Decision owner/boundary | Required concrete next evidence or decision |
| --- | --- |
| Protocol/resource architecture | Explicitly acknowledge the incompatible predicates. A successor must name every changed reservation/scheduler/store formula and its version, preserve the 1 GiB/3,600/10,800/one-in-flight/4,096/10,000 hard limits, and prove complete-cohort fit. Merely adjusting caps cannot fix time/store. If that cannot be proved, remain blocked; any proposal to change hard limits or native coverage is outside this task and requires a separate explicit owner decision. |
| Provider/native qualification | Lawfully retained selected-run native indexes and coherent object size/strong ETags; all 2,713 exact complete message ranges, release/rights and full-grid semantics; proper IFS three-hour/control/perturbed integration and GEFS full-field decoder. Current V4 range offsets are also capped at 1 GiB (lines 682–684); the generic parser's 16 GiB offset allowance does not waive that. No live acquisition follows from this request. |
| Resource implementation | Reviewed successful-path and failure/restart cost bounds for every serialized record, clock/header/dependency graph, durable state/temp lifetime, and report; native decode and scheduler bounds with headroom. Packing objects, changing journal cost reservation, or replacing additive timing all need distinct exact reviewed implementations and equivalence proofs; these are research options, not accepted fixes. |
| Evidence/trust/host | Authentic pre-freeze INDEX_SELECTION, OBJECT_IDENTITY and COHORT_METADATA proofs; original observations and custody, accepted interpretation, rights/freshness, complete restriction-domain/denial history; external producer/reviewer/custodian authentication and revocation; qualified through-use clock/resources and actual A2–A8/G3-L evidence. No invented provider descriptors, smaller-current-body assertions, or self-pins. |
| Independent review | Review this exact candidate commit/tree and source bindings using a different model before any local integration. Documentation PASS can accept the contradiction finding only. No self-approval or merge by this author. |

Any successor must preserve V5 §§2–5 and H1–H6: original slot/reason partitions,
1–2 event/provider expansions, frozen run/cohort/schedule, causal original
receipts/seals and fixed window/cutoffs, lawful rights/retention, denial/retry
restrictions across origins/restarts, no credentials/redirects/automatic retries,
independent pin/custody/revocation and A2–A8/G3-L/replay gates. No subset,
multi-window rollover, offline forecast-body substitution, archive compaction
that loses original evidence, changed packing/grid, or post-hoc cap adjustment
earns full-cohort qualification. Retained public evidence is insufficient to
select or qualify any such architecture.

## 8. Reproduction and handoff

```
python3 -B tools/v11_gate3_v5_feasibility_arithmetic.py --check
python3 -O -B tools/v11_gate3_v5_feasibility_arithmetic.py --check
git diff --check
```

The checker reads only its fixed public source list and companion, uses standard
library integer/AST operations without importing or executing project code, and
tests source formula parity, fanout/object/event/time boundaries, full cap sums,
generic/native cadence loss, minimal-byte counterexamples and historical-estimate
arithmetic. It checks byte-for-byte report reproduction, including every source
hash. These are **32 offline arithmetic/source checks**, not a V5 implementation
test suite, benchmark, field decode or provider/host qualification. `--write` is
candidate-authoring only; regenerating hashes does not independently authenticate
anything. Tests use explicit exceptions and remain active under optimized Python.

Author validation completed: **32/32 checks passed in each mode**; all 22 source
pins matched `git show` at the baseline; all three proposal files matched
`1c0e2d0`; all 34 local citation targets/line anchors resolved. Whitespace checks
passed. These results are author evidence only, pending independent exact review.

Only this dossier, its arithmetic JSON and the checker are candidate additions.
No existing status, operational/legacy/V4/V5 code, financial/V10/Axiom code or
other worktree is changed. No network/provider request, private evidence access,
root authority, service action, orders, funding, execution or SHADOW launch.
Normal sandbox startup failed before the first read (bwrap loopback setup);
subsequent repository-only shell operations used the reviewed sandbox escalation.
No approval rejection was bypassed. Preserve this isolated candidate and hand its
full commit/tree and test results to the independent reviewer; do not integrate it
on the strength of the author's checks.
