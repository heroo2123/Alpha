#!/usr/bin/env python3
"""Offline decoder-build byte inventory probe for Gate 3 item 3.

UNREVIEWED_BUILD_OBSERVATION only. Not a qualification, acceptance or launch
result. Makes no network connection (sockets are disabled below), writes no
file to disk other than the report this script prints on stdout, and reads
only the repository decoder sources, the named Python package RECORD files,
and the native libraries they install. Run with the Alpha development venv
interpreter, from the repository root, e.g.:

    PYTHONDONTWRITEBYTECODE=1 \
      /home/alphaadmin/AlphaV11_Dev/venv/bin/python \
      docs/V11_R09_GATE3_DECODER_BUILD_OBSERVATION_20261001_probe.py

Exits nonzero if the host venv path, RECORD hashes or repository blob hashes
do not match what this report describes, so the companion .md/.json cannot
silently drift from a reread of the actual installed build.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import platform
import resource
import socket
import subprocess
import sys
import sysconfig
import time

EXPECTED_VENV = "/home/alphaadmin/AlphaV11_Dev/venv"
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

DECODER_SOURCES = [
    "polymarket_scanner/v11/ecmwf_grib.py",
    "tools/v11_r09_gate3_offline_io.py",
]

DIST_INFOS = [
    "eccodes-2.48.0.dist-info",
    "eccodeslib-2.49.0.30.dist-info",
    "eckitlib-2.3.0.30.dist-info",
    "findlibs-0.1.3.dist-info",
]

NATIVE_LIB_SUBSTRINGS = (
    "eccodes", "eckit", "libaec", "openjp2", "libpng",
)


class _SocketBlocked(socket.socket):
    def __init__(self, *_a, **_k):  # noqa: D401
        raise RuntimeError("socket creation blocked in capture process")


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


def _record_b64_to_hex(record_field):
    # RECORD hashes are "sha256=<urlsafe-base64-no-padding>".
    assert record_field.startswith("sha256=")
    b64 = record_field[len("sha256="):]
    padded = b64 + "=" * (-len(b64) % 4)
    return base64.urlsafe_b64decode(padded).hex()


def _git_blob_sha1(path):
    out = subprocess.run(
        ["git", "hash-object", path], cwd=REPO_ROOT,
        capture_output=True, text=True, timeout=10, check=True,
    )
    return out.stdout.strip()


def _git_head_blob_sha1(relpath):
    out = subprocess.run(
        ["git", "rev-parse", f"HEAD:{relpath}"], cwd=REPO_ROOT,
        capture_output=True, text=True, timeout=10, check=True,
    )
    return out.stdout.strip()


def _check_repo_sources():
    results = []
    for rel in DECODER_SOURCES:
        abs_path = os.path.join(REPO_ROOT, rel)
        sha256, size = _sha256_and_size(abs_path)
        worktree_blob = _git_blob_sha1(abs_path)
        head_blob = _git_head_blob_sha1(rel)
        results.append({
            "path": rel,
            "sha256": sha256,
            "byte_length": size,
            "git_blob_sha1_worktree": worktree_blob,
            "git_blob_sha1_HEAD": head_blob,
            "matches_committed_HEAD": worktree_blob == head_blob,
        })
    return results


def _check_dist_info(venv_site_packages, dist_info_name):
    record_path = os.path.join(venv_site_packages, dist_info_name, "RECORD")
    entries = []
    mismatches = []
    missing = []
    with open(record_path, encoding="utf-8") as f:
        for line in f:
            line = line.rstrip("\n")
            if not line:
                continue
            # RECORD rows are CSV with exactly 3 fields; paths here never
            # contain commas, so a plain split is sufficient and avoids
            # pulling in a csv-module dependency for a bounded local check.
            parts = line.rsplit(",", 2)
            if len(parts) != 3:
                missing.append({"raw_line": line, "reason": "UNPARSEABLE_RECORD_ROW"})
                continue
            rel_path, hash_field, size_field = parts
            abs_path = os.path.join(venv_site_packages, rel_path)
            if not hash_field:
                # RECORD itself and compiled .pyc entries are legitimately
                # unhashed per PEP 376; record as explicitly unattested.
                entries.append({
                    "path": rel_path, "record_hash": None,
                    "observed_sha256": None, "attested": False,
                })
                continue
            if not os.path.exists(abs_path):
                missing.append({"path": rel_path, "reason": "RECORD_LISTS_MISSING_FILE"})
                continue
            observed_sha256, observed_size = _sha256_and_size(abs_path)
            expected_sha256 = _record_b64_to_hex(hash_field)
            expected_size = int(size_field) if size_field else None
            ok = observed_sha256 == expected_sha256 and (
                expected_size is None or observed_size == expected_size
            )
            entries.append({
                "path": rel_path,
                "record_sha256": expected_sha256,
                "observed_sha256": observed_sha256,
                "record_byte_length": expected_size,
                "observed_byte_length": observed_size,
                "attested": True,
                "matches_record": ok,
            })
            if not ok:
                mismatches.append(rel_path)
    direct_url_path = os.path.join(venv_site_packages, dist_info_name, "direct_url.json")
    return {
        "dist_info": dist_info_name,
        "record_path": record_path,
        "entry_count": len(entries),
        "mismatches": mismatches,
        "missing": missing,
        "has_direct_url_json": os.path.exists(direct_url_path),
        "entries": entries,
    }


def _loaded_native_libraries():
    with open("/proc/self/maps", encoding="utf-8") as f:
        maps_text = f.read()
    seen = {}
    for line in maps_text.splitlines():
        parts = line.split()
        if len(parts) < 6:
            continue
        path = parts[-1]
        if not any(s in path for s in NATIVE_LIB_SUBSTRINGS):
            continue
        if path in seen:
            continue
        if not os.path.isfile(path):
            seen[path] = {"path": path, "resolvable": False}
            continue
        sha256, size = _sha256_and_size(path)
        seen[path] = {
            "path": path,
            "resolvable": True,
            "sha256": sha256,
            "byte_length": size,
        }
    return sorted(seen.values(), key=lambda e: e["path"])


def _run_bounded_synthetic_ccsds_decode():
    """Reuses the existing test fixture builders; constructs no new fixture."""
    tests_dir = os.path.join(REPO_ROOT, "tests")
    sys.path.insert(0, tests_dir)
    sys.path.insert(0, REPO_ROOT)
    import eccodes  # noqa: WPS433 (deliberately local/post-sandbox import)
    from test_v11_grib_fields import grib  # noqa: WPS433
    from test_v11_model_panel import TARGET, ecmwf_bytes, request  # noqa: WPS433
    from polymarket_scanner.v11.ecmwf_grib import decode_station, sections  # noqa: WPS433

    simple_raw = grib(member=0, hour=6, run=1790640000.0, packing=0)
    simple_handle = eccodes.codes_new_from_message(simple_raw)
    eccodes.codes_release(simple_handle)

    outcomes = {}
    for provider in ("ECMWF_IFS_ENS", "ECMWF_AIFS_ENS"):
        handle = eccodes.codes_new_from_message(ecmwf_bytes(provider=provider))
        try:
            eccodes.codes_set(handle, "packingType", "grid_ccsds")
            raw = eccodes.codes_get_message(handle)
        finally:
            eccodes.codes_release(handle)
        template = int.from_bytes(sections(raw)[5][9:11], "big")
        decoded = decode_station(raw, request=request(raw, provider=provider), target=TARGET)
        outcomes[provider] = {
            "ccsds_template_observed": template,
            "decoded_kelvin": decoded["kelvin"],
            "message_byte_length": len(raw),
        }
    return {
        "simple_packing_probe_ok": True,
        "ccsds_probe_outcomes": outcomes,
        "eccodes_api_version": eccodes.codes_get_api_version(),
        "eccodes_python_version": getattr(eccodes, "__version__", None),
        "definition_path": eccodes.codes_definition_path(),
        "samples_path": eccodes.codes_samples_path(),
    }


def main():
    report = {"schema": "R09_GATE3_DECODER_BUILD_OBSERVATION_PROBE_V1"}
    report["observed_utc"] = int(time.time())
    report["interpreter"] = {
        "executable": sys.executable,
        "version": sys.version,
        "platform": platform.platform(),
        "machine": platform.machine(),
        "abi_tag": sysconfig.get_config_var("SOABI"),
        "sys_platform_tag": sysconfig.get_platform(),
    }
    report["expected_venv"] = EXPECTED_VENV
    report["venv_matches_expected"] = sys.executable.startswith(EXPECTED_VENV)

    resource.setrlimit(resource.RLIMIT_CPU, (60, 60))
    resource.setrlimit(resource.RLIMIT_AS, (512 * 1024 * 1024, 512 * 1024 * 1024))
    socket.socket = _SocketBlocked
    try:
        socket.socket()
        blocked = False
    except RuntimeError:
        blocked = True
    report["socket_creation_blocked"] = blocked

    report["repo_head"] = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=REPO_ROOT,
        capture_output=True, text=True, timeout=10, check=True,
    ).stdout.strip()
    report["repo_tree"] = subprocess.run(
        ["git", "rev-parse", "HEAD^{tree}"], cwd=REPO_ROOT,
        capture_output=True, text=True, timeout=10, check=True,
    ).stdout.strip()
    report["repo_worktree_clean"] = subprocess.run(
        ["git", "status", "--porcelain"], cwd=REPO_ROOT,
        capture_output=True, text=True, timeout=10, check=True,
    ).stdout.strip() == ""

    report["decoder_sources"] = _check_repo_sources()

    site_packages = os.path.join(
        EXPECTED_VENV, f"lib/python{sys.version_info.major}.{sys.version_info.minor}",
        "site-packages",
    )
    report["site_packages"] = site_packages
    report["dist_info_record_checks"] = [
        _check_dist_info(site_packages, name) for name in DIST_INFOS
    ]

    probe_result = _run_bounded_synthetic_ccsds_decode()
    report["synthetic_decode_probe"] = probe_result

    report["loaded_native_libraries"] = _loaded_native_libraries()

    usage = resource.getrusage(resource.RUSAGE_SELF)
    report["resource_usage"] = {
        "ru_maxrss_kb": usage.ru_maxrss,
        "ru_utime_s": usage.ru_utime,
        "ru_stime_s": usage.ru_stime,
    }

    report["disclaimer"] = (
        "UNREVIEWED_BUILD_OBSERVATION / NOT_QUALIFIED. No provider request, "
        "no financial/promotion/host/launch authority. G3-L NO-GO."
    )
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
