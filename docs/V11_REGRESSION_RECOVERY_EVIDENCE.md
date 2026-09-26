# V11 regression recovery evidence — 2026-09-26, supervisor batch 2

Recovered a clean worktree on
`weather-v11-profitability-upgrade-2026-09-23`, with local and remote HEAD
`640a5d5421e625059a98aab45294756cc41829cf`, tree
`6a1554517fa4fa55fa0a825b74862fc1c2a50972`. No unfinished pytest process was
found. The authoritative master was read and its recorded SHA-256 verified.
This batch followed the checkpoint's exact next action: one full regression
after the published umask and broker-restart test fixes.

All repository operations used Remote Desktop Commander on alpha-dev. Tests
ran sequentially as foreground children of a monitored process, without shell
backgrounding or detached jobs. The previous tool's 600-second call limit did
not constrain this execution method. There was exactly one full regression.

## Source and runtime identity

The initial targeted, integration and full runs used the clean commit/tree above.
Python: `/home/alphaadmin/AlphaV11_Dev/venv/bin/python`, version 3.12.3;
pytest 8.3.3; Linux 6.8.0-137-generic, x86_64, glibc 2.39.
The subprocess environment was explicitly limited to:

```text
LANG=C.UTF-8
PATH=/usr/local/bin:/usr/bin:/bin
PYTHONDONTWRITEBYTECODE=1
PYTHONPATH=/home/alphaadmin/AlphaV11_Dev/Alpha
PYTHONUNBUFFERED=1
```

The existing offline pytest fixture remained active. A saved SHA-256 manifest
covered 788 tracked non-document inputs, excluding docs, Markdown, local agent
configuration, environment files and database/key file types. All 788 hashes,
HEAD and clean worktree state matched after each initial stage.

Only `tests/test_v11_paper_guardian.py` changed after the full run. Its final
SHA-256 is `f714a9242aafa9a266f956b33b0020401ab11b8f84c138c21a6e224f2621df59`.
The other 787 input hashes are unchanged. All 788 final hashes matched before
and after every post-fix invocation. Production source and safety gates are
unchanged. The publishing recovery commit has a different Git tree; the full
result must not be attributed to the corrected test as a passing full run.

## Commands and results

Working directory: `/home/alphaadmin/AlphaV11_Dev/Alpha`.
Raw logs, JUnit, manifests and diagnostic records remain local under
`/tmp/v11-b2-gvgbqzij/`; none is included in Git.
The common command for the initial stages and final verification was:

```text
/home/alphaadmin/AlphaV11_Dev/venv/bin/python -m pytest -q -p no:cacheprovider
  --tb=short -ra --maxfail=3
  --basetemp=/tmp/v11-b2-gvgbqzij/<stage>-tmp
  --junitxml=/tmp/v11-b2-gvgbqzij/<stage>.xml <selection>
```

| Stage | Selection | Result | pytest time | Exit |
|---|---|---|---|---|
| targeted | Two prior defect cases below | 2 passed | 3.68 s | 0 |
| integration | 17 modules below | 279 passed | 78.14 s | 0 |
| full | No selection/filter; 4764 collected cases | 4752 passed, 1 failed, 11 skipped, 4 warnings | 1245.36 s | 1 |
| stop-before-1 through -5 | Original stop case, one per invocation | 1 passed each | 0.85, 0.88, 0.79, 0.76, 0.84 s | 0 each |
| stop-after-1 through -5 | Corrected stop and unchanged kill cases, both per invocation | 2 passed each; 10 passes over five invocations | 1.24, 1.21, 1.27, 1.23, 1.15 s | 0 each |
| guardian-integration | Eight final modules below | 338 passed; no skips/warnings | 42.88 s | 0 |

The diagnostic `stop-before-*` commands used the same interpreter/environment
and output paths, with `-q -p no:cacheprovider --tb=short`; they omitted
`-ra --maxfail=3`. All exact argv arrays and wrapper wall times are in the
retained metadata. The full invocation began at 22:07:16 UTC and finished at
22:28:03 UTC; wrapper wall time was 1247.176 s. The four warnings were the
existing FastAPI `on_event` deprecations.

Initial targeted selection:

```text
tests/test_operator_executor.py::test_automatic_uses_existing_lifecycle_and_pause_is_immediate
tests/test_v11_guardian_broker.py::test_actual_broker_death_stale_socket_restart_and_receipt_replay
```

Initial integration selection:

```text
tests/test_frozen_production_review.py
tests/test_host_authority_production_boundary.py
tests/test_host_operator_roles.py
tests/test_operator_direct_stop_race.py
tests/test_operator_executor.py
tests/test_operator_notifications.py
tests/test_operator_panel.py
tests/test_operator_recovery.py
tests/test_operator_safety_priority.py
tests/test_production_frozen_fee_review.py
tests/test_production_transport_integration.py
tests/test_v11_guardian_broker.py
tests/test_weather_all_paper_deployment_identity.py
tests/test_weather_rollback_generation_freshness.py
tests/test_v11_event_queue.py
tests/test_v11_queue_admission.py
tests/test_v11_paper_runtime.py
```

Final integration selection:

```text
tests/test_v11_paper_guardian.py
tests/test_v11_guardian_integration.py
tests/test_v11_guardian_publication.py
tests/test_v11_guardian_broker.py
tests/test_v11_guardian_protocol.py
tests/test_v11_runtime_health.py
tests/test_v11_health_publication.py
tests/test_v11_liveness_health.py
```

## Exact failure reconciliation and correction

The retained 47-ID list still has SHA-256
`ddebe803792946708c486011a5c5b91e58d74e5da84c8b55c1929da39c6bb0e0`.
Every one of those IDs appears as passed in this full run's JUnit; none is
missing, failed or skipped. This verifies the earlier fixes in full-suite
context without assuming that older runs with different totals had equal inputs.

The sole full-run failure was:

```text
tests/test_v11_paper_guardian.py::test_actual_guardian_death_closes_lease_while_candidate_still_lives[stop]
```

At the original line 284, `admission(g)` did not raise `EvidenceError`.
The test sent SIGSTOP and immediately checked admission without confirming that
the kernel had stopped the guardian. `guardian_lease.process_identity()`
already rejects T/t/Z/X/x states, and `check_lease()` calls it directly.
The five isolated pre-fix passes show this failure was not deterministic.

A separate synthetic child underwent 100 stop/resume cycles. Immediately after
`os.kill(..., SIGSTOP)` returned, its kernel state was R in 14 cycles and T
in 86. All 100 subsequent `waitpid(WUNTRACED)` notifications confirmed SIGSTOP.
This demonstrates the asynchronous delivery window assumed away by the test;
the probe is test-environment evidence, not production commissioning.

The corrected test sends the same signal, then uses the existing bounded
`wait_for` helper with `waitpid(WUNTRACED | WNOHANG)`. It requires the exact
child PID, a stopped status and SIGSTOP before checking admission once.
The rejection assertion, live candidate assertion and cleanup remain intact.
It does not poll admission until a lease expires, increase a timeout, relax a
production check or change the kill case. The final paired repetitions and
338-case integration above verify this correction.

No second full regression was run: this batch had used its authorized single
full invocation. Post-fix full-suite confirmation remains open; the earlier
full result remains a failure, not a retrospectively green result.

## Unavailable custody cases and remaining acceptance

Eleven full-run skips reported
`EXTERNAL_CUSTODY_GATE_UNAVAILABLE: owner prerequisite: install the standard uidmap package (missing newuidmap)`:

- `tests/test_v11_guardian_custody.py::test_actual_mapped_principal_broker_custody_and_cancel_only_delivery`:
  `none`, `accepted`, `account`, `receipt`.
- `tests/test_v11_liveness_custody.py::test_actual_separate_principal_liveness_custody_and_recovery`:
  `healthy`, `preempt`, `transfer`, `death`, `probe_preempt`, `probe_death`, `worker_stop`.

These are unavailable proofs, not passes. Historical WSL custody evidence is
not relabeled as alpha-dev evidence. No helper/package, host policy or service
was changed. This dependency did not block the bounded test correction or
publication; no owner action was needed for this batch.

R45 remains PARTIAL. Existing C/J is strengthened, with no new C/J/E/A credit:
**85/200 = 42.5% (approximately 43%); completed requirements 1/50 (2%)**.
Independent security/operating acceptance and the remaining master gates are
open. **NOT_READY_TO_FUND**. No V10 runtime access/change, production credential,
service, deployment, wallet, funding, live order or financial-authority action.

**Exact next unfinished action:** in the next batch, run one full regression
against the published stop-synchronization fix, retaining the same input and
runtime attribution. Separately, actual custody evidence requires an already
authorized namespace-capable runner or a separately approved host prerequisite;
do not treat the skipped cases as accepted or change alpha-dev policy implicitly.

## Local artifact identities

| Local artifact | SHA-256 |
|---|---|
| `metadata.json` | `ae190553a4edd6db3fbc16ce2786b08b6e5fb053d5682c565379fa67a9b40e9b` |
| `inputs.json` | `0353ac62b70fba36d6dfb8b0df0dc37b4280f6d5b9d05bd4146bd2ba3e296ddb` |
| `full.log` | `78e4c0eb550d934a04759872ee26e110e495063fe32ee620a2cf41ea5e8858a7` |
| `full.xml` | `06b8273326ac8bf1a4df7241ec360cfb8c36b8357e84886b63fd4a82d5eeb55e` |
| `reconciliation.json` | `01e1fd3d6c312f34a6bc4bbc26266a64c901fb806d25117a6ac8574c1db01b58` |
| `stop-before.json` | `795fbf711fa91a50cfad91c937a3efda39855168139ade5f716c030127258292` |
| `sigstop-probe.json` | `136d1195d5ae15e4b571eeb0f06967664a21da8220df0221227ad8109b677eb1` |
| `final-inputs.json` | `4cb1e97adc35ed5b7535b49e0e51a4ea548ade519eb9e35089a6ffd6dbdbcad7` |
| `guardian-stop.patch` | `24d48dee76b436ed92a76b7a942d61a662534c2bab011793ae006f7b3f691b04` |
| `final-metadata.json` | `fcf5cc49683f884753bd330460c6ca8ac4cf93023d30cc0d4e970317c4abdf02` |
| `guardian-integration.log` | `7e9ddd2fd8c959b2882141e8d1099237b1cd952edb387fbb801b8d09f42e5949` |
| `guardian-integration.xml` | `698418c5a6e426cf8f3b53eb87473e96d66a1a8396eded3630c13364af6f5554` |

The metadata also retains hashes for each focused/integration log and JUnit
file. Resolve the publishing commit separately with
`git log -1 --format='%H %T' -- docs/V11_REGRESSION_RECOVERY_EVIDENCE.md`;
the source commit above deliberately identifies the actual full-run input.
