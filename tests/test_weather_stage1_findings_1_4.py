from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
DEPLOY = ROOT / "deploy"
SHA_A = "a" * 40
SHA_B = "b" * 40
GEN = "c" * 32


def load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def git(repo: Path, *args: str) -> str:
    cp = subprocess.run(["git", "-C", str(repo), *args], text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)
    return cp.stdout.strip()


def init_repo(tmp_path: Path) -> tuple[Path, str, dict[str, str]]:
    repo = tmp_path / "repo"; repo.mkdir()
    git(repo, "init"); git(repo, "config", "user.email", "stage1@example.invalid"); git(repo, "config", "user.name", "Stage1")
    protected = (
        "deploy/weather-paper-host-release-gate.py", "deploy/weather-paper-host-recovery.sh",
        "deploy/weather-paper-host-snapshot.sh", "deploy/weather-paper-venv-snapshot.py",
        "deploy/install-weather-paper-host-trust.sh",
    )
    for p in protected:
        q=repo/p; q.parent.mkdir(parents=True,exist_ok=True); q.write_text("trusted-"+p+"\n")
    (repo/"app.txt").write_text("A\n"); git(repo,"add","."); git(repo,"commit","-m","A")
    a=git(repo,"rev-parse","HEAD"); blobs={p:git(repo,"rev-parse",f"{a}:{p}") for p in protected}
    return repo,a,blobs


def test_finding1_candidate_modifies_release_gate_rejected(tmp_path: Path) -> None:
    gate=load(DEPLOY/"weather-paper-host-release-gate.py","stage1_gate_a")
    repo,a,blobs=init_repo(tmp_path); p=repo/"deploy/weather-paper-host-release-gate.py"; p.write_text("candidate replacement\n")
    git(repo,"add",str(p.relative_to(repo))); git(repo,"commit","-m","replace release gate"); b=git(repo,"rev-parse","HEAD")
    with pytest.raises(SystemExit, match="HOST_RELEASE_CANDIDATE_REDEFINES_AUTHORITY"):
        gate.verify_candidate_authority_files(repo,b,{"protected_candidate_blobs":blobs})


def test_finding1_candidate_modifies_recovery_rejected(tmp_path: Path) -> None:
    gate=load(DEPLOY/"weather-paper-host-release-gate.py","stage1_gate_b")
    repo,a,blobs=init_repo(tmp_path); p=repo/"deploy/weather-paper-host-recovery.sh"; p.write_text("candidate replacement\n")
    git(repo,"add",str(p.relative_to(repo))); git(repo,"commit","-m","replace recovery"); b=git(repo,"rev-parse","HEAD")
    with pytest.raises(SystemExit, match="HOST_RELEASE_CANDIDATE_REDEFINES_AUTHORITY"):
        gate.verify_candidate_authority_files(repo,b,{"protected_candidate_blobs":blobs})


def test_finding1_branch_movement_and_new_candidate_do_not_replace_authority(tmp_path: Path) -> None:
    gate=load(DEPLOY/"weather-paper-host-release-gate.py","stage1_gate_c")
    repo,a,blobs=init_repo(tmp_path)
    (repo/"app.txt").write_text("B\n"); git(repo,"add","app.txt"); git(repo,"commit","-m","B app only"); b=git(repo,"rev-parse","HEAD")
    git(repo,"branch","moving",a); git(repo,"branch","-f","moving",b)
    gate.verify_candidate_authority_files(repo,b,{"protected_candidate_blobs":blobs})
    assert git(repo,"rev-parse","moving") == b
    assert all(git(repo,"rev-parse",f"{b}:{p}")==blob for p,blob in blobs.items())


def test_finding1_external_authority_is_digest_pinned_and_not_replaceable() -> None:
    text=(DEPLOY/"install-weather-paper-host-trust.sh").read_text()
    assert "--authority-source" in text and "--bundle-sha256" in text and "--bootstrap" in text
    assert "authority source may not reside in candidate application tree" in text
    assert "host authority already installed; bootstrap refuses replacement" in text
    assert "git -C \"${APP_DIR}\" show" not in text
    gate=(DEPLOY/"weather-paper-host-release-gate.py").read_text()
    assert "verify_authority" in gate and "HOST_AUTHORITY_FILE_DIGEST_MISMATCH" in gate


def test_finding1_recovery_survives_corrupt_candidate_worktree() -> None:
    text=(DEPLOY/"weather-paper-host-recovery.sh").read_text()
    assert 'reset --hard "${PREDECESSOR_SHA}"' in text
    assert "clean -ffdx" in text
    assert "predecessor.git.bundle" in text


def test_finding2_immutable_generation_binds_required_state_and_second_snapshot_fails() -> None:
    snap=(DEPLOY/"weather-paper-host-snapshot.sh").read_text()
    required=("generation_id","predecessor_sha","predecessor_tree","candidate_sha","candidate_tree","predecessor_unit_digest","predecessor_db_digest","predecessor_venv_digest","venv_manifest_digest","active","enabled","release_marker","deploy_user","app_dir","creation_timestamp")
    for name in required: assert repr(name) in snap or f"'{name}'" in snap
    assert '[[ "${PREDECESSOR_SHA}" != "${CANDIDATE_SHA}" ]]' in snap
    assert "cutover generation for candidate already exists" in snap
    assert 'GENERATIONS="${ROLLBACK_DIR}/generations"' in snap
    assert "snapshot-generation-v4" not in snap
    recovery=(DEPLOY/"weather-paper-host-recovery.sh").read_text()
    assert '--generation-id' in recovery and '${ROLLBACK_DIR}/generations/${GENERATION_ID}' in recovery


def test_finding2_state_machine_a_snapshot_b_failure_restores_only_a() -> None:
    prepare=(DEPLOY/"prepare-weather-paper-candidate.sh").read_text()
    assert prepare.index('"${HOST_SNAPSHOT}" --candidate-sha') < prepare.index('checkout --detach "${RELEASE_SHA}"')
    start=(DEPLOY/"start-weather-paper-candidate.sh").read_text()
    assert 'verify-generation --generation-id "${EXPECTED_GENERATION}" --sha "${EXPECTED_SHA}"' in start
    assert '"${HOST_RECOVERY}" --generation-id "${EXPECTED_GENERATION}"' in start
    recovery=(DEPLOY/"weather-paper-host-recovery.sh").read_text()
    assert 'checkout --detach "${PREDECESSOR_SHA}"' in recovery
    assert '"${PREDECESSOR_TREE}"' in recovery and '"${PREVIOUS_ACTIVE}"' in recovery and '"${PREVIOUS_ENABLED}"' in recovery


def tree_digest(path: Path) -> str:
    h=hashlib.sha256()
    for p in sorted(path.rglob("*")):
        rel=str(p.relative_to(path)); h.update(rel.encode()+b"\0")
        if p.is_file(): h.update(p.read_bytes())
        elif p.is_symlink(): h.update(os.readlink(p).encode())
    return h.hexdigest()


def test_finding3_fresh_candidate_env_cannot_see_predecessor_injected_or_removed_dependency(tmp_path: Path) -> None:
    import venv
    predecessor=tmp_path/".venv"; candidate=tmp_path/".releases"/SHA_B/"venv"
    venv.EnvBuilder(with_pip=False).create(predecessor)
    pred_site=next(predecessor.glob("lib/python*/site-packages")); pred_site.mkdir(parents=True,exist_ok=True)
    (pred_site/"injected_only.py").write_text("VALUE=1\n"); (pred_site/"removed_dependency.py").write_text("VALUE=2\n")
    before=tree_digest(predecessor)
    venv.EnvBuilder(with_pip=False,system_site_packages=False).create(candidate)
    env={"PATH":str(candidate/"bin")+":/usr/bin:/bin","PYTHONNOUSERSITE":"1","HOME":str(tmp_path)}
    for mod in ("injected_only","removed_dependency"):
        cp=subprocess.run([str(candidate/"bin/python"),"-E","-s","-c",f"import {mod}"],env=env,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
        assert cp.returncode != 0
    assert tree_digest(predecessor)==before


def test_finding3_manifest_is_exact_and_forbidden_trading_packages_fail(tmp_path: Path) -> None:
    mod=load(DEPLOY/"weather-paper-release-venv.py","stage1_venv")
    lock=tmp_path/"lock.txt"; lock.write_text("alpha==1.0 --hash=sha256:"+"0"*64+"\n")
    v=tmp_path/"venv"; (v/"bin").mkdir(parents=True); (v/"bin/python").symlink_to(sys.executable)
    payload=mod.canonical_manifest(v,lock,[{"name":"alpha","version":"1.0"},{"name":"pip","version":"1"}])
    assert payload["distributions"][0]["name"]=="alpha" and len(payload["manifest_sha256"])==64
    with pytest.raises(SystemExit,match="RELEASE_VENV_FORBIDDEN_DISTRIBUTION"):
        mod.canonical_manifest(v,lock,[{"name":"alpha","version":"1.0"},{"name":"web3","version":"7"}])


def test_finding3_builder_requires_empty_release_and_hashed_lock() -> None:
    text=(DEPLOY/"prepare-weather-paper-candidate.sh").read_text()
    assert '.releases/${RELEASE_SHA}' in text and '[[ ! -e "${RELEASE_ROOT}" ]]' in text
    builder=(DEPLOY/"weather-paper-release-venv.py").read_text()
    assert 'system_site_packages=False' in builder and '"--require-hashes"' in builder and '"--no-deps"' in builder
    assert "RELEASE_VENV_UNEXPECTED_DISTRIBUTION" in builder


@pytest.mark.parametrize("bad",["PYTHONPATH","PYTHONHOME","PYTHONUSERBASE","PYTHONSTARTUP","PYTHONINSPECT","LD_PRELOAD","LD_LIBRARY_PATH"])
def test_finding4_malicious_loader_environment_fails_closed(tmp_path: Path,bad: str) -> None:
    attest=load(DEPLOY/"attest-weather-paper-runtime.py","stage1_attest_"+bad.lower())
    app=tmp_path/"app"; py=app/".releases"/SHA_B/"venv"/"bin"/"python"; py.parent.mkdir(parents=True); py.symlink_to(sys.executable)
    argv=(str(py),"-E","-s","-m",attest.FINAL_WEATHER_MODULE,"--db","/tmp/db")
    env={"PYTHONNOUSERSITE":"1",bad:"/tmp/evil"}
    with pytest.raises(RuntimeError,match="STRICT_PROCESS_FORBIDDEN_ENV"):
        attest.strict_process_checks(os.getpid(),argv,env,app,SHA_B)


def test_finding4_unit_strips_loader_environment_and_uses_release_interpreter(tmp_path: Path) -> None:
    render=load(DEPLOY/"render-weather-paper-unit.py","stage1_render")
    app=tmp_path/"app"; cfg=tmp_path/"cfg"; app.mkdir(); cfg.mkdir()
    text=render.render(app,cfg,"runner",SHA_B)
    assert f"ExecStart={app}/.releases/{SHA_B}/venv/bin/python -E -s -m" in text
    assert "Environment=PYTHONNOUSERSITE=1" in text
    unset=" ".join(line.split("=",1)[1] for line in text.splitlines() if line.startswith("UnsetEnvironment="))
    for name in ("PYTHONPATH","PYTHONHOME","PYTHONUSERBASE","PYTHONSTARTUP","PYTHONINSPECT","LD_PRELOAD","LD_LIBRARY_PATH"): assert name in unset


def test_finding4_attestor_reads_proc_environ_and_probes_sys_path_user_site_modules() -> None:
    text=(DEPLOY/"attest-weather-paper-runtime.py").read_text()
    assert '/proc/{pid}/environ' in text and 'site.ENABLE_USER_SITE' in text and 'sys.path' in text
    assert 'module_locations' in text and 'sys.prefix' in text
