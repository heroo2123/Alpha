# Independent exact-commit review: Alpha V11 A4 R3 repair

**PASS_IN_SCOPE — R3 FIFO alternate refusal is repaired in this three-file change.** This is an independent Sol/high review of commit `95541daed694db55e2e5d1fc460d94404395645b`, tree `a20045e26db8e16ccb3aa74e574e80defb8de7eb`, parent `6ed21e8f451399773142c69c4f1422a4e6c394a5`. The detached review checkout remained clean before and after testing. `git diff --check` passed. The changed paths are exactly the candidate document, A4 tests, and verifier listed below; their SHA-256 values match the coordinator's author manifest.

| Changed path | SHA-256 |
| --- | --- |
| `docs/V11_R09_GATE3_A4_RUNTIME_VERIFICATION_CANDIDATE_20261001.md` | `463ee7d74e50b541da57fffc5a2ca92c16df9820633d9952da9ea7e9141762f2` |
| `tests/test_v11_r09_gate3_a4_verify.py` | `2e2693222ff44f1e9621fede368ff758035af9e006a5a3538b4939f1f98bc14d` |
| `tools/v11_r09_gate3_a4_verify.py` | `3a122d1433231ad789779a1bc85e6e7d4ba5ee39f9b699e5674407974ab57f28` |

## Finding and independent checks

The prior completed independent review of `6ed21e8` found that opening `.git/objects/info/alternates` could wait indefinitely on a FIFO with no writer. This repair adds `O_NONBLOCK` while retaining `O_NOFOLLOW`. On this host, the nonblocking read-open of the no-writer FIFO returns immediately; the verifier closes the descriptor and raises `GIT_IDENTITY_UNAVAILABLE`. A symlink still fails the no-follow open and is converted to the same refusal. Regular files and directories open and then reach the explicit refusal. The existing absent-alternate path and packed/loose Git-object checks continue to pass. The added candidate FIFO test uses a two-second alarm; I adapted a **copy** of the prior independent defect-reproduction probe into an independent two-second refusal assertion. The retained historical evidence was not edited.

The copied probe also exercised regular-file, directory, symlink, late child-only host-module collision (top-level and submodule), and worktree-gitfile refusal. The focused suite exercised the retained R1/R2 and F1–F6 controls. This finding closes R3 only for the tested local alternate forms and bounded offline verifier behavior; it does not expand R1/R2 or the earlier controls beyond their accepted scope.

## Runs and evidence

All runs used `/home/alphaadmin/AlphaV11_Dev/venv/bin/python`, `PYTHONDONTWRITEBYTECODE=1`, `PYTHONPATH` set to this checkout, `-p no:cacheprovider`, synthetic local fixtures, a copied prior runner with its `__main__` guard, and external timeouts. The runner's cleanup was restricted to newly created completed test fixture directories and their leaked descriptors. No old scratch was removed and no test or disk reservation was weakened. The runner's Python socket audit observed zero attempts in each run; that observation is process-local, not a system-wide trace.

| Run | Exact test selection | Result | Log SHA-256 |
| --- | --- | --- | --- |
| Focused, 120 s cap | `tests/test_v11_r09_gate3_a4_verify.py`, `tests/test_v11_r09_gate3_a4_review_28dd604.py`, copied independent probes | 64 passed in 42.52 s; zero socket-audit attempts | `3cb7deab87f4a005672bb649c6d237a82615a6d0fb53285727072e5864130e6c` |
| Adjacent, 150 s cap | `tests/test_v11_r09_gate3_h1h6.py`, `tests/test_v11_r09_gate3_runtime.py`, `tests/test_v11_r09_gate3_store_v1.py`, `tests/test_v11_r09_gate3_restart_composition.py` | 254 passed in 27.03 s; two existing fork deprecation warnings; zero socket-audit attempts | `a53f2639bddb2c308b13710a7a1415cfae73b88dfa5c1a2bb7e3251794ddb333` |
| Standalone executable probes, 120 s cap | copied independent probes only | 7 passed in 3.84 s; zero socket-audit attempts; repeated within focused run | `bc8caa94ecbabf97ef47422829f17414c2efea1f60f335033b234b285bcfb825` |

Review-owned reproduction artifacts are `/tmp/alpha-v11-gate3-a4-r3-review-95541da-runner.py` (SHA-256 `89fce932012c43fcee4efbd5cf6e032825579e88e5f0b009740b06d7831c61c2`), `/tmp/alpha-v11-gate3-a4-r3-review-95541da-probes.py` (`fa1066f0555635f64e1dbba4ab2aaa48d526eb273598429fd5ab8617b026d90f`), and executable `/tmp/alpha-v11-gate3-a4-r3-review-95541da-probes.sh` (`4b044f615dd1919dd4d4f814a3444e05afdf589ed4cf602df5666304f224fed8`). Run the shell launcher directly to repeat the independent probes. The focused and adjacent invocations are recorded in the machine verdict with their test selections and bounds. The author manifest was read and hash-compared, but author tests were not used as independent acceptance. The earlier completed review report, terminal, and original defect probe were read; the earlier aborted review is not acceptance.

## Scope and disposition

**A4 remains OPEN and G3-L remains NO-GO.** This narrow pass does not supply an accepted A2/A3 lock or reproducible build, pre-import/bootstrap and Git/native loader closure, native decoder and MEMFS selection bound through actual decode, or genuine current-run evidence. It does not qualify A5/A6/A7/A8, authorize provider/network traffic or financial execution, or cross any C/J/E/A score threshold. No provider request, credential, service, root authority, V10/AxiomTrade action, merge, publication, or ledger R1 change was made. No full-release or live integration run is claimed. The source checkout was inspected and tested read-only; only review-owned `/tmp` evidence was written. Local commands used approved sandbox overrides because the sandbox wrapper could not initialize its loopback interface.
