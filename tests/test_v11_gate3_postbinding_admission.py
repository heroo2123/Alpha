"""Offline negative control for public Gate-3 byte checks and G3-L admission.

The projection below belongs only to this test. It never grants authority.
"""

import copy
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import socket
import subprocess

import pytest

from tools import validate_g3l_original_terminal_candidate as terminal
from tools import v11_gate3_current_executable_binding as binding
from tools import v11_r09_gate3_g3l_identity_audit as audit_module
from tools import v11_r09_gate3_g3l_prep as prep
from tools.v11_multimodel_panel import canonical
from tools.v11_r09_gate3_launch import LaunchContractError
from tools.v11_r09_gate3_launch_v4 import validate_manifest_v4


REPO = Path(__file__).resolve().parents[1]
ARGS = dict(target_date="2026-10-04", now_utc=1790989200,
            free_disk_bytes=3_000_000_000,
            available_memory_bytes=1_000_000_000)


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def forbidden(*_args, **_kwargs):
        raise AssertionError("socket access in offline postbinding test")

    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket.socket, "connect_ex", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    for key, value in {
        "GIT_NO_LAZY_FETCH": "1", "GIT_ALLOW_PROTOCOL": "",
        "GIT_PROTOCOL_FROM_USER": "0", "GIT_TERMINAL_PROMPT": "0",
        "GIT_NO_REPLACE_OBJECTS": "1", "GIT_GRAFT_FILE": os.devnull,
        "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull,
    }.items():
        monkeypatch.setenv(key, value)


@pytest.fixture
def checkout(tmp_path):
    root = tmp_path / "checkout"
    root.mkdir()
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    objects = subprocess.check_output(
        ["git", "-C", str(REPO), "rev-parse", "--path-format=absolute",
         "--git-path", "objects"], text=True).strip()
    (root / ".git/objects/info/alternates").write_text(objects + "\n")
    head = subprocess.check_output(["git", "-C", str(REPO), "rev-parse", "HEAD"],
                                   text=True).strip()
    subprocess.run(["git", "-C", str(root), "update-ref", "refs/heads/main", head],
                   check=True)
    subprocess.run(["git", "-C", str(root), "symbolic-ref", "HEAD", "refs/heads/main"],
                   check=True)
    tracked = subprocess.check_output(["git", "-C", str(REPO), "ls-files", "-z"])
    for raw in tracked.split(b"\0"):
        if not raw:
            continue
        name = os.fsdecode(raw)
        source, target = REPO / name, root / name
        if source.is_file() and not source.is_symlink():
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)
    return root


def _inventory():
    start = prep.freeze_checklist(ARGS["target_date"])["window_start_utc"]
    return prep.make_report(target_date=ARGS["target_date"], run_utc=start - 14 * 3600,
                            free_disk=ARGS["free_disk_bytes"],
                            available_memory=ARGS["available_memory_bytes"],
                            observed_utc=ARGS["now_utc"])["inventory_template"]


def _project(byte_result, terminal_result, audit_result, inventory, object_root):
    """Test-local admission projection: every path returns HOLD."""
    hold = {"status": "HOLD", "qualification_credit": 0, "launchable": False}
    if byte_result != {
        "source_commit": binding.SOURCE_COMMIT, "source_tree": binding.SOURCE_TREE,
        "verified_files": 92, "launchable": False, "qualification_credit": 0,
    }:
        return {**hold, "reason": "binding result refused"}
    if (type(terminal_result) is not dict or set(terminal_result) !=
            {"document", "report", "terminal"} or
            any(type(value) is not str or re.fullmatch(r"[0-9a-f]{64}", value) is None
                for value in terminal_result.values())):
        return {**hold, "reason": "historical terminal result refused"}
    if (type(audit_result) is not dict or
        audit_result.get("schema") != "R09_GATE3_G3L_LOCAL_IDENTITY_AUDIT_V1" or
        audit_result.get("launchable") is not False or
        type(audit_result.get("qualification_credit")) is not int or
        audit_result["qualification_credit"] != 0 or
        audit_result.get("g3l") != "NO-GO" or
        audit_result.get("category_counts") != {
            audit_module.RETAINED_SCOPED: 6, audit_module.OFFLINE: 1,
            audit_module.FUTURE: 70, audit_module.INVALID: 0,
        } or audit_result.get("final_only_unassembled") != list(prep.FINAL_ONLY_IDS) or
        type(audit_result.get("identities")) is not dict or
        set(audit_result["identities"]) != set(prep.PRE_REVIEW_IDS) or
        any(type(row) is not dict or row.get("qualified_entry") is not None
            for row in audit_result["identities"].values()) or
        type(audit_result.get("screen")) is not dict or
        audit_result["screen"].get("missing_before") != 77 or
        audit_result["screen"].get("missing_after") != 77 or
        audit_result["screen"].get("screen_runs") != 1 or
        audit_result["screen"].get("other_findings") != []):
        return {**hold, "reason": "G3-L audit result refused"}
    try:
        before = prep.check_inventory(inventory, target_date=ARGS["target_date"],
                                      now_utc=ARGS["now_utc"], stage="PRE_REVIEW",
                                      object_root=object_root)
        final = prep.check_inventory(inventory, target_date=ARGS["target_date"],
                                     now_utc=ARGS["now_utc"], stage="FINAL",
                                     object_root=object_root)
    except (ValueError, OSError, TypeError, KeyError):
        return {**hold, "reason": "inventory refused"}
    if ({(item["id"], item["state"]) for item in before} !=
            {(identity, "MISSING") for identity in prep.PRE_REVIEW_IDS} or
        len(before) != 77 or
        {(item["id"], item["state"]) for item in final} !=
            {(identity, "MISSING") for identity in prep.ALL_IDS} or
        len(final) != 79):
        return {**hold, "reason": "unreviewed inventory refused"}
    return {**hold, "reason": "scoped bytes verified; 77 G3-L entries unqualified"}


def probe(repo, inventory=None, object_root=None, tamper=None):
    """Call public checks in order, then project their scope without promotion."""
    try:
        byte_result = binding.verify(repo)
        terminal_result = terminal.validate(repo)
        audit_result = audit_module.audit(repo, **ARGS)
        results = (byte_result, terminal_result, audit_result)
        if tamper is not None:
            results = tamper(copy.deepcopy(results))
        return _project(*results, _inventory() if inventory is None else inventory,
                        object_root)
    except (ValueError, OSError, TypeError, KeyError, AttributeError, IndexError):
        return {"status": "HOLD", "qualification_credit": 0,
                "launchable": False, "reason": "public verifier refused"}


def test_scoped_public_success_still_has_zero_g3l_credit(checkout):
    result = probe(checkout)
    assert result == {"status": "HOLD", "qualification_credit": 0,
                      "launchable": False,
                      "reason": "scoped bytes verified; 77 G3-L entries unqualified"}
    with pytest.raises(LaunchContractError):
        validate_manifest_v4(canonical(prep.private_v4_null_template(ARGS["target_date"])),
                             repo=checkout, object_root=checkout, now_utc=ARGS["now_utc"])


def test_mismatched_missing_and_symlinked_public_bytes_refuse(checkout, tmp_path):
    for name, mutation in (
        (binding.MANIFEST, "mismatch"),
        ("tools/v11_r09_gate3_launch_v4.py", "mismatch"),
        (terminal.TERMINAL, "mismatch"),
        (binding.MANIFEST, "missing"),
        (terminal.REPORT, "symlink"),
    ):
        path = checkout / name
        original = path.read_bytes()
        path.unlink()
        if mutation == "mismatch":
            path.write_bytes(original + b"x")
        elif mutation == "symlink":
            outside = tmp_path / "same-public-bytes"
            outside.write_bytes(original)
            path.symlink_to(outside)
        try:
            assert probe(checkout)["reason"] == "public verifier refused"
        finally:
            path.unlink(missing_ok=True)
            path.write_bytes(original)

    parent = checkout / "docs/g3l_original_terminal_candidate"
    outside = tmp_path / "candidate-directory"
    parent.rename(outside)
    parent.symlink_to(outside, target_is_directory=True)
    try:
        assert probe(checkout)["reason"] == "public verifier refused"
    finally:
        parent.unlink()
        outside.rename(parent)


def test_stale_and_unresolved_local_objects_refuse(checkout, tmp_path):
    inventory = _inventory()
    start = prep.freeze_checklist(ARGS["target_date"])["window_start_utc"]
    run = start - 14 * 3600
    identity = sorted(prep.RUN_SPECIFIC)[0]
    refs = []
    for name, data in (("evidence.json", b'{"local":"public test"}'),
                       ("review.json", b'{"local":"separate review test"}')):
        (tmp_path / name).write_bytes(data)
        refs.append({"sha256": hashlib.sha256(data).hexdigest(),
                     "byte_length": len(data), "media_type": "application/json",
                     "path": name})
    inventory["evidence"][identity] = {
        "ref": refs[0], "review_ref": refs[1], "observed_utc": run - 1,
        "scope": f"run:{run}",
    }
    def state():
        findings = prep.check_inventory(inventory, target_date=ARGS["target_date"],
                                        now_utc=ARGS["now_utc"], stage="PRE_REVIEW",
                                        object_root=tmp_path)
        return next(row["state"] for row in findings if row["id"] == identity)

    assert state() == "STALE"
    assert probe(checkout, inventory, tmp_path)["reason"] == "unreviewed inventory refused"
    (tmp_path / "evidence.json").unlink()
    assert state() == "INVALID"
    assert probe(checkout, inventory, tmp_path)["reason"] == "unreviewed inventory refused"
    # Link to a *separate* copy of the exact evidence bytes, not to the distinct
    # review bytes: if the symlink guard were bypassed, length/hash would match
    # and this would wrongly read STALE instead of INVALID, so this discriminates
    # the guard itself rather than merely re-detecting a byte mismatch.
    (tmp_path / "evidence-copy.json").write_bytes(b'{"local":"public test"}')
    (tmp_path / "evidence.json").symlink_to(tmp_path / "evidence-copy.json")
    assert state() == "INVALID"
    assert probe(checkout, inventory, tmp_path)["reason"] == "unreviewed inventory refused"


def test_fabricated_positive_projection_and_unreviewed_row_refuse(checkout):
    def changed(index, key, value):
        def mutate(results):
            results[index][key] = value
            return results
        return mutate

    for tamper in (
        changed(0, "launchable", True), changed(0, "qualification_credit", 1),
        changed(1, "g3l", "PASS"),
        changed(1, "terminal", "not-a-sha"),
        changed(2, "g3l", "PASS"), changed(2, "qualification_credit", 1),
        changed(2, "launchable", True),
        lambda results: (results[0], results[1], {
            **results[2], "identities": dict(list(results[2]["identities"].items())[:76])}),
    ):
        assert probe(checkout, tamper=tamper)["reason"].endswith("refused")

    raw = (checkout / audit_module.RECONCILIATION).read_bytes()
    source = json.loads(raw)
    source["identities"][prep.PRE_REVIEW_IDS[0]]["qualified_entry"] = {"claimed": "PASS"}
    (checkout / audit_module.RECONCILIATION).write_text(json.dumps(source))
    assert probe(checkout)["reason"] == "public verifier refused"
