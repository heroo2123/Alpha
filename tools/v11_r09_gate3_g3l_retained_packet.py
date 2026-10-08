"""Verify a date-independent G3-L local reconciliation packet, offline only.

Candidate references are review inputs, not qualified PRE_REVIEW entries. This
module never creates a launch manifest, selects a cohort, or accesses a provider.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from tools import v11_r09_gate3_g3l_identity_audit as audit
from tools.v11_r09_gate3_g3l_prep import ALL_IDS, FINAL_ONLY_IDS, PRE_REVIEW_IDS

PACKET = "docs/V11_R09_GATE3_G3L_RETAINED_PROTOCOL_PACKET.json"
SCHEMA = "R09_GATE3_G3L_RETAINED_PROTOCOL_PACKET_V1"
DISPOSITION = (
    "Six original local references are reviewable reconciliation candidates only; "
    "all 77 PRE_REVIEW qualified entries remain missing, including the other 71. "
    "Independent package-entry review and all selected-window, provider-rights, "
    "clock, storage, and detached-review evidence remain open."
)
ROWS = {
    "protocol.g3i_composition_review_terminal": (
        "docs/V11_R09_GATE3_COMPOSITION_REVIEW_0b7209d.md",
        "docs/V11_R09_GATE3_COMPOSITION_REVIEW_0b7209d_terminal.json"),
    "protocol.g3p_addendum_commit_tree_document": (
        "docs/V11_R09_GATE3_LAUNCH_CONTRACT_ADJUDICATION.md",
        "docs/V11_R09_GATE3_LAUNCH_CONTRACT_REVIEW_14c2413.md",
        "docs/V11_R09_GATE3_LAUNCH_CONTRACT_REVIEW_14c2413_terminal.json"),
    "protocol.g3p_addendum_review_terminal": (
        "docs/V11_R09_GATE3_LAUNCH_CONTRACT_REVIEW_14c2413.md",
        "docs/V11_R09_GATE3_LAUNCH_CONTRACT_REVIEW_14c2413_terminal.json"),
    "protocol.g3p_original_commit_tree_document": (
        "docs/V11_R09_GATE3_COLLECTION_PROTOCOL.md",
        "docs/V11_R09_GATE3_PROTOCOL_REVIEW_117830a.md",
        "docs/V11_R09_GATE3_PROTOCOL_REVIEW_117830a_terminal.json"),
    "protocol.g3p_original_review_terminal": (
        "docs/V11_R09_GATE3_PROTOCOL_REVIEW_117830a.md",
        "docs/V11_R09_GATE3_PROTOCOL_REVIEW_117830a_terminal.json"),
    "protocol.transport_design_review_terminal": (
        "docs/V11_R09_GATE3_TRANSPORT_RUNTIME_DESIGN.md",
        "docs/V11_R09_GATE3_TRANSPORT_RUNTIME_REVIEW_7e132a0.md",
        "docs/V11_R09_GATE3_TRANSPORT_RUNTIME_REVIEW_7e132a0_terminal.json"),
}
TERMINALS = {
    "docs/V11_R09_GATE3_COMPOSITION_REVIEW_0b7209d_terminal.json": (
        "docs/V11_R09_GATE3_COMPOSITION_REVIEW_0b7209d.md",
        "0b7209dba3e0c6d3ab55aa984881b83855ae0692",
        "87cbb35b6615d21b36a989197e490acebdfe0ad0"),
    "docs/V11_R09_GATE3_LAUNCH_CONTRACT_REVIEW_14c2413_terminal.json": (
        "docs/V11_R09_GATE3_LAUNCH_CONTRACT_REVIEW_14c2413.md",
        "14c2413c4cef63dab35a806955fcdd3ef0942f69",
        "d37d7ad55a76902cae0536e6f4d21b9db6aeaa81"),
    "docs/V11_R09_GATE3_PROTOCOL_REVIEW_117830a_terminal.json": (
        "docs/V11_R09_GATE3_PROTOCOL_REVIEW_117830a.md",
        "117830a9b418cdf2a1b1f5146e074343623b8e3f",
        "07c4d72fcafb4897896e27dfbcc415e7ab078ee9"),
    "docs/V11_R09_GATE3_TRANSPORT_RUNTIME_REVIEW_7e132a0_terminal.json": (
        "docs/V11_R09_GATE3_TRANSPORT_RUNTIME_REVIEW_7e132a0.md",
        "7e132a02ae0fcceed5e3fea65f7b86bed4a92c23",
        "89332f46dfc6b2111fe331fca54467cce96a3895"),
}
RECOVERED_TERMINAL = audit.ORIGINAL_TERMINAL
RECOVERED_PIN = (383, "414aef99c0576a0e96b86aa9244ad1ca4ffe4b02f161c69e0193f78498d3db22")


def _json(data: bytes) -> dict:
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"duplicate JSON key: {key}")
            result[key] = value
        return result
    value = json.loads(data, object_pairs_hook=unique)
    if type(value) is not dict:
        raise ValueError("JSON root must be an object")
    return value


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _terminal(path: str, data: dict, report_sha: str, repo: Path) -> None:
    report, commit, tree = TERMINALS[path]
    if audit._git_tree_oid(repo, commit) != tree:
        raise ValueError(f"different reviewed commit/tree: {path}")
    if path == RECOVERED_TERMINAL:
        valid = (data.get("status") == "PASS" and data.get("exit") == 0
                 and data.get("error") is None and data.get("marker") == audit.ORIGINAL_MARKER
                 and data.get("commit") == commit and data.get("tree") == tree
                 and data.get("report_sha256") == report_sha)
    elif "COMPOSITION_REVIEW" in path:
        hashes = data.get("artifacts_sha256")
        valid = (data.get("status") == "SCOPED_PASS_OFFLINE_COMPOSITION"
                 and data.get("candidate_clean_unchanged_unmerged") is True
                 and data.get("candidate_commit") == commit and data.get("candidate_tree") == tree
                 and isinstance(hashes, dict)
                 and hashes.get("/home/alphaadmin/AlphaV11_Dev/Alpha/" + report) == report_sha)
    else:
        valid = (data.get("status") == "COMPLETED"
                 and data.get("reviewed_commit") == commit and data.get("reviewed_tree") == tree
                 and data.get("report") == report and data.get("report_sha256") == report_sha)
        if "LAUNCH_CONTRACT" in path:
            valid = valid and data.get("verdict") == "PASS_OFFLINE_G3P_ADDENDUM_ONLY"
        else:
            valid = (valid and data.get("verdict") == "PASS_DESIGN_ONLY"
                     and data.get("reviewed_document") == "docs/V11_R09_GATE3_TRANSPORT_RUNTIME_DESIGN.md")
    if not valid:
        raise ValueError(f"different or incomplete review terminal: {path}")


def verify(repo: Path, packet_path: Path | None = None) -> dict:
    """Recheck packet, original Git bytes and completed review identities."""
    repo = repo.resolve()
    cache: dict[str, bytes] = {}
    read = lambda path: audit._evidence_bytes(repo, path, cache)
    packet = _json((packet_path or repo / PACKET).read_bytes())
    expected_keys = {"schema", "launchable", "g3l", "qualification_credit",
                     "provider_request_authority", "target_date", "selected_cohort",
                     "source_reconciliation_sha256", "source_review_terminal_sha256",
                     "qualified_entries", "candidates", "disposition"}
    if set(packet) != expected_keys or packet["schema"] != SCHEMA:
        raise ValueError("packet schema changed")
    if (packet["launchable"] is not False or packet["g3l"] != "NO-GO"
            or packet["qualification_credit"] != 0
            or packet["provider_request_authority"] is not False
            or packet["target_date"] is not None or packet["selected_cohort"] is not None
            or packet["disposition"] != DISPOSITION):
        raise ValueError("packet claims authority or a selected date/cohort")
    if (set(packet["qualified_entries"]) != set(ALL_IDS)
            or any(value is not None for value in packet["qualified_entries"].values())
            or len(PRE_REVIEW_IDS) != 77 or len(FINAL_ONLY_IDS) != 2):
        raise ValueError("qualified entries must all remain null")
    if set(packet["candidates"]) != set(ROWS):
        raise ValueError("candidate identity coverage changed")
    for path in audit.TRUST_ROOT_BYTES:
        blob = read(path)
        length, sha = audit.TRUST_ROOT_BYTES[path]
        if len(blob) != length or _sha(blob) != sha:
            raise ValueError(f"reviewed root bytes changed: {path}")
    source = _json(read(audit.RECONCILIATION))
    verdict = _json(read(audit.REVIEW))
    review_terminal = _json(read(audit.REVIEW_TERMINAL))
    if (packet["source_reconciliation_sha256"] != _sha(read(audit.RECONCILIATION))
            or packet["source_review_terminal_sha256"] != _sha(read(audit.REVIEW_TERMINAL))
            or read(audit.RECONCILIATION) != audit._git_bytes(repo, audit.RECONCILIATION_COMMIT, audit.RECONCILIATION)
            or review_terminal.get("exit_code") != 0
            or review_terminal.get("review_scope") != "RECONCILIATION_ONLY_NOT_G3L"
            or review_terminal.get("head") != audit.RECONCILIATION_COMMIT
            or review_terminal.get("tree") != audit.REVIEWED_TREE
            or review_terminal.get("report_sha256") != _sha(read(audit.REVIEW_REPORT))
            or review_terminal.get("verdict_sha256") != _sha(read(audit.REVIEW))
            or verdict.get("status") != "PASS_IN_SCOPE"
            or verdict.get("review_scope") != "RECONCILIATION_ONLY_NOT_G3L"
            or verdict.get("candidate_commit") != audit.RECONCILIATION_COMMIT
            or verdict.get("candidate_tree") != audit.REVIEWED_TREE
            or verdict.get("candidate_json_sha256") != _sha(read(audit.RECONCILIATION))):
        raise ValueError("reconciliation review binding changed")
    if set(source["identities"]) != set(ALL_IDS):
        raise ValueError("source identity coverage changed")
    if any(row["qualified_entry"] is not None for row in source["identities"].values()):
        raise ValueError("source claims a qualified entry")
    for identity, paths in ROWS.items():
        row = packet["candidates"][identity]
        category = audit.OFFLINE if identity == audit.ORIGINAL_ID else audit.RETAINED_SCOPED
        terminal = paths[-1]
        _, reviewed_commit, reviewed_tree = TERMINALS[terminal]
        if (set(row) != {"category", "review_status", "refs", "review_terminal",
                         "reviewed_commit", "reviewed_tree"}
                or row["category"] != category
                or row["review_status"] != "LOCAL_RECONCILIATION_PENDING_INDEPENDENT_ENTRY_REVIEW"
                or row["review_terminal"] != terminal
                or row["reviewed_commit"] != reviewed_commit
                or row["reviewed_tree"] != reviewed_tree
                or [ref.get("path") for ref in row["refs"]] != list(paths)):
            raise ValueError(f"candidate changed: {identity}")
        original = source["identities"][identity]
        if (original["qualified_entry"] is not None
                or (identity != audit.ORIGINAL_ID and original["material_status"] != "REUSABLE_SCOPED_ARTIFACTS")
                or not set(original["artifacts"]).issubset(paths)):
            raise ValueError(f"source row changed: {identity}")
        for ref in row["refs"]:
            path = ref["path"]
            if path == RECOVERED_TERMINAL:
                length, sha = RECOVERED_PIN
                commit = audit.ORIGINAL_TERMINAL_RECOVERY_COMMIT
            else:
                claim = source["artifacts"][path]
                length, sha, commit = claim["byte_length"], claim["sha256"], claim["git_commit"]
            blob = read(path)
            if (set(ref) != {"path", "byte_length", "sha256", "git_commit"}
                    or ref != {"path": path, "byte_length": length, "sha256": sha, "git_commit": commit}
                    or len(blob) != length or _sha(blob) != sha
                    or audit._git_bytes(repo, commit, path) != blob):
                raise ValueError(f"original bytes changed: {identity}: {path}")
    for path, (report, _, _) in TERMINALS.items():
        _terminal(path, _json(read(path)), _sha(read(report)), repo)
    return {"candidate_count": 6, "pre_review_missing": 77,
            "other_pre_review_missing": 71, "qualification_credit": 0,
            "launchable": False, "g3l": "NO-GO"}


if __name__ == "__main__":
    print(json.dumps(verify(Path(__file__).resolve().parents[1]), sort_keys=True))
