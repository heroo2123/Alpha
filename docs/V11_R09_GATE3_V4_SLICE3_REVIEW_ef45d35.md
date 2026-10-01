# Gate 3 V4 slice 3 repair — independent exact-commit review

**CHANGES_REQUIRED. G3-L NO-GO.** This is an offline implementation review, not provider, launch, SHADOW, financial, or production authorization.

- Candidate: `ef45d355f8d7e3a924564cdf9f5f2601c01a50f6`
- Tree: `048551f28b5845d4197de3dae6c6be8a258993f3`
- Base: `8e446fd7ee74b3cba987dc6db56ce0a28ab5c959`
- Checkout: `/tmp/alpha-v11-gate3-v4-slice3-repair-20261001`
- Review date: 2026-10-01 UTC; independent reviewer in a separate agent from the Sol/high author.

## Scope and validation

Read the predecessor CHANGES_REQUIRED review and its probes, the accepted transport/runtime design and review, and slice-2 carry-forward review. The design SHA-256 remains `0b121fbc422208e2fa89e0b2f7362725cac60115ada966ed83b9d1ed15ac8e4a`. Inspected the complete five-file repair diff: runtime, ledgers, budget consume change, and both changed test modules. Candidate and main were not edited. Candidate was clean before and after validation. No provider, network, service, or financial action occurred.

Executed against the exact candidate:

| Check | Result |
| --- | --- |
| Runtime, ledger, launch focused tests | 186 passed, 10.82 seconds |
| Entire `tests/test_v11_r09_gate3*.py` family | 427 passed, 2 existing fork deprecation warnings, 33.94 seconds |
| Independent adversarial probes | 9 passed, 1.02 seconds; each passing test reproduces a defect |
| `git diff --check 8e446fd..ef45d35` | Clean |

The independent probes are `/tmp/alpha-v11-slice3-review-ef45d35-probes.py`. Run:

```bash
PYTHONDONTWRITEBYTECODE=1 /home/alphaadmin/AlphaV11_Dev/venv/bin/pytest -q -p no:cacheprovider /tmp/alpha-v11-slice3-review-ef45d35-probes.py
```

Predecessor probes were read as immutable evidence. They were not blindly rerun: the repaired constructor and transport signatures changed. The independent probes above target the repaired API. No full release suite was repeated.

## R1–R8 disposition

| Prior finding | Disposition |
| --- | --- |
| R1: deadline / known closure | Substantially repaired: fixed persisted deadline, read allowance, explicit framing/close, deadline hold and post-fsync recheck. Error-path accounting remains incomplete (F2). |
| R2: durable clock / elapsed state | Partially repaired: pre-dispatch validation, elapsed deadline and observed offset interval survive reopen. Store-phase clocks bypass the session intersection; shared boot is omitted (F1, F4). |
| R3: post-close pacing | Closed for this offline slice: persisted closure sample plus frozen interval controls later dispatch, including reopen. Both delayed transport and restart regression tests pass. |
| R4: denial classification / receipt | Partially repaired: any Retry-After, header receipt sample, retained raw evidence, date parsing, and duplicate rejection are present. Elapsed-expiry observations are lost and resumption uses unsafe time bounds (F2, F3). |
| R5: frozen plan / journals / capture | Partially repaired: exact request equality/order, plan digest pin, common manifest checks and capture records exist. This is not a validated V4 manifest projection and all four journal contexts are not bound (F4). |
| R6: prefetched overdelivery | Normal well-formed overdelivery now charges the whole eager tuple and remains held. Rejected header/clock paths still omit already delivered bytes (F2). |
| R7: denominator / report reserve | Partially repaired: durable captures drive reports; real allocation on initial creation, bounded output and completion digest exist. Fields can disappear, refusals inflate attempts, and reopened reserves need not be physically allocated (F5, F6). |
| R8: prospective capacity | Partially repaired: nonzero resource subtraction and admission checks exist. Counts use optimistic chunk and session-event estimates (F7). |

## Blocking findings

All implementation line numbers refer to `tools/v11_r09_gate3_runtime.py` at the exact candidate unless another file is named.

### F1 — P1 — Store-phase clocks can violate the global intersection and still produce COMPLETE

Lines 992–1013 take body/decode/seal samples directly through the store and create a fresh per-receipt `ClockSequence`. Those observations never pass through the durable session `observe_clock` path at lines 691–694.

`test_store_clocks_not_in_session_intersection` records the first transport at offset 0 ±0.05 seconds, then its store phases at +0.08 ±0.05. Its individual receipt is valid. The session incorrectly retains [-0.05, +0.05], although incorporating the store evidence would narrow it to [+0.03, +0.05]. The next request uses -0.08 ±0.05; it also returns SUCCESS. The complete set of retained receipt clocks has an empty offset intersection, yet `finalize_report()` returns COMPLETE.

Persist/check every original session sample, including local decode and store seal, against the same durable interval. Separate acquisition-window checks from clock-validity checks so legitimately late local work remains diagnostic without discarding session clock constraints. Replay/report validation must reject the contradictory retained evidence.

### F2 — P1 — Error paths discard observed denial and already delivered body bytes

Lines 757–765 and 861–871 validate headers and clock/window state before the recovery accounting path can retain known observations. The eager boundary explicitly says its entire tuple is delivered at dispatch.

- `test_elapsed_expiry_loses_observed_denial_and_prefetched_bytes`: elapsed cap 1 second, dispatch advances 2 seconds and returns a framed 503 with Retry-After and four body bytes. `_account_prefetched_on_deadline` raises `RUNTIME_ELAPSED_DEADLINE` before recording the denial or accounting. Durable known bytes are 0 and denial history is empty.
- `test_bad_header_loses_all_eager_overdelivery_bytes`: duplicate ETag plus 31 returned body bytes against reservation 10 raises `RUNTIME_DUPLICATE_HEADER`; received bytes remain 0 and the budget is not marked violated.

Both cases correctly keep the open shared intent and full reservation; this does not restore missing observations or overdelivery accounting. Record every known delivered byte even when semantic/header/clock validation fails, retain uncertainty where measurement cannot be trusted, and record observable denials with bounded evidence or an explicit missing-evidence cause. Keep the hold; do not release accounting to repair this.

### F3 — P1 — Cooldown can resume before the conservative UTC lower bound reaches expiry

Lines 800–801 and 824–825 pass UTC + uncertainty to `is_blocked`; line 814 passes nominal UTC to the shared open check. The proof of expiry needs UTC - uncertainty.

`test_cooldown_dispatch_uses_nominal_time_not_receipt_lower_bound` imports a synthetic completed denial with expiry 1000. A new-window request at UTC 1000 ±0.05 dispatches and succeeds although the shared ledger still reports blocked at the valid conservative time 999.95. The new-window clock itself is valid. Receipt upper bounds are appropriate when computing expiry, but resumption requires the lower bound to have reached it.

Use the conservative lower bound consistently at every pre-dispatch/open restriction check, and retain the original-window no-retry rule.

### F4 — P1 — FrozenPlan is not a validated manifest projection; shared journal boot is omitted

Lines 378–451 validate hashes of caller-constructed requests and a five-field review summary. They never resolve or validate the named V4 manifest, prove the schedule is its exact projection, or enforce its mandatory FIELD prerequisite/mapping relationships. The transport still receives only a request ID (lines 854–856), not the bound immutable request intent.

The independent successful FIELD probe supplies no INDEX/OBJECT_ID prerequisites and no original slot identity; the runtime accepts it. Existing V4 validation requires the overhead/prerequisite relationships (`tools/v11_r09_gate3_launch_v4.py:510`), but nothing connects its validated output to this runtime constructor. A matching opaque manifest digest across journals does not prove that the request schedule came from those manifest bytes.

Additionally, lines 589–590 compare only session/budget/store boots. `test_shared_journal_boot_is_not_bound_to_other_three` constructs a shared journal under `different-boot` and the other three under `boot-a`; runtime dispatch and SUCCESS are accepted.

Construct the runtime plan from exact validated V4 bytes and their pinned review context, verify all four journal identities/policies/boots against that context, and supply the injected boundary with the immutable bound intent. Source qualification and real networking may remain separately gated.

### F5 — P2 — Successful fields disappear from the denominator; refused requests count as attempts

Lines 327–329 allow FIELD `slot_index=None`; lines 1134–1135 silently omit such a request. `test_successful_field_can_disappear_from_full_denominator` obtains a successful four-byte FIELD receipt. The report then says `raw_completed_count=1` while all 2,713 rows have `request_id=None` and NEVER_ATTEMPTED. Integer-row uniqueness alone does not establish the original-slot partition.

Lines 1163–1172 and 1225 derive attempted totals from session intents rather than actual budget reservations. `test_refused_entry_inflates_attempted_accounting` refuses before any transport/shared open/budget reserve, but both global and INDEX attempted counts are 1 while `budget.count=0`.

Require every FIELD to map exactly to its frozen original slot, validate report row coverage against that mapping, and derive actual attempted counts from budget reserve events. Report refusals separately. The plan/report also still lack the frozen primary-reason precedence and retained multiple reasons required by section 6; `primary_provider` in an event is not a primary-reason precedence definition.

### F6 — P2 — Reopened report reserve need not contain reserved physical capacity

Lines 1262–1274 accept an existing regular private file with the right logical size and link count, then set `reserved=True` without proving or acquiring its physical allocation. Initial creation does call `posix_fallocate`.

`test_sparse_report_reserve_is_accepted_as_preallocation` creates a 16 MiB sparse file with `st_blocks=0`. Reopening `ReportSink` reports `reserved=True` and leaves it at zero allocated blocks. Runtime accepts that flag as its mandatory acquisition prerequisite. Validate/reestablish the actual allocation under the lock, with suitable failure handling and descriptor/name checks, before declaring the reserve ready.

### F7 — P2 — Capacity preflight assumes maximum-size chunks and too few session events

Lines 236–244 use `ceil(reservation/65536)` as a worst-case chunk count although the synthetic interface permits smaller chunks. Lines 606–608 allow only 12 session records per remaining request despite the new clock/deadline/capture events.

`test_tiny_chunks_exceed_frozen_capacity_estimate` delivers 32 one-byte chunks successfully. It adds 34 budget records against a frozen estimate of four; the successful session also exceeds the admitted per-request session-event estimate. Larger valid responses can hit the independent event caps long before the admitted byte allowance. Existing hard journal caps still hold; the claim that the frozen subset's worst-case record capacity was checked before dispatch is false.

Define an enforced delivery/event policy or conservatively budget all admissible chunk patterns up to the hard limits. Count every clock/deadline/capture/completion record and required dependency/report allocation, including remaining capacity on reopen. Keep exact-floor tests, but derive their expected costs independently of the production formula.

## Disposition

Retain `ef45d35` unmerged. Repair the outstanding obligations in the isolated offline branch, preserve these review probes, and obtain another independent exact-commit review. Do not treat the passing candidate suite as R1–R8 closure. The explicit ledger schema-version boundary is appropriate; no migration should be inferred.

No launch/source qualification, real-clock adapter, networking, genuine GRIB replay, or historical feature-use proof was supplied by this review. **G3-L NO-GO; NOT_READY_TO_FUND.**
