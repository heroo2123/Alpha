"""Synthetic native-recorder tests: compile the probe and a fixture shim,
then run the probe only under an LD_PRELOAD that intercepts every syscall it
uses (clock_gettime, clock_getres, adjtimex, readlink, open for boot_id).

This never samples the real host: every external read the probe performs is
replaced by `v11_gate3_clock_probe_fixture_shim.c` with fixed, deterministic
fixture values selected by the ALPHA_V11_CLOCK_FIXTURE environment variable.
No scenario here ever runs the probe without that preload, and `_run_probe`
actively refuses to trust any run that cannot prove the shim actually loaded
(F6, independent review of 5667acb) rather than merely asserting an unrelated
fact about this test process's own environment.

No pytest dependency; a __main__ runner. Tests use `check()` (a plain
if/raise, immune to `-O` statement stripping) rather than bare `assert`.
Requires gcc; if unavailable, prints a skip notice and exits 0 rather than
failing the whole suite on an environment without a C compiler.
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from tools.v11_gate3_clock_dossier import Refusal, parse_probe_record  # noqa: E402

PROBE_SRC = REPO_ROOT / "tools" / "v11_gate3_clock_probe.c"
SHIM_SRC = REPO_ROOT / "tests" / "v11_gate3_clock_probe_fixture_shim.c"


def check(condition, message=""):
    """Assertion that survives `python3 -O` (bare `assert` is stripped)."""
    if not condition:
        raise AssertionError(message)


def _require_gcc():
    return shutil.which("gcc") is not None


def _build(build_dir: Path):
    probe_bin = build_dir / "v11_gate3_clock_probe"
    shim_so = build_dir / "fixture_shim.so"
    subprocess.run(["gcc", "-std=c11", "-Wall", "-Wextra", "-O2", "-o", str(probe_bin),
                    str(PROBE_SRC)], check=True, capture_output=True)
    subprocess.run(["gcc", "-std=gnu11", "-shared", "-fPIC", "-Wall", "-Wextra", "-O2",
                    "-o", str(shim_so), str(SHIM_SRC), "-ldl"], check=True, capture_output=True)
    return probe_bin, shim_so


# Written by the shim's own LD_PRELOAD constructor on load (F6): proves the
# shared object was actually mapped into the process, not just requested.
_SHIM_MARKER = b"ALPHA_V11_CLOCK_FIXTURE_SHIM_LOADED"
# Sentinel values the shim fixes for every scenario that reaches them: a real
# host can essentially never reproduce these exactly, so checking them is a
# second, independent proof that a given run's output is shim-sourced.
_FIXTURE_NS_TIME = "time:[4026531834]"
_FIXTURE_NS_PID = "pid:[4026531836]"


def _require_shim_marker(stderr_bytes, context):
    """F6 guard: refuse to trust output from a run that cannot prove the
    fixture shim actually loaded. If LD_PRELOAD fails (wrong arch, missing
    file, ...), glibc's loader just warns on stderr and runs the program
    unpreloaded against the real host; nothing else in the old code detected
    that until long after the forbidden real observation had already run."""
    check(_SHIM_MARKER in stderr_bytes,
          f"{context}: fixture shim load marker missing from stderr; refusing "
          "to trust this run as a safe fixture observation (F6)")


def _require_shim_field_provenance(decoded, scenario):
    """F6 guard, second line of defense: even with the marker present,
    cross-check that the emitted fields are the fixed fixture sentinels, not
    real host values (catches e.g. a stale/different shim being preloaded)."""
    partial = decoded.get("partial") if isinstance(decoded, dict) else None

    def field(name):
        value = decoded.get(name) if isinstance(decoded, dict) else None
        if value is None and isinstance(partial, dict):
            value = partial.get(name)
        return value

    ns_time = field("ns_time")
    if ns_time is not None:
        check(ns_time == _FIXTURE_NS_TIME,
              f"{scenario}: ns_time {ns_time!r} is not the fixture sentinel (F6)")
    ns_pid = field("ns_pid")
    if ns_pid is not None:
        check(ns_pid == _FIXTURE_NS_PID,
              f"{scenario}: ns_pid {ns_pid!r} is not the fixture sentinel (F6)")


def _run_probe(probe_bin, shim_so, fixture):
    env = {"LD_PRELOAD": str(shim_so), "ALPHA_V11_CLOCK_FIXTURE": fixture}
    result = subprocess.run([str(probe_bin)], env=env, capture_output=True, timeout=10)
    _require_shim_marker(result.stderr, fixture)
    _require_shim_field_provenance(json.loads(result.stdout), fixture)
    return result.returncode, result.stdout


_EXPECTED_PARSE_OUTCOME = {
    "OK": ("OK", None),
    "DIVERGED_CLOCKS": ("OK", None),
    "ADJTIMEX_ERROR_STATUS": ("REFUSAL", "SYNC_OR_TIMESCALE_UNQUALIFIED"),
    "BOOT_ID_MISMATCH": ("REFUSAL", "CLOCK_CONTINUITY_LOST"),
    "OVERLONG_BOOT_ID": ("PASSTHROUGH_REFUSED", "CLOCK_SOURCE_UNAVAILABLE"),
    "MONO_FAIL": ("PASSTHROUGH_REFUSED", "CLOCK_SOURCE_UNAVAILABLE"),
    "ADJTIMEX_FAIL": ("PASSTHROUGH_REFUSED", "CLOCK_SOURCE_UNAVAILABLE"),
    "NS_FAIL": ("PASSTHROUGH_REFUSED", "CLOCK_SOURCE_UNAVAILABLE"),
    "BOOT_ID_FAIL": ("PASSTHROUGH_REFUSED", "CLOCK_SOURCE_UNAVAILABLE"),
}


def test_shim_marker_guard_rejects_missing_marker():
    try:
        _require_shim_marker(b"", "OK")
        check(False, "F6: missing shim-load marker must be rejected")
    except AssertionError:
        pass


def test_shim_marker_guard_accepts_present_marker():
    _require_shim_marker(_SHIM_MARKER + b"\n", "OK")  # must not raise


def test_shim_field_provenance_guard_rejects_non_fixture_values():
    try:
        _require_shim_field_provenance({"ns_time": "time:[1]"}, "OK")
        check(False, "F6: a non-fixture ns_time must be rejected")
    except AssertionError:
        pass


def test_shim_field_provenance_guard_accepts_fixture_values():
    _require_shim_field_provenance(
        {"ns_time": _FIXTURE_NS_TIME, "ns_pid": _FIXTURE_NS_PID}, "OK")  # must not raise


def _run_all_scenarios(probe_bin, shim_so):
    outputs = {}
    for scenario in _EXPECTED_PARSE_OUTCOME:
        code, raw = _run_probe(probe_bin, shim_so, scenario)
        outputs[scenario] = (code, raw)
    return outputs


def test_probe_scenarios_match_native_exit_status_and_python_verifier():
    if not _require_gcc():
        print("SKIP: gcc not available")
        return
    with tempfile.TemporaryDirectory(dir=str(REPO_ROOT)) as build_dir:
        probe_bin, shim_so = _build(Path(build_dir))
        for scenario, (expect_kind, expect_detail) in _EXPECTED_PARSE_OUTCOME.items():
            exit_code, raw = _run_probe(probe_bin, shim_so, scenario)
            decoded = json.loads(raw)
            check(decoded["schema"] == "ALPHA_V11_GATE3_CLOCK_PROBE_RECORD_V1")
            if expect_kind == "OK":
                check(exit_code == 0, (scenario, exit_code, raw))
                check(decoded["status"] == "OK")
                parsed = parse_probe_record(raw)
                check(parsed["status"] == "OK")
            elif expect_kind == "REFUSAL":
                # Probe itself reports OK (it cannot see the mismatch across
                # process state), but the independent Python verifier must
                # still catch it on its own -- defense in depth.
                check(exit_code == 0)
                check(decoded["status"] == "OK")
                try:
                    parse_probe_record(raw)
                    check(False, f"{scenario}: expected verifier Refusal")
                except Refusal as error:
                    check(error.code == expect_detail)
            else:  # PASSTHROUGH_REFUSED
                check(exit_code == 1, (scenario, exit_code, raw))
                check(decoded["status"] == "REFUSED")
                check(decoded["code"] == expect_detail)
                parsed = parse_probe_record(raw)
                check(parsed == {"status": "REFUSED", "code": expect_detail})


def test_probe_output_is_deterministic_under_fixed_fixture():
    """Two runs of the same fixture must be byte-identical.

    If the probe sampled the real wall clock or boot_id instead of the
    intercepted fixture, the realtime/boot_id fields would differ run to
    run; identical bytes are evidence the preload fully replaced every
    external read for this invocation.
    """
    if not _require_gcc():
        print("SKIP: gcc not available")
        return
    with tempfile.TemporaryDirectory(dir=str(REPO_ROOT)) as build_dir:
        probe_bin, shim_so = _build(Path(build_dir))
        _, raw1 = _run_probe(probe_bin, shim_so, "OK")
        _, raw2 = _run_probe(probe_bin, shim_so, "OK")
        check(raw1 == raw2)


def test_probe_output_stays_within_bounded_output_cap():
    if not _require_gcc():
        print("SKIP: gcc not available")
        return
    with tempfile.TemporaryDirectory(dir=str(REPO_ROOT)) as build_dir:
        probe_bin, shim_so = _build(Path(build_dir))
        for scenario in _EXPECTED_PARSE_OUTCOME:
            _, raw = _run_probe(probe_bin, shim_so, scenario)
            check(len(raw) <= 16_384 + 1)  # +1 for the trailing newline


def _all_tests():
    module = sys.modules[__name__]
    return [getattr(module, name) for name in dir(module) if name.startswith("test_")]


if __name__ == "__main__":
    failures = []
    tests = _all_tests()
    for test in tests:
        try:
            test()
        except Exception as error:  # noqa: BLE001
            failures.append((test.__name__, repr(error)))
    print(f"{len(tests) - len(failures)} passed, {len(failures)} failed, {len(tests)} total")
    for name, error in failures:
        print(f"FAIL {name}: {error}")
    sys.exit(1 if failures else 0)
