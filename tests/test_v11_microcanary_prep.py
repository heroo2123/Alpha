"""Adversarial offline checks. No exchange client or network is involved."""
from __future__ import annotations

import json
import os
from pathlib import Path
import socket
import sqlite3
import subprocess
import sys

import pytest

from polymarket_scanner.v11.microcanary_prep import (
    CanaryJournal, CanaryRefusal, HARD_NOTIONAL_USD, REQUIRED_EVIDENCE, credential_file_present,
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
               cumulative_fill_usd="0", realized_loss_usd="0", account_census_complete=True,
               observed_at=110, open_order_count=1)
    row.update(changes)
    return row


def expect_refusal(code, fn):
    """Compare the exact refusal code via an explicit raise, not a bare
    `assert` or `pytest.raises(match=...)`. Under `python -O`, assert
    statements (including the one inside pytest's own match= check) are
    compiled out, so those checks silently pass regardless of the code.
    This helper keeps the comparison meaningful in both modes."""
    try:
        fn()
    except CanaryRefusal as exc:
        if exc.args[0] != code:
            raise AssertionError(f"expected refusal {code!r}, got {exc.args[0]!r}")
        return
    raise AssertionError(f"expected refusal {code!r}, no CanaryRefusal raised")


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
        {"remote_order_id": ""}, {"cumulative_fill_usd": "3"},
        {"status": "FILLED", "open_order_count": 0},
        {"status": "REJECTED", "open_order_count": 0, "cumulative_fill_usd": "1"},
    ):
        with pytest.raises(CanaryRefusal):
            journal.reconcile(snapshot(key, **edit), now=110)
    initial = journal.reconcile(snapshot(key), now=110)
    assert initial["state"] == "RECOVERY_HOLD"
    assert journal.reconcile(snapshot(key), now=110) == initial
    with pytest.raises(CanaryRefusal, match="REMOTE_ORDER_ID_CHANGED"):
        journal.reconcile(snapshot(key, remote_order_id="remote-2"), now=110)
    loss = journal.reconcile(snapshot(key, cumulative_fill_usd="1", realized_loss_usd="1.50"), now=110)
    assert loss["state"] == "LOSS_HALT"
    with pytest.raises(CanaryRefusal, match="RECONCILIATION_REGRESSION"):
        journal.reconcile(snapshot(key, realized_loss_usd="0"), now=110)
    open_halt = journal.reconcile(snapshot(key, cumulative_fill_usd="1", realized_loss_usd="1.50"), now=110)
    assert open_halt["state"] == "LOSS_HALT" and open_halt["cancel_required"]
    terminal = journal.reconcile(snapshot(key, status="CANCELLED", open_order_count=0,
                                          cumulative_fill_usd="1", realized_loss_usd="1.50"), now=110)
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
    # F6: a remote terminal status (REJECTED here) must not regress to OPEN
    # even though the local hold is ABORTED, not TERMINAL.
    expect_refusal("REMOTE_TERMINAL_REOPENED",
                   lambda: journal.reconcile(snapshot(key, status="OPEN"), now=110))
    other = CanaryJournal(tmp_path / "b.sqlite", scope())
    other_key = other.prepare(order(), now=110)["client_key"]
    other.reconcile(snapshot(other_key, status="FILLED", open_order_count=0,
                             cumulative_fill_usd="1"), now=110)
    with pytest.raises(CanaryRefusal, match="TERMINAL_ORDER_REOPENED"):
        other.reconcile(snapshot(other_key, cumulative_fill_usd="1"), now=110)
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


def test_status_cannot_replace_journal_or_sqlite_sidecar(tmp_path):
    path = tmp_path / "canary.sqlite"
    journal = CanaryJournal(path, scope())
    journal.prepare(order(), now=110)
    before = path.read_bytes()
    for protected in (path, Path(str(path) + "-journal"),
                      Path(str(path) + "-wal"), Path(str(path) + "-shm")):
        expect_refusal("STATUS_PATH_INVALID", lambda: journal.write_status(protected, now=110))
    alias_parent = tmp_path / "parent-alias"
    alias_parent.symlink_to(tmp_path, target_is_directory=True)
    expect_refusal("STATUS_PATH_INVALID",
                   lambda: journal.write_status(alias_parent / path.name, now=110))
    alias_file = tmp_path / "hardlink"
    os.link(path, alias_file)
    expect_refusal("STATUS_PATH_INVALID", lambda: journal.write_status(alias_file, now=110))
    alias_file.unlink()
    assert path.read_bytes() == before
    assert journal.status(now=110)["state"] == "PREPARED"
    journal.close()
    recovered = CanaryJournal(path, scope())
    assert recovered.status(now=110)["state"] == "RECOVERY_HOLD"
    recovered.close()


def test_delayed_fill_reason_survives_identical_replay(tmp_path):
    journal = CanaryJournal(tmp_path / "canary.sqlite", scope())
    key = journal.prepare(order(), now=110)["client_key"]
    cancelled = snapshot(key, status="CANCELLED", open_order_count=0)
    journal.reconcile(cancelled, now=110)
    filled = snapshot(key, status="FILLED", open_order_count=0,
                      cumulative_fill_usd="0.5")
    first = journal.reconcile(filled, now=110)
    if first["reason"] != "DELAYED_FILL_AFTER_CANCEL":
        raise AssertionError(first)
    second = journal.reconcile(filled, now=110)
    if second != first:
        raise AssertionError((first, second))
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


def test_packet_cli_refuses_fifo_and_oversized_regular_file(tmp_path):
    """F9: a non-regular file (FIFO/device) must be refused by type, never
    read, so it cannot hang the CLI; an oversized regular file is refused by
    its stat()ed size before the bytes are read."""
    tool = Path(__file__).resolve().parents[1] / "tools/v11_microcanary_packet.py"
    fifo = tmp_path / "request.fifo"
    os.mkfifo(fifo)
    run = subprocess.run([sys.executable, str(tool), str(fifo)], capture_output=True, timeout=10)
    if run.returncode != 2:
        raise AssertionError(("fifo", run.returncode, run.stderr))

    oversized = tmp_path / "oversized.json"
    oversized.write_bytes(b"[" + b"0," * 70000 + b"0]")
    run = subprocess.run([sys.executable, str(tool), str(oversized)], capture_output=True, timeout=10)
    if run.returncode != 2:
        raise AssertionError(("oversized", run.returncode, run.stderr))

    nested = tmp_path / "nested.json"
    nested.write_text("[" * 2000 + "0" + "]" * 2000)
    run = subprocess.run([sys.executable, str(tool), str(nested)], capture_output=True, timeout=10)
    if run.returncode != 2 or b"Traceback" in run.stderr:
        raise AssertionError(("nested", run.returncode, run.stderr))


def test_packet_evidence_claims_stay_listed_as_unverified():
    """F4: a caller claiming every evidence flag true must not be able to
    empty the blocker list; this tool verifies nothing itself."""
    all_claimed_true = {name: True for name in REQUIRED_EVIDENCE}
    packet = funding_decision_packet(scope=scope(), evidence=all_claimed_true, credential_present=True)
    if packet["decision"] != "NOT_READY_TO_FUND":
        raise AssertionError(packet)
    vanished = [name for name in REQUIRED_EVIDENCE if name not in packet["unverified_or_missing"]]
    if vanished:
        raise AssertionError(f"caller-claimed evidence vanished from blockers: {vanished}")
    if packet["missing_evidence"]:
        raise AssertionError(packet["missing_evidence"])
    expect_refusal("EVIDENCE_SCHEMA", lambda: funding_decision_packet(evidence=[]))


def test_timeout_reports_cancel_or_census_requirement_truthfully(tmp_path):
    """F1: TIMED_OUT_HOLD with a known resting OPEN order must say
    cancel_required=True; TIMED_OUT_HOLD with no remote census yet must say
    remote_census_required=True rather than a false cancel_required=False."""
    known = CanaryJournal(tmp_path / "timeout-known.sqlite", scope())
    key = known.prepare(order(), now=110)["client_key"]
    known.mark_dispatch_uncertain(key)
    known.reconcile(snapshot(key), now=110)
    after = known.status(now=200)
    if after["state"] != "TIMED_OUT_HOLD" or after["remote_status"] != "OPEN":
        raise AssertionError(after)
    if after["cancel_required"] is not True:
        raise AssertionError("a known resting OPEN order past the window must require cancel")
    if after["remote_census_required"] is not False:
        raise AssertionError("a known remote status does not need a fresh census")
    known.close()

    unknown = CanaryJournal(tmp_path / "timeout-unknown.sqlite", scope())
    unknown.prepare(order(), now=110)
    after_unknown = unknown.status(now=200)
    if after_unknown["state"] != "TIMED_OUT_HOLD" or after_unknown["remote_status"] is not None:
        raise AssertionError(after_unknown)
    if after_unknown["cancel_required"] is not False:
        raise AssertionError("an unknown remote status is not provably cancel-required")
    if after_unknown["remote_census_required"] is not True:
        raise AssertionError("an unknown remote status after timeout must demand a census")
    unknown.close()


def test_reconcile_accepts_precise_notional_and_canonical_zero_to_terminal(tmp_path):
    """F2/F3: a fully filled order whose accepted notional carries more than
    2dp (0.55 x 1.50 = 0.8250) must be able to reconcile to TERMINAL, and any
    canonical zero form for fill/loss must be accepted, not only the literal
    '0'."""
    precise_scope = dict(market_id="market-1", token_id="token-1", owner_amount_usd="1.00",
                          max_loss_usd="1.00", window_start=100, window_end=200,
                          owner_decision_id="owner-pending-precise")
    precise_order = dict(market_id="market-1", token_id="token-1", side="BUY",
                          price_usd="0.55", quantity="1.50", max_fee_usd="0.00", source_at=108)
    journal = CanaryJournal(tmp_path / "precise.sqlite", precise_scope)
    key = journal.prepare(precise_order, now=110)["client_key"]
    for zero in ("0", "0.00", "0.0000", "-0"):
        held = journal.reconcile(snapshot(key, realized_loss_usd=zero), now=110)
        if held["state"] != "RECOVERY_HOLD":
            raise AssertionError((zero, held))
    filled = journal.reconcile(snapshot(key, status="FILLED", open_order_count=0,
                                        cumulative_fill_usd="0.825", realized_loss_usd="0.00"), now=110)
    if filled["state"] != "TERMINAL":
        raise AssertionError(filled)
    if filled["cumulative_fill_usd"] != "0.825":
        raise AssertionError(filled)
    journal.close()


def test_journal_refuses_foreign_sqlite_file_and_hardlink_alias(tmp_path):
    """F7: the journal must refuse to adopt a pre-existing SQLite file it did
    not create itself, and must refuse a hard-linked alias of its own file."""
    foreign = tmp_path / "production.sqlite"
    foreign_db = sqlite3.connect(foreign)
    foreign_db.execute("CREATE TABLE evidence (id INTEGER)")
    foreign_db.commit()
    foreign_db.close()
    os.chmod(foreign, 0o600)
    expect_refusal("JOURNAL_CUSTODY_INVALID", lambda: CanaryJournal(foreign, scope()))
    with sqlite3.connect(foreign) as check:
        tables = {row[0] for row in check.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if "canary" in tables:
        raise AssertionError("foreign SQLite file must not be mutated")

    empty_foreign = tmp_path / "empty-foreign.sqlite"
    empty_foreign.touch(mode=0o600)
    expect_refusal("JOURNAL_CUSTODY_INVALID", lambda: CanaryJournal(empty_foreign, scope()))
    if empty_foreign.stat().st_size != 0:
        raise AssertionError("pre-existing empty file must not be adopted")

    genuine = tmp_path / "genuine.sqlite"
    journal = CanaryJournal(genuine, scope())
    journal.close()
    hardlink = tmp_path / "genuine-hardlink.sqlite"
    os.link(genuine, hardlink)
    expect_refusal("JOURNAL_CUSTODY_INVALID", lambda: CanaryJournal(hardlink, scope()))


def test_recovery_cancel_census_and_loss_anomaly(tmp_path):
    journal = CanaryJournal(tmp_path / "recovery.sqlite", scope())
    key = journal.prepare(order(), now=110)["client_key"]
    journal.mark_dispatch_uncertain(key)
    if journal.status(now=110)["remote_census_required"] is not True:
        raise AssertionError("uncertain dispatch requires remote census")
    opened = journal.reconcile(snapshot(key), now=110)
    if opened["cancel_required"] is not True:
        raise AssertionError("known open order in recovery requires cancellation")
    anomalous = journal.reconcile(snapshot(key, realized_loss_usd="1.02"), now=110)
    if anomalous["state"] != "LOSS_HALT" or anomalous["reason"] != "LOSS_EXCEEDS_AT_RISK_ANOMALY":
        raise AssertionError(anomalous)
    journal.close()


def test_delayed_fill_after_cancel_stays_terminal(tmp_path):
    journal = CanaryJournal(tmp_path / "delayed.sqlite", scope())
    key = journal.prepare(order(), now=110)["client_key"]
    journal.reconcile(snapshot(key, status="CANCELLED", open_order_count=0), now=110)
    delayed = journal.reconcile(snapshot(key, status="FILLED", open_order_count=0,
                                         cumulative_fill_usd="0.50"), now=110)
    if delayed["state"] != "TERMINAL" or delayed["reason"] != "DELAYED_FILL_AFTER_CANCEL":
        raise AssertionError(delayed)
    expect_refusal("TERMINAL_ORDER_REOPENED", lambda: journal.reconcile(snapshot(key), now=110))
    journal.close()
