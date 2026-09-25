# V11 bounded causal economic replay

`PerformanceLab.replay_temperature(evaluation_id, policy=ReplayPolicy(...))`
recomputes retained FUTURE_FORECAST, SAME_DAY_LATE_LOCK, PWS_OBSERVATION_LEAD,
SOURCE_SHOCK and RELEASE_OPPORTUNITY economic evaluations.
This is read-only and separate from runtime admission, current model authority,
external transport and the original common-account commands.

A bounded SQLite read snapshot pins immutable original request/start/admission/
valuation references. HistoricalView restricts every source lookup to both original
receipt sequence and original availability/recorded time, including equal-clock
later receipts and backdated observations. Raw derivation and exact target/coverage
checks remain in the existing inference path. A current source or better book
cannot replace a missing original. Unknown availability gates the comparison.

Protected model history is read through existing custody checks. Replay reconstructs
and hashes state prefixes, validates complete previous-state/event links, review
scope/epoch/preimage/time and overlays, then reads the exact original numeric bundle
through the existing approved artifact reader. It does not change any model state,
relax active review checks, or return a renewed model/admission pin. Missing old
history/artifacts stays GATED after a promotion, demotion or rollback.

Runtime and replay share temperature input construction, predict_with_bundle and
settlement_entry_details. The ordinary runtime still persists settlement_entry and
performs its existing independent source/authority revalidation. Replay compares
original canonical prediction, valuation, economic outcome and reasons, at original
inference and valuation times. It does not call a user-supplied evaluator.

The pre-decision common-account snapshot is selected at the same receipt boundary.
Without an archived account head, historical initial cash/policy and risk remain
UNKNOWN; the current caller policy cannot fill that gap.
Policy identity and retention bounds are checked. The existing PaperCoordinator risk
calculation reproduces the stored risk at its original timestamp and computes
context at decision time; later account appends and midnight/report time cannot
silently replace it. This is account context, not reservation-command replay.

Limits: existing 8192-record/8 MiB read view, explicit 0.05–5 second cooperative
budget (default two seconds), 1000 retained model epochs, existing account limits
and 512 KiB result. Deadline/overflow/malformed inputs gate the entire comparison.
No favorable partial result can be reported as an economic match.

ECONOMICS_REPRODUCED means equality of the shared numerical result on retained
inputs, not full control-flow replay, original executable attestation, independent
source truth or renewed permission. GATED and MISMATCH remain distinct. Early
control gates, PWS label/outcome scoring, full relative-value/maker/exit selection,
challenger comparison,
full preparation/control-flow and operational acceptance remain open. Conditional account effects are described below.
Scheduled candidate audit integration is described below. Tests/provenance:
V11_WORK_CHECKPOINT.md. NOT_READY_TO_FUND; no financial authority is added.


## Scheduled candidate integration

AuditPolicy.replay accepts an optional explicit ReplayPolicy. Its omission preserves
legacy config/assembly and report identities. Config changes use the existing
review/reconfiguration gates. Scheduling remains a small request in the safety loop;
replay calculations run only in the separate publishing worker.

Original completed temperature decisions are selected by half-open recorded-time
window within the pinned archive sequence. Start-only records are not decisions.
All completed variants count, including early control gates and unknown variants. The worker retains at most eight exact refs and the full count.
Overflow or incomplete archive scanning gates the entire cohort. Unknown individual
comparisons stay visible; no sample of favorable results is called full coverage.

One shared configured replay budget (default two seconds, maximum five) applies to
the entire publication cohort, in addition to the existing scan/metadata and optional
cost-report budgets. Deadline failure clears prefix matches. Reports retain compact
comparison/model/account references and a hash of each full result, with explicit
separate selection/economic coverage and false full-control-flow/executable flags.
Report-before-cursor recovery does not rerun already-published comparisons. Later
source/account rows, model promotions and expired current data cannot substitute
original inputs. Retained scope coverage does not attest the market universe.

The finite candidate integration uses mocked public source input and synthetic
model/account history. The uncalibrated temperature decision stays rejected; replay
does not make it eligible. Final targeted 27 passed in 4.94 s. Affected 218 passed in 28.45 s; locked full 4073 passed with four existing warnings
in 285.58 s, all 849 tracked inputs unchanged at 1fea164a. Exact provenance:
docs/V11_REPLAY_REGRESSION_EVIDENCE.md. All original acceptance/authority boundaries remain open.


## Historical PWS observation pair and separate payout

PWS_OBSERVATION_LEAD now reconstructs its original preconfirmation/admission
records and both protected model history prefixes. The original observation
champion is scoped to NEXT_OFFICIAL_OBSERVATION; the original payout champion
remains FINAL_CONTRACT_PAYOUT. The exact without-PWS bundle is read as a research
ablation, never promoted or inferred from today's champion.

`pws_lead.paired_inference` is shared with ordinary runtime observation research.
Both PWS-on/off variants use the original lead feature-ready time and sequence
boundary, official anchor/horizon, model revisions and non-PWS leaf evidence.
Raw-to-QC derivation is verified. Later same-clock source revisions and later
official reports cannot enter either variant. Separately, the original entry
payout and valuation recompute at their existing boundaries; observation output
never becomes a payout or exit price. Historical source/pin/model/artifact absence
gates the whole comparison. Numeric changes produce MISMATCH even if the payout
still matches. No control permission or account command is recreated.

Optional candidate audits retain compact separate target identities, comparisons
and a full-result hash within the existing shared eight-decision/time budget.
Assembled PWS lane and report-before-cursor recovery are covered. Final focused
15 passed / 5.15 s; combined affected/full verification remains due. Full control
flow, original executable attestation, separate label/outcome replay, empirical
lead/lag benefit and independent acceptance remain open. These are synthetic
checks and do not certify PWS independence or source truth. NOT_READY_TO_FUND.


## Historical received-source reactions

SOURCE_SHOCK and RELEASE_OPPORTUNITY now reconstruct the original release pin,
scoped admission/model, immediate before/after same-source report pair, post-receipt
exact book, archived event context, declared schedule and recomputed model lineage.
The runtime's receipt-pair and change-type helpers are shared. Original pin time and
sequence bound the read-only predecessor scan to at most 1000 records; later reports
cannot overflow or replace that original population. Original payout inference,
valuation and common-account context use the existing historical engine.

A numerical source/lineage or payout mismatch remains MISMATCH; missing original
report/book/event/schedule/model evidence gates the entire result. Optional candidate
audits include compact receipt/pin/event refs, separate comparisons and a full-result
hash, within the same shared budget. No schedule proves an actual release, no event
permission is reissued and no settlement finality is inferred. Full control-flow,
account-command and independent operational acceptance remain unimplemented/unpassed.
Combined focused 79 / 17.50 s; PWS baseline affected 380 / 57.74 s; combined affected/
full checks are due. Exact evidence: V11_SCOPED_REPLAY_REGRESSION_EVIDENCE.md.


## Combined verified checkpoint

All above numerical joins passed **463 affected checks / 64.41 s** and locked
full **4104 tests / four existing warnings / 296.20 s**, exit 0, at published
`41d406951579a4c0acbe75f256cf3fc96ac588ed`, tree
`1d3cb8c4ea543a61681361428e68d44cd988ce1d`, all **853 tracked inputs unchanged**.
Final focused 79 / 17.50 s. See V11_SCOPED_REPLAY_REGRESSION_EVIDENCE.md for exact
metadata, complete shared input map, outputs and corrected development failures.
These results supersede the pending verification notes above; no new implementation
or acceptance claim is inferred from the test count. Full control/command/label/
executable replay and independent operational acceptance remain open.


## Original PAPER account numerical effects

`PerformanceLab.replay_account_command(command_id, policy=ReplayPolicy(...))`
uses shared coordinator effect methods for COORDINATE, TRANSITION, RECOVER, FILL
and TERMINAL. New journal rows preserve pre-state/request hashes, exact original
pre-ranking preparation (including rejections), conditional exit control results,
clock reads and atomic guard inputs. Replay reconstructs the immediate original
account predecessor and uses only an exactly matching original policy hash. Even
initial cash requires the original command's policy hash; this does not change
the UNKNOWN policy rule for a decision without any archived account command.

The original preparation transcript is input, never reconstructed from the final
ranking or state. Runtime admission/exit checks and all atomic guards still run.
Replay consumes the original conditional exit result and never calls current
protected admission, submits a command or changes source/account records. Read-only
historical safety overlays and guard heads cannot be replaced by current flags.
The shared engine compares full state/effects/risk and cash, intents, lots, fills,
FIFO realized allocation and faults at the original numerical clock reads.
MISMATCH distinguishes a differing numerical output from missing-input GATED.

This **does not replay preparation/control-flow**, establish independent source
truth or attest the historical executable. Missing old transcripts, predecessor,
policy, required receipts, clocks or guard heads gate. Optional telemetry failures
cannot hide a known original fill; missing replay receipts gate the whole replay.
A safe cancellation under raw clock regression remains recorded and effective,
while that history cannot claim causal numerical replay.

`AuditPolicy(account_replay=ReplayPolicy(...))` passes through the existing typed
candidate. It is optional and omitted from default policy hashing. The complete
pinned audit scan retains all account commands in the half-open original window,
including unknown actions and legacy rows. At most 32 command references, 64 clock
reads/guard heads, six conditional exit checks per batch and 512 KiB input metadata
are permitted. Existing account and read-view bounds still apply. One cooperative
0.05–5 s policy budget (default two seconds) covers the entire account cohort.
Incomplete scans, overflow or deadline exhaustion clear comparisons; no favorable
prefix receives complete credit. Reports remain durable across report-before-cursor
crashes. The reporting budget is separate from cooperative safety-loop work and
is not evidence of the required independently isolated guardian or host capacity.

Tests and raw evidence: `tests/test_v11_account_replay.py`,
`tests/test_v11_account_replay_integration.py`, the joined candidate case in
`tests/test_v11_causal_replay.py`, and `docs/V11_ACCOUNT_REPLAY_EVIDENCE.md`.
All new accounting/strategy evidence is synthetic. NOT_READY_TO_FUND.

## Original prepared basket and exit numerical valuations

`PerformanceLab.replay_account_command(..., replay_valuations=True)` additionally
reconstructs the original prepared basket/exit valuations before account effects.
For scheduled candidate audits use typed
`AuditPolicy(account_replay=ReplayPolicy(...), account_valuation_replay=True)`.
The option is omitted from default policy payloads, preserving previous identities.

Shared `basket_details` computes the whole-event prediction/scenarios and costs
without returning an already archived journal row as a recomputation. Shared
`payout_inputs` and `_value` recompute exit payout, joint hold/sale and FIFO economics
from the original pre-evaluation account snapshot. Replay uses original admission,
protected bundle history, source leases, raw derivation and exact book/time/receipt
boundaries. Basket hypothetical scenario positions remain declared inputs; they
are not relabeled as actual common-account holdings. Synthetic provenance and
missing raw derivation stay explicit. Later models, fills or books cannot replace
the original inputs. Runtime current-admission and atomic controls still execute.

All original prepared candidates remain counted, including later allocation
rejections. Unsupported entry values stay GATED; original rejected preparation and
control-flow remain conditional. Account effects and numerical valuation matches
have separate coverage fields. Unknown commands prevent complete preparation
coverage. Both per-command valuation proofs and the account-audit aggregate have
256 KiB bounds; the existing shared cohort deadline includes final aggregation.
Overflow/deadline clears all favorable prefixes and coverage. Report-before-cursor
recovery reuses the published report without another computation or command.

The typed candidate schedules this audit while its derived new-risk gates remain
intact. Separate mock-census strategy fixtures verify raw normalized-book lineage
under explicitly synthetic risk inputs. These do not attest actual forward
eligibility, executable identity, independent source truth or operational readiness.
Exact full/affected/final-focused evidence and limits:
`docs/V11_PORTFOLIO_REPLAY_EVIDENCE.md`. **NOT_READY_TO_FUND**.


## Original prepared temperature entries

Prepared single-leg temperature entries now reuse the same historical numerical
engine inside the account replay source snapshot and deadline. The original
valuation must link to its completed strategy decision before the account command
in both receipt sequence and recorded time. The exact archived proposal must match
the original account request. Missing/legacy/unsupported linkage remains GATED.

All five temperature sleeves are supported. Prediction, valuation, reasons and
outcomes compare independently; separate temperature comparison keys also preserve
PWS observation/ablation and source-release mismatches. Later models, sources,
books, accounts and dates cannot replace originals. The result retains compact
original references and comparison hashes, without duplicating model vectors.

Every prepared proposal still counts, including allocation rejections. Preparation
rejections remain separately counted, and the temperature decision audit retains
its wider population of early gates and rejected decisions. A prepared-subset match
is not full preparation/control replay. Shared deadline or output overflow clears
favorable prefix results. Scheduled candidate audits and report-before-cursor
recovery use the existing mechanisms without reserving or creating fills.

Positive entry tests explicitly inject a synthetic payout oracle after the actual
prediction binding/target/age/bound checks. This is downstream plumbing evidence,
not calibrated probability evidence or production entry qualification. Five
unmodified-model checks preserve vacuous-bound rejection; removing the oracle
causes the synthetic positive valuation to mismatch. No calibration, empirical
source truth, independent acceptance or financial authority is claimed.
