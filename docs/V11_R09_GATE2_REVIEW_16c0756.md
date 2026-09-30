# R09 gate 2 final-repair acceptance review — CHANGES_REQUIRED

Reviewed commit: `16c0756d90762ec2fee59d50737374d0848eb0f8`.
Tree: `2c8e70418ef89f1f01f3a7595a72a2904f53a6b5`.
Reviewer: independent Astra/high, separate from the repair builder.
Date: 2026-09-30 UTC. Exact detached review checkout:
`/tmp/alpha-v11-r09-gate2-review-16c0756/Alpha`.
Main inspected: `e1b67da5368ddbd8c8441afca5e82e7aec08d5c8`.

**CHANGES_REQUIRED. Four remaining P2 contract/evidence-binding defects block
acceptance. Do not merge this candidate or advance to real trajectory admission.**
These are reproducible offline validation defects; every resulting record remains
SYNTHETIC with all financial/admission authorities false. No financial bypass or
real provider acquisition was attempted.

## Independent evidence and compatibility

Read the three durable navigation records, repository guidance, the accepted
[data-contract adjudication](V11_R09_DATA_CONTRACT_ADJUDICATION.md), and the
[prior R1–R7 findings](V11_R09_GATE2_REVIEW_2d116af.md). Read the complete repaired
validator and its fixtures/adversarial cases. The private FINAL-REVIEWED master
was verified in place against the existing SHA-256 pin; no private content was
copied. The accepted public contract already establishes these requirements.

- Exact-commit trajectory and affected multi-model panel suites: **167 passed,
  one skipped in 10.54 s**. Log: `targeted.log` in the review directory.
- Independent synthetic harness: **12/12 checks completed**, exit 0. This includes
  six incorrect acceptances grouped into four findings and six positive/rejection
  controls; it does not mean the implementation passed acceptance.
- Every incorrect-acceptance probe reaches BOTH `validate_example` and
  `validate_corpus`. The resolver uses a frozen dictionary of preexisting evidence
  bytes. Probes neither replace resolver content nor rely on private reflection,
  `object.__setattr__`, or bypassing the public typed constructors.
- `git diff --check cdbc95c..16c0756` passes. Candidate and detached review checkout
  are clean. Candidate lineage adds only the validator and its tests.
- Non-mutating `git merge-tree --write-tree e1b67da 16c0756` succeeds, producing
  hypothetical tree `5f13a977bf0ba2c8dab7a62d16da4c18a29263e2`; relative to main it
  adds exactly those two files. The imported panel helper has not changed since
  the candidate's base. This proves mechanical compatibility, not release or
  merged-tree acceptance. No merge or full-suite rerun was performed after the
  acceptance counterexamples were established.

Review artifacts: `repro_review.py`, `repro_results.json`, `repro.log`,
`targeted.log`, `review.md`, `verdict.json`, `terminal.json`, all under
`/tmp/alpha-v11-r09-gate2-review-16c0756/`. The terminal binds exact commit/tree
and hashes of source, tests, report and independent evidence. It records completed
CHANGES_REQUIRED review, not implementation acceptance. The older builder terminal
for `fed1cbe` remains unrelated to this final repair.

Reproduce without modifying the candidate:

```bash
PYTHONDONTWRITEBYTECODE=1 /home/alphaadmin/AlphaV11_Dev/venv/bin/python /tmp/alpha-v11-r09-gate2-review-16c0756/repro_review.py
```

## What the repair establishes

| Prior finding | Reviewed result |
| --- | --- |
| R1 artifact and conservative timing | Conservative pre-day freeze, artifact byte identity, split/policy/partition checks and availability-by-decision now have passing controls. No remaining R1 blocker identified in this bounded review. |
| R2 public record forgery | Corpus revalidates retained source inputs with fresh shared registries and compares the complete result. Missing source messages and summary forgery reject. |
| R3 settlement identity | Foreign station labels, version drift and family grouping are checked; settlement timezone remains unbound (N3). |
| R4 label lineage | Family/partition drift, corpus-wide version conflicts and explicit as-of selection are checked; immutable evidence does not bind label payload (N2). |
| R5 capture provenance | Raw/index/manifest/dependency bytes and receipt clocks are resolved; decoded source coordinates and grid geometry remain detached (N1). |
| R6 multiple stations | Separate raw/extraction registries now permit legitimate station extractions from the same message; existing positive test passes. Do not regress this in N1 repair. |
| R7 coverage/run policy | Content-derived immutable policy, schedule, provider/fallback reporting and grouped trial counts work; the candidate inventory can still be selectively truncated (N4). |

## N1 — P2 — message semantics and extraction geometry are not bound to evidence (R5/F7)

Locations in the reviewed source: `ExtractionEvidence` at line 213,
`validate_coverage` at 472, `CaptureRegistry.register` at 821, and the point
admission loop at 1062.

One valid GEFS member-0/hour-12 point can be copied with `dataclasses.replace`
into all 31 members at hours 12 and 24, updating only the caller's member/hour
and declared valid time. Both admission APIs accept **62/62 required slots** from
**one raw capture and one extraction manifest**. The existing evidence resolver
is unchanged. Coverage counts caller-supplied coordinates; the capture/extraction
manifests do not bind provider/release/run/member/forecast-hour/valid-time or a
message/field identity to decoded bytes. Registry keys partition these invented
coordinates instead of verifying them against evidence.

Separately, changing one extraction's latitude from 33.64 to -33.64, longitude
from -84.43 to 84.43, distance from 5 km to 0, and grid resolution from 0.25 to
2.5 still passes a fresh corpus with the SAME grid hash, extraction manifest and
resolved evidence. A shared registry detects a second conflicting observation,
but cannot authenticate the first observation in each admission. The extraction
manifest binds only the grid hash, not these geometry fields or their decoded
interpretation.

Required repair: a content-bound synthetic decoded-message/extraction manifest,
verified through the injected evidence interface, must bind complete source
coordinates, parameter/unit, raw response/message location, station metadata,
grid geometry and extraction policy to the admitted point. Cross-check every
semantic field at corpus admission. A synthetic decoder/verifier is sufficient;
no real acquisition or real fitter belongs in this repair. Preserve legitimate
multi-station and multi-message responses; merely banning repeated raw hashes
would incorrectly reject them. Include single-corpus relabeling/replication and
grid-mutation cases against an unchanged resolver.

Probes: `R5_member_hour_replication`, `R5_grid_metadata_rewrite`.

## N2 — P2 — label version and clock evidence do not authenticate label content (R4/F6)

Locations: `_label_fact_seal` at 652, `LabelVersionRegistry` at 662, and selected
label admission at 1096–1105.

Take a valid label with winner bucket 3. Change only `winner_bucket` to 7,
retaining the version, target, knowable timestamp and all resolver evidence.
The changed label passes both APIs in a fresh corpus. Original and altered
admissions therefore produce different outcomes from the same immutable evidence.
Removing the bytes under the label-version digest entirely also passes.

The fresh registry correctly rejects two conflicting versions supplied together,
but `_label_fact_seal` is computed from caller claims and never matched to a
resolved canonical label manifest. `verify_bound_clock` authenticates a timestamp
whose subject is the version string; it does not authenticate the outcome,
status, revision link or settlement payload identified by that string.

Required repair: resolve and verify a canonical immutable label payload containing
its target, winner, status, revision link and provenance. Bind version identity
and its clock evidence to that payload, avoiding circular hash definitions.
Validate the complete lineage used for selection and retain corpus-wide conflict
checks. A new outcome must require a new append-only version and matching evidence.
Test changed winner/status/target/revision and missing label bytes with the SAME
resolver, including a corpus containing only the altered example.

Probes: `R4_label_winner_rewrite`, `R4_label_bytes_absent`.

## N3 — P2 — a valid timezone file is not settlement-timezone binding (R3/F5)

Locations: `LocalDay` at 251, `SettlementTarget` at 558, and target checks at
1107–1122.

Replace only a KATL example's `LocalDay` from America/New_York to
America/Los_Angeles, using each zone's genuine pinned file and valid geometry.
Both APIs accept it with unchanged target, station/rule versions, protocol,
labels, clocks and resolver. The settlement window moves **10,800 seconds** and
in-day points fall from 62 to 31. The canonical target contains the date but no
zone/window binding; station metadata bytes are checked for existence/hash only.
A correct tzdata hash does not establish which timezone the settlement uses.

Required repair: bind the settlement's IANA timezone and day-window interpretation
to versioned station/rule metadata and the canonical target, then require the
`LocalDay` to match that evidence. Preserve timezone file pinning and genuine
23/25-hour/fractional-offset behavior. Test a wrong but otherwise valid timezone
with unchanged metadata evidence, and legitimate zones with their own metadata.

Probe: `R3_timezone_rewrite`.

## N4 — P2 — latest-run selection trusts a caller-truncated inventory (R7/F4)

Locations: `RunCandidate` at 369 and inventory/selection checks at 1023–1042.

With an older selected run plus a newer complete, ready-by-decision run in
`run_candidates`, admission correctly rejects `RUN_SELECTION_NOT_LATEST_ELIGIBLE`.
Remove only the newer candidate from that tuple, keeping the protocol and resolver
unchanged: both APIs now accept the older run. The newer run is six hours later,
its readiness is after its own initialization and before the decision, and both
runs meet the frozen age/cycle limits. This is not an impossible pre-init candidate.

The check named `RUN_CANDIDATE_INVENTORY_INCOMPLETE_OR_DUPLICATE` compares provider
sets and duplicate keys; it does not establish inventory completeness. Independent
ready-clock payloads for individual runs do not freeze the set that was searched.

Required repair: represent and verify a protocol/decision/provider-bound inventory
covering the finite eligible lookback/cycles, including unavailable/incomplete
candidates and the evidence for their status. Select from that evidenced inventory,
not an arbitrarily supplied subset. The offline repair needs only synthetic
inventory/status manifests. Do not turn it into data collection. Test omission,
status alteration and changed inventory identity while preserving correct latest
eligible selection and explicit outage handling.

Probe: `R7_run_inventory_omission`.

## Durable handoff and boundaries

Next: substantive repair of N1–N4 in the SAME preserved builder worktree
`/tmp/alpha-v11-r09-trajectory-gate2/Alpha`, retaining `16c0756` and all newer work.
Use this harness as independent acceptance cases; each incorrect acceptance must
become a rejection while baseline, multi-station, multi-provider, grouping and
geometry positives continue passing. Run focused/affected tests, commit the final
repair, and obtain a fresh independent exact-commit review before integration.
This is normal substantive implementation suitable for Sonnet/high; no deeper
architecture escalation is needed. No duplicate worker was started during review.

Main and SHADOW (`15e99bd`) were clean. PAPER scanner, V10 paper demo, and V11/weather
controller/execution units are inactive; weather execution is masked. Protected
model-authority paths remain absent. Since the 16:00 checkpoint only commissioning
watchdog status files changed; no qualifying forward sample appeared. Root disk
is 76% used with 4.7 GiB free; available memory about 997 MiB. No service, V10,
protected-authority or financial action was taken. The earlier publication block
remains: automatic approval rejected the configured GitHub destination because
trust/privacy and ownership were not established. No push was retried.

Score remains **91/200 (45.5%), formal 1/50; NOT_READY_TO_FUND**.

R09_GATE2_REVIEW_CHANGES_REQUIRED
