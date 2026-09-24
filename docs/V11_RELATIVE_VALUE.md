# Whole-event relative-value discovery

`v11/relative_value.py` supplies a bounded discovery path for the separately scoped
CROSS_TEMP_RELATIVE_VALUE and STRUCTURAL sleeves. It obtains the exact rule,
protected model bundle and archived inputs from the strategy admission pin. The
whole-event probability vector must be complete and coherent; current conservative
single-token bounds remain vacuous. Prediction, calibration, execution-cost,
feature and strategy-quality artifact identities and model epoch remain in the
decision record.

Discovery enumerates individual tokens, adjacent YES pairs, exhaustive YES and NO
sets, and same-condition YES/NO complements. Duplicate economic leg sets retain
their diagnostic labels without creating another proposal. Bounds are explicit:
16 buckets, 32 supplied instruments, 65 candidate plans and at most six proposals.
Larger input sets fail closed rather than being silently truncated.

Point-price gaps and adjacent probability/cost differences are recorded as
diagnostics. Adjacent bucket probabilities are not assumed to increase with
temperature. Cumulative model probability is kept separate from cumulative
acquisition cost; market quotes are never normalized into a probability vector.
Missing instruments cannot form a complete set, and unknown costs cannot become
zero-fee underrounds. Every candidate uses the tested joint-depth/cost/scenario
valuation. The same account subsequently applies desired-position, aggregate
cash, price-limit EV, event, certification and risk checks.

Candidates rank by conservative joint EV per reserved capital. Overlapping
opportunities still compete in the shared account; discovery does not reserve
cash or create fills. Durable start/value/result identities permit interrupted
work to resume without refreshing inference time or expiry. Each candidate gets
an exact output ID for event-queue completion, and the account revalidates again.
Discovered, semantically supported, source-ready, evaluated and candidate stages
have bounded counts and reasons. Risk acceptance and actual fills are downstream.

Verification: 12 new tests and 92 related discovery/basket/queue/strategy checks
passed in 22.85 seconds. A targeted correction made an incomplete model vector a
durable GATED result instead of an unstructured error. Prior common-account
implementation passed 2,851 full-regression tests, four existing warnings, and
GitHub CI run 35980156386. The full suite was not repeated for this additive module.

Remaining work includes conditioned remaining-day basket inference, exact provider
adapters and periodic runtime integration, actual approved/calibrated models,
complete-set claim/redemption and empirical strategy acceptance. No live-eligible
strategy or financial authority is established. V10 remains unchanged; all tests
ran off-host. The protected V10 freshness read remains owner-only.
