# V11 pull-only weather model input panel

Agent-2 implementation, 2026-09-29. Nonfinancial source/archive infrastructure;
no service deployment, model promotion, settlement change or acceptance credit.
Existing NOAA GEFS files and BrainWork historical downloader are unchanged.

## Supported boundary

| Provider | Implemented now | Explicit remaining gate |
| --- | --- | --- |
| NOAA GEFS | Compatibility bridge through the existing field normalizer into the common typed point contract | Existing GEFS operational/evidence gates remain unchanged |
| ECMWF IFS ENS | Exact request identity, Cycle-50r1 `oper/fc` control plus `enfo/ef` perturbed layout, JSON-lines index selection, anonymous streamed byte-range capture, CCSDS GRIB2 station extraction, archive replay | Current public-open-data parity is verified on real 2026-09-29 bytes; full historical archive still requires reviewed external archive access |
| ECMWF AIFS ENS | Exact request identity, separate `enfo/cf` and `enfo/pf` files, the same bounded CCSDS/index/archive path, separate source/model identity and `ai` class | Current public-open-data parity is verified on real 2026-09-29 bytes; full historical archive still requires reviewed external archive access |
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
`class=ai`, `stream=enfo`, separate `type=cf` and `type=pf` files, cycles
00/06/12/18 UTC and six-hour steps 0–360, including surface `2t`. Since IFS Cycle
50r1 (May 2026), the IFS control is the deterministic `stream=oper,type=fc`
forecast while the 50 perturbed members remain indexed as `type=pf` inside the
`stream=enfo` `ef` file. The code uses a conservative 72-hour eligibility window;
eligible does not mean published or accessible. Historical mode returns
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
requires allowlisting for operational access. This source is now explicitly
**DEFERRED_NO_ACCESS** and is not on the V11 critical path; GCS, BigQuery and Earth
Engine transports remain future optional work. Historical availability and local
entitlement are not assumed. Passing `AccessState.REVIEWED` alone cannot activate
a connector: absent transport returns `NOT_AVAILABLE`; an arbitrary transport
returns `CONNECTOR_REVIEW_REQUIRED`.

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
- Index: 3 MiB / 12000 lines; strict JSON, duplicate keys rejected; ordered,
  nonoverlapping offsets. Those bounds cover the observed 2026-09-29 IFS `enfo`
  index (1,995,799 bytes / 8,500 rows) without accepting an unbounded document.
  Planning remains at most 51 fields and 16 MiB selected bytes.
- Field: 4 MiB maximum, bounded object offsets, exact HTTP 206/Content-Range and
  byte count; injected test transports are forced to SYNTHETIC evidence. No
  redirect, cookie forwarding, auth, proxy environment, compressed
  HTTP body, full-file fallback or retry loop. Each collection has a 30-second
  wall deadline and 10-second HTTP timeout.
- Default EvidenceStore record limit remains 1 MiB. Larger GRIB fields require an
  explicitly sized existing `Limits(payload_bytes=8*1024**2)` archive; otherwise
  its normal budget rejection applies. Database/disk limits remain enforced.
- GRIB decoder: one regular 0.25-degree field, at most 1440×721 grid points.
  Simple and IEEE packing use bounded direct extraction. CCSDS template 5.42 is
  decoded through ECMWF ecCodes when that reviewed optional dependency is available;
  for CCSDS only, the single bounded field is fully decoded and re-encoded before
  the selected station value is trusted, and must reproduce the original GRIB bytes exactly;
  this closes an independently reproduced ecCodes behavior where self-consistent
  truncated CCSDS could otherwise return plausible wrong values. Missing Python or
  native ecCodes, malformed metadata/index types, JPEG/complex packing, bitmaps and
  other grids fail closed. Real 2026-09-29 AIFS control/member and IFS control/member
  fields all pass the hardened path from anonymous HTTP ranges. A release/header
  signature mismatch still fails closed; unannounced vendor changes invisible in
  metadata cannot be inferred, so operational release monitoring remains required.
- WN extraction envelope: 64 KiB, one variable/run/time/grid point, at most 16
  raw-parent hashes. No raw ensemble compression/decoding dependency is installed.

The existing forecast feature contract permits 126 member features total. The
four core ensembles exceed it (31+51+51+64). This build does not widen that budget
or discard members to fit it. Reviewed feature reduction/schema, local-day
assembly, dependence-aware held-out comparisons and calibration are subsequent
work. Source presence or provider agreement earns no learning/financial authority.

## Verification and handoff

Final focused result after the adapter-v2 provenance bump: **110 passed / 9.81 s**.
Independent post-hardening affected regression: **290 passed / 485.22 s**, exit 0.
The separate high-reasoning detached review reported **NO BLOCKING FINDINGS** on
`640ea632db1478cb0d30ad866819d1396aa787bd`: **262 tests passed**, including all
110 model-panel tests, and an environment without ecCodes produced **108 passed / 2
expected skips**. The reviewed set covers the model panel plus forecast sources, GEFS
schedule/source/GRIB decoding, remaining-day forecast assembly and forecast learning.
No financial/order/promotion path changed.

The focused suite covers both ECMWF providers, Cycle-50r1/AIFS-v2 URL layouts,
control/member metadata, simple/IEEE/CCSDS packing, member edges, schema/run/version
mismatch, bounds, source dependence, unit conversion, transport denial/range
failures, receipt causality, archive tampering, derived-value replay, WN
statistics/member separation, absent access and the GEFS bridge. Independent review
also reproduced and is now regression-covered for: self-consistent CCSDS truncation,
missing native ecCodes, malformed JSON selector types, and undersized product metadata.

A bounded anonymous live-parity check on the public 2026-09-29 00z release passed
four representative paths end to end: AIFS control (`enfo/cf`), AIFS member 35
(`enfo/pf`), IFS control (`oper/fc`) and IFS member 12 (`enfo/ef`). All four were
CCSDS template 5.42 regular 0.25-degree fields and decoded to the same nearest KATL
grid point (33.75, -84.5). The observed IFS ensemble index was 1,995,799 bytes / 8,500
rows, motivating the still-bounded 3 MiB / 12,000-row index ceiling. This proves
current public-source layout/decoder parity only; it does not prove indefinite
retention, future release stability, calibration or acceptance. WeatherNext is
**DEFERRED_NO_ACCESS** and is not a V11 critical-path blocker.

A separate anonymous-retention probe of ECMWF's official public AWS replica returned
HTTP 206 for AIFS control/member and IFS control/member paths on sampled runs from
**2026-05-13 through 2026-09-25**. All 541 preregistered Brain station-days fall
between 2026-08-23 and 2026-09-28, so the planned cohort is inside the observed
public-replica history window. This supports a bounded historical IFS/AIFS backfill
without MARS credentials; it does not claim permanent AWS retention or substitute
for the full ECMWF archive. WeatherBench2 remains research-only auxiliary material
and is not required for this production-oriented backfill.

ECMWF ecCodes was installed only in the development test interpreter to exercise
CCSDS; production runtime requirements and services were not changed. The adapter
imports it lazily and fails closed if unavailable. No V10, PAPER service, BrainWork
downloader, financial credential or execution authority was changed.

Independent review on 2026-09-29 re-ran the final focused model-panel suite
(**110/110 passed / 11.06 s**) and the base GRIB suite (**36/36 passed / 0.53 s**),
and independently repeated the four real public ECMWF decode paths successfully.
An affected GEFS-source module produced one transient source-gate failure under load;
the exact case passed alone (**1/1**) and untouched main passed the complete same
module (**32/32**), while the Agent-2 branch changes no GEFS source/schedule/GRIB
file or import dependency. Preserve that diagnostic as a non-ECMWF flake signal.
Current credit remains unchanged by source plumbing alone: **89/200 (44.5%), 1/50
fully accepted**; **NOT_READY_TO_FUND**.
