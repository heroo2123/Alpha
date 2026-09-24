# V11 paper cancellation delivery and telemetry

Implemented off-host in v11/paper_cancellation.py, joined to the existing common
paper account. This is a bounded local cancellation integration, not a deployed
guardian, live cancellation transport or first-canary commissioning pass.

## Trigger and identity boundaries

PaperCancellation.plan accepts existing operator cancellation/quarantine records
or an archived EVENT cancellation record. Superseded EVENT requests remain
deliverable only for intents whose valuation predates that trigger; newer intents
are excluded from the older request. It freezes only already-managed intents
in the exact account/city/station/event scope. EVENT targets passive maker or BUY
new-risk intents; ordinary inventory-reducing SELL intents are retained. An
explicit operator cancel-all can include those sells. Other safety actions do not
silently turn into cancel-all commands. Actor strings remain attribution, not
proof of protected operator authentication.

Plans pin account policy, namespace, trigger and existing intent economic identity.
They are bounded at 256 targets; oversized plans fail visibly instead of silently
omitting orders. Plan publication guards the account and trigger heads atomically.
A plan is a snapshot of its selected existing intents; new orders need a new
scope scan. CANCEL_AND_HALT's existing opening gate remains effective separately.
Nothing clears safety flags, hard limits or reconciliation faults.

## Local delivery and reconciliation

Each cycle issues at most 16 local CANCEL_REQUESTED transitions (or the smaller
configured bound). The account transition optionally verifies an exact identity
digest inside its own transaction, preventing an ID repurposed with different
economics from being cancelled through an old plan. The optional pin cannot be
used for SUBMITTING or any opening transition. Existing callers are unchanged.

Stable per-plan/intent command IDs survive interruption between account mutation
and telemetry publication. Resume discovers the prior local request rather than
delivering it twice. Atomic account/plan guards reject stale telemetry. Local CAS
failures remain visible and may be retried on a later bounded cycle; identity
conflicts require reconciliation. This is not a claim about exchange retry safety.

Cancellation requests retain account reservations. Wall-clock expiry, public
prints and even an archived but unapplied synthetic terminal receipt cannot
confirm cancellation. Only the common account's already reconciled terminal state
can close the paper target. FILLED, CANCELED, EXPIRED and REJECTED remain distinct.
Late fills/acks preserve the cancel request, actual synthetic inventory and cash.
A fill after terminal reconciliation revokes clean cancellation reporting and
keeps the account's sticky fault and any remaining exposure.

## Telemetry and acceptance limits

Reports bind source plan, durable local request and observed account head. They
record first local-request time, first observed terminal time, cumulative filled
units, remaining reservation status and local errors. The elapsed interval is
local paper observation latency; exchange cancellation latency is UNKNOWN.
Real cancellation confirmation, real orders, replacement orders and financial
authority remain false. Cancelling resting exposure never claims an inventory exit.
Explicit observations and historical replay do not issue new cancellation requests.

29 new cancellation tests / 223 related account/event/evidence/maker/basket/exit
tests passed in 36.50 s. Initial cancellation/account checks passed 43 tests in
3.87 s; additional negative cases cover bounded retries, terminal regression and
plan publication races. All fixtures are synthetic, including downstream account
economic-admission stubs; no empirical eligibility or independent review is claimed.

R34, R37 and R39 remain PARTIAL. This module has no network/credential/order-opening
calls but shares the paper coordinator process and is not an OS-isolated security
boundary. Production identity, supported cancel-only route, authentication after
session expiry/revocation, network outage recovery, GTD limits, trustworthy clock
handling, external reconciliation and guardian liveness remain uncommissioned.
The restricted safety-audit path now permits exact cancel-only mutations during
raw wall-clock regression. Bounded runtime scheduling and source/heartbeat/clock
monitoring are integrated off-host; this remains a shared-process paper boundary.
Independent external cancellation availability and live telemetry remain pending. No V10 workload, deployment, executor change, real cancellation, order,
funding or activation occurred.
