# Gate 3 validated-plan export: supplied-byte prerequisite

Status: **author candidate only; no production export, acceptance, or credit**.
Baseline `f7692447232a047d93d343ccb5e19d317633c329`. The independently
PASS-reviewed design is
`V11_GATE3_VALIDATED_PLAN_EXPORT_DESIGN_20261003.md`; exact review is retained
at `/tmp/alpha-v11-validated-export-review-988f544.final`. This slice includes
the review's nonblocking P3 role, `event_policy_review`, among the seven exact
input roles.

`tools/v11_gate3_validated_plan_export_offline.py` accepts only exact immutable
bytes for those seven roles, rejects missing/extra roles, caps each buffer and
the aggregate, preflights JSON nesting/tokens, checks canonical encoding and
closed boundary records, and returns a frozen **untrusted** byte snapshot. It
derives the entire 2,713-slot denominator, ordered request/slot/purpose and
event-key/provider/field-list projections, checks the three plan-review
projection hashes, and hashes the complete schedule and seven-role input
bundle. Every event identity includes the copied cohort and time fields in
the reviewed design. A changed manifest, policy, or review changes the bundle
identity. The snapshot makes no V4 validation assertion.

`consume_accepted_export` takes supplied export/custody/acceptance bytes and
an external pin-channel argument. It checks byte/canonical bounds, then
always refuses `VPE_EXTERNAL_TRUST_UNAVAILABLE`. The protocol names the
future boundary but does not invoke caller-supplied implementations or treat
their hashes as authority. A forged object or internally consistent
self-rehashed omission can never produce an accepted report here. The module
imports no runtime constructors and has no filesystem, subprocess, socket,
provider, credential, account, order, or collateral path.

This is intentionally a dependency-first slice. Missing before any accepted
export or estimate: a separately reviewed and bounded production V4 mapping
amendment; complete V4/private-artifact validation under authorized custody;
an exporter immediately after that validation with race-free immutable
publication; reviewed producer/build/source pins; independently authenticated
policy review, pin issuer, custody receipt, and exact acceptance review;
consumer verification of the complete export/custody/acceptance schemas and
external pin chain; resource report generation and point-of-use rechecks.
This module does not authenticate source truth, a reviewer, clocks, provider
rights, retained restrictions, or host capacity. A self-rehashed one-event
snapshot can be internally consistent, but its changed bundle has no accepted
external pin and receives no credit. The present V4 production mapping still
refuses 11 of the required 15 provider-purpose pairs. G3-L stays **NO_GO**;
G3-E, SHADOW, and C/J/E/A credit remain gated.

Focused tests use synthetic bytes. They cover deterministic replay, seven-role
closure, malformed/canonical/bounded JSON, omitted and substituted events and
schedule rows, rehashed caller-controlled policy/manifest changes, spoofed pin
objects, false flags, and a socket-connect tripwire. The adjacent V4 validator
and offline resource suites run with the new tests in normal and optimized
Python. This candidate still requires different-model exact review before
any integration; it is not self-approved.
