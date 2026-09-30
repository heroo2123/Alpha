# Gate 3 restart review — exact 74bd122

**CHANGES_REQUIRED — five P2 implementation findings; restart acceptance held.**

Independent Astra/high review of Sol/high commit
`74bd1222ebf0a17ec654abb2783f2444ae119430`, tree
`ed59c54c22a63ee4e16b6ff6274a910ca26447ba`, against the
[approved design](V11_R09_GATE3_STORE_RESTART_DESIGN_7b5a235.md).
The candidate stayed clean, unchanged and unmerged in
`/tmp/alpha-v11-r09-gate3-strict-offline-20260930`.

Independent verification: **204 affected tests passed (3.62 s)** and
**60 independent probes passed (0.95 s)**. Seven probe cases reproduce the
five defects below; the other 53 are positive controls and fault/integrity
checks. A passing defect probe means the defect was reproduced, not that
acceptance passed. [Reproducible probes](V11_R09_GATE3_STORE_RESTART_REVIEW_74bd122_probes.py)
and [completed terminal](V11_R09_GATE3_STORE_RESTART_REVIEW_74bd122_terminal.json)
bind evidence. Logs are in `/tmp/alpha-v11-gate3-restart-review-74bd122/`.

## Findings and repair criteria

**P2 R1 — expected recovery identity is ignored on an empty namespace.**
`tools/v11_r09_gate3_store_v1.py:327-369`: `_open_or_initialize` chooses
initialization solely from names. `_initialize` never checks either supplied
`expected_descriptor_sha256` or `expected_head`. All three independent cases
(descriptor pin, head pin, both) create new metadata and return VALID despite
an impossible retained checkpoint. Thus loss of all metadata/objects on the same
pinned root can silently become a new store even when the caller retained the
very evidence intended to detect it. This is not the explicitly unprovable
rollback-without-an-external-head case.

Require explicit fresh initialization with no retained recovery expectation.
An empty namespace with either pin must refuse before creating anything.
Test same-root complete-state loss, both pins individually/together, and an
ordinary new-store positive control; preserve old stores and fixtures.

**P2 R2 — close bypasses ownership serialization and callback rejection.**
`tools/v11_r09_gate3_store_v1.py:684-693`: `close()` does not take `_mutex`
or check `_callback`, unlike read/seal. The independent threaded probe pauses a
seal inside its recorder, closes the store from another thread, and successfully
opens a competing store before that recorder returns. The competitor sees the
outstanding PREPARE; the first operation subsequently errors. Ownership has
nevertheless been released during an active critical section, contrary to the
lifetime contract. A recorder can also call close reentrantly.

Serialize normal close with in-flight operations and explicitly reject reentrant
callback close. Keep the fork-child path lock-free and descriptor-close-only;
never LOCK_UN the parent's shared description. Test concurrent close/read/seal,
callback close, fork cleanup and exec release, with bounded joins and child cleanup.

**P2 R3 — mutable original clock prefix can acknowledge an unreplayable seal.**
`tools/v11_r09_gate3_store_v1.py:595-619,648-658`: validation explicitly accepts
lists, but seal retains the caller's collection across recorder execution.
The probe gives a valid list, replaces its three entries inside the recorder
with another internally valid clock sequence, and receives
ACKNOWLEDGED_THIS_SESSION plus readable bytes. PREPARE holds the old prefix;
COMMIT/receipt hold the new one. Reopen rejects JOURNAL_OR_IDENTITY_INVALID.
This needs no file tampering or compromised process; an accepted mutable API
argument changes during the supported callback.

Freeze a validated tuple before any mutation and use that same immutable snapshot
for PREPARE, complete-clock validation, COMMIT and receipt. Alternatively reject
mutable inputs before PREPARE, explicitly. Preserve the frozen dataclass/raw-byte
checks. Test mutation from both recorder and another thread; a returned success
must reopen with identical original evidence.

**P2 R4 — transaction reserve can admit PREPARE but reject its own COMMIT.**
`tools/v11_r09_gate3_store_v1.py:409-413,607-613`: seal checks space for three
maximum records plus report, then every `_append` requires that entire reserve
again. Near the cap, PREPARE consumes some admitted space; COMMIT now fails its
renewed reserve check after the object and original seal sample already exist.
The probe scales only MAX_JOURNAL to the exact boundary (current size + three
records + report + 10 bytes), observes a durable PREPARE/final object, and gets
STORE_JOURNAL_CAPACITY. The store is permanently unresolved despite passing its
pre-transaction reserve check. The same moving reserve affects recovery.

Account for the complete bounded transaction before PREPARE, then consume its
reserved allowance with appropriate per-event remaining reserve. Validate encoded
record sizes before publication where possible. Test just-below/exact/just-above
boundaries, COMMIT and recovery capacity, and that ordinary capacity refusal
leaves a healthy store unchanged. Do not grow caps or evict evidence.

**P2 R5 — original host identity is not persisted or checked.**
`tools/v11_r09_gate3_store_v1.py:236-240,340-351,393-408,491-493`: host_id
is accepted but appears only in later RECOVERY_VALIDATED events. The original
descriptor, PREPARE and COMMIT omit it. The probe seals on `host-original`, proves
that string is absent from persisted evidence, then reopens with `different-host`
and the same supplied boot and successfully makes another seal. The original
host cannot be recovered. This violates the design's original host/boot evidence
and runtime-identity contract even though the API remains synthetic and caller
claims are not attestation.

Bind original host identity durably in the pinned v1 context/receipt provenance;
validate it at reopen and before new acquisition. Explicitly preserve historical
cross-boot inspection without allowing acquisition. Cover an INIT-only cross-boot
store as well as stores with receipts; the current cross-boot flag is derived only
from existing COMMIT clocks. No real host attestation or authority installation
is authorized by this repair.

## What passed and what remains unproved

The independently rerun suite covers the author's 28 restart cases plus affected
offline I/O, launch, collector and GRIB cases. Our 53 non-defect cases include
three consecutive recoveries with unchanged original clocks and UNKNOWN
acknowledgement; descriptor/head controls; eight sparse/nonregular/namespace
refusals; twenty before/after seal write/fsync/link/unlink fault cases; ten
recovery-fsync fault cases; twelve initialization write/fsync fault cases; and
two zero/torn PREPARE-write cases. They preserve names and confirm poisoning,
whole-store refusal, and the distinction between a surviving complete COMMIT
and historical caller acknowledgement. The inherited fork handling and separate
legacy refusal tests also pass in the author suite.

The approved interruption matrix is still not fully demonstrated. The author's
process tests exit after link or complete COMMIT only; its seven survivor fixtures
are useful examples, not systematic exploration of all volatile file/name and
synced-state boundaries. Our added OSError probes are not SIGKILL or power-loss
qualification. Repair must add a traceable matrix including temp/metadata create,
recorder and caller-return boundaries, thread/exec ownership, recovery-record
write/interruptions, and deterministic survival/loss of unsynced bytes/names.
Keep physical filesystem qualification, raw clock attestation, real provider
context, feature cutoff/dependency admission and historical runtime-use proof
explicitly outside this synthetic acceptance. No strengthened causal or learner
claim follows from an object receipt.

## Exact next step and safety state

Repair R1–R5 plus missing interruption coverage in the **same preserved held
worktree**, limited to offline store/clock helpers, synthetic tests and handoff.
Keep 74bd122 as lineage. Add regression assertions for desired fixed behavior;
do not simply carry the defect-reproduction assertions forward as acceptance.
Run focused/affected suites, commit the candidate and write an exact commit/tree
terminal. Then obtain fresh different-model independent review before a separate
integration decision. Sonnet's latest actual router result remains session-limited
until 22:20 UTC (review time about 22:04), so Sol/high is the existing implementation
failover for the next routed batch. No duplicate worker was launched during this
bounded review; this document/terminal completes the review, not the repair.

Recovered main was clean at 38d6aa9; SHADOW and candidate stayed clean. New
commissioning writes since the prior checkpoint are watchdog/manager statuses,
not forward samples. Accepted release resolution 6ec371e remains current.
Private FINAL-REVIEWED master SHA-256 matches its pin. Read-only system checks:
V10 demo inactive/disabled, execution inactive/masked, protected model-authority
paths absent. Disk 4.4 GiB free; memory about 1.0 GiB available. No V10,
AxiomTrade, service, authority, provider capture, financial or publication action.
GEFS forward SHADOW remains owner/root-gated. All merge, publication, capture,
G3-L, SHADOW and learner-admission holds remain. **91/200 (45.5%), formal 1/50;
NOT_READY_TO_FUND**, no C/J/E/A crossing.
