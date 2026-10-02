**PASS_IN_SCOPE** — IS-R1 is closed for this exact offline observer candidate. No new actionable finding was identified in the reviewed and exercised scope. `qualification=false`; `financial_authority=false`.

**Exact identity and review scope**

- Commit: `2033b8235d7f1e25c799fd504f42353752d6d463`
- Tree: `8f0acf25d3dcdfe80d9a38999d003731a777bc85`
- Parent: `b04cc7b69b213ad3b199625de024d57ece933e9c`
- Worktree: `/home/alphaadmin/AlphaV11_InventoryShadow`; clean before and after, including all untracked files. Identity and status were rechecked at report generation. Both the repair and complete observer range pass `git diff --check`.
- Independent review session: no author changes were made. Requested review configuration was Astra/high Fast; the review does not claim independently verified backend model or service-tier metadata.

Read both `/tmp/alpha-v11-inventory-shadow-review-b04cc7b.review.md` and its `.verdict.json`, the complete original `c22b34c..b04cc7b` observer diff, the complete `b04cc7b..2033b82` repair diff, current implementation and dependencies, observer tests, saved Singapore and RPC-error fixtures, and retained InventoryTransform reviews for f6c7c90, 16e984a and accepted 3d44aa6, including the accepted terminal JSON and regression probe. No applicable AGENTS.md was found in the worktree or its ancestors. The repair changes only the observer and its tests (52 insertions, 2 deletions). Original observer scope adds only those files and its documentation.

The accepted `structural_evidence.py` SHA-256 remains `e50afe579c0a8532fdf82bf646384d083da8f4840d8edac826a7dfc40c915c19`; its tests remain `33a80ddaad4451cfa1e88fb79ddd2c4569b1e42ecd65fa1f125d939ac03a9415`. Acceptance history was context; all reported tests below were rerun against this checkout.

**IS-R1 and malformed-input closure**

`inventory_shadow.py:43` validates capture-time metadata before dataclass conversion; lines 142–145 validate normalized activity side/token metadata before reconciliation and serialization. The wrapper at lines 108–115 translates residual recursion to `ShadowInputError`. Lines 121–126 translate loader filesystem and extreme-decimal exceptions.

Fresh actual-module CLI subprocesses independently reproduced all three 600-level metadata inputs (`captured_at_utc`, `side`, `token_id`): each exits **2**, emits a refusal without traceback, and creates **no artifact**. Directory and extreme positive decimal-exponent inputs also exit 2 without artifact. Negative extreme exponents refuse in the independent matrix. An injected serialization `RecursionError` is translated to controlled refusal.

Boundary probes accept capture strings of length 256 and normalized side/token strings of length 4096, and refuse 257/4097 respectively. Source parameter count 64 is accepted; 65 is refused. Non-string truthy metadata is refused. An oversized token on another event's retained row also refuses. Other source identity strings and row identifiers rely on the overall byte/output bounds, rather than these new per-field limits.

The independently written 448-case matrix exercises source, activity and pagination fields with nulls, booleans, numbers, strings, containers, non-finite values and escaped surrogates, plus malformed encodings/envelopes, deep JSON, exact boundaries and fresh CLI cases. Results: **179 controlled refusals, 269 bounded diagnostics, zero unexpected exceptions**. A successful diagnostic is not a claim that every malformed field is accepted as valid: the inherited normalizer can omit invalid rows, lower coverage, or normalize falsy optional row metadata to null. The repaired boundary concerns safe normalized output and controlled refusal, not blanket rejection of every imperfect raw row.

**Authority, coverage, replay and resource checks**

- Explicit local regular-file input only. URL-looking paths, missing files, final/ancestor symlinks, symlink-plus-parent traversal and FIFOs refuse; directory exceptions now become controlled observer refusals. Retained acceptance probes also pass open-time final/ancestor symlink swaps and opened-inode byte/hash checks. Output symlink, FIFO, directory and conflict cases refuse and leave no temporary names.
- No provider client, network fetch, receipt acquisition, account/order dispatcher or weather runtime integration exists in the reviewed path. Package initialization installs the existing logging filter. Runtime import probes find no weather/provider/account/execution module. No socket/DNS event was attempted in audited runs.
- Singapore remains 13 API_OBSERVED rows (11 purchases, one conversion, one merge), INCOMPLETE and CHAIN_UNVERIFIED, with vendor cash diagnostics `0.007412` and `0.147630`. CHAIN_RECEIPT count stays zero; receipt, transaction-proof, qualification and financial authority remain absent/false. Account/order effects remain empty. RPC-error fixtures confer no receipts or cash/inventory effects.
- COMPLETE, INCOMPLETE and UNKNOWN coverage remain distinct. Empty request cursors, duplicate request parameters and malformed response cursors cannot prove COMPLETE. Even COMPLETE leaves opening inventory, basis, fees, conversion mask, transaction lineage and deployed route unresolved.
- Synthetic conversion remains separate SYNTHETIC_PROOF with null coverage and empty effects. Synthetic collateral arithmetic grants no spendable collateral. Deployed attestation, token aliases, boolean fee values and oversized synthetic inventories refuse. Rehashed attempts to enable authority or effects are rejected by the artifact writer.
- Repeated replay, changed ambient Decimal context, and independent fresh CLI processes produce artifact SHA-256 `55fb1c34ccf168806b65bd6f4a582a21edb094445e6eaf5c557bb61ef4feb728`, observation ID `533ae8bd8df563ae8a0dbdc817b66fc909af7dce3a0cbe00ff7460577130b37a`. Identical replay preserves bytes, inode and mtime; eight concurrent writers produce exactly one creation and seven identical replays. Conflicting content is not replaced.
- Exercised byte, record and cooperative deadline caps, 10,000-level JSON, invalid encoding, metadata caps, and sub-2 MB input whose encoded output exceeds the 4 MB artifact cap all behave as expected. No new serialization exception was found.

**Execution evidence**

All runs used `/home/alphaadmin/AlphaV11_Dev/venv/bin/python`, an empty inherited environment except explicit local path/test variables, disabled bytecode/plugin autoload/cacheprovider, and fresh `/tmp` scratch paths. The runner denies all `socket.*` audit events, including DNS, and caps address space at 768 MiB and CPU at 50 seconds, with an outer 60-second timeout. Fresh CLI children install denial before importing the candidate; the retained acceptance FIFO child also installs denial. These controls and source inspection support the offline claim; they are not a general OS network sandbox.

| Run | Result | Evidence under `/tmp/alpha-v11-inventory-shadow-review-2033b82-evidence` |
| --- | --- | --- |
| Observer + structural evidence + NegRisk | 42 passed, 1.46 s | `focused.log` |
| Scenario risk + evidence foundation | 43 passed, 3.60 s | `adjacent.log` |
| Accepted 3d44aa6 regression probe | All assertions passed | `accepted-regression.log` |
| Prior observer probes copied to fresh scratch paths, adjusted to recognize CLI exit 2, with asserted overall success | 12 groups passed, 0 failed | `regression-probes.py`, `regression-probes.log`, `probes.json` |
| Newly written malformed-input/boundary/CLI matrix | 448 cases, 0 unexpected exceptions | `independent.py`, `independent.log`, `independent.json` |

Every parent runner reports `NETWORK_ATTEMPTS=0`. Probe sources and detailed results are retained; original candidate and evidence artifacts were not edited. To rerun tests, invoke `runner.py pytest -p no:cacheprovider --basetemp=<fresh /tmp path>` with the listed test files under the environment above. Probe scripts intentionally require fresh scratch output paths.

**Limits and final status**

This is bounded offline code acceptance, not deployment, qualification or financial authorization. No broad release suite was run. Existing cooperative timing checks do not guarantee a hard deadline for every filesystem operation. Opening a regular inode does not authenticate which regular inode occupied the pathname before open. These previously accepted limitations remain unchanged.

The default sandbox failed before execution with the existing `bwrap` loopback setup error. Approved local escalated commands were used for read-only inspection and bounded offline tests; no approval rejection remained. Only new `/tmp` review/test artifacts were written. No candidate/evidence edit, provider request, financial action, V10/AxiomTrade action, service/root-authority change, merge, push or install occurred.

`clean=true`; `qualification=false`; `financial_authority=false`.

INVENTORY_SHADOW_2033B82_REVIEW_COMPLETE
