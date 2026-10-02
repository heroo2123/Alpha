# Gate 3 real-evidence intake through the checker -- implementation and handoff

Sonnet/high, base commit `5bc5321b7999dc18965483cdcb7cc16ad6cafe07`, isolated
worktree `/tmp/alpha-v11-gate3-postguard-prereqs-20261002` branch
`gate3-postguard-prereqs-20261002`. **Offline/local prerequisite slice only;
no provider request, no dispatch, no SHADOW admission, no score change.**

## What this closes

Picked up the prior checkpoint's open directive to decompose the Gate 3
critical path into independent, currently-codable prerequisite lanes and
start whichever is genuinely unblocked
(`docs/V11_WORK_CHECKPOINT.md`, "Weather Gate-3 attempt-guard hardening...
merged" entry, final paragraph). Of the candidate lanes -- collector/launch
wiring, fresh-date package preparation, clock/storage readiness, G3-L
identity/retained-restriction reconciliation -- the genuinely unblocked,
non-duplicative one was **collector/launch wiring to consume the already-
reviewed evidence-preflight package safely**: every prior evaluation of the
real retained `package.json`/`restriction-history.json` was either a
historical snapshot recorded once by hand in a review document (the "22
blockers" result in `docs/V11_R09_GATE3_PREFLIGHT_NEXT_SLICE_HANDOFF_
20261002.md`, measured at `2026-10-02T13:18:53Z`), or exercised the checker
against hand-written *synthetic* stand-in bytes
(`tests/v11_gate3_preflight_synthetic_cases.py`). Nothing in the repository
read the actual retained private bytes at their own bound path and fed them,
with a fresh honest current clock/resource measurement, into the already-
reviewed checker -- so nothing could answer "what does this real package
evaluate to *right now*" without a human re-deriving those inputs by hand.

## What this adds

- `tools/v11_gate3_evidence_preflight_real_intake.py` -- read-only, offline:
  reads the public binding JSON's own `private_package`/`private_
  restrictions`/`protocol` references, opens those exact absolute paths,
  verifies each file's actual bytes against the binding's own declared
  SHA-256/length *before* using them (`verify_retained_bytes`; refuses on
  any mismatch, oversize, or non-`bytes` input), takes an honest current
  clock reading (explicit "not qualified" uncertainty/calibration-age
  sentinel, see below) and an honest current disk/memory measurement
  (`physically_reserved_bytes=0`, matching the real package's own
  `storage_qualification.physically_reserved_bytes=0` -- never fabricates a
  reservation), and calls the already independently reviewed
  `check_evidence_preflight_package` directly. `run_real_evidence_intake`
  orchestrates all of this end-to-end; `__main__` prints the resulting
  report as JSON to stdout. No file is written to the private root or the
  tracked protocol document; the only new writes are this handoff and the
  retained output snapshot below.
- `tests/test_v11_gate3_evidence_preflight_real_intake.py` -- 20 offline
  tests. Every fixture is synthetic (the already-reviewed
  `tests/v11_gate3_preflight_synthetic_cases.py` bytes, or freshly
  fabricated synthetic bytes under `tmp_path`); the suite never opens the
  actual private root, per the attempt model's own established "no private
  fixture tests collected or executed" convention.

## A documented negative result, found and recorded rather than discarded

The original plan (before this exact implementation) was to route the real
bytes through the merged offline attempt model
(`tools/v11_gate3_preflight_attempt_model.py`, `AttemptModelGuard`) instead
of the checker directly, on the theory that `admit_synthetic`'s
`mode="SYNTHETIC_ONLY"` marker is "a type/scope boundary, not an
authenticity credential" (per that model's own acceptance doc) and so would
tolerate real bytes. Building and testing against that plan found this is
only half true: `admit_synthetic` also calls `_walk_synthetic_paths` on the
parsed package/restrictions/binding objects, which refuses outright
(`NON_SYNTHETIC_PATH_OR_INVALID_JSON`) the instant any `"path"`/
`"private_root"` key is not `synthetic://`-prefixed. The real retained
`package.json` genuinely contains such a key (`prerequisites.
owner_directive_original_record.path`), so routing it through
`admit_synthetic` always short-circuits to that single uninformative
reason before the richer checker evaluation ever runs -- confirmed
empirically by
`tests/test_v11_gate3_evidence_preflight_real_intake.py::
TestEvaluateRealEvidenceAgainstSyntheticFixtures::
test_real_shaped_absolute_paths_in_package_do_not_short_circuit_the_checker_layer`,
which reproduces the exact same package shape through both layers in one
test and asserts the contrast directly, not just in prose. This is a
deliberate, code-level boundary in the already-reviewed attempt model (it
structurally cannot be fed anything but synthetic-shaped bytes), not a
defect to fix -- the final implementation routes real bytes through the
checker layer (`check_evidence_preflight_package`) instead, which has no
such restriction and was already independently reviewed against this exact
real package. Recording this here so a future cycle does not repeat the
same (reasonable-looking, but wrong) plan.

## Honest clock/resource inputs -- not a fabricated pass

This host has no qualified clock-calibration recorder and no real physical
storage-reservation mechanism (both remain open external prerequisites;
see `docs/V11_R09_GATE3_G3L_RECONCILIATION_20261002.md`'s `clocks.*` and
`storage.*` rows). Rather than inventing plausible-looking values,
`current_clock_observation` reports an explicit `NO_QUALIFIED_CLOCK_SECONDS
= 86_400.0` sentinel for both `uncertainty_seconds` and
`calibration_age_seconds` -- far past the checker's real 1-second/60-second
floors, so it can never be mistaken for, or drift into, a passing
measurement -- and `current_resource_observation` reports the host's real
`shutil.disk_usage`/`/proc/meminfo` counters with `physically_reserved_
bytes=0`. One side effect, confirmed rather than hidden: because the
sentinel (1 day) is far wider than the package's own 3.5-hour window, the
checker's margin-folding logic correctly reports the measured instant as
*both* `CLOCK_BEFORE_WINDOW_START` and `EXPIRED_WINDOW` simultaneously --
not a bug, just the honest consequence of stating "we do not have a
qualified clock" as an enormous uncertainty rather than silently using a
smaller, unjustified one.

## Real run, this cycle

Ran `python3 -m tools.v11_gate3_evidence_preflight_real_intake` against the
actual retained evidence at
`/home/alphaadmin/AlphaV11_Gate3EvidencePreflight/20261002-gefs-index-v1/`
(byte-for-byte verified against the public binding's own SHA-256/length
before evaluation) with `HOME=/nonexistent` and under
`strace -f -e trace=network,connect`: **0** network/connect/socket trace
lines (the only prior precedent for this exact check, the InventoryTransform
review, found 0 real network syscalls and two failed local nscd lookups;
this run found literally zero `connect`/`socket` trace lines of any kind).
Exit code 0. Output retained verbatim at
`docs/V11_R09_GATE3_EVIDENCE_PREFLIGHT_REAL_INTAKE_20261002.output.json`
(reason codes and booleans only; no raw private bytes, paths or evidence
content). Outcome: **`CHECKER_REFUSED_BEFORE_DISPATCH`**, `satisfied: false`,
26 refusal reasons -- the previously-documented 22 (12 null prerequisites
plus `GEFS_LINEAGE_UNRESOLVED`/`GEFS_STATUS_NOT_ADMISSIBLE`/
`UNRESOLVED_GEFS_SCOPE`/`NULL_COMPLETE_LINEAGE_REVIEW`/
`NULL_SHARED_HISTORY_HEAD`/`NULL_UNRESOLVED_ATTEMPT_RECONCILIATION`/
`MISSING_EXECUTION_REVIEW`/`MISSING_LIVE_LEDGER`/
`MISSING_STORAGE_PERSISTENCE_REVIEW`/`NO_PHYSICAL_STORAGE_RESERVATION`),
measured at `2026-10-02T20:06:59.507417Z` -- plus 4 new, honestly-derived
reasons the earlier 13:18 UTC snapshot could not yet show because the
frozen window (`2026-10-02T10:00:00Z`-`2026-10-02T13:30:00Z`) had not yet
expired at that time: `CLOCK_BEFORE_WINDOW_START`,
`EXCESSIVE_CLOCK_UNCERTAINTY`, `EXPIRED_CLOCK_CALIBRATION`,
`EXPIRED_WINDOW`. This is the expected, correct outcome; it changes no
score, no identity status and no gate -- **91/200 (45.5%), formal 1/50;
A2/A3 UNQUALIFIED; A4 OPEN; A8 UNQUALIFIED; G3-L NO-GO; NOT_READY_TO_FUND**,
unchanged. The frozen `alpha-v11-evidence-preflight-20261002` campaign/
window/package bytes are themselves untouched by this slice -- this module
only reads them.

## Verification (this batch, foreground)

- `python3 -m py_compile tools/v11_gate3_evidence_preflight_real_intake.py tests/test_v11_gate3_evidence_preflight_real_intake.py` -- clean.
- `tests/test_v11_gate3_evidence_preflight_real_intake.py` -- **20 passed**.
- Targeted regression, plain and under `-O`, `--basetemp` off `/tmp`:
  `tests/test_v11_gate3_evidence_preflight_real_intake.py
  tests/test_v11_gate3_evidence_preflight_checker.py
  tests/test_v11_gate3_preflight_attempt_model.py
  tests/test_v11_gate3_attempt_runtime_wiring.py` -- **697 passed** both
  ways (one pre-existing, unrelated pytest `-O`-assert-mode warning, same
  as prior cycles).
- Wider regression (every `test_v11_r09_gate3_*.py` and `test_v11_gate3_*.py`
  file): **1433 passed**, 2 pre-existing unrelated warnings (an `os.fork()`
  `DeprecationWarning` inside `test_v11_r09_gate3_store_v1.py`, not touched
  by this batch).
- `git diff --check` -- clean (both new files only; no existing file
  touched).
- Real run under `strace -f -e trace=network,connect` against the actual
  retained evidence -- 0 network/socket/connect lines, exit 0 (see above).
- Disposable pytest scratch (`--basetemp=/home/alphaadmin/pytest-scratch-
  intake`) deleted immediately after the runs above completed; free disk
  recovered to 4.6 GiB, still above the 2 GiB G3-L floor. No change to the
  protected A8 fixture or any retained evidence/terminal.

## What this does not do

No network, no subprocess, no decode, no transport, no provider SDK, no
resolver call, no CLI flag that can dispatch anything. No change to
`polymarket_scanner/v11/`, V10, AxiomTrade, the checker, the attempt model,
`GateRuntime`/`AttemptModelGuard`, or any existing test file. No SHADOW
admission, qualification or authority change; `execution_authority`/
`provider_authority`/`capture_authority` are hardcoded `False` in every
report this module can produce and are never read from, or settable by, the
evaluated bytes. No score or gate-status change. The 77 missing PRE_REVIEW
launch identities, the real clock-calibration recorder, the real physical
storage reservation, and genuine GEFS/ECMWF access/restriction-domain
resolution remain exactly as blocked as before -- this module can only
report their current status honestly, not resolve them.

## Next action

Independent exact-commit review of this slice, covering at minimum: the
byte-verification logic's refusal-before-use ordering (`verify_retained_
bytes`), the honest-sentinel claims for clock/resource observations, and
the `_walk_synthetic_paths` contrast finding above (re-derive it
independently rather than trusting this document's narrative). After that
review, this module can be re-run on any later date to re-confirm the
frozen package's status without re-deriving clock/resource inputs by hand;
it does not by itself motivate preparing a new dated package (that remains
a separately versioned proposal requiring its own fresh review, per the
protocol's section 7). Separately, the checkpoint's other candidate lanes
(fresh-date package preparation, a real clock-calibration recorder build,
a real physical storage reservation mechanism) remain open, external-
evidence-gated or genuinely-new-build prerequisites, not advanced by this
batch.
