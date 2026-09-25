# Pinned PWS receipt-score replay and label dataset evidence

2026-09-25. Builds on published temperature-account replay commit
`0776697aab3032853d99af35902a7f9eafb4ba6a`, tree
`acf1ba8f19ab02900c6c405d36424cbca8506c2c`. The authoritative private master is
unchanged. All changes are local, nonfinancial V11 implementation; V10 remains
unchanged and maintenance deferred.

## Behavior

Each new PWS receipt score has a durable start record binding its observation.
The start's sequence minus one fixes the official-report population; its original
recorded time fixes the label cutoff. An interrupted score reuses that start.
A completed score returns unchanged, including UNKNOWN; a later observation of
the archive requires a new score ID. Different requests cannot reuse the ID.

The runtime and read-only replay share report selection and paired Brier/log-loss
calculation. Corrections, proxies, unknown-availability receipts, late reports and
negative PWS improvement retain their semantics. The score archives an ordered
scan-reference digest, count, original boundary and the first update's identity,
including skipped updates. Numerical distributions must have bounded unique
partition-matched buckets, finite coherent probabilities and the original
event/rule/target/prediction time. Infinite log loss is explicitly represented.

Replay borrows the existing bounded source snapshot, writes nothing and gates on
missing/legacy pins, malformed vectors, incomplete scans or elapsed deadlines.
It compares the complete score and evidence references. A failed final budget
check discards positive comparison fields. Observation datasets now require this
proof and retain its digest and receipt boundaries. Direct store callers open
one outer read-only view; paired children reuse it. Original prediction/feature
cutoffs remain earlier than the later label cutoff, and the ablation pair still
represents one event rather than two independent samples.

This proof reproduces scoring of archived predictions; it does not recompute
those predictions or attest source truth, collection continuity, true next
publication, calibration, executable repricing, P&L or model promotion. Missing
legacy boundaries are never reconstructed from a final score's timestamp.

## Verification

- Focused plus candidate integration: **149 passed in 37.30 s**, exit 0,
  session **70788**. New cases cover durable recovery, same-clock later receipts,
  1,000 later receipts, original overflow, missing/skipped receipts, tampered
  starts, score mismatch, legacy dataset rejection, malformed vectors, infinite
  log loss, deadline clearing and original paired dataset timing.
- Affected learning, admission and audit regression: **233 passed in 62.06 s**,
  exit 0, session **2645**. Both runs preserved all source/mirror input hashes.
  No test operation remains running.
- Read-only code review and `git diff --check` passed. No review is represented as
  independent original-specification acceptance.

The previous full **4195 passed / 578.00 s**, four existing FastAPI warnings,
belongs to `0776697` before these PWS changes. It is not a full pass for this
new milestone. Related tests are selected for the new runtime scoring and label
dataset changes; unchanged full regression is not duplicated without cause.

Both new runs use the same isolated Python 3.11.16 / pytest 8.3.3 environment,
hashed repository dependencies, canonical Git input bytes, exclusive verification
lock and bounded local temporary storage. No dependency, production durability,
risk configuration, credential or live service changes were made.

Local runner:
`/home/hero_/.local/share/alpha-v11-tools/canonical-verification-20260925T145115Z/v11_canonical_runner.py`.
Evidence root:
`/home/hero_/.local/share/alpha-v11-tools/canonical-verification-20260925T145115Z/evidence`.
Focused run: `20260925T145129.245980Z-pws-score-targeted`.
Affected run: `20260925T145259.273807Z-pws-score-affected`.
The synthetic logs and complete input maps are outside the repository; private
input contents and private runtime evidence are absent.

Canonical non-document input count: **765**, manifest SHA-256
`b8607873d0565acc26b66626fe9edaccfa5ea58d71f7549f643f4404132e1426`.
- `20260925T145129.245980Z-pws-score-targeted`: output SHA-256 `565c9d92ffc11e828cd5721fa245f61aefaf3004247fbaa741f6cab31572aff8`, source/mirror inputs unchanged; exit 0. UTC 2026-09-25T14:51:30.973692+00:00 to 2026-09-25T14:52:11.094930+00:00; temporary peak 761856 bytes.
- `20260925T145259.273807Z-pws-score-affected`: output SHA-256 `d9c9184ed35f894913be6e3938dddbe84e8676fd3b5133edc41aec9a42162c72`, source/mirror inputs unchanged; exit 0. UTC 2026-09-25T14:53:01.216319+00:00 to 2026-09-25T14:54:05.893212+00:00; temporary peak 1736704 bytes.

Only `pws_lead.py`, `pws_scoring.py`, `target_learning.py` and
`test_v11_pws_score_replay.py` differ from the preceding published implementation.

## Remaining scope

This closes the pinned descriptive receipt-score and dataset replay join. Independent
observation inference/source attestation, raw-label derivation, broader control/challenger
replay, observation-target fitting, isolated learning operations, real evidence
and independent acceptance remain separate unfinished requirements.
**83/200 = 41.5%, approximately 42%; formal 1/50 (2%)**, unchanged.
**NOT_READY_TO_FUND**; no financial or promotion authority is granted.
