# R23 regional/correlated-exposure mapping: real-source evidence

Master upgrade I2 (section 13A) requires "a versioned mapping or clustering
layer capable of representing: station -> city; city/station -> region; shared
synoptic/weather-system exposure where evidence supports it; common
model/source dependence." The requirements matrix listed R23's remaining tail
as "actual mappings/protected review/runtime integration pending." This batch
closes the "actual mappings" piece only, using two real, free, unauthenticated
public endpoints already reachable from this host (`api.weather.gov`), and
does not attempt protected-review governance or candidate-runtime wiring.

## What was added

- `polymarket_scanner/weather_only_station_region.py`: a strict pure-parser
  pair (`parse_nws_point_office`, `parse_nws_office_region`) plus a bounded
  read-only `NWSStationRegionClient`, mirroring the existing
  `weather_only_station_metadata.py` pattern. It chains the real
  `/points/{lat},{lon}` -> `cwa` -> `/offices/{cwa}` -> `nwsRegion` endpoints
  to resolve a certified station's real NWS regional-headquarters assignment
  (`EASTERN`/`SOUTHERN`/`CENTRAL`/`WESTERN`/`PACIFIC`/`ALASKA`). An
  unrecognized region code, a coordinate echo mismatch, or a broken
  point->office chain fails closed; no region is ever defaulted or guessed.
- `polymarket_scanner/v11/region_membership.py`: `build_station_membership`
  binds an already-certified `StationMetadata` (v11/certification.py) to this
  real region evidence, producing a `scenario_risk.StationMembership` whose
  `region` is the real NWS region, whose conservative `weather_groups` default
  to that same real region (an interpretable synoptic-exposure proxy, not
  invented precision, per the master's explicit "do not create fragile
  pseudo-precision" instruction), and whose `source_groups`/`model_groups`
  default to the station's own already-certified real
  `observation_providers`/`forecast_providers`. A coordinate or office-chain
  mismatch between the certified station and the region evidence fails closed.

## Tests

`tests/test_weather_only_station_region.py` (14 cases) and
`tests/test_v11_region_membership.py` (9 cases): **23 passed / 0.43 s**, no
skips/warnings. Broader affected selection
(`-k "scenario_risk or certification or station_metadata or station_region or
region_membership or forecast_sources"`): **104 passed / 6.81 s**, no
skips, four pre-existing FastAPI warnings, no failures. No full regression
(new, additive modules with no changed call sites in existing production
code).

## Real live-source verification (not part of the automated suite)

Run once against the live public `api.weather.gov` service, 2026-09-27, to
confirm the adapter actually resolves real regions and not just canned
fixtures (consistent with `PUBLIC_EXTERNAL_EVIDENCE`: free, unauthenticated,
no owner/credential action):

```
KATL -> cwa=FFC, region=SOUTHERN
KDEN -> cwa=BOU, region=CENTRAL
KLAX -> cwa=LOX, region=WESTERN
KJFK -> cwa=OKX, region=EASTERN
KSEA -> cwa=SEW, region=WESTERN
```

These match the real, independently verifiable NWS regional-headquarters
structure (Eastern Region/Bohemia NY, Southern Region/Fort Worth TX, Central
Region/Kansas City MO, Western Region/Salt Lake City UT). This live run is not
wired into CI/pytest to avoid a network-flaky test; it is a one-time
operational confirmation that the parser's real-schema assumptions
(`properties.cwa`, `properties.forecastOffice`, `nwsRegion`) match the live
service, not just the hand-built fixtures in the unit tests.

## What remains open

- **Protected review**: no authority/governance binding (comparable to
  `host_trust/*/authority.py` patterns used elsewhere) yet gates who may
  approve a `CorrelationMap` for a candidate. The master does not itself
  prescribe such a protocol for this requirement, so none is invented here.
- **Runtime integration**: no candidate/guardian construction path yet calls
  `build_station_membership` to assemble a real `CorrelationMap` for an actual
  running candidate; `PaperGuardian`/`PaperCoordinator` still accept a
  caller-supplied `CorrelationMap` exactly as before this batch.
- City identity still comes from the caller-supplied contract/rule data
  (already real, unchanged by this batch), not from this new adapter.

No V10, credential, private-input, or existing production call-site was
touched. No new formal C/J/E/A milestone is claimed: this closes one of
R23's three explicitly named remaining tails, leaving protected review and
runtime integration open, consistent with this project's practice of not
crediting a boundary until a requirement's full remaining tail set for that
boundary is closed.
