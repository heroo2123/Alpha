# Gate 3 checker: systematic repair after Astra/high exact review

The independent Astra/high review of exact `fe854fc5486d4ea24eda442308034b150feeaeda`
is **CHANGES_REQUIRED**. The prior three repairs pass and all 91 committed
tests pass, but independent synthetic probes reproduce nine exceptions and
19 incorrect satisfied results. Read
`V11_R09_GATE3_PREFLIGHT_CHECKER_REVIEW_fe854fc.md`, its `.verdict.json`,
`.binding.json`, `.repro.py` and `.repro.json` before editing.

Do not repeat host inspection or the review already completed. Recover actual
worktree HEAD/status and any live author first. Implement in
`/tmp/alpha-v11-gate3-preflight-checker-repair-20261002`, presently clean at
`fe854fc`; preserve newer work if present. No other author was active at handoff.
Scope: checker, focused tests, and a concise repair note if necessary. Do not
edit the accepted protocol, private evidence, provider paths/dates/limits or
main implementation. Main's review/checkpoint commit is documentation only.

Repair all four finding families coherently:

- Total refusal for non-object JSON roots without weakening result invariants.
- Reference/head validation and explicit admissible GEFS status handling;
  `HELD`, `DENIED`, unknown/empty states and fake false-valued proofs refuse.
  Keep known ECMWF holds, all scope requirements and real blockers intact.
- Audit every declared field for type, finite values, string/container bounds,
  reference shape and nested key sets. Bound raw parse work. Reject overflowing
  JSON exponents as well as named nonfinite literals. Use exact scalar types.
- Reject naive clocks, non-boolean monotonic flags and malformed terminal
  exit/error fields. Preserve offset-aware clocks and all earlier overflow,
  resource, replay/reset and forbidden-promotion regression protections.

Positive examples must remain explicitly synthetic and non-executable. The
offline checker cannot establish provider rights, custody, physical storage,
clock calibration or a genuine completed exact-byte review by inspecting a
JSON reference. Do not invent such evidence or confuse schema satisfaction
with permission. Do not add a real transport, external client or CLI/network
probe. No provider request is authorized.

Reproduce failures with rebound hashes/lengths, so changed-byte refusal does
not hide the policy defect. Add meaningful systematic malformed-input tests,
run focused tests and relevant adjacent offline tests, commit the repair, and
obtain different-model exact-commit review before integration. Keep the real
package refused and score at 91/200. InventoryTransform SHADOW and Brain lanes
remain independent; do not start additional heavy workers under this headroom
if that would slow weather.

The original Astra review completion is recorded by its parent coordinator
after this handoff returns. Its `.binding.json` identifies exact process/log
and output locations. Verify the exact `route_end route=ASTRA_HIGH rc=0 ...
log=planner-20261002T110242Z-2-astra_high.log` record; copy that original parent
return-code evidence plus final log/output hashes into a separate review
terminal. Do not assert exit zero from pytest or a shell verification command.
If the parent records failure or no terminal, retain that fact and do not
claim a sealed review PASS. The findings already forbid integration regardless.

Normal substantive implementation routing is appropriate; architecture and
the acceptance decision above are complete. No stronger-model escalation is
required to implement these specified refusals. Preserve all SAFE NONFINANCIAL,
V10, provider-control, authority-custody and unfinished-work boundaries.
