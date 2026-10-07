"""Adversarial offline checks. No exchange client or network is involved."""
from __future__ import annotations

import json
import os
from pathlib import Path
import socket
import subprocess
import sys

import pytest

from polymarket_scanner.v11.microcanary_prep import (
    CanaryJournal, CanaryRefusal, HARD_NOTIONAL_USD, credential_file_present,
    dry_run_order, funding_decision_packet,
)


def scope():
    return dict(market_id="market-1", token_id="token-1", owner_amount_usd="2.00",
                max_loss_usd="1.50", window_start=100, window_end=200,
                owner_decision_id="owner-pending-1")


def order():
    return dict(market_id="market-1", token_id="token-1", side="BUY",
                price_usd="0.50", quantity="2", max_fee_usd="0.01", source_at=108)


def snapshot(key, **changes):
    row = dict(client_key=key, remote_order_id="remote-1", status="OPEN",
               cumulative_fill="0", realized_loss_usd="0", account_census_complete=True,
               observed_at=110, open_order_count=1)
    row.update(changes)
    return row


def test_dry_run_is_unsigned_deterministic_and_hard_bounded(monkeypatch):
    monkeypatch.setattr(socket, "socket", lambda *a, **k: pytest.fail("network socket"))
    first = dry_run_order(scope(), order(), now=110)
    assert first == dry_run_order(scope(), order(), now=111)
    assert json.loads(first["serialized"]) == first["payload"]
    assert first["payload"]["financial_authority"] is False
    assert "signature" not in first["serialized"] and "credential" not in first["serialized"]
    for edit in (
        {"market_id": "market-2"}, {"token_id": "token-2"}, {"side": "SELL"},
        {"quantity": "5"}, {"quantity": "NaN"}, {"price_usd": "1.00"},
        {"source_at": 94}, {"source_at": 112}, {"max_fee_usd": "2.00"},
    ):
        candidate = order() | edit
        with pytest.raises(CanaryRefusal):
            dry_run_order(scope(), candidate, now=110)
    with pytest.raises(CanaryRefusal, match="CANARY_TIMEOUT"):
        dry_run_order(scope(), order(), now=200)
    for edit in ({"owner_amount_usd": "5.01"}, {"max_loss_usd": "2.01"},
                 {"owner_amount_usd": "0"}, {"window_end": 401}):
        with pytest.raises(CanaryRefusal):
            dry_run_order(scope() | edit, order(), now=110)
    assert HARD_NOTIONAL_USD == 5


def test_one_order_idempotence_restart_hold_and_no_resubmission(tmp_path):
    path = tmp_path / "canary.sqlite"
    journal = CanaryJournal(path, scope())
    first = journal.prepare(order(), now=110)
    assert journal.prepare(order(), now=110) == first
    with pytest.raises(CanaryRefusal, match="ONE_ORDER_OR_HALT"):
        journal.prepare(order() | {"quantity": "1"}, now=110)
    journal.mark_dispatch_uncertain(first["client_key"])
    assert journal.status(now=111)["state"] == "DISPATCH_UNCERTAIN"
    journal.close()
    alias = tmp_path / "journal-link"
    alias.symlink_to(path)
    with pytest.raises(OSError):
        CanaryJournal(alias, scope())
    journal = CanaryJournal(path, scope())
    assert journal.status(now=111)["state"] == "RECOVERY_HOLD"
    with pytest.raises(CanaryRefusal, match="ONE_ORDER_OR_HALT"):
        journal.prepare(order(), now=111)
    with pytest.raises(CanaryRefusal, match="SCOPE_CHANGED_ON_RESTART"):
        CanaryJournal(path, scope() | {"market_id": "market-2"})
    journal.close()


def test_reconciliation_census_identity_loss_and_terminal_are_sticky(tmp_path):
    journal = CanaryJournal(tmp_path / "canary.sqlite", scope())
    key = journal.prepare(order(), now=110)["client_key"]
    for edit in (
        {"client_key": "different"}, {"account_census_complete": False},
        {"open_order_count": 2}, {"observed_at": 90},
        {"remote_order_id": ""}, {"cumulative_fill": "3"},
        {"status": "FILLED", "open_order_count": 0},
        {"status": "REJECTED", "open_order_count": 0, "cumulative_fill": "1"},
    ):
        with pytest.raises(CanaryRefusal):
            journal.reconcile(snapshot(key, **edit), now=110)
    initial = journal.reconcile(snapshot(key), now=110)
    assert initial["state"] == "RECOVERY_HOLD"
    assert journal.reconcile(snapshot(key), now=110) == initial
    with pytest.raises(CanaryRefusal, match="REMOTE_ORDER_ID_CHANGED"):
        journal.reconcile(snapshot(key, remote_order_id="remote-2"), now=110)
    loss = journal.reconcile(snapshot(key, cumulative_fill="1", realized_loss_usd="1.50"), now=110)
    assert loss["state"] == "LOSS_HALT"
    with pytest.raises(CanaryRefusal, match="RECONCILIATION_REGRESSION"):
        journal.reconcile(snapshot(key, realized_loss_usd="0"), now=110)
    open_halt = journal.reconcile(snapshot(key, cumulative_fill="1", realized_loss_usd="1.50"), now=110)
    assert open_halt["state"] == "LOSS_HALT" and open_halt["cancel_required"]
    terminal = journal.reconcile(snapshot(key, status="CANCELLED", open_order_count=0,
                                          cumulative_fill="1", realized_loss_usd="1.50"), now=110)
    assert terminal["state"] == "LOSS_HALT"
    journal.close()
    assert CanaryJournal(tmp_path / "canary.sqlite", scope()).status(now=111)["state"] == "LOSS_HALT"


def test_operator_abort_timeout_and_terminal_reopen_refusal(tmp_path):
    journal = CanaryJournal(tmp_path / "a.sqlite", scope())
    key = journal.prepare(order(), now=110)["client_key"]
    journal.abort()
    journal.abort()
    assert journal.status(now=110)["state"] == "ABORTED"
    assert journal.reconcile(snapshot(key), now=110)["cancel_required"]
    with pytest.raises(CanaryRefusal):
        journal.prepare(order(), now=110)
    terminal = journal.reconcile(snapshot(key, status="REJECTED", open_order_count=0), now=110)
    assert terminal["state"] == "ABORTED"
    other = CanaryJournal(tmp_path / "b.sqlite", scope())
    other_key = other.prepare(order(), now=110)["client_key"]
    other.reconcile(snapshot(other_key, status="FILLED", open_order_count=0,
                             cumulative_fill="1"), now=110)
    with pytest.raises(CanaryRefusal, match="TERMINAL_ORDER_REOPENED"):
        other.reconcile(snapshot(other_key, cumulative_fill="1"), now=110)
    assert other.status(now=200)["state"] == "TERMINAL"
    assert CanaryJournal(tmp_path / "c.sqlite", scope()).status(now=200)["state"] == "TIMED_OUT_HOLD"


def test_credential_metadata_only_status_artifact_and_packet(tmp_path, monkeypatch):
    secret = tmp_path / "credential"
    secret.write_text("DO_NOT_PRINT_SECRET")
    os.chmod(secret, 0o600)
    assert credential_file_present(secret)
    os.chmod(secret, 0o644)
    assert not credential_file_present(secret)
    os.chmod(secret, 0o600)
    alias = tmp_path / "alias"
    alias.symlink_to(secret)
    assert not credential_file_present(alias)
    monkeypatch.setattr(Path, "read_bytes", lambda *a, **k: pytest.fail("credential read"))
    assert credential_file_present(secret)
    packet = funding_decision_packet(scope=scope(), evidence={"paper_v11_ready": True},
                                     credential_present=True)
    assert packet["decision"] == "NOT_READY_TO_FUND"
    assert "reviewed_activation_artifact" in packet["unverified_or_missing"]
    assert "DO_NOT_PRINT_SECRET" not in json.dumps(packet)
    journal = CanaryJournal(tmp_path / "canary.sqlite", scope())
    artifact = tmp_path / "status.json"
    report = journal.write_status(artifact, now=110)
    assert json.loads(artifact.read_text()) == report
    assert artifact.stat().st_mode & 0o077 == 0
    assert report["execution_masked"] and not report["new_order_allowed"]
    alias_status = tmp_path / "status-link"
    alias_status.symlink_to(artifact)
    with pytest.raises(CanaryRefusal, match="STATUS_PATH_INVALID"):
        journal.write_status(alias_status, now=110)
    journal.close()


def test_packet_cli_rejects_duplicate_keys_and_never_claims_activation(tmp_path):
    tool = Path(__file__).resolve().parents[1] / "tools/v11_microcanary_packet.py"
    request = tmp_path / "request.json"
    request.write_text(json.dumps({"scope": scope(), "evidence": {}}))
    run = subprocess.run([sys.executable, str(tool), str(request)], text=True, capture_output=True)
    assert run.returncode == 0
    packet = json.loads(run.stdout)
    assert packet["decision"] == "NOT_READY_TO_FUND" and packet["execution_masked"]
    request.write_text('{"scope":null,"scope":null}')
    assert subprocess.run([sys.executable, str(tool), str(request)], capture_output=True).returncode == 2
