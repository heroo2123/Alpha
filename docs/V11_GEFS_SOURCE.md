# Bounded run-bound NOAA GEFS source

`grib_fields.py`, `gefs_sources.py` and `gefs_runtime.py` connect a narrowly
allowlisted anonymous NOMADS GET to the existing archive, finite candidate,
source-health/admission fences and immutable probability bundle interface.
No account, service, order, funding or model-promotion interface is added.

The decoder accepts exactly one operational NCEP GEFS field: GRIB edition 2,
product template 4.1, generating process 107, point temperature at two metres,
one control or perturbation member, and an explicit six-hour initialization.
It checks that the run/member/forecast hour inside the bytes match the request.
Initialization remains distinct from unknown publication time and original Alpha
receipt. Supported data packing is simple (5.0) or IEEE (5.4); other formats,
bitmaps, missing data, extra fields and unsupported grids are explicit gates.
No native dependency was installed or general-purpose decoder parity claimed.

Each response is capped at 64 KiB and at 25 grid points before value allocation.
The request is limited to a half-degree station tile; the response and selected
nearest point must match that tile and the configured distance bound. Grid values
stay continuous and retain their Kelvin origin. Raw bytes and SHA-256 are archived
before parsing, with source/member identities and causal receipts.

A complete input requires all 31 members on the same grid and every three-hour
point bracketing the entire contract local day. DST days use their actual length.
At most 341 fields are accepted. The model computes a **piecewise-linear forecast
path**, interpolates the two local-day boundaries and derives its high or low in
the contract unit. It cannot observe intrastep extrema. That approximation and
its uncalibrated uncertainty are explicit, and its distinct model identity
`NOAA_GEFS_0P50_LINEAR_DAY_V1` cannot silently inherit another model's parameters.
This is a forecast hypothesis, not an exact station observation or payout label.
Current conservative probability bounds remain vacuous.

The worker collects at most one file per step using the shared durable scheduler.
Successful requests have a minimum one-second spacing; failed requests wait at
least a minute, with longer backoff and Retry-After retained. No immediate
transport retry is made for NOMADS. Partial fields survive interruption. A
reserved GET without a receipt is reconciled as interrupted, never blindly
duplicated; a committed field or complete run replays without new HTTP/receipt
time. Current constituent changes invalidate model health/admission. The original
64-head account bound is retained through one aggregate MODEL compare-and-swap
guard, rather than silently truncating constituent evidence. Assembly has a
two-second cooperative budget in the worker; this is not an isolated guardian.

Plans retain an explicit initial run. Optional `GEFSRunPolicy` now advances the
requested run on the six-hour grid after a configured 1–6 hour request lag,
capped at the last run that can cover the entire local day. This lag is a request
schedule, not proof of provider publication. Matching bytes still prove identity;
missing files retain the ordinary shared cooldown and source gate. The policy is
bound into candidate/worker recovery identity and cannot silently replace it.
No policy means the original fixed-run behavior and worker identity remain.

Rollover archives a separate transition and retains the previous state and all
partial fields. Run-specific identities prevent cross-run mixing. An interrupted
old-run operation is reconciled before selecting another run. The existing
completed model is not overwritten by a request or a missing response; its own
freshness/admission checks still apply. Ended target days and stale plans gate.
Eighteen new schedule/candidate cases join the related suite: **112 passed in
38.26 s**. An initial run found one recovered-timestamp validation defect and one
fixture incorrectly expecting collection during the separate rollover step;
both were corrected. These checks are synthetic, not actual source acceptance.

A bounded multi-step MODEL census protocol remains unfinished. A complete model
does not clear an unrelated stream
gap or approve any strategy. The existing census still refuses a required MODEL
adapter until its complete fresh-input protocol is implemented.

Verification: 68 new synthetic cases, including byte decoding, bad dimensions,
unsupported packing, exact request identity, complete member/day coverage, DST,
source races, replay, interrupted GET/field recovery and finite candidate safety.
The full archived-run worker feeds model health; a changed constituent revokes it.
The source also feeds an immutable data-only probability bundle without fitting,
calibration or activation. Initial checks: 61 passed in 4.88 s; expanded checks:
31 passed and one fixture lookup failure, then the corrected candidate case
passed in 1.75 s. Final related suite: **242 passed in 46.97 s**. An earlier wider
command named a nonexistent test file and exited 4 before running tests; the
corrected command produced the recorded pass. Full regression is due for the
shared collector/candidate/health changes, not yet claimed for this milestone.

One authorized bounded off-host public probe failed **before HTTP**: the pinned
HTTP client rejects this environment's `socks5h` proxy scheme. No proxy or access
restriction was bypassed, and no actual NOAA field was received. Private result:
`v11-private-evidence/gefs-access-probe-20260924-01.json`, SHA-256
`2e9eb774c838f790f03a481bb469253ef715214fe9911584bda425d86d17fc1f`.
The configured package-index lookup also returned no ecCodes distribution; no
dependency change followed. These are environment findings, not claims that the
public upstream package/data is unavailable. Actual provider bytes, packing
parity, availability/latency, full-run coverage and empirical model quality
remain unverified. No E/A acceptance milestone is earned by synthetic tests.

Primary references reviewed September 24, 2026:

- [NOAA subset scripting](https://www.cpc.ncep.noaa.gov/products/tools/scripting_grib_filter.html)
  documents variable/level/region filtering and automated requests.
- [NWS data use and appropriate usage](https://www.weather.gov/disclaimer)
  supplies data-use conditions, refresh-aware collection and failure spacing.
- [GEFS filter](https://nomads.ncep.noaa.gov/gribfilter.php?ds=gefs_atmos_0p50a)
  and [operational inventory](https://www.nco.ncep.noaa.gov/pmb/products/gens/gec00.t00z.pgrb2a.0p50.f003.shtml)
  distinguish point TMP from interval maximum/minimum products.
- NCEP format definitions: [identification](https://www.nco.ncep.noaa.gov/pmb/docs/grib2/grib2_doc/grib2_sect1.shtml),
  [grid 3.0](https://www.nco.ncep.noaa.gov/pmb/docs/grib2/grib2_doc/grib2_temp3-0.shtml),
  [product 4.1](https://www.nco.ncep.noaa.gov/pmb/docs/grib2/grib2_doc/grib2_temp4-1.shtml),
  [simple packing](https://www.nco.ncep.noaa.gov/pmb/docs/grib2/grib2_doc/grib2_temp5-0.shtml),
  [IEEE packing](https://www.nco.ncep.noaa.gov/pmb/docs/grib2/grib2_doc/grib2_temp5-4.shtml),
  and [model identifiers](https://www.nco.ncep.noaa.gov/pmb/docs/on388/tablea.html).

V10 remains unchanged. Its stale-cycle/resource findings and isolation gate
remain open, and maintenance is deferred. This code is not a deployment or
funding approval; **NOT_READY_TO_FUND**.
