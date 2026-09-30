# R09 Gate 3 (G3-I) collector independent review

Reviewed commit: `de8c7bcd0d70781eb498227c4f7c5b86c7a9eb6e`
Reviewed files (only new files in this commit, confirmed by `git show --stat HEAD`
and `git diff 40ad8f9 HEAD --stat`; nothing else touched):
- `tools/v11_r09_gate3_collector.py`
- `tests/test_v11_r09_gate3_collector.py`
- `docs/V11_R09_GATE3_COLLECTOR_G3I.md`

Parent commit confirmed: `40ad8f9` (the accepted G3-P PASS commit). Linear
history verified with `git log --oneline -5`.

Reviewer: Sonnet/independent, 2026-09-30, isolated read-only worktree
`/tmp/alpha-v11-r09-gate3-collector-review-de8c7bc/Alpha`.

**Verdict: CHANGES REQUIRED, scoped strictly to G3-I (collector review).**
This review covers only whether the concrete offline collector objects at
this exact commit correctly and safely implement what G3-P requires before a
future G3-L review. It grants no launch, no network acquisition, no G3-L
manifest approval, and no financial/production/host authority. One P2 defect
was found (a documented safety invariant that the code does not actually
enforce); it must be fixed and re-reviewed before this module's `BudgetTracker`
is trusted to bind a future G3-L manifest.

## Scope and base-state verification

- `git log --oneline -5` shows `de8c7bc` directly on top of `40ad8f9`, matching
  the assigned commit and its stated parent.
- `git show --stat HEAD` and `git diff 40ad8f9 HEAD --stat` both show exactly
  the three files listed above, 1354 insertions, 0 deletions, 0 other files.
- `git diff 40ad8f9 HEAD -- polymarket_scanner/` is empty (0 lines) — no
  production file touched.
- `git diff 40ad8f9 HEAD -- docs/V11_R09_GATE3_COLLECTION_PROTOCOL.md` and
  `...V11_R09_GATE3_PROTOCOL_REVIEW_117830a.md` are both empty — the accepted
  protocol and its PASS review are byte-identical to before this commit,
  confirming the handoff doc's claim that it deliberately did not edit the
  protocol text.

## Checks executed (commands actually run, not just read)

1. `python3 -m py_compile tools/v11_r09_gate3_collector.py
   tests/test_v11_r09_gate3_collector.py` — clean.
2. Confirmed a working pytest venv exists:
   `/home/alphaadmin/alpha-review-test-venv/bin/python3 -c "import pytest"` →
   pytest 8.3.3.
3. `/home/alphaadmin/alpha-review-test-venv/bin/python3 -m pytest
   tests/test_v11_r09_gate3_collector.py -q` → **45 passed** (also confirmed
   `grep -c "^def test_" tests/test_v11_r09_gate3_collector.py` = 45,
   matching the claimed count).
4. `/home/alphaadmin/alpha-review-test-venv/bin/python3 -m pytest
   tests/test_v11_trajectory_contract.py tests/test_v11_gefs_sources.py
   tests/test_v11_model_panel.py -q` → **263 passed, 2 skipped, 0 failed**,
   matching the handoff's claimed regression result exactly.
5. Independently recomputed the denominator by hand and by exercising the
   code: `31*25 + 51*25 + 51*13 = 2713` (python3 arithmetic) and
   `CaptureManifest.nominal_denominator == 2713` on a synthetic manifest
   built from the test file's own `make_manifest()` helper
   (`test_manifest_nominal_denominator_matches_reviewed_arithmetic` also
   independently proves this in the suite). Confirmed `PROVIDERS =
   {'GEFS': 31, 'IFS': 51, 'AIFS': 51}` in `tools/v11_multimodel_panel.py`
   matches the protocol's member tables (GEFS 0-30, IFS/AIFS 0-50).
6. Confirmed `g3i.TERMINAL_REASONS` is set-equal (`==`) to the 14 reasons
   enumerated in protocol Section 6, computed programmatically — exact
   match, no fewer, no extras.
7. Confirmed existing bounds referenced by the module:
   `polymarket_scanner/v11/ecmwf_sources.py: MAX_INDEX_BYTES = 3 * 1024 *
   1024`; `polymarket_scanner/v11/model_panel.py: MAX_RAW_BYTES = 4 * 1024 *
   1024` (imported into `ecmwf_sources.py` as `MAX_FIELD_BYTES`);
   `polymarket_scanner/v11/grib_fields.py: MAX_BYTES = 64 * 1024`. Confirmed
   `polymarket_scanner/v11/ecmwf_sources.py`'s `ECMWFRequest.__post_init__`
   still enforces `step % 6` (line 45-46, `ECMWF_STEP_BOUND`), unmodified.
8. Ran the adversarial counterexamples in item 4 below directly against the
   library (not just reading the tests).
9. `grep -rn "NativeECMWFThreeHourRequest\|v11_r09_gate3_collector"
   polymarket_scanner/` → empty (zero production imports).
10. `grep` for `import httpx|import requests|import socket|import urllib` in
    the new module → none found.
11. `grep` for `host_trust|financial|v10|V10|axiom|Axiom` in the new module →
    only the `financial_authority` dataclass field/guards and one docstring
    sentence disclaiming financial/host authority; no import of any such
    module.

## Adversarial counterexamples tried (executed, not just inspected)

All of the following were run directly against the library in this worktree:

- **`CaptureManifest` with an authority flag `True`**: rejected at
  *construction* (`MANIFEST_AUTHORITY_FLAGS_MUST_STAY_FALSE`), including via
  `dataclasses.replace(base, financial_authority=True)` — `__post_init__`
  re-runs on `replace()` for a frozen dataclass, so this bypass path is also
  closed. This is stronger than gating only `.launchable`.
- **Manifest/dossier with `index_sidecar_available`/`supports_byte_range_206`/
  `raw_directory_index_verified`/`ifs_three_hour_path_verified` False**: the
  manifest still constructs (these are legitimate false states pending
  verification) but `.launchable` is correctly `False`, and
  `require_launch_prerequisites` raises `MANIFEST_NOT_LAUNCHABLE`. Confirmed
  both for "all dossiers unverified" and "exactly one dossier (IFS)
  unverified while the others are verified" — the `all(...)` check in
  `launchable` is not short-circuited into an `any()` bug.
- **`AttemptLedger` disguised retry**: recording the same key twice (even as
  a distinct tuple object with identical value, and even with a different
  reason the second time) is rejected with
  `LEDGER_KEY_ALREADY_TERMINAL_NO_RETRY`. `finalize()` with a missing key
  raises `LEDGER_INCOMPLETE_PARTITION`; a key outside the manifest is
  rejected at `record()` time (`LEDGER_KEY_NOT_IN_MANIFEST`), so double
  counting via an out-of-cohort key is impossible.
- **`RestrictionLedger.resume()` tampering**: reordering the two records,
  dropping one, and duplicating one were all tried against a captured
  `content_sha256()` and all three were rejected
  (`RESTRICTION_LEDGER_HASH_MISMATCH_ON_RESUME`). `canonical()` (in
  `tools/v11_multimodel_panel.py`) uses `json.dumps(..., sort_keys=True)`
  over a Python *list* of record payloads — `sort_keys` only sorts dict keys,
  it does not reorder list elements — so record order is genuinely part of
  the hash preimage, not silently normalized away.
- **`BudgetTracker` ceiling bypass attempts**: single-in-flight violation,
  request-count ceiling, minimum-interval enforcement (exactly-2.0s boundary
  allowed, 1.0s rejected), elapsed-window ceiling, and total-received-bytes
  ceiling were all independently re-triggered outside the test file and
  behaved as claimed. **However, see Finding P2-1 below**: the *construction*
  ceiling on `max_field_bytes` does not actually bind to the stricter
  existing bound.
- **`estimate_feasibility` determinism and priority**: called twice with
  identical inputs → identical `fallback_keys` tuple (object equality, not
  just set equality). Inspected the actual fallback order for the GEFS
  provider under a tight-ceiling scenario: `[('GEFS',0,0), ('GEFS',0,3),
  ('GEFS',0,6), ('GEFS',0,9), ('GEFS',0,12), ...]` — control member (0) first,
  ascending hours, exactly matching the documented "control member, lowest
  hours first" rule and the `_fallback_priority` key
  `(provider, member != 0, member, hour)`. No I/O, no response-content
  inspection anywhere in the function body (confirmed by reading the full
  function; it only touches its own arguments).
- **`Transport`/`check_index_availability`**: no real transport is defined;
  `Transport.head_or_range_probe` raises `NotImplementedError` by default
  (confirmed by direct call); `check_index_availability` requires an
  `isinstance(transport, Transport)` and calls only the caller-supplied
  object's method — no socket/HTTP call anywhere in this module.
- **`causal_feature_eligible`**: uses `require_trusted` and
  `Timestamp.conservative_upper_bound` / `conservative_lower_bound` imported
  directly from `tools/v11_trajectory_contract.py` (not redefined), and its
  comparison (`feature_ready_at.conservative_upper_bound <=
  decision_at.conservative_lower_bound`) is the identical pattern used
  throughout the already-reviewed Gate 2 contract module (e.g. lines 796,
  1191-1193 of that file) — clock-trust semantics are reused, not weakened.

## Findings

**P2-1 (correctness / false safety claim): `BudgetTracker`'s `max_field_bytes`
constructor ceiling does not actually bind to the stricter existing provider
bound, contrary to the module's own docstring and the handoff document's
explicit claim.**

The module docstring (`tools/v11_r09_gate3_collector.py:497-503`) and the
handoff doc (`docs/V11_R09_GATE3_COLLECTOR_G3I.md`, "BudgetTracker" bullet)
both assert: *"constructed so its own index/field byte ceilings can never be
set looser than the tightest already-reviewed provider bound (ECMWF
`MAX_INDEX_BYTES`=3 MiB / `MAX_RAW_BYTES`=4 MiB; GEFS `grib_fields.MAX_BYTES`=
64 KiB)."*

This claim is true for `max_index_bytes` (the constructor `require()` caps it
at exactly 3 MiB — the real, confirmed `ecmwf_sources.MAX_INDEX_BYTES`), but
**false** for `max_field_bytes`: the constructor only enforces
`0 < max_field_bytes <= 16 * 1024 * 1024` (the *protocol's own* nominal
ceiling, per `docs/V11_R09_GATE3_COLLECTION_PROTOCOL.md` Section 4's
"16 MiB/field"), not the actual, already-reviewed, stricter
`model_panel.MAX_RAW_BYTES = 4 * 1024 * 1024` bound the docstring itself
names two lines earlier. Verified directly (not just read):

```
>>> from tools import v11_r09_gate3_collector as g3i
>>> t = g3i.BudgetTracker(max_field_bytes=10*1024*1024)
>>> t.max_field_bytes
10485760
>>> t2 = g3i.BudgetTracker(max_field_bytes=16*1024*1024)
>>> t2.max_field_bytes
16777216
```

Both constructions succeed and are 2.5x-4x looser than the real existing
4 MiB `MAX_RAW_BYTES` bound (and 160-256x looser than GEFS's real 64 KiB
`grib_fields.MAX_BYTES`, which the same docstring also names as a bound this
module "can never loosen"). The existing test
`test_budget_cannot_loosen_protocol_index_or_field_ceilings` only checks that
`17 * 1024 * 1024` is rejected — i.e. it tests against the protocol's own
16 MiB ceiling, not against the actually-stricter 4 MiB/64 KiB bounds the
docstring and handoff both specifically claim are enforced. The test suite
therefore does not catch this gap either.

This directly matters because protocol Section 4 states, as a hard
requirement: *"Apply a stricter existing parser/field bound where present;
this document cannot loosen it."* The `max_index_bytes` path honors this
correctly; the `max_field_bytes` path does not. A future G3-L implementer who
trusts this module's docstring (exactly the kind of unverified-claim trust
this review was instructed not to extend) could construct
`BudgetTracker(max_field_bytes=16*1024*1024)` believing the class itself
would refuse anything looser than the real 4 MiB bound, and ship a manifest
whose per-field byte ceiling is 4x looser than the already-reviewed provider
bound this exact protocol forbids loosening.

**Required repair**: change the constructor's field-bytes validation (and,
for symmetry/defense-in-depth, ideally import the real constants from
`polymarket_scanner/v11/ecmwf_sources.py` / `model_panel.py` /
`grib_fields.py` rather than re-hardcoding numbers) to
`require(0 < max_field_bytes <= 4 * 1024 * 1024, ...)`, and add a test that
specifically exercises the gap this review found — a value strictly between
4 MiB and 16 MiB must be rejected, not just values above 16 MiB. Re-run
`tests/test_v11_r09_gate3_collector.py` after the fix.

No other P1/P2 finding.

## P3 observations (non-blocking)

**P3-a**: The handoff's resolution of the prior review's P3-1 (leaving the
protocol document text unedited and only documenting the zero-retry rationale
in code/handoff) is a defensible reading of the protocol's own
self-invalidation clause ("any change to accepted protocol... invalidates the
corresponding approval... and requires fresh review"), and the substance the
prior review asked for (a clear statement that the deviation is deliberate,
not an oversight) is genuinely present at the implementation site
(`ZERO_RETRY_IS_DELIBERATE_PILOT_EXCEPTION = True`, module docstring, and
handoff section). This is an acceptable interim resolution for G3-I scope,
but the underlying suggestion (one clarifying sentence added to the protocol
document itself) remains open and should still be done at or before G3-L,
since it was the prior reviewer's literal suggested correction and costs
nothing to add via a fresh, separately reviewed protocol revision.

**P3-b**: `SourceDossier.dataset` and `operational_release_id` are free-text
strings with no enum/pattern restricting them (e.g. nothing here would by
itself catch "AIFS Single substituted for ENS" or a false vendor-release
string) — this is appropriate for G3-I's schema-only scope (protocol Section
7's named counterexamples for exactly this are assigned to G3-L/G3-E, where
real dossier values exist to check), but should be flagged as an explicit
G3-L review item rather than assumed covered by this commit.

**P3-c**: `BudgetTracker.complete_request`, on a total-bytes ceiling breach,
sets `self.in_flight = False` and raises without incrementing
`total_received_bytes` for that call. This correctly preserves the "total
never exceeds ceiling" invariant, but `request_count` (incremented in
`begin_request`) is not rolled back, meaning a request that ultimately breaches
the byte ceiling still consumes one unit of the 3,600-request budget. This
matches the protocol's "counting failed/partial attempts" language and is not
a defect, but is worth an explicit one-line confirmation in the G3-L manifest
review that this is the intended accounting.

## Targeted structural checks against the review brief

- No import of `host_trust`, any financial/execution/production module, or
  any V10/AxiomTrade reference anywhere in the new file.
- No default/real network transport; every acquisition-adjacent entrypoint
  (`check_index_availability`) requires a caller-supplied `Transport`
  instance and raises `NotImplementedError` on the base class.
- `NativeECMWFThreeHourRequest` is a genuinely separate code path (own
  dataclass, own cadence-by-provider logic) from
  `polymarket_scanner/v11/ecmwf_sources.py::ECMWFRequest`, which is
  confirmed byte-identical and still enforces its own six-hour-step rule.
- `CaptureManifest` cannot self-grant `financial_authority`,
  `promotion_authority`, or `host_approved` — these are hard-rejected at
  construction, not merely defaulted.
- Terminal-reason set is an exact match (not a superset or subset) of the
  protocol's Section 6 enumeration.

## Gate scope of this verdict

This review covers **G3-I only**: independent review and adversarial testing
of the concrete bounded collector objects at commit `de8c7bc`. It does not
authorize G3-L manifest preparation, G3-L launch, G3-E corpus replay, any
Gate 4/5 action, or any financial/production/host action. Per the protocol's
own gate table, even a full PASS here would permit only "preparation of a
concrete launch manifest; no automatic network start" — and this review is
not a full PASS: the P2-1 finding above must be repaired and this module
re-reviewed (at minimum, a focused re-check of the fixed `BudgetTracker`
constructor and its new test) before that preparation work should rely on
this module's stated budget-ceiling guarantee.

R09_GATE3_COLLECTOR_REVIEW_CHANGES_REQUIRED
