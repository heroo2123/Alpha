# Gate 3 real-evidence intake wired into the actual launch path -- implementation and handoff

Sonnet/high, base commit `57b6fd1794d37ca9e40fb071502573a133c4f400`, isolated
worktree `/tmp/alpha-v11-gate3-intake-launch-integration-20261002` branch
`gate3-intake-launch-integration-20261002`. **Offline/local prerequisite
wiring only; no provider request, no dispatch, no SHADOW admission, no
score change.**

## What this closes

The independent Opus review of the merged real-evidence intake
(`tools/v11_gate3_evidence_preflight_real_intake.py`, commit `781c000`)
found, as its single most load-bearing informational note, that the tool
"is a standalone status reporter with no collector/launch module calling it
yet, so it is correctly **not** credited as formal (J) integration"
(`docs/V11_WORK_CHECKPOINT.md`). This slice is that wiring: the smallest
fail-closed integration that makes a real-evidence-intake outcome an
enforced pre-dispatch prerequisite in the actual Gate 3 runtime
(`tools/v11_r09_gate3_runtime.py::GateRuntime.run_attempt`), without
granting any provider/network/execution authority anywhere.

## What this adds

- `tools/v11_gate3_evidence_intake_guard.py` (new file):
  - `EvidenceIntakeRecord` -- a frozen dataclass built only via
    `from_report(report)`, which accepts exactly the dict shape
    `tools.v11_gate3_evidence_preflight_real_intake.build_report` produces
    (closed key set; refuses on any missing/extra key), refuses outright if
    any of `execution_authority`/`provider_authority`/`capture_authority`
    is not literally `False`, and refuses if `satisfied` is inconsistent
    with `refusal_reasons` (mirrors the checker's own `CheckResult`
    invariant). Any malformed, truncated or tampered report fails closed at
    construction -- never silently admitted.
  - `EvidenceIntakeGuard` -- wraps one frozen `EvidenceIntakeRecord`.
    `require_admission()` raises `LaunchContractError
    ('RUNTIME_EVIDENCE_INTAKE_NOT_SATISFIED')` unless the bound record's
    `satisfied` is `True`. Stateless and deterministic: the record never
    changes after construction, so repeated calls on the same guard always
    return the same verdict -- no retry path to a later admission.
  - `EvidenceIntakeGuard.from_real_intake(*, repo_docs_dir, binding_path)`
    -- the one sanctioned route for a real (non-synthetic) caller: runs the
    already-reviewed, read-only `run_real_evidence_intake` fresh (never a
    cached report) and wraps its own output. No network, no subprocess, no
    write.

- `tools/v11_r09_gate3_runtime.py` (existing file, additive change only):
  - `GateRuntime.__init__` gains one new optional keyword-only parameter,
    `evidence_intake: EvidenceIntakeGuard | None = None`, validated exactly
    like the existing `attempt_model` parameter (`RUNTIME_COMPOSITION_SHAPE`
    on a wrong type) and stored as `self.evidence_intake`. Every existing
    caller that omits it is completely unaffected -- confirmed by the full
    pre-existing Gate 3 regression suite passing unchanged (see
    Verification below).
  - `GateRuntime.run_attempt` gains exactly one new statement, the first
    line inside the existing pre-Step-1 `try` block (before
    `_resolve_prerequisites`, `_check_capacity_resources`, `_enforce_window`,
    the control-domain-blocked check, and any bound `AttemptModelGuard`):
    ```python
    if self.evidence_intake is not None:
        self.evidence_intake.require_admission()
    ```
    A refusal here is caught by the same existing `except LaunchContractError`
    and routed through the same existing `_session_refuse` path used by
    every other precondition -- durable, exactly-once, no-refund-ambiguity
    accounting is completely unchanged. No existing check is removed,
    reordered relative to each other, or weakened; this only adds one more
    gate that must also pass before Step 1 (the first durable
    session/shared mutation, and therefore before `self.transport.dispatch`
    can ever be reached).

- `tests/test_v11_gate3_evidence_intake_launch_wiring.py` (new file, 23
  tests, all offline/synthetic) proves:
  - backward compatibility: a runtime built without `evidence_intake`
    dispatches exactly as before;
  - a satisfied record lets `run_attempt` proceed only to the *next*
    existing gate -- it is not itself a bypass (an otherwise-admitting
    guard still lets an earlier-failing window check refuse, and the
    evidence-intake reason never appears in that refusal's `reasons`);
  - an unsatisfied record blocks `run_attempt` before any durable
    session/shared/budget mutation and before `transport.dispatch` is ever
    reached (`SyntheticExchange({})`, whose `.take()` would raise if ever
    called, backs every such test);
  - the evidence-intake gate and a bound `AttemptModelGuard` are each
    independently authoritative in both directions: an unsatisfied
    evidence-intake guard blocks dispatch even when the attempt model would
    admit (and leaves the attempt-model guard unconsumed, since it is never
    reached), and a refusing attempt-model guard still blocks dispatch even
    when the evidence-intake guard is satisfied;
  - no duplicate dispatch/retry escape: `require_admission` is
    deterministic across repeated direct calls, and the runtime's existing
    `RUNTIME_FROZEN_REQUEST_MISMATCH` contract still prevents a second
    `run_attempt` call on an already-attempted request id;
  - malformed/tampered reports (missing key, extra key, non-dict, any
    authority flag `True`, wrong `intake_schema`, inconsistent
    `satisfied`/`refusal_reasons`, non-bool `satisfied`) all fail closed at
    `EvidenceIntakeRecord` construction, never at `require_admission` time;
  - composition itself refuses a wrong-typed `evidence_intake` outright
    (`RUNTIME_COMPOSITION_SHAPE`), exactly like the existing `attempt_model`
    parameter;
  - the one sanctioned real-evidence route
    (`EvidenceIntakeGuard.from_real_intake`) round-trips a real
    `evaluate_real_evidence`/`build_report` outcome (synthetic fixture bytes
    under `tmp_path` only -- never the actual private root, matching the
    real-intake module's own established test convention) into a guard
    whose `require_admission()` behaves correctly for both a satisfied and
    an unsatisfied real checker outcome.

## What this does not do

No change to `tools/v11_gate3_evidence_preflight_real_intake.py`,
`tools/v11_gate3_evidence_preflight_checker.py`,
`tools/v11_gate3_preflight_attempt_model.py`,
`tools/v11_r09_gate3_collector.py`, `tools/v11_r09_gate3_launch.py`,
`tools/v11_r09_gate3_launch_v4.py`, or any existing test file. No change to
`tools/v11_gate3_fresh_window_readiness.py` or
`tests/test_v11_gate3_fresh_window_readiness.py` (the other parallel
worker's lane). No network, subprocess, decode, transport, credential use,
funding/order/account action, or V10/AxiomTrade/root-authority change
anywhere in the new code. `execution_authority`/`provider_authority`/
`capture_authority` remain hardcoded `False` on every record this module
can construct and are never read from, or settable by, a caller. No real
collector/launch caller is introduced that would actually construct a
`GateRuntime` against the real retained package and dispatch; this slice
only makes the enforcement point exist and proves it is correct against
synthetic fixtures. No score or gate-status change: the real retained
package remains refused exactly as before
(`docs/V11_R09_GATE3_EVIDENCE_PREFLIGHT_REAL_INTAKE_20261002.output.json`
is untouched by this slice), so the enforced prerequisite this slice adds
would itself currently block any real attempt from ever reaching Step 1 --
**91/200 (45.5%), formal 1/50; A2/A3 UNQUALIFIED; A4 OPEN; A8 UNQUALIFIED;
G3-L NO-GO; NOT_READY_TO_FUND**, unchanged.

## Verification (this batch)

- `python3 -m py_compile tools/v11_gate3_evidence_intake_guard.py
  tools/v11_r09_gate3_runtime.py
  tests/test_v11_gate3_evidence_intake_launch_wiring.py` -- clean.
- New focused suite (`tests/test_v11_gate3_evidence_intake_launch_wiring.py`)
  -- **23 passed** plain and **23 passed** under `-O`.
- Targeted combined regression (the new file plus
  `tests/test_v11_gate3_attempt_runtime_wiring.py`,
  `tests/test_v11_gate3_evidence_preflight_real_intake.py`,
  `tests/test_v11_gate3_evidence_preflight_checker.py`,
  `tests/test_v11_gate3_preflight_attempt_model.py`,
  `tests/test_v11_r09_gate3_runtime.py`) -- **829 passed**.
- Wider regression (every `test_v11_r09_gate3_*.py`/`test_v11_gate3_*.py`
  file) -- **1456 passed** both plain and under `-O` (same 2-3 pre-existing,
  unrelated `os.fork()` `DeprecationWarning`/`pytest -O` assert-mode
  warnings as prior cycles; no new warning).
- `git diff --check` -- clean.
- Disposable `--basetemp` scratch directories deleted immediately after
  each run; free disk confirmed recovered (>4 GiB, above the 2 GiB G3-L
  floor) before finishing.

## Next action

Independent exact-commit review of this slice, covering at minimum: the
exact pre-Step-1 placement of the new check inside `run_attempt` (confirm
it cannot be reached after any durable mutation and cannot be skipped once
`evidence_intake` is supplied), the closed-key-set/authority-forbidden
validation in `EvidenceIntakeRecord.from_report`, and the composition-level
type check for `evidence_intake` in `GateRuntime.__init__`. After that
review, a future collector/launch caller preparing a real (non-synthetic)
`FrozenPlan`/`GateRuntime` composition has a clear, reviewed route
(`EvidenceIntakeGuard.from_real_intake`) to bind the real retained
package's current status as an enforced prerequisite -- but building that
real caller, and any of the still-open external prerequisites the real
intake module's own report already documents (clock-calibration recorder,
physical storage reservation, GEFS/ECMWF access/restriction-domain
resolution), remain separate, not-yet-started work.
