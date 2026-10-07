# Forward qualification candidate — 2026-10-07

This isolated-worktree candidate adds a nonfinancial, same-ledger path from an
original scoped admission through a complete forecast decision vector to one
grouped, exact-token payout outcome. `record_forward_admission` writes a
qualification receipt only after read-only verification. `evidence_status`
rechecks the receipt and counts distinct original admissions and distinct grouped
outcomes for the exact plan target. Repeated captures of one admission or payout
group are refused, and both distinct counts must meet the sample target.
The receipt has no order, account, settlement, calibration or promotion authority.
Independent code review is still required before release or any readiness claim.
Forward qualification currently fails closed with
`FORWARD_PROTECTED_INTERVAL_UNPROVEN`: the protected certification manifest has
no historical transition record and cannot be fenced by the ledger append.
No real forward sample can receive credit until a separately reviewed authority
mechanism proves continuous eligibility through durable capture publication.

Qualification requires all partition markets from the original rule, one YES
decision and one label for each, exactly one payout winner, pinned feature and
model receipts before inference, a durable complete capture before every raw
closed-market receipt and label knowability, raw
closed Gamma responses matching exact condition and token IDs, and explicit
label source hashes. The admission must have been captured in the same archive
before those decisions and still valid at capture. Its event, scope, rule,
release, bundle, protected model epoch/state, model leases and every admission
source, rule and station head must match the frozen plan and decision group
through the complete capture. The capture revalidates the original admission
after all child decisions and publishes under atomic same-ledger head guards.
The archive's complete event label
history is scanned; any duplicate or conflicting label for a partition market
fails the group. Malformed labels refuse qualification without aborting status.
Bounds fail closed.

## Retained Oct 5/6 result

Read-only inspection of the local `AlphaV11_BrainForward` source ledgers found:

| Day | LABEL rows / markets | Markets with duplicate LABEL rows | Selected labels whose `knowable_at` precedes raw receipt | Original `admission_ref` |
| --- | ---: | ---: | ---: | --- |
| 2026-10-05 | 19 / 11 | 2 | 11 / 11 | absent |
| 2026-10-06 | 16 / 11 | 5 | 11 / 11 | absent |

The unchanged ledgers therefore **do not qualify**. Both fail first on label
duplicates; even selecting one label per market would leave pre-receipt
`knowable_at` and the missing original admission pin. No retrospective pin or
timestamp was created. The Brain forecast decisions are also marked
`FORECAST_ONLY_NO_EXECUTABLE_ECONOMICS`; this candidate does not claim economic
proposal, account reservation, PWS, independent label attestation, or PAPER
readiness credit from them.

Tests use only local retained SQLite reads and an isolated synthetic store.
A read-only test view chooses one label per market, uses its actual source
receipt as knowability, and omits raw history to exercise other lineage checks.
That deliberately incomplete proxy is a diagnostic probe, not an acceptance or
backfill. Production qualification reads the unchanged complete archive.

The original protected certification and model assessment is pinned in the
admission; capture revalidates it against the protected readers. Those reads
do not prove uninterrupted validity through publication. The qualifier rejects
every such capture until an independently custodied historical interval proof
and publication fence are available. Same-ledger head checks remain in force.
