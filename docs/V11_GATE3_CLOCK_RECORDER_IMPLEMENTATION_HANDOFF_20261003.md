# Gate 3 passive clock recorder: implementation handoff

Status: **PROPOSED OFFLINE IMPLEMENTATION CANDIDATE — independent
different-model exact-commit review required before any further step.**
Author: Claude Sonnet 5/high, 2026-10-03. This candidate implements only the
isolated files named below; it changes no existing module, caller, test,
protocol document or frozen constant. No real host clock observation,
network request, provider request, daemon, service, root install or
financial/account/order-path code is part of this candidate.

This implements the slice specified in
`docs/V11_GATE3_CLOCK_METHOD_HANDOFF_20261003.md` (author Codex Astra/high),
whose *design* received verdict `PASS_IN_SCOPE_PROPOSED_OFFLINE_DESIGN` from
an independent different-model exact-commit review recorded in
`docs/V11_GATE3_CLOCK_METHOD_REVIEW_9759ecf.md`. That review accepted a
*proposed offline method* only; it did not run or validate native recorder
behavior. This candidate is the first implementation of that design and
needs its own independent different-model exact-commit review before any
real local recording, calibration, custody or capture integration decision.

## 1. Exact scope: what this candidate adds

Four new files only, no other paths touched:

| File | Bytes | SHA-256 |
| --- | ---: | --- |
| `tools/v11_gate3_clock_probe.c` | 13938 | `7704cba8f34c149c83bb88b07c144b4501e034db60f7b864c236b9c5e86f5b4c` |
| `tools/v11_gate3_clock_dossier.py` | 21132 | `357835e9e213fe891677a167ede63ae5c7ed1df4ceb186d25fba40ac6ef4a82a` |
| `tests/v11_gate3_clock_probe_fixture_shim.c` | 5500 | `a8425d420c7084bf2529a8e994bbcfd93ee32358f81999aa92e7e2b91c6e4b36` |
| `tests/test_v11_gate3_clock_dossier.py` | 22844 | `d8c757d4f2427863bd5d189609f837a0bb9ebe17a379c60115e65fce01b44134` |
| `tests/test_v11_gate3_clock_probe_native.py` | 6734 | `a2e1ac4434e67770b7038ce0c3b3f9118bd5d230a74f0e474c24bb12c340ee54` |

This handoff document is a sixth new file and is not included in its own
table to avoid a self-referential hash. No existing file's bytes changed;
`git diff --check` against the baseline commit is clean for all of the
above. No production module imports or calls any of these five files; they
are reachable only by a human or test invoking them directly.

Baseline: commit `e5073a44d264145c4a4997ba40c723104ba4f7d1`, tree
`a7bed61086b20b7e5527f292f8319ba0171a019e` (from
`/tmp/alpha-v11-gate3-clock-recorder-20261003.launch.json`), which already
contains the merged, reviewed design document and review-intake record
above.

## 2. `tools/v11_gate3_clock_dossier.py`: pure verifier and session writer

Implements, in the style of the existing `_refuse`/`_keys`/`_reference`
pattern in `tools/v11_gate3_readiness_boundaries.py` (not imported from it —
this module is self-contained and isolated, per scope):

- `parse_probe_record(raw)`: closed-schema, bounded (<=16 KiB) parser for
  exactly the native probe's own output shape (Section 3 below). Checks
  duplicate keys, exact key sets, signed-64 integer bounds, bool/float
  rejection, oversized/deep nesting, and internal consistency: both
  same-clock ordering (`after >= before`) and cross-clock nesting
  (`monotonic_raw` and `boottime` brackets must fall inside
  `[m_before, m_after]`, since the recorder reads them in that nested
  order). Refuses on a nonzero `adjtimex` `modes`, an unrecognized or
  unsynchronized `adjtimex` result code, a mismatched before/after boot ID,
  or an empty identity read. It never asserts clock accuracy or sync state;
  a structurally valid `"OK"` record means only that the bytes are
  well-formed and internally consistent, not that anything is calibrated.
- `record_digests(raw, parsed)`: SHA-256 of the exact supplied `raw` bytes,
  and a *separate* SHA-256 of the canonical JSON of the parsed projection.
  The raw digest is always computed over the caller's literal bytes, never
  a reserialization of the parsed structure (tested: two differently
  formatted encodings of the same logical record get different raw digests
  but the same projection digest).
- `drift_envelope_ns` / `project_event_interval_ns` / `to_microseconds_outward`:
  the Section 3 equations (`age_upper = d - a`,
  `P(event) = [L0+(c-b)-D(d-a), U0+(d-a)+D(d-a)]`, `D(t) = ceil(rho*t)+j`)
  as pure integer arithmetic. `D` requires an explicit nonnegative rational
  rate (`rho_num/rho_den`) and jitter; there is no path that defaults
  unknown drift to zero. Interval conversion to microseconds floors the
  lower bound and ceils the upper bound (never narrows), and refuses a
  radius over 1,000,000 microseconds. Requires `a<=b<=c<=d` and `L0<=U0`.
- `build_session(...)`: assembles one closed-schema session record with
  `FIXED_AUTHORITY_FLAGS` (`execution_authority`, `provider_authority`,
  `capture_eligibility`, `clock_qualification` all `False`,
  `qualification_credit=0`, `g3l="NO_GO"`) always present and never
  caller-overridable. Status is `STRUCTURALLY_LINKED_UNQUALIFIED` only if a
  `method_envelope_ref` is supplied, else `RECORDED_UNQUALIFIED`; never
  `READY`. `event_kind` is restricted to exactly `LOCAL_OBSERVATION` or
  `SYNTHETIC`, set by the caller invoking this module -- the probe output
  itself carries no such label, since the native program has no way to
  know whether its own syscalls were real or test-intercepted.
- `write_session_file(root, session)`: for synthetic tests only. Opens with
  `O_CREAT | O_EXCL | O_NOFOLLOW` (refuses a pre-existing symlink target or
  a duplicate write), `fsync`s the written file, and re-validates the
  nonce/sequence it uses for the filename independently of whatever
  `build_session` already checked, since this is a disk-write path and
  must not trust a hand-built caller dict. This is explicitly *not* the
  custody-qualified durable store described in Section 5 of the design
  handoff (no private root outside Git/`/tmp`, no descriptor-relative
  traversal hardening beyond `O_NOFOLLOW`, no external checkpoint, no
  uid/mode/device/inode verification, no directory fsync) -- that remains
  future work requiring its own separate authorization and review, per the
  design handoff's explicit deferral of real retention.

## 3. `tools/v11_gate3_clock_probe.c`: one-shot native recorder

Unprivileged, standalone, one observation per invocation, compile-time
fixed allowlist with nothing else linked in or called:

- `clock_gettime`/`clock_getres` for `CLOCK_REALTIME`, `CLOCK_MONOTONIC`,
  `CLOCK_MONOTONIC_RAW`, `CLOCK_BOOTTIME`.
- Read-only `adjtimex()` with a zero-initialized `struct timex` and
  `modes = 0` hardcoded in source -- no `ADJ_*` operation is ever
  constructed, and no caller input reaches `modes`.
- `readlink()` of `/proc/self/ns/time` and `/proc/self/ns/pid`.
- `open`/`read`/`close` of `/proc/sys/kernel/random/boot_id` only (no
  machine-ID or arbitrary `/proc` scan).

No socket, subprocess, `exec`, shell, environment read, configuration read
or privilege-escalation call exists anywhere in this file. Order of
operations: boot ID (before) -> namespace links -> clock resolutions ->
monotonic/raw/boottime (before, nested open-to-close) -> `adjtimex` query ->
realtime read -> boottime/raw/monotonic (after, closing in reverse order) ->
boot ID (after). Any failure at any step emits a bounded `"REFUSED"` JSON
record on stdout (exit 1) carrying whatever raw fields were already
collected, never a fabricated success. A complete observation emits a
bounded `"OK"` record (exit 0) serializing every individual clock
return/errno, both resolutions, every initialized `struct timex` field
(zero-initialized before the call, so no uninitialized padding is ever
serialized), and the outer monotonic bracket. Total output is capped at
16 KiB; if construction would exceed that, it falls back to a fixed
minimal `OUTPUT_BOUNDS` refusal record instead. The program takes exactly
one sample and never loops or retries for a favorable result.

Build: `gcc -std=c11 -Wall -Wextra -O2 -o v11_gate3_clock_probe
tools/v11_gate3_clock_probe.c`. No external library beyond libc.

## 4. What is explicitly NOT implemented in this candidate

Per the design handoff's own staging ("Real recording, independent
method/source/custody qualification, and actual execution integration are
three separate subsequent decisions"), this candidate does not implement:

- Real local recording. The compiled probe binary was never executed
  without the test fixture's `LD_PRELOAD` interposing every syscall it
  uses; see Section 5. Running it unpreloaded on a real host is a
  separate, later decision this candidate does not make or request.
- The durable custody chain (private root outside Git/`/tmp`,
  single-writer lock, exclusive atomic no-clobber publication beyond the
  minimal `O_EXCL`/`O_NOFOLLOW` already in `write_session_file`, object and
  directory fsync, restart hash verification, independent external
  checkpoints). `write_session_file` is a bounded diagnostic writer for a
  temporary root inside the worktree only, as the design handoff requires
  for this slice.
- Any calibration source, drift-rate policy, review-identity/custody
  acceptance, or P1/capture window-fit integration. The interval-arithmetic
  functions implement the Section 3 equations as pure functions; nothing
  in this candidate supplies a numeric `rho`/`j`, a calibration source, or
  wiring into `tools/v11_gate3_readiness_boundaries.py`,
  `tools/v11_gate3_preflight_attempt_model.py` or
  `tools/v11_r09_gate3_g3l_prep.py`. None of those existing files are
  imported, modified or called by this candidate.
- The full adversarial test matrix in Section 6 of the design handoff
  (that table is explicitly scoped to "the future implementation" after
  real recording and custody review exist). This candidate's tests cover a
  representative subset directly relevant to what it implements:
  acquisition failures, ABI/unit mismatches, bracket-ordering violations,
  signed/overflow/bool/float rejection, outward-rounding at 1 s/60 s
  boundaries, radius-cap refusal, fixed authority flags, duplicate-key and
  deep/hostile input bounds, oversized reference-blob output bounds, and
  write-path symlink/no-clobber refusal. It does not cover fit-window,
  full custody rollback/checkpoint, or post-fsync seal-timestamp causality
  cases, since the code paths those would exercise are not implemented
  here.

## 5. Testing without any real host observation

The compiled `v11_gate3_clock_probe` binary is run **only** under
`tests/v11_gate3_clock_probe_fixture_shim.c`, an `LD_PRELOAD` shim built for
tests only (never linked into the production probe) that intercepts every
external call the probe makes -- `clock_gettime`, `clock_getres`,
`adjtimex`, `readlink`, and `open` for the exact boot_id path -- and returns
fixed, deterministic fixture values selected by `ALPHA_V11_CLOCK_FIXTURE`.
`MONOTONIC`/`MONOTONIC_RAW`/`BOOTTIME` share one incrementing call-sequence
counter in the shim so their fixture values stay correctly nested, matching
what genuine same-family hardware-timer clocks would produce for the
probe's fixed read order. Under this preload the probe never reaches the
real kernel clock, the real `adjtimex` syscall, or the real `boot_id` file;
`tests/test_v11_gate3_clock_probe_native.py` additionally asserts that two
runs of the same fixture scenario produce byte-identical output, which
would not hold if any real host state leaked through.

`tests/test_v11_gate3_clock_probe_native.py` also demonstrates
defense-in-depth: the `BOOT_ID_MISMATCH` and `ADJTIMEX_ERROR_STATUS`
fixtures make the native probe itself report a structurally well-formed
`"OK"` record (it cannot detect across-call mismatches or interpret
`TIME_ERROR` as unsync by itself), but `parse_probe_record` independently
refuses both (`CLOCK_CONTINUITY_LOST` and
`SYNC_OR_TIMESCALE_UNQUALIFIED` respectively) -- the Python verifier does
not trust the native program's own status field blindly.

## 6. Validation performed on this candidate

- `python3 tests/test_v11_gate3_clock_dossier.py` (pure verifier, no C
  compiler needed): `46 passed, 0 failed, 46 total`.
- `python3 -O tests/test_v11_gate3_clock_dossier.py`: `46 passed, 0 failed,
  46 total`.
- `python3 tests/test_v11_gate3_clock_probe_native.py` (compiles the probe
  and fixture shim with the system `gcc 13.3.0`, runs 8 fixture scenarios
  under `LD_PRELOAD`): `4 passed, 0 failed, 4 total`.
- `python3 -O tests/test_v11_gate3_clock_probe_native.py`: `4 passed, 0
  failed, 4 total`.
- `gcc -std=c11 -Wall -Wextra -O2 -c tools/v11_gate3_clock_probe.c`: clean,
  no warnings.
- `gcc -std=c11 -Wall -Wextra -Wpedantic -O2 -fanalyzer -c
  tools/v11_gate3_clock_probe.c`: clean, no diagnostics.
- `gcc -std=gnu11 -Wall -Wextra -Wpedantic -O2 -fanalyzer -fPIC -shared -c
  tests/v11_gate3_clock_probe_fixture_shim.c`: two expected `-Wpedantic`
  notes about the POSIX `dlsym`-to-function-pointer idiom (the same pattern
  already used in
  `docs/V11_R09_GATE3_DECODER_POINTOFUSE_OBSERVATION_20261001_shim.c`); no
  `-fanalyzer` diagnostics.
- `python3 -m py_compile tools/v11_gate3_clock_dossier.py
  tests/test_v11_gate3_clock_dossier.py
  tests/test_v11_gate3_clock_probe_native.py`: clean.
- `git diff --check` against the baseline commit for all five implementation
  files plus this document: clean.
- `git status --short` after every test run: no stray build artifacts or
  leftover temporary session files outside the test process's own
  `tempfile.TemporaryDirectory(dir=<worktree>)`, which self-deletes.
- Pytest is not installed in this environment (confirmed:
  `ModuleNotFoundError: No module named 'pytest'`); both new test files are
  therefore written without a pytest dependency (plain `assert` plus a
  `__main__` runner), rather than relying on the ast-stripping stdlib
  harness used for the pre-existing pytest-based test files in this repo.

## 7. Next steps (not taken by this candidate)

1. Independent different-model exact-commit review of this implementation
   candidate, per the design handoff's requirement, before any further
   step.
2. If accepted: review of a numeric drift-rate policy (`rho`, `j`) and a
   reference/calibration source, which this candidate deliberately does
   not supply.
3. If accepted: a separately authorized real durable storage root and
   custody chain, per Section 5 of the design handoff, before any real
   retention.
4. Only after both of the above: a decision about running the compiled
   probe against a real host, which remains entirely unauthorized by this
   candidate.

No clock, reference, host, custody, SHADOW or G3-L qualification is
asserted or enabled by this candidate. No execution or provider authority
is granted by any code path added here.
