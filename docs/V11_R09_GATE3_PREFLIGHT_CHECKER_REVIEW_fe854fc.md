# Gate 3 offline checker: independent Astra/high review of fe854fc

**CHANGES_REQUIRED. Do not integrate this candidate.**

Reviewed commit `fe854fc5486d4ea24eda442308034b150feeaeda`, tree
`3bc097f7859553c2fc3e2e3f300d44b00ff0e733`, parent
`b15356039820154e25ea5b40a69393418e5e639d`. The repair author was Sol/high;
this independent reviewer is `gpt-6-astra`, high reasoning, Fast service,
verified from the actual reviewer process arguments (PID 2187417).

The clean repair worktree is
`/tmp/alpha-v11-gate3-preflight-checker-repair-20261002`.
I read the full checker, tests, accepted protocol and prior review, and reviewed
the two-file repair diff. No candidate file was edited. The full candidate
relative to main adds only the checker and its tests; the repair relative to
its parent changes 89 insertions/15 deletions in those same two files.

## Verification and reproduced repairs

`/home/alphaadmin/AlphaV11_Dev/venv/bin/python -m pytest
tests/test_v11_gate3_evidence_preflight_checker.py -q -p no:cacheprovider`
passed **91 tests in 0.41 seconds**, exit 0. `git diff --check` passed and
the candidate remained clean at the exact commit/tree above. No full suite
or unrelated release suite was run.

The retained independent reproduction script and JSON output are adjacent to
this report as `.repro.py` and `.repro.json`. The script checks the exact Git
commit and patches socket creation and DNS resolution to raise. It uses the
existing explicitly synthetic positive fixture, rebinding package/restriction
hashes and lengths after mutations so unrelated byte mismatches cannot mask
validation defects. It records 35 cases: one valid synthetic baseline,
19 unexpected satisfied outcomes, nine exceptions, five repaired-case
refusals and one real-package refusal. The harness exits 0 to mean that it
recorded its observations, **not** that the checker passed review.

All three previous findings are fixed: scalar `directories` and `input_refs`
refuse; float byte length/missing path refuse; invalid resource observations
still report an independently missing physical reservation.

The real retained package and restriction history still match the public
binding and protocol hashes. With synthetic in-window clock/resources, the
real package refuses with the same **21 reasons**, including all 12 null
prerequisites. These observations are test inputs, not current clock/storage
qualification or evidence of a dispatch opportunity.

## Findings

1. **High — valid JSON of the wrong top-level type raises instead of refusing.**
   At checker lines 692–696, `_safe_parse` accepts JSON `null`, `[]` or `1`
   without adding a reason. The early return constructs `OUTCOME_REFUSED`
   with an empty reason tuple; `CheckResult.__post_init__` raises `ValueError`.
   All nine combinations across package, restrictions and binding reproduce.
   Record a labeled non-object reason for every malformed root before
   returning. Preserve the result invariant and invalid-JSON diagnostics.

2. **High — unresolved/held restriction evidence can satisfy the checker.**
   At lines 555–587, most prerequisite checks reject only `None`. Setting
   `complete_lineage_review`, `shared_history_head`,
   `unresolved_attempt_reconciliation`, or GEFS `scope_independence_review`
   individually to `false` yields satisfied with zero reasons. Setting both
   GEFS domain status and the request's matching restriction status to `HELD`,
   `DENIED`, `UNKNOWN`, or an empty string also yields satisfied. Equality
   across two documents is not proof that their common status is admissible.
   Require well-formed references and a valid SHA-256 head, explicitly refuse
   unknown/unresolved/denied/held GEFS states, and preserve the historical
   ECMWF hold rules and scope prerequisites. Do not claim that reference shape
   alone establishes genuine provider rights or an authenticated history.

3. **Medium — the advertised closed, bounded, typed schema is incomplete.**
   `storage_qualification.persistence_review=false` satisfies (lines 438–440).
   So do request port `443.0` (ordinary equality at lines 630–635), a
   4,097-character `author_model`, boolean `blocking_reasons`, an over-limit
   65-element `unknown_outputs_not_required_as_inputs`, malformed
   `binding.source_inputs`, and boolean `binding.owner_instruction_record`.
   `strict_json_loads` rejects named NaN/Infinity but accepts numeric exponent
   overflow `1e999`; placed in the unvalidated `author_model`, it produces a
   satisfied result containing a nonfinite parsed value. There is no raw JSON
   byte bound before parsing and many nested containers/strings bypass bounds.
   Audit the complete declared schema, not only the sampled fields. Validate
   types, known nested key sets, finite values and container/string bounds;
   bound parser work before allocation where practical. Preserve the real
   retained shapes and do not edit immutable protocol/package/evidence bytes.

4. **Medium — malformed clock and terminal observations are accepted.**
   `_parse_utc` at lines 296–305 accepts a timestamp without a zone and lets
   `astimezone` interpret it in the host's local timezone. The naive
   `2026-10-02T10:05:00` satisfies on this UTC host. `_check_clock` accepts
   truthy string `"false"` as `monotonic_consistent`. The terminal checker
   at lines 645–656 accepts boolean `false` as exit code zero and treats a
   missing `error` key as explicit null. Each case yields satisfied with no
   reasons. Require an explicit timezone and exact boolean/integer types,
   required terminal keys, and bounded valid observation values. Keep
   offset-aware timestamps supported; do not replace measured evidence with
   a current host timestamp or turn these synthetic terminal checks into
   executable review authority.

These are defects in an offline refusal/schema checker, not a demonstrated
financial or network execution route. The module still imports only local
standard-library validation facilities, has no transport or filesystem writes,
and fixes eligibility to `DISCOVERY_ONLY_NOT_G3E`. Even its incorrect satisfied
outcomes explicitly confer no execution authority. That containment does not
satisfy the protocol's required checker behavior.

## Next repair and acceptance

Repair the checker and its focused tests in the same isolated repair worktree,
preserving history and starting from exact `fe854fc`. Add mutually consistent
synthetic adversarial fixtures for all findings, including an exhaustive
field/type/bounds audit rather than another one-example patch. Keep real
evidence immutable and refused. Require a fresh different-model exact-commit
review, an original completed reviewer-process terminal, and newer-main
reconciliation before integration. No request, date roll-forward, network
adapter or public/private package rewrite is authorized by this review.

The current review is itself inside the persistent coordinator's original
Astra process. Its original exit code cannot truthfully be asserted before
this turn ends. The adjacent `.binding.json` pins the clean checkout and
report/verdict/reproduction hashes and names the parent's retained log.
The next route must seal the completed original process using the exact
parent `route_end` entry and retained log/output hashes; never substitute the
pytest or reproduction exit code. This pending completion seal grants no PASS
and does not prevent repair of reproduced CHANGES_REQUIRED findings.

The protected FINAL-REVIEWED master hash is unchanged (`a0e16d9b...659b4a`).
No V10, AxiomTrade, provider, financial, service or authority action occurred.
The SHADOW and Brain-readiness trees are clean; ECMWF's unfinished untracked
backfill remains preserved. Weather execution is inactive/masked, no separate
Alpha worker was found, and protected authority roots remain absent. Host
headroom was about 2.85 GiB free disk and 632 MiB MemAvailable: below preferred
3 GiB/900 MiB, above the 2 GiB disk floor. No extra parallel heavy worker or
full-suite rerun was justified. The earlier load/order-sensitive release
failure was already resolved by the retained 5,460-pass/13-skip release,
which remains historical.

**91/200, formal 1/50; A2/A3 UNQUALIFIED; A4 OPEN; A8 UNQUALIFIED;
G3-L NO-GO; NOT_READY_TO_FUND.** No capture slot, forward SHADOW sample,
identity or C/J/E/A boundary is newly qualified.
