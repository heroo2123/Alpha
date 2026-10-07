# V11 micro-canary pre-funding candidate — 2026-10-07

**Decision: NOT READY TO FUND. Execution remains masked.** This commit adds an
offline rehearsal and a decision inventory. It has no signer, credential reader,
exchange client, network call, service unit, activation parser, or live order
route. No V10 file is changed. The code cannot place or cancel an order.
The generated current-state inventory is
`docs/V11_MICROCANARY_DECISION_PACKET_20261007.json`; its missing evidence and
owner scope are left explicitly unresolved.

## What this candidate proves offline

`polymarket_scanner/v11/microcanary_prep.py` requires one concrete owner scope:
one market and token, owner decision identifier, amount, maximum loss and a
window of at most five minutes. The owner amount cannot exceed the candidate
hard ceiling of **$5.00**. One BUY dry-run order is serialized canonically and
bound to that scope. Price times quantity plus the declared fee ceiling must
fit both the owner amount and maximum loss. The order requires a source
timestamp no more than 15 seconds old. It is unsigned and explicitly marked
nonfinancial. These are proposed upper ceilings, not an owner-approved amount.

The local SQLite rehearsal journal accepts one order identity per journal
file; a different journal path, or the same path after the file is deleted,
is an independent identity and this candidate does not claim otherwise. A
repeated identical preparation is idempotent; a different order is refused.
An uncertain handoff, restart, stale source, expired window, operator abort,
missing account census, conflicting remote identity, regressed fill/loss,
open order after halt, and maximum-loss threshold retain a hold. A window
that expires while the remote order status is a known resting `OPEN` is
reported as `cancel_required=true`; a window that expires before any remote
status is known is reported as `remote_census_required=true`; cancellation
need is decided after that census. Reconciliation accepts a caller supplied
complete snapshot, measured in USD (`cumulative_fill_usd`,
`realized_loss_usd`) against the same USD notional the order was accepted
under, and is idempotent for the same snapshot. A remote terminal status
(`REJECTED`/`CANCELLED`/`FILLED`) can never regress to `OPEN`, regardless of
the local hold state. A delayed fill after `CANCELLED` stays terminal and is
labeled `DELAYED_FILL_AFTER_CANCEL`; loss above the order's at-risk ceiling
enters `LOSS_HALT` with an anomaly reason. That snapshot is **not authenticated
venue evidence**. Opening the journal for monitoring currently puts a prepared
or uncertain order into recovery hold; a separate read-only monitor is a live
integration prerequisite. A prepared order with no dispatch still needs a
truthful account census before it can be declared terminal.
The journal refuses an unmarked pre-existing file or a hard-linked alias of
its own file. `write_status()` publishes a redacted
atomic local status artifact. `credential_file_present()` checks only file
metadata and never opens or hashes credential contents.

To generate the inventory without credentials or network access:

```sh
python3 tools/v11_microcanary_packet.py /absolute/path/to/request.json
```

The request is a JSON object with optional `scope`, `evidence`, and
`credential_path`. Scope fields are `market_id`, `token_id`, `owner_amount_usd`,
`max_loss_usd`, `window_start`, `window_end`, and `owner_decision_id`. The
credential path is checked by metadata only. Evidence flags are caller claims,
not verified proofs, and a caller claiming every flag true cannot shrink the
`unverified_or_missing` list: this tool verifies nothing itself, so every
required item stays listed regardless of what is claimed (`missing_evidence`
narrows that to items not even claimed). The generated packet **always** says
`NOT_READY_TO_FUND`, even when all flags are supplied true. Neither it nor a
dry-run journal can serve as the reviewed activation artifact.

## Remaining prerequisites before an owner funding decision

1. **Current V11 evidence:** PAPER V11 READY is 9/11, with current-input
   requirements 8 and 9 still open. The initial champion is not accepted.
   `shadow_commission.py` deliberately forces forward qualification counts to
   zero until causal decision lineage and grouped outcomes are implemented and
   independently reviewed. Gate 3 G3-L remains NO-GO with 77 unqualified
   identities. The broader program ledger remains 91/200, formal 1/50. These
   are independent blockers; this packet grants no credit.
2. **Live safety integration:** an independently reviewed V11 live adapter must
   enforce the exact owner scope and hard caps at the last order submission
   boundary, bind a venue-supported idempotency identity to durable intent,
   refuse ambiguous re-posts, validate current market/source data, and reconcile
   the complete account and delayed fills before first submission and after
   restart. This offline journal is not connected to the production engine.
   Venue behavior, fees, minimum order size, cancel semantics and market
   availability must be checked against current supported interfaces.
3. **Independent controls:** deploy and prove a separately custodied cancel-only
   guardian, a tested timeout and max-loss halt with real account P&L, an
   operator-authenticated abort that actually cancels/reconciles resting orders,
   monitoring/alert delivery, clock and storage health, and ambiguous POST and
   restart recovery. The current offline `abort()` is a local hold, not a venue
   cancellation.
4. **Account and host acceptance:** verify the selected wallet/auth adapter,
   geographic/account eligibility, protected credential custody, account-wide
   balances/allowances/activity, isolated deployment and recovery, owner/operator
   identity, current release hashes and independent security/regression review.
   No production credential was read here; metadata presence does not prove
   validity or authorization.
5. **Owner gate:** the owner must explicitly name the market/token, funding
   amount, maximum loss, fee ceiling, one-order time window and abort contacts;
   review a current evidence packet; and separately authorize a reviewed,
   scope-bound activation artifact. Funding, unmasking and any live order remain
   separate owner actions. No amount or artifact is inferred from this candidate.

Source basis: `docs/V11_FORWARD_EVIDENCE_HARVEST_20261007.md`,
`docs/V11_SHADOW_READINESS_20261004.md`, R37–R49 in
`docs/V11_REQUIREMENTS_MATRIX.md`, and the current production engine,
reconciliation, guardian and shadow commissioning implementations. The proposed
$5 ceiling does not imply an economic recommendation or venue minimum.
