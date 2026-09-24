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
