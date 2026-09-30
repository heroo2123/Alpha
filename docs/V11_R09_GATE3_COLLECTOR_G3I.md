# R09 Gate 3 (G3-I) bounded collector — implementation and handoff

Status: **AWAITING INDEPENDENT EXACT-COMMIT G3-I REVIEW**. Built against the
accepted [G3-P protocol](V11_R09_GATE3_COLLECTION_PROTOCOL.md) (reviewed
`117830a`, [PASS](V11_R09_GATE3_PROTOCOL_REVIEW_117830a.md)) in an isolated
worktree/branch (`r09-gate3-collector-20260930`). This document grants no
launch, collection, model, host, or financial admission by itself. Per the
protocol's own gate table, a G3-I PASS only "permits preparation of a
concrete launch manifest; no automatic network start." No forecast data was
acquired and no capture was launched while building or testing this work.
**91/200; formal 1/50; NOT_READY_TO_FUND**, unchanged.

## What this batch built

`tools/v11_r09_gate3_collector.py` (offline, no network access anywhere) plus
`tests/test_v11_r09_gate3_collector.py` (45 synthetic/offline cases, all
passing; targeted regression on `tests/test_v11_trajectory_contract.py`,
`tests/test_v11_gefs_sources.py`, `tests/test_v11_model_panel.py` — 263
passed / 2 skipped, no failures, no change to those files):

- `SourceDossier` — the versioned per-provider source identity protocol
  Section 2 requires (no `latest` alias, HTTPS-only/no-credential origin,
  explicit expected GRIB signature fields, explicit `index_sidecar_available`
  / `supports_byte_range_206` booleans that never default to permitted).
- `NativeECMWFThreeHourRequest` — a separate, G3-I-only IFS/AIFS request
  builder supporting the pilot's native 0,3,...,72 IFS cadence. This does
  **not** modify `polymarket_scanner/v11/ecmwf_sources.py::ECMWFRequest`,
  which still enforces `step % 6 == 0` for its own live callers, unchanged
  and regression-tested (`test_production_ecmwf_request_still_rejects_three_hour_step_unmodified`).
- `CaptureManifest` — the frozen Section 3 manifest schema: single-station/
  single-date/at-most-two-HIGH-LOW-event pilot bound, frozen
  `allowed_cycles=(0,)`/`max_run_age_seconds=86400`/
  `run_selection=LATEST_COMPLETE_READY`/`fallback_mode=NONE`, window-before-
  decision ordering, all three numeric ceilings, and
  `financial_authority=promotion_authority=host_approved=False` enforced at
  construction. `expected_raw_message_keys`/`nominal_denominator`
  independently reproduce the review's `31*25 + 51*25 + 51*13 = 2,713`
  arithmetic in executable form
  (`test_manifest_nominal_denominator_matches_reviewed_arithmetic`) and keep
  HIGH/LOW sharing raw captures rather than multiplying them; a separate
  `feature_eligibility_keys` property implements the "expand every requested
  key times all three providers" feature-accounting cohort without touching
  the raw denominator. `seal_manifest`/`manifest_bytes` bind `manifest_id` to
  every other field's canonical content.
- `AttemptLedger` — per-raw-message-key terminal-state recording over the
  full `TERMINAL_REASONS` enumeration from protocol Section 6, a frozen
  `TERMINAL_PRECEDENCE` for the "one stable primary reason" requirement,
  refusal of any second record for an already-terminal key (the ledger-level
  enforcement of the pilot's zero-retry rule), and `finalize()` failing
  closed unless every manifest key is covered exactly once.
- `RestrictionLedger` — persistent, append-only per-origin holds (401/403 =
  origin-scoped for the pilot; 429/503/denial/Retry-After = window-scoped),
  with `resume()` refusing to rehydrate unless the caller's persisted content
  hash matches, so a crash-restart can never silently reset a cooldown.
- `BudgetTracker` — request-count/elapsed-time/minimum-interval/total-bytes/
  single-in-flight enforcement, constructed so its own index/field byte
  ceilings can never be set looser than the tightest already-reviewed
  provider bound (ECMWF `MAX_INDEX_BYTES`=3 MiB / `MAX_RAW_BYTES`=4 MiB;
  GEFS `grib_fields.MAX_BYTES`=64 KiB).
- `estimate_feasibility` / `FeasibilityPlan` — the P3-3 dry-run estimator
  (below).
- `Transport` / `check_index_availability` — the P3-2 origin dry-run check
  (below). No default/real transport is implemented anywhere in this module.
- `require_launch_prerequisites` — the explicit stop-gate proving a
  manifest's own `.launchable` computation runs end-to-end; passing it is
  necessary, not sufficient, for G3-L.
- `causal_feature_eligible` — reuses `Timestamp`/`require_trusted` from the
  already-reviewed `tools/v11_trajectory_contract.py` rather than
  redefining clock trust, per protocol Section 7's "reuse proven parsers
  only where their semantics match."

## Addressing the G3-P review's three P3 notes

**P3-1 (zero-retry vs. master F2).** The review found this a non-blocking,
strictly-more-conservative deviation, not a defect, and suggested one
sentence be added to the *protocol document* clarifying it as deliberate.
This implementation does not edit `V11_R09_GATE3_COLLECTION_PROTOCOL.md`:
per that document's own closing rule, "any change to accepted protocol...
invalidates the corresponding approval... and requires fresh review," and
reopening the already-passed G3-P review was not the assigned task. Instead
the deliberate-exception rationale is recorded here in code and in this
handoff: `ZERO_RETRY_IS_DELIBERATE_PILOT_EXCEPTION = True` at the top of
`tools/v11_r09_gate3_collector.py`, with a module-docstring paragraph
explaining it, and the `AttemptLedger` itself has no retry path anywhere —
`test_ledger_refuses_retry_on_already_terminal_key` proves a second record
for the same key is refused, not overwritten. A future G3-L/G3-E reviewer
reading this file will see the exception documented at the implementation
site even though the protocol text itself is untouched.

**P3-2 (unverified raw-NOMADS-directory origin pattern).** The review noted
the protocol's candidate GEFS raw-directory origin is a different, unflagged
access path from the existing reviewed production collector's CGI filter
endpoint (`gefs_sources.py`'s `https://nomads.ncep.noaa.gov/cgi-bin/filter_gefs_atmos_0p50a.pl`,
confirmed unchanged by this batch), and asked that the dry-run plan
explicitly confirm the raw directory's index/byte-range semantics before
G3-L. This is now a first-class, non-defaultable gate: `SourceDossier`
carries explicit `index_sidecar_available` / `supports_byte_range_206`
booleans (never true unless a caller sets them), `CaptureManifest` carries
top-level `raw_directory_index_verified` / `ifs_three_hour_path_verified`
booleans, and `CaptureManifest.launchable` — checked by
`require_launch_prerequisites` — is `False` whenever any of these remain
unverified. `check_index_availability(transport, origin_url, now_utc=...)`
implements the actual confirmation logic against a caller-supplied
`Transport`; this repository defines no real transport and performs no
network probe itself, so every fixture in this batch's tests is
unverified-by-default and only a synthetic `FakeTransport` in the test file
exercises the check. G3-L must supply and independently review its own real
transport, run this check against the live origin, and only then may a
dossier/manifest report `True`.

**P3-3 (1 GiB ceiling vs. the full 2,713-message nominal count).** The
review flagged this as likely-to-trigger, already correctly anticipated by
the protocol's own dry-run/refusal language, and asked that G3-I's dry run
size the "prespecified bounded feasibility attempt" using real observed
message sizes rather than the nominal count. `estimate_feasibility` takes a
caller-supplied `provider_message_size_estimate_bytes` mapping (meant to be
populated from real observed GRIB2 message sizes at G3-L, not invented
here), computes the full-denominator byte estimate, and — only if that
exceeds the ceiling — computes a deterministic bounded fallback ordered by a
frozen, documented priority (control member and lowest forecast hours
first, per provider) rather than an ad hoc or post-hoc choice.
`test_feasibility_bounded_fallback_when_messages_exceed_ceiling` reproduces
the review's own "plausibly over 1 GiB" scenario with realistic per-message
sizes and proves the resulting fallback is both under-ceiling and
deterministic across repeated calls with the same inputs.

## What this does not do

No network access, no real transport implementation, no real GRIB parsing
of live bytes, no private station/event/date values, no G3-L manifest
freeze, and no independent review of this exact commit yet. The protocol's
own gate table is unchanged: this G3-I implementation, once independently
reviewed and PASSed at its exact commit, permits only "preparation of a
concrete launch manifest; no automatic network start." G3-L (freezing and
independently reviewing the actual private manifest digest, with a real,
separately reviewed transport) remains the next distinct gate, and G3-E
(independent replay of actual raw bytes) remains after that. No code in
`polymarket_scanner/v11/` was modified.

## Verification (this batch, foreground)

- `python3 -m py_compile tools/v11_r09_gate3_collector.py tests/test_v11_r09_gate3_collector.py` — clean.
- `tests/test_v11_r09_gate3_collector.py` — 45/45 passed.
- Targeted regression (shared infrastructure: `tools/v11_multimodel_panel.py`
  helpers, `tools/v11_trajectory_contract.py` `Timestamp`/`require_trusted`,
  and confirming `polymarket_scanner/v11/ecmwf_sources.py` is unmodified):
  `tests/test_v11_trajectory_contract.py`, `tests/test_v11_gefs_sources.py`,
  `tests/test_v11_model_panel.py` — 263 passed / 2 skipped, 0 failed.
- No full-suite regression run this batch (not justified: this is a new,
  self-contained, offline-only module with no changes to any existing
  production or test file; per the testing-budget policy, a full regression
  is reserved for a coherent completed batch/shared-infrastructure change,
  neither of which applies here beyond what the targeted set above already
  covers).

## Next action

Obtain an independent exact-commit review of this branch's HEAD (protocol
Section 7: "G3-I/G3-L/G3-E reviews must check concrete counterexamples, not
just hash presence"), covering at minimum: the native-cadence request path's
non-interference with production `ECMWFRequest`; the ledger's zero-retry/
exact-partition guarantees under adversarial reason combinations; the
budget tracker's inability to be constructed looser than existing provider
bounds; and the feasibility estimator's determinism and priority ordering.
Only after that PASS should a concrete, private G3-L manifest (real station/
event/date, real reviewed transport, real observed message sizes) be
prepared and separately reviewed. This branch remains unmerged into
`weather-v11-profitability-upgrade-2026-09-23` pending that review, matching
the precedent set by the Gate 2 trajectory-contract branch
(`r09-trajectory-contract-gate2-20260930`, merged only after its own PASS).
