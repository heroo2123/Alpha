Independent GPT-6 Astra/high exact-commit review — **CHANGES_REQUIRED**

Reviewed on 2026-10-02 in detached checkout `/tmp/alpha-v11-gate3-fresh-readiness-review-947bf68`.

- Commit: `947bf687822d0a42e9992adc3049091e677a41ed`
- Tree: `448dc0b375f4111d74563e2c423158fa4addfbf2`
- Comparison base: `6af46331a424f7abcb5167723bff3f31b835e969`
- Identity matches the requested candidate; checkout was clean before and after review.

**G3-L NO-GO.** This review covers only the offline fresh-window readiness planner candidate. No executable preflight, provider/socket access, account/order action, funding, gate promotion, or execution authority is approved. Even a PASS_IN_SCOPE would apply only to this offline candidate; this candidate instead requires changes.

I read the supplied previous `.review.md` and `.verdict.json`, the complete exact three-file diff `6af4633..947bf68`, the candidate handoff, planner and planner tests, and the reused checker validators and frozen protocol/binding. No repository instructions requiring additional review steps were found. No checkout, safety-gate, or private-evidence file was modified. No dependencies were installed or downloaded.

The original ordinary-dictionary defects are repaired: preallocated 100,000-entry dictionaries now return structured refusal/incompleteness with approximately 12 KB additional evaluator peak allocation, instead of the previous multi-megabyte copies. Honest oversized Mapping objects are rejected with zero keys yielded and zero values read on all three outer surfaces. All six ordinary-input low-memory cases also return structured results in plain and optimized Python. However, the accepted Mapping/dict-subclass boundary remains vulnerable to finite adversarial implementations. F1/F2 are therefore **partially repaired, not closed** under the explicitly requested adversarial-mapping scope.

| Finding | Assessment |
| --- | --- |
| F1: pre-enumeration outer cardinality / bounded normalization | Ordinary dictionaries and honest oversized mappings fixed; unchecked enumeration and inconsistent mapping views still bypass the bound and redaction. Blocking. |
| F2: nested reference cardinality | Ordinary dictionaries fixed on all direct reference paths; overridden dictionary length still bypasses the guard. Blocking. |
| Earlier R2/R3/R4 | Remain closed in scope by explicit missing-policy/provenance blockers; no duration ceiling, plausible resource ceiling, or authenticated clock is claimed to have been established. |

**F1 — blocking, medium: outer bounds trust a reported length and a different view from the one subsequently copied.** Locations: `tools/v11_gate3_fresh_window_readiness.py:226`, `:260`, `:299`, `:611`; delegated diagnostic echo at `tools/v11_gate3_evidence_preflight_checker.py:472`.

The new length check is in the right place for ordinary inputs, but `_has_unsafe_key` still exhausts `for k in obj` with no independent iteration budget. A finite Mapping reporting the schema's length while yielding 100,000 short keys causes 100,001 key yields (the full first pass plus the next check's first key) on each of `proposed_window`, `prerequisites`, and `storage_qualification`. Increasing iterator length increases evaluator work without a schema-derived stop.

The storage path has a stronger failure: a Mapping can report length three and expose exactly the three permitted keys through `__iter__`, while `keys()` exposes additional entries. Both validation passes inspect `__iter__`; `dict(storage_qualification)` then consumes the unchecked `keys()` view. This does not require races, threads, infinite iterators, monkeypatching, or expensive callbacks. A single extra synthetic key yields `UNKNOWN_KEY:storage_qualification.REVIEW_PRIVATE_SENTINEL_0` in the returned diagnostics. With 10,000 extra keys, the evaluator reads 10,003 values, allocates approximately 2.75 MB at peak, and returns 659,375 serialized bytes containing the synthetic sentinel. With 16,000 extra keys it allocates approximately 4.17 MB and returns **1,061,375 bytes**, exceeding the frozen 1,048,576-byte diagnostic limit. `_bound_diagnostic_output` counts only UTF-8 reason contents, omitting JSON delimiters/overhead, and does not prevent that observed overrun. No actual private secret was used in these probes.

A separate storage split-view probe with 100,000 entries and 1 MiB additional address-space headroom raises uncaught `MemoryError` in both plain and `-O` Python. The final diagnostic cap cannot repair an earlier unbounded copy or reason-list allocation.

Required repair: make the supported input boundary explicit and enforce it. One option is to refuse non-exact built-in dictionaries before invoking their overridable protocols. If custom Mapping support is retained, normalize once with an independent schema-derived iteration budget, reject duplicate/excess/inconsistent keys, and validate/use only that bounded snapshot; do not subsequently invoke an unchecked `keys()`/`dict(mapping)` view. Preserve structural-refusal versus evidence-incompleteness semantics and generic redacted reasons. Bound the actual serialized diagnostic representation, not just reason text. Add regressions for underreported cardinality, differing iteration/key views, diagnostic redaction, and serialization overhead.

**F2 — blocking, medium: nested dictionary subclasses can bypass the reference length guard.** Locations: `tools/v11_gate3_fresh_window_readiness.py:213`, `:570`, `:577`, `:621`; delegated allocation at `tools/v11_gate3_evidence_preflight_checker.py:332`.

`_oversized_reference` accepts dictionary subclasses and calls overridable `len(v)`. A dictionary subclass whose only override is `def __len__(self): return 3` can hold 100,000 real entries and pass this guard. The frozen `_is_ref` then allocates `set(v.keys())` over the full underlying dictionary. The same counterexample applies to all **twelve nullable prerequisite references, the owner reference, and storage persistence reference**. Owner's four-key schema does not help: three is still below its threshold.

Independent probes covered every one of those fourteen paths at 10,000 and 100,000 entries in both modes. Additional evaluator peak allocation grows from about 663 KB to about **6.30 MB**. Separate low-memory probes reproduce uncaught `MemoryError` for a nullable prerequisite, owner reference, and persistence reference in each mode. These are controlled low-headroom demonstrations, not claims that 100,000 entries alone exceed 256 MiB. The defect is the absence of an enforced upper bound on the accepted direct object.

Required repair: ensure every direct reference handed to the frozen checker has a trustworthy bounded representation. Reject unsupported subclasses or normalize through a bounded, validated snapshot before `_is_ref`; a second overridable `len()` check is insufficient. Cover all nullable references, owner, and persistence, and retain malformed-evidence incompleteness and non-echoing diagnostics. The frozen checker need not be modified.

These findings concern work and allocations requested by the validator itself, using finite, pure object methods. They do not require treating the planner as a general sandbox for arbitrary hostile Python code. If only exact built-in data objects are intended to be supported, that restriction must be enforced rather than accepting arbitrary Mapping implementations and dict subclasses. No readiness promotion or external authority bypass was observed.

The new tests cover correctly reported oversized lengths and ordinary dictionaries. Their counting subclass reports 100,000 entries while storing none, so it proves the early positive-size branch only. It does not test underreported lengths or inconsistent `__iter__`/`keys()` views. The handoff's unqualified claims that the post-check copy is bounded and both findings are fully fixed should be corrected with the implementation repair. Its reference count is also inaccurate: there are twelve nullable references plus the owner, not eleven plus the owner.

**Validation evidence.** Interpreter `/home/alphaadmin/AlphaV11_Dev/venv/bin/python` (Python 3.12.3). The managed sandbox initially failed to initialize with `bwrap: loopback: Failed RTM_NEWADDR: Operation not permitted`; approved external execution was used for the same read-only and /tmp-only operations. No approval rejection occurred.

Focused commands, executed from the exact checkout:

```text
PYTHONDONTWRITEBYTECODE=1 PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 timeout 120s /home/alphaadmin/AlphaV11_Dev/venv/bin/python -B -m pytest -p no:cacheprovider tests/test_v11_gate3_fresh_window_readiness.py tests/test_v11_gate3_evidence_preflight_checker.py -q --basetemp=/tmp/alpha-v11-947bf68-review-plain
543 passed in 6.40s

PYTHONDONTWRITEBYTECODE=1 PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 timeout 120s /home/alphaadmin/AlphaV11_Dev/venv/bin/python -O -B -m pytest -p no:cacheprovider tests/test_v11_gate3_fresh_window_readiness.py tests/test_v11_gate3_evidence_preflight_checker.py -q --basetemp=/tmp/alpha-v11-947bf68-review-opt
543 passed, 1 warning in 6.09s
```

The optimized warning states that assertions outside rewritten test modules/plugins are disabled. All independent probe checks use explicit exceptions, so remain active under `-O`.

- Retained adverse harness, pointed at this candidate: **106 semantic probes per mode**, all expected refusal/incompleteness checks passed; ten allocation measurements and three honest-mapping traversal measurements per mode. Coverage includes unknown/oversized/surrogate/nonstring keys, nested references, malformed/duplicate/deep/oversized JSON, extreme window horizons/durations, resource types and large magnitudes, stale/future matching clocks, missing inputs, and roll-forward refusal. Maximum serialized result in this ordinary-data suite: 1,833 bytes. Limits: 30-second wall timeout, 20-second CPU ceiling, 256 MiB address space.
- New independent mapping harness: **40 probes per mode**, including all fourteen direct reference paths at both sizes. It reproduces the findings above in both modes. Limits: 30-second wall timeout, 10-second CPU ceiling, 256 MiB address space.
- Low-memory harness: **20 subprocess cases total**. All twelve ordinary cases return structured results; all eight adversarial cases reproduce uncaught `MemoryError`. Each child: 15-second wall timeout, 10-second CPU ceiling, preexisting virtual size plus 1 MiB address space (approximately 50–52 MB total).
- An audit hook active during the semantic/allocation/mapping evaluations prohibited filesystem opens/mutations, socket events, and subprocess/process-launch events. **Zero such events occurred.** Harness fixture reads occurred before activation; the separate resource-limit harness reads `/proc/self/statm` before evaluation. Review orchestration launches only local bounded test/probe processes.

Evidence artifacts:

- `/tmp/alpha-v11-947bf68-adverse.py`
- `/tmp/alpha-v11-947bf68-adverse-plain.json`
- `/tmp/alpha-v11-947bf68-adverse-opt.json`
- `/tmp/alpha-v11-947bf68-mapping-probes.py`
- `/tmp/alpha-v11-947bf68-mapping-plain.json`
- `/tmp/alpha-v11-947bf68-mapping-opt.json`
- `/tmp/alpha-v11-947bf68-memory-cap.py`
- `/tmp/alpha-v11-947bf68-memory-cap.json`

The unchanged checker tests read the two referenced retained private files (`package.json` and `restriction-history.json`) and compare them to the public binding. I additionally rechecked both byte lengths and SHA-256 values after testing: both match. No other private evidence was needed or read by the review harnesses. No private evidence was modified. The twelve null/unqualified prerequisites remain blockers in the real binding. Missing duration/magnitude/provenance policies remain explicit; all three readiness flags stay false in the evaluator probes. Restriction holds, forbidden authority promotion, and the non-executable eligibility label remain enforced. Static inspection found no planner provider, socket, DNS, account, or order effect. The exact diff does not alter the frozen checker, protocol, binding, or safety gates.

`git diff --check 6af4633..947bf68` passed. Final `git status --porcelain=v1 --untracked-files=all` was empty; commit/tree were rechecked. Bytecode and pytest cache writes were disabled.

| Reviewed file | SHA-256 |
| --- | --- |
| planner | `c38734c481cd5e58f4576a8d6fe323cb4edd014f7a67fca23be7eef2354b4bd6` |
| planner tests | `157fd2d726e0e6d8f8839410622abc6510c1b0aeb7b968e9088cc8fa3e8efbb5` |
| handoff | `8557dc06fa9e41d1336c5c6547fd97fa18c2d8b1a2149c585cc45650f7cdcd1b` |
| frozen protocol | `ae59812fa58ec41895b87408f0ea175988ae6d20a1f4d5dabcd96a01c2dd3a68` |
| frozen binding | `4e9af52d3e2f6f9037e38fb7570c1d43a3984ed25c34090aedecfc1d2549e361` |

Verdict: **CHANGES_REQUIRED** for incomplete F1/F2 closure. **G3-L NO-GO** remains unchanged.
