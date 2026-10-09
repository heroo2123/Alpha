# KATL economic-only (no-PWS) lane commissioning note (2026-10-09)

**Not installed. Not executed on any host. No order, credential, root, or
service action was taken to produce this note.** It documents exactly what
`deploy/katl_continuous_shadow_economic.patch` now does, what it still
leaves `UNKNOWN`/`GATED`, and the exact (unexecuted) install procedure for
an operator who later decides to commission it, before `2026-10-11`.

## What changed and why

This patch is now **economic + EventRisk lane only** -- it no longer loads
or requires the per-day `candidate-pws-config-YYYY-MM-DD.json` artifact the
2026-10-07 handoff documented as **absent on the host**
(`docs/V11_KATL_LIVE_PLAN_DEPLOYMENT_HANDOFF_20261007.md`). PWS stays a
separate, later lane: `upgrade_host_plan(..., pws_config=None)` is the only
value this template now passes, which `katl_live_plan.py`'s own shape check
accepts only for the no-PWS economic plan (`commission_targets`'s
`pws_config=None` branch, `KATL_PLAN_COMMISSION_SHAPE_REQUIRED` otherwise).

### `risk_settlement_window` -- set

```python
risk_settlement_window=SettlementWindowPolicy(SETTLEMENT_WINDOW_VERSION, 86400.)
```

`86400.` is not a new number invented for this patch. It is the exact
`rule_max_age_seconds` literal `katl_live_plan.build_plan` already uses, for
this same event's own `RuleFingerprint`, in its own already-reviewed
`ScopeInputs(context, scope, rule, binding, stage, 86400., ...)` calls
(`polymarket_scanner/v11/katl_live_plan.py`, both `inputs` and
`temperature_inputs`). It is also the exact pairing the already-merged
(`02e3105`) test `test_risk_policy_opt_ins_reach_candidate_event_and_release_
binding` in `tests/test_v11_katl_live_plan.py` uses:
`SettlementWindowPolicy(SW_VERSION, 86400.)`. Reusing that literal here is a
scope choice ("how old can this event's own already-reviewed rule binding
be"), not a new settlement-timing assumption.

`settlement_window.py`'s own acceptance check is unchanged and untouched by
this patch: it only ever returns `DERIVED` for a `RuleGuard`-bound,
unquarantined, fresh rule whose payload has
`observation_population == 'WRH_HOURLY_DATA'` and
`finality_and_deadline_policy ==
'FIRST_FOLLOWING_DATE_DATAPOINT_OR_NEXT_DAY_2359_ET'` -- the one rule family
that module has concretely confirmed against
`weather_only_rules.py`/`weather_only_calibration_capture.py`. Any other
rule family, or a stale/quarantined/unverified one, stays `UNKNOWN`
(typed reason), never a guessed close instant. The derived instant is a
conservative upper bound on remaining tradeable-information time (the local
target date's observation-window close), **never settlement/resolution
finality** -- `settlement_window.py`'s own docstring and `BASIS` constant
label this explicitly, and nothing here touches
`paper_execution_health_promotion.promote_settlement_timing` or
`AUTHORIZED_SETTLEMENT_PROVIDERS`, which stay untouched and disabled.

### `risk_execution_health` -- left `None` (unsourced; fail-closed)

The patch never passes `risk_execution_health=`. `ObservationPolicy`
(`polymarket_scanner/v11/paper_risk_observation.py`) only stops reporting
`OBSERVATION_POLICY_INCOMPLETE` when **every** one of these fields is set:
`lookback_seconds`, `horizon_seconds`, `maximum_horizon_delay_seconds`,
`maximum_book_receipt_delay_seconds`, `maximum_measurement_age_seconds`,
`complete_history_scan_bound`, `book_provider`, `book_source_convention`.
None of the six numeric timing fields has an authoritative reviewed
production value anywhere in this repository:

- The repository's only numeric "markout horizon" constant,
  `MARKOUT_SECONDS = (1, 5, 30, 120, 600)` in `measurement.py` (1s/5s/30s/
  2m/10m), is real and reviewed, but it is consumed by
  `fill_markout.FillMarkoutPolicy`/`maker_telemetry.py` for **per-quote**
  maker-markout telemetry -- a different, already-scoped consumer -- not by
  `ObservationPolicy.horizon_seconds`'s *own-execution adverse-fill lookback*
  semantics. Reusing one of its values here for a different, unreviewed
  purpose would be exactly the kind of invented threshold this task
  forbids.
- The repository's only existing `ObservationPolicy` instance is
  `tests/test_v11_paper_risk_observation.py`'s `policy()` fixture, whose own
  `version` literal is `'fixture-only-policy'` -- i.e., explicitly not a
  production value.
- `paper_execution_health_measurement.py`'s own module docstring states
  plainly: *"This module never chooses production `ObservationPolicy`
  windows... choosing real windows... remain[s] the separate, explicit
  owner decisions."* Selecting one here would override that documented
  boundary, not merely fill in a missing default.

Every one of `lookback_seconds`, `horizon_seconds`,
`maximum_horizon_delay_seconds`, `maximum_book_receipt_delay_seconds`, and
`maximum_measurement_age_seconds` (and, by the same reasoning,
`complete_history_scan_bound`, which is a scan-cost/coverage judgment, not a
timing fact) is therefore left **unsourced** and `risk_execution_health`
stays `None`. Per `risk_inputs.py`'s existing (unmodified) design, this
keeps `adverse_fills`/`recent_markout_per_share` typed `UNKNOWN`
(`EXECUTION_HEALTH_POLICY_NOT_CONFIGURED`), which independently keeps
`EventRiskEngine` from leaving the `EVENT`/degraded-guard state on execution
health grounds alone -- a fail-closed outcome, not a gap this patch papers
over.

### `pws_config` -- `None`

No PWS sleeve. The per-day PWS config/model-pair artifact the handoff names
is still absent; forcing `pws_config` to a non-`None` value here would
either require fabricating that artifact or crash `build_runner()` at
read-time on a missing file, so this template no longer attempts to load
it. PWS is a separate, later lane, exactly as this task scopes it.

## Cost coverage verdict: GATED (unchanged by this patch)

`valuation.py`'s `_costs()` requires every entry of
`ENTRY_RISKS = {ACQUISITION_FEES, POST_SNAPSHOT_SLIPPAGE,
EXECUTION_UNCERTAINTY, SETTLEMENT_REVISION, SOURCE_FALLBACK,
OPPORTUNITY_RISK, REDEMPTION_COST}` to be covered by a `per_share`-priced
`CostComponent`; any missing or unpriced risk sets `reasons +=
['UNKNOWN_OR_MISSING_COST_COVERAGE']` and the valuation outcome is `GATED`.

This patch does not add a `temperature_costs=` argument to
`upgrade_host_plan`. With `pws_config=None` and no override,
`upgrade_host_plan` keeps `temperature_costs = base_costs =
event.lanes[0].targets[0].costs` from the **existing host smoke plan**,
which is the empty tuple `()` (`TargetPlan(first['market_id'],'YES','1',
'1',())` in the unpatched template's own `build_plan`). Cost coverage is
therefore **exactly as empty after this patch as before it** -- `0` of `7`
`ENTRY_RISKS` covered.

Of the seven, exactly one has *any* sourced, reviewed building block in this
repository: `ACQUISITION_FEES`, via `valuation.buy_fee_cost()`, which is
backed by `polymarket_scanner.production.fees.fee_requirement` (the real
Polymarket public fee schedule already in the repo). It is not usable as a
static plan-level `CostComponent` here, though: `buy_fee_cost()` requires a
live, freshly-received per-request fee/book snapshot (`received_at`/`ttl`
bound against the actual order-book provider at valuation time), not a
constant choosable at template-authoring time, and wiring it in would still
leave `6` of `7` `ENTRY_RISKS` uncovered. The other six
(`POST_SNAPSHOT_SLIPPAGE`, `EXECUTION_UNCERTAINTY`, `SETTLEMENT_REVISION`,
`SOURCE_FALLBACK`, `OPPORTUNITY_RISK`, `REDEMPTION_COST`) have **no** sourced,
reviewed numeric assumption anywhere in this repository today. No cost
value is fabricated here to work around that; this remains the same
`UNKNOWN_OR_MISSING_COST_COVERAGE` blocker the 2026-10-07 handoff already
recorded, carried forward honestly rather than closed.

## Checksums

| Artifact | SHA-256 |
| --- | --- |
| Host template preimage (`.../continuous-shadow-v2/katl_continuous_shadow.py`), verified unchanged 2026-10-09 | `cb6862fdfa3eddad134d70ecc8d4061d29726751e006639ffaa7156713b053f4` |
| `deploy/katl_continuous_shadow_economic.patch` (this revision) | `61647ff0250ecc718c64c2c9a71f2ed9e65e3a41803a1670ccf24e7223f6cf49` |
| `polymarket_scanner/v11/katl_live_plan.py` (unchanged by this task; already on main at `7eea83f`) | `ee328a8b652694b3a147531472715c89d7a001b6e65890bb1758e57745e92246` |
| `polymarket_scanner/v11/settlement_window.py` (unchanged by this task; already on main at `7eea83f`) | `fdc7539d8ef2ed8ccd3743f8709625517b23d59cab463d4f73532c1a741a4db4` |

Recheck every value above against the live host and this repository before
any install step; a changed template or module needs a new review, not a
forced patch.

## Install steps (NOT executed; for a later authorized operator)

1. Recheck `sha256sum
   /home/alphaadmin/AlphaV11_ForwardShadow/continuous-shadow-v2/
   katl_continuous_shadow.py` still equals the preimage above, and that
   `perpetual_day_preparer.py` still matches its previously recorded
   checksum (`4f5dd035...`, per this task's own briefing) -- a changed
   preparer or template invalidates this review.
2. Confirm `polymarket_scanner/v11/katl_live_plan.py` and
   `polymarket_scanner/v11/settlement_window.py` installed on the host match
   the checksums above (both already merged to `main` prior to this task;
   this patch does not change either module).
3. `patch --dry-run --directory=/home/alphaadmin/AlphaV11_ForwardShadow/continuous-shadow-v2 -p1 < deploy/katl_continuous_shadow_economic.patch`
   -- compare the patch's own `sha256sum` against the table above before
   trusting the dry run; only then drop `--dry-run` to apply for real.
4. This must happen before `perpetual_day_preparer.py` prepares the
   **2026-10-11** dated runner (around `2026-10-10 03:30Z`): the preparer
   generates each dated module from this template by text substitution and
   never overwrites an existing dated module, and `2026-10-09`/`2026-10-10`
   are already prepared smoke-only.
5. No `candidate-pws-config-YYYY-MM-DD.json` is required or read by this
   lane.
6. After any real install, rebuild and checksum the resulting
   `CandidatePlan`/commission target/binding locally and compare before
   treating any forward cycle as meaningful; this review alone is not an
   acceptance decision.

## What would count as forward evidence (none is claimed here)

Nothing here is a forward sample. A later, genuinely-installed cycle would
only add evidence toward PAPER_8 if, for a real, fresh, in-window
`WRH_HOURLY_DATA` event: (a) `derive_time_to_observation_close` returns
`DERIVED` (not `UNKNOWN_OR_CLOSED`) against the live `RuleGuard`-bound rule;
(b) whole-event book/model coverage stays fresh and the account/scenario
gates are reached without an `EVENT`-state suppression caused solely by
`SETTLEMENT_WINDOW_UNKNOWN_OR_CLOSED`; and (c) — separately, since cost
coverage stays `GATED` above — any candidate that reaches a real EV number
would still be refused by `UNKNOWN_OR_MISSING_COST_COVERAGE` until a
reviewed, sourced `CostComponent` tuple covers all seven `ENTRY_RISKS`. That
last step is not delivered by this patch and is not claimed as evidence;
`risk_execution_health` staying `None` similarly means `EXECUTION_HEALTH_
POLICY_NOT_CONFIGURED` keeps `adverse_fills`/`recent_markout_per_share`
`UNKNOWN` until a separate, owner-authorized `ObservationPolicy` is
reviewed and supplied.
