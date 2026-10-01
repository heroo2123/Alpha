# Independent exact-commit review — Gate 3 V4 slice 3 J1

**PASS for the offline injected-runtime scope. G3-L remains NO-GO.**

Reviewer: Astra/high, independent of the Sonnet/high author. Exact commit
`6340cb455eebae374039c9bae23cd806681dacb0`, tree
`b452ba91072bb78146ef6f4140f5ad41898aed28`, parent `3a066a6`.
Detached checkout: `/tmp/alpha-v11-gate3-slice3-review-6340cb4`.
Main context: `b0111d5`. This verdict precedes newer-main reconciliation.

## Findings and disposition

No blocking finding reproduced in the changed boundary. J1 is closed for
ordinary clock-source exceptions under the reviewed valid synthetic transport
and healthy-journal contract. Both the recovery receipt resample and normal
header-receipt sample now catch `Exception`, matching `_postdispatch_monotonic`.
Recovery preserves an explicit unresolved HTTP restriction when time is
unavailable, accounts eager bytes once, leaves the intent/reservation held and
re-raises the original ordinary exception. No clock evidence or expiry is
fabricated. The unrelated journal/denial-write catches are unchanged.

The 24 independent ordinary-failure cases cover OSError, RuntimeError,
ValueError and OverflowError at six source-call onsets (1, 2, 3, 5, 7, 9).
All retain six delivered bytes and one restriction event, preserve original
exception identity and hold the budget through two reopens with unchanged
journal heads. This includes all four failed acceptance cases from the prior
3a066a6 review. The product suite separately covers a custom direct Exception
subclass; the implementation uses the general boundary, not a finite list.

Twelve fresh process-termination cases cover KeyboardInterrupt, SystemExit
and a direct BaseException subclass at the first post-dispatch and header
receipt source calls, and during recovery after an ordinary initial failure.
All propagate the exact termination object; no successful terminal or refund
is produced. Holds survive two reopens. Direct termination bypasses ordinary
recovery (zero bytes recorded at these onsets); termination inside recovery
still executes its finally block (six bytes recorded). This is intentional
termination propagation, not a claim of complete observation durability under
abrupt process death. Recovery termination may replace an earlier ordinary
error, and is never swallowed to preserve that earlier error.

The carried 95-case acceptance harness passes, including H1 clock age,
H2 restriction retention, H3/H4 manifest precedence/event binding, H5 restart
and external-head rollback, H6/I2 independent refusal reasons, I1 sampling
boundaries, I3 cutoff/RAW classification, bounded eager accounting, cooldown
lower bounds, clock intersection and physical report reserve. In conjunction
with the prior independent lineage review, J1 no longer blocks I1/H2/G2/F2.
Prior limitations and the RAW-only scope remain; this is not feature admission.

## Executed evidence

All artifacts use `/tmp/alpha-v11-slice3-review-6340cb4` as prefix:

| Suffix | Result |
| --- | --- |
| `.family-results.json`, `.family-*.log`, `.family-runner.py` | 502 passed across ten sequential files; two existing fork warnings |
| `.probes.py`, `.probes.log` | 95 carried independent acceptance cases passed |
| `.extra.py`, `.extra.log`, `.extra-*.json` | 24 ordinary-failure cases passed with journal snapshots |
| `.termination.py`, `.termination.log`, `.termination-*.json` | 12 fresh process-termination cases passed |
| `.probe-results.json` | Exact commands and exit codes for independent probes |
| `.terminal.json` | Exact identity, verdict and artifact digests |

Family command: `python3 /tmp/alpha-v11-slice3-review-6340cb4.family-runner.py`.
Each probe command uses the review venv Python, `-m pytest -q
--import-mode=importlib -p no:cacheprovider`, a unique prefix-owned basetemp
and the corresponding probe file, with `PYTHONDONTWRITEBYTECODE=1`.
Independent probes prohibit socket connections. All responses, resources and
clock failures are synthetic. Prior harnesses were copied to this exact root;
only paths changed. Prior author/reviewer artifacts were not overwritten.

Candidate status remains clean; exact commit/tree unchanged; cumulative
`git diff --check 9c3e748..6340cb4` passed. Scope includes the two-file J1 diff,
clock/recovery callers, existing design and prior 3a066a6/8efb60a independent
reports. Unchanged cumulative architecture relies on that prior review chain
plus the rerun controls; this is not a fresh architectural audit of every line.

The family runtime file left only 11,698,176 bytes free at its peak; it passed
without ENOSPC. Its completed review-owned scratch was then removed by the
runner, restoring roughly 1.4 GiB. Future reconciliation tests must clean each
completed test's own disposable data rather than accumulate the whole runtime
file. No other worker's evidence or scratch was removed. Resource samples are
host observations, not launch qualification.

## Remaining gates

Reconcile against newer main (including provider mapping and offline prep),
then validate the combined tree before local integration. No release-wide suite
was run here. The prior load-sensitive markout fix `6ec371e` is already an
ancestor of main and has not been reopened from the stale handoff.

No concrete adapter, live decoder/provider, host clock/persistence, physical
launch storage, feature/runtime-use pipeline or G3-L package is accepted here.
The frozen offline inventory still has 77 missing pre-review identities, and
the original October 1 window has elapsed. It must not be silently rolled
forward. There has been no provider request or forward SHADOW sample.

PAPER demo/scanner/controller are inactive/disabled, execution inactive/masked;
protected authority roots are absent. Private master hash remains
`a0e16d9bd7344c943a54a16a53c6757662363d93642f6e5cb7953cd047659b4a`.
No V10, AxiomTrade, service, credential, financial or private-master change.
No C/J/E/A boundary crossed: **91/200, formal 1/50, NOT_READY_TO_FUND**.
