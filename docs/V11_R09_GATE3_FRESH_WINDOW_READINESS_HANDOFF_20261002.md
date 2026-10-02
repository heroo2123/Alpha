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
`tests/test_v11_gate3_fresh_window_readiness.py` (43 synthetic/offline
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
  wrong-typed input, unparseable restriction JSON, oversized/unsafe key, too
  many keys for a closed schema, or a same-cardinality unknown key). Nothing
  downstream is evaluated once this fires.
- `PREPARATION_INCOMPLETE_PREREQUISITES_MISSING` — the inputs are
  structurally sound but one or more of: a null/malformed prerequisite
  reference, no clock observation supplied, an out-of-floor clock (source,
  monotonicity, uncertainty, calibration age, or disagreement between the
  reading and the caller's own `now_utc` beyond the reading's own stated
  uncertainty), no storage observation/qualification supplied, an
  under-floor or inconsistent physical storage reservation, a
  restriction-history record that does not demonstrate the retained ECMWF
  holds / GEFS lineage block are both present and unmodified, **or one of
  three standing policy/trust-boundary gaps that this revision added and
  that always fire today** (see below). This is the expected, honest result
  today: every real prerequisite in the committed binding is still null, so
  feeding that real state into this planner (see the test
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
  **As of this revision this outcome is unreachable** — see "Three standing
  blockers" below — until a later, separately reviewed change actually
  establishes the missing policy or trust boundary.

## Three standing blockers added by this revision (independent-review R2/R3/R4)

An independent Codex GPT-6 Astra/high review of the prior candidate
(`8dd8054`) found that, despite every other check passing, the planner could
still report `window_is_fresh=True`/`clock_ready=True`/`storage_ready=True`
for inputs that should not honestly be called ready: an unbounded window
duration/horizon, an arbitrary-precision impossible resource integer (e.g.
`2**64` bytes of disk), and a clock reading that is internally consistent
with the caller's own `now_utc` but could just as easily be a stale or
future-forged pair as an honest one. In each case, no reviewed numeric
policy or trust boundary for the missing dimension exists anywhere in this
repository's reviewed protocol or `FROZEN_LIMITS` today, and inventing one
here would itself be exactly the kind of unsupported value this module
exists to avoid.

This revision therefore treats the **absence** of each reviewed
policy/boundary as its own standing, honestly-named, always-firing
incompleteness reason, rather than silently treating the unbounded case as
ready:

- `NO_REVIEWED_WINDOW_DURATION_HORIZON_POLICY` — fires for every
  structurally valid window, including an entirely ordinary few-hour one
  (`window_is_fresh` stays `False`).
- `NO_REVIEWED_RESOURCE_MAGNITUDE_CEILING` — fires for every evaluated
  storage observation, in addition to (not instead of) the standing floor,
  exact-type and reservation-equality checks, which are unchanged
  (`storage_ready` stays `False`).
- `NO_REVIEWED_CLOCK_PROVENANCE_BOUNDARY` — fires for every evaluated clock
  observation, in addition to (not instead of) the standing quality-floor
  checks, which are unchanged (`clock_ready` stays `False`).

The practical effect is that `PREPARATION_CANDIDATE_ALL_INPUTS_PRESENT_NOT_EXECUTABLE`
cannot currently be produced by any input. That is the intended, honest
result given today's actual reviewed-policy landscape, not a defect: this
project consistently prefers an explicit blocker to an invented threshold
(see `tools/v11_gate3_fresh_window_readiness.py` module docstring for the
full reasoning on each of the three). Closing any one of them for real
requires a separate, later, independently reviewed addition that actually
defines the missing numeric ceiling or provenance boundary — not a change
to this planner's own judgment.

## Why key logic is reused, not reimplemented

The storage/resource floor check (`_check_storage`), the generic
bounded-reference/string/closed-key validators, and the frozen
resource/clock floors (`FROZEN_LIMITS`) are imported directly from
`tools/v11_gate3_evidence_preflight_checker.py` rather than copy-pasted.
That module is **not modified** by this candidate (confirmed:
`tests/test_v11_gate3_evidence_preflight_checker.py` still shows 472/472
passed, unmodified, after this candidate's tests ran in the same process).
Re-deriving that arithmetic here would risk a second, silently-diverging
copy of a safety-relevant comparison; reusing it is the smaller, more
reviewable diff. The frozen expired October 2 window (`FROZEN_WINDOW`,
used only as the roll-forward comparison target, including any window that
*overlaps* it) is likewise imported and hard-bound internally -- it is not
a parameter a caller can override.

The one checker function deliberately **not** reused is `_check_clock`:
that function enforces that the measured reading already sits inside the
proposed dispatch window, which is a dispatch-time overlap check, not a
preparation-time quality check, and would make an honest reading taken
before a future window opens impossible to satisfy. This module's own
`_check_clock_quality` instead checks the reading's method/quality (source,
monotonicity, uncertainty, calibration age) plus that the reading agrees
with the caller's own `now_utc` within the reading's own stated
uncertainty -- that agreement check demonstrates internal *consistency*
between two caller-supplied values, not authenticity (see "Three standing
blockers" above: a matching stale or future-forged pair passes it exactly
as an honest pair does, which is why `clock_ready` additionally always
reports the standing `NO_REVIEWED_CLOCK_PROVENANCE_BOUNDARY` gap). Restriction
bytes are parsed through the checker's own `_safe_parse`/`MAX_RAW_BYTES`
size cap rather than an unbounded `strict_json_loads` call. Every closed-key
check in this module (`proposed_window`, `prerequisites`, `restrictions`)
goes through a local `_check_closed_bounded` wrapper that bounds mapping
cardinality against the schema's own fixed key count before doing any
per-key work, and never embeds a caller-supplied key -- oversized,
non-UTF-8-encodable, or simply unrecognized (e.g. a short private
sentinel) -- verbatim into a reason string; `storage_qualification` and the
nested `known_control_domains.ECMWF`/`.GEFS` mappings go through the
equivalent `_unsafe_untrusted_submapping` go/no-go guard before being
handed to the checker's own unbounded, verbatim-echoing `_check_storage`/
`_check_restriction_domains`. A final `_bound_diagnostic_output` step caps
the total serialized size of every returned reason list against the
already-reviewed `diagnostic_output_bytes` frozen limit, as defense in
depth independent of the raw-input byte cap.

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
python -B -m pytest tests/test_v11_gate3_fresh_window_readiness.py tests/test_v11_gate3_evidence_preflight_checker.py -q   # 531 passed (59 planner + 472 checker, unmodified)
python -O -B -m pytest tests/test_v11_gate3_fresh_window_readiness.py tests/test_v11_gate3_evidence_preflight_checker.py -q   # 531 passed, 1 unrelated pytest-config warning
python -m py_compile tools/v11_gate3_fresh_window_readiness.py tests/test_v11_gate3_fresh_window_readiness.py tools/v11_gate3_evidence_preflight_checker.py   # clean
git diff --check                                                        # clean
```

No socket, HTTP, DNS, decode, provider request, service/authority/
financial/V10/AxiomTrade action, or SHADOW/Brain change occurred while
building or testing this candidate.

## Review and repair history

The original candidate (`aa80818`, 36 tests) received an independent
Opus review with verdict `CHANGES_REQUIRED`: the clock check reused the
checker's dispatch-time window-overlap logic, making the `CANDIDATE`
outcome reachable only via a clock reading dated after `now_utc` (a
forged reading) and never via an honest one (F1, blocking); no size cap
on `restrictions_raw` and unbounded key echoing into reason strings (F2);
a caller-overridable `expired_window` parameter (F3); and exact-identity-
only roll-forward detection (F4, strongly recommended). F1 and F3 were
fixed in commit `5a33489`, along with the overlap half of F4 and part of
F2 (the size cap and bounded keys for `proposed_window`/`prerequisites`/
`restrictions`); a second independent Opus re-review of `5a33489` found
F2 was not fully closed for `storage_qualification` (which reaches the
checker's own unbounded `_check_closed` through `_check_storage`,
including a short-but-UTF-8-unsafe lone-surrogate key that a length-only
bound would miss) and that this document still described the old,
superseded clock logic. Both were fixed in commit `8dd8054`: a
`_has_unsafe_key` guard was applied to `storage_qualification` before
`_check_storage` ran, and this document described `_check_clock_quality`
and the bounded-key wrapper accurately as of that commit (43 tests).

An independent Codex GPT-6 Astra/high exact-commit review of `8dd8054`
found that revision's bounding was still incomplete and its freshness
claims too broad, returning `CHANGES_REQUIRED` with four findings:
mapping cardinality and nested `known_control_domains.ECMWF`/`.GEFS`/
`storage_qualification` keys were still unbounded in aggregate work and
output, and a short private-sentinel key could still be echoed if it
replaced (rather than added to) an expected key (R1); no window
duration/horizon ceiling existed, so a 100-year window, a `9999`-dated
horizon, and a one-microsecond window all reported `window_is_fresh=True`
(R2); disk/memory/reservation integers of `2**64` or `10**400` still
passed every check (R3); and a clock reading that matches the caller's own
`now_utc` but is itself stale or future-forged by hours, days, or
millennia passed every quality floor exactly as an honest reading does,
because agreement is consistency, not authenticity (R4).

This revision fixes all four. R1: `_check_closed_bounded` now refuses on
mapping cardinality alone (bounded by the schema's own fixed key count)
before any per-key work, and a same-cardinality unknown key is reported
generically (`UNKNOWN_KEY:<label>`, no key text) rather than individually
echoed; the same bounding (`_unsafe_untrusted_submapping`,
`_check_nested_domain_bounded`) now also gates `storage_qualification` and
the nested ECMWF/GEFS mappings before they reach the checker's own
unbounded `_check_closed`; a final `_bound_diagnostic_output` step caps
total reason output against the already-reviewed `diagnostic_output_bytes`
frozen limit. R2/R3/R4: rather than invent a numeric duration/horizon
ceiling, resource magnitude ceiling, or clock-provenance boundary — none of
which exists anywhere in this repository's reviewed protocol or
`FROZEN_LIMITS` — each is now an explicit, always-firing, honestly-named
incompleteness reason (`NO_REVIEWED_WINDOW_DURATION_HORIZON_POLICY`,
`NO_REVIEWED_RESOURCE_MAGNITUDE_CEILING`,
`NO_REVIEWED_CLOCK_PROVENANCE_BOUNDARY`; see "Three standing blockers"
above). **This means `PREPARATION_CANDIDATE_ALL_INPUTS_PRESENT_NOT_EXECUTABLE`
is not reachable by any input as of this revision** — an intentional,
honest consequence of there being no reviewed policy for any of the three
gaps today, not a regression in what this planner can detect. 16 tests
were added in this pass (59 total).

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

Separately, and not requested or scheduled by this document either: before
`PREPARATION_CANDIDATE_ALL_INPUTS_PRESENT_NOT_EXECUTABLE` can ever be
produced again, a later, independently reviewed change must actually
establish one or more of the three standing gaps above — a reviewed window
duration/horizon ceiling, a reviewed resource/reservation magnitude
ceiling, and a reviewed trusted clock-observation provenance boundary. This
planner does not propose candidate values for any of them; doing so here
would be exactly the invented-threshold problem this revision fixed.
