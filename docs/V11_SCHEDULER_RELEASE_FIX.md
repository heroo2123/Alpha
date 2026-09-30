# V11 candidate scheduler release investigation — 2026-09-30

Base: `fd59e667bc948445e102241ace77653e1c3f0fe2`.
Branch: `v11-candidate-scheduler-release-fix-20260930`.

## Finding and scope

The reported missing DRIFT result is compatible with the finite runner's
documented budget, not evidence that the round-robin cursor lost work. The
original integration fixture required four jobs within five real seconds.
SQLite/fixture cost and CPU scheduling could exhaust that budget before the
fourth job. A baseline run in this checkout passed 48/48, consistent with the
reported intermittent failures; this investigation did not reproduce the
previous full-suite failure deterministically.

`CandidateRunner._run` persists `next_kind`, sequence and active command before
dispatch. The next invocation continues that cursor, and an interrupted command
retains its exact identity for recovery. Replaying a completed run ID intentionally
does not renew work. Job count, elapsed time and safety-tick limits are independent;
`maximum_jobs=4` is an upper bound, not a promise to complete four workers.

DRIFT is safety-relevant: it measures an evidence cohort and can apply a reviewed
scoped reduction. Its measurement, immutable recovery, current-review/model
checks and guarded demotion remain real in these tests. Runtime ticks revalidate
resting admissions and dispatch cancellation after a reduction. Existing operator
polling and runtime safety ticks have priority over ordinary worker dispatch.
There is no demonstrated requirement that every optional measurement complete
within every finite invocation. Moving DRIFT first would also reinterpret saved
numeric cursors unless a migration preserved their meaning; repeatedly reserving
a priority job in short runs could starve the other workers.

The checked-in master mapping was reviewed for R05 (markouts), R33 (bounded
event work), R37 (independent guardian) and R42 (reviewed drift/lifecycle), together
with `V11_RUNTIME.md` and the scheduler, drift and runtime safety implementations.
The private master identified by `V11_INPUT_MANIFEST.json` is absent from this
checkout. Its original contents were not directly reverified; no claim of full
master compliance or operational safety acceptance is made by this fix.

Production scheduling is unchanged. The defensible narrow correction is to
control scheduling time in the integration tests, while retaining separate
real-time timeout, blocked-collection, operator-priority and recovery coverage.

## Deterministic coverage

The opt-in `candidate_clock` fixture gives the candidate and asyncio timers a
shared virtual monotonic clock. It advances to the next timer only when ready
coroutines have drained. Host contention and synchronous fixture/database cost
therefore cannot consume the candidate's scheduling budget. The shared stdlib
clock, worker-internal deadlines, evidence timestamps and health clocks are not
patched. This fixture is only for mocked transports; it is not a latency test.

Both fill-receipt variants still require exactly one
`SCOPED_SAFETY_REDUCTION_APPLIED`, actual cancellation, unchanged cash/lots,
reconciliation and audit inclusion, and false financial/order authority. The
similar explicit-cohort candidate test also uses the controlled clock.

Two added variants inject a CENSUS delay longer than a 0.1-second run budget,
with a one-job limit. They require budget termination, drained cancellation,
the persisted active command and cursor, then reconstruct the runner and execute
four bounded continuations. The exact interrupted command must resume, and the
completed sequence must be CENSUS, DISCOVERY, AUDIT, DRIFT. They retain every
measurement/cancellation/inventory/audit assertion, check the count/time bounds,
and check that completed-run replay leaves the candidate head unchanged.

This proves finite continuation and worker fairness for the reproduced scheduling
condition. It does not promise progress without subsequent invocations, a healthy
clock, or recoverable workers, nor hard real-time execution under arbitrary load.

## Verification

All tests are offline foreground invocations from the authorized checkout using
Python 3.12.3 / pytest 8.3.3 and the existing development interpreter, with
`PYTHONDONTWRITEBYTECODE=1` and `-q -p no:cacheprovider`.

- Unmodified fill-markout baseline: 48 passed in 46.40 seconds.
- Focused corrected fill candidate variants and queued drift candidate: 5 passed
  in 41.24 seconds.
- Requested families (`test_v11_fill_markout.py`, `test_v11_candidate_runner.py`,
  `test_v11_candidate_assembly.py`, `test_v11_drift_runtime.py`): 168 passed in
  199.55 seconds.
- Related `test_v11_drift.py`, `test_v11_calibration_drift.py`,
  `test_v11_realized_drift.py`, `test_v11_markout_drift.py`,
  `test_v11_paper_runtime.py`, `test_v11_paper_cancellation.py`,
  `test_v11_lifecycle_runtime.py` and `test_v11_audit_reports.py`: 205 passed in
  161.32 seconds. These cover reduction policy/fairness, interruption/recovery,
  enforcement and reporting beyond the changed integration tests.
- Two further foreground repetitions of the complete corrected fill-markout
  file: 50 passed in 69.04 seconds; 50 passed in 66.79 seconds. Together with the
  requested-family run, the corrected file passed three complete invocations.

All post-change runs exited zero with no skips or warnings. The full repository
suite was not rerun; these results do not replace that release gate. The final
diff passes `git diff --check`; manual private-content review and a scan for
private-key/token/credential-URL patterns found no private material. Changes are
limited to the opt-in test fixture, two integration-test files and this note.

No production module, service, protected state, credentials, live execution or
financial authority is changed. Test stores and synthetic accounts are temporary.
