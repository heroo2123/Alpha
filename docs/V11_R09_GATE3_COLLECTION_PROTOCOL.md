# R09 Gate 3: prospective source and collection protocol v1

Status: **DESIGN_PENDING_INDEPENDENT_REVIEW**. Author: Astra/high,
2026-09-30. Base: `fd77930`; offline contract accepted at `1ab551d`, integrated
at `0050759`. This document grants no collection, model, host or financial
admission. Gate 3 remains OPEN. Zero real examples, zero forward predictions;
**91/200; formal 1/50; NOT_READY_TO_FUND**.

The [contract adjudication](V11_R09_DATA_CONTRACT_ADJUDICATION.md) and
[Gate 2 acceptance](V11_R09_GATE2_REVIEW_1ab551d.md) remain binding. The private
FINAL-REVIEWED master was consulted in place and its SHA-256 verified against
`a0e16d9bd7344c943a54a16a53c6757662363d93642f6e5cb7953cd047659b4a`.
No private input is reproduced here. Preserve both historical stores and all
seven original pins; their 541 rows and 357/120/64 splits receive no causal
admission through this protocol.

## 1. Decision and distinct gates

Use a new prospective **capture-feasibility pilot**, not a fit or an evaluation
cohort. Its purpose is to establish truthful source identity, complete native
trajectories, receipt and refusal evidence. Retain the predictor identity
`R09_NATIVE_2T_TRAJECTORY_V1`; never call its points daily extrema.

| Gate | Required evidence | What a PASS permits |
| --- | --- | --- |
| G3-P protocol | Independent cross-model review of this exact document commit/tree, explicit findings and completed terminal | Offline, synthetic collector implementation only |
| G3-I collector | Exact-commit independent review and adversarial tests of a separate bounded capture tool | Preparation of a concrete launch manifest; no automatic network start |
| G3-L launch | Independent review binding G3-P, G3-I and the exact private manifest digest; every mandatory input below resolved | One bounded anonymous capture window, within the approved manifest only |
| G3-E corpus | Independent replay of actual raw bytes, operational release binding, clocks, metadata and full-cohort refusal report | Declare individually eligible *feature captures*, with label and learner eligibility separately gated |

G3-E does not admit anything through Gate 2's synthetic validator or authorize
Gate 4 adapter/fit. Gate 3 is not closed by G3-P alone. A later Gate 4 review
must resolve the feature-capture-to-example bridge, actual labels, split clocks
and any schema extension before real learner admission. Gate 5 trained-model
forward predictions and root-custodied SHADOW admission remain separate.
No fake `prediction_frozen_at`, training split or label may be created just to
construct a Gate 2 `ExampleInputs` object.

## 2. Operational source identity, separate from run and receipt

Each provider requires a versioned source dossier with immutable supporting
bytes and independent review, containing:

- Provider, dataset, operational model/release identifier and effective run
  interval; official release/product documents with retrieval records; exact
  HTTPS origin and path templates; licence/access terms applicable to public
  anonymous retrieval. No `latest` identifier or guessed release number.
- Expected GRIB centre/subcentre, table versions, operational status, generating
  process, product template/type, level, parameter, grid and packing signatures;
  separate pins for control/perturbed products when needed. Retain the decoded
  fields as well as hashes. An observed header hash alone is not release proof.
- A reviewed mapping from operational release documentation and product identity
  to those expected fields and the requested run interval. Mere coexistence of
  a release web page and unrelated bytes is insufficient. Contradictory or
  unresolvable mappings mean `SOURCE_RELEASE_UNRESOLVED`.
- Explicit member numbering and native hour sets, exact original unit K,
  instantaneous 2 m temperature semantics, and the decoder/dependency/code pins.
  Unknown grid, process, packing or release transition quarantines affected
  captures; never update pins automatically from the failing response.

The contract's `source_release = provider:date:cycle` identifies a run, not a
vendor software release. A separate capture-manifest `operational_release_id`
and `source_dossier_sha256` must carry the latter. Do not overload
`release_binding='ATTESTED'`: Gate 2 also requires an independently evidenced
publication timestamp for that value. A reviewed operational identity does not
prove publication time. `source_published_at` remains null unless exact-byte
contemporaneous evidence supports it; present local receipt can establish
prospective availability without inventing that optional clock. Preserve this
distinction in the future real adapter rather than weakening Gate 2.

Initial intended sources, subject to the launch dossier (not approved pins):

| Provider | Product intent | Members | Pilot forecast hours |
| --- | --- | --- | --- |
| GEFS | NOAA operational GEFS 0.50 degree instantaneous 2 m temperature; control plus perturbations | 0–30 | 0,3,...,72 |
| IFS | ECMWF IFS 0.25 degree ensemble; separately bind control and perturbation products | 0–50 | 0,3,...,72 |
| AIFS | ECMWF AIFS ENS 0.25 degree, not AIFS Single | 0–50 | 0,6,...,72 |

These are proposed frozen expectations, not assertions that all objects exist.
Do not shrink them to whatever an index returns. Current ECMWF documentation
lists three-hour IFS steps through 144 h and six-hour AIFS output. It also
records product changes around IFS 50r1; capture must verify the release-specific
control mapping. [ECMWF Open Data](https://www.ecmwf.int/en/forecasts/datasets/open-data).
The official change record lists IFS release dates, but is not an attestation of
our inspected bytes. [IFS change record](https://www.ecmwf.int/en/forecasts/documentation-and-support/changes-ecmwf-model).
NOAA's product catalogue is a starting point for the GEFS dossier, not historical
receipt proof. [NCEP GEFS products](https://www.nco.ncep.noaa.gov/pmb/products/gens/).
Documentation was checked on 2026-09-30; no forecast data was acquired for this
design. Earlier 429/503 evidence is preserved and must be considered at launch.

## 3. Concrete launch manifest and frozen denominator

A template with nulls is NOT launchable. Before any network acquisition, freeze
and independently review canonical manifest bytes and their SHA-256. Required:

1. Protocol, collector commit/tree, dependency/decoder builds, source dossiers,
   endpoint policy, output-root identity and all numeric limits. Freeze an
   absolute UTC start/end, decision time and expiry; no relative `tomorrow` or
   unattended roll-forward. Review must finish before the start.
2. Exactly one named station and one future local target date for this pilot;
   include every explicitly requested HIGH/LOW event, at most two. Freeze
   station/event IDs, coordinates, IANA timezone and tzdata bytes, official
   settlement source, rule version/fingerprint, Celsius unit, exact rounding,
   complete bucket partition and contemporaneous metadata references. This
   Celsius-only pilot must not relabel Fahrenheit or unsupported rounding.
   Station/date selection must be determined before weather/label inspection
   and its rationale recorded; do not pick an observed successful capture.
3. Explicit requested keys `(station_id, event_id, rule_id, target_date, family)`
   and Gate 2 trial keys `(station_id, event_id, target_date)` with their
   decision mapping; retain `city_day` as a separate display/grouping label.
   Expand every requested key
   times all three providers before collection. Missing/cancelled/unsupported
   events remain visible; neither survivors nor later labels define the cohort.
4. `allowed_cycles=(0,)`, `max_run_age_seconds=86400`,
   `run_selection=LATEST_COMPLETE_READY`, `fallback_mode=NONE`. Decision is
   18:00 UTC on the UTC calendar date preceding the named local date, and must
   be strictly before local-day start. Materialize the candidate slots using
   the Gate 2 `run_candidate_slots` rule and the conservative decision bound.
   Freeze exact run timestamps and every member/hour slot. For this fixed daily
   cycle and cutoff there is one eligible 00Z run/provider. If any temporal
   invariant fails, refuse the manifest; do not choose a different run silently.
5. The acquisition window is 14:00–17:00 UTC on that same preceding date;
   feature validation/sealing must finish by the 18:00 cutoff. Preregistration,
   review and metadata receipt must precede acquisition. This leaves a fixed
   hour for decode/validation; late work is diagnostic only.
6. Prospective clock-evidence policy, independent reviewer identity, review
   artifacts and their digests; explicit `financial_authority=false`,
   `promotion_authority=false`, `host_approved=false`. All values and raw
   evidence live in a new private store outside Git and protected authority.

Full nominal raw demand is 2,713 distinct messages:
`31*25 + 51*25 + 51*13`. HIGH/LOW share captures without becoming independent
trials. All expected raw slots are listed before response inspection. A dry-run
plan computes unique index requests, ranges, and worst-case budget usage where
known. If the frozen ceilings cannot accommodate acquisition, refuse launch or
record a prespecified bounded feasibility attempt; do not increase budgets
mid-window or reduce the denominator. Feasibility failure earns no admission.
Additional stations, cycles, dates or different cadence need a new reviewed
manifest/version; this single pilot cannot establish statistical sufficiency.

## 4. Bounded acquisition and provider controls

Only anonymous public requests from the exact reviewed origins/path templates:
initial candidate weather origins are `https://data.ecmwf.int/forecasts/` and
`https://nomads.ncep.noaa.gov/pub/data/nccf/com/gens/prod/`. Exact metadata and
label endpoints require their own manifest allowlist; there is no wildcard
HTTP permission. No cookies, netrc, ambient credential/proxy inheritance,
authorization headers, cloud credentials, signed URLs, entitlement services,
private-address destinations or redirects. DNS/TLS failures fail closed.

Hard ceilings for the entire one-window pilot, counting failed/partial attempts
and all response bodies, indexes and metadata: one request in flight globally,
minimum 2 seconds between starts, 3,600 requests, 1 GiB received, 3 hours elapsed,
3 MiB/index, 16 MiB/field, 30-second total request deadline. Apply a stricter
existing parser/field bound where present; this document cannot loosen it.
Maintain at least 2 GiB free disk and 512 MiB available host memory; check before
launch, each request and decoding batch. Decode one field at a time. A limit
hit stops new requests, persists the refusal report and closes the window.
Preallocate/reserve report capacity within the output budget. No file removal,
service interruption or resource reclamation from V10 or AxiomTrade.

Use exact single byte ranges selected from pinned indexes; require valid 206,
exact Content-Range and expected length, identity encoding and complete GRIB
message framing. A 200 response to a range request is refused and streaming
aborted under the byte cap; never fall back to a full file. Cache each index
and raw response once per identity; no repeated per-station downloads. Bind
index/object validator (strong ETag or independently reviewed immutable object
identity), resource size and message tuple; conflicting versions or inability
to establish coherent index-to-object identity mean refusal. Retain attempts.

No automatic retry in this pilot. 401/403 stop the origin; 429/503, explicit
provider denial or Retry-After stop the origin for the remainder of the window.
Record the restriction/cooldown in a persistent ledger shared across restarts;
no mirror, DNS/origin rotation or alternative client to evade it. Other network
errors/404 produce distinct attempt states, never claims of global absence.
A pre-existing active or unresolved restriction prevents launch against that
origin until its documented expiry or separately reviewed permitted resumption.
Absence of a Retry-After value is not permission for immediate retry.

A crash resumes the same manifest, request/byte budgets and restriction ledger;
verify existing hashes first. Never reset counters, extend the window or relabel
a new download with an old receipt. An uncertain in-flight attempt is recorded
and charged conservatively to the reserved request/byte ceiling; no silent
retry. After expiry, resume only local finalization and refusal reporting.

## 5. Receipt, decoding and immutable sealing

Capture request start, complete-body receipt, decoder completion and durable
feature-manifest commit separately. UTC records need contemporaneous time-sync
health, a measured conservative uncertainty bound, monotonic elapsed time and
host/boot identity. Pilot maximum uncertainty is 1 second. A clock step,
unsynchronised clock or missing evidence fails causal eligibility; time sync
service active status alone is insufficient. Freeze the actual evidence method
in G3-L; no signed/root authority installation is authorized. A local hash chain
is tamper evidence, not an independent attestation of time or publisher truth.

For causal feature eligibility, the upper bound of EVERY feature dependency's
receipt, metadata availability and feature-ready time must be no later than the
lower bound of the fixed decision. Feature-ready includes full decode/checks
and durable persistence, not merely last socket read. The decision precedes
target local-day start, computed with the pinned timezone (including 23/25-hour
and fractional-offset days). Forecast valid time may be after decision; raw
receipt may not. Out-of-day forecast points remain explicit predictors, not
in-day observations. Late replays never acquire retrospective Alpha knowledge.

Preserve raw index/HTTP/GRIB bytes, bounded response headers, URL/range, request
outcome, exact clock evidence, message offsets/lengths, run/member/hour/valid time,
parameter/level/unit, grid coordinates/displacement, decoder and extraction
manifests. Independently decode each field; bind every value to its raw message
and station version. Preserve native cadence, use only K minus 273.15 to C,
no interpolation, member replication or omission. Reject missing, duplicated,
conflicting or unsupported expected messages and grid displacement over 50 km.

Append attempts and content-addressed objects atomically; a sealed manifest
references only verified durable objects. Partial/failed attempts remain distinct
from sealed fields and complete provider trajectories. Shared bytes can have
multiple station extractions but one raw identity. Byte conflicts quarantine,
never overwrite. Keep an append-only chain of capture events and terminal report;
restart/replay must reproduce hashes and original clocks. Protect against output
path/symlink escape and conflicting concurrent writers. Hashes and self-generated
clock payloads do not substitute for the G3-E independent evidence review.

## 6. Coverage, uncertainty and later labels

Every frozen request key/provider/run/member/hour has a terminal state, including
not attempted. Use distinct reasons such as `NOT_ATTEMPTED_BUDGET`,
`NOT_ATTEMPTED_PROVIDER_HOLD`, `HTTP_404_AT_ATTEMPT`, `HTTP_THROTTLED`,
`PARTIAL_RESPONSE`, `INDEX_OBJECT_CONFLICT`, `SOURCE_RELEASE_UNRESOLVED`,
`DECODE_UNSUPPORTED`, `EXPECTED_MESSAGE_MISSING`, `CLOCK_UNTRUSTED`,
`LATE_FEATURE_READY`, `METADATA_UNAVAILABLE`, `RULE_CHANGED` and
`CAPTURE_ELIGIBLE_LABEL_PENDING`. Report all applicable reasons, plus one
stable primary reason under a precedence list frozen in the manifest.
No fabricated ready time for an unattempted/unavailable provider: retain a
separate observed status time, its context and cause. Gate 2's synthetic
`UNAVAILABLE` representation is not independent proof of a provider outage.

Terminal accounting must partition the original requested denominator exactly:
no absent rows, no duplicate counting. Report raw-message completion, provider
feature eligibility, all-provider intersection and event/family eligibility
separately; never multiply sample counts by members, snapshots or shared HIGH/LOW
features. With `fallback_mode=NONE`, an incomplete provider prevents a complete
multi-model example. Retain other providers diagnostically without renormalizing
weights or disguising a survivor subset as the frozen comparison cohort.

Freeze features without inspecting labels. Later label acquisition is a separate
reviewed bounded manifest; this pilot makes no automatic post-window label
requests. Bind official raw outcome bytes, settlement station/event/rule,
local date/unit/rounding, revision lineage, actual receipt and earliest evidenced
Alpha knowability. Pending/disputed labels remain gated. Rule drift or corrected
labels quarantine/version evaluation, never rewrite the original record.
No retrospective inferred label times, selection splits, fitted probabilities,
calibration or forward sample credit. Keep IFS/AIFS common lineage and city-day/
date dependence explicit in all later statistics.

## 7. Implementation boundaries and acceptance probes

Gate 2 is deliberately synthetic: `verify_synthetic_decoding` parses synthetic
messages and corpus summaries hardcode zero real admissions. Do not inject GRIB
as synthetic bytes or remove those flags. Build a separate capture-manifest and
attempt ledger without importing production/host-trust/financial services.
Reuse proven parsers only where their semantics match. In particular the current
`ECMWFRequest` intentionally limits IFS to six-hour requests: a reviewed native
three-hour request path is needed. Do not downsample IFS or modify a live caller
to hide that incompatibility. This protocol approves no code change itself.

G3-I/G3-L/G3-E reviews must check concrete counterexamples, not just hash presence:

- Same header with false vendor release; same run with wrong control mapping;
  AIFS Single substituted for ENS; release transition during the window.
- Genuine raw bytes plus forged message/member/hour/grid manifest; duplicate
  slots, truncated index, changed object at the same URL, 200-to-range fallback.
- Newer eligible candidate omitted from an expanded policy inventory; absent
  provider silently deleted; budget/clock/refusal rows removed from denominator.
- Late final byte or metadata, ready-before-decode, uncertainty crossing cutoff,
  forged initialization-as-receipt, restart with changed clock/budget/cooldown.
- Throttle followed by mirror switch, hidden retry, redirect/credential inheritance,
  oversized streaming body, symlink output escape and low-resource termination.
- DST/fractional-zone targets, wrong event/rule/unit, amended label, cancelled
  event, duplicate city-day and HIGH/LOW treated as independent outcomes.
- Positive controls: all complete truthful slots, future-valid timely forecasts,
  shared raw bytes with distinct extraction identities, and exact replay of a
  failed bounded capture with its original full denominator.

Each review records exact commit/tree, manifest/document hashes, reviewer/model,
executed probes or explicit documentary checks, verdict and completed terminal.
Missing/conflicting terminals mean INCOMPLETE. PASS only within its stated gate;
no writer self-acceptance. Preserve findings and superseded versions. Any change
to accepted protocol, code, source identity, cohort or bounds invalidates the
corresponding approval for future launches and requires fresh review.

## Next action

Independently review this exact committed protocol in an isolated worktree.
No acquisition, adapter, fit, service or authority action during review. On a
completed G3-P PASS, implement/test the bounded collector offline and obtain
G3-I review, then resolve and review the concrete private G3-L manifest. A
source/clock/provider-access blocker leaves only that path gated; safe GEFS
work may continue, but its current root-authority and forward-evidence gates
are unchanged. The existing GitHub publication hold remains binding.
