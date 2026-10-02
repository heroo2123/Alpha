# G3-L retained-identity audit — hardening of the independent review's F1 — 2026-10-02

**Offline candidate for independent review, base `main` `40bc64e`. G3-L remains
NO-GO.** This repairs `tools/v11_r09_gate3_g3l_identity_audit.py` against
finding F1 of the independent exact-commit review
`docs/V11_R09_GATE3_G3L_IDENTITY_AUDIT_REVIEW_131eb12.md`, and makes the two
scoped F2/F3 wording and safety improvements that review authorized. It
creates no qualified inventory entry, private V4 manifest, capture, dispatch,
or approval, and changes no provider/credential/socket/root-authority path.
A second independent exact-commit review of this candidate's own commit
`c9e3b8d` (`/tmp/alpha-v11-g3l-hardening-review-c9e3b8d.review.md`, OpenAI
GPT-6 Astra/high) returned `CHANGES_REQUIRED` for two generic hardening gaps
in that F1 fix, R1 and R2; both are repaired below (see "F1 hardening repair
— R1/R2"). Its R3 nonblocking documentation finding is also corrected in
this revision.

## F1 — generic code-dependency recheck (repaired)

The prior tool never re-checked the reconciliation's `code_byte_observations`;
it downgraded only `code.slice3_exact_commit_review`, hard-coded by identity
ID, when `tools/v11_r09_gate3_runtime.py` drifted. The review's own probe
confirmed the resulting gap: a drift in `tools/v11_r09_gate3_launch_v4.py`
would leave `code.mapping_exact_commit_review` falsely `RETAINED`, because
that row's own `"artifacts"` list cites only its review report and terminal,
never the validator source file it is actually about.

The fix removes the `SLICE3_ID` special case entirely and adds
`_code_observation_refs()`: for every PRE_REVIEW identity, each of its own
cited JSON artifacts (review terminals and reconciliation records) is read
for a recorded reviewed-commit field (tried in order: `head`,
`candidate_commit`, `reviewed_commit`, `commit`, `candidate` — the field
names actually used across this repo's existing exact-review terminal
schemas). Any match against a `code_byte_observations` entry's `commit_oid`
pulls that code file's current-byte-freshness check into the row's own ref
set, so a `REUSABLE_SCOPED_ARTIFACTS` row can only stay `RETAINED` if every
code file its own evidence actually certifies still matches current bytes.
This is derived per row from data already in the reconciliation, not from an
identity-ID allowlist, so it generalizes to any future drifted dependency,
not just the one this review happened to find.

Verified against the current repo (`docs/V11_R09_GATE3_G3L_RECONCILIATION_20261002.json`):
**three** identities correlate against the 7 tracked observation commits
(checked exhaustively against all 77 identities' JSON artifacts and all 7
observations) — `code.mapping_exact_commit_review`,
`code.slice3_exact_commit_review`, and `code.launch_validator_commit_tree`.
The third already lists `tools/v11_r09_gate3_launch_v4.py` directly in its
own `"artifacts"`, so the generic match adds no new ref and has no
classification effect; only the first two change category on drift. No
other row's artifacts cite any of the 7 tracked commits. Re-running the
repaired tool today reproduces the same `6/1/70/0` category counts as the
frozen `131eb12` snapshot — `code.mapping_exact_commit_review` stays
`RETAINED` because `tools/v11_r09_gate3_launch_v4.py` has not actually
drifted, and `code.slice3_exact_commit_review` is still `FUTURE` because
`tools/v11_r09_gate3_runtime.py` still has. The content differences from the
frozen snapshot are: `code.mapping_exact_commit_review` now lists the
validator file explicitly in its `source_refs` (previously absent), and
`code.slice3_exact_commit_review` now lists both `tools/v11_r09_gate3_ledgers.py`
and `tools/v11_r09_gate3_runtime.py` explicitly (previously hard-coded by ID,
with `remaining_obligation` a hand-written sentence rather than generated
from the actual drifted ref paths).

New adverse tests (`tests/test_v11_r09_gate3_g3l_identity_audit.py`):
- `test_launch_validator_drift_downgrades_mapping_row_without_hardcoding` —
  simulates a live `tools/v11_r09_gate3_launch_v4.py` byte change (the exact
  scenario the review's probe flagged as undetected) and asserts
  `code.mapping_exact_commit_review` now downgrades to `FUTURE`, with
  `category_counts` moving from `6/1/70/0` to `5/1/71/0`.
- `test_ledgers_drift_also_caught_generically_for_slice3_row` — simulates an
  additional, independent drift on `tools/v11_r09_gate3_ledgers.py` (a second
  file the slice-3 row's prose depends on but never lists) to confirm the
  mechanism is not special-cased to the one file the original review found.
- `test_malformed_code_byte_observation_refuses` — a `code_byte_observations`
  entry missing `commit_oid` fails closed with `ValueError`.

## F1 hardening repair — R1/R2 (this candidate)

The second independent review (`c9e3b8d`, Astra/high) found that the F1 fix
above, while correct on the frozen snapshot, still let a malformed or
incomplete `code_byte_observations` input falsely retain a row:

- **R1.** `commit_oid` was validated only as a non-empty string. An empty
  string, `"not-a-commit"`, or a well-formed but nonexistent 40-zero OID all
  passed, and since none of them match a real reviewed-commit field, the
  affected observation silently dropped out of dependency coverage instead
  of being refused. Removing a whole observation entry (e.g. `injected_runtime`)
  from the dict had the same silent-narrowing effect.
- **R2.** The dependency byte baseline was taken from `artifacts[obs_path]`,
  checked at the *artifact's* recorded `git_commit` — not at the
  *observation's own* `commit_oid`/`sha256`/`tree_oid`. Rebinding only the
  artifact dict's baseline to current bytes (while leaving the observation
  and the exact-review terminal untouched) could mask real drift in the
  dependency; a path substitution onto another tracked file was likewise
  unchecked.

Fix, in `tools/v11_r09_gate3_g3l_identity_audit.py`:

1. `code_byte_observations` must now contain *exactly* the fixed, known set
   of 7 names (`CODE_BYTE_OBSERVATION_NAMES`, a tool constant, not
   reconciliation data) — adding, removing, or renaming an entry fails
   closed with "missing or incomplete" rather than silently changing
   coverage.
2. Each observation's `commit_oid`, `sha256`, and `tree_oid` must be
   well-formed (40-hex / 64-hex), `commit_oid` must resolve to a real commit
   via `git rev-parse --verify <commit>^{tree}`, the resolved tree must equal
   the recorded `tree_oid`, and the blob at `<commit_oid>:<path>` must hash
   to the recorded `sha256` — all before the observation is used at all.
   Any failure raises `ValueError` (fail-closed), never a silent drop.
3. `_code_observation_refs()` now returns a ref built from the observation's
   own verified commit/hash/tree (current bytes compared against the
   observation's own `sha256`), not the artifact dict's. The artifact dict's
   `git_commit` is no longer used as a stand-in baseline for a code
   dependency.

New regression tests (`tests/test_v11_r09_gate3_g3l_identity_audit.py`):
`test_empty_observation_commit_refuses`,
`test_syntactically_invalid_observation_commit_refuses`,
`test_well_formed_but_unresolvable_observation_commit_refuses`,
`test_missing_individual_observation_refuses`,
`test_observation_path_substituted_for_another_tracked_path_refuses`,
`test_observation_wrong_sha256_refuses`,
`test_observation_wrong_tree_oid_refuses`,
`test_observation_missing_tree_oid_refuses`,
`test_artifact_rebound_to_newer_commit_does_not_mask_observation_drift`,
`test_valid_unchanged_observations_retain_mapping_row`.

This changes the generated candidate snapshot's bytes (see "New machine
snapshot" below for the updated hash) because the two correlated rows'
`source_refs` for the validator/ledgers/runtime files now report the
observation's own `git_commit`/`tree_oid` instead of the artifact dict's —
the classification outcome (`6/1/70/0`, `NO-GO`, `qualification_credit: 0`,
77/77 `MISSING`) is unchanged; independently confirmed by re-running the
reviewer's own adversarial cases against the repaired tool.

## F2 — wording (addressed)

`screen.missing_after` was always the same measurement as `missing_before`;
the handoff's "77 MISSING before and 77 MISSING after" could read as two
runs. Both fields are kept unchanged for compatibility, and two fields are
added: `screen.screen_runs: 1` and `screen.before_after_note`, stating
explicitly that this is one PRE_REVIEW measurement and the audit fills no
identity, so no second run occurs. Covered by
`test_screen_before_after_wording_states_single_measurement`.

## F3 — only the two authorized scoped items (addressed)

- **Terminal marker/error.** The recovered G3-P terminal binding
  (`docs/V11_R09_GATE3_PROTOCOL_REVIEW_117830a_terminal.json`) now also
  requires `marker == "R09_GATE3_PROTOCOL_REVIEW_PASS"` and `error is None`,
  in addition to the existing status/exit/commit/tree/report-hash checks.
  Covered by `test_original_terminal_marker_mismatch_refuses` and
  `test_original_terminal_nonnull_error_refuses`.
- **Subprocess timeout.** `_git_bytes` now runs `git cat-file blob
  <commit>:<path>` (plumbing, not `git show`) with a bounded 30-second
  timeout, raising `ValueError` (fail-closed) on `TimeoutExpired`. Verified
  byte-identical output to the prior `git show` call for an existing path/
  commit before switching. Covered by `test_git_read_timeout_fails_closed`.

Out of scope, left untouched per the authorized scope: F3's "duplicate G3-P
report reference" and "test pins the live runtime-drift set" nits are
documented observations only, not safety or correctness defects, and were not
touched.

## What is not touched

- `docs/V11_R09_GATE3_G3L_IDENTITY_AUDIT_REVIEW_131eb12.{md,verdict.json,terminal.json}`
  and `docs/V11_R09_GATE3_G3L_IDENTITY_AUDIT_20261002.json` (SHA-256
  `357f3a2a31456175ad19c68807cd53019f2464de0c13ea7608d478b51f9b80e8`) are
  unmodified. They remain the accurate historical record of what the
  independent reviewer examined at commit `131eb12` with the pre-fix tool;
  nothing here retroactively changes that record.
- No provider/socket/credential/capture/dispatch/V10/AxiomTrade/funding/
  order/root-authority path was touched. No `503`/`503`/`429` restriction
  wording, resumption rule, or hold was changed.
- `tools/v11_r09_gate3_g3l_prep.py`, the G3-L prep checker, evidence-preflight
  checker, attempt model, intake guard and GateRuntime gates are unchanged.
- The separate fresh-readiness worktree and `main` were not touched.

## New machine snapshot (this candidate, not a replacement of `131eb12`)

Re-running the repaired tool against current repo state, replaying the same
recorded host-resource proposal (target date `2026-10-04`, `observed_utc`
`1790979652`, free disk 4,137,082,880 bytes, MemAvailable 949,186,560 bytes)
regenerated `docs/V11_R09_GATE3_G3L_IDENTITY_AUDIT_HARDENING_20261002.json`
after the R1/R2 repair above, now 187,952 bytes, SHA-256
`2d60e266ce1467c3acf84fc4e631491fe8f4473c6faf60a2b064cbc7161e12c4` (prior,
pre-repair bytes for this same candidate path: 187,907 bytes, SHA-256
`566eb4beb8e8c15f359c7af2205acc91e1bc9235ae0df54f5a5ea4cb23719b1b` — only the
`code.mapping_exact_commit_review` and `code.slice3_exact_commit_review`
rows' `source_refs` changed shape, per "F1 hardening repair" above). It
reproduces: `g3l: NO-GO`, `launchable: false`, `qualification_credit: 0`,
`screen.missing_before == screen.missing_after == 77`, and
`category_counts` `{RETAINED_REVIEWED_LOCAL_SCOPE: 6,
DETERMINISTIC_OFFLINE_RECONCILIATION: 1, FUTURE_EVIDENCE_REVIEW_OR_EXTERNAL_RIGHT: 70,
INVALID_STALE_OR_DUPLICATE_REQUIREMENT: 0}` — identical to the frozen
`131eb12` snapshot's boundary. `target_date_proposal` "2026-10-04" remains a
proposal only; no date or cohort is approved.

## Tests and checks

`tests/test_v11_r09_gate3_g3l_identity_audit.py` and
`tests/test_v11_r09_gate3_g3l_prep.py`: **37 passed** (20 pre-existing + 7
from the F1 fix + 10 new R1/R2 regressions), offline, with explicit
`--basetemp=/tmp/<bounded>`, `-p no:cacheprovider`, `PYTHONDONTWRITEBYTECODE=1`.
`python3 -m py_compile` clean on all changed files. `git diff --check` clean.
No network/provider call, credential, capture, dispatch, financial, V10,
AxiomTrade or root-authority action was performed. The reviewer's own
adversarial probes (`/tmp/c9e3b8d-review-probes.py`) were independently
re-run against the repaired tool: every R1/R2 case that previously returned
`RETAINED` incorrectly now either fails closed with `ValueError` or
correctly reports `FUTURE` with real drift still detected.

This candidate is isolated in its own worktree and is unmerged. Review its
exact commit and the two JSON hashes above independently before treating
this audit as a handoff. No score crossing: **91/200; formal 1/50;
NOT_READY_TO_FUND**.
