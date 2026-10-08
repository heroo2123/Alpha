# InventoryTransform offline SHADOW observer: start contract

**Current status: reviewed and integrated, observer dormant.** The accepted start contract and subsequent hardening are recorded by the reviewed merges through `5394d19`; the original candidate below was based on `3b7cb091c09ab61d9949e1d87f577f3ce807ce0c`. Section 9 retains the historical candidate review handoff. No observer start is claimed. This contract is independent of weather Gate 3 and grants nothing to it. `qualification=false`; `financial_authority=false`; G3-L, funding, and score are unchanged by this document.

The accepted one-shot observer (`polymarket_scanner/v11/inventory_shadow.py`, reviewed at `2033b82`) was left untouched by the original contract commit `0998743` but is **changed** by the hardening commit `5601851` (independent review findings L2/L5): `write_artifact` now rejects an artifact with any extra/missing top-level key or a non-`int` evidence-class count (L2), and takes an optional `dir_fd` parameter so it can write through the already-validated, already-locked output directory descriptor instead of re-opening the path by name, closing a same-uid rename race between validation and write (L5). This candidate's runner, `polymarket_scanner/v11/inventory_shadow_start.py`, focused tests, and this document complete the rest of the change set. There is no daemon, service, timer, or registration.

## 1. Allowed inputs

Only local saved JSON fixtures named explicitly on the command line: `--input FILE` (repeatable, at most 32) or `--input-dir DIR` (every entry of that one directory, non-recursive, at most 32). There is no default path, discovery, download, or watch.

Each fixture is one JSON object:

| Field | Requirement |
| --- | --- |
| `fixture_version` | required, string of 1–256 characters |
| `evidence_class` | required, exactly `API_OBSERVED` |
| `source` | required, `structural_evidence.Source` shape; `raw_sha256` must be present and non-null (lineage of the saved response) |
| `payload` | required, saved activity page object |
| `coverage` | required object; `coverage.state` must equal the coverage the engine computes |
| `chain_status` | optional; if present, exactly `CHAIN_UNVERIFIED` |
| `synthetic_conversion` | optional; accepted synthetic NegRisk model input, `SYNTHETIC_ONLY` route |
| `raw_source_file`, `expected`, `unresolved` | optional annotations; not read, not verified, not copied to output |

Any other top-level field refuses the start.

Evidence classes stay distinct:

- `API_OBSERVED`: the fixture rows and reconciliation. Vendor observations only.
- `SYNTHETIC_PROOF`: only from `synthetic_conversion`, reported separately with `coverage: null`. It is never merged into API rows and proves nothing about a deployed route.
- `CHAIN_RECEIPT`: count is always zero. This runner has **no receipt acceptance path**. A fixture with `evidence_class: CHAIN_RECEIPT`, a non-`CHAIN_UNVERIFIED` `chain_status`, or any receipt-shaped extra field is refused. Supplying authentic local receipt evidence requires a separate, separately reviewed acceptance path and a new contract version; it cannot be enabled by a flag or fixture content here.

## 2. Output identity, replay, idempotence

`--output-dir` must already exist, be reached without any symlink, be owned by the invoking user, and not be group- or other-writable. It is dedicated to this observer: it may contain only observer artifacts.

Each (observer version, input file bytes, event) triple owns exactly one file:

```
inventory-shadow-<sha256(json([observer VERSION, source_file_sha256, event_slug]))>.json
```

File content is the accepted observer's canonical report (sorted keys, ASCII, one trailing newline) with `observation_id = sha256(report without observation_id)`. Content depends only on the input bytes and the event, never on time, host, path, or environment.

- First run creates the file (create-once hard link; never rename-over).
- Replaying the same input and event verifies the existing file byte-for-byte, leaves its inode and mtime unchanged, and reports `created: false`.
- An existing file is never replaced or repaired. Different bytes at an owned name refuse the start.

The runner writes nothing else: no manifest, log, state, or lock file. It prints one JSON summary line on stdout (`version`, `activation`, authority flags, and per input `artifact`, `source_file_sha256`, `observation_id`, `coverage`, `created`). Only `created` differs between a first run and its replay.

## 3. Isolation

- **No sockets, DNS, HTTP, or child processes.** The runner and observer import only the standard library, `structural_evidence`, and `neg_risk_contract`. The process entry point additionally installs a Python audit hook that raises on every `socket.*` event, on process-spawn events, and (since the hardening commit `5601851`, independent review finding L1; extended by independent review finding L-B to add `_cffi_backend`, closing the same raw-libc-handle evasion reachable through `cffi.FFI().dlopen(None)`) on every `import` audit event whose module name's last dotted component is `ctypes`, `_ctypes`, `_posixsubprocess`, or `_cffi_backend`, before any input is read (independent review finding F2 of the L-B repair: an exact-name check misses the same extension file reimported under a dotted alias such as `aliaspkg._cffi_backend`, so the match is on the final component, not the whole dotted name). The matcher denies outright, rather than matching against the denied set, any import name that is not exactly `type(name) is str` or that contains a NUL byte (independent review findings R1/R2 of the F1/F2 repair, candidate `polymarket_scanner/v11/inventory_shadow_start.py`, pending the independent review required before integration; see section 8). The `import` audit event is raised only when a module is actually loaded, not when an already-loaded module is looked up again, so the process entry point also refuses with `DENIED_MODULE_PRELOADED` if any module already in `sys.modules` when the hook is installed has one of those four names as its last dotted component; that preload scan shares the same matcher, so it fails closed the same way. This is an in-process tripwire supporting source inspection; it is not an OS network sandbox, and it only ever sees an event CPython chooses to raise for the `import` audit hook — a load that raises no such event is invisible to it regardless of name (limits in section 8, finding R3).
- **No account, order, or collateral effects.** No such module is imported; `account_effects` and `order_effects` are always `[]`; activity cash is a vendor observation, not collateral.
- **No credentials.** The runner reads no environment variable or credential file. The start command runs under `env -i`.
- **No weather SHADOW coupling.** The process entry point refuses to run if any `polymarket_scanner` module other than the package roots, `safe_logging`, the observer, the runner, `structural_evidence`, and `neg_risk_contract` is loaded, before and after the run. The dedicated-directory rule refuses an output directory that holds any other file, so weather SHADOW state cannot share it. Nothing in weather SHADOW imports or reads this observer.

## 4. Startup refusal conditions

Every refusal exits `2` with `inventory shadow start refused: <CODE>` on stderr. All inputs are observed in a first pass before any write of this batch begins, so a refusal raised before the write phase (process-entry checks, output-directory checks, verification of existing artifacts, and the first pass over the inputs) creates no artifact. That guarantee ends once the write phase begins. The second pass re-reads each input from disk immediately before writing it, so a refusal can also occur mid-batch, after earlier artifacts of the same batch were already written: a second-pass input re-read refusal (`INPUT_CHANGED_DURING_START`, or any input code in the table below that the re-read can still raise), `RUN_TIME_LIMIT`, or an output I/O error. `MODULE_COUPLING_DETECTED_AFTER_RUN` is reported after the whole batch was written, and `OUTPUT_DIR_ENTRY_LIMIT` can refuse a later invocation because of artifacts earlier invocations wrote. Section 6 lists these post-write classes; no refusal there removes an artifact.

| Condition | Code |
| --- | --- |
| Running as root | `ROOT_REFUSED` |
| Input missing, symlink (final or ancestor), FIFO, directory, other nonregular | `INPUT_NOT_REGULAR_FILE`, `INPUT_PATH_INVALID` |
| Input directory is a symlink / unreadable | `INPUT_DIR_REFUSED` |
| Input directory entry is a symlink, subdirectory, FIFO, hidden, or not `*.json` | `INPUT_DIR_ENTRY_REFUSED` |
| No inputs; both selection forms | `EMPTY_BATCH`; `INPUT_SELECTION_AMBIGUOUS` (argparse error on the CLI) |
| Completeness required (`--require-complete`) but coverage is INCOMPLETE or UNKNOWN | `COMPLETENESS_REQUIRED_BUT_INCOMPLETE`, `COMPLETENESS_REQUIRED_BUT_UNKNOWN` |
| Fixture declares a coverage the engine does not compute (e.g. claims COMPLETE) | `DECLARED_COVERAGE_MISMATCH` |
| Coverage declaration absent | `COVERAGE_DECLARATION_REQUIRED` |
| Malformed JSON, envelope, source, metadata, nesting, synthetic input | `INPUT_INVALID_JSON`, `INPUT_SCHEMA`, `SOURCE_*`, `ROW_METADATA_INVALID`, `INPUT_NESTING_BOUND`, `SYNTHETIC_*`, `EVENT_SLUG_INVALID` |
| Missing fixture version | `FIXTURE_VERSION_REQUIRED` |
| Unsupported evidence class or chain status; unreviewed field | `API_OBSERVATION_CLASS_REQUIRED`, `ROW_EVIDENCE_CLASS_INVALID`, `CHAIN_STATUS_UNSUPPORTED`, `CHAIN_RECEIPT_NOT_ACCEPTED`, `UNSUPPORTED_FIXTURE_FIELD` |
| Missing required lineage (`source.raw_sha256`) | `LINEAGE_RAW_HASH_REQUIRED` |
| Existing artifact from another observer version | `OUTPUT_STALE_VERSION` |
| Leftover temporary from an interrupted run | `OUTPUT_STALE_TEMPORARY` |
| Existing artifact altered, non-canonical, policy-violating, wrong identity, or not a regular file | `OUTPUT_TAMPERED_*`, `OUTPUT_IDENTITY_MISMATCH`, `OUTPUT_NOT_REGULAR`, `OUTPUT_CONFLICT` |
| Output directory absent, symlinked, not private, shared with other files, or in use by another start | `OUTPUT_DIR_REFUSED`, `OUTPUT_DIR_NOT_PRIVATE`, `OUTPUT_DIR_FOREIGN_ENTRY`, `OUTPUT_DIR_BUSY` |
| Resource caps: input > 2,000,000 bytes; > 32 inputs; > 1024 output entries; artifact > 4,000,000 bytes; run > 120 s; per-file 2 s; input changed between passes | `INPUT_BYTE_LIMIT`, `BATCH_INPUT_LIMIT`, `OUTPUT_DIR_ENTRY_LIMIT`, `ARTIFACT_BYTE_LIMIT`, `RUN_TIME_LIMIT`, `INPUT_TIME_LIMIT`, `INPUT_CHANGED_DURING_START` |
| Output write or directory I/O error during the write phase | `OUTPUT_IO_REFUSED` |
| `ctypes`, `_ctypes`, `_posixsubprocess`, or `_cffi_backend` already loaded when the process entry point installs the audit hook | `DENIED_MODULE_PRELOADED` |
| Other project module loaded in the process before the run; after the run (artifacts already written) | `MODULE_COUPLING_REFUSED`; `MODULE_COUPLING_DETECTED_AFTER_RUN` |

More than 2,000 rows in a page is not a refusal: the page is truncated and coverage is lowered to INCOMPLETE with `RECORD_LIMIT` (inherited, accepted behaviour). Without `--require-complete`, INCOMPLETE and UNKNOWN coverage are not refusals either; they are carried into the artifact and the summary unchanged, with `EVENT_WINDOW_COVERAGE_UNPROVEN`.

## 5. What "activated" means

After acceptance, "activated" means exactly: an operator may run the start command below, by hand or from an operator-owned unprivileged shell, against explicit local fixtures, and the process may emit SHADOW diagnostics into its dedicated directory and exit.

It does **not** mean, and no output may be read as:

- qualification of any strategy, route, or inventory transform (`qualification: false`);
- transaction-level or chain proof (`transaction_level_proof: false`, `chain_status: CHAIN_UNVERIFIED`, `receipt_status: NO_VERIFIED_RECEIPTS`);
- financial authority, funding readiness, or any G3-L change (`financial_authority: false`);
- deployment of trading logic. No order, account, collateral, paper, or live path consumes these artifacts, and none may be wired to them under this contract.

Even COMPLETE coverage leaves opening inventory, basis, fees, conversion mask, transaction lineage, and deployed route unresolved. The summary states `activation: OFFLINE_SHADOW_DIAGNOSTICS_ONLY`.

## 6. Start command

Unprivileged, no systemd, no background process. It processes the batch and terminates.

```sh
cd /home/alphaadmin/AlphaV11_Dev/Alpha   # checkout at the accepted commit
mkdir -m 700 -p "$HOME/inventory-shadow-out"
env -i PATH=/usr/bin:/bin PYTHONDONTWRITEBYTECODE=1 \
  timeout 180 /home/alphaadmin/AlphaV11_Dev/venv/bin/python \
  -m polymarket_scanner.v11.inventory_shadow_start \
  --input-dir /path/to/explicit/fixtures \
  --event highest-temperature-in-singapore-on-october-3-2026 \
  --output-dir "$HOME/inventory-shadow-out"
```

Use `--input FILE` (repeatable) instead of `--input-dir` for single fixtures. Add `--require-complete` when a downstream reader would need window completeness. Exit `0`: all inputs processed, summary on stdout. Exit `2`: refused. Any other exit (including the `timeout` kill at 180 s or a tripped audit hook) is a failure to investigate, not a result. Do not run it under `sudo`, a unit file, cron, or `nohup`.

One event per invocation. Exit `2` does not by itself mean the output directory is untouched: only a refusal raised before the write phase guarantees that no artifact was created. A refusal after the write phase has begun may leave earlier artifacts of the batch in place; each is complete, valid, and replayable, and none is removed or rolled back. The post-write refusal classes (independent review of `5601851`) are:

- `INPUT_CHANGED_DURING_START`: the second-pass re-read of an input no longer reproduces its first-pass observation;
- second-pass input re-read refusals: `INPUT_NOT_REGULAR_FILE`, `INPUT_PATH_INVALID`, `INPUT_BYTE_LIMIT`, `INPUT_TIME_LIMIT`, `INPUT_INVALID_JSON`, `INPUT_SCHEMA`, `SOURCE_*`, `SYNTHETIC_*`, and every other per-input code in section 4, when an input is removed, replaced, or altered between the passes;
- `RUN_TIME_LIMIT`: checked before each input of each pass, so it can fire between two writes;
- I/O errors: `OUTPUT_IO_REFUSED`, `OUTPUT_NOT_REGULAR`, `OUTPUT_CONFLICT` from the write itself;
- `MODULE_COUPLING_DETECTED_AFTER_RUN`: reported after the whole batch was written and its summary printed, if another project module was loaded during the run (review finding L7). The exit code is `2`, but every artifact of the batch is on disk;
- `OUTPUT_DIR_ENTRY_LIMIT` on a later invocation: the cap is checked at start, not at write time, so successful writes can take the directory past 1024 entries and every subsequent start then refuses until an operator intervenes (review finding L6).

The last two are detective, fail-closed checks by design, not write-time guards; their behaviour is unchanged here. Anything that is not a `ShadowInputError` (a tripped audit hook, an unexpected exception, the outer `timeout`) exits with another status at an arbitrary point and may likewise leave earlier artifacts, or a `.inventory-shadow-*` temporary (section 8), in place.

## 7. Tests

`tests/test_v11_inventory_shadow_start.py`:

- start command in a fresh child process with the audit hook active: succeeds with zero socket/DNS/spawn events, emits only the artifact, authority flags false, CHAIN_RECEIPT zero, byte-identical replay;
- the hook denies socket creation, DNS, and child processes;
- the process entry refuses when another project module (weather stand-in) is loaded;
- the runner's imports are exactly the standard library plus the observer and `structural_evidence`; a run loads no weather module;
- directory batch replay: stable names, bytes, inode, mtime; a different event gets a different identity;
- uncertainty: INCOMPLETE and UNKNOWN propagate into artifact and summary; `--require-complete` refuses them; COMPLETE still leaves qualification false and inventory unresolved;
- malformed/unsupported fixtures (13 cases, including CHAIN_RECEIPT, receipt-shaped field, false COMPLETE claim, missing lineage) refuse in the first pass with no output;
- symlink, missing, FIFO, subdirectory, hidden, wrong-suffix inputs; byte, batch-size, and run-time caps;
- stale version, stale temporary, tampered, non-canonical, misnamed, symlinked, and foreign output; shared, symlinked, non-private, locked output directory; root.
- (hardening commit `5601851`) denied-module import (`ctypes`, raw `_posixsubprocess`) is refused by the audit hook (finding L1, `tests/test_v11_inventory_shadow_start.py`); a crafted artifact with an extra top-level key or a non-`int` evidence-class count is rejected (finding L2, `tests/test_v11_inventory_shadow.py`); a same-uid rename of the output directory after validation cannot redirect the write (finding L5, `tests/test_v11_inventory_shadow_start.py`).
- (follow-up to the review of `5601851`) the process entry refuses with `DENIED_MODULE_PRELOADED` and writes nothing when `_ctypes`, `_posixsubprocess`, or `ctypes` was imported before the hook was installed (`tests/test_v11_inventory_shadow_start.py`).
- (independent review of the L-B repair, finding F1) the direct `import _cffi_backend` is denied with the exact refusal message and leaves `_cffi_backend` and `_posixsubprocess` out of `sys.modules`; `cffi.FFI()` still fails afterward. The probe pins `_cffi_backend` by a literal, not by iterating `_DENIED_IMPORT_MODULES`, and uses explicit child-side `sys.exit` codes instead of `assert` (`tests/test_v11_inventory_shadow_start.py`).
- (independent review of the L-B repair, finding F2) a synthetic dotted alias (`aliaspkg._cffi_backend`, backed by the real extension file) is denied the same way at import time, and is also caught by `DENIED_MODULE_PRELOADED` when already in `sys.modules` before the hook installs; if the match ever regresses, the probe proves raw libc reach with `getpid()` only and never opens a socket (`tests/test_v11_inventory_shadow_start.py`).
- (unreviewed candidate, independent review findings R1/R2 of the F1/F2 repair) a direct unit test that `_denied_import_match` fails closed on a NUL-suffixed name and on two different adversarial `str` subclasses (one overriding `rpartition`, one overriding `__eq__`/`__hash__`), while leaving ordinary names such as `json` and `aliaspkg._cffi_backend` unaffected; and two process-level probes, built the same way as the F1/F2 probes, that reach the real `_cffi_backend` extension through `_imp.create_dynamic`/`ExtensionFileLoader` under a NUL-suffixed name (R1, also checked at preload time against `DENIED_MODULE_PRELOADED`) and under a `str` subclass that overrides only `rpartition` (R2); if the match ever regresses, each probe proves raw libc reach with `getpid()` only and never opens a socket (`tests/test_v11_inventory_shadow_start.py`).

## 8. Known limits (stated, not hidden)

- The audit hook and module check are in-process controls. They do not constrain native code or replace an OS sandbox. None of the imported modules contains native project code.
- A regular input file replaced by another regular file at the instant of open is read as the replacement; bytes and hash always describe the opened inode (inherited, accepted limit).
- Time limits are cooperative checks between steps, not hard real-time bounds; the outer `timeout` is the hard bound.
- Startup verification proves an existing artifact is self-consistent (policy, content identity, canonical bytes, name). It cannot prove a self-consistent artifact was derived from a genuine input unless that input is in the current batch, in which case the bytes are compared.
- Verification of existing artifacts reuses the accepted writer, which briefly creates and removes a `.inventory-shadow-*` temporary in the output directory. A crash at that instant leaves a temporary that the next start refuses as `OUTPUT_STALE_TEMPORARY`; an operator removes it by hand after inspection.
- Directory locking is advisory (`flock`) and only excludes other starts of this runner.
- The L1 import-deny hook fires only when a denied module is actually loaded. A denied module already loaded before hook install (for example by a venv `.pth` file) raises no event; the process entry point therefore checks `sys.modules` for the denied names once, right after installing the hook, and refuses with `DENIED_MODULE_PRELOADED` (independent review finding, hardening review of `5601851`; extended to `_cffi_backend` by independent review finding L-B). That check matches the last dotted component of each `sys.modules` key, so a dotted alias such as `aliaspkg._cffi_backend` is caught the same as the bare name (independent review finding F2 of the L-B repair), but it is still only at start: it does not see a reference to a denied module's functions captured before start under another name, an entry removed from `sys.modules` after loading, a `sys.modules` key whose *last* dotted component does not match any denied name while an earlier component does, or any other native extension offering equivalent raw syscall access.
- The L2 artifact-schema check covers top-level keys and `evidence_class_counts` values only. A forged artifact with a recomputed `observation_id` could still hide authority-like keys inside `metrics`, `rows[i]`, `source`, or `reconciliation` and pass startup verification (independent review finding, hardening review of `5601851`).
- (independent review of the F1/F2 repair, findings R1/R2; closed by an unreviewed candidate, pending the independent review required before integration) The last-dotted-component matcher used two methods it did not control on the attacker-supplied name: C-string truncation of the name (R1) and the Python `str.rpartition` method on an attacker-subclassed instance (R2). R1: CPython resolves an extension module's init symbol from a C string, which a NUL byte truncates, so `_imp.create_dynamic`/`ExtensionFileLoader` can load the real `_cffi_backend` extension under a Python-level name such as `"_cffi_backend\x00x"` whose last dotted component is not literally in `_DENIED_IMPORT_MODULES`; the reach was raw libc (`getpid()` only, demonstrated, never a socket), both at import time and preloaded. R2: `_denied_import_match` called `name.rpartition(".")`, a method a `str` subclass instance can override to report a harmless split while `_imp.create_dynamic` still resolves the real denied extension by its true name; this is the same class of bypass as the exact-name check it replaced (which an overridden `__eq__`/`__hash__` could already beat), now reachable at `rpartition` specifically. The candidate closes both: `_denied_import_match` denies outright, rather than matching, any name that contains a NUL byte or that is not exactly `type(name) is str`, before it ever calls `str.rpartition` on it. This does not change what counts as a denied module, only how trustworthy the name must be before the matcher inspects it.
- (independent review of the F1/F2 repair, finding R3; pre-existing since the hardening commit `5601851`; **not closed by this or any candidate in this file**) `_posixsubprocess` is a builtin module. `importlib.machinery.BuiltinImporter.find_spec` plus `importlib.util.module_from_spec`, or `_imp.create_builtin` directly, constructs it without raising a Python `import` audit event at all; the audit hook in this file only ever sees events CPython chooses to raise, and recorded audit events during `_imp.create_builtin` are empty. The `fork_exec` primitive becomes reachable this way (not called, by design, in every probe that has demonstrated it). This is a gap in what the `import` audit event covers, not a naming gap the matcher in this file can close by any amount of name handling, and no change to `_denied_import_match` changes it. Honestly: this in-process Python audit hook is a tripwire against ordinary import paths and careless native-handle reuse, not an OS-level sandbox; it does not and cannot constrain a builtin module construction path that never emits the event it listens for. Closing R3 would require either removing `_posixsubprocess` from the running interpreter's builtin table (an interpreter-build change, out of scope for this runner) or an OS-level control (seccomp, a restricted interpreter, a separately sandboxed process) outside this tripwire's reach. The observer and runner stay dormant and contain no call to `_imp.create_builtin` or `BuiltinImporter`; this is a statement about what an adversarial import inside this process could still reach, not a path the runner's own code takes.

## 9. Independent review handoff

Reviewer: a different model/session than the author, read-only on the candidate, exact-commit review. Do not treat author statements, including the test list above, as acceptance evidence.

**The author session could not execute Python or pytest (permission denied by the host harness). The new tests have not been run by the author. The candidate is uncommitted until the host coordinator runs section 10 step 1.** The same holds for the follow-up on top of `5601851` (the `DENIED_MODULE_PRELOADED` check and its test): its author session could not execute Python either, so that test is unrun until the host coordinator runs section 10 step 1.

Scope to review: `polymarket_scanner/v11/inventory_shadow_start.py`, `tests/test_v11_inventory_shadow_start.py`, this document, and the one-sentence pointer added to `docs/V11_INVENTORY_TRANSFORM_SHADOW_OBSERVER.md`. At the hardening commit `5601851`, scope also includes the L2/L5 changes to `polymarket_scanner/v11/inventory_shadow.py` and the new test in `tests/test_v11_inventory_shadow.py` (see line 5 and section 8). Context: `docs/V11_INVENTORY_SHADOW_REVIEW_2033b82.md`, `docs/V11_INVENTORY_TRANSFORM_REVIEW_3d44aa6.md`.

Probe independently, at minimum:

1. Reuse of `write_artifact` as the verifier of existing artifacts: can any existing-file state cause it to create, replace, or leave a file? Can a crafted artifact pass verification while carrying true authority flags, non-zero CHAIN_RECEIPT, or effects?
2. Two-pass batch: an input-caused refusal can occur after a write (sections 4 and 6); is the list of post-write refusal classes complete, and is every artifact left behind complete and replayable? Is memory bounded for 32 inputs at the byte cap?
3. Directory handling: symlink swaps of input/output directories and entries between listing and open; behaviour of `flock` on the directory descriptor; entry caps.
4. Audit-hook coverage: any reachable network or spawn path that emits no denied event; any denied event the legitimate path emits.
5. Fixture allowlist: any field through which receipt, qualification, or authority material could reach the artifact or summary.
6. Whether any refusal code in section 4 is unreachable or mislabelled, and whether any reachable exception escapes as a traceback instead of exit 2.

## 10. Acceptance criteria

All must hold on one exact commit:

1. Host coordinator, from the worktree, with the socket-denying runner used in prior reviews:
   ```sh
   env -i PATH=/usr/bin:/bin PYTHONDONTWRITEBYTECODE=1 \
     /home/alphaadmin/AlphaV11_Dev/venv/bin/python -m pytest -p no:cacheprovider \
     --basetemp=/tmp/alpha-v11-inventory-shadow-start-basetemp \
     tests/test_v11_inventory_shadow_start.py tests/test_v11_inventory_shadow.py \
     tests/test_v11_structural_evidence.py tests/test_v11_neg_risk_contract.py
   git diff --check
   ```
   Zero failures, zero errors, zero skips; `git diff --check` clean. Expected, at the original contract commit `0998743` relative to `3b7cb09`: every previously passing test in the three existing files plus 23 new test cases. At the hardening commit `5601851` relative to `0998743`: 3 further new test cases (2 in `tests/test_v11_inventory_shadow_start.py`, bringing that file to 25; 1 in `tests/test_v11_inventory_shadow.py`), 68 test cases across the four files. At the follow-up commit on top of `5601851`: 1 further new test case (preloaded denied module, bringing `tests/test_v11_inventory_shadow_start.py` to 26), 69 test cases across the four files, all passing, plain and under `-O`. At the L-B commit (`_cffi_backend` added to the deny set): 1 further new test case, bringing that file to 27, 70 across the four files. At the independent review repair of L-B (findings F1/F2): the `_cffi_backend` test is corrected in place (no count change) and 1 further new test case is added (dotted-alias import and preload), bringing `tests/test_v11_inventory_shadow_start.py` to 28, 71 across the four files, all passing, plain and under `-O`. At the unreviewed follow-up candidate addressing findings R1/R2 (section 8): 3 further new test cases (a direct matcher unit test, and two process-level probes for the NUL-suffixed name and the `rpartition`-overriding `str` subclass), bringing `tests/test_v11_inventory_shadow_start.py` to 31, 74 across the four files, all passing, plain and under `-O`. `git diff --check` does not cover untracked files; run it after `git add -N` of any new paths.
2. `git diff 3b7cb09 -- polymarket_scanner/v11/structural_evidence.py polymarket_scanner/v11/neg_risk_contract.py` is empty at every reviewed commit through `5601851`; neither file is touched by the original contract or the hardening commit. `structural_evidence.py` SHA-256 remains `e50afe579c0a8532fdf82bf646384d083da8f4840d8edac826a7dfc40c915c19`. `git diff 0998743 -- polymarket_scanner/v11/inventory_shadow.py` is **not** expected to be empty at `5601851`: it carries exactly the L2/L5 hardening described at line 5, reviewed directly. The follow-up commit on top of `5601851` does not touch `inventory_shadow.py`.
3. At the hardening commit `5601851`, the change set touches exactly these five paths: `polymarket_scanner/v11/inventory_shadow.py`, `polymarket_scanner/v11/inventory_shadow_start.py`, `tests/test_v11_inventory_shadow.py`, `tests/test_v11_inventory_shadow_start.py`, and this document (plus the coordinator's own `V11_REQUIREMENTS_MATRIX.md`/`V11_WORK_CHECKPOINT.md` housekeeping, which carries no code). The follow-up commit on top of `5601851` touches only `polymarket_scanner/v11/inventory_shadow_start.py`, `tests/test_v11_inventory_shadow_start.py`, and this document. No weather Gate 3, V10, Axiom, credential, service, or root-owned path.
4. Independent review returns PASS_IN_SCOPE with its own probes for section 9 items 1–6 and `NETWORK_ATTEMPTS=0`.
5. Section 6 command, run by the reviewer on the Singapore fixture copied to a fresh directory, exits 0 twice; the second run reports `created: false`; artifact bytes are identical across both runs and across a fresh output directory; the report shows 13 `API_OBSERVED`, 0 `CHAIN_RECEIPT`, coverage `INCOMPLETE`, all authority flags false.
6. The review verdict restates: no qualification, no transaction proof, no financial authority, no G3-L or score change, no coupling to weather SHADOW.

Until 1–6 hold, the observer stays dormant.
