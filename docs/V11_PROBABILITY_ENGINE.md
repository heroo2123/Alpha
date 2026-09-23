# V11 probability engine

`polymarket_scanner/v11/probability.py` implements a bounded mixture CDF over
whole-degree temperature partitions compiled by the existing strict rule parser.
Every input binds the exact event/rule/target, source digest, receipt, feature
completion and model issue time when known. Missing native run time stays unknown.

Each model's members share its declared model weight. Related models share an
explicit dependence-group budget. Replicating members or a same-weight model
within its group does not add independent evidence. The declared groups still
require empirical validation; the engine never derives confidence from the
number of ensemble members.

The CDF differences produce one fair vector summing to one. Conservative bounds
are separate and currently default to `[0, 1]` because no V11 calibration evidence
has been accepted. They are never normalized. A binary NO lower bound is one minus
the YES upper bound. Fitting, scoring and a numerical forecast are not calibration.

The temperature-kernel bias/dispersion and nearest-whole-degree mapping are
versioned model hypotheses. They do not redefine official source precision or
rounding. Unsupported source precision fails closed. No empirical parameters
have been fitted from the inspected V10 baseline.

## Same-day constraints and targets

An accepted exact-source whole-degree extreme can constrain a remaining-path
distribution through max/min. The input must use the unresolved-extreme target;
a whole-day forecast cannot be passed accidentally. A bound coverage manifest
must partition the complete local day into accepted and unresolved intervals,
including elapsed gaps, with no omission or overlap. This structural check still
requires a certified upstream source/model adapter; a supplied role string or
coverage digest is not independent authority.

The resulting distribution is explicitly conditional on the accepted source
revision. Revision and source-fallback uncertainty remain unmodeled, with full
conservative bounds. The engine does not smear arbitrary mass onto an outcome
that is impossible under its declared observation constraint.

`NEXT_OFFICIAL_OBSERVATION` and `FINAL_CONTRACT_EXTREME` have different target
identities. Accessing an observation prediction as final payout or executable
early-exit value fails. There is no executable exit model or order path here.

Scoring reports multiclass Brier, log loss, sharpness and reliability bins.
Weights are equal by declared city-day, then event, then repeated decision.
Impossible outcomes retain infinite log loss. Group counts are reported without
claiming that regional weather outcomes are independent.

## Verification and remaining integration

31 focused tests cover analytic CDF values, negative temperature boundaries,
randomized probability invariants, dependence weighting, NO bounds, same-day gap
coverage, target confusion, stale/late inputs and repeated-outcome accounting.
These are synthetic code tests, not live calibration or economic evidence.

Remaining work includes source-derived coverage/metadata integration, reviewed
artifact parameter loading, station/horizon/season feature selection, coherent
calibration and revision/fallback models supported by causal labels, source
failure fallback, strategy valuation and final decision revalidation.
