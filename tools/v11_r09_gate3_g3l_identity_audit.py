"""Read-only, fail-closed audit of retained G3-L reconciliation evidence.

This reports material availability, never qualified inventory entries. In
particular, a historical review of one commit is not a review of later code.
"""

from __future__ import annotations

import argparse
from contextvars import ContextVar
import hashlib
import json
import os
import re
import stat
import subprocess
from pathlib import Path

from tools.v11_r09_gate3_g3l_prep import (
    ALL_IDS, FINAL_ONLY_IDS, PRE_REVIEW_IDS, check_inventory, freeze_checklist,
    make_report,
)

RECONCILIATION = "docs/V11_R09_GATE3_G3L_RECONCILIATION_20261002.json"
REVIEW = "docs/V11_R09_GATE3_G3L_RECONCILIATION_REVIEW_027fd7a.verdict.json"
REVIEW_REPORT = "docs/V11_R09_GATE3_G3L_RECONCILIATION_REVIEW_027fd7a.md"
REVIEW_TERMINAL = "docs/V11_R09_GATE3_G3L_RECONCILIATION_REVIEW_027fd7a.terminal.json"
ORIGINAL_TERMINAL = "docs/V11_R09_GATE3_PROTOCOL_REVIEW_117830a_terminal.json"
ORIGINAL_REPORT = "docs/V11_R09_GATE3_PROTOCOL_REVIEW_117830a.md"
ORIGINAL_TERMINAL_RECOVERY_COMMIT = "5a06629c34577c14c2fffd5ced1fda7bd62ab7f2"
ORIGINAL_ID = "protocol.g3p_original_review_terminal"
SLICE3_ID = "code.slice3_exact_commit_review"
ORIGINAL_MARKER = "R09_GATE3_PROTOCOL_REVIEW_PASS"
RECONCILIATION_COMMIT = "027fd7a1ed82e403780473757cad1227ecc17115"
REVIEW_RETENTION_COMMIT = "52e0356c8c62910a15ff893e0815e88149071efe"
REVIEWED_TREE = "bd4cde2eb36dfdc4ddc410f3e2349376eb612130"
MAPPING_COMMIT = "23c11e059048257a284d514d3454b811b3866515"
SLICE3_COMMIT = "6340cb455eebae374039c9bae23cd806681dacb0"

# Immutable bytes accepted by the scoped reconciliation review. These pins
# are independent of the local Git object database and its replace refs.
TRUST_ROOT_BYTES = {
    RECONCILIATION: (66862, "fa089c078a43c18a52f7f685d449e98129bc37b0d1c946afd1155a6297a3eda6"),
    REVIEW: (1525, "15d4c22494912746a11f24f7fa6717520d77a9e3c9685e8ba0660e557d7a4b8f"),
    REVIEW_REPORT: (5572, "b6a811fd66f96d738a98499139ea3d84d70c96aa042044b165123aab01b1fed4"),
    REVIEW_TERMINAL: (759, "6ba3a34a7679130409a966560ad7313227bb749d6b0ffa1a13b10d109850b11f"),
}

# Field names observed across this repo's exact-review terminal/reconciliation
# schemas for "the code commit this record reviewed". Used to discover, for
# any identity row, which `code_byte_observations` its own cited JSON
# artifacts actually depend on -- rather than trusting a row's "artifacts"
# list to already include the underlying source file.
_COMMIT_FIELD_NAMES = ("head", "candidate_commit", "reviewed_commit", "commit", "candidate")

# The reconciliation's fixed, known set of code_byte_observations. A row can
# only be retained on the strength of a dependency check that actually ran;
# an observation silently dropped from this set (rather than refused) would
# let that dependency's drift go unchecked. The set is a hardening-tool
# constant, not reconciliation data, so removing or renaming an entry in the
# input cannot shrink what is required.
CODE_BYTE_OBSERVATION_NAMES = frozenset({
    "a7_decoder", "a8_preparation", "collector", "injected_runtime",
    "launch_validator", "ledgers", "offline_decoder_and_clock_types",
})
CODE_BYTE_OBSERVATION_PATHS = {
    "a7_decoder": "tools/v11_r09_gate3_a7_decoder.py",
    "a8_preparation": "tools/v11_r09_gate3_a8_composition.py",
    "collector": "tools/v11_r09_gate3_collector.py",
    "injected_runtime": "tools/v11_r09_gate3_runtime.py",
    "launch_validator": "tools/v11_r09_gate3_launch_v4.py",
    "ledgers": "tools/v11_r09_gate3_ledgers.py",
    "offline_decoder_and_clock_types": "tools/v11_r09_gate3_offline_io.py",
}
REUSABLE_ROW_CODE_DEPENDENCIES = {
    "code.mapping_exact_commit_review": frozenset({
        (CODE_BYTE_OBSERVATION_PATHS["launch_validator"], MAPPING_COMMIT),
    }),
    SLICE3_ID: frozenset({
        (CODE_BYTE_OBSERVATION_PATHS["injected_runtime"], SLICE3_COMMIT),
        (CODE_BYTE_OBSERVATION_PATHS["ledgers"], SLICE3_COMMIT),
    }),
    **{name: frozenset() for name in (
        "protocol.g3i_composition_review_terminal",
        "protocol.g3p_addendum_commit_tree_document",
        "protocol.g3p_addendum_review_terminal",
        "protocol.g3p_original_commit_tree_document",
        "protocol.transport_design_review_terminal",
    )},
}

# Keep reviewed row-to-artifact coverage fixed. Otherwise an omitted artifact
# can make an empty dependency list pass the later all(...) freshness check.
REUSABLE_ROW_ARTIFACTS = {
    "code.mapping_exact_commit_review": frozenset({
        "docs/V11_R09_GATE3_V4_PROVIDER_MAPPING_REPAIR_REVIEW_23c11e0.md",
        "docs/V11_R09_GATE3_V4_PROVIDER_MAPPING_REPAIR_REVIEW_23c11e0_terminal.json",
    }),
    SLICE3_ID: frozenset({
        "docs/V11_R09_GATE3_V4_SLICE3_REVIEW_6340cb4.md",
        "docs/V11_R09_GATE3_V4_SLICE3_REVIEW_6340cb4_terminal.json",
        "docs/V11_R09_GATE3_V4_SLICE3_RECONCILIATION_6340cb4.json",
    }),
    "protocol.g3i_composition_review_terminal": frozenset({
        "docs/V11_R09_GATE3_COMPOSITION_REVIEW_0b7209d.md",
        "docs/V11_R09_GATE3_COMPOSITION_REVIEW_0b7209d_terminal.json",
    }),
    "protocol.g3p_addendum_commit_tree_document": frozenset({
        "docs/V11_R09_GATE3_LAUNCH_CONTRACT_ADJUDICATION.md",
        "docs/V11_R09_GATE3_LAUNCH_CONTRACT_REVIEW_14c2413.md",
        "docs/V11_R09_GATE3_LAUNCH_CONTRACT_REVIEW_14c2413_terminal.json",
    }),
    "protocol.g3p_addendum_review_terminal": frozenset({
        "docs/V11_R09_GATE3_LAUNCH_CONTRACT_REVIEW_14c2413.md",
        "docs/V11_R09_GATE3_LAUNCH_CONTRACT_REVIEW_14c2413_terminal.json",
    }),
    "protocol.g3p_original_commit_tree_document": frozenset({
        "docs/V11_R09_GATE3_COLLECTION_PROTOCOL.md",
        "docs/V11_R09_GATE3_PROTOCOL_REVIEW_117830a.md",
    }),
    "protocol.transport_design_review_terminal": frozenset({
        "docs/V11_R09_GATE3_TRANSPORT_RUNTIME_DESIGN.md",
        "docs/V11_R09_GATE3_TRANSPORT_RUNTIME_REVIEW_7e132a0.md",
        "docs/V11_R09_GATE3_TRANSPORT_RUNTIME_REVIEW_7e132a0_terminal.json",
    }),
}

_COMMIT_OID_RE = re.compile(r"^[0-9a-f]{40}$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_ACTIVE_EVIDENCE: ContextVar[tuple[Path, dict[str, bytes]] | None] = ContextVar(
    "g3l_active_evidence", default=None)

RETAINED_SCOPED = "RETAINED_REVIEWED_LOCAL_SCOPE"
OFFLINE = "DETERMINISTIC_OFFLINE_RECONCILIATION"
FUTURE = "FUTURE_EVIDENCE_REVIEW_OR_EXTERNAL_RIGHT"
INVALID = "INVALID_STALE_OR_DUPLICATE_REQUIREMENT"


def _json(path: Path) -> dict:
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"duplicate JSON key: {key}")
            result[key] = value
        return result
    active = _ACTIVE_EVIDENCE.get()
    if active is None:
        raise ValueError("JSON evidence requires an active audit")
    repo, cache = active
    data = _evidence_bytes(repo, str(path.relative_to(repo)), cache)
    result = json.loads(data, object_pairs_hook=unique)
    if type(result) is not dict:
        raise ValueError(f"JSON root is not an object: {path}")
    return result


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _evidence_bytes(repo: Path, path: str, cache: dict[str, bytes]) -> bytes:
    """Read one regular evidence file once, without following symlinks or FIFOs."""
    if path in cache:
        return cache[path]
    relative = Path(path)
    if relative.is_absolute() or ".." in relative.parts or not relative.parts:
        raise ValueError(f"unsafe evidence path: {path}")
    try:
        directory = os.open(repo, os.O_RDONLY | os.O_DIRECTORY)
        try:
            for component in relative.parts[:-1]:
                child = os.open(component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                                dir_fd=directory)
                os.close(directory)
                directory = child
            fd = os.open(relative.parts[-1], os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW,
                         dir_fd=directory)
            with os.fdopen(fd, "rb") as handle:
                if not stat.S_ISREG(os.fstat(handle.fileno()).st_mode):
                    raise ValueError(f"non-regular evidence file: {path}")
                data = handle.read()
        finally:
            os.close(directory)
    except OSError as exc:
        raise ValueError(f"unreadable evidence file: {path}") from exc
    cache[path] = data
    return data


def _git_bytes(repo: Path, commit: str, path: str) -> bytes:
    try:
        proc = subprocess.run(
            ["git", "--no-replace-objects", "cat-file", "blob", f"{commit}:{path}"], cwd=repo,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
            timeout=30,
        )
    except subprocess.TimeoutExpired as exc:
        raise ValueError(f"git read timed out: {commit}:{path}") from exc
    if proc.returncode:
        raise ValueError(f"missing Git evidence: {commit}:{path}")
    return proc.stdout


def _git_tree_oid(repo: Path, commit: str) -> str:
    try:
        kind = subprocess.run(
            ["git", "--no-replace-objects", "cat-file", "-t", commit], cwd=repo,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
            timeout=30,
        )
        if kind.returncode or kind.stdout.strip() != b"commit":
            raise ValueError(f"unresolvable commit: {commit}")
        proc = subprocess.run(
            ["git", "--no-replace-objects", "rev-parse", "--verify", f"{commit}^{{tree}}"], cwd=repo,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
            timeout=30,
        )
    except subprocess.TimeoutExpired as exc:
        raise ValueError(f"git read timed out: {commit}^{{tree}}") from exc
    tree = proc.stdout.decode().strip()
    if proc.returncode or not _COMMIT_OID_RE.match(tree):
        raise ValueError(f"unresolvable commit: {commit}")
    return tree


def _validate_artifact_commit(repo: Path, commit: object, path: str) -> None:
    if not isinstance(commit, str) or not _COMMIT_OID_RE.fullmatch(commit):
        raise ValueError(f"malformed artifact git_commit: {path}")
    _git_tree_oid(repo, commit)
    try:
        ancestor = subprocess.run(
            ["git", "--no-replace-objects", "merge-base", "--is-ancestor", commit,
             RECONCILIATION_COMMIT],
            cwd=repo, env={**os.environ, "GIT_GRAFT_FILE": os.devnull},
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False, timeout=30,
        )
    except subprocess.TimeoutExpired as exc:
        raise ValueError(f"git ancestry check timed out: {commit}") from exc
    if ancestor.returncode:
        raise ValueError(f"artifact commit is not a reviewed ancestor: {path}")


def _repo_ref(repo: Path, path: str, commit: str | None = None,
              cache: dict[str, bytes] | None = None) -> dict:
    active = _ACTIVE_EVIDENCE.get()
    data = _evidence_bytes(repo, path, cache if cache is not None else
                           active[1] if active is not None and active[0] == repo else {})
    result = {"path": path, "sha256": _digest(data), "byte_length": len(data),
              "review_status": "HISTORICAL_SCOPE_ONLY"}
    if commit is not None:
        result["git_commit"] = commit
        result["matches_named_git_bytes"] = data == _git_bytes(repo, commit, path)
    return result


def _code_observation_refs(repo: Path, artifact_paths: list[str], observation_refs: dict,
                           commit_to_paths: dict[str, set[str]]) -> list[dict]:
    """Discover which `code_byte_observations` a row's own JSON artifacts cite.

    A row's "artifacts" list is the reviewer's chosen evidence scope, but a
    reviewed terminal/reconciliation JSON may certify a specific code commit
    without that code file itself being listed. Every such JSON artifact is
    parsed from cached evidence bytes for a recorded commit. Any match against
    a known code observation pulls that observation's own verified byte baseline (not the
    artifact dict's, which may be bound to a different commit) into the row's
    own ref set, so drift in the underlying code is never missed.
    """
    extra = []
    seen = set(artifact_paths)
    for path in artifact_paths:
        if not path.endswith(".json"):
            continue
        record = _json(repo / path)
        for field in _COMMIT_FIELD_NAMES:
            value = record.get(field)
            if isinstance(value, str) and value in commit_to_paths:
                for obs_path in sorted(commit_to_paths[value]):
                    if obs_path not in seen:
                        seen.add(obs_path)
                        extra.append(observation_refs[(value, obs_path)])
    return extra


def _observation_fields(name: str, obs: object, artifacts: dict,
                        observed_paths: set[str]) -> tuple[str, str, str, str]:
    if not isinstance(obs, dict):
        raise ValueError(f"malformed code_byte_observation: {name}")
    obs_path, obs_commit, obs_sha256, obs_tree = (
        obs.get("path"), obs.get("commit_oid"), obs.get("sha256"), obs.get("tree_oid"))
    if (not isinstance(obs_path, str) or obs_path != CODE_BYTE_OBSERVATION_PATHS[name] or
            obs_path in observed_paths or obs_path not in artifacts or
            not isinstance(obs_commit, str) or not _COMMIT_OID_RE.fullmatch(obs_commit) or
            not isinstance(obs_sha256, str) or not _SHA256_RE.fullmatch(obs_sha256) or
            not isinstance(obs_tree, str) or not _COMMIT_OID_RE.fullmatch(obs_tree)):
        raise ValueError(f"malformed code_byte_observation: {name}")
    observed_paths.add(obs_path)
    return obs_path, obs_commit, obs_sha256, obs_tree


def _require_code_dependencies(identity: str, code_refs: list[dict]) -> None:
    discovered = {(ref["path"], ref["git_commit"]) for ref in code_refs}
    if discovered != REUSABLE_ROW_CODE_DEPENDENCIES[identity] or len(discovered) != len(code_refs):
        raise ValueError(f"code dependency coverage changed: {identity}")


def _pinned_trust_root(repo: Path, path: str, cache: dict[str, bytes]) -> bytes:
    data = _evidence_bytes(repo, path, cache)
    length, sha256 = TRUST_ROOT_BYTES[path]
    if len(data) != length or _digest(data) != sha256:
        raise ValueError(f"reviewed trust-root bytes changed: {path}")
    return data


def audit(repo: Path, *, target_date: str, now_utc: int,
          free_disk_bytes: int, available_memory_bytes: int) -> dict:
    repo = repo.resolve()
    token = _ACTIVE_EVIDENCE.set((repo, {}))
    try:
        return _audit(repo, target_date=target_date, now_utc=now_utc,
                      free_disk_bytes=free_disk_bytes,
                      available_memory_bytes=available_memory_bytes)
    finally:
        _ACTIVE_EVIDENCE.reset(token)


def _audit(repo: Path, *, target_date: str, now_utc: int,
           free_disk_bytes: int, available_memory_bytes: int) -> dict:
    repo = repo.resolve()
    cache = _ACTIVE_EVIDENCE.get()[1]
    source_bytes = _pinned_trust_root(repo, RECONCILIATION, cache)
    if source_bytes != _git_bytes(repo, RECONCILIATION_COMMIT, RECONCILIATION):
        raise ValueError("reconciliation differs from reviewed Git bytes")
    source = _json(repo / RECONCILIATION)
    for path in (REVIEW, REVIEW_REPORT, REVIEW_TERMINAL):
        if _pinned_trust_root(repo, path, cache) != _git_bytes(repo, REVIEW_RETENTION_COMMIT, path):
            raise ValueError(f"review bundle differs from retained Git bytes: {path}")
    verdict = _json(repo / REVIEW)
    reviewed_report = _repo_ref(repo, REVIEW_REPORT)
    reviewed_terminal = _repo_ref(repo, REVIEW_TERMINAL)
    reviewed_verdict = _repo_ref(repo, REVIEW)
    terminal_record = _json(repo / REVIEW_TERMINAL)
    if (verdict.get("status") != "PASS_IN_SCOPE" or
        verdict.get("candidate_json_sha256") != _digest(source_bytes) or
        verdict.get("review_scope") != "RECONCILIATION_ONLY_NOT_G3L" or
        verdict.get("report_sha256") != reviewed_report["sha256"] or
        terminal_record.get("exit_code") != 0 or
        terminal_record.get("review_scope") != "RECONCILIATION_ONLY_NOT_G3L" or
        terminal_record.get("report_sha256") != reviewed_report["sha256"] or
        terminal_record.get("verdict_sha256") != reviewed_verdict["sha256"] or
        terminal_record.get("head") != RECONCILIATION_COMMIT or
        verdict.get("candidate_commit") != RECONCILIATION_COMMIT or
        terminal_record.get("tree") != REVIEWED_TREE or
        verdict.get("candidate_tree") != REVIEWED_TREE):
        raise ValueError("reconciliation is not bound to its scoped independent review")
    for ref in (reviewed_report, reviewed_terminal, reviewed_verdict):
        ref["review_status"] = "PASS_IN_SCOPE_RECONCILIATION_ONLY_NOT_G3L"
    if set(source["identities"]) != set(ALL_IDS) or len(PRE_REVIEW_IDS) != 77:
        raise ValueError("G3-L identity schema drift; audit classification needs review")
    if any(source["identities"][i]["qualified_entry"] is not None for i in ALL_IDS):
        raise ValueError("source reconciliation contains an unreviewed qualified entry")
    reusable = {identity for identity in PRE_REVIEW_IDS
                if source["identities"][identity]["material_status"] == "REUSABLE_SCOPED_ARTIFACTS"}
    if reusable != set(REUSABLE_ROW_ARTIFACTS):
        raise ValueError("source reconciliation reusable identity coverage changed")
    for identity, expected in REUSABLE_ROW_ARTIFACTS.items():
        paths = source["identities"][identity]["artifacts"]
        if not isinstance(paths, list) or len(paths) != len(expected) or set(paths) != expected:
            raise ValueError(f"source reconciliation artifact coverage changed: {identity}")

    artifacts = {}
    for path, claimed in sorted(source["artifacts"].items()):
        if claimed.get("path") != path:
            raise ValueError(f"artifact path mismatch: {path}")
        _validate_artifact_commit(repo, claimed.get("git_commit"), path)
        git_data = _git_bytes(repo, claimed["git_commit"], path)
        if len(git_data) != claimed["byte_length"] or _digest(git_data) != claimed["sha256"]:
            raise ValueError(f"historical Git artifact mismatch: {path}")
        live = _repo_ref(repo, path)
        artifacts[path] = {**claimed,
                           "historical_git_bytes_verified": True,
                           "review_status": "PRIOR_RECONCILIATION_PASS_IN_SCOPE_BYTES_ONLY",
                           "current_sha256": live["sha256"],
                           "current_byte_length": live["byte_length"],
                           "current_matches_reviewed_bytes": (
                               live["sha256"] == claimed["sha256"] and
                               live["byte_length"] == claimed["byte_length"])}

    observations = source["code_byte_observations"]
    if not isinstance(observations, dict) or set(observations) != CODE_BYTE_OBSERVATION_NAMES:
        raise ValueError("source reconciliation code_byte_observations is missing or incomplete")
    commit_to_paths: dict[str, set[str]] = {}
    observation_refs: dict[tuple[str, str], dict] = {}
    observed_paths: set[str] = set()
    for name, obs in sorted(observations.items()):
        obs_path, obs_commit, obs_sha256, obs_tree = _observation_fields(
            name, obs, artifacts, observed_paths)
        # Resolve and verify the baseline at the observation's own commit --
        # never at the (possibly different) commit the artifact dict records.
        if _git_tree_oid(repo, obs_commit) != obs_tree:
            raise ValueError(f"code_byte_observation commit/tree mismatch: {name}")
        blob = _git_bytes(repo, obs_commit, obs_path)
        if _digest(blob) != obs_sha256:
            raise ValueError(f"code_byte_observation baseline mismatch: {name}")
        live = artifacts[obs_path]
        observation_refs[(obs_commit, obs_path)] = {
            "path": obs_path, "git_commit": obs_commit, "sha256": obs_sha256,
            "tree_oid": obs_tree, "byte_length": len(blob),
            "historical_git_bytes_verified": True,
            "review_status": "CODE_BYTE_OBSERVATION_BYTES_ONLY",
            "current_sha256": live["current_sha256"],
            "current_byte_length": live["current_byte_length"],
            "current_matches_reviewed_bytes": (
                live["current_sha256"] == obs_sha256 and live["current_byte_length"] == len(blob)),
        }
        commit_to_paths.setdefault(obs_commit, set()).add(obs_path)

    terminal = _repo_ref(repo, ORIGINAL_TERMINAL, ORIGINAL_TERMINAL_RECOVERY_COMMIT)
    report = _repo_ref(repo, ORIGINAL_REPORT)
    terminal_data = _json(repo / ORIGINAL_TERMINAL)
    if (not terminal["matches_named_git_bytes"] or
        terminal_data.get("status") != "PASS" or terminal_data.get("exit") != 0 or
        terminal_data.get("marker") != ORIGINAL_MARKER or
        terminal_data.get("error") is not None or
        terminal_data.get("commit") != "117830a9b418cdf2a1b1f5146e074343623b8e3f" or
        terminal_data.get("tree") != "07c4d72fcafb4897896e27dfbcc415e7ab078ee9" or
        terminal_data.get("report_sha256") != report["sha256"] or
        report["sha256"] != artifacts[ORIGINAL_REPORT]["sha256"]):
        raise ValueError("recovered original G3-P terminal does not bind its review")
    terminal["review_status"] = "PASS_G3P_PROTOCOL_ONLY"
    report["review_status"] = "PASS_G3P_PROTOCOL_ONLY"

    screen = make_report(target_date=target_date,
                         run_utc=freeze_checklist(target_date)["window_start_utc"] - 14 * 3600,
                         free_disk=free_disk_bytes,
                         available_memory=available_memory_bytes,
                         observed_utc=now_utc)
    inventory = screen["inventory_template"]
    findings = check_inventory(inventory, target_date=target_date,
                               now_utc=now_utc, stage="PRE_REVIEW")
    missing = [f["id"] for f in findings if f["state"] == "MISSING"]
    if set(missing) != set(PRE_REVIEW_IDS):
        raise ValueError("local PRE_REVIEW screen no longer matches required identities")

    rows = {}
    for identity in PRE_REVIEW_IDS:
        old = source["identities"][identity]
        refs = [artifacts[path] for path in old["artifacts"]]
        if identity == ORIGINAL_ID:
            category = OFFLINE
            refs.extend((report, terminal))
            disposition = "Recovered retained PASS terminal binds the exact G3-P report; package sealing and independent entry review remain pending."
        else:
            code_refs = _code_observation_refs(repo, old["artifacts"], observation_refs,
                                               commit_to_paths)
            if old["material_status"] == "REUSABLE_SCOPED_ARTIFACTS":
                _require_code_dependencies(identity, code_refs)
            refs += code_refs
            if old["material_status"] == "REUSABLE_SCOPED_ARTIFACTS" and all(
                    ref["current_matches_reviewed_bytes"] for ref in refs):
                category = RETAINED_SCOPED
                disposition = "Exact scoped historical material is retained; sealing and independent package-entry review remain pending."
            elif old["material_status"] == "REUSABLE_SCOPED_ARTIFACTS":
                category = FUTURE
                drifted = sorted(ref["path"] for ref in refs if not ref["current_matches_reviewed_bytes"])
                disposition = ("Scoped review material is retained for its exact historical commit, but current "
                               "bytes for " + ", ".join(drifted) + " differ from the reviewed baseline; current "
                               "executable closure needs independent review.")
            else:
                category = FUTURE
                disposition = old["remaining_obligation"]
        rows[identity] = {
            "category": category, "qualified_entry": None,
            "required_freshness": old["required_freshness"],
            "prior_material_status": old["material_status"],
            "review_status": "SCOPED_HISTORICAL_ONLY" if category != FUTURE else "NOT_QUALIFIED",
            "source_refs": refs, "remaining_obligation": disposition,
        }
    counts = {category: sum(row["category"] == category for row in rows.values())
              for category in (RETAINED_SCOPED, OFFLINE, FUTURE, INVALID)}
    return {
        "schema": "R09_GATE3_G3L_LOCAL_IDENTITY_AUDIT_V1", "launchable": False,
        "g3l": "NO-GO", "qualification_credit": 0,
        "target_date_proposal": target_date, "observed_utc": now_utc,
        "source_reconciliation": {"path": RECONCILIATION,
                                  "sha256": _digest(source_bytes),
                                  "byte_length": len(source_bytes),
                                  "review_report": reviewed_report,
                                  "review_verdict": reviewed_verdict,
                                  "review_terminal": reviewed_terminal},
        "screen": {"missing_before": len(missing), "missing_after": len(missing),
                   "screen_runs": 1,
                   "before_after_note": ("missing_before and missing_after are the same single PRE_REVIEW "
                                         "measurement; the audit fills no identity, so no second run occurs."),
                   "other_findings": [f for f in findings if f["state"] != "MISSING"],
                   "attempt_slots": len(screen["capacity_plan"]["attempt_slots"]),
                   "denominator": screen["capacity_plan"]["denominator"],
                   "resource_snapshot": screen["capacity_plan"]["resource_snapshot"]},
        "category_counts": counts, "artifacts": artifacts,
        "final_only_unassembled": list(FINAL_ONLY_IDS), "identities": rows,
        "restriction_rule": "503/503/429 lineage remains a hold; later 200s and elapsed time are not resumption evidence.",
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--target-date", required=True)
    parser.add_argument("--now-utc", type=int, required=True)
    parser.add_argument("--free-disk-bytes", type=int, required=True)
    parser.add_argument("--available-memory-bytes", type=int, required=True)
    args = parser.parse_args(argv)
    result = audit(args.repo, target_date=args.target_date, now_utc=args.now_utc,
                   free_disk_bytes=args.free_disk_bytes,
                   available_memory_bytes=args.available_memory_bytes)
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
