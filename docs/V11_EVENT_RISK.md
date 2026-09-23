# V11 event states and operator reductions

`v11/event_risk.py` implements NORMAL, CAUTION, EVENT and RECOVERY in a separate
nonfinancial evidence namespace. Every transition binds event/account/city/station,
release/model/rules, explicit versioned thresholds, metrics and archived source/book
identities. Unknown health, stale data, websocket loss, source revisions, model
releases, market dislocation, exposure and adverse execution can suppress risk.
Thresholds are supplied policy, not learned from the inspected V10 sample.

Startup and changed policy/model/source scope enter recovery. Recovery needs a
declared number and span of healthy samples with advancing observation timestamps
for every required source/book. Re-receiving old observations or waiting does not
count. Source identity changes reset recovery. CAUTION and RECOVERY retain stricter
size, EV, lifetime, liquidity and revalidation limits. EVENT suppresses passive and
ordinary new risk and records a cancellation request. Source-shock research still
requires exact fresh source, CLOB, scoped certification and stronger economics.

State heads use atomic compare-and-swap, and replaying the same request ID is
idempotent. Revalidation checks current state/operator heads and the earliest
source/metric/guard expiry. This is not atomic account admission; the coordinator
must enforce its own final checks and reservations.

Operator reductions are durable across account, city, station and event scopes.
NO_NEW_ORDERS, REDUCE_ONLY, quarantine, manual review and inventory-operation
disable flags only accumulate. There is no automatic restore method. Cancellation
requests are separate records, never confirmed cancels, inventory exits or P&L.
Actor labels are attribution, not proof of authenticated operator authority.

28 focused tests cover hazards, real evidence advancement, restarts, scope changes,
replay conflicts, safety reductions, source expiry and concurrent state writers.
All are synthetic code checks. Feature-to-metric derivation, production operator
authentication, scoped strategy checks, cancel outbox/reconciliation and full
guardian/coordinator integration remain pending. No service has been deployed.
