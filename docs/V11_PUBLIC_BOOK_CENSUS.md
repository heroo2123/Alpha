# Public books and fresh census recovery

`v11/book_inputs.py` binds the public CLOB GET `/book?token_id=...` response to
the exact event, condition, token and contract side in the current rule. It
validates server milliseconds, depth, duplicate prices, tick alignment, minimum
size and crossing; it sorts both sides instead of assuming provider array order.
Empty depth is archived as absent liquidity. It cannot become an executable price.
The response schema is documented in the venue's
[prices and order books reference](https://docs.polymarket.com/market-data/prices-order-books).

The raw receipt, server book timestamp and normalization availability remain
separate. Replaying normalization returns the same immutable row. A different
interpretation cannot turn an already superseded raw response into fresh evidence.
The venue hash is opaque: it is never treated as sequence continuity, a fill or
proof of execution. REST books support point features; temporal microstructure
remains UNKNOWN without a separately validated continuous sequence. Fees and
network latency remain unknown. The server timestamp can describe an old book
event; policy deliberately gates it when too old, even after a recent HTTP receipt.

Weather normalization likewise keeps its original receipt and exposes the latest
accepted provider reading timestamp for source-health consumers. That timestamp
does not certify the full observation population, settlement authority, finality,
next-observation prediction or calibration labels. METAR remains a proxy.

`v11/census_worker.py` performs at most one event census per call. It uses the
existing anonymous GET collector, durable provider cooldowns and a separate worker
lock. Up to 63 requests are split into batches of at most 16, within both the
configured monotonic budget and event claim deadline. Further CLOB batches respect
the persisted interval; a rate limit or long recovery delay is not bypassed.
Successful captures survive another provider's failure. Interrupted cycles are
not repeated under the same command identity; subsequent distinct cycles retain
cooldowns and recover abandoned claims through a fresh census.

The built-in plan covers exact full books and the existing AWC official proxy.
Required MODEL census sources without a reviewed run-bound adapter stay GATED
before any request. No issue time, model probability or QC certificate is invented.
Current rule evidence is mandatory. This limitation remains an open source/runtime
integration requirement, rather than being hidden by a cached-data fallback.

EventQueue requires every supplied book/source and its declared raw response to
postdate the claim. A monotonic per-event loss generation prevents a later gap,
overflow or expired update from being cleared by a census begun before that loss.
Loss on another event does not invalidate healthy coverage. Queue/source CAS guards
remain in force. Loss between coverage and completion leaves the worker GATED.

Coverage requests ordinary reevaluation using an existing receipt. It is not an
economic proposal and cannot satisfy common-account admission on its own. The
paper scheduler drops an obsolete census retry delay once fresh coverage resolves
the dependency. Cancellation is serviced independently while census awaits HTTP;
ambiguous reservations stay reserved. This is local paper integration, not the
independently commissioned live guardian or a deployed service.

Synthetic integration covers HTTP-shaped book/proxy responses -> normalization ->
fresh census -> durable queue -> protected temperature model pipeline -> conservative
valuation. The uncalibrated proposal is rejected, with no intent or fill created.
Separate mechanics fixtures exercise cancellation while the collector waits,
partial success, cooldown, interruption, batching and repeated-gap races.

Initial book/census tests: 40 passed in 2.75 s. The wider run found one integration
failure (227 passed / 1 failed): weather normalization omitted its available actual
observation timestamp. After correction, 228 related checks passed in 10.42 s.
A later completion-race guard and regression passed with the census/pipeline group:
14 passed in 3.40 s. The first attempted wider command named a nonexistent test file
and exited 4 without running tests; the corrected commands above are authoritative.
Full repository regression is recorded in the work checkpoint after completion.
All results are off-host mock/synthetic tests, not forward or empirical evidence.

V10 maintenance remains deferred. Its stale successful cycles and memory pressure
leave resource/isolation gates open. No additional host workload, service action,
executor change, deployment or financial authority is introduced here.

The later required-PWS extension is documented in V11_PWS_OBSERVATIONS.md. It
collects a fresh bounded MADIS response through shared cooldowns, joins prior
causal history for QC, checks current metadata and rejects a newly processed
pre-claim response as gap-recovery proof. Source/clock/strategy eligibility and
unknown-run forecast gates remain independent of collection success.
