# V11 station capability registry

Implemented in `v11/certification.py`, with append-only evidence in
`v11/evidence.py`. Metadata includes exact station/city/country, coordinates,
elevation, timezone, settlement-source relationship, observation/forecast
providers, raw provenance and retrieval time. Missing optional discovery
metadata is preserved; observation alone does not certify identity.

Capability scopes distinguish station, HIGH/LOW, rule/source family, model,
horizon, strategy, season and time of day. There is no wildcard scope. Required
capabilities differ by strategy: structural finality is not replaced by a
calibration score, and PWS lead requires separate source/QC, causal lead,
observation model, contract economics, revalidation and common-risk evidence.

`observe`, `proof`, `demote` and `assess` retain metadata and evidence history.
Material metadata drift or a safety demotion requires a new review; restoration
of old metadata, elapsed time or a later passing record cannot erase a failure.
Compare-and-swap append guards prevent stale metadata writers from overwriting
newer audit state. Reviews bind exact proof IDs/hashes and a review watermark.

The application only reads `/etc/alpha-v11/approvals/station-capabilities.json`.
The file and every parent must have root custody and no group/world write bit;
symlinks, oversized documents, expired/future reviews and mismatched proof hashes
fail closed. This path is an implementation interface, **not a commissioned
trust anchor**. The repository does not install or self-approve such a manifest.
Separate owner/independent host review and provisioning remain necessary.

PAPER, SHADOW, CANARY_ELIGIBLE, CANARY_VERIFIED and LIVE_LIMITED are separate
review scopes. The first tiny canary is not required to have already generated
real fills; actual-canary verification and later scaling have additional evidence
requirements. Synthetic records cannot supply a real-execution proof. Every
result still has `financial_authority=false` and `activation_authorized=false`.
Actual financial permission remains with the separately protected execution path.

Current state: tested component, no live station approvals, no provisioned review
manifest, and no financial activation. Automatic observation/forecast identity
checkers, learner lifecycle wiring and final runtime admission integration remain
pending; no checker-version string or caller's PASS claim proves those checks.

Tests: `test_v11_certification_rules.py` covers scope isolation, failed/expired
reviews, custody, metadata drift/reversion, real-versus-synthetic evidence,
monotonic demotion and reviewed recertification using explicitly synthetic review
fixtures. Those tests are not independent review or live eligibility evidence.
