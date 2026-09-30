# R09 gate 2 fresh exact-commit acceptance review — PASS

Reviewed commit: `1ab551dd1b01f85b0e58928e9739d0b6e9dbe92b`.
Tree: `ad54216637213d2c0d428de1d1acdae01d45098b`.
Independent reviewer: `/root/r09_exact_review`, 2026-09-30 UTC.

**PASS for this exact offline synthetic repair. No P1 or P2 findings remain in the reviewed N1–N4 scope.** This is a new verdict based on the new commit and fresh executions; the earlier `a1e29fa` CHANGES_REQUIRED verdict remains intact for that older commit.

## Evidence

Inspected the complete incremental two-file diff from `a1e29fa` and the validator's type, evidence, decoder, settlement, lineage, inventory, example-admission and corpus-replay logic. Final HEAD/tree matched the identities above, the candidate worktree was clean, and `git diff --check a1e29fa 1ab551d` passed.

Focused trajectory and affected multi-model panel suites: **181 passed, 1 skipped in 13.97 s** (`targeted.log`). Tests ran with bytecode and pytest cache writes disabled.

Fresh independent harness: **45/45 probes passed**, comprising **37 negative probes verified through BOTH `validate_example` and `validate_corpus`**, and eight positive controls. The harness uses fixture constructors for setup, independent mutations/assertions and fresh registries; it does not invoke builder test functions. Each counterexample uses an unchanged resolver dictionary for the two admission calls. Foreign context probes include correctly hashed payloads and matching clocks already present before freezing that resolver, so rejection is not merely missing-byte detection. Exact source snapshots and the diff are preserved under this review directory.

## Acceptance results

| Area | Independent result |
| --- | --- |
| N4 prior fallback bypass | Substituting the preexisting `station-v1` payload for outage evidence now rejects in both APIs with `INJECTED_EVIDENCE_VERIFICATION_FAILED`. |
| N4 finite inventory | Removing all IFS candidates or one IFS cycle rejects with `RUN_CANDIDATE_INVENTORY_INCOMPLETE_OR_DUPLICATE`. All four IFS slots are required even when no IFS points are admitted. |
| N4 status integrity | Reclassification and status-clock mutation reject against unchanged evidence. Properly evidenced INCOMPLETE/UNAVAILABLE status combinations preserve legitimate fallback. |
| N4 outage context and clock | Wrong provider, protocol, decision, inventory, contract or status payloads reject even with their own valid matching clocks. Changed clocks, provider-only clocks, absent payloads/clocks and duplicate outage records reject. |
| N4 eligible missing provider | A fully evidenced complete ready IFS candidate, together with re-bound inventory/outage payloads, rejects with `OUTAGE_PROVIDER_HAS_ELIGIBLE_RUN`. |
| N4 latest run | Omission, status alteration, inventory-identity alteration and missing status-clock bytes reject for present providers. A newer ready run rejects the older selection; correct latest selection among two ready runs passes. |
| N1 | Member/hour replication, grid mutation and indexed-message-location changes reject. New extraction manifests cannot relabel unchanged raw synthetic bytes. |
| N2 | Winner, status, revision, target, provenance, version and clock changes reject. Missing label payload rejects. A valid append-only correction is selected and its missing parent rejects. |
| N3 | A genuine but wrong timezone rejects against unchanged target metadata. Legitimate Los Angeles and fractional-offset St Johns metadata/schedule controls pass. The affected suite retains 23/25-hour and fractional-offset day tests. |
| Positive controls | Baseline, two stations sharing 62 raw captures with distinct extraction values, two messages sharing one response, correct latest-run selection, and explicit outage fallback all pass. Fallback reporting remains GEFS=1/IFS=0 over the frozen full cohort. |

The decisive repair computes the inventory over `coverage_policy.required_providers`, verifies canonical context-bound outage bytes, binds the outage clock to those exact bytes, and rejects fallback for an absent provider with an eligible ready run. Corpus admission replays these same checks with fresh shared registries and compares the complete result.

## Reproduction and scope

```bash
PYTHONDONTWRITEBYTECODE=1 /home/alphaadmin/AlphaV11_Dev/venv/bin/python /tmp/alpha-v11-r09-review-1ab551d/repro_review.py
```

Artifacts: `review.md`, `terminal.json`, `repro_review.py`, `repro_results.json`, `repro.log`, `targeted.log`, `exact.diff`, and exact source snapshots under `exact/`.

This PASS establishes the reviewed offline synthetic contract behavior. It grants no real-adapter, collection, fitting, production, financial, promotion or host authority. Every admitted control remained SYNTHETIC with all authority flags false and zero real admissions. No candidate edits, merge, service action, real data acquisition/admission, V10 change or financial action occurred. No merged-tree acceptance is claimed. Candidate worktree reads are complete.

R09_GATE2_REVIEW_PASS
