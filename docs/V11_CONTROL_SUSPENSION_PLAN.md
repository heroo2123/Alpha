# V10 preservation, temporary suspension and recovery plan

Status: PREPARED FOR EXPLICIT OWNER APPROVAL; NOT EXECUTED.
Scope: only alpha-paper-demo.service on alpha-dev. No V11 deployment or financial
authority is included. The original master specification and executor mask apply.

## Approval decision

Requested decision: approve one preservation-first temporary V10 suspension using
the conditional procedure below. Approval does not authorize recovery or live
activation. An executor with existing approved owner access must perform protected
reads and the signal. The connected development tool previously rejected the
privileged probe as Command not allowed; do not evade that restriction. Prepare
and review the concrete preservation command/manifest before requesting any
necessary owner execution. The successful owner health probe needs no repetition.

Reason: the latest persisted successful cycle is over 34 hours old, with severe
memory reclaim pressure. Expected benefit is release of about 433 MiB charged to
V10 if it exits, plus possible pressure relief. There is no otherwise-ready V11
paper/shadow release. No benefit is assumed until post-exit measurement.

## Preconditions and preservation (before any signal)

1. Pin UTC time, boot ID, main PID plus its start-time identity, cgroup membership,
   service unit/drop-in paths and hashes, launcher/source identity and dirty or
   untracked work. Reconfirm financial executor MASKED/INACTIVE, controller
   INACTIVE and unchanged V10 source/configuration. Inspect only allowlisted unit
   properties; keep credential-bearing content out of chat and public Git.
2. Require the same Type=simple, Restart=on-failure, empty RestartForceExitStatus
   and no additional stop hooks or pending service jobs. Confirm the cgroup has
   only the expected main process (threads are not extra processes), no child
   service or external writer, and no supervisor that would restart it. Recheck
   these immediately before the signal. Unexpected state aborts this plan.
3. Create a NEW root-owned mode-0700 directory, for example
   /var/tmp/alpha-v10-presuspension-20260924-<actual-UTC-time>, with exclusive
   creation. Never reuse or modify /var/tmp/alpha-v11-control-evidence-20260923.
   Inventory the preservation size first; require room for both consistent and
   physical copies plus a safety margin. Bound preservation time/I/O; if memory
   pressure or storage makes safe capture infeasible, abort before signaling.
4. Use the already tested tools/v11_snapshot.py pinned read-only transaction
   procedure for a current consistent SQLite backup, including committed WAL.
   Supply freshly verified source/tree/config identities, not historical guessed
   values. Keep its existing deadline and size limits. A timeout, incomplete
   marker, hash error or integrity failure aborts suspension. Never checkpoint or
   vacuum the live database to simplify preservation.
5. Preserve the current source workspace and Git history (including dirty and
   untracked files), deployed source/launcher, unit/drop-ins, referenced runtime
   configuration and credentials, retained status/evidence/history, installed
   package metadata, and available relevant service journal history. Preserve
   owner/group, modes, timestamps, symlink targets and ACLs where present. Record
   exact coverage and any inaccessible or changing file in the private manifest.
   Credential copies remain private with original restrictions or stricter ones;
   never print their values. Compare pre/post hashes for configuration/code.
   Do not silently omit history or claim unavailable rotated journals exist.
6. Pre-signal raw DB/WAL/SHM copies, if collected, are physical evidence only and
   MUST NOT be called a consistent restore set while a writer exists. The pinned
   backup is the verified pre-signal committed view. Verify it off-host using
   read-only/immutable access, preserving its bytes. Preserve existing original
   files in place. Do not delete, reset, rename, restore over or clean anything.

## One bounded non-escalating suspension attempt

Only after explicit approval and all preservation checks pass, revalidate the
PID/start-time/cgroup/unit identity and executor mask. The proposed signal is:

    systemctl kill --signal=SIGTERM --kill-whom=all alpha-paper-demo.service

This is a one-time signal, not systemctl stop or restart. No timeout wrapper may
send a later kill to the service. Do not change SendSIGKILL, resource limits,
permissions or the unit. SIGTERM is a clean termination signal for the observed
simple service under Restart=on-failure, provided its actual exit follows that
path. No application SIGTERM drain handler was found, so committed data must
already be protected; in-flight uncommitted work is not promised to survive.

Observe for at most 60 seconds using read-only process/cgroup/unit checks. Record
signal time, actual exit/status and any process remaining. No second signal,
SIGKILL, kill -9, force-terminate, cancellation escalation or automatic restart is
authorized. If D state delays termination or the service starts again, report
SUSPENSION_NOT_CONFIRMED, preserve all state and stop this operational procedure.
Do not chase the replacement process. An empty expected cgroup and inactive unit
are both required before reporting success. Unexpected child/writer/job presence
before the signal aborts rather than invoking the unit's forceful cleanup path.

After confirmed exit, capture a second stable physical preservation set of the
original DB and any extant WAL/SHM plus current code/config/status/history.
Record absent WAL/SHM as absent; do not manufacture or remove them. Verify all
hashes and the recoverability of a separate off-host working copy. Keep the first
consistent snapshot and second physical set distinct. Recheck resource relief
and executor containment. If verification fails, do not overwrite originals or
start a different release; report the exact failure.

## Evidence interruption and separately approved recovery

Record the last known successful cycle (2026-09-22T23:41:47.652562Z), the later
owner observation confirming staleness, signal/exit timestamps, preservation
identities, and the eventual first verified successful post-recovery cycle.
Distinguish the pre-existing health gap from intentional suspension. Exclude both
from claims of uninterrupted forward V10/V11 comparison; do not backfill cycles.

Recovery needs a separate owner decision after checking unchanged source, unit,
configuration, database lineage, financial containment and adequate V10-only
resources. Require the previous process and any replacement to be absent. Start
only the exact preserved original V10 service against its retained original data;
never restore a snapshot over it merely to start cleanly. A needed restoration or
resource-limit change requires its own review and approval. Verify advancing
successful cycle timestamps, status and evidence on the configured 180-second
cadence, not just active/running. Record failures honestly and avoid restart loops.
No V11 service, new wallet, funds, orders or mask change belongs to this plan.

## Mechanism references and limits

The installed systemd 255 manuals and live properties were inspected read-only.
Official references: [systemctl signaling](https://www.freedesktop.org/software/systemd/man/systemctl.html),
[service restart behavior](https://www.freedesktop.org/software/systemd/man/systemd.service.html),
[kill timeout behavior](https://www.freedesktop.org/software/systemd/man/systemd.kill.html).
This plan has not been rehearsed against V10 and is not an independent review.
It provides no V11 deployment or funding acceptance.
