# A2/A3 offline evidence decision handoff — 2026-10-02

Route: ASTRA_HIGH. This is an architecture and acceptance decision about
contradictory retained package evidence, not permission to qualify A2/A3.

Use `docs/V11_R09_GATE3_A2A3_OFFLINE_DOSSIER_20261001.md`,
`docs/V11_R09_GATE3_A2A3_RETAINED_AUDIT_20261001.md` and its JSON,
`docs/V11_R09_GATE3_A4_BOOTSTRAP_DESIGN_20261002.md`, and the exact current
main tree. Verify actual local artifacts and any newer work before deciding.
The original cached `eckitlib` wheel is absent; 25 installed library bytes
disagree with their RECORD hash/size fields across three related environments.
The historical mapped set and static `DT_NEEDED` graph do not establish actual
loader closure or independently authenticated provenance.

Deliver a bounded, offline-only decision record:

1. State which A2/A3 claims remain impossible from retained bytes alone and
   which narrowly scoped observations can still be tested locally.
2. Give exact required external/authenticated artifacts for the original
   `eckitlib` bytes, every RECORD discrepancy, interpreter/toolchain and
   complete installed closure. Identify who must supply or accept each item.
3. Separate a safe implementable next slice, if any, from evidence acquisition
   that is owner/external blocked. Specify exact independent review gates.
4. Preserve `A2/A3 UNQUALIFIED`, `A4 OPEN`, `A8 UNQUALIFIED`, `G3-L NO-GO` unless
   genuine new evidence crosses those boundaries. Do not infer acceptance from
   matching related installations or synthetic graph consistency.

No network/provider request, package installation, authority/service change,
real financial action, or modification of V10, AxiomTrade or the private
FINAL-REVIEWED master. The separate B1 author retry is already live in its own
worktree until the 04:20 UTC provider reset; do not duplicate or edit it.
