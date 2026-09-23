# V11 causal learning datasets

`polymarket_scanner/v11/datasets.py` adds immutable feature-schema identities,
archived numeric feature values and exact derivation references. Optional missing
features remain missing. Shape, finite-value and predeclared numeric bounds are
checked before archiving; no label or unknown-availability backfill is a feature.

A learning example pins its original decision, feature, rule/release/config/model
binding and exact label version. The derivation graph preserves source identity,
revision, issue/observation/publication/receipt times and hashes. It cannot follow
a later revision or label into the earlier feature state. Labels retain their
availability time and evidence class. A correction produces a new example hash;
it cannot silently replace the old label or duplicate a decision within a fit.
Payout examples additionally bind the exact market, condition, token and side to
the original decision; matching only the event cannot attach a sibling label.

The current builder records stored label provenance, not independent label
attestation. Exact-source finality checkers and reconciled execution adapters must
be integrated before those labels support production promotion. Synthetic labels
remain synthetic. Observation labels cannot become payout labels, and rejected
counterfactuals cannot supply real execution-cost labels.

## Splits and repeated evaluation

Dataset plans bind training cutoff, development and confirmation windows, target,
feature schema and comparison-policy digest. Training labels must be available
by the historical fit cutoff. All labels must be available by the dataset's
evaluation watermark. Events and city-days cannot cross partitions. The manifest
includes actual time ranges, example/event/group counts and station/horizon/
season/strategy/selection slices. It preserves the distinction between all
supported predictions and selected trades.

All inspected V10 snapshot data remains DEVELOPMENT. A caller's uninspected
designation is a provenance claim, not proof of an untouched holdout. The
experiment journal records plan registration, every started attempt, failures,
results and confirmation reveals. A reveal overlapping previously revealed
city-days is development evidence even if its candidate or dataset hash differs.
Retrospective registrations never claim first prospective confirmation.

The append uses the same journal sequence that was inspected for overlap or
completion, so concurrent changes fail comparison-and-swap. Finished attempts
cannot be rewritten, and the learner has no promotion result type.

## Verification and remaining work

20 focused tests cover provenance, feature bounds, unavailable labels, label
corrections, temporal/event/city-day leakage, selected-trade evidence boundaries,
manifest tampering, holdout reuse and durable training failure. No actual V11
training run or promotion has occurred.

The offline learner and immutable compatible bundle registry are now implemented
and tested separately. Bounded scheduling, source-specific label attestation,
protected host commissioning and complete runtime integration remain pending. A host must isolate
the learner and its research journal from financial credentials and live pointers;
these data structures alone do not prove that OS boundary.
