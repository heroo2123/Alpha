# R09 Gate 3 protocol (G3-P) independent review — PASS

Reviewed commit: `117830a9b418cdf2a1b1f5146e074343623b8e3f`
Reviewed tree: `07c4d72fcafb4897896e27dfbcc415e7ab078ee9`
Reviewed document: `docs/V11_R09_GATE3_COLLECTION_PROTOCOL.md`
Reviewer: Sonnet/high (independent), 2026-09-30, isolated worktree
`/tmp/alpha-v11-r09-gate3-protocol-review-117830a/Alpha`, read-only.

**Verdict: PASS, scoped strictly to G3-P (protocol review).** This PASS
authorizes exactly what the document's own gate table says G3-P authorizes:
*offline, synthetic collector implementation only*. It grants no launch, no
network acquisition, no evidence admission, no Gate 4/5 action, and no
funding decision. No P1 or P2 blocker remains in this scope.

## Scope and base-state verification

- Repo HEAD/tree matched the assigned identities; `git status` clean;
  working directory not on a branch (detached review worktree), as expected.
- Protocol's stated base `fd77930` is the direct parent of `117830a`
  (`git log --oneline` confirms linear history: `117830a` → `fd77930` →
  `43a607b` → `c40eacd` → `73721b7`). `docs/V11_WORK_CHECKPOINT.md`'s latest
  entries (publication hold, SHADOW reconciliation) contain no newer R09
  gate state than what this protocol document already encodes — no stale
  gate assumption found.
- Private FINAL-REVIEWED master: recomputed SHA-256 of
  `Alpha_V11_Master_Prompt_Controlled_Continual_Learning_PWS_Observation_Lead_V10_Forensic_Baseline_Final_Reviewed(1).txt`
  in place = `a0e16d9bd7344c943a54a16a53c6757662363d93642f6e5cb7953cd047659b4a`,
  matching both the protocol document's pin and the repo's independent
  `config/v11/r09_multimodel_input_pins.json:master_sha256` pin. Read in
  full for relevant sections (causal clocks, promotion/financial-authority
  separation, collector resilience upgrade F2, GEFS/ECMWF member counts);
  no text reproduced here or in any artifact.
- Recomputed and matched the three evidence-document hashes cited in
  `V11_R09_DATA_CONTRACT_ADJUDICATION.md` against the actual files:
  historical panel, native-extrema doc, and
  `config/v11/r09_ecmwf_extrema_public_evidence.json` all match exactly.
  `r09_multimodel_input_pins.json` independently contains 7 `inputs`
  entries, matching the adjudication's "seven original pins" claim.
- Read the accepted adjudication (`V11_R09_DATA_CONTRACT_ADJUDICATION.md`)
  and the accepted Gate 2 review (`V11_R09_GATE2_REVIEW_1ab551d.md`, PASS,
  45/45 probes). The Gate 3 protocol's stated boundaries (contract identity
  `R09_NATIVE_2T_TRAJECTORY_V1`, synthetic-only Gate 2 admission, clock
  taxonomy, no-retrospective-label rule) are consistent with both documents;
  no contradiction or silent relaxation found.

## Checks executed (all offline, read-only, no network)

1. `sha256sum` on the private master and the three evidence artifacts
   (above) — matched all pinned hashes.
2. Read `tools/v11_trajectory_contract.py` (Gate 2 validator) and confirmed:
   - `CONTRACT_ID = 'R09_NATIVE_2T_TRAJECTORY_V1'` exists as claimed.
   - `evidence_class == 'SYNTHETIC'` is a hard `require()` in both
     `ExampleInputs` construction and corpus validation
     (`REAL_ADAPTER_NOT_IMPLEMENTED` otherwise) — the protocol's claim that
     Gate 2 "hardcode[s] zero real admissions" is accurate, not aspirational.
   - `run_candidate_slots(policy, decision_lower)`, `max_run_age_seconds`,
     `allowed_cycles` (constrained to `{0,6,12,18}`) and
     `run_selection == 'LATEST_COMPLETE_READY'` all exist exactly as the
     protocol's manifest section references them.
3. Read `polymarket_scanner/v11/ecmwf_sources.py`
   (`ECMWFRequest.__post_init__`): confirmed `step % 6 == 0` is enforced in
   code today. The protocol's own admission in §7 ("the current
   `ECMWFRequest` intentionally limits IFS to six-hour requests: a reviewed
   native three-hour request path is needed") is verified true, not
   understated — the pilot's proposed IFS cadence (`0,3,...,72`) is
   genuinely incompatible with the current code, and the document correctly
   defers that fix to G3-I rather than claiming it already works.
4. Read `polymarket_scanner/v11/gefs_sources.py` and `model_panel.py`:
   confirmed `maximum_grid_distance_km` default is exactly `50.` — the
   protocol's "reject ... grid displacement over 50 km" reasserts the
   existing bound rather than loosening it.
5. Confirmed existing field-size bounds are stricter than the protocol's
   stated ceilings, so the protocol's "this document cannot loosen it"
   claim holds: ECMWF `MAX_INDEX_BYTES = 3 MiB` (protocol: 3 MiB/index,
   exact match) and `MAX_RAW_BYTES = 4 MiB` (protocol ceiling: 16 MiB/field,
   i.e. strictly tighter existing bound applies); GEFS `grib_fields.MAX_BYTES
   = 64 KiB` (far tighter than 16 MiB).
6. Independent arithmetic check (system `python3`, no repo import, no
   network) of the claimed "2,713 distinct messages" denominator:
   `31*25 + 51*25 + 51*13 = 775 + 1275 + 663 = 2713`, with GEFS/IFS hour
   sets `0,3,...,72` (25 values) and AIFS `0,6,...,72` (13 values)
   independently enumerated and counted. Matches exactly.
   (Note: the task-specified venv
   `/home/alphaadmin/AlphaV11_Dev/Alpha/.venv/bin/python` does not exist on
   this host; system `/usr/bin/python3` was used instead for this one
   pure-arithmetic check — no repo code was imported or executed.)
7. Checked the 3,600-request / 2-second-minimum-interval / 3-hour-window
   ceilings for internal consistency: 3 h = 10,800 s; at minimum 2 s
   between request starts, at most 5,400 request-starts are physically
   possible in the window, so the 3,600 cap is satisfiable inside the time
   budget (not a self-contradictory pair of limits).

## Findings

No P1 or P2 finding. Three non-blocking P3 observations for G3-I/G3-L to
address when the concrete collector and manifest are built — none requires
a change to this document to pass G3-P.

**P3-1 (process clarity): Zero-retry policy vs. the master's general
collector-resilience upgrade is undocumented as a deliberate exception.**
The private master's "REQUIRED UPGRADE F2 — COLLECTOR RESILIENCE" section
requires "bounded retries with backoff/jitter appropriate to the provider"
for V11 collectors generally. §4 of this protocol instead mandates *no*
automatic retry for this pilot ("No automatic retry in this pilot... no
mirror, DNS/origin rotation or alternative client to evade it"). This is
strictly *more* conservative than F2, not a relaxation, and is consistent
with the pilot's one-window, non-production, non-financial framing — so it
is not a defect. Failure scenario if unaddressed: a future reviewer or
implementer could mistake the deviation from F2 for an oversight and either
(a) add retries that the protocol forbids, or (b) flag a false conflict
with the master. Suggested correction: add one sentence in §4 noting this
zero-retry rule is a deliberate stricter exception scoped to this single
capture-feasibility pilot, not a change to the general F2 collector policy.

**P3-2 (forward-looking, G3-I scope): proposed GEFS origin differs from the
existing reviewed GEFS collector's transport pattern, unflagged.**
§4's candidate GEFS origin is the raw product directory
`https://nomads.ncep.noaa.gov/pub/data/nccf/com/gens/prod/`. The existing,
already-reviewed production GEFS collector
(`polymarket_scanner/v11/gefs_sources.py`) instead uses NOMADS's
*filter CGI* endpoint (`cgi-bin/filter_gefs_atmos_0p50a.pl`), which performs
server-side subsetting/transformation of the GRIB2 output — a different
resource that would not satisfy §5's "preserve raw ... bytes" / "no
interpolation" requirements if reused for this pilot. The protocol is
correct to avoid the CGI endpoint, and §7 already forbids importing the
production service, but unlike the IFS 6-hour-step incompatibility (which
is explicitly named in §7), this origin-pattern change is not explicitly
called out as a new, unverified access path. Failure scenario if
unaddressed: G3-I could discover late that the raw directory does not
publish `.idx` sidecars compatible with §4's byte-range/206/strong-ETag
requirements, after other manifest work is already done. Suggested
correction: have the §3 "dry-run plan" explicitly confirm the raw NOMADS
directory's index-file availability and byte-range semantics before G3-L,
alongside the already-required IFS three-hour-path resolution.

**P3-3 (feasibility risk, already procedurally covered): the 1 GiB
total-received ceiling may not accommodate the full 2,713-message nominal
denominator.** Global 0.25°/0.50° ensemble 2 m-temperature GRIB2 messages
commonly run from roughly a few hundred KB to low single-digit MB each
depending on packing; 2,713 such messages could plausibly total well over
1 GiB. The document already anticipates and handles this correctly — §3
requires a pre-launch dry-run budget estimate and explicitly states "If the
frozen ceilings cannot accommodate acquisition, refuse launch or record a
prespecified bounded feasibility attempt; do not increase budgets mid-window
or reduce the denominator. Feasibility failure earns no admission." This is
not a defect in the document; it is flagged here only so G3-I's dry run
treats this as a likely-to-trigger scenario (not an edge case) and sizes
the "prespecified bounded feasibility attempt" fallback using real observed
message sizes rather than the nominal full count.

## Targeted structural checks against the review brief

- **Source identity (run vs. operational release vs. publication
  attestation):** §2 explicitly separates `source_release` (a run
  identifier), `operational_release_id`/`source_dossier_sha256` (vendor
  release), and `source_published_at` (optional attested publication),
  and explicitly forbids overloading `release_binding='ATTESTED'` with a
  merely-reviewed operational identity. Counterexample tried: "same header
  with false vendor release" and "genuine bytes plus forged manifest" are
  both named in §7's required G3-I/L/E probe list, not just asserted safe.
- **Finite native member/hour inventory and future API compatibility:**
  member bounds (GEFS 0–30, IFS/AIFS 0–50) match the existing
  `ECMWFRequest` code's enforced `0 <= member <= 50` bound exactly; the
  IFS-cadence incompatibility is verified real (item 3 above) and honestly
  disclosed rather than silently assumed away.
- **Bounded anonymous acquisition/provider controls and crash accounting:**
  §4 fully enumerates origin allowlisting, credential/redirect prohibition,
  fail-closed DNS/TLS, request/byte/time ceilings, and a persistent
  restriction ledger; the crash-resume paragraph requires hash
  verification before any resumption and forbids counter reset, window
  extension, or re-labeling a new download under an old receipt — this
  directly answers the "throttle then mirror switch" and "restart with
  changed clock/budget/cooldown" counterexamples §7 itself demands be
  tested.
- **Truthful UTC/clock evidence:** §5's causal-eligibility rule (every
  feature dependency's receipt/availability/feature-ready upper bound
  `<=` the decision lower bound) and 1-second maximum uncertainty match the
  adjudication's clock taxonomy (`feature_ready_at` includes "complete
  decode/checks and durable persistence, not merely last socket read").
  No clock field is redefined more permissively than the adjudication.
- **Prospective metadata / frozen cohort selection:** §3.2 requires
  station/date selection "determined before weather/label inspection" with
  rationale recorded, and explicitly forbids "pick[ing] an observed
  successful capture" — a genuine cherry-picking guard, not just a general
  preregistration statement.
- **Full refusal reporting:** §6's terminal-reason enumeration plus "must
  partition the original requested denominator exactly: no absent rows, no
  duplicate counting" is a concrete, checkable completeness requirement,
  not a vague aspiration.
- **G3-P/I/L/E vs. G4/G5 boundaries:** the gate table (§1) and its
  following paragraph are unambiguous and non-circular: G3-E can produce
  genuinely new raw-evidence admission (individually eligible feature
  captures) entirely independent of Gate 2's permanently-synthetic
  validator, while explicitly deferring the feature-capture-to-example
  bridge, real labels, and split clocks to a distinct future Gate 4 review.
  This is real forward progress without any gate approving itself or
  reusing another gate's PASS as its own evidence.
- **No relaxation of accepted source/safety gates:** every numeric bound
  checked against code (grid distance, field-size caps, member bounds)
  matched or was stricter than the existing accepted values; no bound in
  this document is looser than precedent.
- **Launch prerequisite vs. missing requirement:** the document repeatedly
  and explicitly states it "grants no collection, model, host or financial
  admission," that the provider table is "not approved pins," and that "a
  template with nulls is NOT launchable" — it does not claim a concrete
  launch manifest or source pin exists yet, consistent with the review
  brief's framing.

## Gate scope of this PASS

This review covers **G3-P only**: independent review of the exact committed
protocol document. It does not evaluate, and does not authorize, any G3-I
collector implementation, G3-L manifest/launch, G3-E corpus replay, Gate 4
adapter/fit, or Gate 5 forward evidence. No acquisition, adapter, fit,
production/V10/service action, credential, root authority, financial
action, push, or publish occurred during this review. No code was changed.

R09_GATE3_PROTOCOL_REVIEW_PASS
