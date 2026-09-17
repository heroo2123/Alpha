# Weather production application — operator guide

Repository implementation and target-host acceptance are separate gates. This guide describes the canonical composed production path. Deploy only a separately approved, verified immutable release; the historical checkpoint PR is not a release.

For the private button interface and per-trade confirmation, use [Telegram operator panel](TELEGRAM_OPERATOR_PANEL.md). Its three components are `scanner`, `controller`, and optional `execution`; the combined text-only `signals` path below remains compatible when no operator-control grant is configured.

## Components and operating modes

The canonical module is `polymarket_scanner.production`. It composes source validation, signal observations and a separate execution worker. It does not run the final_vN PAPER service or simulate fills behind a live label.

- `LIVE_SIGNALS`: real public market/weather detection, useful Telegram messages, durable invalidation and observed signal outcomes. No trading credentials or signing libraries are needed by this component. Delivery never implies the operator traded.
- `LIVE_EXECUTION`: the same signal component plus a separately started, authenticated execution worker. Authority depends on intended EOA identity, actual credentials/allowances, explicit configuration-bound activation, configured risk limits, successful reconciliation and no active stop/fault.
- Historical PAPER/SIMULATION tools remain explicit development entrypoints. They use a separate database. Their results, especially uncertified legacy maker history, never enter actual account performance.

Examples are in `config/production/`. The execution example deliberately contains null account/risk/fee-policy values: there is no invented bankroll, loss budget or allocation. Every financial limit must be chosen by the operator. Changing the configuration file immediately removes a running worker's opening authority as well as invalidating the activation digest. Example raw model-gap thresholds are heuristics, not calibrated probabilities or profitability estimates.

The signals process may read a non-secret execution status file. In production use only the independently provisioned host protocol through `deploy/production-host-control.sh`; retired weather PAPER deployment scripts refuse mutation. It cannot read the execution credential file. The execution process reads the signal database without write access and independently refetches the contract, weather evidence and CLOB snapshot before signing. Separate OS identities and release-specific virtual environments enforce this separation on the host. Signal and execution databases and reports require distinct state directories; a shared read-only group grants the worker access to signal DB/WAL/SHM and grants signals access to execution status, never the execution journal or signing credential. In LIVE_EXECUTION both processes use the same non-secret configuration digest; choosing the signals component does not load execution credentials.

## Signal setup

1. Independently provision the reviewed host authority, release approval and component policy described in the host-trust documentation. The application release cannot bootstrap or replace its own privileged verifier.
2. Prepare a clean signals environment from `requirements-runtime-hashed.txt` and sealed source at the approved immutable SHA. Use the host authority's approved source/import mechanism for isolated `-I` startup; do not add PYTHONPATH.
3. Copy the signal configuration to an operator-owned config path. Create the Telegram file privately with JSON fields `token`, `chat_id`, and `operator_user_ids` (numeric Telegram user IDs). Never put the real token into Git, shell arguments, examples or logs. Credential files must be mode0600, owned by the appropriate service/operator.
4. After separate deployment authorization, the signals component starts as:

```sh
/path/to/signals/venv/bin/python -I -m polymarket_scanner.production signals --config /etc/alpha-weather/config.json
```

The independently rendered service supplies a minimal environment, not the interactive shell's environment. PYTHONPATH/PYTHONHOME/PYTHONUSERBASE, LD injection, proxies, custom CA variables and dotenv loading are rejected. Signals use the `weather-paper-runtime.lock` writer lease in their database directory; this also prevents an old runtime sharing that state directory.

Signals contain precise side/token/price, fee estimate, visible capacity, station/date, evidence classification, expiry, skip conditions and market URL. Quotes are snapshots. Manual users must not pay more or assume later executable liquidity. Unknown or expired evidence is invalidated visibly where Telegram allows an edit. Deleted messages are terminal synchronization outcomes; failed edits have bounded retries and visible pending/escalated status. A lost send response is UNKNOWN, not automatically resent.

Discovery reports complete Gamma enumeration separately from the strict supported semantic subset, including unknown weather templates, rejection reasons and census age. Unsupported contracts are skipped; parsing is not relaxed to improve coverage. Events and unresolved signal outcomes use durable rotating selection so a fixed small prefix cannot monopolize evaluation.

The signal database binds receipts to the original numeric bot ID and chat ID before any send. Changing either identity while reusing that database fails startup; rotating the same bot's token is supported. Preserve the old bot/chat until its terminal outbox is drained. A new chat uses a new signal database and must not inherit old message IDs.

## Historical cutover

Choose a fresh production signal database. Starting directly on a legacy weather database fails with `LEGACY_DATABASE_IMPORT_REQUIRED`; otherwise old delivered alerts could remain visibly actionable while the new application reported an empty outbox.

During a separately authorized, quiescent cutover, use the original bot/chat credentials and import the predecessor snapshot:

```sh
/path/to/signals/venv/bin/python -I -m polymarket_scanner.production import-legacy --config /etc/alpha-weather/config.json --legacy-db /private/operator/predecessor-weather.db --legacy-bot-id ORIGINAL_NUMERIC_BOT_ID --legacy-chat-id ORIGINAL_CHAT_ID
```

This command performs no network calls, takes both writer leases, reads a consistent SQLite snapshot (including committed WAL), and leaves source tables unchanged. It archives all weather research tables, original evidence, maker settlements and audits as excluded history. Delivered alerts without a completed terminal synchronization enter the canonical durable invalidation outbox, with their original message IDs and retry budget. Subsequent separately authorized signal startup drains that outbox. Already applied edits remain applied; exhausted retries remain visible as escalated. Re-import is idempotent; source mutation after import is an explicit conflict. None of these rows enters execution accounting or observed production signal outcomes. `/stats` counts excluded history and full local exports preserve it. Inspect migration counts and pending/escalated edits before accepting cutover.

## Execution setup and activation

Two explicit execution adapters are supported by this development branch: the retained direct EOA route (`wallet_type=EOA`, wallet=signer, signature type 0) and a restricted Deposit Wallet Session Key route (`wallet_type=DEPOSIT_WALLET`, signature type 3). Legacy Safe/Proxy identities and silent owner-key fallback remain unsupported. Both routes require a dedicated account/wallet with no unmanaged activity while opening authority is enabled.

The Deposit route requires a distinct Session Key EOA, `session_scopes=["CLOB"]`, the externally verified venue authorization expiry, and a separately reviewed `session_exclusive_until` commitment that no owner/manual CLOB writer or other trading session is active for the wallet. The executor credential file contains only the Session Key private key and that session's CLOB L2 credentials. Deposit Wallet Owner and Builder credentials remain off-host. The restriction is narrower signing authority, not protection against trading losses.

The operator must separately establish valid existing API credentials, collateral funding and necessary allowances. The application does not create wallets/API credentials, authorize Session Keys, fund accounts, approve unlimited allowances, bypass geoblocking, or repair allowance failures by retrying an order. Session authorization/revocation is an owner/Builder-device operation.

Create an isolated execution environment from `requirements-execution-hashed.txt`. Set all mandatory account/risk settings and an explicit `fee_policy`, a suitable HTTPS Polygon JSON-RPC endpoint with finalized-block support, private credentials and the independent host policy. The credential schema remains `private_key`, `api_key`, `api_secret`, `api_passphrase`; which signing key those fields represent is selected only by the protected wallet adapter configuration. Never copy real values into a report or test.

Run the read-only preflight under the execution identity:

```sh
/path/to/execution/venv/bin/python -I -m polymarket_scanner.production preflight --config /etc/alpha-weather/config.json
```

For pre-funding account/security compatibility, add `--allow-unfunded`. That
explicit mode may succeed with `account_reconciled=true` while leaving
`funding_ready=false`, `reconciled=false`, and financial authority false. Before
any financial activation, rerun ordinary `preflight` without the flag; the strict
form additionally requires collateral balance and allowance sufficient for at
least the configured per-order limit. Preflight performs no exchange order POST
or DELETE; it may update the local reconciliation/audit journal.

It checks credentials/account identity, geographic/closed-only eligibility, balances/allowances, orders/trades/positions, confirmed fills and outstanding/unknown order recovery. The Deposit route additionally requires unexpired CLOB-only Session Key metadata, an unexpired dedicated-wallet exclusivity assertion and a complete wallet-wide public TRADE witness. Authenticated CLOB order/trade history is session-scoped; public `/v2/activity` and positions are wallet-scoped. Any wallet TRADE not accounted for by the configured session's confirmed history becomes a sticky external-activity fault. This still cannot enumerate an unfilled resting order owned by another session, so the exclusivity assertion remains mandatory.

Preflight does not submit financial orders. Missing settings fail with field-specific configuration errors. A target-host geoblock is an eligibility failure; moving traffic through a proxy is not an approved workaround. The historical US VM must not be assumed eligible.

Generate an activation request into a review file distinct from the active file:

```sh
/path/to/execution/venv/bin/python -I -m polymarket_scanner.production activation-request --config /etc/alpha-weather/config.json --output /private/operator/activation-review.json
```

After independently reviewing the wallet, config digest, limits, readiness and host acceptance, the operator installs the exact request at `activation_file` with approved custody/mode. Merely selecting LIVE_EXECUTION or starting a process grants no authority. Then the separately authorized execution service uses:

```sh
/path/to/execution/venv/bin/python -I -m polymarket_scanner.production execution --config /etc/alpha-weather/config.json
```

## Limits and order lifecycle

Limits are denominated in collateral units except prices/slippage/fee per share and the integer count/time limits. `capital`, `per_order`, `per_station_day`, `max_loss`, `daily_loss`, `max_price`, `max_slippage`, `max_fee_per_share`, `legging_loss`, `max_open_orders`, `max_positions`, and `max_maker_rest_seconds` are all mandatory. Daily limits use UTC days. Full position cost is treated conservatively as possible loss; unrealized winnings are not reusable capital.

The worker reserves limit-price cost and the applicable fee allowance transactionally, independently refreshes evidence and market parameters, signs, persists the immutable intent/order hash/exact wire payload, then attempts one POST. FAK taker orders and post-only GTD maker orders are supported. GTD includes the platform's expiry safety interval. Maker evidence is continuously revalidated and invalidated/expired proposals request cancellation. Temporary inability to manage an order does not erase it.

`max_maker_rest_seconds` is the maximum requested server lifetime, including the platform safety interval; the worker never adds time beyond it. The supported range is 181–3600 seconds, consistent with the adapter's conservative 180-second minimum at preparation entry. Slow preparation may therefore reject a short-lived request. This is an exchange-enforced expiry request, not a cryptographically signed V2 expiry guarantee. Local thesis invalidation still requests earlier cancellation and preserves every resulting fill.

Accepted responses are acknowledgements, not fills. Timeout, response loss or an unfamiliar submission response becomes UNKNOWN; the signed order is never blindly reposted. Restart reconciles deterministic order IDs and authenticated trades with finalized chain receipts. Partial fills retain remaining reservation; cancel requests release nothing until terminal state and all matched fills reconcile. Account polling retains a durable match-time cursor with overlap and covers the oldest outstanding order. Each cycle also audits a bounded historical time window and five terminal orders; finalized history remains subject to rolling proof checks.

For Deposit Sessions, the worker additionally compares a seven-day overlapping wallet-wide public TRADE census with the configured session's authenticated confirmed trade history; first reconciliation starts from the served full-history floor. The public witness is never used to invent fills or P&L. An extra public wallet trade is a sticky fault even when the resulting position has already been closed. A foreign **resting** session order remains invisible until it executes, so no public-data check replaces the dedicated-session operating rule.

Held inventory is bounded by operator-selected max_positions; the worker never re-requests every lifetime trade on every tick. Successful direct-EOA redemption transaction gas is reported separately in POL wei, deduplicated by transaction, without invented USD conversion. Failed/provisioning operator transaction costs are outside the bot position-P&L scope. Actual fees come from confirmed OrderFilled logs, not assumed public prints or fee rates. Unexpected actual costs are recorded before a fault stops new positions.

V2 has no signed per-order fee cap. Choose one `fee_policy` explicitly; changing it invalidates the activation digest. There is no automatic downgrade:

- `ONCHAIN_BOUND`: require a positive current onchain maximum and reserve `limit price × maximum bps / 10000` per share. Zero means unbounded and fails this policy. The exchange administrator can change this maximum after signing.
- `EXCHANGE_PUBLISHED_SCHEDULE`: use the fresh CLOB `fd` curve and taker/maker rule. Check a conservative fee envelope over every BUY fill price at or below the limit, including fee quantization, then reserve the **entire operator-selected `max_fee_per_share`** per share. Zero onchain maximum is disclosed as unbounded; it is never interpreted as a free trade. Missing, malformed or unsupported fee evidence rejects the opportunity. See the API policy for the formula and rounding assumptions.

The second policy explicitly accepts reliance on the venue's mutable published schedule. Its configured fee limit is a local submission/reservation control, not an exchange-enforced maximum. Fees can change at matching; this application cannot guarantee a hard monetary ceiling against a venue policy change. Confirmed actual fees are recorded independently. A fee/price/quantity breach atomically records the fill, stops new openings, and queues cancellation of all managed remainders; cancellation is attempted immediately and retried durably. Existing fills and reservations remain until independently reconciled. Operator recovery requires successful reconciliation, never deletion of a costly fill.

## Stop, recovery, settlement and exports

- Authenticated `/stop`: durable stop on new openings; existing orders and positions continue reconciliation and expiry management.
- Authenticated `/cancel_open`: also requests cancellation of managed outstanding orders. This does not claim that cancellation succeeded.
- Touch configured `stop_file` or remove the activation file to remove new-position authority locally. Existing order management remains active. Do not stop the worker merely to stop openings; server GTD is the last-resort expiry if the process is unavailable.
- `/status`, `/recent`, `/positions`, `/stats` distinguish signals, unknown/outstanding orders, actual confirmed fills, and separately excluded simulations. The panel may select an approved experience and resume an existing valid grant after confirmation; no Telegram command grants initial authority or clears a reconciliation fault.
- Local `resume-openings --config ... --expected-stop-generation N` removes authenticated stop requests only under a current matching activation request, fresh reconciled execution status and the unchanged stop generation displayed by /status. It does not clear an execution fault.
- Local `recover --config ... --expected-fault CODE` requires fresh successful reconciliation and an exact unchanged fault before auditing/removing that fault. UNKNOWN submissions with no conclusive exchange evidence remain blocked and must not be “fixed” by deleting the DB or resubmitting them.
- Before reconciling an older execution journal, a versioned audit checks cumulative filled quantity, cost and fees against each original order's limits. The audit preserves fills and atomically queues cancellation on a breach. Its completion marker survives explicit operator recovery, so the same acknowledged historical breach does not recur merely because the worker restarts. Every new fill remains subject to both individual and cumulative checks.

The implementation buys long outcome tokens and holds actual inventory to market resolution; it does not invent a forced liquidation or automatic redemption transaction. Final CTF payout evidence establishes claimable settlement value. Claimable P&L and verified cash redemption proceeds are separate reports. The read-only `record-redemption --config ... --transaction TX --condition CONDITION` importer remains restricted to its verified direct-EOA CTF receipt path. Deposit Wallet execution rejects that command until a separately reviewed smart-wallet/adapter receipt path can attribute the owner-side redemption correctly. Direct CTF USDC.e proceeds are labeled separately from spendable pUSD; unsupported adapter attribution is rejected.

`export --config ... --output /private/operator/export.json` writes full signal and actual-account records without signed payloads or credentials. It never mixes historical PAPER databases into actual performance.

## Strategy scope

| Strategy | Evidence/eligibility | Execution and invalidation | Accounting |
|---|---|---|---|
| Directional | Strict station/date/unit contract; fresh31-member GEFS mapping for future local dates; raw support gap | Fresh evidence/contract/book each submission; skip changed or stale thesis and local-midnight boundary | Signal outcome separate; only confirmed BUY fills enter actual positions |
| Same-day | WRH observed extreme plus NWS near-term and remaining-hour GEFS support; existing strict support/trend/population gates | Re-fetch all relevant sources; cancel resting exposure when support/revision/date invalidates it | Uncalibrated evidence, never asserted calibrated probability |
| Source shock | Fresh official observations show an extreme change excluding a precise bucket; revision-sensitive | Recheck source, prior/current extrema, bucket support and quote | No assertion of finality from a mutable observation |
| Structural | Same-condition YES/NO or independently certified exactly-one complete bucket set | Sequential legs; operator's SEQUENTIAL_FAK_FULL_RESERVATION_STOP_ON_KNOWN_FAILURE; reserve full worst-case cost of all legs; proceed after ACK while final receipts may be pending; stop on a known partial/rejected/unknown submission, and hold actual inventory within the full-cost loss cap | No guaranteed profit from a quote; actual leg cost/fees/fills only |
| Maker | Reviewed directional support plus passive non-crossing bid | Authenticated post-only GTD; fresh quote/evidence; actual order cancellation/fill reconciliation | Book touch/public SELL print is never evidence our order filled |
| Result-lag | Exact current contract and sufficient source-publication/correction finality | Current WRH certificate cannot prove required cutoff state; reject that opportunity with a precise reason | No manufactured finality or theoretical actual position |

## Backup, rollback and host acceptance

Keep the signal database and execution journal separate. Backups must use SQLite backup APIs with WAL consistency, verified restore tests and bounded writer exclusion. Never copy only the main SQLite file while omitting live WAL contents.

**Never rewind an execution journal after an order may have been submitted or filled.** Rollback preserves that journal and requires compatible code/schema plus startup reconciliation. An older code release that cannot interpret the journal is not an acceptable automatic rollback. This release refuses automatic rollback when the financial journal changed after cutover; retain it for separately approved compatible forward recovery. Signal DB rewind after deliveries can also duplicate/erase operator state; unchanged-only recovery is the default unless a separately reviewed reconciliation procedure is used.

Independent host authority owns immutable generation identity, predecessor source/unit/venv/manifests, writer locks and recovery custody. Candidate preparation builds fresh environments without modifying predecessor distributions. Failed stop/quiescence checks must abort before mutation. Corrupted application code must not prevent independently held recovery.

Repository tests and mocked financial APIs do not certify the real e2-micro, transport eligibility, user credentials, actual allowance configuration or a funded account. Separate controlled acceptance must verify effective process environment/imports, resource limits under representative load, Telegram operator identity/delivery without enabling orders, backups/recovery, account eligibility and a current documented release approval. Real-money activation requires a separate explicit operator action after those checks.
