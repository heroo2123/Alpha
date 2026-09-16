"""Three independent UIDs/venvs and rollback custody; temporary reference only."""
from copy import deepcopy
import json
from pathlib import Path
import sqlite3
import shutil
import subprocess

import pytest

from test_host_authority_production_boundary import host, prepare, cutover


@pytest.fixture
def panel_host(host):
    policy=host.policy
    controller=policy["components"].pop("signals")
    controller.update(command="controller",user="controller")
    policy["components"]["controller"]=controller
    scan_dir=host.root/"scanner-state"; scan_dir.mkdir()
    policy.update(scanner_db_path=str(scan_dir/"scanner.sqlite"),scanner_status_path=str(scan_dir/"status.json"),
        operator_db_path=str(host.db.parent/"operator.sqlite"),telegram_file=str(host.root/"telegram.json"),
        execution_credentials_file=str(host.root/"credentials.json"),execution_activation_file=str(host.root/"activation.json"))
    for key in ("scanner_db_path","operator_db_path"):
        with sqlite3.connect(policy[key]) as db: db.execute("CREATE TABLE original_state(value TEXT)")
    scanner=deepcopy(controller)
    scanner.update(command="scanner",user="scanner",uid=1004,unit_file=str(host.root/"scanner.service"),unit_name="scanner.service",config_file=str(host.root/"scanner.json"))
    Path(scanner["unit_file"]).write_text("[Service]\nDescription=Stopped predecessor scanner\n")
    policy["components"]["scanner"]=scanner
    policy["writer_lock_paths"].append(policy["scanner_db_path"]+".writer.lock")
    raw=json.loads(Path(controller["config_file"]).read_text())
    raw.update(telegram_file=policy["telegram_file"],credentials_file=policy["execution_credentials_file"],activation_file=policy["execution_activation_file"],
        operator_control={"version":1,"db":policy["operator_db_path"],"scanner_db":policy["scanner_db_path"],"scanner_status":policy["scanner_status_path"]})
    for component in policy["components"].values(): Path(component["config_file"]).write_text(json.dumps(raw))
    path=Path(policy["approval_file"]); approvals=json.loads(path.read_text())
    for record in approvals["approved"]:
        signal=record["components"].pop("signals")
        record["components"].update(controller=signal,scanner=deepcopy(signal))
    path.write_text(json.dumps(approvals))
    return host


def test_three_role_policy_uses_independent_paths_and_fresh_environments(panel_host):
    host=panel_host
    assert set(host.m._validate_policy(host.policy)["components"])=={"scanner","controller","execution"}
    gid,manifest=prepare(host)
    assert len({x["venv_path"] for x in manifest["components"].values()})==3
    for name in ("scanner","controller"):
        assert manifest["components"][name]["lock_file"]=="requirements-runtime-hashed.txt"
        assert manifest["components"][name]["distributions"]==[{"name":"demo","version":"1.0"}]
    for name in manifest["components"]:
        text=Path(host.policy["components"][name]["unit_file"]).read_text()
        assert f"-m polymarket_scanner.production {name} --config" in text
        assert "EnvironmentFile=" not in text and "PYTHONPATH=" not in text
        if name!="controller": assert "InaccessiblePaths=-"+host.policy["telegram_file"] in text
        if name!="execution": assert host.policy["execution_credentials_file"] in text
    assert host.m.verify_runtime_files(gid,host.b,require_root=False)["manifest"]==manifest


@pytest.mark.parametrize("kind",["uid","directory","config_path","lock","mixed"])
def test_role_boundary_confusion_rejected(panel_host,kind):
    host=panel_host; policy=deepcopy(host.policy)
    if kind=="uid": policy["components"]["scanner"]["uid"]=policy["components"]["controller"]["uid"]
    elif kind=="directory": policy["scanner_db_path"]=str(host.db.parent/"scanner.sqlite")
    elif kind=="lock": policy["components"]["controller"]["lock_file"]="requirements-execution-hashed.txt"
    elif kind=="mixed": policy["components"]["signals"]=policy["components"]["controller"]
    else:
        for comp in policy["components"].values():
            path=Path(comp["config_file"]); raw=json.loads(path.read_text()); raw["operator_control"]["db"]=str(host.root/"outside.sqlite"); path.write_text(json.dumps(raw))
        with pytest.raises(host.m.AuthorityError): host.m._component_configurations(policy,require_root=False)
        return
    with pytest.raises(host.m.AuthorityError): host.m._validate_policy(policy)


@pytest.mark.parametrize("key",["operator_db_path","scanner_db_path"])
def test_recovery_never_rewinds_control_or_scanner_cursor(panel_host,key):
    host=panel_host; gid=cutover(host)
    with sqlite3.connect(host.policy[key]) as db: db.execute("INSERT INTO original_state VALUES('post-cutover')")
    with pytest.raises(host.m.AuthorityError,match="CONTROL_OR_SCANNER_STATE_CHANGED"):
        host.m.recover(gid,require_root=False)
    with sqlite3.connect(host.policy[key]) as db: assert db.execute("SELECT value FROM original_state").fetchone()[0]=="post-cutover"


def test_second_snapshot_cannot_replace_predecessor_for_three_roles(panel_host):
    host=panel_host; gid,manifest=prepare(host)
    host.m.activate_checkout(gid,host.b,require_root=False)
    with pytest.raises(host.m.AuthorityError): cutover(host)
    host.m.recover(gid,require_root=False)
    assert (host.app/"version.txt").read_text()=="A\n"


def test_systemd_validates_all_three_isolated_units(panel_host):
    verifier=shutil.which("systemd-analyze")
    if not verifier:
        pytest.skip("systemd-analyze unavailable; target acceptance remains required")
    prepare(panel_host)
    result=subprocess.run([verifier,"verify","--man=no",*[r["unit_file"] for r in panel_host.policy["components"].values()]],
        env={"PATH":"/usr/bin:/bin","SYSTEMD_LOG_LEVEL":"err"},capture_output=True,text=True,timeout=30)
    assert result.returncode==0,result.stderr
