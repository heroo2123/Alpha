# Independent Brain offline repair review — 58b0b79

**PASS for the requested offline repair scope. Recorded before integration.**

Astra/high independently reviewed Sol/high commit
`58b0b79971c49cc265148ffcf550cdeb31005fb9`, tree
`dba54e265e04b49b7e6b1291c4e7f6ed73bc664c`, on 2026-09-30 in the clean
`/home/alphaadmin/AlphaV11_BrainReadiness/Alpha` worktree. No candidate files
were changed. This closes the three P2 findings and P3 handoff correction in
[the rejected review](V11_BRAIN_OFFLINE_READINESS_REVIEW_a8362ad.md).

## Findings resolved

1. `_score_rows` binds each `(station, local_date)` to one city-day and split.
   A second alias is rejected both within one split and across DEVELOPMENT /
   HISTORICAL_CONFIRMATION. Valid shared HIGH/LOW city-days retain one weight;
   separate station/day identities remain usable.
2. `evaluate` builds event-to-bundle identity from the consistently sorted rows
   and observations. Every cost row and markout row must match its observation's
   exact candidate bundle. Reversed input order and both directions of HIGH/LOW
   substitution were checked for C and F. Correct numeric reports still work;
   unknown events and mixed-family rows under a single markout bundle fail closed.
3. Markout requires an actual dictionary request with namespace `V11_PAPER`.
   Missing, null, malformed, LIVE, boolean, differently cased and padded
   namespace inputs fail before numeric diagnostics are returned.
4. The handoff now requires separately reviewed native sampled-trajectory
   feature binding under R09, replacing the ambiguous exact-day adapter wording.

No remaining P1/P2 blocker was found in this repair scope. The evaluator remains
caller-supplied offline diagnostic machinery: report hashes do not independently
prove ledger truth, calibration, causal availability or cohort preregistration.
Those documented limits are unchanged and are not accepted as real evidence.

## Independent verification

- Exact-candidate affected suites: **145 passed, 1 skipped in 57.33 s**:
  `test_v11_brain_readiness.py`, `test_v11_multimodel_panel.py`,
  `test_v11_execution_costs.py`, `test_v11_fill_markout.py`.
- [Independent synthetic probes](V11_BRAIN_OFFLINE_READINESS_REVIEW_58b0b79_probes.py):
  **23 passed in 0.32 s**, including positive controls and withheld authority.
  The review harness initially used pytest's reserved `request` parameter name;
  that collection-only harness error was corrected before these results.
- The same independently constructed inputs reproduce all five rejected-code
  defect manifestations at `a8362ad`: two aliases, cross-family numeric cost,
  cross-family numeric markout, and LIVE-namespace numeric markout.
- Exact repair diff passes `git diff --check`; candidate worktree remains clean.
  Main `7311713` has no divergent changes in the three candidate paths.
- [Terminal verdict](V11_BRAIN_OFFLINE_READINESS_REVIEW_58b0b79_terminal.json)
  pins the exact commit, tree, probe hash and review scope.

To replay the probes at the candidate, set `PYTHONPATH` to that worktree and run
its existing development virtualenv's `python -m pytest -q -p no:cacheprovider`
against the linked probe file. All inputs are synthetic and local.

## Acceptance boundary

This verdict permits compatible local integration of the Brain candidate under
the standing coordinator instruction. It grants no Gate 3 launch, SHADOW
qualification, IFS/AIFS real learner admission, model installation, promotion,
financial authority or publication. Gate 3 `1693dd5` retains its separate merge
hold. Main publication hold remains. Forward calibration and owner/root model
authority are still open. **91/200 (45.5%), formal 1/50; NOT_READY_TO_FUND**.
