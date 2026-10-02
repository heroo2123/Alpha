# Independent exact-commit review: offline inventory transform repair

**Verdict: CHANGES_REQUIRED** for the bounded offline research scope. The repair closes IT-R2, IT-R3 and IT-R5 under the reviewed normalizer/loader flow, but IT-R1 and IT-R4 still have reproduced P2 gaps. There is no discovered live execution or account write path in the six-file addition. This review grants no receipt, account, financial, provider, G3-L, funding or deployment authority.

## Exact identity and acceptance context

- Sole independent Sol/high reviewer; no agents or implementation changes. Detached clean checkout `/tmp/alpha-v11-inventory-transform-review-f6c7c90` at commit `f6c7c903f1f4de907ac10bfd7d91e89418b6aea5`, tree `fc40edabeb0f8d223250b3fcd31ba0890590dc0c`, parent `9600510c70a9302b75ff26432367be84ce43dd79`.
- The original addition is the six files added by `9600510` relative to `624f4bb32c166303c3b48181a1aac124fd60b547`; the repair modifies only the two modules and their two tests. Both fixture blobs are unchanged. `git diff --check` passed for the repair; the checkout remained clean after probes and tests.
- Prior independent review: `/tmp/alpha-v11-inventory-transform-review-9600510.md`, five P2 findings IT-R1..IT-R5. Author terminal `/tmp/alpha-v11-inventory-transform-p2-repair-20261002.terminal.json` agrees with head/tree and records clean exit 0. Author record `/tmp/alpha-v11-inventory-transform-p2-repair-20261002.author.json` claims 24 targeted and 43 adjacent passes and eight pre-repair defect confirmations; these claims were treated as context, not reviewer acceptance.
- Read current main's `docs/V11_WORK_CHECKPOINT.md`, `docs/V11_REQUIREMENTS_MATRIX.md`, and `docs/V11_ENGINEERING_PROGRESS.md` at observed clean checkout head `879a456d5597a70d6150618a20bbc3c981946200` (tree `b1e27374e011d00783e664d91b6d39c86738e10f`). Their SHA-256 values were `223f7db283434d6a9aecdc3475efec7d5f872fbc0dec3733449fe669c88cfdd6`, `e2d843d44326376534f5c894fdd65ec2680bfb1dd3dc339442823478e3e5967b`, and `6d4f7461cf1f9f0f7554403d2577e30b6c1be3aba2b2d61c7e6a685281a0b100`, respectively. Current main says this candidate is unaccepted/unmerged pending exact review and newer-main reconciliation. G3-L remains NO-GO; 91/200 (45.5%), formal 1/50, NOT_READY_TO_FUND. Main was read only.

Reviewed all six original files in full: `neg_risk_contract.py`, `structural_evidence.py`, both test files, and both JSON fixtures. The addition's only Python consumers are its two tests (`rg -n 'structural_evidence|neg_risk_contract' --glob '*.py' .`). It introduces no runtime dispatcher, receipt acceptance, ledger, account, provider or order call. The RPC screen returns only `RPC_ERROR`, `MALFORMED_RPC_RESPONSE` or `UNVERIFIED_RPC_RESPONSE`, with empty accepted receipts and effects. Singapore remains API_OBSERVED / CHAIN_UNVERIFIED with incomplete page coverage, unknown original-source provenance and unresolved opening inventory, basis, fees, mask, transaction lineage and deployed route. Its 11 observed purchases, one conversion and one merge and the displayed cash arithmetic are vendor observations, not profit or inventory proof.

## Findings

### P2 — IT-R1 remains open: ambiguous pagination is COMPLETE

`polymarket_scanner/v11/structural_evidence.py:194-202, 209-227`; `Source.parameters` validation at lines 80-83.

The repair validates `offset` type but uses the truthiness of `next_cursor` and collapses request parameter pairs with `dict(source.parameters)`. In an explicitly bounded first-page source, `pagination={"has_more": false, "next_cursor": 0}` or `next_cursor=false` yields `COMPLETE` with no discrepancy, although those are malformed cursor identities. Likewise `(("cursor","later"),("cursor",""))` and `(("offset","1000"),("offset","0"))` in source parameters hide an explicitly later-page request: the last duplicate wins and the result is `COMPLETE` with no discrepancy. An explicit `offset:null` is also accepted as absent. These are independent saved-metadata cases, not a claim that any provider emitted them. They violate the stated rule that contradictory or unverified first-page metadata cannot prove a whole window. `reconcile_event_window` then omits `EVENT_WINDOW_COVERAGE_UNPROVEN` for such batches.

The author test at `tests/test_v11_structural_evidence.py:121-150` covers nonzero offset, one nonempty cursor, malformed offset string and `page_number=1`; it does not cover cursor type or duplicate request keys. Validate pagination field types and reject ambiguous repeated request keys before constructing first-page proof. Treat malformed/ambiguous metadata as UNKNOWN or INCOMPLETE with a discrepancy.

### P2 — IT-R4 remains open: opened file need not match checked path

`polymarket_scanner/v11/structural_evidence.py:94-123`.

The new ancestor walk rejects stable parent symlinks, and `fstat` establishes only that the **opened** descriptor is regular. Between `is_symlink`/`is_file`/`stat` and `Path.open`, a component or final file may change. A deterministic temporary-file probe intercepted `Path.open` after the checks and replaced `checked.json` with a symlink to a different regular JSON file. The loader returned the other file's payload with no discrepancy. A second probe atomically replaced the checked inode with a different regular file and likewise returned the replacement with no discrepancy. No protected path was accessed. These probes model ordinary filesystem races and show that the claimed component-wise no-follow and opened-fd identity are not enforced. The raw hash describes the bytes read, but the pre-open path/size proof describes another object.

The author's parent-symlink test at `tests/test_v11_structural_evidence.py:194-205` covers only a stable symlink. Open path components with no-follow semantics and verify the opened descriptor's identity against the checked file, or narrow the loader contract and acceptance claim explicitly. A final-component `O_NOFOLLOW` alone would not close ancestor swaps or same-path regular-inode replacement.

## Closure checks and limits

| Finding | Independent result |
| --- | --- |
| IT-R1 | Direct nonzero request/response offset and later `page_number` regressions pass; malformed `next_cursor` and duplicate request pairs still promote COMPLETE. **Open.** |
| IT-R2 | Global YES/NO token namespace rejects same-condition and cross-condition aliases; list topology is refused, leaving validated ordered tuple fields immutable. Independent alias/list probes reject. **Closed for synthetic tuple inputs.** Route/rule assertions remain synthetic, not deployed attestation. |
| IT-R3 | Loader preserves `0.1234567890123456789` exactly as `Decimal`; an `1e100` cash row becomes INCOMPLETE/INVALID_ACTIVITY_ROW; reconciliation uses a fixed local context and the saved-fixture context regressions pass. A 100,000-digit numeric lexeme fits the byte cap and loads as Decimal, then requires row-level rejection if used as a modeled amount. **Closed for bounded normalized rows.** Arbitrary caller-constructed `Observations` and parse-time resource use were not certified. |
| IT-R4 | Stable parent symlinks and a `linked/../page.json` probe reject. Open-time symlink and inode swaps still read unchecked bytes. **Open.** |
| IT-R5 | 20,001-byte / 10,000-level JSON returns UNKNOWN/INVALID_JSON with a raw hash, rather than throwing `RecursionError`. **Closed for reproduced deep input.** |

The original Singapore fixture still reports 13 observed rows: purchase cash `49.992588`, conversion/merge cash fields `45` and `5`, apparent cash difference `0.007412`, and price-product diagnostic `0.147630`. These numbers do not authenticate transactions or establish a residual position. Both saved RPC fixtures are `-32000` upstream errors; no reviewed path emits `CHAIN_RECEIPT` or nonempty account effects.

## Commands and results

The default sandbox failed before command execution with `bwrap: loopback: Failed RTM_NEWADDR: Operation not permitted`; local escalated commands were approved and used only for this read-only review and `/tmp` artifacts. No approval rejection remained. The test runner `/tmp/alpha-v11-inventory-transform-review-f6c7c90-test-runner.py` installs a Python audit hook denying all `socket.*` events. Each pytest run used a distinct fresh `/tmp` basetemp, an empty inherited environment, bytecode and plugin autoload disabled, and cacheprovider disabled. No network/provider request was made or attempted in either test run.

```text
git rev-parse HEAD HEAD^{tree}; git diff --name-status 624f4bb... 9600510...; git diff --name-status 9600510... f6c7c90...; git diff --check 9600510... f6c7c90...; git status --porcelain=v1
  => expected exact identities, six added files, four modified files, no whitespace errors, clean checkout

timeout 60 env -i PATH=/usr/bin:/bin PYTHONDONTWRITEBYTECODE=1 PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 /home/alphaadmin/AlphaV11_Dev/venv/bin/python /tmp/alpha-v11-inventory-transform-review-f6c7c90-test-runner.py -p no:cacheprovider --basetemp=/tmp/alpha-v11-it-f6c7c90-targeted-20261002 tests/test_v11_structural_evidence.py tests/test_v11_neg_risk_contract.py -q
  => 24 passed in 1.01s; NETWORK_ATTEMPTS=0

timeout 60 env -i PATH=/usr/bin:/bin PYTHONDONTWRITEBYTECODE=1 PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 /home/alphaadmin/AlphaV11_Dev/venv/bin/python /tmp/alpha-v11-inventory-transform-review-f6c7c90-test-runner.py -p no:cacheprovider --basetemp=/tmp/alpha-v11-it-f6c7c90-adjacent-20261002 tests/test_v11_scenario_risk.py tests/test_v11_evidence_foundation.py -q
  => 43 passed in 4.03s; NETWORK_ATTEMPTS=0

env -i PATH=/usr/bin:/bin PYTHONPATH=. PYTHONDONTWRITEBYTECODE=1 /home/alphaadmin/AlphaV11_Dev/venv/bin/python -c 'import sys,runpy; sys.addaudithook(lambda event,args: (_ for _ in ()).throw(RuntimeError("NETWORK_DENIED:"+event)) if event.startswith("socket.") else None); runpy.run_path("/tmp/alpha-v11-inventory-transform-review-f6c7c90-probe.py",run_name="__main__")'
  => independent closure controls and residual reproductions shown above; no socket event
```

Probe source is retained at `/tmp/alpha-v11-inventory-transform-review-f6c7c90-probe.py`. It creates only disposable temporary JSON files; no candidate or main file is changed. The controlled path swaps occur between the loader's check and open, not via a provider or privileged path. No broad release suite was run because the new modules have no other Python consumers and disk is limited.

## Reviewed six-file blob identities

| File | Git blob | SHA-256 |
| --- | --- | --- |
| `polymarket_scanner/v11/neg_risk_contract.py` | `ddd9cdd5e37a56a863e19fa219054c937a5acad9` | `b8badca12ddf4df7e9e8da5df59013da5136fa24d69f5c37abd30970050a5c8a` |
| `polymarket_scanner/v11/structural_evidence.py` | `3f2a496c7f9f31665393bc6f9b1850d7ad25cc00` | `fb5b367facd2b36e49cd51c345c98a661792f97dd36ed51778665b9d972989d8` |
| `tests/fixtures/v11_inventory_transforms/rpc_error_receipts.json` | `b3bbac9d32c58a6409545d7fe55f242edbdc4f62` | `39a4cba42ab2541cdeca873bf94a30612e2dee3955414d824c6049afe6690d9b` |
| `tests/fixtures/v11_inventory_transforms/singapore_20261003_api_observed.json` | `7fa16936ce510d9fbb3bdd714fbdafe23cad8795` | `74e1ca72cf25875e2fbc0ba38cf962c04f12703c982a393298d63c5425c8849e` |
| `tests/test_v11_neg_risk_contract.py` | `38ecde1bea092a860396cf2435327279be875ee2` | `7c730927258caef128198a5822e8e782cfe89dc45bbe5d039ce5405e08d9fc8b` |
| `tests/test_v11_structural_evidence.py` | `05e8efcf3f68f3143f222c9a05f956257bc21059` | `af4259d36306bb5dd45359f4530c437f353cbfe7e8e6fe04319d32e97ec0c6e4` |

**Acceptance limit:** `CHANGES_REQUIRED` for this exact offline candidate. No merge or main reconciliation was performed. No V10, AxiomTrade, service, credential, protected-authority, real-order, real-provider, production or financial action occurred. G3-L and funding authority remain false.
