# Bounded V10 operational assessment — 2026-09-24

Current decision: owner approved preservation preparation ONLY. V10 remains
unchanged. The final preparation checklist in V11_CONTROL_SUSPENSION_PLAN.md is
SUSPENSION_NOT_READY: fresh protected preservation is pending. Read-only inspection
at 11:42 identified a reversible same-boot runtime drop-in/marker mechanism for
systemd restart, activation and final-kill behavior. It is documented and not
installed; configuration changes and SIGTERM remain unapproved. Kernel OOM is not
prevented and memory/OOM safeguards are not relaxed. The unconditional guarantee
remains unavailable. No signal is armed. The next owner-only action is the
prepared protected metadata inventory; exact command/hash are in that plan.
The earlier conditional suspension recommendation is not execution approval.
Continue independent off-host V11 implementation.

## Closed freshness check

The owner ran the prepared read-only probe at 10:31:16 UTC with effective UID 0
and supplied its result. Provenance is OWNER_REPORTED, not an independent read of
the protected files through the development account. That account's access has
not changed. The prior request to run this probe is complete; do not repeat it.

| Check | Result |
|---|---|
| Latest successful cycle | September 22, 23:41:47.652562 UTC; age 125,369 seconds (34 h 49 m) |
| Configured interval | 180 seconds; neither one- nor two-interval freshness passes |
| Current status | Exact hash match to the preserved snapshot; mtime September 22, 23:42:08 UTC |
| Database / WAL | Last mtimes September 22, 23:26:35 / September 23, 00:05:39 UTC |
| SHM | September 23, 20:03:50 UTC; this is not cycle-success evidence |
| Journal metadata | Successful privileged query, no records in the last two hours; no raw messages read |
| Historical flags | cycle_ok and paper_tracker_ok true, but attached to the stale cycle |
| Financial flags | financial_authority, automatic_order_placement and wallet_or_order_api_loaded false in that same stale status |
| Live containment | Independently read at 10:33 UTC: executor MASKED/INACTIVE; controller INACTIVE |
| Probe side effects | No database opened, snapshot changed, service/permission changed or deployment authorized |

**Current fresh successful-cycle health: FAIL.** Persisted status and database
metadata provide no fresh forward-control evidence. This does not prove every
possible in-memory activity has stopped. Empty journal metadata is not proof of
healthy cycles or a complete root-cause diagnosis. The preserved snapshot remains
useful for the completed forensic baseline; it cannot represent a healthy forward
comparison. Record the evidence gap from the last verified successful cycle,
separately from any future approved suspension interval.

## Resource evidence

Two lightweight read-only samples at 09:12:44 and 09:14:17 UTC found:

| Check | Result |
|---|---|
| V10 unit | Active/running, same main process, zero restarts |
| Memory | 454,397,952 bytes (433.3 MiB), above MemoryHigh 419,430,400 bytes; MemoryMax 524,288,000 bytes |
| Pressure | Full memory PSI avg60 approximately 75.4–75.9%; memory.high events increased by 3,672 in about 93 seconds |
| OOM / swap | No recorded cgroup OOM or OOM kill; cgroup swap zero |
| Process | D state at the first sample; CPU advanced only about 0.095 seconds between samples |
| Host | One CPU; about 751 MiB available RAM, 747 MiB swap used and 5.34 GiB free disk; severe host memory PSI |

Memory usage remained 454,397,952 bytes at the 10:33 UTC service-property read.
The pressure finding is confirmed. Reclaim pressure and D state are consistent
with stalling, but do not establish the complete cause. No limits were increased
or protections weakened. No training, tests or V11 workload ran on this host.

Expected benefit of an approved successful suspension: release approximately
433 MiB currently charged to this unit and potentially reduce reclaim pressure.
Actual host relief must be measured; not all charged memory necessarily becomes
immediately free, and other pressure causes may remain. A D-state process may not
exit promptly on SIGTERM, so the expected benefit is not guaranteed.

## Operational boundaries and remaining deployment blockers

The unit has Type=simple, Restart=on-failure, KillSignal=SIGTERM,
KillMode=control-group, TimeoutStopUSec=25s and SendSIGKILL=yes. No explicit SIGTERM
handler was found in the inspected launcher/runtime path. Normal SIGTERM must not
be described as application-drained shutdown. Ordinary stop can escalate; the
separate plan requires a verified preservation set and a non-escalating signal
path, bounded observation, and no repeat signal or forced kill.

There is **no specific otherwise-ready V11 PAPER/SHADOW deployment** whose sole
remaining obstacle is V10 resource use. Provider/runtime scheduling, exact-source
and finality support, remaining strategy/exit integration, approved calibration
and champion artifacts, protected capability/model-authority commissioning,
isolated service/resource configuration, guardian/clock/operator integration and
recovery/acceptance remain unfinished or unverified. Independent review has not
occurred. No V11 service was installed. Suspension cannot clear these gates.

Resource/isolation acceptance remains failed/unproven for adding a V11 workload.
It does not block unrelated off-host work. Fixed completion remains 1/50 fully
accepted local packages. Status remains NOT_READY_TO_FUND. No funding, account
creation, money movement, real order or executor-mask change is authorized.
