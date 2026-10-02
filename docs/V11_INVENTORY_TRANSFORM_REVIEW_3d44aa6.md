# Independent exact review: inventory repair 3d44aa6

**Verdict: PASS_IN_SCOPE** for the bounded offline code change `16e984a..3d44aa6`. No finding remains from IT-R1 or IT-R4 in the exercised scope. This is code review acceptance only; it grants no provider, account, receipt, financial, G3-L, funding, deployment, or launch authority. G3-L remains NO-GO and score 91/200 remains unchanged. The coordinator must reconcile newer main before any integration.

## Identity and scope

The detached checkout `/tmp/alpha-v11-inventory-transform-review-3d44aa6` is clean at head `3d44aa6d78d80c3d4121f00e204a19cb54c9f3e4`, tree `4cc2c6556ced619e69d021f0c20c47141b3500e5`, parent `16e984a63156f12252e2811f84b6106b1f84fac7`. `git diff --check 16e984a..3d44aa6` passes. The range changes only `polymarket_scanner/v11/structural_evidence.py` (SHA-256 `e50afe579c0a8532fdf82bf646384d083da8f4840d8edac826a7dfc40c915c19`) and `tests/test_v11_structural_evidence.py` (SHA-256 `33a80ddaad4451cfa1e88fb79ddd2c4569b1e42ecd65fa1f125d939ac03a9415`); the corresponding Git blobs are `93a62f068c8a152af74e8712542d356907c3ec5c` and `00ff11657e6f517e634e0fa201e178c5d3ee1c4e`.

I read the prior `16e984a` report/probe, the older `f6c7c90` report/probe, the author JSON/terminal, the exact diff, affected implementation and tests, and relevant negative-risk and reconciliation code. The author results were context, not acceptance evidence. No candidate bytes were changed.

## Closure and regression evidence

- **IT-R1, repaired** at `structural_evidence.py:283-288`: first-page proof requires the request `cursor` key to be absent. An explicit `("cursor", "")` now returns UNKNOWN with `WINDOW_OR_PRIOR_PAGES_UNVERIFIED` for both activity and positions; an otherwise identical request without the key remains COMPLETE. The independent probe also confirms refusal of nonempty or duplicate cursor, duplicate/nonzero offset, malformed response cursor (`0`, `False`, `""`), explicit null response offset, later page number, and continuing pagination.
- **IT-R4, repaired** at `structural_evidence.py:120-153`: the final descriptor open uses `O_NOFOLLOW | O_NONBLOCK`; a FIFO with no writer returns `NOT_REGULAR_FILE` without waiting (audited child measured 0.00012 s, with a 0.1 s loader limit). Independent controls read and hash a regular JSON file, reject final/ancestor symlinks, reject symlinks substituted at final and ancestor open, and preserve all-component no-follow behavior. The loader's `fstat` and bytes/hash apply to the opened descriptor. A regular-file rename at the final open can select the replacement regular inode; the probe confirms that its bytes and hash match that opened inode. Pre-open path identity is not promised or provable here.
- Inherited controls: the independent probe confirms byte, record, deadline and deep-JSON refusals and negative-risk token alias refusal. The unchanged normalization/reconciliation path keeps incomplete coverage unproven; no receipt or account effect is conferred by this review.

Focused offline tests: **32 passed** (`test_v11_structural_evidence.py`, `test_v11_neg_risk_contract.py`). Adjacent offline tests: **43 passed** (`test_v11_scenario_risk.py`, `test_v11_evidence_foundation.py`). Both used `/home/alphaadmin/AlphaV11_Dev/venv/bin/python`, the retained socket-denying test runner, an empty inherited environment except required path/test variables, disabled plugin autoload/cache/bytecode, and separate fresh `/tmp` basetemps. Both report `NETWORK_ATTEMPTS=0`. The independent probe and its FIFO child also deny socket audit events and report zero attempts. No broad release suite was run.

Retained artifacts (SHA-256):

| Artifact | SHA-256 |
| --- | --- |
| `/tmp/alpha-v11-inventory-transform-review-3d44aa6.probe.py` | `d841bec624e0ab5b167b5e0b0db1b904d31be088041841994047da13d24d2e70` |
| `/tmp/alpha-v11-it-3d44aa6-review-probe.log` | `9bb9883718e4ac4c10e34cd384e5c384af9d651ec69922e960c7028bcf787bc9` |
| `/tmp/alpha-v11-it-3d44aa6-review-focused.log` | `7ffc2305ab1e33cb933d5acd79aa5668e6355ec31c7347ac7d84683c719b137c` |
| `/tmp/alpha-v11-it-3d44aa6-review-adjacent.log` | `78cc4adce018dcdc2559097f70ed3de3b76a1e7c7c8538d554e772f4d7ad822a` |

## Limits

The open is safe against symlink following at each traversed component, and returned bytes come from the opened regular inode. It cannot authenticate which regular inode occupied the pathname before the final open. `O_NONBLOCK` prevents the reproduced FIFO wait; elapsed-time limits are cooperative checks around file read and JSON processing, not a hard real-time guarantee for every filesystem operation. These limits do not alter the PASS_IN_SCOPE verdict for the specified offline repair. No provider/network request, credential or account access, financial execution, V10/AxiomTrade operation, service/root-authority change, merge, commit, push, or launch occurred.
