#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import importlib.util
import json
import os
import re
import site
import sys
from pathlib import Path

VERSION = "weather_paper_release_venv_v1_fresh_hash_locked_exact_inventory"
EVIDENCE_VERSION = "weather_paper_release_evidence_v1"


class ReleaseEnvironmentError(RuntimeError):
    pass


def _sha_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _canon(value: str) -> str:
    return re.sub(r"[-_.]+", "-", value).lower()


def _lock(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        row = raw.split("#", 1)[0].strip()
        if not row:
            continue
        spec = row.split()[0]
        if "==" not in spec or "--hash=sha256:" not in row:
            raise ReleaseEnvironmentError("HASH_LOCK_ROW_INVALID")
        name, version = spec.split("==", 1)
        out[_canon(name.split("[", 1)[0])] = version
    if not out:
        raise ReleaseEnvironmentError("HASH_LOCK_EMPTY")
    return out


def _inventory() -> dict[str, str]:
    rows: dict[str, str] = {}
    for dist in importlib.metadata.distributions():
        name = dist.metadata.get("Name")
        if name:
            rows[_canon(name)] = dist.version
    return rows


def _tree(root: Path) -> list[dict]:
    root = root.resolve()
    rows: list[dict] = []
    for base, dirs, files in os.walk(root, followlinks=False):
        dirs.sort()
        files.sort()
        base_path = Path(base)
        rel = "." if base_path == root else base_path.relative_to(root).as_posix()
        rows.append(
            {"path": rel, "kind": "dir", "mode": base_path.lstat().st_mode & 0o777}
        )
        for name in files:
            path = base_path / name
            rel_file = path.relative_to(root).as_posix()
            stat_result = path.lstat()
            if path.is_symlink():
                rows.append(
                    {
                        "path": rel_file,
                        "kind": "symlink",
                        "target": os.readlink(path),
                        "mode": stat_result.st_mode & 0o777,
                    }
                )
            else:
                rows.append(
                    {
                        "path": rel_file,
                        "kind": "file",
                        "size": stat_result.st_size,
                        "sha256": _sha_file(path),
                        "mode": stat_result.st_mode & 0o777,
                    }
                )
        for name in list(dirs):
            path = base_path / name
            if path.is_symlink():
                rows.append(
                    {
                        "path": path.relative_to(root).as_posix(),
                        "kind": "symlink",
                        "target": os.readlink(path),
                        "mode": path.lstat().st_mode & 0o777,
                    }
                )
                dirs.remove(name)
    rows.sort(key=lambda row: (row["path"], row["kind"]))
    return rows


def _tree_sha(rows: list[dict]) -> str:
    return hashlib.sha256(
        json.dumps(rows, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _inspect(args: argparse.Namespace) -> dict:
    expected = _lock(args.lock)
    actual = _inventory()
    if actual != expected:
        raise ReleaseEnvironmentError(
            "VENV_DISTRIBUTION_INVENTORY_MISMATCH:"
            f"missing={sorted(set(expected) - set(actual))}:"
            f"unexpected={sorted(set(actual) - set(expected))}"
        )
    if site.ENABLE_USER_SITE is not False:
        raise ReleaseEnvironmentError("VENV_USER_SITE_ENABLED")
    venv = Path(sys.prefix).resolve()
    requested = args.venv.resolve()
    if venv != requested:
        raise ReleaseEnvironmentError("VENV_PREFIX_MISMATCH")
    cfg = (requested / "pyvenv.cfg").read_text(encoding="utf-8").lower()
    if "include-system-site-packages = false" not in cfg:
        raise ReleaseEnvironmentError("VENV_SYSTEM_SITE_PACKAGES_ENABLED")
    spec = importlib.util.find_spec(args.runtime_module)
    origin = Path(str(None if spec is None else spec.origin)).resolve()
    try:
        origin.relative_to(args.app_dir.resolve())
    except ValueError:
        raise ReleaseEnvironmentError("APP_MODULE_ORIGIN_OUTSIDE_CANDIDATE") from None
    rows = _tree(requested)
    return {
        "version": VERSION,
        "release_sha": args.release_sha.lower(),
        "release_tree": args.release_tree.lower(),
        "generation_id": args.generation_id,
        "app_dir": str(args.app_dir.resolve()),
        "venv_path": str(requested),
        "interpreter_path": str((requested / "bin/python").absolute()),
        "requirements_lock_sha256": _sha_file(args.lock),
        "runtime_module": args.runtime_module,
        "system_site_packages": False,
        "user_site_enabled": False,
        "distribution_inventory": [
            {"name": name, "version": actual[name]} for name in sorted(actual)
        ],
        "tree_sha256": _tree_sha(rows),
        "tree_entry_count": len(rows),
        "sys_path": list(sys.path),
        "module_origin": str(origin),
    }


def _write(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload, sort_keys=True, indent=2) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    for command in ("manifest", "verify"):
        item = sub.add_parser(command)
        item.add_argument("--venv", type=Path, required=True)
        item.add_argument("--app-dir", type=Path, required=True)
        item.add_argument("--release-sha", required=True)
        item.add_argument("--release-tree", required=True)
        item.add_argument("--generation-id", required=True)
        item.add_argument("--lock", type=Path, required=True)
        item.add_argument("--runtime-module", required=True)
        item.add_argument("--manifest", type=Path, required=True)
    evidence = sub.add_parser("evidence")
    evidence.add_argument("--manifest", type=Path, required=True)
    evidence.add_argument("--output", type=Path, required=True)
    evidence.add_argument(
        "--service-identity", default="polymarket-weather-paper.service"
    )
    args = parser.parse_args()
    try:
        if args.command in ("manifest", "verify"):
            payload = _inspect(args)
            if args.command == "manifest":
                _write(args.manifest, payload)
            elif json.loads(args.manifest.read_text(encoding="utf-8")) != payload:
                raise ReleaseEnvironmentError("VENV_MANIFEST_RUNTIME_MISMATCH")
        else:
            raw = args.manifest.read_bytes()
            manifest = json.loads(raw)
            payload = {
                "version": EVIDENCE_VERSION,
                "release_sha": manifest["release_sha"],
                "release_tree": manifest["release_tree"],
                "generation_id": manifest["generation_id"],
                "requirements_lock_sha256": manifest["requirements_lock_sha256"],
                "runtime_module": manifest["runtime_module"],
                "service_identity": args.service_identity,
                "venv_path": manifest["venv_path"],
                "interpreter_path": manifest["interpreter_path"],
                "venv_tree_sha256": manifest["tree_sha256"],
                "venv_manifest_sha256": hashlib.sha256(raw).hexdigest(),
                "venv_manifest_path": str(args.manifest.resolve()),
            }
            _write(args.output, payload)
    except (ReleaseEnvironmentError, OSError, KeyError, ValueError, json.JSONDecodeError) as exc:
        print(f"FAIL_RELEASE_ENV:{exc}", file=sys.stderr)
        return 2
    print(json.dumps({"acceptance": "PASS_RELEASE_ENV_" + args.command.upper()}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
