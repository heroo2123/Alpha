# Coherent PAPER health publication and guardian acceptance

Implemented locally after published custody milestone
`4fae0b89b3fef191a7e5f5a201ac7861e88a9c7f`, tree
`33c0afa5df02b75b19f0354eef894d7fde275696`. Synthetic PAPER fixtures only;
no alpha-dev, V10, service, credential, wallet or financial action.

## Publication and observation contract

`RuntimeHealth.publish` commits a heartbeat and its bound health sample in one
SQLite write transaction. The private borrowed store permits exactly those two
nonfinancial safety record shapes, retains existing append validation/CAS and
resource budgets, and accounts for cumulative bytes. A failed second append,
caught append failure or process death before commit leaves neither record.
External synchronization probing happens before acquiring the writer lock.
Replay returns the original complete pair without probing, changing timestamps
or counting another recovery sample. Half-pairs, legacy pairs and changed keys,
generation or configuration are refused rather than completed retrospectively.

The candidate runtime uses this paired path when it owns a configured worker.
Standalone sampling and legacy heartbeat APIs remain available; an unpaired newer
heartbeat still invalidates the earlier sample. Missing other workers are never
invented or renewed. All raw wall/monotonic, boot, spaced recovery, source and
account gates remain in force.

Health readers obtain the sample, declared bounded worker heads and archive high
water from one SQLite read snapshot. Clock observation follows that snapshot (and
the sync probe), preventing ordinary concurrent appends from appearing to be a
backward clock. A real backward clock remains unhealthy with raw times preserved.
Admission retains exact original health/worker CAS heads and existing source gates.

The guardian fences a new trigger or READY decision with its observed health and
worker heads. A changed publication causes a bounded reread, not a cancellation
based on a discarded observation. At most two attempts inspect at most twice the
configured intent count; repeated contention remains GATED. Already durable
cancellation is resumed before new health checks and cannot be revoked by recovery.
READY rechecks pinned sample freshness and actual worker/guardian/broker process
identity inside the write transaction. Expiry or stopped/dead workers cannot gain
a lease through time spent waiting for the writer lock.

Malformed health is a cancellation failure, not an uncaught shape exception.
The guardian verifies the original policy/account/scopes/source-requirement config
digest, finite timestamps and heartbeat identity, and independently enforces policy
sample age. Altered finite expiry values cannot extend that age. Missing/malformed
source admission data stays under the same failure/CAS path. All cancellation
checks preserve account economics, reservations and explicit terminal reconciliation.

## Verification

Final targeted WSL run: **184 passed / 30.75 s / exit 0**, session **35878**, no skips.
All **781 canonical non-document source/mirror inputs unchanged**. Input-map SHA-256:
`5a1f385a157c31eacf6e215a84994fb565193f11344a64be2ea25b87dada6ca6`.
The earlier **173 / 30.70 s** run precedes the final malformed-data/age hardening
and is not the final artifact's result. Canonical Linux mirror preserves Git input
bytes and executable modes; bounded runner output/databases remain outside Git.

New checks include transaction rollback, actual forked-child death between inserts,
reader visibility between writes, second-connection commit between read queries,
immutable replay, half-pair refusal, guarded READY/trigger races, durable cancellation
after restart, expiry/worker-stop at the writer lock, malformed data/config and
extended finite deadlines. An actual sibling repeatedly publishes healthy pairs
while the guardian checks a retained synthetic intent; no false cancel occurs.
Related suites cover runtime health, independent guardian, broker, account/basket/
maker integration and runtime scheduling. Strategy economics in the shared fixture
use an explicit synthetic oracle; no empirical viability is inferred.

Full regression: **4549 passed / four existing FastAPI `on_event` deprecation
warnings / 623.00 s / exit 0**, session **81263**, no skips. Same 781 canonical
source/mirror inputs unchanged and same input-map digest. The changed shared
append/transaction integration justifies this full run; it includes the actual
distinct-principal custody/restart tests as well as all existing integrated gates.
No source change occurred after the final targeted run. This is local regression,
not a claim of independent full-package acceptance or operating deployment.

## Remaining boundary

This is coherent local shared-store integration. It does not give a custody-denied
candidate a route into the broker's private ledger. A separately authenticated,
bounded producer path and appropriate trusted computation remain unfinished.
The guardian socket remains SNAPSHOT/CHECK/CANCEL only. Supported real cancel
authentication, protected routing, deployment and independent operational acceptance
remain open. **NOT_READY_TO_FUND**; V10 maintenance remains DEFERRED.

Existing R37/R38 C/J credit is strengthened; no new E/A or formal requirement
completion: **85/200 = 42.5%, approximately 43%; 1/50 (2%)**.
