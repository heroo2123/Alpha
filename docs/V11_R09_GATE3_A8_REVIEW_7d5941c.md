# Independent exact-commit A8 offline composition-preparation review

**CHANGES_REQUIRED. G3-L remains NO-GO.** Two P2 defects remain in the new checker’s adapter and live-context identity checks. Neither is a demonstrated real launch bypass: the candidate has no launch/provider-request path, and unchanged V4 deliberately refuses every currently representable real three-provider package.

Reviewed only detached, clean `/tmp/alpha-v11-gate3-a8-review-7d5941c`:

- Commit: `7d5941cdae0f4f74383a86969b4bfd968c304d98`
- Tree: `1fe9271848e8f1b7e9a6fabd86094a635f882378`
- Parent: `6ce37ab6ba8b93bafacdcb1597f53916d994eea7`
- Reviewer: independent Astra/high review of the Sol-authored candidate.

The four-file diff, candidate document/terminal, identity acceptance criteria, prior G3-L preparation and repair reviews (`3241abf`, `f03d2fd`), mapping and repair reviews (`15f054f`, `23c11e0`), synthetic composition review (`0b7209d`), and slice-3 review (`6340cb4`) were read. Relevant V4, FrozenPlan, runtime, store and inventory implementations/tests were inspected. This is an offline preparation review, not A1–A8 acceptance, decoder qualification, integration approval or G3-L acceptance.

## F1 — P2: Source pinning does not bind loaded class methods, and changed method code survives recheck

Locations: `tools/v11_r09_gate3_a8_composition.py:93–123`, particularly `117–123`; identity comparison at `393–395`.

For class adapters, `_component` obtains the class’s source path and hashes that file. It then accepts any Python function currently assigned to the selected class method, provided there is no instance-level override. It does not establish that this function is the implementation in the pinned source. A class-level replacement from the external probe file passes while the actual repository source, source hash and qualified class name remain unchanged.

More directly, the returned identity records `id(method)`, but not the method’s code object. After preparation, assigning a different `Store._check_dirs.__code__` changes behavior while retaining the object, class, function identity and every source-file identity field. The second `_component` result is exactly equal to the first. `recheck_a8_composition` accepts this unchanged tuple.

Reproduction in the preserved probe file:

- `test_f1_foreign_class_method_is_accepted_as_pinned_source`: temporarily replaces `VersionedImmutableObjectStore._check_dirs` with an external function; `_component` accepts it as the pinned store implementation.
- `test_f1_changed_method_code_survives_composition_recheck`: records the genuine method, changes only its `__code__`, demonstrates the new return value, and reproduces an accepted recheck comparison. The original code is restored in `finally`.

The latter deliberately substitutes `validate_a8_composition` with a small shim returning the **real, unchanged `_component` result**, to reach the recheck comparison independently of the currently impossible real V4 package. It does not demonstrate an end-to-end real-package acceptance. Neither probe edits any candidate source file.

Required correction: bind the loaded callable implementation to the reviewed source through a reviewed loading/verification boundary, and retain/recheck the executable identity of all relevant methods. At minimum, reject foreign class methods and detect in-place method-code substitution; a function object ID alone is insufficient. Cover the before-preparation replacement and after-preparation code-change cases with refusal tests. Do not claim that this supplies the separately required A4 native/transitive immutability boundary.

## F2 — P2: The live storage/clock context is compared with itself rather than fully with the reviewed context

Locations: `tools/v11_r09_gate3_a8_composition.py:230–264`, especially `235`, `248–249`, `258–264`; recheck retains only host tuple elements `0:3` at `395`.

The package binds `plan.runtime_context_raw`, which includes the reviewed `clock_method`, `store_descriptor` and `store_policy`. `_fresh_context` extracts only its `boot_id`. The observed clock method need only equal mutable `storage.context['clock_method']`. The store need only match the plan’s manifest and its own internally recorded directory identities. No comparison binds its descriptor or policy to the reviewed runtime context. Directory checking also does not require the store to remain usable.

Four passing counterexamples in `test_f2_changed_live_context_still_passes` start with a real `VersionedImmutableObjectStore` on a disposable private root and a passing isolated context check. Independently changing:

1. both the clock’s method and the store’s method from `reviewed-clock` to `unreviewed-clock`, leaving the plan’s reviewed bytes unchanged;
2. `store.descriptor_sha256`;
3. `store.context['policy']`; or
4. `store._failed` to true

still passes `_fresh_context`. All retain the same boot/device/inode tuple used by the outer recheck. The fixture clock/resource values are explicitly synthetic; this exercises the context helper, not `_adapters` or real qualification. The actual store directory/ownership checks execute unmodified. The first case directly contradicts the candidate’s claim that a fresh sample must match the **reviewed** method.

Required correction: compare actual clock method, store descriptor and policy with the reviewed context and manifest; require healthy usable storage; preserve/recheck the applicable identities. Add positive exact-context and negative changed-context tests. Existing `GateRuntime` already compares the complete expected/actual runtime context at `tools/v11_r09_gate3_runtime.py:938–951`; that later check does not establish that this independent A8 preparation result is accurate. Binding mutable fields to one another does not bind them to reviewed evidence.

## Verification and scope of passing checks

All three author-listed SHA-256s match exact candidate bytes:

| File | SHA-256 |
| --- | --- |
| `docs/V11_R09_GATE3_A8_COMPOSITION_PREP_20261001.md` | `b6715c91303d2726c0eb5e4b370dfff80faefe198606ab371518ace509e8dc7b` |
| `tests/test_v11_r09_gate3_a8_composition.py` | `d6e21348ca242349e42ebe1e2aee26640151b5122723d4dbf48ec4906d099b70` |
| `tools/v11_r09_gate3_a8_composition.py` | `42c0a5d5926e2f9dabc83a0bde28ce18ab892bec9ac751b00842a7dddbb8af9c` |

Author terminal SHA-256: `be1caa7ea3cd4419937bddd2f42f29f6be6be963e1f5a5ed1e6254b805254cc2`.

Final independent executions:

- Reported four-file targeted suite: **194 passed**, independently reproduced (27.51 seconds).
- Adjacent store-v1/restart-composition suite: **116 passed**, with two existing multithreaded-fork deprecation warnings.
- Independent review probes: **19 passed**, including six tests that positively reproduce F1/F2. Passing counterexample tests establish defects, not acceptance.
- `git diff --check` against the exact parent passed. Detached HEAD/tree and clean status were rechecked.

Commands use `PYTHONDONTWRITEBYTECODE=1 /home/alphaadmin/AlphaV11_Dev/venv/bin/python /tmp/alpha-v11-gate3-a8-review-7d5941c-probes.py`, with `--author`, `--adjacent`, or no argument respectively. The runner invokes pytest with cache disabled, importlib mode, a newly generated `/tmp/a8-7d5941c-*` basetemp, and a socket-connect audit refusal. It cleans only completed tests’ own scratch and closes leftover descriptors beneath those unique paths. No product assertion or active test reservation is reduced. Targeted-run minimum sampled free disk at teardown was **1,321,447,424 bytes**; this is not a continuous peak measurement or launch capacity qualification.

Two preliminary targeted attempts are preserved transparently: the initial cleanup removed path names that pytest then reused, conflicting with fixture-local cached ledger heads (166 passed/28 failed); keeping empty name markers fixed that review-harness problem. The next run obtained 193 passes and one `REPORT_RESERVE_UNAVAILABLE` failure because unclosed report descriptors retained unlinked reservations until process exit. Closing only completed-test scratch descriptors yielded the final 194 passes. These are not additional candidate findings. No other worker’s scratch was removed.

The independent probes check the external review-hash mismatch and exact manifest/inventory bytes; every one of 79 FINAL identities when missing or hash-corrupted (158 negative checks); all seven prerequisite names; all 20 component-review names and identity bindings; all 15 purpose/parser bindings; source hash/inode and object substitution; and fresh clock, boot, disk, memory, manifest and directory-mode refusals. The unchanged product tests additionally cover sealed-byte/symlink refusal, synthetic adapter/manifest refusal, FrozenPlan/V4 projection and review reconciliation, plan/window mismatch, and point-of-use package/object/window refusal. The V4 real-scope refusal was independently repeated without modifying its mapping-validation logic.

No real-purpose positive control is claimed. The typed purpose helper can match fabricated dictionaries, and the prerequisite helper accepts meaningless evidence wrapped in correctly bound `ACCEPTED` records. The candidate explicitly documents these as byte/schema checks requiring external substantive review and authentication. Therefore this behavior alone is **not** a new authority bypass: an independently supplied hash is necessary but does not authenticate a reviewer. The successful synthetic helper controls establish neither real contracts nor A1–A7 acceptance. That external boundary must remain explicit.

Documentation correction: the candidate links `G3L_PREP_REVIEW_3241abf` as accepting the inventory/planner, but that report says CHANGES_REQUIRED. The accepting repair report is `G3L_PREP_REPAIR_REVIEW_f03d2fd`; cite the repaired lineage accurately.

## Remaining launch prerequisites

1. Repair F1/F2 and independently review the resulting exact integration commit/tree after reconciliation. This preparation checker still grants no runtime authority.
2. Obtain separately accepted A1 evidence; authenticated A2 provenance with every RECORD discrepancy adjudicated; complete reproducible A3 Python/native/data/environment closure; and A4 verification before import/native execution with substitution protection through use.
3. Obtain genuine A5 provider/model/release/access/restriction semantics and A6 selected-run readiness, independent section pins, index/object/range identities and causal receipts. Historical or synthetic data cannot fill current-run identities.
4. Complete A7 real retained-field and adversarial decoder/resource qualification on the locked build/ABI, including the required predecode checks and permitted bounded CCSDS integrity work. This review neither interferes with nor accepts the active A7 repair.
5. Supply separately reviewed real contracts for all 15 provider/purpose combinations, implement and independently review the currently unsupported mappings, and qualify the actual transport/clock/storage/resource adapters and decoder integration. Preserve real-origin refusal until that work is accepted.
6. Assemble genuine cohort, schedule, restriction/denial, custody/storage and clock evidence; reconcile exact canonical V4 bytes with FrozenPlan and actual integration identities; measure fresh host headroom and establish physical reservations. Current disk remains below the 2 GiB floor, before prospective reservations.
7. Complete the 77-input PRE_REVIEW assembly, substantive independent exact-package G3-L review and completed terminal, then all 79 FINAL identities and point-of-use checks. Resolve any current-run acquisition dependency through separate protocol/owner authorization; this review authorizes no provider preflight or exception. A8 and G3-L acceptance must not be inferred from these offline tests.

G3-E, feature/Brain admission and genuine forward SHADOW remain separate gates. No provider/network request, financial action, V10/AxiomTrade operation, service/authority/credential change, merge or push occurred. Candidate/main files were not changed. Outputs and disposable fixtures were confined to review-owned `/tmp` paths.

## Bound artifacts

Probe/runner: `/tmp/alpha-v11-gate3-a8-review-7d5941c-probes.py`, SHA-256 **`07be197274dae19df41886e4d40ae1bca55782b1b135edfe106da817a73e2210`**.

`/tmp/alpha-v11-gate3-a8-review-7d5941c_terminal.json` binds this report’s exact SHA-256, the probe SHA-256, all retained execution logs, author artifacts and inspected review documents to the exact commit/tree/parent above. It records preliminary attempts separately from the final results.
