# V11 PWS observation-lead research

`v11/pws_lead.py` implements an observation-only research sleeve named
PWS_OBSERVATION_LEAD. It consumes separately pinned numeric next-observation
bundles and archived model inputs, with a declared official anchor, receipt
horizon and source-age policy. It does not convert raw PWS votes or a fixed
temperature difference into a probability. Bundle hashes preserve research
identity; they do not attest champion approval or source authority.

The paired PWS-on/PWS-off inference traverses a bounded, hash-bound derivation
graph. Both variants must share the same non-PWS leaf evidence. The PWS-on graph
must include the current QC neighborhood; the ablation cannot retain that input
directly or through another feature. Both require the same official anchor and
horizon. New official/PWS arrivals, model revisions, stale sensors, changed rules
or arrivals racing publication reject the old pre-confirmation calculation.
Bias, smoothing and dependence weights come from immutable bundles.

Outputs retain both observation distributions, conservative uncalibrated bounds,
artifact identities, causal provenance, source heads, horizon and source-quality
limitations. All results remain GATED in RESEARCH. Settlement prediction,
contract payout, executable exit proceeds and order proposals are explicitly
absent. No common account reservation is created, and there is no special PWS
risk allocation or financial authority.

A separate bounded measurement scores the first eligible archived new official
report after inference. Anchor corrections invalidate the thesis but are not
labeled as a new observation. Reports outside the declared horizon remain
unknown. Paired Brier/log-loss differences can be positive or negative; no
benefit is presumed. Receipt lead is an Alpha receipt-time difference, not proof
of provider publication lead, complete feed coverage or market information lead.
The result is DEVELOPMENT and SCORED_NOT_CALIBRATED, never a final-settlement
label, actual fill, executable markout, realized P&L or independent validation.

18 tests cover target/horizon separation, hidden PWS leakage, unmatched controls,
source revisions, stale sensor ages, atomic publication races, corrections,
missing/late/proxy labels and harmful PWS forecasts. Together with existing
probability, artifact, QC and strategy tests, 119 focused checks pass. The prior
full regression remains 2,739 passed; this isolated module adds 18 passing checks.

The separate economic integration is documented in `V11_PWS_OBSERVATION_LEAD.md`.
It joins separately reviewed observation/payout scopes and common paper gates;
the research record itself still creates no reservation or authority.

Remaining work includes reviewed raw-to-inference/official-report adapters,
causal lead/lag training and independent label/continuity evidence, station/regime
out-of-sample validation and demotion, separately approved observation and payout
champions, runtime acceptance and executable-proceeds validation.
Observation evidence alone cannot qualify the economic route. No actual PWS
advantage, calibrated model, eligible paper entry or live readiness is claimed.
