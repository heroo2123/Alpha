#!/usr/bin/env python3
from __future__ import annotations

"""Snapshot, verify and restore the exact weather-PAPER virtualenv tree.

The previous rollback path reinstalled package names into the candidate-mutated venv.
This helper instead preserves regular-file bytes, modes and symlink targets and verifies
the restored tree before the old service may restart.
"""

import argparse
import hashlib
import json
import os
import shutil
import stat
import tarfile
import tempfile
from pathlib import Path, PurePosixPath


VERSION = "weather_paper_venv_snapshot_v1_exact_tree"


class VenvSnapshotError(RuntimeError):
    pass


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _validate_venv(path: Path) -> Path:
    venv = path.expanduser().absolute()
    if venv.name != ".venv" or venv.is_symlink() or not venv.is_dir():
        raise VenvSnapshotError("VENV_ROOT_INVALID")
    python = venv / "bin" / "python"
    if not python.exists():
        raise VenvSnapshotError("VENV_PYTHON_MISSING")
    return venv


def _tree(venv: Path) -> list[dict]:
    parent = venv.parent
    entries: list[dict] = []
    pending = [venv]
    while pending:
        path = pending.pop()
        info = path.lstat()
        rel = path.relative_to(parent).as_posix()
        mode = stat.S_IMODE(info.st_mode)
        if stat.S_ISDIR(info.st_mode):
            entries.append({"path": rel, "kind": "dir", "mode": mode})
            children = sorted(path.iterdir(), key=lambda item: item.name, reverse=True)
            pending.extend(children)
        elif stat.S_ISREG(info.st_mode):
            entries.append(
                {
                    "path": rel,
                    "kind": "file",
                    "mode": mode,
                    "size": info.st_size,
                    "sha256": _sha256_file(path),
                }
            )
        elif stat.S_ISLNK(info.st_mode):
            entries.append(
                {
                    "path": rel,
                    "kind": "symlink",
                    "mode": mode,
                    "target": os.readlink(path),
                }
            )
        else:
            raise VenvSnapshotError(f"VENV_UNSUPPORTED_FILE_TYPE:{rel}")
    entries.sort(key=lambda row: row["path"])
    return entries


def _tree_sha(entries: list[dict]) -> str:
    raw = json.dumps(
        entries, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _atomic_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix="." + path.name + ".", dir=path.parent)
    os.close(fd)
    tmp = Path(tmp_name)
    try:
        tmp.write_text(json.dumps(payload, sort_keys=True, indent=2) + "\n", encoding="utf-8")
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def _safe_members(tar: tarfile.TarFile) -> list[tarfile.TarInfo]:
    members = tar.getmembers()
    names: list[tuple[str, ...]] = []
    symlinks: list[tuple[str, ...]] = []
    for member in members:
        pure = PurePosixPath(member.name)
        parts = pure.parts
        if pure.is_absolute() or not parts or parts[0] != ".venv" or ".." in parts:
            raise VenvSnapshotError("VENV_ARCHIVE_PATH_INVALID")
        if not (member.isdir() or member.isfile() or member.issym()):
            raise VenvSnapshotError(f"VENV_ARCHIVE_TYPE_INVALID:{member.name}")
        names.append(parts)
        if member.issym():
            symlinks.append(parts)
    # No later archive member may be nested beneath a symlink. This prevents a
    # malicious/tampered archive from redirecting extraction outside the venv.
    for link in symlinks:
        for parts in names:
            if len(parts) > len(link) and parts[: len(link)] == link:
                raise VenvSnapshotError("VENV_ARCHIVE_SYMLINK_PREFIX_INVALID")
    return members


def snapshot(venv_path: Path, archive: Path, manifest: Path) -> dict:
    venv = _validate_venv(venv_path)
    archive = archive.expanduser().absolute()
    manifest = manifest.expanduser().absolute()
    archive.parent.mkdir(parents=True, exist_ok=True)
    before = _tree(venv)

    fd, tmp_name = tempfile.mkstemp(prefix="." + archive.name + ".", dir=archive.parent)
    os.close(fd)
    tmp = Path(tmp_name)
    try:
        with tarfile.open(tmp, mode="w", format=tarfile.PAX_FORMAT, dereference=False) as tar:
            tar.add(venv, arcname=".venv", recursive=True)
        os.chmod(tmp, 0o600)
        after = _tree(venv)
        if before != after:
            raise VenvSnapshotError("VENV_CHANGED_DURING_SNAPSHOT")
        archive_sha = _sha256_file(tmp)
        os.replace(tmp, archive)
    finally:
        tmp.unlink(missing_ok=True)

    payload = {
        "version": VERSION,
        "archive": str(archive),
        "archive_sha256": archive_sha,
        "tree_sha256": _tree_sha(before),
        "entry_count": len(before),
        "entries": before,
    }
    _atomic_json(manifest, payload)
    verify_archive(archive, manifest)
    return payload


def _load_manifest(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise VenvSnapshotError("VENV_MANIFEST_INVALID") from exc
    if not isinstance(value, dict) or value.get("version") != VERSION:
        raise VenvSnapshotError("VENV_MANIFEST_VERSION_INVALID")
    entries = value.get("entries")
    if not isinstance(entries, list) or not entries:
        raise VenvSnapshotError("VENV_MANIFEST_ENTRIES_INVALID")
    if value.get("tree_sha256") != _tree_sha(entries):
        raise VenvSnapshotError("VENV_MANIFEST_TREE_DIGEST_MISMATCH")
    return value


def verify_archive(archive_path: Path, manifest_path: Path) -> dict:
    archive = archive_path.expanduser().absolute()
    manifest = _load_manifest(manifest_path.expanduser().absolute())
    if not archive.is_file() or archive.is_symlink():
        raise VenvSnapshotError("VENV_ARCHIVE_INVALID")
    if _sha256_file(archive) != str(manifest.get("archive_sha256") or ""):
        raise VenvSnapshotError("VENV_ARCHIVE_SHA256_MISMATCH")
    with tarfile.open(archive, mode="r") as tar:
        members = _safe_members(tar)
    expected_paths = {str(row.get("path") or "") for row in manifest["entries"]}
    member_paths = {member.name.rstrip("/") for member in members}
    if expected_paths != member_paths:
        raise VenvSnapshotError("VENV_ARCHIVE_MANIFEST_PATHSET_MISMATCH")
    return manifest


def verify_tree(venv_path: Path, manifest_path: Path) -> dict:
    venv = _validate_venv(venv_path)
    manifest = _load_manifest(manifest_path.expanduser().absolute())
    observed = _tree(venv)
    if observed != manifest["entries"]:
        raise VenvSnapshotError("VENV_RESTORED_TREE_MISMATCH")
    return manifest


def restore(venv_path: Path, archive_path: Path, manifest_path: Path) -> dict:
    venv = venv_path.expanduser().absolute()
    if venv.name != ".venv":
        raise VenvSnapshotError("VENV_ROOT_INVALID")
    app_dir = venv.parent
    manifest = verify_archive(archive_path, manifest_path)
    if venv.exists() or venv.is_symlink():
        if venv.is_symlink() or not venv.is_dir():
            raise VenvSnapshotError("CURRENT_VENV_ROOT_INVALID")
        shutil.rmtree(venv)

    archive = archive_path.expanduser().absolute()
    with tarfile.open(archive, mode="r") as tar:
        members = _safe_members(tar)
        # Members were generated locally from a trusted venv and the exact archive
        # digest is verified above. Path/symlink-prefix checks prevent traversal. Use
        # an explicit extraction policy so Python 3.14 cannot silently change restore
        # semantics after the manifest/archive have already been certified.
        tar.extractall(path=app_dir, members=members, filter="fully_trusted")
    verify_tree(venv, manifest_path)
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("snapshot", "verify", "restore", "verify-tree"):
        item = sub.add_parser(name)
        item.add_argument("--venv", type=Path, required=True)
        item.add_argument("--archive", type=Path)
        item.add_argument("--manifest", type=Path, required=True)
    args = parser.parse_args()
    try:
        if args.command == "snapshot":
            if args.archive is None:
                raise VenvSnapshotError("VENV_ARCHIVE_ARGUMENT_REQUIRED")
            payload = snapshot(args.venv, args.archive, args.manifest)
        elif args.command == "verify":
            if args.archive is None:
                raise VenvSnapshotError("VENV_ARCHIVE_ARGUMENT_REQUIRED")
            payload = verify_archive(args.archive, args.manifest)
        elif args.command == "restore":
            if args.archive is None:
                raise VenvSnapshotError("VENV_ARCHIVE_ARGUMENT_REQUIRED")
            payload = restore(args.venv, args.archive, args.manifest)
        else:
            payload = verify_tree(args.venv, args.manifest)
    except (VenvSnapshotError, OSError, tarfile.TarError) as exc:
        print(f"FAIL_VENV_SNAPSHOT:{exc}")
        return 2
    print(
        json.dumps(
            {
                "acceptance": "PASS_VENV_SNAPSHOT_" + args.command.upper().replace("-", "_"),
                "version": payload["version"],
                "archive_sha256": payload["archive_sha256"],
                "tree_sha256": payload["tree_sha256"],
                "entry_count": payload["entry_count"],
            },
            sort_keys=True,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
