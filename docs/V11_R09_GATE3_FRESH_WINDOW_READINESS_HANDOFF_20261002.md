# Gate 3 fresh-window preflight preparation readiness — candidate handoff — 2026-10-02

**Candidate for independent review. NOT an executable preflight PASS, NOT a
G3-L determination, NOT a resolved prerequisite, and NOT independent review.**
Author: Claude Sonnet 5, parallel worktree
`/tmp/alpha-v11-gate3-fresh-readiness-20261002`, baseline `5bc5321`
(`docs/V11_R09_GATE3_EVIDENCE_PREFLIGHT_PROTOCOL_20261002.md` SHA-256
`ae59812fa58ec41895b87408f0ea175988ae6d20a1f4d5dabcd96a01c2dd3a68`;
`docs/V11_R09_GATE3_EVIDENCE_PREFLIGHT_PACKAGE_20261002.json` SHA-256
`4e9af52d3e2f6f9037e38fb7570c1d43a3984ed25c34090aedecfc1d2549e361`, i.e. the
exact package committed at this baseline, all twelve prerequisites still
null). A second Sonnet worker was concurrently active in
`/tmp/alpha-v11-gate3-postguard-prereqs-20261002` owning
collector/launch/runtime integration; this candidate touches none of those
files and was not coordinated with that worker beyond reading the same
baseline commit.

## What problem this closes

`tools/v11_gate3_evidence_preflight_checker.py` (the protocol section 7
offline checker) validates exactly one thing: the frozen October 2
`P1_GEFS_INDEX` proposal. Every scalar, the window, and the request body are
checked against hard-coded frozen constants (`FROZEN_SCALARS`,
`FROZEN_WINDOW`, `FROZEN_REQUEST`, ...), so by design it refuses any other
date, window or request outright — it cannot be pointed at a different
candidate window without itself being a new reviewed package. The October 2
10:00–13:30Z window is now expired or about to be (today is 2026-10-02); the
protocol (section 7) and the review handoff both say explicitly that a new
date requires fresh exact-byte review, never a silent roll-forward of the
expired package.

Nothing in the repository, before this candidate, let an operator cheaply ask
"are the upstream prerequisites for *some other* dated window actually in
hand yet, before anyone spends effort authoring and sending a brand-new
package for independent review?" This candidate is exactly that one
narrow upstream check — nothing more.

## What this candidate is

A new, self-contained module,
`tools/v11_gate3_fresh_window_readiness.py`
(`evaluate_fresh_window_readiness`), plus
`tests/test_v11_gate3_fresh_window_readiness.py` (36 synthetic/offline
tests, all passing, no execution restriction encountered in this session).
It takes, as explicit caller-supplied arguments:

- a **proposed** fresh window (not yet a package, not yet reviewed);
- the current prerequisite-reference bundle (same twelve keys the checker's
  package schema requires; today, in the real committed binding, all twelve
  are null — this candidate does not change that fact, only reports it);
- the actual current restriction-inventory bytes (same shape as the private
  `restriction-history.json` the checker validates), so the three retained
  ECMWF denials and the GEFS lineage block are read from real retained
  evidence, never a freshly-constructed empty history;
- an optional `ClockObservation` and `ResourceObservation` (the same
  dataclasses the checker already defines and tests) and an optional storage
  qualification record;
- the caller's own measured "now" (never `datetime.now()` inside the
  module — nothing in this candidate reads the wall clock, a socket, or the
  filesystem).

It returns a `FreshWindowReadinessResult` with exactly three allowed
outcomes, each with a closed, non-promotable label:

- `FRESH_WINDOW_REFUSED_BEFORE_PREPARATION` — a structural/safety problem
  (malformed window, window not strictly in the future, window byte-for-byte
  identical to the known expired October 2 window — i.e. a silent
  roll-forward attempt —, `automatic_roll_forward` not exactly `False`,
  wrong-typed input, unparseable restriction JSON, unknown/missing schema
  key). Nothing downstream is evaluated once this fires.
- `PREPARATION_INCOMPLETE_PREREQUISITES_MISSING` — the inputs are
  structurally sound but one or more of: a null/malformed prerequisite
  reference, no clock observation supplied, an out-of-floor clock
  (uncertainty/calibration age/window overlap), no storage
  observation/qualification supplied, an under-floor physical storage
  reservation, or a restriction-history record that does not demonstrate the
  retained ECMWF holds / GEFS lineage block are both present and
  unmodified. This is the expected, honest result today: every real
  prerequisite in the committed binding is still null, so feeding that real
  state into this planner (see the test
  `test_real_binding_prerequisites_remain_incomplete_today`) reports all
  twelve missing, never a fabricated pass.
- `PREPARATION_CANDIDATE_ALL_INPUTS_PRESENT_NOT_EXECUTABLE` — every
  supplied input clears its floor. This still confers **no** execution
  authority, provider right, discovery eligibility, or G3-L credit; it means
  only that authoring an actual exact-byte package for this window, and
  sending it for its own required fresh independent review, is now worth
  doing. The dataclass's `__post_init__` makes this outcome unconstructable
  unless every one of `window_is_fresh`/`clock_ready`/`storage_ready`/
  `restriction_history_preserved` is true and `missing_prerequisites` is
  empty — not a status label a caller can spoof by only checking `outcome`.

## Why key logic is reused, not reimplemented

The clock-uncertainty/window-overlap check (`_check_clock`, including the
`Fraction`-based rounding that decides whether the measured clock's stated
uncertainty interval still fits inside the proposed dispatch window) and the
storage/resource floor check (`_check_storage`) are imported directly from
`tools/v11_gate3_evidence_preflight_checker.py` and called against the
*proposed* window/limits rather than copy-pasted. That module is **not
modified** by this candidate (confirmed: `tests/test_v11_gate3_evidence_preflight_checker.py`
still shows 472/472 passed, unmodified, after this candidate's tests ran in
the same process). Re-deriving that arithmetic here would risk a second,
silently-diverging copy of a safety-relevant interval comparison; reusing it
is the smaller, more reviewable diff. The frozen resource/clock floors
(`FROZEN_LIMITS`) and the frozen expired October 2 window (`FROZEN_WINDOW`,
used only as the roll-forward comparison target) are likewise imported, not
restated.

## What this candidate is not

- Not a package author: it produces a readiness verdict about *inputs*, not
  an `ALPHA_V11_EVIDENCE_PREFLIGHT_PACKAGE_V1` document, and performs no
  schema promotion from readiness to an executable package.
- Not a relaxation of the checker: the checker's exact frozen-package
  refusal behavior for the October 2 proposal is untouched and unimported
  for mutation — only its already-reviewed pure validator functions and
  constants are read.
- Not a clock, storage, or provider-access source: every `ClockObservation`,
  `ResourceObservation`, and prerequisite reference is an explicit argument;
  the module contains no `time.time()`/`datetime.now()`, no `socket`, no
  `subprocess`, no filesystem write, and performs no DNS/HTTP/decode.
- Not a replacement for `tools/v11_r09_gate3_g3l_prep.py` (the separate,
  pre-existing G3-L 2,713-slot / 77-identity capacity planner): that tool
  answers a different question (how many capture slots a *reviewed* V4
  package could fit) and is untouched here.
- Not score, gate, or credit: no qualifying slot or C/J/E/A boundary is
  crossed by this candidate. **91/200, formal 1/50; G3-L NO-GO;
  NOT_READY_TO_FUND** — unchanged, and this candidate cannot change it.

## Verification run this session

```
python -m pytest tests/test_v11_gate3_fresh_window_readiness.py -q      # 36 passed
python -O -m pytest tests/test_v11_gate3_fresh_window_readiness.py -q   # 36 passed, 1 unrelated pytest-config warning
python -m pytest tests/test_v11_gate3_evidence_preflight_checker.py -q  # 472 passed, unmodified file
python -m py_compile tools/v11_gate3_fresh_window_readiness.py tests/test_v11_gate3_fresh_window_readiness.py   # clean
git diff --cached --check                                               # clean
```

(bounded `pytest --basetemp=/tmp/alpha-v11-gate3-fresh-readiness-basetemp`,
removed after this run.) No socket, HTTP, DNS, decode, provider request,
service/authority/financial/V10/AxiomTrade action, or SHADOW/Brain change
occurred while building or testing this candidate.

## Required before any further promotion

This is a candidate only. Before it is relied on for an actual dated
proposal: independent exact-commit review by a different model (per the
standing review-separation requirement already in force across this gate's
other candidates), reconciliation against whatever newer main exists at
review time, and — separately, and only after that review — the actual
twelve prerequisites it currently reports missing must be genuinely
satisfied by real reviewed evidence before any real package is authored.
This document does not request, imply, or schedule that evidence; it only
builds the tool that will report honestly once it exists.
