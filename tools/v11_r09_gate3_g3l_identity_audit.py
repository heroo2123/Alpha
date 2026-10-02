"""Read-only, fail-closed audit of retained G3-L reconciliation evidence.

This reports material availability, never qualified inventory entries. In
particular, a historical review of one commit is not a review of later code.
"""

from __future__ import annotations

import argparse
import hashlib
import json
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

# Field names observed across this repo's exact-review terminal/reconciliation
# schemas for "the code commit this record reviewed". Used to discover, for
# any identity row, which `code_byte_observations` its own cited JSON
# artifacts actually depend on -- rather than trusting a row's "artifacts"
# list to already include the underlying source file.
_COMMIT_FIELD_NAMES = ("head", "candidate_commit", "reviewed_commit", "commit", "candidate")

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
    result = json.loads(path.read_bytes(), object_pairs_hook=unique)
    if type(result) is not dict:
        raise ValueError(f"JSON root is not an object: {path}")
    return result


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _git_bytes(repo: Path, commit: str, path: str) -> bytes:
    try:
        proc = subprocess.run(
            ["git", "cat-file", "blob", f"{commit}:{path}"], cwd=repo,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
            timeout=30,
        )
    except subprocess.TimeoutExpired as exc:
        raise ValueError(f"git read timed out: {commit}:{path}") from exc
    if proc.returncode:
        raise ValueError(f"missing Git evidence: {commit}:{path}")
    return proc.stdout


def _repo_ref(repo: Path, path: str, commit: str | None = None) -> dict:
    relative = Path(path)
    if relative.is_absolute() or ".." in relative.parts or not (repo / relative).is_file():
        raise ValueError(f"unsafe or missing evidence path: {path}")
    data = (repo / relative).read_bytes()
    result = {"path": path, "sha256": _digest(data), "byte_length": len(data),
              "review_status": "HISTORICAL_SCOPE_ONLY"}
    if commit is not None:
        result["git_commit"] = commit
        result["matches_named_git_bytes"] = data == _git_bytes(repo, commit, path)
    return result


def _code_observation_refs(repo: Path, artifact_paths: list[str], artifacts: dict,
                           commit_to_paths: dict[str, set[str]]) -> list[dict]:
    """Discover which `code_byte_observations` a row's own JSON artifacts cite.

    A row's "artifacts" list is the reviewer's chosen evidence scope, but a
    reviewed terminal/reconciliation JSON may certify a specific code commit
    without that code file itself being listed. Every such JSON artifact is
    re-read here for a recorded commit; any match against a known code
    observation pulls that observation's current byte-freshness into the
    row's own ref set, so drift in the underlying code is never missed.
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
                        extra.append(artifacts[obs_path])
    return extra


def audit(repo: Path, *, target_date: str, now_utc: int,
          free_disk_bytes: int, available_memory_bytes: int) -> dict:
    repo = repo.resolve()
    source = _json(repo / RECONCILIATION)
    verdict = _json(repo / REVIEW)
    reviewed_report = _repo_ref(repo, REVIEW_REPORT)
    reviewed_terminal = _repo_ref(repo, REVIEW_TERMINAL)
    reviewed_verdict = _repo_ref(repo, REVIEW)
    terminal_record = _json(repo / REVIEW_TERMINAL)
    source_bytes = (repo / RECONCILIATION).read_bytes()
    if (verdict.get("status") != "PASS_IN_SCOPE" or
        verdict.get("candidate_json_sha256") != _digest(source_bytes) or
        verdict.get("review_scope") != "RECONCILIATION_ONLY_NOT_G3L" or
        verdict.get("report_sha256") != reviewed_report["sha256"] or
        terminal_record.get("exit_code") != 0 or
        terminal_record.get("review_scope") != "RECONCILIATION_ONLY_NOT_G3L" or
        terminal_record.get("report_sha256") != reviewed_report["sha256"] or
        terminal_record.get("verdict_sha256") != reviewed_verdict["sha256"] or
        terminal_record.get("head") != verdict.get("candidate_commit") or
        terminal_record.get("tree") != verdict.get("candidate_tree")):
        raise ValueError("reconciliation is not bound to its scoped independent review")
    for ref in (reviewed_report, reviewed_terminal, reviewed_verdict):
        ref["review_status"] = "PASS_IN_SCOPE_RECONCILIATION_ONLY_NOT_G3L"
    if set(source["identities"]) != set(ALL_IDS) or len(PRE_REVIEW_IDS) != 77:
        raise ValueError("G3-L identity schema drift; audit classification needs review")
    if any(source["identities"][i]["qualified_entry"] is not None for i in ALL_IDS):
        raise ValueError("source reconciliation contains an unreviewed qualified entry")

    artifacts = {}
    for path, claimed in sorted(source["artifacts"].items()):
        if claimed.get("path") != path:
            raise ValueError(f"artifact path mismatch: {path}")
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
    if not isinstance(observations, dict) or not observations:
        raise ValueError("source reconciliation is missing code_byte_observations")
    commit_to_paths: dict[str, set[str]] = {}
    for name, obs in observations.items():
        obs_path, obs_commit = obs.get("path"), obs.get("commit_oid")
        if not isinstance(obs_path, str) or not isinstance(obs_commit, str) or obs_path not in artifacts:
            raise ValueError(f"malformed code_byte_observation: {name}")
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
            refs += _code_observation_refs(repo, old["artifacts"], artifacts, commit_to_paths)
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
