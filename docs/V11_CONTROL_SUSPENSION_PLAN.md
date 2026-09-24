# V10 preservation preparation and suspension readiness

Updated September 24 after explicit approval for PREPARATION ONLY.
**SUSPENSION_NOT_READY. No SIGTERM, stop, restart, restore, executor change or V11
service deployment is approved or performed.** This supersedes the previous
conditional suspension recommendation. Do not use that older plan as permission.

## Actual supervision check (11:06 UTC)

The unit remains active/running with the same main process, six threads, a single
cgroup PID, D state and 454,397,952 bytes charged. Source HEAD is still
5bbac24759349714d4521faf9e087a14c5c0ae05, tree
d5d2b806e273f11e2f832940f483a5f656462584; source status is clean.
Financial executor remains MASKED/INACTIVE; controller INACTIVE.

Observed: Type=simple, Restart=on-failure, RestartUSec=15s,
RestartForceExitStatus empty, KillMode=control-group, KillSignal=15,
FinalKillSignal=9, SendSIGKILL=yes, TimeoutStopUSec=25s, OOMPolicy=stop.
Watchdog is disabled; RuntimeMax is infinite. ExecStop and ExecStopPost are empty;
OnSuccess, OnFailure, Triggers, TriggeredBy and pending jobs are empty. The main
process's reported signal masks show SIGTERM neither caught, ignored, blocked nor
pending. This is a point-in-time observation, not a guarantee about future races.

The installed systemd 255 manuals confirm that normal SIGTERM termination of this
simple service is a clean exit under Restart=on-failure. A direct signal operation
is different from an ordinary service stop. However, the unit retains automatic
failure restart and final-kill policies, and kernel/parent-cgroup/external actions
cannot be excluded. D state can delay signal delivery and memory release. Normal
SIGTERM does not promise application cleanup, a completed cycle or flushed
uncommitted buffers. No application SIGTERM drain handler was found.

**The requested unconditional guarantee that no automatic restart, escalation or
SIGKILL can occur is NOT SATISFIED.** The preparation code cannot issue a signal or
service command, but cannot disable systemd or kernel behavior. No unit mutation,
resource-policy weakening, mask change or privilege workaround is proposed as an
implicit fix. Ordinary systemctl stop remains prohibited. The earlier proposed
single SIGTERM command is withdrawn from execution readiness pending review.
No approved signal executable is staged, scheduled or armed.

Exact operation previously proposed, for REVIEW ONLY and not execution:

    systemctl kill --signal=SIGTERM --kill-whom=all alpha-paper-demo.service

This requests SIGTERM for the unit members; it does not request start/restart or
change unit properties. It does not disable the manager's existing policies.
The current single-PID observation must not be assumed to remain true later.

## Exact prepared artifacts and destination

Staged, hash verified, and NOT executed:
/home/alphaadmin/alpha-v11-preservation-prep-20260924

| Artifact | SHA-256 |
|---|---|
| control_preservation.py | 1aa9bd6df51fd7c6c0ac15f2524329385a9f4f3d73ff1360d1dd9c435d84495a |
| v11_snapshot.py | 6317cc599935147a9084d4775cb9099898db5c3f1365fcf67398ef79c1b1a172 |
| Current V10 launcher | 6b531d769d12e498426857ef213eaf70cdac1c10dd42c18edcc011b86659826c |
| Current main V10 unit | e5d201296d6e4dd04139ae1b2d134824862279df5ae359bf20b3d72087130bc7 |

Proposed new private destination:
/var/tmp/alpha-v10-presuspension-20260924-preparation-01

It did not exist at the 11:06 UTC read. It has NOT been created. Any eventual
creation must be exclusive, root-owned and mode 0700. If it exists then, abort
and preserve it; do not reuse, overwrite or delete it. Staged scripts remain
owner-review artifacts outside the deployment; do not execute writable staging
bytes with privilege without verifying the reviewed hash at the point of use.

The original /var/tmp/alpha-v11-control-evidence-20260923 remains unchanged.
Its off-host standalone SQLite copy passed the new read-only recovery check:
SHA-256 3a3c1efe0e3021a609800f8c71b9d324fdb0a6e75990e6819321176cd966ed71,
quick_check=ok, committed_wal_included=true. This verifies that preserved database,
not a current backup or complete service recovery. No new backup hash exists yet.

## Preservation procedure, before any future suspension decision

1. Recheck PID/start-time/boot identity, complete cgroup membership, unit and drop-in
   hashes, source HEAD/tree/dirty state and containment. Obtain a new private
   runtime/configuration manifest covering all referenced files. Protected live
   state and telegram.json are inaccessible to the development identity; its
   previously rejected privileged route must not be bypassed. The completed owner
   health probe is not being requested again.
2. Inventory size, permissions, mounted filesystems, file types and write activity
   before copying. Check Git common-directory/alternate-object/shallow references
   and external interpreter dependencies; record any missing dependency rather
   than calling the archive a portable runtime restore. Retain source/git history
   and uncommitted/untracked work. The
   physical copier caps 512 MiB and 30,000 entries, requires its full byte budget
   plus 1 GiB disk headroom and checks a 60-second cooperative deadline. A blocked
   kernel read can exceed a cooperative deadline; no escalation is used to force
   completion. Severe host pressure remains a resource concern. If capture cannot
   safely fit these bounds, stop preparation and review a preservation mechanism;
   do not quietly omit files or increase resource limits.
3. Under the new private root, use the existing pinned read-transaction SQLite
   backup into a NEW consistent/ subdirectory. Source is exactly
   /var/lib/alpha-paper-demo/weather-paper.sqlite. Bind newly verified source/tree
   and private configuration-manifest digests. Use the prepared v11_snapshot.py
   --source, --destination, --release-sha, --tree-sha and --config-sha256 options.
   Include status and runtime identity as bounded bracketing context files.
   The default 45-second and size bounds remain; no live WAL checkpoint or vacuum.
   Read transactions can touch SQLite SHM locking metadata; source SQL data is not
   written. Do not promise all SHM/access-time bytes remain unchanged during reads.
4. Privately preserve available V10 journal history with exact query bounds and
   raw-export digest. Coverage begins at the earliest retained relevant record;
   unavailable/rotated history stays explicitly unavailable. Journal export is
   still an owner-access step; no unbounded journal scan was run on the host.
5. Use control_preservation.py archive with exact --source-root LABEL=PATH entries
   and a NEW physical/ destination. Required coverage includes the following.

| Root label | Source |
|---|---|
| source_workspace | /home/alphaadmin/alpha-owner-auth-fix-20260918 (including Git and unfinished work) |
| deployed_runtime | /opt/alpha-paper-demo (launcher, deployed source and venv) |
| runtime_state | /var/lib/alpha-paper-demo (all retained status/history, DB and extant WAL/SHM) |
| control_config | /etc/alpha-weather/telegram.json and any additional actually referenced V10 configuration |
| service_unit | /etc/systemd/system/alpha-paper-demo.service |
| resource_dropins | /etc/systemd/system.control/alpha-paper-demo.service.d |
| enablement_manifest | A private metadata record of existing V10 enablement links/targets/owners; root symlinks are not accepted as archive roots |
| journal_export | The newly captured private V10 export and coverage metadata |

Additional required configuration paths must be inventoried before capture;
financial executor credentials are outside this V10 capture scope. The copier
preserves file bytes, numeric owner/group, modes, nanosecond timestamps, symlink
targets, inode/hardlink metadata and ACL/xattr bytes. Links are not followed.
Special files, missing paths, unsafe roots, changes during copying or exhausted
bounds fail visibly. Failed output stays private and incomplete. Neither source
files nor older preservation attempts are deleted. Private configuration,
credentials and raw evidence are never committed or printed into chat.

6. Verify physical/ using control_preservation.py verify --directory and
   --expected-manifest-sha256 pinned from the separately recorded capture result.
   Both archive and every member are checked against the private manifest. The
   manifest hash alone is not external authenticity unless its value is retained
   independently. Verify consistent/ with open_verified_snapshot and a read-only
   quick_check off-host where possible. No restore may target original paths.
7. The pre-suspension physical DB/WAL/SHM copies remain individually checked
   physical evidence, NOT an atomic live-database restore set. The pinned SQLite
   backup is the committed-state restore authority for its capture instant.
   Post-capture commits must not disappear: any later approved termination would
   require a second stable physical capture after all writers have exited. That
   post-exit capture is not authorized or completed by this preparation.

## Recovery and rollback plan (not executable authority)

There is no configuration change to roll back. Keep original code, configuration,
permissions and database paths in place. Never reset Git or restore the historical
snapshot over current data simply to obtain a clean start. Any future recovery
requires a separate owner decision, confirmation that old/replacement processes
are absent, exact release/configuration/data lineage and executor containment,
and sufficient resources for V10 alone. Only then may the original service be
started; validate actual advancing successful cycles/evidence at the configured
180-second cadence. Do not install V11 as a substitute.

If original data becomes unusable, isolate the problem without overwriting it.
Verify the chosen backup and its independently recorded manifest hash, reproduce
DB/WAL recovery only in a new private scratch directory, and review lost commits,
permissions/ACLs, hardlinks, external interpreter dependencies and lineage before
any separately approved restoration. A standalone SQLite readability check does
not certify full OS/runtime restoration. No automatic restore/restart loop exists.

## Evidence that suspension would interrupt or lose

Already observed gap: the latest successful cycle ended September 22 at
23:41:47.652562 UTC; the September 24 owner probe confirmed the unchanged stale
status. Do not label this period healthy forward-control evidence.

A future suspension would additionally prevent new control collections/cycles,
PWS/official receipt timing, revision sequences, order-book/trade observations,
rejection funnels and counterfactual markouts during the downtime. Some public
history may later be fetchable, but original receipt timing and transient source
states cannot be recreated or backfilled as prospective evidence. In-flight
transactions, buffered output, caches and incomplete network requests can be lost
on SIGTERM; only committed state at the pinned snapshot boundary is assured by
that backup. A stable post-exit physical set is needed to preserve later commits.
Record last success, probe, capture boundaries, any eventual signal/actual exit,
and first successful recovery separately. Exclude both the pre-existing health
gap and intentional suspension from uninterrupted V10/V11 comparisons.

## Final preparation checklist

| Gate | Status |
|---|---|
| Preparation-only authorization recorded; no service action | PASS |
| Current source/unit identity and executor containment | PASS at recorded reads |
| Existing snapshot recovery/hash check | PASS off-host; historical snapshot only |
| Preservation-only code/hash and synthetic WAL recovery | PASS; 16 new / 41 related tests |
| Exact new destination selected and nonexistence checked | PASS at recorded read; creation pending |
| Fresh private config/history inventory and preservation set | PENDING owner access; no new capture/hash |
| Complete current backup and full-runtime recovery verification | NOT PASSED |
| Unconditional no automatic escalation/SIGKILL/restart guarantee | NOT SATISFIED under unchanged unit/kernel policies |
| Explicit approval to send SIGTERM | ABSENT; wait for a new owner decision |
| V11 resource/isolation and deployment acceptance | NOT PASSED |

Full V11 development continues off-host. NOT_READY_TO_FUND. No financial authority.
