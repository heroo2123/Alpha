# V11 universal rule binding

`v11/rules.py` reuses the existing strict compiler and rule authority. It binds
event/market/condition/token identities, title/questions, operative rule text,
station/city, day/timezone, unit/statistic, observation population, precision,
complete bucket partition, primary/fallback source, correction, finality/deadline,
no-data outcome, metadata fingerprint and parser versions.

Canonical immutable preimages carry a semantic SHA-256. Raw response identity is
separate, so an irrelevant volume update does not change settlement semantics.
Before recording a fingerprint, the guard recompiles the exact archived raw
event and checks that it produces the supplied preimage. Unsupported rules stay
unsupported; no broader city/source admission is inferred from a hash.

Material drift appends RULE_DRIFT and requests cancellation of managed new-risk
orders while preserving fill/inventory reconciliation. Returning to earlier
bytes does not automatically clear quarantine. Explicit recertification rereads
the protected capability review and requires a watermark after the changed
evidence. Compare-and-swap appends reject concurrent stale transitions.

`revalidate` checks the current binding, receipt freshness and clock direction.
It is a data gate. Every admission must also pass current scoped certification,
fresh market/execution checks and common risk/authority gates.

Current status: component implemented/tested; full propagation through strategy
proposal, operator confirmation, execution intent and final pre-submit, and
actual guardian cancellation handling are still pending. The cancellation flag
is an audit request, never a claim that an exchange cancelled an order.
