# V11 bounded causal economic replay

`PerformanceLab.replay_temperature(evaluation_id, policy=ReplayPolicy(...))`
recomputes a retained FUTURE_FORECAST or SAME_DAY_LATE_LOCK economic evaluation.
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
control gates, PWS/source-release/relative-value/maker/exits, challenger comparison,
reservation/execution command replay and operational acceptance remain open.
Scheduled candidate audit integration follows this checkpoint. Tests/provenance:
V11_WORK_CHECKPOINT.md. NOT_READY_TO_FUND; no financial authority is added.
