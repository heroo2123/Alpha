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

# Guardian/liveness custody namespace fixture finding

Every GitHub Actions `tests` run since commit `7b5db86f7d` ("Add PAPER
cancel-only guardian broker and durable recovery", 2026-09-25) failed the same
11 parametrized cases in `tests/test_v11_guardian_custody.py` and
`tests/test_v11_liveness_custody.py`, each raising
`guardian_custody_namespace.NamespaceFailure` wrapping
`PermissionError: [Errno 1] Operation not permitted` inside
`_enter_custody_namespace` at its first `os.setgroups([])` call.

This dev host lacks `newuidmap`/`newgidmap`, so `prerequisites()` always
raises `NamespaceUnavailable` here and the same 11 cases skip locally
(`EXTERNAL_CUSTODY_GATE_UNAVAILABLE`); GitHub's runner has real `uidmap`
tooling installed, so it is the first environment that ever executed this
fixture's actual namespace path. Root cause: the outer helper-mapped
namespace tried to clear its inherited groups only after the external
unprivileged `newuidmap`/`newgidmap` helpers had already written its GID map;
per Linux user-namespace semantics that write permanently denies
`setgroups()` for that process as a side effect, so the later clear call
always fails with `EPERM` on any host with real `uidmap` tooling — not a
GitHub-specific restriction.

Fix (`tests/guardian_custody_namespace.py`, batch 12, 2026-09-27): moved the
group-clearing call to immediately after the first `unshare(CLONE_NEWUSER)`,
before the external uidmap helpers run, when the process is still
full-capability in its own fresh namespace and no GID map has been written
yet. No production code changed; no security property loosened. Local
`-k "guardian or liveness or custody"`: 491 passed, 11 skipped (same skip
count as before, all attributable to the missing local `uidmap` package), 0
failed. Confirmation that this resolves the actual GitHub Actions failure
requires the next run on the real runner; record that run's ID/result here
once observed.
