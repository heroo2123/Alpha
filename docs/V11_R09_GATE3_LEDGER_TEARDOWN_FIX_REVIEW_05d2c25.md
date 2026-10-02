# Offline SharedLedger fixture teardown review — 2026-10-02

- Exact candidate: `05d2c251a8a8431649914a76d12a3b99dd25d229`, tree `635768d55dd0862ff2688514b7b78d376f13a1f4`.
- Author scope: 11 added lines in the synthetic runtime and H1–H6 test fixtures; production SharedLedger code and safety checks are unchanged.
- Cause: module-level fixture caches keyed by `tmp_path` retained expected heads after pytest deleted a passing test's temporary directory. A later test could reuse that path and supply a stale expected head to a new empty ledger.
- Author verification: 138 affected tests pass with `tmp_path_retention_policy=failed`, both plain and under Python `-O`; `git diff --check` passed.
- Independent different-model Astra/high read-only review: **PASS_IN_SCOPE** for the clean exact commit/tree. It found that function-scoped cache resets preserve within-test reopen checks, and independently ran 197 offline runtime, H1–H6, ledger and restart tests under the failing retention policy: all passed. This was an in-session reviewer, so there is no separate CLI reviewer-process terminal artifact; no such terminal is claimed.
- Newer-main reconciliation found no changes to either candidate test file since its base. Merged locally as `bc9994c`; the same 197 tests passed on merged main. No provider request, capture, SHADOW qualification, or acceptance-score change follows.
