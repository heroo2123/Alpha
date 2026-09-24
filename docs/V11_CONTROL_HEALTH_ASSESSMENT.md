# Bounded V10 operational assessment — 2026-09-24

Recommendation: preserve V10 unchanged while obtaining the owner-only redacted
health read below. Continue V11 implementation and tests off-host. A suspension
solely to make room for V11 is not yet justified: no specific V11 PAPER/SHADOW
deployment has passed its other readiness gates. This assessment authorizes no
service stop, restart, deployment, funding or financial activation.

## Observed evidence

Two lightweight, read-only samples were taken at 09:12:44 and 09:14:17 UTC through
the authorized development connection. No live database query or backup ran.

| Check | Result |
|---|---|
| V10 unit | Active/running since September 22; same main process; zero restarts |
| V10 memory | 454,397,952 bytes (433.3 MiB), above 419,430,400-byte MemoryHigh; MemoryMax 524,288,000 bytes |
| V10 pressure | Full memory PSI avg60 approximately 75.4–75.9%; memory.high events increased by 3,672 in about 93 seconds |
| V10 OOM | No recorded OOM or OOM kill; cgroup swap is zero |
| Process | D state at the first sample; protected process I/O details unavailable |
| Host | One CPU; 751 MiB available RAM, approximately 747 MiB host swap used, 5.34 GiB free disk; host memory PSI also severe |
| Configured cycle interval | 180 seconds, read as a numeric literal from the existing launcher |
| Financial containment | Executor MASKED/INACTIVE; controller INACTIVE; no mask or authority change |
| Current cycle status / DB / WAL / SHM | Development account receives PermissionError |
| Current journals | No journal files opened: insufficient permissions |

The preserved September 23 snapshot contains a successful-cycle finish timestamp
of September 22 at 23:41:47.652562 UTC. It was already stale at capture. Its true
cycle_ok flag is historical evidence, not a current heartbeat. It remains useful
for the completed forensic baseline; fresh forward-control evidence since capture
is **UNVERIFIED**. No absence of fills or alerts is being used as a health proxy.

The pressure finding is confirmed. A causal diagnosis of the stalled/stale cycle
is not complete. D state and memory reclaim pressure are consistent with resource
stalling, but do not alone identify the complete cause or latest useful evidence.

## One owner-only action

A standalone, bounded probe was prepared at:
`/home/alphaadmin/alpha-v11-control-health-20260924.py`

SHA-256:
`5cc5c61285577bd1bd63bb4f6cbbda5d511d9957e0a08eb4c0b8c5de9b3d0578`

After reviewing these exact bytes, the owner can run:

```sh
sudo /usr/bin/timeout 12s /usr/bin/python3 -I -B /home/alphaadmin/alpha-v11-control-health-20260924.py
```

The connected tool rejected the corresponding noninteractive privileged call as
**Command not allowed**. No privilege-route workaround was attempted. The probe
reads bounded status JSON, stats database/WAL/SHM files without opening a database,
and requests only 20 journal timestamp/priority records from the past two hours.
It excludes raw log messages, credentials, financial aggregates and unknown JSON
fields. It neither writes V10 files nor changes services/permissions. Seven local
tests passed, including stale/future timestamps, redaction, read bounds and races.

Expected benefit: determine the age and success of the actual latest cycle and
whether evidence files are advancing, without interrupting control history or
changing its containment. A single status sample is not continuous-cycle proof.
Further targeted diagnosis can then be based on that evidence.

## Suspension and preservation boundary

No suspension is recommended for approval yet, and none was performed. The unit
currently has a 25-second stop timeout and SendSIGKILL=yes. An ordinary stop could
therefore escalate beyond the user's no-force-kill boundary; it must not be issued
as a convenient diagnostic. The process's D state also makes prompt graceful exit
uncertain. Stopping is not a substitute for demonstrated V11 readiness.

If a later assessment supports temporary suspension, a separate approval must
name the exact graceful shutdown/recovery mechanism and preservation destination.
It must preserve the existing immutable snapshot; create a separately named,
consistent current SQLite backup including committed WAL; preserve current source,
configuration, permissions, unit state and history; verify resulting hashes and
integrity; retain rather than delete WAL/SHM or code; and record the forward-control
interruption interval. Only the exact reviewed V10 identity may resume, with
containment and genuinely fresh successful cycles rechecked. No such procedure
has been executed or represented as approved.

## What still blocks V11 deployment

Shared-account basket code passed its off-host full regression, but this is not a
deployment package acceptance. Provider/runtime scheduling, remaining strategy
and exit integration, actual approved calibration/model artifacts, protected
capability/model-authority commissioning, isolated service/resource configuration,
guardian/clock/operator integration and recovery/acceptance remain unfinished or
unverified. No V11 service is installed by this task. Independent review has not
occurred. Freeing V10 memory alone cannot satisfy these gates.

Resource/isolation acceptance remains failed/unproven for adding a V11 workload to
this host. It does not block unrelated off-host engineering. The fixed completion
matrix remains 1/50 fully accepted local packages, and status is NOT_READY_TO_FUND.
