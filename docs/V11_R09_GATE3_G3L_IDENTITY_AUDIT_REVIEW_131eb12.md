# Independent exact-commit review — G3-L retained-evidence identity audit

- Reviewer: Claude Opus 5.5 (high), independent of the author; review only.
- Candidate commit `131eb127f0f7d82beae95fdb024f2de7eb38807a`, tree `659ef4d8b787f19ddd96e8b815d925b281774691` (verified via `git rev-parse`; worktree clean before and after; no tracked file modified).
- Audit JSON `docs/V11_R09_GATE3_G3L_IDENTITY_AUDIT_20261002.json`: 185,836 bytes, SHA-256 `357f3a2a31456175ad19c68807cd53019f2464de0c13ea7608d478b51f9b80e8` (matches handoff).
- **Verdict: PASS_IN_SCOPE, scope limited to the audit/reconciliation index.** This is **not** a G3-L PASS. All 77 PRE_REVIEW identities remain MISSING, every `qualified_entry` is null, `qualification_credit` is 0, `launchable` is false, and **G3-L remains NO-GO**. The two FINAL-only detached-review outputs remain unassembled.

## Independent verification

1. **Exact reproduction.** Re-running `tools/v11_r09_gate3_g3l_identity_audit.py` with the recorded inputs (target 2026-10-04, now 1790977445 = 2026-10-02T21:44:05Z, disk 4,396,154,880, MemAvailable 1,052,667,904) reproduces the committed JSON exactly: parsed-equal, and byte-identical under `indent=2, sort_keys=True` plus a trailing newline.
2. **Prior reconciliation chain.** The reconciliation JSON's hash and length match. The 027fd7a review `.md`, `.verdict.json` and `.terminal.json` are byte-identical to the retained `/tmp/alpha-v11-g3l-reconciliation-review-027fd7a.*` originals. The terminal shows exit 0, scope `RECONCILIATION_ONLY_NOT_G3L`, clean, and matching head, tree, report and verdict hashes.
3. **Recovered G3-P terminal binding.** `docs/V11_R09_GATE3_PROTOCOL_REVIEW_117830a_terminal.json` is 383 bytes with SHA-256 `414aef99…3db22`. It is identical to the retained `/tmp/alpha-v11-r09-gate3-protocol-review-117830a/terminal.json`, which has a Sep 30 17:51 mtime and was written by that directory's `worker.py` driver. It is also identical to the bytes at recovery commit `5a06629` and at HEAD. Its contents are status PASS, exit 0, error null, marker `R09_GATE3_PROTOCOL_REVIEW_PASS`, commit `117830a9…`, and tree `07c4d72f…`; I verified separately that `07c4d72f…` is the real tree of `117830a9…`. Its `report_sha256` `7d2bda20…50b4` equals the retained `review.md`, the committed `docs/V11_R09_GATE3_PROTOCOL_REVIEW_117830a.md` (first committed in `40ad8f9`, 2026-09-30), and the reconciliation's claimed hash. The retained `verdict.json` agrees (PASS, same commit and tree). The scope is G3-P protocol only, and the audit labels it `PASS_G3P_PROTOCOL_ONLY`, not a G3-L terminal.
4. **All 39 artifact comparisons.** I checked these with my own script (`git cat-file blob`), not the tool. For all 39, the Git bytes at the named commit match the claimed SHA-256 and length, each commit is an ancestor of HEAD, the recorded `current_sha256`/`current_byte_length` are correct, and each `current_matches_reviewed_bytes` flag is correct. The worktree equals HEAD for each file. Exactly one file has drifted: `tools/v11_r09_gate3_runtime.py`, from 110,679 bytes / `3a45eb46…f9ed` to 120,122 bytes / `276d9779…fcd3c`. The changes come from `7ef7d5d`, `9a844b1` and `70ef1a1` (attempt guard and intake gate wiring).
5. **Code-byte observations.** I re-checked all 7 `code_byte_observations` in the source reconciliation, which the tool itself does not do (see F1). For all 7, the Git bytes at the named commit match. Only `injected_runtime` (the runtime file) has changed. The launch validator, ledgers, collector, offline I/O, A7 decoder and A8 composition files are unchanged. So the reconciliation's statement that "runtime and ledger bytes still match" is stale, and its statement that "validator bytes still match" is still true.
6. **Categories (6/1/70/0).** Mapping prior status to category: REUSABLE_SCOPED_ARTIFACTS→RETAINED 6, REUSABLE_SCOPED_ARTIFACTS→FUTURE 1 (slice-3, correctly downgraded for runtime drift), SUPPORT_ONLY→OFFLINE 1 (`protocol.g3p_original_review_terminal`), SUPPORT_ONLY→FUTURE 10, BLOCKED_DEPENDENCY→FUTURE 41, MISSING_SELECTED_WINDOW→FUTURE 18. The six RETAINED rows match the handoff list, and all of their source refs match current bytes. All 7 identities that reference the runtime file are FUTURE. `required_freshness` is carried over unchanged, and all 77 rows have `qualified_entry` null.
7. **Screen.** The PRE_REVIEW checker on an all-null inventory returns exactly the 77 required IDs as MISSING and no other findings. It proposes 38 of 2,713 slots, and the resource snapshot matches the handoff. `freeze_checklist('2026-10-04')` gives a window of 2026-10-03 14:00–17:00Z, which is the earliest future window at 21:44Z on Oct 2, since the Oct 3 target's window had already closed.
8. **503/503/429 restrictions.** I confirmed these in the retained A5/A6 audit (lines 95–103) and in the preflight-checker review verdict: S3 503 at 2026-09-30T07:48:13.707996Z, S3 503 at 07:54:39.435641Z, and public-origin 429 at 07:55:15.376499Z. None has a Retry-After header or an expiry adjudication. In the audit, `network.ecmwf_503_429_expiry_resumption_review`, `network.restriction_domain_lineage` and `storage.denial_root_history_head` are all FUTURE/NOT_QUALIFIED. The `restriction_rule` says later 200 responses and elapsed time are not resumption evidence. I found no wording that weakens the hold. My bounded search did not find the private `aws-retry.json` original, so its body hash rests on the reviewed A5/A6 record.
9. **Tests.** `tests/test_v11_r09_gate3_g3l_identity_audit.py` and `tests/test_v11_r09_gate3_g3l_prep.py`: **20 passed in 0.77 s**. These ran offline with `--basetemp=/tmp/g3l-idreview-basetemp/bt`, `-p no:cacheprovider`, and `PYTHONDONTWRITEBYTECODE=1`.
10. **Probes** (run in a disposable `/tmp` clone, since deleted):
    - These all raised `ValueError`, so they fail closed: a 1-byte change to the reconciliation, a duplicate JSON key, a changed terminal commit, terminal status FAIL, a non-object terminal root, and a tampered prior verdict.
    - A drifted retained document moves its row from RETAINED to FUTURE.
    - A symlink pointing outside the repo is hashed, mismatches, and the row goes to FUTURE. File contents are never printed.
    - **A drifted launch validator `tools/v11_r09_gate3_launch_v4.py` is not detected** (F1).
11. **Privacy, bounds, wording.** The JSON and handoff contain no `/home`, private-directory, credential or token strings. The tool's work is bounded: 40 git subprocesses and about 1 s of runtime. The handoff consistently describes itself as an index only, says G3-L is NO-GO, says no provider request is allowed before G3-L PASS, and repeats the score lines unchanged. No live, provider, credential, V10/Axiom, authority or funding action took place during this review.

## Findings (none blocking for this exact JSON)

- **F1 — P3, latent false-retention in tool reruns.** The tool never re-checks the reconciliation's `code_byte_observations`. It downgrades only `code.slice3_exact_commit_review`, hard-coded by ID. The probe confirmed that if `tools/v11_r09_gate3_launch_v4.py` drifted, `code.mapping_exact_commit_review` would still come out as RETAINED, while its source scope still says "validator bytes still match". This is the same class of drift this commit found for the runtime. The committed JSON is correct today because all 7 observations were re-verified. No credit is affected (credit 0, entries null). Fix: derive downgrades from every reviewed code observation in a row's scope, and add a regression test for validator drift.
- **F2 — P3, wording.** `screen.missing_after` is the same variable as `missing_before`. No second check runs. The handoff's "77 MISSING before and 77 MISSING after" is true in substance, because the audit fills nothing, but it describes a single measurement. It should say so.
- **F3 — nits.**
  - The terminal binding does not check `marker` or `error`. These are redundant with status/exit under the original `worker.py`.
  - The `protocol.g3p_original_review_terminal` row lists the G3-P report twice.
  - `_git_bytes` uses `git show <commit>:<path>` without a timeout. `git cat-file blob` would be safer. Inputs are hash-bound, so the risk is low.
  - The first test asserts the exact live runtime-drift set, so it will need updating as the runtime changes.

## Scope statement

A PASS_IN_SCOPE here accepts only this reconciliation/identity index at the exact commit, tree and JSON hash above. It does not:
- qualify any inventory entry;
- create or review a private V4 manifest;
- review current GateRuntime bytes;
- adjudicate the 503/503/429 holds;
- authorize capture, dispatch, provider contact or funding.

It is also not an acceptance in itself. Acceptance requires the coordinator's completed original reviewer-process record. **G3-L remains NO-GO, with 77 PRE_REVIEW identities missing.**
