"""Offline, read-only corroboration of retained A2/A3 bytes; no trust verdict."""

import argparse
import base64
import csv
import hashlib
import io
import json
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
DOSSIER = ROOT / "docs/V11_R09_GATE3_A2A3_OFFLINE_DOSSIER_20261001.json"
SITES = {
    "alpha_dev": Path("/home/alphaadmin/AlphaV11_Dev/venv/lib/python3.12/site-packages"),
    "ecmwf_backfill": Path("/home/alphaadmin/AlphaV11_ECMWFBackfill/Alpha/.venv/lib/python3.12/site-packages"),
    "brain_history": Path("/home/alphaadmin/AlphaV11_BrainWork/history_env/lib/python3.12/site-packages"),
}
TAG = re.compile(r"\((NEEDED|RPATH|RUNPATH|SONAME)\).*?\[([^]]+)\]")
READELF = Path("/usr/bin/readelf")


def digest(path):
    h = hashlib.sha256()
    size = 0
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
            size += len(block)
    return h.hexdigest(), size


def record_rows(path):
    return {row[0]: row for row in csv.reader(io.StringIO(path.read_text()))}


def record_hash(value):
    algorithm, encoded = value.split("=", 1)
    if algorithm != "sha256":
        raise ValueError("unsupported RECORD algorithm")
    return base64.urlsafe_b64decode(encoded + "===").hex()


def elf_dynamic(path):
    result = subprocess.run([str(READELF), "-d", str(path)], capture_output=True,
                            text=True, check=True)
    dynamic = {key: [] for key in ("NEEDED", "RPATH", "RUNPATH", "SONAME")}
    for line in result.stdout.splitlines():
        match = TAG.search(line)
        if match:
            dynamic[match.group(1)].append(match.group(2))
    return dynamic


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, help="write JSON only at this explicit path")
    args = parser.parse_args()
    prior = json.loads(DOSSIER.read_text())
    expected = prior["record_discrepancies"]
    if len(expected) != 25 or len({e["path"] for e in expected}) != 25:
        raise RuntimeError("historical dossier does not contain 25 unique rows")
    records = {}
    for label, site in SITES.items():
        path = site / "eckitlib-2.3.0.30.dist-info/RECORD"
        records[label] = {"sha256": digest(path)[0], "rows": record_rows(path)}
    if len({value["sha256"] for value in records.values()}) != 1:
        raise RuntimeError("retained RECORD copies disagree")
    rows = []
    for old in expected:
        name = old["path"]
        copies = {}
        for label, site in SITES.items():
            row = records[label]["rows"][name]
            if (record_hash(row[1]), int(row[2])) != (old["record_sha256"], old["record_bytes"]):
                raise RuntimeError(f"RECORD row drift: {label}: {name}")
            actual = digest(site / name)
            if actual != (old["cached_wheel_and_installed_sha256"],
                          old["cached_wheel_and_installed_bytes"]):
                raise RuntimeError(f"payload drift: {label}: {name}")
            if actual == (old["record_sha256"], old["record_bytes"]):
                raise RuntimeError(f"expected discrepancy absent: {label}: {name}")
            copies[label] = {"sha256": actual[0], "bytes": actual[1]}
        rows.append({"path": name, "record_sha256": old["record_sha256"],
                     "record_bytes": old["record_bytes"], "copies": copies,
                     "cause": "UNDETERMINED", "review_verdict": "UNRESOLVED_DO_NOT_QUALIFY"})

    # This graph is restricted to the historical mapped-file set. A name match is
    # an observed candidate, not a proof of the loader's resolution or lazy loads.
    mapped = prior["native_mapped_file_observations"]
    names = {}
    dynamics = {}
    for entry in mapped:
        path = Path(entry["path"])
        if digest(path) != (entry["sha256"], entry["bytes"]):
            raise RuntimeError(f"mapped-file drift: {path}")
        dynamic = elf_dynamic(path)
        dynamics[str(path)] = dynamic
        for name in {path.name, *dynamic["SONAME"]}:
            names.setdefault(name, []).append(str(path))
    graph = []
    for entry in mapped:
        path = Path(entry["path"])
        dynamic = dynamics[str(path)]
        graph.append({"path": str(path), "sha256": entry["sha256"],
                      "dynamic": dynamic,
                      "needed_candidates_in_historical_map": [
                          {"soname": name, "paths": names.get(name, [])}
                          for name in dynamic["NEEDED"]]})

    site = SITES["alpha_dev"]
    config_paths = [
        site / "eckitlib/lib64/pkgconfig/eckit.pc",
        site / "eckitlib/lib64/cmake/eckit/eckit-targets-minsizerel.cmake",
        site / "eccodeslib/lib64/cmake/eccodes/eccodes-targets-minsizerel.cmake",
        site / "eccodeslib/include/eccodes_ecbuild_config.h",
        site / "eccodeslib/include/eccodes_config.h",
    ]
    build_metadata = []
    for path in config_paths:
        sha, size = digest(path)
        lines = path.read_text(errors="replace").splitlines()
        markers = [line.strip() for line in lines if
                   line.startswith(("CXX=", "CMAKE_BUILD_TYPE=")) or
                   "IMPORTED_CONFIGURATIONS MINSIZEREL" in line or
                   "#define HAVE_" in line]
        build_metadata.append({"path": str(path), "sha256": sha,
                               "bytes": size, "selected_markers": markers[:80]})

    cache = Path(prior["cache"]["path"])
    report = {
        "schema": "ALPHA_V11_GATE3_A2A3_RETAINED_AUDIT_V1",
        "observed_utc": datetime.now(timezone.utc).isoformat(),
        "historical_dossier_sha256": digest(DOSSIER)[0],
        "readelf": {"path": str(READELF), "sha256": digest(READELF)[0],
                    "version_line": subprocess.run([str(READELF), "--version"],
                        capture_output=True, text=True, check=True).stdout.splitlines()[0]},
        "historical_cache": {"path": str(cache), "present_now": cache.is_file(),
                             "prior_sha256": prior["cache"]["sha256"]},
        "record_copies": {label: value["sha256"] for label, value in records.items()},
        "record_discrepancies": rows,
        "historical_map_elf_dynamic": graph,
        "historical_map_static_edge_summary": {
            "mapped_files": len(graph),
            "needed_edges": sum(len(item["dynamic"]["NEEDED"]) for item in graph),
            "missing_from_historical_map_by_filename_or_soname": sum(
                not candidate["paths"] for item in graph
                for candidate in item["needed_candidates_in_historical_map"]),
            "ambiguous_in_historical_map": sum(
                len(candidate["paths"]) > 1 for item in graph
                for candidate in item["needed_candidates_in_historical_map"]),
            "scope": "static DT_NEEDED names within historical mapped set only; excludes lazy loads and loader proof",
        },
        "packaged_build_metadata": build_metadata,
        "qualification_credit": 0,
        "authenticated_original": False,
        "complete_loader_closure": False,
        "reproducible_installation_verified": False,
        "provider_requests": 0,
        "package_installations": 0,
    }
    serialized = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.write_text(serialized)
    else:
        print(serialized, end="")


if __name__ == "__main__":
    main()
