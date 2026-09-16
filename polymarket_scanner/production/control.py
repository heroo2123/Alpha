"""Versioned operator requests. This module has no signer or Telegram token access.

The protected base configuration is an external ceiling, never a chat-editable
file. The controller writes requests; the executor owns their financial effects.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
from decimal import Decimal
import json
import math
import os
from pathlib import Path
import re
import secrets
import sqlite3
import time

from .config import ConfigurationError, FEE_POLICIES, RiskLimits, canonical, decimal, digest

EXPERIENCES = frozenset({"SIGNALS", "CONFIRM", "AUTOMATIC"})
OPERATIONS = frozenset({"PAUSE", "CANCEL", "RESUME", "SET", "CONFIRM_TRADE"})
INTEGER_LIMITS = frozenset({"max_open_orders", "max_positions", "max_maker_rest_seconds"})


class ControlError(ConfigurationError):
    pass


def schedule(value):
    """Explicit UTC weekday/minute windows; overnight periods must be split."""
    if not isinstance(value, list) or len(value) > 28:
        raise ControlError("INVALID_TRADING_SCHEDULE")
    rows = []
    for row in value:
        if (not isinstance(row, list) or len(row) != 3 or any(type(v) is not int for v in row)
            or not 0 <= row[0] <= 6 or not 0 <= row[1] < row[2] <= 1440):
            raise ControlError("INVALID_TRADING_SCHEDULE")
        rows.append(row)
    ordered = sorted(rows)
    if any(a[0] == b[0] and a[2] > b[1] for a, b in zip(ordered, ordered[1:])):
        raise ControlError("OVERLAPPING_TRADING_SCHEDULE")
    return ordered


def schedule_subset(selected, ceiling):
    return all(any(day == cd and start >= cs and end <= ce for cd, cs, ce in ceiling)
               for day, start, end in schedule(selected))


@dataclass(frozen=True)
class ControlPolicy:
    db: Path
    scanner_db: Path
    scanner_status: Path
    authorization_version: str
    expires: float
    bot_id: int
    chat_id: int
    operators: tuple[int, ...]
    experiences: tuple[str, ...]
    fee_policies: tuple[str, ...]
    schedule: list

    @classmethod
    def parse(cls, raw, *, mode, fee_policy, paths):
        keys = {"version", "db", "scanner_db", "scanner_status", "authorization_version",
                "authorization_expires_at", "bot_id", "chat_id", "operator_user_ids",
                "allowed_experiences", "allowed_fee_policies", "trading_schedule_utc"}
        if not isinstance(raw, dict) or set(raw) != keys or type(raw["version"]) is not int or raw["version"] != 1:
            raise ControlError("OPERATOR_CONTROL_V1_SCHEMA_REQUIRED")
        version = raw["authorization_version"]
        if not isinstance(version, str) or not re.fullmatch(r"[A-Za-z0-9_-]{8,64}", version):
            raise ControlError("INVALID_CONTROL_AUTHORIZATION_VERSION")
        for key in ("bot_id", "chat_id"):
            if type(raw[key]) is not int or raw[key] <= 0:
                raise ControlError("CONTROL_REQUIRES_NUMERIC_BOT_AND_PRIVATE_CHAT")
        operators = raw["operator_user_ids"]
        if (not isinstance(operators, list) or not 1 <= len(operators) <= 10
            or any(type(x) is not int or x <= 0 for x in operators) or len(set(operators)) != len(operators)):
            raise ControlError("CONTROL_NUMERIC_OPERATORS_REQUIRED")
        # A private chat belongs to one user; this prevents group or forwarded UI use.
        if raw["chat_id"] not in operators:
            raise ControlError("PRIVATE_CHAT_OWNER_MUST_BE_OPERATOR")
        experiences = raw["allowed_experiences"]
        fees = raw["allowed_fee_policies"]
        if (not isinstance(experiences, list) or not experiences or any(not isinstance(x, str) or x not in EXPERIENCES for x in experiences)
            or "SIGNALS" not in experiences or mode != "LIVE_EXECUTION" and set(experiences) != {"SIGNALS"}):
            raise ControlError("EXPERIENCE_EXCEEDS_BASE_MODE")
        if not isinstance(fees, list) or any(not isinstance(x, str) or x not in FEE_POLICIES for x in fees):
            raise ControlError("INVALID_AUTHORIZED_FEE_POLICIES")
        if mode == "LIVE_EXECUTION" and fee_policy not in fees or mode != "LIVE_EXECUTION" and fees:
            raise ControlError("FEE_POLICY_AUTHORIZATION_MISMATCH")
        resolved = []
        for key in ("db", "scanner_db", "scanner_status"):
            value = raw[key]
            if not isinstance(value, str) or not Path(value).is_absolute() or ".." in Path(value).parts:
                raise ControlError("CONTROL_ABSOLUTE_PATH_REQUIRED")
            resolved.append(Path(value).resolve())
        if len(set(resolved + list(paths.values()))) != len(resolved) + len(paths):
            raise ControlError("CONTROL_PATH_ALIAS")
        if resolved[0].parent != paths["signal_db"].parent or resolved[1].parent != resolved[2].parent:
            raise ControlError("CONTROL_STATE_DIRECTORY_MISMATCH")
        if resolved[1].parent in {paths["signal_db"].parent, getattr(paths.get("execution_db"), "parent", None)}:
            raise ControlError("SCANNER_REQUIRES_SEPARATE_STATE_DIRECTORY")
        expires = float(decimal(raw["authorization_expires_at"], "authorization_expires_at"))
        if not math.isfinite(expires):
            raise ControlError("INVALID_AUTHORIZATION_EXPIRY")
        return cls(*resolved, version, expires,
                   raw["bot_id"], raw["chat_id"], tuple(operators), tuple(experiences), tuple(fees), schedule(raw["trading_schedule_utc"]))

    def identity(self, config):
        return {"protocol": 1, "config_sha256": config.config_sha256,
                "authorization_version": self.authorization_version, "bot_id": self.bot_id, "chat_id": self.chat_id}


def initial_settings(config):
    return {"revision": 0, "experience": "SIGNALS", "paused": True,
            "strategies": sorted(config.strategies), "risk": risk_values(config.risk),
            "fee_policy": config.fee_policy, "min_model_gap": str(config.min_model_gap),
            "min_structural_edge": str(config.min_structural_edge),
            "schedule_utc": config.operator_control.schedule}


def validate_request(row, config, settings, epoch, last_update):
    """Validate at the consuming boundary, including after controller restart."""
    policy = config.operator_control
    body = json.loads(row["body"])
    keys = set(policy.identity(config)) | {"id", "actor", "operation", "data", "revision", "safety_epoch", "created", "expires", "update_id"}
    if not isinstance(body, dict) or set(body) != keys or digest(body) != row["body_hash"] or body["id"] != row["action_id"]:
        raise ControlError("CONTROL_ENVELOPE_INVALID")
    if any(body[k] != v or type(body[k]) is not type(v) for k, v in policy.identity(config).items()):
        raise ControlError("CONTROL_AUTHORIZATION_MISMATCH")
    if type(body["actor"]) is not int or body["actor"] not in policy.operators:
        raise ControlError("CONTROL_ACTOR_UNAUTHORIZED")
    for key in ("revision", "safety_epoch", "update_id"):
        if type(body[key]) is not int or body[key] < 0:
            raise ControlError("CONTROL_COUNTER_INVALID")
    for key in ("created", "expires"):
        if type(body[key]) not in (int, float) or not math.isfinite(body[key]):
            raise ControlError("CONTROL_TIME_INVALID")
    safety = body["operation"] in {"PAUSE","CANCEL"} and body["data"] == {}
    # A callback must be accepted while fresh. Once accepted, a safety reduction
    # is a durable intent, not an expiring authorization to open a position.
    checked_at = row["created"] if safety else time.time()
    if type(checked_at) not in (int,float) or not math.isfinite(checked_at) or not body["created"] <= checked_at < body["expires"] <= body["created"] + 120:
        raise ControlError("CONTROL_REQUEST_EXPIRED")
    if not safety and (body["revision"] != settings["revision"] or body["safety_epoch"] != epoch):
        raise ControlError("CONTROL_REVISION_OR_PAUSE_CHANGED")
    if body["update_id"] <= last_update:
        raise ControlError("CONTROL_UPDATE_REPLAYED")
    if not isinstance(body["data"], dict):
        raise ControlError("CONTROL_DATA_INVALID")
    return body


def risk_values(risk):
    return {k: str(v) if isinstance(v, Decimal) else v for k, v in asdict(risk).items()} if risk else None


def changed_settings(config, current, key, value):
    """Returns an independently bounded new setting and whether authority grows."""
    new = json.loads(canonical(current))
    increases = False
    if key in RiskLimits.__dataclass_fields__:
        if config.risk is None:
            raise ControlError("EXTERNAL_RISK_CONFIGURATION_REQUIRED")
        if key in INTEGER_LIMITS:
            if type(value) is not int:
                raise ControlError("INTEGER_SETTING_REQUIRED")
        else:
            value = str(decimal(value, key, positive=key not in {"max_slippage", "max_fee_per_share"}))
        if Decimal(str(value)) > Decimal(str(getattr(config.risk, key))):
            raise ControlError("SETTING_EXCEEDS_INDEPENDENT_CEILING")
        increases = Decimal(str(value)) > Decimal(str(current["risk"][key]))
        new["risk"][key] = value
        RiskLimits.parse(new["risk"])
    elif key.startswith("strategy:"):
        name = key.removeprefix("strategy:")
        if name not in config.strategies or type(value) is not bool:
            raise ControlError("STRATEGY_NOT_IN_AUTHORIZED_SCOPE")
        if name == "RESULT_LAG" and value:
            raise ControlError("RESULT_LAG_PUBLICATION_FINALITY_UNAVAILABLE")
        selected = set(new["strategies"])
        increases = value and name not in selected
        selected.add(name) if value else selected.discard(name)
        new["strategies"] = sorted(selected)
    elif key == "experience":
        if value not in config.operator_control.experiences:
            raise ControlError("EXPERIENCE_NOT_PREAUTHORIZED")
        increases = value != "SIGNALS"
        new.update(experience=value, paused=True)  # applying a mode is not a resume
    elif key == "fee_policy":
        if value not in config.operator_control.fee_policies:
            raise ControlError("FEE_POLICY_NOT_PREAUTHORIZED")
        increases = value != current[key]
        new[key] = value
    elif key in {"min_model_gap", "min_structural_edge"}:
        parsed = decimal(value, key)
        if not getattr(config, key) <= parsed < 1:
            raise ControlError("THRESHOLD_EXCEEDS_AUTHORIZED_SCOPE")
        increases = parsed < Decimal(current[key])
        new[key] = str(parsed)
    elif key == "schedule_utc":
        value = schedule(value)
        if not schedule_subset(value, config.operator_control.schedule):
            raise ControlError("SCHEDULE_NOT_PREAUTHORIZED")
        increases = not schedule_subset(value, current[key])
        new[key] = value
    else:
        raise ControlError("SETTING_NOT_CHAT_EDITABLE")
    new["revision"] += 1
    return new, increases


def schedule_open(windows, now=None):
    current = datetime.fromtimestamp(time.time() if now is None else now, timezone.utc)
    minute = current.hour * 60 + current.minute
    return any(day == current.weekday() and start <= minute < end for day, start, end in windows)


class ControlStore:
    """Controller-owned nonsecret requests/outbox. Executor access is read-only."""
    def __init__(self, config):
        self.config, self.policy = config, config.operator_control
        self.path = self.policy.db
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.path.is_symlink():
            raise ControlError("CONTROL_DB_SYMLINK")
        with self.connect() as db:
            db.executescript("""
            CREATE TABLE IF NOT EXISTS operator_state(key TEXT PRIMARY KEY,value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS operator_actions(id TEXT PRIMARY KEY,body TEXT NOT NULL,
                body_hash TEXT NOT NULL,message_id INTEGER,expires REAL NOT NULL,state TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS operator_updates(id INTEGER PRIMARY KEY,created REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS operator_requests(seq INTEGER PRIMARY KEY,action_id TEXT NOT NULL UNIQUE,
                body TEXT NOT NULL,body_hash TEXT NOT NULL,created REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS operator_request_results(id TEXT PRIMARY KEY,seq INTEGER NOT NULL,
                result TEXT NOT NULL,created REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS operator_notifications(id TEXT PRIMARY KEY,event TEXT NOT NULL,
                state TEXT NOT NULL,attempts INTEGER NOT NULL DEFAULT 0,due REAL NOT NULL,message_id INTEGER);
            CREATE INDEX IF NOT EXISTS notification_due ON operator_notifications(state,due);
            CREATE TRIGGER IF NOT EXISTS immutable_control_request BEFORE UPDATE ON operator_requests
                BEGIN SELECT RAISE(ABORT,'IMMUTABLE_CONTROL_REQUEST'); END;
            """)
        os.chmod(self.path, 0o640)
        with self.transaction() as db:
            identity = canonical(self.policy.identity(config))
            previous = db.execute("SELECT value FROM operator_state WHERE key='identity'").fetchone()
            if previous and previous[0] != identity:
                raise ControlError("CONTROL_DB_AUTHORIZATION_IDENTITY_CHANGED_USE_REVIEWED_MIGRATION")
            db.execute("INSERT OR IGNORE INTO operator_state VALUES('identity',?)", (identity,))
            db.execute("INSERT OR IGNORE INTO operator_state VALUES('safety_epoch','0')")
            db.execute("INSERT OR IGNORE INTO operator_state VALUES('signals_settings',?)", (canonical(initial_settings(config)),))
            db.execute("INSERT OR IGNORE INTO operator_state VALUES('collection_settings',?)", (canonical(initial_settings(config)),))
            db.execute("UPDATE operator_notifications SET state='UNKNOWN' WHERE state='SENDING'")
        self._anchor = sqlite3.connect(self.path, isolation_level=None)
        self._anchor.execute("PRAGMA journal_mode=WAL")
        self._anchor.execute("SELECT COUNT(*) FROM operator_state")

    def close(self):
        self._anchor.close()

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=10, isolation_level=None)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("PRAGMA synchronous=FULL")
        try:
            yield db
        finally:
            db.close()

    @contextmanager
    def transaction(self):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                yield db
                db.execute("COMMIT")
            except BaseException:
                db.execute("ROLLBACK")
                raise

    def state(self, key, default="0"):
        with self.connect() as db:
            row = db.execute("SELECT value FROM operator_state WHERE key=?", (key,)).fetchone()
        return row[0] if row else default

    def set_state(self, key, value):
        with self.transaction() as db:
            db.execute("INSERT OR REPLACE INTO operator_state VALUES(?,?)", (key, str(value)))

    def action(self, actor, operation, data, revision, *, expires=None):
        if type(actor) is not int or actor not in self.policy.operators or operation not in OPERATIONS | {"NAV", "INPUT"}:
            raise ControlError("ACTION_NOT_AUTHORIZED")
        now = time.time()
        expiry = min(now + 120, expires if expires is not None else now + 120)
        action_id = secrets.token_urlsafe(18)
        body = dict(self.policy.identity(self.config), id=action_id, actor=actor,
                    operation=operation, data=data, revision=revision,
                    safety_epoch=int(self.state("safety_epoch")), created=now, expires=expiry)
        if len(canonical(body)) > 8192:
            raise ControlError("CONTROL_ACTION_TOO_LARGE")
        with self.transaction() as db:
            db.execute("UPDATE operator_actions SET state='EXPIRED' WHERE state='PREVIEW' AND expires<=?", (now,))
            db.execute("DELETE FROM operator_actions WHERE state!='PREVIEW' AND expires<?", (now-86400,))
            db.execute("DELETE FROM operator_updates WHERE id NOT IN (SELECT id FROM operator_updates ORDER BY id DESC LIMIT 1000)")
            if db.execute("SELECT COUNT(*) FROM operator_actions WHERE state='PREVIEW'").fetchone()[0] >= 500:
                if operation not in {"PAUSE","CANCEL"}:
                    raise ControlError("CONTROL_PREVIEW_CAP")
                # Make room for safety commands by retiring an unused preview.
                # No financial request/receipt is removed or replayed.
                db.execute("UPDATE operator_actions SET state='EXPIRED' WHERE id=(SELECT id FROM operator_actions WHERE state='PREVIEW' ORDER BY expires,id LIMIT 1)")
            db.execute("INSERT INTO operator_actions VALUES(?,?,?,NULL,?,'PREVIEW')", (action_id, canonical(body), digest(body), expiry))
        return action_id

    def bind_message(self, action_ids, message_id):
        if type(message_id) is not int or message_id <= 0:
            return
        with self.transaction() as db:
            db.executemany("UPDATE operator_actions SET message_id=? WHERE id=? AND message_id IS NULL AND state='PREVIEW'", ((message_id, identity) for identity in action_ids))

    def click(self, action_id, *, actor, message_id, update_id, revision):
        """An opaque button only consumes its server-held action, once."""
        with self.transaction() as db:
            row = db.execute("SELECT * FROM operator_actions WHERE id=?", (action_id,)).fetchone()
            if not row:
                raise ControlError("BUTTON_UNKNOWN")
            body = json.loads(row["body"])
            if digest(body) != row["body_hash"]:
                raise ControlError("BUTTON_EVIDENCE_CHANGED")
            if (type(actor) is not int or actor != body["actor"] or actor not in self.policy.operators
                or type(message_id) is not int or message_id != row["message_id"]):
                raise ControlError("BUTTON_ACTOR_OR_MESSAGE_MISMATCH")
            if any(body.get(k) != v for k, v in self.policy.identity(self.config).items()):
                raise ControlError("BUTTON_AUTHORIZATION_CHANGED")
            if row["state"] != "PREVIEW":
                raise ControlError("BUTTON_ALREADY_USED")
            if not body["created"] <= time.time() < body["expires"]:
                raise ControlError("BUTTON_EXPIRED")
            if body["revision"] != revision or body["safety_epoch"] != int(self.state("safety_epoch")):
                raise ControlError("BUTTON_STATE_CHANGED")
            if type(update_id) is not int or update_id < 0:
                raise ControlError("UPDATE_ID_INVALID")
            previous = db.execute("SELECT MAX(id) FROM operator_updates").fetchone()[0]
            if previous is not None and update_id <= previous:
                raise ControlError("UPDATE_STALE_OR_REPLAYED")
            db.execute("INSERT INTO operator_updates VALUES(?,?)", (update_id, time.time()))
            db.execute("UPDATE operator_actions SET state='USED' WHERE id=?", (action_id,))
            if body["operation"] in OPERATIONS:
                if body["operation"] in {"PAUSE", "CANCEL"}:
                    db.execute("UPDATE operator_state SET value=CAST(value AS INTEGER)+1 WHERE key='safety_epoch'")
                body["update_id"] = update_id
                db.execute("INSERT INTO operator_requests(action_id,body,body_hash,created) VALUES(?,?,?,?)",
                           (action_id, canonical(body), digest(body), time.time()))
            return body

    def compact_notifications(self):
        # Keep unresolved/ambiguous delivery evidence; bound the routine receipt cache.
        # The executor retains the original financial audit independently.
        with self.transaction() as db:
            db.execute("DELETE FROM operator_notifications WHERE state='SENT' AND id NOT IN (SELECT id FROM operator_notifications WHERE state='SENT' ORDER BY due DESC LIMIT 5000)")


class ControlReader:
    def __init__(self, policy):
        self.path = policy.db

    def query(self, sql, params=()):
        with sqlite3.connect(self.path.as_uri() + "?mode=ro", uri=True, timeout=2) as db:
            db.row_factory = sqlite3.Row
            return [dict(row) for row in db.execute(sql, params)]

    def state(self, key, default="0"):
        rows = self.query("SELECT value FROM operator_state WHERE key=?", (key,))
        return rows[0]["value"] if rows else default

    def latest(self):
        return self.query("SELECT COALESCE(MAX(seq),0) AS seq FROM operator_requests")[0]["seq"]

    def requests(self, after):
        return self.query("SELECT * FROM operator_requests WHERE seq>? ORDER BY seq LIMIT 25", (after,))
