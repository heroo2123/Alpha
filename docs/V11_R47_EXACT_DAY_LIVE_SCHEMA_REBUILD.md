# V11 R47 exact-day / live-schema research rebuild — 2026-09-30

## Scope and authority

This is research/shadow evidence only. It does not approve a champion, write protected model-authority state, grant financial authority, authorize orders, or prove profitability/calibration. The resulting candidates remain FITTED_NOT_CALIBRATED, NO_PROMOTION, host_approved=false, financial_authority=false, and order_authority=false.

## Why a rebuild was required

The first artifact-backed retrospective candidates used model id gefs31. The deployed run-bound GEFS source uses NOAA_GEFS_0P50_LINEAR_DAY_V1, so the ordinary bundle contract correctly rejected natural live-shaped input.

A model-id rename was also not sufficient. The first all-market historical pipeline calculated each member daily high/low as a max/min across all 3-hour bracketing snapshots. The deployed gefs_sources.linear_extreme() clips the piecewise-linear path to the exact local-day interval, interpolating the local-midnight boundaries and excluding out-of-window bracket extrema. Those semantics differ whenever a local-day boundary is not on the three-hour grid.

No alias/remap was installed.

## Corrected historical calculation

Coordinator research script:
/home/alphaadmin/AlphaV11_BrainWork/build_exact_day_live_schema_bundles_20260930.py

Script SHA-256:
38e3650e65508a7889255ba5ef5737f4c3a0be9695cfe85d271bbc459b6b5336

Inputs were the already-preserved 541-station-day NOAA GEFS backfill, frozen historical plan/catalog, and the predeclared train/development/historical-confirmation split. No new market or weather observations were fetched.

The correction re-ran each of the 31 member paths through the same linear_extreme() implementation used by the deployed scanner.

Boundary correction impact:
- HIGH: 16,771 member paths; 236 changed; max correction 1.6140950521 C; mean 0.0068779418 C.
- LOW: 16,771 member paths; 3,673 changed; max correction 3.1454264323 C; mean 0.1085556868 C.

This proves the original snapshot-max/min representation was not live-equivalent and must not be commissioned.

## Corrected fit result

After recomputation, both families still selected bias 0.0 C and kernel sigma 0.5 C. Both still passed the frozen research comparison on DEVELOPMENT (120 city-days) and HISTORICAL_CONFIRMATION (64 city-days). The historical-confirmation slice is explicitly not a new forward untouched holdout.

Corrected dataset SHA-256:
649fd39acab88dd2508c34668f37228b173b8d5902c93f59f2b75f50bbf990a9

## Current live-schema research bundles

Private artifact store:
/home/alphaadmin/AlphaV11_BrainWork/exact_day_live_schema_bundles_20260930/objects

All bundles target FINAL_CONTRACT_PAYOUT and use live model id NOAA_GEFS_0P50_LINEAR_DAY_V1 with exactly 31 members.

- HIGH / C: a7b8c839a85595f3ea58d5c6f84c26403b73e71aafdacb1521d3ad51860c224d — sigma 0.5 C.
- HIGH / F: ad72639cad997a1a3c8850fc0da44ab8f5bab517a36f9c7e657fdad63eb575ca — sigma 0.9 F.
- LOW / C: c2b8718ebf86b8f845d49e719685b2f97a2ef8d3ac2ca2809a9a469957356e54 — sigma 0.5 C.
- LOW / F: 8feed17660068f11fe4d692dc1ad7ce02da15e1f33eff5ab96050725b52bd02c — sigma 0.9 F.

Commissioning evidence:
/home/alphaadmin/AlphaV11_Commissioning/evidence/v11_gefs_exact_day_live_schema_bundle_manifest_20260930.json

File SHA-256: 8d4b4a93ed34b64dcc34e9c16565c2b3856e2b08982d2650d4c61c1e79212e57
Canonical manifest artifact SHA-256: 8645055c9258476312d65ce34f07270ef0e81045da50977328b4f1947c63bf55

## Live-schema compatibility evidence

Development gefs_sources.py SHA-256:
7d02d473d17647ece47f79a7ceb29b80e1a031c1cabef6d8eb3640f156e30df5

Deployed release ac3b722b39744ce58295d88a9998e38f81bcfaf9 gefs_sources.py SHA-256:
7d02d473d17647ece47f79a7ceb29b80e1a031c1cabef6d8eb3640f156e30df5

They are byte-for-byte identical.

Each candidate was independently pinned and validated with ArtifactStore / PinnedBundle, required by ForecastFeatureContract using NOAA_GEFS_0P50_LINEAR_DAY_V1 and 31 members, and exercised through predict_with_bundle(). Each probability vector summed to 1 and retained financial_authority=false.

A 1,000-vector per-family Celsius/Fahrenheit affine check produced worst absolute differences of 4.440892098500626e-16 for HIGH and 5.551115123125783e-16 for LOW, i.e. floating-point rounding noise.

The running alpha-weather-scanner.service was active with PID 514629 and zero restarts at verification. This is source/artifact compatibility evidence; it is not a claim that a protected champion pointer has been installed or that a live forward shadow sample has accumulated.

## Regression and remaining boundary

After accepting the isolated R47 harness, the V11 development environment ran the new harness, GEFS source, and model-artifact suites: 77 passed.

Closed locally: real artifact-backed isolated research injection; exact deployed model-id/member-width contract for corrected candidates; exact-day feature mismatch identified and corrected; C/F schema and probability-transform compatibility.

Still open: independent/owner review; root-owned model-authority installation/reviewed shadow pointer; actual forward current-input shadow evidence and frozen evidence-based sample target; calibration/promotion evidence; execution-cost evidence; every financial/live readiness gate.

Therefore R47 remains OPEN and Alpha remains NOT_READY_TO_FUND.
