# Independent SHADOW commissioning review — 2026-09-30

Verdict: **CHANGES_REQUIRED; do not integrate `4557904` or commission it.**
GPT-6 Astra independently reviewed Sonnet commit
`4557904bec339298205372ea57b39c22942c403c`, tree
`c86a66b97c6e33aa9d5206e9e5b0105f18ac95bd`, in the unchanged, clean
`/home/alphaadmin/AlphaV11_ShadowCommission/Alpha` worktree. This review
resolves the coordinator's scope/event-binding escalation with executable
reproductions. Existing runtime admission gates still protect their own
paths; the findings invalidate this wrapper's claimed commissioning and
forward-evidence boundary, not a demonstrated financial execution bypass.

## Blocking findings

1. **P1 — The frozen plan is not bound to the runner's actual cohort.**
   `shadow_commission.py:332–347` checks only namespace and worker ID;
   `run_once` then invokes the supplied runner without comparing its event
   routes, rules, strategy scopes, units or model bindings with the plan.
   A real CandidateRunner configured for KATL passes a KJFK-only plan and
   executes. The plan does not even carry exact event/rule identities.
   Hashing both unrelated configurations together does not establish equality.
   Repair through the existing typed candidate assembly boundary: expose/bind
   an immutable, verifiable cohort contract covering events, rules, scopes,
   units, bundle/release identities and applicable workers; reject extra,
   missing or changed bindings before any work. Do not trust a second
   caller-supplied description unrelated to the actual runner.

2. **P1 — Preflight accepts incompatible or ineligible protected state.**
   `shadow_commission.py:286–296` treats a successful model pin as sufficient
   and only checks that a station manifest contains the scope name. A C
   fixture bundle with a different model_version passes an F target. A real
   certification fixture changed to an expired review plus a model demoted
   to zero size/manual review also passes. Existing StrategyAdmission rejects
   that expired certification correctly, demonstrating the gap is in this
   preflight. Validate the current exact station/rule certification and model
   overlay through existing validators. Bind and verify the reviewed bundle,
   epoch/state digest, model_version, family/unit and actual forecast feature
   contract (including the R47 GEFS identity/member/day semantics). Detect
   changes before each run; retain these identities in evidence. Do not
   install protected state or weaken downstream admission to make this pass.

3. **P1 — Admission timestamps are mislabeled as forward samples.**
   `shadow_commission.py:416–426` counts every REGISTRY row at the admission
   event whose write time is after plan.created_at. Ten genuine synthetic
   StrategyAdmission pins of the same event and source satisfy target=10,
   with zero commissioning runs. A different plan also receives those samples.
   Neither a recent write nor a backdateable plan timestamp proves a new
   forward input/decision. Require durable pre-run freeze provenance and
   exact plan/config/release/bundle/run linkage, actual eligible decision or
   prediction evidence, live causal source provenance and an explicit grouped
   sample identity. Exclude synthetic, replay, historical and unbound rows.
   Repeated pins/snapshots of one outcome must not become independent samples.
   Until supported, report qualifying evidence as unavailable/zero with a
   reason; do not relabel admission traffic as forward acceptance.

4. **P2 — Refusals count as forward commissioning runs.**
   `shadow_commission.py:427–433` counts every wrapper status row by time,
   including PREFLIGHT_FAILED. A release-mismatched invocation that never
   starts CandidateRunner produces forward_commission_runs_recorded=1.
   Filter and deduplicate records by exact plan/config and valid linked run;
   distinguish attempts, refused/degraded/completed runs and evidence-qualified
   runs. A completed tick alone is still not a forward sample.

5. **P2 — Status silently truncates history.**
   `shadow_commission.py:418–419,427` reads only the first bounded page because
   EvidenceStore.records orders by ascending seq. After 64 pre-freeze records
   (target=1), a newer post-freeze record is invisible. The run counter has
   the same issue after 1,000 rows. Use bounded pagination or a versioned
   incremental aggregate; expose incomplete coverage rather than claiming
   complete totals from a truncated prefix.

## Independent verification

The original 21 SHADOW tests passed. Four independent defect reproductions
passed alongside them: **25 passed / 15.21 s, exit 0**. Two additional focused
reproductions (expired/demoted state and pagination) passed: **2 passed,
4 deselected / 3.47 s, exit 0**. A passing reproduction asserts the observed
incorrect behavior; it is not an acceptance test or a product PASS.

All tests used temporary synthetic stores, MockTransport where needed, and
monkeypatched protected-state readers. No protected files were provisioned.
Source and branch remained unchanged. No duplicate full suite was started.
Local reproduction directory: `/tmp/alpha-v11-shadow-review-4557904/`.

- `test_review_findings.py` SHA-256:
  `b8f4549e92641a08c8600f2f6f41da16a2fb0aa3d7bbd1152851e624cb40a673`.
- `pytest.log` SHA-256:
  `a69dda350e2b872055e92272bbd0fd0b52d4c7dcca7c3fddba96cb894e3416c6`.
- `supplement.log` SHA-256:
  `0e21551b2506eb4859bbd1060a8927b98a5f2fcf37af5d7fb0cbf95ec035666f`.

Reproduce from the SHADOW worktree with its root and tests directory on
PYTHONPATH, using `/home/alphaadmin/AlphaV11_Dev/venv/bin/python -m pytest
-q -s -p no:cacheprovider --tb=short` on the original test file and the local
review test file. Convert the defect reproductions into rejection/exclusion
regressions during repair; add a positive typed-assembly test that proves
binding, and reviewed live-shaped synthetic lineage tests without claiming
those fixtures are genuine forward evidence.

## Continuation and host observations

Normal substantive repair belongs in the existing isolated SHADOW worktree,
followed by independent review of the repaired commit before integration.
Keep all newer main changes, especially reviewed R09 native-extrema work.
The SHADOW branch's stale assertions that no compatible R47 bundle exists,
that local work is exhausted, and that root installation is the next sole
step are superseded by newer main and this review. Fixing these defects is
unblocked local work; real commissioning remains owner/root gated.

At 09:43 UTC the existing scheduler full-suite retry (pytest PID 723867)
was still running around 36%, without a terminal result. Preserve
`/tmp/alpha-v11-scheduler-full-suite-retry.{log,terminal}` and inspect its
actual completion; do not duplicate the run or claim release PASS. The
existing coordinator PID 716927 owns routing; no second coordinator or
implementation worker was launched during this review.

PAPER scanner PID 514629 remained active with zero restarts. The controller
was inactive/disabled and execution service inactive/masked; no service was
changed. Protected model-authority paths remained absent. Available disk
was 3.8 GiB, memory about 635 MiB plus 1.3 GiB free swap at inspection.
Commissioning directory updates were watchdog heartbeats, not new forward
samples. The private FINAL-REVIEWED master's SHA-256 still matched
`a0e16d9bd7344c943a54a16a53c6757662363d93642f6e5cb7953cd047659b4a`;
its current-live-input and grouped city-day evidence requirements informed
this review. No private contents or artifacts were copied into this report.

No C/J/E/A boundary crossed: **91/200 (45.5%), formal 1/50;
NOT_READY_TO_FUND**. Existing GitHub destination approval block remains;
no publication attempted and no alternate route used.
