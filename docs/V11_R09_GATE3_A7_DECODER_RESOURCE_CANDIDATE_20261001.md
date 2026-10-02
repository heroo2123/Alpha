# Gate 3 A7 offline decoder/resource candidate terminal

**Status: readiness infrastructure only. A7 is unqualified; G3-L remains NO-GO.**
This candidate neither fills an identity inventory entry nor changes any launch
path. A2–A6 exact evidence, an A4 point-of-use lock of the full Python/native/data
closure, representative real-field and adversarial measurements on that locked
ABI, host reservation review, and fresh independent review are still required.

## Boundary supplied

`tools/v11_r09_gate3_a7_decoder.py` accepts only bytes, an explicit ECMWF IFS
or AIFS request identity, independently supplied SHA-256 pins, a station, and
resource limits. It makes no requests and has no launch adapter. Its parent
checks the decoder file, source identity, whole field, every non-payload
section, envelope, product, bitmap, grid/count, packing template, and station
distance before starting the worker. The worker repeats those checks before
decode. CCSDS additionally requires explicit SHA-256 pins for named native
files, including `libeccodes.so`; missing or changed files refuse before the
worker. The shared `ecmwf_grib` preflight uses the exact station decoder gates.

For template 5.42 the existing decoder calls ecCodes for a full field decode,
requires finite values and the exact expected count, sets those values on a
clone, and requires byte-for-byte equality with the original message. Simple
and IEEE fields retain their existing bounded station extraction. GEFS CCSDS,
complex, JPEG, and unknown packing remain closed in this runner.

The child starts with Python isolated mode, a minimal environment, core dumps
disabled, and process limits. Fixed caps are 4 MiB compressed input and
1,038,240 regular grid points under the existing decoder geometry cap.
Default configurable bounds are 15 CPU seconds, 20 wall seconds, 512 MiB
address space, 16 KiB output file, 128 MiB additional memory headroom, and
16 MiB additional disk headroom. Accepted ranges are 1–60 CPU seconds,
1–90 wall seconds (at least the CPU bound), 128 MiB–2 GiB address space,
1–64 KiB output, and 0–2 GiB for each additional headroom setting. These
are offline runner controls; zero additional headroom is accepted by its
interface. They cannot reduce existing launch-planner floors or reservations.

**This repair fails closed on memory admission on every deployment.** The
candidate has no trustworthy deployment/namespace boundary proving that every
effective cgroup ancestor is visible. `_available_memory()` therefore refuses
with `A7_HEADROOM_UNKNOWN` before staging or worker creation. Neither a `/`
root in namespace-relative procfs/mountinfo nor comparison with a potentially
namespaced PID 1 proves global visibility. There is no caller flag, environment
override, or namespace-inode allowlist to enable admission. A future separately
reviewed deployment integration must supply that proof before admission can
be enabled; this repair makes no namespace or root configuration changes.

`_visible_memory_headroom()` is diagnostic only. It takes the minimum of host
`MemAvailable` and each visible cgroup-v2 ancestor's limit minus usage. It
refuses incomplete visible controls and subtree mounts, but cannot detect
ancestors hidden outside a cgroup namespace. Even both controller files being
absent at the visible root does not establish completeness. Synthetic tests
explicitly substitute a known complete hierarchy or ample capacity to exercise
the downstream worker controls; those substitutions are not host admission
or evidence of physical capacity.

The downstream admission thresholds remain the configured address-space bound
plus memory headroom, and scratch disk for two maximum inputs, bounded output
and disk headroom. Limits and thresholds are unchanged. Capacity snapshots
would still not reserve resources or prevent concurrent consumption, membership,
limit or mount/path changes; trustworthy stability and physical reservations
remain integration prerequisites.

The parent is the sole owner of child reaping through PID-specific `wait4`.
Timeout sends SIGKILL with `os.kill` (without Popen polling), then reaps exactly
once, including when the child exits before the signal. `A7_WALL_LIMIT`
refusals retain full kernel rusage as `child_usage`. A child already exited when
the deadline is observed also refuses with that reason. The wait budget includes
time spent in process creation, although parent/process-creation work cannot
be forcibly interrupted by this wait loop.

Successful results report full reaped child user plus system `cpu_seconds`
and Linux `wait4.ru_maxrss * 1024` as `max_rss_bytes`, including serialization,
stdout writing and interpreter teardown. The earlier worker RSS sample has
been removed. RSS is a complete kernel high-water measurement and a post-exit
check against the configured memory bound; `RLIMIT_AS` remains the enforced
address-space limit, not a physical RSS reservation. `inner_cpu_seconds`
measures from just before input parsing through decode, before serialization.
Wall time is parent-observed through reaping; output size must be strictly
less than the configured file cap for success. Parent preflight, hashing,
input staging and scratch costs need separate full-envelope qualification.
These controls and historical observations cannot qualify realistic resource use.

The `Pins` evidence class is an explicit label. `SYNTHETIC` and
`RETAINED_UNVERIFIED` outputs cannot be promoted by a matching hash. Even a
`RETAINED_REAL` label is caller supplied and carries no acceptance authority;
the independently reviewed A2–A6 dossier must establish that fact outside this
runner. The runner checks one decoder source file and caller-named native files,
not the full A3/A4 dependency closure, actual mappings, or a race-free immutable
snapshot. That limitation prevents this
candidate from serving as runtime verification.

## Offline terminal

The parent `e08858b` terminal recorded targeted and adjacent tests using the
existing local development environment with ecCodes and no installation:

```text
python -m pytest -q tests/test_v11_r09_gate3_a7_decoder.py \
  tests/test_v11_model_panel.py tests/test_v11_r09_gate3_offline_io.py
181 passed in 18.49s
git diff --check: pass
```

The prior `313eeaf` candidate reported the same focused and adjacent files:
**192 passed in 8.00 s**, with pytest cache and bytecode writes disabled and
a unique bounded `/tmp` basetemp. Independent review of `313eeaf` found the
namespace-root admission gap, timeout double-reap race and incomplete RSS interval. This repair still requires fresh
exact-commit independent review.

The **synthetic** tests exercise wrong build/source/raw/section/provider,
truncated envelope, bitmap, grid/count, packed count, remote station, complex
and JPEG template, CCSDS exact round trip, self-consistent CCSDS payload
truncation, changed/missing native-file pin, absent/failing native decoder,
worker-start failure, nested and ancestor cgroup limits, an unlimited root,
missing/unreadable controls, an exact memory admission threshold, complete
child CPU accounting, and CPU/wall/address-space/output exhaustion.
An insufficient host-headroom check refuses before child creation. The ecCodes
fixtures generated by existing tests are tiny and provide no A7 qualification.

One already retained local full field at `/tmp/aifs_cf.grib2` was measured
offline. Its byte SHA-256 is
`42dcdd7b3d8f63c5353dcb2cbb91fc15a478d9cd92121ff8ad936d2b5be988e4`
and length is 621,131 bytes. It has an observed AIFS control 2t GRIB2 header,
CCSDS packing, 1,440 × 721 = 1,038,240 points, September 29 2026 00 UTC and
step 6. No independently authenticated object, index, range, release, current
run, or build provenance was available for that file, so it was labeled
`RETAINED_UNVERIFIED`. The diagnostic source pin used
`model_version=UNVERIFIED_LOCAL_OBSERVATION` and a zero release digest; neither
is a qualified identity. It was not added to the repository or used as a live
input.

```text
Python 3.12.3 (/usr/bin/python3.12); ecCodes API 2.49.0 (unlocked local install)
template 42; count 1,038,240; selected grid index 324382
full decode + exact re-encode: succeeded
inner CPU 0.407673045 s; wall 0.989880085 s; pre-serialization RSS sample 93,192,192 bytes
output 237 bytes; selected kelvin 288.98785400390625
decoder source SHA-256 a5fd93a02873a1082f45ac6674b08990cb54d0b1d95ca9b72e0b05d8885b03e4
local libeccodes.so SHA-256 1d6b8a3a8206cc439a3497dad4dc96370ed0b247798ac27e6c7bbd65cac4b87c
local libeccodes_memfs.so SHA-256 f984057845fd0a569dd775907d4629b83b09434959436863790492aae32e5fb9
```

This single observation cannot establish representative real-field or
adversarial maxima on the eventual locked ABI. Fresh exact-run measurements,
including a full resource envelope and host headroom, remain an A7 acceptance
gate after A2–A6.

The independent review of parent `e08858b` repeated the retained-field run:
inner CPU was 0.429719832 s and full child CPU was 0.510312 s. That review
identified the narrower interval in the original output. Neither run is a
qualified resource maximum.

## Repair of the `313eeaf` findings

Focused and bounded adjacent validation: **202 passed in 23.51 s**, no skips,
using the same three files, bytecode/cache disabled and a unique `/tmp`
basetemp. Test scratch was 2,286,261 bytes and was removed. The initial run
was 201 passed / 1 failed: the CPU exhaustion test reached its four-second
wall deadline before consuming one CPU second under contention. Its test-only
wall allowance is now 20 seconds; production limits are unchanged. Both logs
are retained in the author evidence.

F1 uses the fail-closed alternative explicitly allowed by the review; a trusted
complete-visibility mechanism remains a prerequisite, not an inferred fact.
F2 preserves timeout refusal and exact per-child accounting across live-at-kill,
exited-before-kill, ESRCH and already-reaped-by-wait4 deadline cases. F3 collects
full kernel peak RSS; real synthetic worker regressions inject bounded resident
memory during serialization and at interpreter shutdown, then compare the
reported peak with independently captured reaped usage.

The author evidence at `/tmp/alpha-v11-gate3-a7-repair-313eeaf.author.json`
binds the exact candidate commit/tree, test results, reproduced parent defects,
updated probes and log hashes. Parent defects were reproduced with the original
independent probe logic before editing. Updated probes and tests are author
validation, not independent acceptance. No retained real decode is admitted by
this candidate without a future reviewed complete-visibility boundary. Earlier
retained measurements above are historical and remain `RETAINED_UNVERIFIED`.

A2 source/build lineage (including all 25 eckit RECORD discrepancies), A3 full
reproducible dependency lock, A4 immutable point-of-use execution/data closure,
A5 authenticated provider/release/access/semantics, and A6 genuine current-run
object/index/range/causal evidence remain open. A7 still needs that exact qualified
integration, representative retained fields and adversarial maxima on its locked
ABI, parent/scratch accounting, stable effective capacity and physical
reservations, all refusal controls and fresh independent review. A8 launch
composition, the remaining identity inventory, canonical G3-L acceptance and
FINAL reconciliation remain separate. No real provider/network request before
G3-L PASS. **A7 remains unqualified; G3-L remains NO-GO.**
