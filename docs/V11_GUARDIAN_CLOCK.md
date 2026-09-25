# V11 clock, liveness and cancellation integration

Implemented off-host; no service deployment or financial authority. The runtime
health monitor and paper cancellation adapter share a process and are NOT a
commissioned independent production guardian. R37/R38 remain PARTIAL.

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
