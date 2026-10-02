**CHANGES_REQUIRED — independent Astra/high exact-commit review. A4 remains OPEN; G3-L remains NO-GO.**

Reviewed Sol's offline runtime-verifier repair at commit `28dd60403bdc8e819e64e9dd60be0109669c4960`, tree `c5dfc479f083a8fdcfa1d897e69a927871a1b9ee`, against `ef53d61174e7c23e58ddf879d94918c0eafd58e1`. The detached checkout `/tmp/alpha-v11-gate3-a4-review-28dd604` was clean before and after review. All four changed files were inspected, together with the A4 acceptance row and prior F1–F6 review. The original six examples are repaired, but two adjacent counterexamples prevent acceptance of this repair as an offline A4 component.

**R1 — P1: Git object lookup can execute an unpinned helper before refusing.**

Location: [Git environment and subprocess boundary](/tmp/alpha-v11-gate3-a4-review-28dd604/tools/v11_r09_gate3_a4_verify.py:70), with `_GIT_ENV` at lines 35–37 and the first object lookup at line 299. Disabling replacement objects and global/system configuration does not disable repository-local `.git/config`, partial-clone fetches or transport helpers. The Git child runs before the runtime child's seccomp restriction and before the interpreter/bootstrap checks.

The [independent local-only probe](/tmp/alpha-v11-gate3-a4-review-28dd604-probes.py:193) first creates a valid pinned lock and durable record, then changes only the temporary repository's Git configuration and removes its loose commit object. It sets an origin promisor remote, partial-clone configuration, `protocol.ext.allow=always`, and the URL `ext::/usr/bin/touch <temporary-marker>`. Calling `open_verified()` raises `GIT_IDENTITY_UNAVAILABLE`, **but the marker already exists**. The unpinned helper executed while the verifier attempted to read the missing commit. No source, lock, environment pin, or Git executable digest needed changing. The probe used no network address or request.

This is a pre-refusal execution gap, not a successful Git hash forgery. Locally recomputing commit/tree/blob hashes remains useful, but happens too late to prevent helper side effects. The documented future Git loader-closure review does not make arbitrary repository-selected helper execution safe in the present offline verifier.

Required repair: make object queries incapable of fetching or executing unverified helpers through mutable repository configuration. Establish a reviewed isolated object/configuration boundary and/or enforce helper and network restrictions before those queries. Missing commit, tree and blob objects must fail without helper side effects. Retain the exact local promisor counterexample as a regression test; do not rely solely on environment scrubbing or a Git-version-dependent flag without verifying its behavior.

**R2 — P3: the host-module collision guard is checked only when the session opens.**

Location: [one-time collision check](/tmp/alpha-v11-gate3-a4-review-28dd604/tools/v11_r09_gate3_a4_verify.py:321), `check_all()` at line 500, and [from-import handling](/tmp/alpha-v11-gate3-a4-review-28dd604/tools/v11_r09_gate3_a4_verify.py:555).

The [independent probe](/tmp/alpha-v11-gate3-a4-review-28dd604-probes.py:180) locks `from pkg import ghost` and a call to `ghost.value()`. After opening the session, the host registers a fileless `types.ModuleType('pkg.ghost')` containing an unpinned function. `check_all()` accepts because the Python bootstrap inventory records file paths, not the complete module namespace. `run()` also accepts: CPython's `IMPORT_FROM` fallback finds the inherited host module and the child calls its function, returning `616`.

This requires a colliding host module to appear while the session is open. It does not require the locked child to access `runtime.__globals__`, bypass seccomp or mutate its own verifier. Construction-time collision refusal and ordinary missing-import refusal both pass. The remaining defect is specifically the period between opening and execution; the candidate's stated closure of host fallback is incomplete.

Required repair: validate or scrub the inherited module namespace in the forked child before locked execution, and make unresolved from-imports refuse before CPython can fall back to host `sys.modules`. A parent-only repeated check would still leave a fork-time race in a threaded host. Add the late fileless-module counterexample.

No additional P2 finding is raised. These are unresolved adjacent gaps, not claims that every part of the repair regressed. Bootstrap qualification, native integration and hostile-code sandboxing remain explicitly unaccepted prerequisites rather than newly scored defects.

**Disposition of the six prior findings.**

| Prior finding | Independent result and remaining boundary |
| --- | --- |
| F1, P1 — restart build binding | Repaired within the coordinator-custody contract. A changed build refuses in a fresh Python interpreter without `resume_lock_sha256`. Missing records and live record replacement refuse; reopening the same lock succeeds. The coordinator must independently preserve run identity, lock/record location and record custody. Inventing a new run or deleting/recreating its authority is not authenticated by this library. |
| F2, P1 — Git replacement/environment spoofing | Original blob-replacement probe refuses; a commit replacement is ignored. Simultaneously pinned `GIT_DIR`, work-tree/object/alternate directories, replacement namespace, config overrides and `GIT_EXEC_PATH` do not enter the scrubbed Git child. Raw object and local blob hashing close the original identity forgery. R1 remains an adjacent configuration/execution gap. |
| F3, P2 — ambiguous JSON | Top-level and nested duplicates, noncanonical whitespace, BOM, `NaN`, `Infinity` and `-Infinity` refuse. Canonical bytes are checked before the record can authorize parsing-dependent behavior. |
| F4, P2 — pure-Python bootstrap binding | Changed source, distinct-inode same-byte replacement, added and removed file-backed modules refuse. The verifier now binds the loaded `.py`/`.pyc` path/device/inode/hash set and documents that file binding is after load. It does not establish that in-memory code originally came from those bytes or verify bootstrap before import. |
| F5, P3 — metadata mutation | Original chmod refusal passes. Independent raw syscall probes for all 24 listed chmod/chown/xattr/time/node/pidfd/process-memory calls return `EPERM` in the confined child. This is evidence for these calls, not exhaustive sandbox qualification. |
| F6, P3 — from-import host fallback | Existing host top-level/submodule collisions refuse at open. A missing child without a host fallback refuses. R2 reproduces the late-registration case and leaves this boundary incomplete. |

**Validation and evidence integrity.**

| Check | Result |
| --- | --- |
| Exact commit/tree, detached state, clean working tree | Matched supplied identities; unchanged at completion |
| `git diff --check ef53d61174e7c23e58ddf879d94918c0eafd58e1..28dd604` | Clean |
| Candidate's three claimed file hashes | All match |
| Four retained author log hashes | All match the candidate terminal |
| Focused candidate tests | **27 passed**, 15.55 s |
| Remaining 11 Gate 3 test files | **551 passed**, run independently with bounded fixture retention |
| Combined candidate test family | **578 passed** across 12 files; not a shared-directory run |
| Final independent probe artifact | **22 passed**, 22.15 s: **20 controls and 2 defect reproductions** |

The adjacent results are collector 49, preparation 16, H1–H6 29, launch 71, V4 launch 58, ledgers 52, message sizes 3, offline I/O 48, restart composition 7, runtime 109 and store 109. Store tests emit the same two fork/thread deprecation warnings represented in the author log. No candidate-test failures remain in the corrected review run.

The author's retained shared-directory log reports 551 passes and 26 `REPORT_RESERVE_UNAVAILABLE` failures. Its disk-exhaustion explanation is consistent with those failures and successful fresh-space reruns; historical free-space telemetry was not independently recreated. It is **not accepted as a clean shared-directory run**. Its 577 recorded outcomes also must not be substituted for the final 578-test population. The separate author per-file log totals 578 and is corroborated by this review's bounded runs.

Each review test process used a unique temporary basetemp and a timeout. For adjacent files, the probe artifact's cleanup plugin removed test contents only after fixture finalization while retaining empty numbered directories until process completion. The largest observed completed-test allocation was **67,342,336 bytes**. Approximately 1.44 GiB remained free at completion. Review-owned temporary directories were removed; existing scratch, the candidate and the active A7 lane were untouched.

Two review-harness issues are disclosed in the terminal with their initial outputs. An unlink/recreate bootstrap control could reuse the inode; retaining the old inode made the replacement control deterministic. The first cleanup implementation removed numbered directories and caused pytest to reuse paths cached by runtime test helpers, producing 18 lineage-head failures. Preserving empty directories fixed the harness; all 29 H1–H6 tests then passed. Neither initial result is counted as a clean run or concealed as a candidate regression.

Reproduce the independent tests from the candidate checkout with `PYTHONDONTWRITEBYTECODE=1 PYTHONPATH="$PWD" /home/alphaadmin/AlphaV11_Dev/venv/bin/python -m pytest -q -p no:cacheprovider --rootdir=. --basetemp=<fresh-review-owned-directory> /tmp/alpha-v11-gate3-a4-review-28dd604-probes.py`. Tests named `test_gap_*` deliberately assert the two defects. The artifact also exposes `--adjacent-child <fresh-basetemp> <test-file>` for bounded adjacent testing. Exact executed commands, outputs and output hashes are retained in the terminal.

**Acceptance prerequisites; no A4 or launch promotion.**

- **Repair acceptance:** resolve R1 and R2 and obtain a fresh independent review of the exact resulting commit/tree. Do not treat the 578 passing tests as overriding the independent counterexamples.
- **A2:** authenticate the original package/source artifacts and provenance; bind exact hashes, build recipe, toolchain, options, patches, platform and ABI. Supply individually reviewed explanations for all 25 `eckitlib.libs` RECORD discrepancies tied to actual loaded payloads. Local wheel/install agreement does not establish this.
- **A3:** accept a complete reproducible lock and its independent reconstruction in a separate unprivileged environment. Cover Python/interpreter, wrappers and transitive Python modules, CFFI/NumPy where used, native loader and full dependency closure, definitions/samples and environment/search rules, with exact source/output hashes. Separately review the translator from that accepted lock to this candidate format. Synthetic self-generated pins do not qualify a build.
- **A4:** bind the actual reviewed entrypoint and every transitive executable to commit/tree/source hashes; verify before import, `dlopen`, constructors or decode, including the bootstrap and Git/helper closure. Bind actual mappings and ecCodes definitions, samples and embedded MEMFS selection through use to the same accepted A2/A3 and static inventory bytes, using immutable snapshots or a reviewed equivalent. Demonstrate changed wrapper/native/MEMFS/decoder bytes, environment/loader overrides, path/symlink races, unexpected lazy loading and different-build restarts all fail before unverified execution. Preserve durable independently identified run/build custody. This candidate exposes no native decoder call or accepted loader integration; after-load bootstrap file checks cannot supply pre-import verification.
- **A5:** separately qualify GEFS, IFS and AIFS release documents and retrieval provenance, effective intervals, licence/access/restriction lineage, origin/purpose, control/perturbed member layout, native hours and exact GRIB parameter/level/centre/tables/process/status/product/grid/packing semantics against retained real headers and object/index/range evidence.
- **A6:** supply genuine current-run readiness within the frozen age rule, pinned index bytes/row/object size/ETag/coherent range identity and independently frozen required section hashes. Preserve distinct request-start, body-receipt, decode-complete and durable-seal bounds. Historical/synthetic evidence cannot become current-run evidence; missing pins stay null. Any authorization dependency remains unresolved rather than creating a network exception.

A7 realistic decoder/resource qualification and A8 concrete launch composition remain separate, followed by the remaining inventory evidence, PRE_REVIEW, independent exact canonical G3-L package review and terminal, and FINAL. **G3-L remains NO-GO without that separate launch evidence.** This review does not fill dependency-build, decoder-identity or runtime-entrypoint launch fields.

**Artifact binding and scope.**

The prior review was absent from this candidate tree and was read without modification from `/home/alphaadmin/AlphaV11_Dev/Alpha/docs/V11_R09_GATE3_A4_REVIEW_ef53d61.md`. Its report SHA-256 `fb9e5894c80df8cad5fda980cba3dc3337465a77bb90a0e019d940f0698c4939` and original probe SHA-256 `3eaf57771d040373ed16f62aaf01f14e00e8c92df7ef9765513307af8b2b43cf` match that review's terminal.

The final [independent probe artifact](/tmp/alpha-v11-gate3-a4-review-28dd604-probes.py) SHA-256 is `50151af5354d5dc6d24a35f6c5088bac2ff58a371ba36c825b9d3f83b6b3b8da`. The [machine terminal](/tmp/alpha-v11-gate3-a4-review-28dd604_terminal.json) binds this report's exact SHA-256, that probe hash, the commit/tree, inspected candidate and prior-evidence hashes, all retained test results, and the verdict.

Only these review artifacts and automatically removed synthetic test fixtures were written. Candidate/main code was not modified. Execution was unprivileged (UID 1000); the sandbox wrapper initially failed before command execution, so approved local sandbox overrides were used. No provider/network request, financial action, V10/AxiomTrade work, service or root-authority action, credential access, merge or push occurred.
