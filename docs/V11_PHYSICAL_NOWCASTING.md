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
