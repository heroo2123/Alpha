# R09 Gate 3 launch-contract adjudication — 2026-09-30

**G3-L BLOCKED / CHANGES_REQUIRED.** Astra/high reviewed local main
`866a6a506c663681b4075b2cc2f35d8aa6a3497b` for the routed acceptance question.
The offline GEFS ceiling correction's independent PASS remains valid within
its stated scope. It does not establish a complete collector or launch gate.
The contract below is a proposed addendum requiring independent cross-model
review; this author does not accept its own design. No network probe, capture,
private launch manifest, source-release attestation or new authority exists
as a result of this adjudication. **91/200; formal 1/50; NOT_READY_TO_FUND.**

Read this alongside the unchanged [accepted protocol](V11_R09_GATE3_COLLECTION_PROTOCOL.md),
[G3-P review](V11_R09_GATE3_PROTOCOL_REVIEW_117830a.md),
[G3-I review](V11_R09_GATE3_COLLECTOR_REVIEW_3e6a872.md), and
[ceiling correction review](V11_R09_GATE3_GEFS_CEILING_REVIEW_95e07fa.md).
Their original text is preserved as review history. Later launch acceptance
must explicitly bind this addendum's reviewed bytes; it cannot infer approval
from the old PASS labels. The private master hash was verified in place:
`a0e16d9bd7344c943a54a16a53c6757662363d93642f6e5cb7953cd047659b4a`.

## 1. Reconcile acquisition paths, not product-family names

| Path | Applicable bound | Acceptance consequence |
| --- | --- | --- |
| Existing GEFS NOMADS CGI subregion decoder | `grib_fields.MAX_BYTES=65,536`, `MAX_POINTS=25` | Unchanged. Cannot decode the intended full field. No production modification or bound increase is authorized. |
| Proposed GEFS S3 `noaa-gefs-pds`, `pgrb2ap5`, `.idx` plus single `TMP:2 m` range | 2,097,152 bytes/field, below the shared 4,194,304-byte bound | Corrected offline preflight at `95e07fa`, independently reviewed. Real origin, range identity, full-grid decoder and release mapping remain launch prerequisites. |
| Proposed IFS/AIFS full-field range acquisition | 4,194,304 bytes/field; 3,145,728 bytes/index | Native IFS three-hour and AIFS six-hour semantics remain fixed. Separate control/perturbed mappings must be evidenced. |

The historical assertion that GEFS's 64 KiB limit applied to Gate 3's full
field was incorrect. Applying the stricter *applicable* bound preserves
protocol Section 4; importing a bound from an incompatible transformation
does not. The production CGI decoder and its safety limits remain unchanged.
The nominal protocol 16 MiB field ceiling is not the effective launch cap.

There is a second, separate change of assumption: protocol Section 4 names
NOMADS raw-directory and `data.ecmwf.int` candidate origins. The size evidence
comes from GEFS and ECMWF S3 paths. Candidate origins were never approved
pins. Historical S3 success is neither current access permission nor evidence
that an unresolved hold on a different origin may be evaded. The launch
dossier must choose and pin each exact origin/path, explain its product and
release identity, and reconcile all relevant prior denial/restriction records.
No mirror switching, endpoint substitution, CGI fallback or implicit S3
allowlist is granted by this decision. Current provider documentation and
access facts remain unverified in this offline adjudication.

The future GEFS full-field decoder needs its own pinned implementation and
dependency builds, supported grid/packing signatures, maximum decoded point
count and memory/runtime bounds. A larger compressed-byte cap alone proves
none of these. It must validate the complete GRIB identity and native values,
extract the pinned station within 50 km, and perform only K minus 273.15.
Do not enlarge or reuse the incompatible CGI subset decoder silently.

## 2. Feasibility decision under unchanged ceilings

The committed observed-size evidence SHA-256 is
`efefd2396c35ac672e18094d611c7a956b709f7deeabb99d1e3e2f1831211eeb`.
It is historical retrieval-size evidence only, not release, clock, launch or
forward evidence. Per-provider observed maxima give:

| Provider | Fixed raw slots | Observed maximum bytes | Estimated raw bytes |
| --- | ---: | ---: | ---: |
| GEFS | 775 | 245,209 | 190,036,975 |
| IFS | 1,275 | 672,912 | 857,962,800 |
| AIFS | 663 | 635,346 | 421,234,398 |
| Total | 2,713 | — | 1,469,234,173 |

This raw-only planning estimate exceeds 1 GiB by **395,492,349 bytes**, before
indexes, probes, metadata and failed/partial bodies. It is not a prediction
or a lower/upper guarantee about future compression. A full-cohort launch
cannot be justified by this estimate. The existing helper proposes 2,125
raw-only fallback slots; that is **not an approved attempt schedule** because
it omits acquisition overhead and conservative per-request reservations.
GEFS historical index lengths were not recorded; unknown does not mean zero.

A reviewed bounded feasibility attempt remains possible under protocol
Section 3. Freeze all 2,713 denominator slots plus an explicit ordered attempt
subset before responses. Reserve unique indexes, object-identity requests,
metadata and failure-body limits first; compute request and elapsed-time
usage as well as bytes. Unknown index sizes reserve the applicable ceiling.
Each request reserves its permitted maximum before opening the socket; known
field lengths must also obey the provider cap. Release unused reservation
only after a durable known outcome; crash-uncertain reservations remain
charged. Count received failed/partial bytes truthfully, including violations.
Streaming enforcement must abort at the remaining allowance, not after a
complete oversized body. Never enlarge budgets or denominator-relabel.

The manifest must distinguish `capture_mode=BOUNDED_FEASIBILITY` from
`fallback_mode=NONE` (run/model policy). An attempt subset is not a replacement
cohort; untouched slots get terminal reasons and incomplete providers cannot
form a complete multi-model example. A new future full-capture budget would
require a new protocol review; this addendum does not propose increasing it.

## 3. Exact private launch package to implement

Use a versioned, closed-schema private JSON payload
`R09_GATE3_LAUNCH_MANIFEST_V2`, outside Git and protected authority. Reject
duplicate/unknown keys, nonfinite or wrongly typed numbers, placeholder
identities, unsealed references and unresolved mandatory values. Canonicalize
with the repository's pinned `canonical` function: UTF-8, sorted keys,
compact separators, ASCII escapes and no NaN. Arrays have schema-defined
order. Store precisely those canonical bytes; `manifest_sha256` is their
SHA-256, held externally, not a self-referential field. Every artifact reference
is `{sha256, byte_length, media_type}` resolved inside the private object store;
absolute output paths belong only in the private payload.

The existing `CaptureManifest` is an offline helper, not this complete schema.
Implement a separate strict launch validator with the following required groups:

| Group | Required content and validation |
| --- | --- |
| `identity` | Schema, unique pilot ID, nonfinancial purpose, capture mode and creation evidence; fixed false financial, promotion, host and launch authority flags. A payload cannot grant itself permission. |
| `code` | Repository object format, actual Git commit and tree OIDs for collector, launch validator, transport, decoder and clock recorder; source-file SHA-256s and dependency/build lock artifacts. Resolve OIDs against the real repository and reject dirty executable code. Git SHA-1 OIDs are 40 hex; artifact SHA-256s are 64 hex. Do not hash a commit label and call it a Git OID. |
| `protocol` | Exact original G3-P commit/tree and document hash, this addendum's accepted version/hash, complete G3-P/G3-I review and terminal references, including provider-bound and `95e07fa` corrections. No historical review silently approves newer executable bytes. |
| `storage` | New private canonical output root, owner/mode and directory identity, object/ledger layout, exclusive writer lock, atomic durable sealing method, symlink/path escape rejection and reserved final-report capacity. Exclude Git, V10, AxiomTrade, credentials and protected authority. No removal/reclamation policy. |
| `cohort` | Exactly one station/version and local target date; one HIGH and/or one LOW event; coordinates, IANA timezone plus tzdata hash; official rule/settlement source, Celsius units, exact rounding and complete buckets; contemporaneous metadata objects/receipt evidence and pre-weather selection rationale. Full requested keys, Gate 2 trial keys and mapping, separate city-day grouping. |
| `time` | Absolute preregistration/review-before-start requirement; preceding UTC date 14:00 start, 17:00 last acquisition bound, 18:00 decision; feature sealing upper bound no later than decision lower bound; decision strictly before local-day start. Freeze expiry, local-day boundaries and one-second maximum uncertainty. Validate DST/fractional offsets from pinned tzdata. No roll-forward. |
| `sources` | Exactly GEFS/IFS/AIFS dossiers with operational release documentation bytes and retrieval provenance, licence/access evidence, effective run interval, exact origin/path templates, control/perturbed mappings, centre/subcentre/tables/process/status/product/level/parameter/grid/packing pins and decoder builds. Bind evidence of index availability and coherent 206/range identity; booleans alone are insufficient. Missing publication attestation stays null with a reason. |
| `runs_and_slots` | `(0,)`, 86,400 seconds, `LATEST_COMPLETE_READY`, `NONE`; exact run UTC per provider reconstructed with Gate 2 candidate selection and conservative decision bound. Explicit slots `(provider, run_utc, member, hour)` with expected native valid times: GEFS 0–30 x 0:3:72; IFS 0–50 x 0:3:72; AIFS 0–50 x 0:6:72. Exactly 775/1,275/663 unique slots. No omissions from mutable dossiers. |
| `network` | Exact HTTPS origins, methods and fully constrained path templates for weather and any metadata; request purposes and index/object binding method. Anonymous only: no redirects, cookies, netrc, inherited proxies/credentials, signed URLs, private destinations or retries. DNS/TLS and resolved-address checks; restriction lineage across restarts and relevant previous jobs, including unresolved holds. No post-window label requests. |
| `limits` | At most 3,600 requests, 1,073,741,824 received body bytes across all purposes, 10,800 seconds; one in flight, starts at least two seconds apart, 30-second total deadline; index <=3,145,728 bytes and GEFS/IFS/AIFS fields <=2,097,152/4,194,304/4,194,304. At least 2 GiB free disk and 512 MiB available memory before requests/decoding. Explicit bounded headers, metadata, decoded points/memory and report-storage limits must fit these ceilings. |
| `schedule` | Hash of the full slot inventory; exact stable request/attempt order, shared index mapping/cache identities, explicit bounded subset, prerequisites per attempt, observed-size evidence and conservative overhead/reservation calculation. Persist remaining budgets and uncertain attempts. Probes count; HIGH/LOW share raw requests. Distinguish estimated totals from enforceable caps. |
| `clocks_and_receipts` | Concrete unprivileged measured time-sync/uncertainty method, host/boot and monotonic identity, raw evidence references and maximum measurement age; request start, complete body receipt, decode completion and durable seal clocks separately. Clock steps/missing bounds fail causal eligibility. Service-active or a self-written SYNCED flag is insufficient. |
| `accounting` | Frozen full terminal-reason precedence, all-applicable-reasons recording, crash journal/hash validation, no silent retry, expired-window local-only finalization. Exact raw-slot partition, event/provider eligibility and all-provider intersection reported separately. Diagnostics cannot acquire learner, label, calibration or forward credit. |

Runtime results are append-only artifacts referencing the frozen manifest digest;
they do not mutate the approved payload. Later review artifacts also live outside
the payload to avoid circular hashes. A detached review envelope binds
`manifest_sha256`, exact code OIDs/trees and protocol/dossier hashes, reviewer
identity/model, actual completion time, scope, probes, findings, verdict and
completed terminal hash. Approval and runtime checks must agree on the same
bytes. Any change requires a new digest and review, including a new date after
expiry; prior approval cannot authorize a replacement manifest.

## 4. Proven launch blockers in today's offline helpers

Six offline checks were executed at `866a6a5` with the project venv, using the
existing synthetic `make_manifest` fixture and `dataclasses.replace`:

1. Replace every dossier's members/hours with `(0,0)` / `(0,)`: denominator
   becomes 3, yet `require_launch_prerequisites` returns true.
2. Pass the original fixture with empty `manifest_id`: the same helper returns
   true. Its fixture clocks also are not the required date-derived pilot window.
3. Substitute actual 40-hex HEAD into `collector_commit_sha`: rejected with
   `MANIFEST_COLLECTOR_COMMIT_REQUIRED`, because the helper expects 64 hex.
4. With `BudgetTracker(max_total_received_bytes=1)`, complete one 1-byte request
   at monotonic 0, then begin another at 2: exhausted total budget does not stop
   the second request from beginning.
5. Complete that second request with one received byte: it raises, but the
   received-byte counter remains 1 although two bytes were reported received.
6. Feed the committed provider maxima to `estimate_feasibility`: 2,713 slots,
   1,469,234,173 raw bytes, full launch false, raw-only fallback 2,125.

All six observations reproduced. Local result:
`/tmp/alpha-v11-g3l-adjudication-866a6a5/probes.json`, SHA-256
`31c2339a2e89cf7334c1babbec099272cd738c19ca1806d6f05c51a50eb32e63`.
These are launch-integration defects/gaps, not evidence of an active unsafe
collector: no real transport exists. A fresh G3-I extension review must prove
the actual launch entrypoint rejects these cases and enforces byte accounting
during reads. Existing offline PASS labels and `.launchable=True` cannot
substitute for that work. No full regression is justified by this docs-only
adjudication; the accepted `6ec371e` release remains the evidence that closed
the historical load-sensitive failure, not a claim about a new release.

## 5. Independent acceptance and next work

1. A different model independently reviews this exact addendum commit/tree,
   its path-bound interpretation, six observations and private-package schema.
   Record findings, a scoped verdict and completed terminal. This is a G3-P
   addendum review only, with no acquisition permission. Preserve the original
   protocol and reviews; a PASS must explicitly identify what it supersedes.
2. Build the strict offline manifest validator, complete cohort/time checks,
   real-OID handling, conservative byte reservation/stream accounting and
   crash-persistent state under synthetic tests in an isolated worktree. Review
   the exact resulting G3-I extension independently before wiring real I/O.
3. Supply and independently review the real transport, full-field decoder,
   clock recorder and immutable-store integration. Prove the protocol's full
   adversarial list, including restrictions, ambient credentials, DNS/redirects,
   partial/oversized bodies, resource limits, index/object conflicts, release
   substitution, symlink/concurrent writers and crash recovery. A unit budget
   helper alone cannot establish these properties.
4. Resolve real dossiers, metadata, identity/range and clock evidence. Do not
   fabricate verification flags to escape the bootstrap problem: existing
   evidence may qualify only if independently reviewed for the exact intended
   path/release. If new live probes are necessary, prepare a separately bounded
   private preflight proposal and obtain fresh independent protocol/manifest
   approval for that scope **before** any request. The current G3-P table grants
   no preliminary network permission; this document grants none either. Carry
   all preflight restriction and receipt history into final launch review.
5. Only after those prerequisites, freeze a concrete private package with a
   future date and review its exact digest. The reviewer must be independent of
   the implementation/manifest author; include an identity/model distinct from
   the writer, explicit counterexamples and positive controls, and a completed
   terminal agreeing with the report. Missing, contradictory or expired review
   means INCOMPLETE, not permission. Exact-digest G3-L PASS permits only its
   one bounded anonymous window under the standing nonfinancial boundary.
6. G3-E independently replays actual raw bytes and receipts; Gate 4 separately
   reviews labels/example admission/fit; Gate 5 and root-custodied SHADOW
   admission remain separate. The GEFS SHADOW authority blocker cannot be
   bypassed by any R09 manifest or review.

Immediate handoff: independent cross-model review of this addendum only, then
the offline G3-I extension if accepted. Do not assemble a purported launchable
private manifest from synthetic fixtures or start a weather probe. Keep the
publication hold, original master, V10, protected authority and all unfinished
work intact.
