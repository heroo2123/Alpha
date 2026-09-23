# V11 requirement-to-code/test/evidence matrix

Authority: final-reviewed specification SHA-256 `a0e16d9bd7344c943a54a16a53c6757662363d93642f6e5cb7953cd047659b4a`.

This index covers 50 deliverable work packages. Each is 2% of overall progress;
only a delivered and locally verified package counts as complete. Partial,
blocked, unverified, or stub work scores zero. Existing code is a reuse candidate,
not an automatic V11 acceptance pass. Tests are mapped when run. Runtime, canary
eligibility and empirical validation are separate columns; no unit test grants
financial authority. Detailed requirements remain in the private specification.

All implementation paths below are relative to `polymarket_scanner/` unless
explicitly prefixed with `docs/`, `tests/`, `deploy/` or `host_trust/`.

| ID | Phase | Deliverable | Reuse / implementation | Status / evidence | Runtime / canary / empirical |
|---|---|---|---|---|---|
| R00 | 0 | Input/control identity | V11_INPUT_MANIFEST.json; production/runtime attestation | OPEN: Source hash and 221 file comparisons; protected runtime evidence blocked | NOT VERIFIED / NOT ELIGIBLE / NONE |
| R01 | 0 | Consistent snapshot and V10 forensics | weather_only_paper_backup.py; weather_only_paper_post_receipt.py | OPEN: Online backup primitive exists; stable snapshot analysis missing | NOT VERIFIED / NOT ELIGIBLE / NONE |
| R02 | 1 | Causal evidence archive | weather_only_calibration_capture.py; weather_only_same_day_capture_store_compressed.py | OPEN: Narrow capture exists; shared V11 archive missing | NOT VERIFIED / NOT ELIGIBLE / NONE |
| R03 | 1 | Decision explanations and lane funnels | weather_only_operator_commands_v10.py; weather_only_paper_store.py | OPEN: Signal evidence exists; pre-signal V11 funnels missing | NOT VERIFIED / NOT ELIGIBLE / NONE |
| R04 | 1 | Deterministic causal replay | weather_only_predictions.py; tests/test_weather_maker_trade_stream_causality.py | OPEN: Existing targeted chronology checks; V11 replay integration missing | NOT VERIFIED / NOT ELIGIBLE / NONE |
| R05 | 1 | Counterfactual and executable markout measurement | shadow_telemetry.py; weather_only_trade_feed_public_v2.py | OPEN: Public-trade evidence must never establish maker fills | NOT VERIFIED / NOT ELIGIBLE / NONE |
| R06 | 2 | Partial-success collector resilience | weather_only_clob.py; weather_only_discovery.py | OPEN: Bounded retries exist; source-committed V11 collection missing | NOT VERIFIED / NOT ELIGIBLE / NONE |
| R07 | 2 | Station registry and capability certification | weather_only_station_metadata.py; weather_only_wrh_station_metadata.py | OPEN: Metadata checks exist; durable V11 lifecycle missing | NOT VERIFIED / NOT ELIGIBLE / NONE |
| R08 | 2 | Universal rule fingerprints and quarantine | weather_only_rules.py; weather_only_contract_strict.py | OPEN: Strict parsing and rule evidence exist; universal propagation missing | NOT VERIFIED / NOT ELIGIBLE / NONE |
| R09 | 2 | Official observations and forecast source health | weather_only_sources.py; weather_only_wrh_client.py | OPEN: Official proxy/settlement separation exists; V11 source health pending | NOT VERIFIED / NOT ELIGIBLE / NONE |
| R10 | 2 | Free PWS ingestion, QC and latency coverage | weather_only_live_paper_three_layer_validation.py | OPEN: PWS hook is not verified MADIS ingestion; live coverage unknown | NOT VERIFIED / NOT ELIGIBLE / NONE |
| R11 | 3 | Coherent multi-model distribution and bounds | weather_only_forecast.py; weather_only_conditioned_extremes.py | OPEN: GEFS and conditioning reusable; V11 multi-model validation pending | NOT VERIFIED / NOT ELIGIBLE / NONE |
| R12 | 3 | Calibration and conservative fallback | weather_only_calibration.py; weather_only_calibration_dataset.py | OPEN: Exact-label guards reusable; V11 evidence selection blocked on baseline | NOT VERIFIED / NOT ELIGIBLE / NONE |
| R13 | 3 | Causal physical nowcasting features | weather_only_three_layer.py | OPEN: Existing observed/remaining path separation; extra feature value unproven | NOT VERIFIED / NOT ELIGIBLE / NONE |
| R14 | 3A | Dataset provenance and leakage controls | weather_only_predictions.py; weather_only_calibration_dataset.py | OPEN: V11 immutable datasets and training cutoff checks missing | NOT VERIFIED / NOT ELIGIBLE / NONE |
| R15 | 3A | Reproducible offline learner and evaluation | weather_calibration_experiment.py | OPEN: V11 challenger run/evaluation integration missing | NOT VERIFIED / NOT ELIGIBLE / NONE |
| R16 | 3A | Data-only champion/challenger bundle registry | weather_only_calibration_authority.py | OPEN: V11 compatible artifact bundle registry missing | NOT VERIFIED / NOT ELIGIBLE / NONE |
| R17 | 3A | Reviewed atomic promotion, overlay and rollback | production/control.py | OPEN: Model-specific governance missing; financial promotion approval required | NOT VERIFIED / NOT ELIGIBLE / NONE |
| R18 | 4 | Event risk state engine | weather_only_runtime.py | OPEN: V11 NORMAL/CAUTION/EVENT/RECOVERY integration missing | NOT VERIFIED / NOT ELIGIBLE / NONE |
| R19 | 4 | Conservative executable EV and target contracts | execution_depth.py; production/fees.py | OPEN: Depth/fee foundation reusable; target separation and EV decomposition missing | NOT VERIFIED / NOT ELIGIBLE / NONE |
| R20 | 5 | Namespaced city-day coordinator | production/engine.py; production/ledger.py | OPEN: Existing production checks; V11 strategy proposal coordinator missing | NOT VERIFIED / NOT ELIGIBLE / NONE |
| R21 | 5 | Atomic account reservations and ambiguity | production/ledger.py | OPEN: Durable intents/fills reusable; V11 concurrent cash and inventory integration pending | NOT VERIFIED / NOT ELIGIBLE / NONE |
| R22 | 5 | Event scenario-risk matrix | weather_only_inventory.py | OPEN: V11 all-outcome scenario view missing | NOT VERIFIED / NOT ELIGIBLE / NONE |
| R23 | 5 | Regional and source-dependence ceilings | production/config.py | OPEN: V11 versioned correlation map and scenarios missing | NOT VERIFIED / NOT ELIGIBLE / NONE |
| R24 | 5 | Dynamic sizing and opportunity ranking | production/config.py; production/signals.py | OPEN: Protected ceilings reusable; V11 sizing/ranking missing | NOT VERIFIED / NOT ELIGIBLE / NONE |
| R25 | 6 | Future forecast migration | weather_only_live_paper.py | OPEN: V10 baseline heuristic is not V11 policy | NOT VERIFIED / NOT ELIGIBLE / NONE |
| R26 | 6 | Same-day late lock migration | weather_only_same_day_envelope.py | OPEN: V11 calibration/economics integration missing | NOT VERIFIED / NOT ELIGIBLE / NONE |
| R27 | 6 | PWS observation lead sleeve | weather_only_three_layer.py | OPEN: Next observation, settlement and executable exit models remain distinct; sleeve missing | NOT VERIFIED / NOT ELIGIBLE / NONE |
| R28 | 6 | Source shock and release opportunities | weather_only_live_paper_all_signals_final.py | OPEN: V11 scheduled/revision/arrival engine missing | NOT VERIFIED / NOT ELIGIBLE / NONE |
| R29 | 6 | Cross-temperature relative value | weather_only_structural.py | OPEN: V11 relative value and correlated leg accounting missing | NOT VERIFIED / NOT ELIGIBLE / NONE |
| R30 | 6 | Structural basket migration | weather_only_structural.py; production/engine.py | OPEN: Existing structural guards reusable; V11 coordinator integration pending | NOT VERIFIED / NOT ELIGIBLE / NONE |
| R31 | 6 | Result-lag finality | weather_only_wrh_finality.py; weather_only_result_lag.py | OPEN: Strict gating reusable; research results never prove finality | NOT VERIFIED / NOT ELIGIBLE / NONE |
| R32 | 7 | Active exits and reductions | production/engine.py; production/ledger.py | OPEN: V11 hold-versus-executable-sale engine missing | NOT VERIFIED / NOT ELIGIBLE / NONE |
| R33 | 2/4 | Bounded event triggers and backpressure | backpressure.py; weather_only_incremental.py | OPEN: Existing bounds reusable; V11 source/event routing missing | NOT VERIFIED / NOT ELIGIBLE / NONE |
| R34 | 8 | Maker research and first-canary baseline | weather_only_maker_paper_accounting_v6.py | OPEN: Queue-evidence distinction reusable; V11 maker policy pending | NOT VERIFIED / NOT ELIGIBLE / NONE |
| R35 | 8 | Maker microstructure and markout features | weather_only_trade_feed_public_v2.py | OPEN: V11 empirical microstructure feature engine missing | NOT VERIFIED / NOT ELIGIBLE / NONE |
| R36 | 8 | Rewards and rebates outside trading alpha | production/fees.py | OPEN: V11 reward qualification and reconciliation missing | NOT VERIFIED / NOT ELIGIBLE / NONE |
| R37 | 9 | Independent cancel-only guardian | production/executor_control.py | OPEN: Separate guardian commissioning/invariants missing | NOT VERIFIED / NOT ELIGIBLE / NONE |
| R38 | 9 | Clock health and safe recovery | production/wallet_attestation.py | OPEN: Host sync observed; financial clock gate integration missing | NOT VERIFIED / NOT ELIGIBLE / NONE |
| R39 | 5/9 | Durable operator safety reductions | production/control.py; production/telegram.py | OPEN: Existing controls reusable; full V11 safety-mode coverage pending | NOT VERIFIED / NOT ELIGIBLE / NONE |
| R40 | 1/6 | Performance lab and concentration | weather_only_operator_commands_v10.py | OPEN: V11 attribution, slices and concentration reporting missing | NOT VERIFIED / NOT ELIGIBLE / NONE |
| R41 | 9 | Durable daily/weekly audits | production/notifications.py | OPEN: V11 scheduled intelligence integration missing | NOT VERIFIED / NOT ELIGIBLE / NONE |
| R42 | 3A/6 | Drift and station/strategy lifecycle | weather_only_calibration_readiness.py | OPEN: V11 safe demotion overlay and recovery policy missing | NOT VERIFIED / NOT ELIGIBLE / NONE |
| R43 | 9 | Supported auth adapters and entitlement | production/exchange.py; production/owner_account.py | OPEN: Session first; account-specific entitlement and EOA allowlist unverified | NOT VERIFIED / NOT ELIGIBLE / NONE |
| R44 | 9 | Isolated host/deployment and recovery | host_trust/; deploy/production-host-control.sh | OPEN: Independent host authority retained; no V11 service deployed | NOT VERIFIED / NOT ELIGIBLE / NONE |
| R45 | all | Integrated regression and security acceptance | tests/; requirements-dev.txt | OPEN: Baseline suite running off-host; V11 full gates pending | NOT VERIFIED / NOT ELIGIBLE / NONE |
| R46 | 9 | V11 paper acceptance and V10 comparison | weather_only_all_paper_deployment_acceptance.py | OPEN: V10 protected snapshot and isolated V11 runtime required | NOT VERIFIED / NOT ELIGIBLE / NONE |
| R47 | 3A/9 | Controlled-learning acceptance | docs/V11_ACCEPTANCE.md (pending) | OPEN: No initial champion or accepted learning plane | NOT VERIFIED / NOT ELIGIBLE / NONE |
| R48 | 9 | Unfunded execution acceptance | production/wallet_attestation.py | OPEN: No credential read, account mutation or order authorized by test status | NOT VERIFIED / NOT ELIGIBLE / NONE |
| R49 | 9 | Verified release and funding handoff | docs/V11_WORK_CHECKPOINT.md | OPEN: All readiness gates pending; executor mask retained | NOT VERIFIED / NOT ELIGIBLE / NONE |
