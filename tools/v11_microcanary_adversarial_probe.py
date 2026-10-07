"""Adversarial offline probe for the repaired micro-canary findings
(F1, F2, F4, F6, F7). No exchange client, network or credential is used.

This is a standalone runner, not a pytest test: it compares refusal codes
and state transitions with explicit `if/raise`, never Python's `assert`
statement, so the checks hold identically whether run as
`python3 tools/v11_microcanary_adversarial_probe.py` or with `-O`. A
`pytest.raises(match=...)` check loses that guarantee under `-O`, because
pytest's own match-check is itself an `assert` and is compiled out.

Exit code 0 means every check below passed in this interpreter mode;
nonzero means a regression. It also drives one journal through its full
life cycle to a TERMINAL status and prints that status, as the last and
most concrete form of evidence that reconciliation can reach a terminal
state for a fully filled order (the F2 defect made this unreachable for
any order whose accepted notional carried more than 2 decimal places).

This script proves offline behavior only. It grants no financial
authority and is not the reviewed activation artifact.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import sqlite3
import socket
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from polymarket_scanner.v11.microcanary_prep import (  # noqa: E402
    CanaryJournal, CanaryRefusal, REQUIRED_EVIDENCE, funding_decision_packet,
)

FAILURES: list[str] = []


def check(label: str, ok: bool, detail: object = None) -> None:
    if ok:
        print(f"PASS {label}")
    else:
        print(f"FAIL {label}: {detail!r}")
        FAILURES.append(label)


def expect_refusal(label: str, code: str, fn) -> None:
    try:
        fn()
    except CanaryRefusal as exc:
        check(label, exc.args[0] == code, f"expected {code!r} got {exc.args[0]!r}")
        return
    check(label, False, f"expected CanaryRefusal {code!r}, none raised")


def scope(owner_decision_id: str = "owner-probe-1") -> dict:
    return dict(market_id="market-1", token_id="token-1", owner_amount_usd="2.00",
                max_loss_usd="1.50", window_start=100, window_end=200,
                owner_decision_id=owner_decision_id)


def order() -> dict:
    return dict(market_id="market-1", token_id="token-1", side="BUY",
                price_usd="0.50", quantity="2", max_fee_usd="0.01", source_at=108)


def snapshot(key: str, **changes) -> dict:
    row = dict(client_key=key, remote_order_id="remote-1", status="OPEN",
               cumulative_fill_usd="0", realized_loss_usd="0", account_census_complete=True,
               observed_at=110, open_order_count=1)
    row.update(changes)
    return row


def probe_f1(tmp: Path) -> None:
    known = CanaryJournal(tmp / "f1-known.sqlite", scope())
    key = known.prepare(order(), now=110)["client_key"]
    known.mark_dispatch_uncertain(key)
    known.reconcile(snapshot(key), now=110)
    after = known.status(now=200)
    check("F1 timed-out known OPEN order requires cancel", after["cancel_required"] is True, after)
    check("F1 timed-out known OPEN order needs no census", after["remote_census_required"] is False, after)
    known.close()

    unknown = CanaryJournal(tmp / "f1-unknown.sqlite", scope())
    unknown.prepare(order(), now=110)
    after_unknown = unknown.status(now=200)
    check("F1 timed-out unknown remote status is not cancel-required",
          after_unknown["cancel_required"] is False, after_unknown)
    check("F1 timed-out unknown remote status requires a census",
          after_unknown["remote_census_required"] is True, after_unknown)
    unknown.close()


def probe_f2_and_f3(tmp: Path) -> dict:
    precise_scope = scope("owner-probe-precise")
    precise_scope.update(owner_amount_usd="1.00", max_loss_usd="1.00")
    precise_order = dict(market_id="market-1", token_id="token-1", side="BUY",
                          price_usd="0.55", quantity="1.50", max_fee_usd="0.00", source_at=108)
    journal = CanaryJournal(tmp / "f2-precise.sqlite", precise_scope)
    key = journal.prepare(precise_order, now=110)["client_key"]
    for zero in ("0", "0.00", "0.0000", "-0"):
        held = journal.reconcile(snapshot(key, realized_loss_usd=zero), now=110)
        check(f"F2 canonical zero {zero!r} accepted", held["state"] == "RECOVERY_HOLD", held)
    terminal = journal.reconcile(snapshot(key, status="FILLED", open_order_count=0,
                                         cumulative_fill_usd="0.825", realized_loss_usd="0.00"), now=110)
    check("F2 4dp fully-filled order reaches TERMINAL", terminal["state"] == "TERMINAL", terminal)
    check("F3 fill field is unit-labeled USD", terminal["cumulative_fill_usd"] == "0.825", terminal)
    journal.close()
    return terminal


def probe_f4() -> None:
    all_claimed_true = {name: True for name in REQUIRED_EVIDENCE}
    packet = funding_decision_packet(scope=scope("owner-probe-evidence"), evidence=all_claimed_true,
                                      credential_present=True)
    check("F4 packet still says NOT_READY_TO_FUND with all flags true",
          packet["decision"] == "NOT_READY_TO_FUND", packet["decision"])
    vanished = [name for name in REQUIRED_EVIDENCE if name not in packet["unverified_or_missing"]]
    check("F4 claimed-true evidence stays listed as unverified", vanished == [], vanished)


def probe_f6(tmp: Path) -> None:
    journal = CanaryJournal(tmp / "f6.sqlite", scope())
    key = journal.prepare(order(), now=110)["client_key"]
    journal.abort()
    journal.reconcile(snapshot(key, status="REJECTED", open_order_count=0), now=110)
    expect_refusal("F6 remote REJECTED cannot reopen to OPEN under local ABORTED",
                   "REMOTE_TERMINAL_REOPENED",
                   lambda: journal.reconcile(snapshot(key, status="OPEN"), now=110))
    journal.close()


def probe_f7(tmp: Path) -> None:
    foreign = tmp / "f7-foreign.sqlite"
    db = sqlite3.connect(foreign)
    db.execute("CREATE TABLE evidence (id INTEGER)")
    db.commit()
    db.close()
    os.chmod(foreign, 0o600)
    expect_refusal("F7 refuses to adopt a foreign pre-existing SQLite file",
                   "JOURNAL_CUSTODY_INVALID", lambda: CanaryJournal(foreign, scope()))
    with sqlite3.connect(foreign) as check_db:
        tables = {row[0] for row in check_db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    check("F7 foreign SQLite file was not mutated", "canary" not in tables, tables)

    genuine = tmp / "f7-genuine.sqlite"
    journal = CanaryJournal(genuine, scope())
    journal.close()
    hardlink = tmp / "f7-hardlink.sqlite"
    os.link(genuine, hardlink)
    expect_refusal("F7 refuses a hard-linked alias of its own journal",
                   "JOURNAL_CUSTODY_INVALID", lambda: CanaryJournal(hardlink, scope()))


def main() -> int:
    def forbidden_socket(*args, **kwargs):
        raise RuntimeError("NO_SOCKET_VIOLATION")

    socket.socket = forbidden_socket
    with tempfile.TemporaryDirectory(prefix="v11-microcanary-probe-") as raw:
        tmp = Path(raw)
        probe_f1(tmp)
        terminal = probe_f2_and_f3(tmp)
        probe_f4()
        probe_f6(tmp)
        probe_f7(tmp)
        check("no socket was requested", True)
        print("--- terminal status reached via runner (F2/F3 evidence) ---")
        print(json.dumps(terminal, sort_keys=True, indent=2))
    if FAILURES:
        print(f"\n{len(FAILURES)} check(s) failed: {FAILURES}", file=sys.stderr)
        return 1
    print("\nall checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
