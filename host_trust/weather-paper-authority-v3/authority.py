#!/usr/bin/env python3
from __future__ import annotations

"""NON-AUTHORITATIVE reference for an independently provisioned host authority.

This repository never installs this implementation or supplies its trust anchor.
An operator must independently review/provision an authority implementing this
protocol. A matching digest detects changes; it does not prove provenance.
"""

import argparse
import contextlib
import fcntl
import functools
import hashlib
import importlib.metadata
import json
import os
import pwd
import grp
import re
import shutil
import sqlite3
import stat
import subprocess
import sys
import tarfile
import tempfile
import threading
import time
from pathlib import Path, PurePosixPath

AUTHORITY_VERSION = "weather-paper-host-authority-v3-immutable-runtime"
ANCHOR = Path("/etc/polymarket-weather-paper/authority-anchor-v3.json")
ACTIVE = Path("/etc/polymarket-weather-paper/active-cutover-v3.json")
DEFAULT_GENERATIONS = Path("/var/lib/polymarket-weather-paper-rollback/generations-v3")
MUTATION_LOCK = Path("/var/lib/polymarket-weather-paper-runtime/authority.lock")
INSTALLED_AUTHORITY = Path("/usr/local/libexec/polymarket-weather-paper-v3/authority.py")
FINAL_MODULE = "polymarket_scanner.production"
MAX_GIT_PACK_BYTES = 512 * 1024 * 1024
FORBIDDEN_PROCESS_ENV = {
    "HTTP_PROXY","HTTPS_PROXY","ALL_PROXY","NO_PROXY","http_proxy","https_proxy",
    "all_proxy","no_proxy","SSL_CERT_FILE","SSL_CERT_DIR","PYTHONPATH","PYTHONHOME",
    "PYTHONUSERBASE","PYTHONSTARTUP","PYTHONINSPECT","PYTHONWARNINGS",
    "PYTHONBREAKPOINT","LD_PRELOAD","LD_LIBRARY_PATH","BASH_ENV","ENV","CDPATH",
    "GIT_DIR","GIT_WORK_TREE","GIT_CONFIG_GLOBAL","GIT_CONFIG_SYSTEM",
}
ALLOWED_PROCESS_ENV = {
    "PATH","HOME","LANG","PYTHONUNBUFFERED","PYTHONNOUSERSITE",
    "PYTHONDONTWRITEBYTECODE","ALPHA_DISABLE_DOTENV","ALPHA_RELEASE_SHA",
    "ALPHA_CUTOVER_GENERATION","ALPHA_RUNTIME_SOURCE","TELEGRAM_BOT_TOKEN",
    "TELEGRAM_CHAT_ID",
}
_MUTATION_LOCAL = threading.local()


class AuthorityError(RuntimeError):
    pass


def fail(code: str) -> None:
    raise AuthorityError(code)


def _hex(value: object, length: int, code: str) -> str:
    text = str(value or "").strip().lower()
    if len(text) != length or any(c not in "0123456789abcdef" for c in text):
        fail(code)
    return text


def _canonical(value: object) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True) + "\n").encode()


def _sha_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _read_json(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise AuthorityError("AUTHORITY_JSON_INVALID") from exc
    if not isinstance(value, dict):
        fail("AUTHORITY_JSON_INVALID")
    return value


def _regular_nosymlink(path: Path) -> os.stat_result:
    st = path.lstat()
    if path.is_symlink() or not stat.S_ISREG(st.st_mode):
        fail("AUTHORITY_FILE_NOT_REGULAR")
    return st


def _root_custody(path: Path) -> os.stat_result:
    st = _regular_nosymlink(path)
    if st.st_uid != 0 or st.st_mode & 0o022:
        fail("AUTHORITY_FILE_CUSTODY_INVALID")
    return st


def _root_path_chain(directory: Path) -> None:
    """Root files below a deploy-writable parent are not immutable custody."""
    current = directory
    while True:
        _secure_root_dir(current)
        if current == current.parent:
            return
        current = current.parent


def _abs(value: object, code: str) -> Path:
    path = Path(str(value or "")).expanduser()
    if not path.is_absolute() or ".." in path.parts or not re.fullmatch(r"[/A-Za-z0-9_.-]+", str(path)):
        fail(code)
    return path


def _run(args: list[str], *, check: bool = True, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    clean = {"PATH": "/usr/bin:/bin", "HOME": "/nonexistent", "LANG": "C.UTF-8",
             "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": "/dev/null",
             "GIT_TERMINAL_PROMPT": "0", "PIP_CONFIG_FILE": "/dev/null",
             "PYTHONDONTWRITEBYTECODE": "1"}
    if env:
        clean.update(env)
    result = subprocess.run(
        args, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        check=False, env=clean, timeout=300
    )
    if check and result.returncode != 0:
        fail("AUTHORITY_COMMAND_FAILED:" + Path(args[0]).name)
    return result


def _git(repo: Path, *args: str) -> str:
    """Only for authority-created root-owned repositories, never app_dir/.git."""
    st = repo.lstat()
    if repo.is_symlink() or not stat.S_ISDIR(st.st_mode) or st.st_uid != os.geteuid() or st.st_mode & 0o022:
        fail("AUTHORITY_GIT_REPOSITORY_CUSTODY_INVALID")
    return _run(_git_command(repo, *args)).stdout.strip()


def _git_command(repo: Path, *args: str) -> list[str]:
    return ["/usr/bin/git", "--no-replace-objects", "-c", "core.hooksPath=/dev/null",
            "-c", "core.fsmonitor=false", "-c", "submodule.recurse=false",
            "-C", str(repo), *args]


def _deploy_command(policy: dict, args: list[str], *, require_root: bool = True) -> list[str]:
    if require_root:
        if policy["deploy_uid"] == 0 or policy["deploy_gid"] == 0:
            fail("AUTHORITY_DEPLOY_IDENTITY_MUST_BE_UNPRIVILEGED")
        return ["/usr/bin/setpriv", f"--reuid={policy['deploy_uid']}",
                f"--regid={policy['deploy_gid']}", "--clear-groups", "--no-new-privs",
                "--inh-caps=-all", "--ambient-caps=-all", "--bounding-set=-all", *args]
    return args


def _app_git(policy: dict, *args: str, require_root: bool = True) -> str:
    return _run(_deploy_command(policy, _git_command(Path(policy["app_dir"]), *args),
                                require_root=require_root)).stdout.strip()


def _import_app_objects(policy: dict, commits: list[str], destination: Path, *, require_root: bool) -> None:
    """Copy Git object bytes across the privilege boundary, never .git configuration.

    The exporter runs as the deploy principal. Its output is copied into a second,
    never-shared file before root parses it, and all objects are checked by Git.
    Even a hostile exporter cannot choose the independently approved SHA/tree.
    """
    for commit in commits:
        _hex(commit, 40, "CUTOVER_COMMIT_INVALID")
    destination.mkdir(mode=0o700)
    _run(["/usr/bin/git", "init", "--bare", "--template=", str(destination)])
    export = destination / "untrusted-export.pack"
    sealed = destination / "sealed-export.pack"
    command = _deploy_command(policy, ["/usr/bin/prlimit", f"--fsize={MAX_GIT_PACK_BYTES}",
                              "--", *_git_command(Path(policy["app_dir"]),
                                                    "pack-objects", "--stdout", "--revs")],
                              require_root=require_root)
    clean = {"PATH": "/usr/bin:/bin", "HOME": "/nonexistent", "LANG": "C.UTF-8",
             "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_TERMINAL_PROMPT": "0"}
    try:
        with export.open("xb") as out:
            result = subprocess.run(command, input=("\n".join(commits) + "\n").encode(),
                                    stdout=out, stderr=subprocess.DEVNULL, env=clean,
                                    timeout=120, check=False)
        if result.returncode or export.stat().st_size > MAX_GIT_PACK_BYTES:
            fail("CUTOVER_OBJECT_EXPORT_FAILED")
        with export.open("rb") as src, sealed.open("xb") as dst:
            shutil.copyfileobj(src, dst, 1024 * 1024)
        export.unlink()
        with sealed.open("rb") as src:
            result = subprocess.run(_git_command(destination, "index-pack", "--strict", "--stdin"),
                                    stdin=src, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                    env=clean, timeout=120, check=False)
        if result.returncode:
            fail("CUTOVER_OBJECT_IMPORT_FAILED")
        for commit in commits:
            if _git(destination, "rev-parse", commit + "^{commit}") != commit:
                fail("CUTOVER_OBJECT_IDENTITY_MISMATCH")
    finally:
        export.unlink(missing_ok=True)
        sealed.unlink(missing_ok=True)


def _serialized_mutation(function):
    @functools.wraps(function)
    def guarded(*args, **kwargs):
        if getattr(_MUTATION_LOCAL, "held", False):
            return function(*args, **kwargs)
        require_root = kwargs.get("require_root", True)
        if require_root:
            if os.geteuid() != 0:
                fail("CUTOVER_ROOT_REQUIRED")
            _secure_root_dir(MUTATION_LOCK.parent)
        fd = os.open(MUTATION_LOCK, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
        try:
            st = os.fstat(fd)
            if not stat.S_ISREG(st.st_mode) or (require_root and (st.st_uid != 0 or st.st_mode & 0o077)):
                fail("AUTHORITY_LOCK_CUSTODY_INVALID")
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                fail("CUTOVER_MUTATION_BUSY")
            _MUTATION_LOCAL.held = True
            try:
                return function(*args, **kwargs)
            finally:
                _MUTATION_LOCAL.held = False
        finally:
            os.close(fd)
    return guarded


def _safe_write(path: Path, data: bytes, *, mode: int = 0o440, root_custody: bool = True) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmpname = tempfile.mkstemp(prefix="." + path.name + ".", dir=path.parent)
    tmp = Path(tmpname)
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        os.chmod(tmp, mode)
        if root_custody:
            os.chown(tmp, 0, 0)
        os.replace(tmp, path)
        _sync_directory(path.parent)
    finally:
        tmp.unlink(missing_ok=True)


def _sync_directory(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _restore_database(source: Path, target: Path, identity: dict, *, require_root: bool) -> None:
    fd, temporary = tempfile.mkstemp(prefix=".restore-", dir=target.parent)
    tmp = Path(temporary)
    try:
        with os.fdopen(fd, "wb") as dst, source.open("rb") as src:
            shutil.copyfileobj(src, dst, 1024 * 1024)
            dst.flush()
            os.fchmod(dst.fileno(), 0o640)
            if require_root:
                os.fchown(dst.fileno(), identity["uid"], identity["gid"])
            os.fsync(dst.fileno())
        os.replace(tmp, target)
        _sync_directory(target.parent)
    finally:
        tmp.unlink(missing_ok=True)


def authority_digest() -> str:
    return sha256_file(Path(__file__).resolve())


def _validate_policy(raw: dict) -> dict:
    required_paths = (
        "app_dir", "release_file", "unit_file", "db_path", "legacy_predecessor_venv",
        "runtime_root", "runtime_python", "approval_file", "signal_status_path",
    )
    out = dict(raw)
    for key in required_paths:
        out[key] = str(_abs(raw.get(key), "AUTHORITY_POLICY_PATH_INVALID:" + key))
    unit = str(raw.get("unit_name") or "")
    deploy_user = str(raw.get("deploy_user") or "")
    remote = str(raw.get("repo_remote_url") or "")
    if not re.fullmatch(r"[A-Za-z0-9_.@-]+", unit) or not re.fullmatch(r"[A-Za-z0-9_.@-]+", deploy_user):
        fail("AUTHORITY_POLICY_IDENTITY_INVALID")
    if not remote.startswith("https://github.com/") or any(x in remote for x in ("\n", "\r", " ")):
        fail("AUTHORITY_POLICY_REMOTE_INVALID")
    for key in ("deploy_uid", "deploy_gid"):
        try:
            value = int(raw.get(key))
        except Exception:
            fail("AUTHORITY_POLICY_UID_INVALID")
        if value <= 0:
            fail("AUTHORITY_POLICY_UID_INVALID")
        out[key] = value
    out["unit_name"] = unit
    out["deploy_user"] = deploy_user
    out["repo_remote_url"] = remote
    components = raw.get("components")
    valid_sets = ({"signals"}, {"signals", "execution"}, {"scanner", "controller"}, {"scanner", "controller", "execution"})
    if not isinstance(components, dict) or set(components) not in valid_sets:
        fail("AUTHORITY_COMPONENT_POLICY_REQUIRED")
    primary = "controller" if "controller" in components else "signals"
    if len({row.get("uid") for row in components.values() if isinstance(row, dict)}) != len(components):
        fail("AUTHORITY_EXECUTION_IDENTITY_NOT_ISOLATED")
    if len(components)>1 and (not raw.get("shared_read_group") or len({row.get("gid") for row in components.values() if isinstance(row,dict)}) != 1):
        fail("AUTHORITY_READONLY_REPORT_GROUP_REQUIRED")
    for name, component in components.items():
        if not isinstance(component, dict) or component.get("module") != FINAL_MODULE:
            fail("AUTHORITY_COMPONENT_MODULE_INVALID")
        if component.get("command") != name:
            fail("AUTHORITY_COMPONENT_COMMAND_INVALID")
        for key in ("config_file", "unit_file"):
            _abs(component.get(key), "AUTHORITY_COMPONENT_PATH_INVALID:" + key)
        if not re.fullmatch(r"[A-Za-z0-9_.@-]+", str(component.get("unit_name", ""))):
            fail("AUTHORITY_COMPONENT_UNIT_INVALID")
        lock = component.get("lock_file")
        expected_lock = "requirements-execution-hashed.txt" if name == "execution" else "requirements-runtime-hashed.txt"
        if lock != expected_lock:
            fail("AUTHORITY_COMPONENT_LOCK_INVALID")
        if not re.fullmatch(r"[A-Za-z0-9_.-]+", str(component.get("user", ""))):
            fail("AUTHORITY_COMPONENT_USER_INVALID")
        for key in ("uid", "gid"):
            if not isinstance(component.get(key), int) or component[key] <= 0:
                fail("AUTHORITY_COMPONENT_UID_INVALID")
    if components[primary]["unit_name"] != unit or components[primary]["unit_file"] != out["unit_file"]:
        fail("AUTHORITY_PRIMARY_UNIT_MISMATCH")
    if "execution" in components:
        if components["execution"]["uid"] == components[primary]["uid"]:
            fail("AUTHORITY_EXECUTION_IDENTITY_NOT_ISOLATED")
        if not raw.get("shared_read_group") or components["execution"]["gid"] != components[primary]["gid"]:
            fail("AUTHORITY_READONLY_REPORT_GROUP_REQUIRED")
        out["execution_db_path"] = str(_abs(raw.get("execution_db_path"), "AUTHORITY_EXECUTION_DB_REQUIRED"))
        if out["execution_db_path"] == out["db_path"]:
            fail("AUTHORITY_ACCOUNT_JOURNAL_MUST_BE_SEPARATE")
        if Path(out["execution_db_path"]).parent == Path(out["db_path"]).parent:
            fail("AUTHORITY_STATE_DIRECTORIES_MUST_BE_SEPARATE")
        out["execution_status_path"] = str(_abs(raw.get("execution_status_path"), "AUTHORITY_EXECUTION_STATUS_REQUIRED"))
        if Path(out["execution_status_path"]).parent != Path(out["execution_db_path"]).parent:
            fail("AUTHORITY_EXECUTION_STATUS_DIRECTORY_MISMATCH")
    if Path(out["signal_status_path"]).parent != Path(out["db_path"]).parent:
        fail("AUTHORITY_SIGNAL_STATUS_DIRECTORY_MISMATCH")
    paths = [out["db_path"], out["signal_status_path"]]
    if primary == "controller":
        for key in ("operator_db_path", "scanner_db_path", "scanner_status_path", "telegram_file"):
            out[key] = str(_abs(raw.get(key), "AUTHORITY_PANEL_PATH_REQUIRED:" + key))
        if Path(out["operator_db_path"]).parent != Path(out["db_path"]).parent:
            fail("AUTHORITY_CONTROL_DIRECTORY_MISMATCH")
        if Path(out["scanner_db_path"]).parent != Path(out["scanner_status_path"]).parent:
            fail("AUTHORITY_SCANNER_DIRECTORY_MISMATCH")
        if Path(out["scanner_db_path"]).parent in {Path(out["db_path"]).parent, Path(out.get("execution_db_path", "/")).parent}:
            fail("AUTHORITY_STATE_DIRECTORIES_MUST_BE_SEPARATE")
        paths += [out["operator_db_path"], out["scanner_db_path"], out["scanner_status_path"]]
        if "execution" in components:
            for key in ("execution_credentials_file", "execution_activation_file"):
                out[key] = str(_abs(raw.get(key), "AUTHORITY_PRIVATE_PATH_REQUIRED:" + key))
    if "execution" in components:
        paths += [out["execution_db_path"], out["execution_status_path"]]
    if len(set(paths)) != len(paths):
        fail("AUTHORITY_STATE_PATHS_MUST_BE_DISTINCT")
    if any(component["uid"] == out["deploy_uid"] for component in components.values()):
        fail("AUTHORITY_DEPLOY_IDENTITY_MUST_BE_SEPARATE")
    if len({row["unit_name"] for row in components.values()}) != len(components) or len({row["unit_file"] for row in components.values()}) != len(components):
        fail("AUTHORITY_COMPONENT_UNITS_MUST_BE_DISTINCT")
    locks = raw.get("writer_lock_paths")
    if not isinstance(locks, list) or not locks or len(set(locks)) != len(locks):
        fail("AUTHORITY_WRITER_LOCK_POLICY_REQUIRED")
    for lock in locks:
        _abs(lock, "AUTHORITY_WRITER_LOCK_PATH_INVALID")
    if raw.get("database_restore_policy", "unchanged_only") != "unchanged_only":
        fail("AUTHORITY_DATABASE_REWIND_FORBIDDEN")
    out["database_restore_policy"] = "unchanged_only"
    out["components"] = components
    if raw.get("shared_read_group") and not re.fullmatch(r"[A-Za-z0-9_.-]+", str(raw["shared_read_group"])):
        fail("AUTHORITY_SHARED_READ_GROUP_INVALID")
    _hex(raw.get("runtime_python_sha256"), 64, "AUTHORITY_INTERPRETER_DIGEST_REQUIRED")
    expected_locks = {str(Path(out["db_path"]).parent / "weather-paper-runtime.lock")}
    if "execution" in components:
        expected_locks.add(out["execution_db_path"] + ".writer.lock")
    if "scanner" in components:
        expected_locks.add(out["scanner_db_path"] + ".writer.lock")
    if set(locks) != expected_locks:
        fail("AUTHORITY_WRITER_LOCK_BINDING_INVALID")
    return out


def _component_configurations(policy: dict, *, require_root: bool) -> dict[str, str]:
    """Bind runtime writers to host-approved paths without importing application code.

    Risk values are operator configuration, not authority granted by a candidate.
    Root may change them without a new Git SHA; the runtime requires activation
    matching the resulting configuration identity. Paths remain independently bound.
    """
    data, digests = {}, {}
    for name, component in policy["components"].items():
        path = Path(component["config_file"])
        _root_custody(path) if require_root else _regular_nosymlink(path)
        raw = _read_json(path)
        if raw.get("signal_db") != policy["db_path"] or raw.get("status_path") != policy["signal_status_path"]:
            fail("RUNTIME_SIGNAL_CONFIG_PATH_MISMATCH")
        mode = raw.get("mode")
        if mode not in {"LIVE_SIGNALS", "LIVE_EXECUTION"} or (name == "execution" and mode != "LIVE_EXECUTION"):
            fail("RUNTIME_COMPONENT_CONFIG_MODE_INVALID")
        if mode == "LIVE_EXECUTION":
            if "execution" not in policy["components"]:
                fail("RUNTIME_EXECUTION_COMPONENT_NOT_PROVISIONED")
            if raw.get("execution_db") != policy["execution_db_path"]:
                fail("RUNTIME_EXECUTION_CONFIG_PATH_MISMATCH")
        if "execution" in policy["components"] and raw.get("execution_status_path") != policy["execution_status_path"]:
            fail("RUNTIME_EXECUTION_STATUS_PATH_MISMATCH")
        data[name] = raw
        digests[name] = _sha_bytes(_canonical(raw))
    primary = _primary_component(policy)
    if any(raw != data[primary] for raw in data.values()):
        fail("RUNTIME_COMPONENT_CONFIG_IDENTITY_MISMATCH")
    raw = data[primary]
    control = raw.get("operator_control")
    if primary == "controller":
        if not isinstance(control, dict) or control.get("version") != 1:
            fail("RUNTIME_OPERATOR_CONTROL_REQUIRED")
        for key, bound in (("db","operator_db_path"),("scanner_db","scanner_db_path"),("scanner_status","scanner_status_path")):
            if control.get(key) != policy[bound]:
                fail("RUNTIME_CONTROL_CONFIG_PATH_MISMATCH")
        if raw.get("telegram_file") != policy["telegram_file"]:
            fail("RUNTIME_TELEGRAM_CUSTODY_PATH_MISMATCH")
        if "execution" in data and (raw.get("credentials_file") != policy["execution_credentials_file"] or raw.get("activation_file") != policy["execution_activation_file"]):
            fail("RUNTIME_CREDENTIAL_CUSTODY_PATH_MISMATCH")
    elif control is not None:
        fail("RUNTIME_PANEL_COMPONENTS_NOT_PROVISIONED")
    return digests


def _primary_component(policy):
    return "controller" if "controller" in policy["components"] else "signals"


def _component_database(policy, name):
    return policy[{"signals":"db_path", "controller":"db_path", "scanner":"scanner_db_path", "execution":"execution_db_path"}[name]]


def _auxiliary_state_identities(policy):
    return {key:_logical_db_digest(Path(policy[key])) for key in ("operator_db_path", "scanner_db_path") if key in policy}


def _verify_state_paths(policy: dict) -> None:
    """Separate writable directories prevent a peer UID replacing another journal."""
    for name, component in policy["components"].items():
        db = Path(_component_database(policy, name))
        directory = db.parent
        _root_path_chain(directory.parent)
        st = directory.lstat()
        if directory.is_symlink() or not stat.S_ISDIR(st.st_mode) or st.st_uid != component["uid"] or st.st_gid != component["gid"] or stat.S_IMODE(st.st_mode) != 0o750:
            fail("RUNTIME_STATE_DIRECTORY_CUSTODY_INVALID")
        databases = [db] + ([Path(policy["operator_db_path"])] if name=="controller" else [])
        for path in [file for database in databases for file in (database,Path(str(database)+"-wal"),Path(str(database)+"-shm"))]:
            if not path.exists() and not path.is_symlink():
                continue
            st = _regular_nosymlink(path)
            expected_mode = 0o600 if name == "execution" else 0o640
            if st.st_uid != component["uid"] or st.st_gid != component["gid"] or stat.S_IMODE(st.st_mode) != expected_mode:
                fail("RUNTIME_DATABASE_CUSTODY_INVALID")


def verify_self(anchor: Path = ANCHOR, *, require_root: bool = True) -> dict:
    if anchor != ANCHOR and require_root:
        fail("AUTHORITY_ANCHOR_OVERRIDE_FORBIDDEN")
    if require_root:
        if Path(__file__).resolve() != INSTALLED_AUTHORITY:
            fail("REFERENCE_AUTHORITY_IS_NOT_HOST_AUTHORITY")
        _root_custody(INSTALLED_AUTHORITY)
        _root_custody(anchor)
    raw = _read_json(anchor)
    if raw.get("version") != AUTHORITY_VERSION:
        fail("AUTHORITY_VERSION_MISMATCH")
    expected = _hex(raw.get("authority_sha256"), 64, "AUTHORITY_DIGEST_INVALID")
    if authority_digest() != expected:
        fail("AUTHORITY_SELF_DIGEST_MISMATCH")
    policy = _validate_policy(raw.get("policy") if isinstance(raw.get("policy"), dict) else {})
    if require_root:
        for directory in (anchor.parent, INSTALLED_AUTHORITY.parent, Path(policy["approval_file"]).parent,
                          Path(policy["runtime_root"]), Path(policy["app_dir"]).parent,
                          Path(policy["release_file"]).parent, DEFAULT_GENERATIONS, MUTATION_LOCK.parent):
            _root_path_chain(directory)
        if Path(policy["runtime_root"]).stat().st_dev != Path(policy["app_dir"]).parent.stat().st_dev:
            fail("AUTHORITY_ATOMIC_RECOVERY_FILESYSTEM_REQUIRED")
        for component in policy["components"].values():
            for key in ("config_file", "unit_file"):
                _root_path_chain(Path(component[key]).parent)
            try:
                identity = pwd.getpwnam(component["user"])
            except KeyError:
                fail("AUTHORITY_COMPONENT_OS_USER_MISSING")
            if identity.pw_uid != component["uid"]:
                fail("AUTHORITY_COMPONENT_OS_USER_MISMATCH")
            if set(os.getgrouplist(component["user"], component["gid"])) != {component["gid"]}:
                fail("AUTHORITY_COMPONENT_EXTRA_GROUPS_FORBIDDEN")
        if policy.get("shared_read_group"):
            try:
                identity = grp.getgrnam(policy["shared_read_group"])
            except KeyError:
                fail("AUTHORITY_SHARED_READ_GROUP_MISSING")
            if any(component["gid"] != identity.gr_gid for component in policy["components"].values()):
                fail("AUTHORITY_SHARED_READ_GROUP_MISMATCH")
        _verify_state_paths(policy)
    policy_digest = _sha_bytes(_canonical(policy))
    if raw.get("policy_sha256") != policy_digest:
        fail("AUTHORITY_POLICY_DIGEST_MISMATCH")
    interpreter = Path(policy["runtime_python"]).resolve()
    if require_root:
        _root_custody(interpreter)
        _root_path_chain(interpreter.parent)
    if sha256_file(interpreter) != policy["runtime_python_sha256"]:
        fail("AUTHORITY_INTERPRETER_DIGEST_MISMATCH")
    return {"anchor": raw, "policy": policy, "policy_sha256": policy_digest}


def _release_approval(policy: dict, sha: str, *, require_root: bool) -> dict:
    path = Path(policy["approval_file"])
    if require_root:
        _root_custody(path)
    data = _read_json(path)
    if data.get("version") != "alpha-host-release-approvals-v1":
        fail("HOST_RELEASE_APPROVAL_VERSION_INVALID")
    matches = [row for row in data.get("approved", []) if isinstance(row, dict) and row.get("sha") == sha]
    if len(matches) != 1:
        fail("HOST_RELEASE_NOT_INDEPENDENTLY_APPROVED")
    approval = matches[0]
    _hex(approval.get("tree"), 40, "HOST_RELEASE_APPROVED_TREE_INVALID")
    components = approval.get("components")
    if not isinstance(components, dict) or set(components) != set(policy["components"]):
        fail("HOST_RELEASE_COMPONENT_APPROVAL_MISSING")
    for name, component in components.items():
        _hex(component.get("lock_sha256"), 64, "HOST_RELEASE_LOCK_APPROVAL_MISSING")
        pins = component.get("distributions")
        if not isinstance(pins, dict) or not pins or any(not isinstance(k, str) or not isinstance(v, str) or not v for k, v in pins.items()):
            fail("HOST_RELEASE_DISTRIBUTION_APPROVAL_MISSING")
    return approval


def _secure_root_dir(path: Path) -> None:
    st = path.lstat()
    if path.is_symlink() or not stat.S_ISDIR(st.st_mode) or st.st_uid != 0 or st.st_mode & 0o022:
        fail("AUTHORITY_DIRECTORY_CUSTODY_INVALID")


def _generation_root(policy: dict) -> Path:
    root = DEFAULT_GENERATIONS
    if not str(root).startswith("/var/lib/polymarket-weather-paper-rollback/"):
        fail("AUTHORITY_GENERATION_ROOT_INVALID")
    return root


def _generation_dir(policy: dict, generation_id: str) -> Path:
    gid = _hex(generation_id, 64, "CUTOVER_GENERATION_ID_INVALID")
    return _generation_root(policy) / gid


def _systemctl(unit: str, verb: str) -> bool:
    return _run(["/usr/bin/systemctl", verb, "--quiet", unit], check=False).returncode == 0


@contextlib.contextmanager
def _quiescent_writers(policy: dict, *, stop: bool, require_root: bool):
    """Do not confuse a stop request with proof that all writers have exited."""
    handles = []
    try:
        if require_root:
            for component in policy["components"].values():
                unit = component["unit_name"]
                if stop and _run(["/usr/bin/systemctl", "stop", unit], check=False).returncode:
                    fail("RECOVERY_SERVICE_STOP_FAILED")
                result = _run(["/usr/bin/systemctl", "show", unit, "--property=MainPID,ActiveState"], check=False)
                state = dict(row.split("=", 1) for row in result.stdout.splitlines() if "=" in row)
                if result.returncode or state.get("MainPID") != "0" or state.get("ActiveState") not in {"inactive", "failed"}:
                    fail("CUTOVER_SERVICE_NOT_QUIESCENT")
        for value in policy["writer_lock_paths"]:
            path = Path(value)
            # Runtime owns this stable inode too. Never unlink/recreate an existing
            # lease, which would allow the process and authority to lock different files.
            try:
                fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
                created = True
            except FileExistsError:
                fd = os.open(path, os.O_RDWR | os.O_NOFOLLOW | os.O_CLOEXEC)
                created = False
            handles.append(fd)
            if not stat.S_ISREG(os.fstat(fd).st_mode):
                fail("CUTOVER_WRITER_LOCK_INVALID")
            name = "execution" if value == policy.get("execution_db_path", "") + ".writer.lock" else "scanner" if value == policy.get("scanner_db_path", "") + ".writer.lock" else _primary_component(policy)
            owner = policy["components"][name]
            if require_root:
                if created:
                    os.fchown(fd, owner["uid"], owner["gid"])
                elif os.fstat(fd).st_uid != owner["uid"]:
                    fail("CUTOVER_WRITER_LOCK_OWNER_INVALID")
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                fail("CUTOVER_WRITER_STILL_ACTIVE")
        yield
    finally:
        for fd in handles:
            os.close(fd)


def _seal_tree(root: Path, *, require_root: bool) -> None:
    for directory, _, files in os.walk(root):
        if require_root:
            os.chown(directory, 0, 0)
        os.chmod(directory, 0o755)
        for name in files:
            path = Path(directory) / name
            if not path.is_symlink():
                if require_root:
                    os.chown(path, 0, 0)
                os.chmod(path, 0o555 if path.stat().st_mode & stat.S_IXUSR else 0o444)


def _logical_db_digest(path: Path) -> str:
    if not path.exists():
        return "ABSENT"
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=5.0)
    try:
        con.execute("BEGIN")
        digest = hashlib.sha256()
        for pragma in ("user_version", "application_id"):
            digest.update(f"{pragma}={con.execute('PRAGMA ' + pragma).fetchone()[0]}\n".encode())
        for row in con.iterdump():
            digest.update((row + "\n").encode())
    finally:
        con.close()
    return digest.hexdigest()


def _backup_db(source: Path, target: Path) -> tuple[str, str]:
    if not source.exists():
        return "ABSENT", "ABSENT"
    if source.is_symlink() or not source.is_file():
        fail("CUTOVER_DB_SOURCE_INVALID")
    tmp = target.with_name("." + target.name + ".tmp")
    src = sqlite3.connect(f"file:{source}?mode=ro", uri=True, timeout=5.0)
    dst = sqlite3.connect(tmp, timeout=5.0)
    try:
        src.execute("PRAGMA busy_timeout=5000")
        src.backup(dst, pages=256, sleep=0.01)
    finally:
        dst.close()
        src.close()
    chk = sqlite3.connect(f"file:{tmp}?mode=ro", uri=True, timeout=5.0)
    try:
        if chk.execute("PRAGMA quick_check").fetchall() != [("ok",)]:
            fail("CUTOVER_DB_BACKUP_INVALID")
    finally:
        chk.close()
    os.replace(tmp, target)
    return sha256_file(target), _logical_db_digest(target)


def _stat_identity(st: os.stat_result) -> tuple:
    return (st.st_dev, st.st_ino, st.st_mode, st.st_uid, st.st_gid,
            st.st_size, st.st_mtime_ns, st.st_ctime_ns, st.st_nlink)


def _walk_tree(root: Path, *, reader_uid: int | None = None, reader_gid: int | None = None):
    """Read through pinned parent fds; never reopen candidate paths as root."""
    count = 0

    def readable(st, *, directory=False, traverse_only=False):
        if reader_uid is None:
            return
        shift = 6 if st.st_uid == reader_uid else (3 if st.st_gid == reader_gid else 0)
        need = 1 if traverse_only else (5 if directory else 4)
        if ((st.st_mode >> shift) & need) != need:
            fail("CUTOVER_SNAPSHOT_INPUT_NOT_READABLE_BY_DEPLOY_PRINCIPAL")

    def directory(fd: int, relative: str):
        nonlocal count
        current = os.fstat(fd)
        readable(current, directory=True)
        yield {"path": relative, "kind": "dir", "mode": stat.S_IMODE(current.st_mode),
               "uid": current.st_uid, "gid": current.st_gid}, None, current
        for name in sorted(os.listdir(fd)):
            count += 1
            if count > 100000 or len(PurePosixPath(relative).parts) > 64:
                fail("CUTOVER_TREE_RESOURCE_LIMIT")
            captured = os.stat(name, dir_fd=fd, follow_symlinks=False)
            rel = relative + "/" + name
            row = {"path": rel, "mode": stat.S_IMODE(captured.st_mode), "uid": captured.st_uid, "gid": captured.st_gid}
            if stat.S_ISDIR(captured.st_mode):
                child = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=fd)
                try:
                    if _stat_identity(captured) != _stat_identity(os.fstat(child)):
                        fail("CUTOVER_TREE_CHANGED_DURING_READ")
                    yield from directory(child, rel)
                finally:
                    os.close(child)
            elif stat.S_ISREG(captured.st_mode):
                readable(captured)
                child = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=fd)
                try:
                    if _stat_identity(captured) != _stat_identity(os.fstat(child)):
                        fail("CUTOVER_TREE_CHANGED_DURING_READ")
                    digest = hashlib.sha256()
                    while block := os.read(child, 1024 * 1024):
                        digest.update(block)
                    os.lseek(child, 0, os.SEEK_SET)
                    row.update(kind="file", size=captured.st_size, sha256=digest.hexdigest())
                    yield row, child, captured
                    if _stat_identity(captured) != _stat_identity(os.fstat(child)):
                        fail("CUTOVER_TREE_CHANGED_DURING_READ")
                finally:
                    os.close(child)
            elif stat.S_ISLNK(captured.st_mode):
                row.update(kind="symlink", target=os.readlink(name, dir_fd=fd))
                if _stat_identity(captured) != _stat_identity(os.stat(name, dir_fd=fd, follow_symlinks=False)):
                    fail("CUTOVER_TREE_CHANGED_DURING_READ")
                yield row, None, captured
            else:
                fail("CUTOVER_TREE_FILE_TYPE_INVALID")
        if _stat_identity(current) != _stat_identity(os.fstat(fd)):
            fail("CUTOVER_TREE_CHANGED_DURING_READ")

    absolute = root.absolute()
    if ".." in absolute.parts:
        fail("CUTOVER_TREE_PATH_INVALID")
    # O_NOFOLLOW on the final name alone would still permit a swapped ancestor.
    # Open every path component relative to an already pinned parent descriptor.
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
    fd = os.open("/", flags)
    try:
        for name in absolute.parts[1:]:
            readable(os.fstat(fd), traverse_only=True)
            child = os.open(name, flags, dir_fd=fd)
            os.close(fd)
            fd = child
        yield from directory(fd, root.name)
    finally:
        os.close(fd)


def _tree_entries(root: Path, *, reader_uid: int | None = None, reader_gid: int | None = None) -> list[dict]:
    return sorted((row for row, _, _ in _walk_tree(root, reader_uid=reader_uid, reader_gid=reader_gid)),
                  key=lambda row: row["path"])


def _tree_digest(root: Path) -> str:
    return _sha_bytes(_canonical(_tree_entries(root)))


def _snapshot_venv(venv: Path, archive: Path, manifest: Path, *, root_custody: bool = True,
                   approved_interpreters: dict[str, str] | None = None,
                   reader_uid: int | None = None, reader_gid: int | None = None) -> tuple[str, str, str]:
    if not venv.is_dir() or venv.is_symlink() or not (venv / "bin/python").exists():
        fail("CUTOVER_PREDECESSOR_VENV_INVALID")
    if root_custody and (not reader_uid or not reader_gid):
        fail("CUTOVER_SNAPSHOT_READER_IDENTITY_REQUIRED")
    before = _tree_entries(venv, reader_uid=reader_uid, reader_gid=reader_gid)
    external = {}
    for row in before:
        if row["kind"] != "symlink":
            continue
        path = venv.parent / row["path"]
        target = PurePosixPath(row["target"])
        if not target.is_absolute() and ".." not in target.parts:
            continue
        resolved = path.resolve()
        if path.parent != venv / "bin" or not re.fullmatch(r"python[0-9.]*", path.name):
            fail("CUTOVER_UNAPPROVED_EXTERNAL_VENV_LINK")
        if root_custody:
            expected_digest = (approved_interpreters or {}).get(str(resolved))
            if expected_digest is None:
                fail("CUTOVER_PREDECESSOR_INTERPRETER_NOT_APPROVED")
            _root_custody(resolved)
            _root_path_chain(resolved.parent)
        digest = sha256_file(resolved)
        if root_custody:
            if expected_digest != digest:
                fail("CUTOVER_PREDECESSOR_INTERPRETER_NOT_APPROVED")
        external[row["path"]] = {"target": row["target"], "resolved": str(resolved), "sha256": digest}
    archived = []
    with tarfile.open(archive, "w", format=tarfile.PAX_FORMAT) as tf:
        for row, fd, captured in _walk_tree(venv, reader_uid=reader_uid, reader_gid=reader_gid):
            member = tarfile.TarInfo(row["path"])
            member.mode, member.uid, member.gid = row["mode"], captured.st_uid, captured.st_gid
            member.mtime = captured.st_mtime
            if row["kind"] == "dir":
                member.type = tarfile.DIRTYPE
                tf.addfile(member)
            elif row["kind"] == "symlink":
                member.type, member.linkname = tarfile.SYMTYPE, row["target"]
                tf.addfile(member)
            else:
                # Each hardlinked regular file is materialized from its checked
                # descriptor; a pathname-based archiver must never reopen it.
                member.size = row["size"]
                with os.fdopen(os.dup(fd), "rb") as stream:
                    tf.addfile(member, stream)
            archived.append(row)
    after = _tree_entries(venv, reader_uid=reader_uid, reader_gid=reader_gid)
    if before != after or before != sorted(archived, key=lambda row: row["path"]):
        fail("CUTOVER_PREDECESSOR_VENV_CHANGED")
    tree_sha = _sha_bytes(_canonical(before))
    payload = {"version":"authority-v3-predecessor-venv","venv_name":venv.name,"source_path":str(venv),"tree_sha256":tree_sha,"entries":before,"archive_sha256":sha256_file(archive),"external_interpreters":external}
    _safe_write(manifest, _canonical(payload), root_custody=root_custody)
    return payload["archive_sha256"], sha256_file(manifest), tree_sha


def _safe_extract_tar(archive: Path, destination: Path, *, reject_symlinks: bool) -> None:
    with tarfile.open(archive, "r:*") as tf:
        members = tf.getmembers()
        for member in members:
            pure = PurePosixPath(member.name)
            if pure.is_absolute() or ".." in pure.parts:
                fail("AUTHORITY_ARCHIVE_PATH_INVALID")
            if member.isdir() or member.isfile():
                continue
            if member.issym() and not reject_symlinks:
                target = PurePosixPath(member.linkname)
                if target.is_absolute() or ".." in target.parts:
                    fail("AUTHORITY_ARCHIVE_SYMLINK_INVALID")
                continue
            fail("AUTHORITY_ARCHIVE_TYPE_INVALID")
        tf.extractall(destination, members=members, filter="fully_trusted")


def _safe_extract_venv(archive: Path, parent: Path, expected_name: str, manifest: Path | None = None) -> None:
    expected = _read_json(manifest) if manifest else None
    if expected and (expected.get("archive_sha256") != sha256_file(archive) or expected.get("venv_name") != expected_name):
        fail("CUTOVER_VENV_ARCHIVE_MANIFEST_MISMATCH")
    expected_rows = {row["path"]: row for row in expected.get("entries", [])} if expected else {}
    with tarfile.open(archive, "r") as tf:
        members = tf.getmembers()
        names = [m.name.rstrip("/") for m in members]
        if len(names) != len(set(names)) or (expected and set(names) != set(expected_rows)):
            fail("CUTOVER_VENV_ARCHIVE_FILESET_MISMATCH")
        links = {m.name for m in members if m.issym()}
        for member in members:
            pure = PurePosixPath(member.name)
            if pure.is_absolute() or not pure.parts or pure.parts[0] != expected_name or ".." in pure.parts:
                fail("CUTOVER_VENV_ARCHIVE_PATH_INVALID")
            if any(str(ancestor) in links for ancestor in pure.parents):
                fail("CUTOVER_VENV_ARCHIVE_SYMLINK_ANCESTOR")
            if not (member.isdir() or member.isfile() or member.issym()):
                fail("CUTOVER_VENV_ARCHIVE_TYPE_INVALID")
            row = expected_rows.get(member.name, {})
            kind = "symlink" if member.issym() else "dir" if member.isdir() else "file"
            if expected and (row.get("kind") != kind or row.get("mode") != member.mode or row.get("uid") != member.uid or row.get("gid") != member.gid):
                fail("CUTOVER_VENV_ARCHIVE_METADATA_MISMATCH")
            if member.issym():
                if expected and row.get("target") != member.linkname:
                    fail("CUTOVER_VENV_ARCHIVE_SYMLINK_INVALID")
                target = PurePosixPath(member.linkname)
                if target.is_absolute() or ".." in target.parts:
                    external = (expected or {}).get("external_interpreters", {}).get(member.name)
                    if not external or external.get("target") != member.linkname:
                        fail("CUTOVER_VENV_ARCHIVE_SYMLINK_INVALID")
                    original = Path(expected["source_path"]) / Path(member.name).relative_to(expected_name)
                    resolved = (original.parent / member.linkname).resolve()
                    if str(resolved) != external.get("resolved") or sha256_file(resolved) != external.get("sha256"):
                        fail("CUTOVER_VENV_INTERPRETER_CHANGED")
            elif member.isfile() and expected:
                stream = tf.extractfile(member)
                if stream is None:
                    fail("CUTOVER_VENV_ARCHIVE_FILE_INVALID")
                digest = hashlib.sha256()
                for block in iter(lambda: stream.read(1024 * 1024), b""):
                    digest.update(block)
                if digest.hexdigest() != row.get("sha256") or member.size != row.get("size"):
                    fail("CUTOVER_VENV_ARCHIVE_FILE_DIGEST_MISMATCH")
        target_root = parent / expected_name
        if target_root.exists() or target_root.is_symlink():
            fail("CUTOVER_VENV_EXTRACTION_TARGET_EXISTS")
        # All members are validated before any write; links are created LAST, so
        # a valid absolute interpreter link can never redirect file extraction.
        regular = [m for m in members if not m.issym()]
        tf.extractall(parent, members=regular, filter="fully_trusted")
        for member in members:
            if member.issym():
                path = parent / member.name
                os.symlink(member.linkname, path)
                if os.geteuid() == 0:
                    os.chown(path, member.uid, member.gid, follow_symlinks=False)


def _repo_bundle(repo: Path, commit: str, bundle: Path, generation_id: str) -> str:
    # repo is a clean authority-owned object store, never the application .git.
    ref = f"refs/heads/alpha-predecessor-{generation_id}"
    _git(repo, "update-ref", ref, commit)
    try:
        _git(repo, "bundle", "create", str(bundle), ref)
        _git(repo, "bundle", "verify", str(bundle))
    finally:
        _git(repo, "update-ref", "-d", ref)
    return sha256_file(bundle)


def _active_runtime(policy: dict, *, require_root: bool = True) -> dict | None:
    path = Path(policy["runtime_root"]) / "active-release.json"
    if not path.exists():
        return None
    if require_root:
        _root_custody(path)
    raw = _read_json(path)
    if raw.get("version") != "weather-paper-active-release-v3":
        fail("ACTIVE_RELEASE_MANIFEST_INVALID")
    return raw


def _predecessor_venv(policy: dict, *, require_root: bool = True) -> Path:
    active = _active_runtime(policy, require_root=require_root)
    if active:
        path = _abs(active.get("venv_path"), "ACTIVE_RELEASE_VENV_PATH_INVALID")
        runtime_root = Path(policy["runtime_root"]).resolve()
        if runtime_root not in path.resolve().parents:
            fail("ACTIVE_RELEASE_VENV_OUTSIDE_RUNTIME_ROOT")
        return path
    return Path(policy["legacy_predecessor_venv"])


def _load_generation(generation_id: str, policy: dict, *, require_root: bool = True, require_active: bool = True) -> tuple[Path, dict]:
    gid = _hex(generation_id, 64, "CUTOVER_GENERATION_ID_INVALID")
    gdir = _generation_dir(policy, gid)
    manifest = gdir / "manifest.json"
    active = ACTIVE
    if require_root:
        _root_custody(manifest)
        _secure_root_dir(gdir)
        if require_active:
            _root_custody(active)
    data = _read_json(manifest)
    active_data = _read_json(active) if require_active else None
    if data.get("version") != "weather-paper-cutover-v3" or data.get("generation_id") != gid:
        fail("CUTOVER_GENERATION_MANIFEST_INVALID")
    if require_active and (not isinstance(active_data, dict) or active_data.get("generation_id") != gid):
        fail("CUTOVER_ACTIVE_GENERATION_MISMATCH")
    if data.get("policy_sha256") != _sha_bytes(_canonical(policy)):
        fail("CUTOVER_POLICY_MISMATCH")
    if data.get("predecessor_sha") == data.get("candidate_sha"):
        fail("CUTOVER_PREDECESSOR_EQUALS_CANDIDATE")
    if data.get("authority_sha256") != authority_digest():
        fail("CUTOVER_AUTHORITY_CHANGED")
    for name, key in (("predecessor-unit.service","predecessor_unit_sha256"),("predecessor-venv.tar","predecessor_venv_archive_sha256"),("predecessor-venv-manifest.json","predecessor_venv_manifest_sha256"),("predecessor.bundle","predecessor_bundle_sha256")):
        path = gdir / name
        if require_root:
            _root_custody(path)
        if sha256_file(path) != data.get(key):
            fail("CUTOVER_PAYLOAD_DIGEST_MISMATCH:" + name)
    if data.get("predecessor_db_present"):
        db = gdir / "predecessor.sqlite3"
        if require_root:
            _root_custody(db)
        if sha256_file(db) != data.get("predecessor_db_sha256"):
            fail("CUTOVER_DB_DIGEST_MISMATCH")
        if _logical_db_digest(db) != data.get("predecessor_db_logical_sha256"):
            fail("CUTOVER_DB_LOGICAL_DIGEST_MISMATCH")
    if _tree_digest(gdir / "objects.git") != data.get("objects_tree_sha256"):
        fail("CUTOVER_OBJECT_STORE_CHANGED")
    for row in data.get("predecessor_extra_venvs", {}).values():
        for filename, key in ((row["archive"], "archive_sha256"), (row["manifest"], "manifest_sha256")):
            path = gdir / filename
            if path.parent != gdir:
                fail("CUTOVER_PAYLOAD_PATH_INVALID")
            if require_root:
                _root_custody(path)
            if sha256_file(path) != row[key]:
                fail("CUTOVER_EXTRA_VENV_DIGEST_MISMATCH")
    for row in data.get("predecessor_units", {}).values():
        if row["present"] and sha256_file(gdir / row["file"]) != row["sha256"]:
            fail("CUTOVER_UNIT_DIGEST_MISMATCH")
    return gdir, data


@_serialized_mutation
def create_cutover(candidate_sha: str, *, anchor: Path = ANCHOR, require_root: bool = True) -> str:
    verified = verify_self(anchor, require_root=require_root)
    policy = verified["policy"]
    repo = Path(policy["app_dir"])
    if ACTIVE.exists() or ACTIVE.is_symlink():
        fail("CUTOVER_ALREADY_ACTIVE")
    if repo.is_symlink() or not (repo / ".git").is_dir():
        fail("CUTOVER_APP_REPO_INVALID")
    predecessor = _hex(_app_git(policy, "rev-parse", "HEAD", require_root=require_root), 40, "CUTOVER_PREDECESSOR_SHA_INVALID")
    marker = _hex(Path(policy["release_file"]).read_text().strip(), 40, "CUTOVER_RELEASE_MARKER_INVALID")
    if marker != predecessor:
        fail("CUTOVER_RELEASE_MARKER_MISMATCH")
    if _app_git(policy, "status", "--porcelain", "--untracked-files=all", require_root=require_root):
        fail("CUTOVER_PREDECESSOR_DIRTY")
    candidate = _hex(candidate_sha, 40, "CUTOVER_CANDIDATE_SHA_INVALID")
    if candidate == predecessor:
        fail("CUTOVER_CANDIDATE_ALREADY_CURRENT")
    approval = _release_approval(policy, candidate, require_root=require_root)
    previous_approval = _release_approval(policy, predecessor, require_root=require_root)
    configurations = _component_configurations(policy, require_root=require_root)
    gid = _sha_bytes(f"{predecessor}:{candidate}:{time.time_ns()}:{os.urandom(32).hex()}".encode())
    root = _generation_root(policy)
    root.mkdir(parents=True, exist_ok=True)
    gdir = root / gid
    gdir.mkdir(mode=0o700)
    objects = gdir / "objects.git"
    _import_app_objects(policy, [predecessor, candidate], objects, require_root=require_root)
    for sha, approved in ((predecessor, previous_approval), (candidate, approval)):
        if _git(objects, "rev-parse", sha + "^{tree}") != approved["tree"]:
            fail("HOST_RELEASE_APPROVED_TREE_MISMATCH")
    unit_file = Path(policy["unit_file"])
    if unit_file.is_symlink() or not unit_file.is_file():
        fail("CUTOVER_PREDECESSOR_UNIT_INVALID")
    if require_root:
        _root_custody(unit_file)
    unit_copy = gdir / "predecessor-unit.service"
    shutil.copyfile(unit_file, unit_copy)
    unit_states = {}
    for name, component in policy["components"].items():
        path = Path(component["unit_file"])
        row = {"present": path.exists(), "active": _systemctl(component["unit_name"], "is-active") if require_root else False,
               "enabled": _systemctl(component["unit_name"], "is-enabled") if require_root else False}
        if path.exists():
            _root_custody(path) if require_root else _regular_nosymlink(path)
            copy = gdir / ("predecessor-unit.service" if name == _primary_component(policy) else f"predecessor-{name}-unit.service")
            if copy != unit_copy:
                shutil.copyfile(path, copy)
            row.update(file=copy.name, sha256=sha256_file(copy))
        unit_states[name] = row
    with _quiescent_writers(policy, stop=True, require_root=require_root):
        db_sha, db_logical = _backup_db(Path(policy["db_path"]), gdir / "predecessor.sqlite3")
        active_release = _active_runtime(policy, require_root=require_root)
        if active_release and active_release.get("candidate_sha") != predecessor:
            fail("CUTOVER_ACTIVE_RELEASE_IDENTITY_MISMATCH")
        predecessor_venv = _predecessor_venv(policy, require_root=require_root)
        interpreters = {str(Path(policy["runtime_python"]).resolve()): policy["runtime_python_sha256"],
                        **policy.get("predecessor_interpreters", {})}
        venv_archive_sha, venv_manifest_sha, venv_tree_sha = _snapshot_venv(
            predecessor_venv, gdir / "predecessor-venv.tar", gdir / "predecessor-venv-manifest.json",
            root_custody=require_root, approved_interpreters=interpreters,
            reader_uid=policy["deploy_uid"] if require_root else None, reader_gid=policy["deploy_gid"] if require_root else None)
        extra_venvs = {}
        for name, component in (active_release or {}).get("components", {}).items():
            path = Path(component["venv_path"])
            if path == predecessor_venv:
                continue
            archive, manifest = gdir / f"predecessor-{name}-venv.tar", gdir / f"predecessor-{name}-venv.json"
            ar_sha, mf_sha, tr_sha = _snapshot_venv(path, archive, manifest, root_custody=require_root, approved_interpreters=interpreters,
                reader_uid=policy["deploy_uid"] if require_root else None, reader_gid=policy["deploy_gid"] if require_root else None)
            extra_venvs[name] = {"path": str(path), "archive": archive.name, "manifest": manifest.name,
                                 "archive_sha256": ar_sha, "manifest_sha256": mf_sha, "tree_sha256": tr_sha}
        bundle_sha = _repo_bundle(objects, predecessor, gdir / "predecessor.bundle", gid)
        _seal_tree(objects, require_root=require_root)
        now = time.time()
        data = {"version": "weather-paper-cutover-v3", "generation_id": gid, "created_at": now, "sealed_at": now,
                "policy_sha256": verified["policy_sha256"], "predecessor_sha": predecessor, "predecessor_tree": previous_approval["tree"],
                "candidate_sha": candidate, "candidate_tree": approval["tree"], "candidate_approval": approval,
                "initial_configuration_sha256": configurations,
                "objects_tree_sha256": _tree_digest(objects), "predecessor_unit_sha256": sha256_file(unit_copy),
                "predecessor_units": unit_states, "predecessor_db_present": db_sha != "ABSENT", "predecessor_db_sha256": db_sha,
                "predecessor_db_logical_sha256": db_logical,
                "predecessor_auxiliary_state_identities": _auxiliary_state_identities(policy),
                "predecessor_execution_journal_identity": _logical_db_digest(Path(policy["execution_db_path"])) if "execution" in policy["components"] else None,
                "predecessor_venv_path": str(predecessor_venv),
                "predecessor_venv_archive_sha256": venv_archive_sha, "predecessor_venv_manifest_sha256": venv_manifest_sha,
                "predecessor_venv_tree_sha256": venv_tree_sha, "predecessor_bundle_sha256": bundle_sha,
                "predecessor_active_release": active_release, "predecessor_extra_venvs": extra_venvs,
                "predecessor_enabled": unit_states[_primary_component(policy)]["enabled"], "predecessor_active": unit_states[_primary_component(policy)]["active"],
                "release_marker": marker, "deploy_user": policy["deploy_user"], "deploy_uid": policy["deploy_uid"],
                "deploy_gid": policy["deploy_gid"], "app_dir": policy["app_dir"], "release_file": policy["release_file"],
                "unit": policy["unit_name"], "unit_file": policy["unit_file"], "db_path": policy["db_path"],
                "authority_version": AUTHORITY_VERSION, "authority_sha256": authority_digest()}
        _safe_write(gdir / "manifest.json", _canonical(data), root_custody=require_root)
        if require_root:
            for path in gdir.iterdir():
                if path.is_file():
                    os.chmod(path, 0o440)
                    os.chown(path, 0, 0)
            os.chmod(gdir, 0o550)
        _safe_write(ACTIVE, _canonical({"version": "weather-paper-active-cutover-v3", "generation_id": gid,
                    "predecessor_sha": predecessor, "candidate_sha": candidate, "created_at": now,
                    "policy_sha256": verified["policy_sha256"]}), root_custody=require_root)
        return gid


def _inventory(python: Path) -> list[dict]:
    # Inspect metadata as data. Never import candidate site hooks as root merely
    # to find which packages are installed.
    sites = list((python.parent.parent / "lib").glob("python*/site-packages"))
    if len(sites) != 1 or sites[0].is_symlink():
        fail("RUNTIME_SITE_PACKAGES_AMBIGUOUS")
    rows = [{"name": d.metadata.get("Name", ""), "version": d.version}
            for d in importlib.metadata.distributions(path=[str(sites[0])])]
    return sorted(rows, key=lambda row: (row["name"].lower(), row["version"]))


def _norm(name: object) -> str:
    return re.sub(r"[-_.]+", "-", str(name or "")).lower()


def _lock_pins(lock: Path) -> dict[str, str]:
    pins: dict[str, str] = {}
    logical = ""
    for raw in lock.read_text(encoding="utf-8").splitlines():
        row = raw.split("#", 1)[0].strip()
        if not row:
            continue
        logical += " " + row
        if logical.endswith("\\"):
            logical = logical[:-1]
            continue
        row = logical.strip()
        logical = ""
        match = re.fullmatch(r"([A-Za-z0-9_.-]+)(?:\[[A-Za-z0-9_,.-]+\])?==([A-Za-z0-9_.+!-]+)((?:\s+--hash=sha256:[0-9a-f]{64})+)", row)
        if not match:
            fail("RUNTIME_LOCK_NOT_EXACT_HASHED")
        name, version = _norm(match.group(1)), match.group(2)
        if name in pins:
            fail("RUNTIME_LOCK_DUPLICATE_DISTRIBUTION")
        pins[name] = version
    if logical or not pins:
        fail("RUNTIME_LOCK_NOT_EXACT_HASHED")
    return pins


def _lock_names(lock: Path) -> set[str]:
    return set(_lock_pins(lock))


def _validate_inventory(rows: list[dict], lock: Path, approved: dict[str, str] | None = None) -> None:
    expected = _lock_pins(lock)
    if approved is not None and expected != {_norm(k): v for k, v in approved.items()}:
        fail("RUNTIME_LOCK_NOT_APPROVED_FOR_COMPONENT")
    observed = {}
    for row in rows:
        name = _norm(row.get("name"))
        if not name or name in observed:
            fail("RUNTIME_DUPLICATE_DISTRIBUTION")
        observed[name] = row.get("version")
    extra, missing = set(observed) - set(expected), set(expected) - set(observed)
    if extra:
        fail("RUNTIME_UNEXPECTED_DISTRIBUTIONS:" + ",".join(sorted(extra)))
    if missing:
        fail("RUNTIME_MISSING_DISTRIBUTIONS:" + ",".join(sorted(missing)))
    if observed != expected:
        fail("RUNTIME_DISTRIBUTION_VERSION_MISMATCH")


def _archive_candidate(repo: Path, candidate: str, destination: Path) -> None:
    archive=destination/"candidate-source.tar"
    with archive.open("wb") as fh:
        result=subprocess.run(_git_command(repo,"archive","--format=tar",candidate),stdout=fh,stderr=subprocess.DEVNULL,check=False,timeout=120,env={"PATH":"/usr/bin:/bin","HOME":"/nonexistent","LANG":"C.UTF-8","GIT_CONFIG_NOSYSTEM":"1","GIT_CONFIG_GLOBAL":"/dev/null"})
    if result.returncode != 0: fail("RUNTIME_GIT_ARCHIVE_FAILED")
    source=destination/"source"; source.mkdir(mode=0o755); _safe_extract_tar(archive,source,reject_symlinks=True); archive.unlink()
    for bad in ("sitecustomize.py","usercustomize.py"):
        if any(p.name.lower()==bad for p in source.rglob("*")): fail("RUNTIME_STARTUP_HOOK_FORBIDDEN:"+bad)
    if any(p.suffix.lower()==".pth" for p in source.rglob("*") if p.is_file()): fail("RUNTIME_SOURCE_PTH_FORBIDDEN")


def _verify_source_against_git(repo: Path, candidate: str, source: Path) -> str:
    listing=_git(repo,"ls-tree","-r",candidate).splitlines(); expected={}
    for line in listing:
        try: left,name=line.split("\t",1); mode,kind,blob=left.split()
        except ValueError: fail("RUNTIME_GIT_TREE_PARSE_FAILED")
        if kind!="blob" or mode not in {"100644","100755"}: fail("RUNTIME_GIT_TREE_NONREGULAR_ENTRY")
        expected[name]=(mode,blob)
    actual={p.relative_to(source).as_posix():p for p in source.rglob("*") if p.is_file()}
    if set(actual)!=set(expected): fail("RUNTIME_SOURCE_FILESET_MISMATCH")
    rows=[]
    for name,(mode,blob) in sorted(expected.items()):
        path=actual[name]
        if path.is_symlink(): fail("RUNTIME_SOURCE_SYMLINK")
        hasher = hashlib.sha1(b"blob " + str(path.stat().st_size).encode() + b"\0")
        with path.open("rb") as handle:
            for block in iter(lambda: handle.read(1024 * 1024), b""):
                hasher.update(block)
        if hasher.hexdigest()!=blob: fail("RUNTIME_SOURCE_BLOB_MISMATCH:"+name)
        executable=bool(path.stat().st_mode & stat.S_IXUSR)
        if executable!=(mode=="100755"): os.chmod(path,0o755 if mode=="100755" else 0o644)
        rows.append({"path":name,"mode":mode,"blob":blob})
    return _sha_bytes(_canonical(rows))


def _render_unit(policy: dict, generation_id: str, candidate: str, runtime: dict, component_name: str = "signals") -> str:
    component = policy["components"][component_name]
    source = Path(runtime["source_path"])
    python = Path(runtime["components"][component_name]["venv_path"]) / "bin/python"
    database = _component_database(policy, component_name)
    state = str(Path(database).parent)
    unset = " ".join(sorted(FORBIDDEN_PROCESS_ENV))
    authority = INSTALLED_AUTHORITY
    group = policy.get("shared_read_group", "")
    supplementary = f"SupplementaryGroups={group}\n" if group else ""
    clean = "PATH=/usr/bin:/bin HOME=/nonexistent LANG=C.UTF-8"
    hidden = []
    if "controller" in policy["components"]:
        if component_name != "controller":
            hidden.append(policy["telegram_file"])
        if component_name != "execution" and "execution" in policy["components"]:
            hidden.extend([policy["execution_credentials_file"], policy["execution_activation_file"], policy["execution_db_path"], policy["execution_db_path"]+"-wal", policy["execution_db_path"]+"-shm"])
    inaccessible = "InaccessiblePaths=" + " ".join("-"+path for path in hidden) + "\n" if hidden else ""
    return f"""[Unit]
Description=Alpha weather production {component_name}
Wants=network-online.target
After=network-online.target
StartLimitIntervalSec=600
StartLimitBurst=3

[Service]
Type=simple
User={component['user']}
Group={component['gid']}
{supplementary}WorkingDirectory={source}
UnsetEnvironment={unset}
ExecStartPre=+/usr/bin/env -i {clean} /usr/bin/python3 -I -s -E {authority} verify-runtime-files --generation-id {generation_id} --candidate-sha {candidate}
ExecStart=/usr/bin/env -i {clean} PYTHONUNBUFFERED=1 PYTHONNOUSERSITE=1 PYTHONDONTWRITEBYTECODE=1 ALPHA_DISABLE_DOTENV=1 ALPHA_RELEASE_SHA={candidate} ALPHA_CUTOVER_GENERATION={generation_id} ALPHA_RUNTIME_SOURCE={source} {python} -I -s -E -B -m {component['module']} {component['command']} --config {component['config_file']}
Restart=on-failure
RestartSec=15
TimeoutStopSec=30
MemoryHigh=280M
MemoryMax=350M
MemorySwapMax=0
TasksMax=48
NoNewPrivileges=true
PrivateTmp=true
PrivateDevices=true
ProtectHome=read-only
ProtectSystem=strict
ProtectKernelTunables=true
ProtectKernelModules=true
ProtectControlGroups=true
RestrictSUIDSGID=true
LockPersonality=true
CapabilityBoundingSet=
AmbientCapabilities=
ReadWritePaths={state}
{inaccessible}RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6
UMask=0027

[Install]
WantedBy=multi-user.target
"""


def _runtime_manifest_path(policy: dict, candidate: str) -> Path:
    return Path(policy["runtime_root"])/"releases"/candidate/"runtime-manifest.json"


@_serialized_mutation
def prepare_candidate(generation_id: str, candidate_sha: str, *, anchor: Path = ANCHOR, require_root: bool = True) -> dict:
    verified = verify_self(anchor, require_root=require_root)
    policy = verified["policy"]
    gdir, generation = _load_generation(generation_id, policy, require_root=require_root)
    candidate = _hex(candidate_sha, 40, "CUTOVER_CANDIDATE_SHA_INVALID")
    if generation["candidate_sha"] != candidate:
        fail("CUTOVER_CANDIDATE_MISMATCH")
    approval = _release_approval(policy, candidate, require_root=require_root)
    configurations = _component_configurations(policy, require_root=require_root)
    if approval != generation["candidate_approval"]:
        fail("CUTOVER_RELEASE_APPROVAL_CHANGED")
    objects = gdir / "objects.git"
    if _git(objects, "rev-parse", candidate + "^{tree}") != generation["candidate_tree"]:
        fail("CUTOVER_CANDIDATE_TREE_MISMATCH")
    releases = Path(policy["runtime_root"]) / "releases"
    releases.mkdir(parents=True, exist_ok=True)
    release_root = releases / candidate
    if release_root.exists() or release_root.is_symlink():
        fail("RUNTIME_RELEASE_ALREADY_EXISTS")
    with _quiescent_writers(policy, stop=False, require_root=require_root):
        release_root.mkdir(mode=0o755)
        _archive_candidate(objects, candidate, release_root)
        source = release_root / "source"
        source_git_sha = _verify_source_against_git(objects, candidate, source)
        components = {}
        for name, settings in policy["components"].items():
            module_file = source.joinpath(*settings["module"].split("."))
            if not module_file.with_suffix(".py").is_file() and not (module_file / "__main__.py").is_file():
                fail("RUNTIME_FINAL_MODULE_MISSING")
            lock = source / settings["lock_file"]
            component_approval = approval["components"][name]
            if not lock.is_file() or lock.is_symlink() or sha256_file(lock) != component_approval["lock_sha256"]:
                fail("RUNTIME_LOCK_NOT_INDEPENDENTLY_APPROVED")
            if _lock_pins(lock) != {_norm(k): v for k, v in component_approval["distributions"].items()}:
                fail("RUNTIME_LOCK_NOT_APPROVED_FOR_COMPONENT")
            venv = release_root / "venvs" / name
            python = venv / "bin/python"
            _run([policy["runtime_python"], "-I", "-s", "-E", "-m", "venv", "--copies", str(venv)])
            # Only binary, independently approved hash-pinned wheels are installed.
            # No predecessor packages, sdists, build backends, or system sites are used.
            _run([str(python), "-I", "-s", "-E", "-m", "pip", "uninstall", "-y", "setuptools", "wheel"], check=False)
            _run([str(python), "-I", "-s", "-E", "-m", "pip", "install", "--disable-pip-version-check",
                  "--only-binary=:all:", "--require-hashes", "--no-deps", "-r", str(lock)])
            cfg = (venv / "pyvenv.cfg").read_text().lower()
            if "include-system-site-packages = false" not in cfg:
                fail("RUNTIME_SYSTEM_SITE_ENABLED")
            sites = list((venv / "lib").glob("python*/site-packages"))
            if len(sites) != 1 or sites[0].is_symlink():
                fail("RUNTIME_SITE_PACKAGES_AMBIGUOUS")
            site = sites[0]
            for path in site.rglob("*"):
                if path.suffix == ".pth" or path.name.lower().startswith(("sitecustomize", "usercustomize")):
                    fail("RUNTIME_DEPENDENCY_STARTUP_HOOK_FORBIDDEN")
            # Read-only package checks execute without root authority. Inventory is
            # read as metadata by this trusted process, never by importing the venv.
            _run(_deploy_command(policy, [str(python), "-I", "-s", "-E", "-m", "pip", "check"], require_root=require_root))
            # pip is a build tool, not a silently permitted unpinned runtime package.
            if "pip" not in component_approval["distributions"]:
                for path in site.glob("pip*"):
                    if path.name == "pip" or path.name.startswith("pip-"):
                        shutil.rmtree(path) if path.is_dir() else path.unlink()
                for path in (venv / "bin").glob("pip*"):
                    path.unlink()
            rows = _inventory(python)
            _validate_inventory(rows, lock, component_approval["distributions"])
            (site / "alpha-reviewed-source.pth").write_text(str(source) + "\n")
            _seal_tree(venv, require_root=require_root)
            components[name] = {"venv_path": str(venv), "lock_file": settings["lock_file"],
                                "requirements_lock_sha256": sha256_file(lock), "venv_tree_sha256": _tree_digest(venv),
                                "distributions": rows, "module": settings["module"], "command": settings["command"]}
        _seal_tree(source, require_root=require_root)
        manifest = {"version": "weather-paper-runtime-release-v3", "generation_id": generation_id,
                    "candidate_sha": candidate, "candidate_tree": generation["candidate_tree"],
                    "release_root": str(release_root), "source_path": str(source),
                    "venv_path": components[_primary_component(policy)]["venv_path"], "components": components,
                    "initial_configuration_sha256": configurations,
                    "source_git_manifest_sha256": source_git_sha, "source_tree_sha256": _tree_digest(source),
                    "python_flags": ["-I", "-s", "-E", "-B"], "final_module": FINAL_MODULE,
                    "execution_authority": "derived_by_execution_component_from_configuration_readiness_and_activation",
                    "prepared_at": time.time()}
        manifest_path = release_root / "runtime-manifest.json"
        _safe_write(manifest_path, _canonical(manifest), mode=0o444, root_custody=require_root)
        unit_evidence = {}
        for name, settings in policy["components"].items():
            copy = gdir / f"candidate-{name}-unit.service"
            _safe_write(copy, _render_unit(policy, generation_id, candidate, manifest, name).encode(), root_custody=require_root)
            unit_evidence[name] = {"file": copy.name, "sha256": sha256_file(copy)}
        evidence = {"version": "weather-paper-runtime-evidence-v3", "generation_id": generation_id,
                    "candidate_sha": candidate, "runtime_manifest_sha256": sha256_file(manifest_path),
                    "runtime_source_tree_sha256": manifest["source_tree_sha256"], "units": unit_evidence,
                    "sealed_at": time.time()}
        _safe_write(gdir / "candidate-runtime-evidence.json", _canonical(evidence), root_custody=require_root)
        # Unit installation follows complete environment validation and sealing.
        for name, settings in policy["components"].items():
            target = Path(settings["unit_file"])
            _safe_write(target, (gdir / unit_evidence[name]["file"]).read_bytes(), mode=0o644, root_custody=require_root)
        if require_root:
            _run(["/usr/bin/systemctl", "daemon-reload"])
    return manifest


def verify_runtime_files(generation_id: str, candidate_sha: str, *, anchor: Path = ANCHOR, require_root: bool = True) -> dict:
    verified = verify_self(anchor, require_root=require_root)
    policy = verified["policy"]
    configurations = _component_configurations(policy, require_root=require_root)
    gdir, generation = _load_generation(generation_id, policy, require_root=require_root, require_active=False)
    candidate = _hex(candidate_sha, 40, "CUTOVER_CANDIDATE_SHA_INVALID")
    if generation["candidate_sha"] != candidate:
        fail("CUTOVER_CANDIDATE_MISMATCH")
    evidence_path = gdir / "candidate-runtime-evidence.json"
    manifest_path = _runtime_manifest_path(policy, candidate)
    if require_root:
        _root_custody(evidence_path)
        _root_custody(manifest_path)
    evidence, manifest = _read_json(evidence_path), _read_json(manifest_path)
    if any(row.get("candidate_sha") != candidate or row.get("generation_id") != generation_id for row in (evidence, manifest)):
        fail("RUNTIME_EVIDENCE_CANDIDATE_MISMATCH")
    if sha256_file(manifest_path) != evidence.get("runtime_manifest_sha256"):
        fail("RUNTIME_MANIFEST_DIGEST_MISMATCH")
    release_root = Path(policy["runtime_root"]) / "releases" / candidate
    if manifest.get("release_root") != str(release_root) or manifest.get("source_path") != str(release_root / "source"):
        fail("RUNTIME_RELEASE_ROOT_MISMATCH")
    source = release_root / "source"
    if _tree_digest(source) != evidence.get("runtime_source_tree_sha256"):
        fail("RUNTIME_SOURCE_TREE_MISMATCH")
    if set(manifest.get("components", {})) != set(policy["components"]):
        fail("RUNTIME_COMPONENT_SET_MISMATCH")
    for name, settings in policy["components"].items():
        component = manifest["components"][name]
        venv = release_root / "venvs" / name
        if component.get("venv_path") != str(venv) or _tree_digest(venv) != component.get("venv_tree_sha256"):
            fail("RUNTIME_VENV_TREE_MISMATCH")
        lock = source / settings["lock_file"]
        approved = generation["candidate_approval"]["components"][name]
        if sha256_file(lock) != component.get("requirements_lock_sha256") or sha256_file(lock) != approved["lock_sha256"]:
            fail("RUNTIME_LOCK_DIGEST_MISMATCH")
        rows = _inventory(venv / "bin/python")
        if rows != component.get("distributions"):
            fail("RUNTIME_INVENTORY_MISMATCH")
        _validate_inventory(rows, lock, approved["distributions"])
        unit = evidence.get("units", {}).get(name, {})
        copy = gdir / str(unit.get("file", ""))
        installed = Path(settings["unit_file"])
        if copy.parent != gdir or not copy.is_file() or sha256_file(copy) != unit.get("sha256"):
            fail("RUNTIME_UNIT_EVIDENCE_MISMATCH")
        if not installed.is_file() or installed.is_symlink() or sha256_file(installed) != unit.get("sha256"):
            fail("RUNTIME_INSTALLED_UNIT_MISMATCH")
        if require_root:
            _root_custody(copy)
            _root_custody(installed)
    return {"policy": policy, "generation": generation, "manifest": manifest, "evidence": evidence,
            "current_configuration_sha256": configurations}


def verify_telegram_env(*, anchor: Path = ANCHOR, require_root: bool = True) -> dict:
    policy=verify_self(anchor,require_root=require_root)["policy"]; path=Path(policy["telegram_env_file"]); st=_regular_nosymlink(path)
    if stat.S_IMODE(st.st_mode)!=0o600 or st.st_uid!=policy["deploy_uid"]: fail("TELEGRAM_ENV_CUSTODY_INVALID")
    keys=[]
    for raw in path.read_text(encoding="utf-8").splitlines():
        row=raw.strip()
        if not row or row.startswith("#") or "=" not in row: fail("TELEGRAM_ENV_ROW_INVALID")
        key,value=row.split("=",1)
        if key not in {"TELEGRAM_BOT_TOKEN","TELEGRAM_CHAT_ID"} or not value or key in keys: fail("TELEGRAM_ENV_KEYSET_INVALID")
        keys.append(key)
    if set(keys)!={"TELEGRAM_BOT_TOKEN","TELEGRAM_CHAT_ID"}: fail("TELEGRAM_ENV_KEYSET_INVALID")
    return {"path":str(path),"keys":sorted(keys),"values_disclosed":False}


@_serialized_mutation
def activate_checkout(generation_id: str, candidate_sha: str, *, anchor: Path = ANCHOR, require_root: bool = True) -> None:
    verified = verify_runtime_files(generation_id, candidate_sha, anchor=anchor, require_root=require_root)
    policy, generation = verified["policy"], verified["generation"]
    _load_generation(generation_id, policy, require_root=require_root)
    candidate = generation["candidate_sha"]
    with _quiescent_writers(policy, stop=False, require_root=require_root):
        # Optional operator worktree follows the sealed runtime, with no root Git
        # hooks, filters, fsmonitor, local configuration, or checkout execution.
        _app_git(policy, "checkout", "--detach", "-f", candidate, require_root=require_root)
        if _app_git(policy, "rev-parse", "HEAD^{tree}", require_root=require_root) != generation["candidate_tree"]:
            fail("CANDIDATE_CHECKOUT_TREE_MISMATCH")
        if _app_git(policy, "status", "--porcelain", "--untracked-files=all", require_root=require_root):
            fail("CANDIDATE_CHECKOUT_DIRTY")
        release = Path(policy["release_file"])
        _safe_write(release, (candidate + "\n").encode(), mode=0o600, root_custody=False)
        if require_root:
            os.chown(release, policy["deploy_uid"], policy["deploy_gid"])


def verify_checkout(generation_id: str, candidate_sha: str, *, anchor: Path = ANCHOR, require_root: bool = True) -> None:
    policy = verify_self(anchor, require_root=require_root)["policy"]
    _, generation = _load_generation(generation_id, policy, require_root=require_root)
    candidate = _hex(candidate_sha, 40, "CUTOVER_CANDIDATE_SHA_INVALID")
    if generation["candidate_sha"] != candidate:
        fail("CUTOVER_CANDIDATE_MISMATCH")
    if _app_git(policy, "rev-parse", "HEAD", require_root=require_root) != candidate:
        fail("CANDIDATE_CHECKOUT_IDENTITY_MISMATCH")
    if _app_git(policy, "rev-parse", "HEAD^{tree}", require_root=require_root) != generation["candidate_tree"]:
        fail("CANDIDATE_CHECKOUT_TREE_MISMATCH")
    if _app_git(policy, "status", "--porcelain", "--untracked-files=all", require_root=require_root):
        fail("CANDIDATE_CHECKOUT_DIRTY")
    if Path(policy["release_file"]).read_text().strip() != candidate:
        fail("CANDIDATE_RELEASE_MARKER_MISMATCH")


def _verify_import_environment(policy: dict, source: Path, venv: Path, *, require_root: bool) -> dict:
    code = ("import sys,site,json,importlib.util; s=importlib.util.find_spec('polymarket_scanner.production');"
            "print(json.dumps({'path':sys.path,'prefix':sys.prefix,'base_prefix':sys.base_prefix,"
            "'executable':sys.executable,'user_site':site.ENABLE_USER_SITE,'isolated':sys.flags.isolated,"
            "'origin':s.origin if s else None}))")
    python = venv / "bin/python"
    data = json.loads(_run(_deploy_command(policy, [str(python), "-I", "-s", "-E", "-B", "-c", code],
                                           require_root=require_root)).stdout)
    origin = Path(data.get("origin") or "/nonexistent")
    if data.get("prefix") != str(venv) or Path(data.get("executable", "/")) != python:
        fail("RUNTIME_PYTHON_PREFIX_MISMATCH")
    if data.get("user_site") is not False or data.get("isolated") != 1 or source not in origin.parents:
        fail("RUNTIME_IMPORT_ISOLATION_FAILED")
    base_paths = json.loads(_run([policy["runtime_python"], "-I", "-S", "-c", "import sys,json;print(json.dumps(sys.path))"]).stdout)
    expected = set(base_paths) | {str(source), str(next((venv / "lib").glob("python*/site-packages")))}
    if {str(Path(path).resolve()) for path in data.get("path", [])} != {str(Path(path).resolve()) for path in expected}:
        fail("RUNTIME_SYS_PATH_MISMATCH")
    return data


def verify_process(generation_id: str, candidate_sha: str, *, anchor: Path = ANCHOR, component_name: str = "signals") -> dict:
    verified = verify_runtime_files(generation_id, candidate_sha, anchor=anchor, require_root=True)
    policy, manifest = verified["policy"], verified["manifest"]
    if component_name not in policy["components"]:
        fail("RUNTIME_COMPONENT_NOT_CONFIGURED")
    component = policy["components"][component_name]
    unit = component["unit_name"]
    if not _systemctl(unit, "is-active"):
        fail("RUNTIME_SERVICE_NOT_ACTIVE")
    raw = _run(["/usr/bin/systemctl", "show", unit, "--property", "MainPID", "--value"]).stdout.strip()
    try:
        pid = int(raw)
    except ValueError:
        fail("RUNTIME_PID_INVALID")
    if pid <= 1:
        fail("RUNTIME_PID_INVALID")
    venv = Path(manifest["components"][component_name]["venv_path"])
    expected_python = venv / "bin/python"
    source = Path(manifest["source_path"])
    if Path(os.readlink(f"/proc/{pid}/exe")).resolve() != expected_python.resolve():
        fail("RUNTIME_PROCESS_EXECUTABLE_MISMATCH")
    if Path(os.readlink(f"/proc/{pid}/cwd")).resolve() != source:
        fail("RUNTIME_PROCESS_CWD_MISMATCH")
    argv = [value.decode() for value in Path(f"/proc/{pid}/cmdline").read_bytes().split(b"\0") if value]
    expected_argv = [str(expected_python), "-I", "-s", "-E", "-B", "-m", FINAL_MODULE, component_name, "--config", component["config_file"]]
    if argv != expected_argv:
        fail("RUNTIME_PROCESS_ARGV_MISMATCH")
    process_env = dict(value.decode().split("=", 1) for value in Path(f"/proc/{pid}/environ").read_bytes().split(b"\0") if b"=" in value)
    expected_env = {"PATH": "/usr/bin:/bin", "HOME": "/nonexistent", "LANG": "C.UTF-8", "PYTHONUNBUFFERED": "1",
                    "PYTHONNOUSERSITE": "1", "PYTHONDONTWRITEBYTECODE": "1", "ALPHA_DISABLE_DOTENV": "1",
                    "ALPHA_RELEASE_SHA": candidate_sha, "ALPHA_CUTOVER_GENERATION": generation_id,
                    "ALPHA_RUNTIME_SOURCE": str(source)}
    if process_env != expected_env:
        fail("RUNTIME_PROCESS_ENV_IDENTITY_MISMATCH")
    status = Path(f"/proc/{pid}/status").read_text()
    uid_line = next((line for line in status.splitlines() if line.startswith("Uid:")), "")
    if not uid_line or any(int(value) != component["uid"] for value in uid_line.split()[1:]):
        fail("RUNTIME_PROCESS_UID_MISMATCH")
    gid_line = next((line for line in status.splitlines() if line.startswith("Gid:")), "")
    if not gid_line or any(int(value) != component["gid"] for value in gid_line.split()[1:]):
        fail("RUNTIME_PROCESS_GID_MISMATCH")
    groups_line = next((line for line in status.splitlines() if line.startswith("Groups:")), "")
    if {int(value) for value in groups_line.split()[1:]} - {component["gid"]}:
        fail("RUNTIME_PROCESS_GROUPS_MISMATCH")
    imports = _verify_import_environment(policy, source, venv, require_root=True)
    return {"pid": pid, "component": component_name, "environment_keys": sorted(process_env), "imports": imports}


def _remove_path(path: Path) -> None:
    if path.is_symlink() or path.is_file():
        path.unlink()
    elif path.exists():
        shutil.rmtree(path)


def _publish_directory(staged: Path, target: Path) -> None:
    """Publish a validated directory while preserving the old one on failure."""
    retired = target.parent / (".alpha-retired-" + os.urandom(16).hex())
    had_previous = target.exists() or target.is_symlink()
    if had_previous:
        os.replace(target, retired)
        _sync_directory(target.parent)
    try:
        os.replace(staged, target)
        _sync_directory(target.parent)
    except BaseException:
        if had_previous and not target.exists() and not target.is_symlink():
            os.replace(retired, target)
            _sync_directory(target.parent)
        raise
    _remove_path(retired)


@_serialized_mutation
def recover(generation_id: str, *, anchor: Path = ANCHOR, require_root: bool = True) -> None:
    policy = verify_self(anchor, require_root=require_root)["policy"]
    gdir, data = _load_generation(generation_id, policy, require_root=require_root)
    states = data["predecessor_units"]
    with _quiescent_writers(policy, stop=True, require_root=require_root):
        # A rejected recovery must remain stopped across reboot. A successful
        # unchanged-data rollback restores the captured enable/active state below.
        if require_root:
            for component in policy["components"].values():
                _run(["/usr/bin/systemctl", "disable", component["unit_name"]])
        db = Path(policy["db_path"])
        # Never erase post-cutover deliveries or fills. The execution journal is a
        # separate database and is NEVER restored, replaced, or deleted here.
        if _logical_db_digest(db) != data["predecessor_db_logical_sha256"]:
            fail("RECOVERY_DB_CHANGED_REQUIRES_OPERATOR_RECONCILIATION")
        if "execution" in policy["components"] and _logical_db_digest(Path(policy["execution_db_path"])) != data.get("predecessor_execution_journal_identity"):
            fail("RECOVERY_EXECUTION_JOURNAL_CHANGED_REQUIRES_FORWARD_RECOVERY")
        if _auxiliary_state_identities(policy) != data.get("predecessor_auxiliary_state_identities", {}):
            fail("RECOVERY_CONTROL_OR_SCANNER_STATE_CHANGED_REQUIRES_FORWARD_RECOVERY")
        app = Path(policy["app_dir"])
        recovery_root = Path(policy["runtime_root"]) / "recovery"
        recovery_root.mkdir(mode=0o700, parents=True, exist_ok=True)
        if require_root:
            _secure_root_dir(recovery_root)
        staging = Path(tempfile.mkdtemp(prefix=generation_id + "-", dir=recovery_root))
        temp_repo = staging / "checkout"
        _run(["/usr/bin/git", "-c", "core.hooksPath=/dev/null", "clone", "--no-checkout", "--template=",
              str(gdir / "predecessor.bundle"), str(temp_repo)])
        _git(temp_repo, "checkout", "--detach", "-f", data["predecessor_sha"])
        if _git(temp_repo, "rev-parse", "HEAD^{tree}") != data["predecessor_tree"]:
            fail("RECOVERY_TREE_MISMATCH")
        _git(temp_repo, "remote", "set-url", "origin", policy["repo_remote_url"])
        venvs = [{"path": data["predecessor_venv_path"], "archive": "predecessor-venv.tar",
                  "manifest": "predecessor-venv-manifest.json", "tree_sha256": data["predecessor_venv_tree_sha256"]},
                 *data.get("predecessor_extra_venvs", {}).values()]
        replacements = []
        for index, row in enumerate(venvs):
            original = Path(row["path"])
            if app in original.parents:
                target = temp_repo / original.relative_to(app)
                extraction = target
            elif Path(policy["runtime_root"]) in original.parents:
                target = original
                if target.is_dir() and not target.is_symlink() and _tree_digest(target) == row["tree_sha256"]:
                    continue
                extraction = staging / "venvs" / str(index) / target.name
                replacements.append((extraction, target))
            else:
                fail("RECOVERY_VENV_OUTSIDE_BOUND_ROOTS")
            if target.is_symlink():
                fail("RECOVERY_VENV_TARGET_INVALID")
            extraction.parent.mkdir(parents=True, exist_ok=True)
            _safe_extract_venv(gdir / row["archive"], extraction.parent, extraction.name, gdir / row["manifest"])
            if _tree_digest(extraction) != row["tree_sha256"]:
                fail("RECOVERY_VENV_TREE_MISMATCH")
        for extraction, target in replacements:
            _publish_directory(extraction, target)
        # Chown inside root-owned staging, before exposing the operator worktree.
        if require_root:
            for directory, dirs, files in os.walk(temp_repo):
                os.chown(directory, policy["deploy_uid"], policy["deploy_gid"])
                for name in dirs + files:
                    os.chown(Path(directory) / name, policy["deploy_uid"], policy["deploy_gid"], follow_symlinks=False)
        _publish_directory(temp_repo, app)
        Path(str(db) + "-wal").unlink(missing_ok=True)
        Path(str(db) + "-shm").unlink(missing_ok=True)
        if data["predecessor_db_present"]:
            signal_identity = policy["components"][_primary_component(policy)]
            _restore_database(gdir / "predecessor.sqlite3", db, signal_identity, require_root=require_root)
            if _logical_db_digest(db) != data["predecessor_db_logical_sha256"]:
                fail("RECOVERY_DB_LOGICAL_MISMATCH")
        else:
            db.unlink(missing_ok=True)
        for name, component in policy["components"].items():
            row = states[name]
            unit_file = Path(component["unit_file"])
            if row["present"]:
                _safe_write(unit_file, (gdir / row["file"]).read_bytes(), mode=0o644, root_custody=require_root)
            else:
                unit_file.unlink(missing_ok=True)
        release = Path(policy["release_file"])
        _safe_write(release, (data["predecessor_sha"] + "\n").encode(), mode=0o600, root_custody=False)
        if require_root:
            os.chown(release, policy["deploy_uid"], policy["deploy_gid"])
        active_release = Path(policy["runtime_root"]) / "active-release.json"
        previous_active = data.get("predecessor_active_release")
        if previous_active is None:
            active_release.unlink(missing_ok=True)
        else:
            _safe_write(active_release, _canonical(previous_active), mode=0o444, root_custody=require_root)
        if require_root:
            _run(["/usr/bin/systemctl", "daemon-reload"])
        shutil.rmtree(staging)
    # Release writer leases BEFORE restarting any predecessor process.
    if require_root:
        for name, component in policy["components"].items():
            if states[name]["enabled"]:
                _run(["/usr/bin/systemctl", "enable", component["unit_name"]])
            if states[name]["active"]:
                _run(["/usr/bin/systemctl", "start", component["unit_name"]])
    ACTIVE.unlink(missing_ok=True)


@_serialized_mutation
def finalize(generation_id: str, candidate_sha: str, *, anchor: Path = ANCHOR, require_root: bool = True) -> None:
    verified = verify_runtime_files(generation_id, candidate_sha, anchor=anchor, require_root=require_root)
    policy, manifest = verified["policy"], verified["manifest"]
    _load_generation(generation_id, policy, require_root=require_root)
    active_release = Path(policy["runtime_root"]) / "active-release.json"
    payload = {"version": "weather-paper-active-release-v3", "candidate_sha": candidate_sha,
               "generation_id": generation_id, "release_root": manifest["release_root"],
               "source_path": manifest["source_path"], "venv_path": manifest["venv_path"],
               "components": manifest["components"],
               "runtime_manifest_sha256": sha256_file(_runtime_manifest_path(policy, candidate_sha)),
               "finalized_at": time.time()}
    _safe_write(active_release, _canonical(payload), mode=0o444, root_custody=require_root)
    ACTIVE.unlink(missing_ok=True)


def _parser() -> argparse.ArgumentParser:
    parser=argparse.ArgumentParser(); subs=parser.add_subparsers(dest="cmd",required=True); subs.add_parser("authority-info")
    for name in ("create-cutover","prepare-candidate","activate-checkout","verify-generation","verify-runtime-files","verify-checkout","verify-process","finalize"):
        p=subs.add_parser(name)
        if name!="create-cutover": p.add_argument("--generation-id",required=True)
        p.add_argument("--candidate-sha",required=True)
        if name=="verify-process": p.add_argument("--component",choices=("signals","scanner","controller","execution"),default="signals")
    p=subs.add_parser("recover"); p.add_argument("--generation-id",required=True); subs.add_parser("verify-telegram-env"); return parser


def main() -> int:
    args=_parser().parse_args()
    try:
        if args.cmd=="authority-info":
            verified=verify_self(); print(json.dumps({"version":AUTHORITY_VERSION,"authority_sha256":authority_digest(),"policy_sha256":verified["policy_sha256"]},sort_keys=True))
        elif args.cmd=="create-cutover": print(create_cutover(args.candidate_sha))
        elif args.cmd=="prepare-candidate":
            result=prepare_candidate(args.generation_id,args.candidate_sha); print(json.dumps({"acceptance":"PASS_PREPARE_CANDIDATE","release_root":result["release_root"]},sort_keys=True))
        elif args.cmd=="activate-checkout": activate_checkout(args.generation_id,args.candidate_sha); print("PASS:activate-checkout")
        elif args.cmd=="verify-generation":
            verified=verify_self(); _,data=_load_generation(args.generation_id,verified["policy"])
            if data["candidate_sha"]!=_hex(args.candidate_sha,40,"CUTOVER_CANDIDATE_SHA_INVALID"): fail("CUTOVER_CANDIDATE_MISMATCH")
            print("PASS:verify-generation")
        elif args.cmd=="verify-runtime-files": verify_runtime_files(args.generation_id,args.candidate_sha); print("PASS:verify-runtime-files")
        elif args.cmd=="verify-checkout": verify_checkout(args.generation_id,args.candidate_sha); print("PASS:verify-checkout")
        elif args.cmd=="verify-process": print(json.dumps(verify_process(args.generation_id,args.candidate_sha,component_name=args.component),sort_keys=True))
        elif args.cmd=="verify-telegram-env": print(json.dumps(verify_telegram_env(),sort_keys=True))
        elif args.cmd=="recover": recover(args.generation_id); print("PASS:recover")
        elif args.cmd=="finalize": finalize(args.generation_id,args.candidate_sha); print("PASS:finalize")
        return 0
    except (AuthorityError,OSError,ValueError,json.JSONDecodeError,tarfile.TarError,subprocess.TimeoutExpired) as exc:
        print(f"FAIL_HOST_AUTHORITY:{exc}",file=sys.stderr); return 2


if __name__=="__main__": raise SystemExit(main())
