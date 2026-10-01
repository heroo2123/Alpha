# Independent offline G3-L preparation review — 3241abf

**Verdict: CHANGES_REQUIRED. G3-L remains NO-GO.** This is an offline preparation review, not a launch approval, provider preflight approval, or acceptance of any later main commit.

## Exact scope

- Candidate: `3241abf1640a631052a71b66d5a2c683f8b6e6e8`
- Candidate tree: `d4ef2d2602a1fda2c9f45be33cf9ee88114f993b`
- Base: `98b5c26ff63b504fdd388fe21ebdfef685044f86`
- Base tree: `55b2c450372e0f1979089a307b324ea26a136db0`
- Worktree: `/tmp/alpha-v11-gate3-g3l-prep-20261001`
- Reviewed the five changed files: planner/checker, preparation documentation, checked-in 2,713-row report, null V4 template, and preparation tests. Compared resource accounting and schema with `tools/v11_r09_gate3_launch_v4.py`, accepted transport design, launch addendum/readiness requirements, and the V4 tests at this exact candidate.
- Candidate identity and clean status were verified before and after review. No repository or worktree edits, provider requests, service operations, private launch-manifest creation, or durable runtime initialization were performed. Probe files and their synthetic data are local review artifacts under `/tmp`.

## Findings

### F1 — P1: No complete inventory can satisfy the storage measurement scope

Locations: `tools/v11_r09_gate3_g3l_prep.py:106–111`, `:429–436`.

`storage.live_disk_memory_quota_measurement` belongs to both `RUN_SPECIFIC` and `WINDOW_SPECIFIC`. The checker independently requires its single `scope` string to equal both `run:1790812800` and `window:1790863200:1790874000` for the October 2 target. These strings differ, and the `elif` chain does not skip the window check when the run check succeeds.

Reproduction uses a structurally complete 79-entry synthetic review packet with actual local files, matching hashes and lengths, distinct evidence/review bytes, current timestamps, and valid scopes for every other identity:

- With run scope, the sole finding is `INVALID / wrong window scope` for the storage measurement.
- With window scope, the sole finding is `INVALID / wrong run scope` for the storage measurement.

Consequently the advertised `ASSEMBLED_FOR_INDEPENDENT_REVIEW` success path and exit code 0 are unreachable for any valid ordinary JSON packet. The committed tests never exercise a complete inventory; the stale-run test selects a different identity and misses the contradiction.

Required repair: define a satisfiable scope contract. Prefer assigning this live resource observation to the acquisition window; if both bindings are needed, represent both explicitly and validate them separately. Preserve the applicable freshness requirement. Add an all-identities-complete positive test and a direct storage-observation scope/freshness regression.

### F2 — P2: The pre-review assembly state requires the review outputs

Locations: `tools/v11_r09_gate3_g3l_prep.py:101–104`, `:357–361`, `:490–492`; `docs/V11_R09_GATE3_G3L_OFFLINE_PREP.md:17–35`.

The contract describes a resolved inventory as only assembled for independent review, and the CLI's success state has exactly that meaning. Yet `REQUIRED` includes both `review.detached_g3l_report` and `review.detached_g3l_completed_terminal`. Leaving those two later review outputs null produces two mandatory `MISSING` findings even when every other evidence identity is supplied (plus the separate F1 error). Each output also requires its own independent-review reference under the generic entry schema.

This reverses the documented workflow: the completed detached review must already exist before the package can be labelled ready to undergo that review. It encourages either placeholders or out-of-band assembly and makes the intended intermediate state unusable even after F1 is fixed. The accepted readiness sequence freezes and assembles the package first, then obtains its exact-manifest detached review and terminal.

Required repair: distinguish package inputs from subsequent approval outputs. Permit a genuinely complete pre-review input packet to reach the assembly state while keeping the later review fields explicitly unresolved/nonlaunchable. Validate the final detached report and terminal only in the separate post-review stage. Add positive tests for both stages and retain the launch refusal without completed approval.

## Verified behavior and capacity assessment

Both checked-in JSON artifacts reproduce exactly from the candidate generators. The report contains 2,713 unique raw slots, denominators GEFS 775 / IFS 1,275 / AIFS 663, 79 unresolved evidence identities, and no fabricated launch approval. The null V4 template has the expected groups and the V4 validator refuses it. That refusal alone is not a test of every eventual filled nested value.

The checked-in resource snapshot is **2026-10-01 13:22:57 UTC**, free disk **2,926,313,472 bytes**, and available memory **753,352,704 bytes**. The larger 3,757,068,288-byte disk figure belongs to a preparation test constant, not this report. The report proposes eight fields in provider round-robin order, leaving 2,705 rows explicitly unattempted:

| Bound | Recomputed value |
|---|---:|
| Requests | 32 |
| Reserved received bodies | 119,537,664 bytes |
| Store objects | 65 |
| Store events | 129 |
| Session events | 257 |
| Budget events | 1,089 |
| Serial duration | 1,142 seconds |
| Local storage quota | 744,488,960 bytes |
| Additional disk/memory headroom | 33,554,432 bytes |

Each field reserves one full 3 MiB INDEX, one 4 MiB OBJECT_ID, one 4 MiB METADATA, and its provider's full 2/4/4 MiB FIELD cap. No overhead body or request is omitted. The store-object aggregation, four-journal quota, response reservations, report reserve, decoded reserve, and event calculations agree with the V4 validator. At the recorded snapshot the quota plus headroom leaves 2,148,270,080 free bytes, only **786,432 bytes above the 2 GiB floor**. This is particularly sensitive to subsequent disk use.

Seven resource probes covered zero/floor resources, the checked-in snapshot, the test snapshot, abundant resources, and both sides of the memory admission boundary. Every nonempty plan preserved the declared numeric caps and floors. With abundant host resources the body limit selects 71 fields / 284 requests and reserves 1,066,401,792 body bytes. Zero-resource plans retain all denominator rows and propose no requests; their reported baseline quota is a prospective requirement, not a claim that it fits.

No additional capacity bypass was reproduced in the mathematical planner. Its memory condition reserves 64 MiB decoded space plus 32 MiB headroom; this review does not establish the actual runtime/decoder peak, which remains an explicitly unresolved qualification. The snapshot cannot qualify current launch resources: the coordinator reported a later approximately 2.6 GiB free-disk observation, and this reviewer did not independently remeasure the host. The candidate correctly documents that changed host conditions invalidate the snapshot.

Inventory probes also confirmed rejection of equal evidence/review artifacts, changed bytes, traversal, symlinks, future observations, stale run/window evidence, and wrong run scopes. These are structural/hash checks for review assembly, not proof of the semantic truth or independence of the underlying evidence.

## Executed validation

1. Project virtualenv, bytecode disabled, pytest cache disabled: `tests/test_v11_r09_gate3_g3l_prep.py tests/test_v11_r09_gate3_launch_v4.py` — **64 passed** (6 preparation, 58 V4), 9.94 seconds.
2. External probes `/tmp/alpha-v11-g3l-prep-review-3241abf-probes.py`, same interpreter and candidate PYTHONPATH — **19 passed**, 0.55 seconds. Three tests positively reproduce the two defects; the remaining sixteen verify refusals, resource recounts, and artifact reproduction.

**Total: 83 passed, 0 failed.** Passing defect-reproduction tests establish the reported defects; they do not imply candidate acceptance. Initial attempts with system Python and `/tmp/gefs_test_venv/bin/python` could not start pytest because it was not installed in those interpreters; the existing project virtualenv completed both suites. No packages were installed.

Required follow-up is a repaired exact-commit review including successful complete pre-review inventory coverage. Later integration must reconcile newer main independently. G3-L remains **NO-GO**, and G3-E, learner, and SHADOW authority remain unaffected.
