"""Offline exact-byte verifier for the proposed current G3-L executable review.

This is a review candidate, never a G3-L identity grant or launch check.  The
historical reconciliation and its seven reusable document identities stay intact.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import subprocess
from functools import cache
from pathlib import Path

MANIFEST = "docs/V11_GATE3_CURRENT_EXECUTABLE_BINDING_20261007.json"
SOURCE_COMMIT = "fea59027cd3296e55db564a8aaece8975203706b"
SOURCE_TREE = "022fd9663ab2603639ab1451f596a170a0641d88"
HISTORICAL = {
    # Prior observations remain scoped to their own frozen source commits.
    "polymarket_scanner/v11/evidence.py": "d1c5602aa77e0d835e416d281b4a78754a3a79df",
    "polymarket_scanner/v11/forecast_features.py": "d1c5602aa77e0d835e416d281b4a78754a3a79df",
    "polymarket_scanner/v11/learning_capture.py": "d1c5602aa77e0d835e416d281b4a78754a3a79df",
    "polymarket_scanner/v11/model_artifacts.py": "d1c5602aa77e0d835e416d281b4a78754a3a79df",
    "polymarket_scanner/v11/physical_inference.py": "d1c5602aa77e0d835e416d281b4a78754a3a79df",
    "polymarket_scanner/v11/pws_admission.py": "d1c5602aa77e0d835e416d281b4a78754a3a79df",
    "tests/test_v11_gate3_attempt_runtime_wiring.py": "58e63fc8409f49d60b2c4a06efa377a6b30ee195",
    "tests/test_v11_gate3_evidence_intake_launch_wiring.py": "58e63fc8409f49d60b2c4a06efa377a6b30ee195",
    "tests/test_v11_r09_gate3_runtime.py": "58e63fc8409f49d60b2c4a06efa377a6b30ee195",
    "tools/v11_gate3_evidence_preflight_real_intake.py": "58e63fc8409f49d60b2c4a06efa377a6b30ee195",
    "tools/v11_r09_gate3_collector.py": "95e07fab75a3818560378d71556efc55ce122a56",
    "tools/v11_r09_gate3_runtime.py": "6340cb455eebae374039c9bae23cd806681dacb0",
    "tools/v11_r09_gate3_ledgers.py": "6340cb455eebae374039c9bae23cd806681dacb0",
}
DRIFT_COMMITS = {
    "polymarket_scanner/v11/evidence.py": (
        "3ae3852474ad87977ccd50aac010d3d1ed813c96",),
    "polymarket_scanner/v11/forecast_features.py": (
        "fd965a96f9bbad4d3b9e99dec11d169c8ff8c33f",),
    "polymarket_scanner/v11/learning_capture.py": (
        "90d2d6446155e928d66c16660b5e90fda3a77fb0",),
    "polymarket_scanner/v11/model_artifacts.py": (
        "fd965a96f9bbad4d3b9e99dec11d169c8ff8c33f",),
    "polymarket_scanner/v11/physical_inference.py": (
        "fd965a96f9bbad4d3b9e99dec11d169c8ff8c33f",),
    "polymarket_scanner/v11/pws_admission.py": (
        "3ae3852474ad87977ccd50aac010d3d1ed813c96",
        "0d55adbd70c74375c1dbdd9d0dcac0344f4202ce",
        "5c8ae333c8eb2d2c73fbfeb25ce6c707eb3b19a7",
        "b2bbb0df76ccbcf4300903c04eb2748a3ef61de6"),
    "tests/test_v11_gate3_attempt_runtime_wiring.py": (
        "ef4f534eaae442dd0aac149c9850f0ef3c76d78f",),
    "tests/test_v11_gate3_evidence_intake_launch_wiring.py": (
        "ef4f534eaae442dd0aac149c9850f0ef3c76d78f",),
    "tests/test_v11_r09_gate3_runtime.py": (
        "7adbd18b0fcd0ae1e970264334e1b477c980e5aa",
        "d806c11082fe81defd74993152906ee7454bce1d",
        "fe311384953e01cc132ca8fe39f8b631b2f7c52a",
        "7ff30948dfbcc0d164b6395e4749094472bfe134"),
    "tools/v11_gate3_evidence_preflight_real_intake.py": (
        "a582a005d08c0bcb62a9061946aa0bf096657360",),
    "tools/v11_r09_gate3_collector.py": (
        "ef7b47030259b79d19b2db3817b5ae660a0b4f64",),
    "tools/v11_r09_gate3_runtime.py": (
        "7ef7d5d29043be4a6c7a4f64808a26f39e7c874b",
        "9a844b1863c38ab83384d393ab0c0cae5ae5ae99",
        "70ef1a1f9155e662a166ee72359c4f3c1d5f0b19",
        "9d0e9a14aa13566ce25823c70a368cd2ce1446a5",
        "60ac12061154eedbc7f83077aa1c756cd1e7359a",
        "f1e85bab4cd272dd302945f20fc1193b122c4990",
        "5a0ce218c2815512ee592e9203d718cfc458b43a",
        "dd529ee548a6752aa21d7c53f8f662f4fdb7f570",
        "7adbd18b0fcd0ae1e970264334e1b477c980e5aa",
        "d806c11082fe81defd74993152906ee7454bce1d",
        "fe311384953e01cc132ca8fe39f8b631b2f7c52a"),
    "tools/v11_r09_gate3_ledgers.py": (
        "dd529ee548a6752aa21d7c53f8f662f4fdb7f570",
        "fe311384953e01cc132ca8fe39f8b631b2f7c52a",
        "0090c1f7967aa45db360e47f31c297f5c534400d",
        "7ff30948dfbcc0d164b6395e4749094472bfe134",
        "23f11501d26b785c549e1568cca0d7d375ed4e5e"),
}
DEPENDENCIES = frozenset({
    "polymarket_scanner/__init__.py",
    "polymarket_scanner/config.py",
    "polymarket_scanner/production/__init__.py",
    "polymarket_scanner/production/chain.py",
    "polymarket_scanner/production/fees.py",
    "polymarket_scanner/safe_logging.py",
    "polymarket_scanner/v11/__init__.py",
    "polymarket_scanner/v11/account_effects.py",
    "polymarket_scanner/v11/allocation.py",
    "polymarket_scanner/v11/basket_coordinator.py",
    "polymarket_scanner/v11/basket_valuation.py",
    "polymarket_scanner/v11/candidate_liveness.py",
    "polymarket_scanner/v11/certification.py",
    "polymarket_scanner/v11/collection.py",
    "polymarket_scanner/v11/datasets.py",
    "polymarket_scanner/v11/ecmwf_grib.py",
    "polymarket_scanner/v11/ecmwf_sources.py",
    "polymarket_scanner/v11/event_queue.py",
    "polymarket_scanner/v11/event_risk.py",
    "polymarket_scanner/v11/evidence.py",
    "polymarket_scanner/v11/fill_evidence.py",
    "polymarket_scanner/v11/forecast_features.py",
    "polymarket_scanner/v11/forecast_sources.py",
    "polymarket_scanner/v11/gefs_sources.py",
    "polymarket_scanner/v11/grib_fields.py",
    "polymarket_scanner/v11/guardian_lease.py",
    "polymarket_scanner/v11/guardian_protocol.py",
    "polymarket_scanner/v11/learning_capture.py",
    "polymarket_scanner/v11/learning_sources.py",
    "polymarket_scanner/v11/liveness_protocol.py",
    "polymarket_scanner/v11/measurement.py",
    "polymarket_scanner/v11/metar_features.py",
    "polymarket_scanner/v11/model_artifacts.py",
    "polymarket_scanner/v11/model_panel.py",
    "polymarket_scanner/v11/model_registry.py",
    "polymarket_scanner/v11/nowcast_features.py",
    "polymarket_scanner/v11/paper_cancellation.py",
    "polymarket_scanner/v11/paper_coordinator.py",
    "polymarket_scanner/v11/paper_guardian.py",
    "polymarket_scanner/v11/paper_guardian_broker.py",
    "polymarket_scanner/v11/paper_reconciliation.py",
    "polymarket_scanner/v11/physical_inference.py",
    "polymarket_scanner/v11/position_attribution.py",
    "polymarket_scanner/v11/position_management.py",
    "polymarket_scanner/v11/probability.py",
    "polymarket_scanner/v11/pws_admission.py",
    "polymarket_scanner/v11/pws_lead.py",
    "polymarket_scanner/v11/pws_quality.py",
    "polymarket_scanner/v11/pws_scoring.py",
    "polymarket_scanner/v11/remaining_forecast.py",
    "polymarket_scanner/v11/rules.py",
    "polymarket_scanner/v11/runtime_health.py",
    "polymarket_scanner/v11/scenario_risk.py",
    "polymarket_scanner/v11/source_release.py",
    "polymarket_scanner/v11/strategy_admission.py",
    "polymarket_scanner/v11/strategy_pipeline.py",
    "polymarket_scanner/v11/target_learning.py",
    "polymarket_scanner/v11/valuation.py",
    "polymarket_scanner/v11/weather_sources.py",
    "polymarket_scanner/v11/weathernext_sources.py",
    "polymarket_scanner/weather_only_contract_strict.py",
    "polymarket_scanner/weather_only_contracts.py",
    "polymarket_scanner/weather_only_forecast.py",
    "polymarket_scanner/weather_only_rules.py",
    "tools/v11_gate3_evidence_preflight_checker.py",
    "tools/v11_gate3_evidence_preflight_real_intake.py",
    "tools/v11_multimodel_panel.py",
    "tools/v11_multimodel_stacking.py",
    "tools/v11_r09_gate3_a7_decoder.py",
    "tools/v11_r09_gate3_eligibility.py",
    "tools/v11_r09_gate3_launch_v4.py",
    "tools/v11_r09_gate3_offline_io.py",
    "tools/v11_trajectory_contract.py",
    "config/v11/r09_gate3_observed_message_sizes_20260930.json",
    "docs/V11_R09_GATE3_COLLECTION_PROTOCOL.md",
    "docs/V11_R09_GATE3_LAUNCH_CONTRACT_ADJUDICATION.md",
    "docs/V11_R09_GATE3_TRANSPORT_RUNTIME_DESIGN.md",
    "tests/test_v11_r09_gate3_collector.py",
    "tests/test_v11_r09_gate3_message_sizes.py",
    "tests/test_v11_r09_gate3_runtime.py",
    "tests/test_v11_r09_gate3_ledgers.py",
    "tests/test_v11_gate3_attempt_runtime_wiring.py",
    "tests/test_v11_gate3_evidence_intake_launch_wiring.py",
    "tests/test_v11_gate3_raw_decoder_binding.py",
    "tools/v11_r09_gate3_message_sizes.py",
    "tools/v11_r09_gate3_launch.py",
    "tools/v11_gate3_preflight_attempt_model.py",
    "tools/v11_gate3_evidence_intake_guard.py",
    "tools/v11_gate3_raw_decoder_binding.py",
    "tools/v11_r09_gate3_store_v1.py",
})
PATHS = frozenset(HISTORICAL) | DEPENDENCIES
OID = re.compile(r"[0-9a-f]{40}\Z")
SHA = re.compile(r"[0-9a-f]{64}\Z")


def _git(repo: Path, *args: str) -> bytes:
    env = {**os.environ, "GIT_NO_REPLACE_OBJECTS": "1", "GIT_GRAFT_FILE": os.devnull}
    proc = subprocess.run(["git", "--no-replace-objects", *args], cwd=repo,
                          env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          check=False, timeout=30)
    if proc.returncode:
        raise ValueError(f"unavailable Git object: {args!r}")
    return proc.stdout


@cache
def _commit(repo: Path, oid: str) -> tuple[str, tuple[str, ...]]:
    if not isinstance(oid, str) or not OID.fullmatch(oid):
        raise ValueError("commit must be an exact SHA-1 object ID")
    raw = _git(repo, "cat-file", "commit", oid)
    headers = raw.split(b"\n\n", 1)[0].splitlines()
    trees = [line[5:].decode("ascii") for line in headers if line.startswith(b"tree ")]
    parents = tuple(line[7:].decode("ascii") for line in headers if line.startswith(b"parent "))
    if len(trees) != 1 or not OID.fullmatch(trees[0]) or any(not OID.fullmatch(p) for p in parents):
        raise ValueError("malformed commit object")
    return trees[0], parents


def _blob(repo: Path, commit: str, path: str) -> tuple[str, bytes]:
    if path not in PATHS:
        raise ValueError("path outside fixed current-executable coverage")
    rows = _git(repo, "ls-tree", commit, "--", path).splitlines()
    if len(rows) != 1:
        raise ValueError(f"missing exact tree path: {path}")
    meta, named = rows[0].split(b"\t", 1)
    mode, kind, oid = meta.split()
    if named.decode() != path or mode not in (b"100644", b"100755") or kind != b"blob":
        raise ValueError(f"non-regular tree path: {path}")
    data = _git(repo, "cat-file", "blob", oid.decode())
    if hashlib.sha1(b"blob " + str(len(data)).encode() + b"\0" + data).hexdigest() != oid.decode():
        raise ValueError(f"Git blob content mismatch: {path}")
    return oid.decode(), data


def _live(repo: Path, path: str) -> bytes:
    # Hold directory and file descriptors while reading; never follow a symlink.
    parts = Path(path).parts
    fd = None
    try:
        fd = os.open(repo, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        for part in parts[:-1]:
            try:
                next_fd = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                                  dir_fd=fd)
            except OSError as exc:
                raise ValueError(f"non-directory binding path: {path}") from exc
            os.close(fd)
            fd = next_fd
        try:
            # A writerless FIFO must not block before the fstat type check.
            file_fd = os.open(parts[-1], os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW,
                              dir_fd=fd)
        except OSError as exc:
            raise ValueError(f"non-regular binding path: {path}") from exc
        with os.fdopen(file_fd, "rb") as stream:
            if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                raise ValueError(f"non-regular binding path: {path}")
            return stream.read()
    except OSError as exc:
        raise ValueError(f"unavailable binding path: {path}") from exc
    finally:
        if fd is not None:
            os.close(fd)


def _raw_ancestor(repo: Path, ancestor: str, descendant: str) -> bool:
    # Walk immutable parent headers, so replace refs and local grafts cannot
    # invent source ancestry. Bounded to prevent pathological object graphs.
    pending, seen = [descendant], set()
    while pending:
        oid = pending.pop()
        if oid == ancestor:
            return True
        if oid in seen:
            continue
        seen.add(oid)
        if len(seen) > 20000:
            raise ValueError("commit ancestry exceeds verifier bound")
        pending.extend(reversed(_commit(repo, oid)[1]))
    return False


def _unique(pairs):
    out = {}
    for key, value in pairs:
        if key in out:
            raise ValueError(f"duplicate manifest key: {key}")
        out[key] = value
    return out


def verify(repo: Path, manifest_path: Path | None = None) -> dict:
    repo = repo.resolve()
    path = manifest_path or repo / MANIFEST
    manifest = json.loads(path.read_bytes(), object_pairs_hook=_unique)
    if (type(manifest) is not dict or set(manifest) != {
        "schema", "source_commit", "source_tree", "launchable", "qualification_credit",
        "historical_baselines", "files", "drift_commits", "review_limit",
    } or manifest["schema"] != "V11_GATE3_CURRENT_EXECUTABLE_BINDING_V1"
        or manifest["source_commit"] != SOURCE_COMMIT or manifest["source_tree"] != SOURCE_TREE
        or manifest["launchable"] is not False or manifest["qualification_credit"] != 0
        or set(manifest["files"]) != PATHS or set(manifest["historical_baselines"]) != set(HISTORICAL)
        or set(manifest["drift_commits"]) != set(HISTORICAL)):
        raise ValueError("current-executable manifest coverage or authority changed")
    if _commit(repo, SOURCE_COMMIT)[0] != SOURCE_TREE:
        raise ValueError("pinned source commit/tree mismatch")
    head = _git(repo, "rev-parse", "--verify", "HEAD").decode().strip()
    if not OID.fullmatch(head) or not _raw_ancestor(repo, SOURCE_COMMIT, head):
        raise ValueError("current checkout does not descend from pinned source")
    for file_path in sorted(PATHS):
        row = manifest["files"][file_path]
        oid, data = _blob(repo, SOURCE_COMMIT, file_path)
        if (type(row) is not dict or set(row) != {"git_blob", "sha256", "byte_length"}
            or row["git_blob"] != oid or row["sha256"] != hashlib.sha256(data).hexdigest()
            or row["byte_length"] != len(data) or type(row["byte_length"]) is not int
            or not SHA.fullmatch(row["sha256"])):
            raise ValueError(f"source blob or digest mismatch: {file_path}")
        if _blob(repo, head, file_path) != (oid, data) or _live(repo, file_path) != data:
            raise ValueError(f"current executable/dependency byte drift: {file_path}")
    for file_path, commit in HISTORICAL.items():
        baseline = manifest["historical_baselines"][file_path]
        oid, data = _blob(repo, commit, file_path)
        if (type(baseline) is not dict or set(baseline) != {"commit", "tree", "git_blob", "sha256", "byte_length"}
            or baseline != {"commit": commit, "tree": _commit(repo, commit)[0],
                            "git_blob": oid, "sha256": hashlib.sha256(data).hexdigest(),
                            "byte_length": len(data)}):
            raise ValueError(f"historical review baseline changed: {file_path}")
        if oid == manifest["files"][file_path]["git_blob"]:
            raise ValueError(f"expected post-baseline drift absent: {file_path}")
        traces = manifest["drift_commits"][file_path]
        if (type(traces) is not list or
            tuple(t.get("commit") if type(t) is dict else None for t in traces)
                != DRIFT_COMMITS[file_path]):
            raise ValueError(f"partial drift trace: {file_path}")
        seen = set()
        for trace in traces:
            if type(trace) is not dict or set(trace) != {"commit", "classification"}:
                raise ValueError(f"malformed drift trace: {file_path}")
            change = trace["commit"]
            if change in seen or not _raw_ancestor(repo, commit, change) or not _raw_ancestor(repo, change, SOURCE_COMMIT):
                raise ValueError(f"stale or duplicate drift trace: {file_path}")
            seen.add(change)
            parents = _commit(repo, change)[1]
            if not parents or _blob(repo, change, file_path)[0] == _blob(repo, parents[0], file_path)[0]:
                raise ValueError(f"drift commit did not change pinned path: {file_path}")
            if type(trace["classification"]) is not str or not trace["classification"]:
                raise ValueError(f"unclassified drift commit: {file_path}")
    return {"source_commit": SOURCE_COMMIT, "source_tree": SOURCE_TREE,
            "verified_files": len(PATHS), "launchable": False, "qualification_credit": 0}


if __name__ == "__main__":
    print(json.dumps(verify(Path(__file__).resolve().parents[1]), sort_keys=True))
