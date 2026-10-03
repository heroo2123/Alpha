# Gate 3 validated-plan export and provenance handoff — 2026-10-03

Status: **UNREVIEWED DESIGN; NO PRODUCTION BINDING.** Sole-author, public-source,
SAFE NONFINANCIAL investigation. Requires a later authorized different-model
review of the exact document commit/tree; this document is not that review.

Baseline: `b2129df92961b8916e970fed46d0f3fac2bf700c`, tree
`96ad1c376033d5a7d49e6d4cdc259149cca43fe6`. Work is confined to the isolated
validated-plan-export design worktree. No production validation, provider call,
private evidence, credentials, service, protected authority root, V10, Axiom,
account, order, funding or execution surface was used. The separate held
`V11_GATE3_FROZEN_EVENT_BINDING_DESIGN_20261003.md` was not needed or read; its
UNREVIEWED hypothesis is neither adopted nor treated as policy.

## 1. Finding and stopping boundary

There is **no existing authenticated immutable validated-plan export hook in the
inspected tracked Gate 3 contracts**. `FrozenPlan.sha256` supplies a reproducible
content digest, and `from_validated_manifest` constructs an ordered projection;
neither exports a provenance receipt nor authenticates the caller's expected
hash. A8 explicitly requires external authentication of review authority. No
trust root for that authentication or for this proposed export handoff is
established by these modules. Do not interpret this scoped finding as an audit
of unrelated authority infrastructure; none was inspected.

Moreover, a real V4 three-provider manifest cannot pass at this baseline:
`validate_manifest_v4` requires GEFS/IFS/AIFS and all five purpose mappings, but
its non-synthetic branch permits only IFS/AIFS FIELD/INDEX. GEFS, OBJECT_ID,
METADATA and PROBE fail `SOURCE_MAPPING_UNSUPPORTED` [V4:387–440,558–560]. The
A8 module documents this hold itself [A8:1–6]. Synthetic V4 validation is not a
way around it. `synthetic_fixture=False` on a Python plan alone does not prove
`mapping_scope=PROVIDER_REVIEW_REQUIRED`; A8 checks that separately.

Consequently this change stops at a design. It creates no exporter, consumer,
accepted export, signing key, production hook, launch package or authority.
Sections 3–9 specify **proposed future contracts**, requiring separate
implementation and exact review. If either the independently authenticated
pin channel or reviewed production validator/hook remains absent, the proposed
consumer must refuse a validated-production claim. A conditional synthetic
resource calculation may remain an explicitly unverified proposal through a
separate interface; it must never manufacture this export type.

## 2. What tracked source proves, conditionally on its inputs

Anchors below refer to the immutable baseline files in section 10, not to the
current line numbers of a future implementation. A source check is not evidence
that it was executed on production inputs.

| Contract | Enforced behavior | Limit of the inference |
| --- | --- | --- |
| `canonical`, `parse_canonical` | Sorted keys, compact separators, ASCII escaping, no NaN; duplicate-key rejection, UTF-8 decoding, byte-for-byte reserialization comparison; 32 MiB parser input ceiling [C:47–49; L:85–106]. | Not RFC 8785/JCS. No parser-level depth/node/string bound. Integer `1` and float `1.0` have different valid bytes. Schema checks remain necessary. |
| `validate_manifest_v4` | Closed groups, false self-authority flags, Git commit/tree/source/worktree checks, pinned historical protocol/design references, artifact digest/length resolution [V4:217–292,837–838; L:117–203]. | Returns SHA-256, not a validation object or signed receipt. Most referenced dossiers/reviews are opaque byte references; matching hashes do not establish truth, reviewer identity, provider rights or acceptance. Historical design pins do not approve a new export. |
| Cohort and schedule | One station/date, 1–2 distinct ordered HIGH/LOW sides, five-element requested keys and three-element trial mapping; exact 2,713 ordered slots; endpoint/object/index/cache binding; prior overhead and exact attempt partition; recount of all five purpose budgets [V4:320–355,457–506,592–720,767–782]. | Requested-key event/rule strings are not semantically resolved against official rule/metadata bytes here. A bounded attempt subset is permitted; schedule completeness does not mean complete provider trajectories. |
| `FrozenEvent`, `FrozenPlan.__post_init__` | Exact request types, unique request IDs/FIELD slots, bounded event/link counts, referenced FIELD existence, primary provider present, closed review checksum schema and prior request order [R:446–575]. | `FrozenEvent` does not validate requested/trial key types itself. Python frozen dataclasses are not a security boundary against `object.__setattr__`, forged construction or mutable nested values. No reviewer identity/status is in the plan review record. |
| `verify_validated_projection`, factory | Revalidates V4, compares window and every ordered request projection, source/decoder/parser/clock pins, terminal-precedence artifact, event side sequence, keys and full scheduled FIELD list [R:589–736]. | Events and supplemental ETag/size/dependency pins originate with caller inputs. Event `event_id` is not equated to requested-key event ID; primary provider is not derived from V4. Review hashes bind these choices but do not authenticate them. Revalidation uses stored `validation_now_utc`, not a new clock observation. |
| Plan digest/review | Plan hash covers manifest/review hashes, window, ordered requests/events, fixture flag, precedence and context hash; review v2 covers window/schedules/supplemental pins/precedence/context [R:538–587]. | It omits validation repository/root/time as provenance. A caller who controls content and expected hashes can consistently replace both. Digest recomputation does not prove a prior successful call to the factory. |
| A8 | Checks externally supplied review hash, exact package/manifest/inventory/plan/context, typed accepted prerequisite/component reviews, G3-L terminal and HEAD/tree; revalidates V4 at supplied current time; checks loaded methods, fresh clock/resource/store context; repeats at use [A8:89–181,205–461]. | Its docstring requires separate reviewer authentication. Distinct evidence/review hashes are not proof of independent humans/models. `PreparedComposition` has no run/dispatch method. Runtime does not require a `PreparedComposition` argument; there is no automatic A8-to-runtime admission bridge. |
| `GateRuntime` | Rechecks plan hash/projection, budget/store/root/boot context, retained denial head, request order, prospective capacity, dependencies, clocks and control-domain holds [R:1003–1182,1184–1202,1485–1504]. | Injected interfaces and caller hashes do not authenticate approval. Runtime construction is not an export seam: it binds a journal context and samples resources [R:1150–1176]. Do not instantiate it merely to obtain an offline estimate. |
| Terminal report | Partitions 2,713 rows, reconciles durable attempts/receipts and purpose accounting; computes event completion from scheduled FIELD outcomes and provider presence [R:1814–2037]. | `SUCCESS` includes late diagnostic RAW captures [R:1734–1781]. `complete_event_ids` is a scheduled-RAW predicate, not full native-trajectory, causal feature or G3-E eligibility. `decode_complete` sampling alone is not qualified feature decoding. |

The public local resource-budget probe reports that removing caller-supplied
events reduced an estimate while preserving proposal flags [B:1–9]. That is
context from a tracked report about a held candidate, not a reproduced result
against this baseline and not an accepted review. The replacement design must
bind both the entire schedule and the entire event collection: checking request
triples, checking only each present event, or comparing a hash supplied beside
its own bytes does not solve omission or caller substitution.

## 3. Trust model, custody and timing (proposed)

There are three distinct principals: a separately reviewed validation producer,
an independently authenticated review/custody channel, and a pure downstream
resource estimator. The untrusted caller may control all candidate bytes,
filenames, dictionaries and hashes submitted with them. It may replay an old
export, reorder arrays, delete events, change provider identity, replace source
files, or present a fabricated accepted review. It must not control the trusted
pin channel or the isolated producer's loaded code/state. Compromise of those
principals is outside a hash-only guarantee; refuse if their independence cannot
be established. No same-process Python object is an unforgeable capability.

The required trust root is an **external authenticated exact-pin decision**
covering the producer build, input bundle, export and review. Its custodian,
authentication mechanism, revocation/staleness policy and integration hook are
unresolved prerequisites, not new powers granted here. Do not install a key,
use a protected root, infer trust from a Git author name, accept a candidate's
own `trusted=true`, or let a caller choose both a pinset and the pinset's expected
hash. A signature also needs an independently trusted verifier/key policy.

Proposed one-way sequence:

1. In a later separately authorized environment, freeze bounded canonical input
   bytes: manifest, plan review v2, supplemental pins, runtime context,
   precedence and event policy. Independently authenticate their exact pins and
   reviewed producer build. No evidence acquisition occurs inside the exporter.
2. Resolve those exact artifacts under the existing authorized private validation
   boundary, check current expiry/time and retained restrictions, and call
   `validate_manifest_v4` in production mapping scope. At the present baseline
   this step refuses. Never monkeypatch this refusal for a production export.
3. Derive complete event records and request projection from the frozen inputs;
   construct via `FrozenPlan.from_validated_manifest`. Immediately rerun full
   constructor invariants and `verify_validated_projection` against the same
   immutable snapshots. Use a newly bounded validation-time input, not the old
   stored time as a freshness claim. Verify the actual producer modules and
   dependency build against independently accepted pins; selected adapter
   method checks alone do not cover all transitive code/globals.
4. **The future export seam belongs here: after successful full production
   validation/projection and before runtime construction or estimator use.**
   Snapshot canonical value bytes under exclusive producer custody. Recompute
   every derived projection and digest from that snapshot; compare the original
   and final input digests. On races, incomplete validation or changed bytes,
   publish nothing. Do not accept a ready-made `FrozenPlan`, caller event list or
   a free-form exported dictionary at this seam as proof of validation.
5. Seal the bounded export bytes with create-only publication, hash/length,
   fsync and a detached custody receipt. Failed/truncated writes remain
   non-accepted. Preserve original bytes and records; never overwrite a prior
   accepted digest or issue a mutable `latest` reference. Byte identity is
   sufficient for idempotent rereading; changed content requires a new review.
6. An independent reviewer examines the exact export and producing build/inputs,
   then the external channel pins a separate acceptance record and custody
   receipt. Review follows export, so neither export nor its existing plan
   review contains the hash of that later acceptance: there is no hash cycle.
7. Consumer receives only immutable bytes plus externally resolved trusted pins.
   It checks all bounds, canonical bytes, acceptance/custody and semantic equality
   before calculation. Its output references the export; it cannot change,
   refill or mint a plan, grant a provider purpose, or write back to the producer.

A8 can subsequently bind the same plan hash in its independently reviewed
package. Export acceptance is only acceptance for offline resource analysis;
it does not preempt A8/G3-L or require a fake A8 success to break a preparation
cycle. A later production-use bridge must separately require current A8 recheck,
G3-L, original context and restrictions, and existing runtime checks. G3-E
requires actual-byte replay and original causal evidence after capture. Export
validation time, review completion time, serialization time and file mtime are
never substituted for provider release, receipt, decode, seal or runtime-use
clocks. Re-exporting never refreshes the original evidence.

## 4. Closed schemas and canonical bytes (proposed)

All object key sets below are exact: missing/unknown fields fail, with no
extension bag, ignored field, implicit default or coercion. Arrays are ordered.
Only explicit nullable fields allow null. `H` is lowercase 64-hex SHA-256;
`N` is an exact nonnegative integer at most `2^63-1` (bool is not an integer).
`Ref` is exactly `{sha256:H, byte_length:N, media_type:string}` with positive
bounded length. Digest-named references never permit arbitrary paths or URLs
as resolver instructions. Original URLs are inert identity data, never fetched.

Use precisely baseline `canonical` [C:47–49]: Python JSON sorted keys,
`separators=(',', ':')`, `ensure_ascii=True`, `allow_nan=False`, UTF-8 bytes,
no BOM/newline. Pin encoder/runtime build in provenance; do not silently switch
to readiness `_canonical` (which uses `ensure_ascii=False` [Q:233–235]) or JCS.
The hash is SHA-256 of exact canonical bytes, with no embedded self-hash.
A schema literal supplies domain separation for each new artifact. Existing
plan/review hashes retain their existing formulas and names.

New scalar fields are integer/string/bool/null only, except verbatim copied
V4/plan numerical fields (coordinates, uncertainty/window values) that retain
their exact validated numeric types and canonical spelling. Reject nonfinite
values and negative zero; never convert `1.0` to `1`, round time or normalize
Unicode. Bound float tokens to 32 ASCII bytes and numbers to finite binary64;
new integers have at most 19 decimal digits and are range checked. A source
value outside the export profile is refused rather than silently repaired.

### 4.1 Producer input set

The boundary accepts immutable byte buffers for exactly these seven roles,
with a separately authenticated expected `Ref` for each:

| Role | Closed content |
| --- | --- |
| `manifest` | V4 exact schema, including every nested group as checked by baseline `validate_manifest_v4`; additionally subject to section 7 limits. Production mapping scope only. |
| `plan_review` | Existing v2 record with exactly `schema_version`, `manifest_sha256`, `window_sha256`, `request_schedule_sha256`, `event_schedule_sha256`, `supplemental_pins_sha256`, `terminal_precedence_sha256`, `runtime_context_sha256` [R:538–565]. |
| `supplemental_pins` | Object keyed by exactly the manifest request IDs; each value exactly `expected_etag`, `expected_object_bytes`, `dependency_commit_hashes` [R:704–714]. No additional source/parser/clock overrides. |
| `runtime_context` | Exactly `manifest_runtime_sha256`, `boot_id`, `shared_root`, `session_root`, `budget_root`, `store_descriptor`, `report_root`, `store_policy`, `clock_method`, `allowed_peer_ips` [R:648–662]. Further exact types required: hashes for the digest fields, bounded nonempty strings for boot/method, four two-integer directory identities, unique literal IP strings. No hostname lookup. |
| `terminal_precedence` | Seven-element unique array, identical to the resolved manifest artifact; precisely the categories accepted at [R:498–503], in its reviewed order. |
| `event_policy` | Exactly `{schema, manifest_sha256, entries}`; schema `ALPHA_V11_GATE3_EVENT_EXPORT_POLICY_V1`; entries in cohort order, each exactly `{requested_key, primary_provider}`. Keys equal V4 requested keys; provider in GEFS/IFS/AIFS and present among scheduled fields. This fills a missing V4 primary-provider policy; it must have separate external review, never a default chosen by the estimator. |
| `event_policy_review` | Exactly `{schema, status, event_policy_sha256, manifest_sha256, producer_commit_oid, producer_tree_oid, reviewer_id, reviewer_model, completed_utc}`; schema `ALPHA_V11_GATE3_EVENT_EXPORT_POLICY_REVIEW_V1`, status `ACCEPTED`. OIDs must resolve to the accepted exact producer commit/tree, not merely match a regex. Review identity is authenticated externally. |

Producer environment handles (approved repository and object-store descriptors,
verified code identity and validation-time evidence) are external capabilities,
not candidate-selectable JSON paths. The manifest's existing private root still
must match its validation root. No current task authorization to access such
roots is implied. The producer input bundle digest is the canonical hash of
`{schema:"ALPHA_V11_GATE3_PLAN_EXPORT_INPUTS_V1", refs:{...seven exact roles...}}`.
References to original private artifacts stay within their original access
boundary; this document contains no exported private values.

### 4.2 Export body

Exactly the following top-level keys:

| Key | Type and required value |
| --- | --- |
| `schema` | `ALPHA_V11_GATE3_VALIDATED_PLAN_EXPORT_V1` |
| `scope` | `OFFLINE_RESOURCE_ESTIMATE_ONLY` |
| `provenance` | Record defined below |
| `input_refs` | Exact seven-role `Ref` map from 4.1 |
| `manifest_projection` | Record defined below |
| `plan_projection` | Exact content body hashed by baseline `FrozenPlan.sha256` |
| `plan_sha256` | H; recomputed from `plan_projection`, equals externally accepted value |
| `event_identities` | Complete ordered event records defined in section 5 |
| `flags` | Fixed record defined below |

`provenance` is exactly `{producer_commit_oid, producer_tree_oid,
producer_build_sha256, validator_source_sha256, exporter_source_sha256,
input_bundle_sha256, validation_utc, validation_clock_ref}`. OID format must
match the accepted repository object format and resolve commit to tree;
source hashes refer to actual loaded reviewed code, and build hash binds its
closed dependency inventory. `validation_utc` is a positive integer second;
`validation_clock_ref` is a bounded `Ref` to the original evidence used by the
producer, not an assertion of clock qualification. A caller-supplied timestamp
without authenticated provenance is insufficient.

`manifest_projection` is exactly `{cohort, time, runs_and_slots, schedule,
limits, accounting, runtime, endpoints, source_pins, restriction_lineage}`. First seven values are
verbatim copies of those V4 groups, with the exact nested schemas in [V4].
`endpoints` is the full V4 endpoint array, including all 15 provider/purpose
combinations. `source_pins` is exactly a GEFS/IFS/AIFS map, each value exactly
`{dossier_sha256, decoder_build_sha256}`, derived from manifest sources. `restriction_lineage` is the exact `Ref` from
`manifest.network.restriction_lineage`. The producer proves equality to the whole manifest bytes identified by
`input_refs.manifest`; the consumer cannot establish that original validation
solely from this projection. The external provenance chain supplies that link.

`plan_projection` is exactly `{manifest, review, window, requests, events,
synthetic_fixture, terminal_precedence, runtime_context_sha256}` [R:579–587].
Here `manifest` and `review` equal the corresponding input-ref SHA-256 values, fixture is
strictly false and context hash is non-null. `window` has exactly
`start_utc`, `acquisition_end_utc`, `decision_lower_utc`,
`request_deadline_seconds`, `elapsed_cap_seconds`, `uncertainty_cap_seconds`.
Each request has exactly the 19 `AttemptRequest` fields at [R:372–390]:
`request_id`, `purpose`, `endpoint_id`, `control_domain_id`, `origin`, `path`,
`provider`, `slot_index`, `range_start`, `range_end`, `reservation_bytes`,
`expected_etag`, `expected_object_bytes`, `source_pin`, `decoder_pin`,
`clock_policy_sha256`, `validator_sha256`, `dependency_commit_hashes`,
`prerequisite_request_ids`.
Arrays replace tuples only by baseline canonical serialization. Requests must
pass the original constructor constraints plus section 7. FIELD provider is
non-null; overhead runtime provider is null while its V4 schedule/endpoint
provider remains present. All production slot indices are non-null. ETag and
external dependencies come only from the reviewed supplemental pins. Nullable
object size means unknown, never zero bytes or no reservation.

Each plan event has exactly `{event_id, side, primary_provider,
field_request_ids, requested_key, gate2_trial_key}`. The new profile derives
`event_id = requested_key[1]`; there is no caller alias field. If an original
key cannot fit the runtime ID syntax or produces duplicate event IDs, refuse
`VPE_EVENT_IDENTITY`; changing this runtime convention requires a separately
reviewed version. Do not silently hash/truncate the official identifier.

`flags` is exactly `{execution_authority:false, launch_authority:false,
financial_authority:false, promotion_authority:false, host_approved:false,
resource_qualification:false, provider_rights:false, clock_qualification:false,
capture_eligibility:false, feature_eligibility:false, label_eligibility:false,
learner_admission:false, shadow_admission:false, g3l:"NO_GO", g3e:"GATED",
qualification_credit:0}`. Export acceptance never flips any of them.

### 4.3 Detached records and consumer outputs

`custody_receipt` is exactly `{schema, export_sha256, export_byte_length,
input_bundle_sha256, producer_build_sha256, custody_id, sealed_utc}` with schema
`ALPHA_V11_GATE3_PLAN_EXPORT_CUSTODY_V1`. It is a statement whose authentication
must come from the external channel; the file alone proves no custody. The
custody ID is a bounded opaque identifier, not a filesystem path.

`acceptance` is exactly `{schema, status, scope, export_sha256,
export_byte_length, custody_receipt_sha256, input_bundle_sha256,
producer_commit_oid, producer_tree_oid, producer_build_sha256,
reviewer_id, reviewer_model, completed_utc, valid_until_utc}`. Schema is
`ALPHA_V11_GATE3_PLAN_EXPORT_REVIEW_V1`; status `ACCEPTED`; scope
`OFFLINE_RESOURCE_ESTIMATE_ONLY`. Require export/input/build/commit/tree exact
binding, authenticated reviewer identity and different-model independence from
the producing author, completed terminal and no unresolved findings. Completion
must follow the original seal; validity and manifest expiry must exceed the
trusted evaluation time. Internal time inequalities do not authenticate clocks.

The consumer's input arguments are exactly `export_raw`, `custody_raw`,
`acceptance_raw` (exact bytes), and an externally supplied `TrustedPinSet`.
The latter is not constructed from any of those bytes. Its closed value view is
`{schema, export_ref, custody_ref, acceptance_ref, input_bundle_sha256,
producer_commit_oid, producer_tree_oid, producer_build_sha256,
reviewer_id, reviewer_model, evaluated_utc}`; schema
`ALPHA_V11_GATE3_PLAN_EXPORT_TRUSTED_PINS_V1`. Issuance, revocation checking and
trusted evaluation time are unresolved trusted-channel responsibilities. A
plain dictionary passed by the untrusted caller must fail
`VPE_EXTERNAL_TRUST_UNAVAILABLE`, even if all its hashes agree.

After acceptance, an estimator may return only the closed resource report:
`{schema, status, export_sha256, acceptance_sha256, estimator_build_sha256,
request_count, scheduled_field_count, event_count, event_field_link_count,
full_denominator, purpose_plan, estimate, flags}`. Schema
`ALPHA_V11_GATE3_EXPORTED_PLAN_RESOURCE_REPORT_V1`, status
`OFFLINE_ESTIMATE_FROM_ACCEPTED_EXPORT`, denominator 2713. `purpose_plan` has
exactly the five purpose keys and each `{requests, reservation_bytes}`.
`estimate` is exactly `{runtime_capacity, v4_local_storage_quota_bytes,
model_scope}`. `runtime_capacity` contains the five `CapacityPlan` fields
`disk_bytes`, `memory_bytes`, `budget_records`, `store_events`, `aggregate_nodes`
[R:281–324]; `model_scope` is `FROZEN_INITIAL_PLAN_NO_LIVE_STATE`.
All counts/bytes are N, recomputed, never input hints. `flags` is the fixed
record above. Formula identity is covered by the externally reviewed estimator
build. Existing runtime and V4 estimates differ; report both rather than
silently using the smaller value. Restart feasibility needs actual journal
occupancy and fresh checks and cannot be inferred here.

Refusal output is exactly `{schema, status, code, flags}` with schema
`ALPHA_V11_GATE3_PLAN_EXPORT_REFUSAL_V1`, status `REFUSED`, one code from
section 8 and the same fixed flags. No partial estimates, echoed paths, raw
private values, accepted export or fabricated digest accompany a refusal.

The consumer does not rerun V4 or open original artifact references. It checks
the serialized schedules, event identities, projection formulas, counts, caps
and external acceptance chain. Equality of omitted original input bytes (such
as the plan-review body and runtime-context body) and their semantic validation
is attested by the authenticated producer/reviewer chain, not independently
proved by the consumer. If that chain is unavailable, stop with refusal; do not
replace it with internally consistent hashes.

## 5. Full event identity and complete schedules (proposed)

For every cohort position, `event_identities` contains exactly
`{ordinal, event_id, side, primary_provider, requested_key, gate2_trial_key,
station_id, station_version, latitude, longitude, timezone, tzdata, target_date,
units, rounding, buckets, rule, settlement, metadata, selection, city_day,
local_day_start_utc, local_day_end_utc, decision_utc, decision_lower_utc,
field_request_ids}`. Copy each scalar/ref exactly from validated cohort/time;
copy primary provider from accepted event policy. Ordinals are `0..E-1`.
No display label, field success or later metadata determines identity.

Enforce equality of complete sequences, not membership/subsets: E equals the
number of cohort sides, requested keys, trial keys, policy entries and plan
events, with `1 <= E <= 2`. Preserve original HIGH/LOW order. Keys are exact
nonempty five-/three-string arrays; requested key is
`(station_id,event_id,rule_id,target_date,family)` and trial key is
`(station_id,event_id,target_date)`. Family is determined by side. Distinct
requested keys and event IDs are required. Shared station/date does not collapse
two requested events. Rule ID remains bound to its rule reference and metadata;
the existing V4 checks only its presence, so new semantic rule/station/event
resolution requires separately reviewed evidence interpretation. Do not claim
that a checksum proves the rule text or official provider truth.

For each event, `field_request_ids` equals **all** scheduled FIELD IDs in original
request order, as current production projection requires [R:637–647]. Do not
substitute provider-only lists, duplicate them, remove an event with no successes,
or silently use just its primary provider. Every event/provider pair remains
represented conceptually even when that provider has no scheduled field. Export
may describe a bounded feasibility subset; such absence grants no eligibility.

Preserve every V4 request field, including `object_id`, `index_id`, `cache_id`,
schedule provider and prerequisite indices, in `manifest_projection.schedule`.
These are not all present in `AttemptRequest`; exporting only that dataclass
would lose identity information. Recompute request projection from the ordered
V4 schedule/endpoints/source pins and compare every field, including conversion
of prerequisite indices to IDs. Requests must be unique, their FIELD slots must
equal `attempt_slots` in order, and every overhead/prerequisite must remain.
Recount counts and reservations for FIELD/INDEX/OBJECT_ID/METADATA/PROBE, including
zero-count purposes, and compare to frozen `runtime.purpose_plan`. Savings or
unused headroom cannot be transferred across purpose, provider, request or slot.

Recompute the immutable slot list using [L:244–247]: GEFS 31×25, IFS 51×25,
AIFS 51×13, in provider/member/hour order with the exact frozen run timestamps.
Verify its hash and 2,713-row denominator. Omitted attempt slots remain explicit
unattempted rows in downstream terminal reporting. All-provider intersection
requires all three providers and the relevant full native trajectories with
qualified causal evidence; presence of three providers among a successful
subset is insufficient. HIGH/LOW shared captures do not double raw counts or
independent trials. No fallback/survivor cohort, changed run, cadence, date,
rounding or bucket partition is inferred from an estimate.

## 6. Causality, retained restrictions and purpose of estimates

Preserve V4 preregistration <= review < start, the fixed preceding-date
14:00–17:00 UTC acquisition window and 18:00 decision, pinned timezone local-day
boundaries, decision before local-day start, feature-seal upper <= decision
lower, expiry and <=1-second uncertainty [V4:357–385]. New input reviews and
export acceptance needed for later use must also finish before acquisition;
otherwise refuse that prospective use. Export creation after a historical
window cannot resurrect its authority or invent original knowability.

Retain exact restriction-lineage artifact and expected denial-history head from
the manifest; copied fresh identifiers cannot reset them. Existing shared-ledger
checks and current reconciliation remain mandatory at later authorized use.
401/403, 429/503, explicit denials and Retry-After are not cleared by resource
headroom, elapsed wall time guessed offline, alternate origins, different
clients, new export versions or a new run date. Any unknown/missing lineage
keeps the hold. This task did not read retained records or adjudicate a live
restriction [P:171–184; R:1059–1066,1498–1504].

The producer's proof is of validation/projection under exact inputs, not of
provider rights, remote availability, true clocks or real host capacity. A
reviewed estimate remains arithmetic. It reserves no disk/memory/report space,
performs no capture, decodes no qualified feature and grants neither G3-L nor
G3-E. Live headroom and journal occupancy can change after review. Original
request/body/decode/seal observations and original post-return runtime-use proof
remain separate; a durable RAW receipt or successful report alone does not
establish historical feature readiness [T:313–330]. Labels, fitting, learner
admission and SHADOW remain separately gated [P:24–37,237–252].

## 7. Bounded profile (proposed additional limits)

These are proposed **export-processing** ceilings, not increases to provider,
store or runtime budgets. An otherwise valid V4 candidate exceeding this
profile refuses export; no truncation, subset selection or automatic limit
increase is allowed. Bounds are checked before allocation/recursion/read where
possible, with a streaming/token preflight before materializing JSON trees.

| Quantity | Ceiling / invariant |
| --- | --- |
| Manifest / export bytes | Each 32 MiB; exact declared length and EOF, no compression or archive expansion |
| Plan review / runtime context / precedence | 4,096 bytes each (precedence additional export bound) |
| Supplemental pins | 2 MiB total |
| Event policy / policy review | 65,536 / 16,384 bytes |
| Custody / acceptance / trusted pin view | 16,384 bytes each |
| Resource output / refusal output | 65,536 / 4,096 bytes, never silent truncation |
| Aggregate supplied producer input buffers | 40 MiB, excluding export; referenced validation objects need separate below-mentioned budgets |
| Aggregate consumer buffers | 33 MiB; bound simultaneous copies in implementation, do not infer RAM fit from byte sizes |
| JSON tree | Depth <=16 (root depth 0); <=500,000 nodes total per artifact including keys; <=64 object keys except supplemental map <=3,600; all arrays <=3,600 unless a smaller domain limit applies |
| Strings | <=4,096 decoded UTF-8 bytes and <=24,576 encoded token bytes; valid Unicode scalar values, no lone surrogates; names/IDs follow existing narrower syntax; no normalization |
| Requests / request record | 1–3,600, also <= manifest max; each canonical record <=8,192 bytes |
| Events / field links | 1–2 production events; <=2,713 unique FIELD IDs per event, total <=5,426 (within current plan's 8,192 link ceiling) |
| Dependencies | Each request combined prerequisite IDs + external commit hashes <=256; total prerequisite edges <=16,384; total external dependency hashes <=8,192; no duplicates/cycles/dangling references |
| Other cardinalities | Exactly 2,713 slots; exactly 15 endpoints; exactly 3 source-pin providers; exactly 5 purpose entries; <=64 literal peer IPs; 7 precedence categories |
| Arithmetic | Checked integer sums/products <=2^63−1 before allocation; reject bool/float coercion for integer fields |

The producer's referenced V4 artifacts are not embedded in this export and must
not be recursively copied to a public estimator. Existing `Ref` permits up to
1 GiB per object [L:78–82]; that is not an aggregate export-worker budget.
Before future production implementation, separately review bounded unique-ref
resolution: proposed <=4,096 unique references, <=1 GiB each, <=4 GiB total
hashed bytes, bounded streaming buffers and no recursive opaque-object
expansion. Enforce declared lengths plus EOF and per-operation cancellation;
missing budget/custody support refuses `VPE_INPUT_BOUNDS`. These maxima are
processing limits, not measured host capacity. Current `_resolve_refs` does
not supply this aggregate budget and the proposed seam must not hide that gap.

Keep existing acquisition limits: <=3,600 requests, <=1 GiB received body,
<=10,800 seconds, one in flight, >=2 seconds between starts, <=30-second request
deadlines, INDEX <=3 MiB, GEFS FIELD <=2 MiB, IFS/AIFS FIELD <=4 MiB, other
metadata-purpose requests <=4 MiB (or smaller frozen caps), <=32 body chunks,
16 MiB report reserve, 4,096 store objects and 10,000 store events [V4:563–588,
790–835; R:79]. The export must never allocate those ceilings or claim they
fit: prospective runtime checks retain the >=2 GiB free disk / >=512 MiB
available memory floors after allocation, with stricter manifest floors
preserved [R:327–335,1167–1176].

## 8. Explicit refusal contract (proposed)

Checks run in this order; within a stage use schema field order and array order.
Return the first stable code. A future implementation must translate bounded
parser/I/O/arithmetic failures into a code and must not catch a failure into
an accepted result. Existing source exception strings are not silently renamed
as success; these new `VPE_` codes belong only to the proposed boundary.

| Stage / code | Refusal condition |
| --- | --- |
| `VPE_INPUT_BOUNDS` | Byte/token/depth/node/cardinality/ref-resolution budget exceeded, zero/truncated/oversized body, excessive read, output bound exceeded |
| `VPE_CANONICAL_BYTES` | Invalid UTF-8/JSON, duplicate keys, noncanonical bytes, unsupported numeric encoding, NaN/infinity/surrogates |
| `VPE_SCHEMA` | Unknown/missing key, wrong exact type, schema/version/scope mismatch, null where prohibited or false authority invariant violated |
| `VPE_EXTERNAL_TRUST_UNAVAILABLE` | No authenticated independent pin/custody channel; caller supplies its own purported trust root |
| `VPE_EXTERNAL_PIN_MISMATCH` | Actual length/hash or exact producer/input identity differs from the externally pinned record |
| `VPE_PRODUCER_BUILD` | Unresolved commit/tree, wrong source/loaded code/dependencies, dirty/substituted producer or missing reviewed export hook |
| `VPE_REVIEW_NOT_ACCEPTED` | Missing/unfinished/wrong-scope/self/same-model/unauthed review, unresolved findings, recycled terminal or wrong input binding |
| `VPE_CUSTODY` | Missing seal/receipt, publication failure, overwrite, race, mutable alias, file-type/link substitution, broken one-way handoff |
| `VPE_VALIDATION_TIME` | Untrusted or stale evaluation, expired inputs/acceptance, invalid review/seal order, stale replay represented as current validation |
| `VPE_PRODUCTION_MAPPING_UNSUPPORTED` | Synthetic scope/fixture used as production, unsupported provider-purpose contract (including current V4 blocker) |
| `VPE_MANIFEST_VALIDATION` | Any other V4 validation failure or inability to resolve required original evidence under authorized custody |
| `VPE_PROJECTION_MISMATCH` | Manifest/window/request/source/parser/clock/context/precedence or supplemental projection differs, plan/review digest inconsistent |
| `VPE_EVENT_IDENTITY` | Event alias/rule/station/date/side/provider/key/trial/policy mismatch, unsupported official-ID representation |
| `VPE_EVENT_COMPLETENESS` | Missing/extra/reordered/duplicate events or mismatched complete ordered field lists |
| `VPE_SCHEDULE_COMPLETENESS` | Dropped/extra/reordered request or overhead, wrong endpoint/object/index/cache/slot, missing/invalid prerequisite, changed 2,713 cohort |
| `VPE_PURPOSE_BUDGET` | Recount differs, any cap exceeded, cross-purpose transfer, incorrect reservation/range or silent budget widening |
| `VPE_RESTRICTION_LINEAGE` | Required lineage/head missing, substituted or unresolved for proposed use; any attempt to clear a retained hold |
| `VPE_ARITHMETIC` | Checked arithmetic overflow or estimate cannot be computed in the accepted bounded formula |

Absence of production mapping support is already enough to stop production
export today; an earlier missing trust/hook check may refuse first. Neither
failure is a request to supply private evidence in this task.

## 9. Adversarial offline test plan and checks actually performed

Future tests must use isolated synthetic bytes and temporary stores only,
record all mocks, and prove no dispatch, network, subprocess service or protected
path access. A mocked validator success can test a seam's mechanics, never
support a production-binding claim. Separate the pure consumer's positive test
with an explicit test-only external pin provider from production trust tests.

| Test family | Required assertions |
| --- | --- |
| Canonical/schema | Golden exact bytes/hashes including Unicode, integers/floats, bool-as-int, negative zero, duplicate nested keys, trailing newline/BOM, unknown/missing fields, NaN, huge exponent, lone surrogate. Distinct numeric bytes are not silently normalized. |
| Bounds | Exact limit and limit+1 for every section 7 dimension; malicious depth before decode, huge key/token, aggregate refs with individually valid lengths, declared-length mismatch, repeated refs, output overflow. Fail boundedly with no partial success. |
| Caller substitution | Mutate export then recompute every internal hash/review; old external pins must fail. Substitute external pinset argument supplied by caller, fabricated accepted terminal, same-model review, signed record with untrusted key, stale/revoked acceptance. None authenticates itself. |
| Production seam | Current non-synthetic V4 remains rejected for every required unsupported mapping; fixture flag/scope swaps fail. Assert exporter is unreachable before full validation/projection, on validation exception, and on missing hook/build/trust. |
| Event identity | Independently mutate every full-identity field; alias event ID, requested-key rule, primary provider, trial key, timezone/tzdata, local-day boundaries, rounding/buckets and selection. Rehashed caller policy without external review still fails. |
| Event omission | Empty list, drop HIGH or LOW, duplicate, swap order, extra side, drop one field, provider-only subset, duplicated reference, event with unscheduled/non-FIELD ID. Retain both events when captures are shared. |
| Schedule omission | Keep request count/triples equal while changing ID, object/index/cache, range, provider, source/decoder/parser, overhead, dependency or slot. Drop overhead and rehash, reorder before prerequisites, hide unscheduled rows; all refuse. |
| Cohort/intersection | Exactly 2,713 native slots; missing one member/hour, late RAW successes, three-provider partial subset, complete primary alone and absent provider get no G3-E or intersection eligibility. Shared HIGH/LOW never doubles trial counts. |
| Budgets/resources | Purpose recount vs same global total shifted between purposes; zero-count purpose omitted; overflow, cap+1, unknown object size, stale host headroom, restart with occupied journals. Report both V4 and runtime formula scopes; no actual-capacity claim. |
| Custody/races | Mutate inputs between validation and export, after export before review and before consumer use; replace file/symlink/FIFO, truncate, duplicate write, interrupted seal, copied receipt from another build/export, forged dataclass. No accepted artifact survives inconsistency. |
| Timing/restrictions | Replay across expiry/window/host/boot; replace validation time to refresh old evidence; missing/changed denial head; 429/503 or absent Retry-After with new run/origin/export. Preserve refusal; no retry/reset/roll-forward. |
| Point of use | Export acceptance without A8/G3-L stays offline; changed A8 adapter/context/package must re-refuse; G3-E needs real independent evidence even after resource-estimate acceptance. |
| Non-authority | Every success/refusal serialization carries all fixed flags; estimator cannot reserve, dispatch, create receipts, issue launch credentials or change gates. |

Performed for this document: static reads of tracked source and tests; AST/symbol
inspection; baseline source SHA-256 anchoring; isolated pure Python 3 probes
using only AST-selected `canonical`, `_pairs`, `parse_canonical` and
`FrozenEvent` definitions, standard-library objects and synthetic literals.
No project module was imported, validator invoked, or private evidence opened.
Probe results:

- Canonical `{"a":1}` and `{"a":1.0}` both pass as distinct bytes; duplicate keys
  refuse `DUPLICATE_JSON_KEY`; NaN refuses `NONFINITE_JSON`.
- A trailing newline refuses; currently the `LaunchContractError(ValueError)`
  raised for noncanonical bytes is caught by `parse_canonical`'s final
  `except ValueError`, yielding `NONFINITE_JSON`. The proposed boundary should
  expose its stable `VPE_CANONICAL_BYTES` code, not depend on this quirk.
- A class-only `FrozenEvent('alias','HIGH','GEFS',('field',), mutable_key, None)`
  accepts a mutable key list and reflects a later mutation. This probes only
  that class's validation, **not** acceptance by production `FrozenPlan`, whose
  key comparison requires equality to tuples. It illustrates why immutable
  canonical snapshots are required; it is not a demonstrated production bypass.
- Enumerating the production mapping predicate admits only IFS/AIFS FIELD/INDEX,
  four of the fifteen required pairs. This is a pure predicate check alongside
  source inspection, not a production validation run.

`python` was unavailable; the probes used `python3`. Sandbox startup initially
failed before execution (`bwrap` loopback setup); subsequent authorized local
commands used the tool's reviewed escalation. No network was used. Author
verification matched all 11 whole-file hashes to both baseline Git blobs and
unchanged worktree files, checked cited range bounds and named AST symbols,
and confirmed the explicit request schema has 19 fields. `git diff --check`
passed; the staged version is checked again before commit, with exactly this
one new document allowed. The later exact reviewer must examine
the final commit/tree; author checks are not independent acceptance.

## 10. Reproducible source anchors

All paths below are tracked at the baseline. Hashes are SHA-256 of whole-file
bytes. To reproduce without executing repository code, use
`git show b2129df92961b8916e970fed46d0f3fac2bf700c:<path>` and hash those bytes.
Line ranges in the text identify the cited symbol/check, with these aliases.

| Alias | Path | SHA-256 |
| --- | --- | --- |
| L | `tools/v11_r09_gate3_launch.py` | `d126744f3905b65b97dd19ccddc787d4bcdd71fca88288c1504966b0fb2681b8` |
| V4 | `tools/v11_r09_gate3_launch_v4.py` | `a2fe4d99666ef744927cfba49de03726b3fc695a98e7836dc1f3642781cbe978` |
| R | `tools/v11_r09_gate3_runtime.py` | `276d9779b1bcf1a6ca0b59b284d47ebd1540600c0280c57669ee6a6c589fcd3c` |
| A8 | `tools/v11_r09_gate3_a8_composition.py` | `7daaa970dbcaf34e6d1bed927f36e575dc54adab0fe33e17670bdf9b340a763f` |
| C | `tools/v11_multimodel_panel.py` | `ecc08fca4ad92585a3028a5188afeedacc90512adeec94d89f54736d3bffe8f2` |
| Q | `tools/v11_gate3_readiness_boundaries.py` | `50ba2c894f9cc20dee8349498dab2aab4a3f5b25e48680436929ba545d8e4e69` |
| P | `docs/V11_R09_GATE3_COLLECTION_PROTOCOL.md` | `da3144c558134e7bd6a06b5c3f298740e86cee13be7c54a9932204362c269524` |
| T | `docs/V11_R09_GATE3_TRANSPORT_RUNTIME_DESIGN.md` | `0b121fbc422208e2fa89e0b2f7362725cac60115ada966ed83b9d1ed15ac8e4a` |
| RT | `tests/test_v11_r09_gate3_runtime.py` | `8ef5c9c629dd2ca56e7527190229fedaadf9a5f0efd33c74804c8d368076e466` |
| AT | `tests/test_v11_r09_gate3_a8_composition.py` | `5e2eafd69475d9de9d4afd893f9fc260972cf0a33ad619201f539c370ff56995` |
| B | `docs/V11_GATE3_RESOURCE_BUDGET_LOCAL_PROBE_20261003.md` | `2cc9e7bb0edb4a7b337fc783cb9c0e8b5edfde32beb9d675284b723214508889` |

Test-source anchors are descriptive, not suite results: `RT:1482–1582`
(`test_frozen_plan_derives_from_validated_v4_manifest_bytes`) builds a fixture
and its review bytes locally; `RT:1585–1591` tests missing validated context.
`AT:224–254` and `AT:496–514` use isolated monkeypatched A8 validation and forged
minimal plan objects to test substitution checks. None supplies independent
production export provenance. No existing full test suite or production test
was run for this documentation-only change.
