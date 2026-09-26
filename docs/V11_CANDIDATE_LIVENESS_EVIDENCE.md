# Authenticated PAPER candidate liveness

Continued locally after published `3e80339818ddc5b67b4485c28b9fda54c4f391e8`,
tree `7237035f2142c9335c93694b14a4d90ec42610db`. The original private specification
remains authoritative. All new observations and accounts used for verification
are explicit synthetic fixtures. No alpha-dev, V10, service, credential, wallet
or financial action is part of this work.

## Authority and provenance

The candidate uses a separate Linux Unix packet socket. It sends only a PULSE
request identifier and the broker's fresh challenge under the pinned configuration.
It cannot supply timestamps, health, READY, source or account state, policy changes,
orders or financial operations. The guardian's original stream protocol remains
SNAPSHOT/CHECK/CANCEL only. Broker-owned endpoint metadata and parent-directory
custody are checked; both peers are checked against kernel process credentials.
Every producer packet additionally requires exactly one matching SCM_CREDENTIALS
record, preventing another process from renewing the pinned worker through an
inherited or transferred connected socket. Received descriptors are closed even
on rejection or ancillary truncation.

The trusted broker pins the worker's PID/start time/UID/boot, GID, generation and
exact health configuration. It records the original challenge and receipt clocks.
An accepted receipt is durable before publication; the paired heartbeat and sample
reference its immutable identity and hash. Heartbeat age starts at receipt, not
at completion of a slow probe. Missing, malformed, ambiguous, stale or changed
identity/configuration fails closed. Challenge clock discontinuities are refused
before acceptance under the existing health policy.

Completed retries return the original receipt without probing or renewing health.
After interruption, recovery can only return an already complete original pair
or durably refuse the old observation. It never reruns an old pulse. Half-pairs
and conflicting evidence remain errors. Fresh new observations can proceed after
that refusal; old timestamps and stable-sample counts are preserved.

## Cancellation and resource bounds

The broker dispatches at most one publication child. The event loop does not wait
for a producer database lock or clock probe. A valid authenticated guardian request
kills and reaps the publication child before the existing safety handler runs.
The private process group covers its clock-probe subprocess; Linux parent-death
guards also terminate the chain if the broker dies. Children close inherited
listeners, connections, lock descriptors and standard streams. The fixed launcher
uses a sanitized environment, closed descriptors and a bounded input schema.

Guardian and producer endpoints have separate connection budgets and reserved
slots. Packet size, challenge age, response deadline, publication duration, rate,
durable observation count and total run duration are bounded. Invalid producer
traffic cannot supply guardian operations or consume its reserved connection
budget. Existing account, basket, maker, source, reconciliation and admission
checks remain in force. Cancellation still requests cancellation only; it does
not invent venue confirmation or release unresolved reservations.

## Verification

Targeted new-module suite (`tests/test_v11_candidate_liveness.py`,
`tests/test_v11_liveness_broker.py`, `tests/test_v11_liveness_custody.py`,
`tests/test_v11_liveness_health.py`, `tests/test_v11_liveness_protocol.py`):
**202 passed, 7 skipped**, exit 0. Skips are the same local `uidmap`
distinct-principal prerequisite already noted for R37 custody proof, not a new
gap. Affected guardian/health/evidence integration (`pytest tests/ -k
"guardian or health or evidence or liveness"`): **782 passed, 11 skipped**,
exit 0, plus one pre-existing failure
(`tests/test_v11_guardian_broker.py::test_actual_broker_death_stale_socket_restart_and_receipt_replay`)
reproduced identically against the unmodified published
`3e80339818ddc5b67b4485c28b9fda54c4f391e8` tree, so it is unrelated to this
work: real-subprocess broker-restart timing already fails on this host and is
not attributed to or fixed by this milestone. No source/test hash outside the
files listed at the top of this document changed.

## Remaining boundary

This endpoint proves local protected liveness publication. It is not full custody
of the current candidate's source ingestion, execution, model control or account
work: the existing shared-store candidate composition still needs an appropriate
trusted execution boundary. No background helper is allowed to manufacture a
healthy candidate while that candidate is blocked. Shared-disk failure remains
a common failure and cannot be solved by priority scheduling alone.

Supported real cancel authentication, protected routing, independent operating
acceptance and production commissioning remain open. WSL tests are local evidence,
not production-host acceptance. **NOT_READY_TO_FUND**; V10 maintenance DEFERRED.
Existing R37/R38 C/J are strengthened, with no additional E/A or formal completion:
**85/200 = 42.5%, approximately 43%; fully completed requirements 1/50 (2%)**.
