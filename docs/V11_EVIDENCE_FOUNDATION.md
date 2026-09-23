# V11 evidence foundation: first implementation increment

This code runs independently of V10. It grants no financial authority and has
not been deployed as a trading, paper, shadow, or learning service. The private
final-reviewed specification remains authoritative. These notes describe the
implemented subset and its limits, not substitute requirements.

## Components and verification

| Component | Implemented behavior | Evidence / remaining integration |
|---|---|---|
| `tools/v11_snapshot.py` | Bounded SQLite read transaction and online backup; committed WAL included; copy-only integrity checks; private, immutable-by-permissions output with hashes | Concurrent WAL writer, bounds, preservation, symlink and tamper tests in `tests/test_v11_snapshot_forensics.py`; actual V10 capture blocked by source permissions |
| `v11/forensics.py` | Snapshot-only paper scorecard, protocol/quarantine separation, accounting reconciliation, forecast slices, known lane funnels and selected-trade probability diagnostics | Synthetic schema/outcome tests; no actual control scorecard yet; rule causality, release epochs and independent settlement re-attestation remain unproven |
| `v11/evidence.py` | Private per-namespace append-only archive, receipt-based availability, source/revision identity, release-bound decisions, exact-input replay, explicit lane and source outcomes | `tests/test_v11_evidence_foundation.py`; strategy adapters, complete rule/registry bindings, production scheduling and external attestation remain pending |
| `v11/collection.py` | Anonymous allowlisted public GETs, bounded total attempt time and response size, limited transport retries, no immediate 429 retry, successful captures committed before later failures | `tests/test_v11_collection.py`; raw responses are not normalized/certified source inputs; persistent provider cooldown and runtime orchestration remain pending |
| `v11/measurement.py` | Visible-depth bid/ask cost, fee-aware counterfactual markouts at explicit horizons, descriptive Brier/log loss | Missing depth/fees/stream health yield unknown; no hypothetical quote or public trade proves a fill; no empirical strategy acceptance |

All application paths above are under `polymarket_scanner/`. Components use
existing locked dependencies; no dependency upgrade or V10 source change is
required. Development tests are run off the control host.

## Snapshot and forensic analysis

An already authorized operator runs the standard-library snapshot tool with
absolute source/destination paths and separately verified release/tree/config
hashes. The destination must be new. Default bounds are 45 seconds, 1 GiB, and
64 MiB disk reserve; resource isolation and host headroom must also be checked
before invoking it. Read permission is not granted by this program.

The SQLite source is opened `mode=ro`, with `query_only`, a pinned read
transaction and bounded backup chunks. It never invokes a WAL checkpoint on
the live database. Only the private copy is switched to DELETE journal mode so
it is standalone. A failed attempt removes its own temporary output and never
retains a misleading complete manifest or deletes previous snapshots.

Run analysis off-host, from the repository, on a completed authorized copy:

```sh
python -m polymarket_scanner.v11.forensics \
  --snapshot /absolute/private/control-snapshot \
  --output /absolute/private/v10-forensics.json
```

Keep the database, manifest, raw scorecard, runtime status and journals private.
Do not commit them. The reader refuses mutable snapshots, companion WAL files
and mismatched hashes. Inputs supplied by an operator are recorded identities,
not proof that the deployed runtime uses them.

## Causality and target boundaries

Availability is local receipt time, not a backdated sensor observation or model
issue time. Old records and revisions stay distinct. Unknown historical arrival
times cannot become decision features. Labels cannot become features. A saved
decision references exact input hashes and code, configuration, model-bundle and
rule identities. Replay checks recomputed explanations against that pinned set;
the caller must supply the corresponding deterministic evaluator. Arbitrary
caller code is not sandboxed or independently certified by this API.

Next official observation, final exact-contract settlement and executable
repricing are separate targets. `OBSERVATION_ONLY` decisions cannot be accepted
as research trades. The archive does not itself approve economic models; every
record says no financial authority and decisions say not submitted. Later EV,
calibration and exit-model gates must be integrated before any trade eligibility.

Visible depth estimates are counterfactual measurements, never fills. A later
official confirmation cannot replace absent bids, fees, or exit depth. Unknown
measurements stay unknown; synthetic records remain labeled synthetic. Group
counts do not certify statistical independence or calibration.

## Resource and trust limits

One archive belongs to one V11 paper/challenger/ablation namespace. Opening a
foreign/V10/live database is refused before schema or WAL mutations. Existing
files must already have private permissions. Limits bound record size, row
count, file growth, disk reserve, source count, attempts and response time.
Capacity exhaustion preserves existing evidence and raises an explicit error.
There is no automatic retention purge or history rewrite.

SQLite append-only triggers and content hashes catch accidental mutation; they
are not a security boundary against a process with file ownership. External
release/snapshot attestation and acceptance remain required. Secret-key field
rejection is an additional guard, not authorization to archive credentials.

The public collector's endpoint list is deliberately narrow. Current primary
references checked on 2026-09-23: [AWC API](https://aviationweather.gov/data/api/)
and [Gamma event API](https://docs.polymarket.com/api-reference/events/list-events).
AWC documents bounded query scope, custom user agents and rate limits. Continuous
collection, MADIS access, usable station latency and contract source certification
are not established by those documents or these unit tests.
