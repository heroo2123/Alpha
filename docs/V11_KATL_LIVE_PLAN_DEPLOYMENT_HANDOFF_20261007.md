# KATL economic/PWS plan candidate — nonfinancial handoff (2026-10-07)

## Verdict

This worktree contains a reviewable plan builder and an exact patch for the
host's daily template. **It is not deployed or accepted.** The builder retains
the host plan's paper account policy, correlation map, and scenario limits;
it adds a current-input economic lane and a mandatory, separately configured
PWS observation-lead lane. Candidate assembly, admission, event risk,
valuation, PWS pairing, and common-account scenario reservation remain the
existing repo implementations. No source, model, review, settlement fact,
execution fact, or authority is created by the plan.

**It cannot make requirement 8 or 9 PASS on the next fresh sample with the
current code and evidence.** `EventRiskInputs.evaluate()` in
`polymarket_scanner/v11/risk_inputs.py` sets
`time_to_settlement_seconds`, `adverse_fills`, and
`recent_markout_per_share` to `None` even when books and models are fresh.
`EventRiskEngine.step()` therefore records `SETTLEMENT_WINDOW_UNKNOWN_OR_CLOSED`
and `EXECUTION_HEALTH_UNKNOWN`, sets state `EVENT`, and independently forbids
new risk. `PaperCoordinator.coordinate()` revalidates that state and refuses
scenario reservation. The focused test reaches a real valuation and confirms
both refusals; it never substitutes a healthy event state. With empty costs,
that real valuation is `GATED` for `UNKNOWN_OR_MISSING_COST_COVERAGE`, with no
conservative EV. A separate scenario test explicitly overwrites valuation as
a downstream fixture; it does not establish economic qualification.

## Actual host assembly and mapping

| Host item (read only in this work) | Role | Repo candidate |
| --- | --- | --- |
| `/home/alphaadmin/AlphaV11_ForwardShadow/continuous-shadow-v2/katl_continuous_shadow.py` | Template consumed by `perpetual_day_preparer.py` to generate each dated runner | `deploy/katl_continuous_shadow_economic.patch` imports and calls `katl_live_plan.upgrade_host_plan()` after existing `CandidatePlan` construction |
| `/home/alphaadmin/AlphaV11_ForwardShadow/continuous-shadow-v2/katl_continuous_shadow_YYYYMMDD.py` | Dated runner imported by `continuous_day_manager.py`; its `build_runner()` calls its own `build_plan()` | A future dated module must be generated from the reviewed patched template, or receive an independently reviewed equivalent patch; the preparer does **not** overwrite existing dated modules |
| `/home/alphaadmin/AlphaV11_ForwardShadow/continuous-shadow-v2/candidate-static-config-YYYY-MM-DD.json` | Existing route/model pin for each day | Still used by the base host plan and its checks |
| `/home/alphaadmin/AlphaV11_ForwardShadow/continuous-shadow-v2/candidate-pws-config-YYYY-MM-DD.json` | **Required new, separately reviewed local input**; absent currently | `PWSSleeve` fields except `official`, plus commission identities; missing or malformed input fails closed |
| `/home/alphaadmin/AlphaV11_ForwardShadow/build_katl_decision_plan.py` | Separate bounded preflight/export script used by `run_katl_shadow_preflight.py` | **Not** the continuous daily runtime assembly; changing it alone will not change the daily runner |

The existing daily host module hard-codes
`economic_policy='SMOKE_ONLY_NOT_LIVE_ACCEPTED'`, a `future-forecast-smoke`
lane, `CensusPlan(..., False, None)`, and no PWS worker. Its template has
`CandidatePolicy.maximum_jobs=12` but `RuntimePolicy` uses a 3600-second census
interval and short book limits. The separate `build_katl_decision_plan.py`
uses `maximum_jobs=1`. The repo builder uses a 90-second census interval and
110-second book limits within `MicrostructurePolicy`'s 120-second ceiling;
these are still hard freshness limits, not a stale-evidence exception. It
requires official and PWS census coverage, retains the host's GEFS worker,
and refuses missing reviewed PWS configuration. The patched `build_runner()`
passes three scope targets to `ShadowCommissionPlan`: forecast, PWS final
payout, and PWS next observation. Preflight checks each target against its
protected model pin and declared feature contract. The GEFS width restriction
applies to the forecast scope. The base target's epoch and state checks remain.

The patch's preimage was checked against the host template with SHA-256
`cb6862fdfa3eddad134d70ecc8d4061d29726751e006639ffaa7156713b053f4`.
The inspected `perpetual_day_preparer.py` SHA-256 was
`1df752027e9e26c774347b7d386ebe911ca85cad8240af7b4ef002e66a3f67d0`.
The separate preflight builder SHA-256 was
`f4efe67a688a81ee26f0b055256015d72e779bc414d9ac8622c1fda4a68b7865`.
Recheck those values before using the patch; a changed template needs a new
review, not a forced patch.

## Required per-day PWS configuration

The JSON file must have exactly these keys: `quality_policy`, `payout_scope`,
`observation_scope`, `observation_bundle_sha256`, `payout_sources`,
`observation_sources`, `without_pws_sources`, `lead_policy`,
`rule_max_age_seconds`, `payout_costs`, and `commission`. An optional
`temperature_costs` list supplies reviewed assumptions for the original
temperature target. Each scope is a serialized `CapabilityScope` with
`strategy="PWS_OBSERVATION_LEAD"`. Both model scopes must share station,
family, rule family, strategy, season, and time of day. They must have
**different** payout and observation bundles. Each source list needs exactly
one `MODEL`, `OFFICIAL`, and `PWS` selector with current provider and source
identity. Payout also needs exactly one causal `FEATURES` selector for the
remaining extreme conditioned on the exact official report. Its coverage
payload must pass the existing request assembly and admission gates. The
ablation list contains only separately causal `MODEL`
selectors. `quality_policy` is a serialized `PWSPolicy` and `lead_policy` is a
serialized `LeadPolicy`. The official `StationMetadata` comes from the
host's existing protected station record, not from this JSON.

`commission` has exact `payout` and `observation` entries, each with
`model_epoch`, `model_state_sha256`, and `feature_contract`. A forecast
contract declares `model_widths`, `unit`, `family`, `quantization`, and
`prediction_target`; a physical contract declares `model_widths`, `unit`,
`family`, and `input_target`; an exact pinned schema contract declares
`unit`, `family`, `target`, `feature_schema_sha256`, and `model_ids`. The
observation target must be `NEXT_OFFICIAL_OBSERVATION`; payout must be
`FINAL_CONTRACT_PAYOUT`. Select the contract matching the reviewed bundle's
actual feature family. These are expected identities, not authority:
commissioning still revalidates each protected model. An unrelated schema
cannot pass merely because a target says next observation.

`payout_costs` is a list of at most 16 serialized `CostComponent` values for
the PWS payout target. `temperature_costs` has the same bound and format.
If omitted, the temperature target retains its base host costs. A nonempty
base tuple cannot be replaced by a conflicting config tuple.
Each component has named risk coverage, final-payout horizon, an assumption
hash, and a per-share value or `null`; optional fee price and expiry fields
retain their normal valuation checks. Declaring a cost does not attest it.
Unknown, missing, expired, or scope-mismatched costs keep valuation gated;
zero is never a default. Complete coverage needs acquisition fees, execution
uncertainty, opportunity risk, post-snapshot slippage, redemption cost,
settlement revision, and source fallback. Each lane uses its own tuple and
the plan hash binds both.

EventRisk uses the union of the base lane and PWS payout selectors, excluding
FEATURES coverage, with the stricter age for an identical selector. The
temperature lane keeps its own sources. Observation-model probability is not
used to price final payout. Current source, book, review, event-state, and
account gates remain required.

No generic placeholder or fixture config is safe to install. A reviewer must
bind each day's values to that day's rule fingerprint, exact official report,
MADIS neighborhood/QC lineage, separate approved next-observation champion,
approved final-payout champion, source leases, model epochs, and protected
capability reviews. A PWS QC summary is auxiliary and never settlement
authority. The AWC METAR proxy collected by the census does not by itself
prove the exact contract report required by `pws_lead._report()`; that exact
official observation must exist and pass ordinary source/admission checks.

## Review and copy procedure (not executed here)

1. In the isolated candidate, review the committed diff and run the focused
   offline suite. Record `git rev-parse HEAD` and
   `sha256sum polymarket_scanner/v11/katl_live_plan.py deploy/katl_continuous_shadow_economic.patch`.
2. On the host, recheck the template and preparer checksums above. Run
   `patch --dry-run --directory=/home/alphaadmin/AlphaV11_ForwardShadow/continuous-shadow-v2 -p1 < deploy/katl_continuous_shadow_economic.patch`.
   The dry run was successful in this worktree; it changes no file.
3. Only after independent review, copy the **exact committed** changed V11
   modules (`katl_live_plan.py`, `shadow_commission.py`,
   `forecast_features.py`, `physical_inference.py`, and `model_artifacts.py`)
   into the isolated installed code tree, and apply the **exact committed** patch
   to `continuous-shadow-v2/katl_continuous_shadow.py`. Before activation,
   compare source/destination module checksums with `sha256sum` and compare
   `patch --dry-run` output against the approved preimage. Do not force hunks.
   Review the generated dated module separately because the preparer creates
   it by text replacement and never rewrites an existing one.
4. Supply the reviewed per-day PWS JSON and compare its SHA-256 against the
   approved manifest. Rebuild and checksum the resulting `CandidatePlan`,
   three commission targets, `candidate_cohort`, and returned binding/config
   hash. Run commissioning preflight for all three protected scopes. Use a reviewed fresh
   isolated daily ledger or explicit reviewed migration; old config-bound
   runtime heads may reject changed plan bytes. Existing account limits and
   protected review status must be checked before a sample is accepted.

No host file, service, configuration, database, or status was changed in this
worktree. No provider request, order, account mutation, credential, or root
operation was made. The candidate has `financial_authority=false` and the
paper runtime has no automatic order placement path; PWS remains observation
only for lead research and never labels settlement or prices payout.

## Exact remaining evidence for requirements 8 and 9

1. A separately reviewed implementation must derive **true** settlement
   timing and execution-health metrics from causal, scoped evidence. Current
   `risk_inputs.py` always leaves them unknown; a fresh book cannot solve it.
   The event then needs the policy's distinct, spaced recovery samples, healthy
   clock/source/whole-event book sequence, current model and rule pins, and
   no operator reduction before new risk can be considered.
2. Target-specific, supported cost components must cover all seven risks;
   current empty tuples do not. An actual positive conservative economic
   valuation must pass the unchanged
   threshold and the retained account/scenario limits. Neither a plan nor a
   synthetic test valuation is a forward proposal or reservation.
3. For requirement 9, a real current MADIS neighborhood, healthy defensive QC,
   exact official report, distinct protected observation/payout model pair,
   causal ablation, and valid lead window must produce the PWS admission and
   observation record. A qualifying PWS-attributed proposal must then pass
   the same event-state and common-account netting checks to show forward
   reservation evidence. No part of this plan grants settlement authority to
   PWS.

Until those observations and code review exist, requirements 8 and 9 remain
PARTIAL. A no-network fixture passing here is construction/mechanics evidence,
not a forward sample or deployment acceptance.
