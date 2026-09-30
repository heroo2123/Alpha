# SHADOW repair acceptance adjudication — dd18e38

Date: 2026-09-30. Reviewer: GPT-6 Astra/high. Verdict: **CHANGES_REQUIRED**.
Target commit: `dd18e3800003a23e0a6bd005b9dd6dc6c8116d1d`;
tree: `8a1669873612eb815c24b21469811e4d5113b478`.
Main at adjudication: `79177470d26c8d413fe624fd8e8d7ed7783e49d1`.
The repair remains unmerged. This decision supersedes the unresolved
report/terminal conflict and the earlier report's non-blocking classification
of P3-a/P3-b. It does not modify either original review artifact.

## Report versus terminal

The Opus report is substantive independent review evidence, not an empty or
fabricated PASS. Its driver writes its terminal verdict by searching only
`worker.log` for a final console marker; it does not read `review.md`.
The reviewer wrote the report, then exited 1 with a session-limit message and
no console verdict. Consequently `SHADOW_REPAIR_REVIEW_INCOMPLETE` truthfully
records incomplete worker termination, while the file records the reviewer's
written opinion. Neither artifact should be rewritten or silently preferred.
The report is usable evidence, but independent merge acceptance is **not
established**, because two of its observations reveal contract defects below.

## P2: ordinary configuration drift passes an exact-cohort attestation

`candidate_cohort.py:96-107` snapshots worker `config` digests but omits the
current queue policy and census book policy; the stored constructor digests
do not change when these attributes are reassigned. The policies are actively
used by the runtime (including book-age validation). Independent reproductions
replace either policy after the wrapper is constructed, then assert that
preflight returns both `passed=True` and `runner_binding_verified=True` and
that `run_once` performs mocked collection and records a linked run.

This is a configuration-provenance defect, not a claim that Python object
inspection can resist hostile code in the interpreter. The documented repair
promises to re-read changed cohort configuration before work, and already
supports this exact kind of mutation detection for other policy/plan fields.
The arbitrary-code disclaimer does not make this positive attestation accurate.
P3-a is therefore a residual P2 binding defect for merge acceptance.

Repair: bind current queue/book policy values, and audit the analogous
behavior-bearing PWS `policy`/`without` fields and request-factory settings.
Use explicit typed values or recomputed descriptions rather than trusting
stale cached digests. Preserve ordinary assembly behavior and do not introduce
an arbitrary-code security claim. Add tests asserting refusal before collection
when these values differ from the registered cohort.

## P2: advancing-clock replay prevents bounded lifecycle recovery

`shadow_commission.py:317-346` reuses a candidate run key but unconditionally
rewrites the result audit row; repeated refusals have the same problem.
`EvidenceStore.audit` includes the current timestamp in the immutable body,
so the existing ID with a different timestamp raises `RECORD_ID_CONFLICT`.
The frozen-clock idempotency test does not cover this ordinary condition.

Independent reproduction: finish `restart:iteration:0`, advance the clock one
second, and call `run_bounded('restart')` for a two-iteration lifecycle. It
raises `RECORD_ID_CONFLICT` on the completed first iteration and never reaches
the second. There is no duplicate collection or false forward count; nevertheless
normal interrupted lifecycle recovery is broken. Repeating an unchanged refusal
also substitutes a record conflict for the intended preflight error. P3-b is
therefore P2 operational correctness, not merely diagnostic presentation.

Repair: preserve immutable existing matching audit rows after validating their
kind, channel, configuration and semantic details. Do not suppress unrelated
record conflicts or skip current eligibility/freeze checks. Cover advancing-clock
completed replay, bounded restart, repeated preflight and freeze refusals, and
changed authority/configuration still refusing work. No duplicate work, changed
history or additional forward credit is allowed.

## Remaining P3 dispositions and release evidence

- P3-c: overlapping refused/completed buckets are acceptable attempt-level
  history; they must not be summed as mutually exclusive counts. The original
  reproduction used distinct configurations and does not prove a same-config
  defect. No new blocker is inferred.
- P3-d: strict manifest/certification freeze is conservative and acceptable.
  A new reviewed plan is required after identity changes; do not relax this gate.
- P3-e: bounded status scan cost is an operational concern, not an acceptance
  defect demonstrated by this review. No load experiment was needed here.
- P3-f: non-GEFS feature-width mismatch fails closed downstream. It remains a
  compatibility limitation; no claim of successful multi-model commissioning
  or source readiness follows from preflight alone.

The Opus affected set remains **395 passed, 2 failed**. Two isolated reruns
passing do not turn that suite green or prove absence of an interaction.
The failures are consistent with the known scheduler timing issue, and the
isolated release branch contains test-clock changes, but neither that branch
nor a combined release is accepted. Its previous full suite remains **5,330
passed, 12 skipped, 2 failed**; the GEFS source-view deadline failure remains
open. No full suite was rerun for this adjudication.

## New independent evidence and bounded repair handoff

Four executable reproductions in
`/tmp/alpha-v11-shadow-adjudication-dd18e38/test_adjudication.py` passed in
**6.33 seconds** against the unchanged detached repair tree. They assert observed
defects, so these passes support CHANGES_REQUIRED, not implementation acceptance.
All use existing synthetic protected-reader fixtures, temporary stores and
MockTransport. No commissioning CLI or real network source was used.

Command (from the detached repair tree):

```sh
PYTHONPATH=/tmp/alpha-v11-shadow-repair-review-dd18e38/Alpha:/tmp/alpha-v11-shadow-repair-review-dd18e38/Alpha/tests /home/alphaadmin/AlphaV11_Dev/venv/bin/python -m pytest -q -p no:cacheprovider --tb=short /tmp/alpha-v11-shadow-adjudication-dd18e38/test_adjudication.py
```

SHA-256 evidence pins:

| Artifact | SHA-256 |
| --- | --- |
| New reproduction file | `e21b9742602ef2cf5fa90bed9e2d8eec045ec9a8cc9604283d4f4a30c939b9a1` |
| New `repro.log` | `1e69360fd4b73e9cec61f6f374af3e1964f14ced9fcfeaa4574979e17cbb2805` |
| Original Opus `review.md` | `b0e82efcc2188801cca1d752a8e1b9e54db262987f9e8b618f39a3e7f4a822f9` |
| Original Opus `terminal.json` | `b365e3e4956f59f6155317a572404a33f39d0bff2d398aff6482669db81171ff` |

Next task is substantive implementation: repair these two P2s in the existing
clean `/home/alphaadmin/AlphaV11_ShadowCommission/Alpha` worktree, preserve its
history, run focused and affected tests, then obtain fresh independent review
of the exact resulting commit. No duplicate repair/review worker was launched
by this adjudicator; the coordinator router receives the implementation handoff.
Do not merge older branch ledger history over newer main. The release-diagnosis
branch remains separate at `26056af`, including `e35cbfc` and `f1462c7`.

## State and safety

PAPER scanner PID 514629 remains active with zero restarts. Queried V11 controller
and execution units are inactive; protected authority paths remain absent.
No service or protected state changed. The private FINAL-REVIEWED master hash
still matches `a0e16d9bd7344c943a54a16a53c6757662363d93642f6e5cb7953cd047659b4a`.
Current evidence directory updates are watchdog heartbeats; no new forward
qualification was found. Host headroom at recovery: 4.4 GiB disk available,
1,061 MiB available memory and 1,593 MiB free swap. Newer R09/native-extrema
integration and all unfinished branches are preserved.

No push attempted: the prior automatic approval rejection of the GitHub
destination remains binding. **91/200 (45.5%), formal 1/50; NOT_READY_TO_FUND**.
No C/J/E/A, calibration, owner acceptance or forward evidence is awarded.
