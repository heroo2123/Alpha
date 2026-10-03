"""Synthetic native-recorder tests: compile the probe and a fixture shim,
then run the probe only under an LD_PRELOAD that intercepts every syscall it
uses (clock_gettime, clock_getres, adjtimex, readlink, open for boot_id).

This never samples the real host: every external read the probe performs is
replaced by `v11_gate3_clock_probe_fixture_shim.c` with fixed, deterministic
fixture values selected by the ALPHA_V11_CLOCK_FIXTURE environment variable.
No scenario here runs the probe until a harmless separate process has proved
that the same preload loads and binds all five observation symbols to this
shim. `_run_probe` then checks the marker and fixture fields after execution
as additional diagnostics (F6, independent review of f0ca627).

No pytest dependency; a __main__ runner. Tests use `check()` (a plain
if/raise, immune to `-O` statement stripping) rather than bare `assert`.
Requires gcc; if unavailable, prints a skip notice and exits 0 rather than
failing the whole suite on an environment without a C compiler.
"""

import json
import hashlib
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from tools.v11_gate3_clock_dossier import Refusal, parse_probe_record  # noqa: E402

PROBE_SRC = REPO_ROOT / "tools" / "v11_gate3_clock_probe.c"
SHIM_SRC = REPO_ROOT / "tests" / "v11_gate3_clock_probe_fixture_shim.c"
BINDINGS_SRC = REPO_ROOT / "tests" / "v11_gate3_clock_fixture_bindings.c"
_BUILD_IDENTITIES = {}


def _digest(path):
    return hashlib.sha256(Path(path).read_bytes()).digest()


def check(condition, message=""):
    """Assertion that survives `python3 -O` (bare `assert` is stripped)."""
    if not condition:
        raise AssertionError(message)


def _require_gcc():
    return shutil.which("gcc") is not None


def _build(build_dir: Path):
    probe_bin = build_dir / "v11_gate3_clock_probe"
    shim_so = build_dir / "fixture_shim.so"
    bindings_bin = build_dir / "fixture_bindings"
    subprocess.run(["gcc", "-std=c11", "-Wall", "-Wextra", "-O2", "-o", str(probe_bin),
                    str(PROBE_SRC)], check=True, capture_output=True)
    subprocess.run(["gcc", "-std=gnu11", "-shared", "-fPIC", "-Wall", "-Wextra", "-O2",
                    "-o", str(shim_so), str(SHIM_SRC), "-ldl"], check=True, capture_output=True)
    subprocess.run(["gcc", "-std=c11", "-Wall", "-Wextra", "-Wpedantic", "-O2",
                    "-o", str(bindings_bin), str(BINDINGS_SRC), "-ldl"],
                   check=True, capture_output=True)
    _BUILD_IDENTITIES[str(probe_bin.resolve())] = (
        _digest(probe_bin), _digest(shim_so), _digest(bindings_bin))
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
    # The helper invokes no observation function. Its dlsym/dladdr results
    # must establish every binding before the probe may execute. Keep a digest
    # check around preflight so a changed library cannot silently be trusted.
    probe_bin = Path(probe_bin).resolve(strict=True)
    shim_so = Path(shim_so).resolve(strict=True)
    bindings_bin = Path(probe_bin).parent / "fixture_bindings"
    expected = _BUILD_IDENTITIES.get(str(probe_bin))
    check(expected is not None and
          (_digest(probe_bin), _digest(shim_so), _digest(bindings_bin)) == expected,
          "F6: compiled fixture binaries changed before preflight")
    env = {"LD_PRELOAD": str(shim_so), "ALPHA_V11_CLOCK_FIXTURE": fixture}
    preflight = subprocess.run([str(bindings_bin), str(shim_so)], env=env,
                              capture_output=True, timeout=10)
    _require_preflight(preflight, expected, probe_bin, shim_so, bindings_bin)
    result = subprocess.run([str(probe_bin)], env=env, capture_output=True, timeout=10)
    _require_shim_marker(result.stderr, fixture)
    _require_shim_field_provenance(json.loads(result.stdout), fixture)
    return result.returncode, result.stdout


def _require_preflight(result, expected, probe_bin, shim_so, bindings_bin):
    check(result.returncode == 0 and
          result.stdout == b"VERIFIED_ALL_FIVE_SHIM_BINDINGS\n" and
          _SHIM_MARKER in result.stderr and
          (_digest(probe_bin), _digest(shim_so), _digest(bindings_bin)) == expected,
          "F6: preload identity or observation-symbol bindings unverified")


_EXPECTED_PARSE_OUTCOME = {
    "OK": ("OK", None),
    "ADJTIMEX_NANO": ("OK", None),
    "DIVERGED_CLOCKS": ("OK", None),
    "ADJTIMEX_ERROR_STATUS": ("REFUSAL", "SYNC_OR_TIMESCALE_UNQUALIFIED"),
    "BOOT_ID_MISMATCH": ("REFUSAL", "CLOCK_CONTINUITY_LOST"),
    "OVERLONG_BOOT_ID": ("PASSTHROUGH_REFUSED", "CLOCK_SOURCE_UNAVAILABLE"),
    "MONO_FAIL": ("PASSTHROUGH_REFUSED", "CLOCK_SOURCE_UNAVAILABLE"),
    "ADJTIMEX_FAIL": ("PASSTHROUGH_REFUSED", "CLOCK_SOURCE_UNAVAILABLE"),
    "RT_FAIL": ("PASSTHROUGH_REFUSED", "CLOCK_SOURCE_UNAVAILABLE"),
    "NS_FAIL": ("PASSTHROUGH_REFUSED", "CLOCK_SOURCE_UNAVAILABLE"),
    "BOOT_ID_FAIL": ("PASSTHROUGH_REFUSED", "CLOCK_SOURCE_UNAVAILABLE"),
}


def test_shim_marker_guard_rejects_missing_marker():
    try:
        _require_shim_marker(b"", "OK")
    except AssertionError:
        pass
    else:
        check(False, "F6: missing shim-load marker must be rejected")


def test_shim_marker_guard_accepts_present_marker():
    _require_shim_marker(_SHIM_MARKER + b"\n", "OK")  # must not raise


def test_shim_field_provenance_guard_rejects_non_fixture_values():
    try:
        _require_shim_field_provenance({"ns_time": "time:[1]"}, "OK")
    except AssertionError:
        pass
    else:
        check(False, "F6: a non-fixture ns_time must be rejected")


def test_shim_field_provenance_guard_accepts_fixture_values():
    _require_shim_field_provenance(
        {"ns_time": _FIXTURE_NS_TIME, "ns_pid": _FIXTURE_NS_PID}, "OK")  # must not raise


def test_preflight_rejection_prevents_probe_launch():
    class BadBindings:
        returncode = 3
        stdout = b""
        stderr = _SHIM_MARKER + b"\n"

    class UnexpectedProbe:
        returncode = 0
        stdout = b"{}"
        stderr = _SHIM_MARKER + b"\n"

    with tempfile.TemporaryDirectory(dir=str(REPO_ROOT)) as root:
        probe = Path(root) / "probe"
        shim = Path(root) / "fixture_shim.so"
        bindings = Path(root) / "fixture_bindings"
        probe.write_bytes(b"probe placeholder; no executable run")
        shim.write_bytes(b"fixture placeholder; no executable run")
        bindings.write_bytes(b"checker placeholder; no executable run")
        with patch.dict(_BUILD_IDENTITIES, {str(probe):
                       (_digest(probe), _digest(shim), _digest(bindings))}):
          with patch("subprocess.run", side_effect=[BadBindings(), UnexpectedProbe()]) as launched:
            try:
                _run_probe(probe, shim, "OK")
            except AssertionError:
                pass
            else:
                check(False, "F6: failed binding preflight must reject")
            check(launched.call_count == 1,
                  "F6: a probe was launched after failed binding preflight")


def test_preflight_guard_rejects_missing_binding_proof():
    class NoProof:
        returncode = 0
        stdout = b""
        stderr = _SHIM_MARKER

    with tempfile.TemporaryDirectory(dir=str(REPO_ROOT)) as root:
        probe = Path(root) / "probe"
        shim = Path(root) / "shim.so"
        bindings = Path(root) / "fixture_bindings"
        probe.write_bytes(b"probe")
        shim.write_bytes(b"fixture")
        bindings.write_bytes(b"bindings")
        try:
            _require_preflight(NoProof(), (_digest(probe), _digest(shim), _digest(bindings)),
                               probe, shim, bindings)
        except AssertionError:
            pass
        else:
            check(False, "F6: missing symbol binding proof must reject")


def test_changed_shim_identity_prevents_any_launch():
    with tempfile.TemporaryDirectory(dir=str(REPO_ROOT)) as root:
        probe = Path(root) / "probe"
        shim = Path(root) / "fixture_shim.so"
        bindings = Path(root) / "fixture_bindings"
        for path in (probe, shim, bindings):
            path.write_bytes(b"original")
        identity = (_digest(probe), _digest(shim), _digest(bindings))
        shim.write_bytes(b"changed after build")
        with patch.dict(_BUILD_IDENTITIES, {str(probe): identity}):
            with patch("subprocess.run") as launched:
                try:
                    _run_probe(probe, shim, "OK")
                except AssertionError:
                    pass
                else:
                    check(False, "F6: changed shim must reject before launch")
                check(launched.call_count == 0)


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
                if scenario == "RT_FAIL":
                    partial = decoded["partial"]
                    check(partial["adjtimex"]["call_result"] == 0)
                    check(partial["realtime"]["result"] == -1)
                    check(all(partial[name]["after_result"] == -1 for name in
                              ("monotonic", "monotonic_raw", "boottime")))


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
