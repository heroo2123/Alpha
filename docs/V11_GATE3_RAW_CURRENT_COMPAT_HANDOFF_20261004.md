# Gate 3 RAW current-main compatibility repair — 2026-10-04

Status: PROVISIONAL / UNQUALIFIED / independent exact review still required before any G3-L credit.

Current-main post-merge verification exposed a strict-schema mismatch after reviewed transport integration: closure evidence now includes durable `read_bytes`, while the reviewed RAW binding schema predated that field and rejected otherwise-valid current-main closures with `BINDING_CLOSURE`.

The repair:
- admits exactly the reviewed closure field `read_bytes`;
- requires exact `int` type;
- for the currently permitted `SyntheticResponseStream`, requires `read_bytes == delivered_bytes`;
- adds a focused current-transport regression asserting the durable closure value and successful RAW release.

Validation in the isolated worktree:
- RAW focused suite: 51/51 normal;
- RAW focused suite: 51/51 optimized;
- adjacent ledger/store close/restart/custody/clock selection: 38 passed, 123 deselected;
- `git diff --check`: PASS.

This repair grants no provider authority, physical storage qualification, G3-L identity credit, capture authority, SHADOW admission, funding, or live execution authority. A genuinely separate exact review remains required before formal qualification.
