# V11 bounded maker research features

Status: implemented and locally verified foundation; R35 remains PARTIAL.
No maker quote, fill model, trading authority or runtime deployment is granted.

`polymarket_scanner/v11/microstructure.py` consumes the existing namespaced
evidence archive. A measurement binds the complete contract target, rule hash,
collateral asset, exact input IDs, policy and original decision cutoff. Contract
side is YES/NO; `aggressor_side` is separately BUY/SELL. The two meanings must
never share a payload field. Only explicitly declared taker semantics permit
directional public-print features.

## Implemented observations

- Best bid/ask, midpoint, L1 microprice, spread, L1 and selected-depth quantities
  and imbalance. Crossed/locked, missing or unhealthy current books gate the
  whole measurement. Existing strict depth validation is reused.
- Price velocity/acceleration, RMS midpoint change per observed update, visible
  depth deltas and update rate from the latest contiguous declared sequence.
  This sampled variation is not an annualized volatility or learned model.
- Archived public-print count, volume, declared aggressor direction and flow
  imbalance. Exact provider/trade-ID repeats deduplicate. Conflicting revisions,
  account-fill payloads and unproven cross-provider deduplication fail closed.
- Book observation and receipt ages, archived input provenance, explicit gap/
  reconnect resets and deterministic historical replay.

Book sequence metadata is a versioned adapter declaration, not independent venue
attestation. Missing or broken predecessor/epoch/time continuity resets temporal
features. A fresh valid current snapshot may still support static features.
Visible depth changes do not identify cancellation, addition or trade causes.
Public-print rates describe the supplied archived subset, never a proven complete
venue feed. Public trade-through never proves a fill of our hypothetical order.

## Bounds and durable behavior

Each call accepts at most 32 book and 31 public-print IDs. History is at most ten
minutes; maximum book age at most two minutes; maximum sequence gap cannot exceed
the history window. Minimum sample spacing is at least one millisecond. Selected
depth is at most 20 levels, with at most 1,000 validated levels per side in each
archived snapshot. Bounded decimal representations are reused from scenario risk.
These are implementation ceilings, not empirical recommendations for a live policy.

Observed, received and available times must be causal and within the declared
history/cutoff. Historical availability unknown cannot enter these features.
The current exact book channel must match the archive head. Source-head compare
and swap prevents committing measurements over a concurrent source advance.
Reusing a record ID returns its original historical measurement; it cannot refresh
the cutoff or create current admission authority. Policy/input collisions reject.

All tests are synthetic off-host fixtures. No actual feed or empirical execution
quality has been accepted. The original 109 pass / two failure run exposed the
contract/aggressor side collision. After separating the fields and adding YES/NO
and legacy-collision coverage: **27 new tests and 113 related tests passed in
1.82 seconds**. Related coverage includes evidence, valuation and event queue.

## Remaining dependencies

Fill probability, fill hazard, queue position, expected markout, conditional
adverse move and conservative maker EV remain UNKNOWN. Current inventory, event
state, distance to protected fair value, quote age/survival and time to release
still need common-account/risk/source integration. No unobserved cancellation or
addition rates are fabricated. Provider adapters must preserve exact identity,
causal receipt/sequence semantics and completeness limitations.

Maker research proposals, sampled eligibility and horizon markout observations
are now implemented through the evidence and common account/risk interfaces;
see V11_MAKER_RESEARCH.md. Next complete the reviewed bounded baseline policy.
Cancellation and telemetry acceptance remain
required. A first separately approved tiny canary need not already have real
maker fills; unlearned metrics stay unknown. Broader learned authority or scaling
still requires prospective/shadow support, genuine tiny-canary evidence and
out-of-sample execution validation. No canary or funding is authorized now.

Rewards/rebates remain separate from trading alpha. Guardian, clock, operator,
deployment and the remaining full specification requirements remain in the
requirements matrix. Off-host implementation continues while V10 resource and
suspension-readiness findings remain open.
