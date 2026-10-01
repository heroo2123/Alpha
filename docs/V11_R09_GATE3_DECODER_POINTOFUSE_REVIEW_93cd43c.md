# Decoder MEMFS point-of-use exact review — 93cd43c

**Verdict: POINTOFUSE_REVIEW_CHANGES_REQUIRED. Do not merge this candidate.**
Independent GPT-6 Astra/high review of commit
`93cd43c411de696dd575efc7038eca723ed1e41a`, tree
`3bc648294df7db5e10b85208cc2cf101b9702c13`, in the clean author worktree
`/tmp/alpha-v11-gate3-decoder-pointofuse-20261001`.
The reviewer did not edit candidate files. Review time: 2026-10-01T21:51:31.019507+00:00.

The four added files, their author-terminal hashes, source parent `95f0339`,
and clean worktree were independently checked. Only Markdown/JSON differ
between that source parent and the reviewed tip. All four author hashes match.
The accompanying [machine evidence](V11_R09_GATE3_DECODER_POINTOFUSE_REVIEW_93cd43c.json)
binds this report, complete reproduction, reviewer programs and failure controls.

## Reproduced evidence

The exact candidate ran with the development Python interpreter, bytecode and
core dumps disabled. A reviewer-only inherited native seccomp filter denied
`socket` and `connect`; IPv4/IPv6 socket denial was checked before the probe.
No provider or other network request was made. Compilation was restricted to
the candidate temporary shim; no installed library/package was changed.

Both author negative probes reproduced (wrong library: SIGABRT/-6 and FATAL;
no preload: successful decode, no trace). Main capture returned zero with empty
stderr. All 176 records have unique contiguous indices 1–176. Independent raw
reads, without relying on the candidate's cross-check function, compared all
86 stream dumps byte-for-byte to their static-inventory library offsets,
lengths and hashes. All 90 presence results agree. The complete cross-check
object equals the author's: 71 distinct opened paths, 90 distinct existence
paths, no mismatches. Template 5.42 is opened at call 169. Dumps total
180,691 bytes; the largest is 15,150 bytes.

The installed library remains 39,767,864 bytes, SHA-256
`f984057845fd0a569dd775907d4629b83b09434959436863790492aae32e5fb9`.
Independent `dladdr` inspection resolves both real symbols to that MEMFS
library (the loader spells its path with `/lib64/../lib64/`). A separate run
without the shim reproduces simple packing and both IFS/AIFS synthetic
CCSDS results: template 42, 290.0 K, 205/208-byte messages respectively.
This validates those outputs, not universal instrumentation transparency.

Actual resource inspection after the child's function confirms CPU soft/hard
limits 60/60 seconds and address-space limits 536,870,912/536,870,912 bytes.
The code also applies 90-second wall timeout to main capture, 60-second timeouts
to compilation and each negative subprocess. Those negatives/compiler do not
receive the child's CPU/address-space limits from this candidate. Core=0 and
native network denial are reviewer wrapper properties, not built-in candidate
guarantees. The Python socket replacement alone is not a native network sandbox.

## Required corrections

### R1 — P2: trace write failures are silent; incomplete traces remain consistent

In `_shim.c` lines 87–90, 119–122 and 132–135, `fprintf` and `fflush` results
are ignored. This contradicts the fail-loud observer contract. With the genuine
shim and installed library, setting only the trace destination to `/dev/full`
returns `DECODE_OK`, exit 0, empty stderr, no FATAL. Dump bytes still exist.
The normal main parser would reject that completely empty trace, so this
control alone is **not** a demonstrated positive main result.

The separate suffix-loss control copies the genuine successful capture but
removes calls 170–176. Replaying those 169 records through unchanged `main()`
reporting/cross-check logic (reusing the genuine child result and prior negative
results) still returns zero and `POINTOFUSE_OBSERVED_CONSISTENT`. This is an
explicit reviewer-injected replay, not an observed disk failure in the normal
run. Together the controls show that silent trace loss can evade the claimed
complete observation: current checks require merely some opens, some exists
and template 5.42, with no successful completion/count binding.

Check every trace write/flush and abort on error. Add a successful end-of-capture
record/count or equivalent independently checked completion binding, validate
record types and contiguous unique indices, and reject truncated captures.
Exercise a failed trace sink and missing suffix/completion marker with bounded
negative controls. Do not hardcode 176 as a universal ecCodes call count.

### R2 — P2: claimed pre-use source check occurs after execution

`_probe.py` lines 223–228 load and execute the sibling module and its decoder
function; its first `HEAD` comparison appears at lines 234–236 afterward.
Both the probe docstring and Markdown claim the sibling is hash-checked first.
A reviewer-only harmless alternate sibling raising
`UNREVIEWED_SIBLING_EXECUTED_BEFORE_HASH_CHECK` executes with zero `_git` calls
before the exception. No candidate or installed file was modified in this test.
The clean exact run's source hashes are correct, but the claimed pre-use
protection does not exist.

Validate the sibling against the pinned reviewed commit/blob before import or
execution, fail before executing mismatched bytes, and add a harmless mismatch
control proving that ordering. Keep the post-run identity observation if useful.
This is a local exact-source check, not authenticated upstream build provenance.

### C1 — P3: narrow inaccurate scope statements in the probe/shim comments

The probe introduction says it builds/modifies nothing although it compiles a
temporary shim and writes artifacts. It references a nonexistent companion
section. Its passivity discussion and the C header overstate general unchanged
decode behavior: file hashes and two output fixtures cannot prove that. Match
the Markdown's existing caveat: only the captured bytes, restored position and
reported outputs were checked. Explicitly state that candidate socket blocking
covers Python `socket.socket` only; native network denial came from this review.
Do not claim compiler/negative resource limits that are not applied.

## Acceptance boundary and next action

The clean run is useful installed-byte observation, but R1/R2 prevent accepting
this reproducibility harness as documented. Repair only its four observation
artifacts in the same isolated worktree, regenerate truthful evidence, commit,
then obtain fresh different-model review of the exact corrected commit/tree.
Newer-main reconciliation is required only after acceptance. No merge or remote
publication was performed by this review; no product code/test was changed.
The prior full release PASS at `6ec371e` remains valid historical evidence;
this observation review makes no new full-suite claim.

Missing authenticated source/build lineage, inconsistent wheel RECORD,
provider/CCSDS qualification and G3-L identity are unchanged. The recent
commissioning files are status/watchdog artifacts, not new forward SHADOW.
G3-L remains **NO-GO**; **91/200, formal 1/50; NOT_READY_TO_FUND**.
V10, AxiomTrade, protected authority, services and financial execution were not
modified. The protected FINAL-REVIEWED master hash remains
`a0e16d9bd7344c943a54a16a53c6757662363d93642f6e5cb7953cd047659b4a`.
