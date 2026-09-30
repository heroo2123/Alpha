# Independent strict Gate 3 offline repair review — 1693dd5

**PASS for the five P2 offline repair findings only. Candidate remains unmerged.**

On 2026-09-30, Astra/high independently reviewed the Sol/high repair branch
`r09-gate3-strict-offline-20260930` at exact commit
`1693dd5b1cce5c0bbc857fed2c0e475d27bcc8ad`, tree
`09840fc34f569202e6f820d78fbd80cc13cf6a17`, in
`/tmp/alpha-v11-r09-gate3-strict-offline-20260930`. The worktree was clean
and unchanged by review. The baseline findings are in
[the dc7f83b review](V11_R09_GATE3_STRICT_REVIEW_dc7f83b.md).

## Review sequence and evidence

- The author converted the independent defect probes to rejection tests and
  committed `ebebad9`. The first independent review reproduced three residual
  defects: a field range over its provider cap, URL paths that could change the
  frozen origin or encode traversal, and a journal directory that could lose
  private mode without stopping reservations.
- The author repaired those defects at `e8cf57b`. The second independent
  review confirmed all three repairs, then reproduced a delivered-byte
  accounting gap when journal privacy failed during an in-flight request.
- The author repaired that gap at the exact commit above. The affected offline
  Gate 3 launch and collector suites passed **95 tests**. The final independent
  review repeated **12 failure scenarios** across directory privacy, journal
  and lock modes, privacy changes between checks, and write/fsync failures,
  with ordinary and oversized body receipts. **120 independent assertion
  groups passed**. The reviewer found bytes counted exactly once, uncertainty
  and reservations retained, permanent refusal after failure, and safe
  reopening with uncertain reservations held. A durable stream-violation
  control recorded its count without false uncertainty.

The repaired validator now uses an exact native-cycle candidate inventory,
strict scalar types and a hash of the provided 2,713 slots. Planned requests
bind provider, origin, canonical path, object/index/cache identities, range,
reservation and compatible earlier prerequisites. The bounded serial schedule
includes start pacing, deadlines, processing and finalization. Pinned TZif bytes
govern local-day calculations and must match the named host zone bytes. The
journal validates private single-link files and directory identity, fails
closed on persistence or privacy loss, and reports known delivered bytes even
when sealing fails.

## Scope limit

This PASS accepts the **offline repair** only. Artifact references still need
typed semantic review and genuine evidence; transport, full-field decoder,
clock integration, a private exact-digest G3-L package, capture and forward
SHADOW evidence remain open. The review grants no network launch, merge,
promotion or financial authority. The separate Brain candidate `a8362ad`
remains unmerged and has its own independent review path. The score remains
**91/200 (45.5%), formal 1/50; NOT_READY_TO_FUND**.
