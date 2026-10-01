"""Read-only, offline A2/A3 installed-byte dossier; no qualification verdict."""

import base64
import csv
import hashlib
import io
import json
import sys
import zipfile
from datetime import datetime, timezone
from importlib.metadata import PathDistribution
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
OBSERVATION = ROOT / "docs/V11_R09_GATE3_DECODER_BUILD_OBSERVATION_20261001.json"
REVIEW = ROOT / "docs/V11_R09_GATE3_DECODER_BUILD_REVIEW_82e1619.json"
SITE = Path("/home/alphaadmin/AlphaV11_Dev/venv/lib/python3.12/site-packages")
PACKAGE_NAMES = ("eccodes", "eccodeslib", "eckitlib", "findlibs", "numpy", "attrs", "cffi", "pycparser")


def digest_stream(stream):
    h = hashlib.sha256()
    size = 0
    for part in iter(lambda: stream.read(1024 * 1024), b""):
        h.update(part)
        size += len(part)
    return h.hexdigest(), size


def digest_file(path):
    with Path(path).open("rb") as stream:
        return digest_stream(stream)


def sha_from_record(value):
    algorithm, encoded = value.split("=", 1)
    if algorithm != "sha256":
        raise ValueError("unexpected RECORD algorithm")
    return base64.urlsafe_b64decode(encoded + "===").hex()


def package_evidence(name):
    matches = sorted(SITE.glob(name.replace("-", "_") + "-*.dist-info"))
    if len(matches) != 1:
        raise RuntimeError(f"expected one installed dist-info for {name}: {matches}")
    dist = PathDistribution(matches[0])
    record = matches[0] / "RECORD"
    record_hash, record_size = digest_file(record)
    rows = list(csv.reader(io.StringIO(record.read_text())))
    mismatches = []
    missing = []
    for relpath, declared_hash, declared_size in rows:
        if not declared_hash:
            continue
        installed = SITE / relpath
        if not installed.is_file():
            missing.append(relpath)
            continue
        actual_hash, actual_size = digest_file(installed)
        if (actual_hash, actual_size) != (sha_from_record(declared_hash), int(declared_size)):
            mismatches.append(relpath)
    return {
        "name": name,
        "installed_version": dist.version,
        "dist_info": str(matches[0]),
        "metadata_sha256": digest_file(matches[0] / "METADATA")[0],
        "record_sha256": record_hash,
        "record_bytes": record_size,
        "record_rows": len(rows),
        "unhashed_record_rows": sum(not row[1] for row in rows),
        "missing_hashed_rows": missing,
        "mismatched_hashed_rows": mismatches,
        "requires_dist": dist.requires or [],
        "direct_url_present": (matches[0] / "direct_url.json").exists(),
        "authenticated_original": False,
        "reproducible_installation_verified": False,
    }


def main():
    observation = json.loads(OBSERVATION.read_text())
    review = json.loads(REVIEW.read_text())
    if review["verdict"] != "PASS_OBSERVATION_ONLY":
        raise RuntimeError("build observation has no accepted observation review")
    cached = observation["local_cached_wheel_comparison"]
    archive = Path(cached["cache_locator"])
    archive_hash, archive_size = digest_file(archive)
    if (archive_hash, archive_size) != (cached["archive_sha256"], cached["archive_bytes"]):
        raise RuntimeError("cached archive changed")
    original_mismatches = next(
        p["mismatched_entry_details"]
        for p in observation["python_package_record_checks"]
        if p["dist_info"].startswith("eckitlib-")
    )
    if len(original_mismatches) != 25:
        raise RuntimeError("expected 25 accepted-observation discrepancies")
    expected = {item["path"]: item for item in original_mismatches}
    discrepancies = []
    with zipfile.ZipFile(archive) as wheel:
        wheel_record_name = next(n for n in wheel.namelist() if n.endswith("eckitlib-2.3.0.30.dist-info/RECORD"))
        wheel_record = wheel.read(wheel_record_name)
        wheel_rows = {row[0]: row for row in csv.reader(io.StringIO(wheel_record.decode()))}
        installed_record = (SITE / "eckitlib-2.3.0.30.dist-info/RECORD").read_bytes()
        installed_rows = {row[0]: row for row in csv.reader(io.StringIO(installed_record.decode()))}
        for name in sorted(expected):
            if wheel_rows[name] != installed_rows[name]:
                raise RuntimeError(f"changed discrepancy RECORD row: {name}")
            _, recorded_hash, recorded_size = wheel_rows[name]
            with wheel.open(name) as stream:
                wheel_hash, wheel_size = digest_stream(stream)
            installed_hash, installed_size = digest_file(SITE / name)
            record_hash = sha_from_record(recorded_hash)
            if (wheel_hash, wheel_size) != (installed_hash, installed_size):
                raise RuntimeError(f"installed/cached divergence: {name}")
            if (record_hash, int(recorded_size)) == (wheel_hash, wheel_size):
                raise RuntimeError(f"missing expected RECORD discrepancy: {name}")
            if (record_hash, int(recorded_size), installed_hash, installed_size) != (
                expected[name]["record_sha256"], expected[name]["record_bytes"],
                expected[name]["observed_sha256"], expected[name]["observed_bytes"]
            ):
                raise RuntimeError(f"accepted observation changed: {name}")
            discrepancies.append({
                "path": name,
                "record_sha256": record_hash,
                "record_bytes": int(recorded_size),
                "cached_wheel_and_installed_sha256": wheel_hash,
                "cached_wheel_and_installed_bytes": wheel_size,
                "delta_bytes": wheel_size - int(recorded_size),
                "local_comparison": "INSTALLED_EQUALS_CACHED_WHEEL_PAYLOAD; BOTH_DISAGREE_WITH_RECORD",
                "cause": "UNDETERMINED",
                "authenticated_original": "MISSING",
                "review_verdict": "UNRESOLVED_DO_NOT_QUALIFY",
            })
    prior_paths = {entry["path"] for entry in observation["loaded_native_libraries"]}
    extra_paths = review["independent_comparison"]["unlisted_mapped_shared_objects"]
    native = []
    for path in sorted(prior_paths | set(extra_paths)):
        p = Path(path)
        h, size = digest_file(p)
        native.append({
            "path": path, "sha256": h, "bytes": size,
            "observation_class": "FILTERED_DECODER_MAP" if path in prior_paths else
            ("REVIEW_CONTAINMENT_ONLY" if "libseccomp" in path else "REVIEW_UNCLASSIFIED_MAP"),
            "runtime_dependency_proved": False,
        })
    for entry in observation["loaded_native_libraries"]:
        now = next(n for n in native if n["path"] == entry["path"])
        if (now["sha256"], now["bytes"]) != (entry["sha256"], entry["byte_length"]):
            raise RuntimeError(f"native installed byte drift: {entry['path']}")
    interpreter = Path(observation["interpreter_platform"]["executable"]).resolve()
    interpreter_hash, interpreter_bytes = digest_file(interpreter)
    report = {
        "schema": "ALPHA_V11_GATE3_A2A3_OFFLINE_DOSSIER_V1",
        "observed_utc": datetime.now(timezone.utc).isoformat(),
        "verdict": "OBSERVATION_ONLY_A2_A3_UNQUALIFIED",
        "g3l": "NO-GO",
        "source_observation_sha256": digest_file(OBSERVATION)[0],
        "source_review_sha256": digest_file(REVIEW)[0],
        "cache": {"path": str(archive), "sha256": archive_hash, "bytes": archive_size,
                  "wheel_record_sha256": hashlib.sha256(wheel_record).hexdigest(),
                  "installed_record_sha256": hashlib.sha256(installed_record).hexdigest(),
                  "authenticated_origin": False},
        "interpreter": {"observed_executable": observation["interpreter_platform"]["executable"],
                        "resolved_path": str(interpreter), "sha256": interpreter_hash,
                        "bytes": interpreter_bytes, "abi": observation["interpreter_platform"]["abi_tag"]},
        "python_packages": [package_evidence(name) for name in PACKAGE_NAMES],
        "record_discrepancies": discrepancies,
        "native_mapped_file_observations": native,
        "native_observation_counts": {
            "filtered_decoder_map": len(prior_paths),
            "review_unclassified_or_containment_map": len(set(extra_paths)),
            "total_unique": len(native),
        },
        "missing_evidence": [
            "independently authenticated original wheels/source and signatures or trusted release digests for all eight packages",
            "authenticated eckitlib original and per-file explanation/review for all 25 internal RECORD discrepancies",
            "exact build recipe, toolchain, options, patches and platform/ABI lineage for native wheel contents",
            "reviewed hashes of complete Python/runtime/native/loader transitive closure and environment/search rules",
            "separate unprivileged reconstruction from pinned authenticated artifacts with exact output hash comparison",
            "reviewed runtime selection and immutable point-of-use binding; MEMFS definition/sample origins and semantics",
        ],
        "reconstruction_attempted": False,
        "provider_requests": 0,
        "package_installations": 0,
        "qualification_credit": 0,
    }
    json.dump(report, sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write("\n")


if __name__ == "__main__":
    main()
