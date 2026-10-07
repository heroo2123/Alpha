# G3-L current executable byte-binding candidate — 2026-10-07

Status: **UNREVIEWED CANDIDATE; G3-L NO-GO; qualification credit 0.** This
package records current executable bytes for independent exact review. It does
not revise `V11_R09_GATE3_G3L_RECONCILIATION_20261002.json`, reclassify its
historical identities, manufacture missing G3-I terminals, or authorize a
provider request, capture, launch, order, or financial action.

The e98f2f7 seal candidate packages seven historical document/review identities.
The supplied Astra verdict says the current collector, runtime and ledgers have
legitimate post-baseline drift, so historical exact reviews cannot be reused as
reviews of these current bytes. The e98f2f7 commit's seal map and tests were
read from local Git. No persisted e98f2f7 reviewer report or verdict file was
present in this checkout or its detached review worktree; the verdict statement
above is therefore the one supplied with this task, not a locally reverified
review artifact.

## Exact byte roots

`V11_GATE3_CURRENT_EXECUTABLE_BINDING_20261007.json` pins source commit
`58e63fc8409f49d60b2c4a06efa377a6b30ee195`, tree
`5d68756a9aea768acbfa4c99b82d1e61a7a6ffbb`, and exact Git blob ID,
SHA-256 and byte length for each of 92 files. It includes the three target
executables, all 81 statically reachable local Python modules from those
executables and the already pinned support modules (including package
initializers), the collection/launch/transport protocol documents, observed
GEFS sizing input, and seven directly relevant tests. Some imported modules
serve other product paths; their source bytes are pinned here without changing
or authorizing those paths. A separate test recomputes the transitive AST
import closure from source-commit Git blobs and requires it to be a subset of
the fixed verifier coverage. It refuses recognized dynamic import calls for
separate review.
The verifier checks raw commit-parent ancestry, historical and source blobs,
all 92 live files, and the listed drift-commit coverage. Git replacement refs and
local grafts cannot redefine its source bytes or ancestry. The source commit
precedes this candidate; an independent reviewer must additionally pin and
review the final candidate commit and tree.

| Executable | Historical observation | Current source SHA-256 | Post-baseline changes |
| --- | --- | --- | --- |
| `tools/v11_r09_gate3_collector.py` | `95e07fa`, `44da155a…` | `0cf745d9…` | `ef7b470`: budget and sizing refusal repair; its collector and message-size tests changed together. Earlier provider-bound and GEFS ceiling changes already precede the historical observation. |
| `tools/v11_r09_gate3_runtime.py` | `6340cb4`, `3a45eb46…` | `ffce597f…` | `7ef7d5d`, `9a844b1`, `70ef1a1`: optional attempt and evidence intake gates; `9d0e9a1`, `60ac120`, `f1e85ba`, `5a0ce21`: bounded stream contract and byte-conservation repairs; `dd529ee`: RAW decoder custody and closure binding. |
| `tools/v11_r09_gate3_ledgers.py` | `6340cb4`, `a049e7bc…` | `b7e8f77c…` | `dd529ee`: mutex-protected RAW handoff close and explicit clock phase custody. |

These are Git path-change classifications, not retroactive independent approval.
The collector's earlier baseline includes the `3e6a872`, `ae53102` and
`95e07fa` ceiling lineage. The e98f2f7 review boundary does not invalidate
the seven historical document/review identities, and this package does not
attempt to seal them.

## Current-main dependency repin

The repin freezes current main at `58e63fc8409f49d60b2c4a06efa377a6b30ee195`
(tree `5d68756a9aea768acbfa4c99b82d1e61a7a6ffbb`). The starting checkout
`6e031585bba83d5ab9e208dd32879a16f4de5382` was its direct parent; the only
intervening changes were two checkpoint documents outside the binding. The
candidate branch advanced to this existing commit without a merge. Before
editing, all 92 old manifest entries were verified against real `d1c5602`
blobs, and all 92 starting HEAD/live files matched the frozen current-main
blobs. Exactly the following four paths differed from that previous source.

Each new historical baseline is the previous frozen source commit
`d1c5602aa77e0d835e416d281b4a78754a3a79df`, tree
`7da55dd47f4a0acfb6062765ec5df67bada1ebf4`. These are byte observations,
not claims that an independent review approved the dependencies.

| Dependency under `polymarket_scanner/v11/` | Exact change commit | Classification |
| --- | --- | --- |
| `learning_capture.py` | `90d2d6446155e928d66c16660b5e90fda3a77fb0` | Recheck admission after child decisions and atomically guard pinned heads at capture publication. |
| `forecast_features.py` | `fd965a96f9bbad4d3b9e99dec11d169c8ff8c33f` | Validate final-payout or next-observation prediction targets against bundles; initial research bundles remain final-payout only. |
| `model_artifacts.py` | `fd965a96f9bbad4d3b9e99dec11d169c8ff8c33f` | Pass the pinned bundle target into forecast feature-contract validation. |
| `physical_inference.py` | `fd965a96f9bbad4d3b9e99dec11d169c8ff8c33f` | Exclude the new prediction-target field from the physical schema digest to preserve its established identity. |

Raw commit ancestry and each change against its first parent were checked.
Path-specific history has exactly the listed non-merge change per dependency;
merge integration does not invent a new author change. All 92 manifest file
rows were regenerated from real frozen-source Git objects, with only these
four rows changing. The prior three historical baselines/traces and the fixed
92-path set remain intact. Live or committed drift still refuses. The test
suite independently recomputes the 81-module static import closure and probes
drift and provenance tampering for every repinned dependency.

This is an author candidate requiring a subsequent independent different-model
review of its exact final commit/tree. It grants no G3-L identity or score
credit and keeps `launchable=false`, `qualification_credit=0`.

## Identity effect after independent review

An exact independent PASS on the successor candidate could support source-byte portions
of `code.collector_commit_tree` and `code.source_file_hashes`, and provide the
current runtime/ledger scope needed to supersede the historical
`code.slice3_exact_commit_review` baseline. None is credited by this package.
The code identities still need separate G3-L package-entry binding and review;
`code.transport_commit_tree` additionally needs a concrete real adapter/build.
`code.dependency_build_lock`, `code.runtime_entrypoint_review`, and missing
historical G3-I review terminals remain separate. The identity audit's seven
retained/offline rows, 70 future rows, 77-slot unfilled baseline screen, and
zero qualification credit remain unchanged.

This binding covers source files in the source-commit static local import
closure. It does not bind Python bytecode caches, `sys.path`, the interpreter,
third-party packages, dynamically selected modules, or the executed image.
Those limits prevent a source-byte PASS from serving as launch approval.

Run the offline verifier with
`python -m tools.v11_gate3_current_executable_binding` from this checkout.
It returns a byte-binding result only and has no provider or launch path.

The previous 897d064 candidate received CHANGES_REQUIRED in the independent
exact review because eight imported local modules lacked pins. This successor
adds the complete statically discovered closure, an independent closure-subset
test, live and committed drift probes for a formerly unpinned module, and a
stable missing-file refusal. The prior 58-case claim was not reproducible;
the focused binding and identity-audit suite passed 40 cases in normal mode
and 40 under `python -O`. These are author checks, not an independent verdict.

## Repin author validation

Interpreter: `/home/alphaadmin/AlphaV11_Dev/venv/bin/python`, Python 3.12.3,
pytest 8.3.3. Each command below ran once normally and once with `-O` inserted
before `-m`. No skips: **134 passed per mode** for the first suite and
**121 passed per mode** for the second (255 per mode, 510 total). Optimized
runs emitted pytest's expected warning about assertions outside test modules.

```sh
GIT_NO_LAZY_FETCH=1 /home/alphaadmin/AlphaV11_Dev/venv/bin/python -m pytest -q tests/test_v11_gate3_current_executable_binding.py tests/test_v11_gate3_postbinding_admission.py tests/test_v11_r09_gate3_g3l_identity_audit.py tests/test_g3l_original_terminal_candidate.py tests/test_v11_learning_capture.py tests/test_v11_model_artifacts.py tests/test_v11_physical_inference.py --tb=short
GIT_NO_LAZY_FETCH=1 /home/alphaadmin/AlphaV11_Dev/venv/bin/python -m pytest -q tests/test_v11_forward_qualification_repairs.py tests/test_v11_shadow_commission.py tests/test_v11_katl_live_plan.py tests/test_v11_forecast_learning.py --tb=short
python3 -m tools.v11_gate3_current_executable_binding
python3 -O -m tools.v11_gate3_current_executable_binding
git diff --check
```

Both standalone verifier runs passed: 92 files, frozen source `58e63fc`,
`launchable=false`, `qualification_credit=0`. Whitespace validation passed.
The initial 134-test runs had 133 passes and one failure in each mode because
the author supplied `GIT_NO_REPLACE_OBJECTS=1` to the entire test process:
this prevented the replacement-ref test's intentional ordinary-Git positive
control from seeing its replacement blob. Removing that outer environment
override made the isolated control and both complete reruns pass; the
verifier's internal replacement/graft protections were unchanged. The system
`python3` lacked pytest, so tests used the existing development virtualenv;
no dependencies were installed.

Main later advanced to `4830849350d2c55a7e97affe39bec74e272c069b` (tree
`7516a074a8ebfc356a3e768da422b2df07da1034`) while author validation ran.
Read-only comparison found only checkpoint documents changed and zero changes
to the 92 pinned paths. This candidate retains the original frozen source
anchor instead of following a moving branch. No merge, deployment, provider
request, runtime/service/V10 operation, or financial action was performed.
Author validation is not the required independent exact-review verdict.
