#!/usr/bin/env python3
from __future__ import annotations

"""Independently provisioned host trust authority for weather-only PAPER releases.

IMPORTANT: ordinary candidate deployment MUST NOT install this file. The reviewed
source is packaged/provisioned out-of-band and pinned by a root-owned component
manifest. Candidate deployments may only submit immutable release data to it.
"""

import argparse
import hashlib
import json
import os
import re
import shlex
import shutil
import sqlite3
import stat
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any

AUTHORITY_VERSION = "weather-paper-host-authority-v2"
APPROVAL_VERSION = "weather-paper-release-approvals-v2"
GENERATION_VERSION = "weather-paper-cutover-generation-v1"
RUNTIME_EVIDENCE_VERSION = "weather_paper_release_evidence_v1"
RUNTIME_VENV_VERSION = "weather_paper_release_venv_v1_fresh_hash_locked_exact_inventory"
DEFAULT_AUTHORITY_MANIFEST = Path("/etc/polymarket-weather-paper/host-authority-v2.json")
DEFAULT_APPROVAL_MANIFEST = Path("/etc/polymarket-weather-paper/approved-releases-v2.json")
DEFAULT_HOST_PATHS = Path("/etc/polymarket-weather-paper/host-paths-v2.json")
DEFAULT_GENERATIONS_ROOT = Path("/var/lib/polymarket-weather-paper-rollback/generations")
DEFAULT_RELEASES_ROOT = Path("/var/lib/polymarket-weather-paper-releases")
DEFAULT_UNIT = "polymarket-weather-paper.service"
DEFAULT_RUNTIME_MODULE = "polymarket_scanner.weather_only_live_paper_all_signals_final_v9"
PROTECTED_CANDIDATE_PATHS = (
    "deploy/weather-paper-host-authority-v2.py",
    "deploy/weather-paper-host-release-gate.py",
    "deploy/weather-paper-host-snapshot.sh",
    "deploy/weather-paper-host-recovery.sh",
    "deploy/weather-paper-venv-snapshot.py",
    "deploy/install-weather-paper-host-trust.sh",
)


class HostAuthorityError(RuntimeError):
    pass


def _fail(code: str) -> None:
    raise HostAuthorityError(code)


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _hex(value: Any, n: int, code: str) -> str:
    text = str(value or "").strip().lower()
    if len(text) != n or any(ch not in "0123456789abcdef" for ch in text):
        _fail(code)
    return text


def _canonical_name(value: str) -> str:
    return re.sub(r"[-_.]+", "-", value).lower()


def _safe_root_file(path: Path, *, expected_uid: int = 0) -> None:
    try:
        st = path.lstat()
    except FileNotFoundError:
        _fail("HOST_TRUST_FILE_MISSING")
    if stat.S_ISLNK(st.st_mode) or not stat.S_ISREG(st.st_mode):
        _fail("HOST_TRUST_FILE_NOT_REGULAR")
    if st.st_uid != expected_uid:
        _fail("HOST_TRUST_FILE_OWNER_INVALID")
    if st.st_mode & 0o022:
        _fail("HOST_TRUST_FILE_WRITABLE_BY_NONOWNER")


def _safe_root_dir(path: Path, *, expected_uid: int = 0) -> None:
    st = path.lstat()
    if stat.S_ISLNK(st.st_mode) or not stat.S_ISDIR(st.st_mode):
        _fail("HOST_TRUST_DIR_INVALID")
    if st.st_uid != expected_uid or st.st_mode & 0o022:
        _fail("HOST_TRUST_DIR_CUSTODY_INVALID")


def _load_json(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise HostAuthorityError("HOST_TRUST_JSON_INVALID") from exc
    if not isinstance(value, dict):
        _fail("HOST_TRUST_JSON_OBJECT_REQUIRED")
    return value


def _run(
    args: list[str], *, check: bool = True, env: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    clean = env if env is not None else {
        "PATH": "/usr/bin:/bin",
        "HOME": "/nonexistent",
        "GIT_CONFIG_NOSYSTEM": "1",
    }
    proc = subprocess.run(
        args,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
        env=clean,
    )
    if check and proc.returncode != 0:
        raise HostAuthorityError(
            f"HOST_COMMAND_FAILED:{Path(args[0]).name}:{proc.returncode}"
        )
    return proc


def _git(repo: Path, *args: str) -> str:
    return _run(["git", "-C", str(repo), *args]).stdout.strip()


def _git_bytes(repo: Path, sha: str, path: str) -> bytes:
    proc = subprocess.run(
        ["git", "-C", str(repo), "show", f"{sha}:{path}"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
        env={
            "PATH": "/usr/bin:/bin",
            "HOME": "/nonexistent",
            "GIT_CONFIG_NOSYSTEM": "1",
        },
    )
    if proc.returncode != 0:
        _fail(f"HOST_RELEASE_PROTECTED_PATH_MISSING:{path}")
    return proc.stdout


def load_authority_manifest(
    path: Path = DEFAULT_AUTHORITY_MANIFEST, *, expected_uid: int = 0
) -> dict:
    _safe_root_file(path, expected_uid=expected_uid)
    data = _load_json(path)
    if data.get("version") != AUTHORITY_VERSION:
        _fail("HOST_AUTHORITY_VERSION_INVALID")
    authority_id = str(data.get("authority_id") or "")
    if not re.fullmatch(r"[A-Za-z0-9_.:-]{8,128}", authority_id):
        _fail("HOST_AUTHORITY_ID_INVALID")
    components = data.get("components")
    if not isinstance(components, dict) or not components:
        _fail("HOST_AUTHORITY_COMPONENTS_INVALID")
    protected = data.get("protected_candidate_paths")
    if not isinstance(protected, dict):
        _fail("HOST_AUTHORITY_PROTECTED_POLICY_INVALID")
    if set(protected) != set(PROTECTED_CANDIDATE_PATHS):
        _fail("HOST_AUTHORITY_PROTECTED_POLICY_INCOMPLETE")
    for name, digest in protected.items():
        _hex(digest, 64, f"HOST_AUTHORITY_PROTECTED_DIGEST_INVALID:{name}")
    return data


def verify_component_digests(
    data: dict, *, expected_uid: int = 0
) -> dict[str, str]:
    checked: dict[str, str] = {}
    components = data.get("components")
    if not isinstance(components, dict):
        _fail("HOST_AUTHORITY_COMPONENTS_INVALID")
    for name, row in components.items():
        if not isinstance(name, str) or not isinstance(row, dict):
            _fail("HOST_AUTHORITY_COMPONENT_ROW_INVALID")
        path = Path(str(row.get("path") or ""))
        if not path.is_absolute():
            _fail("HOST_AUTHORITY_COMPONENT_PATH_INVALID")
        expected = _hex(
            row.get("sha256"), 64, "HOST_AUTHORITY_COMPONENT_DIGEST_INVALID"
        )
        _safe_root_file(path, expected_uid=expected_uid)
        actual = _sha256_file(path)
        if actual != expected:
            _fail(f"HOST_AUTHORITY_COMPONENT_DIGEST_MISMATCH:{name}")
        checked[name] = actual
    return checked


def verify_authority(
    authority_manifest: Path = DEFAULT_AUTHORITY_MANIFEST,
    *,
    expected_uid: int = 0,
) -> dict:
    data = load_authority_manifest(authority_manifest, expected_uid=expected_uid)
    checked = verify_component_digests(data, expected_uid=expected_uid)
    return {
        "version": AUTHORITY_VERSION,
        "authority_id": data["authority_id"],
        "authority_manifest_sha256": _sha256_file(authority_manifest),
        "components": checked,
    }


def load_approvals(
    path: Path = DEFAULT_APPROVAL_MANIFEST, *, expected_uid: int = 0
) -> dict[str, dict]:
    _safe_root_file(path, expected_uid=expected_uid)
    data = _load_json(path)
    if data.get("version") != APPROVAL_VERSION:
        _fail("HOST_RELEASE_APPROVAL_VERSION_INVALID")
    rows = data.get("approved")
    if not isinstance(rows, list) or not rows:
        _fail("HOST_RELEASE_APPROVALS_EMPTY")
    out: dict[str, dict] = {}
    for row in rows:
        if not isinstance(row, dict):
            _fail("HOST_RELEASE_APPROVAL_ROW_INVALID")
        sha = _hex(row.get("sha"), 40, "HOST_RELEASE_APPROVAL_SHA_INVALID")
        tree = _hex(row.get("tree"), 40, "HOST_RELEASE_APPROVAL_TREE_INVALID")
        lock = _hex(
            row.get("requirements_lock_sha256"),
            64,
            "HOST_RELEASE_APPROVAL_LOCK_INVALID",
        )
        if (
            row.get("runtime_module") != DEFAULT_RUNTIME_MODULE
            or row.get("service_identity") != DEFAULT_UNIT
        ):
            _fail("HOST_RELEASE_APPROVAL_RUNTIME_IDENTITY_INVALID")
        normalized = dict(row)
        normalized.update(
            {"sha": sha, "tree": tree, "requirements_lock_sha256": lock}
        )
        if sha in out and out[sha] != normalized:
            _fail("HOST_RELEASE_APPROVAL_DUPLICATE_CONFLICT")
        out[sha] = normalized
    return out


def verify_object_policy(
    repo: Path, sha: str, authority: dict, approvals: dict[str, dict]
) -> dict:
    repo = repo.resolve()
    if not (repo / ".git").is_dir():
        _fail("HOST_RELEASE_REPOSITORY_MISSING")
    sha = _hex(sha, 40, "HOST_RELEASE_SHA_INVALID")
    row = approvals.get(sha)
    if row is None:
        _fail("HOST_RELEASE_SHA_NOT_APPROVED")
    if _git(repo, "rev-parse", f"{sha}^{{commit}}").lower() != sha:
        _fail("HOST_RELEASE_COMMIT_IDENTITY_MISMATCH")
    tree = _git(repo, "rev-parse", f"{sha}^{{tree}}").lower()
    if tree != row["tree"]:
        _fail("HOST_RELEASE_TREE_IDENTITY_MISMATCH")
    protected = authority["protected_candidate_paths"]
    for path in PROTECTED_CANDIDATE_PATHS:
        actual = _sha256_bytes(_git_bytes(repo, sha, path))
        expected = str(protected[path]).lower()
        if actual != expected:
            _fail(f"HOST_RELEASE_PROTECTED_IMPLEMENTATION_CHANGED:{path}")
    lock_bytes = _git_bytes(repo, sha, "requirements-runtime-hashed.txt")
    if _sha256_bytes(lock_bytes) != row["requirements_lock_sha256"]:
        _fail("HOST_RELEASE_HASH_LOCK_DIGEST_MISMATCH")
    return row


def verify_object(
    repo: Path,
    sha: str,
    *,
    authority_manifest: Path = DEFAULT_AUTHORITY_MANIFEST,
    approval_manifest: Path = DEFAULT_APPROVAL_MANIFEST,
    expected_uid: int = 0,
) -> dict:
    authority = load_authority_manifest(
        authority_manifest, expected_uid=expected_uid
    )
    verify_component_digests(authority, expected_uid=expected_uid)
    approvals = load_approvals(approval_manifest, expected_uid=expected_uid)
    return verify_object_policy(repo, sha, authority, approvals)


def verify_checkout(repo: Path, release_file: Path, **kwargs: Any) -> dict:
    if not release_file.is_file() or release_file.is_symlink():
        _fail("HOST_RELEASE_MARKER_MISSING")
    head = _git(repo, "rev-parse", "HEAD").lower()
    marker = release_file.read_text(encoding="utf-8").strip().lower()
    if marker != head:
        _fail("HOST_RELEASE_MARKER_HEAD_MISMATCH")
    row = verify_object(repo, head, **kwargs)
    if _git(repo, "status", "--porcelain", "--untracked-files=all"):
        _fail("HOST_RELEASE_CHECKOUT_DIRTY")
    return row


def _load_host_paths(
    path: Path = DEFAULT_HOST_PATHS, *, expected_uid: int = 0
) -> dict:
    _safe_root_file(path, expected_uid=expected_uid)
    data = _load_json(path)
    required = (
        "app_dir",
        "config_dir",
        "db_path",
        "unit",
        "deploy_user",
        "deploy_uid",
        "deploy_gid",
        "releases_root",
        "generations_root",
    )
    if any(key not in data for key in required):
        _fail("HOST_PATHS_FIELDS_MISSING")
    for key in (
        "app_dir",
        "config_dir",
        "db_path",
        "releases_root",
        "generations_root",
    ):
        if not Path(str(data[key])).is_absolute():
            _fail(f"HOST_PATH_INVALID:{key}")
    if data["unit"] != DEFAULT_UNIT:
        _fail("HOST_PATH_UNIT_INVALID")
    return data


def _generation_dir(generation_id: str, root: Path) -> Path:
    if not re.fullmatch(
        r"gen-[0-9]{8}T[0-9]{6}Z-[0-9a-f]{12}-[0-9a-f]{12}-[0-9a-f]{12}",
        generation_id,
    ):
        _fail("CUTOVER_GENERATION_ID_INVALID")
    return root / generation_id


def load_generation(
    generation_id: str,
    *,
    root: Path = DEFAULT_GENERATIONS_ROOT,
    expected_uid: int = 0,
) -> tuple[Path, dict]:
    directory = _generation_dir(generation_id, root)
    _safe_root_dir(directory, expected_uid=expected_uid)
    manifest = directory / "generation.json"
    _safe_root_file(manifest, expected_uid=expected_uid)
    data = _load_json(manifest)
    if (
        data.get("version") != GENERATION_VERSION
        or data.get("generation_id") != generation_id
    ):
        _fail("CUTOVER_GENERATION_MANIFEST_IDENTITY_INVALID")
    return directory, data


def validate_generation_payload(
    data: dict,
    *,
    generation_id: str,
    app_dir: Path,
    candidate_sha: str,
    phase: str,
    current_sha: str,
    current_tree: str,
    release_marker: str,
) -> None:
    if data.get("generation_id") != generation_id:
        _fail("CUTOVER_GENERATION_ID_MISMATCH")
    if Path(str(data.get("app_dir") or "")).resolve() != app_dir.resolve():
        _fail("CUTOVER_GENERATION_APP_DIR_MISMATCH")
    candidate_sha = _hex(
        candidate_sha, 40, "CUTOVER_CANDIDATE_SHA_INVALID"
    )
    if data.get("candidate_sha") != candidate_sha:
        _fail("CUTOVER_GENERATION_CANDIDATE_MISMATCH")
    if phase == "predecessor":
        expected_sha = data.get("predecessor_sha")
        expected_tree = data.get("predecessor_tree")
        expected_marker = data.get("release_marker")
        if current_sha == candidate_sha:
            _fail("CUTOVER_PREDECESSOR_SNAPSHOT_AFTER_CANDIDATE_FORBIDDEN")
    elif phase == "candidate":
        expected_sha = data.get("candidate_sha")
        expected_tree = data.get("candidate_tree")
        expected_marker = candidate_sha
    else:
        _fail("CUTOVER_PHASE_INVALID")
    if (
        current_sha != expected_sha
        or current_tree != expected_tree
        or release_marker != expected_marker
    ):
        _fail("CUTOVER_GENERATION_PHASE_IDENTITY_MISMATCH")


def verify_generation(
    generation_id: str,
    *,
    app_dir: Path,
    release_file: Path,
    candidate_sha: str,
    phase: str,
    root: Path = DEFAULT_GENERATIONS_ROOT,
    expected_uid: int = 0,
) -> dict:
    _, data = load_generation(
        generation_id, root=root, expected_uid=expected_uid
    )
    current_sha = _git(app_dir, "rev-parse", "HEAD").lower()
    current_tree = _git(app_dir, "rev-parse", "HEAD^{tree}").lower()
    release_marker = release_file.read_text(encoding="utf-8").strip().lower()
    validate_generation_payload(
        data,
        generation_id=generation_id,
        app_dir=app_dir,
        candidate_sha=candidate_sha,
        phase=phase,
        current_sha=current_sha,
        current_tree=current_tree,
        release_marker=release_marker,
    )
    return data


def _tree_rows(root: Path, *, include_owner: bool) -> list[dict]:
    root = root.resolve()
    if not root.is_dir() or root.is_symlink():
        _fail("VENV_ROOT_INVALID")
    rows: list[dict] = []
    pending = [root]
    while pending:
        path = pending.pop()
        st = path.lstat()
        rel = "." if path == root else path.relative_to(root).as_posix()
        base = {"path": rel, "mode": stat.S_IMODE(st.st_mode)}
        if include_owner:
            base.update({"uid": st.st_uid, "gid": st.st_gid})
        if stat.S_ISDIR(st.st_mode):
            base["kind"] = "dir"
            rows.append(base)
            pending.extend(sorted(path.iterdir(), reverse=True))
        elif stat.S_ISREG(st.st_mode):
            base.update(
                {
                    "kind": "file",
                    "size": st.st_size,
                    "sha256": _sha256_file(path),
                }
            )
            rows.append(base)
        elif stat.S_ISLNK(st.st_mode):
            base.update(
                {"kind": "symlink", "target": os.readlink(path)}
            )
            rows.append(base)
        else:
            _fail(f"VENV_UNSUPPORTED_FILE:{rel}")
    rows.sort(key=lambda row: (row["path"], row["kind"]))
    return rows


def _tree_digest(rows: list[dict]) -> str:
    return _sha256_bytes(
        json.dumps(rows, sort_keys=True, separators=(",", ":")).encode()
    )


def _parse_exec_interpreter(unit_text: str) -> str:
    rows = [
        line.split("=", 1)[1].strip()
        for line in unit_text.splitlines()
        if line.strip().startswith("ExecStart=")
    ]
    if len(rows) != 1:
        _fail("CUTOVER_UNIT_EXECSTART_INVALID")
    tokens = shlex.split(rows[0])
    if not tokens:
        _fail("CUTOVER_UNIT_EXECSTART_INVALID")
    if tokens[0] == "/usr/bin/env":
        try:
            boundary = next(
                i
                for i, token in enumerate(tokens[2:], start=2)
                if token.startswith("/") and token.endswith("/bin/python")
            )
        except StopIteration:
            _fail("CUTOVER_UNIT_INTERPRETER_MISSING")
        return tokens[boundary]
    if tokens[0].startswith("/") and tokens[0].endswith("/bin/python"):
        return tokens[0]
    _fail("CUTOVER_UNIT_INTERPRETER_INVALID")
    raise AssertionError


def _sqlite_backup(source: Path, target: Path) -> None:
    tmp = target.with_name("." + target.name + ".tmp")
    tmp.unlink(missing_ok=True)
    src = sqlite3.connect(f"file:{source}?mode=ro", uri=True, timeout=5.0)
    dst = sqlite3.connect(tmp, timeout=5.0)
    try:
        src.execute("PRAGMA busy_timeout=5000")
        dst.execute("PRAGMA busy_timeout=5000")
        src.backup(dst, pages=256, sleep=0.01)
    finally:
        dst.close()
        src.close()
    check = sqlite3.connect(f"file:{tmp}?mode=ro", uri=True, timeout=5.0)
    try:
        if check.execute("PRAGMA quick_check").fetchall() != [("ok",)]:
            _fail("CUTOVER_DB_BACKUP_QUICK_CHECK_FAILED")
    finally:
        check.close()
    os.replace(tmp, target)


def _systemctl_state(unit: str, verb: str) -> bool:
    return _run(["systemctl", verb, "--quiet", unit], check=False).returncode == 0


def snapshot_generation(candidate_sha: str) -> dict:
    if os.geteuid() != 0:
        _fail("HOST_SNAPSHOT_ROOT_REQUIRED")
    verify_authority()
    paths = _load_host_paths()
    app_dir = Path(paths["app_dir"])
    config_dir = Path(paths["config_dir"])
    db_path = Path(paths["db_path"])
    releases_root = Path(paths["releases_root"])
    generations_root = Path(paths["generations_root"])
    unit = str(paths["unit"])
    release_file = config_dir / "weather-paper-release.sha"
    candidate_sha = _hex(
        candidate_sha, 40, "CUTOVER_CANDIDATE_SHA_INVALID"
    )
    verify_checkout(app_dir, release_file)
    candidate = verify_object(app_dir, candidate_sha)
    predecessor_sha = _git(app_dir, "rev-parse", "HEAD").lower()
    predecessor_tree = _git(app_dir, "rev-parse", "HEAD^{tree}").lower()
    if predecessor_sha == candidate_sha:
        _fail("CUTOVER_PREDECESSOR_SNAPSHOT_AFTER_CANDIDATE_FORBIDDEN")
    generations_root.mkdir(parents=True, exist_ok=True)
    os.chown(generations_root, 0, 0)
    os.chmod(generations_root, 0o755)
    for child in generations_root.glob("gen-*/generation.json"):
        try:
            old = _load_json(child)
        except Exception:
            continue
        if (
            old.get("predecessor_sha") == predecessor_sha
            and old.get("candidate_sha") == candidate_sha
        ):
            _fail("CUTOVER_GENERATION_ALREADY_EXISTS")
    unit_file = Path("/etc/systemd/system") / unit
    _safe_root_file(unit_file)
    unit_text = unit_file.read_text(encoding="utf-8")
    interpreter = Path(_parse_exec_interpreter(unit_text))
    predecessor_venv = interpreter.parent.parent
    if not predecessor_venv.is_dir():
        _fail("CUTOVER_PREDECESSOR_VENV_MISSING")
    venv_rows_before = _tree_rows(predecessor_venv, include_owner=True)
    venv_digest = _tree_digest(venv_rows_before)
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    generation_id = (
        f"gen-{stamp}-{predecessor_sha[:12]}-{candidate_sha[:12]}-"
        f"{uuid.uuid4().hex[:12]}"
    )
    final_dir = _generation_dir(generation_id, generations_root)
    temp_dir = Path(
        tempfile.mkdtemp(prefix=".generation-", dir=generations_root)
    )
    try:
        os.chmod(temp_dir, 0o700)
        unit_copy = temp_dir / unit
        shutil.copyfile(unit_file, unit_copy)
        os.chmod(unit_copy, 0o400)
        db_present = db_path.is_file()
        db_digest = "ABSENT"
        if db_present:
            db_copy = temp_dir / "predecessor.sqlite3"
            _sqlite_backup(db_path, db_copy)
            os.chmod(db_copy, 0o400)
            db_digest = _sha256_file(db_copy)
        venv_manifest = temp_dir / "predecessor-venv.json"
        venv_payload = {
            "version": "weather-paper-predecessor-venv-v1-exact-tree",
            "venv_path": str(predecessor_venv),
            "tree_sha256": venv_digest,
            "entries": venv_rows_before,
        }
        venv_manifest.write_text(
            json.dumps(venv_payload, sort_keys=True, indent=2) + "\n",
            encoding="utf-8",
        )
        os.chmod(venv_manifest, 0o400)
        venv_manifest_digest = _sha256_file(venv_manifest)
        venv_rows_after = _tree_rows(predecessor_venv, include_owner=True)
        if venv_rows_after != venv_rows_before:
            _fail("CUTOVER_PREDECESSOR_VENV_CHANGED_DURING_SNAPSHOT")
        marker = release_file.read_text(encoding="utf-8").strip().lower()
        manifest = {
            "version": GENERATION_VERSION,
            "generation_id": generation_id,
            "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "predecessor_sha": predecessor_sha,
            "predecessor_tree": predecessor_tree,
            "candidate_sha": candidate_sha,
            "candidate_tree": candidate["tree"],
            "predecessor_unit_digest": _sha256_file(unit_copy),
            "predecessor_db_digest": db_digest,
            "predecessor_db_present": db_present,
            "predecessor_venv_path": str(predecessor_venv),
            "predecessor_venv_digest": venv_digest,
            "venv_manifest_digest": venv_manifest_digest,
            "active": _systemctl_state(unit, "is-active"),
            "enabled": _systemctl_state(unit, "is-enabled"),
            "release_marker": marker,
            "release_marker_digest": _sha256_bytes((marker + "\n").encode()),
            "deploy_user": paths["deploy_user"],
            "deploy_uid": int(paths["deploy_uid"]),
            "deploy_gid": int(paths["deploy_gid"]),
            "app_dir": str(app_dir),
            "config_dir": str(config_dir),
            "db_path": str(db_path),
            "unit": unit,
            "releases_root": str(releases_root),
        }
        if marker != predecessor_sha:
            _fail("CUTOVER_RELEASE_MARKER_PREDECESSOR_MISMATCH")
        if (
            _git(app_dir, "rev-parse", "HEAD").lower() != predecessor_sha
            or _git(app_dir, "rev-parse", "HEAD^{tree}").lower()
            != predecessor_tree
        ):
            _fail("CUTOVER_PREDECESSOR_CHANGED_DURING_SNAPSHOT")
        if (
            release_file.read_text(encoding="utf-8").strip().lower()
            != predecessor_sha
        ):
            _fail("CUTOVER_RELEASE_MARKER_CHANGED_DURING_SNAPSHOT")
        generation_json = temp_dir / "generation.json"
        generation_json.write_text(
            json.dumps(manifest, sort_keys=True, indent=2) + "\n",
            encoding="utf-8",
        )
        os.chmod(generation_json, 0o400)
        for item in temp_dir.iterdir():
            os.chown(item, 0, int(paths["deploy_gid"]))
        os.chown(temp_dir, 0, int(paths["deploy_gid"]))
        os.chmod(temp_dir, 0o750)
        os.rename(temp_dir, final_dir)
    finally:
        if temp_dir.exists():
            shutil.rmtree(temp_dir)
    return manifest


def _validate_generation_artifacts(
    directory: Path, data: dict, *, expected_uid: int = 0
) -> None:
    unit = str(data.get("unit") or "")
    unit_file = directory / unit
    venv_manifest = directory / "predecessor-venv.json"
    _safe_root_file(unit_file, expected_uid=expected_uid)
    _safe_root_file(venv_manifest, expected_uid=expected_uid)
    if _sha256_file(unit_file) != data.get("predecessor_unit_digest"):
        _fail("CUTOVER_UNIT_DIGEST_MISMATCH")
    if _sha256_file(venv_manifest) != data.get("venv_manifest_digest"):
        _fail("CUTOVER_VENV_MANIFEST_DIGEST_MISMATCH")
    venv_data = _load_json(venv_manifest)
    venv_path = Path(str(data.get("predecessor_venv_path") or ""))
    if str(venv_data.get("venv_path")) != str(venv_path):
        _fail("CUTOVER_VENV_PATH_MISMATCH")
    observed = _tree_rows(venv_path, include_owner=True)
    if (
        observed != venv_data.get("entries")
        or _tree_digest(observed) != data.get("predecessor_venv_digest")
    ):
        _fail("CUTOVER_PREDECESSOR_VENV_CHANGED")
    db_present = bool(data.get("predecessor_db_present"))
    db_copy = directory / "predecessor.sqlite3"
    if db_present:
        _safe_root_file(db_copy, expected_uid=expected_uid)
        if _sha256_file(db_copy) != data.get("predecessor_db_digest"):
            _fail("CUTOVER_DB_DIGEST_MISMATCH")
    elif data.get("predecessor_db_digest") != "ABSENT" or db_copy.exists():
        _fail("CUTOVER_DB_ABSENCE_MISMATCH")


def _restore_db(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name("." + target.name + ".restore")
    tmp.unlink(missing_ok=True)
    src = sqlite3.connect(f"file:{source}?mode=ro", uri=True, timeout=5.0)
    dst = sqlite3.connect(tmp, timeout=5.0)
    try:
        src.backup(dst, pages=256, sleep=0.01)
    finally:
        dst.close()
        src.close()
    Path(str(target) + "-wal").unlink(missing_ok=True)
    Path(str(target) + "-shm").unlink(missing_ok=True)
    os.replace(tmp, target)
    os.chmod(target, 0o600)


def recover_generation(generation_id: str) -> dict:
    if os.geteuid() != 0:
        _fail("HOST_RECOVERY_ROOT_REQUIRED")
    verify_authority()
    paths = _load_host_paths()
    root = Path(paths["generations_root"])
    directory, data = load_generation(generation_id, root=root)
    _validate_generation_artifacts(directory, data)
    app_dir = Path(data["app_dir"])
    config_dir = Path(data["config_dir"])
    db_path = Path(data["db_path"])
    unit = data["unit"]
    if (
        str(app_dir) != str(paths["app_dir"])
        or str(config_dir) != str(paths["config_dir"])
        or str(db_path) != str(paths["db_path"])
    ):
        _fail("CUTOVER_GENERATION_HOST_PATHS_MISMATCH")
    _run(["systemctl", "stop", unit], check=False)
    _run(["systemctl", "disable", unit], check=False)
    predecessor_sha = data["predecessor_sha"]
    predecessor_tree = data["predecessor_tree"]
    if _git(app_dir, "rev-parse", f"{predecessor_sha}^{{tree}}").lower() != predecessor_tree:
        _fail("CUTOVER_PREDECESSOR_GIT_OBJECT_MISMATCH")
    _run(["git", "-C", str(app_dir), "checkout", "-f", "--detach", predecessor_sha])
    _run(["git", "-C", str(app_dir), "reset", "--hard", predecessor_sha])
    predecessor_venv = Path(data["predecessor_venv_path"]).resolve()
    app_venv = (app_dir / ".venv").resolve()
    clean_args = ["git", "-C", str(app_dir), "clean", "-fdx"]
    if predecessor_venv == app_venv:
        clean_args.extend(["-e", ".venv/"])
    _run(clean_args)
    if (
        _git(app_dir, "rev-parse", "HEAD").lower() != predecessor_sha
        or _git(app_dir, "rev-parse", "HEAD^{tree}").lower() != predecessor_tree
    ):
        _fail("CUTOVER_PREDECESSOR_CHECKOUT_RESTORE_FAILED")
    release_file = config_dir / "weather-paper-release.sha"
    release_file.parent.mkdir(parents=True, exist_ok=True)
    release_file.write_text(data["release_marker"] + "\n", encoding="utf-8")
    os.chown(
        release_file, int(data["deploy_uid"]), int(data["deploy_gid"])
    )
    os.chmod(release_file, 0o600)
    if data["predecessor_db_present"]:
        _restore_db(directory / "predecessor.sqlite3", db_path)
    else:
        db_path.unlink(missing_ok=True)
        Path(str(db_path) + "-wal").unlink(missing_ok=True)
        Path(str(db_path) + "-shm").unlink(missing_ok=True)
    unit_target = Path("/etc/systemd/system") / unit
    shutil.copyfile(directory / unit, unit_target)
    os.chown(unit_target, 0, 0)
    os.chmod(unit_target, 0o644)
    _run(["systemctl", "daemon-reload"])
    if data["enabled"]:
        _run(["systemctl", "enable", unit])
    else:
        _run(["systemctl", "disable", unit], check=False)
    if data["active"]:
        _run(["systemctl", "start", unit])
    _validate_generation_artifacts(directory, data)
    verify_checkout(app_dir, release_file)
    candidate_root = Path(data["releases_root"]) / data["candidate_sha"]
    if candidate_root.exists():
        try:
            predecessor_venv.relative_to(candidate_root.resolve())
            predecessor_inside_candidate = True
        except ValueError:
            predecessor_inside_candidate = False
        if not predecessor_inside_candidate:
            shutil.rmtree(candidate_root)
    return data


def _lock_inventory_from_bytes(data: bytes) -> dict[str, str]:
    out: dict[str, str] = {}
    for raw in data.decode("utf-8").splitlines():
        row = raw.split("#", 1)[0].strip()
        if not row:
            continue
        spec = row.split()[0]
        if "==" not in spec or "--hash=sha256:" not in row:
            _fail("RUNTIME_LOCK_ROW_INVALID")
        name, version = spec.split("==", 1)
        name = name.split("[", 1)[0]
        out[_canonical_name(name)] = version
    return out


def verify_runtime_evidence(
    *,
    repo: Path,
    sha: str,
    generation_id: str,
    evidence_path: Path,
    expected_uid: int = 0,
) -> dict:
    _safe_root_file(evidence_path, expected_uid=expected_uid)
    evidence = _load_json(evidence_path)
    if evidence.get("version") != RUNTIME_EVIDENCE_VERSION:
        _fail("RUNTIME_EVIDENCE_VERSION_INVALID")
    sha = _hex(sha, 40, "RUNTIME_EVIDENCE_SHA_INVALID")
    if (
        evidence.get("release_sha") != sha
        or evidence.get("generation_id") != generation_id
    ):
        _fail("RUNTIME_EVIDENCE_RELEASE_OR_GENERATION_MISMATCH")
    row = verify_object(repo, sha, expected_uid=expected_uid)
    if (
        evidence.get("release_tree") != row["tree"]
        or evidence.get("requirements_lock_sha256")
        != row["requirements_lock_sha256"]
    ):
        _fail("RUNTIME_EVIDENCE_GIT_BINDING_MISMATCH")
    manifest_path = Path(str(evidence.get("venv_manifest_path") or ""))
    _safe_root_file(manifest_path, expected_uid=expected_uid)
    if _sha256_file(manifest_path) != evidence.get("venv_manifest_sha256"):
        _fail("RUNTIME_VENV_MANIFEST_DIGEST_MISMATCH")
    manifest = _load_json(manifest_path)
    if manifest.get("version") != RUNTIME_VENV_VERSION:
        _fail("RUNTIME_VENV_MANIFEST_VERSION_INVALID")
    venv = Path(str(evidence.get("venv_path") or ""))
    interpreter = Path(str(evidence.get("interpreter_path") or ""))
    if (
        interpreter != venv / "bin" / "python"
        or manifest.get("venv_path") != str(venv)
    ):
        _fail("RUNTIME_VENV_IDENTITY_MISMATCH")
    if not venv.is_dir() or venv.is_symlink():
        _fail("RUNTIME_VENV_ROOT_INVALID")
    rows = _tree_rows(venv, include_owner=False)
    if (
        _tree_digest(rows) != evidence.get("venv_tree_sha256")
        or manifest.get("tree_sha256") != evidence.get("venv_tree_sha256")
    ):
        _fail("RUNTIME_VENV_TREE_DIGEST_MISMATCH")
    expected_inventory = _lock_inventory_from_bytes(
        _git_bytes(repo, sha, "requirements-runtime-hashed.txt")
    )
    inventory_rows = manifest.get("distribution_inventory")
    if not isinstance(inventory_rows, list):
        _fail("RUNTIME_VENV_INVENTORY_INVALID")
    actual_inventory = {
        _canonical_name(str(item.get("name") or "")): str(
            item.get("version") or ""
        )
        for item in inventory_rows
        if isinstance(item, dict)
    }
    if actual_inventory != expected_inventory:
        _fail("RUNTIME_VENV_INVENTORY_NOT_EXACT_LOCK")
    return evidence


def _print(payload: dict) -> None:
    print(json.dumps(payload, sort_keys=True, indent=2))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("verify-authority")
    obj = sub.add_parser("verify-object")
    obj.add_argument("--app-dir", type=Path, required=True)
    obj.add_argument("--sha", required=True)
    chk = sub.add_parser("verify-checkout")
    chk.add_argument("--app-dir", type=Path, required=True)
    chk.add_argument("--release-file", type=Path, required=True)
    gen = sub.add_parser("verify-generation")
    gen.add_argument("--generation-id", required=True)
    gen.add_argument("--app-dir", type=Path, required=True)
    gen.add_argument("--release-file", type=Path, required=True)
    gen.add_argument("--candidate-sha", required=True)
    gen.add_argument(
        "--phase", choices=("predecessor", "candidate"), required=True
    )
    snap = sub.add_parser("snapshot")
    snap.add_argument("--candidate-sha", required=True)
    rec = sub.add_parser("recover")
    rec.add_argument("--generation-id", required=True)
    rt = sub.add_parser("verify-runtime")
    rt.add_argument("--app-dir", type=Path, required=True)
    rt.add_argument("--sha", required=True)
    rt.add_argument("--generation-id", required=True)
    rt.add_argument("--evidence", type=Path, required=True)
    args = parser.parse_args()
    try:
        if args.command == "verify-authority":
            result = verify_authority()
        elif args.command == "verify-object":
            result = verify_object(args.app_dir, args.sha)
        elif args.command == "verify-checkout":
            result = verify_checkout(args.app_dir, args.release_file)
        elif args.command == "verify-generation":
            result = verify_generation(
                args.generation_id,
                app_dir=args.app_dir,
                release_file=args.release_file,
                candidate_sha=args.candidate_sha,
                phase=args.phase,
            )
        elif args.command == "snapshot":
            result = snapshot_generation(args.candidate_sha)
        elif args.command == "recover":
            result = recover_generation(args.generation_id)
        else:
            result = verify_runtime_evidence(
                repo=args.app_dir,
                sha=args.sha,
                generation_id=args.generation_id,
                evidence_path=args.evidence,
            )
    except (
        HostAuthorityError,
        OSError,
        UnicodeError,
        json.JSONDecodeError,
        sqlite3.Error,
    ) as exc:
        print(f"FAIL_HOST_AUTHORITY:{exc}", file=sys.stderr)
        return 2
    _print(
        {
            "acceptance": "PASS_HOST_AUTHORITY_"
            + args.command.upper().replace("-", "_"),
            "result": result,
        }
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
