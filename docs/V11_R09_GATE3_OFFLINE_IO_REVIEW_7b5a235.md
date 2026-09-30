# Independent Gate 3 offline I/O repair review — 7b5a235

**PASS for the five P2 repairs, within the offline interface scope. All holds remain.**

Astra/high independently reviewed Sol/high commit
`7b5a23582d444c0c499faec63b6afd8872a3e90d`, tree
`3ff45d661fcd4c7db0faa536a9aae2152b992c6a`, against
[the rejected a804034 review](V11_R09_GATE3_OFFLINE_IO_REVIEW_a804034.md).
The held candidate worktree `/tmp/alpha-v11-r09-gate3-strict-offline-20260930`
remained clean and unchanged. This verdict clears the five repair findings;
it does not authorize merge, publication, capture, G3-L, SHADOW or learners.

## Independent evidence

- Affected offline I/O, strict launch, collector and GRIB suites:
  **176 passed in 3.13 seconds**.
- [Independent synthetic acceptance probes](V11_R09_GATE3_OFFLINE_IO_REVIEW_7b5a235_probes.py):
  **53 passed in 0.71 seconds**. These assert repaired behavior, unlike the
  prior defect-reproduction probes. They reuse the prior independent fixtures,
  invert the relevant defect expectations, and extend publication/reopen cases.
- Logs: `/tmp/alpha-v11-gate3-io-review-7b5a235/{affected,independent}.log`.
  [Completed terminal](V11_R09_GATE3_OFFLINE_IO_REVIEW_7b5a235_terminal.json)
  binds exact commit/tree and report/probe/log hashes.
- Reproduce from the exact candidate worktree using `PYTHONPATH=.` and
  `/home/alphaadmin/AlphaV11_Dev/venv/bin/python -m pytest -q`, with the absolute
  probe path. Tests use only disposable synthetic stores, bytes and subprocesses;
  bounded child cleanup terminates only children created by the probe.
- Candidate diff check clean. No candidate code changed during review. The
  accepted `6ec371e` release result remains current; no full-suite rerun was
  warranted for this bounded repair review.

## Resolution of the five findings

| Prior finding | Independent resolution |
| --- | --- |
| P2-1: swapped grid increments | Correct Di/Dj byte offsets. Independent rectangular fixtures decode 293 K at zero displacement in all four supported scan modes; reversed-endpoint fixtures reject. Square-grid controls pass. Author tests additionally cover both simple and IEEE packing. |
| P2-2: incomplete causal clock checks | Every phase upper bound must precede the decision lower bound. The earlier-receipt counterexample rejects; separately raising each of the four phase bounds also rejects. Globally inconsistent pairwise-compatible offsets reject. Stable sequences and inclusive boundary controls pass. |
| P2-3: uncertain publication readable/reusable | Any uncertain publication poisons the current instance. Remaining bytes/names are preserved; nonempty reopen refuses both reads and seals. Independent fault and process-exit cases below pass. |
| P2-4: FIFO blocks before validation | O_PATH file-type inspection precedes data open, with O_NONBLOCK and inode verification for the subsequent open. Digest names replaced by FIFOs, symlinks and directories reject promptly in bounded child probes. Preexisting nonregular names also trigger the nonempty-reopen refusal. |
| P2-5: malformed ETags accepted | Exact equality now requires a single bounded strong entity-tag syntax. Wildcard, list, unquoted, weak, control, DEL, embedded-quote and out-of-byte-range values reject. Empty, ordinary, backslash and obs-text controls pass. |

The grid offsets agree with [ECMWF template 3.0](https://codes.ecmwf.int/grib/format/grib2/templates/3/0/).
The strong-tag syntax agrees with [RFC 9110 section 8.8.3](https://www.rfc-editor.org/rfc/rfc9110.html#section-8.8.3).
Only public format documentation was consulted; no provider weather data was acquired.

## Store durability and reopen adjudication

Eight independent fault cases cover first/last directory fsync, link failure
before/after publication, unlink failure before/after removal, and failed
pre-publication cleanup unlink/fsync. Every uncertain instance refuses further
reads and seals even after fault removal. Where names remain, bytes survive and
reopening refuses use without altering those names. A file-fsync failure with
successful temporary cleanup allows a safe retry before any publication.
Duplicate-name refusal preserves the existing object and leaves a healthy
session usable.

Five disposable subprocesses exit immediately after file fsync, link, first
directory fsync, temporary unlink, or final directory fsync. Every remaining
nonempty store refuses reuse. This tests process interruption and state handling,
not physical power-loss persistence. Additional reopen probes cover a successful
object, unfamiliar temporary bytes, FIFO, symlink and directory; all refuse
without cleanup. Delivered-chunk journal write/fsync failure controls still
count received bytes once and hold the reservation across restart.

**The conservative refusal is acceptable for this offline repair.** A successful
store also becomes unusable after close/reopen: success provenance and original
seal clocks exist in no durable recovery record. Do not interpret this PASS as
restart readiness. If pre-publication cleanup removed all names but its directory
fsync failed, the original instance is poisoned; an empty reopen may create fresh
objects. It admits no prior object or clock. If a name survives, reopen refuses.

The next store work needs a separately reviewed recovery design: exclusive
runtime ownership, durable successful-seal provenance, reconciliation of uncertain
names, and preservation of original seal-clock evidence without fabricating
causal eligibility. No orphan reclamation or real-store alteration is authorized
by this verdict. The present interface remains a held synthetic component.

## Boundaries and durable state

Real transport enforcement, external clock attestation, source-specific semantic
pins/dossier, private launch manifest, runtime integration, G3-L acceptance and
Gate 4 learner admission remain open. The exact routed review is complete; no
implementation worker was needed or left running, and no duplicate was launched.
GEFS forward SHADOW remains owner/root-gated. Only watchdog status artifacts are
newer than the 06:43 commissioning evidence; there is no new forward sample.

Read-only safety verification: V10 demo, PAPER scanner and controller inactive /
disabled; V11 execution inactive / masked. `/etc/alpha-v11` and protected model
authority remain absent. FINAL-REVIEWED master SHA-256 matches its pin. SHADOW
worktree is clean. Host had 4.4 GiB disk free and about 1.0 GiB available memory.
No V10/AxiomTrade/service/authority/financial or publication action occurred.
Main recovered at `477a99d`; this review only adds evidence and ledger entries.
No C/J/E/A crossing: **91/200 (45.5%), formal 1/50; NOT_READY_TO_FUND**.
