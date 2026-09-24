# V11 controlled continual learning

`v11/offline_learning.py` is a bounded deterministic research worker. Its current
supported fit varies additive temperature bias and kernel dispersion for one
existing model, over a frozen finite parameter grid. Model-member inputs and
bucket boundaries come from the archived numeric feature schema. The same
Gaussian-member CDF family is used by inference; a different model family cannot
silently use this learner.

The dataset plan binds the entire learning/comparison policy before fitting.
Every grid trial is logged, including failures. Parameter selection uses TRAIN
only. One frozen candidate then receives comparison with the exact parent on
identical development/confirmation rows; parent parameters provide the ablation.
Scoring preserves event/target/city-day groups and selected-trade cohort identity.

Comparisons report Brier, log loss, reliability, sharpness, declared group counts,
a deterministic city-day bootstrap interval, leave-best-city-day-out improvement
and station/horizon/season/strategy/selection slices. The bootstrap does not prove
regional independence. Required group/station support and tolerances are explicit
policy inputs, not fixed canary waiting periods or thresholds fitted to V10 loss
patterns. The initial accepted champion need not wait for a new challenger win.

Candidate output is an immutable compatible data bundle with full run provenance.
Training never mutates the parent. Sparse training support produces no candidate;
numerical/time budgets produce a durable failed attempt. Fit trials and final
results are saved in the append-only research journal. Holdout reuse is recorded
as development evidence, including repeated runs with different candidate hashes.

The worker currently returns `NO_PROMOTION` because independent label attestation
and dependence review are not integrated. Favorable synthetic comparisons do not
override that requirement. There is no pointer, account credential, order or
cancel interface in the learner API. No actual V10 dataset has been fitted.

The forecast job in `v11/forecast_learning.py` now connects declared bundle
FEATURES contracts to complete captured event vectors and the learner. All
member/cut columns must match the parent; dropping members or interpreting a
nullable cut as a member is rejected. The dataset recipe and trials stay in a
separate CHALLENGER journal. Exact completed requests return their saved result;
an incomplete attempt requires review and is not implicitly rerun. Tests verify
candidate-inference probability parity and unchanged source evidence/parent.
This is an explicit research call, not an automatic scheduler or initial
champion approval. Exact source derivation and OS isolation remain open work.

Eight synthetic tests verify reproducibility, train-only selection, confirmation
reuse, complete trial accounting, sparse-data behavior and resource failure.
Actual OS credential/network/CPU/RAM isolation, bounded scheduling/backoff,
source-specific authoritative labels, wider model families, strategy-quality and
execution learning, promotion evidence and operational acceptance remain pending.
No learner workload has been added to alpha-dev.
