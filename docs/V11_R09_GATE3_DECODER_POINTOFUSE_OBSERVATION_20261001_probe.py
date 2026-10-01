#!/usr/bin/env python3
"""Offline decoder point-of-use trace probe for Gate 3 (MEMFS item 3 follow-up).

UNREVIEWED_BUILD_OBSERVATION only. Not a qualification, acceptance or launch
result. This probe does not establish build/source provenance, CCSDS/
provider permission, G3-L identity, or any launch/financial/promotion
authority; see the companion Markdown's "What remains open" section. A zero
exit code means its bounded checks passed, not Gate 3 acceptance. The probe
compiles a temporary shim, writes temporary trace/dump files and emits JSON;
it makes no network request, installs no package, and never
enables CCSDS for any provider/live path: the CCSDS re-encode it exercises
is the exact same offline synthetic fixture call already used unchanged by
the committed, independently-reviewed decoder build observation probe
(V11_R09_GATE3_DECODER_BUILD_OBSERVATION_20261001_probe.py).

What this probe actually does, in one command, from the repository root:

    PYTHONDONTWRITEBYTECODE=1 \
      /home/alphaadmin/AlphaV11_Dev/venv/bin/python \
      docs/V11_R09_GATE3_DECODER_POINTOFUSE_OBSERVATION_20261001_probe.py

1. Compiles the co-located LD_PRELOAD shim source
   (..._shim.c) fresh with the host's gcc, into a temporary .so. The shim's
   full source is inspectable before this step; nothing here downloads or
   patches it.
2. Runs two bounded negative probes as subprocesses, each a tiny decode
   under a deliberately broken environment, to show the shim fails loudly
   rather than silently measuring nothing:
     A. ALPHA_V11_MEMFS_LIBRARY_PATH pointed at a nonexistent file with the
        shim preloaded -- must abort (nonzero exit, "FATAL" on stderr).
     B. No LD_PRELOAD at all -- decode must succeed normally and no trace
        file may appear, showing a trace is only ever produced by genuine
        interposition, never fabricated by this harness.
3. Runs the real bounded capture: the exact, unmodified
   `_run_bounded_synthetic_ccsds_decode` function from the already-reviewed
   decoder build observation probe (its bytes are compared to the pinned
   reviewed source blob, then that verified snapshot is executed), with
   the shim preloaded via LD_PRELOAD, RLIMIT_CPU=60s,
   RLIMIT_AS=512 MiB and Python socket.socket blocking as in that
   probe. Every codes_memfs_open/codes_memfs_exists call made by the real
   library during that one bounded decode is logged by the shim with the
   requested path; codes_memfs_open calls additionally have their full
   returned byte stream dumped by the shim (a passive read-back that
   restores the stream position before returning to the real caller. The
   reported outputs and library hash are checked; general equivalence to
   an uninstrumented run is not established).
4. Independently, in this Python process (not the shim), for every captured
   codes_memfs_open call: hashes the dumped bytes; looks up the same path in
   the already-independently-reviewed MEMFS static observation's
   `full_entry_inventory` table; separately re-reads the exact byte range
   that table records (`file_offset` .. `file_offset+symbol_size`) directly
   from the installed library file; and reports whether all three
   (shim dump, static-table sha256, independent direct file read) agree.
   For every codes_memfs_exists call, checks whether the boolean result
   agrees with simple presence/absence of that path in the same table.

Writes temporary trace/dump/shim files under a fresh `tempfile.mkdtemp`
directory (not inside the repository, not committed) and leaves them in
place for inspection; the printed JSON report records that directory's
path. Reads only: this repository's own files, the Alpha development venv's
installed eccodes/eckit files, and `/proc/self/maps`-style identity already
covered by the sibling decoder build probe. No file inside the repository
or venv is ever opened for writing.
"""
from __future__ import annotations

import hashlib
import ctypes
import json
import os
import platform
import resource
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import types

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EXPECTED_VENV = "/home/alphaadmin/AlphaV11_Dev/venv"
MEMFS_LIBRARY_RELPATH = (
    "lib/python3.12/site-packages/eccodeslib/lib64/libeccodes_memfs.so"
)
SHIM_SOURCE = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "V11_R09_GATE3_DECODER_POINTOFUSE_OBSERVATION_20261001_shim.c",
)
SIBLING_DECODER_PROBE = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "V11_R09_GATE3_DECODER_BUILD_OBSERVATION_20261001_probe.py",
)
STATIC_OBSERVATION_JSON = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "V11_R09_GATE3_MEMFS_STATIC_OBSERVATION_20261001.json",
)
SIBLING_REVIEWED_COMMIT = "95f03393467fd65e851721dedc9fffe991889552"
SIBLING_REVIEWED_BLOB = "d814a6f5a9139647bc68d8a782d97b2046b62051"
SIBLING_RELPATH = "docs/V11_R09_GATE3_DECODER_BUILD_OBSERVATION_20261001_probe.py"

NEGATIVE_DECODE_SNIPPET = (
    "import sys; sys.path.insert(0, 'tests'); sys.path.insert(0, '.');\n"
    "import eccodes\n"
    "from test_v11_grib_fields import grib\n"
    "h = eccodes.codes_new_from_message(grib(member=0, hour=6, run=1790640000.0, packing=0))\n"
    "eccodes.codes_release(h)\n"
    "print('DECODE_OK')\n"
)


def _sha256_and_size(path):
    h = hashlib.sha256()
    size = 0
    with open(path, "rb") as f:
        while True:
            chunk = f.read(1024 * 1024)
            if not chunk:
                break
            h.update(chunk)
            size += len(chunk)
    return h.hexdigest(), size


def _git(args):
    return subprocess.run(
        ["git", *args], cwd=REPO_ROOT, capture_output=True, text=True,
        timeout=10, check=True,
    ).stdout.strip()


def _verified_sibling_source(path=SIBLING_DECODER_PROBE):
    """Return only bytes equal to the pinned reviewed blob; execute these bytes."""
    ref = f"{SIBLING_REVIEWED_COMMIT}:{SIBLING_RELPATH}"
    if _git(["rev-parse", ref]) != SIBLING_REVIEWED_BLOB:
        raise ValueError("reviewed sibling commit/blob binding changed")
    expected = subprocess.run(
        ["git", "cat-file", "blob", SIBLING_REVIEWED_BLOB], cwd=REPO_ROOT,
        capture_output=True, timeout=10, check=True,
    ).stdout
    with open(path, "rb") as f:
        source = f.read()
    if source != expected:
        raise ValueError("sibling bytes differ from pinned reviewed blob")
    return source


def _load_verified_sibling(path=SIBLING_DECODER_PROBE):
    source = _verified_sibling_source(path)
    module = types.ModuleType("decoder_build_observation_probe")
    module.__file__ = path
    exec(compile(source, path, "exec"), module.__dict__)
    return module, hashlib.sha256(source).hexdigest()


def _compile_shim(workdir):
    shim_so = os.path.join(workdir, "shim.so")
    proc = subprocess.run(
        ["gcc", "-shared", "-fPIC", "-O2", "-Wall", "-Wextra",
         "-o", shim_so, SHIM_SOURCE, "-ldl"],
        capture_output=True, text=True, timeout=60,
    )
    return {
        "source": SHIM_SOURCE,
        "source_sha256": _sha256_and_size(SHIM_SOURCE)[0],
        "compiler_argv": proc.args,
        "exit_code": proc.returncode,
        "stderr": proc.stderr.strip(),
        "shim_so": shim_so if proc.returncode == 0 else None,
    }


def _run_negative_probe_a(shim_so, workdir):
    """Wrong library path + shim preloaded must abort loudly."""
    env = dict(os.environ)
    env["LD_PRELOAD"] = shim_so
    env["ALPHA_V11_MEMFS_LIBRARY_PATH"] = os.path.join(workdir, "does_not_exist.so")
    env["ALPHA_V11_MEMFS_TRACE_PATH"] = os.path.join(workdir, "negA.trace.jsonl")
    env["ALPHA_V11_MEMFS_DUMP_DIR"] = workdir
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    proc = subprocess.run(
        [sys.executable, "-c", NEGATIVE_DECODE_SNIPPET], cwd=REPO_ROOT, env=env,
        capture_output=True, text=True, timeout=60,
    )
    return {
        "description": "LD_PRELOAD active with a nonexistent ALPHA_V11_MEMFS_LIBRARY_PATH",
        "exit_code": proc.returncode,
        "aborted_nonzero": proc.returncode != 0,
        "stderr_contains_fatal": "FATAL" in proc.stderr,
        "expected": "aborted_nonzero=True and stderr_contains_fatal=True",
        "passed": proc.returncode != 0 and "FATAL" in proc.stderr,
    }


def _run_negative_probe_b(workdir):
    """No LD_PRELOAD: decode must succeed and no trace file may appear."""
    trace_path = os.path.join(workdir, "negB.trace.jsonl")
    env = {k: v for k, v in os.environ.items() if not k.startswith("ALPHA_V11_MEMFS_")}
    env.pop("LD_PRELOAD", None)
    env["ALPHA_V11_MEMFS_TRACE_PATH"] = trace_path
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    proc = subprocess.run(
        [sys.executable, "-c", NEGATIVE_DECODE_SNIPPET], cwd=REPO_ROOT, env=env,
        capture_output=True, text=True, timeout=60,
    )
    trace_exists = os.path.exists(trace_path)
    return {
        "description": "No LD_PRELOAD; decode must succeed with no interception trace",
        "exit_code": proc.returncode,
        "decode_ok": proc.returncode == 0 and "DECODE_OK" in proc.stdout,
        "trace_file_created": trace_exists,
        "expected": "decode_ok=True and trace_file_created=False",
        "passed": proc.returncode == 0 and "DECODE_OK" in proc.stdout and not trace_exists,
    }


def _run_negative_probe_c(shim_so, workdir, memfs_library_path):
    """A writable-looking sink that fails on flush must abort."""
    env = dict(os.environ)
    env.update({
        "LD_PRELOAD": shim_so,
        "ALPHA_V11_MEMFS_LIBRARY_PATH": memfs_library_path,
        "ALPHA_V11_MEMFS_TRACE_PATH": "/dev/full",
        "ALPHA_V11_MEMFS_DUMP_DIR": workdir,
        "PYTHONDONTWRITEBYTECODE": "1",
    })
    proc = subprocess.run(
        [sys.executable, "-c", NEGATIVE_DECODE_SNIPPET], cwd=REPO_ROOT, env=env,
        capture_output=True, text=True, timeout=60,
    )
    return {
        "description": "Genuine shim and library, trace sink /dev/full",
        "exit_code": proc.returncode,
        "stderr_contains_fatal": "FATAL" in proc.stderr,
        "passed": proc.returncode != 0 and "FATAL" in proc.stderr,
    }


def _run_main_capture(shim_so, workdir, memfs_library_path):
    trace_path = os.path.join(workdir, "main.trace.jsonl")
    dump_dir = os.path.join(workdir, "dumps")
    os.makedirs(dump_dir, exist_ok=True)
    env = dict(os.environ)
    env["LD_PRELOAD"] = shim_so
    env["ALPHA_V11_MEMFS_LIBRARY_PATH"] = memfs_library_path
    env["ALPHA_V11_MEMFS_TRACE_PATH"] = trace_path
    env["ALPHA_V11_MEMFS_DUMP_DIR"] = dump_dir
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["_ALPHA_V11_POU_CHILD"] = "1"
    proc = subprocess.run(
        [sys.executable, os.path.abspath(__file__)], cwd=REPO_ROOT, env=env,
        capture_output=True, text=True, timeout=90,
    )
    return proc, trace_path, dump_dir


def _child_main_capture():
    """Runs inside the LD_PRELOAD-active subprocess spawned by _run_main_capture."""
    resource.setrlimit(resource.RLIMIT_CPU, (60, 60))
    resource.setrlimit(resource.RLIMIT_AS, (512 * 1024 * 1024, 512 * 1024 * 1024))

    class _SocketBlocked(socket.socket):
        def __init__(self, *_a, **_k):
            raise RuntimeError("socket creation blocked in capture process")

    socket.socket = _SocketBlocked

    memfs_library_path = os.environ["ALPHA_V11_MEMFS_LIBRARY_PATH"]
    sha_before, size_before = _sha256_and_size(memfs_library_path)

    sibling, sibling_sha256 = _load_verified_sibling()
    decode_result = sibling._run_bounded_synthetic_ccsds_decode()
    completion = ctypes.CDLL(None).alpha_v11_memfs_trace_complete
    completion.restype = ctypes.c_long
    completed_calls = completion()

    sha_after, size_after = _sha256_and_size(memfs_library_path)

    print(json.dumps({
        "child_schema": "R09_GATE3_DECODER_POINTOFUSE_CHILD_V1",
        "sibling_probe_sha256": sibling_sha256,
        "sibling_probe_reviewed_commit": SIBLING_REVIEWED_COMMIT,
        "sibling_probe_reviewed_blob": SIBLING_REVIEWED_BLOB,
        "sibling_probe_verified_before_execution": True,
        "trace_completed_call_count": completed_calls,
        "installed_library_sha256_before": sha_before,
        "installed_library_sha256_after": sha_after,
        "installed_library_size_before": size_before,
        "installed_library_size_after": size_after,
        "installed_library_unchanged": sha_before == sha_after and size_before == size_after,
        "decode_result": decode_result,
    }))
    return 0


def _parse_trace(trace_path, expected_count):
    calls_open, calls_exists = [], []
    with open(trace_path, "rb") as f:
        lines = f.readlines()
    if not lines or any(not line.endswith(b"\n") for line in lines):
        raise ValueError("missing or truncated trace")
    for number, line in enumerate(lines, 1):
        rec = json.loads(line)
        if not isinstance(rec, dict):
            raise ValueError("trace record is not an object")
        if rec.get("record_type") == "completion":
            if number != len(lines) or set(rec) != {"record_type", "call_count"}:
                raise ValueError("misplaced or malformed completion")
            if type(rec["call_count"]) is not int or rec["call_count"] != number - 1:
                raise ValueError("completion count differs from trace length")
            if rec["call_count"] != expected_count:
                raise ValueError("completion count differs from child result")
            continue
        if number == len(lines):
            raise ValueError("missing completion record")
        if rec.get("call_index") != number or type(rec.get("call_index")) is not int:
            raise ValueError("duplicate, missing or gapped call index")
        if not isinstance(rec.get("path"), str):
            raise ValueError("invalid call path")
        if rec.get("function") == "codes_memfs_open":
            if set(rec) == {"call_index", "function", "path", "result"}:
                if rec["result"] is not None:
                    raise ValueError("invalid null open result")
            elif set(rec) == {"call_index", "function", "path", "size", "dump_file"}:
                if type(rec["size"]) is not int or rec["size"] < 0 or not isinstance(rec["dump_file"], str):
                    raise ValueError("invalid open record")
            else:
                raise ValueError("invalid open record fields")
            calls_open.append(rec)
        elif rec.get("function") == "codes_memfs_exists":
            if set(rec) != {"call_index", "function", "path", "result"} or type(rec["result"]) is not int or rec["result"] not in (0, 1):
                raise ValueError("invalid exists record")
            calls_exists.append(rec)
        else:
            raise ValueError("unknown trace record type")
    return calls_open, calls_exists


def _run_trace_integrity_controls(trace_path, expected_count, workdir):
    with open(trace_path, "rb") as f:
        lines = f.readlines()
    suffix_loss = min(7, expected_count)
    variants = {
        "missing_suffix": lines[:-(suffix_loss + 1)] + lines[-1:],
        "missing_completion": lines[:-1],
        "duplicate_record": lines[:2] + lines[1:2] + lines[2:],
        "gapped_record": lines[:1] + lines[2:],
        "invalid_record_type": [b'{"call_index":1,"function":"invalid","path":"x"}\n'] + lines[1:],
        "truncated_record": lines[:-1] + [lines[-1][:-1]],
    }
    results = {}
    for name, contents in variants.items():
        path = os.path.join(workdir, f"control_{name}.trace.jsonl")
        with open(path, "wb") as f:
            f.writelines(contents)
        try:
            _parse_trace(path, expected_count)
        except (ValueError, KeyError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            results[name] = {"passed": True, "rejection": str(exc)}
        else:
            results[name] = {"passed": False}
    return results


def _run_sibling_mismatch_control(workdir):
    path = os.path.join(workdir, "unreviewed_sibling.py")
    with open(path, "w", encoding="utf-8") as f:
        f.write("raise RuntimeError('UNREVIEWED_SIBLING_EXECUTED')\n")
    try:
        _load_verified_sibling(path)
    except ValueError as exc:
        return {"passed": True, "rejection": str(exc), "marker_executed": False}
    except RuntimeError as exc:
        return {"passed": False, "marker_executed": True, "error": str(exc)}
    return {"passed": False, "marker_executed": False}


def _cross_check(calls_open, calls_exists, dump_dir, memfs_library_path):
    with open(STATIC_OBSERVATION_JSON, encoding="utf-8") as f:
        static_doc = json.load(f)
    by_path = {e["path"]: e for e in static_doc["full_entry_inventory"]}
    installed_bytes = open(memfs_library_path, "rb").read()
    static_input = static_doc["input_verification"]
    static_input_matches = (
        static_doc["target_path"] == memfs_library_path
        and static_input["observed_byte_length"] == len(installed_bytes)
        and static_input["observed_sha256"] == hashlib.sha256(installed_bytes).hexdigest()
        and static_input["matches"]
    )

    open_checks = []
    mismatches = 0
    not_in_table = 0
    for rec in calls_open:
        if rec.get("size") is None:
            open_checks.append({**rec, "null_result": True})
            continue
        dump_hash, dump_size = _sha256_and_size(rec["dump_file"])
        entry = by_path.get(rec["path"])
        if entry is None:
            not_in_table += 1
            open_checks.append({**rec, "in_static_table": False})
            continue
        direct = installed_bytes[entry["file_offset"]: entry["file_offset"] + entry["symbol_size"]]
        direct_hash = hashlib.sha256(direct).hexdigest()
        three_way_match = (
            dump_hash == entry["sha256"] == direct_hash
            and dump_size == entry["symbol_size"] == rec["size"]
        )
        if not three_way_match:
            mismatches += 1
        open_checks.append({
            "call_index": rec["call_index"],
            "path": rec["path"],
            "in_static_table": True,
            "symbol": entry["symbol"],
            "shim_dump_sha256": dump_hash,
            "static_table_sha256": entry["sha256"],
            "independent_direct_read_sha256": direct_hash,
            "three_way_match": three_way_match,
        })

    exists_checks = []
    exists_mismatches = 0
    for rec in calls_exists:
        present = rec["path"] in by_path
        expected = 1 if present else 0
        ok = rec["result"] == expected
        if not ok:
            exists_mismatches += 1
        exists_checks.append({
            "call_index": rec["call_index"],
            "path": rec["path"],
            "result": rec["result"],
            "present_in_static_table": present,
            "matches_expectation": ok,
        })

    distinct_open_paths = sorted({c["path"] for c in open_checks if c.get("in_static_table")})
    return {
        "static_inventory_input_matches_installed_library": static_input_matches,
        "total_open_calls": len(calls_open),
        "distinct_open_paths": len(distinct_open_paths),
        "total_exists_calls": len(calls_exists),
        "distinct_exists_paths": len(sorted({c["path"] for c in calls_exists})),
        "open_three_way_mismatches": mismatches,
        "open_paths_not_in_static_table": not_in_table,
        "exists_boolean_mismatches": exists_mismatches,
        "template_5_42_exercised": "/MEMFS/definitions/grib2/templates/template.5.42.def" in distinct_open_paths,
        "distinct_open_paths_list": distinct_open_paths,
        "open_checks": open_checks,
        "exists_checks": exists_checks,
    }


def main():
    if os.environ.get("_ALPHA_V11_POU_CHILD") == "1":
        return _child_main_capture()

    report = {"schema": "R09_GATE3_DECODER_POINTOFUSE_OBSERVATION_PROBE_V1"}
    report["observed_utc"] = int(time.time())
    report["interpreter"] = {
        "executable": sys.executable,
        "version": sys.version,
        "platform": platform.platform(),
    }
    report["venv_matches_expected"] = sys.executable.startswith(EXPECTED_VENV)
    report["repo_head"] = _git(["rev-parse", "HEAD"])
    report["repo_tree"] = _git(["rev-parse", "HEAD^{tree}"])
    report["repo_worktree_clean"] = _git(["status", "--porcelain"]) == ""
    report["probe_sha256"] = _sha256_and_size(os.path.abspath(__file__))[0]
    report["static_observation_json_sha256"] = _sha256_and_size(STATIC_OBSERVATION_JSON)[0]

    memfs_library_path = os.path.join(EXPECTED_VENV, MEMFS_LIBRARY_RELPATH)
    report["memfs_library_path"] = memfs_library_path
    report["memfs_library_sha256"], report["memfs_library_size"] = _sha256_and_size(memfs_library_path)

    workdir = tempfile.mkdtemp(prefix="alpha_v11_pou_")
    report["workdir"] = workdir

    compile_result = _compile_shim(workdir)
    report["shim_compile"] = compile_result
    if compile_result["exit_code"] != 0 or not compile_result["shim_so"]:
        report["status"] = "SHIM_COMPILE_FAILED"
        print(json.dumps(report, indent=2, sort_keys=True))
        return 1
    shim_so = compile_result["shim_so"]

    report["negative_probe_a"] = _run_negative_probe_a(shim_so, workdir)
    report["negative_probe_b"] = _run_negative_probe_b(workdir)
    report["negative_probe_c_failed_sink"] = _run_negative_probe_c(shim_so, workdir, memfs_library_path)
    report["sibling_mismatch_control"] = _run_sibling_mismatch_control(workdir)

    proc, trace_path, dump_dir = _run_main_capture(shim_so, workdir, memfs_library_path)
    report["main_capture_exit_code"] = proc.returncode
    report["main_capture_stderr_tail"] = proc.stderr[-2000:]
    if proc.returncode != 0:
        report["status"] = "MAIN_CAPTURE_FAILED"
        print(json.dumps(report, indent=2, sort_keys=True))
        return 1
    try:
        child_report = json.loads(proc.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        report["status"] = "MAIN_CAPTURE_UNPARSEABLE_OUTPUT"
        report["main_capture_stdout"] = proc.stdout[-4000:]
        print(json.dumps(report, indent=2, sort_keys=True))
        return 1
    report["main_capture"] = child_report

    try:
        calls_open, calls_exists = _parse_trace(trace_path, child_report["trace_completed_call_count"])
    except (OSError, ValueError, KeyError, TypeError) as exc:
        report["status"] = "TRACE_INTEGRITY_FAILED"
        report["trace_error"] = str(exc)
        print(json.dumps(report, indent=2, sort_keys=True))
        return 1
    report["trace_integrity"] = {
        "completed_call_count": child_report["trace_completed_call_count"],
        "typed_contiguous_records": True,
        "controls": _run_trace_integrity_controls(trace_path, child_report["trace_completed_call_count"], workdir),
    }
    report["cross_check"] = _cross_check(calls_open, calls_exists, dump_dir, memfs_library_path)

    negatives_ok = (
        report["negative_probe_a"]["passed"]
        and report["negative_probe_b"]["passed"]
        and report["negative_probe_c_failed_sink"]["passed"]
        and report["sibling_mismatch_control"]["passed"]
        and all(control["passed"] for control in report["trace_integrity"]["controls"].values())
    )
    cross_ok = (
        report["cross_check"]["static_inventory_input_matches_installed_library"]
        and report["cross_check"]["total_open_calls"] > 0
        and report["cross_check"]["total_exists_calls"] > 0
        and report["cross_check"]["template_5_42_exercised"]
        and report["cross_check"]["open_three_way_mismatches"] == 0
        and report["cross_check"]["open_paths_not_in_static_table"] == 0
        and report["cross_check"]["exists_boolean_mismatches"] == 0
    )
    report["status"] = (
        "POINTOFUSE_OBSERVED_CONSISTENT"
        if negatives_ok and cross_ok and child_report["installed_library_unchanged"]
        and child_report["sibling_probe_verified_before_execution"]
        and child_report["installed_library_sha256_before"] == report["memfs_library_sha256"]
        and child_report["decode_result"]["simple_packing_probe_ok"]
        and all(
            outcome["ccsds_template_observed"] == 42 and outcome["decoded_kelvin"] == 290.0
            for outcome in child_report["decode_result"]["ccsds_probe_outcomes"].values()
        )
        else "POINTOFUSE_OBSERVATION_INCONSISTENT"
    )
    report["disclaimer"] = (
        "UNREVIEWED_BUILD_OBSERVATION / NOT_QUALIFIED. No provider request, "
        "no CCSDS enablement beyond the already-reviewed offline synthetic "
        "fixture, no financial/promotion/host/launch authority. G3-L NO-GO."
    )
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["status"] == "POINTOFUSE_OBSERVED_CONSISTENT" else 1


if __name__ == "__main__":
    sys.exit(main())
