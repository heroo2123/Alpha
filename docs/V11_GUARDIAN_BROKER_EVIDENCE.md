# Local PAPER guardian broker evidence

Date: 2026-09-25. Recovered/fetched parent `5bd4460404ab6d384d44f34d17c6bebcdda6e81d`,
tree `d0e3be325a8787e1a860180204e8abfcc99404a6`, matching the published V11 branch.
No rollback, production-host access, V10 change, service change or financial action.
Private originals remain outside Git; all five SHA-256 identities were rechecked
against their private hash list and repository metadata without emitting contents.

## Implemented and verified locally

The broker owns the PAPER archive, guardian safety engine and exact cancel-only
account transition. The client has only bounded AF_UNIX SNAPSHOT/CHECK/CANCEL verbs.
UID/GID kernel peer credentials, process start/boot identity, endpoint custody and
typed configuration bind both sides. The normal policy requires distinct UIDs.
Kernel overflow UID/GID values are refused even when they match configuration:
an unmapped peer can otherwise appear to have that same identity. Missing or
malformed overflow metadata also fails closed before process lookup.
The preliminary executable tests explicitly select synthetic same-UID mechanics.
The latter proves protocol/lifecycle behavior, not protection from a hostile process
with the same UID or archive write access.

Durable ACCEPTED discovery precedes account effects. Stable account command IDs
and immutable COMPLETED responses survive lost replies, client replacement and
restart. Recovery precedes later safety checks, so recovered health cannot revoke
pending cancellation. Original account snapshot/hash/signatures constrain targets.
Intervening fills and explicit terminal receipts retain their existing semantics.
No attempted cancellation becomes a fill, terminal confirmation or released hold.
CHECK replay cannot refresh a lease. Broker READY requires both live process
identities and is checked by the existing account/basket/maker transaction fences.

Actual Linux process tests cover broker death, stale-socket restart, interrupted
requests, disconnect/retry, stalled/malformed/unsupported requests, stopped peers,
missing broker/client, foreign endpoint files/symlinks/configuration, archive and
lock custody, storage failure, backward clocks and finite sibling client driving.
The client exits on connection failure without a database fallback. Launcher
failures, including oversized numeric input, return redacted nonauthorizing results.

Frames are at most 32 KiB, nesting is bounded, duplicate keys are rejected and
per-connection transport has an absolute timeout. This timeout is not a whole
account-operation deadline: an uncertain delivery is retried by original request
identity. Finite launchers enforce the existing resource limits and a terminal
65-second alarm. CHECK is client-driven; the broker is not an unattended service.

## Exact test evidence

All runs use canonical Git-filtered LF inputs in a fresh native WSL Linux mirror,
Python 3.11.16, bounded tmpfs fixtures and the external verification runner. Raw
fixtures, databases and runner logs remain outside Git.

- Initial broker/legacy integration: **76 passed / 18.59 s**, exit 0, 774 canonical
  inputs unchanged. This predates strict typed result validation and client runner.
- Protocol/restart expansion: **214 passed / 21.71 s**, pytest exit 0, 775 canonical
  inputs unchanged. This predates the finite client and final broker READY guard.
- Broad runtime/account integration: **512 passed / 75.25 s**, exit 0, session
  **78271**, all **776 canonical non-document inputs unchanged** in source/mirror.
  Input-map digest: `c5f342c2c4f3a9963c456d3800e1b9bfafb53fb3adbe859845e3bbe24e5baa81`.
  Suites: guardian protocol/broker/direct/integration, evidence foundation, account,
  basket, maker, cancellation, runtime health/maker/PAPER, candidate assembly/runner,
  lifecycle and reconciliation. This precedes the final overflow-identity refusal;
  all other runtime code is unchanged in the final run below.
- Final identity/custody/guardian/cancellation/health checks: **305 passed, 1
  skipped / 26.83 s**, exit 0, session **88990**. All **778 canonical non-document
  inputs unchanged** in source/mirror. Input-map digest:
  `404f752596a53bbc13f5769d81323e022dae07f8b7b343986154042315681233`.
  Includes the final overflow guard, 16 pure custody-harness validation cases and
  the one explicitly unavailable mapped-principal integration gate. Exact skip:
  `EXTERNAL_CUSTODY_GATE_UNAVAILABLE: owner prerequisite: install the standard uidmap package (missing newuidmap)`.

The earlier full **4294 passed / four existing warnings / 600.37 s** belongs to
published parent `5bd4460`. It is retained as historical evidence, not attributed
to this patch or rerun merely to increase the number.

## Separate-custody gate remains open

`tests/guardian_custody_namespace.py` prepares a disposable unprivileged Linux
user/mount/PID/network namespace harness. A tmpfs owns synthetic fixture state;
read-only bind views avoid changing host home/repository permissions. Three mapped
roles have distinct real/effective/saved UID/GIDs, no supplementary groups, no
capabilities and no-new-privileges. Namespace setup privilege never becomes host
root, a persistent account or a service. Finite bounds and PID-namespace teardown
limit fixture descendants. The collected custody test exercises the actual broker,
authorized guardian cancellation/replay, rejected candidate peer, denied archive/
WAL/SHM/config access, endpoint replacement and cross-principal SIGTERM.

**Actual mapped-principal execution is not verified yet.** Local Ubuntu has the
assigned subordinate UID/GID ranges but lacks `newuidmap` and `newgidmap`. Missing
prerequisites produce an explicit unavailable/skip, never passing custody evidence.
The sole current owner prerequisite is local WSL Ubuntu:

```sh
sudo apt-get install uidmap
```

After that owner action, run the collected mapped-principal broker test under the
same canonical runner, resolve any namespace/runtime failure, and extend actual
distinct-principal restart/failure acceptance. No alpha-dev access is requested.
The helper is prepared test infrastructure, not a commissioned isolation control.
No project/test/guardian process remains running after verification.

## Credit and remaining acceptance

This strengthens existing R37 C/J; no new milestone is earned. Fixed denominator:
**85/200 = 42.5%, approximately 43%; formal 1/50 (2%)**. R37 E/A and R43/R44 remain
open. Shared SQLite/storage, coherent healthy publication, supported real cancel
authentication/entitlement, protected operating custody, deployment and independent
unfunded acceptance are still required. **NOT_READY_TO_FUND. V10 DEFERRED.**
