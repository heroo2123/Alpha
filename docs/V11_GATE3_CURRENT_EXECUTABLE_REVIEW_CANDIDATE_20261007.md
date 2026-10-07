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
`d1c5602aa77e0d835e416d281b4a78754a3a79df`, tree
`7da55dd47f4a0acfb6062765ec5df67bada1ebf4`, and exact Git blob ID,
SHA-256 and byte length for each of 20 files. It includes the three target
executables, the collection/launch/transport protocol documents, observed GEFS
sizing input, associated supporting code, and seven directly relevant tests.
The verifier checks raw commit-parent ancestry, historical and source blobs,
all 20 live files, and exact drift-commit coverage. Git replacement refs and
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

## Identity effect after independent review

An exact independent PASS on the candidate could support current-byte portions
of `code.collector_commit_tree` and `code.source_file_hashes`, and provide the
current runtime/ledger scope needed to supersede the historical
`code.slice3_exact_commit_review` baseline. None is credited by this package.
The code identities still need separate G3-L package-entry binding and review;
`code.transport_commit_tree` additionally needs a concrete real adapter/build.
`code.dependency_build_lock`, `code.runtime_entrypoint_review`, and missing
historical G3-I review terminals remain separate. The identity audit's seven
retained/offline rows, 70 future rows, 77-slot unfilled baseline screen, and
zero qualification credit remain unchanged.

Run the offline verifier with
`python -m tools.v11_gate3_current_executable_binding` from this checkout.
It returns a byte-binding result only and has no provider or launch path.

Candidate verification: 58 identity/binding/adversarial cases passed, including
replacement, graft, missing pin, stale baseline, duplicate JSON key and live
byte drift attacks. The seven focused collector/runtime/ledger/dependency test
files passed 405 cases. These are author checks, not an independent verdict.
