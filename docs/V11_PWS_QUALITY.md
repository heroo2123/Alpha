# V11 PWS defensive quality and neighborhood features

`v11/pws_quality.py` adds a bounded causal QC and feature layer to the existing
public MADIS/CWOP adapter. Every threshold is an explicit versioned policy input;
the test fixture is not a calibrated production policy.

The archive wrapper recompiles the preserved raw XML, verifies normalized values
and receipt identities, and binds the exact settlement-station/event context.
Future receipts and unknown-availability historical captures cannot enter replay.
Repeated cached records preserve first receipt and do not create sample support.
Revisions remain separately counted and use only versions already received.

Checks cover provider flags, physical range, stale/clock errors, discontinuous
history, jumps/rates, flat-sensor suspicion, independent-neighbor disagreement,
distance/elevation mismatch, duplicate co-located feeds and material metadata
changes. Flat readings are downweighted without inventing an indoor-sensor
diagnosis. Communication gaps reset recent support. Relocation is detected even
when coordinates revert later in the supplied history; a durable quarantine
persists across process restart and history-window expiry. No automatic restore
operation is provided.

Neighborhood weights use configured distance bands, elevation, freshness,
provider checks and recent availability. Outputs retain sensor counts, distance,
bearing, elevation difference, QC reasons, weights, first/current-version receipt,
observed local-day envelopes, weighted median, IQR, spread, observation ages and
5/15/30/60-minute changes where actual endpoint support exists. No missing trend
is interpolated. A weighted spatial gradient needs non-collinear support.
Official-minus-neighborhood residuals retain the official/proxy source role and
require both a fresh observation and a causal receipt.

Health is explicitly HEALTHY, DEGRADED or UNAVAILABLE. Empty/unusable PWS input
does not fabricate sensors. PWS-dependent valuation must widen uncertainty or
gate when coverage fails; other safe source paths remain available. This module
does not by itself implement the strategy dependency routing.

All outputs remain **informational** with no trading, settlement, label or lead
authority. Historical official/PWS residual models, learned reliability,
validated exposure/terrain effects, paired timely official-feed lead evidence,
event-trigger integration and out-of-sample with/without-PWS ablation remain
pending. A public coverage probe and a QC pass cannot prove trading value.

23 focused synthetic tests verify these implemented checks, raw-to-feature hash
bindings and restart persistence. No new remote query, deployment or host
workload was used for this milestone.
