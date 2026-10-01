# Gate 3 A4 offline runtime verifier — independent exact-commit review

**CHANGES_REQUIRED. A4 remains OPEN. G3-L NO-GO.** This is an offline implementation review. It does not authorize a provider request, launch, SHADOW run, financial action or production use.

- Candidate: `ef53d61174e7c23e58ddf879d94918c0eafd58e1`
- Tree: `d632c94475fd100e51c2417479ffd4fa054587f2`
- Base: `6ce37ab6ba8b93bafacdcb1597f53916d994eea7`
- Checkout: `/tmp/alpha-v11-gate3-a4-runtime-verification-20261001`
- Reviewer: Claude Opus. The author was a different model (Codex lane `a4`). Review date: 2026-10-01/02 UTC.

## Scope and validation

I read the A4 row in `V11_R09_GATE3_IDENTITY_ACCEPTANCE_20261001.md` and the complete four-file diff: the verifier, its tests, the candidate document and the terminal. I did not edit the candidate. It was clean before and after validation. No provider, network, service or financial action occurred.

| Check | Result |
| --- | --- |
| Author tests `tests/test_v11_r09_gate3_a4_verify.py` | 18 passed, 3.13 s (reproduces author terminal) |
| Independent adversarial probes (`V11_R09_GATE3_A4_REVIEW_ef53d61_probes.py`) | 6 passed, 2.38 s; **each pass reproduces a defect** |
| `git diff --check HEAD^ HEAD` | Clean (per recovered checkpoint) |

Run the probes from the candidate checkout:

```bash
cd /tmp/alpha-v11-gate3-a4-runtime-verification-20261001
PYTHONDONTWRITEBYTECODE=1 /home/alphaadmin/AlphaV11_Dev/venv/bin/python -m pytest -q -p no:cacheprovider --rootdir=. <path>/V11_R09_GATE3_A4_REVIEW_ef53d61_probes.py
```

## What is sound

- Executed and read bytes come from write-sealed memfds. Path checks can only detect changes; they are not the protection. This addresses the substitution race in the A4 row.
- Lock bytes are pinned by a digest supplied by the caller. The lock path and fd are rechecked. Artifacts are walked component by component with `O_NOFOLLOW` and dev/ino retention. Exact executable-mapping sets are bound to dev/ino plus content.
- `RTLD_NOLOAD` for libseccomp avoids running a new constructor. The seccomp backstop denies opens, exec, process creation and sockets in the child. Undeclared lazy loads from disk therefore fail closed.
- The candidate document honestly lists most of its trust-base limits: the bootstrap, the native loader closure, the absent `dlopen` path and the sandbox scope.

## Findings

| ID | Pri | Finding | Probe |
| --- | --- | --- | --- |
| F1 | P1 | **A restart under a different build is accepted.** `RESTART_BUILD_CHANGED` fires only when the caller passes `resume_lock_sha256`. A restarted process that omits it silently runs a new build. The A4 row requires "restart under a different build" to refuse. The verifier keeps no durable run/session binding, so the check is opt-in. Repair: add a mandatory run identity with a durable, write-once first-lock record (or an equivalent reviewed mechanism). A restart for the same run must then match it, and a missing record must refuse. | `test_restart_under_new_build_accepted_when_resume_digest_omitted` |
| F2 | P1 | **Git source identity can be spoofed by the object database.** `rev-parse`/`cat-file` honour `refs/replace/*`, and the git child gets the full inherited environment, including any `GIT_DIR`, `GIT_OBJECT_DIRECTORY`, `GIT_ALTERNATE_OBJECT_DIRECTORIES`, `GIT_REPLACE_REF_BASE` or config. Only a digest of that environment is pinned, so a reviewer cannot audit it. A replace ref makes the lock name the genuine reviewed blob ID while unreviewed bytes pass `GIT_SOURCE_MISMATCH` and execute. Repair: compute the blob ID locally (`sha1(b"blob %d\0" + content)`) and compare it with `git_blob`. Run git with `--no-replace-objects` / `GIT_NO_REPLACE_OBJECTS=1` and a scrubbed explicit environment (`GIT_CONFIG_NOSYSTEM=1`, `GIT_CONFIG_GLOBAL=/dev/null`, no `GIT_*` overrides). Preferably re-hash the raw commit and tree objects along the path so the commit-to-tree-to-blob chain does not trust the object database. | `test_git_replace_ref_spoofs_source_identity` |
| F3 | P2 | **The bytes a reviewer reads and the parsed lock can differ.** `json.loads` keeps the last duplicate key and accepts `NaN`/`Infinity`. A reviewer who checks the pinned bytes can read `"entrypoint": "pkg.main"` while the verifier runs `decoder`. Repair: reject duplicate keys (`object_pairs_hook`) and non-finite constants, and require canonical bytes (re-serialised canonical JSON must equal the raw bytes). | `test_duplicate_json_key_last_wins` |
| F4 | P2 | **Pure-Python bootstrap is unbound, and the document overstates this.** The candidate document says "bootstrap modules … are checked after load". Only native executable mappings and `/proc/self/exe` are bound. Loaded stdlib `.py`/`.pyc` files, the venv's site packages and the verifier module itself are never hashed. The child inherits all of them through `sys.modules`. Repair: bind the `sys.modules` source/cached-file set (path, dev/ino, SHA-256) in the lock, or correct the document and record this explicitly as an unclosed A4 trust-base gap. | `test_pure_python_bootstrap_module_change_undetected` |
| F5 | P3 | **The seccomp "path mutation" coverage is incomplete.** `chmod`/`fchmod`/`fchmodat`/`fchmodat2`, the `chown` family, the `setxattr`/`removexattr` family, `utimensat`, `mknod`/`mknodat` and `pidfd_open`/`pidfd_getfd`/`process_vm_*` are allowed. The cross-process ones rely on Yama `ptrace_scope=1`, which is the current host value. The child can change a locked file's mode. Repair: extend the deny list, or narrow the document's claim. | `test_child_can_mutate_path_metadata` |
| F6 | P3 | **The locked importer falls back to unverified host modules.** For `from X import Y`, CPython falls back to `sys.modules["X.Y"]`. A locked package whose name collides with an already-loaded host package (for example `json`) gets the unverified host `json.decoder` without `UNEXPECTED_LAZY_IMPORT`. Repair: refuse locked top-level names already present in `sys.modules`, or give the child a scrubbed module namespace. | `test_from_import_falls_back_to_unverified_host_module` |

Informational, not scored: the trusted child can reach host modules through `runtime` attributes and function `__globals__`. The document already treats the reviewed source as part of the trust base and says the seccomp filter is not a hostile-code sandbox. I accept that framing for an offline candidate. A partial child write produces `JSONDecodeError` instead of `VerificationError`; it still fails closed.

## Disposition

- **Do not integrate `ef53d61` into main as an A4 component** until F1–F4 are repaired and an exact-commit re-review accepts the repair. F5 and F6 may be fixed or explicitly narrowed in the document in the same repair.
- The repair is narrow: one module, its tests and the candidate document. It should be done by the original author lane or another model, not by this reviewer, so independence is preserved.
- Even after repair, A4 PASS still requires the unclosed items the candidate itself names: the accepted A2/A3 lock and translator, a reviewed bootstrap and native loader closure, and decoder/MEMFS selection binding.
- No C/J/E/A boundary is crossed. **91/200 (45.5%), formal 1/50; NOT_READY_TO_FUND.**
