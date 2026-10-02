# A2/A3 decision and pending intake independent review

**PASS_IN_SCOPE — decision and pending intake only.** Candidate commit `fb3a8047760bcdaf0a94be20afcea7d61a7c2785`, tree `a09d184c91011e0fa8ee0d171f8b9edd4dc7ea68`, parent `3878fa9ee312b6d1fe45705bbba0821aa0656c0c`. The commit changes only `docs/V11_R09_GATE3_A2A3_DECISION_20261002.md` and `.json`. This review grants no authenticated original, A2/A3 build or closure, runtime, or G3-L acceptance.

## Decision assessment

- Historical qualification H correctly requires independently authenticated originals, actual payload and RECORD assertion binding, authenticated build/postprocessing stages, causal attribution and a separate per-file verdict for each of 25 discrepancies. A matching retained copy or authenticated final archive alone cannot explain an internal RECORD disagreement. Where no real historical RECORD preimage exists, the proposed producer record must explain assertion generation; the decision does not invent a preimage.
- Replacement R remains separate. It permits authenticated binary installation while retaining A2 build recipe, toolchain, options, patches, platform and ABI obligations. It explicitly leaves the old 25 discrepancies unresolved and excludes their bytes unless independently qualified. It calls for fresh affected A1, A3, MEMFS, A4, A7 and A8 evidence, conditional A5 reuse only within original provenance/scope/time, decoder-dependent semantic comparison, and new code/lock/run/G3-L reconciliation. Version equality carries no acceptance.
- Supplier, custody, independent provenance, per-file adjudication, closure, data-selection and reconstruction responsibilities are assigned distinctly. Candidate-supplied hashes, keys, local RECORDs, authorization and static graph agreement are not treated as self-authenticating anchors. The proposed complete closure includes interpreter startup, Python/transitive modules, native loader, lazy/plugin loads, selection rules and data overrides. It requires future accounting for all 484 observed unhashed RECORD rows without blessing locally added hashes.
- The JSON is a bounded pending ledger: eight observed packages and 25 unresolved files, null authentication/acceptance references, no chosen path, no installation or reconstruction credit. Its 78 historical mappings and package list are identified as lower-bound observations. The document assigns no duplicate B1 work; B1 retains separate exact implementation review, while B2 requires accepted A2/A3 and trusted bootstrap capability and B3 remains gated. The timestamped B1 and resource observations in the decision were not independently refreshed in this static review and convey no qualification.

## Exact checks

- Read the heads of `V11_WORK_CHECKPOINT.md`, `V11_REQUIREMENTS_MATRIX.md` and `V11_ENGINEERING_PROGRESS.md` first; compared the candidate with controlling identity acceptance, accepted retained audit/review, A4 bootstrap design/review and A2/A3 handoff.
- Verified HEAD, tree, parent, two-file commit scope and clean checkout. `git diff --check HEAD^ HEAD` passed.
- Candidate Markdown: 11,803 bytes, SHA-256 `524d72c7b8ee5a032708682935a9aa9246d5cc80bd187d04851321d54a421978`. Candidate JSON: 22,339 bytes, SHA-256 `627028d8467101665a9755a8d609f2c1a22f65ab31d1c2daf38c10bb2f7f4892`.
- Verified byte length and SHA-256 for every one of the seven JSON evidence references, including the candidate Markdown. Compared all eight package names, observed versions, METADATA and RECORD hashes, and unhashed counts with accepted dossier JSON; counts total 484. Compared all 25 unique discrepancy paths, recorded hashes/sizes, observed hashes/sizes against the accepted dossier and all three retained-audit copies; every row matches.
- Verified historical cache locator/hash/size and interpreter observation against source JSON. The named fresh audit file exists: 118,802 bytes, SHA-256 `82f6e27da20d0008368bb88f488078f3c4bdbcc15756cbe6f4ef957c65ffe1a7`; its parsed JSON equals retained audit JSON after excluding only `observed_utc`.
- Checked the candidate's unqualified/no-go fields and null external-evidence references. No decoder/native import, broad suite, package installation, provider request, network action, reconstruction or repo edit was performed. Local read commands used narrowly approved escalation after the sandbox wrapper failed before execution.

## Findings and disposition

No correction findings in this decision/intake scope. A2 and A3 remain **UNQUALIFIED**, A4 **OPEN**, A8 **UNQUALIFIED**, G3-L **NO-GO**, `launchable=false`, and qualification credit zero. Exact future artifact authentication, causal attribution, complete closure, reconstruction and separate independent reviews remain prerequisites. Checkout is clean.

A2A3_DECISION_REVIEW_COMPLETE
