# V11 received-source reaction

`v11/source_release.py` implements the received official temperature report and
revision path for separately attributed SOURCE_SHOCK and RELEASE_OPPORTUNITY.
It requires exact contract station/population/rule identity and the immediately
preceding archived report from the same provider/channel. A newer observation
and a correction of the same observation are distinct. A duplicate receipt is
not a revision. Late older observations require a distinct revision model and
remain gated in this implementation. Archived sequence is not proof that every
provider publication was collected.

A scheduled release notice is optional provenance only. Its record must predate
the actual receipt and match the station/event. It cannot substitute for a
received official observation, establish provider publication time or prove
that a forecast/release occurred. Forecast-only releases are not yet integrated.

Every payout-model component must be recomputed with hash-bound causal lineage
including the received official record. A pre-release model or stale sibling
cannot inherit the new source's permission. Optional PWS inputs require their
own current QC/freshness leases. The independent model and station/strategy
reviews remain required. This code never learns a temperature/price threshold
from an anecdote or labels reported temperature change as a payout edge.

The exact book must be observed and archived after the official receipt. Current
book revision and event source/book/metric identities are pinned. The common
valuation subsequently checks both book sides, depth, fees and reserves. For
same-day entries the entire unresolved contract day is still modeled using the
official constraint; release receipt is not settlement finality.

A directional EVENT data path requires fresh exact inputs, protected scoped
capabilities and healthy clock, source, stream, execution and risk metrics.
Unknown or EVENT-level non-release hazards reject it. Operator NO_NEW_ORDERS,
REDUCE_ONLY, quarantine and manual review continue to suppress new exposure.
The EVENT size, liquidity, lifetime and extra-EV guards remain in force. Passive
new-risk permission stays false; cancellation remains requested, not confirmed.

Both the temperature evaluator and common paper coordinator require this pin.
Source changes, new books, demotion, expiry and transaction races invalidate it.
Paper submission-state revalidation preserves reservations on failure. All
directional exposure uses the existing shared cash, scenario/correlation and
netting machinery. A single release pin cannot approve mixed strategy attribution.
There is no real order transport or financial authority.

Tests exercise actual source/model/admission/risk code with synthetic protected
reviews. Actual current conservative bounds reject entry. Positive-EV fixtures
test downstream account behavior only. Raw exact-source adapters, independently
derived market/event metrics, forecast-release support, PWS corroboration metrics,
calibration, runtime scheduling and independent acceptance remain unfinished.
