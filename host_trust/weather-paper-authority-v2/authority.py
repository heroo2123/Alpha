#!/usr/bin/env python3
from __future__ import annotations

"""Independently pinned root host authority for weather PAPER cutovers.

This program is NOT installed from an application candidate.  The reference source is
kept here for review/reproducibility, but bootstrap requires an independently supplied
SHA-256 before any root installation.  Ordinary candidate scripts may submit release
data to the installed authority, never replace its implementation or expected digest.
"""

import argparse
import hashlib
import json
import os
import secrets
import shutil
import sqlite3
import stat
import subprocess
import sys
import tarfile
import tempfile
import time
from pathlib import Path, PurePosixPath

AUTHORITY_VERSION = "weather-paper-host-authority-v2-independent-cutover"
DEFAULT_ANCHOR = Path("/etc/polymarket-weather-paper/authority-anchor-v2.json")
DEFAULT_GENERATIONS = Path("/var/lib/polymarket-weather-paper-rollback/generations")
DEFAULT_ACTIVE = Path("/etc/polymarket-weather-paper/active-cutover-v2.json")
DENIED_DISTRIBUTIONS = {
    "py-clob-client", "py_clob_client", "web3", "eth-account", "eth_account",
    "eth-keys", "eth_keys", "coincurve", "brownie", "ape", "web3auth",
}

class AuthorityError(RuntimeError):
    pass

def fail(code: str) -> None:
    raise AuthorityError(code)

def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()

def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()

def canonical_json(value: object) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True) + "\n").encode()

def regular_nosymlink(path: Path) -> os.stat_result:
    st = path.lstat()
    if not stat.S_ISREG(st.st_mode) or path.is_symlink():
        fail("AUTHORITY_FILE_NOT_REGULAR")
    return st

def secure_root_file(path: Path) -> os.stat_result:
    st = regular_nosymlink(path)
    if st.st_uid != 0 or st.st_mode & 0o022:
        fail("AUTHORITY_FILE_CUSTODY_INVALID")
    return st

def read_json(path: Path) -> dict:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise AuthorityError("AUTHORITY_JSON_INVALID") from exc
    if not isinstance(raw, dict):
        fail("AUTHORITY_JSON_INVALID")
    return raw

def hexid(value: object, n: int, code: str) -> str:
    text = str(value or "").strip().lower()
    if len(text) != n or any(c not in "0123456789abcdef" for c in text):
        fail(code)
    return text

def run(args: list[str], *, check: bool = True, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    clean = {"PATH": "/usr/bin:/bin", "HOME": "/root", "LANG": "C.UTF-8"}
    if env:
        clean.update(env)
    p = subprocess.run(args, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False, env=clean)
    if check and p.returncode != 0:
        fail("AUTHORITY_COMMAND_FAILED")
    return p

def git(repo: Path, *args: str) -> str:
    return run(["/usr/bin/git", "-C", str(repo), *args]).stdout.strip()

def authority_digest() -> str:
    return sha256_file(Path(__file__).resolve())

def verify_self(anchor: Path = DEFAULT_ANCHOR, *, require_root: bool = True) -> dict:
    if require_root:
        secure_root_file(anchor)
    raw = read_json(anchor)
    if raw.get("version") != AUTHORITY_VERSION:
        fail("AUTHORITY_VERSION_MISMATCH")
    expected = hexid(raw.get("authority_sha256"), 64, "AUTHORITY_DIGEST_INVALID")
    actual = authority_digest()
    if actual != expected:
        fail("AUTHORITY_SELF_DIGEST_MISMATCH")
    return raw

def _safe_write_root(path: Path, data: bytes, mode: int = 0o440) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmpname = tempfile.mkstemp(prefix="." + path.name + ".", dir=path.parent)
    tmp = Path(tmpname)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data); f.flush(); os.fsync(f.fileno())
        os.chmod(tmp, mode)
        if os.geteuid() == 0:
            os.chown(tmp, 0, 0)
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)

def logical_db_digest(path: Path) -> str:
    if not path.exists():
        return "ABSENT"
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=5.0)
    try:
        rows = list(con.iterdump())
    finally:
        con.close()
    return sha256_bytes(("\n".join(rows) + "\n").encode())

def backup_db(source: Path, target: Path) -> tuple[str, str]:
    if not source.exists():
        return "ABSENT", "ABSENT"
    tmp = target.with_name("." + target.name + ".tmp")
    src = sqlite3.connect(f"file:{source}?mode=ro", uri=True, timeout=5.0)
    dst = sqlite3.connect(tmp, timeout=5.0)
    try:
        src.execute("PRAGMA busy_timeout=5000"); src.backup(dst, pages=256, sleep=0.01)
    finally:
        dst.close(); src.close()
    check = sqlite3.connect(f"file:{tmp}?mode=ro", uri=True, timeout=5.0)
    try:
        if check.execute("PRAGMA quick_check").fetchall() != [("ok",)]:
            fail("CUTOVER_DB_BACKUP_INVALID")
    finally:
        check.close()
    os.replace(tmp, target)
    return sha256_file(target), logical_db_digest(target)

def tree_entries(root: Path) -> list[dict]:
    base = root.parent
    rows: list[dict] = []
    pending = [root]
    while pending:
        p = pending.pop()
        st = p.lstat(); rel = p.relative_to(base).as_posix(); mode = stat.S_IMODE(st.st_mode)
        if stat.S_ISDIR(st.st_mode):
            rows.append({"path": rel, "kind": "dir", "mode": mode})
            pending.extend(sorted(p.iterdir(), reverse=True))
        elif stat.S_ISREG(st.st_mode):
            rows.append({"path": rel, "kind": "file", "mode": mode, "size": st.st_size, "sha256": sha256_file(p)})
        elif stat.S_ISLNK(st.st_mode):
            rows.append({"path": rel, "kind": "symlink", "mode": mode, "target": os.readlink(p)})
        else:
            fail("CUTOVER_TREE_FILE_TYPE_INVALID")
    return sorted(rows, key=lambda r: r["path"])

def tree_digest(root: Path) -> str:
    return sha256_bytes(canonical_json(tree_entries(root)))

def snapshot_venv(venv: Path, archive: Path, manifest: Path) -> tuple[str, str]:
    if not venv.is_dir() or venv.is_symlink() or not (venv / "bin/python").exists():
        fail("CUTOVER_PREDECESSOR_VENV_INVALID")
    before = tree_entries(venv)
    with tarfile.open(archive, "w", format=tarfile.PAX_FORMAT, dereference=False) as tf:
        tf.add(venv, arcname=venv.name, recursive=True)
    after = tree_entries(venv)
    if before != after:
        fail("CUTOVER_PREDECESSOR_VENV_CHANGED")
    payload = {"version": "authority-v2-venv-exact-tree", "venv_name": venv.name,
               "tree_sha256": sha256_bytes(canonical_json(before)), "entries": before,
               "archive_sha256": sha256_file(archive)}
    _safe_write_root(manifest, canonical_json(payload))
    return payload["archive_sha256"], sha256_file(manifest)

def safe_extract_venv(archive: Path, parent: Path, expected_name: str) -> None:
    with tarfile.open(archive, "r") as tf:
        members = tf.getmembers()
        for m in members:
            pure = PurePosixPath(m.name)
            if pure.is_absolute() or not pure.parts or pure.parts[0] != expected_name or ".." in pure.parts:
                fail("CUTOVER_VENV_ARCHIVE_PATH_INVALID")
            if not (m.isdir() or m.isfile() or m.issym()):
                fail("CUTOVER_VENV_ARCHIVE_TYPE_INVALID")
        tf.extractall(parent, members=members, filter="fully_trusted")

def systemctl_state(unit: str, verb: str) -> bool:
    return run(["/usr/bin/systemctl", verb, "--quiet", unit], check=False).returncode == 0

def _generation_dir(root: Path, generation_id: str) -> Path:
    gid = hexid(generation_id, 64, "CUTOVER_GENERATION_ID_INVALID")
    p = root.resolve() / gid
    if p.parent != root.resolve(): fail("CUTOVER_GENERATION_PATH_INVALID")
    return p

def load_generation(generation_id: str, root: Path, active: Path, *, require_root: bool = True) -> tuple[Path, dict]:
    gdir = _generation_dir(root, generation_id)
    manifest = gdir / "manifest.json"
    if require_root:
        secure_root_file(manifest); secure_root_file(active)
    data = read_json(manifest); act = read_json(active)
    if data.get("version") != "weather-paper-cutover-v2" or data.get("generation_id") != generation_id:
        fail("CUTOVER_GENERATION_MANIFEST_INVALID")
    if act.get("generation_id") != generation_id:
        fail("CUTOVER_ACTIVE_GENERATION_MISMATCH")
    if data.get("predecessor_sha") == data.get("candidate_sha"):
        fail("CUTOVER_PREDECESSOR_EQUALS_CANDIDATE")
    for name, key in (("predecessor-unit.service","predecessor_unit_sha256"),
                      ("predecessor-venv.tar","predecessor_venv_archive_sha256"),
                      ("predecessor-venv-manifest.json","predecessor_venv_manifest_sha256")):
        p = gdir / name
        if require_root: secure_root_file(p)
        if sha256_file(p) != str(data.get(key) or ""):
            fail("CUTOVER_PAYLOAD_DIGEST_MISMATCH")
    if data.get("predecessor_db_present"):
        db = gdir / "predecessor.sqlite3"
        if require_root: secure_root_file(db)
        if sha256_file(db) != data.get("predecessor_db_sha256"):
            fail("CUTOVER_DB_DIGEST_MISMATCH")
        if logical_db_digest(db) != data.get("predecessor_db_logical_sha256"):
            fail("CUTOVER_DB_LOGICAL_DIGEST_MISMATCH")
    return gdir, data

def create_cutover(args) -> str:
    if os.geteuid() != 0: fail("CUTOVER_ROOT_REQUIRED")
    verify_self(args.anchor)
    repo = args.app_dir.resolve(); release = args.release_file.resolve()
    if args.active.exists(): fail("CUTOVER_ALREADY_ACTIVE")
    predecessor = hexid(git(repo, "rev-parse", "HEAD"), 40, "CUTOVER_PREDECESSOR_SHA_INVALID")
    marker = hexid(release.read_text().strip(), 40, "CUTOVER_RELEASE_MARKER_INVALID")
    if marker != predecessor: fail("CUTOVER_RELEASE_MARKER_MISMATCH")
    predecessor_tree = hexid(git(repo, "rev-parse", "HEAD^{tree}"), 40, "CUTOVER_PREDECESSOR_TREE_INVALID")
    candidate = hexid(args.candidate_sha, 40, "CUTOVER_CANDIDATE_SHA_INVALID")
    candidate_tree = hexid(args.candidate_tree, 40, "CUTOVER_CANDIDATE_TREE_INVALID")
    if candidate == predecessor: fail("CUTOVER_CANDIDATE_ALREADY_CURRENT")
    if git(repo, "rev-parse", f"{candidate}^{{tree}}") != candidate_tree: fail("CUTOVER_CANDIDATE_TREE_MISMATCH")
    if git(repo, "status", "--porcelain", "--untracked-files=all"): fail("CUTOVER_PREDECESSOR_DIRTY")
    args.generations.mkdir(parents=True, exist_ok=True)
    entropy = f"{predecessor}:{candidate}:{time.time_ns()}:{secrets.token_hex(32)}".encode()
    gid = sha256_bytes(entropy); gdir = args.generations / gid
    gdir.mkdir(mode=0o700)
    unit_copy = gdir / "predecessor-unit.service"; shutil.copyfile(args.unit_file, unit_copy)
    db_sha, db_logical = backup_db(args.db_path, gdir / "predecessor.sqlite3")
    venv_archive_sha, venv_manifest_sha = snapshot_venv(args.predecessor_venv, gdir / "predecessor-venv.tar", gdir / "predecessor-venv-manifest.json")
    now = time.time()
    data = {
        "version": "weather-paper-cutover-v2", "generation_id": gid,
        "created_at": now, "sealed_at": now,
        "predecessor_sha": predecessor, "predecessor_tree": predecessor_tree,
        "candidate_sha": candidate, "candidate_tree": candidate_tree,
        "predecessor_unit_sha256": sha256_file(unit_copy),
        "predecessor_db_present": db_sha != "ABSENT", "predecessor_db_sha256": db_sha,
        "predecessor_db_logical_sha256": db_logical,
        "predecessor_venv_path": str(args.predecessor_venv.resolve()),
        "predecessor_venv_archive_sha256": venv_archive_sha,
        "predecessor_venv_manifest_sha256": venv_manifest_sha,
        "predecessor_venv_tree_sha256": tree_digest(args.predecessor_venv.resolve()),
        "predecessor_enabled": systemctl_state(args.unit, "is-enabled"),
        "predecessor_active": systemctl_state(args.unit, "is-active"),
        "release_marker": marker, "deploy_user": args.deploy_user,
        "app_dir": str(repo), "release_file": str(release), "unit": args.unit,
        "unit_file": str(args.unit_file.resolve()), "db_path": str(args.db_path.resolve()),
        "authority_version": AUTHORITY_VERSION, "authority_sha256": authority_digest(),
    }
    _safe_write_root(gdir / "manifest.json", canonical_json(data))
    _safe_write_root(args.active, canonical_json({"version":"weather-paper-active-cutover-v2","generation_id":gid,
                                                   "predecessor_sha":predecessor,"candidate_sha":candidate,"created_at":now}))
    for p in gdir.iterdir():
        if p.is_file(): os.chmod(p, 0o440); os.chown(p, 0, 0)
    os.chmod(gdir, 0o550); os.chown(gdir, 0, 0)
    return gid

def _inventory(venv_python: Path) -> list[dict]:
    code = "import json,importlib.metadata as m; print(json.dumps(sorted([{'name':d.metadata.get('Name',''),'version':d.version} for d in m.distributions()], key=lambda x:(x['name'].lower(),x['version']))))"
    p = run([str(venv_python), "-I", "-s", "-E", "-c", code])
    value = json.loads(p.stdout)
    if not isinstance(value, list): fail("CANDIDATE_ENV_INVENTORY_INVALID")
    return value

def seal_candidate_environment(args) -> None:
    if os.geteuid() != 0: fail("CUTOVER_ROOT_REQUIRED")
    verify_self(args.anchor)
    gdir, data = load_generation(args.generation_id, args.generations, args.active)
    candidate = hexid(args.candidate_sha, 40, "CUTOVER_CANDIDATE_SHA_INVALID")
    if data["candidate_sha"] != candidate: fail("CUTOVER_CANDIDATE_MISMATCH")
    evidence = gdir / "candidate-environment.json"
    if evidence.exists(): fail("CUTOVER_CANDIDATE_ENV_ALREADY_SEALED")
    manifest_path = args.environment_manifest.resolve(); manifest = read_json(manifest_path)
    if manifest.get("release_sha") != candidate: fail("CANDIDATE_ENV_RELEASE_MISMATCH")
    venv = Path(manifest.get("venv_path") or "").resolve()
    expected = (args.app_dir.resolve() / ".releases" / candidate / "venv").resolve()
    if venv != expected: fail("CANDIDATE_ENV_PATH_MISMATCH")
    observed_tree = tree_digest(venv)
    if observed_tree != manifest.get("venv_tree_sha256"): fail("CANDIDATE_ENV_TREE_MISMATCH")
    inventory = _inventory(venv / "bin/python")
    if inventory != manifest.get("distributions"): fail("CANDIDATE_ENV_INVENTORY_MISMATCH")
    installed = {str(x.get("name") or "").lower().replace("_","-") for x in inventory}
    if {x.replace("_","-") for x in DENIED_DISTRIBUTIONS} & installed:
        fail("CANDIDATE_ENV_FINANCIAL_PACKAGE_PRESENT")
    payload = {"version":"weather-paper-candidate-environment-v2", "generation_id":args.generation_id,
               "candidate_sha":candidate, "manifest_sha256":sha256_file(manifest_path),
               "venv_tree_sha256":observed_tree, "inventory_sha256":sha256_bytes(canonical_json(inventory)),
               "sealed_at":time.time()}
    _safe_write_root(evidence, canonical_json(payload)); os.chmod(evidence,0o440); os.chown(evidence,0,0)

def verify_candidate_environment(args) -> None:
    verify_self(args.anchor)
    gdir, data = load_generation(args.generation_id, args.generations, args.active)
    candidate = hexid(args.candidate_sha,40,"CUTOVER_CANDIDATE_SHA_INVALID")
    if data["candidate_sha"] != candidate: fail("CUTOVER_CANDIDATE_MISMATCH")
    ev = gdir / "candidate-environment.json"; secure_root_file(ev); payload = read_json(ev)
    manifest = args.environment_manifest.resolve()
    if payload.get("candidate_sha") != candidate or sha256_file(manifest) != payload.get("manifest_sha256"):
        fail("CANDIDATE_ENV_EVIDENCE_MISMATCH")
    raw = read_json(manifest); venv = Path(raw.get("venv_path") or "").resolve()
    expected = (args.app_dir.resolve()/".releases"/candidate/"venv").resolve()
    if venv != expected or tree_digest(venv) != payload.get("venv_tree_sha256"):
        fail("CANDIDATE_ENV_TREE_MISMATCH")
    if _inventory(venv/"bin/python") != raw.get("distributions"):
        fail("CANDIDATE_ENV_INVENTORY_MISMATCH")

def verify_generation_cmd(args) -> None:
    verify_self(args.anchor)
    _, data = load_generation(args.generation_id, args.generations, args.active)
    if args.candidate_sha and data["candidate_sha"] != hexid(args.candidate_sha,40,"CUTOVER_CANDIDATE_SHA_INVALID"):
        fail("CUTOVER_CANDIDATE_MISMATCH")
    repo = args.app_dir.resolve()
    candidate_tree = git(repo,"rev-parse",f"{data['candidate_sha']}^{{tree}}")
    predecessor_tree = git(repo,"rev-parse",f"{data['predecessor_sha']}^{{tree}}")
    if candidate_tree != data["candidate_tree"] or predecessor_tree != data["predecessor_tree"]:
        fail("CUTOVER_GIT_OBJECT_MISMATCH")

def verify_checkout(args) -> None:
    verify_self(args.anchor)
    _, data = load_generation(args.generation_id,args.generations,args.active)
    head = git(args.app_dir.resolve(),"rev-parse","HEAD"); marker=args.release_file.read_text().strip()
    if head != data["candidate_sha"] or marker != head: fail("CANDIDATE_CHECKOUT_IDENTITY_MISMATCH")
    if git(args.app_dir.resolve(),"rev-parse","HEAD^{tree}") != data["candidate_tree"]: fail("CANDIDATE_CHECKOUT_TREE_MISMATCH")
    if git(args.app_dir.resolve(),"status","--porcelain","--untracked-files=all"): fail("CANDIDATE_CHECKOUT_DIRTY")

def recover(args) -> None:
    if os.geteuid() != 0: fail("CUTOVER_ROOT_REQUIRED")
    verify_self(args.anchor); gdir,data=load_generation(args.generation_id,args.generations,args.active)
    unit=data["unit"]; run(["/usr/bin/systemctl","stop",unit],check=False); run(["/usr/bin/systemctl","disable",unit],check=False)
    repo=Path(data["app_dir"]); run(["/usr/bin/git","-C",str(repo),"checkout","--detach","-f",data["predecessor_sha"]])
    if git(repo,"rev-parse","HEAD^{tree}") != data["predecessor_tree"]: fail("RECOVERY_TREE_MISMATCH")
    db=Path(data["db_path"]); db.parent.mkdir(parents=True,exist_ok=True)
    Path(str(db)+"-wal").unlink(missing_ok=True); Path(str(db)+"-shm").unlink(missing_ok=True)
    if data["predecessor_db_present"]:
        source=gdir/"predecessor.sqlite3"; tmp=db.with_name("."+db.name+".restore")
        shutil.copyfile(source,tmp); os.replace(tmp,db); os.chmod(db,0o600)
        if logical_db_digest(db) != data["predecessor_db_logical_sha256"]: fail("RECOVERY_DB_LOGICAL_MISMATCH")
    else: db.unlink(missing_ok=True)
    venv=Path(data["predecessor_venv_path"]); parent=venv.parent
    if venv.exists() or venv.is_symlink():
        if venv.is_symlink() or not venv.is_dir(): fail("RECOVERY_VENV_TARGET_INVALID")
        shutil.rmtree(venv)
    safe_extract_venv(gdir/"predecessor-venv.tar",parent,venv.name)
    if tree_digest(venv) != data["predecessor_venv_tree_sha256"]: fail("RECOVERY_VENV_TREE_MISMATCH")
    release=Path(data["release_file"]); _safe_write_root(release,(data["predecessor_sha"]+"\n").encode(),0o600)
    # Preserve deploy ownership of the release marker when run as root.
    if args.deploy_uid is not None: os.chown(release,int(args.deploy_uid),int(args.deploy_gid))
    shutil.copyfile(gdir/"predecessor-unit.service",Path(data["unit_file"])); os.chmod(Path(data["unit_file"]),0o644)
    run(["/usr/bin/systemctl","daemon-reload"])
    if data["predecessor_enabled"]: run(["/usr/bin/systemctl","enable",unit])
    if data["predecessor_active"]: run(["/usr/bin/systemctl","start",unit])
    args.active.unlink()

def parser() -> argparse.ArgumentParser:
    p=argparse.ArgumentParser(); sub=p.add_subparsers(dest="cmd",required=True)
    q=sub.add_parser("authority-info"); q.add_argument("--anchor",type=Path,default=DEFAULT_ANCHOR)
    for name in ("verify-generation","verify-checkout","verify-candidate-environment"):
        q=sub.add_parser(name); q.add_argument("--anchor",type=Path,default=DEFAULT_ANCHOR); q.add_argument("--generations",type=Path,default=DEFAULT_GENERATIONS); q.add_argument("--active",type=Path,default=DEFAULT_ACTIVE); q.add_argument("--generation-id",required=True); q.add_argument("--app-dir",type=Path,required=True); q.add_argument("--candidate-sha");
        if name=="verify-checkout": q.add_argument("--release-file",type=Path,required=True)
        if name=="verify-candidate-environment": q.add_argument("--environment-manifest",type=Path,required=True)
    q=sub.add_parser("create-cutover"); q.add_argument("--anchor",type=Path,default=DEFAULT_ANCHOR); q.add_argument("--generations",type=Path,default=DEFAULT_GENERATIONS); q.add_argument("--active",type=Path,default=DEFAULT_ACTIVE); q.add_argument("--app-dir",type=Path,required=True); q.add_argument("--release-file",type=Path,required=True); q.add_argument("--candidate-sha",required=True); q.add_argument("--candidate-tree",required=True); q.add_argument("--unit",default="polymarket-weather-paper.service"); q.add_argument("--unit-file",type=Path,required=True); q.add_argument("--db-path",type=Path,required=True); q.add_argument("--predecessor-venv",type=Path,required=True); q.add_argument("--deploy-user",required=True)
    q=sub.add_parser("seal-candidate-environment"); q.add_argument("--anchor",type=Path,default=DEFAULT_ANCHOR); q.add_argument("--generations",type=Path,default=DEFAULT_GENERATIONS); q.add_argument("--active",type=Path,default=DEFAULT_ACTIVE); q.add_argument("--generation-id",required=True); q.add_argument("--candidate-sha",required=True); q.add_argument("--app-dir",type=Path,required=True); q.add_argument("--environment-manifest",type=Path,required=True)
    q=sub.add_parser("recover"); q.add_argument("--anchor",type=Path,default=DEFAULT_ANCHOR); q.add_argument("--generations",type=Path,default=DEFAULT_GENERATIONS); q.add_argument("--active",type=Path,default=DEFAULT_ACTIVE); q.add_argument("--generation-id",required=True); q.add_argument("--deploy-uid",type=int); q.add_argument("--deploy-gid",type=int)
    return p

def main() -> int:
    args=parser().parse_args()
    try:
        if args.cmd=="authority-info":
            raw=verify_self(args.anchor); print(json.dumps({"version":AUTHORITY_VERSION,"authority_sha256":authority_digest(),"anchor":raw},sort_keys=True)); return 0
        if args.cmd=="create-cutover": print(create_cutover(args)); return 0
        if args.cmd=="verify-generation": verify_generation_cmd(args)
        elif args.cmd=="verify-checkout": verify_checkout(args)
        elif args.cmd=="seal-candidate-environment": seal_candidate_environment(args)
        elif args.cmd=="verify-candidate-environment": verify_candidate_environment(args)
        elif args.cmd=="recover": recover(args)
        print("PASS:"+args.cmd); return 0
    except (AuthorityError,OSError,ValueError,tarfile.TarError,json.JSONDecodeError) as exc:
        print(f"FAIL_HOST_AUTHORITY:{exc}",file=sys.stderr); return 2
if __name__=="__main__": raise SystemExit(main())
