# Forward qualification candidate — 2026-10-07

This isolated-worktree candidate adds a nonfinancial, same-ledger path from an
original scoped admission through a complete forecast decision vector to one
grouped, exact-token payout outcome. `record_forward_admission` writes a
qualification receipt only after read-only verification. `evidence_status`
rechecks the receipt and counts each capture once for the exact plan target.
The receipt has no order, account, settlement, calibration or promotion authority.
Independent code review is still required before release or any readiness claim.

Qualification requires all partition markets from the original rule, one YES
decision and one label for each, exactly one payout winner, pinned feature and
model receipts before inference, decision times before label knowability, raw
closed Gamma responses matching exact condition and token IDs, and explicit
label source hashes. The admission must have been captured in the same archive
before those decisions and still valid at capture. Its event, scope, rule,
release, bundle, protected model epoch/state, model leases and rule head must
match the frozen plan and decision group. The archive's complete event label
history is scanned; any duplicate or conflicting label for a partition market
fails the group. Bounds fail closed.

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
A read-only test view chooses one label per market and uses its actual source
receipt as knowability to exercise the rest of the real Oct 5/6 record shapes;
that view is an adversarial probe, not an acceptance or backfill. Production
qualification always reads the unchanged complete archive.
