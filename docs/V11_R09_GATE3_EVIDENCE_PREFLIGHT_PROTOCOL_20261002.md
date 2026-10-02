# Gate 3 evidence-only preflight protocol P1 — 2026-10-02

**Candidate for independent design/package review. NOT EXECUTABLE.**
Author: Astra/high; baseline main `74075b5`. This separately named exception
implements the owner's October 2 preparation directive and conditional execution
authority. It does not grant G3-L, provider rights, capture admission, source
qualification, root custody, SHADOW or financial authority. The accepted
[bootstrap adjudication](V11_R09_GATE3_BOOTSTRAP_ADJUDICATION_20261002.md),
[review](V11_R09_GATE3_BOOTSTRAP_REVIEW_4c6d480.md), collection protocol and
A2–A8 criteria remain controlling outside this exact evidence-only scope.

## 1. Amendment, phase boundary and concrete proposal

Only an **EXECUTABLE_PREFLIGHT_PASS** binding the exact completed protocol,
implementation/build, private package and prerequisite evidence may exempt its
listed requests from the no-request-before-capture-G3-L rule. A design PASS,
blocked-package PASS, owner acceleration, historic success or a populated
boolean is insufficient. The owner has already conditionally authorized such
an executable package; do not ask again when every stated condition is proved.
Any missing condition still stops before DNS. No capture validator is relaxed.

P1 proposes one anonymous GET of one GEFS index, without Range, request body,
query, credentials, decode, field retrieval, official outcomes or labels:

| Frozen item | Proposal |
| --- | --- |
| Campaign | `alpha-v11-evidence-preflight-20261002`, never a new budget on restart |
| Stage/request | `P1_GEFS_INDEX` / `p1-gefs-2026100200-c00-f024-index` |
| Candidate run | `2026-10-02T00:00:00Z` (proposal, not a READY assertion) |
| Origin | `https://noaa-gefs-pds.s3.amazonaws.com`, port 443 only |
| Exact request path | `/gefs.20261002/00/atmos/pgrb2ap5/gec00.t00z.pgrb2a.0p50.f024.idx` |
| Method/purpose | `GET` / `INDEX_DISCOVERY_ONLY`; range absent, not an open-ended range |
| Earliest dispatch / absolute expiry | `2026-10-02T10:00:00Z` / `2026-10-02T13:30:00Z` |
| Statistical use | None; structural selection fixed before new response inspection |

The S3 origin/key is an **unqualified proposal**, not established by the CGI
adapter. Retained code/comment and historical size evidence justify reviewing
this candidate, not accessing it. The prior provider-mapping review expressly
refuses a guessed S3 mapping. Anonymous/public-access rights, exact endpoint
mapping and applicability of restrictions must be independently established
from lawful retained/offline input before this request becomes executable.
No request is permitted to discover whether permission exists. No documentation
URL, HEAD, listing, probe, alternate origin or ECMWF path is implicitly allowed.

P1 answers only whether this one exact index can yield retained structural
evidence. It cannot establish full-run readiness, all members/hours, object size,
field ETag, index/field version coherence, operational release, section pins,
cohort metadata, a forecast value, or any of the 2,713 capture slots. A successful
index response can still leave all 77 launch identities unresolved. Subset
selection for capture remains independently preregistered before weather/value/
label inspection; this index choice is not cohort selection.

## 2. Inputs, outputs and schema boundaries

The concrete private `package.json` and `restriction-history.json` are bound by
SHA-256 and byte length in the public binding JSON. Private roots are separate
from protected master inputs, repos, /tmp and root-owned authority roots. The
package's closed top-level schema is the enumerated key set in these candidate
bytes. Reject duplicate/unknown keys, nonfinite numbers, bool-as-integer,
missing keys, unsupported schema versions and unbounded strings/arrays. Its
current state is `PREPARED_BLOCKED`, with explicit null prerequisite references.
Null is a blocker, not an accepted empty proof. This package is not a V4 manifest.

Required pre-request inputs are exact scope/schedule/limits, original owner
exception record, accepted protocol review, independently qualified anonymous
access/path/control-domain evidence, imported restriction and unfinished-intent
lineage, actual store descriptor/persistence/lock/reservation evidence, measured
clock method/calibration, verified executable/transitive build/trust/DNS/parser
identities and independent completed execution review. No preregistration input
requires a hash, ETag, offset, readiness claim or body obtained from this first
index request. Authenticated provider rights may pre-exist offline; if they do
not, stop and separately adjudicate their acquisition instead of expanding P1.

The execution review is a detached envelope, not a hash recursively embedded in
its reviewed package. `prerequisites` contains only pre-existing evidence.
A final executable package must have nonnull qualified references for every
prerequisite and the review must explicitly say EXECUTABLE_PREFLIGHT_PASS.
A later package version requires fresh exact-byte review, even for a date change.

Outputs have a separate closed schema `ALPHA_V11_PREFLIGHT_RESULT_V1`:
`campaign_id`, `stage_id`, `request_id`, `package_sha256`, `runtime_lock_sha256`,
`review_envelope_sha256`, `input_history_head`, `output_history_head`,
`attempt_state`, `outcome`, `refusal_reasons`, `intent_ref`, `accounting_ref`,
`transport_ref`, `raw_headers_ref`, `body_ref`, `clock_refs`, `seal_ref`,
`parser_ref`, `parse_result_ref`, `eligibility`. Each ref is null with an explicit
absence reason or a bounded immutable artifact `{sha256, byte_length, relative_path}`.
No raw executable/object supplied by a response is loaded. Relative paths must
be canonical, beneath their descriptor-anchored root, with no symlink traversal.
`eligibility` is always `DISCOVERY_ONLY_NOT_G3E`. Decode time is absent with
reason `NOT_PERFORMED_BYTES_ONLY`; never invent a fourth phase timestamp.

Separate transport/parse/refusal outcomes: `REFUSED_BEFORE_DISPATCH`,
`RETAINED_UNQUALIFIED`, `RETAINED_INVALID`, `DENIED_HELD`, `UNCERTAIN_HELD`.
No outcome is named CAPTURED, READY, QUALIFIED or G3L_PASS. Preserve known bytes
and attempted clocks on errors; missing evidence remains missing. Bounded parser
failure must not destroy the original receipt or erase a restriction.

## 3. Exact request and bounded byte contract

The implementation is absent; this section is a required acceptance contract,
not a claim that the injected synthetic transport already implements it.
Preflight has its own entrypoint and closed schema. Do not enable unsupported
real purposes by changing V4, importing a relaxed manifest, or using a generic
URL client/CLI. An offline implementation candidate and fresh independent
exact-commit review must precede any executable package.

The adapter accepts only the frozen immutable intent. Verify origin/path/method
before any DNS. Fixed request headers: original Host, `Accept: text/plain`,
`Accept-Encoding: identity`, `Connection: close`, and a reviewed fixed User-Agent;
no Range, If-Range, cookies, auth, netrc, proxy, client certificate, cloud SDK or
ambient credential discovery. No redirects, retries, keep-alive reuse, alternate
peer, alternate IP family after failure, recursive fetch or hidden request.
Resolve once within the reserved attempt, at most 16 answers, with a reviewed
bounded resolver; reject nonpublic/special-use answers and unknown classification.
Freeze one eligible peer deterministically, connect once and check actual peer.
Validate TLS hostname for the original host and the locked trust store; never
skip certificate verification. DNS and TLS failure consume the attempt.

Successful framing requires 200, bounded unique case-insensitive headers,
identity content encoding, no Transfer-Encoding, a single decimal Content-Length
in 1..3,145,728 and exactly that many entity bytes. All headers together <=4,096
bytes, <=32 fields, name <=64/value <=1,024. Missing, weak or malformed index
ETag does not prove field identity; retain its exact original bytes as diagnostic
metadata. Never normalize a retained ETag into a stronger claim. Unexpected
status/framing/content-type/body refuses qualification. Error entity bytes and
read-ahead delivered to the transport boundary still count. A status denial is
recorded immediately, before reading further body. Bound TLS/wire framing/buffers
separately; entity-body totals are not claims about TCP/TLS wire totals.

The offline parser is pure bounded text processing: ASCII, no NUL, <=8,192 rows,
<=4,096 bytes/line, original row bytes retained. Proposed grammar has numeric
record number, byte offset, exact `d=2026100200` run token and colon-separated
native descriptors; record numbers unique/increasing, offsets nonnegative and
strictly increasing. Accept no executable text/URL. Extract only structural
`TMP:2 m above ground:24 hour fcst` candidates; require exactly one exact match
for a diagnostic selected row. Grammar and actual native semantics need a
qualified reference and independent parser tests before use. Observed unrecognized
rows stop parsing; never adapt the grammar after seeing the live response.

A following row offset may define a **proposed** inclusive end of start-next-1;
no last-row guessed length. Such diagnostics are not approved field ranges or
object size. A new reference-stage proposal must freeze exact integer range,
coherence evidence and response contract after independent review. It may not
chain a GET, run a decoder or replenish a budget. P1 has no field permission.

## 4. Restriction import and cumulative accounting

The private history preserves the three known September 30 ECMWF denials,
original response records and retained raw evidence identities:
503 `07:48:13.707996Z`, S3 503 `07:54:39.435641Z`, public-origin 429
`07:55:15.376499Z`. There is no accepted expiry/resumption for any. Missing
Retry-After stays missing. Later successes, elapsed time, owner authority and
an empty coordinator cooldown file do not close them. Both IFS and AIFS and
all ECMWF origins stay held. No reviewer may infer a separate control domain
from different hostnames, models, buckets, workers or public versus S3 access.

GEFS itself is blocked until a reviewed inventory establishes the complete
relevant restriction/unfinished-intent lineage and the scope relationship to
known holds (including shared-host/provider controls). Neither this proposal nor
its imported three records claims a complete history or independent domain.
If scope is unknown, preserve the affected hold; do not use GEFS as failover for
an ECMWF request. Resumption needs genuine evidence and independent scope/expiry
adjudication, append-only, never deletion of the original denial. The current
private history is an **inventory snapshot, not a live ledger or genesis**.

Before the first DNS, hold the shared restriction lock and stage/session/budget/
store locks, pin expected history heads, persist/fsync intent and full request/
byte reservation. Imported history and unresolved attempts must be reconciled
with all related roots by an independently reviewed admission. Do not initialize
an empty lineage because no live root was found. At most one global in-flight
attempt. Restriction heads must have independently retained external checkpoints;
a hash chain in a user-writable directory alone cannot prove rollback resistance.

| Limit | P1 | Entire named preflight campaign, including later separately reviewed stages |
| --- | ---: | ---: |
| Attempts, including DNS/TLS/error attempts | 1 | 8 |
| Charged entity-body bytes | 3,145,728 | 33,554,432 |
| Total stage elapsed seconds | 60 | 120 (sum of stage elapsed, including failed stages) |
| Per-attempt DNS through closure deadline | 30 seconds | 30 seconds |
| In flight / minimum start spacing | 1 / 2 seconds | 1 / 2 seconds |

Only P1's one attempt is proposed now. The campaign ceiling reserves no additional
paths/purposes. Across restarts/stages/hosts, completed attempts debit delivered
bytes, incomplete attempts retain full ceilings and time reservation; no retry
or allowance reset. Pre-reserve worst-case stage time; release unused time only
on evidenced clean closure. Ambiguity blocks subsequent stages. Overdelivery is
recorded in full (not clipped), poisons the session and never allows completion
or refund. Bound record count and write failures as well as body size.

Before success/error settlement, retain eager denials, close transport, persist
closure/accounting references and exact received totals, then release unused
reservation only on proven closure. A process crash, clock failure, ledger-full,
fsync failure or missing closure retains the hold. Carry all 401/403, 429/503,
explicit denial and Retry-After forward for at least the original window and
any longer evidenced restriction; unknown expiry requires separate resumption.
Never retry in the same stage/window even after a finite Retry-After.

Capture retains its original <=3,600 requests/1,073,741,824 body-byte and all
other limits. In addition, charge this campaign's consumed/outstanding allowance
against those same aggregate ceilings for the linked prospective pilot, so the
exception cannot expand a combined allowance. Do not hide preflight as free
"overhead." G3-L reconciliation must prove both independent capture requirements
and combined headroom; if the existing mandatory schedule no longer fits, refuse
and obtain a separately reviewed future plan without discarding accounting.

## 5. Clock, storage and execution qualification

Freeze host/boot, clock executable and dependencies, independently measured
UTC offset/uncertainty method and raw calibration evidence; service-active and
HTTP Date are insufficient. Use local already-authorized clock observations;
P1 grants no NTP or other network probe. Uncertainty <=1 second, calibration
age <=60 seconds at dispatch, and monotonic/UTC offset intervals must remain
consistent through body/parse/seal. Require start lower bound >=10:00Z,
all dispatch/body/seal upper bounds <13:30Z and both attempt/stage deadlines.
No fresh timestamp on recovered or copied bytes; absent/malformed/nonfinite/
stepping clock makes eligibility refuse while accounting/holds remain retained.
Clock evidence required for executable review may bind a qualified recorder
and prospective freshness predicates; actual per-phase observations are outputs,
not fabricated pre-review inputs. A calibration prerequisite is genuine prior
evidence of the method, not the future dispatch measurement.

Selected store is the package's explicit unprivileged state root, with separate
shared-history, session, objects and reports namespaces. Before execution, qualify
uid/mode/device/inode, no symlinks, private parent traversal, exclusive locking,
bounded descriptor-relative reads, atomic seal, fsync/restart behavior and real
filesystem persistence. The prepared directories/observations do not establish
those properties. No writes under `/etc/alpha-v11`, `/var/lib/alpha-v11`, protected
master inputs, V10 or AxiomTrade. No authority bootstrap or self-installation.

Pre-reserve 64 MiB of physically allocated storage: 8 MiB raw/temp objects,
8 MiB session/accounting, 8 MiB shared-history working allowance, 16 MiB report,
8 MiB clock/headers/receipts and 16 MiB margin. Immutable imported evidence has
its separately counted existing size. Journal <=8 MiB each, <=2,048 records,
record <=64 KiB; descriptor <=4 KiB. Stage working RSS <=128 MiB, process address
space <=256 MiB, CPU <=10 seconds, emitted diagnostic output <=1 MiB. An audited
watchdog enforces 60-second wall and kills/reaps bounded child work without
losing parent-held reservation. Prove actual dependency startup fits these bounds.
No ecCodes, NumPy, GRIB decode, shell helper or downloaded code in this phase.

Before allocating and before every request, leave >=2 GiB free disk and
>=512 MiB MemAvailable **after all remaining reservations**; target >=3 GiB free
and refuse if the hard floors cannot be maintained. Reserve host memory
conservatively for the 128 MiB working bound plus other active work. A snapshot
is not a quota; actual reservation/enforcement and monitored abort path must be
qualified. Retain the report reservation even on failure. Space recovery may
remove only verified inactive disposable scratch/cache, never protected A8,
evidence, terminals, authority inputs or unfinished worktrees.

## 6. Independent evidence and later admission

Retain response bytes, full bounded header transcript, actual peer/TLS observations,
intent/denial/accounting, four distinct applicable events (request, body, parse,
seal), raw clock evidence, boot ID and hash-bound custody. Parser-complete is not
decode-complete. Independent exact-output review verifies original times and
provider/access/hold conformance; P1 outputs remain discovery-only even on PASS.

Future section-reference acquisition requires its own explicit FIELD-reference
scope and concrete reviewed ranges. Its expected hashes are unknown outputs at
acquisition, never fed back as that same response's expected inputs. An independent
source/semantic adjudication must state how a defensible independent pin origin
is established (e.g. authenticated provider release/encoding facts plus a
separately lawful reference with independent validation). A second GET, a second
reviewer computing the same hash, or mechanically copying observed section hashes
is insufficient by itself. If independence cannot be demonstrated, A6 remains
unqualified and explicit criterion adjudication is required. This protocol makes
no finding that the currently retained evidence can satisfy it.

Freeze any accepted reference dossier before later capture uses the field as
evidence. A changed run/member/hour/index/object/version/range/pin refuses; no
in-place pin refresh. Reference receipts never become capture receipts and never
receive G3-E credit. Capture must independently satisfy A2–A8, cohort selection,
all 77 PRE_REVIEW identities, its exact implementation/package review and normal
G3-L PASS. Re-run the local G3-L screen on new qualified input; report only the
identities actually supported. Brain integration and forward SHADOW remain gated.

## 7. Review verdicts and next implementation slice

Independent review must bind actual Git commit/tree, protocol/binding JSON hashes,
private package/history and all copied input hashes, author/reviewer model IDs,
report hash and original completed exit-0 runner terminal. Dirty or changed input,
missing terminal, different package, expired dates, incomplete lineage or a
conditional design approval cannot be translated into executable PASS.

Permitted present verdict: `PASS_IN_SCOPE_DESIGN_BLOCKED_PACKAGE` or
`CHANGES_REQUIRED`. Neither permits requests. The reviewer must enumerate all
null/unqualified prerequisites and compare public/private scope and limits.
Actual implementation/build/parser, lawful exact GEFS access/mapping, complete
restriction/domain reconciliation, storage/persistence/time/resource qualification
and execution review are absent. Do not manufacture their identifiers.

After accepted design, the next unblocked implementation slice is an **offline
closed-schema package checker and synthetic refusal/state-machine tests**, with
no socket creation, HTTP library, provider CLI or real adapter. It must refuse
this concrete package, a changed path/date/limit, unknown keys/null prerequisites,
unresolved history, changed private bytes, missing review/terminal, expired clock,
insufficient post-reservation resources, attempted FIELD/ECMWF/second request,
replay/reset, and every forbidden attempt to promote outputs. Positive tests use
clearly synthetic offline identities and confer no real permission. Scope that
candidate separately and get a different-model exact-commit review. A real
transport is a later explicitly scoped implementation/review, not an automatic
consequence of a checker PASS. Provider rights and genuine custody evidence
remain external inputs; no software implementation can invent them.

No production source, safety gate, provider, service, authority or private master
was changed by this design. No qualifying slot or C/J/E/A boundary crosses:
**91/200, formal 1/50; G3-L NO-GO; NOT_READY_TO_FUND**.
