# Gate 3 A4 offline runtime verifier candidate

**Status: implementation candidate, unreviewed. A4 remains open; G3-L remains NO-GO.**
This file records the scope of `tools/v11_r09_gate3_a4_verify.py` against
`V11_R09_GATE3_IDENTITY_ACCEPTANCE_20261001.md` A4. It is not a dependency
lock, decoder qualification, launch approval, or independent review.
The independent reviews of `ef53d61` and `28dd604` returned
**CHANGES_REQUIRED**. This repair addresses F1–F6 and the later R1/R2
counterexamples. Independent review of `6ed21e8` then found R3: a FIFO
at the alternate path blocked before refusal. The R3 correction opens that
path nonblocking and adds a bounded refusal regression. It requires a fresh
independent exact-commit review; prior author evidence does not accept it.

## Lock input and behavior

`open_verified(lock_path, expected_lock_sha256=...)`
requires an independently supplied digest for the exact JSON lock. The JSON
has schema `ALPHA_V11_GATE3_A4_OFFLINE_LOCK_V1`. Bytes must be canonical JSON
(sorted keys, compact separators, ASCII escapes); duplicate keys and non-finite
constants refuse. The exact keys are:

| Key | Meaning |
| --- | --- |
| `root`, `commit`, `tree` | Absolute source root and exact Git object IDs. The commit's tree must match. |
| `entrypoint` | Fully qualified Python module declared in `artifacts`. |
| `environment_sha256` | SHA-256 of canonical JSON for the complete active environment, so any override refuses without storing values in the lock. |
| `interpreter` | SHA-256 and byte length of `/proc/self/exe`. |
| `git_tool` | Absolute Git executable path, SHA-256 and byte length; copied to a sealed memfd before Git object queries. |
| `bootstrap_mappings` | Exact executable file mappings observed in `/proc/self/maps`, each with path, SHA-256 and byte length. |
| `bootstrap_files` | Exact loaded Python `.py`/`.pyc` files found through `sys.modules`, including the verifier: resolved path, device, inode, SHA-256 and size. |
| `run_id`, `run_record` | Mandatory run identity and absolute durable first-lock record path. The latter must be exactly the lock path plus `.a4-first-lock`. |
| `artifacts` | Exact path, kind (`python`, `native`, `data`), SHA-256, size, module name or null, and Git blob ID or null. Python sources require a Git blob matching the pinned commit and actual bytes. |

Before the first open, the run coordinator calls
`initialize_run_record(path, run_id=..., lock_sha256=...)` once. It creates the
record exclusively and fsyncs the file and parent directory. Every open and
preflight requires that record to exist and match the lock pin and run ID;
missing, changed or replaced records refuse. A restart for the same run needs
no opt-in resume argument. The caller must preserve the same run ID and
record path across restarts, and protect record custody from deletion or
rewriting. The verifier cannot authenticate a caller that invents a new run.
Changing `run_record` inside a rewritten lock cannot redirect this check.

Every artifact is opened component by component with `O_NOFOLLOW`, checked
against its pinned bytes, and copied to a write-sealed memfd. Original fds are
held until the session closes, so a removed file cannot regain the same inode
number. The lock file's original fd is also held, and its pathname and bytes
are checked before and after use. The verifier checks every declared artifact,
full environment, root identity, and executable mapping again before and after the test
entrypoint. It also checks the loaded Python file set and run record. It refuses changed paths even when the immutable snapshot would
still contain the old bytes. `read_data(path)` accepts only locked data paths
and reads the sealed copy. Git object queries use a private temporary bare
repository with fixed config and no remote or transport settings. Its only
alternate is the held `.git/objects` directory fd from the source root;
repositories with an existing object alternate or a non-directory `.git`
refuse. The source repository's local config is never read by the object query.
The Git child also has a scrubbed environment, disables replacement objects
and lazy fetch, and re-hashes raw commit/tree/blob bytes. The artifact blob ID
is independently computed from the locked bytes. Missing objects refuse
without consulting the source repository's promisor remote or helper. Local
loose and packed objects were both exercised on the installed Git 2.43 host.

`run()` forks one child after parent preflight. The child closes unrelated file
descriptors, installs a libseccomp filter that denies new opens, process
creation, sockets, network connection and path mutation, then compiles only
locked Python module bytes from sealed memfds. A local importer rejects
undeclared imports. Locked top-level module names that collide with host
modules are checked at open, in the forked child after confinement, and at
each locked import. An unresolved `from X import Y` refuses inside the
importer before CPython's `IMPORT_FROM` host-module fallback. The filter also denies chmod/chown,
extended-attribute, timestamp and node mutation calls, plus pidfd and
cross-process memory calls used in the reviewed probes. The child returns a JSON-compatible value over a pipe;
the parent rechecks the lock before returning it. These tests are synthetic
and unprivileged. There is no provider, network, credential, service, or live
runtime entrypoint in this module.

## Scope limits requiring later work

- The A2/A3 dossier has no accepted complete lock. This format is a candidate
  interface; a later translator and the exact accepted lock need separate
  review. A self-created lock digest is not evidence of qualification.
- Python and the verifier are already running when this library begins. The
  interpreter, loaded Python files and previously mapped native libraries are
  checked after load; this cannot prove verification before their own imports
  or constructors. The complete bootstrap and Git executable loader closure need
  independent review. Loaded in-memory Python code may differ from the current
  source bytes; the file binding detects later changes, not pre-load tampering.
- Native files are verified and sealed but this candidate exposes no native
  `dlopen` or decoder call. The child denies an unexpected lazy native load.
  A reviewed mechanism must bind the complete native loader closure and actual
  mappings, including constructors, before real decoder execution. It must
  also bind ecCodes definition/sample selection and embedded MEMFS range
  selection to accepted A2/A3 and static inventory bytes.
- The seccomp filter is a focused offline test restriction, not a complete
  hostile-code sandbox or an exhaustive filesystem-mutation policy. The lock's
  reviewed source and the bootstrap remain part of the trusted computing base.
  The current runner accepts only
  JSON-compatible synthetic results.

The candidate therefore demonstrates refusal controls and immutable data use
without claiming the full A4 positive criterion. Fresh independent review,
accepted A2/A3 artifacts, and an exact native/decoder integration review are
still required before any A4 PASS assertion.

## Repair verification

The prior 27-test A4 suite is extended with late fileless-module, unresolved
from-import, missing commit/tree/blob promisor-helper and packed-object tests.
The independent 28dd604 local-only probe artifact is retained in
`tests/test_v11_r09_gate3_a4_review_28dd604.py`; its two reproduction
assertions now require refusal. Bounded results and hashes are recorded in the
accompanying terminal JSON. These tests are author evidence, not an
independent review or an A4 acceptance verdict.
