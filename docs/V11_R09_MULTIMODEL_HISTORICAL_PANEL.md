# R09 historical multi-model panel — 2026-09-30

The completed GEFS/IFS/AIFS downloads do **not** support the requested exact-day,
causal multi-model fit under the no-interpolation constraint. This implementation
produces a deterministic, integrity-checked historical **point panel**, preserves
all preregistered splits, and refuses to turn unsupported features into daily
extremes. Real candidate scores are null; status is **NOT_FITTED /
NOT_CALIBRATED / NO_PROMOTION**. It would be misleading to say this panel was
fitted, even with the weaker FITTED_NOT_CALIBRATED label.

This is useful R09 implementation and evidence, but no new C/J/E/A credit:
**91/200 (45.5%), 1/50 fully accepted; NOT_READY_TO_FUND**.

## Why the requested fit fails closed

The original GEFS plan brackets the local day with three-hour point temperatures.
The reviewed ECMWF downloader filters that plan to supported six-hour hours.
Filtering does not preserve brackets:

| Station timezone | Day boundaries relative to selected run | Retained six-hour fields | Lost bracket |
| --- | --- | --- | --- |
| New York / Berlin / Paris | 4–28 h | 6,12,18,24,30 | start |
| Chicago / London | 5–29 h | 6,12,18,24,30 | start |
| Denver | 6–30 h | 6,12,18,24,30 | neither |
| Los Angeles | 1–25 h | 0,6,12,18,24 | end |
| Sao Paulo | 3–27 h | 6,12,18,24 | both |

These are actual August/September cohort offsets, not year-round assumptions.
Calendar boundaries are computed with IANA timezones; tests cover 23/25-hour DST
days and fractional offsets. The interval is `[local midnight, next midnight)`.
Next midnight and other outside-day samples never enter the sampled extrema.

Even Denver's exact boundary samples do not identify between-sample extrema.
Two paths can match every retained point and have different highs/lows between
points. The pipeline therefore retains `exact_day_extrema_c=null` for every
provider, including GEFS. Native in-day sampled extrema are clearly marked audit
diagnostics, with common six-hour GEFS sampling for cadence comparability. They
are not passed to the learner, and no interpolation or missing-value repair is
performed. The existing GEFS piecewise-linear live/R47 contract is unchanged and
is not silently assigned to these ECMWF samples.

Additional evidence gates are independently material:

- Historical forecast availability is UNKNOWN; later retrieval timestamps cannot
  be backdated to run initialization. The frozen latest-pre-midnight run selection
  is verified, but is not a publication/receipt proof.
- Catalog payout vectors are complete and one-hot, but historical label knowable
  times, revisions, original market metadata availability, and exact settlement
  rule fingerprints are unavailable. Current NWS source identities do not fill
  those gaps. Catalog labels remain separate from forecast features.
- The SQLite stores retain index/GRIB hash references, not raw GRIB bytes. File
  integrity and relational provenance can be verified; decoding those original
  bytes or independently attesting their source truth cannot.
- ECMWF's recorded release binding is OBSERVED_HEADER_ONLY, with
  `operational_release_reviewed=false`.

## Implemented boundary

`tools/v11_multimodel_panel.py` reads only immutable SQLite URIs, refuses a nonempty
WAL/journal, verifies SQLite integrity, recomputes every-table canonical content
digests and file SHA-256s, and rechecks input files after assembly. It also checks
the legacy GEFS content digest against its already-frozen manifest. No source DB
is copied over, indexed, migrated, or otherwise written.

The hash-only input pins in `config/v11/r09_multimodel_input_pins.json` bind the
existing plan/catalog, completion reports, GEFS manifest and both databases.
Plan/catalog self-digests and links are recomputed. All point rows must join exact
provider/run/member/hour messages; members must be exactly 0–30 or 0–50, with no
missing/extra/duplicate values. The audit verifies station metadata identity,
split/date identity, source URLs/ranges/hash shapes, finite Kelvin values and
grid distances. Extra/orphan messages/points fail closed. It verifies complete
whole-degree bucket partitions, complementary final payouts, unique target IDs,
both HIGH/LOW events per station-day, and the one-station-per-city cohort identity
from both event families. A generalized multi-station city mapping is not inferred.

Outputs retain all 541 station-days and 1,082 events, including unavailable/gated
rows. TRAIN / DEVELOPMENT / HISTORICAL_CONFIRMATION remain 357 / 120 / 64
city-days, with no cherry-picked eligible subset. Historical confirmation is
explicitly **not forward untouched evidence**. Counts of city-days/date blocks
are reported, while statistical effective independence remains unknown.

The deterministic gzip dataset, manifest and gate result are local private
evidence, excluded from Git. Outputs are write-once: an identical rerun is allowed;
different bytes at an existing destination fail. The manifest binds the actual
committed code bytes, commit/tree, input digests, frozen evidence timestamp,
Python/SQLite/zlib versions and system timezone-file digests. There is no runtime
model bundle, live inference adapter, protected-state access or promotion path.

## Reference comparison implementation

`tools/v11_multimodel_stacking.py` implements a small grouped linear probability
pool. Provider member scenarios share a normalized provider budget. IFS/AIFS
share an ECMWF lineage group; GEFS gets a separate group budget. Neither providers
nor members count as independent trials. All models also share weather-system
dependence, so group names do not assert independence.

For each HIGH/LOW family, TRAIN chooses the IFS share within ECMWF from
`0, 0.5, 1`; DEVELOPMENT then chooses the GEFS group share from
`0, 0.25, 0.5, 0.75, 1`. Ties use grid order. All eight attempts are retained.
The parameter digest freezes before confirmation scoring; confirmation cannot
enter the selection API. The baseline compares GEFS-only, IFS-only, AIFS-only and
the frozen pool on identical examples. It records multiclass Brier, unclipped
log loss (infinity represented explicitly), reliability, calibration error,
sharpness and family/station counts. Weighting is by city-day, not buckets or
member counts. Paired date-block bootstrap comparisons preserve simultaneous
station dependence; their intervals are descriptive, not acceptance evidence.
Conservative bounds remain vacuous `[0,1]` and are never normalized.

The numerical evaluator currently accepts only explicitly SYNTHETIC examples:
there is **no real exact-day causal adapter** for these stores. This is an
intentional admission gate, not a flag users can override. Tests demonstrate
selection, scoring and reproducibility; they do not constitute real fitting or
calibration evidence. No champion comparison, skill improvement or profitability
is claimed. A real adapter requires reviewed feature semantics and the missing
source/label availability evidence before this API may be extended.

## Remaining R09 work

1. Review the proven point-versus-day-extreme incompatibility. Either supply
   sufficient source-native interval extreme evidence, or explicitly review a
   different sampled-predictor/trajectory contract. Do not rename the current
   samples or silently permit interpolation. Any expanded acquisition must use a
   new immutable artifact; preserve the completed stores.
2. Establish exact source/rule/revision identity, forecast and market availability,
   and label knowable-time evidence sufficient for causal learner admission.
3. After an independently reviewed real adapter exists, freeze a new comparison
   protocol, run actual provider ablations/stacking, assess calibration and broad
   station/family performance, and retain honest historical confirmation status.
4. Independent acceptance and genuinely forward evidence remain outstanding.
   WeatherNext remains DEFERRED_NO_ACCESS. Nothing here installs or promotes a
   model or changes financial authority.

Recovery used clean branch `r09-multimodel-brain-20260930` at `7bb4f27`.
`CLAUDE.md` was absent in this worktree; the read-only development copy supplied
the instructions. The requested `V11_WEATHER_PROBABILITY.md` was absent in both
trees; the existing design was `V11_PROBABILITY_ENGINE.md`. The reviewed private
master SHA-256 was verified as
`a0e16d9bd7344c943a54a16a53c6757662363d93642f6e5cb7953cd047659b4a`.
Private master text, raw databases, catalog and point data are not Git artifacts.
