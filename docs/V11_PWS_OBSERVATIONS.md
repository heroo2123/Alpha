# V11 public PWS observations

The implemented adapter uses the documented NOAA MADIS public Surface Viewer
GET endpoint, restricted to APRSWXNET/CWOP temperature data, bounded geographic
boxes and recent observation windows. It uses no account or paid subscription.
Anonymous bounded requests on 2026-09-23 returned real temperature XML around all
13 control settlement-station areas. The private coverage report retains exact
counts, receipt ages and raw response hashes; this public repository does not
redistribute the observations.

Official references checked for this implementation:

- [Public Surface Viewer](https://madis-data.ncep.noaa.gov/public/sfcdumpguest.shtml)
- [Viewer parameters and units](https://madis-data.ncep.noaa.gov/sfcdump_help.html)
- [CWOP overview](https://madis.ncep.noaa.gov/madis_cwop.shtml)
- [Provider restrictions](https://madis.ncep.noaa.gov/madis_restrictions.shtml)

NOAA identifies CWOP as public; other mesonets may be restricted. Provider
attribution and applicable redistribution conditions remain relevant. The parser
preserves QC descriptors/masks and converts documented Kelvin temperatures to
Celsius. Provider filtering and no failed bitmask checks do not prove sensor
representativeness or locally calibrated QC.

`v11/weather_sources.py` enforces source/variable identity, response/record bounds,
geographic containment, finite physical ranges, stale/future timestamps, duplicate
station/time rejection and XML declaration refusal. Observation identity excludes
local receipt, so repeated retrieval is not a new independent sensor observation.
Raw revisions and receipt/feature-ready times remain distinct.

Current limits: the probe is one coverage sample; observations can be delayed or
backfilled. Publication/provider-receipt timestamps are absent in the selected
XML format. Age at local receipt is not network latency. No paired official-arrival
series, local stuck/outlier/bias/duplicate-network validation, lead calibration or
out-of-sample incremental value has been established. These remain explicit
implementation/evidence work, not passed readiness gates.

PWS is auxiliary only. Predicting the next official observation or a threshold
crossing does not establish final exact-bucket payout or an executable early exit.
The observation adapter cannot settle contracts, certify finality, open positions
or bypass station, rule, valuation, coordinator or protected risk gates.

## Candidate runtime and fresh-census integration

`v11/pws_runtime.py` now processes one configured event per bounded step, with
at most 64 normalized captures, the existing 128-station / 16,384-sample limits
and a two-second cooperative work budget. Overflow gates the event without
selecting a subset. The candidate's existing anonymous collection job supplies
MADIS receipts; a separate scheduled QC job performs no HTTP and has durable
input identity, interruption recovery, an exclusive worker lock and configuration
checks. Completed work replays without renewing receipt or sensor timestamps.

QC reparses raw XML, retains source hashes and original first receipt times,
and checks source/metadata concurrency at publication. Original sensor identity
and relocation quarantine persist across events, process recovery and coordinate
reversion. Re-observing unchanged metadata does not create new identity epochs.
Every actual reported coordinate remains in the source archive. Runtime health
and strategy admission validate current raw/QC lineage and propagate station
metadata guards into account opening checks. The existing aggregate 64-head
guard limit remains: a neighborhood exceeding safe admission capacity stays
informational/gated, even though the QC archive supports up to 128 stations.

Explicitly PWS-dependent census plans now collect and normalize MADIS through
the same provider cooldowns, then calculate QC from bounded archived history.
At least the latest raw response must have arrived after the census claim;
processing a pre-claim response again cannot clear a gap. Older causal trajectory
samples remain usable history. Coverage expiry uses the oldest contributing
sensor, and metadata/source changes fence the census commit. Optional PWS is
not added to unrelated census requirements. Missing/empty neighborhoods retain
other source receipts and remain gated.

Verification: 255 related checks passed in 32.63 s; four focused candidate PWS/QC
checks passed after adding the final census/periodic policy-consistency guard.
The combined candidate test demonstrates mocked public GET collection, QC,
receipt routing and unchanged common-account cash without fills. Other new cases
cover raw/metadata races, cross-event drift, interrupted metadata/QC publication,
no-renewal replay, overflow, clock failure and fresh-census lineage.
These are off-host synthetic/mock checks, not observed information lead,
calibrated reliability, independent review or deployment acceptance.
