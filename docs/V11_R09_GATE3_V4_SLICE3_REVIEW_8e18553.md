# Gate 3 V4 slice 3 second repair — independent exact-commit review

**CHANGES_REQUIRED. G3-L NO-GO.** Offline implementation review only; no
provider, launch, SHADOW, financial, or production authority is granted.

- Candidate: `8e18553227522fa28e829ee66a32a8064f28c55f`
- Tree: `32044de41265f22aadf3582781fc337b5afdfcfb`
- Parent reviewed candidate: `ef45d355f8d7e3a924564cdf9f5f2601c01a50f6`
- Worktree: `/tmp/alpha-v11-gate3-v4-slice3-repair-20261001`
- Reviewer: independent Astra/high, separate from the Sonnet/high repair author.
- Review date: 2026-10-01, approximately 14:41–14:51 UTC.
- Main at review start: `e796fcc`; candidate remains unmerged and unchanged.

## Scope and validation

Read the authoritative checkpoint/matrix/progress, prior F1–F7 report and
probes, accepted runtime design sections 4–7, slice-2 carry-forward review,
the entire two-file repair diff, runtime composition/report code, relevant V4
validation, and ledger/budget replay and persistence paths. The repair is
600 insertions / 75 deletions; `git diff --check ef45d35..8e18553` is clean.
The accepted design's full slice-3 obligations still apply; individual new
regression tests do not supersede them.

| Independent check | Result |
| --- | --- |
| Entire `tests/test_v11_r09_gate3*.py` family | **439 passed**, two existing fork warnings, 32.20 seconds |
| Independent probes, final revision | **11 passed**, 1.23 seconds: eight defect reproductions and three closure controls |
| Candidate git status, before and after | Clean; exact HEAD/tree above unchanged |
| Full release suite | Not repeated; accepted prior release remains 5,460 passed / 13 skipped |

Tests used only offline fixtures; the repository's network guard remained in
place. Disk was below the launch floor. A review-only pytest teardown plugin
closed leaked fixture descriptors and removed files only under this review's
unique completed `tmp_path` directories, preserving directory names/inodes.
No existing worktree/test artifacts were removed. An earlier cleanup revision
also removed those directories, causing path/inode reuse against fixture
history and uncertain-root registries: **75 failed / 364 passed**. That run
is invalid as candidate evidence. The corrected plugin produced the full
439-pass run; both logs remain under `/tmp`. No candidate code was patched
to obtain the pass, and no resource/safety gate was relaxed.

Durable probes: [review probes](V11_R09_GATE3_V4_SLICE3_REVIEW_8e18553_probes.py).
The probes accept `ALPHA_REVIEW_CANDIDATE` to point to an unchanged checkout
of the exact candidate. Tests named `test_defect_*` intentionally pass when
the defect is reproduced; they must be converted to refusal/correct-accounting
assertions for implementation regressions. `test_closure_*` assert repaired
behavior, including same-boot reopen for the clock intersection.

Review runner and log hashes/paths are recorded in the
[terminal](V11_R09_GATE3_V4_SLICE3_REVIEW_8e18553_terminal.json).

## F1–F7 disposition

| Prior finding | Disposition |
| --- | --- |
| F1: store-phase global clocks | Closed for the reported offline runtime path: local body/decode/seal samples now enter the durable intersection. Independent test proves the narrowing survives reopen and refuses a contradictory next request. |
| F2: error-path denial/body accounting | Partial; normal elapsed-expiry and duplicate-header examples repaired, but G2 below remains. |
| F3: cooldown expiry | Closed offline: all three pre-dispatch/open checks use UTC minus uncertainty. Independent boundary control refuses dispatch. |
| F4: validated plan and shared boot | Partial; all four journal boots now compared, and transport receives the immutable request. Validated manifest binding remains bypassable/incomplete (G1). |
| F5: denominator/reporting | Partial; missing FIELD slot rejected and numeric attempted counts now use reservations. Frozen reason precedence/all reasons and attempted ID consistency remain open (G3). |
| F6: physical report reserve | Closed for the reported case: reopen locks/checks identity, fallocates/fsyncs and verifies allocated blocks. Independent sparse-file control passes. |
| F7: capacity | Partial; normal <=32 chunk and session-event admission improved, but failure accounting exceeds the proposed bound and disk arithmetic remains stale (G4). |

Prior R3 post-close pacing remains covered by the passing family. No new
source, decoder, real clock, provider/storage qualification, feature-use proof,
or G3-L acceptance is inferred from any closure above.

## Blocking findings

All source line references below are to `tools/v11_r09_gate3_runtime.py` at
the exact reviewed commit.

### G1 — P1 — Validated V4 binding is still optional and the factory ignores frozen time/context

The added factory at lines 502–556 calls V4 validation, but `FrozenPlan(...)`
at lines 426–492 and `GateRuntime` at lines 690–693 still accept the original
caller-built five-field review summary. No retained validated manifest bytes
or validated projection is required at the runtime boundary.

`test_defect_direct_unvalidated_field_plan_still_succeeds` dispatches and
reports COMPLETE for a synthetic FIELD with no INDEX/OBJECT_ID/METADATA
prerequisites and an opaque manifest hash. The helper constructs ordinary
production `FrozenPlan`/`GateRuntime` objects; no production validation is
monkeypatched in that probe.

Even choosing the new factory does not bind its `window`, `events`, or
`request_pins` to the validated context. The factory projects only schedule
and endpoint fields. `test_defect_factory_accepts_window_and_pins_unrelated_to_manifest`
uses the existing offline V4 fixture (with its fixture-only identity pins),
runs its real V4 validator, and produces a plan starting at UTC 0 and ending
at 2,000,000,000 despite the manifest's fixed prospective three-hour window.
An arbitrary clock-policy digest is also accepted. The manifest already has
time/limits, source decoder references, endpoint parser identities, runtime
clock policy, journal root/lineage references and accounting policy; these
must not be described collectively as fields absent from its schema.

Required repair: make exact V4 validation/projection mandatory for this
runtime's accepted plans, including original-slot/provider relationships,
mandatory prerequisites, frozen time/limits, and relevant source/clock/parser/
journal/review context. Bind additional pins to explicit evidence where the
schema lacks a direct representation. Keep test fixture construction explicit
and incapable of being mistaken for the validated boundary. Do not add a real
adapter or treat a Python constructor token as external launch authority.

### G2 — P1 — Recovery still loses known observations when validation itself fails

At lines 930–932 the recovery helper validates a new clock before preserving
the known denial or body. A timeout or duplicate-header response combined
with uncertainty 2 seconds causes `RUNTIME_CLOCK_UNCERTAINTY_EXCEEDED` and
leaves **zero known delivered bytes and no denial history**, despite an eager
framed HTTP 503 with Retry-After and four already-delivered bytes. Both trigger
variants are reproduced independently. The intent/reservation remains held;
the hold does not restore the missing observations.

At lines 927–936, any header error clears the entire header map and prevents
Retry-After-based OTHER classification. A 200 response with an observable
Retry-After plus duplicate ETag charges its four bytes but records no denial.
Header invalidity must not turn an observed restriction into absence.

At lines 603–604, a bounded 400-digit numeric Retry-After becomes infinity.
The shared denial validation then raises before denial/body accounting. The
independent 503 example again leaves zero known bytes and no denial record.
Invalid/unrepresentable expiry must be retained as unresolved, never discarded
or interpreted as permission to resume.

Required repair: retain known eager delivery independently of trustworthy
clock/header parsing, preserve bounded observable denial evidence or explicit
missing/invalid-evidence causes, and keep an unresolved global hold. Handle
invalid clocks without claiming them valid, and nonfinite/overflowing expiry
without losing the restriction. Do not weaken ledger evidence requirements or
refund an unknown reservation to make these cases pass.

### G3 — P2 — Report still omits all reasons/frozen precedence and mislabels refused IDs

Lines 1346–1363 emit only one `reason`. No plan/report field binds the V4
`accounting.terminal_precedence` evidence or retains all applicable reasons.
The independent probe has a FIELD whose prerequisite failed and whose clock
is before the window: only `RUNTIME_PREREQUISITE_MISSING` survives, with no
reason collection or frozen primary precedence. This was already an explicit
part of prior F5 and accepted design section 6.

The numeric attempted counters are corrected, but line 1426 still emits all
session intents as `attempted_request_ids`. In the same probe both requests
are refused before reservation: `attempted_count=0` while attempted IDs are
`['req-1', 'req-2']`.

Required repair: bind/use the frozen precedence, retain all applicable reasons
that can be established from original evidence, propagate prerequisites
without inventing later evidence, and derive attempted IDs and counts from
the same durable reservation set. Report refused IDs separately. Keep the
2,713 original-slot partition and raw/event/provider accounting distinct.

### G4 — P2 — Chunk policy rejects too late to bound recovery journal consumption

At lines 1037–1039 a >32-chunk response enters recovery before the policy
exception. Lines 945–957 then append one budget record per eager chunk.
`test_defect_chunk_policy_error_path_exceeds_capacity` supplies 64 one-byte
chunks within a 64-byte reservation: admission budgets **35** records, but
the attempt adds **65** before raising the chunk-policy error. A larger
already-delivered response can hit the hard journal cap before all known
bytes are retained. Rejection after eager dispatch is not an enforced bound
on the accounting work caused by that dispatch.

Separately, `CapacityPlan.for_requests` lines 277–281 still budget
`records + 19*n` (comment: three store + twelve session + four shared), while
session admission now uses **20** per request. Update the disk bound alongside
event admission, including initialization/finalization and any dependency
aggregation required by the frozen slice; do not merely adjust test constants.

Required repair: impose a real bounded delivery/accounting policy at the
boundary or durably aggregate eager error accounting with bounded events,
preserving every known byte and all holds. Independently derive normal,
denial, violation and recovery worst-case counts and byte costs, including
remaining capacity on reopen.

## Repair handoff and safety state

Keep `8e18553` unmerged. One Sonnet/high implementation worker should repair
G1–G4 in the existing isolated worktree after checking that no newer writer
or commit has appeared. Preserve predecessor reviews/probes and the already
closed F1/F3/F6 behavior. No second generic supervisor or duplicate writer.
Require focused adversarial regressions, full offline Gate 3 family evidence,
a clean exact candidate commit/tree and terminal, then a fresh independent
different-model review and reconciliation with newer main before any merge.
The new candidate terminal must say G3-L NO-GO, not launch-ready.

At 14:48 UTC, PAPER demo/scanner/controller and execution are inactive;
demo/scanner/controller disabled, execution masked. `/etc/alpha-v11` and
`/var/lib/alpha-v11` are absent. Private FINAL-REVIEWED master SHA-256 remains
`a0e16d9bd7344c943a54a16a53c6757662363d93642f6e5cb7953cd047659b4a`.
Root free space was approximately 450 MiB, available RAM 750 MiB plus
1.5 GiB free swap: passive observations, below launch disk qualification.
No V10, AxiomTrade, service, authority, provider, financial or remote
publication changes occurred. Score remains **91/200 (45.5%), formal 1/50;
NOT_READY_TO_FUND**. The prospective 14:00 UTC window was missed; this review
does not re-date or qualify it.
