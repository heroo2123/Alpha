# V11 Brain offline readiness handoff — 2026-09-30

`tools/v11_brain_readiness.py` is a read-only diagnostic evaluator for the four
corrected R47 GEFS v2 candidate bundle identities. It freezes their reviewed
manifest hash and HIGH/LOW × C/F bundle hashes from
`V11_R47_EXACT_DAY_LIVE_SCHEMA_REBUILD.md`. It does not fit, retune, install,
promote, or change their parameters. The corrected dataset digest is pinned for
the historical diagnostic class. Historical confirmation remains inspected
historical evidence, never a forward untouched holdout.

## Ready for use

Supply one local JSON object with `spec` and `observations`, and optionally
both `cost_report` and `markout_report` keys (either may be `null`). Run:

```sh
/home/alphaadmin/AlphaV11_Dev/venv/bin/python tools/v11_brain_readiness.py /path/to/local-input.json > /path/to/offline-report.json
```

The input schema and accepted fields are enforced by the code. `spec.event_ids`
is the sorted, unique, frozen **whole** event roster. Every observation must
appear exactly once and carry its source digest, label digest, family-specific
candidate bundle digest, decision time, label receipt time, and complete
BASELINE/CHAMPION/CANDIDATE probability vectors. Forward and synthetic rows
also need a source receipt time no later than the decision. Historical rows
explicitly have unknown source availability. The evaluator rejects changed
rosters, missing arms, incoherent vectors, family/bundle mismatches, lookahead,
and mixed split identity within a city-day. It scores the complete population
with city-day-weighted multiclass Brier and log loss, 10 reliability bins,
calibration error, sharpness, and descriptive paired date-block Brier deltas.
The output binds the sorted observations and cohort to SHA-256 digests and is
byte-deterministic for the same input, including reordered observation rows.

Existing `execution_costs.measure_execution_costs` and
`fill_markout.measure_fill_window` outputs can be supplied offline. The report
checks PAPER namespace, synthetic-fill class, bundle/event scope, complete cost
status and measured markout status before exposing their typed diagnostics.
Partial or absent results retain `null` values. Missed-fill rate, slippage,
P&L and drawdown stay `null`: the present typed inputs do not establish those
quantities for the candidate comparison. The report hashes each supplied
cost/markout report but cannot independently re-audit its underlying ledger;
that remains the upstream evidence code's responsibility.

The multi-model interface accepts provider probability vectors with exactly
the GEFS / IFS / AIFS dependence-group mapping used by R09. A fixed 50/50
GEFS/ECMWF group pool and 50/50 IFS/AIFS share exercise the machinery on
complete **synthetic** examples only. Missing providers produce no multi-model
score; no fallback or provider-count weighting is inferred. Historical IFS/AIFS
point-panel bytes remain outside this interface and are not causal learner
examples. Any real multi-model score still requires reviewed Gate 3/4 admission
and separately reviewed native sampled-trajectory feature binding under the
R09 contract.

## Empirical and review boundaries

Every output says `MECHANISM_VALIDATED`,
`CALIBRATION_EVIDENCE_PENDING_FORWARD_DATA`, `FITTED_NOT_CALIBRATED`, and
`NO_PROMOTION`. This is a machinery result, including when an input is labelled
`FORWARD_SHADOW`: caller-supplied hashes and timestamps are bindings, not an
independent proof of source truth, label finality, original inference, or cohort
pre-registration. The caller must supply forward PAPER/SHADOW evidence produced
and reviewed under the existing controls; this tool cannot create that evidence
or grant acceptance. Actual forward calibration, execution costs, missed fills,
slippage, P&L/drawdown, independent/owner review, and model-authority installation
remain open. No C/J/E/A credit is claimed.

Focused synthetic tests cover deterministic output, historical/forward status,
frozen roster and lineage failures, provider dependence and missing-provider
behavior, partial and complete typed PAPER diagnostic handling, and CLI duplicate
key rejection. No network, Gate-3 capture, protected authority, or financial
execution is used.
