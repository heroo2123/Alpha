"""Offline exact-byte check for one untrusted historical G3-P candidate.

This reads only three tracked evidence files, the candidate manifest, and
local Git objects. It does not import or execute Gate-3 application code.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess


ROOT = Path(__file__).resolve().parents[1]
MANIFEST = "docs/g3l_original_terminal_candidate/identity.json"
MANIFEST_LENGTH = 1605
MANIFEST_SHA256 = "2e9ac2aeee8e8aebf4101d70fd9ce58f81e9db124ae87ec5c8a437b619a5e414"
PROTOCOL_COMMIT = "117830a9b418cdf2a1b1f5146e074343623b8e3f"
PROTOCOL_TREE = "07c4d72fcafb4897896e27dfbcc415e7ab078ee9"
DOCUMENT = "docs/V11_R09_GATE3_COLLECTION_PROTOCOL.md"
REPORT = "docs/V11_R09_GATE3_PROTOCOL_REVIEW_117830a.md"
TERMINAL = "docs/V11_R09_GATE3_PROTOCOL_REVIEW_117830a_terminal.json"
RECOVERY_COMMIT = "5a06629c34577c14c2fffd5ced1fda7bd62ab7f2"
EXPECTED_REFS = {
    "document": (DOCUMENT, PROTOCOL_COMMIT, PROTOCOL_TREE),
    "report": (REPORT, "24ac951704ba06d295b28a2b80ca6acdf197329d",
               "a8c4ed31fe8c75af923b5b2b54913cc67081ed6e"),
    "terminal": (TERMINAL, RECOVERY_COMMIT,
                 "a1411f46f1dfd014b5e84895960a820c155b9563"),
}


def fail(message: str) -> None:
    raise ValueError(message)


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def regular_bytes(path: Path) -> bytes:
    if not stat.S_ISREG(path.lstat().st_mode):
        fail(f"non-regular file: {path}")
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd, "rb") as handle:
        if not stat.S_ISREG(os.fstat(handle.fileno()).st_mode):
            fail(f"non-regular file: {path}")
        return handle.read()


def unique_object(data: bytes) -> dict:
    def unique(pairs: list[tuple[str, object]]) -> dict:
        result = {}
        for key, value in pairs:
            if key in result:
                fail(f"duplicate JSON key: {key}")
            result[key] = value
        return result

    result = json.loads(data, object_pairs_hook=unique)
    if type(result) is not dict:
        fail("JSON root must be an object")
    return result


def git(root: Path, *args: str) -> bytes:
    result = subprocess.run(
        ["git", "--no-replace-objects", *args], cwd=root,
        stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        check=False, timeout=15,
    )
    if result.returncode:
        fail(f"local Git lookup failed: {' '.join(args)}")
    return result.stdout


def check_terminal_binding(terminal: dict, report_sha256: str) -> None:
    if (terminal.get("status") != "PASS" or terminal.get("exit") != 0 or
            terminal.get("error") is not None or
            terminal.get("marker") != "R09_GATE3_PROTOCOL_REVIEW_PASS" or
            terminal.get("commit") != PROTOCOL_COMMIT or
            terminal.get("tree") != PROTOCOL_TREE or
            terminal.get("report_sha256") != report_sha256):
        fail("terminal does not bind original protocol and exact report")


def check_report_binding(report: bytes) -> None:
    for line in (
        f"Reviewed commit: `{PROTOCOL_COMMIT}`".encode(),
        f"Reviewed tree: `{PROTOCOL_TREE}`".encode(),
        f"Reviewed document: `{DOCUMENT}`".encode(),
        b"**Verdict: PASS, scoped strictly to G3-P (protocol review).**",
    ):
        if line not in report:
            fail("report does not name the original scoped protocol review")


def validate(root: Path = ROOT, manifest_path: Path | None = None) -> dict[str, str]:
    root = root.resolve()
    manifest_path = manifest_path or root / MANIFEST
    raw = regular_bytes(manifest_path)
    if len(raw) != MANIFEST_LENGTH or digest(raw) != MANIFEST_SHA256:
        fail("candidate manifest byte pin mismatch")
    candidate = unique_object(raw)
    if (set(candidate) != {"schema", "identity", "scope", "reviewed_protocol_commit",
                           "reviewed_protocol_tree", "reviewed_document",
                           "recovery_commit", "artifacts"} or
            candidate["schema"] != "g3l_historical_exact_byte_candidate_v1" or
            candidate["identity"] != "protocol.g3p_original_review_terminal" or
            candidate["scope"] != "UNTRUSTED_HISTORICAL_G3P_PROTOCOL_ONLY" or
            candidate["reviewed_protocol_commit"] != PROTOCOL_COMMIT or
            candidate["reviewed_protocol_tree"] != PROTOCOL_TREE or
            candidate["reviewed_document"] != DOCUMENT or
            candidate["recovery_commit"] != RECOVERY_COMMIT or
            type(candidate["artifacts"]) is not dict or
            set(candidate["artifacts"]) != set(EXPECTED_REFS)):
        fail("candidate identity or scope mismatch")

    evidence = {}
    sha = {}
    for role, (path, commit, tree) in EXPECTED_REFS.items():
        ref = candidate["artifacts"][role]
        if (type(ref) is not dict or
                set(ref) != {"path", "commit", "tree", "git_blob", "byte_length", "sha256"} or
                ref["path"] != path or ref["commit"] != commit or ref["tree"] != tree or
                type(ref["byte_length"]) is not int or ref["byte_length"] < 1 or
                type(ref["sha256"]) is not str or len(ref["sha256"]) != 64):
            fail(f"malformed reference: {role}")
        if git(root, "cat-file", "-t", commit).strip() != b"commit":
            fail(f"not a commit: {role}")
        if git(root, "rev-parse", "--verify", f"{commit}^{{tree}}").strip().decode() != tree:
            fail(f"commit/tree mismatch: {role}")
        tree_line = git(root, "ls-tree", commit, "--", path).rstrip(b"\n")
        expected_line = f"100644 blob {ref['git_blob']}\t{path}".encode()
        if tree_line != expected_line:
            fail(f"path/blob mismatch: {role}")
        blob = git(root, "cat-file", "blob", ref["git_blob"])
        live = regular_bytes(root / path)
        if (blob != live or len(blob) != ref["byte_length"] or
                digest(blob) != ref["sha256"]):
            fail(f"historical/current exact-byte mismatch: {role}")
        evidence[role] = live
        sha[role] = digest(live)

    terminal = unique_object(evidence["terminal"])
    check_terminal_binding(terminal, sha["report"])
    check_report_binding(evidence["report"])
    return sha


if __name__ == "__main__":
    for role, sha256 in validate().items():
        print(f"{role}: {sha256}")
    print("PASS: historical G3-P terminal candidate byte identity only")
