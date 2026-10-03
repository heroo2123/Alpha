#!/usr/bin/env python3
"""Check local Gate 3 review-packet integrity; never grant review or launch approval."""

from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import re
import subprocess
import sys


def git(checkout: pathlib.Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(checkout), *args],
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


def verify(manifest: pathlib.Path) -> list[str]:
    errors: list[str] = []
    packet = json.loads(manifest.read_text(encoding="utf-8"))
    if not isinstance(packet, dict) or not isinstance(packet.get("candidates"), list):
        return ["manifest: missing candidates array"]

    for index, candidate in enumerate(packet["candidates"]):
        label = f"candidate[{index}]"
        try:
            checkout = pathlib.Path(candidate["exact_checkout"])
            commit = candidate["commit"]
            parent = candidate["parent"]
            tree = candidate["tree"]
            files = candidate["files"]
            for name, oid in (("commit", commit), ("parent", parent), ("tree", tree)):
                if not isinstance(oid, str) or re.fullmatch(r"[0-9a-f]{40}", oid) is None:
                    raise ValueError(f"{name} must be a full lowercase Git object ID")
            if not checkout.is_absolute() or not checkout.is_dir():
                raise ValueError("exact checkout must be an existing absolute directory")
            if not isinstance(files, list) or not files:
                raise ValueError("files must be a nonempty array")
            if git(checkout, "rev-parse", "HEAD") != commit:
                errors.append(f"{label}: HEAD differs from manifest")
            if git(checkout, "rev-parse", "HEAD^{tree}") != tree:
                errors.append(f"{label}: tree differs from manifest")
            if git(checkout, "rev-parse", "HEAD^") != parent:
                errors.append(f"{label}: parent differs from manifest")
            if git(checkout, "status", "--porcelain", "--untracked-files=all"):
                errors.append(f"{label}: checkout is dirty")

            base = candidate.get("review_base", parent)
            if not isinstance(base, str) or re.fullmatch(r"[0-9a-f]{40}", base) is None:
                raise ValueError("review_base must be a full lowercase Git object ID")
            changed = set(git(checkout, "diff", "--name-only", base, commit).splitlines())
            listed = {entry["path"] for entry in files}
            if changed != listed or len(listed) != len(files):
                errors.append(f"{label}: changed-path scope differs from manifest")

            for entry in files:
                relative = pathlib.PurePosixPath(entry["path"])
                if relative.is_absolute() or ".." in relative.parts or not relative.parts:
                    raise ValueError(f"unsafe file path: {entry['path']}")
                path = checkout.joinpath(*relative.parts)
                if not path.is_file() or path.is_symlink() or not path.resolve().is_relative_to(checkout.resolve()):
                    errors.append(f"{label}: missing or symlinked {relative}")
                    continue
                data = path.read_bytes()
                blob = git(checkout, "rev-parse", f"{commit}:{relative}")
                if blob != entry["blob_oid"]:
                    errors.append(f"{label}: Git blob differs for {relative}")
                if len(data) != entry["bytes"]:
                    errors.append(f"{label}: byte length differs for {relative}")
                if hashlib.sha256(data).hexdigest() != entry["sha256"]:
                    errors.append(f"{label}: SHA-256 differs for {relative}")
        except (KeyError, TypeError, ValueError, OSError, subprocess.CalledProcessError) as exc:
            errors.append(f"{label}: verification error: {exc}")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=pathlib.Path)
    args = parser.parse_args()
    try:
        errors = verify(args.manifest)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        errors = [f"manifest: {exc}"]
    print(json.dumps({"integrity_only": True, "ok": not errors, "errors": errors}, sort_keys=True))
    return 0 if not errors else 2


if __name__ == "__main__":
    sys.exit(main())
