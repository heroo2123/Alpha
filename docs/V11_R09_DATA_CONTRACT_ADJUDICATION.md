# R09 IFS/AIFS data-contract adjudication — 2026-09-30

Decision by Astra/high on main `7ff539e`: pursue a separately versioned
**source-native sampled-trajectory predictor** for R09. The existing evidence
cannot support an exact-local-day multi-model fit. This resolves the architecture
route, not learner admission or model acceptance. Independent review of this new
contract is required before a real adapter or fit. Until then R09 remains
**NOT_FITTED / NOT_CALIBRATED / NO_PROMOTION**, with zero admitted real examples.
No C/J/E/A change: **91/200 (45.5%), formal 1/50; NOT_READY_TO_FUND**.

The authoritative private master was consulted and its SHA-256 verified as
`a0e16d9bd7344c943a54a16a53c6757662363d93642f6e5cb7953cd047659b4a`.
This decision preserves its raw-evidence, causal-learning, settlement-identity,
validation and authority requirements. It does not amend the settlement target.

## Evidence and exact-day verdict

The [reviewed point-panel evidence](V11_R09_MULTIMODEL_HISTORICAL_PANEL.md) and
[native-extrema evidence](V11_R09_ECMWF_NATIVE_EXTREMA.md), including
`config/v11/r09_ecmwf_extrema_public_evidence.json`, establish:

| Question | Adjudication |
| --- | --- |
| Exact extrema from retained instantaneous points? | Not identifiable between samples, even with boundary points. |
| Native IFS intervals for the frozen cohort? | 73 aligned station-days; 468 have crossing intervals that cannot be split. |
| Evidence for the 73 aligned days? | Two days have complete index coverage; zero have complete raw-field day coverage. Endpoint equivalence and causal evidence remain missing. |
| Native AIFS extrema? | None in the four inspected AIFS products. No all-products/all-history absence claim. |
| Can an archive download alone close this? | No. It cannot recover subinterval information or historical Alpha receipt, label/rule provenance, or AIFS fields absent from a product. |
| Admitted exact-day / causal days now? | Zero / zero. |

Inference: source-native exact-day evidence is **not attainable from the inspected
products for the full frozen cohort**, even if their archive became fully
accessible. A restricted IFS-only aligned cohort might become feasible with new
raw coverage and endpoint proof, but would be a separately preregistered study,
not the existing 541-day GEFS/IFS/AIFS comparison. Retain the 73-day feasibility
result; do not select that subset after seeing outcomes or drop the other days.

This is not a claim that every possible future source is inadequate. Reopen the
exact-day route only for materially new evidence: sufficient time-resolved native
fields or exact windows for each target/provider, explicit endpoint semantics,
complete raw coverage/member identity, reviewed release binding, and causal
source/rule/label lineage. More retries of the same interval products do not meet
that condition. Native model extrema also remain model-grid statistics, not
observed official-station or continuous physical extrema.

The current [ECMWF product documentation](https://www.ecmwf.int/en/forecasts/datasets/open-data)
was checked on 2026-09-30: it lists IFS interval-temperature extrema, six-hourly
AIFS ENS output and point `2t`, without AIFS temperature extrema in that list.
Publication schedules describe expected release, not Alpha receipt. The
[GRIB time-interval table](https://codes.ecmwf.int/grib/format/grib2/ctables/4/11/)
describes processing increments; it does not establish the target's half-open
calendar-day endpoint equivalence. These documentation checks are not new raw
captures or independent operational-release attestation. No data-origin retry
was performed, and earlier 429/503 outcomes remain preserved.

## Selected predictor and target contract

Proposed contract identity: `R09_NATIVE_2T_TRAJECTORY_V1`. This must be a distinct
feature/model identity; never alias it to `EXACT_LOCAL_DAY_EXTREME`, the live GEFS
piecewise-linear contract, or a legacy model bundle.

Inputs are ordered native instantaneous 2m-temperature samples, indexed by
provider, source release, initialization, member, forecast hour/valid UTC,
parameter/units, grid and extraction identity. Preserve original units and exact
conversion provenance, native cadence, UTC/local-day coordinates, run age,
station/grid displacement, expected member/hour sets, and coverage masks.
Use IANA timezone rules and pinned timezone data for 23/25-hour and fractional-
offset days. No interpolation, extrapolation, temporal resampling, duplicate
repair, member replication, or substitution of observed temperatures for forecasts.

A forecast's valid time may be later than the decision: this is normal forecast
information, provided its bytes were available before the decision. The schema
may retain native forecast points outside the target day with explicit relative
time coordinates. They must not be counted as in-day observations or extrema.
Legitimate geometry/cadence gaps differ from a missing expected message: the
latter gates that provider/example. Masks cannot conceal incomplete downloads.
Missing providers stay visible; any fallback requires a separately frozen policy
and separate reporting, not silent pool-weight renormalization.

Targets remain the official settlement HIGH/LOW payout vectors for the exact
station/event, rule fingerprint, local day `[midnight, next midnight)`, unit,
rounding and bucket partition. Forecast trajectories are predictors of those
outcomes; sampled maxima/minima are not outcomes or native exact-day extremes.
V1 consumes the trajectory directly. Adding sampled-extreme summaries or native
IFS interval features later requires a separately versioned feature review.

Do not feed point temperatures straight into the existing synthetic
`member_probabilities` scenario API as though each were a daily extreme. A later
reviewed model must learn an explicit trajectory-to-target probability mapping
with coherent full-bucket probabilities, uncertainty and sparse-data fallback.
The algorithm and finite search space are frozen before evaluation; this decision
does not select a fit or certify calibration. IFS and AIFS share an ECMWF lineage
budget, with a separate GEFS budget; neither providers nor ensemble members are
independent outcome trials. Leave existing R09 synthetic evaluator gates intact.

## Causal clocks and admission predicates

All timestamps require explicit UTC, origin, uncertainty/clock-health evidence
and immutable evidence links. Unknown is not zero or initialization time.

| Clock / identity | Required meaning |
| --- | --- |
| `run_initialized_at`, `valid_at` | Model initialization and predicted time, neither an availability attestation. |
| `source_published_at` | Optional attested publication of these exact bytes; a schedule or Last-Modified header alone is insufficient. |
| `response_completed_at` | Actual local receipt of the complete referenced response bytes. Preserve request start/end and HTTP outcome. |
| `feature_ready_at` | Time all required bytes, metadata, decoding and validation were available to Alpha; includes complete-member and rule/station dependencies. |
| `decision_at` | Preregistered decision cutoff, distinct from later replay time. |
| `prediction_frozen_at` | Actual immutable prediction/artifact persistence before the prospective target outcome is knowable. |
| `label_knowable_at` | Earliest evidenced availability to Alpha of the specific admissible outcome/rule revision, not the weather-day end or catalog creation time. |
| `fit_cutoff`, `selection_freeze_at`, `evaluation_asof` | Separate training, final model-selection and scoring clocks; never one global cutoff for all splits. |

For an as-of feature example require every feature dependency's conservative
availability upper bound <= `decision_at`. Clock uncertainty crossing that cutoff
fails. Historical public availability is not historical Alpha knowledge. Later
retrieval gives present receipt only; retrospective availability may be established
only by independently reviewed contemporaneous evidence tied to identical bytes.
Keep separate fields for external availability and local knowledge.

For TRAIN, label versions used by fitting must be knowable by that fit cutoff.
For DEVELOPMENT, labels used for selection/calibration must be knowable before
selection freezes. Require frozen trained parameters before each held-out
prediction they claim to simulate; use embargoes where label delay overlaps the
next split. CONFIRMATION labels can become knowable after model freeze and are
read only at scoring as-of; they cannot influence fit, features, thresholds or
candidate selection. A later correction is a new append-only label version and
explicit dataset rebuild, never a rewrite of the old example. Pending/disputed
labels remain gated under the existing finality rules.

The old 541-day plan retains its fixed initialization, splits and all unavailable
rows. Never choose a later run to repair it. New prospective collection needs a
new preregistration: station/date cohort, decision schedule, provider run-selection
rule, finite freshness/lookback limits, expected fields/members, outage policy and
clock tolerance. Select only a complete run actually ready by the cutoff. For
FUTURE_FORECAST the decision and frozen prediction must precede local-day start.
Provider runs may differ only under the frozen policy, with explicit age features.
No protocol or missing limit means no admission.

## Immutable capture and preserved historical evidence

Keep both completed SQLite stores and seven input pins unchanged. Their hashes
verify identity, not missing source truth or causality. Preserve all 541 days and
357/120/64 split counts; they remain historical diagnostic evidence with zero
causal admissions. Do not refit merely by changing their semantic label.

New captures go to a new private, content-addressed store. Save raw indexes and
exact GRIB response bytes, URLs/ranges, status and receipt records, response/header
identity, parameter/run/member/grid metadata, extraction/decoder identity and
provider-release attestation separately. Pin code commit/tree, schema/policy,
dependencies, timezone files and capture manifest. Refuse conflicting overwrite;
seal atomically only after complete validation, retain partial/failure manifests,
and verify bytes and decoded metadata independently. A hash chain supplies
integrity, not trusted receipt timing or host approval; clock and provenance review
remain mandatory. Do not copy private data into Git.

Capture contemporaneous market/rule/station metadata separately from later
versioned labels. The rule fingerprint in a prediction must be the rule available
at decision time; later source/rule changes quarantine or version the evaluation
under reviewed policy. A complete one-hot payout catalog alone proves none of
this. Anonymous public acquisition remains bounded and sequential, with fixed
approved origins, no ambient credentials, and stop/backoff on provider throttling.
No replica/origin switching to evade an active provider restriction.

## Independent review and evidence gates

1. **Contract review:** an independent model reviews this exact committed decision,
   the private master in place, both evidence documents and existing source gates.
   Reproduce temporal/geometry counterexamples. Record exact commit/tree, findings,
   PASS/CHANGES_REQUIRED and terminal completion. A missing/conflicting terminal
   is not acceptance. This decision is not its own independent review.
2. **Offline implementation:** build a separate typed trajectory/capture contract
   and admission validator with synthetic adversarial cases first. No changes to
   real-admission flags in existing panel/extrema/stacking APIs. Test late partial
   receipt, missing expected members, future-valid but already-received forecasts,
   initialization-as-availability fraud, timezone boundaries, delayed/corrected
   labels, rule drift, duplicate city-days, clock uncertainty and immutable replay.
   Independently review exact implementation and verify compatibility with main.
3. **New causal corpus:** independently review release/source identity and bounded
   collection protocol, then collect actual raw evidence and contemporaneous
   metadata. Report coverage and refusals over the full frozen requested cohort.
   Prospective capture alone is not a trained-model forward prediction. Old
   noncausal examples remain excluded from real training; this may require waiting
   for new labels, while GEFS commissioning continues independently.
4. **Real adapter and fit:** only after gates 1–3 establish real eligible examples,
   independently review the distinct adapter. Preregister training/selection/
   confirmation dates, city-day grouping, finite search, all attempted candidates,
   baseline/ablations, primary metrics and numeric tolerances before scoring.
   Compare GEFS-only, IFS-only, AIFS-only and grouped pool on identical eligible
   examples; report full-cohort coverage and fallback separately. Preserve original
   split identities; previously inspected confirmation is not untouched evidence.
   Probability quality does not establish execution profitability.
5. **Forward evidence:** preregister numeric minimum city-day/date-block and
   station/family/horizon coverage targets, uncertainty/precision and regression
   criteria, missingness limits, stopping/repeated-testing rules and a fixed model
   before starting its evaluation window. Values require evidence and independent
   review; unset values block acceptance, and no arbitrary new long PAPER period
   is imposed. Freeze real predictions and full inputs before their target day,
   join later versioned official labels, and score only after maturation. Replays,
   watchdog ticks, synthetic inputs and repeated snapshots earn no sample credit.
   Shared weather/date dependence must be reported; date blocks are not presumed
   independent. Model changes start a new declared cohort. Root-custodied SHADOW
   admission and all financial/host authority gates remain separate and unchanged.

## Next bounded action

Obtain independent cross-model review of this document in an isolated worktree;
no implementation, fit, data acquisition or service action in that review. If it
passes, route normal substantive implementation of gate 2 to Sonnet/high, followed
by independent exact-commit review. If findings require an architecture decision,
return them to Astra/high with the concrete counterexample. Keep the existing GEFS
release diagnostic running and the reviewed SHADOW integration candidate unmerged
until its release/integration gates are actually resolved.

## Verification of this adjudication

On 2026-09-30 at 12:24 UTC, all seven original file-hash pins matched, including
both preserved SQLite stores. Existing geometry/causal-boundary tests: **34 passed,
103 deselected / 0.53 s** using the two R09 test files and the selection below:

```text
point_samples_cannot or complete_window_is_only or window_cannot_split or
no_aifs_point or filtering_plan or local_calendar or dst_and_fractional or
sampled_extreme_excludes or evaluator_causal or confirmation_label
```

These tests substantiate the existing rejection boundaries, not implementation
or acceptance of the new contract. No executable code or gate was changed.
Input-document SHA-256 identities for the independent reviewer:

- Historical panel: `c67fdf127ca4bf178b844a645328c1de0e227436456e16be34e9e2966eda56ac`.
- Native extrema: `74db60b1314318bc2e7bda9e2b97bee7575dc8afa7ca9f09014c6c9959587ea6`.
- Public extrema evidence: `6fd3fc1defd4b9d10459903a9543c3118cbc34e27e3665588463d68140cb6f11`.
