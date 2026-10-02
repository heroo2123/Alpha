# G3-L retained-identity audit — hardening of the independent review's F1 — 2026-10-02

**Offline candidate for independent review, base `main` `40bc64e`. G3-L remains
NO-GO.** This repairs `tools/v11_r09_gate3_g3l_identity_audit.py` against
finding F1 of the independent exact-commit review
`docs/V11_R09_GATE3_G3L_IDENTITY_AUDIT_REVIEW_131eb12.md`, and makes the two
scoped F2/F3 wording and safety improvements that review authorized. It
creates no qualified inventory entry, private V4 manifest, capture, dispatch,
or approval, and changes no provider/credential/socket/root-authority path.

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
only `code.mapping_exact_commit_review` and `code.slice3_exact_commit_review`
have any such correlation at all (checked exhaustively against all 77
identities' JSON artifacts and all 7 observations; no other row's artifacts
cite any of the 7 tracked commits). Re-running the repaired tool today
reproduces the same `6/1/70/0` category counts as the frozen `131eb12`
snapshot — `code.mapping_exact_commit_review` stays `RETAINED` because
`tools/v11_r09_gate3_launch_v4.py` has not actually drifted, and
`code.slice3_exact_commit_review` is still `FUTURE` because
`tools/v11_r09_gate3_runtime.py` still has. The only content differences
from the frozen snapshot are: `code.mapping_exact_commit_review` now lists
the validator file explicitly in its `source_refs` (previously absent), and
`code.slice3_exact_commit_review`'s `remaining_obligation` is now generated
from the actual drifted ref paths instead of a hand-written sentence.

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

Re-running the repaired tool against current repo state and a fresh host
read (`2026-10-02T22:20:52Z`, free disk 4,137,082,880 bytes, MemAvailable
949,186,560 bytes) produced
`docs/V11_R09_GATE3_G3L_IDENTITY_AUDIT_HARDENING_20261002.json`, 187,907
bytes, SHA-256
`566eb4beb8e8c15f359c7af2205acc91e1bc9235ae0df54f5a5ea4cb23719b1b`. It
reproduces: `g3l: NO-GO`, `launchable: false`, `qualification_credit: 0`,
`screen.missing_before == screen.missing_after == 77`, and
`category_counts` `{RETAINED_REVIEWED_LOCAL_SCOPE: 6,
DETERMINISTIC_OFFLINE_RECONCILIATION: 1, FUTURE_EVIDENCE_REVIEW_OR_EXTERNAL_RIGHT: 70,
INVALID_STALE_OR_DUPLICATE_REQUIREMENT: 0}` — identical to the frozen
`131eb12` snapshot's boundary. `target_date_proposal` "2026-10-04" remains a
proposal only; no date or cohort is approved.

## Tests and checks

`tests/test_v11_r09_gate3_g3l_identity_audit.py` and
`tests/test_v11_r09_gate3_g3l_prep.py`: **27 passed** (20 pre-existing + 7
new), offline, with explicit `--basetemp=/tmp/g3l-audit-hardening-basetemp/bt`,
`-p no:cacheprovider`, `PYTHONDONTWRITEBYTECODE=1`. `python3 -m py_compile`
clean on both changed files. `git diff --check` clean. No network/provider
call, credential, capture, dispatch, financial, V10, AxiomTrade or
root-authority action was performed.

This candidate is isolated in its own worktree and is unmerged. Review its
exact commit and the two JSON hashes above independently before treating
this audit as a handoff. No score crossing: **91/200; formal 1/50;
NOT_READY_TO_FUND**.
