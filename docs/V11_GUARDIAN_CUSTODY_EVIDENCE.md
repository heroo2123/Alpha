# Separate-principal PAPER custody and restart evidence

Verified locally in WSL2 Ubuntu on 2026-09-25, from published
`7b5db86f7d0d66a26318478581819005862bb10a` (tree
`8097c2ed408e79bfed48f65a473eb0791e991d62`). Owner installation of the standard
`uidmap` package supplied the previously missing helpers. No additional owner
action, host-root shell, production access, permanent account or service change.

## Environment repair without reducing controls

The first actual run found EPERM when the prepared harness tried to write
`setgroups=deny` after populating `gid_map`. Linux forbids that ordering. The
helper-authorized outer namespace now clears inherited supplementary groups;
a nested namespace denies setgroups before its parent writes the inner maps.
The harness proves even namespace-root cannot subsequently call `setgroups([])`.
All broker/guardian/candidate roles have separate real/effective/saved UID/GIDs,
empty supplementary groups, zero inheritable/permitted/effective/bounding/ambient
capabilities and `no_new_privs=1`, checked before and after exec. The caller's
sudo/docker group membership never reaches these roles.

A second fixture failure exercised the unchanged archive free-space guard: the
original 64 MiB tmpfs could not retain the required 64 MiB safety headroom plus
records. The disposable fixture capacity is now bounded at 128 MiB. Archive
limits, free-space checks and all production safety controls remain unchanged.
Namespace setup and child failures now include bounded stage diagnostics.

## Actual acceptance exercised

Four independently created user/mount/PID/network namespace scenarios run the
actual PAPER broker with distinct principals, a broker-owned synthetic database,
readonly code/runtime views and a broker-owned socket directory:

- Guardian and candidate cannot read/write the ledger, WAL, SHM or private config,
  unlink/rename the endpoint, or SIGTERM the broker.
- The candidate peer is rejected before receiving protocol data. Even the
  authenticated guardian's NEW_ORDER verb is rejected. Authorized snapshot and
  pinned cancellation succeed; duplicate delivery returns the original receipt.
- Test-only abrupt broker exits occur immediately after durable acceptance,
  after the account mutation, and after the final receipt. No fault hooks were
  added to production code. Stale endpoints are replaced on restart; a new
  guardian PID retrieves the same request/receipt under the same principal policy.
- Pending cancellation is recovered before the replacement broker publishes its
  listening socket and before the client retries. Exactly one account cancellation
  command remains. The entire account state equals its prior state except the
  exact cancel-request fields; holds remain and no terminal confirmation is inferred.
- Each fixture uses a different network namespace from the caller. PID-namespace
  teardown and bounded process/output/time limits contain disposable descendants.

The account uses the explicitly synthetic downstream oracle already used by
coordinator mechanics tests. This is actual Linux custody and crash evidence for
that local PAPER slice, not real source/profitability evidence, venue cancellation,
production deployment or independent operational commissioning. The previous
same-UID protocol/resource/lease tests remain historical evidence for their scope.

## Verification

Final canonical WSL runner: **20 passed / 9.84 s / exit 0**, session **80805**, no
skips. Includes four actual custody/restart scenarios and sixteen pure harness
validation cases. All **778 non-document inputs unchanged** in source and native
Linux mirror. Input-map SHA-256:
`f08075120907294d0af1707c3785ceaa72fc908be2b2ec2fbe77c611aba8ffa6`.
Only harness/tests changed; the broker and account/runtime production code are
identical to the preceding published milestone. Its 512 integrated and final
305/one-unavailable checks are recorded in `V11_GUARDIAN_BROKER_EVIDENCE.md` and
were not repeated solely for a larger count. Raw temporary databases/probe output
remain outside Git. No test or role process remains running.

## Remaining requirements and credit

The local separate-principal custody/restart gate is now verified. No current
owner-only blocker remains. Next independent action: coherent heartbeat/health
publication and consistent bounded guardian/admission reads, retaining exact head
CAS, original timestamps, pending cancellation and all health gates. The existing
split publication can otherwise cause conservative false cancellation during a
healthy update. A separately bounded producer channel into protected broker state,
supported venue authentication, cgroup/host deployment, forward operational evidence
and independent commissioning remain open.

R37 C/J is strengthened; E requires all required operational/deployment evidence,
and A requires the full package. No new fixed milestone or formal requirement is
earned: **85/200 = 42.5%, approximately 43%; completed requirements 1/50 (2%)**.
**NOT_READY_TO_FUND. V10 unchanged/maintenance DEFERRED.**
