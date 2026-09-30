# Combined release integration acceptance — 2026-09-30

**PASS for local development integration only.** Astra/high independently
accepted release `6ec371e6c03eebf5f83550e16ac2cb318e6726d8` (tree `4d268adb7cf1870c07ea1b76e0b40b53d1c2d566`) against development
main `9711391b05cecbe59f2d34109f7d823c1b421b0f`. No blocking integration finding remains in this exact change.
This is not deployment, commissioning, forward-evidence, model-promotion or
financial acceptance.

## Scope and compatibility

The common base is `89b5f76`. Newer main changes only the three navigation
ledgers and the R09 gate-2 review document. The release adds the three SHADOW
source files, seven changed/new test files, the shared opt-in test fixture,
and the scheduler investigation note (12 files total). The normal two-parent
merge is conflict-free. Its entire non-document tree matches the passing
release exactly; all four newer-main documents were preserved before adding
this acceptance entry. The R09 gate-2 implementation remains unmerged.

All five SHADOW source/test files are byte-identical to independent-review
commit `15e99bd09ff7e7b9905c6e93baaead1dedbb03d3`. That review directory contains the independent probe
source and its 13-pass log, but no standalone verdict or terminal JSON.
The checkpoint's reported PASS is therefore supporting history, not a missing
artifact inferred into existence. This fresh acceptance binds the combined
release and newer main directly, with a new verdict and terminal record.

## Safety review

The typed assembly registers its actual cohort; commissioning checks current
runtime configuration against that cohort before collection. Current rule,
station certification, protected model identity, feature schema, release and
worker identities remain required. Frozen authority changes refuse replay;
audit reuse checks kind, channel and semantic content. Missing protected
state fails closed. No root authority installer, financial entry point,
service definition or V10 artifact is added or changed.

The wrapper restricts its namespace to CHALLENGER/ABLATION and retains false
financial/promotion/deployment authority. Its static import check is only a
source check, not a transitive sandbox or host-custody proof. Operational run
links do not establish causal forward evidence: qualifying sample and forward
commission counts are explicitly zero. Actual forward lineage and grouped
outcome qualification still require separate implementation and acceptance.

Scheduler changes are confined to tests. The virtual clock is opt-in for
mocked transports and explicitly tests interruption, durable cursor recovery,
continued DRIFT work, cancellation and unchanged inventory. Synthetic GEFS
preparation alone substitutes process CPU time for elapsed wall time; runtime
source-view bounds are unchanged. Production candidate scheduling, evidence
source views, GEFS assembly and paper runtime match the common base exactly.
This clears the diagnosed test-budget failures for this release; it does not
certify hard real-time host performance.

The private FINAL-REVIEWED master's SHA-256 was independently matched to the
existing pin, and relevant namespace, nonfinancial execution and bounded
shadow-work requirements were inspected. No private input was modified or
copied into this report.

## Verification

- Existing exact-release affected gate: **396 passed / 417.19 s**, exit 0.
- Existing exact-release full gate: **5,460 passed, 13 skipped, 4 warnings /
  1,509.45 s**, exit 0. Both log hashes and terminal commit/tree matched.
  The unchanged full suite was not rerun.
- Fresh independent acceptance: **18 passed / 15.32 s**, exit 0. Re-executed
  the prior 13 configuration/replay probes against the combined release,
  plus real-time timeout recovery, cancellation during blocked public I/O,
  GEFS expired-deadline refusal, absent-authority refusal, and live-shaped
  schema compatibility without forward-evidence claims. All data/authority
  fixtures were synthetic; no actual collection or authority installation.
- Merge index: no conflicts, clean whitespace check, exact release code/test
  identity and preservation of newer main records independently asserted.

Evidence: `/tmp/alpha-v11-release-acceptance-6ec371e/` (`probes.log`,
`verdict.json`, final `terminal.json`); existing release terminal
`/tmp/alpha-v11-release-gate-6ec371e.terminal.json`.
Affected log SHA-256: `813f60f9486daeb57401d824a00da9be651e64ad8f73cac19a5abe37cff20d27`.
Full log SHA-256: `c61c3f5861faebb7d586211b17efb8eb709b67bf6a1be410906a76c2048d4246`.
Fresh probe log SHA-256: `85e5f4d994aadb245a85fa0d990c938af98d5ca9d20c39891a494103f091730c`.

## Remaining boundaries and next work

PAPER scanner remains inactive following `DISK_AT_OR_ABOVE_85_PERCENT`;
root disk is 86% used with 2.8 GiB free. Weather execution remains masked
and inactive, controller/paper demo inactive, protected authority paths absent.
No service, deployment, financial or V10 action was taken. Current evidence
changes are watchdog status, not new forward qualification. Local integration
does not clear the prior GitHub destination approval rejection; no push was
attempted.

Path A requires owner/root-custodied authority and separately reviewed causal
forward qualification; leave the scanner stopped. The next unblocked substantive
work is R09 gate-2 R1–R7 repair in the existing preserved builder worktree,
then independent exact-commit review. Its seven P2 blockers and OPEN gate are
unchanged. **91/200 (45.5%), formal 1/50; NOT_READY_TO_FUND**.
