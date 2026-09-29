# V11 pull-only weather model input panel

Agent-2 implementation, 2026-09-29. Nonfinancial source/archive infrastructure;
no service deployment, model promotion, settlement change or acceptance credit.
Existing NOAA GEFS files and BrainWork historical downloader are unchanged.

## Supported boundary

| Provider | Implemented now | Explicit remaining gate |
| --- | --- | --- |
| NOAA GEFS | Compatibility bridge through the existing field normalizer into the common typed point contract | Existing GEFS operational/evidence gates remain unchanged |
| ECMWF IFS ENS | Exact request identity, JSON-lines index selection, anonymous streamed byte-range capture, strict GRIB2 station extraction, archive replay | Reviewed operational release/header pins and real-data decoder parity; unsupported packing/grid returns a decoder gate |
| ECMWF AIFS ENS | Same machinery, separate source/model identity and `ai` class | Same decoder/release gates; full historical archive requires external access |
| Google WeatherNext 3 | Exact dataset/request identity, allowlist gate, bounded archived station/grid temperature schema, 64-member or separate statistics normalization | No reviewed cloud connector, credentials or local historical access; public evidence normalization is refused |
| AIFS Single | Auxiliary source/dependence identity only | No adapter; shares AIFS vote family, never an additional independent vote |

This is a station-point input layer for **later day-extreme assembly**. New panel
records intentionally have no `temperature_input` daily-extreme payload and cannot
enter the existing payout learner as if point samples were daily highs/lows.
`replay_normalized` supplies the offline source boundary for a later reviewed
assembler/capture integration. There is no scheduler or runtime registration.

## Public source facts and identities

[ECMWF Open Data](https://www.ecmwf.int/en/forecasts/datasets/open-data)
retains approximately the latest 12 IFS/AIFS runs. AIFS ENS uses `model=aifs-ens`,
`class=ai`, `stream=enfo`, `type=pf/cf`, cycles 00/06/12/18 UTC, six-hour steps
0–360, including surface `2t`. The code uses a conservative 72-hour eligibility
window; eligible does not mean published or accessible. Historical mode returns
`EXTERNAL_ACCESS_REQUIRED`, old/future run requests `NOT_AVAILABLE`.

The [ECMWF client documentation](https://github.com/ecmwf/ecmwf-opendata)
describes indexed field selection and individual downloads. The adapter uses
`.index` `_offset`/`_length` metadata and one HTTP Range per selected field,
without adding that client as a dependency. It deliberately requests the
six-hour IFS subset (up to 360h for 00/12; 144h for 06/18).

[WeatherNext model documentation](https://developers.google.com/weathernext/guides/models)
specifies 64 members and hourly initialization. Six-hourly cycles extend to
360h; interim cycles to 48h. Grid temperature and station-head temperature are
separate variables/identities, not independent models. IFS, AIFS and WeatherNext
have ECMWF analysis/training lineage; all four core models share the atmosphere.
The contract makes no numerical correlation or independence claim.

[WeatherNext GCS documentation](https://developers.google.com/weathernext/guides/gcs)
identifies `gs://weathernext3_spatial/weathernext_3_0_0/zarr/` for the full ensemble
and `gs://weathernext3_statistics_spatial/weathernext_3_0_0_statistics/zarr/` for
statistics. Full-member time coordinates retain `lead_time + lead_subtime`;
statistics use flattened hourly lead times. The full-ensemble bucket has requester
pays enabled. No billing configuration or chargeable request is implemented here.

[WeatherNext access documentation](https://developers.google.com/weathernext/guides/access-forecast)
requires allowlisting for operational access. GCS, BigQuery and Earth Engine
remain separate future transport implementations. Historical availability and
local entitlement are not assumed. Passing `AccessState.REVIEWED` alone cannot
activate a connector: absent transport returns `NOT_AVAILABLE`; an arbitrary
transport returns `CONNECTOR_REVIEW_REQUIRED`. No access request was submitted.

## Contracts and data flow

`model_panel.SourceIdentity` binds provider, exact model version, dataset and a
reviewed release-evidence digest. Its key changes for every changed pin; `latest`,
`current` and `unknown` versions are refused. Provider family, model identifier,
ensemble count, dependence metadata and auxiliary status are derived, not supplied
by downloaded values. Release hashes identify reviewed material; they do not
independently attest its truth or confer approval.

`StationTarget` binds event, station, station metadata/rule fingerprints, local
calendar target, timezone, coordinates, grid-distance ceiling and output unit.
Point validity can bracket the local target day; the eventual assembler must
validate local-day coverage and preserve the exact contract's settlement rules.

`ForecastSlice` binds that context to initialization, valid time, receipt,
availability, optional publication, grid identity/coordinates, original and output
units, exact member IDs/count, raw record/hash, response hash and evidence class.
Run and slice identities are deterministic. Missing members never become synthetic
members. WN member captures require the complete unique IDs 0–63. Summary mean and
quantiles are separate from members, with ordered quantiles, and never count as
samples or vote counts.

The source flow is:

1. Construct reviewed source, station and request pins. ECMWF additionally requires
   an exact GRIB release-header signature. No operational pins are shipped.
2. `ECMWFCollector.collect` fetches and archives the index first, then archives the
   selected field. A failed second request retains the successful index. Statuses
   distinguish access denial, missing data and other unavailable responses.
3. `normalize_ecmwf` rechecks archived index selection, both request identities,
   evidence classes, field length/hash and byte-bound run/member/step, then extracts
   the nearest allowed grid point. It never calls transport.
4. `persist_slice` recomputes from raw archive evidence before appending a derived
   MODEL record. Modified derived values are refused. Completion time is the
   derived record's availability; replays never backdate it to initialization.
5. `replay_normalized` requires the original source/target pins and a causal
   cutoff, rederives from raw bytes, verifies the complete normalized payload and
   rejects unknown historical availability or synthetic-as-live evidence.

Imported raw data defaults to `HISTORICAL_AVAILABILITY_UNKNOWN`; a later download
cannot claim earlier Alpha availability. Publication schedules are not receipt
proof. Fixture imports are explicitly `SYNTHETIC`. WN's JSON schema is an **Alpha
bounded extraction envelope**, not a claim that GCS directly publishes JSON.
Its generation and parent object hashes retain extraction provenance. A future
connector must retain the actual parent objects and reviewed selection/decoder
identity; no Zarr decoding or parent-object truth is claimed by these fixtures.

`normalize_gefs_panel` calls the existing GEFS field normalizer and preserves its
original archived records, freshness checks and decoder. GEFS continues using its
existing persistence/day-path pipeline; `persist_slice` is for the two new adapter
families. No GEFS transport, schedule, model input or learner behavior changes.

## Bounds and unsupported data

- Up to 64 members; ECMWF control plus 50 perturbed members. No member concatenation
  across providers and no voting/confidence formula.
- Index: 768 KiB / 4096 lines; strict JSON, duplicate keys rejected; ordered,
  nonoverlapping offsets. Planning: at most 51 fields and 16 MiB selected bytes.
- Field: 4 MiB maximum, bounded object offsets, exact HTTP 206/Content-Range and
  byte count; injected test transports are forced to SYNTHETIC evidence. No
  redirect, cookie forwarding, auth, proxy environment, compressed
  HTTP body, full-file fallback or retry loop. Each collection has a 30-second
  wall deadline and 10-second HTTP timeout.
- Default EvidenceStore record limit remains 1 MiB. Larger GRIB fields require an
  explicitly sized existing `Limits(payload_bytes=8*1024**2)` archive; otherwise
  its normal budget rejection applies. Database/disk limits remain enforced.
- GRIB decoder: one regular 0.25-degree field, at most 1440×721 grid points;
  only selected nearest candidates are unpacked. Simple and IEEE packing work.
  CCSDS, JPEG, complex packing, bitmaps and other grids fail closed. This build
  does not claim operational ECMWF packing coverage. A release/header signature
  mismatch fails closed; unannounced vendor changes invisible in metadata cannot
  be inferred, so operational release monitoring/parity remains required.
- WN extraction envelope: 64 KiB, one variable/run/time/grid point, at most 16
  raw-parent hashes. No raw ensemble compression/decoding dependency is installed.

The existing forecast feature contract permits 126 member features total. The
four core ensembles exceed it (31+51+51+64). This build does not widen that budget
or discard members to fit it. Reviewed feature reduction/schema, local-day
assembly, dependence-aware held-out comparisons and calibration are subsequent
work. Source presence or provider agreement earns no learning/financial authority.

## Verification and handoff

Focused result: **99 passed / 5.79 s**, no skips or warnings. Command:
`pytest -q -p no:cacheprovider --tb=short --maxfail=3 tests/test_v11_model_panel.py`.
Only this new test surface was run; no broad regression.

Focused synthetic tests cover both ECMWF providers, all supported packing modes,
member edges, schema/run/version mismatch, bounds, source dependence, unit
conversion, transport denial/range failures, receipt causality, archive tampering,
derived-value replay, WN statistics/member separation, absent access and the GEFS
bridge. No new financial, order, label or promotion events are emitted.

One bounded anonymous request for an ECMWF AIFS index returned HTTP 404. No live
forecast bytes, source parity, Google access or historical entitlement were
established. Tests and fixtures are not live evidence. No packages were installed.
The existing test interpreter was reused read-only with bytecode/cache disabled;
all test databases were temporary. No V10, service, separate checkout or BrainWork
downloader files were changed.

Independent review must inspect this branch against its base, rerun only
`tests/test_v11_model_panel.py`, and review the documented operational gates before
merging `agent2-weather-model-panel-20260929` into
`weather-v11-profitability-upgrade-2026-09-23`. Merge and deployment are not part of
this task. Current credit remains **89/200 (44.5%), 1/50 fully accepted**;
**NOT_READY_TO_FUND**.
