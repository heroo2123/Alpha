# Independent exact-commit review — Gate 3 transport/runtime design (7e132a0)

**PASS for offline design only; review COMPLETED. No implementation authorized.**
Sonnet/high independently reviewed the Astra/high author's exact commit
`7e132a02ae0fcceed5e3fea65f7b86bed4a92c23`, tree
`89332f46dfc6b2111fe331fca54467cce96a3895`, in the primary worktree
(`/home/alphaadmin/AlphaV11_Dev/Alpha`), 2026-10-01. The worktree was clean
before and after this review; no document or source byte was changed. This
review covers only the design document's claims and internal consistency; it
is not acceptance of any V4 schema, transport, or implementation, none of
which exists yet. No network request, provider contact, or executable change
occurred.

## Evidence and scope

Verified by direct inspection, not trusted from the author's completion
record:

- `sha256sum docs/V11_R09_GATE3_TRANSPORT_RUNTIME_DESIGN.md` =
  `0b121fbc422208e2fa89e0b2f7362725cac60115ada966ed83b9d1ed15ac8e4a` —
  matches the author's `document_sha256` exactly.
- `sha256sum` of all four cited source files (`tools/v11_r09_gate3_launch.py`,
  `tools/v11_r09_gate3_collector.py`, `tools/v11_r09_gate3_offline_io.py`,
  `tools/v11_r09_gate3_store_v1.py`) matches the author's `source_sha256`
  entries exactly, byte for byte.
- Git ancestry: `f11541c` (tree `accb47f9...`) is a real ancestor of `01dfd73`,
  which is the direct parent of `7e132a0`, matching the document's declared
  base lineage. `6ec371e` is an ancestor of current `HEAD`. Commits `e563e45`
  and `15e99bd` exist as real commit objects.
- Private master hash: `sha256sum` of the FINAL-REVIEWED master file in
  `/home/alphaadmin/AlphaV11_Private/Alpha_V11_Codex_Private_Inputs/` equals
  `a0e16d9bd7344c943a54a16a53c6757662363d93642f6e5cb7953cd047659b4a`, the pin
  in `CLAUDE.md` and the document. No private content was copied or quoted
  here beyond this hash-only check.
- Full read of `tools/v11_r09_gate3_launch.py` (932 lines),
  `tools/v11_r09_gate3_collector.py` (736 lines),
  `tools/v11_r09_gate3_offline_io.py` (492 lines), and
  `tools/v11_r09_gate3_store_v1.py` (726 lines) at this exact commit, each
  cross-checked line by line against the document's six required areas below.
  No executable or test change was made or is implied by a documentary PASS.

## 1. V4 endpoint mappings (Section 1 table, Section 2)

Confirmed accurate. `validate_manifest`'s schedule loop (`launch.py:568-625`)
derives `object_id`/`index_id`/`cache_id` from one shared `path_template`
regardless of purpose (INDEX/OBJECT_ID/METADATA/FIELD all resolve the same
formatted path for a given slot), and `network['path_templates']` is one
template per provider (`launch.py:513`). The document's claim that V3
"cannot describe an actual separate `.idx` or official metadata endpoint" is
exactly what this code shows — there is no second URL/path for overhead
purposes distinct from the field object's own path. The proposed V4
`sources/network` closed group (distinct endpoint IDs, per-purpose
constrained path templates, explicit index/object/metadata mapping evidence)
is a real, correctly motivated gap closure, not a loosening: V3's existing
checks (cohort/source/time/network/limits groups) are explicitly retained.

## 2. Durable denial history (Section 3)

Confirmed accurate. `RestrictionLedger` (`collector.py:442-485`) is
in-memory (`self._records = []`) with a `resume()` classmethod that verifies
a caller-supplied content hash before trusting rehydrated records — exactly
the "in-memory record set with hash-checked restoration" the document
describes, not a durable multi-job service. Its `is_restricted(origin, *,
now_utc, window_end_utc)` takes the comparison window as a caller argument
rather than storing per-record expiry, which is a real limitation the
proposed V4 durable denial record (storing "original clock evidence, parsed
Retry-After and original window end" directly) correctly fixes. The 401/403
→ `scope='ORIGIN'` vs. 429/503/Retry-After → `scope='WINDOW'` distinction
already enforced by `RestrictionRecord.__post_init__`
(`collector.py:433-434`) is consistent with the document's crash/cooldown
rules in Section 3 (permanent-until-reviewed-resumption vs.
window-bounded).

## 3. Purpose budgets (Section 2 table, Section 4)

Confirmed accurate and non-loosening. `DurableBudget` (`launch.py:669-933`)
tracks only global `reserved`/`received`/`count` with no purpose dimension at
all — the document's claim that it "does not enforce... purpose semantics"
is exactly right, and the proposed per-purpose `charged(p) = delivered bytes
on completed attempts + full ceilings on unfinished attempts` is new,
additive accounting, not a replacement of the existing global check. Byte
ceilings cross-checked exactly: INDEX ≤3,145,728 matches
`launch.py:533` (`INDEX_CAP`, 3 MiB); FIELD GEFS/IFS/AIFS ≤2,097,152 /
≤4,194,304 matches `FIELD_LIMITS` at `launch.py:29-30` exactly; the proposed
OBJECT_ID/METADATA/PROBE ≤4,194,304 cap is strictly tighter than V3's current
unbounded-up-to-1 GiB `limits['metadata']` field (`launch.py:540-541`), so it
tightens rather than loosens. Global ceilings ≤3,600 requests and
≤1,073,741,824 bytes match `MAX_BYTES = 1024**3` and the `3600` request cap
used throughout `launch.py` (e.g. lines 525, 680).

## 4. Crash ordering (Section 4)

Confirmed internally consistent and correctly conservative. The six-step
order (`ATTEMPT_INTENT`→shared `INTENT_OPEN`→`BUDGET_RESERVED`→
`DISPATCH_INTENT`→transport→chunk accounting→`TRANSPORT_CLOSED`/
`INTENT_CLOSED`→`budget.complete`→`ACCOUNTED`→store seal→`OBJECT_WITNESSED`)
is a write-ahead-log discipline: each durable record is persisted strictly
before the action it authorizes, so a missing record proves the action never
happened, while a present record never proves the action succeeded — this
matches the existing `DurableBudget.reserve`/`consume`/`complete` pattern
(persist-then-mutate-state via `_append` before `_state()`,
`launch.py:860-920`) and the existing store's PREPARE-before-write,
COMMIT-after-fsync pattern (`store_v1.py:644-687`). The claim "complete
releases only unused reservation... not semantic success" matches
`DurableBudget.complete` exactly (`launch.py:915-919`): it only requires
`in_flight == key and not violated`, independent of response validity. The
claim that `complete()` cannot proceed after a stream violation is confirmed
by the explicit `not self.violated` guard. No step depends on information
that would not yet be durable at that point in the sequence.

## 5. Absolute window, pacing and measured time (Section 5)

Confirmed accurate, including the feasibility-arithmetic claim. S=14:00 UTC
and A=17:00 UTC (start+3h) and the decision point at start+4h=18:00 UTC
exactly match `PILOT_WINDOW_FIXED`'s check (`launch.py:396-399`). The
document's `D` is used only as an upper-bound ceiling for post-A work
(`decision_lower_utc`, which the schema already requires ≤ the fixed 18:00
`decision_utc`), which is the conservative direction and does not contradict
the code. The claimed V4 feasibility inequality
`N * request_deadline + (N-1) * start_interval + processing + finalization
<= elapsed cap` is algebraically verified strictly stricter than V3's actual
`(N-1) * max(interval, deadline) + deadline + processing + finalization`
(`launch.py:640-645`): since `interval + deadline > max(interval, deadline)`
whenever both are positive (true here, `deadline>0`, `interval>=2`), the V4
sum is strictly larger for any N>1, confirming the document's claim without
reproducing the author's untested numeric example independently (no
executable change was made to verify this; the inequality was checked
symbolically).

## 6. Receipt composition (Section 6)

Confirmed accurate. The proposed `CaptureReceiptV1` is a new session-level
record that wraps and references, but does not replace, the existing
`ObjectReceipt`/`ObjectProvenance` in `store_v1.py`. The 256-dependency cap
the document cites matches `ObjectProvenance.checked()` exactly
(`store_v1.py:188-189`), as does the RAW-vs-FEATURE_MANIFEST
dependency-count invariant (`store_v1.py:194-195`, `(kind ==
'FEATURE_MANIFEST') == bool(dependencies)`). `ObjectProvenance` indeed carries
no `purpose` field, confirming the document's statement that purpose is
retained in the session receipt, not the store receipt. The claim "store's
post-object-fsync sample precedes COMMIT fsync... an on-disk COMMIT therefore
does not prove a historical decision had the receipt" is confirmed by
`seal_with_provenance`'s actual order (`store_v1.py:644-687`): object data is
written and fsynced, then `recorder()` is invoked to obtain the `final` clock
sample used in `COMMIT`, and only then is `COMMIT` appended/fsynced — exactly
the ordering described. The recovered-receipt `acknowledgement='UNKNOWN'`
claim matches `_replay`'s `COMMIT` handling exactly (`store_v1.py:512-513`).

## Controls that work and limits of this review

Document bytes, all four cited source digests, cited commit/tree ancestry,
and the private master hash pin all verify exactly as claimed — nothing in
the completion record was fabricated or stale. No contradiction, no
loosening of any existing V3 check, and no gap between the document's
characterization of current code and the code's actual behavior was found
across the six required areas. This is a documentary-consistency and
source-compatibility review only: it did not execute code, construct a V4
schema, or exercise any crash/timing scenario empirically, because none of
that exists yet to run. It does not evaluate whether the proposed V4 schema
is *sufficient* for every acceptance case in Section 7 of the document —
only whether what the document claims about existing code and arithmetic is
true, and whether its proposals are self-consistent and non-loosening. That
deeper sufficiency question is explicitly deferred to the Section 7
acceptance-case work during implementation, as the document itself states.

## Disposition and next action

Design accepted for implementation purposes only: **PASS, offline design
only**. No provider requests, G3-L, SHADOW, learner, publication, root
authority, or financial permission follows from this review. Per the
document's own Section 7, implementation may now proceed one bounded offline
slice at a time in an isolated worktree (schema/budget replay first, then
durable ledgers with a crash matrix, then transport/clock/resource state
machine and receipt/report composition), each slice requiring focused tests
and a fresh different-model exact-commit review before integration. No
concrete socket adapter, live preflight, or service wiring belongs in those
batches.

Main is clean at `7e132a0`, 25 ahead/0 behind its local upstream tracking
ref (no remote refresh or push performed by this review). No Gate 3 worker
was active during this review. V10 is untouched; AxiomTrade was not touched
(a separate, unrelated Opus session on this host is working on AxiomTrade
independently). Protected authority directories `/etc/alpha-v11` and
`/var/lib/alpha-v11` remain absent. The FINAL-REVIEWED master hash matches
`a0e16d9bd7344c943a54a16a53c6757662363d93642f6e5cb7953cd047659b4a`. Disk
about 4.0 GiB free; memory about 831 MiB available. No service, authority,
credential, financial, or publication action was performed. No C/J/E/A
boundary crosses from a design review: **91/200 (45.5%), formal 1/50;
NOT_READY_TO_FUND**, unchanged.
