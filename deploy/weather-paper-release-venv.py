#!/usr/bin/env python3
from __future__ import annotations

"""Build and attest an immutable release-specific weather PAPER virtualenv."""

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import venv
from pathlib import Path

VERSION = "weather-paper-release-venv-v1"
BOOTSTRAP_DISTS = {"pip"}
FORBIDDEN_DISTS = {
    "web3", "eth-account", "eth-keyfile", "eth-keys", "eth-rlp", "py-clob-client",
    "coincurve", "ecdsa", "bip-utils", "mnemonic",
}


def fail(code: str) -> "None":
    raise SystemExit(code)


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def norm(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def locked_names(lock: Path) -> set[str]:
    names: set[str] = set()
    row = ""
    for raw in lock.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        row = (row + " " + line).strip() if row else line
        if row.endswith("\\"):
            row = row[:-1].rstrip()
            continue
        if row.startswith(("--", "-r", "-c", "git+", "http:", "https:")):
            fail("RELEASE_VENV_LOCK_UNSUPPORTED_DIRECTIVE")
        match = re.match(r"^([A-Za-z0-9_.-]+)(?:\[[^]]+\])?==[^ ;]+(?:\s|$)", row)
        if not match:
            fail("RELEASE_VENV_LOCK_NOT_EXACT")
        if "--hash=sha256:" not in row:
            fail("RELEASE_VENV_LOCK_ENTRY_UNHASHED")
        names.add(norm(match.group(1)))
        row = ""
    if row or not names:
        fail("RELEASE_VENV_LOCK_INVALID")
    return names


def inventory(python: Path) -> list[dict[str, str]]:
    code = '''import importlib.metadata,json,re\nnorm=lambda s: re.sub(r"[-_.]+","-",s).lower()\nrows=[]\nfor d in importlib.metadata.distributions():\n n=d.metadata.get("Name")\n if n: rows.append({"name":norm(n),"version":d.version})\nprint(json.dumps(sorted(rows,key=lambda r:(r["name"],r["version"])),separators=(",",":")))'''
    env = {"PATH": str(python.parent) + ":/usr/bin:/bin", "PYTHONNOUSERSITE": "1", "HOME": "/nonexistent"}
    cp = subprocess.run([str(python), "-E", "-s", "-c", code], text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env, check=False)
    if cp.returncode:
        fail("RELEASE_VENV_INVENTORY_FAILED")
    try:
        rows = json.loads(cp.stdout)
    except json.JSONDecodeError:
        fail("RELEASE_VENV_INVENTORY_INVALID")
    if not isinstance(rows, list):
        fail("RELEASE_VENV_INVENTORY_INVALID")
    return rows


def canonical_manifest(venv_dir: Path, lock: Path, rows: list[dict[str, str]]) -> dict:
    locked = locked_names(lock)
    installed = {norm(str(r["name"])) for r in rows}
    unexpected = sorted(installed - locked - BOOTSTRAP_DISTS)
    missing = sorted(locked - installed)
    forbidden = sorted(installed & FORBIDDEN_DISTS)
    if unexpected:
        fail("RELEASE_VENV_UNEXPECTED_DISTRIBUTION:" + ",".join(unexpected))
    if missing:
        fail("RELEASE_VENV_LOCKED_DISTRIBUTION_MISSING:" + ",".join(missing))
    if forbidden:
        fail("RELEASE_VENV_FORBIDDEN_DISTRIBUTION:" + ",".join(forbidden))
    payload = {
        "version": VERSION,
        "venv": str(venv_dir.resolve()),
        "python": str((venv_dir / "bin/python").resolve()),
        "lock_sha256": sha256(lock),
        "distributions": rows,
    }
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    payload["manifest_sha256"] = hashlib.sha256(raw).hexdigest()
    return payload


def clean_env(venv_dir: Path) -> dict[str, str]:
    return {
        "PATH": str(venv_dir / "bin") + ":/usr/bin:/bin",
        "PYTHONNOUSERSITE": "1",
        "PIP_CONFIG_FILE": os.devnull,
        "PIP_DISABLE_PIP_VERSION_CHECK": "1",
        "HOME": "/nonexistent",
    }


def build(venv_dir: Path, lock: Path, manifest: Path) -> dict:
    venv_dir, lock = venv_dir.absolute(), lock.absolute()
    if venv_dir.exists() or venv_dir.is_symlink():
        fail("RELEASE_VENV_MUST_NOT_EXIST")
    if not lock.is_file():
        fail("RELEASE_VENV_LOCK_MISSING")
    locked_names(lock)
    venv.EnvBuilder(with_pip=True, system_site_packages=False, clear=False, symlinks=True).create(venv_dir)
    python = venv_dir / "bin/python"
    env = clean_env(venv_dir)
    cp = subprocess.run([str(python), "-E", "-s", "-m", "pip", "install", "--require-hashes", "--no-deps", "-r", str(lock)], env=env, check=False)
    if cp.returncode:
        shutil.rmtree(venv_dir, ignore_errors=True)
        fail("RELEASE_VENV_HASH_INSTALL_FAILED")
    if subprocess.run([str(python), "-E", "-s", "-m", "pip", "check"], env=env, check=False).returncode:
        shutil.rmtree(venv_dir, ignore_errors=True)
        fail("RELEASE_VENV_PIP_CHECK_FAILED")
    payload = canonical_manifest(venv_dir, lock, inventory(python))
    manifest.parent.mkdir(parents=True, exist_ok=True)
    tmp = manifest.with_name("." + manifest.name + f".{os.getpid()}.tmp")
    tmp.write_text(json.dumps(payload, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    os.chmod(tmp, 0o600)
    os.replace(tmp, manifest)
    return payload


def verify(venv_dir: Path, lock: Path, manifest: Path) -> dict:
    try:
        expected = json.loads(manifest.read_text(encoding="utf-8"))
    except Exception:
        fail("RELEASE_VENV_MANIFEST_INVALID")
    if expected.get("version") != VERSION or expected.get("venv") != str(venv_dir.absolute().resolve()):
        fail("RELEASE_VENV_MANIFEST_IDENTITY_MISMATCH")
    if expected.get("lock_sha256") != sha256(lock):
        fail("RELEASE_VENV_LOCK_DIGEST_MISMATCH")
    actual = canonical_manifest(venv_dir, lock, inventory(venv_dir / "bin/python"))
    if actual != expected:
        fail("RELEASE_VENV_MANIFEST_MISMATCH")
    return actual


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("command", choices=("build", "verify"))
    ap.add_argument("--venv", type=Path, required=True)
    ap.add_argument("--lock", type=Path, required=True)
    ap.add_argument("--manifest", type=Path, required=True)
    args = ap.parse_args()
    payload = build(args.venv, args.lock, args.manifest) if args.command == "build" else verify(args.venv, args.lock, args.manifest)
    print(json.dumps(payload, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
