# V11 clock, liveness and cancellation integration

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

Current polling is intentionally strict: a heartbeat arriving between the worker's
sample and guardian poll invalidates the pinned sample and can durably request
cancellation. A later healthy sample cannot revoke that request. This behavior is
covered mechanically, not accepted as profitable continuous operation. Coherent
cross-process health publication and forward cancellation/churn measurement remain
required before an operating acceptance claim.

SQLite writer contention, archive limits and shared storage remain common failure
domains. The guardian cannot bypass them or claim a real/exchange cancellation.
Failure prevents renewal; process death or lease expiry blocks further admission.
There is no same-UID hostile-process security guarantee, kernel network restriction,
supported authenticated cancel route, service deployment, separate-custody proof or
independent commissioning. Exact local checks: `V11_GUARDIAN_ISOLATION_EVIDENCE.md`.

### Next custody boundary

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
