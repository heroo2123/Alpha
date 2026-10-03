# Gate 3 production mapping: source-bound architecture proposal

Status: **PROPOSED_BLOCKED; independent review pending; no implementation or protocol amendment.**
Author: Astra/high, 2026-10-03. Source baseline: `9c5783c`.
The companion `.sources.json` binds full baseline commit/tree, source bytes and
clause anchors. Only tracked repository source was consulted for this design;
no provider documentation was fetched or private evidence inspected.

## Decision

Do not manufacture eleven endpoint contracts to make the V4 Cartesian product
pass. Preserve V4 and its intentional production refusal. Its four admitted
ECMWF path pairs are grammar support, **not four qualified provider contracts**.
There is no source-only executable solution for the other eleven pairs.

The smallest evidence-complete route to propose for separate acceptance is a
**versioned separation of evidence obligations from network request mappings**:
retain all three providers and all 2,713 denominator slots; require all field
prerequisite facts; list only genuinely scheduled network operations. Official
metadata and object identity may be supplied by qualifying sealed offline
artifacts under a new explicit contract, never converted into fictional HTTP
receipts. No probe is needed merely to satisfy a table. This is a proposed
protocol change, not a reinterpretation or relaxation of V4. It remains blocked
until independently accepted and implemented, and until actual evidence exists.

A V4-preserving alternative remains possible only if lawful retained evidence
establishes all fifteen current contracts, including bounded GET-200 object
descriptors and official metadata. The inspected source establishes neither
such descriptors nor metadata endpoints. Do not assume those services exist.
An evidence search must have a stopping condition: absent a supported contract,
record MISSING rather than generating more hypothetical URLs or design layers.

## What the current source proves

`validate_manifest_v4` requires `sources == SLOTS`, all five `PURPOSES` in every
source, and exactly fifteen provider-purpose endpoint pairs. Its nonsynthetic
predicate accepts only IFS/AIFS FIELD/INDEX at `https://data.ecmwf.int`.
`_check_mapping_spec` additionally fixes ordered literals and typed components.
Thus no current production manifest can pass, even if its attempted subset
contains only ECMWF. Zero scheduled PROBE requests does not waive its mapping.

For each FIELD, INDEX, OBJECT_ID and METADATA overhead requests must precede it
and share its provider/object/index/cache identities. All overhead has null
range bounds and no prerequisites. FIELD has frozen integer ranges; purpose
reservations are recomputed. A request cannot become an object-identification
range probe just by changing its purpose label.

The reviewed transport design section 2 defines OBJECT_ID as a bounded 200
descriptor proving target size and strong field ETag, METADATA as official
event/rule/station data, and FIELD as an exact 206 with If-Range. The index's
own ETag is not proof of the field ETag. `AttemptRequest` also requires a strong
expected ETag for every purpose, not just FIELD. `FrozenPlan` revalidates V4
and its runtime projection: a new mapping checker alone cannot admit a plan.
Opaque mapping/parser/response hashes are neither semantic validators nor
independent approval of the referenced assertions.

GEFS `field_request` describes NOMADS CGI query parameters and a filtered
200 response. It does not prove a direct-object S3 path, an `.idx` sibling,
byte-range behavior, or compatibility with V4's query-free 206 FIELD contract.
ECMWF `ECMWFRequest.url`, `selectors` and `plan_ranges` describe FIELD/INDEX
syntax and selectors. They do not establish provider rights, publication,
current release, strong ETag linkage or a successful bounded runtime attempt.

## Complete provider-purpose disposition

Each row requires the common evidence/rights packet below. “Missing” describes
the inspected source, not a claim that a provider cannot supply the evidence.
No row is currently launch-qualified.

| Provider | Purpose | Current support and exact additional requirement | Proposed destination |
| --- | --- | --- | --- |
| GEFS | FIELD | CGI grammar only. Official direct-object origin/key, anonymous range rights, exact native TMP/2m/member/run/hour/grid semantics, strong field ETag, size, frozen index-derived range and 206/If-Range evidence missing. Do not transplant CGI into S3. | Reviewed direct-object FIELD contract, or blocked. |
| GEFS | INDEX | Synthetic placeholder only. Official exact index-object relationship and suffix, row grammar, slot selectors, offsets/lengths including last-message bounds, own validator and version coherence missing. | Reviewed native INDEX contract, or blocked. |
| GEFS | OBJECT_ID | No bounded GET-200 descriptor contract. Need authoritative target origin/key, strong field ETag and total size tied to selected index version. An index listing or path digest alone is insufficient. | Qualified sealed object-identity evidence, or separately reviewed descriptor request; otherwise blocked. |
| GEFS | METADATA | No official event/rule/station endpoint mapping. A forecast index is not settlement metadata. Need exact selected event, station/version, timezone, date, units, rounding, buckets, rules and settlement source with original receipt/validity. | Qualified sealed cohort metadata shared by explicit reference. |
| GEFS | PROBE | No real contract. A probe must have an explicit necessary question, frozen endpoint, bounded response/parser, own rights and cumulative budget. | No scheduled probe by default; absent capability cannot dispatch. |
| IFS | FIELD | Exact ECMWF grammar only: control oper/fc, perturbed enfo/ef. Need applicable release/dataset evidence, anonymous range rights, run availability, size/strong field ETag, index linkage, exact member range and GRIB/section expectations. | Existing grammar plus separately qualified FIELD contract. |
| IFS | INDEX | Exact .index sibling grammar only. Need retained exact-run native rows, offset/length correctness, control number handling, own ETag and binding to field version. | Existing grammar plus separately qualified INDEX contract. |
| IFS | OBJECT_ID | No bounded GET-200 descriptor. Need target size/strong ETag and index-to-field version binding, not the index response ETag. | Same sealed-identity option as GEFS, scoped to IFS. |
| IFS | METADATA | No official cohort metadata mapping; weather model release metadata cannot substitute. | Same qualified cohort artifact, explicit IFS dependency. |
| IFS | PROBE | No real contract or implied exploration authority. | Unscheduled/absent unless separately justified and reviewed. |
| AIFS | FIELD | Exact ECMWF aifs-ens/enfo/cf-or-pf grammar only; perturbed members share file. Need AIFS-specific release/semantics, access, strong ETag/size, selected range, version coherence and independent GRIB pins. IFS evidence is not interchangeable. | Existing grammar plus separately qualified AIFS FIELD contract. |
| AIFS | INDEX | Exact .index sibling grammar only. Need AIFS native selectors, control missing-number normalization, unique member rows and bounded nonoverlapping offsets/lengths, own validator and field linkage. | Existing grammar plus separately qualified AIFS INDEX contract. |
| AIFS | OBJECT_ID | No bounded GET-200 descriptor. Need AIFS target size/strong ETag and exact index-version association. | Same sealed-identity option, scoped to AIFS. |
| AIFS | METADATA | No official cohort metadata mapping. Same cohort does not imply weather-provider authority over rules. | Shared qualified cohort artifact, explicit AIFS dependency. |
| AIFS | PROBE | No real contract or fallback permission. | Unscheduled/absent unless separately justified and reviewed. |

## Common evidence and rights packet

A future immutable contract record must bind all of the following. Missing,
expired, conflicting or unauthenticated evidence is a refusal, not a default.

1. **Applicability and rights:** publisher/access-policy document bytes and
   provenance, issuer, effective dates, dataset/release, allowed anonymous
   machine GET and any range/redistribution/retention constraints relevant to
   this use. Bind exact origin, port, path grammar, purpose and parser version.
   Review must establish applicability, not merely find a “public” label or a
   historical 200. This document supplies no legal or current-provider claim.
2. **Path and data semantics:** authoritative endpoint description and retained
   representative bytes tied to source/version. Cover control/perturbed members,
   run cycles, native steps, grid, parameter/level, file sharing and exact index
   association. No inferred mirror, suffix, directory or latest alias. Preserve
   temporal approximations and native-extrema distinctions in downstream data.
3. **Response and causality:** raw bounded headers/body and original observations,
   purpose parser/build, own strong validator where required, target size/ETag
   and version-coherence proof, exact selected offsets/lengths, independent
   expected section provenance, receipt uncertainty and immutable seals. A
   local file hash proves bytes, not provider authenticity or prior availability.
   Validate selected run/window applicability at each use; do not re-clock old
   evidence. A 200 full field cannot replace the required 206 range.
4. **Restrictions and custody:** exact control-domain grouping across relevant
   origins, retained denials, retry/expiry/resumption evidence, unfinished intents,
   cumulative attempt/body/time debit and custody chain. Unknown relationships
   or expiry block. Tracked bootstrap review records historical ECMWF S3 503s
   and a public-origin 429 without accepted resumption; preserve these as known
   unresolved history until authentic later evidence resolves them. No live
   restriction-state assertion was made in this design.
5. **Independent acceptance binding:** exact contract/code/evidence digests and
   lengths, reviewer identity/model separation, completed verdict/terminal,
   effective interval and permitted scope. A caller-controlled digest or approval
   boolean cannot enroll a contract. Deployment/launch must use independently
   trusted pins under an already authorized custody channel; this proposal does
   not create, install or assume such a channel.

Evidence may enter only through an authorized lawful offline intake or an exact
independently reviewed executable preflight whose prerequisites truthfully pass.
The existing dated P1 GEFS index proposal is not executable, does not establish
S3 rights, and cannot provide field ETag/size, all-provider coverage or metadata.
No extension to HEAD, range identification, listing, another origin, metadata
request or rolled date is implicit. If further acquisition is needed, separately
prepare its concrete bounded package and obtain the required scope/rights/review;
never ask a different actor or machine to bypass the same hold.

## Proposed successor contract: minimum semantic changes

Use a distinct schema and entrypoint (working name V5); reject cross-version
inputs and do not auto-migrate V4 manifests, approvals or journals. Exact design
acceptance must precede implementation. This is an architecture boundary, not a
complete executable schema or authorization to choose constants at runtime.

- Retain the immutable three-provider native inventory and denominator. A frozen
  attempt subset may omit slots only under existing selection rules; omitted or
  failed slots remain visible. This proposal does not authorize a GEFS-only
  denominator, statistical cohort change or SHADOW admission.
- Replace the compulsory fifteen-network-pair table with a closed registry of
  independently reviewed transport capabilities referenced by the actual frozen
  schedule. Every scheduled request needs a unique exact capability; absent
  capability means impossible dispatch. FIELD/INDEX remain real request kinds.
  No scheduled PROBE means zero requests, zero reservation and no probe endpoint;
  it does not mean a caller may add one later. Registry entries cannot be supplied
  as executable callbacks or self-qualified by manifest hashes.
- Preserve three prerequisite **evidence roles** per FIELD: INDEX_SELECTION,
  OBJECT_IDENTITY and COHORT_METADATA. The evidence graph uses immutable artifact
  refs plus explicit provider/run/object/index/slot/cohort applicability edges.
  Every role is mandatory. A role can be satisfied only by an independently
  accepted sealed artifact or a separately contracted earlier scheduled receipt.
  Never fabricate a request, HTTP status, attempt clock or budget debit for a
  local artifact. Deduplication is by validated exact artifact and applicability,
  not by same URL, digest label or provider name.
- Local metadata must satisfy the existing cohort and run/window freshness gates,
  official-source identity and receipt requirements. Local object identity must
  establish the actual remote target's size/strong ETag and exact index coherence.
  A claimed offline artifact with no such provenance remains missing. No new
  metadata producer, object descriptor endpoint or replacement ETag is invented.
- Ranges, expected validators/sizes, dependency artifacts, subset and schedule
  freeze before capture review. Runtime index results may confirm or refuse the
  frozen selection; they cannot select new ranges, discover a new member/file,
  fetch dependencies or expand the schedule. A changed version stops. Newly
  learned facts require separate preparation/review, not inline replanning.
- Preserve separate purpose accounting, all request/body/time caps, overhead-first
  ordering for scheduled overhead, causal cutoffs, shared denial history, original
  clocks, no retries/redirects/credentials, disk floor and resource gates. Local
  evidence parsing/validation consumes explicitly budgeted memory/time/storage
  and bounded records; “no HTTP request” is not “zero resource cost.”
- Implement an exact new projection through launch validation, frozen plan,
  runtime prerequest checks, persisted dependency verification and report replay.
  It must fail closed if any old consumer expects V4's request prerequisites.
  Update G3-L inventory/acceptance criteria only by explicit independently
  reviewed equivalence mapping; do not delete unresolved identities or relabel
  code readiness as qualified evidence. A2–A8, G3-L, G3-E and SHADOW remain gates.

## Reviewable next slices and stop conditions

1. Independently review this source-only decision and choose the proposed
   successor or the fully evidenced V4 alternative. This author's result is not
   that verdict. Preserve all seven earlier held candidates separately; none
   is merged, superseded or authorized for external transfer here.
2. For the successor, write one exact closed-schema/projection amendment with a
   row-by-row preservation map for the existing G3-L identities and A2–A8 duties.
   Resolve local-proof freshness/custody and through-use verification explicitly.
   Do not build a generic network dispatcher or enroll a provider as part of it.
3. Implement only after amendment review: pure validator/projector plus synthetic
   negative tests, then different-model exact-commit review and reconciliation.
   Unsupported production contract registry starts empty. Independently advance
   lawful retained-evidence preparation, clock/storage readiness and the existing
   reviewed preflight chain; none needs these proposed bytes to be executable.
4. Enroll a provider-purpose capability only with the common packet and exact
   review. GEFS direct-object rights/path and every provider's identity/coherence
   are currently evidence gates. If evidence cannot be lawfully obtained, keep
   the affected capture blocked; do not turn an implementation PASS into access.
5. Freeze a fresh complete dated package from accepted bytes/evidence, qualify
   clocks/resources/restrictions, then independent exact G3-L and actual bounded
   capture/G3-E. Preflight evidence is not capture evidence or forward SHADOW.

Required negative acceptance cases for a later implementation: synthetic origin
or placeholder promoted to production; recomputed digest enrolling an unknown
contract; CGI-to-S3 transplantation; swapped IFS/AIFS paths or FIELD/INDEX suffix;
index ETag used as field ETag; wrong shared-file member/range; stale metadata;
local seal substituted for provider receipt; retained hold lost on origin change;
unscheduled probe; missing role hidden by empty request list; field response
changing a frozen range; request accounting expanded by deduplication; V4/V5
mixing; altered dependency after review; and any network/account effect during
pure validation. Include valid synthetic shared-object/deduplicated-artifact
controls while retaining complete denominator and distinct evidence classes.

## Validation and limits

The companion source binding is reproducible against baseline Git bytes.
Existing V4 offline tests and isolated source-derived mapping probes are recorded
in the companion verification artifact; these validate present behavior, not the
proposed successor. No implementation, provider request, current rights lookup,
private evidence transfer, clock/resource qualification or independent verdict
is claimed. No V10/Axiom, execution, authority-root or financial state was changed.
**91/200, formal 1/50; G3-L NO-GO; NOT_READY_TO_FUND.**
