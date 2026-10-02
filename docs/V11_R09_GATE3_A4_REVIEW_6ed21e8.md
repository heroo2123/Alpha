**CHANGES_REQUIRED — completed independent Astra/high exact-commit review.**

Candidate `6ed21e8f451399773142c69c4f1422a4e6c394a5`, tree
`ffa0934aedd37a419f50d87fae6550721330365a`, parent `28dd60403bdc8e819e64e9dd60be0109669c4960`.
The preserved detached checkout `/tmp/alpha-v11-gate3-a4-review-6ed21e8`
remained clean. Its five changed-file hashes match the earlier partial
terminal. The prior aborted review remains intact and supplies no verdict;
this is a newly completed adjudication. No renewed automatic-review rejection
occurred during this adjudication.

**R3 — P2: a FIFO alternate path blocks the new Git-store refusal.**

At `tools/v11_r09_gate3_a4_verify.py:115`, `_isolated_git_store()` opens
`.git/objects/info/alternates` with `O_RDONLY | O_NOFOLLOW`. Any existing
alternate is supposed to refuse, but opening a FIFO with no writer blocks
before reaching that refusal. `open_verified()` has no deadline at this
boundary. The rest of the verifier's artifact opening already uses
`O_NONBLOCK`, which this new open omits.

The independent local-fixture probe creates an ordinary valid synthetic
repository and lock, then places a FIFO at the alternate path. A review-only
one-second alarm interrupts `open_verified()`; the captured signal frame is
exactly `_isolated_git_store`, line 115. Without that external alarm, the
open waits for a writer. This is a deterministic availability/refusal bug,
not evidence of financial execution, provider access or successful acceptance
of unverified source. No real repository or authority path was mutated.

Required correction: reject an existing alternate path without a blocking
read-open, preserving no-follow behavior and refusal of regular files,
directories and symlinks. Add a bounded FIFO regression that expects
`GIT_IDENTITY_UNAVAILABLE` rather than the review alarm. Preserve packed and
loose object behavior and all current identity checks. A fresh exact-commit
independent review is required before integration.

**Prior findings and bounded acceptance.**

- R1: closed for the reported repository-local configuration/helper case.
  Git now queries a private bare directory with fixed config and a held
  source object-directory alternate. Source repository configuration is not
  used. Missing commit, tree and blob tests refuse without the local marker
  helper executing; packed objects still work on this host. This does not
  qualify Git's native loader/bootstrap closure or mutable external storage.
- R2: closed for the late fileless host-module collision and unresolved
  from-import case. The child checks its inherited namespace after confinement,
  and imports require declared modules or existing attributes. Two independent
  controls introduce top-level/submodule collisions only after fork and both
  refuse before the locked entrypoint executes.
- The earlier F1–F6 controls pass in the candidate's retained regression suite.
  This does not expand their prior scope into full A4 acceptance.

**Independent validation.**

| Run | Result |
| --- | --- |
| Exact candidate focused and retained prior-review suites | 56 passed |
| New independent probes | 7 passed: 6 refusal controls, 1 deliberate defect reproduction |
| H1–H6, runtime, storage, restart composition | 254 passed, 2 existing fork/thread deprecation warnings |
| Candidate identity and diff whitespace checks | Clean and unchanged |

Each successful run recorded zero observed Python socket-audit attempts.
This audit covers the instrumented Python process, not a system-wide network
trace. Tests use only synthetic local fixtures; no provider endpoint is used.
There are 310 successful candidate/regression test outcomes, plus seven
independent probes; the defect reproduction must not be counted as acceptance.
No new full-release run is claimed.

Initial unsuccessful review-harness runs are retained. The first stricter
FIFO probe asserted line 110; the captured blocking frame was line 115, so
only that assertion was corrected. The first adjacent runner lacked a
`__main__` guard for multiprocessing spawn and leaked descriptors into
completed fixtures. It reported 241 passed/13 failed, including 12 disk-reserve
failures and one spawned-child timeout. The corrected runner guards startup
and closes only descriptors pointing inside each of this review's completed
test directories after fixture finalization, then preserves empty numbered
directories. The unchanged adjacent tests all passed on rerun. It closed
174 completed-fixture descriptors; maximum observed single-test allocation
was 67,342,336 bytes. No reservation, timing assertion, candidate code or
older scratch directory was changed or removed.

Reproduce from the pinned checkout using the retained runner with
`PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=/tmp/alpha-v11-gate3-a4-review-6ed21e8`
and `/home/alphaadmin/AlphaV11_Dev/venv/bin/python <runner> <test paths>`.
The runner creates and cleans its own unique bounded `/tmp` directory.
The machine terminal binds the exact artifacts and successful/failed logs.
The completion terminal honestly records direct coordinator finalization,
not a successful exit of the earlier aborted reviewer.

**Disposition.** Candidate remains unmerged and unaccepted pending R3 repair.
A4 remains OPEN: accepted A2/A3 artifacts and lock, pre-import bootstrap and
native-loader verification, actual decoder/MEMFS binding and integration are
still absent. A5 model semantics, A6 genuine current-run evidence and concrete
launch qualification remain separate. Offline tests create no provider or
forward SHADOW evidence. G3-L remains NO-GO; the host is also below its
2 GiB disk floor. Score remains 91/200, formal 1/50; NOT_READY_TO_FUND.

Private master hash matches its pin; scanner/controller inactive and disabled,
execution inactive and masked, protected authority roots absent. No V10,
AxiomTrade, credential, financial, provider, service, root-authority or remote
publication action occurred. Local commands used approved sandbox overrides
because the sandbox wrapper could not initialize its loopback interface.
