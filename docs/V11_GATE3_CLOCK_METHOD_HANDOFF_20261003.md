# Gate 3 passive clock evidence: offline design and implementation handoff

Status: **PROPOSED_OFFLINE_DESIGN — independent different-model exact-commit
review required before integration.** Author: Codex Astra/high, 2026-10-03.
Baseline: `b47f3f9c2fa6b31bb3296adc5aab233e7fd64dd2`, tree
`e54f3938b419534990a60f9aa19e73c6495b6272`. Public code/protocol only;
this deliverable contains no host measurement or private/provider evidence.

The shortest useful next slice is a **passive Linux clock recorder plus a pure
bounded dossier verifier**, exercised with synthetic inputs. It preserves actual
local observations and makes their limitations reviewable. It cannot manufacture
UTC calibration, qualify itself, or dispatch. Every output, including refusals,
fixes `execution_authority=false`, `provider_authority=false`,
`capture_eligibility=false`, `clock_qualification=false`,
`qualification_credit=0`, `g3l=NO_GO`. A complete dossier is
`RECORDED_UNQUALIFIED` or `STRUCTURALLY_LINKED_UNQUALIFIED`, never READY.
No implementation, host probe, network request or authority installation is part
of this documentation candidate. No held repair commit or private evidence was read.

## 1. Concrete observation method and its limit

Propose `LINUX_PASSIVE_CLOCK_V1`: a small, standalone, unprivileged native recorder
using `clock_gettime` for `CLOCK_REALTIME`, `CLOCK_MONOTONIC`,
`CLOCK_MONOTONIC_RAW`, `CLOCK_BOOTTIME`, and **read-only `adjtimex` with a
zero-initialized `struct timex`, `modes=0` hardcoded**. No caller-supplied modes,
`ADJ_*` operations, `clock_settime`, socket, daemon query, service command,
configuration read, shell child or privilege escalation. Query capability is a
Linux API design assumption to verify against pinned public headers/libc/kernel
semantics during implementation; no claim is made about this host's availability.
Unsupported ABI, denied query or missing clock refuses the method, with no fallback.

The only identity reads are the bounded boot UUID at
`/proc/sys/kernel/random/boot_id` and namespace links at `/proc/self/ns/time`
and `/proc/self/ns/pid`; no machine-ID or arbitrary `/proc` scan. Treat unavailable
identity reads as refusal. Independent host/build/clocksource binding remains
external; the recorder cannot discover a trustworthy host identity.
One bounded observation reads boot identity before/after, and brackets the query
and realtime read inside ordered monotonic/raw/boottime reads. Retain **all**
individual return values, clock IDs, resolutions, ordering, syscall result/errno,
original timex fields and units/status bits, and the outer monotonic interval
`[m_before,m_after]`. Serialize initialized fields, never uninitialized struct
padding. Decode micro/nanosecond status and scaled frequency units only through
the pinned ABI profile; unknown units/status remain raw diagnostics and refuse
interpretation. The query's returned offset, maxerror, esterror, frequency,
status and leap state are **kernel claims**, not certified UTC error bounds.
`TIME_ERROR`/unsynchronized, pending leap/smear without a qualified mapping, or
inconsistent query/read results prevent qualification; even a clean status does
not establish it. An overlong bracket is retained but unusable, not retried until
one favorable sample appears.

| Source | What it can establish | What it cannot establish |
| --- | --- | --- |
| Bracketed realtime/monotonic reads | Original local clock relationship and sampling latency | Absolute UTC offset, absence of an unobserved step |
| Read-only kernel discipline query | Actual locally reported discipline state/error estimates | Independent reference provenance or worst-case external UTC error |
| Raw/boottime comparison and boot ID | Evidence useful for slew, suspend and continuity checks | Independent oscillator accuracy, trusted host identity, VM rollback resistance |
| Already-authorized reference evidence supplied as exact bytes | Material an independent reviewer can evaluate | Qualification from hashes, repeated copies or a local PASS string |

Do not choose `date`, service-active output, HTTP Date, filesystem mtime, or two
reads of the same disciplined oscillator as a calibration method. Do not run
`chronyc`, `ntpq`, NTP probes or query even a local daemon in this first profile.
An existing daemon's retained reference observations could support later
qualification, but need their own acquisition authority, parser/build binding,
source provenance, original measurement time and custody review. They are not
present inputs to this task. Local kernel readings alone therefore produce
**genuine samples, with `utc_accuracy_bound=null` and `calibration_ref=null`**.
Never convert null to zero or rename a realtime read “calibration.”

The recorder may retain a separately supplied calibration artifact as opaque
bytes with original identity/time; it must not assert that the artifact is fresh,
independent or applicable. No real evidence belongs in Git or this worktree.

## 2. Qualification evidence and detached review

Three separate objects prevent circular approval:

1. **Method dossier (before any future window):** exact recorder source/commit/
   tree, binary, toolchain, libc/ABI and relevant kernel/clocksource/time-namespace
   profile; calibration procedure and retained raw prior calibration trials;
   independently justified reference-to-UTC error, path/asymmetry and sampling
   bounds, oscillator/slew envelope, loss-of-sync behavior, leap/smear policy,
   permissible host/virtualization domain, validity and revocation conditions.
   Empirical residuals alone do not bound future worst-case drift. Kernel
   maxerror/esterror or frequency estimates cannot supply the missing guarantee.
2. **Detached method/custody review:** binds those exact bytes, host scope,
   authorized observation mechanism, custody assessment and findings; names
   author/reviewer models and independent reviewer identity; has report digest
   and genuine completed terminal digest/exit status. The terminal binds the
   report; an outer envelope binds both, avoiding recursive hashes. Its verdict
   separates `METHOD_ACCEPTED_IN_SCOPE` from unresolved host/source/custody gates.
   It contains prospective predicates, not invented future sample hashes.
3. **Observation/session dossier (later):** binds the accepted method-envelope
   digest, actual host/boot/namespace, original calibration, every event sample,
   causal event/artifact reference, sequence/head and durable custody receipts.
   A later detached output review binds these exact bytes. It cannot backdate
   preregistration, approve a missed window or turn diagnostic bytes into receipt.

The closed outer envelope keys are `schema`, `scope`, `subject_manifest_ref`,
`method_policy_ref`, `host_scope_ref`, `custody_checkpoint_ref`, `reviewer_ref`,
`author_model`, `reviewer_model`, `validity_ref`, `verdict`, `findings_ref`,
`report_ref`, `terminal_ref`, and the fixed false authority fields above. Each
`*_ref` binds SHA-256 and byte length to explicitly supplied bytes; null is an
explicit unresolved prerequisite. Nested subject/policy/validity schemas must
freeze the bindings and predicates above in the implementation review. A
syntactically accepted envelope is still not an accepted reviewer credential.

The source's independence must be established by evidence outside the recorder's
control: for example an already-authorized reference-clock operator's retained
measurements and known error guarantees, with documented applicability to this
host's discipline path. An independent code reviewer alone is not an independent
time source. A second client sharing the same oscillator/reference/path is not
independent merely because it has another name. Reference provenance, transport
bias and host compromise assumptions must be explicit; absent support means
`CLOCK_REFERENCE_UNQUALIFIED`.

The pure verifier accepts explicitly supplied immutable bytes and checks closed
schemas, hashes/lengths and linkage. It never dereferences a path/URL and never
accepts a trust key/list from the dossier. A future admission consumer must resolve
reviewer identity, completed verdict, revocation and custody checkpoint against
**separately accepted records**. Neither this design nor the next recorder installs
that authority. A locally computed build hash is useful identity evidence; actual
loaded binary/libraries, namespace and observation origin still require independent
binding. Failure to bind them leaves `BUILD_OR_ORIGIN_UNQUALIFIED`.

## 3. Time arithmetic, continuity and expiration

Raw storage uses exact integer **Unix realtime nanoseconds**, clock-ID-tagged
nonnegative monotonic/raw/boottime nanoseconds, explicit signed offset fields,
and byte-preserved originals. Bound each scalar and checked sum to its declared
signed-64 representation; reject bool/float, overflow and precision loss. Convert
derived intervals to microseconds by flooring lower and ceiling upper bounds.
The readiness validator's `utc_us` is instead microseconds since **0001-01-01**;
any future adapter must explicitly add `62_135_596_800_000_000` to Unix
microseconds and range-check. Never copy native timestamps into that field.
UTC rendering must obey the existing canonical UTC grammar; reject leap seconds
unless a separately reviewed timescale conversion exists. CLOCK_TAI is no shortcut.

For the following equations all times and bounds are nanoseconds. For a
genuinely qualified calibration, retain UTC interval `[L0,U0]` and its
monotonic measurement bracket `[a,b]`. At event bracket `[c,d]`, require `c>=b`
and the same host/boot/clock domain. With a reviewed nonnegative monotonic-to-UTC
drift envelope `D(t)` (including allowed slew, rate error and rounding), use:

```
age_upper = d - a
P(event) = [L0 + (c-b) - D(d-a), U0 + (d-a) + D(d-a)]
```

`D` must be defined over the entire projected interval and expire with its
supporting evidence. A possible reviewed form is `ceil(rho*t)+j`, with explicit
rational rate bound `rho` and jitter `j`; **this design supplies no numeric drift
constant**. Unknown drift is unknown uncertainty, never zero. Add any independent
sampling/reference errors not already included in `[L0,U0]` or `D` exactly once.
Use the enclosing interval (outward-rounded midpoint/radius if an API requires
one); refuse a radius over 1,000,000 microseconds. Do not narrow `P(event)` using
a locally declared smaller uncertainty or by selecting a convenient clock read.

Retain checks against the original anchor **and every applicable earlier
constraint**, projected using the same qualified model. Pairwise overlap alone
can conceal cumulative drift. Require the observed realtime/monotonic offset
interval to be consistent with the qualified projection and all retained
constraints. Incompatible intervals or known clock steps refuse. Discrete samples
cannot prove absence of a step-and-return between reads; qualification must bound
or independently observe that risk. Loss of continuous step/sync evidence during
an attempt is a gap, not a healthy interval inferred from its endpoints.

Bind boot UUID plus an independently assigned host scope and recorded time/PID
namespace identities. Local IDs and `/proc` inode strings remain observations,
not authenticated machine identities. Retain raw/boottime brackets to detect
suspend versus monotonic elapsed time, and compare realtime/monotonic/raw changes
within their reviewed rate/bracket tolerances. Suspend, migration/restore,
namespace/boot/clocksource change, reversal or unknown continuity invalidates the
anchor. Never switch clock domains to hide a reset. Equal/coarse samples cannot
be fabricated into strict increasing times: retain them and refuse a projection
requiring stricter ordering. Proposed first profile forbids mid-attempt rebasing;
a new anchor starts a new diagnostic session, preserving the old one. A future
capture-wide refresh/transition protocol needs separate review.

Expiration is the earliest of method-review expiry/revocation, reference/source
validity, host/boot/profile change, calibration validity, drift-domain exhaustion,
uncertainty ceiling and approved window expiry. Historical bytes remain reviewable
after expiry; they cannot renew their live admissibility. Derive age from original
same-domain monotonic brackets, never copy time, mtime, UTC subtraction or a caller
age field. Cross-boot calibration is unusable even if UTC looks close.

## 4. Frozen protocol reconciliation and window fit

| Boundary | Existing authority | Treatment in this candidate |
| --- | --- | --- |
| P1 window | Oct 2, 2026, 10:00–13:30 UTC; no roll-forward | Expired at this task date; no recorder result revives it. New executable date needs separately reviewed protocol/package. |
| P1 uncertainty/age | <=1 s uncertainty; calibration age <=60 s **at dispatch**; consistent through body/parse/seal | Keep dispatch equality; later intervals must remain supported. No invented periodic recalibration. |
| Readiness `_clock` and attempt `_clock_fields` | Code caps age <=60 s at **each** supplied sample/event | Preserve as stricter existing offline behavior. A future adapter targeting it must budget the whole sequence inside anchor validity or refuse. Relaxation requires separate exact review. |
| Collection clocks | <=1 s; method and measurement-age policy frozen at G3-L | No collection-wide 60 s cap/cadence is currently authorized by P1. Refresh across a 3 h capture needs an explicit method/transition review. |
| P1 stage / attempt | 60 s stage, 30 s attempt | Check complete conservative fit and actual monotonic deadlines separately. |
| Capture schedule | preceding UTC date: 00Z run; 14:00–17:00 acquisition; 18:00 decision before local-day start | Keep exact schedule, 86,400 s run-age rule and full denominator; no use of P1's 3.5 h width. |
| Readiness proposed constants | 86,400 s planning horizon, resource age 60 s, representation/parser caps | Remain proposal/implementation bounds, not clock validity or protocol amendments. |

For prospective P1 fit require qualified dispatch lower >= approved start and
`dispatch_upper + 60 s + qualified_future_drift_margin < approved_end`.
Margin covers future growth beyond uncertainty already in dispatch_upper; no
double debit and no unsupported zero. Preserve strict expiry: equality fails.
With the existing per-event 60 s age cap, additionally require conservative age
at the end of the reserved work <=60 s. A nonzero-age anchor plus a full 60 s
reservation generally **cannot** meet that stronger code constraint. Do not hide
this by rebasing, subtracting elapsed review time or claiming the frozen protocol
mandates it: use a separately justified shorter work bound within the ceilings,
or leave this execution route blocked pending a reviewed policy/code resolution.
The proposed recorder can finish diagnostic evidence without solving this gate.

For capture, apply qualified intervals to acquisition and the existing manifest's
end inequality; preserve the readiness design's conservative strict acquisition
upper <17:00 where used. Bound the final request's remaining 30 s plus drift
before starting it. Full decode/validation/durable feature seal and every
metadata/dependency receipt upper must be **<= decision lower**, which is itself
strictly before local-day start using pinned timezone data. The protocol's causal
`<=` is not silently changed to P1's strict `<`. Review/preregistration must
finish before acquisition. Separately, each qualified metadata-receipt upper
bound must be **strictly before the first acquisition lower bound**; satisfying
the later decision cutoff alone does not establish pre-acquisition receipt.
A local recorder provides no proof that all 2,713
slots, decode work, reservation or the full capture schedule will fit.

A durable seal timestamp needs an **after-successful-fsync** observation whose
upper bound conservatively covers completion, linked by a later receipt record.
Do not timestamp before fsync as “durable,” or create an infinite demand for a
record containing the time of its own final fsync. A missing post-fsync receipt
leaves sealing unproved; later recovery does not invent the missing event time.

## 5. Storage, refusal and concrete implementation slice

Implement only new isolated files, provisionally
`tools/v11_gate3_clock_probe.c`, `tools/v11_gate3_clock_dossier.py`, focused
synthetic tests and a handoff. The native component is a one-shot passive
observation program with a compiled fixed read/query allowlist; the Python
component verifies supplied bytes and writes a bounded standalone session.
Build locally without installation. No execution caller imports or invokes them.
A future P1 integration must respect its prohibition on shell helpers and its
startup/CPU/memory bounds; a reviewed in-process binding would be separate work.

The closed session schema binds method/profile/build declarations, host/boot/
namespace observations, session nonce and sequence, event kind, original raw
sample objects, optional original calibration/reference bytes, prior object head,
method-envelope reference, absence reasons and fixed false authority flags.
Events in synthetic tests are labeled synthetic. Real standalone samples say
`LOCAL_OBSERVATION`; they cannot say dispatch/body/parse/feature-ready without
an actual causally linked operation. Hash both raw bytes and canonical parsed
projection separately; never substitute a reserialization for the original.

Proposed first-slice bounds: one observation per native invocation, <=16 KiB
raw result, <=64 samples, <=64 KiB per record/report, <=1 MiB total session
including imported evidence, bounded depth 8/nodes 4,096/strings 4,096 bytes
in structured metadata. Binary objects are separately length-bounded, not hex
inside a 4,096-character field. These are **new offline recorder caps**, not an
allocation from or expansion of P1's combined 8 MiB clock/header/receipt budget.
Reserve terminal/refusal capacity before recording; capacity exhaustion ends the
session without erasing samples. This first session size cannot be advertised
as full-capture retention capacity; that requires separate budget review.

For synthetic tests use only a temporary root inside the worktree. Eventual real
retention requires a separately authorized private root outside Git and /tmp,
with descriptor-relative no-symlink traversal, verified uid/mode/device/inode,
private parents, single-writer lock, exclusive creation, bounded writes, atomic
no-clobber publication, object/directory fsync and restart hash verification.
Reject path escapes, special files, hardlink/substitution conflicts and uncertain
publication. Same-uid code can still rewrite a local store: permissions, append
chains and fsync are not independent custody. Require independently retained
checkpoints binding session nonce, ordered object digests, final head and receipt
context, with independent replay. If no accepted external custodian/checkpoint
exists, preserve locally as `CUSTODY_UNQUALIFIED`; do not create a signing key or
call a local terminal an external witness. Reboot/crash recovery preserves
original evidence and unknown gaps, never a new calibration age.

Refuse with bounded codes for `CLOCK_SOURCE_UNAVAILABLE`, `ABI_UNSUPPORTED`,
`CLOCK_REFERENCE_UNQUALIFIED`, `SYNC_OR_TIMESCALE_UNQUALIFIED`,
`CLOCK_CONTINUITY_LOST`, `CLOCK_DRIFT_UNSUPPORTED`, `CLOCK_EXPIRED`,
`WINDOW_FIT_REFUSED`, `BUILD_OR_ORIGIN_UNQUALIFIED`, `CUSTODY_UNQUALIFIED`,
`STORE_WRITE_FAILED` and schema/size/hash failures. Keep all known raw bytes and
absence reasons where durable capacity permits. If persistence itself fails,
report that evidence is incomplete; do not claim successful retention. The
recorder owns no request ledger: it cannot refund, clear holds or alter campaign
IDs. A future consumer must stop dispatch before DNS on missing qualification;
a post-dispatch clock failure retains outstanding reservations/holds and bytes.

## 6. Merged-code integration review and acceptance contract

- `readiness_boundaries._clock` checks canonical *parsed* calibration/sample
  references and strongest prior UTC lower bound after the merged F2-R repair;
  it does not acquire raw kernel evidence or implement a drift model. Its
  `review.validity_domain` is only an identifier, not enforced expiry. Its
  reference `bytes_hex` ceiling limits each inline object to 2,048 bytes;
  actual binaries/review reports cannot be squeezed into it or replaced by
  invented short “source” bytes. Keep the new raw dossier separate. No schema
  widening or automatic compatibility adapter in the first slice.
- `ClockObservation` and `_check_clock` check caller scalars, source text and
  window arithmetic. They do not qualify provenance. Never populate
  `monotonic_consistent=True` merely because the recorder returned success.
- `preflight_attempt_model._clock_fields` and `step` retain synthetic
  event/accounting behavior; the adjacent divergence checks (including the
  600-second forward-delta screen) are not a <=1-second accuracy/drift proof.
  Neither that screen nor the readiness lower-bound check is a qualifier.
- `g3l_prep.REQUIRED['clocks']` separates recorder build/method,
  calibration/sync/uncertainty, host/boot identity and age policy. New dossiers
  can eventually support these four review identities, but only an independent
  acceptance consumer may fill them. `freeze_checklist` remains unchanged.

Required adversarial tests for the **future implementation**:

| Family | Mandatory counterexamples and expected result |
| --- | --- |
| Acquisition | Inject missing clocks, denied read, ABI/unit mismatch, nonzero timex modes, subprocess/socket attempt, overlong bracket, unsupported leap state. Retain bounded diagnostics, refuse; assert no clock mutation or network capability. |
| Source independence | Service-active, tiny maxerror, caller uncertainty=0, genuine local bytes plus forged PASS, second same-source client. All remain unqualified; a synthetic externally bounded case proves arithmetic only. |
| Arithmetic | Signed offsets; ns/us/year-1 vs Unix confusion; bool/float/NaN/overflow; outward rounding at 1 s and 60 s plus/minus one tick; bracket age versus midpoint age. Never round inward or accept future calibration. |
| Drift/continuity | Forward/backward steps, adjacent-compatible cumulative drift, retained-anchor/earlier-bound bridge, step-and-return evidence gap, monotonic equality/reversal, suspend, boot/namespace/VM restore, unreviewed recalibration. Refuse; never rewrite original anchor. |
| Fit | P1 lower==start allowed; upper==expiry refused; full-stage equality refused; age 1 s plus 60 s work cannot pass all-event cap; unknown drift; Oct 3 date cannot revive Oct 2 P1; capture ready upper==decision lower allowed only with every other invariant met. |
| Custody | Missing/swapped report or terminal, author self-acceptance, caller trust list, wrong host/build/evidence digest, expired/revoked review; symlink/hardlink escape, duplicate writer, truncation, fsync failure, rollback to an old valid hash chain without external checkpoint. No qualification or silent overwrite. |
| Causality/recovery | Copied sample gets fresh mtime, seal timestamp before fsync, crash after object write/before receipt, recovered unknown event time, omitted failure samples, full store. Keep original clocks, missing status, and holds; no refund/retry. |
| Bounds/separation | Duplicate/unknown keys, oversized/deep/hostile input, binary larger than inline-reference limit, source digest without loaded-byte binding. Bounded deterministic refusal; all authority flags fixed false in normal and optimized modes; no production imports/wiring. |

First implement the pure schema, raw-fixture parser and interval algebra; then
the narrowly allowlisted native recorder and synthetic filesystem/crash probes.
Obtain independent exact-commit different-model review of both before any real
local recording. Real recording, independent method/source/custody qualification,
and actual execution integration are three separate subsequent decisions.
External reference evidence and independent checkpoints cannot be synthesized by
this slice. Root-protected host attestation, clock discipline/configuration changes,
protected custody or SHADOW authority cannot be installed unprivileged and are
not requested. Root is not inherently needed for passive samples or an external
review, but an accepted authority outside the recorder's control is indispensable.
Provider rights, restriction reconciliation, storage/resource enforcement, fresh
executable protocol/package review, G3-L/G3-E and SHADOW remain unresolved gates.

## 7. Source bindings and validation

All bindings below are file bytes at the baseline commit above. Collection review
is scoped G3-P. Public progress records report P1 design as
`PASS_IN_SCOPE_DESIGN_BLOCKED_PACKAGE` and the later readiness implementation
merge; this task did not access private review packages or revalidate their
terminals. The implementation handoff describes earlier repaired bytes; the
actual merged source/test hashes below govern this design.

| Public source | Bytes | SHA-256 |
| --- | ---: | --- |
| `docs/V11_GATE3_READINESS_BOUNDARY_DESIGN_20261003.md` | 17848 | `87281d9fc67a4165c4aacd69288a17d3458f17ea9e236514fdbd8950461f3e7d` |
| `docs/V11_GATE3_READINESS_BOUNDARY_DESIGN_REVIEW_20261003.md` | 2286 | `2552489eead5aac3a7aae6467165efd434e4879570a9b840cc8642118846fc76` |
| `docs/V11_R09_GATE3_COLLECTION_PROTOCOL.md` | 20041 | `da3144c558134e7bd6a06b5c3f298740e86cee13be7c54a9932204362c269524` |
| `docs/V11_R09_GATE3_PROTOCOL_REVIEW_117830a.md` | 13657 | `7d2bda2034257784f8de8ae75f7c502fe9a3003e97bde154638b95e0b53050b4` |
| `docs/V11_R09_GATE3_EVIDENCE_PREFLIGHT_PROTOCOL_20261002.md` | 20274 | `ae59812fa58ec41895b87408f0ea175988ae6d20a1f4d5dabcd96a01c2dd3a68` |
| `docs/V11_GATE3_READINESS_BOUNDARY_IMPLEMENTATION_HANDOFF_20261003.md` | 17759 | `d9f4d3783a1dbdb8b96cb2bfd18ab210cb416124accbbb7eb4b94f1b35fd587c` |
| `tools/v11_gate3_readiness_boundaries.py` | 21121 | `50ba2c894f9cc20dee8349498dab2aab4a3f5b25e48680436929ba545d8e4e69` |
| `tests/test_v11_gate3_readiness_boundaries.py` | 26263 | `6b99a704ca2b4850356573e79c922c705014fbf2e6137e30588b11d20e52f579` |
| `tools/v11_gate3_evidence_preflight_checker.py` | 45681 | `6df49d57b6807d1b6d47075520d45c493f33550baaee1317d416e4d2d6347355` |
| `tools/v11_gate3_preflight_attempt_model.py` | 42781 | `4734e9a1386e5cb96a73fa3ba8959977f9756026d1adace2b985ba3cd388a463` |
| `tools/v11_r09_gate3_g3l_prep.py` | 31205 | `a5051a65aa219d40f5ac67acf9ca6f1e72839eb1d988ae5d08b2ea0565e89481` |
| `docs/V11_ENGINEERING_PROGRESS.md` | 500747 | `fd5d9c6a0be39aa074d326733702a6c4be548587d6c1729c123fa02965a104ed` |

Validation on this documentation candidate:

- Attempted `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 PYTHONDONTWRITEBYTECODE=1
  python3 -m pytest -q -p no:cacheprovider
  tests/test_v11_gate3_readiness_boundaries.py`: unavailable (`No module named
  pytest`). Nothing installed; no full-suite or pytest PASS claimed.
- Stdlib harness parsed that exact public test file with `ast`, removed only
  its unused `import pytest`, compiled with `optimize=0` to retain assertions,
  and invoked all zero-argument `test_*` functions. **20 passed normally and
  20 passed with `python3 -O`** (imported production modules optimized; test
  assertions retained). `PYTHONDONTWRITEBYTECODE=1`; no plugin/config loading.
  The one monkeypatch-fixture test was skipped. Existing tests cover the merged
  clock-history regression, structural nonqualification, strict fit, frozen-date
  refusal, resources and parser bounds. They do not validate the proposed native
  recorder, source accuracy, custody or future adversarial contract above.
- Baseline table hashes/lengths and unmodified source bytes checked; documentary
  epoch/projection/fit arithmetic checked with stdlib assertions. `git diff
  --check` clean. The candidate changes only this document; no prototype.

The candidate commit/tree are supplied in the author completion record, avoiding
self-referential commit hashes here. This is a review handoff, not an independent
review or clock qualification verdict. No G3-L/SHADOW or funding credit.
