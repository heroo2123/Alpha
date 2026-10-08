# Future daily seed plan candidate (offline only)

The deployed preparer at
`/home/alphaadmin/AlphaV11_ForwardShadow/continuous-shadow-v2/perpetual_day_preparer.py`
was inspected at SHA-256
`1df752027e9e26c774347b7d386ebe911ca85cad8240af7b4ef002e66a3f67d0`.
Its `seed_daily()` copies the three baseline rows with source `seq` values,
which produced the observed `[3,4,6]` prefix. This commit adds only a pure,
read-only planning and admission module. It is not wired into that deployed
preparer and does not repair or qualify any existing day.

`plan_daily_seed(snapshot, expected_source_sha256)` requires a separately
sealed, checkpointed, WAL-free SQLite snapshot. It opens one regular inode,
hashes its captured bytes, and gives those same bytes to an in-memory SQLite
connection. Standalone backups that retain SQLite WAL header flags are parsed
with only those two flags changed in the private memory copy; the manifest hash
still identifies the original file bytes. The path and sidecar entries are
checked at acquisition and again before return. It verifies source file identity,
the exact V11 schema and namespace, immutable row envelopes and hashes, and
that all declared baseline evidence, source-capture, and raw-evidence references
resolve within the three selected rows with matching hashes. The baseline
payload must be an object containing only the two reviewed reference pairs.
An unreviewed payload shape or dependency
causes refusal. The output contains the unchanged record bodies, IDs, hashes,
and timestamps with deterministic local `seq=1..3`. A canonical manifest binds
the source file hash and each old-to-new sequence mapping. The caller must
retain and independently pin its SHA-256.

`verify_existing_daily(path, plan, expected_manifest_sha256)` verifies the
captured bytes of a sealed, WAL-free file for a contiguous prefix, exact genesis
rows, valid row hashes and the pinned manifest. Its returned tip describes that
captured image at the time of the call. The caller must separately maintain
custody of the path after return and bind the image to its generation and date;
this helper cannot make a replaceable pathname permanently admitted. An active
runtime database with WAL must be checked by a
separately designed consistent read transaction; opening it through
`EvidenceStore` would perform write-capable pragmas.

## Integration contract still required

An independent change must take a SQLite-consistent source snapshot without
touching the live source; pin its hash; call this planner; stage a new private
database with exact schema and records; validate a sealed staging snapshot;
durably publish an exclusive generation and manifest without overwrite; and
make retries and concurrent initializers refuse ambiguous partial artifacts.
It must validate every existing prepared day before collection and refuse
sparse or partial files without changing them. A runtime verifier must account
for concurrent appends and committed WAL, and generation identity must be
bound to scheduler, manager, review and audit state. No legacy protected review
or frontier can be reused after renumbering. Same-day review supersession and
root commissioning remain separate hard stops. The audit gap refusal is
unchanged.

The three-row closure rule intentionally refuses if the actual baseline has
additional dependencies. Expanding that selection requires a separate review
of the extra records and any invalidating later state. This candidate does not
read private live row bodies to make that decision.
