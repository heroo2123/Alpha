# Archived forecast normalization and remaining source gates

`v11/forecast_sources.py` reuses the existing strict 31-member GEFS daily parser
for authorized archived responses. It verifies the exact endpoint/request,
model, station coordinates, metadata/rule fingerprint, target day, units,
resolved-grid distance and causal raw receipt. Members remain continuous values;
the adapter does not turn vote counts into calibrated contract probabilities.
Raw response hashes and original receipt times survive normalization and replay.

The current seamless endpoint has no verified binding from each returned value
to an exact model initialization. Normalization therefore leaves `issued_at`
and publication time unknown, even if a raw envelope claims a recent issue time.
It does not substitute receipt time, response generation duration or a separate
metadata timestamp. The actual inference/health model-age gate rejects this
output as an eligible forecast input. These members remain research evidence.

The optional `v11/forecast_runtime.py` job processes one configured archive
channel per step, with immutable plan identity, raw/current-source CAS, bounded
input size, clock checks, an exclusive lock and recovery after publication.
The typed candidate schedules it with existing safety work. It adds no HTTP
endpoint, credential access or collection authority. Unverified derived inputs
stay out of event triggers; their own required model-health scope remains gated
without manufacturing a census failure for an unrelated lane.

Primary-source review on September 24, 2026:

- [Model updates](https://open-meteo.com/en/docs/model-updates) distinguishes
  initialization, conversion completion and API availability, and describes
  eventual consistency across servers. A metadata response alone does not bind
  the returned seamless values to a particular run.
- [Ensemble API](https://open-meteo.com/en/docs/ensemble-api) documents the
  member forecast endpoint. [Single runs](https://open-meteo.com/en/docs/single-runs-api)
  is a distinct interface; its availability must not be assumed to establish
  exact-run GEFS ensemble support for this adapter.
- [Terms](https://open-meteo.com/en/terms) limit the free API to noncommercial
  use. Intended-use entitlement for this project is not verified. No new
  Open-Meteo data request, subscription or account was created.
- [NOAA GEFS inventory](https://www.nco.ncep.noaa.gov/pmb/products/gens/) identifies
  run/member forecast products. A directly run-bound alternative still needs a
  bounded decoder, original availability evidence and full-day/grid coverage
  before it can replace the current source gate.

Verification includes strict member/unit/grid/request rejection, backfill and
stale-input exclusion, source races, immutable replay, interrupted publication,
unknown-run inference/health gating and candidate scheduling without new network
requests or paper fills. First related run: 44 passed in 1.16 s; wider source,
candidate, feed, health and strategy integration: 145 passed in 20.51 s. The
final retained-normalization policy check is covered by the subsequent focused
and full regression results in the work checkpoint.

Still open: permitted actual source collection, exact run/member provenance,
same-day unresolved-path/observation population coverage, protected calibrated
artifacts, independent acceptance and deployment. The forecast census remains
gated until its required adapter has verifiable run age; this implementation
does not declare that missing capability complete.
