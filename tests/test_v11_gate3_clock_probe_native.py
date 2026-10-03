"""Synthetic native-recorder tests. The fixture target directly links five
fixed observation functions; it has no imports for the real observation
wrappers. Before execution its exact bytes are checked, copied to a sealed
memfd, and inspected there. The same sealed descriptor is executed. A path
replacement or missing LD_PRELOAD cannot change what the target observes.

No pytest dependency; a __main__ runner. Tests use `check()` (a plain
if/raise, immune to `-O` statement stripping) rather than bare `assert`.
Requires gcc; if unavailable, prints a skip notice and exits 0 rather than
failing the whole suite on an environment without a C compiler.
"""

import json
import hashlib
import fcntl
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
_BUILD_IDENTITIES = {}


def _digest(path):
    return hashlib.sha256(Path(path).read_bytes()).digest()


def check(condition, message=""):
    """Assertion that survives `python3 -O` (bare `assert` is stripped)."""
    if not condition:
        raise AssertionError(message)


def _require_gcc():
    return shutil.which("gcc") is not None and shutil.which("nm") is not None


def _build(build_dir: Path):
    probe_bin = build_dir / "v11_gate3_clock_fixture_target"
    subprocess.run(["gcc", "-std=gnu11", "-DALPHA_V11_FIXTURE_ONLY", "-Wall",
                    "-Wextra", "-Wpedantic", "-O2", "-o", str(probe_bin),
                    str(PROBE_SRC), str(SHIM_SRC)], check=True, capture_output=True)
    _BUILD_IDENTITIES[str(probe_bin.resolve())] = _digest(probe_bin)
    return probe_bin


# Diagnostic marker from the fixture object's constructor; the pre-execution
# proof below is the sealed target's direct bindings, not this marker.
_SHIM_MARKER = b"ALPHA_V11_CLOCK_FIXTURE_SHIM_LOADED"
# Fixed fixture sentinels provide an output sanity check after execution.
_FIXTURE_NS_TIME = "time:[4026531834]"
_FIXTURE_NS_PID = "pid:[4026531836]"


def _require_shim_marker(stderr_bytes, context):
    """Require the linked fixture object's diagnostic constructor marker."""
    check(_SHIM_MARKER in stderr_bytes,
          f"{context}: fixture shim load marker missing from stderr; refusing "
          "to trust this run as a safe fixture observation (F6)")


def _require_shim_field_provenance(decoded, scenario):
    """Check fixed fixture sentinels after execution."""
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


_OBSERVATION_NAMES = {"clock_gettime", "clock_getres", "adjtimex", "open", "readlink"}
_FIXTURE_NAMES = {"fixture_" + name for name in _OBSERVATION_NAMES}
_ALLOWED_UNRESOLVED = {
    "_ITM_deregisterTMCloneTable", "_ITM_registerTMCloneTable", "__cxa_finalize",
    "__errno_location", "__gmon_start__", "__libc_start_main", "__snprintf_chk",
    "__stack_chk_fail", "close", "getenv", "lseek", "memfd_create", "read",
    "strcmp", "strlen", "strstr", "write",
}


def _verify_fixture_target(fd):
    """Inspect the very descriptor that will execute, before any probe call."""
    path = f"/proc/self/fd/{fd}"
    undefined = subprocess.run(["nm", "-u", path], pass_fds=(fd,),
                               check=True, capture_output=True).stdout
    defined = subprocess.run(["nm", "--defined-only", path], pass_fds=(fd,),
                             check=True, capture_output=True).stdout
    unresolved = {line.split()[-1].split(b"@", 1)[0].decode("ascii")
                  for line in undefined.splitlines() if line.split()}
    symbols = {line.split()[-1].decode("ascii")
               for line in defined.splitlines() if line.split()}
    check(unresolved <= _ALLOWED_UNRESOLVED and _FIXTURE_NAMES <= symbols,
          "F6: sealed target has an unexpected import or lacks a direct fixture binding")


def _execute_fixture(fd, fixture):
    return subprocess.run([f"/proc/self/fd/{fd}"], pass_fds=(fd,),
                          env={"ALPHA_V11_CLOCK_FIXTURE": fixture},
                          capture_output=True, timeout=10)


def _run_probe(probe_bin, fixture):
    probe_bin = Path(probe_bin).resolve(strict=True)
    expected = _BUILD_IDENTITIES.get(str(probe_bin))
    blob = probe_bin.read_bytes()
    check(expected is not None and hashlib.sha256(blob).digest() == expected,
          "F6: compiled fixture target changed before sealing")
    fd = os.memfd_create("alpha_v11_clock_fixture", os.MFD_CLOEXEC | os.MFD_ALLOW_SEALING)
    try:
        offset = 0
        while offset < len(blob):
            written = os.write(fd, blob[offset:])
            check(written > 0, "F6: zero-progress sealed target copy")
            offset += written
        seals = fcntl.F_SEAL_WRITE | fcntl.F_SEAL_GROW | fcntl.F_SEAL_SHRINK | fcntl.F_SEAL_SEAL
        fcntl.fcntl(fd, fcntl.F_ADD_SEALS, seals)
        check(fcntl.fcntl(fd, fcntl.F_GET_SEALS) & seals == seals,
              "F6: target descriptor not sealed")
        os.lseek(fd, 0, os.SEEK_SET)
        check(hashlib.sha256(os.read(fd, len(blob) + 1)).digest() == expected,
              "F6: sealed target bytes changed")
        _verify_fixture_target(fd)
        result = _execute_fixture(fd, fixture)
    finally:
        os.close(fd)
    _require_shim_marker(result.stderr, fixture)
    _require_shim_field_provenance(json.loads(result.stdout), fixture)
    return result.returncode, result.stdout


_EXPECTED_PARSE_OUTCOME = {
    "OK": ("OK", None),
    "ADJTIMEX_NANO": ("OK", None),
    "DIVERGED_CLOCKS": ("OK", None),
    "ADJTIMEX_ERROR_STATUS": ("REFUSAL", "SYNC_OR_TIMESCALE_UNQUALIFIED"),
    "BOOT_ID_MISMATCH": ("REFUSAL", "CLOCK_CONTINUITY_LOST"),
    "OVERLONG_BOOT_ID": ("PASSTHROUGH_REFUSED", "CLOCK_SOURCE_UNAVAILABLE"),
    "MONO_FAIL": ("PASSTHROUGH_REFUSED", "CLOCK_SOURCE_UNAVAILABLE"),
    "MONO_RES_FAIL": ("PASSTHROUGH_REFUSED", "CLOCK_SOURCE_UNAVAILABLE"),
    "RAW_RES_FAIL": ("PASSTHROUGH_REFUSED", "CLOCK_SOURCE_UNAVAILABLE"),
    "BOOT_RES_FAIL": ("PASSTHROUGH_REFUSED", "CLOCK_SOURCE_UNAVAILABLE"),
    "RT_RES_FAIL": ("PASSTHROUGH_REFUSED", "CLOCK_SOURCE_UNAVAILABLE"),
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


def test_failed_target_proof_prevents_launch():
    if not _require_gcc():
        return
    with tempfile.TemporaryDirectory(dir=str(REPO_ROOT)) as root:
        probe = _build(Path(root))
        with patch.object(sys.modules[__name__], "_verify_fixture_target",
                          side_effect=AssertionError("binding refused")):
            with patch.object(sys.modules[__name__], "_execute_fixture") as launched:
                try:
                    _run_probe(probe, "OK")
                except AssertionError:
                    pass
                else:
                    check(False, "F6: missing target proof must reject")
                check(launched.call_count == 0)


def test_changed_target_before_sealing_prevents_launch():
    if not _require_gcc():
        return
    with tempfile.TemporaryDirectory(dir=str(REPO_ROOT)) as root:
        probe = _build(Path(root))
        probe.write_bytes(b"replaced after build")
        with patch.object(sys.modules[__name__], "_execute_fixture") as launched:
            try:
                _run_probe(probe, "OK")
            except AssertionError:
                pass
            else:
                check(False, "F6: changed target must reject before launch")
            check(launched.call_count == 0)


def test_final_check_to_launch_path_replacement_cannot_change_target():
    if not _require_gcc():
        return
    with tempfile.TemporaryDirectory(dir=str(REPO_ROOT)) as root:
        probe = _build(Path(root))
        original = _digest(probe)

        def mutate_after_proof(fd, fixture):
            probe.unlink()
            probe.write_bytes(b"replaced at final boundary")
            os.lseek(fd, 0, os.SEEK_SET)
            check(hashlib.sha256(os.read(fd, 1_000_000)).digest() == original)
            try:
                os.write(fd, b"change")
            except OSError:
                pass
            else:
                check(False, "F6: sealed target remained writable")
            return subprocess.CompletedProcess([], 0, b"{}", _SHIM_MARKER)

        with patch.object(sys.modules[__name__], "_execute_fixture",
                          side_effect=mutate_after_proof) as launched:
            check(_run_probe(probe, "OK")[0] == 0)
            check(launched.call_count == 1)


def test_probe_scenarios_match_native_exit_status_and_python_verifier():
    if not _require_gcc():
        print("SKIP: gcc not available")
        return
    with tempfile.TemporaryDirectory(dir=str(REPO_ROOT)) as build_dir:
        probe_bin = _build(Path(build_dir))
        for scenario, (expect_kind, expect_detail) in _EXPECTED_PARSE_OUTCOME.items():
            exit_code, raw = _run_probe(probe_bin, scenario)
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
                if scenario.endswith("_RES_FAIL"):
                    failed_name = {"MONO_RES_FAIL": "monotonic",
                                   "RAW_RES_FAIL": "monotonic_raw",
                                   "BOOT_RES_FAIL": "boottime",
                                   "RT_RES_FAIL": "realtime"}[scenario]
                    partial = decoded["partial"]
                    failed = partial[failed_name]
                    check(failed["res_attempted"] == 1 and
                          failed["res_result"] == -1 and failed["res_errno"] == 38)
                    check(failed["res_sec"] == failed["res_nsec"] == 0)
                    if failed_name == "realtime":
                        check(failed["attempted"] == 0 and failed["result"] == -1)
                    else:
                        check(failed["before_attempted"] == 0 and
                              failed["after_attempted"] == 0)
                    for earlier in ("monotonic", "monotonic_raw", "boottime"):
                        if earlier == failed_name:
                            break
                        check(partial[earlier]["res_attempted"] == 1 and
                              partial[earlier]["res_result"] == 0)
                if scenario == "RT_FAIL":
                    partial = decoded["partial"]
                    check(partial["adjtimex"]["call_result"] == 0)
                    check(partial["realtime"]["result"] == -1)
                    check(all(partial[name]["after_result"] == -1 for name in
                              ("monotonic", "monotonic_raw", "boottime")))


def test_probe_output_is_deterministic_under_fixed_fixture():
    """Two direct-link fixture runs must be byte-identical."""
    if not _require_gcc():
        print("SKIP: gcc not available")
        return
    with tempfile.TemporaryDirectory(dir=str(REPO_ROOT)) as build_dir:
        probe_bin = _build(Path(build_dir))
        _, raw1 = _run_probe(probe_bin, "OK")
        _, raw2 = _run_probe(probe_bin, "OK")
        check(raw1 == raw2)


def test_probe_output_stays_within_bounded_output_cap():
    if not _require_gcc():
        print("SKIP: gcc not available")
        return
    with tempfile.TemporaryDirectory(dir=str(REPO_ROOT)) as build_dir:
        probe_bin = _build(Path(build_dir))
        for scenario in _EXPECTED_PARSE_OUTCOME:
            _, raw = _run_probe(probe_bin, scenario)
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
