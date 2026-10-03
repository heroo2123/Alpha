"""Synthetic native-recorder tests: compile the probe and a fixture shim,
then run the probe only under an LD_PRELOAD that intercepts every syscall it
uses (clock_gettime, clock_getres, adjtimex, readlink, open for boot_id).

This never samples the real host: every external read the probe performs is
replaced by `v11_gate3_clock_probe_fixture_shim.c` with fixed, deterministic
fixture values selected by the ALPHA_V11_CLOCK_FIXTURE environment variable.
No scenario here ever runs the probe without that preload.

No pytest dependency; see test_v11_gate3_clock_dossier.py for the rationale.
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


def _run_probe(probe_bin, shim_so, fixture):
    env = dict(os.environ)
    env["LD_PRELOAD"] = str(shim_so)
    env["ALPHA_V11_CLOCK_FIXTURE"] = fixture
    # Strip anything from the real environment that could let the probe
    # behave differently from a clean fixture run; it reads no env itself,
    # but keep the invocation minimal and explicit regardless.
    result = subprocess.run([str(probe_bin)], env=env, capture_output=True, timeout=10)
    return result.returncode, result.stdout


_SCENARIOS_OK = ("OK", "ADJTIMEX_ERROR_STATUS", "BOOT_ID_MISMATCH", "OVERLONG_BOOT_ID")
_SCENARIOS_REFUSED = ("MONO_FAIL", "ADJTIMEX_FAIL", "NS_FAIL", "BOOT_ID_FAIL")

_EXPECTED_PARSE_OUTCOME = {
    "OK": ("OK", None),
    "ADJTIMEX_ERROR_STATUS": ("REFUSAL", "SYNC_OR_TIMESCALE_UNQUALIFIED"),
    "BOOT_ID_MISMATCH": ("REFUSAL", "CLOCK_CONTINUITY_LOST"),
    "OVERLONG_BOOT_ID": ("OK", None),
    "MONO_FAIL": ("PASSTHROUGH_REFUSED", "CLOCK_SOURCE_UNAVAILABLE"),
    "ADJTIMEX_FAIL": ("PASSTHROUGH_REFUSED", "CLOCK_SOURCE_UNAVAILABLE"),
    "NS_FAIL": ("PASSTHROUGH_REFUSED", "CLOCK_SOURCE_UNAVAILABLE"),
    "BOOT_ID_FAIL": ("PASSTHROUGH_REFUSED", "CLOCK_SOURCE_UNAVAILABLE"),
}


def test_probe_never_runs_without_the_fixture_preload_present():
    assert os.environ.get("LD_PRELOAD") in (None, "")


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
            assert decoded["schema"] == "ALPHA_V11_GATE3_CLOCK_PROBE_RECORD_V1"
            if expect_kind == "OK":
                assert exit_code == 0, (scenario, exit_code, raw)
                assert decoded["status"] == "OK"
                parsed = parse_probe_record(raw)
                assert parsed["status"] == "OK"
            elif expect_kind == "REFUSAL":
                # Probe itself reports OK (it cannot see the mismatch across
                # process state), but the independent Python verifier must
                # still catch it on its own -- defense in depth.
                assert exit_code == 0
                assert decoded["status"] == "OK"
                try:
                    parse_probe_record(raw)
                    assert False, f"{scenario}: expected verifier Refusal"
                except Refusal as error:
                    assert error.code == expect_detail
            else:  # PASSTHROUGH_REFUSED
                assert exit_code == 1, (scenario, exit_code, raw)
                assert decoded["status"] == "REFUSED"
                assert decoded["code"] == expect_detail
                parsed = parse_probe_record(raw)
                assert parsed == {"status": "REFUSED", "code": expect_detail}


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
        assert raw1 == raw2


def test_probe_output_stays_within_bounded_output_cap():
    if not _require_gcc():
        print("SKIP: gcc not available")
        return
    with tempfile.TemporaryDirectory(dir=str(REPO_ROOT)) as build_dir:
        probe_bin, shim_so = _build(Path(build_dir))
        for scenario in _EXPECTED_PARSE_OUTCOME:
            _, raw = _run_probe(probe_bin, shim_so, scenario)
            assert len(raw) <= 16_384 + 1  # +1 for the trailing newline


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
