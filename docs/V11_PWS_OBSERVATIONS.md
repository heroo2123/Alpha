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
