#!/usr/bin/env python3
from __future__ import annotations

"""Independently pinned host authority v3 for immutable weather-PAPER cutovers.

The application candidate may request operations by candidate SHA/generation ID only.
All privileged paths, deployment identity, runtime interpreter, and recovery policy
come from a root-owned anchor that is pinned independently from the application commit.
"""

import argparse
import hashlib
import json
import os
import re
import shutil
import sqlite3
import stat
import subprocess
import sys
import tarfile
import tempfile
import time
from pathlib import Path, PurePosixPath

AUTHORITY_VERSION = "weather-paper-host-authority-v3-immutable-runtime"
ANCHOR = Path("/etc/polymarket-weather-paper/authority-anchor-v3.json")
ACTIVE = Path("/etc/polymarket-weather-paper/active-cutover-v3.json")
DEFAULT_GENERATIONS = Path("/var/lib/polymarket-weather-paper-rollback/generations-v3")
FINAL_MODULE = "polymarket_scanner.weather_only_live_paper_all_signals_final_v10"
DENIED_DISTRIBUTIONS = {
    "py-clob-client", "py_clob_client", "web3", "eth-account", "eth_account",
    "eth-keys", "eth_keys", "coincurve", "brownie", "ape", "web3auth",
}
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
BOOTSTRAP_DISTRIBUTIONS = {"pip"}


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


def _abs(value: object, code: str) -> Path:
    path = Path(str(value or "")).expanduser()
    if not path.is_absolute() or ".." in path.parts:
        fail(code)
    return path


def _run(args: list[str], *, check: bool = True, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    clean = {"PATH": "/usr/bin:/bin", "HOME": "/nonexistent", "LANG": "C.UTF-8"}
    if env:
        clean.update(env)
    result = subprocess.run(
        args, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        check=False, env=clean
    )
    if check and result.returncode != 0:
        fail("AUTHORITY_COMMAND_FAILED:" + Path(args[0]).name)
    return result


def _git(repo: Path, *args: str) -> str:
    return _run(["/usr/bin/git", "-C", str(repo), *args]).stdout.strip()


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
    finally:
        tmp.unlink(missing_ok=True)


def authority_digest() -> str:
    return sha256_file(Path(__file__).resolve())


def _validate_policy(raw: dict) -> dict:
    required_paths = (
        "app_dir", "release_file", "unit_file", "db_path", "legacy_predecessor_venv",
        "runtime_root", "telegram_env_file", "runtime_python",
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
        if value < 0:
            fail("AUTHORITY_POLICY_UID_INVALID")
        out[key] = value
    out["unit_name"] = unit
    out["deploy_user"] = deploy_user
    out["repo_remote_url"] = remote
    return out


def verify_self(anchor: Path = ANCHOR, *, require_root: bool = True) -> dict:
    if anchor != ANCHOR and require_root:
        fail("AUTHORITY_ANCHOR_OVERRIDE_FORBIDDEN")
    if require_root:
        _root_custody(anchor)
    raw = _read_json(anchor)
    if raw.get("version") != AUTHORITY_VERSION:
        fail("AUTHORITY_VERSION_MISMATCH")
    expected = _hex(raw.get("authority_sha256"), 64, "AUTHORITY_DIGEST_INVALID")
    if authority_digest() != expected:
        fail("AUTHORITY_SELF_DIGEST_MISMATCH")
    policy = _validate_policy(raw.get("policy") if isinstance(raw.get("policy"), dict) else {})
    policy_digest = _sha_bytes(_canonical(policy))
    if raw.get("policy_sha256") != policy_digest:
        fail("AUTHORITY_POLICY_DIGEST_MISMATCH")
    return {"anchor": raw, "policy": policy, "policy_sha256": policy_digest}


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


def _logical_db_digest(path: Path) -> str:
    if not path.exists():
        return "ABSENT"
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=5.0)
    try:
        rows = list(con.iterdump())
    finally:
        con.close()
    return _sha_bytes(("\n".join(rows) + "\n").encode())


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


def _tree_entries(root: Path) -> list[dict]:
    base = root.parent
    rows: list[dict] = []
    todo = [root]
    while todo:
        path = todo.pop()
        st = path.lstat()
        rel = path.relative_to(base).as_posix()
        mode = stat.S_IMODE(st.st_mode)
        if stat.S_ISDIR(st.st_mode):
            rows.append({"path": rel, "kind": "dir", "mode": mode})
            todo.extend(sorted(path.iterdir(), reverse=True))
        elif stat.S_ISREG(st.st_mode):
            rows.append({"path": rel, "kind": "file", "mode": mode, "size": st.st_size, "sha256": sha256_file(path)})
        elif stat.S_ISLNK(st.st_mode):
            rows.append({"path": rel, "kind": "symlink", "mode": mode, "target": os.readlink(path)})
        else:
            fail("CUTOVER_TREE_FILE_TYPE_INVALID")
    return sorted(rows, key=lambda row: row["path"])


def _tree_digest(root: Path) -> str:
    return _sha_bytes(_canonical(_tree_entries(root)))


def _snapshot_venv(venv: Path, archive: Path, manifest: Path, *, root_custody: bool = True) -> tuple[str, str, str]:
    if not venv.is_dir() or venv.is_symlink() or not (venv / "bin/python").exists():
        fail("CUTOVER_PREDECESSOR_VENV_INVALID")
    before = _tree_entries(venv)
    with tarfile.open(archive, "w", format=tarfile.PAX_FORMAT, dereference=False) as tf:
        tf.add(venv, arcname=venv.name, recursive=True)
    after = _tree_entries(venv)
    if before != after:
        fail("CUTOVER_PREDECESSOR_VENV_CHANGED")
    tree_sha = _sha_bytes(_canonical(before))
    payload = {"version":"authority-v3-predecessor-venv","venv_name":venv.name,"tree_sha256":tree_sha,"entries":before,"archive_sha256":sha256_file(archive)}
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


def _safe_extract_venv(archive: Path, parent: Path, expected_name: str) -> None:
    with tarfile.open(archive, "r") as tf:
        members = tf.getmembers()
        for m in members:
            pure = PurePosixPath(m.name)
            if pure.is_absolute() or not pure.parts or pure.parts[0] != expected_name or ".." in pure.parts:
                fail("CUTOVER_VENV_ARCHIVE_PATH_INVALID")
            if not (m.isdir() or m.isfile() or m.issym()):
                fail("CUTOVER_VENV_ARCHIVE_TYPE_INVALID")
            if m.issym():
                target = PurePosixPath(m.linkname)
                if target.is_absolute() or ".." in target.parts:
                    fail("CUTOVER_VENV_ARCHIVE_SYMLINK_INVALID")
        tf.extractall(parent, members=members, filter="fully_trusted")


def _repo_bundle(repo: Path, commit: str, bundle: Path, generation_id: str) -> str:
    ref = f"refs/weather-paper-snapshot/{generation_id}"
    _run(["/usr/bin/git", "-C", str(repo), "update-ref", ref, commit])
    try:
        _run(["/usr/bin/git", "-C", str(repo), "bundle", "create", str(bundle), ref])
    finally:
        _run(["/usr/bin/git", "-C", str(repo), "update-ref", "-d", ref], check=False)
    _run(["/usr/bin/git", "bundle", "verify", str(bundle)])
    return sha256_file(bundle)


def _active_runtime(policy: dict) -> dict | None:
    path = Path(policy["runtime_root"]) / "active-release.json"
    if not path.exists():
        return None
    _root_custody(path)
    raw = _read_json(path)
    if raw.get("version") != "weather-paper-active-release-v3":
        fail("ACTIVE_RELEASE_MANIFEST_INVALID")
    return raw


def _predecessor_venv(policy: dict) -> Path:
    active = _active_runtime(policy)
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
    return gdir, data


def create_cutover(candidate_sha: str, *, anchor: Path = ANCHOR, require_root: bool = True) -> str:
    if require_root and os.geteuid() != 0:
        fail("CUTOVER_ROOT_REQUIRED")
    verified = verify_self(anchor, require_root=require_root)
    policy = verified["policy"]
    repo = Path(policy["app_dir"]).resolve(); release = Path(policy["release_file"])
    if ACTIVE.exists():
        fail("CUTOVER_ALREADY_ACTIVE")
    if repo.is_symlink() or not (repo / ".git").exists():
        fail("CUTOVER_APP_REPO_INVALID")
    predecessor = _hex(_git(repo, "rev-parse", "HEAD"), 40, "CUTOVER_PREDECESSOR_SHA_INVALID")
    marker = _hex(release.read_text().strip(), 40, "CUTOVER_RELEASE_MARKER_INVALID")
    if marker != predecessor:
        fail("CUTOVER_RELEASE_MARKER_MISMATCH")
    if _git(repo, "status", "--porcelain", "--untracked-files=all"):
        fail("CUTOVER_PREDECESSOR_DIRTY")
    predecessor_tree = _hex(_git(repo, "rev-parse", "HEAD^{tree}"), 40, "CUTOVER_PREDECESSOR_TREE_INVALID")
    candidate = _hex(candidate_sha, 40, "CUTOVER_CANDIDATE_SHA_INVALID")
    if candidate == predecessor:
        fail("CUTOVER_CANDIDATE_ALREADY_CURRENT")
    _run(["/usr/bin/git", "-C", str(repo), "cat-file", "-e", candidate + "^{commit}"])
    candidate_tree = _hex(_git(repo, "rev-parse", candidate + "^{tree}"), 40, "CUTOVER_CANDIDATE_TREE_INVALID")
    gid = _sha_bytes(f"{predecessor}:{candidate}:{time.time_ns()}:{os.urandom(32).hex()}".encode())
    root = _generation_root(policy); root.mkdir(parents=True, exist_ok=True); gdir = root / gid; gdir.mkdir(mode=0o700)
    unit_file = Path(policy["unit_file"])
    if unit_file.is_symlink() or not unit_file.is_file():
        fail("CUTOVER_PREDECESSOR_UNIT_INVALID")
    unit_copy = gdir / "predecessor-unit.service"; shutil.copyfile(unit_file, unit_copy)
    db_sha, db_logical = _backup_db(Path(policy["db_path"]), gdir / "predecessor.sqlite3")
    predecessor_venv = _predecessor_venv(policy)
    venv_archive_sha, venv_manifest_sha, venv_tree_sha = _snapshot_venv(predecessor_venv, gdir / "predecessor-venv.tar", gdir / "predecessor-venv-manifest.json", root_custody=require_root)
    bundle_sha = _repo_bundle(repo, predecessor, gdir / "predecessor.bundle", gid)
    now = time.time()
    data = {"version":"weather-paper-cutover-v3","generation_id":gid,"created_at":now,"sealed_at":now,"policy_sha256":verified["policy_sha256"],"predecessor_sha":predecessor,"predecessor_tree":predecessor_tree,"candidate_sha":candidate,"candidate_tree":candidate_tree,"predecessor_unit_sha256":sha256_file(unit_copy),"predecessor_db_present":db_sha!="ABSENT","predecessor_db_sha256":db_sha,"predecessor_db_logical_sha256":db_logical,"predecessor_venv_path":str(predecessor_venv.resolve()),"predecessor_venv_archive_sha256":venv_archive_sha,"predecessor_venv_manifest_sha256":venv_manifest_sha,"predecessor_venv_tree_sha256":venv_tree_sha,"predecessor_bundle_sha256":bundle_sha,"predecessor_enabled":_systemctl(policy["unit_name"],"is-enabled") if require_root else False,"predecessor_active":_systemctl(policy["unit_name"],"is-active") if require_root else False,"release_marker":marker,"deploy_user":policy["deploy_user"],"deploy_uid":policy["deploy_uid"],"deploy_gid":policy["deploy_gid"],"app_dir":policy["app_dir"],"release_file":policy["release_file"],"unit":policy["unit_name"],"unit_file":policy["unit_file"],"db_path":policy["db_path"],"authority_version":AUTHORITY_VERSION,"authority_sha256":authority_digest()}
    _safe_write(gdir / "manifest.json", _canonical(data), root_custody=require_root)
    _safe_write(ACTIVE, _canonical({"version":"weather-paper-active-cutover-v3","generation_id":gid,"predecessor_sha":predecessor,"candidate_sha":candidate,"created_at":now,"policy_sha256":verified["policy_sha256"]}), root_custody=require_root)
    if require_root:
        for path in gdir.iterdir():
            if path.is_file(): os.chmod(path,0o440); os.chown(path,0,0)
        os.chmod(gdir,0o550); os.chown(gdir,0,0)
    return gid


def _inventory(python: Path) -> list[dict]:
    code = "import json,importlib.metadata as m;print(json.dumps(sorted([{'name':d.metadata.get('Name',''),'version':d.version} for d in m.distributions()],key=lambda x:(x['name'].lower(),x['version']))))"
    value = json.loads(_run([str(python), "-I", "-s", "-E", "-c", code]).stdout)
    if not isinstance(value, list): fail("RUNTIME_INVENTORY_INVALID")
    return value


def _norm(name: object) -> str:
    return re.sub(r"[-_.]+", "-", str(name or "")).lower()


def _lock_names(lock: Path) -> set[str]:
    names: set[str] = set()
    for raw in lock.read_text(encoding="utf-8").splitlines():
        row = raw.split("#",1)[0].strip()
        if not row: continue
        match = re.match(r"^([A-Za-z0-9_.-]+)(?:\[[^]]+\])?==", row)
        if not match or "--hash=sha256:" not in row: fail("RUNTIME_LOCK_NOT_EXACT_HASHED")
        names.add(_norm(match.group(1)))
    return names


def _validate_inventory(rows: list[dict], lock: Path) -> None:
    names={_norm(row.get("name")) for row in rows}; expected=_lock_names(lock)
    extra=names-expected-{_norm(x) for x in BOOTSTRAP_DISTRIBUTIONS}; missing=expected-names
    if extra: fail("RUNTIME_UNEXPECTED_DISTRIBUTIONS:"+",".join(sorted(extra)))
    if missing: fail("RUNTIME_MISSING_DISTRIBUTIONS:"+",".join(sorted(missing)))
    denied=names & {_norm(x) for x in DENIED_DISTRIBUTIONS}
    if denied: fail("RUNTIME_FINANCIAL_DISTRIBUTION:"+",".join(sorted(denied)))


def _archive_candidate(repo: Path, candidate: str, destination: Path) -> None:
    archive=destination/"candidate-source.tar"
    with archive.open("wb") as fh:
        result=subprocess.run(["/usr/bin/git","-C",str(repo),"archive","--format=tar",candidate],stdout=fh,stderr=subprocess.PIPE,check=False,env={"PATH":"/usr/bin:/bin","HOME":"/nonexistent","LANG":"C.UTF-8"})
    if result.returncode != 0: fail("RUNTIME_GIT_ARCHIVE_FAILED")
    source=destination/"source"; source.mkdir(mode=0o755); _safe_extract_tar(archive,source,reject_symlinks=True); archive.unlink()
    for bad in ("sitecustomize.py","usercustomize.py"):
        if any(p.name.lower()==bad for p in source.rglob("*")): fail("RUNTIME_STARTUP_HOOK_FORBIDDEN:"+bad)
    if any(p.suffix.lower()==".pth" for p in source.rglob("*") if p.is_file()): fail("RUNTIME_SOURCE_PTH_FORBIDDEN")


def _verify_source_against_git(repo: Path, candidate: str, source: Path) -> str:
    listing=_run(["/usr/bin/git","-C",str(repo),"ls-tree","-r",candidate]).stdout.splitlines(); expected={}
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
        if _run(["/usr/bin/git","hash-object",str(path)]).stdout.strip()!=blob: fail("RUNTIME_SOURCE_BLOB_MISMATCH:"+name)
        executable=bool(path.stat().st_mode & stat.S_IXUSR)
        if executable!=(mode=="100755"): os.chmod(path,0o755 if mode=="100755" else 0o644)
        rows.append({"path":name,"mode":mode,"blob":blob})
    return _sha_bytes(_canonical(rows))


def _render_unit(policy: dict, generation_id: str, candidate: str, runtime: dict) -> str:
    root=Path(runtime["release_root"]); source=root/"source"; python=root/"venv/bin/python"; authority=Path(__file__).resolve(); state=str(Path(policy["db_path"]).parent); unset=" ".join(sorted(FORBIDDEN_PROCESS_ENV))
    return f"""[Unit]\nDescription=Polymarket final all-weather PAPER research runtime V10\nWants=network-online.target\nAfter=network-online.target\nStartLimitIntervalSec=600\nStartLimitBurst=3\n\n[Service]\nType=simple\nUser={policy['deploy_user']}\nWorkingDirectory={source}\nEnvironment=PYTHONUNBUFFERED=1\nEnvironment=PYTHONNOUSERSITE=1\nEnvironment=PYTHONDONTWRITEBYTECODE=1\nEnvironment=ALPHA_DISABLE_DOTENV=1\nEnvironment=ALPHA_RELEASE_SHA={candidate}\nEnvironment=ALPHA_CUTOVER_GENERATION={generation_id}\nEnvironment=ALPHA_RUNTIME_SOURCE={source}\nUnsetEnvironment={unset}\nEnvironmentFile={policy['telegram_env_file']}\nExecStartPre=+/usr/bin/python3 {authority} verify-runtime-files --generation-id {generation_id} --candidate-sha {candidate}\nExecStart=/usr/bin/env -i PATH=/usr/bin:/bin HOME=/nonexistent LANG=C.UTF-8 PYTHONUNBUFFERED=1 PYTHONNOUSERSITE=1 PYTHONDONTWRITEBYTECODE=1 ALPHA_DISABLE_DOTENV=1 ALPHA_RELEASE_SHA={candidate} ALPHA_CUTOVER_GENERATION={generation_id} ALPHA_RUNTIME_SOURCE={source} TELEGRAM_BOT_TOKEN=${{TELEGRAM_BOT_TOKEN}} TELEGRAM_CHAT_ID=${{TELEGRAM_CHAT_ID}} {python} -I -s -E -m {FINAL_MODULE} --db {policy['db_path']} --status {state}/status.json --release-file {policy['release_file']} --interval-seconds 180 --forecast-cache-seconds 900 --forecast-raw-gap-min 0.08 --max-forecast-events 6 --paper-stake-usd 10\nRestart=on-failure\nRestartSec=15\nTimeoutStopSec=20\nMemoryHigh=280M\nMemoryMax=350M\nMemorySwapMax=0\nTasksMax=48\nNice=5\nCPUWeight=70\nIOWeight=50\nNoNewPrivileges=true\nPrivateTmp=true\nPrivateDevices=true\nProtectHome=read-only\nProtectSystem=strict\nProtectKernelTunables=true\nProtectKernelModules=true\nProtectControlGroups=true\nRestrictSUIDSGID=true\nLockPersonality=true\nCapabilityBoundingSet=\nAmbientCapabilities=\nStateDirectory=polymarket-weather-paper\nStateDirectoryMode=0700\nReadWritePaths={state}\nUMask=0077\n\n[Install]\nWantedBy=multi-user.target\n"""


def _runtime_manifest_path(policy: dict, candidate: str) -> Path:
    return Path(policy["runtime_root"])/"releases"/candidate/"runtime-manifest.json"


def prepare_candidate(generation_id: str, candidate_sha: str, *, anchor: Path = ANCHOR, require_root: bool = True) -> dict:
    if require_root and os.geteuid()!=0: fail("CUTOVER_ROOT_REQUIRED")
    verified=verify_self(anchor,require_root=require_root); policy=verified["policy"]
    gdir,generation=_load_generation(generation_id,policy,require_root=require_root); candidate=_hex(candidate_sha,40,"CUTOVER_CANDIDATE_SHA_INVALID")
    if generation["candidate_sha"]!=candidate: fail("CUTOVER_CANDIDATE_MISMATCH")
    repo=Path(policy["app_dir"]).resolve()
    if _git(repo,"rev-parse",candidate+"^{tree}")!=generation["candidate_tree"]: fail("CUTOVER_CANDIDATE_TREE_MISMATCH")
    releases=Path(policy["runtime_root"])/"releases"; releases.mkdir(parents=True,exist_ok=True); release_root=releases/candidate
    if release_root.exists(): fail("RUNTIME_RELEASE_ALREADY_EXISTS")
    release_root.mkdir(mode=0o755); _archive_candidate(repo,candidate,release_root); source=release_root/"source"; source_git_sha=_verify_source_against_git(repo,candidate,source)
    module_file=source/"polymarket_scanner/weather_only_live_paper_all_signals_final_v10.py"
    if not module_file.is_file() or module_file.is_symlink(): fail("RUNTIME_FINAL_MODULE_MISSING")
    lock=source/"requirements-runtime-hashed.txt"
    if not lock.is_file() or lock.is_symlink(): fail("RUNTIME_LOCK_MISSING")
    lock_sha=sha256_file(lock); python=release_root/"venv/bin/python"
    _run([policy["runtime_python"],"-m","venv","--copies",str(release_root/"venv")]); _run([str(python),"-I","-s","-E","-m","pip","uninstall","-y","setuptools","wheel"],check=False)
    cfg=(release_root/"venv/pyvenv.cfg").read_text(encoding="utf-8").lower()
    if "include-system-site-packages = false" not in cfg: fail("RUNTIME_SYSTEM_SITE_ENABLED")
    _run([str(python),"-I","-s","-E","-m","pip","install","--disable-pip-version-check","--require-hashes","--no-deps","-r",str(lock)]); _run([str(python),"-I","-s","-E","-m","pip","check"])
    inventory=_inventory(python); _validate_inventory(inventory,lock)
    site_code="import json,site,sys;print(json.dumps({'site':site.getsitepackages(),'user':site.ENABLE_USER_SITE,'prefix':sys.prefix,'base':sys.base_prefix}))"; site_info=json.loads(_run([str(python),"-I","-s","-E","-c",site_code]).stdout)
    if site_info.get("user") is not False: fail("RUNTIME_USER_SITE_ENABLED")
    sites=[Path(x) for x in site_info.get("site") or []]
    if len(sites)!=1: fail("RUNTIME_SITE_PACKAGES_AMBIGUOUS")
    site_packages=sites[0]
    if release_root not in site_packages.resolve().parents: fail("RUNTIME_SITE_PACKAGES_OUTSIDE_RELEASE")
    for path in site_packages.rglob("*.pth"): path.unlink()
    if any(site_packages.rglob("sitecustomize.py")) or any(site_packages.rglob("usercustomize.py")): fail("RUNTIME_DEPENDENCY_STARTUP_HOOK_FORBIDDEN")
    (site_packages/"alpha-reviewed-source.pth").write_text(str(source.resolve())+"\n",encoding="utf-8")
    manifest={"version":"weather-paper-runtime-release-v3","generation_id":generation_id,"candidate_sha":candidate,"candidate_tree":generation["candidate_tree"],"release_root":str(release_root.resolve()),"source_path":str(source.resolve()),"venv_path":str((release_root/"venv").resolve()),"source_git_manifest_sha256":source_git_sha,"source_tree_sha256":_tree_digest(source),"venv_tree_sha256":_tree_digest(release_root/"venv"),"requirements_lock_sha256":lock_sha,"distributions":inventory,"distribution_inventory_sha256":_sha_bytes(_canonical(inventory)),"user_site_enabled":False,"system_site_packages":False,"python_flags":["-I","-s","-E"],"final_module":FINAL_MODULE,"financial_authority":False,"automatic_order_placement":False,"wallet_or_order_api_loaded":False,"prepared_at":time.time()}
    manifest_path=release_root/"runtime-manifest.json"; _safe_write(manifest_path,_canonical(manifest),mode=0o444,root_custody=require_root)
    unit_text=_render_unit(policy,generation_id,candidate,manifest); unit_copy=gdir/"candidate-unit.service"; _safe_write(unit_copy,unit_text.encode(),mode=0o440,root_custody=require_root)
    evidence={"version":"weather-paper-runtime-evidence-v3","generation_id":generation_id,"candidate_sha":candidate,"runtime_manifest_sha256":sha256_file(manifest_path),"runtime_source_tree_sha256":manifest["source_tree_sha256"],"runtime_venv_tree_sha256":manifest["venv_tree_sha256"],"candidate_unit_sha256":sha256_file(unit_copy),"requirements_lock_sha256":lock_sha,"sealed_at":time.time()}; _safe_write(gdir/"candidate-runtime-evidence.json",_canonical(evidence),root_custody=require_root)
    shutil.copyfile(unit_copy,Path(policy["unit_file"])); os.chmod(Path(policy["unit_file"]),0o644)
    if require_root:
        for root,dirs,files in os.walk(release_root):
            os.chown(root,0,0); os.chmod(root,0o755)
            for name in files:
                p=Path(root)/name
                if not p.is_symlink(): os.chown(p,0,0); os.chmod(p,0o555 if p.stat().st_mode & stat.S_IXUSR else 0o444)
        os.chown(Path(policy["unit_file"]),0,0); _run(["/usr/bin/systemctl","daemon-reload"])
    return manifest


def verify_runtime_files(generation_id: str, candidate_sha: str, *, anchor: Path = ANCHOR, require_root: bool = True) -> dict:
    verified=verify_self(anchor,require_root=require_root); policy=verified["policy"]; gdir,generation=_load_generation(generation_id,policy,require_root=require_root,require_active=False); candidate=_hex(candidate_sha,40,"CUTOVER_CANDIDATE_SHA_INVALID")
    if generation["candidate_sha"]!=candidate: fail("CUTOVER_CANDIDATE_MISMATCH")
    evidence_path=gdir/"candidate-runtime-evidence.json"; unit_copy=gdir/"candidate-unit.service"; manifest_path=_runtime_manifest_path(policy,candidate)
    if require_root:
        for p in (evidence_path,unit_copy,manifest_path): _root_custody(p)
    evidence=_read_json(evidence_path); manifest=_read_json(manifest_path)
    if evidence.get("candidate_sha")!=candidate or manifest.get("candidate_sha")!=candidate: fail("RUNTIME_EVIDENCE_CANDIDATE_MISMATCH")
    if sha256_file(manifest_path)!=evidence.get("runtime_manifest_sha256"): fail("RUNTIME_MANIFEST_DIGEST_MISMATCH")
    source=Path(manifest["source_path"]); venv=Path(manifest["venv_path"]); release_root=Path(manifest["release_root"]); expected_root=Path(policy["runtime_root"])/"releases"/candidate
    if release_root.resolve()!=expected_root.resolve(): fail("RUNTIME_RELEASE_ROOT_MISMATCH")
    if _tree_digest(source)!=evidence.get("runtime_source_tree_sha256"): fail("RUNTIME_SOURCE_TREE_MISMATCH")
    if _tree_digest(venv)!=evidence.get("runtime_venv_tree_sha256"): fail("RUNTIME_VENV_TREE_MISMATCH")
    lock=source/"requirements-runtime-hashed.txt"
    if sha256_file(lock)!=evidence.get("requirements_lock_sha256"): fail("RUNTIME_LOCK_DIGEST_MISMATCH")
    inventory=_inventory(venv/"bin/python")
    if inventory!=manifest.get("distributions"): fail("RUNTIME_INVENTORY_MISMATCH")
    _validate_inventory(inventory,lock)
    if sha256_file(unit_copy)!=evidence.get("candidate_unit_sha256"): fail("RUNTIME_UNIT_EVIDENCE_MISMATCH")
    if Path(policy["unit_file"]).exists() and sha256_file(Path(policy["unit_file"]))!=evidence.get("candidate_unit_sha256"): fail("RUNTIME_INSTALLED_UNIT_MISMATCH")
    return {"policy":policy,"generation":generation,"manifest":manifest,"evidence":evidence}


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


def activate_checkout(generation_id: str, candidate_sha: str, *, anchor: Path = ANCHOR, require_root: bool = True) -> None:
    if require_root and os.geteuid()!=0: fail("CUTOVER_ROOT_REQUIRED")
    verified=verify_runtime_files(generation_id,candidate_sha,anchor=anchor,require_root=require_root); policy=verified["policy"]; generation=verified["generation"]; repo=Path(policy["app_dir"]); candidate=generation["candidate_sha"]
    _run(["/usr/bin/git","-C",str(repo),"checkout","--detach","-f",candidate])
    if _git(repo,"rev-parse","HEAD^{tree}")!=generation["candidate_tree"]: fail("CANDIDATE_CHECKOUT_TREE_MISMATCH")
    if _git(repo,"status","--porcelain","--untracked-files=all"): fail("CANDIDATE_CHECKOUT_DIRTY")
    release=Path(policy["release_file"]); _safe_write(release,(candidate+"\n").encode(),mode=0o600,root_custody=False)
    if require_root: os.chown(release,policy["deploy_uid"],policy["deploy_gid"])


def verify_checkout(generation_id: str, candidate_sha: str, *, anchor: Path = ANCHOR, require_root: bool = True) -> None:
    verified=verify_self(anchor,require_root=require_root); policy=verified["policy"]; _,generation=_load_generation(generation_id,policy,require_root=require_root); candidate=_hex(candidate_sha,40,"CUTOVER_CANDIDATE_SHA_INVALID")
    if generation["candidate_sha"]!=candidate: fail("CUTOVER_CANDIDATE_MISMATCH")
    repo=Path(policy["app_dir"])
    if _git(repo,"rev-parse","HEAD")!=candidate: fail("CANDIDATE_CHECKOUT_IDENTITY_MISMATCH")
    if _git(repo,"rev-parse","HEAD^{tree}")!=generation["candidate_tree"]: fail("CANDIDATE_CHECKOUT_TREE_MISMATCH")
    if _git(repo,"status","--porcelain","--untracked-files=all"): fail("CANDIDATE_CHECKOUT_DIRTY")
    if Path(policy["release_file"]).read_text().strip()!=candidate: fail("CANDIDATE_RELEASE_MARKER_MISMATCH")


def verify_process(generation_id: str, candidate_sha: str, *, anchor: Path = ANCHOR) -> dict:
    verified=verify_runtime_files(generation_id,candidate_sha,anchor=anchor,require_root=True); policy=verified["policy"]; manifest=verified["manifest"]; unit=policy["unit_name"]
    if not _systemctl(unit,"is-active"): fail("RUNTIME_SERVICE_NOT_ACTIVE")
    raw_pid=_run(["/usr/bin/systemctl","show",unit,"--property","MainPID","--value"]).stdout.strip()
    try: pid=int(raw_pid)
    except ValueError: fail("RUNTIME_PID_INVALID")
    if pid<=1: fail("RUNTIME_PID_INVALID")
    exe=Path(os.readlink(f"/proc/{pid}/exe")).resolve(); expected_python=(Path(manifest["venv_path"])/"bin/python").resolve()
    if exe!=expected_python: fail("RUNTIME_PROCESS_EXECUTABLE_MISMATCH")
    cwd=Path(os.readlink(f"/proc/{pid}/cwd")).resolve()
    if cwd!=Path(manifest["source_path"]).resolve(): fail("RUNTIME_PROCESS_CWD_MISMATCH")
    argv=[x.decode("utf-8") for x in Path(f"/proc/{pid}/cmdline").read_bytes().split(b"\0") if x]
    if not argv or Path(argv[0]).resolve()!=expected_python or "-I" not in argv or FINAL_MODULE not in argv: fail("RUNTIME_PROCESS_ARGV_MISMATCH")
    env={}
    for item in Path(f"/proc/{pid}/environ").read_bytes().split(b"\0"):
        if b"=" in item:
            k,v=item.split(b"=",1); env[k.decode("utf-8")]=v.decode("utf-8")
    leaked=set(env)&FORBIDDEN_PROCESS_ENV; unexpected=set(env)-ALLOWED_PROCESS_ENV
    if leaked: fail("RUNTIME_PROCESS_FORBIDDEN_ENV:"+",".join(sorted(leaked)))
    if unexpected: fail("RUNTIME_PROCESS_UNEXPECTED_ENV:"+",".join(sorted(unexpected)))
    if env.get("PYTHONNOUSERSITE")!="1" or env.get("ALPHA_RELEASE_SHA")!=candidate_sha or env.get("ALPHA_CUTOVER_GENERATION")!=generation_id or env.get("ALPHA_RUNTIME_SOURCE")!=manifest["source_path"]: fail("RUNTIME_PROCESS_ENV_IDENTITY_MISMATCH")
    return {"pid":pid,"executable":str(exe),"cwd":str(cwd),"environment_keys":sorted(env)}


def recover(generation_id: str, *, anchor: Path = ANCHOR, require_root: bool = True) -> None:
    if require_root and os.geteuid()!=0: fail("CUTOVER_ROOT_REQUIRED")
    verified=verify_self(anchor,require_root=require_root); policy=verified["policy"]; gdir,data=_load_generation(generation_id,policy,require_root=require_root); unit=policy["unit_name"]
    if require_root:
        _run(["/usr/bin/systemctl","stop",unit],check=False); _run(["/usr/bin/systemctl","disable",unit],check=False)
    app=Path(policy["app_dir"]); bundle=gdir/"predecessor.bundle"; tmp_repo=app.with_name("."+app.name+".recovery")
    if tmp_repo.exists(): shutil.rmtree(tmp_repo)
    _run(["/usr/bin/git","clone","--no-checkout",str(bundle),str(tmp_repo)]); _run(["/usr/bin/git","-C",str(tmp_repo),"checkout","--detach","-f",data["predecessor_sha"]])
    if _git(tmp_repo,"rev-parse","HEAD^{tree}")!=data["predecessor_tree"]: fail("RECOVERY_TREE_MISMATCH")
    _run(["/usr/bin/git","-C",str(tmp_repo),"remote","set-url","origin",policy["repo_remote_url"]])
    if app.exists() or app.is_symlink(): app.unlink() if app.is_symlink() else shutil.rmtree(app)
    os.replace(tmp_repo,app)
    if require_root:
        for root,dirs,files in os.walk(app):
            os.chown(root,policy["deploy_uid"],policy["deploy_gid"])
            for name in dirs: os.chown(Path(root)/name,policy["deploy_uid"],policy["deploy_gid"])
            for name in files:
                p=Path(root)/name
                if not p.is_symlink(): os.chown(p,policy["deploy_uid"],policy["deploy_gid"])
    db=Path(policy["db_path"]); Path(str(db)+"-wal").unlink(missing_ok=True); Path(str(db)+"-shm").unlink(missing_ok=True)
    if data["predecessor_db_present"]:
        source=gdir/"predecessor.sqlite3"; tmp=db.with_name("."+db.name+".restore"); shutil.copyfile(source,tmp); os.replace(tmp,db); os.chmod(db,0o600)
        if _logical_db_digest(db)!=data["predecessor_db_logical_sha256"]: fail("RECOVERY_DB_LOGICAL_MISMATCH")
    else: db.unlink(missing_ok=True)
    predecessor_venv=Path(data["predecessor_venv_path"])
    if predecessor_venv.exists() or predecessor_venv.is_symlink():
        if predecessor_venv.is_symlink() or not predecessor_venv.is_dir(): fail("RECOVERY_VENV_TARGET_INVALID")
        shutil.rmtree(predecessor_venv)
    _safe_extract_venv(gdir/"predecessor-venv.tar",predecessor_venv.parent,predecessor_venv.name)
    if _tree_digest(predecessor_venv)!=data["predecessor_venv_tree_sha256"]: fail("RECOVERY_VENV_TREE_MISMATCH")
    release=Path(policy["release_file"]); _safe_write(release,(data["predecessor_sha"]+"\n").encode(),mode=0o600,root_custody=False)
    if require_root: os.chown(release,policy["deploy_uid"],policy["deploy_gid"])
    shutil.copyfile(gdir/"predecessor-unit.service",Path(policy["unit_file"])); os.chmod(Path(policy["unit_file"]),0o644)
    if require_root:
        os.chown(Path(policy["unit_file"]),0,0); _run(["/usr/bin/systemctl","daemon-reload"])
        if data["predecessor_enabled"]: _run(["/usr/bin/systemctl","enable",unit])
        if data["predecessor_active"]: _run(["/usr/bin/systemctl","start",unit])
    ACTIVE.unlink(missing_ok=True)


def finalize(generation_id: str, candidate_sha: str, *, anchor: Path = ANCHOR, require_root: bool = True) -> None:
    if require_root and os.geteuid()!=0: fail("CUTOVER_ROOT_REQUIRED")
    verified=verify_runtime_files(generation_id,candidate_sha,anchor=anchor,require_root=require_root); policy=verified["policy"]; manifest=verified["manifest"]; active_release=Path(policy["runtime_root"])/"active-release.json"
    _safe_write(active_release,_canonical({"version":"weather-paper-active-release-v3","candidate_sha":candidate_sha,"generation_id":generation_id,"release_root":manifest["release_root"],"source_path":manifest["source_path"],"venv_path":manifest["venv_path"],"runtime_manifest_sha256":sha256_file(_runtime_manifest_path(policy,candidate_sha)),"finalized_at":time.time()}),mode=0o444,root_custody=require_root); ACTIVE.unlink(missing_ok=True)


def _parser() -> argparse.ArgumentParser:
    parser=argparse.ArgumentParser(); subs=parser.add_subparsers(dest="cmd",required=True); subs.add_parser("authority-info")
    for name in ("create-cutover","prepare-candidate","activate-checkout","verify-generation","verify-runtime-files","verify-checkout","verify-process","finalize"):
        p=subs.add_parser(name)
        if name!="create-cutover": p.add_argument("--generation-id",required=True)
        p.add_argument("--candidate-sha",required=True)
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
        elif args.cmd=="verify-process": print(json.dumps(verify_process(args.generation_id,args.candidate_sha),sort_keys=True))
        elif args.cmd=="verify-telegram-env": print(json.dumps(verify_telegram_env(),sort_keys=True))
        elif args.cmd=="recover": recover(args.generation_id); print("PASS:recover")
        elif args.cmd=="finalize": finalize(args.generation_id,args.candidate_sha); print("PASS:finalize")
        return 0
    except (AuthorityError,OSError,ValueError,json.JSONDecodeError,tarfile.TarError) as exc:
        print(f"FAIL_HOST_AUTHORITY:{exc}",file=sys.stderr); return 2


if __name__=="__main__": raise SystemExit(main())
