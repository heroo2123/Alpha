# A4 B1 exact-commit review preparation

Prepared 2026-10-02 while the sole B1 author retry waits for 04:20:10 UTC.
This is a review checklist, not a candidate verdict or an A4 qualification.
The controlling architecture is `docs/V11_R09_GATE3_A4_BOOTSTRAP_DESIGN_20261002.md`
at `ae0c772`; its independent design verdict is `PASS_IN_SCOPE` for the
architecture and B1 plan only. The B1 implementation has no candidate yet.

Before an independent reviewer starts, record the author's exact commit/tree,
clean worktree, changed-file hashes, author record, test log and outer terminal.
Review only those exact bytes in a detached checkout. Verify that the change is
limited to the B1 closure-specification checker, its tests and scope document;
reconcile separately with newer main after the verdict.

The reviewer should check these boundaries directly:

1. The parser accepts bounded in-memory JSON only. Raw duplicate keys,
   nonfinite numbers, bool-as-integer values, excessive size/depth/count/string
   lengths and integer overflow refuse. It performs no discovery, subprocess,
   native load, provider request or protected-path write.
2. Artifact IDs, paths, aliases and dependency endpoints are unique and
   unambiguous. Traversal, missing endpoints and mismatched inventory/library
   binding refuse. Native dependency cycles terminate with visited-node
   tracking; reachable declared edges are checked without claiming that the
   declared graph is complete.
3. Bootstrap, interpreter/loader, data-selection and A2/A3/MEMFS evidence
   fields are explicit proposal assertions. Fake `ACCEPTED` labels or shape-valid
   review pins cannot turn proposal input into trusted provenance.
4. Every result, including a structurally valid synthetic proposal, says
   `qualification=UNQUALIFIED`, `launchable=false`, `a4_pass=false`. No B1 result
   supplies A8/G3-L identities or an acceptance token.
5. Focused adverse tests cover malformed input, duplicate keys, ambiguity,
   missing transitive edges, cycles, absent bootstrap/data rules, wrong
   inventory binding and fake acceptance. Report exact tests, observed network
   attempts and any incomplete or resource-limited checks.

Independent PASS in this scope would accept only a structural engineering
checker. A2/A3 provenance, a trusted bootstrap/backend, native loader closure,
real decoder selection, A4 qualification and G3-L remain separate gates.
