# PAPER V11 READY requirements 8 and 9 — evidence-sufficiency readiness probes — 2026-10-07

**Candidate for independent review. Does not change the strict PAPER V11
READY score. Requirements 8 and 9 remain PARTIAL.** Built in isolated worktree
`AlphaV11_Reviews/paper-req8-req9-unblock-20261007` at repo HEAD
`d1c5602aa77e0d835e416d281b4a78754a3a79df`, branch
`paper-req8-req9-unblock-20261007`. SAFE NONFINANCIAL analysis and code only:
no network, provider, credential, order, root, or service action was taken or
is required to build or test this candidate. Gate-3 and Brain code and tests
were read for context only and are untouched.

## What problem this closes

`docs/V11_SHADOW_READINESS_20261004.md` and
`docs/V11_FORWARD_EVIDENCE_HARVEST_20261007.md` both independently confirm
requirements 8 and 9 stay PARTIAL and that **no amount of additional calendar
waiting under the current live configuration can close them** (harvest
sec. 7.3). This candidate asked the two questions the task required:

1. Can a genuine requirement-8 proposal/reservation be produced from
   *retained* evidence right now, legitimately (without weakening any
   freshness/settlement/execution-health gate)? **No** — see "Why requirement
   8 cannot reach PASS from retained evidence" below.
2. Is the requirement-9 PWS sleeve missing *code*, or missing a *live
   collection-plan setting* outside this repository? **The latter** — every
   piece of the PWS observe/QC/lead/precconfirmation/netting path already
   exists and is independently reviewed/tested; only the live deployment's
   `CensusPlan.pws` was never set for KATL (confirmed: `CensusPlan(` and
   `PWSQualityPlan(` are constructed nowhere in this repository outside
   `tests/`, i.e. the real plan assembly lives entirely on the deployment
   host, out of scope for this worktree).

Per the task's own instruction ("if current evidence cannot legitimately
produce it, implement the missing reviewed plumbing so the next legitimate
observation can, and prove fail-closed behavior"), the actual gap in *this
repository* was not a missing execution path for either requirement — it was
the absence of any reviewed, independent reader that can look at an arbitrary
evidence store (a retained ledger today, a live one tomorrow) and say,
honestly and fail-closed, whether either requirement's strict evidence
actually exists there, instead of every reviewer re-deriving that by hand.
This candidate adds exactly that reader, for each requirement, and nothing
else.

## What was built

- `tools/v11_r08_scenario_reservation_readiness.py` /
  `evaluate_scenario_reservation_readiness(coordinator)` — classifies whether
  a real `PaperCoordinator`'s current persisted account state already
  contains a genuine, unresolved, accepted, positively-reserved paper intent.
  Reuses (never re-derives) `PaperCoordinator._head`/`_state`/`_risk` — the
  same path `.snapshot()` uses — and `scenario_risk.number`.
- `tools/v11_r09_pws_lead_readiness.py` /
  `evaluate_pws_lead_readiness(coordinator, ...)` — checks, against a real
  `PaperCoordinator`, three standing PWS-sleeve invariants by calling the
  real reviewed code (never re-deriving it): (1) the named
  `PWSPreconfirmation` pin still independently revalidates now; (2) that
  assessment is structurally incapable of granting PWS settlement authority
  (`valuation_type=='SETTLEMENT'`, `executable_exit_proceeds is None`,
  `strategy_eligibility=='SEPARATE_ECONOMICS_STILL_REQUIRED'`,
  `financial_authority is False`, distinct observation/payout bundles); (3) a
  PWS-attributed proposal lacking its pair is refused live, right now, by the
  real `PaperCoordinator._preconfirmation`. It then reuses requirement 8's
  probe to confirm a genuine, PWS-attributed reservation is actually
  persisted.
- `tests/test_v11_r08_scenario_reservation_readiness.py` (10 tests),
  `tests/test_v11_r09_pws_lead_readiness.py` (11 tests).

Both modules open no socket, import no HTTP/provider client, run no
subprocess, and write nothing; every argument is an explicit, typed,
caller-supplied reference. Both outcome sets are closed two-member enums
whose dataclasses make the positive outcome unconstructable except with a
fully clean result (mirrors `tools/v11_gate3_fresh_window_readiness.py`'s
`FreshWindowReadinessResult` pattern).

## Why requirement 8 cannot reach PASS from retained evidence

The full admission -> event-risk -> proposal -> scenario-reservation chain
(`strategy_admission.py`, `event_risk.py`, `request_assembly.py`,
`scenario_risk.py`, `paper_coordinator.py`) is already implemented and
reviewed. The harvest's own evidence (sec. 2.4) shows every one of the 93
retained live strategy attempts was gated *before* an economic proposal could
exist, by `EVENT_REVALIDATION_EXPIRED` / `ASSEMBLY_BOOK_IDENTITY_OR_FRESHNESS`
— genuine live-data staleness conditions enforced by
`request_assembly.RequestAssembler.book`/`event_risk.EventRiskEngine.revalidate`,
not a code defect. Weakening either check to manufacture a passing cycle is
explicitly forbidden by the task and would not be an honest requirement-8
demonstration. **This candidate does not and cannot change that fact.**

What this candidate proves instead, as the positive-path test
`test_genuine_admission_and_event_risk_backed_reservation_is_demonstrated`
demonstrates: when a `StrategyAdmission` and `EventRiskEngine` state genuinely
are fresh (built via the same `factory` rig `test_v11_strategy_pipeline.py`
already uses, with only the downstream EV *outcome* field synthetically
overridden — the identical, established, explicitly-labeled pattern already
used by `test_v11_paper_coordinator.py::proposal()` and
`test_v11_pws_admission.py::synthetic_proposal()` — never a monkeypatched
gate), `PaperCoordinator.coordinate()` really does persist a genuine,
zero-authority scenario reservation, and the new probe really does recognize
it. `test_real_gates_were_not_bypassed_to_reach_demonstrated` shows the
inverse: letting the same admission's freshness window lapse before
`coordinate()` is called still refuses the reservation and the probe still
reports `NO_GENUINE_SCENARIO_RESERVATION_RECORDED`. Today, against the actual
retained Oct 5-7 ledgers, the probe reports the latter — there is no genuine
reservation anywhere in them.

## Why requirement 9's gap is a live config, not a code, gap

Traced end to end in this review: `weather_sources.madis_request` /
`parse_madis_xml` / `normalize_weather_capture` (real MADIS CWOP capture +
normalization, producing `PWS_OBSERVATION` records), `census_worker.CensusPlan`
(optional `pws: PWSQualityPlan | None` field; when set, `_requests()` appends
a real `madis_request` to the census cycle), `pws_runtime.PWSQualityWorker` +
`pws_quality.archive_neighborhood` (QC), `pws_lead.PWSObservationLead` (paired
observation-only research, never settlement), and `pws_admission.PWSPreconfirmation`
(the revocable join described in `docs/V11_PWS_OBSERVATION_LEAD.md`). Every
one of these is already implemented, typed, and covered by its own test file.
`grep -rn "CensusPlan(\|PWSQualityPlan("` across the whole repository matches
only inside `tests/`: the actual live KATL `CensusPlan` is assembled entirely
by a deployment-host script that is not part of this git repository, and this
worktree has no business changing deployment-host configuration. That is the
entire requirement-9 gap: the live collection plan simply never turns PWS
collection on, which is why the harvest found zero `PWS_OBSERVATION` records
of any kind in any retained ledger.

The positive-path test
`test_genuine_pws_lead_observed_and_netted_as_lead_only_is_demonstrated`
reuses `test_v11_pws_admission.py`'s existing `joined` fixture (a real,
reviewed `PWSObservationLead.observe()` + `PWSPreconfirmation.pin()` +
paired `StrategyAdmission` pins, again with only the downstream EV outcome
synthetically overridden) to show the probe correctly recognizes a genuine
PWS-netted-as-lead-only reservation when the sleeve's evidence is present,
and the four negative tests (`test_no_pair_and_no_reservation_...`,
`test_missing_preconfirmation_pin_...`, `test_expired_sensor_policy_...`,
plus the dataclass invariant tests) show it correctly refuses to claim that
when any one piece -- the pair, its freshness, or the reservation itself --
is absent or stale.

## Are 8 and 9 independent?

Yes, cleanly. Requirement 8's positive test uses the plain, non-PWS `factory`
rig and needs nothing from requirement 9. Requirement 9's probe reuses
requirement 8's probe internally (to confirm a genuine reservation exists) but
requirement 8's own demonstration never requires PWS evidence. Both were
implemented in this one candidate; neither blocked the other.

## What this candidate does NOT do

- It does not flip requirement 8 or 9 to PASS, and does not touch
  `docs/V11_SHADOW_READINESS_20261004.md`'s or any other ledger's recorded
  score. **9/11 stands; 8 and 9 remain PARTIAL.**
- It does not construct, approve, or claim any real economic opportunity: the
  current deliberately conservative model still rejects on EV in every
  positive-path fixture here, exactly as documented upstream
  (`docs/V11_PWS_OBSERVATION_LEAD.md`: "No actual eligible strategy is
  claimed").
- It does not touch `census_worker.py`, any deployment script, or any
  Gate-3/Brain module. It adds two new files under `tools/` and two new test
  files under `tests/`; nothing else in the repository was modified.
- It does not grant execution, order, model, promotion, or settlement
  authority. `financial_authority` is `False` by construction in every result
  either probe can return.

## Verification run this session

```
python -m pytest tests/test_v11_r08_scenario_reservation_readiness.py tests/test_v11_r09_pws_lead_readiness.py \
    tests/test_v11_paper_coordinator.py tests/test_v11_pws_admission.py tests/test_v11_strategy_pipeline.py \
    tests/test_v11_scenario_risk.py tests/test_v11_shadow_commission.py -q   # 153 passed
python -O -m pytest tests/test_v11_r08_scenario_reservation_readiness.py tests/test_v11_r09_pws_lead_readiness.py -q   # 21 passed
python -m py_compile tools/v11_r08_scenario_reservation_readiness.py tools/v11_r09_pws_lead_readiness.py \
    tests/test_v11_r08_scenario_reservation_readiness.py tests/test_v11_r09_pws_lead_readiness.py   # clean
python -m pytest --collect-only -q tests/   # 7999 tests collected, no collection errors
git diff --cached --check   # clean
```

## Required before any further promotion

This is a candidate only: independent exact-commit review by a different
model, per the standing review-separation requirement already in force
across this repository's other candidates. Separately, and not requested or
scheduled by this document: the two actual blockers this review confirms
(live book/event-risk freshness for requirement 8; the live KATL collection
plan's missing `pws=` setting for requirement 9) remain for an owner with
deployment-host access to address; this worktree cannot and does not touch
either.
