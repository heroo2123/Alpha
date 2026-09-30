# R09 ECMWF native-extreme feasibility — 2026-09-30

IFS native interval extrema exist. This is **not** an exact-day fit, model
acceptance, or a completed historical extreme backfill. R09 remains
**NOT_FITTED / NOT_CALIBRATED / NO_PROMOTION**. The original 541-day point
stores and all newer R47 work are preserved. WeatherNext is DEFERRED_NO_ACCESS.

Work started at integrated reviewed main `fd59e667bc948445e102241ace77653e1c3f0fe2`
on `r09-ecmwf-native-extrema-20260930`, with a clean tree. CLAUDE.md and AGENTS.md
were absent in this worktree. The FINAL-REVIEWED private master was read and its
SHA-256 matched `a0e16d9bd7344c943a54a16a53c6757662363d93642f6e5cb7953cd047659b4a`.
No private master contents, databases, station-day catalog or forecast values are
published. No V10, service, protected-state, account or financial changes.

## Public product identity and actual bytes

The [ECMWF public product list](https://www.ecmwf.int/en/forecasts/datasets/open-data)
identifies IFS `class=od`, control `stream=oper,type=fc`, perturbed
`stream=enfo,type=pf`, 3-hour outputs through 144h and 6-hour outputs thereafter
(00/12 runs extend to 360h; 06/18 stop at 144h). Temperature products include:

| Parameter | ID | Provider-defined statistic | Unit |
| --- | --- | --- | --- |
| mx2t3 | 228026 | Maximum 2m temperature in preceding 3 hours | K |
| mn2t3 | 228027 | Minimum 2m temperature in preceding 3 hours | K |
| mx2t6 | 121 | Maximum 2m temperature in preceding 6 hours | K |
| mn2t6 | 122 | Minimum 2m temperature in preceding 6 hours | K |

The actual 2026-09-29 00z three-hour control and member-1 ranges were downloaded
anonymously from the portal and decoded, not inferred from this list. The control
uses `.../ifs/0p25/oper/20260929000000-3h-oper-fc.{index,grib2}`; perturbed
members are multiplexed in `.../ifs/0p25/enfo/20260929000000-3h-enfo-ef.{index,grib2}`.
Index rows say `type=pf`, not `ef`. Members 1–50 are present in the inspected
ensemble indexes; the control is member 0 via `oper/fc`.

Observed native GRIB metadata: product template 4.8 control / 4.11 perturbed;
`startStep=0,endStep=3`, `stepType=max/min`, statistical processing 2/3;
one time range, length 3 with unit code 1 (hours); time-increment type 2;
increment 450 with unit code 13 (seconds); zero missing statistical inputs;
height type 103, value 2m; Kelvin; global 1440×721 regular 0.25° grid;
CCSDS template 5.42; centre 98; generating process 161, table version 32.
The 450-second increment is model processing, not an hourly observation or a
claim of continuous physical extrema. The bounded decoder checks all these facts
and the exact run/member/interval, rather than trusting the short name alone.

Official interpretation references:
[template 4.8](https://codes.ecmwf.int/grib/format/grib2/templates/4/8/),
[template 4.11](https://codes.ecmwf.int/grib/format/grib2/templates/4/11/),
[statistical processing](https://codes.ecmwf.int/grib/format/grib2/ctables/4/10/),
[time-increment type](https://codes.ecmwf.int/grib/format/grib2/ctables/4/11/),
[time units](https://codes.ecmwf.int/grib/format/grib2/ctables/4/4/).
GRIB identifies the native range and processing operation; the inspected evidence
does not independently attest equivalence to the contract's `[midnight,next
midnight)` endpoint convention. Never silently change that convention. Spatial
extraction is a nearest published grid point within 50km, not an exact station
observation or a claim of native-grid/time aggregation commutativity.

AIFS ENS is `model=aifs-ens,class=ai,stream=enfo`, separate `cf` / `pf` files,
6-hour outputs. Inspected indexes have point `2t`, no `mx2t`, `mn2t`,
`mx2t3`, `mn2t3`, `mx2t6` or `mn2t6` (numeric IDs are also checked).
Actual control/member-1 `2t` GRIBs have template 4.1, `stepType=instant`,
`startStep=endStep=6`, parameter 167, Kelvin, process 2 and table version 36.
AIFS's official product list likewise does not advertise temperature extrema.
This is a scoped negative result, not proof that no other ECMWF product or future
AIFS release could ever offer extrema. No AIFS extreme adapter is implemented.

## Exact frozen cohort and coverage limits

The existing plan and hash pins are reused read-only. All 541 station-days / 1,082
HIGH+LOW events retain TRAIN / DEVELOPMENT / HISTORICAL_CONFIRMATION =
357 / 120 / 64. Confirmation remains historical, not forward untouched evidence.
The one-hour-before-midnight, latest-six-hour initialization rule is unchanged;
no later run is substituted to obtain convenient intervals.

| Frozen timezone | Days | Start–end hours after selected run | Three-hour alignment |
| --- | ---: | --- | --- |
| America/New_York | 108 | 4–28 | No |
| America/Chicago | 144 | 5–29 | No |
| America/Denver | 36 | 6–30 | Yes |
| America/Los_Angeles | 108 | 1–25 | No |
| America/Sao_Paulo | 37 | 3–27 | Yes |
| Europe/Berlin | 36 | 4–28 | No |
| Europe/London | 36 | 5–29 | No |
| Europe/Paris | 36 | 4–28 | No |

Thus **73 geometrically aligned / 468 crossing**. Alignment is a necessary
condition, not verified raw coverage or exact endpoint equivalence. For example,
a maximum over hours 3–6 cannot reveal the maximum over 4–6. Different subinterval
paths can produce the same published maximum; neither clipping, interpolation,
resampling nor extra ensemble members resolves that information loss. The same
argument applies to minima. The original six-hour point-panel finding (505/541
missing brackets, zero exact extremes) remains correct for its different inputs.

The [official AWS registry](https://registry.opendata.aws/ecmwf-forecasts/)
identifies the anonymous `ecmwf-forecasts` bucket and distinguishes its replica
from the portal's short rolling retention. Current AWS probes returned HTTP 503
`SlowDown`, including a delayed retry. This does **not** prove historical absence
and does not invalidate the prior completed point backfill. No credentials,
paid historical access or provider-control bypass was attempted.

The portal root listing showed dates September 27–30. Individual availability
still matters: the oldest-cohort August 23 IFS index returned 404; September 27
AIFS 00z indexes returned 404 despite a date directory. September 27 IFS 00z
indexes at all required end hours 6,9,…,30 were present for control and members
1–50, both `mx2t3` and `mn2t3`. These index rows cover KBKF and SBGR's aligned
windows on that date, not full decoded daily fields. September 28 AIFS 6/24h
control/perturbed indexes were present and had no native extreme candidates.
A later portal HTTP 429 stopped further requests from that origin. Unattempted
requests remain explicitly recorded, never relabelled 404 or zero availability.

## Implemented, isolated research boundary

`tools/v11_ecmwf_extrema.py` adds typed product/interval identities, strict JSON
index/range validation, a narrow observed IFS CCSDS native decoder, and adjoining
native-window aggregation. It rejects wrong statistic/height/run/member/time unit,
missing statistical inputs, unsupported release/header/grid/packing, duplicate or
overlapping fields and corrupt CCSDS. It reuses the reviewed bounded full
CCSDS decode/re-encode byte-equality defence. The existing point decoder and point
SQLite stores are unchanged.

Window aggregation requires one run/member/statistic/grid identity and contiguous
whole intervals exactly tiling the requested native window. Gaps, overlaps,
crossing intervals and duplicate windows fail closed. Its output is deliberately
`native_window_extrema_k`; `exact_day_extrema_c` stays null and `learner_admitted`
stays false because endpoint and causal gates are unestablished. There is no
flag to override them and no real stacking/learner/runtime registration.

The anonymous collector has fixed public origins, no ambient authentication,
proxy environment, redirects or cookie reuse; at most 100 requests / 128MiB,
3MiB/12,000-row indexes, 4MiB individual GRIB fields, bounded timeouts, sequential
requests with spacing, and no retry loop. A 429 or 503 halts that origin. Successful
response bytes and receipt metadata are saved separately so a later failure does
not erase them. Source requests are research GETs only. Optional ecCodes remains
in the development interpreter; runtime dependencies were not changed.

New content-addressed raw evidence, sealed capture manifests, per-day coverage,
decoded sample records and deterministic output manifests live under ignored
`private-evidence/r09-extrema/`. Outputs refuse conflicting overwrites. Builders
pin committed executable source bytes/commit/tree, input/capture hashes, ecCodes
and Python versions and timezone files. The first exploratory capture predates
the committed collector; replay manifests identify it as such. New committed-code
captures have a separate collection identity. A hash proves byte identity, not
independent source truth or approval.

## Narrowest alternate contract and remaining gates

The narrowest independently reviewable alternative for all 541 days is the
already preserved **source-native sampled 2m-temperature trajectory predictor**:
ordered provider/run/member/valid-time/grid values and explicit day-relative
coordinates/coverage masks. Predict the separately defined official settlement
HIGH/LOW payout distribution from that trajectory. Do not name the inputs daily
HIGH/LOW, silently interpolate them, backfill future observations, or treat their
sampled max/min as the target itself. Preserve all unavailable rows, splits and
provider/member dependence; compare against GEFS-only on identical admitted rows.
Native IFS interval maxima/minima could be an additional explicit window feature
once archived bytes and availability are established, without claiming every
window equals a calendar day.

This alternative is a review proposal only. No real learner adapter or fit is
implemented because historical forecast receipt/availability, label knowable-time
at each training cutoff, exact rule/revision lineage, independently reviewed
release identity and original point-store GRIB source truth are still missing.
New downloads establish present retrieval only, never historical Alpha knowledge.
Do not mix the observed IFS process-161 header with an independently attested
operational release or treat a source schedule as receipt proof.

Next work is an independent review of this contract/evidence boundary, followed
by a separately bounded archive acquisition after provider availability recovers
and explicit endpoint-semantic evidence if exact-day assembly is still desired.
No champion comparison or promotion claim; no new C/J/E/A credit.
**91/200 (45.5%), formal 1/50; NOT_READY_TO_FUND.**

## Verification checkpoint

Initial native and adversarial suite: 72 passed. Related point-panel/backfill/
model-panel/GRIB regression: 307 passed, 2 expected opt-in skips, 27.85 seconds.
All seven pre-existing input SHA-256 pins still match, including both SQLite
files. Final committed-code capture/reproducibility identities and final test
results are appended after verification; these initial results are not an
independent review.

## Final committed-code verification and public evidence

Final executable commit: `f20d8791f19438b24bc33cda15d2d08ac9374843`;
tree: `a80132c1724f59e360ba9bfe5c28b3dc43e502bd`.
The later evidence-only documentation commit does not change these executable
bytes. Collection commit for the second capture:
`1c8836a8ee7e45a5f544ea48e73cc60c0bb6054f`, tree
`9a73ed4de93b139042b089cc8eb313cd404463b8`.

After backoff, a separate current-only capture used 17 requests / 11,868,660
response bytes: four indexes (HTTP 200) and **12 actual GRIB fields** (HTTP 206),
plus the root listing. Both maximum/minimum parameters were verified at step 3
(range **0–3**) and step 150 (range **144–150**) for control 0 and perturbed
members **1 and 50**. All twelve pass the strict decoder and exact CCSDS byte
round-trip. The six-hour products are really `mx2t6` / `mn2t6`, IDs **121 / 122**,
with the same 450-second processing increment. All 15 frozen station coordinates
were extracted per field in each replay. These September 29 forecasts are outside
the frozen target cohort: **zero historical days are filled by these samples**.
No further AWS request was made in this second capture.

The first capture retained 30 requests / 21,203,504 response bytes. Its 58 planned
index records comprise 22 HTTP 200, five 404, one 503, one 429 and 29 explicitly
unattempted after throttling. The 22 successful indexes are 18 IFS products
(September 27 00z, hours 6,9,…,30, control and perturbed) plus four AIFS products
(September 28 00z, hours 6 and 24, control and perturbed). Index completeness
covers two aligned September 27 days (KBKF, SBGR); **no complete native daily
raw-field backfill was performed**. The remaining 71 aligned days lack complete
index coverage in this capture. Missing raw coverage and unresolved endpoint
semantics preclude exact-day assembly even for the two indexed days.

| Coverage dimension | Count |
| --- | ---: |
| Frozen cohort | 541 station-days / 1,082 HIGH+LOW events |
| IFS three-hour boundary alignment | 73 (TRAIN 48 / DEVELOPMENT 16 / CONFIRMATION 9) |
| IFS unresolvable crossing intervals | 468 |
| Complete IFS member/interval **index** coverage observed | 2 |
| Complete native historical **raw-field** day coverage | 0 |
| Exact-day admitted / causal learner-admitted days | 0 / 0 |
| Native AIFS candidates in four captured cohort indexes | 0 |
| Current IFS representative native fields decoded | 12, outside cohort |

Public metadata, URLs, byte ranges, member identities, response outcomes, hashes
and aggregate results are published in
[`config/v11/r09_ecmwf_extrema_public_evidence.json`](../config/v11/r09_ecmwf_extrema_public_evidence.json).
It includes no forecast values, catalog labels, raw GRIB bytes or private documents.
AIFS's absence claim is scoped to inspected products; the separate IFS-only
capture correctly reports AIFS as NOT_INSPECTED_THIS_CAPTURE.

Two independent output directories per capture reproduce all three output files
byte-for-byte. They are independent executions, **not independent review**.
Local output roots (each has `a/` and `b/`):

- `private-evidence/r09-extrema/verify-capture-v1-f20d8791f194/`
- `private-evidence/r09-extrema/verify-capture-current-1c8836a-f20d8791f194/`

| Artifact | Cohort-index audit SHA-256 | Current native-byte audit SHA-256 |
| --- | --- | --- |
| dataset.json | `fc5eceb546b70fff46bdd0c418d8092c7955488f808eb22c3da7139d73f6945e` | `04810f731a33e59bdb166b9d8414c2cfb8ce0bec74a649598992a4a81fa9942f` |
| result.json | `9717d7d753ee65135cc758728571a0f61a9eb8ced6e59db7efdee1b3efee89a3` | `935b83d4d278b2a0f255064419f6829feecf5774435be22852bb4104f438e89c` |
| manifest.json | `b42030bc4891e4a3c6d9deb2e1280ef90ec43c1f2a750293e5c0e1d25151e4e1` | `8eba7ccb30f15649b735dafadebafef7ce568248c9b0a0b5ec11177cd4e5537b` |

Final focused suite: **77 passed, one expected opt-in skip / 9.93s**. Final
related regression: **312 passed, two expected opt-in skips / 31.06s**. The
explicit real-capture tests then passed separately: cohort **1 passed / 15.50s**,
current native fields **1 passed / 35.20s**. Counts overlap; do not sum them as
unique tests. Compilation, `pip check`, whitespace and staged secret/private
material inspection pass. All original seven file pins, including both complete
point-temperature SQLite databases, match before and after the work.

Reproduction from this worktree (read-only raw captures must be available):

```bash
PY=/home/alphaadmin/AlphaV11_Dev/venv/bin/python
COMMIT=f20d8791f19438b24bc33cda15d2d08ac9374843
$PY -m pytest -q -p no:cacheprovider --tb=short \
  tests/test_v11_ecmwf_extrema.py tests/test_v11_multimodel_panel.py \
  tests/test_v11_ecmwf_historical_backfill.py tests/test_v11_model_panel.py \
  tests/test_v11_grib_fields.py

ALPHA_R09_EXTREMA_REAL=1 ALPHA_R09_EXTREMA_CODE_COMMIT=$COMMIT \
  ALPHA_R09_EXTREMA_CAPTURE=private-evidence/r09-extrema/capture-v1 \
  $PY -m pytest -q -p no:cacheprovider --tb=short \
  tests/test_v11_ecmwf_extrema.py::test_real_capture_reproducible
ALPHA_R09_EXTREMA_REAL=1 ALPHA_R09_EXTREMA_CODE_COMMIT=$COMMIT \
  ALPHA_R09_EXTREMA_CAPTURE=private-evidence/r09-extrema/capture-current-1c8836a \
  $PY -m pytest -q -p no:cacheprovider --tb=short \
  tests/test_v11_ecmwf_extrema.py::test_real_capture_reproducible
```

Logs are `private-evidence/r09-extrema/{hardened-focused,final-related-tests,
final-replay-cohort,final-replay-current}.log`. Re-fetching public URLs later need
not reproduce retention, HTTP outcomes or bytes; the deterministic claim concerns
replay of these preserved captures. The collector offers `collect --probe-only
--code-commit <matching-commit> --output <new-private-directory>` for a separately
bounded future public capture. It must not be used as a loop around throttling.

The task yields a verified, fail-closed feasibility result and a native decoder,
not a completed 541-day native backfill. Full history remains unverified under
current provider throttling, 468 days are geometrically incompatible, 73 need
both raw coverage and endpoint proof, and every causal learning gate above still
applies. No real adapter, fit, calibration, champion comparison or promotion.

R09_EXTREMA_READY_FOR_INDEPENDENT_REVIEW
