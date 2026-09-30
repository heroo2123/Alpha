# V11 R47 exact-day / live-schema research rebuild — 2026-09-30

## R47 v2 same-worktree provenance recovery — 2026-09-30

The interrupted builder's v2 output is **REVIEW_REJECTED**, preserved unchanged as
lineage. Its recorded parent commit did not contain the generator, and its
preregistration-based creation timestamp preceded the completed dataset. The prior
unverified broad-regression claim is superseded by the captured foreground result
below. Historical notes about that rejected attempt are retained and explicitly
marked; they are not acceptance evidence for the final candidate.

Code commit A: `937c968e3edcedc6259c3e8602a29f709a39a668`; exact code tree:
`8a39768a337a38c45d832305de8fa1cdc7340439`. The generator/test code was committed before either final
artifact build. Generator SHA-256:
`0bcf4baade662bd548c7643309ab0408dfd3ea19a776a47c166baaae0d215e9b`.

The generator checks the committed generator bytes, the complete package Python
source set, and requirements file against A/tree; it also verifies dependency and
runtime digests. It refuses an absent generator, wrong tree, changed executable
bytes, changed pinned dataset snapshot, a code timestamp before dataset completion,
ambiguous UTC timestamps, or an existing output directory.

Model `created_at` is the committed generator's committer time,
**2026-09-30T07:03:19+00:00**. This is deterministic code-lineage time, not a claim of
physical execution time. The distinct `causal_watermark` is completed dataset time,
**2026-09-29T13:36:42.402636+00:00** (epoch `1790689002.402636`).
It records frozen evidence completion only: **historical availability and forward
proof are NOT established**. The entire completed source dataset-manifest snapshot
is retained in the private output, with canonical SHA-256
`42efd3f075aceb6ab7d2df8f4a0b2d79598bd997b4b090643264525255895c92`; original source file SHA-256
`c9f2909938a5dabdf856fcd096a04ea843bc0913dffd811222f8fab4bc7a71ff`.
The fitted components' `dataset_sha256` now binds corrected dataset digest,
source-manifest snapshot digest, completed watermark, and those availability limits:
`bbd1d20734f4f0e83c215255d0f3e67ffb0c601694256ef3fc7ad032a9e09478`.
The corrected dataset content digest remains
`649fd39acab88dd2508c34668f37228b173b8d5902c93f59f2b75f50bbf990a9`.

Final candidate artifact root (private, Git-ignored, inside this worktree):
`/home/alphaadmin/AlphaV11_R47V2/Alpha/private-evidence/r47-v2-937c968e3edc`.
Repeat root: `/home/alphaadmin/AlphaV11_R47V2/Alpha/private-evidence/r47-v2-937c968e3edc-repeat`.
All **37 files are byte-identical** across the two foreground builds from exact A/tree.
Canonical manifest artifact SHA-256:
`b9bcd07f252ab38b4a1e6b7c0b2f932fb5968e1bdb5f673e9152e213d27fa957`.
Manifest file SHA-256: `ac1f2026d464e9e226944e193b9d1439a7ec46d998bff395b6fadd57ec1e580b`.

| Candidate | Final bundle SHA-256 |
| --- | --- |
| HIGH / C | `005d661fab697e0d9075a0e80cd54f64cd931b8ca26d8fc50a807c4793c7d7a5` |
| HIGH / F | `fd9a32aa12ac7c8018ec524d1b74514bb57c42c0e60241672a4446b95908e641` |
| LOW / C | `b9a067c9030f41740789a5761d9d4ee68d88192935eda6569d093bc2863887b8` |
| LOW / F | `e5478c88dc7aeca03f486efc845d94c2e2e5c8900f6775d7d0840191e83f3c6d` |

All four are unique and disjoint from every v1 and rejected-v2 candidate hash.
All eight explicit HIGH/LOW × parent/fit × C/F run IDs are unique. The live model
ID remains `NOAA_GEFS_0P50_LINEAR_DAY_V1`, with 31 members, exact `linear_extreme`
local-day clipping and `FINAL_CONTRACT_PAYOUT`; source SHA-256 still matches the
recorded deployed commit `ac3b722b39744ce58295d88a9998e38f81bcfaf9`:
`7d02d473d17647ece47f79a7ceb29b80e1a031c1cabef6d8eb3640f156e30df5`.
Both families still select bias 0.0 C / sigma 0.5 C. The final real bundles were
independently pinned and exercised through `predict_with_bundle` for all four
family/unit contracts. Calibration stays UNCALIBRATED/VACUOUS_BOUNDS; execution
cost evidence stays UNKNOWN.

### Preserved rejected-artifact lineage

Rejected root: `/home/alphaadmin/AlphaV11_BrainWork/exact_day_live_schema_bundles_v2_20260930`.
All 37 original files were hashed before/after recovery and are unchanged.
Its canonical manifest hash is
`30a2fa77a07408b7f87fbe275ed9e3da0ff872666c7fad715323a79dbde69d0d`;
file SHA-256 is `90f1167247e0891327da4d8f57923e5f9562e714a89bd04a3c678cb41da977ce`.
It incorrectly names commit `7bb4f27715a5cbbd6ed1be8d66b029e2db0e06bd` /
tree `0d7a33ccd7b1c3c7b5fac28efddc1b01d8129fde`, before the generator existed.
Its frozen preregistration timestamp predates dataset completion. Its four bundle
hashes are recorded in final manifest `review_rejected_v2_lineage` and retained
in the rejected-attempt section below. Neither rejected output nor its commissioning
copy was overwritten, deleted, aliased, installed, or reused.

### Captured foreground validation

Interpreter for all pytest and real generation commands:
`/home/alphaadmin/AlphaV11_Dev/venv/bin/python`.
Exact focused command:

```sh
/home/alphaadmin/AlphaV11_Dev/venv/bin/python -m pytest -q -p no:cacheprovider --tb=short --maxfail=2 tests/test_gefs_exact_day_live_schema_bundle_v2.py
```

**22 passed, no skips / 99.01 s; exit 0.** Includes both real-evidence cases.
The fast preliminary subset had 20 passed / 17.90 s, with two real cases deselected;
the full result above validates the final source and supersedes that preliminary run.
Exact relevant regression command:

```sh
/home/alphaadmin/AlphaV11_Dev/venv/bin/python -m pytest -q -p no:cacheprovider --tb=short --maxfail=2 tests/test_v11_gefs_sources.py tests/test_gefs_schema_rebind.py tests/test_v11_r47_real_candidate_shadow_injection.py tests/test_v11_offline_learning.py tests/test_v11_forecast_learning.py tests/test_v11_model_artifacts.py tests/test_v11_model_governance.py tests/test_v11_probability.py
```

**181 passed, no skips / 68.75 s; exit 0.** Both foreground terminal exits and logs
were inspected. No detached/background test process was used. `py_compile` on both
new files and `git diff --check` passed. The 329 generator/test/package/requirements
input hashes were checked unchanged after generation. No broad shared production
code changed, so no full-suite claim is made.

Private retained logs/audit: `private-evidence/r47-v2-recovery-validation/`.

- `focused.log` SHA-256: `1738600d03abda33c65b63a2ce9971c2d510b9dfdc5e9ec95c2671188440f0a6`.
- `regression.log` SHA-256: `52b22ac776e25d106d98b3b0fbbef543b5a0a1f385360b2339841086d53f09a2`.
- `final-verification.json` SHA-256: `c04421af9925940b21097399eb05cdade5f12e91783e4eb7ce7b66f4c161512d`.
- `verify_final.py` SHA-256: `2e4b7bedc48a15d1237570434690b7611d277c35dc9e9ed474b502d52722e06d`.
- `tested-inputs.json` SHA-256: `faf54b895c63a73d2e6068ec81a2a0e074ffaad781fef7f40e184f4c7300db0b`.

Exact final generation command (second run changes only output root to the `-repeat` suffix):

```sh
/home/alphaadmin/AlphaV11_Dev/venv/bin/python tools/gefs_exact_day_live_schema_bundle_v2.py \
  --plan /home/alphaadmin/AlphaV11_Commissioning/evidence/v11_brain_historical_backfill_plan_20260929.json \
  --catalog /home/alphaadmin/AlphaV11_Commissioning/evidence/v11_historical_daily_temperature_catalog_20260929.json \
  --preregistration /home/alphaadmin/AlphaV11_Commissioning/evidence/v11_all_market_gefs_preregistration_20260929.json \
  --old-dataset-manifest /home/alphaadmin/AlphaV11_Commissioning/evidence/v11_gefs_all_market_dataset_manifest_20260929.json \
  --dataset-manifest-sha256 42efd3f075aceb6ab7d2df8f4a0b2d79598bd997b4b090643264525255895c92 \
  --gefs-db /home/alphaadmin/AlphaV11_BrainWork/historical_gefs_backfill.sqlite \
  --output-root private-evidence/r47-v2-937c968e3edc \
  --code-commit 937c968e3edcedc6259c3e8602a29f709a39a668 \
  --code-tree 8a39768a337a38c45d832305de8fa1cdc7340439 \
  --v1-manifest /home/alphaadmin/AlphaV11_BrainWork/exact_day_live_schema_bundles_20260930/manifest.json \
  --rejected-v2-manifest /home/alphaadmin/AlphaV11_BrainWork/exact_day_live_schema_bundles_v2_20260930/manifest.json
```

### Acceptance boundary and next action

This recovery accepts only local provenance/reproducibility repair for independent
review. `FITTED_NOT_CALIBRATED`, `NO_PROMOTION`, and
`historical_confirmation_is_forward_holdout=false` remain explicit. All financial,
promotion, order and host-approval flags remain false. No historical availability,
forward untouched evidence, champion acceptance or protected installation is claimed.

R47 remains OPEN: independent/owner review and acceptance; protected model-authority
installation/reviewed shadow pointer; actual forward shadow evidence and frozen
sample target; calibration/promotion evidence; execution-cost evidence. No new
C/J/E/A: **91/200 = 45.5%; formal 1/50 (2%)**, unchanged. **NOT_READY_TO_FUND**.
No V10, service, credential, funding, execution/order, or protected model-state change.
Next: independently review exact code commit A and these new candidate artifacts;
owner/host commissioning remains a separate gated action after review.


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

## Original draft-v1 live-schema research bundles

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


## Fail-closed alias regression and draft-v1 provenance note

The repository now also contains tools/gefs_schema_rebind.py plus tests/test_gefs_schema_rebind.py. The tool proves contract-shape equivalence separately from member-value semantic equivalence and refuses the real historical evidence with GEFS31_LIVE_MEMBER_SEMANTICS_METHOD_MISMATCH. Its focused suite passes 16/16. This permanently guards against treating a model-id rename as a compatibility fix.

The four bundle hashes above are retained as draft research evidence for the corrected feature/model/unit compatibility tests only. They are not eligible for owner review because their first one-shot generator used a wall-clock provenance timestamp and shortened the family suffix to the identical string "ture" for both HIGH and LOW run IDs. Those issues do not alter predictions, but they weaken exact provenance/reproducibility. A deterministic v2 rebuild with a frozen creation timestamp and unique family run IDs is required before any protected model-authority review/install step. No v1 candidate may be promoted or installed.

## Rejected v2 attempt — preserved interrupted-builder notes

> REVIEW_REJECTED: the following original notes are retained as lineage only.
> Their claimed provenance closure is invalid; their broad-regression terminal
> result was not captured. The final recovery section above supersedes those
> claims and identifies the only current candidate hashes.


The v1 defects above are now fixed by a new, committed, tested generator: `tools/gefs_exact_day_live_schema_bundle_v2.py` (see its module docstring for full detail), with `tests/test_gefs_exact_day_live_schema_bundle_v2.py` (14 focused cases, all real-evidence-gated cases run and pass, none skipped on this machine).

Fixes, both independently verified:
- `created_at`/`causal_watermark` are now derived from the source preregistration file's own `created_utc` field (`2026-09-29T09:57:56.773625+00:00`, epoch `1790503076.773625`) instead of `time.time()`. This value is frozen and immutable: it is covered by `v11_all_market_gefs_preregistration_20260929.json`'s own verified `artifact_sha256` self-hash, and it predates the frozen research grid's execution. Repeating generation at different wall-clock times produces byte-identical `created_utc`/`created_at`/`causal_watermark` (test: `test_created_at_is_frozen_from_preregistration_not_wall_clock`).
- Every `run_id` now uses an explicit `HIGH`/`LOW` token (`gefs-exact-day-v2-{parent,fit}-{HIGH,LOW}-{C,F}`), never a family-name slice. `"daily_high_temperature"[-4:] == "daily_low_temperature"[-4:] == "ture"` is asserted directly as a regression (`test_v1_defect_reproduced_family_name_suffix_collision`), and all eight HIGH/LOW x parent/fit x C/F run identities are asserted pairwise distinct (`test_high_and_low_run_identities_never_collide`).

Independently recomputed (never merely trusted) source hashes:
- `v11_brain_historical_backfill_plan_20260929.json`, `v11_historical_daily_temperature_catalog_20260929.json`, and `v11_all_market_gefs_preregistration_20260929.json` each pass a fresh self-hash recomputation (`digest(body without artifact_sha256) == artifact_sha256`) and their declared cross-links (`plan.catalog_sha256`, `preregistration.brain_plan_sha256`/`catalog_sha256`) are checked, not assumed.
- The raw 541-station-day NOAA GEFS SQLite database's content hash was independently recomputed with the exact same table/column/order specification as the original backfill pipeline's `database_content_sha256` (`post_gefs_pipeline.py`) and matches the previously recorded `gefs_database_content_sha256` exactly: `96a40834219a1eb6b1622e3f2438a7e59a6734bd4b73cb5273e058141f80c494`.
- `polymarket_scanner/v11/gefs_sources.py` at this worktree's HEAD is confirmed byte-identical (SHA-256 `7d02d473d17647ece47f79a7ceb29b80e1a031c1cabef6d8eb3640f156e30df5`) to the deployed release commit `ac3b722b39744ce58295d88a9998e38f81bcfaf9`, which is a real ancestor commit reachable in this worktree's history (not merely asserted).

Recomputed corrected dataset: the dataset-construction algorithm itself (exact-local-day `linear_extreme` boundary clipping) is unchanged from the v1 draft, only its provenance wrapping changed, so an independent recomputation reproduces the exact same dataset hash already published above: `649fd39acab88dd2508c34668f37228b173b8d5902c93f59f2b75f50bbf990a9` (16,771 member paths per family, matching the boundary-correction counts already recorded). This cross-checks the dataset-construction step's determinism independently of the provenance fixes.

v2 candidate bundles (live model id `NOAA_GEFS_0P50_LINEAR_DAY_V1`, 31 members, `FINAL_CONTRACT_PAYOUT`, both families still select bias 0.0 C / sigma 0.5 C):
- HIGH / C: `bdf43db4ada1a45e277028dade3cfbeb330cfbdf2b4763174076898da6fb65f7` — run ids `gefs-exact-day-v2-parent-HIGH-C` / `gefs-exact-day-v2-fit-HIGH-C`.
- HIGH / F: `1d54cd137f8028d8a94080ed2c677a150dbbf8a0440a7558946587a8cd6e2cfb` — run ids `gefs-exact-day-v2-parent-HIGH-F` / `gefs-exact-day-v2-fit-HIGH-F`.
- LOW / C: `7f026f52a1ab41d518d87e6098b585978df85d5ac6e1249e8e4badf56e867c86` — run ids `gefs-exact-day-v2-parent-LOW-C` / `gefs-exact-day-v2-fit-LOW-C`.
- LOW / F: `ed4cd08f69829956591da3c2302641d7902c55cdff6d5a4a35b1a802042d5ac5` — run ids `gefs-exact-day-v2-parent-LOW-F` / `gefs-exact-day-v2-fit-LOW-F`.

All four v2 candidate hashes are confirmed disjoint from the four v1 draft hashes (`a7b8c839...`, `ad72639c...`, `c2b8718e...`, `8feed176...`): the v1 candidates are never reused, aliased, or installed by this generator (test: `test_real_evidence_end_to_end_generation_selects_documented_parameters_and_never_reuses_v1_hashes`).

Private artifact store: `/home/alphaadmin/AlphaV11_BrainWork/exact_day_live_schema_bundles_v2_20260930/objects` (mode 0700; each object mode 0400).
Manifest: `/home/alphaadmin/AlphaV11_BrainWork/exact_day_live_schema_bundles_v2_20260930/manifest.json`, `artifact_sha256` = `30a2fa77a07408b7f87fbe275ed9e3da0ff872666c7fad715323a79dbde69d0d`.
Commissioning evidence copy: `/home/alphaadmin/AlphaV11_Commissioning/evidence/v11_gefs_exact_day_live_schema_bundle_manifest_v2_20260930.json`, file SHA-256 `90f1167247e0891327da4d8f57923e5f9562e714a89bd04a3c678cb41da977ce`.
Generated with `--code-commit 7bb4f27715a5cbbd6ed1be8d66b029e2db0e06bd --code-tree 0d7a33ccd7b1c3c7b5fac28efddc1b01d8129fde` (this worktree's HEAD at generation time).

Reproducibility proof: the generator was run twice from a clean output root with real evidence and produced a byte-identical manifest (`artifact_sha256` unchanged) both times; the fast synthetic-evidence test suite additionally proves this property for the generator code path itself with a wall-clock delay between runs (`test_repeat_generation_is_byte_identical`). C/F affine invariance is proven both algebraically against the selected fit parameters and end-to-end via `predict_with_bundle` fail-closed checks for wrong model id, wrong member count, wrong unit, and wrong family (`test_cf_affine_invariance_holds_for_the_selected_fit`, `test_predict_with_bundle_fails_closed_on_wrong_model_unit_family_or_member_count`).

Verification (foreground): `tests/test_gefs_exact_day_live_schema_bundle_v2.py` 14/14 passed (including both real-evidence-gated cases); no skips on this machine. Related suites re-run for regression: `test_v11_gefs_sources.py`, `test_gefs_schema_rebind.py`, `test_v11_r47_real_candidate_shadow_injection.py`, `test_v11_offline_learning.py`, `test_v11_forecast_learning.py`, `test_v11_model_artifacts.py`, `test_v11_model_governance.py`, `test_v11_probability.py` — 181 passed, 0 failed.

This closes the specific documented prerequisite ("a deterministic v2 rebuild ... is required before any protected model-authority review/install step"). It does **not** close R47 itself: independent/owner review, root-owned model-authority installation, actual forward shadow evidence and a frozen sample target, calibration evidence, and execution-cost evidence all remain exactly as open as before. The historical-confirmation slice remains explicitly not a forward untouched holdout (`historical_confirmation_is_forward_holdout: false` in the v2 manifest, carried through unchanged from v1). No financial authority, promotion authority, order authority, or host approval is claimed or created by this work.
