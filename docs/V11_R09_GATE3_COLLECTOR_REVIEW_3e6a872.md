# R09 Gate 3 (G3-I) collector independent review — round 2

Reviewed commit: `3e6a872f39bff616c14460bec792ad514da328b5`
(tree `a81ef0d04bea1c27b8f6096ce85ad5b019ba518b`), one commit ahead of
`de8c7bc` on branch `r09-gate3-collector-20260930`.

Reviewer: Sonnet/independent, second round, isolated read-only worktree
`/tmp/alpha-v11-r09-gate3-collector-review-3e6a872/Alpha`. No network access
performed; no code edited/committed/pushed in this worktree.

**Verdict: PASS, scoped strictly to closing round 1's P2-1 finding on this
exact G3-I collector commit.** This grants no launch, no network acquisition,
no G3-L manifest approval, and no financial/production/host authority.

## What changed since `de8c7bc`

`git diff de8c7bc 3e6a872 -- .` shows exactly two files touched (32
insertions, 3 deletions, 0 other files):
- `tools/v11_r09_gate3_collector.py`: adds two module-level constants
  `_EXISTING_MAX_INDEX_BYTES = 3*1024*1024` / `_EXISTING_MAX_FIELD_BYTES =
  4*1024*1024` (duplicated by value, with an explanatory comment on why they
  are not imported), and changes the `BudgetTracker.__init__` default and
  `require()` ceiling for `max_field_bytes` from the protocol's own nominal
  `16 * 1024 * 1024` to `_EXISTING_MAX_FIELD_BYTES` (4 MiB). `max_index_bytes`
  is re-expressed via the same named constant but its enforced value is
  unchanged (was already 3 MiB).
- `tests/test_v11_r09_gate3_collector.py`: adds one regression test,
  `test_budget_field_bytes_cannot_loosen_past_the_real_stricter_bound`,
  covering exactly 4 MiB (allowed), 4 MiB+1 (rejected) and 10 MiB (rejected,
  the round-1 counterexample).

No other file changed. `RestrictionLedger`, `AttemptLedger`,
`NativeECMWFThreeHourRequest`, `CaptureManifest`, `estimate_feasibility`,
`Transport`/`check_index_availability` and `causal_feature_eligible` are
byte-identical to `de8c7bc`, so round 1's adversarial PASS findings on those
objects still stand unmodified and are not re-litigated here.

## Checks executed (commands actually run)

1. `git diff de8c7bc 3e6a872 -- .` and `git diff de8c7bc 3e6a872 --stat -- .`
   — confirmed scope above.
2. `python3 -m py_compile tools/v11_r09_gate3_collector.py
   tests/test_v11_r09_gate3_collector.py` — clean.
3. Independently constructed the exact round-1 counterexamples directly
   against the library (not via pytest):
   `BudgetTracker(max_field_bytes=10*1024*1024)` → raises `PanelError`
   `BUDGET_MAX_FIELD_BYTES_CANNOT_LOOSEN_PROTOCOL`;
   `BudgetTracker(max_field_bytes=16*1024*1024)` → same rejection;
   `BudgetTracker(max_field_bytes=4*1024*1024)` → succeeds (exactly at the
   real bound); `BudgetTracker()` (all defaults) → succeeds with
   `max_field_bytes == 4194304`. All four match the required repair exactly.
   (Note: `PanelError` actually lives in `tools/v11_multimodel_panel`, not
   `polymarket_scanner/v11/model_panel` — the latter has no such class; this
   is just an import-path detail for the reviewer's own script, not a defect.)
4. Confirmed the live real bounds independently by reading the production
   modules directly: `polymarket_scanner/v11/ecmwf_sources.py:19
   MAX_INDEX_BYTES = 3 * 1024 * 1024`; `polymarket_scanner/v11/model_panel.py:17
   MAX_RAW_BYTES = 4 * 1024 * 1024` (re-exported as `ecmwf_sources.MAX_FIELD_BYTES`).
   Both match `_EXISTING_MAX_INDEX_BYTES`/`_EXISTING_MAX_FIELD_BYTES` exactly.
5. `/home/alphaadmin/alpha-review-test-venv/bin/python3 -m pytest
   tests/test_v11_r09_gate3_collector.py -q` → **46 passed** (also
   `grep -c "^def test_"` = 46, i.e. 45 + 1 new, matching the claimed count).
6. `/home/alphaadmin/alpha-review-test-venv/bin/python3 -m pytest
   tests/test_v11_r09_gate3_collector.py tests/test_v11_trajectory_contract.py
   tests/test_v11_gefs_sources.py tests/test_v11_model_panel.py -q` →
   **309 passed, 2 skipped, 0 failed** (46 new + 263 unchanged regression,
   matching the handoff's claimed count exactly).
7. Read protocol Section 7 (`docs/V11_R09_GATE3_COLLECTION_PROTOCOL.md`):
   "Build a separate capture-manifest and attempt ledger without importing
   production/host-trust/financial services" — confirms that pinning the
   bounds by value rather than importing `ecmwf_sources`/`model_panel` is the
   protocol-mandated choice, not an oversight.
8. Spot-checked every other claimed-bound/ceiling in the file for the same
   "docstring/comment claims a stricter mirrored bound that `require()` does
   not actually enforce" pattern (`max_requests`, `max_total_received_bytes`,
   `min_interval_seconds`/`min_interval_between_starts_seconds`,
   `max_elapsed_seconds`, `min_free_disk_bytes`, `min_available_memory_bytes`):
   none of these claim to mirror any external already-reviewed bound (they
   are the protocol's own numbers only), so none are candidates for this
   defect class.

## Round-1 P2-1 status: CLOSED

Independently re-verified: the fix closes exactly the counterexample round 1
constructed (`max_field_bytes` values strictly between 4 MiB and 16 MiB, and
16 MiB itself, are now both rejected), the default changed from 16 MiB to
4 MiB, `max_index_bytes` (already correct) is unaffected, and the new
regression test is present and passing. No P1/P2 introduced by this diff.

## P3 observation (non-blocking, carried forward from round 1's own text)

**P3-d**: Round 1's finding (P2-1) itself already noted, in passing, that the
module's docstring (`tools/v11_r09_gate3_collector.py:509-516`, unchanged by
this fix) claims the class "can never loosen an existing bound" citing *three*
examples — ECMWF's `MAX_INDEX_BYTES` (3 MiB), `MAX_RAW_BYTES` (4 MiB), *and*
GEFS's `grib_fields.MAX_BYTES` (64 KiB) — but round 1's own required repair
text scoped the fix narrowly to the 4 MiB ECMWF figure only. That scoping is
honored exactly by this commit. However, the residual gap remains real:
`BudgetTracker` exposes a single global `max_field_bytes` (now capped at
4 MiB) applied uniformly to all providers; there is no separate,
GEFS-specific ceiling parameter enforcing the much stricter real 64 KiB
`grib_fields.MAX_BYTES` bound the same docstring names. Independently
confirmed this is not currently exploitable: `check_before_request`'s
`index_bytes`/`field_bytes` keyword arguments are never invoked anywhere in
this module's own code (`grep` shows the only call site, inside
`begin_request`, passes neither) — there is no fetch/decode loop in this
G3-I commit at all (confirmed: no `main`/orchestrator function exists in the
file), so no real GEFS byte count is ever checked against any ceiling today.
This is therefore latent, not live, and does not block G3-I acceptance. It
should be tracked as an explicit item for whoever wires a real fetch loop at
G3-L: either give `BudgetTracker` a per-provider field-byte ceiling (GEFS
64 KiB vs. ECMWF 4 MiB) or narrow the docstring's claim to match what is
actually enforced today.

**P3-e (process note, not a code defect)**: This review's briefing stated the
round-1 review file was "preserved at
`docs/V11_R09_GATE3_COLLECTOR_REVIEW_de8c7bc.md` in this worktree." It is not
present in this worktree (`/tmp/alpha-v11-r09-gate3-collector-review-3e6a872/Alpha`);
it exists only in the separate round-1 worktree
(`/tmp/alpha-v11-r09-gate3-collector-review-de8c7bc/Alpha/docs/...md`), matching
`docs/V11_WORK_CHECKPOINT.md`'s own accurate description ("preserved at ... in
that worktree; not committed to main"). Located and read it there in full
before writing this review. No impact on the verdict, but the handoff/briefing
text describing its location should say which worktree, since these review
artifacts are not committed to the branch being reviewed.

## Gate scope of this verdict

This review covers **G3-I only**, and only the narrow question of whether
commit `3e6a872` correctly closes round 1's P2-1 defect without introducing a
new P1/P2 or reopening any previously-PASSed object. It does not authorize
G3-L manifest preparation, G3-L launch, G3-E corpus replay, any Gate 4/5
action, or any financial/production/host action.

R09_GATE3_COLLECTOR_REVIEW_PASS
