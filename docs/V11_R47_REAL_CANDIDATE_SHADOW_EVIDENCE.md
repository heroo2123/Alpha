# R47 real-candidate isolated shadow-injection evidence — 2026-09-30

## What this is

A narrowly-scoped, root-free, nonfinancial addition to the R47 evidence base.
It exercises the two genuine, immutable, real-data research candidate bundles
already produced outside this repository against the actual, already-reviewed
prediction machinery (`polymarket_scanner/v11/model_artifacts.py::predict_with_bundle`),
via a new isolated injection harness
(`tools/r47_isolated_shadow_injection.py`) and a new regression test file
(`tests/test_v11_r47_real_candidate_shadow_injection.py`).

It does **not** install, approve, or write any protected model-authority
state, does **not** perform an independent/owner review, and does **not**
change R47's OPEN status. See "What remains blocked" below.

## The real candidates

Both bundles were produced by an external, out-of-repo research fit
(`/home/alphaadmin/AlphaV11_Commissioning/evidence/v11_real_data_fit_result_20260929.json`)
and are stored as immutable, content-addressed `ArtifactStore` objects:

| Family | Root | Candidate bundle SHA-256 | Parent bundle SHA-256 |
|---|---|---|---|
| `daily_high_temperature` | `/home/alphaadmin/AlphaV11_BrainWork/real_fit_20260929/high/objects` | `de92cf905b5fa62bfea16bd0f2d7c2983ede00ff3f6a61820b4b25268fd17c46` | `7da9be823091885383fdeeee739c468ade30fa7c338a7daff05368329be877d2` |
| `daily_low_temperature` | `/home/alphaadmin/AlphaV11_BrainWork/real_fit_20260929/low/objects` | `f09730a5fd1ced4c581c425619e987b239f4ed34d1d9b5d1e2485f0dfe05a5a9` | `8480555dfb4d4e3b085772c373ac37f2ec24ebed716894c566692a8aad4e96f3` |

Both are explicitly self-labeled `NO_PROMOTION`, `calibration_status:
FITTED_NOT_CALIBRATED`, `reason: INDEPENDENT_LABEL_AND_DEPENDENCE_REVIEW_REQUIRED`,
`financial_authority: false`, `promotion_authority: false` in the result
record, trained on 20 real Gamma-payout events each (`TRAIN` only, zero
held-out `CONFIRMATION`/`DEVELOPMENT` examples). This work does not change any
of those labels or claim they no longer apply — it preserves them exactly and
adds a byte-for-byte verified, test-covered integration path on top of them.

Both use the same 31-member, single-model `GAUSSIAN_MEMBER_MIXTURE` contract:
`model_id='gefs31'`, `dependence_group='NCEP_GEFS'`, unit `'F'`,
`target='FINAL_CONTRACT_PAYOUT'`. The `FEATURES` component's `forecast-cuts:`
schema identity was independently reproduced from the underlying
`ForecastFeatureContract((('gefs31', 31),), 'F', <family>)` construction (not
trusted from the file), for both families, and matches the bundle's recorded
`feature_schema_sha256` exactly.

## What was built

`tools/r47_isolated_shadow_injection.py` — a small, dependency-light module
(imports only `evidence.py` and `model_artifacts.py`; nothing from
`model_registry.py`, `certification.py`, `host_trust`, or `production`):

- `inject_isolated_research_candidate(private_root, bundle_sha256)` loads an
  explicitly caller-supplied private `ArtifactStore` root and bundle hash,
  validates the bundle and every component/hash using the existing
  `ArtifactStore`/`PinnedBundle` machinery (unmodified), and returns a frozen
  `IsolatedResearchInjection` value object.
- `IsolatedResearchInjection` is a deliberately non-authoritative stand-in for
  `model_registry.DecisionModelPin`. Its `mode` is fixed to `'V11_SHADOW'`,
  its `status` fixed to `'ISOLATED_RESEARCH_INJECTION'`, and
  `host_approved` / `promotion_authority` / `financial_authority` are fixed
  `False` — `__post_init__` raises `EvidenceError` if any of these are
  constructed *or* `dataclasses.replace`d into any other value, or if the
  underlying bundle's own `financial_authority` field is ever not `False`.
- `run_isolated_shadow_prediction(...)` calls the real
  `model_artifacts.predict_with_bundle` and returns its payload alongside the
  fixed research-only labels. It never touches `/var/lib/alpha-v11`, never
  constructs `model_registry.ApprovedArtifactReader`, and never creates a
  TRADE/order/account record (it has no import path to any of that code).

`tests/test_v11_r47_real_candidate_shadow_injection.py` (26 cases; the ones
needing the private, machine-local object store skip cleanly if it is
absent — it is read-only external evidence, never a Git artifact):

1. Both real candidates validate byte-for-byte (`ArtifactStore`/`PinnedBundle`
   hash checks) and report the fixed `ISOLATED_RESEARCH_INJECTION` /
   `NOT_HOST_APPROVED` / `NO_PROMOTION` labels with `financial_authority`
   false throughout.
2. Each candidate's recorded `parent_bundle_sha256` provenance matches the
   values named in the original task context.
3. `ForecastFeatureContract((('gefs31', 31),), 'F', <family>)` independently
   reproduces each bundle's exact `feature_schema_sha256` — the GEFS31
   member-count/unit/family/model-id contract is genuinely, not just
   narratively, compatible.
4. Both candidates are exercised end-to-end through the real
   `FUTURE_FORECAST` prediction path (`predict_with_bundle`, the same
   function every real decision site calls) with a real 31-member GEFS-shaped
   input — not a down-converted toy vector — and produce a correct
   `UNCALIBRATED` prediction bound to the exact candidate hash.
5. Fail-closed coverage: wrong root (raises `OSError`, file absent), a
   world-readable/non-private root (`PRIVATE_ARTIFACT_DIRECTORY_REQUIRED`),
   wrong `model_id` (`BUNDLE_MODEL_INPUT_SET_MISMATCH`), a 30- instead of
   31-member input, and a mismatched family/rule (both
   `FORECAST_FEATURE_PARENT_CONTRACT_MISMATCH`).
6. `IsolatedResearchInjection` cannot be constructed or `replace`d into
   claiming `financial_authority`, `host_approved`, `promotion_authority`,
   a non-`V11_SHADOW` mode, a non-`ISOLATED_RESEARCH_INJECTION` status, or a
   different `target` — each raises `EvidenceError`.
7. A static AST import-boundary check (mirroring
   `test_v11_offline_learning.py::test_learner_plane_never_imports_financial_order_or_host_authority_code`)
   asserts the harness never imports `production`, `host_trust`,
   `model_registry`, `certification`, or raw network/subprocess/pickle
   primitives.
8. The reverse check: none of the 11 real decision-site modules
   (`strategy_pipeline`, `position_management`, `relative_value`,
   `basket_coordinator`, `pws_admission`, `source_release`, `maker_context`,
   `reaction_runtime`, `drift_runtime`, `risk_inputs`, `strategy_admission`)
   import this harness module — it has no reachable path from any live or
   PAPER decision site.

All 26 assertions were independently verified against the real HIGH and LOW
object stores by direct execution in this environment (`python -m pytest`
itself could not be run in this sandboxed tool session — see "Verification
method" below).

## LIVE_INPUT_FEATURE_SCHEMA_COMPATIBILITY_CHECK

This is one of the four prerequisites named in
`v11_brain_shadow_preparation_20260929.json`'s `prerequisites_remaining` list
and one of the two required investigations for this task. It was
investigated using only already-committed source code — no `/var/lib/alpha-
weather-scanner` permission change was made or attempted.

**What matches:** `gefs_sources.py::assemble_path` (the live GEFS collector)
always produces exactly 31 members (`range(31)`, `GEFS_ALL_MEMBERS_AND_
LOCAL_DAY_BRACKETS_REQUIRED` enforces this), in the rule's own declared unit
(`celsius*1.8+32 if plan.rule.payload['unit']=='F' else celsius`,
`gefs_sources.py:219`). Both already match this candidate's contract exactly.

**What does not match:** every live capture unconditionally sets
`temperature_input.model_id = gefs_sources.MODEL_ID` —
`'NOAA_GEFS_0P50_LINEAR_DAY_V1'` (`gefs_sources.py:29,223`) — never
`'gefs31'`, the `model_id` this candidate was fitted and bundled under.
`predict_with_bundle`'s own `BUNDLE_MODEL_INPUT_SET_MISMATCH` check
(`model_artifacts.py:284-285`) means a natural, unmodified live capture
**cannot** presently satisfy this bundle's input-set requirement.
`test_natural_live_shaped_gefs_input_fails_bundle_model_input_set_mismatch_not_calibration`
proves this directly against the real HIGH bundle: a live-shaped 31-member
input fails closed with `BUNDLE_MODEL_INPUT_SET_MISMATCH`, not a calibration
or authority error.

**Conclusion:** this is a genuine, concrete, previously-undocumented
compatibility gap, not merely an "unreviewed" one. Closing it would require
either committed code that remaps/aliases the live `model_id` to `'gefs31'`
for this specific candidate family, or a refit of the candidate under the
live collector's own `model_id` — neither exists in committed code today.
This check is **not closed**; this section records the exact remaining gap
rather than asserting compatibility that isn't there.

## What remains blocked

This work does not change R47's OPEN status and claims no new
requirements-matrix credit. Specifically still absent and not fabricable
locally:

- **Independent/owner review and installation.** Real model state is read
  only from root-owned `/var/lib/alpha-v11/model-authority/...`
  (`model_registry.py`'s `_root_custody`/uid checks), writable only by
  `host_trust/v11-model-authority/authority.py`, whose own header states it
  is "preparation only until independently reviewed and installed by the
  owner." This worker has no root/sudo access and installing or
  self-approving it would fabricate exactly the "reviewed" step the
  prerequisite requires. `IsolatedResearchInjection` is explicitly labeled
  `NOT_HOST_APPROVED` for this reason — it is evidence *for* a future review,
  not a substitute for one.
- **`LIVE_INPUT_FEATURE_SCHEMA_COMPATIBILITY_CHECK`** — investigated and
  found genuinely incompatible today on `model_id` identity (above), not
  closed.
- **`FREEZE_EVIDENCE_BASED_FORWARD_SHADOW_SAMPLE_TARGET`** — logically
  downstream of an installed/reviewed model state; still transitively
  blocked by the same owner-only step.
- **Zero held-out evaluation.** Both candidates were trained on all 20
  available events with zero `CONFIRMATION`/`DEVELOPMENT` examples (per the
  result record); this is unchanged by this work and is a real evidentiary
  gap independent of the shadow-injection question.

## Verification method (this session)

`python -m pytest` could not be executed in this sandboxed tool session: the
project's runtime dependencies (`httpx`, `pydantic-settings`, etc., per
`requirements-dev.txt`/`requirements-runtime-hashed.txt`) are not installed
under the available Python interpreter, and installing them required package-
manager or virtual-environment actions outside this session's permitted
command set. This is a sandbox limitation, not a code or test defect.

Every assertion in `tests/test_v11_r47_real_candidate_shadow_injection.py`
was instead verified by direct, unmocked execution against the real HIGH and
LOW object stores: each test function was called directly (bypassing pytest's
collector, not its logic) against the actual repository code, and separately
`python -m py_compile` confirmed both new files are syntactically valid. All
26 cases passed. The two cases that import `polymarket_scanner.v11.gefs_sources`
(which transitively imports `httpx`) were verified with `httpx` stubbed to
its exact call shape used by the code path under test; they exercise the same
already-committed `gefs_sources.MODEL_ID` constant and `predict_with_bundle`
failure mode that `tests/test_v11_gefs_sources.py` already exercises
elsewhere in this suite, so this is a low-risk gap. A maintainer with the
project's normal dev virtualenv should run
`python -m pytest tests/test_v11_r47_real_candidate_shadow_injection.py -q`
to get an unmocked, fully-installed confirmation.
