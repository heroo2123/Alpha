# V11 bounded public collection

`v11/collection.py` provides reviewed anonymous GET endpoints, response/time/
attempt bounds, independent per-source commits, no redirects and no implicit
compressed-body expansion. Public responses cannot cause a session cookie to be
forwarded. A 429 stops further requests to that host within the cycle; a parsed
Retry-After is durable source-health evidence. Later providers can still succeed.

`v11/observation_runtime.py` adds a namespace-local single-writer lock, durable
pre-request scheduling reservations, restart-safe cooldown/backoff and completion
records. A failed cycle cannot roll back earlier source captures. A crash retains
the reservation; later bounded recovery does not pretend the interrupted response
was received. Documented source update cadence informs conservative polling.
Transport success is explicitly distinct from successful normalization.

`ObservationRuntime.cycle` normalizes supported weather sources, preserves raw
evidence, writes separate normalization failures, records per-event/strategy
source-ready funnels and appends cycle status. It has no execution interface.
No observation/source success certifies calibration or allows paper positions.
Source records include sensor time, local receipt and feature availability;
unavailable publication/provider-receipt times remain unknown.

Supported normalizers: public NOAA MADIS CWOP temperature XML and NOAA AWC METAR
JSON. Neither grants exact contract settlement-population or label authority.
Official WRH/finality, forecasts, book normalization, event-trigger integration
and complete paper strategy runtime remain pending. Source policy defaults are
engineering bounds, not learned strategy thresholds.

Tests cover deadline exhaustion, provider failures, rate limits across restart,
partial success, cookies, malformed responses, XML entities, geographic/time
bounds, source identity, stale/future observations, units and authority flags.
