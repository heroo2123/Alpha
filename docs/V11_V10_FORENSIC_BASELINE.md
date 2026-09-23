# V10 forensic baseline status

Status: **BLOCKED ON AUTHORIZED CONSISTENT CONTROL SNAPSHOT** (2026-09-23).
No latest realized counts, calibrated results, causal legacy-rule verdict, or
V11 threshold recommendation has been inferred from inaccessible runtime data.

## Preserved identity evidence

The authorized local control checkout is clean at
`5bbac24759349714d4521faf9e087a14c5c0ae05`, tree
`d5d2b806e273f11e2f832940f483a5f656462584`. The designated public base
`f5f0661307a8d426a20dbf8308d18a0ee403e9b3` has the same tree. Hash comparisons
matched all 221 deployed application and dependency-lock files. This establishes
source equivalence, not complete runtime equivalence.

Readable installed distribution metadata matched the 23 pinned runtime package
versions in `requirements.txt`; pip 24.0 is additionally installed. This checks
version metadata, not each wheel's installed bytes or an import-time attestation.

| Readable deployed artifact | SHA-256 |
|---|---|
| launcher | `6b531d769d12e498426857ef213eaf70cdac1c10dd42c18edcc011b86659826c` |
| systemd paper unit | `e5d201296d6e4dd04139ae1b2d134824862279df5ae359bf20b3d72087130bc7` |
| runtime dependency pins | `8ce2ef32a60d43527be60255f5f2875c7f7fa72b2c6c2aac7fe330ac547cce5f` |

The readable launcher expects an older release marker,
`2a1fe2b0d199ca87fabc1f119fae6ee4902f73da`. The actual protected release file,
status and database remain unreadable. Preserve both identities and resolve the
distinction from captured runtime evidence; do not relabel the marker as the
source tree or edit the control to make them agree.

Systemd reports the paper service active with no restarts; this is not proof of
successful current collection/settlement cycles. The executor is masked and
inactive; the production controller is inactive. The paper unit denies execution
credential/state paths. No protected credential was read and no balance or
account entitlement was attested.

## Baseline reader delivered; findings still pending

The reader in `polymarket_scanner/v11/forensics.py` requires a verified snapshot
from `tools/v11_snapshot.py`. It separates execution protocol and validation
epochs, checks signal-position identity and core cash arithmetic, reports
no-fills and reason counts, and describes future-forecast slices without choosing
cutoffs from them. Global decision reasons are not attributed to a lane without
persisted linkage. Maker/result-lag auxiliary records do not enter core P&L.

Descriptive aggregate P&L is explicitly paper development evidence. Quarantined
rows retain their separate epoch. Missing station/day/horizon/source/regime
fields remain unknown. Stored outcomes are not independently re-attested venue
labels. Probability scores use selected trades and therefore cannot establish
full-universe calibration. Rule causality remains unproven until further tests.

Required continuation after capture: reconcile complete historical epochs and
settlements, inspect funnel coverage and concentration, freeze development
hypotheses, then predeclare later/event-disjoint validation. Do not finalize V11
calibration, economic thresholds or promotion rules from synthetic test fixtures.

## Reference synthesis guiding implementation

The four supplied references were read in full, including tables and figures.
Their source files remain private. The weather architecture recommendation
motivates coherent distribution/nowcast evidence and strict source semantics;
the Toronto incident motivates exact-bucket versus crossing distinctions and
inventory accounting; the wallet comparison motivates cost-aware strategy
attribution and uncertainty rather than copying headline P&L; the market-making
paper supplies modeling hypotheses, not proof that its assumptions hold in
weather order books. None overrides the final-reviewed specification or grants
empirical V11 acceptance.
