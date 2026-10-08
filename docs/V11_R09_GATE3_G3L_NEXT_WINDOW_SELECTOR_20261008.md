# Gate-3 G3-L offline next-window candidate selector — 2026-10-08

**Status: NON-LAUNCHABLE. G3-L remains NO-GO.** This adds one pure, offline
function, `next_window_candidate(now_utc, *, max_days_ahead=30)`, to
`tools/v11_r09_gate3_g3l_prep.py`. It answers a narrow question — "given an
explicit UTC instant, what is the earliest future target date whose frozen
acquisition window has not already started?" — and nothing else. It does not
replace the existing report (`make_report`), checklist (`freeze_checklist`),
or screen (`check_inventory`); it reuses all three unchanged.

## Contract

```python
next_window_candidate(now_utc: int, *, max_days_ahead: int = 30) -> dict
```

- `now_utc` must be a strict `int` (not `bool`), a positive Unix timestamp,
  and an exact whole UTC second that round-trips through
  `datetime.fromtimestamp(now_utc, tz=timezone.utc)`. Any other value —
  float, string, zero, negative, or an instant outside the platform's
  representable range — raises `ValueError`. Nothing is read from the system
  clock; `now_utc` must always be supplied explicitly.
- `max_days_ahead` bounds the forward search and must be an `int` in
  `[1, MAX_WINDOW_SEARCH_DAYS]` (`MAX_WINDOW_SEARCH_DAYS = 30`). It counts
  future UTC **target dates**, from tomorrow (offset 1) through the date
  `max_days_ahead` days after `now_utc`'s UTC day, inclusive. A bound of 1
  can suggest tomorrow until today's 14:00 UTC window start; at or after
  14:00, the earliest eligible target is two dates ahead and requires a
  bound of at least 2. The search raises `ValueError` if the bound is
  exhausted or the next target date exceeds the representable calendar.
  It never runs unbounded or returns a later, less useful answer.
- Eligibility is exactly: `freeze_checklist(target_date)["window_start_utc"]
  > now_utc` — i.e. the candidate's frozen window has not yet started. This
  is the same half-open-interval convention (`start <= now` ⇒ no longer
  future) used throughout this codebase's other expiration gates (station
  certification, model authority, daily-review authority).
- On success, returns a dict: `schema` (the existing
  `R09_GATE3_G3L_OFFLINE_PREP_V1`), `launchable: False`, `status:
  "CANDIDATE_TARGET_DATE_SUGGESTED"`, the input `now_utc`, the chosen
  `target_date`, `days_from_now_searched` (the selected target date's UTC
  calendar-day offset from `now_utc`, in `1..max_days_ahead`), the full
  `freeze_checklist(...)` for that date (every substantive field still
  `None`, unchanged from today), the existing `PRE_REVIEW`
  `check_inventory(...)` missing-identity screen against an all-`None`
  evidence map for that date (all 77 `PRE_REVIEW_IDS` remain `MISSING`),
  and a `handoff` string stating this is a suggestion only.
- `launchable` is always `False`; no key named `*authority*` appears in the
  result; no qualification credit, approval, or review state is created,
  read, or implied.

## What this does not do

- It never reads an existing frozen/reviewed inventory, package, or
  manifest, and therefore can never mutate, re-date, or roll one. Any
  already-frozen package is completely untouched by this call.
- It performs no file I/O, no socket/network call, and no provider,
  runtime, commissioning, or root action. It is a pure function of its two
  arguments, built only from `freeze_checklist` and `check_inventory`,
  which were already pure and already offline.
- It does not select a station, cohort, event, or run, and does not advance
  or "start" anything — same as every other function in this module, this
  is a proposal, not an authorization. See
  [`V11_R09_GATE3_G3L_OFFLINE_PREP.md`](V11_R09_GATE3_G3L_OFFLINE_PREP.md)
  for the unchanged two-stage evidence/review contract this screen reuses.

## Worked example

At `2026-10-02T21:44:05Z`, `next_window_candidate(1790977445)` returns
`target_date="2026-10-04"` with `freeze_checklist["window_start_utc"]`
equal to `2026-10-03T14:00:00Z` — matching the manually-derived earliest
future window recorded in
[`V11_R09_GATE3_G3L_IDENTITY_AUDIT_HANDOFF_20261002.md`](V11_R09_GATE3_G3L_IDENTITY_AUDIT_HANDOFF_20261002.md):
"the earliest future mechanical acquisition window is October 3
14:00–17:00 UTC, giving a proposal-only October 4 target. No date or
cohort was approved." This function makes that same manual derivation
reproducible and testable; it still approves nothing.

## Tests

`tests/test_v11_r09_gate3_g3l_prep.py` adds coverage for: the 14:00 UTC
window-start boundary (just-before vs. exactly-at), the 17:00/18:00 UTC
fields not affecting date selection, a year-end calendar rollover, the
worked example above, determinism/purity (repeated calls, no socket
access), never granting authority or a qualification credit (in normal and
`python -O` modes), invalid/ambiguous `now_utc` (non-int, bool, float,
string, `None`, zero, negative, unrepresentable instant), the bound-1
before/at/after-14:00 behavior, year-9999 calendar exhaustion, and
bounded search exhaustion failing closed rather than hanging or guessing.
