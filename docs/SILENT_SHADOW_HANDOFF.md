# Silent-shadow production candidate

This release is for **controlled SILENT SHADOW only**. The promotion registry in
`polymarket_scanner/trade_only.py` is exactly empty. The scanner also refuses to
create financial outbox intent, and the command process blocks the financial HTTP
lane. Changing the registry alone does not enable delivery in this release.

No cloud deployment, VM service start, secret change, or paid resource creation is
part of repository engineering. The production candidate is the exact reviewed Git
commit, not whatever a mutable branch happens to contain later.

## Decision and evidence

Candidate `1971089280f945283bb08b1349554a3599988ee2` failed its live acceptance:
zero accepted generations, with financial containment intact. This corrected
release retains the same architecture. Read [the corrective-pass evidence and
migration delta](BUILDER_CORRECTION.md) before the one replacement acceptance run.

Choose **one independent universe-builder process on the same VM**, immutable
SQLite generations, and a scanner that keeps its last accepted generation.

Step 28 completed 232 Gamma pages in 40.599 seconds with Astra stopped. Immediate
repeats on the same and fresh clients averaged 0.112 and 0.107 seconds/page. Live
background crawling had averaged 8.236 seconds/page. This supports runtime/resource
contention as the main remaining hypothesis, not connection aging or cumulative
Gamma throttling. A separate process removes shared event-loop/GIL ownership;
it does not create additional physical CPU capacity.

The old full-object universe and whole-universe recurring CLOB price sweep are
unsuitable for this e2-micro. Complete streaming discovery plus a bounded subset is
appropriate. Existing live measurements (300–343 MB scanner RSS, no process swap,
5–6 second compute) justify trying this bounded architecture on the existing VM.
They do not constitute a live measurement of this final release.

## Final process boundaries

| Process | Owns | Must not own |
|---|---|---|
| `python -m polymarket_scanner.universe_builder --ipv6` | Gamma keyset traversal, inventory ledger, selection, Gamma BBO observations, generation publication | CLOB confirmation, feeds, account DB, Telegram |
| `python -m uvicorn app_trade_only:app --host 127.0.0.1 --port 8000 --workers 1` | Generation acceptance, bounded hot feeds, detectors, exact CLOB preview, silent evidence, settlement | Universe HTTP traversal, Telegram command polling, financial outbox creation |
| `python command_worker_trade_only.py` | Sole Telegram command polling owner, user fill commands, persisted delivery-state handling | Detector promotion or financial delivery in this release |

Existing `app.py`, `app_stable.py`, and `app_stable_v2.py` remain implementation and
research modules. Their standalone research entrypoints are not supported
production services. `app_trade_only_compact.py` and the scanner-owned dedicated
Gamma transport are removed. Legacy GCP/Oracle installers fail with migration
instructions. WARP installation, secret prompts, automatic restarts, and the paid
Render blueprint are retired.

## Generation contract

Default directory: `~/.polymarket-edge-scanner/universe`. It may be relocated using
`UNIVERSE_SNAPSHOT_DIR`; all three services must agree on the path.

* One daemon holds a nonblocking OS `flock` for its lifetime. A second daemon or
  `--once` invocation cannot build concurrently. Process death releases the lock.
* Each build writes `g-SEQUENCE-UUID.sqlite.building` with a 4 MiB SQLite cache.
  The inventory lives on disk; the builder retains one bounded Gamma page and
  selected event materialization at a time. Requests contain 25 events, with one
  request in flight and a 50 ms minimum spacing (at most 20 requests/second).
  The previous page is released before requesting another.
* Tables: `metadata`, `pages`, `inventory`, `events`, `markets`. Market records hold
  all `Market` fields and screening provenance. Original parent membership is
  stored once, including closed/excluded children, and shared on read. Filtering
  children never rewrites evidence about the original parent.
  Snapshot v2 uses lossless per-record zlib compression, bounded decoded lengths,
  binary identity hashes and `WITHOUT ROWID` tables. Parent encoding/comparison
  happens once per event rather than once per selected child. The private,
  disposable build database has no rollback journal or per-page fsync; accepted
  publication retains the full durability sequence below.
* The manifest identifies schema, producer release SHA, selection version,
  generation/sequence IDs, endpoint/query, build start/finish, observation interval,
  page timings, inventory/discovered/materialized counts, and natural exhaustion.
* The page ledger records continuation inputs/outputs, response hashes, local
  receipt times, and request durations. The [official Gamma schema](https://docs.polymarket.com/api-spec/gamma-openapi.yaml)
  specifies an omitted `next_cursor` on the terminal short page; a full page
  without continuation is rejected. Missing/malformed envelopes, malformed
  children, cursor loops, conflicting market identity, caps and incomplete walks
  abort publication. A keyset walk proves observed natural exhaustion, **not a
  transactionally frozen global market set**; Gamma does not promise that here.
* Discovery cap: 250,000 unique active, nonclosed markets. Materialized cap: 30,000.
  Additional safety ceilings: 1,000,000 total inventory rows, 5,000 pages, 16 MiB
  decompressed page, 256 KiB materialized record, 4 MiB complete semantic parent,
  128 MiB aggregate decoded payload, 256 MiB generation file. Any breach
  fails the build; no truncation is accepted.
* After ledger/count/integrity validation, the builder closes and fsyncs the file,
  renames it to its immutable name, fsyncs the directory, validates the reader
  contract, and replaces `current.json` atomically. That pointer includes filename,
  sequence, publication timestamp and SHA-256. There is one publication point.
* A crash before publication leaves the old pointer. A crash after publication
  leaves a complete new generation. Temporary/orphan files are cleaned by the next
  lock owner. At most three completed generations are retained in normal operation.
  Generation data is disposable discovery state, separate from account backups.
* Scanner loading/checksums/decoding run in a worker thread. The scanner commits a
  prepared generation without an `await`, replacing markets, token priorities,
  screening books and authority together. Failed loads leave accepted data intact.
  Rollback, incompatible release/schema/filter and invalid checksums are rejected.
* Reader descriptors can survive Unix unlink during retention cleanup. A reader
  that loses a filename before opening simply retains its previous generation.

## Cadence, freshness and watchdog

| Policy | Value / behavior |
|---|---|
| Builder target cadence | 600 seconds from prior build start |
| Minimum idle after a build | 60 seconds; no overlap or catch-up queue |
| Build deadline | 900 seconds, including transformation/publication |
| Scanner pointer polling | Approximately 5 seconds, subject to normal scan cadence |
| Accepted discovery hard age | 1,800 seconds **from build start**, not acceptance |
| Gamma quote observation hard age | 900 seconds **per page receipt**, not publication |
| Pending candidate limits | 1,000 distinct episodes, 4 MiB payload, 60-second age, two batches maximum |
| WATCH retention | Eight per coalesced batch; visible drop accounting |
| Hot WebSocket tokens | 800 maximum |
| Settlement work | Rotating bounded pages; limited concurrent requests |

The 1,800-second discovery age allows one missed 600-second cycle and a bounded
rebuild while fresh exact candidate checks remain independent. This is a shadow
availability policy, not a financial freshness promise. Quote observations expire
sooner. Missing/stale quote sides are omitted; fresh hot WebSocket data may still
support those tokens. Gamma receipt time is **not a known exchange quote time**.

At hard stale, all broad detector evaluation fails closed while health, feeds,
command service and the builder continue. A responsive scanner waiting for its
first snapshot is not killed by the watchdog. An actual scanner/event-loop stall
still triggers the existing recovery watchdog. A slow/failed builder cannot restart
the scanner through a service dependency. Restart storms are bounded to three
starts per ten minutes.

Systemd limits: builder Nice=5, CPUWeight=50, IOWeight=50, best-effort I/O priority 6,
MemoryHigh=144 MiB, unchanged MemoryMax=160 MiB; scanner
MemoryMax=480 MiB; command worker MemoryMax=112 MiB; aggregate shadow slice
MemoryMax=640 MiB. All three prohibit process swap through their cgroups. These
are containment limits, not a claim that every possible dataset fits. Exceeding
them is a failed capacity gate, not permission to raise them indefinitely.

Builder status retains closed failure codes, bounded numerical details and cgroup
reclaim/pressure counters. `builder-failures.json` retains the last 16 failures
across attempts/restarts, with a lifetime count. HTTP transport logs are disabled;
no exception bodies, request query strings or remote text are persisted. Producer
authority is attested once per daemon startup; marker, detached HEAD and tracked
source signatures are checked before each build and publication without Git
subprocesses. A changed release cannot publish under the old SHA.

## Coverage and trading authority

Gamma BBO is **screening only**. Approximately 77% usable ask-side coverage was
observed on the prior branch; final runtime coverage is measured, not assumed.
Health reports total materialized token sides, quoted/fresh sides, missing/stale
sides and per-lane counts. The denominator is the materialized subset.

The subset does not guarantee complete opportunity recall. Some binary/spread
selection predicates depend on Gamma prices at crawl time. NO quote inference from
YES complement prices can miss same-market underrounds. The independent builder
uses Gamma sports metadata, so sports markets matchable only through a live-feed
slug may be excluded. Unsupported rule scopes remain excluded. These limitations
reduce recall and bias the evidence sample; they cannot be called full Polymarket
price coverage or a lossless prefilter. No 390,000-token CLOB sweep is introduced.

Every ACTIONABLE research observation entering the preview path fetches current
market lifecycle and builds the existing exact CLOB fee/depth/limit certificate on
a copy. Results and rejections are stored under `shadow_execution`, with delivery
permission false and payout explicitly identified as a detector assumption.
This evidence does not certify the underlying contract semantics or actual fills.
The existing future send-time revalidation and delivery-chaos tests remain; the
shadow latch prevents that financial network lane in this release.

Nested and neg-risk text matching still cannot establish general rule equivalence
and exhaustiveness. This release withdraws their guaranteed payout/edge claims,
keeps internal WATCH observations, and quarantines prior claims. It also rejects
negated threshold wording and mismatched question years. User fill tables and
actual cash-flow accounting are not rewritten by that quarantine. Binary identity
checks remain necessary evidence, with no promotion. No edge threshold, fee curve,
depth haircut, calibrated probability or manual leg limit is relaxed.

Weather remains uncalibrated WATCH. Sports causal mapping/freshness hardening is
retained but has not earned promotion. A connected socket is not source freshness.
Manual latency, legging/fill risk, quote survival, market disputes and source-rule
coverage remain future evidence questions.

## Repository validation and hard stop

The validation program comprises:

1. Offline complete pytest suite, including real scanner-loop handoff, zero outbox,
   shadow CLOB preview, integrity/atomicity/cursor/stale/crash-lock regressions,
   original adversarial semantics, and existing fee/delivery/accounting tests.
2. Python compile checks and syntax checks of **every** shell deployment script.
3. Separate-process synthetic scale gate: 929 pages, 23,200 events, 243,750 inventory
   rows including 195,000 active markets; roughly 935 MB raw Gamma-shaped JSON,
   with dense pages over 5.4 MiB, real HTTP/JSON parsing,
   13,500 materialized markets, 27,000 tokens, 800 hot tokens; builder RSS <160 MiB,
   reader RSS <256 MiB, prior-generation reads progress during the build and the
   next complete generation is accepted. Fixture output is not live market evidence.
4. GitHub Actions on Python 3.11 and 3.12 at the exact candidate SHA; no skipped
   async tests. CI uploads test results and synthetic capacity evidence.
5. Final tracked diff, clean checkout, empty promotion registry, dependency policy
   and secret scan. Frozen production state is not changed by any of these checks.

No further transport micro-experiments are required before controlled deployment.
One deployment acceptance run below decides whether the final same-VM architecture
is viable. Fail a correctness gate: stop and fix that concrete defect. Fail sustained
resource/refresh gates: stop the scanner and builder, retain evidence, and rework
isolation or seek explicit approval for a larger VM. Do not respond by growing timeouts,
stale limits, market truncation, or process memory beyond host capacity.

## Migration from failed candidate 1971089280f945283bb08b1349554a3599988ee2

The next engineer performs these steps in the separately authorized deployment
session. This document is not an instruction to deploy an unverified branch tip.

1. Record the supplied final candidate SHA and its green CI run. Check all old
   builder/scanner/command services are stopped; inventory `systemctl cat` and drop-ins.
   Preserve `bot.env`, existing database, `release.sha`, preflight evidence and logs.
2. While still on the prior checkout, run its verified pre-release SQLite backup.
   Confirm `quick_check`, SHA-256 and restore verification. Copy/store that backup
   independently before replacing code. A failed backup stops migration.
3. Import the reviewed candidate through the existing Git-bundle/Cloud Shell/IAP
   route. Fetch the bundle's `main` into `refs/remotes/origin/main`; verify the exact
   commit and its ancestry. Do not fetch or provision paid networking.
4. With a clean checkout and the backup secured, check out the exact
   candidate in detached mode. Run `ALPHA_OFFLINE_BUNDLE=1 bash deploy/prepare-shadow-release.sh
   FINAL_SHA` with the correct `ALPHA_APP_DIR`/`ALPHA_CONFIG_DIR` if nondefault.
   This verifies a backup, pins/records the explicit SHA, installs the exact dependency
   lock, runs release-bound required dependency preflight and checks the existing
   database schema. It does not start services or edit secrets.
5. Confirm nonsecret configuration: `TELEGRAM_COMMANDS_IN_APP=false`, exact account
   DB path, enabled intended feeds, and consistent snapshot directory. Keep market
   WS workers capped at 800 tokens. Old Gamma interval/cap overrides do not authorize
   changing the new fixed production limits.
6. Run `bash deploy/setup-shadow-services.sh`. This renders/validates/installs the
   three canonical units plus their bounded slice and reloads systemd. It removes
   known obsolete overrides, refuses unknown ones, and starts/enables nothing.
   Inspect effective `ExecStart`, `ExecStartPre`, EnvironmentFiles, limits and paths;
   check for overrides in both `/etc` and `/run`.
7. Preserve/verify the existing daily backup timer. Its runner remains compatible;
   if reinstalling, use `deploy/setup-db-backup-service.sh` only in this deployment
   session (that helper deliberately starts a verification backup and timer).
8. Preserve the failed run's logs/status and disposable v1 generation directory
   for diagnosis, then give the builder a clean snapshot directory. Never remove
   the account database. Snapshot v1 cannot be accepted by the v2 reader.
9. Start the builder only in the separately authorized deployment session. In the
   prepared release environment run `.venv/bin/python -m
   polymarket_scanner.universe_ready --timeout 900`. This starts nothing and waits
   for a release-compatible, complete, fresh generation with >=70% measured Gamma
   screening coverage. On timeout, stop and preserve evidence; do not start the
   other two services. On success, start the scanner, then the sole command worker.
   Enable persistent service starts only after the acceptance checklist passes.
   Subsequent refreshes must complete concurrently with the live scanner; the
   bootstrap sequence does not excuse refresh contention.

No account schema version change is required. The new universe SQLite files are
separate and can be regenerated. Structural research-claim quarantine runs once at
scanner startup, preserving user-reported fills. Rolling code back must also restore
the corresponding release marker/preflight; do not relabel newer code as the frozen
SHA. Do not erase newer real fill records by blindly restoring an older account DB.

## One post-deployment acceptance run: 45 minutes

Use the actual final SHA, exact installed dependencies, actual feeds and existing
e2-micro. Start the 45-minute clock when starting the builder. Persist health,
builder status/failure history, cgroup events/pressure and journald through the run.

**PASS only if all of the following hold:**

* SHA/clean-tree/runtime/dependency/schema attestations pass; promotion count is 0.
  Scanner financial enqueue is disabled; command worker financial HTTP is disabled.
  Research produces zero new Telegram outbox rows or financial messages.
* Initial complete generation is accepted within 900 seconds. At least two later
  complete generations are accepted while scanning continues. Each later build
  completes within 600 seconds including publication (use PUBLISHED status elapsed
  time; the embedded manifest closes before final fsync/validation). Accepted and
  partial counters never mix.
* No ordinary-run discovery hard-stale interval; accepted age stays below 1,200
  seconds during the successful refresh cycles. A simulated stale fixture is tested
  in CI, not created by altering production timestamps or lengthening the policy.
* Actual RSS remains below the specified per-process limits; combined shadow cgroup
  memory remains <600 MiB, host MemAvailable >=128 MiB, process swap=0, no OOM kill,
  sustained reclaim thrashing or unexpected restart of any service during builder
  activity/rollover. Per-process RSS caps remain builder 160, scanner 480, command
  112 MiB. The aggregate acceptance gate remains stricter than its 640 MiB cgroup
  emergency limit.
* Detector compute p95 <=10 seconds, maximum <=20 seconds; no completed-scan gap
  >60 seconds after initial readiness. The scanner remains responsive during builds.
* Quote coverage is reported explicitly per lane. Overall fresh Gamma ask-side
  coverage of the materialized subset is >=70% during normal successful cycles.
  This is a shadow data-availability baseline, not a sufficient detector promotion
  criterion. Unexpected collapse/whole-snapshot expiry is a failed acceptance gate.
* Hot WS tokens <=800; stale/future/out-of-order books do not override fresher data.
  Enabled feeds either show valid source progress or honestly show idle/stale
  suppression. Reconnects recover without a restart loop. Zero sports result messages
  during a quiet fixture/window is not proof of feed correctness or failure.
* No candidate overflow in this baseline window, no unbounded pending work, no
  unreported candidate expiry. Overload later marks evidence incomplete.
* Disk free >=2 GiB; generation retention bounded; latest account backup is verified
  restorable; no unsupported schema, unhandled loop failures or unbounded WAL growth.

**FAIL:** any authority/containment violation, partial/corrupt generation accepted,
unbounded resource behavior, failed refresh/rollover criteria, or restart/availability
criteria above. Stop scanner/builder, preserve diagnostics and report the exact failed
gate. No detector promotion, paid upgrade or larger stale limit follows automatically.

After PASS, continue silent evidence collection. Investigate real failures and
detector-specific coverage rather than restarting an open-ended refresh experiment
program. Real-money promotion remains a separate approval/evidence stage.

## Before any future TRADE NOW promotion

Each detector needs independently reviewed contract semantics; unambiguous token
and outcome mapping; empirical source reliability/freshness; detector-specific
coverage and selection-bias accounting; prospective executable edge/capacity and
fee evidence; price/size survival over manual delivery and execution latency;
settlement reconciliation from actual fills; false-positive/dispute/cancellation
analysis; and replay/chaos evidence for final delivery revalidation.

Weather additionally needs prospective empirical calibration for the exact source,
station, unit, bucket and rule contract. Sports needs causal mapping and settlement
evidence for every supported scope. Nested and neg-risk need an actual semantic
proof mechanism, not a more permissive regex. A promotion decision must explicitly
change both a detector-specific registry entry and the shadow delivery containment
policy through a separate reviewed release. This release authorizes neither.
