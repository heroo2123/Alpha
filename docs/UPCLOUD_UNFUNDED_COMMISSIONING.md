# New UpCloud host: unfunded commissioning package

This is a procedure for a **new Ubuntu 24.04 x86_64 host**, not a deployment
record. No server, address, SSH identity, plan or region has been selected here.
No existing Google VM is part of these steps. No billable resource or service
was created or changed. SSH access, independently provisioned authority, data-use
permission and separate test-bot authorization are prerequisites, not completed
acceptance. Stop before account funding or financial activation.

## Release and resources

Use the final continuation commit/tree recorded in
`OPERATOR_CONTINUATION_CHECKPOINT.md` and its exact-commit CI artifacts. Have the
independent host administrator verify that identity, both hash locks and their
complete distribution inventories; never deploy a moving branch name. Record
the selected UpCloud region, vCPU/RAM/disk, CPU model, kernel, Ubuntu image ID,
interpreter version/digest, clock synchronization and expected endpoint access
in the host acceptance receipt. This package does not supply an IP address.

Planning assumptions: the reference unit ceiling is 350 MiB per component
(`MemoryHigh=280M`, no swap), at most 48 tasks. Three ceilings total 1,050 MiB
before the OS, build, backup and filesystem cache. These are limits, **not
measurements or a promise that a 1 GB host works**. Choose the actual plan only
after a representative unfunded measurement and headroom review. Measure builds
and upgrades too: two retained releases and three fresh environments overlap.
Record measured peak RSS, CPU time, disk growth and hourly request counts; do not
extrapolate a single quiet cycle to all weather days.

Suggested initial storage budgets for review, not automatic deletion: journal
logs 250 MiB/7 days; app nonfinancial telemetry 250 MiB; at least two intact
approved releases plus their complete venvs; independent encrypted backups with
7 daily/4 weekly copies if capacity permits. Alert at 70% disk usage and stop new
openings before 85%, with a reviewed local monitoring action. These thresholds
are commissioning defaults for disk capacity, not financial limits. The app
itself has no disk-capacity opening guard; the host administrator must implement
and test that monitor before financial activation. Never delete/rewind financial
audit, fills, unknown intents or required recovery generations to free space.

## Independent first installation and service custody

Read `PRODUCTION_HOST_TRUST.md`. The checked-in authority is a non-authoritative
reference. Obtain its independently reviewed implementation, approval/provenance
receipt, fixed privileged launcher and recovery custody through a separate
administrative channel. Do not install this repository's reference and call its
self-generated digest independent approval. The checked-in bootstrap refuses.

The authority cutover protocol requires an **already accepted predecessor**.
It is not a first-host installer. For this new server, the independent authority
publisher/host administrator must supply and separately review the initial
commissioning procedure and accepted stopped release/environment state. Record
that first-install acceptance externally. Do not fabricate predecessor metadata,
skip verification or adapt a candidate script into its own privileged installer.
That external first-install artifact is an explicit provisioning prerequisite.

Create separate unprivileged service identities, with no login or sudo:

| Role | Command | Writable state | Private input | Approved lock |
|---|---|---|---|---|
| Scanner | `polymarket_scanner.production scanner` | `/var/lib/alpha-weather-scanner` | None | `requirements-runtime-hashed.txt` |
| Controller | `polymarket_scanner.production controller` | `/var/lib/alpha-weather-signals` (signals and operator DBs) | Test/production Telegram file, when separately authorized | `requirements-runtime-hashed.txt` |
| Executor, optional later | `polymarket_scanner.production execution` | `/var/lib/alpha-weather-execution` | Privately provisioned signer/API file and externally issued activation | `requirements-execution-hashed.txt` |

Use one read group but distinct numeric UIDs. State directories are `0750`, with
owner-only writes and root-controlled parents; signal/control/scanner DBs and
their WAL/SHM are `0640`. Execution journal/WAL/SHM are `0600`. Nonsecret status
files are `0640`; root-managed config is readable by the appropriate services
but unwritable by all of them. Private credential files are `0600` owned by the
designated service or securely delivered through equivalent independent policy.
Never place secrets in environment files, Git, shell history, screenshots or
acceptance reports. Root and a compromised host remain a security boundary.

The extended reference policy supports `scanner+controller` and optional
`execution`, besides the old combined interface. Bind `operator_db_path`,
`scanner_db_path`, `scanner_status_path`, `telegram_file`, and (when present)
`execution_credentials_file`/`execution_activation_file` independently. The
primary unit is `controller`; writer leases include the controller's existing
`weather-paper-runtime.lock`, `<scanner-db>.writer.lock`, and optional
`<execution-db>.writer.lock`. Config contents must agree canonically across all
components and match those policy paths. Do not mix `signals` with the panel.

The authority creates fresh, hash-verified release-specific environments,
verifies the complete package inventory/source/import origins and starts Python
with `-I -s -E -B` under a minimal environment. No in-place predecessor upgrades,
dotenv, user-site, proxy/custom-CA or loader injection. Scanner/controller lack
signing dependencies. Units additionally mask the executor credential/activation
and journal paths from other roles, and mask Telegram credentials from the
scanner/executor. Verify these effective mount/UID boundaries on the real host,
not just unit text. The scanner must read controller receipts but cannot write
them; the executor cannot access the Telegram token.

## Network, endpoints and costs

No public dashboard, webhook, database port or bot listening port is needed.
Telegram uses outbound long polling. Limit inbound SSH to the operator's chosen
access method; verify the new host key through the UpCloud console before SSH.
Do not disable host-key checks. Outbound HTTPS/DNS/time synchronization are needed
for the documented public sources and, later, exchange/RPC/Telegram endpoints.
Test ordinary direct egress; do not proxy around geographic/account restrictions.

Check Gamma/CLOB, NWS, WRH/Synoptic public-source provenance, GEFS/Open-Meteo,
Polygon RPC and Polymarket geoblock status from the actual selected region.
Respect source errors/rate limits; no source failure grants permission to trade.
Read the current [NWS API guidance](https://www.weather.gov/documentation/services-web-api)
and [Polymarket rate limits](https://docs.polymarket.com/api-reference/rate-limits).
Provider 429/outage behavior must be measured; API access has no assumed SLA.

The VM is not necessarily the whole recurring cost. Review UpCloud compute,
storage, optional backups, taxes and any networking terms at the actual checkout;
no selected price or purchase is implied. [UpCloud pricing](https://upcloud.com/pricing/)
Independent backup storage, monitoring, RPC capacity, gas and venue fees may cost
extra. This task adds no subscription or paid dependency.

The current GEFS clients use Open-Meteo's free ensemble endpoint. Its free terms
are for noncommercial use and publish request limits; do not assume an ongoing
trading use is licensed. Establish applicable permission/attribution and a measured
budget before continuous use. A paid route requires operator approval, an
ensemble-capable plan and a reviewed endpoint/credential integration: that route
is not silently enabled by this release. WRH's published browser data credential
does not establish a separate commercial data license or service guarantee.
Record any required provider permission. [Open-Meteo terms](https://open-meteo.com/en/terms),
[plan/features](https://open-meteo.com/en/pricing)

## Unfunded acceptance after SSH is available

1. **Approve the scope.** Record exact release SHA/tree, chosen host facts and
   independent authority/initial-install receipts. Use new temporary nonfinancial
   state and `LIVE_SIGNALS`. Do not supply a signer, account credentials, funded
   wallet or execution activation. Populate the protected panel policy only with
   independently selected numeric identities/version/expiry. No financial limits
   are invented. Leave the optional executor service absent/disabled.
2. **Clean environment and public load.** Install the exact hash-locked runtime
   in the independently approved release environment. Check `pip check`, package
   inventory/import origins, effective `/proc` environment and the rendered units
   with `systemd-analyze verify`. From a clean review checkout (not a mutable
   production runtime), run the following with a full SHA from the acceptance
   record and a fresh output path outside the checkout:

   ```sh
   python -m tools.operator_unfunded_probe --expected-sha FULL_APPROVED_SHA --cycles 5 --interval 60 --output /var/tmp/alpha-unfunded-public.json
   python -m tools.production_public_execution_probe
   ```

   The first command allows only bounded GET/HEAD requests to fixed public
   origins, caps requests, collects canonical weather events/evidence and records
   CPU, RSS, response bytes, status counts, wall time and exact SHA/tree. It loads
   no account/Telegram config and writes no trading or signal database. Its
   heuristic thresholds are measurement inputs, not operator capital choices.
   The second performs existing read-only chain/contract checks without an account.
   Run them only where source permissions permit. A zero-candidate result may be
   normal; incomplete census, stale sources or rate limits need an explicit
   acceptance decision. Neither probe certifies private account access.
3. **Representative soak.** With separately approved test identities, measure
   scanner/controller and mocked executor over representative market load,
   including no opportunities, dense census, provider timeout/429 and backlogs.
   Use `systemctl show` CPU/memory/task counters, `journalctl --disk-usage`, `df`,
   `du` and request reports. Record observation duration, per-service peaks,
   DB/WAL growth, disk headroom and API volume. Compare the selected server's
   budget; do not call CI runner numbers UpCloud acceptance. The probe covers
   public collection, while mocked lifecycle tests cover account failures.
4. **Isolated failure and restart drill.** Run the complete supported suites plus
   `tests/test_operator_*`, `test_production_lifecycle.py` and host-boundary tests
   in a separate test directory with temporary DBs and mocked Telegram/financial
   APIs. Exercise lost acceptance/UNKNOWN, rejection, partial fill/cancel race,
   stale callbacks, double click, expired authorization, in-flight pause, config
   mismatch and failed DB commits. Restart the mocked worker with its same
   journal and verify one intent, retained reservations/fills and consumed
   confirmations. Restart controller with pending notifications; ambiguous sends
   stay UNKNOWN, not silently resent. No test needs a real signing key.
5. **Backup and recovery drill.** With services quiesced and writer leases held,
   use SQLite backup API and `quick_check` to copy the *test* signal/control/scan
   stores, including schema and release/config identity. Restore into an isolated
   directory with correct custody, run integrity/migration tests and retain the
   original. Exercise immutable A→B generations, corrupt candidate recovery and
   changed-control/scanner/financial journal refusal. Never copy only a live DB
   file without WAL, and never use this drill to reset a real execution journal.
6. **Test bot, only after separate authorization.** Privately provision a new
   test token/chat; verify `/start`, screens, typed previews, reductions, expired
   buttons, spoofed/forwarded requests and test notifications. Mocked execution
   permits no financial call. Confirm actual credential permissions and request
   latency while Telegram is unavailable. Do not reuse or contact the production
   Telegram bot. Record real test-bot behavior separately from mocks.
7. **STOP.** Retain an unfunded acceptance receipt and an opening-disabled
   configuration. Host acceptance is incomplete until the administrator observes
   these results on the selected host. Do not proceed to account funding,
   financial activation or production Telegram without separate authorization.

## Later private account preflight and recovery

Read `ACCOUNT_SECURITY_ONBOARDING.md` before creating/funding an account. The
finalized `3114657...` host does not contain a Deposit Session executor. A later
development branch implements the preferred CLOB-only Session Key adapter, but
Builder access, independent release review, execution-component host policy and
real unfunded account acceptance are still external gates. Do not silently fall
back to the full-key EOA route. Keep owner recovery, Builder management and
revocation capability independent of Telegram and this host.

Before any later activation: independently verify actual account/wallet/signer
type and geographic eligibility, credential identity/scope/expiry, account
exclusivity, pUSD balance/allowances, all open/unknown orders, chain-confirmed
fills/inventory/settlements, no reconciliation fault, approved risk ceilings and
schedule, stop-file/emergency access and current config-bound activation request.
Use `preflight --allow-unfunded` for the initial account-only check. It must
leave financial authority false when collateral/allowance are absent. Before any
later activation, rerun ordinary strict `preflight` and use the externally issued
activation protocol.
Do not equate claimable winnings or a submitted redemption with available cash.
No initial financial grant can originate from the ordinary panel.

Network/restart behavior: missing control/status/evidence closes opening authority;
UNKNOWN is never blindly resubmitted; claimed trade confirmations are consumed
after crash; pause/cancel requests are persisted before UI replies; cancellation
and reconciliation continue in the executor independently of Telegram. A process
stop is not proof of exchange cancellation. Keep a compatible approved execution
release and independent account access available for recovery.

For upgrades, use exact independently approved predecessor/candidate identities,
fresh environments and immutable generations from the host-trust procedure.
`verify-process` must name `scanner`, `controller` or `execution` explicitly.
Changed control/scanner identities also require compatible forward recovery:
old control commands must never be resurrected by backup rewind. Changed financial
history is never automatically restored. Preserve journals, receipts, faults and
subsequent exchange evidence even during incident recovery.

The old Google deployment remains untouched. Start with separate DB paths,
service identities and test bot. Any later migration, production-token transfer
or single-account writer cutover needs a separate authorized plan that reconciles
the old process and prevents two pollers/executors. Do not copy an old PAPER DB
into actual account performance or infer manual trades from delivered messages.
