# Production host trust and recovery protocol

Repository completion does not provision or certify the target host. The code in
`host_trust/weather-paper-authority-v3/authority.py` is a **non-authoritative
reference/specification**. The repository cannot install, update, or approve its
own authorizer. Both checked-in bootstrap scripts and the old deployment
installer refuse execution. A matching digest of this reference and an anchor
created by the same candidate proves neither independent review nor provenance.

An operator must obtain an independently reviewed authority artifact through a
separate administrative distribution/approval process, preserve its provenance
and review receipt outside the application repository, and provision it at
`/usr/local/libexec/polymarket-weather-paper-v3/authority.py`. Its policy, release
approvals, update procedure, and recovery custody must be controlled independently
of application candidates. Updating this component is an explicit host-trust
change, never part of candidate preparation. This pass neither supplies an
independent artifact nor installs anything on the production host.

## Privilege and component boundaries

The candidate-side `deploy/production-host-control.sh` is a protocol client. It
accepts operation names, full commit IDs and generation IDs. It has no bootstrap,
replacement-authority, arbitrary path, arbitrary module, or arbitrary UID option.
Never grant the application/deploy principal unrestricted `sudo`, `sudo env *`,
Python, Git, shell, or file-install authority. An independently installed launcher
or precisely restricted administrative invocation must fix the trusted authority
path and `env -i ... /usr/bin/python3 -I -s -E` startup before passing data arguments.
The client is not itself privileged or independently trusted.

The external policy fixes:

| Policy field | Required meaning |
| --- | --- |
| `app_dir`, `release_file` | Optional operator checkout and release marker; their parent directories are root-owned and cannot be renamed by a deploy user. |
| `deploy_user`, `deploy_uid`, `deploy_gid` | Unprivileged checkout principal, distinct from both runtime UIDs. |
| `runtime_root` | Root-owned sealed releases, manifests, active-release record and recovery staging. It shares a filesystem with the checkout parent for atomic replacement. |
| `runtime_python`, `runtime_python_sha256` | Independently approved host interpreter. Its base installation/standard library and privileged launcher are part of host trust; a binary digest alone does not attest the OS. |
| `approval_file` | Root-owned independent release approval records, described below. |
| `db_path`, `signal_status_path` | Signals database and status file in one signals-owned state directory. |
| `execution_db_path`, `execution_status_path` | Financial journal and status file in a different execution-owned state directory; required only when execution is provisioned. |
| `writer_lock_paths` | Exactly `<signal-db-directory>/weather-paper-runtime.lock` and, when provisioned, `<execution_db_path>.writer.lock`. |
| `components.signals` | Module `polymarket_scanner.production`, command `signals`, lock `requirements-runtime-hashed.txt`, root-managed config path, independently bound unit path/name and service UID/GID. |
| `components.execution` | Optional separate UID/venv/unit, same module, command `execution`, lock `requirements-execution-hashed.txt`. |
| `shared_read_group` | Shared primary read group for both services when execution is provisioned. Extra service-account groups are rejected. |
| `unit_file`, `unit_name` | Exactly the primary signals component's unit identity. |
| `legacy_predecessor_venv` | Explicitly bound, already accepted predecessor environment for the first cutover. |
| `predecessor_interpreters` | Optional independently approved absolute interpreter-path to SHA-256 mapping for historical venv symlinks. |
| `database_restore_policy` | Only `unchanged_only` is supported. |

State directories must be separate real directories, owned by their respective
service UID and the shared group, mode `0750`, beneath root-controlled parents.
Signals DB/WAL/SHM and public status files use `0640`; the execution DB/WAL/SHM use
`0600`. Execution reads the signals database; signals reads the execution status
report. Neither peer has directory write permission to replace the other's
journal. Signing/API secrets and activation controls belong to the execution
identity or root and are never supplied to the signals service as environment
variables. Telegram credentials are read by the signals identity from its
configured private file. Units do not use `EnvironmentFile`.

For signals alone, a root-managed config selects `LIVE_SIGNALS`. When execution
is provisioned, both services use the same non-secret `LIVE_EXECUTION` JSON
configuration (or byte-independent files with identical canonical JSON). The
signals command still cannot place orders or load credentials. Before startup,
the authority checks config file custody and binds every DB/status path to host
policy. Initial config identities are recorded for audit. Root may change
operator risk settings without a new source SHA; startup revalidates paths and
component agreement, while application activation/readiness binds the current
configuration identity. Credentials, risk values and activation are documented in
`PRODUCTION_OPERATIONS.md`; the host authority does not invent financial limits.

## Independent release approval and clean builds

`approval_file` has version `alpha-host-release-approvals-v1` and an `approved`
array. Every record includes an exact `sha`, exact Git `tree`, and `components`
matching the host policy. Each component contains the approved lock file's
`lock_sha256` plus the **complete** normalized distribution-name to exact-version
mapping. Both predecessor and candidate must be independently approved. No branch
name, timestamp, candidate acceptance flag, self-pinned digest, or repository
bootstrap command grants this approval.

Git operations on the deploy-owned checkout run as that unprivileged principal
with groups/capabilities dropped. Root receives Git object bytes, imports them
with `index-pack --strict` into a newly created bare repository with an empty
template, and verifies the independently approved commit/tree. Candidate Git
configuration, hooks, smudge filters, fsmonitor and recovery helpers never cross
into root execution. Recovery uses a root-custodied bundle with a real head ref;
it works with the entire candidate checkout and its `.git` destroyed.

Preparation creates fresh release-specific `venvs/signals` and, if requested,
`venvs/execution`. It never upgrades the predecessor. Installation accepts only
binary wheels from the appropriate exact hash lock with `--require-hashes
--no-deps`; sdists and build backends are excluded. Dependency startup hooks are
rejected before another venv Python starts. Package checks execute unprivileged;
metadata inventory is read as data by the authority. Unpinned bootstrap tools are
removed. The exact full name/version inventory, complete source/venv file and
mode manifests, trusted source-path entry, and installed units are sealed and
verified. Necessary signing dependencies are allowed only when explicitly pinned
and approved for the execution component; there is no blanket financial-package
ban or implicit permission from an installed package.

Production units run `polymarket_scanner.production signals|execution --config
<root-managed-config>` using their own sealed interpreter with `-I -s -E -B` and
an explicit minimal environment. The privileged prestart verifier is isolated too.
Verification reads actual `/proc` executable, cwd, argv, UID/GID/groups and exact
environment, and probes effective Python paths, prefixes and module origin as an
unprivileged principal. Existing OS account memberships, process limits, clean
base interpreter provenance and actual unit startup still require host acceptance.

## Cutover, rollback and journal preservation

All root mutations serialize through a root-owned `flock`. `create-cutover`
records predecessor unit enable/active state, confirms successful stop and
`MainPID=0`, then holds both stable writer leases while capturing a generation.
Stopping is an operator-controlled deployment action; no such action was run in
this engineering pass. Unit stop failure, a live PID or held writer lease prevents
mutation. The generation binds predecessor/candidate identities, independent
approval, policy, units, service state, DB identity, exact predecessor environments
and root-owned source objects. A second snapshot while a generation is active
cannot replace predecessor A with candidate B.

`prepare-candidate` builds and verifies sealed environments before installing
units; it requires quiescence. `activate-checkout` updates only the operator
checkout/release marker after runtime verification. The operator controls actual
service start and must run public/application preflight and account reconciliation
before opening new positions. `verify-process --component signals|execution`
checks the running component. `finalize` writes the new active-release record and
closes the cutover generation after the operator's acceptance procedure. The
client itself does not automatically start or enable services.

**There is no financial database rewind.** A generation records only a read-only
execution journal identity, never a restorable execution snapshot. If execution
records changed after cutover, automatic recovery refuses with
`RECOVERY_EXECUTION_JOURNAL_CHANGED_REQUIRES_FORWARD_RECOVERY`. It preserves the
journal and both approved code/environment releases. If signal history changed,
recovery similarly refuses with
`RECOVERY_DB_CHANGED_REQUIRES_OPERATOR_RECONCILIATION`; replaying an old snapshot
could duplicate or erase delivered alerts.

After such a refusal the services remain stopped and disabled, the active generation stays
open, and account exposure may still exist. Use the application's opening-stop
control and an independently approved compatible forward recovery, retaining the
same journal and reconciling all unknown/open orders and fills before opening
positions. A PAPER predecessor cannot be presumed capable of managing production
records. No claim is made that an arbitrary schema/code rollback is compatible.
Operational recovery must retain a known compatible execution release and account
access; stopping a process never proves exchange orders were cancelled.

When **both** data identities are unchanged, recovery restores exact predecessor
code, units, service enable/active state and active-release metadata. It validates
all venv archive members and captured interpreter symlinks before publication,
captures each file through descriptor-relative no-follow reads (including every
ancestor), records owner/group as well as content/mode, rejects snapshot input
unreadable by the deploy principal, preserves an intact sealed predecessor in
place, and stages replacements before
retiring old directories. Failed publication restores the prior directory;
retry uses new root-private staging even if an interrupted attempt remains.
Restored signals DB ownership/mode is set before atomic publication. Writer
leases are released before predecessor services restart. Root-owned interrupted
staging/retired directories are forensic data and may be removed only after the
operator verifies no active cutover depends on them.

For routine backups use SQLite's online backup protocol with an independently
administered retention process, include current schema/config/release identities,
and verify copies with `quick_check` and a restore drill in an isolated directory.
Do not copy only an active `.sqlite` file while ignoring WAL. Financial backups
are disaster-recovery evidence, never permission to erase later exchange activity.
Restoring one requires importing/reconciling all subsequent external order/fill
history before activation. This repository did not change any production DB.

## Review ledger and test traceability

The independent review compared Stage1 `80c34842def611ab176fb62bb6e5924441487979`,
Stage2 `a117b149ad0c19b551e9dd8160c47cf3111fa169` and the v3 correction
`c2e20b560afc3682c53cfe4cfd6df3837eada65d`. The integration retained v3's sealed
runtime/bundle architecture and corrected the defects below. Fixture-generated
anchors exercise behavior and explicitly do not certify independent provenance.

| Finding/reproduction | Correction | Regression evidence |
| --- | --- | --- |
| F1: root Git checkout executed a candidate post-checkout hook as UID 0; bootstrap could supply its own authorizer | Unprivileged checkout operations, copied object-only import into fresh root Git, candidate bootstrap refusal and external provenance prerequisite | `test_source_export_never_imports_candidate_git_configuration_or_hooks`, bootstrap/approval/custody cases |
| F2: racing root mutations could overwrite ACTIVE; candidate-current re-snapshot endangered predecessor identity | Root mutation flock, immutable generation binding, exact predecessor bundle, both unit states and active-release restoration | concurrency, A→B→second-snapshot→failure, wrong-generation, corrupt-checkout and next-generation recovery cases |
| F3: v3 hashed trees before chmod, rejected ordinary absolute venv interpreter links, and did not require exact versions | Seal final modes first, validate sealed symlink closure, stage restores, exact component-approved inventories | privileged prepare, polluted predecessor, removed dependency, version/duplicate/package-file changes, exact venv round trip |
| F4: privileged Python could import user startup hooks before its validator; old dependency check imported polluted sitecustomize | env clearing and isolated privileged startup, no EnvironmentFile, metadata-only inventory, actual process/import verification | PYTHONPATH/HOME/USERBASE, native loader, proxy/CA, sitecustomize, actual `/proc` extra-environment cases |
| Snapshot input: a pathname swap after inspection could make root read outside the legacy venv | Descriptor-relative traversal/open, pinned file descriptors through hashing/archive, ancestor no-follow checks and deploy-principal read permission; hardlinks materialized safely | Deterministic baseline c4131c6 fails by reading outside private-fixture bytes; corrected implementation passes; file/directory/ancestor swap and hardlink regressions |
| Recovery: failed stop still allowed mutation; restored DB became root-only; v3 bundle head was not cloneable | Confirm service/lease quiescence, set DB owner before publish, head ref in independent recovery bundle | failed-stop/held-lease, DB owner/mode, destroyed candidate `.git` recovery |
| Production extension: one unit/DB/venv and blind rollback cannot represent actual external fills | Two component locks/identities/units/venvs; distinct writable directories; bound config paths; never restore execution journal; reject changed history | component policy/config redirects, journal mutation refusal, matching config change, signals-only provisioning |

Behavioral coverage is in `test_host_authority_production_boundary.py` and the
modernized host/deployment tests. Former assertions for v2 shell text, v9 labels,
self-supplied bootstrap and missing archive helpers were replaced with equivalent
or stronger runtime/generation behavior. Existing weather/operator acceptance
assertions remain; their fixture includes the actual current V10 research contract.
W7 release tests now use an isolated committed runtime fixture and still reject a
dirty tracked tree, rather than depending on the developer checkout being clean.

Old weather/all-paper deployment shell entrypoints now refuse before any operation
and point to this protocol; their historical bodies remain unreachable for review
traceability. Standalone simulation/runtime tests and unrelated deployment paths
remain available. The production route does not silently launch a PAPER service.

Local regressions use temporary Git repositories, real local Python venvs and
SQLite databases, mocked package installation and mocked service management.
Actual `/proc` probes run only disposable fixture processes. `systemd-analyze
verify` validates generated units without starting them. Full hash-lock installation
and full-suite results are recorded separately in the production checkpoint/CI;
these fixture tests do not prove a funded account or the target e2-micro is ready.

Recorded scoped verification: **124 passed on Python 3.11 (24.27 s) and 124 passed
on Python 3.12 (28.50 s)**; both generated component units validated by systemd;
25 changed shell/bootstrap files passed `bash -n`; the authority compiled on both
Python versions. Logs/JUnit and scope metadata are in
`review-evidence/host-production-2026-09-16.zip`. These are scoped local results;
publication and promotion additionally require exact-candidate full-suite CI.

Post-commit self-review additionally reproduced the pathname race above against
`c4131c64c4603d781bde7ee262183d70d1fbd8fe` using only synthetic temporary files.
The following correction includes durable stopped/disabled service quarantine on
a changed-journal recovery refusal, preventing an automatic reboot restart.
Successful unchanged-data recovery still restores the captured service state.
The final expanded scoped suite passed **130 tests on Python 3.11 (29.95 s) and
130 tests on Python 3.12 (35.35 s)**, run sequentially with disposable fixtures
removed after each run. Both authority compile checks and `git diff --check`
passed. The evidence archive retains the initial results and adds the final
logs/JUnit, source digest and executable baseline reproduction result.

Full-suite integration exposed a fixture path assumption: pytest invoked through
`../venv/bin/python` retained `..` in `sys.executable`, which production policy
correctly rejects. The fixture now binds the resolved real interpreter and its
digest; policy checks remain unchanged. The historical F33 backup replay now
separately verifies retired-script refusal and canonical cutover backup of legacy
tables plus committed WAL data from an unrelated working directory. The expanded
host/replay scope passed **143 tests on Python 3.11 (29.60 s) and 143 tests on
Python 3.12 (38.56 s)** using the relative interpreter invocation; logs/JUnit are
also retained in the evidence archive.
