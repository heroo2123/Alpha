# Independent exact-commit review: 7927067 (RAW binding read_bytes refusal tests)

Reviewer: claude-opus (independent, not the author). Review-only; no edits inside any worktree; no network; no sudo; V10/AxiomTrade untouched.

Verdict: PASS_IN_SCOPE

## Candidate identity
- Commit 7927067009a7f89bfd8f504dcda1e8f90625ff68, tree 85271b99a66edd3c1f0ab9610cbb6e90999b8a66, parent f69cf734f8b6cf73ec3e7477d0ceb18fd780db73. All verified by `git rev-parse` in the frozen checkout.
- Frozen checkout /home/alphaadmin/AlphaV11_Reviews/frozen-review-7927067: HEAD = candidate, `git status --porcelain` empty at start and end. (pytest runs left only gitignored `__pycache__/` dirs; no tracked or untracked-unignored change.)
- `git show --format= 7927067 | sha256sum` = 69a0e4043eb96b304c3261bedac6becdde27f82aba26a82b5fb5b6fd5c5d66c6 (matches expected).
- `git show --stat`: 1 file, tests/test_v11_gate3_raw_decoder_binding.py, +31/-0. No change outside tests/. No production code change. No existing line modified or removed, so no existing test weakened.
- `git diff --check f69cf73 7927067`: clean (rc 0).

## Task 2: test validity

(a) Tamper hook on real success path. tools/v11_r09_gate3_runtime.py L1869-1877 builds `closure_evidence_raw = canonical({...,'read_bytes': state.read_bytes,...})` and L1896-1901 calls `self.session.transport_closed(request.request_id, ..., closure_evidence_raw=closure_evidence_raw, ...)` via instance attribute lookup, so the test's instance-level override `runtime.session.transport_closed = tamper` intercepts it. The session method (tools/v11_r09_gate3_ledgers.py L1078-1110) stores `closure_evidence_raw_b64 = b64(closure_evidence_raw)` and `closure_evidence_sha256 = sha256(closure_evidence_raw)` from the passed (tampered) bytes, so the binding's sha check (binding L178-180) and `transport_closed.closure_evidence_sha256 == attempt.closure_evidence_sha256` (L245-246) both hold; run_attempt still returns SUCCESS (asserted by the test). Only field-level checks can refuse.

(b) Encoding hazards. `canonical` (tools/v11_multimodel_panel.py L47) = `json.dumps(sort_keys=True, separators=(',',':'), ensure_ascii=True, allow_nan=False)`. The test re-encodes with `json.dumps(sort_keys=True, separators=(',',':'))` — defaults ensure_ascii=True; allow_nan only matters for NaN/inf, not used. Values round-trip exactly through json.loads (31.0 -> `31.0` -> float, True -> `true` -> bool, "31" -> str, None -> null), so `canonical(closure) == raw_closure` holds and no case is refused by an encoding mismatch. Proven empirically by the mutants below: with the read_bytes rule removed, every tampered case releases bytes (DID NOT RAISE), i.e. nothing else on the path refuses the re-encoded closure.

Per-case attribution (candidate binding, tools/v11_gate3_raw_decoder_binding.py):
- 30, 32, 0: `closure['read_bytes'] == delivered` (L231).
- 31.0: only `type(...) is int` (L230) refuses (31.0 == 31 is True) — a genuine type-rule test.
- "31": type rule (and equality).
- True: `type(True) is int` is False -> type rule (bool-as-int hazard correctly covered).
- None: type rule.
- missing (value7): exact key-set equality (L174-177) that now includes read_bytes.
- EXTRA_KEY: exact key-set equality (strict schema); not read_bytes-specific, but in scope of the LOW finding ("extra-key").

Mutation testing (git archive export of 7927067 to /home/alphaadmin/AlphaV11_Reviews/review-7927067-mutant/, binding edited there, full test file run, export deleted afterwards; confirmed `tools` imported from the mutant dir):
- M0: drop L230-231 (read_bytes type/equality), keep exact key set. Result 7 failed / 53 passed. Not refused (test fails, DID NOT RAISE): 30, 32, 0, 31.0, "31", True, None. Still refused: missing (key set), EXTRA_KEY (key set).
- M1: M0 + key set accepts read_bytes present-or-absent (`set(closure) - {'read_bytes'} == base10`), extra keys still refused. Result 8 failed / 52 passed. Additionally not refused: missing. Still refused: EXTRA_KEY only.
- M2: M0 + superset key check (extra keys and missing read_bytes admitted). Result 9 failed / 51 passed. All nine cases not refused.
All mutant failures are exactly `Failed: DID NOT RAISE RawBindingRefusal` (7/8/9 occurrences), confirming the success path and released read reached completion and that each case is killed solely by the intended rule. `test_current_transport_closure_binds_read_bytes` passed under all mutants (positive control; it cannot detect these mutants, which is what the new test fixes).

Conclusion: every read_bytes value/type case is killed by removing the read_bytes type/equality rule; the missing case is killed by relaxing the read_bytes key requirement; EXTRA_KEY is killed by relaxing strict-schema extra-key refusal. No case passes for the wrong reason.

(c) `value == 'EXTRA_KEY'` sentinel: compared against int/float/bool/None/object()/str; all non-str compare False without exception; `str(len(BODY))` is "31" != "EXTRA_KEY". The check precedes the identity check for `_MISSING_READ_BYTES` (object() == str is False). No hazard. Stylistically a second object() sentinel would be cleaner, but not a defect.

(d) `runtime.report_sink.close()` omission: ReportSink.close (runtime L2354-2360) closes raw OS fds reserve_fd and dir_fd, which are not reclaimed by GC. Within the test file only test_current_transport_closure_binds_read_bytes calls it; the other ~24 tests (including all pre-existing refusal tests) use only `_close(acquired)`. So the new test follows the dominant file convention; it leaks 2 fds per case (18 total) for the life of the pytest process — harmless at this scale. INFO only, pre-existing pattern.

(e) `match='BINDING_CLOSURE'`: RawBindingRefusal(ValueError) is raised by `_require` as `RawBindingRefusal(reason)`; str() is exactly the reason; pytest `match` uses re.search; read_raw_for_a7 wrapper does not remap. Matches. (re.search would also accept a hypothetical 'BINDING_CLOSURE_X'; none exists. Not material.)

## Task 3: test runs (frozen checkout, one file at a time, basetemp removed after each)
- `/home/alphaadmin/AlphaV11_Dev/venv/bin/python -m pytest tests/test_v11_gate3_raw_decoder_binding.py -p no:cacheprovider -q --basetemp=/home/alphaadmin/AlphaV11_Reviews/review-7927067-basetemp` -> 60 passed (log frozen-review-7927067.normal.log).
- Same with `python -O -m pytest` -> 60 passed, 1 warning (standard PytestConfigWarning that non-test-module asserts are ignored under -O; binding uses `_require`, not assert) (log .optimized.log).
- New test only, -v -> 9 passed: [30],[32],[0],[31.0],[31],[True],[None],[value7],[EXTRA_KEY] (log .newcases.log).
- Mutants: logs .mutant-m0.log, .mutant-m1.log, .mutant-m2.log.
- Author claim 60/60 both modes: confirmed. Disk stayed at 3.6G free (above 2 GiB floor).

## Findings
- INFO tests/test_v11_gate3_raw_decoder_binding.py:183 — finally omits runtime.report_sink.close(); leaks two OS fds per case. Consistent with the majority file convention (only line 152 closes it). Optional hygiene.
- INFO tests/test_v11_gate3_raw_decoder_binding.py:160 — case ids `31` (str) vs `31.0`/`30` and `value7` (missing sentinel) are not self-describing in pytest output; `ids=` would aid triage. Cosmetic.
- INFO tests/test_v11_gate3_raw_decoder_binding.py:170 — EXTRA_KEY exercises generic strict-schema refusal rather than read_bytes specifically (killed only by M2). Appropriate given the LOW finding listed extra keys; noted for precision.

No CRITICAL/HIGH/MEDIUM/LOW findings. The LOW finding from docs/V11_GATE3_RAW_COMPAT_REVIEW_36e769b.md (no committed negative read_bytes test) is closed by this commit.

## Scope limits
- Review covers only this test-only commit and the binding/runtime/ledger code paths it exercises; no full regression run (not required for a test-only single-file change; the binding test file was run in full).
- Synthetic fixtures only; says nothing about real provider bytes, non-synthetic adapters, or Gate-3 live acceptance.
- No grant of provider requests, execution authority, G3L credit, or Shadow admission.
