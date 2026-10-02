# InventoryTransform offline SHADOW observer: start contract (candidate)

**Status: CANDIDATE, NOT YET REVIEWED, NOT YET ACTIVATED.** Base `3b7cb091c09ab61d9949e1d87f577f3ce807ce0c`. This contract becomes effective only after the independent review in section 9 returns PASS_IN_SCOPE on the exact commit. It is independent of weather Gate 3 and grants nothing to it. `qualification=false`; `financial_authority=false`; G3-L, funding, and score are unchanged by this document.

The accepted one-shot observer (`polymarket_scanner/v11/inventory_shadow.py`, reviewed at `2033b82`) is unchanged from the original contract commit `0998743`. The hardening commit `5601851` (independent review findings L2/L5) changes it: `write_artifact` now rejects an artifact with any extra/missing top-level key or a non-`int` evidence-class count (L2), and takes an optional `dir_fd` parameter so it can write through the already-validated, already-locked output directory descriptor instead of re-opening the path by name, closing a same-uid rename race between validation and write (L5). This candidate's runner, `polymarket_scanner/v11/inventory_shadow_start.py`, focused tests, and this document complete the rest of the change set. There is no daemon, service, timer, or registration.

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

- **No sockets, DNS, HTTP, or child processes.** The runner and observer import only the standard library, `structural_evidence`, and `neg_risk_contract`. The process entry point additionally installs a Python audit hook that raises on every `socket.*` event, on process-spawn events, and (since the hardening commit `5601851`, independent review finding L1) on the first normal `import` of `ctypes`, `_posixsubprocess`, and other denied low-level modules, before any input is read. This is an in-process tripwire supporting source inspection; it is not an OS network sandbox. It does not cover a denied module already loaded before the hook installs, or one reached only through `importlib.import_module` after that first import (stated limit, see section 8).
- **No account, order, or collateral effects.** No such module is imported; `account_effects` and `order_effects` are always `[]`; activity cash is a vendor observation, not collateral.
- **No credentials.** The runner reads no environment variable or credential file. The start command runs under `env -i`.
- **No weather SHADOW coupling.** The process entry point refuses to run if any `polymarket_scanner` module other than the package roots, `safe_logging`, the observer, the runner, `structural_evidence`, and `neg_risk_contract` is loaded, before and after the run. The dedicated-directory rule refuses an output directory that holds any other file, so weather SHADOW state cannot share it. Nothing in weather SHADOW imports or reads this observer.

## 4. Startup refusal conditions

Every refusal exits `2` with `inventory shadow start refused: <CODE>` on stderr. All inputs are observed in a first pass before any write of this batch begins, so a refusal from that first pass leaves the output directory unchanged. The second pass re-reads each input from disk immediately before writing it, so an input-caused refusal (including `INPUT_CHANGED_DURING_START` and any `INPUT_*`/`SOURCE_*`/`SYNTHETIC_*` code the re-read can still trigger) can also occur mid-batch, after earlier artifacts of the same batch were already written; see section 6 for which codes are reachable there.

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
| Other project module loaded in the process | `MODULE_COUPLING_REFUSED` |

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

One event per invocation. A refusal after the write phase has begun may leave earlier artifacts of the batch in place; each is complete, valid, and replayable. Reachable there, mid-batch (independent review finding, hardening review of `5601851`): `INPUT_CHANGED_DURING_START`, the re-read input codes (`INPUT_NOT_REGULAR_FILE`, `INPUT_INVALID_JSON`, `INPUT_SCHEMA`, `SOURCE_*`, `SYNTHETIC_*`, and the other codes in section 4 that the second-pass re-read can still raise), `RUN_TIME_LIMIT` (checked before each write), and an output I/O error (`OUTPUT_IO_REFUSED`, `OUTPUT_NOT_REGULAR`, `OUTPUT_CONFLICT`). Two further codes are refusals of a *later* invocation caused by this run's own successful writes, not of the write phase itself: `OUTPUT_DIR_ENTRY_LIMIT` can retroactively trip a subsequent start once this batch's own artifacts push the output directory over the entry cap (independent review finding L6); `MODULE_COUPLING_DETECTED_AFTER_RUN` can fire after a run that already wrote successfully, if another project module was loaded during it (independent review finding L7). Both are detective-only, fail-closed checks by design, not write-time guards; neither's behaviour changes here.

## 7. Tests

`tests/test_v11_inventory_shadow_start.py`:

- start command in a fresh child process with the audit hook active: succeeds with zero socket/DNS/spawn events, emits only the artifact, authority flags false, CHAIN_RECEIPT zero, byte-identical replay;
- the hook denies socket creation, DNS, and child processes;
- the process entry refuses when another project module (weather stand-in) is loaded;
- the runner's imports are exactly the standard library plus the observer and `structural_evidence`; a run loads no weather module;
- directory batch replay: stable names, bytes, inode, mtime; a different event gets a different identity;
- uncertainty: INCOMPLETE and UNKNOWN propagate into artifact and summary; `--require-complete` refuses them; COMPLETE still leaves qualification false and inventory unresolved;
- malformed/unsupported fixtures (13 cases, including CHAIN_RECEIPT, receipt-shaped field, false COMPLETE claim, missing lineage) refuse the whole batch with no output;
- symlink, missing, FIFO, subdirectory, hidden, wrong-suffix inputs; byte, batch-size, and run-time caps;
- stale version, stale temporary, tampered, non-canonical, misnamed, symlinked, and foreign output; shared, symlinked, non-private, locked output directory; root.
- (hardening commit `5601851`) denied-module import (`ctypes`, raw `_posixsubprocess`) is refused by the audit hook (finding L1, `tests/test_v11_inventory_shadow_start.py`); a crafted artifact with an extra top-level key or a non-`int` evidence-class count is rejected (finding L2, `tests/test_v11_inventory_shadow.py`); a same-uid rename of the output directory after validation cannot redirect the write (finding L5, `tests/test_v11_inventory_shadow_start.py`).

## 8. Known limits (stated, not hidden)

- The audit hook and module check are in-process controls. They do not constrain native code or replace an OS sandbox. None of the imported modules contains native project code.
- A regular input file replaced by another regular file at the instant of open is read as the replacement; bytes and hash always describe the opened inode (inherited, accepted limit).
- Time limits are cooperative checks between steps, not hard real-time bounds; the outer `timeout` is the hard bound.
- Startup verification proves an existing artifact is self-consistent (policy, content identity, canonical bytes, name). It cannot prove a self-consistent artifact was derived from a genuine input unless that input is in the current batch, in which case the bytes are compared.
- Verification of existing artifacts reuses the accepted writer, which briefly creates and removes a `.inventory-shadow-*` temporary in the output directory. A crash at that instant leaves a temporary that the next start refuses as `OUTPUT_STALE_TEMPORARY`; an operator removes it by hand after inspection.
- Directory locking is advisory (`flock`) and only excludes other starts of this runner.
- The L1 import-deny hook only fires on a module's first normal `import`. A denied module already loaded before hook install (for example by a venv `.pth` file), or reached only via `importlib.import_module` after that first import, is not caught (independent review finding, hardening review of `5601851`).
- The L2 artifact-schema check covers top-level keys and `evidence_class_counts` values only. A forged artifact with a recomputed `observation_id` could still hide authority-like keys inside `metrics`, `rows[i]`, `source`, or `reconciliation` and pass startup verification (independent review finding, hardening review of `5601851`).

## 9. Independent review handoff

Reviewer: a different model/session than the author, read-only on the candidate, exact-commit review. Do not treat author statements, including the test list above, as acceptance evidence.

**The author session could not execute Python or pytest (permission denied by the host harness). The new tests have not been run by the author. The candidate is uncommitted until the host coordinator runs section 10 step 1.**

Scope to review: `polymarket_scanner/v11/inventory_shadow_start.py`, `tests/test_v11_inventory_shadow_start.py`, this document, and the one-sentence pointer added to `docs/V11_INVENTORY_TRANSFORM_SHADOW_OBSERVER.md`. At the hardening commit `5601851`, scope also includes the L2/L5 changes to `polymarket_scanner/v11/inventory_shadow.py` and the new test in `tests/test_v11_inventory_shadow.py` (see line 5 and section 8). Context: `docs/V11_INVENTORY_SHADOW_REVIEW_2033b82.md`, `docs/V11_INVENTORY_TRANSFORM_REVIEW_3d44aa6.md`.

Probe independently, at minimum:

1. Reuse of `write_artifact` as the verifier of existing artifacts: can any existing-file state cause it to create, replace, or leave a file? Can a crafted artifact pass verification while carrying true authority flags, non-zero CHAIN_RECEIPT, or effects?
2. Two-pass batch: can any input-caused refusal occur after a write? Is memory bounded for 32 inputs at the byte cap?
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
   Zero failures, zero errors, zero skips; `git diff --check` clean. Expected, at the original contract commit `0998743` relative to `3b7cb09`: every previously passing test in the three existing files plus 23 new test cases. At the hardening commit `5601851` relative to `0998743`: 3 further new test cases (2 in `tests/test_v11_inventory_shadow_start.py`, bringing that file to 25; 1 in `tests/test_v11_inventory_shadow.py`), all passing, plain and under `-O`. `git diff --check` does not cover untracked files; run it after `git add -N` of any new paths.
2. `git diff 3b7cb09 -- polymarket_scanner/v11/structural_evidence.py polymarket_scanner/v11/neg_risk_contract.py` is empty at every reviewed commit through `5601851`; neither file is touched by the original contract or the hardening commit. `structural_evidence.py` SHA-256 remains `e50afe579c0a8532fdf82bf646384d083da8f4840d8edac826a7dfc40c915c19`. `git diff 0998743 -- polymarket_scanner/v11/inventory_shadow.py` is **not** expected to be empty at `5601851`: it carries exactly the L2/L5 hardening described at line 5, reviewed directly.
3. At the hardening commit `5601851`, the change set touches exactly `polymarket_scanner/v11/inventory_shadow.py`, `polymarket_scanner/v11/inventory_shadow_start.py`, `tests/test_v11_inventory_shadow.py`, `tests/test_v11_inventory_shadow_start.py`, and this document (plus the coordinator's own `V11_REQUIREMENTS_MATRIX.md`/`V11_WORK_CHECKPOINT.md` housekeeping, which carries no code). No weather Gate 3, V10, Axiom, credential, service, or root-owned path.
4. Independent review returns PASS_IN_SCOPE with its own probes for section 9 items 1–6 and `NETWORK_ATTEMPTS=0`.
5. Section 6 command, run by the reviewer on the Singapore fixture copied to a fresh directory, exits 0 twice; the second run reports `created: false`; artifact bytes are identical across both runs and across a fresh output directory; the report shows 13 `API_OBSERVED`, 0 `CHAIN_RECEIPT`, coverage `INCOMPLETE`, all authority flags false.
6. The review verdict restates: no qualification, no transaction proof, no financial authority, no G3-L or score change, no coupling to weather SHADOW.

Until 1–6 hold, the observer stays dormant.
