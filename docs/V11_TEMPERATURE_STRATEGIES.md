# V11 temperature strategy integration

`v11/strategy_pipeline.py` connects archived temperature inputs, protected
strategy admission, immutable model bundles, event state and executable
settlement valuation. It emits a common-coordinator proposal only after those
gates pass. It has no network, order, account-creation or activation interface.

FUTURE_FORECAST requires a future contract day in the rule's local timezone and
whole-day extreme inputs. SAME_DAY_LATE_LOCK requires the current local contract
day, an exact source-population observation constraint and a partition covering
the entire unresolved day, including elapsed observation gaps. Next-observation
models cannot enter either payout path. An observation is conditional on its
received revision; it does not establish finality or an executable early exit.

PWS_OBSERVATION_LEAD uses the same exact remaining-day payout route, with a
mandatory paired observation/payout admission pin. Its separate next-observation
model cannot substitute for payout. See `V11_PWS_OBSERVATION_LEAD.md`.

SOURCE_SHOCK and RELEASE_OPPORTUNITY require the actual received-source pin and
post-receipt model/book evidence. Their directional EVENT path retains the
stronger common state/risk checks; see `V11_SOURCE_RELEASE.md`.

Model members and target identities are reconstructed from referenced archive
records. Every model input needs an admission source lease. Bias, smoothing and
dependence weights come from the one protected bundle; caller probabilities and
unrecognized input fields are refused. Same-day conditioning binds the exact
observation revision, model evidence hashes and coverage evidence. It preserves
the coverage computation's inference cutoff even when archival and evaluation
finish later, and refuses inputs received after that cutoff.

Stable evaluation IDs preserve the original request and partial valuation across
an interruption. Reusing a completed ID returns historical evidence without
refreshing its expiry. A new source arrival, rule/capability change, model epoch
change or safety reduction requires a new valid evaluation. The coordinator
rechecks admission and event state before its own atomic reservation and paper
submission-state transition. A strategy evaluation itself reserves nothing.

Results retain strategy/version, scoped review reference, code/config/rule/bundle
identity, all five artifact references, model epoch, input hashes, cutoff, depth
valuation, gate reason and funnel stages. Prediction point estimates do not
override conservative bounds. All current settlement evaluations remain
uncalibrated and rejected or gated. There is no V10 fixed hour, member-vote count,
entry-price window or temperature-delta gate in this implementation.

27 synthetic tests exercise both extreme families, local-day boundaries, target
separation, source/model bindings, complete coverage, unknown costs, event/operator
suppression, model demotion and interrupted evaluation. These fixtures exercise
the real joined admission readers with synthetic protected review/artifact data.
They do not attest a real exact-source adapter, approved champion or strategy.

Remaining integration includes provider-specific raw-to-model/condition/coverage
adapters, observed scope-regime derivation, calibrated bounds, causal physical/PWS
features with validated incremental value, runtime routing and full acceptance.
In particular, archived generic input schemas and synthetic source claims are not
independent proofs of source authority. The existing GEFS daily response lacks a
verified run-initialization timestamp; receipt and `generationtime_ms` cannot
replace it. No actual eligible entry or real order has been produced.
