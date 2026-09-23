# V10 baseline forensics — preserved evidence analyzed

The owner-completed control snapshot was independently read and verified on
2026-09-23. The authoritative specification remains the full final-reviewed file
with SHA-256 `a0e16d9bd7344c943a54a16a53c6757662363d93642f6e5cb7953cd047659b4a`.
The snapshot handoff does not narrow the implementation scope.

## Evidence index and privacy

This public repository contains the reusable reader, tests and this redacted
index. The private report contains the actual financial aggregates, station and
probability slices, schema inventory, dated-reference comparison and lane funnels.
Raw control evidence and private source documents are not published here.

| Artifact | SHA-256 |
|---|---|
| Immutable control database | `3a3c1efe0e3021a609800f8c71b9d324fdb0a6e75990e6819321176cd966ed71` |
| Private six-file snapshot archive | `409115e0afeee5d317c8c682fd2c0e3c9ef2323e66b8d39f845f16d84dfe05e7` |
| Schema inventory fingerprint | `6ce56bc0ea97434d1e5adbe79b8f7955cf2bd2744eb9ac341f2da436e24f59b8` |
| Alpha_V10_Forensic_Baseline_20260923.md | `e2cf6cf456f68b69052bbe593e88c26e43b2be8f461154447efa33f6572f9c4e` |
| Alpha_V10_Forensics_20260923_v2.json | `2805cd68e5dedae9e2db06c4c1a29aa015410116b1ef6345cc6be0cd429a860b` |

Database, schema, all context hashes and copied-database `quick_check` passed.
The backup includes committed WAL under one pinned SQLite read transaction.
Context reads bracket the backup and are not claimed transaction-atomic.
The off-host reader uses `mode=ro&immutable=1` and `query_only`; it never opens
the live V10 store. Compressed research records were decoded with bounded pure
functions and checked against their hashes, SQL identities and authority flags.

## Identity and runtime limits

The clean source-equivalent control commit remains
`5bbac24759349714d4521faf9e087a14c5c0ae05`, tree
`d5d2b806e273f11e2f832940f483a5f656462584`, equivalent to pinned public base
`f5f0661307a8d426a20dbf8308d18a0ee403e9b3`. Earlier deployed-file checks cover
221 application/lock files. The captured marker and status agree with the
launcher's older release label `2a1fe2b0d199ca87fabc1f119fae6ee4902f73da`.
That label is preserved; it is not substituted for the verified source tree.
Exact historical runtime release epochs cannot be inferred from that label.

**Current control cycle health is NOT verified.** Captured status is materially
stale despite `cycle_ok=true`. Read-only process/cgroup inspection found severe
memory pressure with usage above `MemoryHigh`; the full cause remains unproven.
No V10 restart, source/config edit, memory-limit change or permission change was
performed. No V11 workload was placed alongside it. Host deployment requires
separate resource isolation and demonstrated headroom.

The executor remains masked/inactive; the production controller is inactive.
Captured authority flags are false. Protected journal access, installed wheel
byte attestation, account entitlement and balances remain separate limitations.

## Findings and their permitted use

The private baseline covers all core positions/signals and auxiliary tables;
reconciles signal/event/token/side identity, fill capital, proceeds and P&L;
separates protocol, release/config labels and quarantine cohorts; and reports
open, resolved, partial and no-fill records with explicit denominators.
Quarantine status overrides a stale `VALIDATED` flag. Historical decisions and
current positions have different lifecycle semantics, so stage totals are not
forced into one cohort or converted into unsupported attrition percentages.

The selected forecast sample exhibits broad losses and descriptive raw-frequency
overconfidence. This is development evidence, not full-universe calibration,
causal rule attribution, an untouched holdout or live execution evidence.
Stored settlement bindings were checked, but venue labels were not independently
re-attested. Missing country, initialization time/forecast horizon, exact
settlement horizon and event-risk state remain unknown. Derived local-day lead
is explicitly different from model run age and contractual settlement time.

Zero-lane analysis distinguishes semantic/finality gates, source collection
failures, uncalibrated research, missing maker delivery receipts and absent
historical evaluation denominators. Zero trades alone never establishes lack of
opportunity. PWS was disabled in this control; it proves no observation lead.

## Frozen development hypotheses and continuation

Every record and inspected slice in this snapshot is DEVELOPMENT data.
Candidate hypotheses concern coherent/smoothed probabilities, exact HIGH/LOW
semantics, price/gap/station heterogeneity, coverage/latency and incremental PWS
value. No profitable cutoff, station whitelist or promotion threshold is chosen
from these post-hoc slices. Legacy trading-rule causal verdicts remain UNPROVEN.
Safety/finality/identity constraints remain requirements regardless of P&L.

Predeclare model metrics and candidate comparisons before later chronological,
event-disjoint and city-day-disjoint evaluation, with availability cutoffs,
label provenance, repeated-holdout accounting and appropriate uncertainty.
Weak evidence means no promotion. Raw certain forecasts losing are not repaired
by score clipping or by treating PWS as settlement authority.

Continue the full specification dependency order after this foundation:
evidence/runtime integration, source registry/rules, coherent valuation,
controlled learning, event/EV/coordinator/risk, strategies/exits/maker, guardian,
permitted deployment and technical/unfunded acceptance. This report is not the
final deliverable and does not establish readiness to fund.

## Reproduction and tests

Run `python -m polymarket_scanner.v11.forensics --snapshot <private-copy>
--output <new-private-json>` off-host. The CLI refuses to overwrite evidence.
The detailed JSON includes schema, all requested available slices, concentration,
selected-sample reliability scores, payload integrity and exact-ID-linked funnels.

`tests/test_v11_snapshot_forensics.py`: 18 tests pass, including quarantine
override, SQL/payload tamper, unknown funnel attribution, calendar-versus-model
horizon distinction and timestamp-order-independent realized drawdown. Full V11
foundation selection: 58 tests pass. Fixtures demonstrate code behavior only.
