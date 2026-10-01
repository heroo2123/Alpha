# Gate 3 bounded transport and runtime design

Status: **DESIGN_PENDING_INDEPENDENT_REVIEW**. Astra/high author, 2026-10-01.
Base `01dfd73`, including offline integration `f11541c` / tree `accb47f9`.
This completes the design task in the [integration review](V11_R09_GATE3_INTEGRATION_REVIEW_e563e45.md),
not its independent acceptance or implementation. No approved live origin exists
as a result of this document. No provider requests, G3-L, SHADOW, learner,
publication, root authority or financial permission follows. **91/200; formal
1/50; NOT_READY_TO_FUND.** All fixtures in the next batch must remain synthetic.

## 1. Compatibility and proposed version boundary

Retain the [protocol](V11_R09_GATE3_COLLECTION_PROTOCOL.md), independently
reviewed [launch addendum](V11_R09_GATE3_LAUNCH_CONTRACT_ADJUDICATION.md),
[strict offline integration](V11_R09_GATE3_INTEGRATION_REVIEW_e563e45.md) and
[store restart contract](V11_R09_GATE3_STORE_RESTART_DESIGN_7b5a235.md).
The private FINAL-REVIEWED master's hash was verified in place and its causal
archive, provider-failure and nonfinancial separation requirements consulted.
No private content is copied here. Prior accepted code remains valid in its
stated offline scope. The following are missing runtime contracts, not claims
that an active collector has violated them:

- `validate_manifest` V3 returns a digest, not approval. It binds every purpose
  to the field object's origin/path, forces overhead before fields, and requires
  a frozen range. It cannot describe an actual separate `.idx` or official
  metadata endpoint. Never append a suffix or substitute a URL behind it.
- `DurableBudget` owns request/byte accounting and relative monotonic pacing;
  it does not enforce absolute UTC, purpose semantics, transport identity or
  denial history. An incomplete reservation blocks further requests globally.
  Its replay currently lacks bounded journal allocation; composition needs a
  bounded-reader repair, with tests, before runtime qualification.
- `RestrictionLedger` is an in-memory record set with hash-checked restoration;
  it is not a durable multi-job denial service. `SyntheticExchange` supplies
  invented peer/TLS facts and does not implement HTTP or DNS.
- Store v1 preserves original clocks and UNKNOWN recovered acknowledgement.
  Neither successful budget completion nor store recovery proves runtime use.

Propose a separately validated `R09_GATE3_LAUNCH_MANIFEST_V4` for future runtime
plans. Do not loosen V3, silently translate V3 into V4, or migrate journals.
Retain all V3 cohort, source, code, review, clock, safety and ceiling checks.
V4 changes only the following closed-schema groups, subject to this review:

| Group | Required addition/change |
| --- | --- |
| sources/network | Ordered endpoint table with unique endpoint ID, provider/control-domain ID, exact HTTPS origin, GET, purpose, constrained path template, and reviewed dossier/access-reference digests. Source dossiers bind the field endpoint plus explicit index/object/metadata mapping evidence. Network allowlist equals the table's unique origins in first-use order. |
| schedule | Each request adds endpoint ID and exact request path distinct from its existing target field-object identity/path. Recompute object/index/cache IDs from reviewed mapping, not URL resemblance. Bind bounded dependency receipts, parser identity and purpose response contract. Preserve unique request IDs, overhead-first order, fixed subset, fixed ranges and original 2,713 slots. |
| runtime | New closed group: runtime policy artifact, canonical schedule digest, shared denial-root/descriptor identity and expected history head, session/report-root identity, per-purpose request/byte totals, journal/report/resource bounds and clock policy. All values immutable and digest-bound. |
| clocks_and_receipts | Separate preregistered method/build/calibration evidence from future per-attempt observations. Request/body/decode/seal observations are append-only runtime records, never fabricated preregistration evidence. Preserve four phases, uncertainty and cutoff checks. |

Use exact canonical encoding and artifact resolution from V3; unknown fields,
missing mappings or unsatisfied references refuse. Detached review envelopes
must bind V4 bytes, endpoint policy, code and completed independent terminal.
V3's pinned original/addendum references remain; add this design's independent
review reference, never replace old accepted hashes. A new V4 implementation
requires exact-commit independent review before integration. This document is
not a ready-to-use manifest or source dossier.

## 2. Origins and purpose-specific transport

An origin is usable only when its exact scheme/host/port/path mapping is covered
by a current dossier, reconciled denial lineage and exact-manifest G3-L envelope.
NOMADS, GEFS S3, ECMWF public data and ECMWF S3 are **unapproved candidates** here.
Historical success confers no approval. Freeze provider/control-domain groups
that connect related origins and earlier preflight jobs; unknown relationship
or unresolved prior restrictions block qualification. There is no mirror list.
Metadata origins need their own approval; a weather allowlist never grants them.
Labels and official outcomes are absent from this pilot's request schedule.

The future adapter accepts an immutable `RequestIntent`, not a free URL, callback,
or caller-provided client. It must validate the exact endpoint and path before
DNS; reject userinfo, fragments, query/signature material, escaping/encoded path
ambiguity and unapproved ports. GET only. No ambient proxies, netrc, cookies,
Authorization, cloud credentials, client authentication, redirects, retry,
connection fallback or automatic decompression. No dependency may create a
hidden second connection or request. A local adapter configuration cannot
expand the approved manifest.

Resolve once inside the reserved attempt and deadline; bounded DNS result set,
reject any nonpublic/special-use answer, and freeze one selected public peer.
Connect directly to that checked address while verifying TLS and hostname for
the approved original host. Recheck the actual peer; a second resolution,
redirect, alternate peer after failure, private address, certificate failure or
unverifiable peer fails closed. TLS trust/build identity and DNS classification
policy must be reviewed and frozen. These are acceptance requirements for a
future adapter, not facts proved by synthetic peer flags. DNS is part of the
single attempt, not an unbudgeted preliminary probe.

Headers are bounded during parsing: <=4,096 encoded bytes total, <=32 entries,
name <=64 bytes and value <=1,024 bytes, or stricter frozen limits. No duplicate
case-insensitive names, conflicting framing, transfer encoding or content
encoding other than identity. TLS/protocol framing uses a separately bounded
transport buffer; body accounting includes every HTTP entity byte delivered to
that boundary, including discarded/error/prefetched bytes. A library that cannot
bound and account read-ahead is unsuitable. Never claim the 1 GiB entity-body
budget measures TCP/TLS wire traffic.

| Purpose | Reservation before DNS/socket | Successful response contract |
| --- | --- | --- |
| INDEX | Frozen index cap, <=3,145,728 bytes | Bounded 200 identity body; parse exact native slots and offsets; keep raw bytes and own validator. Explicit mapping evidence must tie index version to field object. |
| OBJECT_ID | Frozen metadata cap, <=4,194,304 bytes | Bounded 200 purpose-specific descriptor and validator; prove target size and strong field-object ETag through reviewed mapping. No automatic HEAD/range replacement. |
| METADATA | Same frozen metadata cap | Bounded 200, pinned official event/rule/station parser and identity. Receipt remains distinct from forecast receipt. |
| PROBE | Same frozen metadata cap | Only its explicitly reviewed bounded GET contract; never implicit retries or authority to explore endpoints. |
| FIELD | Full frozen provider cap: GEFS <=2,097,152; IFS/AIFS <=4,194,304 bytes | Exact 206, single frozen range, exact Content-Range/object size and body length, identity encoding and matching strong ETag with If-Range. Full GRIB identity/decoder checks remain separate. |

Index and field ETags are not assumed equal: their association needs reviewed
version-coherence evidence. A 200 on FIELD, invalid index mapping, changed object,
weak/missing ETag, missing dependency or unsupported purpose response fails.
Do not derive new ranges or schedule more requests from observed responses.
The pilot can refuse as infeasible if genuine evidence cannot prefreeze ranges
or support these GET-only contracts. Resolving that requires a separately reviewed
preflight or protocol change, not permissive purpose handling. There is no
preflight network authority here.

## 3. Ownership, journals and durable denial history

One coordinator process/thread owns all session operations, rejects reentrancy
and fork-inherited use before locks/state, and acquires nonblocking locks in
this order: shared denial root, session root, `DurableBudget`, store v1. Release
in reverse order. Directory descriptors anchor the first two locks; pin expected
private root device/inode, owner and mode throughout. Budget/store retain their
reviewed lock and child-close semantics. A competing job cannot use the same
control domain by selecting a different manifest or new output directory.
Different denial roots are not interchangeable: the reviewed lineage pins the
shared root and predecessor history. Root creation/installation is not part of
this design; offline tests create only disposable private fixtures.

Separate namespaces: shared denial descriptor/journal; session descriptor and
attempt journal; unchanged budget subdirectory; unchanged store-v1 namespace;
pre-reserved report sink. Do not add unexplained files inside store v1. Metadata
uses exclusive creation, regular-inode pinning before data open, bounded reads,
canonical closed schemas, sequence/previous/event hashes, file fsync and parent
name persistence. Unknown names, missing/torn state, unsafe inode/path, failed
write/fsync, exhaustion or head mismatch hold the session without truncation,
cleanup, force flags or automatic repair. Bind host/boot, code/policy/manifest,
request order and all directory identities. Descriptor <=4,096 bytes.

Proposed additional caps: each session/denial journal <=64 MiB, record <=64 KiB,
<=32,768 events per journal; request intents <=3,600; at most one unfinished
intent; retained raw clock evidence <=16 KiB/record. Existing store caps remain
4 MiB/object, 4,096 objects, 10,000 events and 64 MiB journal. Budget replay must
reject over 64 MiB or 64 KiB/record before unbounded allocation; persist a
<=131,072-event limit including reserve/chunk/complete events. Read chunks at
most 64 KiB; tiny chunks also consume record capacity. Capacity exhaustion stops
work even when body-byte allowance remains. Never increase bounds on replay.

Reserve a separate 16 MiB report area before acquisition, within the declared
local storage quota, plus worst-case descriptor/journal/object/temp capacity for
the frozen subset. Local storage and response-byte budgets are different: count
each explicitly. Validate worst-case journal/receipt/dependency counts as well as
bytes before admitting the subset. No full 2,713-slot store-capacity claim is
made. Keep >=2 GiB disk and >=512 MiB available memory after prospective
allocation, at startup, before each request and each one-field decode batch.
No cleanup, eviction or host/service changes to obtain space.

Durable denial records carry control domain and origin, manifest/request/intent
identity, status/reason, bounded raw evidence or explicit evidence-missing cause,
original clock evidence, parsed Retry-After and original window end. Import prior
history only with its bytes, hash chain, externally expected head and explicit
mapping/review; empty new files are not evidence of no past denials. Missing
lineage or a prior unfinished intent is an unresolved hold across future jobs.

401/403, 429/503, explicit denial, or any Retry-After stops the affected origin
and mapped control domain for the whole original window. Finite cooldown is
at least that window end and any evidenced later provider expiry; delta seconds
are measured from the conservative receipt upper bound. An invalid/missing expiry
remains unresolved for future jobs until separately reviewed permitted resumption.
401/403 require explicit reviewed resumption, never credentials or an inferred
expiry. Expiry never reopens the same pilot or permits a retry. Resumption is an
append-only external review reference, not deletion/reset of the denial. Unrelated
providers may continue only with healthy journals and a known closed request.

Record/fsync a shared `INTENT_OPEN` before any possible request; later record
observed denial immediately when headers/status make it known, before reading
more body or releasing any accounting. A crash between receiving denial and
persisting it leaves INTENT_OPEN unresolved and therefore blocked, including
under a new manifest. If denial persistence fails, close transport, retain full
reservation and stop globally. A diagnostic file elsewhere cannot clear the hold.
Hash chains cannot detect coherent rollback without an independent retained head;
report that limitation and keep real archive/launch qualification gated.

## 4. Session state machine and accounting composition

Durable order for each frozen request (all referenced records use immutable
hashes and request ID, never just object digest):

1. Verify exact plan/review inputs, prerequisite receipts, current denial state,
   ownership, clock/window, resource and journal/report capacity. Refused entries
   acquire terminal reasons without DNS or transport calls. Persist session
   `ATTEMPT_INTENT`, then shared `INTENT_OPEN`, including purpose, endpoint,
   range/validator, denial-head and maximum reservation.
2. `DurableBudget.reserve(request_id, purpose_cap, started_monotonic=...)` must
   persist before transport. Persist session `BUDGET_RESERVED` binding the actual
   reserve event hash. Failure at any intervening boundary means no dispatch and
   conservative recovery hold; never infer that no socket was opened from a
   missing later journal record.
3. Immediately before DNS, recheck time/pacing/denial and persist `DISPATCH_INTENT`
   with actual measured start. Only then call the injected offline transport.
   That durable intent is not proof a request actually reached the provider.
   Keep a single global token until transport is closed and settlement complete.
4. During headers/body, record denials first and account every delivered body
   chunk with `budget.consume` before parser/store use. Each bounded read uses
   `next_read_limit` and remaining deadline. No extra read to test EOF at zero
   allowance: require verified framing/completion, otherwise refuse. An adapter
   overdelivery is charged even beyond allowance and poisons the run. Journal
   failure leaves uncertain received bytes visible but never claims them durable.
5. Close transport; establish bounded known closure/framing and total delivered
   bytes. Persist session `TRANSPORT_CLOSED` and shared `INTENT_CLOSED`, binding
   outcome/denial history and accounting head, before `budget.complete`. Complete
   releases only unused reservation and means accounting closure, not semantic
   success. Known failed/partial closure may complete; ambiguity or stream/journal
   violation cannot. No automatic retries in either case.
6. Persist `ACCOUNTED` with completion-event hash. Successful validated bodies may
   then decode and seal through store v1; attach source/decoder/dependency pins and
   original phase clocks. Persist session `OBJECT_WITNESSED` with the returned
   receipt, then terminal outcome. Store failure cannot refund received bytes.
   Budget failure cannot be completed using a store receipt.

Shared INTENT_CLOSED must contain a complete bounded outcome; it cannot merely
mean the caller invoked close(). An attempt uncertain after process failure holds
the entire same-manifest budget; future jobs inherit a control-domain uncertainty
hold unless exact closure evidence exists. Do not add a recovery path that calls
`complete()` on an old incomplete reservation. Existing legacy helper
`consume_synthetic_response` remains unchanged and is not the runtime entrypoint.

Per-purpose accounting derives exclusively from the frozen request map and actual
budget events: attempted count, known delivered bytes, completed count, outstanding
reserved ceiling and violations. No mutable caller chooses a cheaper purpose.
For purpose p, charged(p) = delivered bytes on completed attempts + full ceilings
on unfinished attempts. The global charge is the sum over purposes; reported
known delivered bytes include violations and remain separate from charged ceilings.
An observed overdelivery halts; do not hide it by capping the report to 1 GiB.
Total requests <=3,600 and aggregate reservations <=1,073,741,824 bytes.
Freeze each purpose's planned request count and reservation sum; unused overhead
or field headroom cannot expand another purpose, subset or schedule. Failed DNS,
connect/TLS, HTTP/semantic errors and partial bodies still consume their reserved
attempt. Planning reserves overhead before fields. Cached bytes share original
receipt identity; they create no new download, clock or statistical trial.

## 5. Absolute window, pacing and measured time

Let S be the frozen preceding-date 14:00 UTC start, A its 17:00 UTC acquisition
end, and D the frozen 18:00 decision lower bound. For each healthy clock sample,
L=UTC-uncertainty and U=UTC+uncertainty. Require L>=S and U<A for every dispatch;
body completion upper bound must be <=A. Freeze the conservative offset interval
O=[UTC-monotonic-uncertainty, UTC-monotonic+uncertainty]; intersect with every
session/attempt sample. Empty intersection, stale measurement, wrong boot,
clock reversal/step or uncertainty >1 second stops acquisition and causal claims.

At dispatch monotonic m with upper offset O_hi, deadline is the minimum of
m+frozen_request_deadline (<=30 seconds), A-O_hi, and the persisted session
elapsed deadline. Persist that elapsed deadline from the first valid session
clock plus <=10,800 seconds; absolute A still caps a late start or restart.
A later clock narrows/shortens permitted time; it never extends an already fixed
request deadline. DNS, TLS, headers, reads and close are bounded by this single
absolute deadline; phase timeout resets are forbidden. If cancellation/close
cannot be proven, hold globally and launch no further request.

Persist DISPATCH_INTENT samples as well as the budget's reservation times;
neither is proof of the exact socket start because fsync can delay dispatch.
For a provable conservative pacing rule, sample monotonic time after each known
transport closure, persist it in TRANSPORT_CLOSED, and require the next dispatch
sample >= that closure sample + the frozen interval (at least two seconds).
Thus the prior actual start is no later than the retained closure sample. Also
preserve the budget reservation interval. A missing closure holds, including
after restart. Recheck immediately before DNS. One token globally covers all purposes/providers and
implicit connection phases. Serialized decoding is local, with one field in
memory; it cannot trigger extra requests. No busy loop, timer rollover or new
clock can reopen an expired window. Same-boot restart replays original starts,
first deadline and uncertainties. Cross-boot acquisition is refused; evidence
inspection preserves original boot/monotonic values without comparison to now.

After A only local decode/seal/report work may run. Every dependency receipt,
metadata clock, decode and feature-manifest seal upper bound must be <=D for a
candidate timely feature. A late completion stays diagnostic, never relabeled.
The report may finish later and records its real time; it is not a retroactive
feature-ready clock. V4 schedule feasibility must include N * request_deadline + (N-1) *
start_interval + processing_seconds + finalization_seconds <= elapsed cap;
this is stricter than V3's max(deadline, interval) spacing because of the
conservative post-close pause. Processing/finalization allowances include local
persistence work; overruns refuse at the absolute boundaries, never extend them.

## 6. Budget, store, clock and runtime-use receipts

A `CaptureReceiptV1` is a typed append-only session record binding manifest/code/
schedule/endpoint digests, request/intent identity and purpose, denial heads,
reserve/chunk/completion event hashes, transport-close evidence, raw-response
identity, source/index/object mapping, decoder and store receipt hash, original
clock sequence, complete dependency graph and explicit outcome. Hash presence
alone attests none of those facts. All graph edges point backward to verified
records in the same approved context; missing/cyclic/substituted edges refuse.

No need for a distributed atomic commit: each crash boundary is conservative.
An orphan reserve holds; orphan bytes remain diagnostic; store PREPARE holds;
store COMMIT without session receipt cannot create a successful attempt;
ACCOUNTED without a store receipt proves accounting only. Valid surviving
records may be inspected/replayed but recovery never synthesizes original
acknowledgement, clock samples or runtime admission. Any cross-journal mismatch
holds. Recovery barriers prove current persistence only.

Store v1 permits RAW or FEATURE_MANIFEST objects and at most 256 dependencies
per manifest. Use RAW for successful index/metadata bodies with purpose retained
in the session receipt. Decode completion for overhead means its actual bounded
schema validation, not GRIB decoding. Failed/header-only responses belong in
bounded attempt evidence, without a fabricated successful four-phase sequence.
For an aggregate with >256 dependencies, use deterministic validated submanifests
of <=256 backward references, then a root manifest; recursively validate every
leaf and clock. These nodes are evidence aggregation only, carry unique manifest
identity/kind bytes, and do not inflate sample counts. Their object/event/byte
cost must fit the frozen subset capacity; otherwise refuse before acquisition.
Duplicate raw bytes reuse an explicitly bound original receipt only where source
and attempt semantics allow it; store duplicate refusal must never be bypassed.

Feature-manifest durability has its own actual store seal clock. Raw seal time
cannot stand in for it. Store's post-object-fsync sample precedes COMMIT fsync and
caller return: an on-disk COMMIT therefore does not prove a historical decision
had the receipt. Keep `historical_feature_eligible=false`, recovered
acknowledgement UNKNOWN, feature/label/learner/SHADOW admission GATED. A later
separately reviewed original runtime-use/decision record must bind the exact
feature receipt and contemporaneous observation after return; this offline
state machine will not create one. G3-E still needs independent actual-byte
replay, genuine time/source/build/storage evidence and that original-use proof.

Terminal report partitions all 2,713 original raw slots exactly once, retaining
all reasons and frozen primary precedence, including never-attempted slots and
failed prerequisites. Show purpose/global accounting, denial lineage, attempted
subset, raw completion, provider trajectories, requested HIGH/LOW events and
all-provider intersection separately. No survivor cohort or fallback provider;
shared fields/members cannot multiply trial counts. Report completion requires
actual durable report evidence; if capacity/persistence fails record INCOMPLETE
where possible and retain the hold. No successful terminal from a printed marker
alone or a valid-prefix journal scan.

## 7. Required independent acceptance and implementation slices

These are proposed acceptance cases, **not tests already executed**:

| Group | Required counterexamples and positive controls |
| --- | --- |
| Endpoint/purpose | Same host wrong path/purpose; `.idx` silently appended; unofficial metadata; changed range/ETag; missing index-object mapping; supported explicit separate paths. Reject before transport when knowable. |
| Denials | Each status and Retry-After form; absent/invalid/long cooldown; restart/new manifest/origin alias; old unresolved preflight; denial-write failure; crash before/after shared open/deny/close; unrelated provider continuation only after known closure. |
| Accounting | Every purpose plus DNS/TLS/404/partial/invalid response; exact and over limits; no hidden retries/read-ahead; tiny chunks hit event cap; per-purpose sums equal global replay; unknown reserve never refunded; no budget widening. |
| Time/serialization | Before S, exactly safe boundary, uncertainty crossing A, late first start, delayed reserve fsync, actual starts <2 seconds despite spaced reservations, deadline in each I/O phase, missing close, clock step/suspend/staleness/boot change, repeated restart preserving original deadlines. |
| Persistence | Interrupt each intent/reserve/dispatch/denial/close/complete/store/report boundary; short write/fsync failures and torn records; bounded replay; external head mismatch; fork/thread/reentrant/competing ownership; paths/FIFOs/devices reject without blocking; report reserve survives ordinary failure. |
| Receipt semantics | Budget complete/store missing and converse; RAW vs feature seal; late dependency in nested graph; >256 leaf aggregation; duplicate bytes/different provenance; recovered UNKNOWN; no synthetic runtime-use proof; missing report preserves INCOMPLETE. |
| Coverage/isolation | Exact frozen 2,713 partition; failure propagation to prerequisites/event/provider; false financial/host/promotion/launch flags; no import by production/Brain/SHADOW entrypoints and no network calls from offline tests. |

First: a different model independently reviews this exact committed design and
source compatibility, especially the V4 change, denial/attempt crash ordering,
actual-start pacing, post-fsync clock boundary and capacity arithmetic. Publish
local report/result/terminal with exact reviewed commit/tree and document digest;
PASS must explicitly mean offline design only. Findings require repair and fresh
review of changed bytes. No writer self-acceptance.

After design acceptance, implement one bounded offline slice at a time in an
isolated worktree: (1) strict V4 schema/frozen purpose plan and bounded budget
replay; (2) durable shared/session ledgers with crash matrix; (3) injected
transport/clock/resource state machine and receipt/report composition. Each slice
gets focused tests, different-model exact-commit review and newer-main
reconciliation before integration. Preserve existing V3 and legacy fixtures.
No concrete socket adapter, real clock recorder, live preflight, launchable
private manifest or service wiring belongs in those batches. Qualifying a real
transport and actual provider/storage/time evidence remains a later explicit
review gate; G3-L precedes any request and cannot be granted by a test fixture.

## 8. Completion evidence and current recovery

Synchronous documentary design; no executable or test changes, no full-suite
rerun or synthetic timing claims. Source inspection covered V3 schedule/network/
time checks, durable budget reserve/replay/complete, restriction restoration,
response validation, ClockSequence, store provenance/seal/recovery, and the
accepted integration/addendum/restart reviews. The companion
[author completion record](V11_R09_GATE3_TRANSPORT_RUNTIME_DESIGN_terminal.json)
binds this document and inspected source bytes; it is not independent acceptance.

Main recovered clean at `01dfd73`, 24 ahead/0 behind its local tracking ref.
Held author `e563e45` and SHADOW `15e99bd` are clean; no Gate 3 worker was active.
Only commissioning watchdog/manager statuses are newer; no forward sample.
Accepted release resolution `6ec371e` remains an ancestor and is not reopened.
Demo/scanner/controller are inactive/disabled, execution inactive/masked;
protected model-authority paths absent. Private master SHA-256 matches its pin.
Disk about 3.9 GiB free; memory about 860 MiB available. V10 untouched; AxiomTrade
observed only for host resources. GEFS forward work remains owner/root gated.
Publication remains held after the earlier automatic rejection; no retry here.
No C/J/E/A boundary crossed. **91/200 (45.5%), formal 1/50; NOT_READY_TO_FUND.**
