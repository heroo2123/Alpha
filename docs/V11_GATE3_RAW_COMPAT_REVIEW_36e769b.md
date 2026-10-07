# Independent exact-commit review: 36e769b "Repair RAW binding for current transport closure evidence"

Reviewer: claude-opus (independent, not the author). Review-only. No edits/commits in any git worktree, no network/provider requests, no sudo, V10/AxiomTrade untouched.

## Identity
- candidate commit: 36e769beede4e3e99670da7ed56a2503488273ab
- candidate tree: ac86264681fd796a40c13ef6ccbdf1876cd50c11
- candidate parent: fb5a43ee5a7aa40e28756ad68489ce8003959502 (single parent)
- provisional integration: merge d6a7215 parents = fb5a43e, 36e769b; `git diff 36e769b d6a7215 -- tools/v11_gate3_raw_decoder_binding.py tests/test_v11_gate3_raw_decoder_binding.py` is empty (identical content integrated).
- frozen worktree /tmp/alpha-v11-raw-compat-review-36e769b: at start `git rev-parse HEAD` = 36e769b..., `git status --porcelain` empty. **The worktree was removed by an external actor partway through this review** (after the RAW-suite runs, whose trailing `git status --porcelain` was still empty; on the next command the directory no longer existed and it is absent from `git worktree list`). The reviewer did not remove it. Remaining checks were run against read-only `git archive` exports of 36e769b and fb5a43e under /tmp (not git worktrees), deleted afterward. Final clean/unchanged status of the original worktree therefore cannot be verified directly; the candidate commit/tree identity was re-verified from the object store at the end (tree ac86264...).

## 1. Exact diff
`git show --stat 36e769b`: 3 files changed, 40 insertions(+), 2 deletions(-)
- docs/V11_GATE3_RAW_CURRENT_COMPAT_HANDOFF_20261004.md (+19, new)
- tests/test_v11_gate3_raw_decoder_binding.py (+16)
- tools/v11_gate3_raw_decoder_binding.py (+5/-2)

`git show --format= 36e769b | sha256sum` = c2600beaad058211c494cb7901d71fa8969b9b827aa31169e63c3cdd8ffa7e2a
`git diff --check fb5a43e 36e769b`: clean.

Code change: (a) adds `'read_bytes'` to the exact set-equality key schema of the closure dict; (b) adds conjuncts `type(closure.get('read_bytes')) is int and closure['read_bytes'] == delivered` inside the existing `BINDING_CLOSURE` _require. Nothing else in the tool changed.

## 2. Semantic correctness
Producer: tools/v11_r09_gate3_runtime.py ~L1870-1878 (`closure_evidence_raw = canonical({...})`), introduced by 9d0e9a1 (2026-10-03 22:01), which is an ancestor of the parent fb5a43e and predates the first RAW binding commit 7fc9223 (22:24). So the binding was strict against a producer that already emitted `read_bytes`; on the parent the RAW binding refused every genuine success.

(a) Key set. Producer keys: adapter, known_closed, status, headers, prefetched_bytes, delivered_bytes, read_bytes, chunks_consumed, chunk_count, deadline_monotonic, closure_clock_sha256 (11). New required set: identical 11. No extra/missing key.

(b) Semantics. `read_bytes` = `StreamSnapshot.read_bytes`, cumulative bytes returned by `read()`. For SyntheticResponseStream, delivered_bytes == prefetched_bytes == sum(chunk lengths) and `_read_bytes` increments by each returned chunk. The runtime already requires, on the non-overdelivered path, `eof_confirmed and state.read_bytes == state.delivered_bytes and charged == delivered` (L~1837-1842, else RUNTIME_EOF_UNPROVEN), and `_StreamAccounting.observe` enforces read_bytes <= delivered_bytes and monotonicity. The binding additionally requires attempt.outcome SUCCESS, overdelivered False, transport_closed outcome OK, closure.adapter == 'SyntheticResponseStream', chunks_consumed == chunk_count, and content-length == delivered. Under those conditions read_bytes == delivered is the exact correct invariant: a partial/truncated read (read_bytes < delivered) is refused, an over-read (read_bytes > delivered) is refused, and the overdelivery/discard path (where read_bytes may legitimately be < delivered) is already excluded from release. The equality cannot admit a truncated or over-read closure. It could only be "wrong" for a future non-synthetic adapter, which the adapter == 'SyntheticResponseStream' conjunct already refuses.

(c) No weakening. All previous conjuncts are retained verbatim. Precisely stated, the change is not purely monotone: it newly admits 11-key closures (previously always refused) and newly refuses 10-key closures lacking read_bytes (previously admitted). The admitted class is exactly the current producer format, with the new field fully constrained (exact int, == delivered). The newly refused 10-key class is not emitted by any producer in the history of the binding (producer predates binding). No prior refusal reason is removed or relaxed.

(d) Hash binding. Unchanged and still covers the full closure: `canonical(closure) == raw_closure` and `sha256(raw_closure) == transport_closed['closure_evidence_sha256']`, and transport_closed.closure_evidence_sha256 == attempt.closure_evidence_sha256. The new field is inside raw_closure and is therefore hash-bound.

## 3. Schema strictness
Still exact set equality (`set(closure) == {...}`); unknown fields are refused. Verified empirically (probe EXTRA below).

## 4. Tests (Python /home/alphaadmin/AlphaV11_Dev/venv/bin/python)
- `python -m pytest -p no:cacheprovider -q tests/test_v11_gate3_raw_decoder_binding.py` (frozen worktree): **51 passed** (rc 0). Log: /tmp/alpha-v11-raw-compat-review-36e769b.raw_normal.log
- `python -O -m pytest -p no:cacheprovider -q tests/test_v11_gate3_raw_decoder_binding.py` (frozen worktree): **51 passed, 1 warning** (rc 0; PytestConfigWarning that non-test-module asserts are ignored under -O). The binding uses explicit `_require`/raise, not assert, so -O does not disable its checks; pytest-rewritten test-module asserts remain active. Log: ...raw_optimized.log
- Adjacent suite: the handoff's "38 passed, 123 deselected" corresponds to 161 = test_v11_r09_gate3_ledgers.py (52) + test_v11_r09_gate3_store_v1.py (109), but the exact `-k` expression is not recorded and probes (e.g. "close or restart or custody or clock" -> 15 selected) did not reproduce 38. Not reproduced exactly. Instead the full superset files were run on the 36e769b export:
  - tests/test_v11_r09_gate3_ledgers.py: **52 passed**
  - tests/test_v11_r09_gate3_store_v1.py: **109 passed, 2 warnings**
  - tests/test_v11_r09_gate3_runtime.py (producer): **135 passed**
  - tests/test_v11_r09_gate3_restart_composition.py: **7 passed**
  Logs: /tmp/alpha-v11-raw-compat-review-36e769b.{r09_gate3_ledgers,r09_gate3_store_v1,r09_gate3_runtime,r09_gate3_restart_composition}.log

## 5. Regression test exercises the fix
Ran the candidate test file against the parent fb5a43e binding (git archive export, candidate test file copied in): **11 failed, 40 passed**; failures include `test_current_transport_closure_binds_read_bytes` and 10 pre-existing success-path tests, all with RawBindingRefusal BINDING_CLOSURE. So the parent was genuinely broken against the current producer and the new test fails pre-repair. Log: ...parent.log

Note: the new test is a positive-path test only. It does not by itself test that mismatched/missing/mistyped read_bytes is refused. Reviewer adversarial probes (file preserved at /tmp/alpha-v11-raw-compat-review-36e769b.probe.py, run outside any worktree, using the suite's existing transport_closed-kwargs tamper pattern so the session hashes the tampered bytes and only field checks can refuse): CONTROL (unmodified) -> releases BODY; read_bytes = 30, 32, 0, 31.0, "31", True, None, key missing, extra unknown key -> all refuse BINDING_CLOSURE. **10 passed** normal, **10 passed** under -O. Log: ...probe.log

## 6. Authority surface
Diff touches only a pure validation predicate, one test and one doc. No new imports, no network/socket/provider/credential/subprocess/env/execution/financial code. Handoff doc explicitly grants no provider, storage, G3-L, capture, SHADOW, funding or live authority. None found.

## 7. Worktree unchanged
Start: HEAD 36e769b, clean. After RAW runs: clean. Thereafter the worktree was externally removed; final state of that worktree not verifiable. Main repo /home/alphaadmin/AlphaV11_Dev/Alpha `git status --porcelain` empty at end. Commit/tree identity re-verified.

## Findings
- LOW (test coverage): tests/test_v11_gate3_raw_decoder_binding.py:140 - the regression test covers only the positive path; there is no committed negative test that read_bytes != delivered / non-int / missing is refused. Reviewer probes confirm the code refuses all these cases, so this is a coverage gap, not a defect. Recommend adding a parametrized negative test in a follow-up.
- INFO (handoff reproducibility): docs/V11_GATE3_RAW_CURRENT_COMPAT_HANDOFF_20261004.md:16 - the adjacent "38 passed, 123 deselected" selection expression is not recorded and could not be reproduced; superset files (161) pass in full.
- INFO (precision of claim): tools/v11_gate3_raw_decoder_binding.py:174-177 - the schema change both admits the 11-key format and refuses legacy 10-key closures; this is correct given the producer always emitted read_bytes since before the binding existed, but "strictly additive" is not literally true.
- INFO (process): the frozen review worktree was removed externally mid-review; final clean state of that worktree is unverified.

## Verdict
PASS_IN_SCOPE. The repair is minimal, matches the producer exactly, keeps exact-schema and hash binding, imposes the correct invariant for the only permitted adapter, removes no prior refusal, and is validated by tests that fail on the parent. Grants none of: provider request authority, execution authority, G3-L credit, SHADOW admission.
