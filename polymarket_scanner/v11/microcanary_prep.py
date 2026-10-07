"""Offline, nonfinancial micro-canary preparation. No signer or order transport.

This journal is a rehearsal and a funding decision input, not an activation
artifact. A future independently reviewed live adapter must enforce these
limits again at its final submission boundary.
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import stat
import tempfile


HARD_NOTIONAL_USD = Decimal("5.00")
HARD_ORDER_COUNT = 1
MAX_DATA_AGE_SECONDS = 15
MAX_CANARY_SECONDS = 300
VERSION = "v11_microcanary_prep_v1"


class CanaryRefusal(ValueError):
    pass


def _require(ok: bool, code: str) -> None:
    if not ok:
        raise CanaryRefusal(code)


def _money(value: object, code: str) -> Decimal:
    _require(type(value) is str, code)
    try:
        result = Decimal(value)
    except InvalidOperation:
        raise CanaryRefusal(code) from None
    _require(result.is_finite() and result > 0 and result.as_tuple().exponent >= -2, code)
    return result


def _nonnegative_money(value: object, code: str) -> Decimal:
    _require(type(value) is str, code)
    try:
        result = Decimal(value)
    except InvalidOperation:
        raise CanaryRefusal(code) from None
    _require(result.is_finite() and result >= 0 and result.as_tuple().exponent >= -2, code)
    return result


def _time(value: object, code: str) -> Decimal:
    _require(type(value) in (int, str), code)
    try:
        result = Decimal(str(value))
    except InvalidOperation:
        raise CanaryRefusal(code) from None
    _require(result.is_finite() and result >= 0, code)
    return result


def _canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _digest(value: object) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def credential_file_present(path: str | Path) -> bool:
    """Metadata only. Never opens, reads, hashes or logs credential contents."""
    p = Path(path)
    if not p.is_absolute() or ".." in p.parts:
        return False
    try:
        info = p.lstat()
    except OSError:
        return False
    return stat.S_ISREG(info.st_mode) and info.st_size > 0 and info.st_mode & 0o077 == 0


def validate_scope(scope: dict) -> dict:
    _require(type(scope) is dict and set(scope) == {
        "market_id", "token_id", "owner_amount_usd", "max_loss_usd",
        "window_start", "window_end", "owner_decision_id",
    }, "SCOPE_SCHEMA")
    for field in ("market_id", "token_id", "owner_decision_id"):
        value = scope[field]
        _require(type(value) is str and 1 <= len(value) <= 128 and
                 all(c.isalnum() or c in "-_:." for c in value), "SCOPE_IDENTITY")
    amount = _money(scope["owner_amount_usd"], "OWNER_AMOUNT_REQUIRED")
    loss = _money(scope["max_loss_usd"], "MAX_LOSS_REQUIRED")
    _require(amount <= HARD_NOTIONAL_USD and loss <= amount, "HARD_CAP_EXCEEDED")
    start = _time(scope["window_start"], "WINDOW_INVALID")
    end = _time(scope["window_end"], "WINDOW_INVALID")
    _require(start < end and end - start <= MAX_CANARY_SECONDS, "WINDOW_INVALID")
    return {**scope, "owner_amount_usd": str(amount), "max_loss_usd": str(loss),
            "window_start": str(start), "window_end": str(end)}


def dry_run_order(scope: dict, order: dict, *, now: object) -> dict:
    """Canonical unsigned rehearsal payload; deliberately not venue wire format."""
    scope = validate_scope(scope)
    _require(type(order) is dict and set(order) == {
        "market_id", "token_id", "side", "price_usd", "quantity", "max_fee_usd", "source_at",
    }, "ORDER_SCHEMA")
    _require(order["market_id"] == scope["market_id"] and
             order["token_id"] == scope["token_id"] and order["side"] == "BUY",
             "ONE_MARKET_BUY_ONLY")
    price = _money(order["price_usd"], "ORDER_PRICE_INVALID")
    _require(price < 1, "ORDER_PRICE_INVALID")
    try:
        quantity = Decimal(order["quantity"]) if type(order["quantity"]) is str else Decimal(0)
    except InvalidOperation:
        raise CanaryRefusal("ORDER_QUANTITY_INVALID") from None
    _require(quantity.is_finite() and quantity > 0 and quantity.as_tuple().exponent >= -2,
             "ORDER_QUANTITY_INVALID")
    notional = price * quantity
    fee = _nonnegative_money(order["max_fee_usd"], "FEE_CAP_INVALID")
    total = notional + fee
    _require(notional <= HARD_NOTIONAL_USD and
             total <= _money(scope["owner_amount_usd"], "OWNER_AMOUNT_REQUIRED") and
             total <= _money(scope["max_loss_usd"], "MAX_LOSS_REQUIRED"),
             "HARD_CAP_EXCEEDED")
    current = _time(now, "CLOCK_INVALID")
    source = _time(order["source_at"], "SOURCE_TIME_INVALID")
    _require(_time(scope["window_start"], "WINDOW_INVALID") <= current <
             _time(scope["window_end"], "WINDOW_INVALID"), "CANARY_TIMEOUT")
    _require(source <= current and current - source <= MAX_DATA_AGE_SECONDS,
             "STALE_OR_FUTURE_DATA")
    payload = dict(version=VERSION, financial_authority=False, kind="UNSIGNED_DRY_RUN_ONLY",
                   market_id=scope["market_id"], token_id=scope["token_id"], side="BUY",
                   price_usd=str(price), quantity=str(quantity), max_notional_usd=str(notional),
                   max_fee_usd=str(fee), max_total_at_risk_usd=str(total),
                   source_at=str(source), owner_scope_sha256=_digest(scope))
    return dict(payload=payload, serialized=_canonical(payload), client_key=_digest(payload))


class CanaryJournal:
    """SQLite single-use rehearsal; restart and uncertain dispatch stay held."""

    def __init__(self, path: str | Path, scope: dict):
        self.path = Path(path)
        self.scope = validate_scope(scope)
        _require(self.path.is_absolute() and ".." not in self.path.parts,
                 "JOURNAL_PATH_INVALID")
        flags = os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0)
        fd = os.open(self.path, flags, 0o600)
        try:
            info = os.fstat(fd)
            _require(stat.S_ISREG(info.st_mode) and info.st_mode & 0o077 == 0,
                     "JOURNAL_CUSTODY_INVALID")
        finally:
            os.close(fd)
        self.db = sqlite3.connect(self.path, timeout=2, isolation_level=None)
        self.db.execute("PRAGMA busy_timeout=2000")
        self.db.execute("CREATE TABLE IF NOT EXISTS canary (singleton INTEGER PRIMARY KEY CHECK(singleton=1),"
                        " scope_hash TEXT NOT NULL, state TEXT NOT NULL, payload TEXT, client_key TEXT,"
                        " remote_order_id TEXT, remote_status TEXT, cumulative_fill TEXT NOT NULL DEFAULT '0',"
                        " realized_loss TEXT NOT NULL DEFAULT '0', reason TEXT NOT NULL)")
        self.db.execute("BEGIN IMMEDIATE")
        try:
            row = self.db.execute("SELECT scope_hash,state FROM canary WHERE singleton=1").fetchone()
            if row is None:
                self.db.execute("INSERT INTO canary(singleton,scope_hash,state,reason) VALUES(1,?,'EMPTY','NONE')",
                                (_digest(self.scope),))
            else:
                _require(row[0] == _digest(self.scope), "SCOPE_CHANGED_ON_RESTART")
                if row[1] in ("PREPARED", "DISPATCH_UNCERTAIN", "OPEN"):
                    self.db.execute("UPDATE canary SET state='RECOVERY_HOLD',reason='RESTART_RECONCILIATION_REQUIRED'")
            self.db.execute("COMMIT")
        except BaseException:
            self.db.execute("ROLLBACK")
            self.db.close()
            raise

    def close(self):
        self.db.close()

    def _row(self):
        row = self.db.execute("SELECT state,payload,client_key,remote_order_id,remote_status,cumulative_fill,realized_loss,reason "
                              "FROM canary WHERE singleton=1").fetchone()
        return dict(zip(("state", "payload", "client_key", "remote_order_id", "remote_status", "cumulative_fill",
                         "realized_loss", "reason"), row))

    def status(self, *, now: object) -> dict:
        row = self._row()
        current = _time(now, "CLOCK_INVALID")
        timed_out = current >= _time(self.scope["window_end"], "WINDOW_INVALID")
        state = "TIMED_OUT_HOLD" if timed_out and row["state"] not in ("ABORTED", "LOSS_HALT", "TERMINAL") else row["state"]
        return dict(version=VERSION, financial_authority=False, execution_masked=True,
                    state=state, reason="CANARY_TIMEOUT" if state == "TIMED_OUT_HOLD" else row["reason"],
                    new_order_allowed=False, order_count=0 if row["payload"] is None else 1,
                    hard_order_count=HARD_ORDER_COUNT, hard_notional_usd=str(HARD_NOTIONAL_USD),
                    scope_sha256=_digest(self.scope), client_key=row["client_key"],
                    remote_order_id=row["remote_order_id"], remote_status=row["remote_status"],
                    cancel_required=row["remote_status"] == "OPEN" and state in ("ABORTED", "LOSS_HALT"),
                    cumulative_fill=row["cumulative_fill"],
                    realized_loss=row["realized_loss"])

    def write_status(self, path: str | Path, *, now: object) -> dict:
        """Publish a redacted, atomic, owner-readable monitoring artifact."""
        target = Path(path)
        _require(target.is_absolute() and ".." not in target.parts and
                 not target.is_symlink(), "STATUS_PATH_INVALID")
        report = self.status(now=now)
        encoded = (_canonical(report) + "\n").encode("utf-8")
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(dir=target.parent, prefix=".canary-status-",
                                             delete=False) as stream:
                temporary = Path(stream.name)
                os.fchmod(stream.fileno(), 0o600)
                stream.write(encoded)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, target)
            directory = os.open(target.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
            return report
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)

    def prepare(self, order: dict, *, now: object) -> dict:
        candidate = dry_run_order(self.scope, order, now=now)
        self.db.execute("BEGIN IMMEDIATE")
        try:
            row = self._row()
            if row["state"] == "PREPARED" and row["client_key"] == candidate["client_key"]:
                self.db.execute("COMMIT")
                return candidate
            _require(row["state"] == "EMPTY", "ONE_ORDER_OR_HALT")
            self.db.execute("UPDATE canary SET state='PREPARED',payload=?,client_key=?,reason='DRY_RUN_PREPARED'",
                            (candidate["serialized"], candidate["client_key"]))
            self.db.execute("COMMIT")
            return candidate
        except BaseException:
            self.db.execute("ROLLBACK")
            raise

    def mark_dispatch_uncertain(self, client_key: str):
        """Model an interrupted external handoff; never sends an order."""
        self.db.execute("BEGIN IMMEDIATE")
        try:
            row = self._row()
            _require(row["state"] == "PREPARED" and row["client_key"] == client_key,
                     "DISPATCH_STATE_INVALID")
            self.db.execute("UPDATE canary SET state='DISPATCH_UNCERTAIN',reason='REMOTE_CENSUS_REQUIRED'")
            self.db.execute("COMMIT")
        except BaseException:
            self.db.execute("ROLLBACK")
            raise

    def reconcile(self, snapshot: dict, *, now: object) -> dict:
        """Caller-supplied full snapshot. Its external authenticity is unproven here."""
        current = _time(now, "CLOCK_INVALID")
        _require(type(snapshot) is dict and set(snapshot) == {
            "client_key", "remote_order_id", "status", "cumulative_fill", "realized_loss_usd",
            "account_census_complete", "observed_at", "open_order_count",
        }, "SNAPSHOT_SCHEMA")
        _require(snapshot["account_census_complete"] is True and
                 type(snapshot["open_order_count"]) is int and 0 <= snapshot["open_order_count"] <= 1,
                 "ACCOUNT_CENSUS_INCOMPLETE")
        observed = _time(snapshot["observed_at"], "SNAPSHOT_TIME_INVALID")
        _require(observed <= current and current - observed <= MAX_DATA_AGE_SECONDS,
                 "STALE_OR_FUTURE_SNAPSHOT")
        fill = _money(snapshot["cumulative_fill"], "FILL_INVALID") if snapshot["cumulative_fill"] != "0" else Decimal(0)
        loss = _money(snapshot["realized_loss_usd"], "LOSS_INVALID") if snapshot["realized_loss_usd"] != "0" else Decimal(0)
        _require(snapshot["status"] in ("OPEN", "FILLED", "CANCELLED", "REJECTED") and
                 type(snapshot["remote_order_id"]) is str and 1 <= len(snapshot["remote_order_id"]) <= 128,
                 "REMOTE_ORDER_INVALID")
        _require(snapshot["status"] != "FILLED" or fill > 0, "FILLED_WITHOUT_FILL")
        _require(snapshot["status"] != "REJECTED" or fill == 0, "REJECTED_WITH_FILL")
        self.db.execute("BEGIN IMMEDIATE")
        try:
            row = self._row()
            _require(row["client_key"] == snapshot["client_key"] and row["payload"] is not None,
                     "UNKNOWN_OR_DUPLICATE_ORDER")
            _require(fill <= Decimal(json.loads(row["payload"])["max_notional_usd"]),
                     "FILL_EXCEEDS_ORDER_NOTIONAL")
            _require(row["remote_order_id"] in (None, snapshot["remote_order_id"]),
                     "REMOTE_ORDER_ID_CHANGED")
            _require(row["state"] != "TERMINAL" or snapshot["status"] != "OPEN",
                     "TERMINAL_ORDER_REOPENED")
            _require(fill >= Decimal(row["cumulative_fill"]) and
                     loss >= Decimal(row["realized_loss"]), "RECONCILIATION_REGRESSION")
            if snapshot["status"] == "OPEN":
                _require(snapshot["open_order_count"] == 1, "OPEN_ORDER_CENSUS_MISMATCH")
            else:
                _require(snapshot["open_order_count"] == 0, "TERMINAL_ORDER_STILL_OPEN")
            state = ("LOSS_HALT" if row["state"] == "LOSS_HALT" or
                     loss >= _money(self.scope["max_loss_usd"], "MAX_LOSS_REQUIRED")
                     else "ABORTED" if row["state"] == "ABORTED"
                     else "RECOVERY_HOLD" if snapshot["status"] == "OPEN"
                     else "TERMINAL")
            reason = ("MAX_LOSS_HALT" if state == "LOSS_HALT" else
                      "OPERATOR_ABORT" if state == "ABORTED" else
                      "OPEN_ORDER_REQUIRES_ABORT_OR_TERMINAL" if state == "RECOVERY_HOLD" else
                      "RECONCILED_TERMINAL")
            self.db.execute("UPDATE canary SET state=?,remote_order_id=?,remote_status=?,cumulative_fill=?,"
                            "realized_loss=?,reason=?", (state, snapshot["remote_order_id"], snapshot["status"],
                            str(fill), str(loss), reason))
            self.db.execute("COMMIT")
            return self.status(now=now)
        except BaseException:
            self.db.execute("ROLLBACK")
            raise

    def abort(self, *, reason: str = "OPERATOR_ABORT") -> None:
        _require(reason == "OPERATOR_ABORT", "ABORT_REASON_INVALID")
        self.db.execute("BEGIN IMMEDIATE")
        try:
            row = self._row()
            if row["state"] not in ("LOSS_HALT", "ABORTED"):
                self.db.execute("UPDATE canary SET state='ABORTED',reason='OPERATOR_ABORT'")
            self.db.execute("COMMIT")
        except BaseException:
            self.db.execute("ROLLBACK")
            raise


REQUIRED_EVIDENCE = (
    "paper_v11_ready", "paper_requirement_8_current_input", "paper_requirement_9_current_input",
    "accepted_initial_champion", "forward_shadow_qualified", "gate3_l_pass",
    "independent_code_review", "isolated_host_acceptance", "supported_account_auth",
    "account_eligibility_and_venue_terms", "credential_custody", "balance_and_allowance_verified",
    "live_adapter_enforces_canary_caps", "live_guardian_acceptance",
    "independent_kill_and_abort_acceptance", "full_account_reconciliation",
    "restart_and_ambiguous_post_recovery", "monitoring_and_operator_identity",
    "owner_funding_decision", "reviewed_activation_artifact",
)


def funding_decision_packet(*, scope: dict | None = None, evidence: dict | None = None,
                            credential_present: bool = False) -> dict:
    """Decision inventory only. Caller claims cannot grant financial authority."""
    evidence = evidence or {}
    _require(type(evidence) is dict and set(evidence) <= set(REQUIRED_EVIDENCE) and
             all(type(v) is bool for v in evidence.values()), "EVIDENCE_SCHEMA")
    blockers = [name for name in REQUIRED_EVIDENCE if evidence.get(name) is not True]
    if scope is None:
        blockers.append("concrete_owner_amount_market_loss_and_window")
        scope_hash = None
        proposed_scope = None
    else:
        proposed_scope = validate_scope(scope)
        scope_hash = _digest(proposed_scope)
    if not credential_present:
        blockers.append("credential_presence_unverified")
    return dict(version=VERSION, decision="NOT_READY_TO_FUND", financial_authority=False,
                execution_masked=True, hard_notional_usd=str(HARD_NOTIONAL_USD),
                hard_order_count=HARD_ORDER_COUNT, scope_sha256=scope_hash,
                proposed_owner_scope=proposed_scope,
                supplied_evidence=evidence, unverified_or_missing=blockers,
                note="Supplied evidence flags are unverified; independent review and a separate live gate are required.")
