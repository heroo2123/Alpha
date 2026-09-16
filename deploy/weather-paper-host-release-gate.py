#!/usr/bin/env python3
from __future__ import annotations

"""Reference implementation for the independently installed weather PAPER host authority.

IMPORTANT: ordinary candidate deployment never installs this file. The live copy is
bootstrapped from an external, operator-pinned authority bundle and verifies its own
root-owned manifest/digests before authorizing a candidate or cutover generation.
"""

import argparse
import hashlib
import json
import os
import stat
import subprocess
from pathlib import Path

DEFAULT_APPROVALS = Path("/etc/polymarket-weather-paper/approved-releases.json")
DEFAULT_AUTHORITY = Path("/etc/polymarket-weather-paper/host-authority.json")
DEFAULT_GENERATIONS = Path("/var/lib/polymarket-weather-paper-rollback/generations")
AUTHORITY_VERSION = "weather-paper-host-release-authority-v2-independent"
PROTECTED_CANDIDATE_PATHS = (
    "deploy/weather-paper-host-release-gate.py",
    "deploy/weather-paper-host-recovery.sh",
    "deploy/weather-paper-host-snapshot.sh",
    "deploy/weather-paper-venv-snapshot.py",
    "deploy/install-weather-paper-host-trust.sh",
)


def fail(code: str) -> "None":
    raise SystemExit(code)


def digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def secure_root_file(path: Path) -> None:
    st = path.lstat()
    if not stat.S_ISREG(st.st_mode) or path.is_symlink():
        fail("HOST_AUTHORITY_NOT_REGULAR")
    if st.st_uid != 0 or st.st_mode & 0o022:
        fail("HOST_AUTHORITY_CUSTODY_INVALID")


def load_json(path: Path) -> dict:
    secure_root_file(path)
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        fail("HOST_AUTHORITY_JSON_INVALID")
    if not isinstance(value, dict):
        fail("HOST_AUTHORITY_JSON_INVALID")
    return value


def _hex(value: object, n: int, code: str) -> str:
    text = str(value or "").strip().lower()
    if len(text) != n or any(c not in "0123456789abcdef" for c in text):
        fail(code)
    return text


def _git(repo: Path, *args: str) -> str:
    cp = subprocess.run(
        ["/usr/bin/git", "-C", str(repo), *args], text=True,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
        env={"PATH": "/usr/bin:/bin", "HOME": "/nonexistent", "PYTHONNOUSERSITE": "1"},
    )
    if cp.returncode:
        fail("HOST_RELEASE_GIT_QUERY_FAILED")
    return cp.stdout.strip()


def verify_authority(authority_manifest: Path = DEFAULT_AUTHORITY) -> dict:
    payload = load_json(authority_manifest)
    if payload.get("version") != AUTHORITY_VERSION:
        fail("HOST_AUTHORITY_VERSION_INVALID")
    files = payload.get("files")
    if not isinstance(files, dict) or not files:
        fail("HOST_AUTHORITY_FILESET_INVALID")
    libexec = Path(str(payload.get("libexec") or "/usr/local/libexec/polymarket-weather-paper")).resolve()
    for name, expected in files.items():
        if not isinstance(name, str) or "/" in name or name.startswith("."):
            fail("HOST_AUTHORITY_FILESET_INVALID")
        expected = _hex(expected, 64, "HOST_AUTHORITY_FILE_DIGEST_INVALID")
        path = libexec / name
        secure_root_file(path)
        if digest(path) != expected:
            fail("HOST_AUTHORITY_FILE_DIGEST_MISMATCH")
    protected = payload.get("protected_candidate_blobs")
    if not isinstance(protected, dict) or set(protected) != set(PROTECTED_CANDIDATE_PATHS):
        fail("HOST_AUTHORITY_PROTECTED_PATHSET_INVALID")
    for value in protected.values():
        _hex(value, 40, "HOST_AUTHORITY_PROTECTED_BLOB_INVALID")
    _hex(payload.get("bundle_sha256"), 64, "HOST_AUTHORITY_BUNDLE_DIGEST_INVALID")
    return payload


def verify_candidate_authority_files(repo: Path, sha: str, authority: dict) -> None:
    protected = authority["protected_candidate_blobs"]
    for path in PROTECTED_CANDIDATE_PATHS:
        observed = _git(repo, "rev-parse", f"{sha}:{path}").lower()
        if observed != str(protected[path]).lower():
            fail("HOST_RELEASE_CANDIDATE_REDEFINES_AUTHORITY")


def load_approvals(path: Path) -> dict[str, str]:
    raw = load_json(path)
    if raw.get("version") not in {"weather-paper-host-release-authority-v1", AUTHORITY_VERSION}:
        fail("HOST_RELEASE_MANIFEST_VERSION_INVALID")
    rows = raw.get("approved")
    if not isinstance(rows, list) or not rows:
        fail("HOST_RELEASE_MANIFEST_EMPTY")
    out: dict[str, str] = {}
    for row in rows:
        if not isinstance(row, dict):
            fail("HOST_RELEASE_MANIFEST_ENTRY_INVALID")
        sha = _hex(row.get("sha"), 40, "HOST_RELEASE_MANIFEST_IDENTITY_INVALID")
        tree = _hex(row.get("tree"), 40, "HOST_RELEASE_MANIFEST_IDENTITY_INVALID")
        if sha in out and out[sha] != tree:
            fail("HOST_RELEASE_MANIFEST_DUPLICATE_CONFLICT")
        out[sha] = tree
    return out


def verify_object(repo: Path, sha: str, approvals: Path, authority_manifest: Path) -> None:
    repo = repo.resolve()
    if not (repo / ".git").is_dir():
        fail("HOST_RELEASE_REPOSITORY_MISSING")
    authority = verify_authority(authority_manifest)
    expected_sha = _hex(sha, 40, "HOST_RELEASE_MANIFEST_IDENTITY_INVALID")
    expected_tree = load_approvals(approvals).get(expected_sha)
    if expected_tree is None:
        fail("HOST_RELEASE_SHA_NOT_APPROVED")
    if _git(repo, "rev-parse", f"{expected_sha}^{{commit}}").lower() != expected_sha:
        fail("HOST_RELEASE_COMMIT_IDENTITY_MISMATCH")
    if _git(repo, "rev-parse", f"{expected_sha}^{{tree}}").lower() != expected_tree:
        fail("HOST_RELEASE_TREE_IDENTITY_MISMATCH")
    verify_candidate_authority_files(repo, expected_sha, authority)


def load_generation(generation_id: str, generations: Path, authority_manifest: Path) -> dict:
    verify_authority(authority_manifest)
    if not generation_id or any(c not in "0123456789abcdef-" for c in generation_id.lower()) or len(generation_id) > 80:
        fail("HOST_GENERATION_ID_INVALID")
    root = (generations / generation_id).resolve()
    if root.parent != generations.resolve():
        fail("HOST_GENERATION_PATH_INVALID")
    manifest = root / "generation.json"
    payload = load_json(manifest)
    if payload.get("version") != "weather-paper-cutover-generation-v1":
        fail("HOST_GENERATION_VERSION_INVALID")
    if payload.get("generation_id") != generation_id:
        fail("HOST_GENERATION_ID_MISMATCH")
    return payload


def verify_generation(generation_id: str, candidate_sha: str, generations: Path, authority_manifest: Path) -> dict:
    payload = load_generation(generation_id, generations, authority_manifest)
    candidate = _hex(candidate_sha, 40, "HOST_GENERATION_CANDIDATE_INVALID")
    if payload.get("candidate_sha") != candidate:
        fail("HOST_GENERATION_CANDIDATE_MISMATCH")
    return payload


def verify_checkout(repo: Path, release_file: Path, generation_file: Path, approvals: Path, authority_manifest: Path, generations: Path) -> None:
    if not release_file.is_file() or not generation_file.is_file():
        fail("HOST_RELEASE_MARKER_MISSING")
    head = _git(repo.resolve(), "rev-parse", "HEAD").lower()
    marker = release_file.read_text(encoding="utf-8").strip().lower()
    generation_id = generation_file.read_text(encoding="utf-8").strip()
    if marker != head:
        fail("HOST_RELEASE_MARKER_HEAD_MISMATCH")
    verify_object(repo, head, approvals, authority_manifest)
    verify_generation(generation_id, head, generations, authority_manifest)
    if _git(repo.resolve(), "status", "--porcelain", "--untracked-files=all"):
        fail("HOST_RELEASE_CHECKOUT_DIRTY")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("command", choices=("verify-authority", "verify-object", "verify-generation", "verify-checkout"))
    ap.add_argument("--app-dir", type=Path)
    ap.add_argument("--release-file", type=Path)
    ap.add_argument("--generation-file", type=Path)
    ap.add_argument("--generation-id")
    ap.add_argument("--sha")
    ap.add_argument("--manifest", type=Path, default=DEFAULT_APPROVALS)
    ap.add_argument("--authority-manifest", type=Path, default=DEFAULT_AUTHORITY)
    ap.add_argument("--generations", type=Path, default=DEFAULT_GENERATIONS)
    args = ap.parse_args()
    if args.command == "verify-authority":
        verify_authority(args.authority_manifest)
    elif args.command == "verify-object":
        if args.app_dir is None or not args.sha: fail("HOST_RELEASE_ARGUMENT_REQUIRED")
        verify_object(args.app_dir, args.sha, args.manifest, args.authority_manifest)
    elif args.command == "verify-generation":
        if not args.generation_id or not args.sha: fail("HOST_GENERATION_ARGUMENT_REQUIRED")
        verify_generation(args.generation_id, args.sha, args.generations, args.authority_manifest)
    else:
        if args.app_dir is None or args.release_file is None or args.generation_file is None:
            fail("HOST_RELEASE_ARGUMENT_REQUIRED")
        verify_checkout(args.app_dir, args.release_file, args.generation_file, args.manifest, args.authority_manifest, args.generations)
    print("PASS: independent host authority verified.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
