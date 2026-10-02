# Independent exact-commit A8 repair review

**CHANGES_REQUIRED — one P2 remains. G3-L remains NO-GO.** The six original adverse cases now refuse, but the repair invokes a store health method that is omitted from its loaded-code identity checks. No candidate code was changed or integrated.

Reviewed clean detached `/tmp/alpha-v11-gate3-a8-review-97290da`:

- Commit: `97290da33a03d71908a35bcf1b4dcc4477f44e30`
- Tree: `e304b941aa76ef16799d8d354586e1060da3ed56`
- Repair base: `7d5941cdae0f4f74383a86969b4bfd968c304d98`
- Reviewer: Astra/high coordinator performing independent review of the Sol/high author candidate. This completes the interrupted review; it does not retroactively complete either prior process.

Inspected the entire A8 implementation, three-file repair diff, author record, preceding exact review, acceptance criteria, relevant store implementation and test/probe harness. All three author candidate-file SHA-256s match. The pinned checkout stayed clean and `git diff --check 7d5941c..HEAD` passed.

## R1 — P2: the actual store health call is not bound to the reviewed implementation

Locations: `tools/v11_r09_gate3_a8_composition.py:168-171` and `:305`; supporting store logic at `tools/v11_r09_gate3_store_v1.py:316-324`.

`_adapters` still records only storage `_check_dirs`. The repair replaces the direct directory call in `_fresh_context` with `storage._usable()`. That new method is neither checked for an instance override nor bound to compiled reviewed code or retained in the component identity. A changed `_usable` can return without checking `_failed`, the recovery classification or any directories. The unchanged `_check_dirs` implementation remains correctly pinned, but need not execute.

Four independent counterexamples reproduce the gap without editing candidate bytes:

1. A genuine disposable `VersionedImmutableObjectStore` is first accepted by `_fresh_context`. Setting `_failed=True` correctly raises `OBJECT_DURABILITY_UNCERTAIN`. An instance `_usable` replacement then makes the same context pass; `_component(..., '_check_dirs')` is byte-for-byte tuple-equal to its initial result and the host tuple is unchanged.
2. The same result follows from a class-level `_usable` replacement.
3. The same result follows from in-place replacement of `_usable.__code__`, restored in `finally`.
4. Changing only the disposable store root to mode 0755 makes the genuine `_check_dirs` reject `OBJECT_DIRECTORY_PRIVATE`. Replacing instance `_usable` nevertheless makes `_fresh_context` pass with unchanged component and host tuples. The mode is restored before cleanup.

These are tests of the real, unmodified `_component` and `_fresh_context` helpers with synthetic clocks/resources. They do not bypass V4 or demonstrate end-to-end real launch acceptance. Current real-purpose refusal still prevents every representable real package from completing A8; the module has no launch method. This is an offline checker's in-scope correctness defect, not a demonstrated financial or provider-execution bypass.

Required repair: bind the directly invoked store health implementation as well as the directory implementation to reviewed source and retain/recheck those executable identities, including instance, class and in-place replacements. Alternatively implement an equivalently reviewed direct check without delegating to an unchecked callable. Preserve every existing health, descriptor, policy, directory, clock and capacity refusal. Add positive healthy-store and negative pre-preparation/post-preparation substitution coverage. This correction is not A4's full transitive/native immutability boundary and must not claim it is.

## Closure of previous findings and other checks

The original foreign `_check_dirs` replacement and in-place code replacement now refuse. Matching compiled code plus namespace/qualified-name checks address those cases. An additional independent control confirms that an equal but distinct code object changes the retained identity, so recheck can detect its replacement.

The four original context changes (joint clock-method drift, descriptor hash, policy and `_failed`) now refuse with the genuine store methods. Descriptor bytes are checked against the reviewed descriptor digest, policy is matched to both plan and payload, and stable descriptor/policy/method fields are retained in the host tuple. Original F2 context binding is repaired; the store-health claim remains incomplete because of R1. The documentation now correctly cites the accepting `f03d2fd` prep-repair review instead of the rejected `3241abf` candidate.

Inherited independent controls were rerun for external review pin and manifest/inventory byte mismatch; all 79 FINAL identities (missing and corrupted references); all seven prerequisite names, 20 component review names and 15 purpose/parser bindings; source inode/hash/object substitution; and boot, clock age, disk, memory, manifest and directory privacy refusals. The real-scope V4 refusal was rerun without changing its validator. Placeholder schema acceptance remains documented as byte binding requiring external semantic review/authentication, not authority.

## Independent executions

Exact runner: `/tmp/alpha-v11-gate3-a8-review-97290da-final-probes.py`.

Commands, each with `PYTHONDONTWRITEBYTECODE=1` and interpreter `/home/alphaadmin/AlphaV11_Dev/venv/bin/python`:

- Runner `--author`: **201 passed** in 20.46 s (A8, G3-L prep, V4 launch, runtime).
- Runner `--adjacent`: **116 passed** in 5.44 s; two existing multithreaded-fork deprecation warnings (store/restart suites).
- Runner without mode: **18 passed, 6 strict xfailed** in 7.97 s. The six xfails specifically require `LaunchContractError` from the old defect-acceptance probes. Four of the 18 passes reproduce R1; passing defect probes do not establish acceptance.

Total: **335 passed, six expected refusals, zero unexpected failures**. No release/full-suite rerun is claimed. There were no failed preliminary runs in this review.

The runner is derived transparently from the preserved previous review/author probe harness, retargeted to the pinned detached checkout with a new exclusive scratch prefix and stronger socket audit refusals, plus five independently added probes. Pytest cache and bytecode writes are disabled. Socket connect, DNS resolution and sendto audit events raise; no provider operation was requested. Each test uses unique `/tmp/a8-exact-97290da-*` scratch. Cleanup closes only descriptors beneath the just-completed test's own path and removes only that completed scratch's contents, preserving a name marker. No product assertion/reservation is reduced. Minimum sampled free disk was 1,079,201,792 bytes at targeted teardown, below G3-L's 2 GiB floor; this is not continuous resource qualification.

## Acceptance limits and disposition

Keep `97290da` unmerged. Repair R1 in the sole existing author worktree, retain all prior logs, obtain a new independent exact-commit review, and reconcile with newer main only after acceptance. Inventory `f6c7c90` separately remains CHANGES_REQUIRED on IT-R1/IT-R4. No inventory candidate is accepted by this review.

A8 remains UNQUALIFIED. Separately accepted A1 evidence, authenticated A2 provenance/RECORD adjudication, complete reproducible A3 closure, A4 verification through use, genuine A5 source semantics and A6 current-run causal pins, and real A7 decoder/resource qualification remain required. Also required are separately accepted actual adapters and all 15 real purpose contracts, exact V4/FrozenPlan/integration reconciliation, genuine cohort/schedule/restriction/custody/clock evidence and physical reservations, the completed 77-input PRE_REVIEW assembly and substantive detached G3-L review, all 79 FINAL identities, and point-of-use checks. There is no permission for a provider request before G3-L PASS.

No financial action, provider request, service change, protected-authority installation, V10 operation, AxiomTrade operation, push, or candidate merge occurred. Score remains **91/200 (45.5%), formal 1/50; NOT_READY_TO_FUND**. Prior interrupted review logs and stale initial-checkout terminal remain untouched. The accompanying machine verdict binds this report, source files, runner, logs and review inputs. The new outer terminal records coordinator finalization only, not an invented successful exit of either interrupted process.
