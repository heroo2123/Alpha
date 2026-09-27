# V11 CI portability finding

GitHub Actions run `35931387025` at implementation
`77a075526854e25986ff753803b3972e6e59d780` failed its Python 3.11 and 3.12 test jobs.
The runtime lock and isolated runtime jobs passed. Decoded test logs identify
three model-authority crash-fixture cases failing `MODEL_AUTHORITY_LOCK_CUSTODY`.
The Python 3.12 result was 2,615 passed / three failed.

The synthetic crash test mocked process root identity and parent custody but left
lock ownership and `fchown` dependent on actual process privilege. The local
environment presented UID 0; GitHub's ordinary runner correctly failed the
production lock owner check before the intended interruption injection.

The correction scopes an OS fixture to the imported test authority module,
models root ownership and records ownership requests without changing owners.
Atomic writes/rename/fsync still exercise real temporary files. A separate
negative test confirms that the production lock owner check rejects non-root
ownership. Production authority code and custody/permission requirements are
unchanged. No runner privilege escalation or test skip is introduced.

22 governance tests pass locally. A local attempt to launch the same suite under
an unprivileged UID was blocked before execution: this environment has UID 0,
zero effective Linux capabilities and NoNewPrivs. That check is not marked passed.
The correction passed GitHub Actions run `35933848981` at commit
`d4902a26aef5964e437f46d605a93db153d4f3ef`, including ordinary Python 3.11 and 3.12
runners. The subsequent coordinator implementation passed run `35933951235` at
`8437079613ec0d3fd17eb20c82ae89bbedebcca9`. This portability finding is closed;
the blocked local UID probe is still not represented as a successful test. These
synthetic checks do not prove real host commissioning or independent review.

# Guardian/liveness custody namespace fixture finding — corrected review

Independent batch-12 review, 2026-09-27, of published
`d4f960d18080f7051cfeb9139f10602c990e0ae1` against
`102812d48bcd54891a84b4e00aeb112cd0675672`.

The published change moved `os.setgroups([])` before the outer GID map.
That ordering cannot work on Linux, even with namespace capabilities.
A disposable unprivileged probe on alpha-dev confirmed: `unshare` succeeded,
`gid_map` was empty, `setgroups` policy was `allow`, and the syscall returned
`EPERM`. The [Linux user-namespace manual](https://man7.org/linux/man-pages/man7/user_namespaces.7.html)
requires a populated GID map before group-changing calls. The
[upstream newgidmap implementation](https://github.com/shadow-maint/shadow/blob/master/src/newgidmap.c)
preserves permission to call setgroups when an assigned subordinate-GID range
is authorized; mapping does not unconditionally force a permanent deny.

The published commit's [CI run 36294265757](https://github.com/heroo2123/Alpha/actions/runs/36294265757)
subsequently failed all 11 custody cases on both Python versions, before
`mapping_ready`, at `_namespace_child` line 400. Python 3.11 recorded
**11 failed, 4909 passed, four warnings / 606.73 s**; Python 3.12 recorded
**11 failed, 4909 passed, four warnings / 483.82 s**. These are failures,
not successful custody proofs or skips.

Correction: wait for the helpers' mapping handshake, verify namespace-only
root IDs, then clear and verify supplementary groups before entering the
inner namespace. The inner deny-before-map handshake, distinct role IDs,
zero-capability/no-new-privileges checks, network/PID isolation, time/resource
bounds and all production code remain unchanged. A post-mapping permission
failure remains a failed fixture and now includes bounded UID/GID-map,
setgroups-policy and process-credential diagnostics. No skip or host-policy
exception was added.

The predecessor's [CI run 36293719634](https://github.com/heroo2123/Alpha/actions/runs/36293719634)
failed at the later, post-mapping group clear. Its logs lack the kernel-state
diagnostics needed to establish that cause. This review fixes the newly
introduced ordering defect; it does **not** claim the older CI failure is
resolved. Read the new diagnostics on the corrected commit's runner before
choosing a further remedy. Do not weaken custody assertions or change host
policy to obtain a green result.

The earlier claim that GitHub was the first environment to execute the fixture
was also incorrect: `V11_GUARDIAN_CUSTODY_EVIDENCE.md` records **20 passed,
zero skipped** in WSL on 2026-09-25, including four actual custody/restart
scenarios. Preserve that historical evidence for its recorded scope.

Local alpha-dev still lacks `newuidmap`/`newgidmap`; all 11 actual custody
cases remain explicitly unavailable. Five new bootstrap regression cases
failed on the published code. The corrected custody modules passed **21,
with 11 prerequisite skips / 0.22 s**. They cover successful mapped clearing,
permission denial, residual groups, and invalid UID/GID mappings; simulated
bootstrap checks do not establish actual mapped-principal custody.
Final related integration, provenance and remaining requirements are recorded
in the independent batch-12 entry in `V11_WORK_CHECKPOINT.md`. No full local
regression, new C/J/E/A credit or production acceptance is claimed.
