# Gate 3 G3-L offline package preparation

**Status: NON-LAUNCHABLE. G3-L remains NO-GO.** This worktree prepares an
inventory and a conservative resource bound only. It issues no requests,
creates no private launch manifest, and cannot produce a launch envelope.

The checked-in [machine report](../config/v11/r09_gate3_g3l_offline_prep_20261001.json)
contains an October 1 local host-resource snapshot, a 2,713-row denominator,
an ordered proposed feasibility subset, and 77 unresolved pre-review evidence
identities. (Two further identities — the detached G3-L review report and its
completed terminal — are deliberately not counted here; see "Two-stage
review" below.) The snapshot expires for resource qualification as soon as
host conditions change. Recompute it before any actual package review.
Proposed attempts are not a request schedule: exact endpoint paths, byte
ranges, shared index/cache identities, receipts, and independent source
reviews are absent.

## Contract and template

`tools/v11_r09_gate3_g3l_prep.py` emits a closed-schema
`R09_GATE3_G3L_OFFLINE_PREP_V1` inventory. Each `evidence` key is an exact
required identity; its null value means missing. Filling an entry requires
separate sealed evidence and independent-review references, each with
`sha256`, `byte_length`, `media_type`, and path relative to a supplied private
object root. Entries also carry `observed_utc` and `scope`. Run-specific scope
is `run:<00Z Unix seconds>`; window-specific scope is
`window:<14Z Unix seconds>:<17Z Unix seconds>`. Each identity is run-specific
or window-specific, never both, so a single `scope` string can always satisfy
its identity. The live disk/memory capacity measurement is window-specific
only: it must be as fresh as possible to the acquisition window, not tied to
any one model run, and the window freshness bound is the tighter of the two.
The checker rejects duplicate JSON keys, unknown or absent evidence IDs,
placeholder references, unresolved or hash-mismatched objects, wrong scopes,
future clocks, stale current-run or window evidence, and a review occurring
after the fixed start. A resolved pre-review inventory is only **assembled
for independent review**.

### Two-stage review

Assembly and review are two separate stages, because the detached review is
what produces the report and terminal — they cannot be required to already
exist before the package they review has even been assembled. The 79 total
evidence identities split into:

- **`PRE_REVIEW`** (77 identities): every identity except
  `review.detached_g3l_report` and `review.detached_g3l_completed_terminal`.
  These two must stay explicitly null at this stage — supplying them early is
  rejected as premature. A complete `PRE_REVIEW` packet reaches
  `ASSEMBLED_FOR_INDEPENDENT_REVIEW`; this is a statement that the inputs are
  ready to send out for the detached review, not a launch grant.
- **`FINAL`** (all 79 identities): also requires the completed detached
  report and terminal, each with their own sealed artifact and independent
  review reference under the same generic entry schema as every other
  identity. A complete `FINAL` packet reaches
  `FINAL_REVIEWED_PACKAGE_NO_LAUNCH_AUTHORITY`. `launchable` is `false` in
  both stages and in every status this tool can emit; this tool never grants
  launch permission at either stage.

`check_inventory(..., stage="PRE_REVIEW" | "FINAL")` and
`--stage pre_review|final` on the CLI select the stage; `pre_review` is the
default.

The eventual private payload must satisfy the existing closed
`R09_GATE3_LAUNCH_MANIFEST_V4` schema and
`validate_manifest_v4(raw, repo=..., object_root=..., now_utc=...)`. The
[null-only V4 key skeleton](../config/v11/r09_gate3_g3l_private_v4_null_template.json)
uses the current validator's groups and fixed nested group keys. Repeating
schedule and receipt entries remain unfilled. It carries only fixed
approved policy/date values; missing identities and receipts remain null. A
test proves the validator refuses it. It must be completed in a private
store only after genuine evidence exists. The validator checks actual Git
commit/tree objects, source/product mappings,
cohort/time, 2,713 slots, exact schedule and purpose totals, resource bounds,
and resolved artifact refs. Its returned digest is not permission. The
detached G3-L report and completed terminal must independently bind precisely
those canonical bytes and accepted executable identities. No manifest hash,
review terminal, directory device/inode, or current-run receipt is predicted
in this preparation artifact.

## Freeze checklist

The candidate local target date is **2026-10-02**, as recorded in the
readiness audit. The approved fixed window is October 1 14:00–17:00 UTC;
decision and feature cutoff are 18:00 UTC. The 00Z run is a candidate only:
`LATEST_COMPLETE_READY` must be evidenced using the conservative decision
bound. One named station/version and one or both HIGH/LOW events must be
chosen and recorded *before* inspecting relevant forecast values or labels.
Freeze coordinates, IANA timezone and tzdata bytes, official event IDs/rules,
Celsius rounding and complete buckets, settlement references,
contemporaneous metadata receipts, requested-to-Gate-2 key mapping, and the
selection rationale. Verify the pinned timezone gives a local-day start
strictly after 18:00 UTC; the date alone cannot prove this. Null checklist
identities intentionally remain null here.

## Conservative resource bound

The planner uses the reviewed GEFS/IFS/AIFS field ceilings of 2/4/4 MiB;
each proposed field pessimistically reserves one unshared 3 MiB index, one
4 MiB object-identity request and one 4 MiB metadata request. Thus it claims
no benefit from shared ECMWF files or cached indexes. All 2,713 slots stay in
the denominator, with nonproposed rows explicitly marked
`NOT_ATTEMPTED_BUDGET`; their reason is a **plan**, not a fabricated runtime
terminal. Stable round-robin by provider and native member/hour order chooses
the subset without forecast or label inspection.

The bound enforces 3,600 requests, 1 GiB received bodies, 10,800 seconds,
30-second per-request deadline plus two-second spacing, 4,096 store objects,
10,000 store events, 32,768 session events, and 131,072 budget events. It
reserves four 64 MiB journals, 16 MiB final report space, 4 MiB per potential
store object, 64 MiB decoded workspace, full response reservations, and
another 32 MiB of disk/memory headroom. It retains 2 GiB free disk and
512 MiB available memory *after* the prospective allocation. This is a
screening upper bound; real decoder peak memory, journal record count,
physical persistence, and actual request schedule still require evidence and
review. No ceiling is widened by the plan.

To regenerate a report, supply contemporaneous measured byte counts and
the observation time:

```bash
python3 -m tools.v11_r09_gate3_g3l_prep \
  --target-date 2026-10-02 \
  --free-disk-bytes <measured-bytes> \
  --available-memory-bytes <measured-bytes> \
  --observed-utc <unix-seconds> > offline-prep.json
```

The command exits 2 while evidence is missing. An inventory can be checked
with `--inventory <inventory.json> --object-root <private-object-root> --stage
pre_review|final` plus the same date/resource/time arguments; `--stage`
defaults to `pre_review`. The checker performs local file reads only. Neither
this command nor a complete inventory at either stage grants G3-L PASS.

The current blockers include unfinished slice-3 acceptance; exact current
runtime/decoder/clock/storage qualification; GEFS/IFS/AIFS source, access,
release and purpose mapping dossiers; unresolved ECMWF 503/429 restriction
lineage; today's station/event metadata and current-run index/object/range
evidence; and a completed detached G3-L review. Where new provider evidence
is necessary, the accepted addendum requires a separately reviewed bounded
preflight before any request. G3-E, learner and SHADOW remain later gates.
