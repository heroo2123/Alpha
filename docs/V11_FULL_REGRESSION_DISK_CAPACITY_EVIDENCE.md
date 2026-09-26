# Full regression confirmation and disk-capacity root-cause — 2026-09-26 (supervisor batch 3)

Source/runtime identity: local and remote HEAD `5b16f4f535025b12994733c742563538d8fcb317`
on `weather-v11-profitability-upgrade-2026-09-23`, clean working tree throughout.
Python `/home/alphaadmin/AlphaV11_Dev/venv/bin/python` 3.12.3; pytest 8.3.3;
Linux 6.8.0-137-generic, x86_64, single vCPU host. No production source, test, or
safety-gate file was changed in this batch.

## Root cause of the previously unexplained 47-failure full-suite runs

The prior checkpoint entry ("Partial reconciliation of the 47 recorded full-suite
failures") left open why a ten-file isolated comparison could not reconcile the
full-suite failure count against a nine-file stash-based comparison. Investigating
this directly: `tests/test_operator_recovery.py` (part of the same recorded failure
groups but omitted from that ten-file selection) passed cleanly in isolation
(10 passed), so the file-selection gap was not itself explanatory.

Running the complete union of all 14 files that had ever appeared in a recorded
failure list (`test_frozen_production_review.py`, `test_host_authority_production_boundary.py`,
`test_host_operator_roles.py`, `test_operator_direct_stop_race.py`,
`test_operator_executor.py`, `test_operator_notifications.py`, `test_operator_panel.py`,
`test_operator_recovery.py`, `test_operator_safety_priority.py`,
`test_production_frozen_fee_review.py`, `test_production_transport_integration.py`,
`test_v11_guardian_broker.py`, `test_weather_all_paper_deployment_identity.py`,
`test_weather_rollback_generation_freshness.py`) together at current HEAD passed
cleanly: **210 passed / 74.12 s**, exit 0.

A first full-suite chunk (99 files, alphabetically split, `split -n l/4`) then
failed with **141 failed, 1248 passed**, far beyond any previously recorded count.
Isolating one failure (`test_production_submission_boundary.py::test_boundary_real_thread_queue_permits_stop_before_post`)
reproduced deterministically alone (0.63 s). Direct inspection of
`ExecutionEngine.base_authority_reason()` (`polymarket_scanner/production/engine.py`)
showed the actual gate name: `STORAGE_CAPACITY_OPENING_STOP`, produced by
`check_storage()` in `polymarket_scanner/production/storage_health.py`, which
compares `os.statvfs()` usage against `STOP_PERCENT = 85`. `df -h /` showed the
host filesystem at **86% used** (20G volume, 2.8G free) at that moment. This is a
genuine, correctly functioning safety gate reacting to real host disk pressure —
not a code or test defect. It affects any test that constructs a real
`ExecutionEngine`/config pointing at the host filesystem, which is why large
combined selections showed far more failures than small ones: enough concurrent
pytest `tmp_path` usage pushed real usage over the 85% stop line mid-run.

`du -xh --max-depth=1 /tmp` identified two large, disposable, non-git directories
driving the usage: `/tmp/pytest-of-alphaadmin` (1.8G; confirmed by `stat`/`find`
timestamps to be exclusively today's own pytest `tmp_path` scratch, `pytest-154`
through `pytest-197`) and `/tmp/v11-b2-gvgbqzij` (1.7G; the batch-2 raw evidence
directory explicitly documented in `docs/V11_REGRESSION_RECOVERY_EVIDENCE.md` as
"local, not included in Git" — its SHA-256 identities are already durably recorded
in that committed document's artifact table, so deleting the raw local copy drops
no committed claim). A third large directory, `/tmp/pharma_visual` (1.1G, ~7 days
old per `stat`), is an unrelated, out-of-scope project on this shared host and was
left untouched.

Removing the two disposable directories (`rm -rf /tmp/pytest-of-alphaadmin
/tmp/v11-b2-gvgbqzij`) dropped host usage from 86% to 68% (13G/20G, 6.2G free).
Re-running the identical chunk selection that had just failed 141/1248 then passed
cleanly: **1389 passed, 4 warnings / 201.79 s**, exit 0 — same files, same HEAD,
only the host disk state changed. This confirms disk capacity, not source code,
was the actual cause. No V10, credential, private-input, or git-tracked file was
touched by this cleanup; only ephemeral pytest scratch and already-hash-preserved
local (non-git) evidence were removed.

## Confirmed clean full-suite regression

With the host below both `WARN_PERCENT` (70) and `STOP_PERCENT` (85), the complete
`tests/test_*.py` collection (4764 collected, verified via `pytest --collect-only`)
was run as four sequential foreground chunks (alphabetical `split -n l/4` of the
353 test files: 99/101/75/78 files respectively), clearing
`/tmp/pytest-of-alphaadmin` between chunks to keep usage bounded. No chunk used
background execution, `&`, `nohup`, or detached shells; each ran to completion
before the next began.

| Chunk | Files | Result | Time | Exit |
|---|---|---|---|---|
| 00 (retry, post-cleanup) | 99 | 1389 passed, 4 warnings | 201.79 s | 0 |
| 01 | 101 | 2231 passed, 11 skipped | 1024.38 s | 0 |
| 02 | 75 | 544 passed | 24.75 s | 0 |
| 03 | 78 | 589 passed | 21.65 s | 0 |

Aggregate: **4753 passed, 11 skipped, 0 failed** = 4764, matching the collected
count exactly. The 11 skips are the existing `EXTERNAL_CUSTODY_GATE_UNAVAILABLE`
cases (missing `newuidmap`), unchanged and unavailable rather than passed. The
four warnings are the existing FastAPI `on_event` deprecations. HEAD remained
`5b16f4f535025b12994733c742563538d8fcb317` and the working tree stayed clean
(`git status --porcelain` empty) throughout; no source, test, or config file
changed. Raw per-chunk logs remain local under `/tmp/fullrun_chunk*.log`
(not committed).

This is the first fully clean (zero-failure) full-suite confirmation recorded on
this branch. It verifies, in full-suite context, the guardian-stop synchronization
fix (5b16f4f), the guardian-broker inode-reuse fix (640a5d5), and the umask fix
(128bd95) together, and closes the "post-fix full-suite confirmation" action left
open by two prior checkpoint entries. It also retroactively explains — without
attributing a new code defect — the 47-failure full-suite run recorded in the
"Event-queue census-only starvation fix" checkpoint entry: that run's exact 47
IDs are the same set analyzed here, and the disk-capacity condition (not the
event-queue change) is now understood to be sufficient to produce that pattern.

## What this does not establish

No new C/J/E/A milestone is credited: this is full-suite verification of already-
credited fixes, not new implementation, integration, or acceptance. The eleven
unavailable custody cases still require an owner-approved `newuidmap` install or
namespace-capable runner. Independent security/operational acceptance, isolated
deployment, and every other open R43-R49 gate remain unearned. No V10 runtime
access/change, production credential, service, deployment, wallet, funding, live
order, or financial-authority action was performed or requested.

**Total: 85/200 (approximately 43%); formal 1/50 (2%), unchanged. NOT_READY_TO_FUND;
V10 unchanged/DEFERRED.**

**Exact next unfinished action:** continue closing other PARTIAL requirements
end-to-end (per the checkpoint's standing priority order); separately, periodic
`/tmp/pytest-of-alphaadmin` cleanup before large test batches is recommended
process hygiene to avoid recurrence of the disk-capacity condition documented
above, and actual custody evidence still needs an owner-approved `newuidmap`
prerequisite or already-authorized namespace-capable runner.
