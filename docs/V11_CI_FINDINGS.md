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
