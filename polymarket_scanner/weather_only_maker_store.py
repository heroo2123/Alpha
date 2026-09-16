from __future__ import annotations

"""Restart-safe SQLite store for weather maker *shadow* evidence.

The store persists immutable virtual-order state plus a hash-chained research event
log. It deliberately has no authenticated order/fill schema and no network methods.
Processed public-trade IDs live inside the order state, so overlapping polling windows
after restart remain idempotent.

State replacement and its audit event occur in one SQLite transaction. Optimistic
state-SHA comparison rejects stale writers. Any authority flag drift, trade-history
regression, fill regression or queue increase fails closed.
"""

import hashlib
import json
import math
import os
import sqlite3
import time
from pathlib import Path

from .weather_only_maker_shadow import (
    CANCELLED,
    EXPIRED,
    MAX_PROCESSED_TRADE_IDS,
    PARTIALLY_SIMULATED,
    RESTING,
    SIMULATED_FILLED,
    VirtualMakerOrder,
    WeatherMakerShadowError,
)


WEATHER_MAKER_STORE_VERSION = "weather_maker_shadow_store_v1_atomic_hash_chain_restart_idempotence"
_ZERO_SHA = "0" * 64
_VALID_STATUS = {RESTING, PARTIALLY_SIMULATED, SIMULATED_FILLED, CANCELLED, EXPIRED}
_VALID_TRANSITIONS = {
    RESTING: {RESTING, PARTIALLY_SIMULATED, SIMULATED_FILLED, CANCELLED, EXPIRED},
    PARTIALLY_SIMULATED: {PARTIALLY_SIMULATED, SIMULATED_FILLED, CANCELLED, EXPIRED},
    SIMULATED_FILLED: {SIMULATED_FILLED},
    CANCELLED: {CANCELLED},
    EXPIRED: {EXPIRED},
}


class WeatherMakerStoreError(RuntimeError):
    def __init__(self, code: str):
        self.code = str(code)
        super().__init__(self.code)


def _canonical(value: object) -> str:
    try:
        return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False)
    except (TypeError, ValueError):
        raise WeatherMakerStoreError("MAKER_STORE_JSON_INVALID") from None


def _sha_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _timestamp(value: float | None) -> float:
    if value is None:
        return time.time()
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise WeatherMakerStoreError("MAKER_STORE_TIMESTAMP_INVALID")
    number = float(value)
    if not math.isfinite(number) or number < 0.0:
        raise WeatherMakerStoreError("MAKER_STORE_TIMESTAMP_INVALID")
    return number


def _order_payload(order: VirtualMakerOrder) -> dict:
    if not isinstance(order, VirtualMakerOrder):
        raise WeatherMakerStoreError("MAKER_STORE_ORDER_TYPE_INVALID")
    payload = order.as_dict()
    if (
        payload.get("research_only") is not True
        or payload.get("actual_order_placed") is not False
        or payload.get("actual_fill_authority") is not False
        or payload.get("financial_authority") is not False
    ):
        raise WeatherMakerStoreError("MAKER_STORE_AUTHORITY_BOUNDARY_BROKEN")
    if order.status not in _VALID_STATUS:
        raise WeatherMakerStoreError("MAKER_STORE_STATUS_INVALID")
    if not order.order_id or not order.token_id or not order.event_id:
        raise WeatherMakerStoreError("MAKER_STORE_ORDER_IDENTITY_INVALID")
    numbers = (
        order.bid_price,
        order.shares,
        order.created_at,
        order.expires_at,
        order.fair_as_of,
        order.created_book_received_at,
        order.queue_ahead_shares,
        order.simulated_filled_shares,
    )
    if any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(float(v)) for v in numbers):
        raise WeatherMakerStoreError("MAKER_STORE_ORDER_NUMBER_INVALID")
    if not 0.0 < float(order.bid_price) < 1.0 or float(order.shares) <= 0.0:
        raise WeatherMakerStoreError("MAKER_STORE_ORDER_NUMBER_INVALID")
    if float(order.queue_ahead_shares) < 0.0 or not 0.0 <= float(order.simulated_filled_shares) <= float(order.shares) + 1e-9:
        raise WeatherMakerStoreError("MAKER_STORE_ORDER_NUMBER_INVALID")
    history = tuple(str(value) for value in order.processed_trade_ids)
    if len(history) > MAX_PROCESSED_TRADE_IDS or len(set(history)) != len(history) or any(not value for value in history):
        raise WeatherMakerStoreError("MAKER_STORE_TRADE_HISTORY_INVALID")
    return payload


def _order_text_and_sha(order: VirtualMakerOrder) -> tuple[str, str]:
    text = _canonical(_order_payload(order))
    return text, _sha_text(text)


def _order_from_payload(payload: object) -> VirtualMakerOrder:
    if not isinstance(payload, dict):
        raise WeatherMakerStoreError("MAKER_STORE_ORDER_PAYLOAD_INVALID")
    if (
        payload.get("research_only") is not True
        or payload.get("actual_order_placed") is not False
        or payload.get("actual_fill_authority") is not False
        or payload.get("financial_authority") is not False
    ):
        raise WeatherMakerStoreError("MAKER_STORE_AUTHORITY_BOUNDARY_BROKEN")
    required = (
        "version", "order_id", "policy_id", "event_id", "market_id", "condition_id",
        "token_id", "outcome", "bid_price", "shares", "created_at", "expires_at",
        "fair_model_version", "fair_evidence_sha256", "fair_as_of",
        "contract_evidence_sha256", "source_generation", "market_parameter_sha256",
        "created_book_hash", "created_book_received_at", "queue_ahead_shares",
        "simulated_filled_shares", "processed_trade_ids", "status",
    )
    if any(name not in payload for name in required):
        raise WeatherMakerStoreError("MAKER_STORE_ORDER_PAYLOAD_MISSING_FIELD")
    history = payload.get("processed_trade_ids")
    if not isinstance(history, (list, tuple)):
        raise WeatherMakerStoreError("MAKER_STORE_TRADE_HISTORY_INVALID")
    try:
        order = VirtualMakerOrder(
            version=str(payload["version"]),
            order_id=str(payload["order_id"]),
            policy_id=str(payload["policy_id"]),
            event_id=str(payload["event_id"]),
            market_id=str(payload["market_id"]),
            condition_id=str(payload["condition_id"]),
            token_id=str(payload["token_id"]),
            outcome=str(payload["outcome"]),
            bid_price=float(payload["bid_price"]),
            shares=float(payload["shares"]),
            created_at=float(payload["created_at"]),
            expires_at=float(payload["expires_at"]),
            fair_model_version=str(payload["fair_model_version"]),
            fair_evidence_sha256=str(payload["fair_evidence_sha256"]),
            fair_as_of=float(payload["fair_as_of"]),
            contract_evidence_sha256=str(payload["contract_evidence_sha256"]),
            source_generation=str(payload["source_generation"]),
            market_parameter_sha256=str(payload["market_parameter_sha256"]),
            created_book_hash=None if payload["created_book_hash"] is None else str(payload["created_book_hash"]),
            created_book_received_at=float(payload["created_book_received_at"]),
            queue_ahead_shares=float(payload["queue_ahead_shares"]),
            simulated_filled_shares=float(payload["simulated_filled_shares"]),
            processed_trade_ids=tuple(str(value) for value in history),
            status=str(payload["status"]),
        )
    except (TypeError, ValueError, OverflowError):
        raise WeatherMakerStoreError("MAKER_STORE_ORDER_PAYLOAD_INVALID") from None
    _order_payload(order)
    return order


def _assert_transition(previous: VirtualMakerOrder, updated: VirtualMakerOrder) -> None:
    immutable = (
        "version", "order_id", "policy_id", "event_id", "market_id", "condition_id",
        "token_id", "outcome", "bid_price", "shares", "created_at", "expires_at",
        "fair_model_version", "fair_evidence_sha256", "fair_as_of",
        "contract_evidence_sha256", "source_generation", "market_parameter_sha256",
        "created_book_hash", "created_book_received_at",
    )
    if any(getattr(previous, name) != getattr(updated, name) for name in immutable):
        raise WeatherMakerStoreError("MAKER_STORE_IMMUTABLE_IDENTITY_DRIFT")
    if updated.status not in _VALID_TRANSITIONS.get(previous.status, set()):
        raise WeatherMakerStoreError("MAKER_STORE_STATUS_REGRESSION")
    if float(updated.simulated_filled_shares) + 1e-12 < float(previous.simulated_filled_shares):
        raise WeatherMakerStoreError("MAKER_STORE_FILL_REGRESSION")
    if float(updated.queue_ahead_shares) > float(previous.queue_ahead_shares) + 1e-12:
        raise WeatherMakerStoreError("MAKER_STORE_QUEUE_INCREASE")
    old_history = tuple(previous.processed_trade_ids)
    new_history = tuple(updated.processed_trade_ids)
    if new_history[: len(old_history)] != old_history:
        raise WeatherMakerStoreError("MAKER_STORE_TRADE_HISTORY_REGRESSION")


class WeatherMakerShadowStore:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        if not self.path.is_absolute():
            raise WeatherMakerStoreError("MAKER_STORE_PATH_NOT_ABSOLUTE")
        if self.path.exists() and (self.path.is_symlink() or not self.path.is_file()):
            raise WeatherMakerStoreError("MAKER_STORE_FILE_INVALID")
        if not self.path.parent.is_dir() or self.path.parent.is_symlink():
            raise WeatherMakerStoreError("MAKER_STORE_PARENT_INVALID")
        existed = self.path.exists()
        self.db = sqlite3.connect(self.path, timeout=5.0, isolation_level=None)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA foreign_keys=ON")
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.execute("PRAGMA busy_timeout=5000")
        self._init_schema()
        if not existed:
            os.chmod(self.path, 0o600)
        mode = self.path.stat().st_mode & 0o777
        if mode & 0o077:
            self.db.close()
            raise WeatherMakerStoreError("MAKER_STORE_PERMISSIONS_TOO_BROAD")

    def close(self) -> None:
        self.db.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        self.close()

    def _init_schema(self) -> None:
        self.db.executescript(
            """
            CREATE TABLE IF NOT EXISTS weather_maker_shadow_orders (
                order_id TEXT PRIMARY KEY,
                store_version TEXT NOT NULL,
                state_json TEXT NOT NULL,
                state_sha256 TEXT NOT NULL CHECK(length(state_sha256)=64),
                updated_at REAL NOT NULL,
                actual_order_placed INTEGER NOT NULL DEFAULT 0 CHECK(actual_order_placed=0),
                actual_fill_authority INTEGER NOT NULL DEFAULT 0 CHECK(actual_fill_authority=0),
                financial_authority INTEGER NOT NULL DEFAULT 0 CHECK(financial_authority=0)
            );
            CREATE TABLE IF NOT EXISTS weather_maker_shadow_events (
                seq INTEGER PRIMARY KEY AUTOINCREMENT,
                order_id TEXT NOT NULL,
                event_type TEXT NOT NULL,
                created_at REAL NOT NULL,
                payload_json TEXT NOT NULL,
                payload_sha256 TEXT NOT NULL CHECK(length(payload_sha256)=64),
                state_sha256 TEXT NOT NULL CHECK(length(state_sha256)=64),
                prev_event_sha256 TEXT NOT NULL CHECK(length(prev_event_sha256)=64),
                event_sha256 TEXT NOT NULL UNIQUE CHECK(length(event_sha256)=64),
                financial_authority INTEGER NOT NULL DEFAULT 0 CHECK(financial_authority=0),
                FOREIGN KEY(order_id) REFERENCES weather_maker_shadow_orders(order_id)
            );
            CREATE INDEX IF NOT EXISTS idx_weather_maker_shadow_events_order_seq
                ON weather_maker_shadow_events(order_id, seq);
            """
        )

    def _latest_event_sha(self, order_id: str) -> str:
        row = self.db.execute(
            "SELECT event_sha256 FROM weather_maker_shadow_events WHERE order_id=? ORDER BY seq DESC LIMIT 1",
            (order_id,),
        ).fetchone()
        return str(row[0]) if row else _ZERO_SHA

    def _append_event(self, order_id: str, event_type: str, payload: object, state_sha: str, created_at: float) -> str:
        kind = str(event_type or "").strip().upper()
        if not kind:
            raise WeatherMakerStoreError("MAKER_STORE_EVENT_TYPE_INVALID")
        if isinstance(payload, dict) and payload.get("financial_authority") not in (None, False):
            raise WeatherMakerStoreError("MAKER_STORE_EVENT_AUTHORITY_BROKEN")
        payload_text = _canonical(payload)
        payload_sha = _sha_text(payload_text)
        previous = self._latest_event_sha(order_id)
        event_body = {
            "order_id": order_id,
            "event_type": kind,
            "created_at": created_at,
            "payload_sha256": payload_sha,
            "state_sha256": state_sha,
            "prev_event_sha256": previous,
        }
        event_sha = _sha_text(_canonical(event_body))
        self.db.execute(
            """INSERT INTO weather_maker_shadow_events
               (order_id,event_type,created_at,payload_json,payload_sha256,state_sha256,prev_event_sha256,event_sha256,financial_authority)
               VALUES (?,?,?,?,?,?,?,?,0)""",
            (order_id, kind, created_at, payload_text, payload_sha, state_sha, previous, event_sha),
        )
        return event_sha

    def save_new_order(self, order: VirtualMakerOrder, *, recorded_at: float | None = None) -> VirtualMakerOrder:
        state_text, state_sha = _order_text_and_sha(order)
        at = _timestamp(recorded_at)
        try:
            self.db.execute("BEGIN IMMEDIATE")
            existing = self.db.execute(
                "SELECT state_sha256 FROM weather_maker_shadow_orders WHERE order_id=?",
                (order.order_id,),
            ).fetchone()
            if existing is not None:
                if str(existing[0]) != state_sha:
                    raise WeatherMakerStoreError("MAKER_STORE_ORDER_ID_CONFLICT")
                self.db.execute("COMMIT")
                return self.load_order(order.order_id)
            self.db.execute(
                """INSERT INTO weather_maker_shadow_orders
                   (order_id,store_version,state_json,state_sha256,updated_at,actual_order_placed,actual_fill_authority,financial_authority)
                   VALUES (?,?,?,?,?,0,0,0)""",
                (order.order_id, WEATHER_MAKER_STORE_VERSION, state_text, state_sha, at),
            )
            self._append_event(order.order_id, "ORDER_CREATED", {"state_sha256": state_sha}, state_sha, at)
            self.db.execute("COMMIT")
        except Exception:
            if self.db.in_transaction:
                self.db.execute("ROLLBACK")
            raise
        return order

    def load_order(self, order_id: str) -> VirtualMakerOrder:
        oid = str(order_id or "").strip()
        if not oid:
            raise WeatherMakerStoreError("MAKER_STORE_ORDER_ID_INVALID")
        row = self.db.execute(
            "SELECT store_version,state_json,state_sha256,actual_order_placed,actual_fill_authority,financial_authority FROM weather_maker_shadow_orders WHERE order_id=?",
            (oid,),
        ).fetchone()
        if row is None:
            raise WeatherMakerStoreError("MAKER_STORE_ORDER_NOT_FOUND")
        if str(row["store_version"]) != WEATHER_MAKER_STORE_VERSION:
            raise WeatherMakerStoreError("MAKER_STORE_VERSION_MISMATCH")
        if any(int(row[name]) != 0 for name in ("actual_order_placed", "actual_fill_authority", "financial_authority")):
            raise WeatherMakerStoreError("MAKER_STORE_AUTHORITY_BOUNDARY_BROKEN")
        text = str(row["state_json"])
        if _sha_text(text) != str(row["state_sha256"]):
            raise WeatherMakerStoreError("MAKER_STORE_STATE_DIGEST_MISMATCH")
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            raise WeatherMakerStoreError("MAKER_STORE_STATE_JSON_INVALID") from None
        order = _order_from_payload(payload)
        if order.order_id != oid:
            raise WeatherMakerStoreError("MAKER_STORE_ORDER_ID_MISMATCH")
        return order

    def update_order(
        self,
        previous: VirtualMakerOrder,
        updated: VirtualMakerOrder,
        *,
        event_type: str,
        payload: object,
        recorded_at: float | None = None,
    ) -> VirtualMakerOrder:
        _order_payload(previous)
        _order_payload(updated)
        _assert_transition(previous, updated)
        previous_text, previous_sha = _order_text_and_sha(previous)
        del previous_text
        updated_text, updated_sha = _order_text_and_sha(updated)
        at = _timestamp(recorded_at)
        try:
            self.db.execute("BEGIN IMMEDIATE")
            row = self.db.execute(
                "SELECT state_sha256 FROM weather_maker_shadow_orders WHERE order_id=?",
                (previous.order_id,),
            ).fetchone()
            if row is None:
                raise WeatherMakerStoreError("MAKER_STORE_ORDER_NOT_FOUND")
            if str(row[0]) != previous_sha:
                raise WeatherMakerStoreError("MAKER_STORE_STALE_WRITER")
            self.db.execute(
                "UPDATE weather_maker_shadow_orders SET state_json=?,state_sha256=?,updated_at=? WHERE order_id=?",
                (updated_text, updated_sha, at, previous.order_id),
            )
            self._append_event(previous.order_id, event_type, payload, updated_sha, at)
            self.db.execute("COMMIT")
        except Exception:
            if self.db.in_transaction:
                self.db.execute("ROLLBACK")
            raise
        return updated

    def append_research_event(
        self,
        order_id: str,
        *,
        event_type: str,
        payload: object,
        recorded_at: float | None = None,
    ) -> str:
        at = _timestamp(recorded_at)
        try:
            self.db.execute("BEGIN IMMEDIATE")
            order = self.load_order(order_id)
            _, state_sha = _order_text_and_sha(order)
            event_sha = self._append_event(order.order_id, event_type, payload, state_sha, at)
            self.db.execute("COMMIT")
            return event_sha
        except Exception:
            if self.db.in_transaction:
                self.db.execute("ROLLBACK")
            raise

    def audit_order(self, order_id: str) -> dict:
        order = self.load_order(order_id)
        _, current_state_sha = _order_text_and_sha(order)
        rows = self.db.execute(
            """SELECT seq,event_type,created_at,payload_json,payload_sha256,state_sha256,prev_event_sha256,event_sha256,financial_authority
               FROM weather_maker_shadow_events WHERE order_id=? ORDER BY seq""",
            (order.order_id,),
        ).fetchall()
        if not rows:
            raise WeatherMakerStoreError("MAKER_STORE_EVENT_CHAIN_MISSING")
        previous = _ZERO_SHA
        for row in rows:
            if int(row["financial_authority"]) != 0:
                raise WeatherMakerStoreError("MAKER_STORE_EVENT_AUTHORITY_BROKEN")
            payload_text = str(row["payload_json"])
            if _sha_text(payload_text) != str(row["payload_sha256"]):
                raise WeatherMakerStoreError("MAKER_STORE_EVENT_PAYLOAD_DIGEST_MISMATCH")
            if str(row["prev_event_sha256"]) != previous:
                raise WeatherMakerStoreError("MAKER_STORE_EVENT_CHAIN_BROKEN")
            body = {
                "order_id": order.order_id,
                "event_type": str(row["event_type"]),
                "created_at": float(row["created_at"]),
                "payload_sha256": str(row["payload_sha256"]),
                "state_sha256": str(row["state_sha256"]),
                "prev_event_sha256": str(row["prev_event_sha256"]),
            }
            calculated = _sha_text(_canonical(body))
            if calculated != str(row["event_sha256"]):
                raise WeatherMakerStoreError("MAKER_STORE_EVENT_DIGEST_MISMATCH")
            previous = calculated
        if str(rows[-1]["state_sha256"]) != current_state_sha:
            raise WeatherMakerStoreError("MAKER_STORE_FINAL_STATE_CHAIN_MISMATCH")
        integrity = self.db.execute("PRAGMA integrity_check").fetchone()
        if integrity is None or str(integrity[0]).lower() != "ok":
            raise WeatherMakerStoreError("MAKER_STORE_SQLITE_INTEGRITY_FAILED")
        return {
            "version": WEATHER_MAKER_STORE_VERSION,
            "order_id": order.order_id,
            "event_count": len(rows),
            "last_event_sha256": previous,
            "state_sha256": current_state_sha,
            "processed_trade_id_count": len(order.processed_trade_ids),
            "simulated_filled_shares": order.simulated_filled_shares,
            "status": order.status,
            "sqlite_integrity": "ok",
            "actual_order_placed": False,
            "actual_fill_authority": False,
            "financial_authority": False,
        }
