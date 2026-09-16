#!/usr/bin/env python3
from __future__ import annotations

"""Host-owned approval gate for weather PAPER release and rollback authority.

This file is installed root-owned outside the candidate checkout.  The deployment
scripts may ask it whether a commit/check-out is approved, but cannot redefine the
approval set.  The manifest deliberately supports multiple exact SHA/tree pairs so
the current known-good rollback release and a reviewed candidate can both remain
startable during cutover.
"""

import argparse
import json
import os
import stat
import subprocess
from pathlib import Path

DEFAULT_MANIFEST = Path("/etc/polymarket-weather-paper/approved-releases.json")
AUTHORITY_VERSION = "weather-paper-host-release-authority-v1"


def fail(code: str) -> "None":
    raise SystemExit(code)


def _secure_root_file(path: Path) -> None:
    st = path.stat()
    if not stat.S_ISREG(st.st_mode):
        fail("HOST_RELEASE_AUTHORITY_NOT_REGULAR")
    if st.st_uid != 0:
        fail("HOST_RELEASE_AUTHORITY_NOT_ROOT_OWNED")
    if st.st_mode & 0o022:
        fail("HOST_RELEASE_AUTHORITY_WRITABLE_BY_NONROOT")


def _run_git(repo: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", "-C", str(repo), *args],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
        env={"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": str(repo.parent)},
    )
    if proc.returncode != 0:
        fail("HOST_RELEASE_GIT_QUERY_FAILED")
    return proc.stdout.strip()


def _hex(value: object, n: int) -> str:
    text = str(value or "").strip().lower()
    if len(text) != n or any(ch not in "0123456789abcdef" for ch in text):
        fail("HOST_RELEASE_MANIFEST_IDENTITY_INVALID")
    return text


def load_manifest(path: Path) -> dict[str, str]:
    _secure_root_file(path)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        fail("HOST_RELEASE_MANIFEST_INVALID")
    if not isinstance(raw, dict) or raw.get("version") != AUTHORITY_VERSION:
        fail("HOST_RELEASE_MANIFEST_VERSION_INVALID")
    rows = raw.get("approved")
    if not isinstance(rows, list) or not rows:
        fail("HOST_RELEASE_MANIFEST_EMPTY")
    approved: dict[str, str] = {}
    for row in rows:
        if not isinstance(row, dict):
            fail("HOST_RELEASE_MANIFEST_ENTRY_INVALID")
        sha = _hex(row.get("sha"), 40)
        tree = _hex(row.get("tree"), 40)
        previous = approved.get(sha)
        if previous is not None and previous != tree:
            fail("HOST_RELEASE_MANIFEST_DUPLICATE_CONFLICT")
        approved[sha] = tree
    return approved


def verify_object(repo: Path, sha: str, manifest: Path) -> None:
    repo = repo.resolve()
    if not (repo / ".git").is_dir():
        fail("HOST_RELEASE_REPOSITORY_MISSING")
    expected_sha = _hex(sha, 40)
    approved = load_manifest(manifest)
    expected_tree = approved.get(expected_sha)
    if expected_tree is None:
        fail("HOST_RELEASE_SHA_NOT_APPROVED")
    actual_commit = _run_git(repo, "rev-parse", f"{expected_sha}^{{commit}}").lower()
    if actual_commit != expected_sha:
        fail("HOST_RELEASE_COMMIT_IDENTITY_MISMATCH")
    actual_tree = _run_git(repo, "rev-parse", f"{expected_sha}^{{tree}}").lower()
    if actual_tree != expected_tree:
        fail("HOST_RELEASE_TREE_IDENTITY_MISMATCH")


def verify_checkout(repo: Path, release_file: Path, manifest: Path) -> None:
    repo = repo.resolve()
    release_file = release_file.resolve()
    if not release_file.is_file():
        fail("HOST_RELEASE_MARKER_MISSING")
    head = _run_git(repo, "rev-parse", "HEAD").lower()
    marker = release_file.read_text(encoding="utf-8").strip().lower()
    if marker != head:
        fail("HOST_RELEASE_MARKER_HEAD_MISMATCH")
    verify_object(repo, head, manifest)
    if _run_git(repo, "status", "--porcelain", "--untracked-files=all"):
        fail("HOST_RELEASE_CHECKOUT_DIRTY")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("verify-object", "verify-checkout"))
    parser.add_argument("--app-dir", type=Path, required=True)
    parser.add_argument("--release-file", type=Path)
    parser.add_argument("--sha")
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    args = parser.parse_args()
    if args.command == "verify-object":
        if not args.sha:
            fail("HOST_RELEASE_SHA_REQUIRED")
        verify_object(args.app_dir, args.sha, args.manifest)
    else:
        if args.release_file is None:
            fail("HOST_RELEASE_MARKER_REQUIRED")
        verify_checkout(args.app_dir, args.release_file, args.manifest)
    print("PASS: host release authority verified exact approved SHA/tree.")


if __name__ == "__main__":
    main()
