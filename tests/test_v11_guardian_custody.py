"""Real mapped-principal PAPER custody proof; skipped when uidmap is absent.

Only synthetic fixture data, local AF_UNIX sockets, and disposable descendants
are used. A skip is an unavailable external gate, never a custody acceptance.
"""
from dataclasses import asdict
import errno
import json
import os
from pathlib import Path
import signal
import socket
import sqlite3
import sys
import time

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import guardian_custody_namespace as custody
from guardian_custody_namespace import (NamespaceFailure, NamespaceUnavailable, _collect,
                                        _credentials, _readline, _validate_role,
                                        prerequisites, run_namespace_fixture)


def _line(process):
    return json.loads(_readline(process.stdout, time.monotonic() + 5))


def _finish(process):
    output = _collect(process, time.monotonic() + 8)
    assert process.returncode == 0, output
    assert not output["stderr"]
    return json.loads(output["stdout"])


def _denials(config):
    """Attempt only documented refusals against this fixture's owned resources."""
    refused = []
    for name in ("paper.sqlite", "paper.sqlite-wal", "paper.sqlite-shm", "fixture-config.json"):
        target = Path(config["private"]) / name
        for operation in ("read", "write"):
            try:
                fd = os.open(target, os.O_RDONLY if operation == "read" else os.O_WRONLY)
            except OSError as exc:
                assert exc.errno in {errno.EACCES, errno.EPERM}, (name, operation, exc)
                refused.append(name + ":" + operation)
            else:
                os.close(fd)
                raise AssertionError("private file access unexpectedly allowed")
    for name, operation in (
        ("socket-unlink", lambda: os.unlink(config["socket"])),
        ("socket-directory-rename", lambda: os.rename(Path(config["socket"]).parent,
                                                       Path(config["socket"]).parent.with_name("forbidden-move"))),
        ("broker-sigterm", lambda: os.kill(config["broker_pid"], signal.SIGTERM)),
    ):
        try:
            operation()
        except OSError as exc:
            assert exc.errno in {errno.EACCES, errno.EPERM}, (name, exc)
            refused.append(name)
        else:
            raise AssertionError(name + " unexpectedly allowed")
    return refused


def _broker(private, endpoint, worker_pid):
    from test_v11_paper_coordinator import coordinator, proposal, rig
    from polymarket_scanner.v11.guardian_lease import GuardianPolicy, process_identity
    from polymarket_scanner.v11.guardian_protocol import BrokerPolicy
    from polymarket_scanner.v11.paper_guardian import PaperGuardian
    from polymarket_scanner.v11.paper_guardian_broker import PaperGuardianBroker

    patch = pytest.MonkeyPatch()
    fixture = rig.__wrapped__(Path(private), patch)
    account = coordinator(fixture)
    account.coordinate("custody-seed", (proposal(fixture, units="2"),))
    original = account.snapshot()
    guardian = PaperGuardian(account, policy=GuardianPolicy("synthetic-custody"),
                             health_config="d" * 64, worker=process_identity(int(worker_pid)))
    policy = BrokerPolicy("synthetic-custody", 1001, 1001, 1002, 1002)
    broker = PaperGuardianBroker(guardian, policy)
    config = dict(socket=endpoint, private=private, policy=asdict(policy), config=broker.config, broker_pid=os.getpid())
    Path(private, "fixture-config.json").write_text(json.dumps(config))
    Path(private, "fixture-config.json").chmod(0o600)
    # Keep actual WAL/SHM files present for access-denial checks; no write lock.
    holder = sqlite3.connect(fixture["store"].path)
    holder.execute("PRAGMA journal_mode=WAL")
    holder.execute("SELECT COUNT(*) FROM v11_records").fetchone()
    print(json.dumps(config), flush=True)
    try:
        broker.serve(Path(endpoint), connections=4, seconds=15)
        state = account._state(account._head())
        assert state["intents"]["proposal"]["cancel_requested"] is True
        assert state["intents"]["proposal"]["status"] == "CANCEL_REQUESTED"
        assert account.snapshot()["reserved_cash"] == original["reserved_cash"]
        requests = [row for row in fixture["store"].records(kind="COORDINATOR_EVENT")
                    if row["body"]["details"].get("request", {}).get("status") == "CANCEL_REQUESTED"]
        assert len(requests) == 1
        print(json.dumps(dict(cancel_requests=len(requests), reserved_cash_unchanged=True,
                              terminal_confirmation=False, financial_authority=False)), flush=True)
    finally:
        holder.close()
        patch.undo()


def _guardian(config):
    from polymarket_scanner.v11.guardian_protocol import BrokerPolicy, GuardianClient
    denied = _denials(config)
    client = GuardianClient(config["socket"], BrokerPolicy(**config["policy"]), config["config"])
    snapshot = client.snapshot("custody-snapshot")["result"]
    assert set(snapshot["intents"]) == {"proposal"}
    arguments = {key: snapshot[key] for key in ("snapshot_id", "snapshot_sha256", "intents")}
    first = client.cancel("custody-cancel", **arguments)
    repeated = client.cancel("custody-cancel", **arguments)
    assert first == repeated and first["outcome"] == "REQUESTED_NOT_CONFIRMED"
    assert first["result"]["terminal_confirmation"] is False
    print(json.dumps(dict(denied=denied, authenticated_cancel=True, replay_identical=True,
                          credentials=_credentials(), financial_authority=False)), flush=True)


def _candidate(config):
    denied = _denials(config)
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as conn:
        conn.settimeout(2)
        conn.connect(config["socket"])
        try:
            answer = conn.recv(1)
        except ConnectionResetError:
            answer = b""
        assert answer == b"", "unauthorized principal received protocol data"
    print(json.dumps(dict(denied=denied, unauthorized_peer_closed=True,
                          credentials=_credentials(), financial_authority=False)), flush=True)


def run(context, payload):
    """Explicit namespace fixture entry point; never invoked during collection."""
    script = str(context.repository / "tests" / Path(__file__).name)
    prefix = [context.python, "-s", "-E", "-B", script]
    worker = context.spawn("candidate", prefix + ["--wait"])
    assert _line(worker) == {"ready": True}
    endpoint = context.socket_directory / "paper.sock"
    broker = context.spawn("broker", prefix + ["--broker", str(context.private["broker"]), str(endpoint), str(worker.pid)])
    config = _line(broker)
    deadline = time.monotonic() + 5
    while not endpoint.is_socket():
        assert broker.poll() is None
        if time.monotonic() >= deadline:
            raise AssertionError("broker did not bind its private-custody endpoint")
        time.sleep(.01)
    for suffix in ("", "-wal", "-shm"):
        assert Path(str(context.private["broker"] / "paper.sqlite") + suffix).is_file()
    guardian = context.spawn("guardian", prefix + ["--guardian"])
    guardian.stdin.write(json.dumps(config).encode())
    guardian.stdin.close()
    guardian.stdin = None
    guardian_result = _finish(guardian)
    candidate = context.spawn("candidate", prefix + ["--candidate"])
    candidate.stdin.write(json.dumps(config).encode())
    candidate.stdin.close()
    candidate.stdin = None
    candidate_result = _finish(candidate)
    broker_result = _finish(broker)
    assert worker.poll() is None
    return dict(guardian=guardian_result, candidate=candidate_result, broker=broker_result,
                actual_distinct_principals=True, production_commissioned=False)


def test_actual_mapped_principal_broker_custody_and_cancel_only_delivery():
    try:
        prerequisites()
    except NamespaceUnavailable as exc:
        pytest.skip("EXTERNAL_CUSTODY_GATE_UNAVAILABLE: " + str(exc))
    result = run_namespace_fixture(__file__, timeout=40)
    assert result["status"] == "PASSED"
    assert result["setgroups"] == "deny"
    assert {proof["role"] for proof in result["roles"]} == {"broker", "guardian", "candidate"}
    assert result["result"]["actual_distinct_principals"] is True


@pytest.mark.parametrize("timeout", [True, None, "30", 0, 61, float("nan")])
def test_namespace_timeout_rejected_before_mapping(timeout, monkeypatch):
    monkeypatch.setattr(custody, "prerequisites", lambda: pytest.fail("mapping prerequisites reached"))
    with pytest.raises(ValueError, match="timeout"):
        run_namespace_fixture(__file__, timeout=timeout)


def test_namespace_missing_helper_is_explicit_prerequisite(monkeypatch):
    monkeypatch.setattr(custody.sys, "platform", "linux")
    monkeypatch.setattr(custody.os, "getuid", lambda: 1000, raising=False)
    monkeypatch.setattr(custody.shutil, "which", lambda *args, **kwargs: None)
    with pytest.raises(NamespaceUnavailable, match="standard uidmap package.*newuidmap"):
        prerequisites()


@pytest.mark.parametrize("mutation", ["capability", "supplementary_group", "saved_uid", "saved_gid", "new_privileges"])
def test_namespace_rejects_incomplete_role_drop(mutation):
    proof = dict(pid=123, uids=[1002] * 3, gids=[1002] * 3, groups=[],
                 capabilities={key: 0 for key in custody.CAP_FIELDS}, no_new_privileges=1)
    _validate_role(proof, "guardian")
    if mutation == "capability":
        proof["capabilities"]["CapBnd"] = 1
    elif mutation == "supplementary_group":
        proof["groups"] = [999]
    elif mutation == "saved_uid":
        proof["uids"][2] = 0
    elif mutation == "saved_gid":
        proof["gids"][2] = 0
    else:
        proof["no_new_privileges"] = 0
    with pytest.raises(NamespaceFailure, match="credential/capability"):
        _validate_role(proof, "guardian")


@pytest.mark.skipif(sys.platform != "linux", reason="Linux subordinate ID metadata")
@pytest.mark.parametrize("entry", ["1000:100000:2", "1000:0:65536", "1000:bad:65536", "2000:100000:65536"])
def test_namespace_requires_three_assigned_subordinate_ids(entry, monkeypatch):
    monkeypatch.setattr(custody.os, "getuid", lambda: 1000)
    monkeypatch.setattr(custody.pwd, "getpwuid", lambda _: type("User", (), {"pw_name": "fixture"})())
    monkeypatch.setattr(custody.Path, "read_text", lambda _: entry)
    with pytest.raises(NamespaceUnavailable):
        custody._subordinate("subuid")


if __name__ == "__main__":
    mode = sys.argv[1]
    role = {"--wait": "candidate", "--broker": "broker", "--guardian": "guardian", "--candidate": "candidate"}.get(mode)
    if role is None:
        raise SystemExit("unsupported explicit synthetic fixture role")
    _validate_role(_credentials(), role)  # Recheck after exec, before fixture access.
    if mode == "--wait":
        print('{"ready":true}', flush=True)
        time.sleep(30)
    elif mode == "--broker":
        _broker(*sys.argv[2:])
    elif mode in {"--guardian", "--candidate"}:
        configuration = json.loads(sys.stdin.buffer.read(32769))
        (_guardian if mode == "--guardian" else _candidate)(configuration)
    else:
        raise SystemExit("unsupported explicit synthetic fixture role")
