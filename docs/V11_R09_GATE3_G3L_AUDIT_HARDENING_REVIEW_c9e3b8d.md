# Independent exact-commit G3-L audit-hardening review

**CHANGES_REQUIRED — offline audit hardening only.** The committed snapshot is correct, but the generic F1 repair still permits false RETAINED classifications when reviewed inputs contain malformed, incomplete, or inconsistent code observations. F2 and the scoped F3 improvements pass. No launch or qualification gate was promoted in the exact candidate.

- Reviewer: OpenAI GPT-6 Astra/high, independent of Claude Sonnet/high author.
- Candidate: `c9e3b8dff0b9a2aa22836a8006c31f522653e523`.
- Tree: `e1579a404b047a53cad54838702eb6d7f122b709`.
- Diff reviewed: `40bc64e..c9e3b8dff0b9a2aa22836a8006c31f522653e523`, seven files. Detached checkout clean before and after; no checkout, retained evidence, or safety-gate files changed.
- Snapshot: `docs/V11_R09_GATE3_G3L_IDENTITY_AUDIT_HARDENING_20261002.json`, 187,907 bytes, SHA-256 `566eb4beb8e8c15f359c7af2205acc91e1bc9235ae0df54f5a5ea4cb23719b1b`.

## Findings requiring changes

**R1 — P2: malformed or incomplete observations silently omit dependencies.** In `tools/v11_r09_gate3_g3l_identity_audit.py:161–169`, `commit_oid` need only be a string. Empty strings, `not-a-commit`, and a nonexistent 40-zero OID pass validation and never match the review terminal. With validator bytes drifted, each probe returned `code.mapping_exact_commit_review = RETAINED_REVIEWED_LOCAL_SCOPE`, with no validator dependency in the row. Removing just `injected_runtime` from the otherwise nonempty map also returned slice-3 as RETAINED despite its real current runtime drift. The previous unconditional slice-3 downgrade had prevented that particular false promotion.

Validate commit syntax and existence, and establish complete dependency coverage from reviewed scope before retaining a code-review row. Unknown or incomplete coverage must refuse or downgrade, not silently mean “no dependency.” Add regressions for empty/unresolvable commits and a missing individual observation. This blocks acceptance of the generic hardening, not the historical snapshot.

**R2 — P2: the dependency freshness baseline is not bound to the observation's commit.** At `tools/v11_r09_gate3_g3l_identity_audit.py:108–112`, the matching observation contributes only `artifacts[obs_path]`. That record was checked at its own `git_commit`; the observation's `commit_oid`, `sha256`, and `tree_oid` are not used to verify those bytes. All seven artifact baseline commits actually differ from their observation commits in the supplied reconciliation; their bytes happen to agree today, independently verified below.

A probe replaced only the runtime artifact's baseline commit/hash/length with the valid current HEAD values, retaining the original `6340cb4…` observation and terminal. The audit returned slice-3 as RETAINED even though current runtime bytes differ from the reviewed observation. A second probe changed the validator observation path to the existing ledgers path while retaining its validator hash/commit, and drifted the validator: mapping again remained RETAINED. Missing or incorrect observation hashes/trees also went unchecked.

Verify the blob at each observation's exact commit/path against its recorded hash and tree, then compare current bytes to that verified baseline. Keep dependency references keyed by commit and path, or explicitly reject inconsistent baselines; a path-only artifact dictionary cannot establish this binding. Add regressions for valid-but-different artifact and observation commits, path/hash mismatch, and missing hash/tree fields.

**Probe boundary:** these malformed-source cases used an in-memory `Path.read_bytes` overlay with a synthetically rebound reconciliation/verdict/terminal hash chain. No evidence was edited on disk. This tests semantic validation behind the integrity gate; it is **not** a demonstrated bypass of the original review hashes. A bare source-byte mutation with the original chain was refused. All returned probe reports retained zero qualification credit and G3-L NO-GO. Nevertheless, malformed-input handling and generic false-retention prevention are part of this hardening review, rather than merely frozen-JSON correctness.

## Nonblocking documentation correction

**R3 — P3:** handoff lines 35–39 say only two identities correlate with the seven observation commits. `code.launch_validator_commit_tree` also cites the mapping terminal's matching `candidate_commit`, making three. This row already directly lists validator source and stays FUTURE. Lines 44–48 also omit the added runtime and ledger `source_refs` on slice-3 when describing snapshot differences. Correct both statements; neither changes the current boundary.

## Independent verification and bounded tests

1. Read the prior `131eb12` independent review, verdict and terminal, the full exact code/test/document diff, the hardening handoff and JSON, source reconciliation, and referenced review JSON. The old snapshot and prior review bundle are byte-identical to base `40bc64e`.
2. Regenerated the hardening snapshot with its recorded target `2026-10-04`, now `1790979652`, disk `4137082880`, and memory `949186560`: parsed-equal and byte-identical under sorted, indented JSON plus newline.
3. Independently checked all **39** artifact hashes/lengths against Git and all recorded live-byte comparison flags; named artifact commits are ancestors of HEAD. Independently read all **7** observation blobs at their own commits, checked hashes and actual trees, and compared current bytes. Only injected runtime drifted. These independent checks establish today's snapshot correctness; the production tool does not perform all these observation checks.
4. Focused suite: **27 passed in 1.92 s**. Command: `PYTHONDONTWRITEBYTECODE=1 PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 timeout 120s /home/alphaadmin/AlphaV11_Dev/venv/bin/python -m pytest -q -p no:cacheprovider --basetemp=/tmp/c9e3b8d-review-focused-bt tests/test_v11_r09_gate3_g3l_identity_audit.py tests/test_v11_r09_gate3_g3l_prep.py`. Repository conftest blocks external socket connections. System Python lacked pytest; the historical review venv was absent, so the available retained development interpreter was used. No dependency installation or network access occurred.
5. Independent standard-library probe run, bounded by `timeout 120s`, retained in `/tmp/c9e3b8d-review-probes.py` and `.json` (53 result entries, including verification summaries). Exercised all seven independent live-code drifts, all five commit-field names, exact versus prefix matching, malformed/missing observation fields and containers, path substitution, inconsistent baseline commits, duplicate/non-object/malformed JSON, and unchanged-chain tampering. Validator drift correctly downgrades mapping with unmodified source observations. Runtime and ledger dependencies are both included; other drifts do not promote any category.
6. F2: wrapped `check_inventory` and verified exactly one invocation; `screen_runs = 1` and before/after wording describe that measurement accurately.
7. F3: marker, non-null error (including `false` and `0`), status, exit, commit, tree, and report-hash adverse cases fail closed. Actual missing marker/error bytes fail immutable Git binding. The semantic `get("error")` check alone treats missing as null; existing immutable binding protects this exact terminal, so this is not a blocking finding. Verified original G3-P actual commit/tree/report hash independently.
8. F3 timeout: injected `TimeoutExpired`, confirmed `git cat-file blob`, a configured 30-second timeout, and conversion to `ValueError`. This tests timeout handling, not an actual 30-second stall. Additional bounded probes reject absolute, parent-traversal, and missing paths. `git diff --check 40bc64e..HEAD` passes.

## Evidence and safety boundary

The exact snapshot retains categories **6 RETAINED / 1 OFFLINE / 70 FUTURE / 0 INVALID**, identical per identity to the historical snapshot. All **77 PRE_REVIEW identities remain MISSING**, every `qualified_entry` is null, qualification credit is **0**, launchable is **false**, and the two FINAL-only outputs remain unassembled. **G3-L remains NO-GO.**

The 503/503/429 hold language is unchanged. The retained A5/A6 audit records no authenticated expiry/Retry-After adjudication; later success and elapsed time remain insufficient for resumption. The expiry/resumption, restriction-lineage, and denial-root identities all remain FUTURE and unqualified. This review used the referenced retained records; it did not recontact providers or independently recover raw private wire evidence.

The exact diff changes only the offline audit tool, its tests, and documentation/snapshot. No runtime, intake, identity qualification, provider, restriction, credential, capture, dispatch, funding, V10/Axiom, or authority gate was modified or approved. Scores remain 91/200 and formal 1/50; NOT_READY_TO_FUND. Even a subsequent PASS_IN_SCOPE would approve only this offline audit hardening, not G3-L or any operational action.

## Reproduction artifacts

- `/tmp/c9e3b8d-review-probes.py` — SHA-256 `00f72a25d7233e7be1f509bdf2372f677e24adf959a987c363a6eda657094575`
- `/tmp/c9e3b8d-review-probes.json` — SHA-256 `5e07d87a654532c5356c1f4fc5104c26201d15158e8f7b0b08c59c2fec5217fc`
- `/tmp/c9e3b8d-review-extra.py` and `.json` — supplementary path and missing-error checks.

No merge, publication, provider request, credential access, capture, dispatch, safety-gate change, or modification of retained evidence occurred.
