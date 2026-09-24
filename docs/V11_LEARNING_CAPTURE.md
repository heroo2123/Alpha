# Forecast capture and causal dataset integration

`v11/learning_capture.py` joins the protected FUTURE_FORECAST prediction in
`TemperatureStrategies.evaluate` to the existing feature, decision and dataset
interfaces. It captures every YES bucket of that evaluated event before the
requested token's entry economics. Complementary NO claims do not double the
training population. This is event-vector coverage, not proof that every
supported event was discovered, admitted or evaluated.

The capture binds the original inference cutoff, source hashes, model members,
issue/receipt/availability times, exact rule and protected bundle. Numbered model
members and explicit quantization cuts form a stable feature schema, bounded to
128 fields. The cut convention is a model hypothesis, not settlement-source
rounding proof. Features retain their actual computation time. Interrupted child
records remain immutable; retries neither backdate them nor renew original
expiry. Completed requests replay without writes. Capture failures retain the
forecast and report a separate dataset gate.

Capture v2 now requires a `PinnedBundle` whose declared FEATURES artifact exactly
matches the shared `ForecastFeatureContract` and whose probability parameters
match the archived prediction. Model IDs/member widths, unit, daily family and
supported quantization cannot change silently. Existing completed v1 captures
remain readable/replayable under their original version; they are not relabeled
as v2 compatibility evidence. A legacy generic parent can still be read, but a
new mismatched forecast capture is gated. Interrupted incompatible legacy children
are retained and require explicit reconciliation, never overwrite or redating.

`build_initial_forecast_bundle` builds new INITIAL_NO_FIT research objects with
this contract and explicit probability/provenance/quality parameters. It refuses
a parent identity, unsupported dimensions and fitted claims; it never rewrites
an existing bundle or installs an active pointer. Calibration stays vacuous and
execution-cost evidence stays UNKNOWN.

`v11/forecast_learning.py` provides the explicit offline join to the existing
bounded learner. A typed complete-label cohort and registered frozen plan produce
a separate CHALLENGER-journal recipe with original capture/example hashes, exact
dataset identity and counts. Training is never invoked by capture or the trading
runtime. Completed jobs replay the saved result; interrupted attempts are gated
for review, not silently refitted. Historical predictions keep their original
bundle identity even when compared under a compatible current research parent.
The cohort is bounded to 128 captures/2048 examples/ten seconds of assembly;
the unchanged learner has its own finite numerical/time limits. OS separation
remains unverified.

The subsequent declared-contract/learner integration passed 145 related tests
in 29.35 seconds, including 29 new cases. Synthetic 31-member C/F and high/low
event vectors traverse capture, complete labels, causal temporal splits, fitting
and candidate inference with numerical parity, parent preservation and no
promotion. This is not actual-label, calibration, source-access or live evidence.

All capture decisions are GATED for executable economics. They create no label,
fill, account mutation, training run, active-model pointer or financial authority.
`labeled_examples` requires a complete, consistent set of explicitly supplied
exact bucket labels and delegates causal/identity checks to `build_example`.
The default cohort is DEVELOPMENT. Label attestation remains false; fabricated
test labels are SYNTHETIC. A successful join does not certify source finality.

The 17 new tests cover the rejected-trade integration, all-event-bucket capture,
dataset construction, original timing, interrupted replay, scope/source defects,
label inconsistency, bounded failure and target separation. The related
dataset/learner/artifact/strategy/candidate run passed 114 tests in 22.55 seconds.
No real or forward learning evidence is claimed.

Still required: actual exact-label production and independent attestation;
universe coverage and selection analysis; conditioned, next-observation,
execution and maker target-specific features; accepted calibration; isolated
training and champion/learning acceptance. A next official observation remains
distinct from final payout and an executable exit.
