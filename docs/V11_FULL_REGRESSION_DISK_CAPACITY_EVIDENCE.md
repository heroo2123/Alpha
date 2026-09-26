# Complete-collection regression and disk-capacity incident — 2026-09-26 (supervisor batch 3)

Batch 3 reported source commit `5b16f4f535025b12994733c742563538d8fcb317`,
tree `41441ca6566481839164ed6e53920514952d446f`, on
`weather-v11-profitability-upgrade-2026-09-23`. Its publishing commit,
`ead5354dea1bdb4c727f14a0a2dc0fed1be76778`, changed only this document and
the three ledgers. No production source, test or safety gate changed.

Independent Codex review of that publication corrected the evidence claims below.
The reported passing results are retained; historical failure attribution,
artifact preservation and whole-session equivalence are not inferred from them.

## The disk incident is separate from the earlier 47-failure cohort

Batch 3 recorded an isolated operator-recovery run (10 passed) and the same
14-module cohort already exercised by batches 1 and 2 (210 passed / 74.12 s).
These repeat checks do not newly reconcile historical runs with unmatched inputs.

The retained first chunk log records **141 failed, 1248 passed, four warnings /
185.02 s** and explicitly contains `STORAGE_CAPACITY_OPENING_STOP` errors.
Batch 3 reported reproducing
`tests/test_production_submission_boundary.py::test_boundary_real_thread_queue_permits_stop_before_post`
alone, inspecting the engine's actual denial reason and observing host disk usage
at 86% with `df -h /`. After cleanup it reported 68%; the retained retry log
records **1389 passed, four warnings / 201.79 s** for the same chunk.

This supports a disk-capacity incident in that chunk. The unchanged production
guard in `production/storage_health.py` uses `os.statvfs()`, specifically
`(f_blocks - f_bavail) / f_blocks`, warning at 70% and stopping openings at 85%.
It includes blocks unavailable to the service user; a rounded `df` percentage
is not the exact guard measurement. The historical disk readings and diagnostic
probe are batch-reported observations, not measurements independently repeated
by this review. No pressure was generated and no gate was bypassed during review.

This does **not** establish disk capacity as the cause of the older 47 failures:

- The retained older log, `/tmp/v11-full-regression-20260926.log`, has SHA-256
  `09c867f938d248d4bcc7aedd607f7876152bbe81f984f9c8dac085e5660c9c0d`
  and records 47 failed / 4700 passed / 11 skipped / 1124.76 s. Its failures
  include `AUTHORITY_GIT_REPOSITORY_CUSTODY_INVALID` and the broker restart's
  inode-reuse assumption. Batch 1 diagnosed and corrected fixture umask and
  broker-test defects; the retained focused logs show 46/1 after the umask fix,
  then 47/0 after the broker fix.
- Only **37** of those 47 IDs occur in the new 141-failure chunk. The failures
  are not an identical set, and overlapping failed assertions do not prove
  identical causes.
- The older 4701-pass / 1113.73-second report and 29-failed / 105-passed stash
  comparison still lack matched source/collection evidence. Their relationship
  remains UNKNOWN, as the earlier independent review recorded.
- Batch 2 reported all 47 retained IDs passing in its single full invocation,
  followed by a distinct SIGSTOP-delivery test race and its synchronization fix.
  Batch 3 does not replace those separate diagnoses.

## Passing coverage of the complete collection

The four retained passing logs agree with the original batch's totals:

| Chunk | Files | Result | pytest time | Reported exit |
|---|---|---|---|---|
| 00, retry | 99 | 1389 passed, 4 warnings | 201.79 s | 0 |
| 01 | 101 | 2231 passed, 11 skipped | 1024.38 s | 0 |
| 02 | 75 | 544 passed | 24.75 s | 0 |
| 03 | 78 | 589 passed | 21.65 s | 0 |

Aggregate: **4753 passed, 11 skipped, 0 failed** = 4764. Independent collection
checks at the unchanged code snapshot verified that the saved
`/tmp/chunk_00` through `/tmp/chunk_03` lists cover all 353 tracked test modules
and all **4764 distinct default-collected node IDs exactly once**. The four
collections contain 1389, 2242, 544 and 589 cases respectively.

Chunk 01 explicitly reports four guardian-custody and seven liveness-custody
skips for `EXTERNAL_CUSTODY_GATE_UNAVAILABLE` / missing `newuidmap`. They are
unavailable proofs, not passes. The four warnings are FastAPI deprecations.

This is passing complete-collection coverage across **four pytest sessions**.
It verifies the corrected tests within their chunks, but does not exercise
cross-chunk session state/order effects as one uninterrupted invocation would.
Earlier clean full regressions are already recorded on this branch, including
4549 passed in `docs/V11_HEALTH_PUBLICATION_EVIDENCE.md`; this is not the
branch's first zero-failure regression.

The original batch reported sequential foreground execution, unchanged HEAD and
a clean tree. The retained terse logs and file lists confirm summaries and the
recoverable selection, but contain no at-run commit/argv/input-hash manifest or
per-case passing JUnit record. Current collection verification cannot recreate
that missing historical provenance. Exact original commands are not claimed.
These results strengthen R45; they do not establish final integrated acceptance.

## Artifact loss and scratch retention

Batch 3 reported removing `/tmp/pytest-of-alphaadmin` and
`/tmp/v11-b2-gvgbqzij` to recover capacity. The latter directory contained the
batch-2 logs, JUnit, input manifests and diagnostic metadata referenced by
`docs/V11_REGRESSION_RECOVERY_EVIDENCE.md`; it was absent when this review began.
The committed summaries and hashes remain, but **hashes do not preserve or
reconstruct deleted content**. Independent reinspection of that raw bundle is
no longer possible from the recorded path. Its loss must not be described as
harmless disposal of fully preserved or superseded evidence.

Keep fixture scratch separate from logs/manifests. For future batches, use an
exact run-owned `--basetemp`, check capacity and confirm the worker has finished
before removing that scratch. Preserve evidence artifacts separately; being
outside Git or having a recorded hash does not make them disposable. This review
deleted no files and performed no V10, service, credential or financial action.

## Independent review verification

Reviewed clean `ead5354dea1bdb4c727f14a0a2dc0fed1be76778`, tree
`c43052403c5cb23b8f8c40ecfb0126246ec16089`, against its stated predecessor.
Local/remote HEAD matched and no Claude worker was active. All operations used
Remote Desktop Commander on alpha-dev, sequentially without agents.

Interpreter: `/home/alphaadmin/AlphaV11_Dev/venv/bin/python`, Python 3.12.3,
pytest 8.3.3; Linux 6.8.0-137-generic, x86_64, glibc 2.39.
Working directory: `/home/alphaadmin/AlphaV11_Dev/Alpha`.
The test subprocess environment contained only `LANG=C.UTF-8`,
`PATH=/usr/local/bin:/usr/bin:/bin`, `PYTHONDONTWRITEBYTECODE=1`,
`PYTHONUNBUFFERED=1` and `PYTHONPATH` equal to that working directory.
The existing offline fixture stayed active. Actual storage unavailability
before the run was 69.22%, measured with the guard's formula.

Evidence directory: `/tmp/v11-codex-b3-d4lw9l7g` (local, not committed).
Collection commands used the interpreter above with
`-m pytest --collect-only -q -p no:cacheprovider`, first with no selection and
then each exact saved chunk list. All five exited 0; the unique-ID union matched.

The focused integration command used that interpreter with
`-m pytest -q -p no:cacheprovider --tb=short -ra --maxfail=3`,
`--basetemp=/tmp/v11-codex-b3-d4lw9l7g/integration-tmp`,
`--junitxml=/tmp/v11-codex-b3-d4lw9l7g/integration.xml`, and:

```text
tests/test_production_storage_health.py
tests/test_production_submission_boundary.py
tests/test_operator_executor.py
tests/test_operator_recovery.py
tests/test_v11_guardian_broker.py
tests/test_v11_paper_guardian.py
tests/test_v11_guardian_integration.py
tests/test_v11_guardian_custody.py
tests/test_v11_liveness_custody.py
```

Result: **166 passed, 11 skipped / 51.89 s, exit 0**, with the same unavailable
custody gates. All 775 selected tracked code/configuration input hashes, HEAD
and clean worktree state stayed unchanged during verification. This covers the
storage boundary, operator/submission integration, broker restart and guardian
stop/kill behavior. No test or production change was needed; no full suite was
rerun for this documentation correction.

## Retained artifact identities

Paths below are local. Hashes identify retained artifacts; they are not backups.

| Artifact | SHA-256 |
|---|---|
| `/tmp/fullrun_chunk00.log` | `416966ee2b34372c3faa515be1bea313adbc12690299e37bd2ad6b3b1c844c76` |
| `/tmp/fullrun_chunk00_retry.log` | `6fa3db39dc5e933e0250db12e767ee73edaa6eeec391d0d18118d251bd71f1f5` |
| `/tmp/fullrun_chunk01.log` | `a739b704c5d73965282536c8cdcf2c80aceece93149c43ca9c575003b6598b99` |
| `/tmp/fullrun_chunk02.log` | `37ce89a6589db94860f2261c666a026f5972310e87570536d7c0b4aaca92f9f1` |
| `/tmp/fullrun_chunk03.log` | `cb48eb49eb36c3ca55aac36082c306ea839efa6d7d6801f9108707a67d513aa6` |
| `/tmp/chunk_00` | `3f5a087f8bdacad7354d13c10d801fe9845f1a46f7fcb60bc7384fe96abc626e` |
| `/tmp/chunk_01` | `674342fa25ac46db117c45102c26f501c88a92e134b9704555aa0d69b71444a4` |
| `/tmp/chunk_02` | `a073868b3d853a384a3445e3f35aba5aeb8ac6ab99027c7c3612cdfeb9b74b0a` |
| `/tmp/chunk_03` | `dc90da765b3b096e771dbd160e02ba7f2457214c01d70701f636f4ba849c475b` |
| Review `collection.json` | `5a7171ba346a43ad86334bcf006dbb3b61e96ea576249cfc9c95d46fb104ba6f` |
| Review `review-inputs.json` | `91a0ccc47aa6a0a6bef3235767b59ce6e277537f67bfb962566a1cee2db418a4` |
| Review `integration.json` | `933bcf1a4c4b2fff2867bfcc5ece4837c4c91c3666121a6addc75255ac0fc6a9` |
| Review `integration.log` | `60a7f1ad02b75da852a85097b5d2576e7a34eb3795b2899de1abae773e099d9a` |
| Review `integration.xml` | `ae8c18f452e83e314019ad4419c0fbb567d09b123ee11485d24cb24f6407967a` |

R45 remains PARTIAL. **85/200 = 42.5% (~43%); formal 1/50 (2%)**, unchanged;
no new C/J/E/A. Independent security/operating acceptance and R43–R49 remain
open. **NOT_READY_TO_FUND; V10 unchanged/DEFERRED.**

**Next unfinished action:** resume required PARTIAL integrations. At the next
required coherent-batch regression, retain exact argv, source/runtime/input
identities and per-case results with capacity planning for one uninterrupted
invocation. Do not rerun an unchanged suite just to restate this document.
Actual custody evidence still needs an already-authorized namespace-capable
runner or a separately approved host prerequisite; this review authorizes neither.
