# V11 clock, liveness and cancellation integration

Coherent local publication now uses one bounded heartbeat/sample transaction,
consistent health snapshots and exact head CAS before guardian triggers/READY.
READY also refreshes time/process checks after acquiring the write lock; malformed
health requests cancellation without changing account economics. Final targeted
**184 passed / 30.75 s**; full **4549 passed / four existing warnings / 623.00 s**,
exit 0 with the same canonical inputs. See `V11_HEALTH_PUBLICATION_EVIDENCE.md` for evidence,
race/restart semantics and the remaining protected producer boundary.

Latest local custody gate: actual distinct capability-free broker/guardian/candidate
principals and three abrupt broker restart boundaries passed **20 checks / 9.84 s**,
no skips, on 2026-09-25. uidmap is installed; no further owner setup is currently
needed. See `V11_GUARDIAN_CUSTODY_EVIDENCE.md` for precise scope. Protected producer
transport and operational commissioning remain open.

## PAPER Unix-socket broker continuation

`paper_guardian_broker.py`, `guardian_protocol.py` and `guardian_client.py` add a
finite local AF_UNIX boundary. The broker retains the existing PAPER coordinator
and guardian engine; the client receives no archive handle. Kernel SO_PEERCRED
authenticates configured UID/GID in both directions, with boot/start/PID checks.
Kernel overflow UID/GID values and unavailable overflow metadata are refused because
unmapped peers can otherwise appear to have those configured identities.
Distinct principals are the default; explicitly named same-UID synthetic mechanics
mode is available for preliminary tests and cannot establish protected custody.
The peer policy binds both broker identity and the downstream guardian config.

The fixed protocol accepts SNAPSHOT, CHECK and CANCEL only. SNAPSHOT returns
bounded managed-intent signatures and an original account record/hash/sequence.
CHECK asks the broker to run the existing safety cycle; all health/clock/status
decisions remain broker-derived. CANCEL pins an exact account snapshot and target
signatures to a request identity. The accepted request itself is its durable local
trigger; it does not supply an independent external trigger or venue attestation.
Unknown/extra operations or fields, ambiguous peers and unavailable identities
fail closed. No order, replacement, fill, terminal, SQL or client database path is
accepted. Typed responses also reject extra state and authority claims.

An atomic ACCEPTED journal head precedes cancellation effects. Stable per-intent
account commands make effect-before-receipt recovery idempotent. COMPLETED receipts
retain the observed account reference and never imply terminal confirmation or
release reservations. Restart drains a pending cancellation before any new CHECK;
interrupted observations become explicit refusals. Replaying CHECK never resamples
or renews its old lease. Both guardian-client and broker process identities must be
present/alive for a broker READY lease. Existing account/basket/maker and transaction
guards still apply. Cancellation and explicit reconciliation keep their own gates.

The broker checks private archive parent/file/sidecar custody, takes the shared
guardian lock, pins endpoint configuration and refuses foreign files/symlinks or
live endpoint replacement. Only a matching stale socket is replaced. Absolute
transport deadlines, 32 KiB frames, JSON depth/duplicate-key checks, bounded target
counts, finite connections and hard child limits bound work. Transport timeouts
can leave delivery uncertain; retry the same request to obtain its durable receipt.
The launcher-level 65-second alarm bounds an operation that outlives socket I/O.

`launch_broker(...)` and `launch_client(...)` are explicit caller-owned local
sibling launchers with fixed modules, clean environments and closed descriptors.
The finite client drives CHECK independently; the broker does not automatically
poll while no client connects. Client disconnection/failure has no archive fallback;
process death or lease expiry closes opening admission. No service is installed.

Separate-user custody needs the local WSL Ubuntu `uidmap` package and the existing
assigned subordinate UID/GID ranges. The disposable harness uses namespace-only
setup privilege, a private tmpfs and distinct capability-free roles; no host-root
launcher, permanent accounts or host permission changes are requested. Missing
prerequisites are an explicit unavailable/skip, never successful isolation evidence.
See `V11_GUARDIAN_BROKER_EVIDENCE.md` for exact verified scope and remaining gates.
Shared storage contention, protected producer integration, supported real cancel
authentication, deployment and independent operational acceptance remain open.

Implemented off-host; no service deployment or financial authority. The original
runtime health monitor and cooperative cancellation adapter still share a process.
The separate finite PAPER guardian described below adds local process independence;
it is NOT a commissioned independent production guardian. R37/R38 remain PARTIAL.

## Independent local PAPER process

`guardian_lease.py` and `paper_guardian.py` add a finite Linux sibling process,
separate from the candidate's event loop and runtime lock. Local verification uses
WSL2 Ubuntu 24.04.2 LTS, Python 3.11.16, a native Linux filesystem, `/proc` process
identity, `flock`/`O_NOFOLLOW`, signals, hard resource limits and `no_new_privs`.
Native Windows cannot meaningfully verify these semantics. This local environment
already exists; no owner installation or alpha-dev access is needed for this slice.

The explicit `launch_guardian(config, cycles=...)` helper uses the current absolute
interpreter, fixed module, closed descriptors, a separate session and a two-variable
environment. Its child accepts one bounded JSON launch description, no arbitrary
commands/callbacks, credentials, fills, exits or order transport. Configuration is
created by `configuration(...)` from the existing PAPER coordinator, guardian policy,
health configuration and `process_identity(candidate_pid)`. Any saved launch input
belongs in private local state outside Git. The caller owns the child handle and
drains its single result line. It is not scheduled by `CandidateRunner` and does
not install a daemon or service. Direct CLI execution inherits the caller's startup
environment; use the explicit sanitized launcher for this local contract.

The child has hard limits of 512 MiB address space, 64 descriptors, 256 MiB file
size, 30 CPU seconds, zero core dump size, and a terminal 65-second wall alarm.
Inherited lower hard limits are preserved. Runs allow at most 200 cycles and 60
seconds of scheduled intervals. The wall alarm escapes internal per-intent error
handlers. The finite process ends with a STOPPED lease. It is not a continuous or
accepted production supervisor, network sandbox, separate UID or credential broker.

Guardian identity binds PID, Linux start ticks, boot and UID. Worker identity,
account policy, archive resource limits, health configuration and guardian policy
bind the configuration digest. A new worker identity/configuration requires review;
restarting the guardian alone preserves durable cancellation. Archive paths are
intentionally portable local locators, not independently attested custody identities.
Two copied same-account archives are not claimed to be the same protected ledger.

An optional `CandidatePlan.guardian_config` requires the first valid guardian lease
before any reservation. Once a guardian journal exists, fresh coordinators also
enforce it. The default omitted option preserves historical assembly/runtime hashes.
Account reservation, single-leg/basket SUBMITTING, and maker admission fence the
exact original journal head. Freshness is checked again after SQLite acquires its
write lock; lease expiry during preparation/waiting cannot admit a new opening.
Stopped/dead/reused guardian identity, raw backward clocks, changed boot/configuration,
missing status and expired wall/monotonic leases gate. This lease does not replace
the existing model, source, rule, risk, receipt or account gates.

Each cycle withdraws its preceding lease before checking the candidate's existing
health journal. It never writes a candidate heartbeat or renews a historical sample.
Pending cancellation is persisted before account effects and retried using the same
plan/request identity even if health recovers or the process restarts. A bounded
rotating scan checks managed resting intents against current health, protected
admissions and event/operator state. Failures request cancellation through the
existing exact cancel-only account transition. Reducing nonmaker SELL intents are
preserved. Cash, fills, inventory, faults and reservations remain unchanged until
the existing explicit reconciliation route supplies its own proof.

Legacy unpaired heartbeat polling remains intentionally strict: a heartbeat after
the sampled worker head can durably request cancellation. The candidate runtime's
paired publication avoids that intermediate state. A publication changing during
guardian decisions causes at most one retry; repeated contention stays GATED.
A later healthy sample never revokes an already durable trigger. Coherent local
cross-process mechanics are verified; forward cancellation/churn measurements are
still required before an operating acceptance claim.

SQLite writer contention, archive limits and shared storage remain common failure
domains. The guardian cannot bypass them or claim a real/exchange cancellation.
Failure prevents renewal; process death or lease expiry blocks further admission.
There is no same-UID hostile-process security guarantee, kernel network restriction,
supported real authenticated cancel route, service deployment or independent
commissioning in this original shared-store mode. The distinct-principal broker
custody proof is documented separately above. Original local process checks:
`V11_GUARDIAN_ISOLATION_EVIDENCE.md`.

### Original custody plan (now verified locally; operational commissioning open)

Implement a PAPER-only cancel broker and typed AF_UNIX client with kernel
`SO_PEERCRED`. The broker owns private synthetic account/journal state; the guardian
client has no direct write route. Accept only a bounded cancellation request bound
to command, account/policy, current intent signature and trigger identity. Keep
idempotent request receipts and existing reservation/reconciliation semantics;
accept no client-chosen database path, SQL, callback, order, replacement, fill or
terminal operation. Bound frames, nesting, connection time, work and shutdown.

That protocol/recovery work is preparable with the existing environment and no
owner action. Once the finite harness is concrete and reviewed, custody acceptance
needs three distinct unprivileged Linux principals: broker, guardian client and
rejected candidate. A finite local privilege-dropping launcher can use temporary
numeric identities and private native-Linux directories; no permanent account,
service, package, Docker membership or venue credential is required. Any privileged
owner step must be limited to that reviewed launcher, not a broad interactive shell.
Prove actual peer rejection, denied candidate/guardian ledger writes, denied
cross-principal signals, successful authorized cancellation and restart/replay.
Do not label same-UID preliminary tests as that proof. No such setup or privileged
launcher execution has occurred in this milestone.

## Existing health and cancellation foundation

runtime_health.py reads the local boot identity, wall/monotonic clock pair and
the fixed read-only timedatectl NTPSynchronized property. Missing/failed/unknown
sync status gates openings. Missing boot identity also gates. The complete archive
raw-timestamp high-water mark is checked, including captures newer than the
previous health sample. Offset is explicitly UNKNOWN; the initial adapter
validates synchronization status, not an independently measured offset. Wall
discontinuity, monotonic regression, boot change, stale heartbeat and missing
required evidence also gate. Recovery needs distinct spaced good samples and
does not clear account/operator faults. Historical replay cannot renew a lease.

The actual bounded off-host probe returned SYNC_STATUS_UNAVAILABLE; no alpha-dev
probe or workload was added. A functioning trustworthy deployed mechanism and
protected runtime identity still need commissioning. Tests use explicitly
SYNTHETIC_OFF_HOST_FIXTURE sync responses; they are not host acceptance.

Sources are leased by exact event/provider/source identity and strategy. A missing
PWS dependency does not gate an unrelated forecast sleeve. Raw/historical unknown
availability, stale revisions, unsynchronized books and failed PWS QC do not become
fresh authority. New source/heartbeat heads require resampling. Health and source
heads are checked again at the common account's reservation/submission transaction
and at maker proposal/observation. Standalone nonfinancial component use without
a runtime monitor fences the monitor's absence atomically; it is not live permission.

Cancellation and local quote retirement retain a restricted sequence-ordered audit
path during backward wall-clock movement. Actual wall timestamps are preserved,
never clamped or rewritten. Ordinary capture, valuation and opening audits keep
their original clock high-water guard. The restricted path rejects opening/model/
valuation actions and validates an exact existing account cancellation or exact
research quote retirement against prior state within the transaction. It cannot
change cash, inventory, order economics, hard limits or faults. References remain
existing immutable records ordered by sequence, with explicitly untrusted wall
chronology. Existing V11 archives without the new time index remain readable;
their MAX(recorded_at) check may cost more and needs capacity validation before
deployment. No V10 schema or file is touched.

Paper requests keep reservations until separately reconciled terminal evidence.
No public print, attempted cancel, expiry or local status implies exchange
confirmation. Normal reconciliation APIs remain available with a valid clock;
production receipt/reconciliation under broken clocks and external cancel routes
still need their own independent implementation and acceptance. A large forward
clock jump can leave the archive high-water guard closed after the clock returns;
do not rewrite timestamps or silently reset the archive to reopen it.

Remaining production gates include OS-separated identity, narrow supported cancel
authentication, expiry/revocation/network failure behavior, GTD defense in depth,
external reconciliation, worker/guardian isolation, resource limits and independent
review. No live guardian, credential route or financial clock authority is claimed.

The runtime now checks retained opening/passive intents against their original
protected strategy admissions, including both PWS model scopes, before optional
event work. A failed check records the account snapshot and exact immutable intent
signature, then uses the same cancellation/reconciliation bridge. The dedicated
rotating sweep shares the existing per-tick update and new-plan budgets. Pending
terminal reconciliation cannot consume its separate intake capacity: at most one
plan is tracked per retained account intent (hard bound 512), alongside at most 32
general trigger plans. Healthy checks do not renew eligibility or recreate orders.
Reducing SELL intents are preserved. Maker research revalidates admission without
waiting for a new book. Runtime/report identities explicitly gate incompatible old
state; there is no silent migration or deletion.

The backward-clock telemetry extension accepts only the exact nonauthorizing
resting-admission-check schema. Capture/model/decision chronology and the existing
cancel-only account state comparison remain unchanged. Daily/weekly reports count
check reasons, cancellation audit outcomes and maker retirements; counts are audit
records, not inferred unique fills or confirmed exchange cancellations. Protected
recovery still requires its existing independent review and cannot undo already
requested cancellation. Rolling statistical drift acceptance and independent live
cancellation remain open.
