# Gate 3 V5 evidence-role amendment proposal — 2026-10-03

**PROPOSED_BLOCKED; documentation acceptance only; no executable V5 or authority.**
Sole author: Codex Astra/high. Baseline `40da1570e66d36f1f7f3e41e12b190a74cf0f9c1`,
tree `d6169d53a42d88636c9a031776cc3b3bc5a8f386`. The companion sources JSON
pins exact public Git bytes; the verification JSON separates performed checks
from future acceptance tests and maps every existing G3-L identity. No provider
or private evidence was acquired/read. The protected FINAL-REVIEWED master was
not accessed. **91/200, formal 1/50; 77 missing identities; G3-L NO-GO;
NOT_READY_TO_FUND.** No G3-E, SHADOW or C/J/E/A credit.

## 1. Decision and source boundary

Propose a new, incompatible V5 contract: mandatory evidence roles are separate
from actual network requests. Preserve V4's refusal and all existing consumers.
V4 requires 15 pairs but its production predicate allows only IFS/AIFS
FIELD/INDEX grammar (four pairs), not four qualified contracts [V4]. The
`1dad476` independent Sol/high PASS accepts this architectural direction only;
its direct-denominator/protocol pin recommendation is addressed here [REVIEW].
The merged export slice has seven input roles and always refuses accepted
exports with `VPE_EXTERNAL_TRUST_UNAVAILABLE` [EXPORT]. None of those facts
establishes provider rights, an authenticated channel or an executable V5.

This is a closed **amendment contract**, using exact source schemas by reference
rather than duplicating unchanged V4 groups. New parser/capability enrollment
and resource formulas remain explicitly blocked decisions (§7), not open JSON
extension points. Documentation review may accept the amendment for later
isolated implementation; only separate reviewed implementations, actual evidence
and existing operational gates could permit subsequent use. No auto-migration,
V4 fallback, monkeypatch, changed legacy pin or inherited V4 approval is allowed.

## 2. Closed bytes and preservation rules

Notation: `H` = lowercase SHA-256; `N` = exact integer 0..2^63−1 (never bool or
float); `T` = positive integer UTC seconds; `S` = nonempty UTF-8 string <=4,096
bytes without surrogates. IDs use `[A-Za-z0-9_-]{1,80}`. `Ref` is exactly
`{sha256:H,byte_length:N>0,media_type:S}`. Resolve only supplied, authorized,
content-addressed bytes; references, origins and paths never trigger I/O.
`h(x)` means SHA-256 of baseline `canonical(x)` [CANONICAL]: sorted keys,
compact separators, ASCII escaping, no NaN, UTF-8, no newline/BOM. Duplicate
keys, unknown/missing keys, coercion, noncanonical bytes and nonfinite numbers
refuse. Only inherited finite coordinate/uncertainty numbers may be floats;
retain exact numeric spelling, reject negative zero. Arrays are ordered; no
silent sorting/normalization. Every object below has exactly its listed keys.
`Named(X)` is `{id:H,body:X}` with `id=h(body)`; every X has its own schema
literal below. IDs must be unique in their collection. Hashes prove bytes only.

`ManifestV5` has exactly the V4 `GROUPS` plus `evidence_roles`, `events`,
`local_budget` [V4]. Apply V4's exact schemas and predicates except the following
exhaustive replacements. This specifies future V5 obligations, not a call to
`validate_manifest_v4` with a changed schema string.

| Group | Exact V5 delta; everything else retained |
| --- | --- |
| identity | `schema=R09_GATE3_LAUNCH_MANIFEST_V5`; production `mapping_scope=PROVIDER_REVIEW_REQUIRED`; all four self-authority flags remain false. Synthetic tests require a separate nonproduction entrypoint/domain and cannot emit this production schema. |
| protocol | Add `v5_amendment` with the exact five-key `reviewed_design` record shape, binding this later independently accepted amendment commit/tree/document/report/terminal. Preserve original/addendum/design pins and five historical reviews. No accepted values supplied here. |
| sources | Exact GEFS/IFS/AIFS map. Per-source remove only `origin`, `path_spec`, `purpose_mappings`; retain every other field/predicate, including dossier/release/licence/index/range/decoder/identity/control-domain refs, effective interval, publication attestation-or-absence, member/hour inventory. Bind transport via capabilities below. |
| network | Remove `path_specs`, replace `endpoints` by `capabilities:Named(Capability)[]`; preserve other fields/policies. `origins` is distinct origins in first scheduled-use order; `purposes` remains the five PURPOSES, with zero-count entries allowed; `index_binding=ETAG_IF_RANGE`. No Cartesian coverage predicate. |
| schedule | Keep all keys; replace request schema with Request below. `attempt_slots` is precisely the unique FIELD slot sequence. Recompute inventory/schedule hashes, reservations, time bound and purpose plan from complete ordered requests; retain observed-size/full-demand evidence, bounded feasibility and processing/finalization minima. |
| runtime | Same keys, exact purpose recount including zeros; `schedule_digest=h(schedule)`. Same resource ceilings, but `required_store_objects` and `local_storage_quota_bytes` must include the evidence graph/local work (§6); old network-only formula is insufficient. Production computation blocked pending a reviewed formula. |
| evidence_roles | Exactly `{schema,objects,indexes,proofs,bindings}`; schema `G3_V5_EVIDENCE_ROLES_1`; first three arrays use Named records below, bindings are FieldRoles records in FIELD order. |
| events | Complete EventIdentity sequence from export design §5 [EXPORT_DESIGN], same exact fields/types; event_id must equal requested_key[1]. Primary provider derives from separately authenticated event policy. All events include all scheduled FIELD IDs in order. |
| local_budget | Exactly `{unique_artifacts,hashed_bytes,retained_bytes,peak_memory_bytes,cpu_milliseconds,wall_milliseconds,graph_nodes,graph_edges,store_objects,store_events,report_bytes}`; all N, positive memory/CPU/wall/report bounds. Derived counts/bytes and worst-case reservations, never caller hints; §6 applies. |

The unchanged groups are code, storage, cohort, time, runs_and_slots, limits,
clocks_and_receipts and accounting. Retain network anonymity/DNS/TLS/denial
lineage, historical approvals and fresh through-use resource/clock checks.
Predicates reading removed mapping fields are replaced by §3, never skipped
selectively to coerce V4 acceptance. No inherited opaque Ref becomes semantic
proof by this incorporation.

`SLOTS={GEFS:(31,3),IFS:(51,3),AIFS:(51,6)}` and `SLOT_COUNT=2713` [SLOTS].
Enumerate provider order GEFS/IFS/AIFS, member ascending, hour 0..72 by cadence,
using the separately frozen run for each provider: 775+1,275+663=2,713 rows.
Every original row survives omission, failure, cancellation and replay. All
2,713 slots have all three role obligations (8,139 role cells), even when no
request is scheduled for that slot. An unattempted slot remains MISSING with
respect to this package's role bindings; it is never vacuously evidence-complete.
All-provider eligibility requires complete qualified native trajectories, not
merely three provider names in a successful subset. Retain
00Z, latest complete ready, age <=86,400 seconds, no fallback; one station/date,
1–2 ordered HIGH/LOW events, complete event×provider expansion and original
requested/trial keys. Retain preceding-date 14:00–17:00 UTC acquisition,
18:00 decision before target local-day start, pinned tzdata/local boundaries,
preregistration <= review < start, metadata receipt < start, feature-seal upper
<= decision lower, <=1-second uncertainty and expiry [PROTOCOL; V4]. New reviews
needed for use also finish before start. A later seal cannot backdate knowability.

## 3. Exact evidence and transport records

All schema strings in this section are literal. `P` is GEFS|IFS|AIFS, `Purpose`
is FIELD|INDEX|OBJECT_ID|METADATA|PROBE. Origin/path types and typed path grammar
retain [V4] syntax restrictions; production contracts must separately enroll
exact grammar, parser, response semantics, rights and domain applicability.
No callable, format string, wildcard URL or caller-selected parser is allowed.

| Type | Exact keys and values |
| --- | --- |
| Capability | `{schema:G3_V5_CAPABILITY_1,provider:P,purpose:Purpose,control_domain_id:H,origin:S,method:GET,path_spec:V4PathSpec,dossier:Ref,access_reference:Ref,response_contract:Ref,parser_identity:Ref}`. Externally enrolled exact body, including its semantics; matching a digest is insufficient. |
| Object | `{schema:G3_V5_OBJECT_1,provider:P,run_utc:T,origin:S,path:S,source_dossier:Ref,field_etag:S,object_bytes:N>0}`. Strong target ETag, never an index ETag; source interval/native product binding required. |
| Index | `{schema:G3_V5_INDEX_1,object_id:H,origin:S,path:S,raw:Ref,index_etag:S,parser_identity:Ref}`. Exact raw index bytes, its own strong ETag, official association to the target object/version; no inferred sibling for GEFS. |
| Scope | `{slot_indexes:N[],cohort_sha256:H,valid_from_utc:T,valid_until_utc:T}`. Nonempty unique ascending original slot indices; `cohort_sha256=h(cohort)`; from <= until. Resolve every slot to frozen provider/run/member/hour, never accept provider-only applicability. |
| Proof | `{schema:G3_V5_PROOF_1,role:Role,scope:Scope,claims:Claims,source_bytes:Ref,source_identity:Ref,original_observations:Ref,custody:Ref,rights:Ref,restriction_lineage:Ref,interpretation:Ref}`. Role is exactly INDEX_SELECTION|OBJECT_IDENTITY|COHORT_METADATA. A closed role-specific Claims union follows. Supporting bytes and the role-specific interpretation/build must be independently accepted; Ref syntax alone cannot satisfy a role. |
| INDEX_SELECTION Claims | `{object_id:H,index_id:H,selections:Selection[]}`; exactly one Selection for each scoped slot in scope order. |
| Selection | `{slot_index:N,range_start:N,range_end:N,selectors:Ref,expected_grib:Ref}`. End >= start, end < object_bytes, within retained V4 offset/field caps. Bind unique native selector, full message framing, parameter/level/member/run/hour/grid and independently expected section fields. |
| OBJECT_IDENTITY Claims | `{object_id:H,index_id:H,coherence:Ref}`. Authoritative target size/strong ETag and exact index-version coherence; self-hash, same path or index ETag alone fails. |
| COHORT_METADATA Claims | `{cohort_sha256:H,official_resolution:Ref}`. Exact cohort equality and official station/version/coordinates/timezone/tzdata/event/rule/settlement/unit/rounding/buckets/date/selection/key resolution. Weather release metadata cannot substitute. |
| RoleUse | `{proof_id:H,mode:SEALED_OFFLINE\|SCHEDULED_CONFIRMATION,request_id:ID\|null}`. Offline requires null; confirmation requires an earlier scheduled matching request. |
| FieldRoles | `{field_request_id:ID,INDEX_SELECTION:RoleUse,OBJECT_IDENTITY:RoleUse,COHORT_METADATA:RoleUse}`. Exactly one binding per FIELD, all three mandatory. |
| Request | V4 request's exact keys, replacing `endpoint_id` with `capability_id:H`, and adding `{expected_etag:S,expected_object_bytes:N\|null,expected_body:Ref\|null,dependency_proof_ids:H[]}`. `prerequisites` remains unique indices of earlier requests. |

Collections contain exactly referenced reachable records, ordered by first use
walking FIELD order, roles in the order shown, then proof→index→object; shared
records occur once. Capabilities contain exactly scheduled-use bodies, first-use
ordered. There is no obligation to populate unused pairs. No PROBE by default:
zero request count, zero reservation, no capability. A probe requires its own
accepted necessity/response contract and frozen row; it cannot satisfy a role
by relabeling. Every capability domain must equal its source control-domain
identity and be covered by the authenticated cross-origin restriction graph; dossier equals
the provider source dossier. No capability or unsupported semantic parser means
no dispatch.

For every FIELD: its provider/slot/run/path/source must equal Object, its index
must target that Object, and INDEX_SELECTION/OBJECT_IDENTITY must name the same
object/index. `object_id` and `index_id` are the Named IDs; `cache_id` is
`h({schema:G3_V5_CACHE_1,object_id,index_id})`. FIELD range equals Selection,
expected ETag/size equal Object, and expected_body is null (no invented forecast
hash). The tuple includes a shared file plus distinct member selection: shared
IFS/AIFS perturbed paths never imply shared ranges or duplicated members.
FIELD `dependency_proof_ids` equals the distinct three RoleUse proof IDs in role
order. Its prerequisites equal distinct confirmation request positions in
schedule order. Proof role and scope must cover that exact slot and whole cohort;
all scope slots must have matching binding uses. The whole uncertainty interval
for freeze and each intended/use-time operation must lie in the proof scope
interval, with the accepted freshness policy also passing; a valid label alone
is insufficient. Extra or dangling edges refuse.

Overhead precedes every FIELD, has null ranges, no prerequisites or dependency
proofs, and retains a representative attempted slot/provider with matching
object/index/cache. Its capability must render the exact request path. Shared
use across members is allowed only when every dependent slot resolves to the
same object/index identity and expected facts. A cross-provider metadata artifact
can be shared by separate explicit scope edges; no fake provider-owned metadata
endpoint follows. INDEX confirms raw index bytes/ETag; OBJECT_ID confirms target
identity through an independently contracted bounded GET-200 descriptor;
METADATA confirms official cohort bytes. Non-FIELD expected_body is a presealed
Ref, expected_etag is the response's own strong ETag; expected_object_bytes is
null. FIELD retains exact 206, If-Range, Content-Range total/length, identity
encoding and complete GRIB checks. Other requests retain bounded GET-200.

**All role facts must already be evidenced and sealed before freeze/review.**
SCHEDULED_CONFIRMATION is a real, charged earlier request whose validated receipt
must confirm those facts; its future receipt hash/clocks are not fabricated at
freeze. It cannot discover new ranges/objects/metadata. COHORT_METADATA must have
its original official receipt before acquisition even if reconfirmed in-window.
A failure, changed ETag/index/version/rule or unavailable proof blocks dependent
FIELDs; no switch to offline mode, refetch, new mirror or replan mid-window.
New facts require a separately prepared/reviewed package. Offline use creates no
HTTP status, request start, attempt debit or provider receipt. It does consume
local resources and requires genuine original source/receipt/custody evidence.

Role-specific supporting formats and interpretation rules are **not implemented
or qualified by the cited source**. This closed envelope intentionally refuses
unknown interpretation pins; an empty production interpretation/capability
registry is the current truth. §7 specifies evidence needed for enrollment.

## 4. Lineage, native semantics and independent trust

Proof scope is necessary, not sufficient: authenticated interpretation must
establish dataset/release/effective run, original publisher and retrieval/issue
records, lawful retention/use/redistribution constraints, original time bounds,
complete custody/seals, and validity for this cohort/run/window at each use.
No assumed maximum age or rights expiry is invented here: a missing reviewed
policy or supporting evidence refuses. Publication remains attested only with
independent exact-byte evidence; otherwise preserve null plus absence reason.
No file mtime, local seal, export time or successful HTTP status proves rights,
publisher authenticity, availability time or historical knowledge.

Retain 2 m instantaneous temperature in K, K−273.15 only, frozen native grid,
nearest extraction and <=50 km displacement, no interpolation/replication/
member omission. These are native trajectory predictors, not daily extrema.
IFS uses 3-hour steps; AIFS 6-hour. IFS `oper/fc` control and `enfo/ef` file with
`pf` member rows differ from AIFS `enfo/cf|pf`; normalize missing control number
only where the independently reviewed native parser permits [ECMWF]. The generic
ECMWFRequest currently enforces six-hour steps for IFS too; it cannot certify
full IFS coverage by itself. The separate G3-I NativeECMWFThreeHourRequest
already supports the required cadence [COLLECTOR]; qualify its exact native
index/decoder integration rather than duplicating or downsampling it. GEFS CGI filtered 200 syntax does not establish direct-object
range/S3/index rights or semantics [GEFS]. Source comments or old inspected
extrema cannot fill current-release/instantaneous-field gaps.

The same frozen restriction-domain graph and authenticated denial-history head
must cover offline evidence lineage and scheduled origins, including related
origins. Preserve original denials, 401/403, 429/503, Retry-After, unresolved
expiry/resumption, cumulative debit and unfinished intents across restart, run,
client and origin changes [BOOTSTRAP; LEDGERS]. Unknown lineage/expiry blocks;
local files do not clear historical ECMWF 503/429 holds. No live restriction
state was inspected here. No cookies/netrc/proxy credentials, signed URLs,
redirects, retries, alternate-origin evasion or automatic label requests.

Separate validation producer, independent different-model reviewer, and
independently authenticated pin/custody custodian. Caller-supplied hashes,
Python interfaces, Git author strings, booleans or an untrusted signing key
cannot authenticate any of them. A later channel decision must bind exact
amendment/build/dependency closure, complete input bundle, manifest/projection,
role proofs/interpretations, capability bodies, all seven export input roles
(including event_policy_review), report and completed terminal, scope, effective
interval and anti-rollback/revocation checkpoint. The channel's issuer/verifier,
key enrollment, separation, freshness and revocation mechanism need independent
review under existing authority; none is selected or installed here.

No hash cycle: freeze source proofs and their detached accepted reviews first;
produce the immutable manifest/projection; independently review those exact
bytes; independently retain/pin that decision. Later acceptance references
previous artifacts, never its own hash. Each role/capability pin is covered by
that external decision, not an embedded self-approval. G3-L final report and
terminal remain later separate outputs; PRE_REVIEW cannot fill them with itself.
Absent that channel, every accepted-production/export claim must refuse.

## 5. Exact projections and cross-component obligations

Define `ProjectionV5` as exactly `{schema:G3_V5_PROJECTION_1,manifest:Ref,
window,requests,events,evidence_roles,capabilities,purpose_plan,local_budget,
terminal_precedence,runtime_context:Ref}`. `window` has the exact six-field
AbsoluteWindow projection [RUNTIME]; requests/events/evidence_roles/capabilities/
local_budget are **entire ordered records** above, copied without loss; purpose
plan is the five-key recount; precedence is the seven-category frozen sequence.
Projection hash is h(ProjectionV5), separate from all V4 plan hashes. The actual
context bytes use export design §4.1's exact schema, with manifest_runtime_sha256
bound to V5 runtime. Bind their immutable Ref plus current authorized environment
identity; stored validation time is not current freshness. Supplied dictionaries
or a frozen dataclass cannot be a validated-plan capability. Derive source/decoder
pins from validated sources, parser/domain pins from the referenced capability,
and clock policy from validated runtime; refuse caller overrides. Runtime must
retain the original provider/object/index/cache identities even for overhead.

The seven-role input set stays exactly `manifest,plan_review,supplemental_pins,
runtime_context,terminal_precedence,event_policy,event_policy_review`.
`PlanReviewV5` is the exact eight-key plan-review v2 schema [EXPORT_DESIGN §4.1]
with `schema_version=3` plus `evidence_roles_sha256`, `capabilities_sha256`,
`local_budget_sha256`; these three hashes cover the complete corresponding
projection values. Existing window/request/event hashes now cover the V5
window, complete Request array and complete EventIdentity array respectively.
Supplemental pins keep the exact three-key-per-request schema; ETag/size must
equal Request. Dependency commit hashes name authenticated, already durable
pre-freeze proof/custody dependencies, never future confirmation receipts;
their interpreted closure must cover every dependency_proof_id. Count the
combined prerequisites plus external dependency hashes against 256 without
allowing an alias to hide an edge. The input bundle hash is
`h({schema:G3_V5_EXPORT_INPUTS_1,refs:{...the seven exact role-to-Ref entries...}})`. Event policy/review keep their exact source shapes
with schema literals `G3_V5_EVENT_POLICY_1` / `G3_V5_EVENT_POLICY_REVIEW_1` and
bind V5 manifest bytes, exact producer and independent reviewer.

Future `ExportV5` keeps the exact nine top-level keys of export design §4.2:
schema becomes `G3_V5_EXPORT_1`; scope/flags retain OFFLINE_RESOURCE_ESTIMATE_ONLY
and every false/no-credit value; provenance/input_refs retain their exact
shapes; manifest_projection keeps its exact source shape except `endpoints`
becomes `capabilities` and adds `evidence_roles,local_budget`; copied groups
follow this amendment. plan_projection is ProjectionV5 and plan_sha256 its hash;
event_identities equals the entire events array. Custody, acceptance and trusted
pin value views keep the exact closed shapes in export design §4.3 (the custody,
acceptance and trusted-pin records), with literals `G3_V5_EXPORT_CUSTODY_1`,
`G3_V5_EXPORT_REVIEW_1`, `G3_V5_EXPORT_TRUSTED_PINS_1`; all digest/length/build/
review/time equalities and external authentication obligations remain. These
shapes are a proposed replacement interface, not accepted records. The external
channel must additionally bind its completed terminal and revocation checkpoint;
those are external trusted evidence, not candidate-selectable new JSON keys.

The report's evidence-role projection is exactly `RoleCoverageV5={schema:
G3_V5_ROLE_COVERAGE_1,manifest_sha256:H,projection_sha256:H,rows:CoverageRow[]}`.
Rows are exactly 2,713 in slot order; CoverageRow is `{slot_index:N,
field_request_id:ID|null,INDEX_SELECTION:RoleState,OBJECT_IDENTITY:RoleState,
COHORT_METADATA:RoleState}`; RoleState is `{proof_id:H|null,state:MISSING|
FROZEN|SATISFIED|REFUSED}`. Unscheduled rows have null field/proof IDs and MISSING
states. Scheduled IDs/proofs must equal frozen bindings: absent proof refuses
validation, FROZEN means only frozen facts pending use, SATISFIED requires
successful independent through-use verification (and confirmation receipt where
scheduled), REFUSED preserves the failed dependency in the terminal report.
Recompute these states from authenticated durable evidence, never caller labels.
This projection adds no feature eligibility and cannot replace the existing
full terminal accounting/reason partition; future report integration binds both.

| Boundary / existing source | Required future obligation; present behavior unchanged |
| --- | --- |
| V5 validator → projector [V4; RUNTIME] | Resolve/hash bounded snapshots, authenticate enrollment/reviews, validate every retained predicate and new edge, derive full projection once, recompute after freeze. Reject V4/V5 mixing, races and dirty/changed loaded build. Unsupported interpretation refuses before runtime. |
| Plan/export → estimator [EXPORT_DESIGN; EXPORT; BUDGET] | New versioned seam only after complete validation and before runtime construction; retain all seven input roles with new manifest/plan-review domains and exact V5 projections. Do not feed V5 into the V4 slice. Complete events/field lists, roles, objects/indexes, local work and both conservative capacity calculations survive export. No accepted estimate while trust/formula/hook is missing. |
| Admission/A8 → runtime [A8] | Separate exact reviewed bridge required; existing PreparedComposition has no dispatch method and runtime does not consume it as admission. Require exact V5/package/build/evidence/context and current A2–A8/G3-L checks at entry and through use; never infer authorization from construction or export acceptance. |
| Runtime → transport/ledger [RUNTIME; LEDGERS] | Only next scheduled request/capability; recheck pins, scope, expiry, clocks, resources, denial head and all prerequisite proof/receipt seals immediately before dispatch. Charge every real attempt/body including failures/eager bytes. No debit for an artifact read; local budget is separate. Prior receipt projection must equal frozen expected facts. |
| Store → decoder/restart [STORE; A7] | Preserve original proof and receipt bytes/observations; content plus applicability edges, typed commits, distinct roots and bounded fanout. Verify dependencies before decode and after reopen, retain uncertainty/holds on torn writes or unfinished intents, no retry/reset. A7 independently expected section/build pins and qualified bounded native decode remain necessary. |
| Replay → report/G3-E [RUNTIME; PROTOCOL] | Re-derive projection, graph closure, raw 2,713-row partition and all-reason precedence from durable bytes and original observations. Report every event×provider and separate RAW success, full native trajectory, causal feature eligibility and labels. Existing complete_event_ids is scheduled-RAW only, never eligibility. Include the exact RoleCoverageV5 projection for every original slot. |
| G3-L [G3L; AUDIT] | Keep every identity and stage/scope. Companion row map preserves each literal obligation, including V4-named legacy rows; new V5 manifest/review identities require explicit independent equivalence adjudication, not string replacement or credit. 77 missing remains 77. |

Preserve separately accepted A1 observer evidence as well. A2 authenticated
source/build lineage (including RECORD discrepancies), A3
complete reproducible dependency lock, A4 verification before execution and
through use, A5 provider/release/access/native semantics, A6 selected-run
index/object/range and independent section pins with causal clocks, A7 bounded
qualified native decode and A8 concrete composition are all retained
[ACCEPTANCE; A2A3; A4; A5A6; A7; A8]. Storage/restart and clock duties remain
additional obligations, not substitutes for those criteria.
Their versioned integration reviews must bind V5 projections and use-time checks.
An implementation PASS or synthetic replay does not qualify any of these.

Replay refuses rather than accepting partial projection on missing artifact,
wrong length/hash/type, unsupported version, stale external checkpoint, broken
custody, cyclic/dangling graph, unknown scope, conflicting receipt, alias/race,
changed restriction history or unfinished intent. Preserve original evidence
and bounded diagnostic refusal. Fixed proposed refusal stages (first failure,
field/array order within stage): BOUNDS, CANONICAL, SCHEMA, TRUST, SOURCE_PIN,
APPLICABILITY, RIGHTS_LINEAGE, PROJECTION, BUDGET, CUSTODY, CLOCK, DEPENDENCY.
All applicable terminal reasons still survive; refusal cannot erase a denial
or already received bytes. No accepted artifact or authority on failure.

## 6. Finite resource profile

Use stricter inherited caps; this proposal increases none. <=3,600 real requests,
<=1 GiB received, <=10,800 seconds including local processing/finalization,
one in flight, >=2 seconds between starts, <=30 seconds/request; INDEX <=3 MiB,
GEFS FIELD <=2 MiB, IFS/AIFS FIELD <=4 MiB, other purposes <=4 MiB; <=32 chunks,
4 KiB headers, >=16 MiB reserved report, >=2 GiB free disk / >=512 MiB available
memory **after** reservations, plus any stricter existing gate/A7 bounds.
Each request reserves its retained full purpose cap; savings cannot move across
purpose/provider/slot. Retain journal caps, 4,096 store objects, 10,000 store
events and 256 combined receipt prerequisites/external dependencies [V4].

Additional proposed processing ceilings: manifest and projection <=32 MiB each;
seven input buffers <=40 MiB total with individual [EXPORT_DESIGN §7] caps;
consumer <=33 MiB; depth <=16, <=500,000 JSON nodes/artifact, <=64 object keys
(except supplemental ID map <=3,600), arrays <=3,600, strings <=4,096 decoded /
24,576 encoded bytes. Exactly 2,713 slots, <=2,713 FIELDs/bindings, 1–2 events,
<=5,426 event links. Proofs/objects/indexes individually <=3,600 records, but
**combined unique referenced artifacts <=4,096**, <=1 GiB each, <=4 GiB total
hashed bytes, <=1 MiB streaming buffer, no compression/recursive opaque expansion.
<=16,384 graph edges, <=8,192 external dependency edges, <=256 combined direct
edges per request/receipt; store cap includes aggregation nodes, imports and
temporary copies. Deduplicate exact bytes only; applicability edges still count.
Numbers and sums/products checked <=2^63−1 before allocation. Read exact length
plus bounded EOF check, reject symlinks/FIFOs/path escape/aliasing, enforce
cancellation before parsing/reading/allocation and keep failures bounded.

Local wall reservation must fit schedule.processing_seconds (<=10,800); local
CPU reservation cannot exceed that wall reservation for one sequential worker.
Peak memory, retained/imported bytes, evidence parsing, two simultaneous byte
snapshots where needed, journal/closure/recovery records, event aggregation,
report/emergency persistence and A7 child allowance must be explicitly costed.
Compute both [RUNTIME] and [V4] capacity estimates, add all new work once to each,
and require both within qualified capacity; never pick the smaller. A reviewed
cost model must prove exact worst cases, including short chunks and restarts.
Until supplied, refuse BUDGET even if the network-only plan fits. These maxima
are refusal ceilings, not reservations, measured feasibility or permission to
allocate 4 GiB. Do not truncate evidence or reduce the denominator to fit.

## 7. Decisions/evidence still required; finite verification

| Hold | Exact evidence/decision needed before dependent acceptance |
| --- | --- |
| H1 protocol | Different-model exact commit/tree PASS on this amendment and explicit versioned preservation/equivalence decision, including all G3-L identities and historical V4-named obligations. No review done by this author. |
| H2 provider contracts | Lawfully retained official origin/path/range/index/response/rights/release evidence and exact parser/response interpretation review. GEFS direct object/index is unsupported; IFS/AIFS grammar is not rights/release/identity proof; qualified full three-hour IFS index/decoder integration remains required despite the existing native request type. No descriptor/metadata/probe endpoint invented. |
| H3 role proofs | Authentic pre-freeze native index selections, target strong ETag/size/coherence, official cohort metadata with original receipt, applicability/freshness policy, rights and independently retained custody. All three roles for every attempted FIELD; evidence needed even with zero overhead requests. |
| H4 controls/trust | Authenticated external issuer/verifier/pin/custody/revocation policy and actual independently retained records; lawful restriction-domain reconciliation including unresolved ECMWF history. A self-rehashed review/terminal cannot close it. |
| H5 implementation | Separately reviewed pure V5 validator/projection, supported interpretation registry, export/plan/runtime/store/replay/report/A8 bridge and bounded local cost model. Legacy consumers must continue refusing V5. No production registry enrollment by code alone. |
| H6 operational evidence | Existing independently qualified A2–A8, fresh clock/resource/custody, actual complete dated G3-L packet/review, then authorized capture and G3-E actual-byte replay. Offline tests supply none. |

Removing compulsory network pairs does not resolve the evidence-acquisition
bootstrap. If required pre-freeze facts cannot be obtained through already
lawful authorized offline intake, retain MISSING and seek a separate concrete
protocol/owner decision for the exact bounded acquisition, if any. Neither this
proposal nor an old dated preflight grants a new request, rolled date, HEAD,
listing, mirror or another actor/machine route around the same hold.

The companion verification matrix is finite (V01–V16), with positive synthetic
controls, adversarial mutations, expected refusal and owning boundary. They are
**future acceptance tests**, not executed V5 tests. Require deterministic normal
and optimized-mode replay, boundary/+1 resource tests, and no-network/protected
path tripwires. Any absent source/decision above keeps dependent tests and use
blocked; do not fill fixtures with invented production assertions.

Actual checks in this candidate: public source/hash/tree bindings, exact prior
review hash, direct denominator arithmetic, complete G3-L preservation-map
coverage, JSON parse/closure, unchanged baseline files, diff whitespace and
selected existing offline tests (commands/results in companion). No private
validator invocation or production transport. No new read-only verification
script or code edits were necessary. All three pre-existing status documents
and newer merged work remain byte-identical. Candidate needs a different-model
exact review before integration; no merge or self-approval is authorized here.
