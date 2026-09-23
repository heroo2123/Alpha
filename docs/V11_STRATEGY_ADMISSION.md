# V11 scoped strategy admission

`v11/strategy_admission.py` joins existing station capabilities, protected model
epochs, universal rule quarantine and causal source leases. A proposal now needs
an admission pin for every attributed strategy. The paper coordinator checks
these pins before reservation and again before entering its submission state.
This is a nonfinancial data gate; it grants no order, activation or funding right.

Pins bind station, HIGH/LOW, source/rule family, model version, horizon, strategy,
season/time regime, exact event/account/city, metadata, rule and release bundle.
Protected certification must permit PAPER or SHADOW. A model overlay can reduce
position size; manual review, zero allocation or changed epochs suppress admission.
An unprovisioned protected registry/review stays gated. No self-approval interface
or production credential is introduced.

Forecast leases require known issue/run time and exact station/date/family/unit/
rule identity. Receipt cannot substitute for run age. Official leases retain the
station relationship; a proxy observation is not upgraded to finality. PWS leases
require the QC adapter's healthy station/metadata context and actual sensor ages,
not a new feature timestamp around old sensors. Historical unknown availability,
wrong targets and older received revisions are refused.

Source heads are captured before individual checks and guarded by the final
SQLite transaction. A source arrival racing pin creation or account reservation
invalidates the update. Every pin watches official arrivals, including an absent
official head. New official evidence invalidates pre-confirmation theses; new PWS
data requires QC recomputation. Certification failures, review changes, rule
drift, expiry and model demotion remain effective after restart and cannot be
cleared by an old pin. The protected model/review files are re-read; ultimate live
executor epoch/custody commissioning remains a separate requirement.

PWS next-observation, final payout and executable-exit values are still separate.
This package does not manufacture a lead probability, payout label, finality or
exit price. Actual accepted models, empirical PWS lead/ablation, complete strategy
factories and live guardians remain unfinished. All current uncalibrated settlement
opportunities remain economically rejected. Existing coordinator acceptance
fixtures explicitly stub protected admission only in their isolated downstream
tests; this module's tests exercise the actual joined readers using synthetic
protected-manifest fixtures and immutable artifacts.

15 admission tests plus the expanded coordinator test and existing dependencies
pass 109 targeted checks. They cover scope mismatch, missing review, source and
model changes, unknown model age, stale PWS sensors, new official observations,
wrong forecast date and arrivals racing the atomic head checks. These tests are
code evidence, not independent review or runtime commissioning.
