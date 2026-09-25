# Retained PWS receipt-score cohort and scheduled audit evidence

2026-09-25. Continued published `fdf85f560f44d0e164038196e52492f3b6f3aac0`,
tree `e8274c87743706cce30f456c3cb4552f9508bf60`. Original master unchanged;
V10 unchanged and maintenance deferred. No private evidence/input, credential,
financial action or live service change is included.

Optional typed `AuditPolicy.pws_score_replay` now selects receipt-score records in
the existing worker's pinned archive/window. Its absence is omitted from policy
serialization, preserving default scheduler/worker/candidate identities. Enabling
it uses existing configuration-review gates. No new scheduler or runtime writer
was introduced.

The cohort counts every selected score, including UNKNOWN, legacy and unsupported
protocols, while excluding observation leads and start records. At most 32 score
references are retained; the full count survives overflow. One bounded source
snapshot and total deadline cover all selected replay work. Originals bind by
ID/hash/sequence/window before replay, so individual legacy/dependency gates remain
visible rows. Missing selected originals, duplicate/changed references, incomplete
scans, output overflow and shared resource exhaustion gate the whole cohort and
clear earlier matches. The result is capped at 256 KiB, including final summaries.

Original score status, replay status and reproduced measured count are separate.
A matching UNKNOWN score supplies no measured label. Each proof retains original
receipt cutoff, sequence boundary and scan digest/count. Scores are not averaged
across targets; no independent sample count is inferred. Existing PAPER/LIVE P&L,
unaccepted PWS ablation status and acceptance/financial authority remain unchanged.
Published-report recovery reuses the original report without recomputing scores.

## Checks and exact boundaries

- Integrated score/audit/candidate/temperature/portfolio regression: **156 passed
  in 58.68 s**, exit 0, session **11892**. This precedes the final shared-resource
  propagation guard and additional compact scan-proof fields.
- Review found that the core converted shared read-budget exhaustion to a per-row
  gate. The cohort now propagates shared read/time exhaustion to its whole-cohort
  gate. Final tests also cover malformed numeric-window overflow and a combined
  temperature/account/PWS report.
- Final guarded score/audit tests: **35 passed in 13.16 s**, exit 0, session
  **23394**, including finite candidate scheduling, recovery and the new budget
  regression. All **767 canonical non-document source/mirror inputs unchanged**.
- Read-only review and `git diff --check` passed. No independent acceptance is
  claimed. No test operation remains running at this checkpoint.

Previous full **4195 / four existing warnings / 578.00 s** belongs to `0776697`,
before the PWS score and cohort changes. The intervening `fdf85f5` score/dataset
milestone passed **149 / 37.30 s** and **233 / 62.06 s**. These boundaries are
explicit; the old full pass is not attributed to newer code.

Local verification uses canonical Git bytes, hashed requirements, isolated Python
3.11.16/pytest 8.3.3, an exclusive runner lock, saved source/mirror input hashes,
a 1,800-second timeout and bounded temporary memory storage. No production
durability or risk policy changes are used to make tests pass.

Initial runner/evidence root:
`/home/hero_/.local/share/alpha-v11-tools/canonical-verification-20260925T150554Z`;
run `20260925T150613.792892Z-pws-score-audit`.
Final runner/evidence root:
`/home/hero_/.local/share/alpha-v11-tools/canonical-verification-20260925T150924Z`;
run `20260925T150942.160823Z-pws-score-audit-final`.
Each root contains `v11_canonical_runner.py`; each run under `evidence/` retains
the command, output, result metadata and complete input maps. Private data is
absent. Final input-map SHA-256:
`873f0d2b714d3d6a640136786c5c7bb61d12f737253b58ebb11ae1930737e3f8`.

Final output SHA-256: `2b033d2cae0aefc832d2b4265ed55b6fa3cdb36ca5bc0c53dec7c4dd2c0a8cbf`.
UTC start/end: `2026-09-25T15:09:44.097958+00:00` / `2026-09-25T15:10:00.339770+00:00`.

## Acceptance still open

This completes the retained descriptive PWS-score-to-scheduled-report join, not
global observation coverage, original model inference, raw label-source truth,
next-publication continuity, calibration, control-flow or independent acceptance.
The six READY_TO_FUND milestones remain in `V11_WORK_CHECKPOINT.md`.
**83/200 (41.5%, approximately 42%); formal 1/50 (2%)**, unchanged.
**NOT_READY_TO_FUND**.
