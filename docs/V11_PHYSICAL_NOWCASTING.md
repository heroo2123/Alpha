# V11 causal physical feature candidates

Implemented `v11/metar_features.py` and `v11/nowcast_features.py`, with the AWC
normalizer versioned to `alpha_v11_awc_metar_v2_physical_body`.

The optional parser reads the archived METAR body using the documented report
format. Wind units are explicit in each token, including knots, m/s and km/h.
Station and observation time must match the normalized observation. Remarks do
not replace body values. Missing/ambiguous optional fields remain missing, while
a valid temperature observation survives an optional decoding failure. Calm and
variable winds do not invent a direction. No cloud fraction is fabricated from
a categorical cloud code.

Primary references: [AWC METAR format](https://aviationweather.gov/help/data/)
and [AWC public API](https://aviationweather.gov/data/api/). The web tool could
read the official format documentation but could not download its binary-served
OpenAPI file; no unverified JSON meteorology field mapping was introduced.

The feature builder records source-derived wind, moisture spread, reported cloud
and weather flags, recent temperature slope/acceleration with actual time span,
and optional PWS/daylight context. Daylight inputs require a causal issue/receipt,
matching station and local date. They represent an astronomical forecast window,
not an assumed temperature or remaining heating/cooling probability.

Every feature is archived with the numeric schema and exact source dependencies.
Later observations or revisions cannot enter earlier archived feature records.
Communication gaps reset trajectory support. Stale or missing PWS values stay
missing without removing healthy official-proxy features. Family ablations keep
the same schema and mark only that family's values missing, so empirical
with/without-feature comparisons can use identical target/evidence definitions.

The observation runtime now accepts explicit required-provider sets per research
strategy. Optional-provider failure does not suppress unrelated source coverage.
All planned requests for a required provider must normalize successfully before
that provider is considered covered. This is a source-coverage funnel, not
certification, calibration or strategy admission.

17 new physical-feature tests plus source/collector tests verify units, missingness,
raw identity, causal timing, gaps/trajectory, ablation, daylight and partial-source
behavior. AWC remains a proxy for the exact contract observation population.
Features are research candidates with no probability, settlement or financial
authority. Incremental out-of-sample value, trained physical/PWS effects, exact
source integration, cache/event scheduling and production acceptance remain open.

## Declared inference integration

`v11/physical_inference.py` now connects these features to immutable numeric model
artifacts and the existing next-observation/same-day inference engines. The
contract binds model member counts, physical schema, unit, family and target.
Standardized feature adjustments use only bounded coefficients from the pinned
PROBABILITY artifact. There are no production meteorological coefficients in
code. Missing material terms contribute no adjustment and require a wider kernel;
an effective adjustment outside the protected bound gates instead of clipping.
All conservative bounds remain vacuous and predictions remain UNCALIBRATED.

The model archive retains original members, run/receipt identity and exact feature
dependencies. Features carry metadata, expiry and separate ablation stream
identities. PWS ablation removes both its values and dependency, preserving the
same non-PWS evidence and frozen observation model. Current source/metadata guards
and append races are checked. Missing PWS can retain healthy official features
and the conservative fallback; material PWS influence still requires healthy QC.

The 26 new synthetic integration cases include raw MADIS/AWC -> QC/features ->
paired next-observation inference -> separate protected payout pin -> exact
same-day conditioning -> conservative settlement economics. Entry is rejected;
observation confirmation supplies neither payout truth nor executable proceeds.
Legacy inference serialization remains unchanged without auxiliary features.
The existing bounded grid learner explicitly rejects the new family. Fitting,
target-specific datasets, actual OOS feature value/calibration, automated current
input preparation and operational acceptance remain required. Fixture reviews
are not independent review, and artifact construction cannot promote a model.
