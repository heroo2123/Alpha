# V11 bounded source-to-event work

`v11/event_queue.py` routes archived books/trades, official observations, QC PWS,
model arrivals and explicit scheduled-release notices to registered events.
Books require the exact event/token; model updates require station/date/family.
PWS uses station-specific age policies and actual sensor ages. Raw PWS cannot
masquerade as a QC neighborhood. Scheduled plans retain their original receipt
and due times; reaching a release window is not an observed official arrival.

The immutable configuration bounds registered events, station fan-out, pending
events, source fan-in, source channels, state bytes, pending age and work duration.
The queue retains the newest received revision for each channel, coalesces work
per event, counts duplicate/older receipts and preserves a durable reason for
overflow, stale data and lost coverage. A later correction with an older sensor
timestamp remains a new received revision. Frequent updates do not extend an old
pending event indefinitely. Configuration changes cannot silently discard state.

SQLite compare-and-swap protects concurrent producers. One nonblocking OS lock
spans worker evaluation; this deliberately serializes more strictly than one
worker per event. A process exit releases the lock, while the abandoned durable
claim remains visible and requires a census. A timestamp or elapsed lease alone
never proves the previous worker stopped. Completed outputs must have both a
causal timestamp and a sequence after the claim or latest census.

New queued updates retain follow-up work and invalidate the current result.
Capture heads are also guarded atomically when claiming and completing work, so
an archived arrival cannot hide in the gap before its queue notification.
Source expiry and exceeded work budgets leave results noncurrent. Actual
submission-ready latency remains unknown: the queue records receipt/routing/work
latency and has no order API.

Stream gaps and lost coverage require a serialized census. Clearing this flag
requires new full-book snapshots for every registered token, fresh required
source kinds, a current nonquarantined rule, and atomic guards against racing
arrivals or another gap. Evaluation must then run after that census. This proves
archived input coverage only; transport resynchronization correctness, source
truth, scoped strategy admission and financial/exchange reconciliation remain
separate requirements. The worker deadline rejects late results; OS resource
isolation must still bound callback CPU/RAM/time before deployment.

32 queue tests and one joined forecast-pipeline test cover affected-event routing,
dedupe/revisions, TTL, fan-out/fan-in/byte bounds, scheduling, competing workers,
crash recovery, late outputs, incomplete resync and concurrent commits. The joined
test evaluates a real synthetic forecast through the protected-reader fixtures;
uncalibrated economics reject it and no fill is created.

Websocket protocol decoding, automatic periodic-census scheduling, protected
route reconfiguration, runtime supervision and propagation of queue faults into
the final coordinator/guardian still require integration. No service or workload
was added to alpha-dev. This module does not close the V10 resource/health gate.
