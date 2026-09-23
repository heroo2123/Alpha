"""Bounded, append-only nonfinancial evidence archive and causal replay.

An archive records receipt, not a vendor's backdated sensor time, as availability.
It grants no trading, calibration, settlement, or model-promotion authority.
SQLite durability is not independent tamper-proof storage: keep signed release and
snapshot identities outside this development process for eventual acceptance.
"""
from __future__ import annotations

from contextlib import closing, contextmanager
from dataclasses import asdict, dataclass
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import sqlite3
import time
from typing import Callable


VERSION = "alpha_v11_evidence_v1"
KINDS = {"BOOK", "TRADE", "OFFICIAL_OBSERVATION", "PWS_OBSERVATION", "MODEL",
         "RULES", "STATION_METADATA", "FEATURES", "LABEL"}
CLASSES = {"PUBLIC_OBSERVED", "SYNTHETIC", "HISTORICAL_AVAILABILITY_UNKNOWN"}
AUDIT_KINDS = {"REGISTRY", "RULE_STATE", "MEASUREMENT", "RUNTIME_STATUS", "MODEL_EVENT",
               "COORDINATOR_EVENT", "OPERATOR_EVENT", "SOURCE_SCHEDULE"}
FUNNEL_STAGES = {"DISCOVERED", "SEMANTICALLY_SUPPORTED", "SOURCE_READY", "EVALUATED",
                 "CANDIDATE", "EV_RISK_ACCEPTED", "SUBMISSION_READY", "ADMITTED", "RESOLVED"}
SECRET_KEYS = {"private_key", "mnemonic", "seed_phrase", "api_key", "api_secret",
               "api_passphrase", "authorization", "password", "bot_token", "credential"}


class EvidenceError(RuntimeError):
    pass


def canonical(value: object) -> str:
    def inspect(item: object, depth: int = 0) -> None:
        if depth > 24:
            raise EvidenceError("PAYLOAD_DEPTH_LIMIT")
        if isinstance(item, dict):
            for key, val in item.items():
                if not isinstance(key, str):
                    raise EvidenceError("NON_STRING_JSON_KEY")
                if key.lower() in SECRET_KEYS:
                    raise EvidenceError("SENSITIVE_FIELD_REFUSED")
                inspect(val, depth + 1)
        elif isinstance(item, (list, tuple)):
            for val in item:
                inspect(val, depth + 1)
    inspect(value)
    try:
        return json.dumps(value, sort_keys=True, separators=(",", ":"),
                          ensure_ascii=True, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise EvidenceError("INVALID_JSON") from exc


def digest(value: object) -> str:
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def finite(value: object, *, nonnegative: bool = True) -> float:
    if type(value) not in (int, float) or not math.isfinite(value):
        raise EvidenceError("NONFINITE_OR_NONNUMERIC")
    if nonnegative and value < 0:
        raise EvidenceError("NEGATIVE_NUMBER")
    return float(value)


def identity(value: str, *, maximum: int = 160) -> str:
    if not isinstance(value, str) or not value or len(value) > maximum or any(ord(c) < 32 for c in value):
        raise EvidenceError("INVALID_IDENTITY")
    return value


def sha(value: str, length: int = 64) -> str:
    if not isinstance(value, str) or not re.fullmatch(f"[0-9a-f]{{{length}}}", value):
        raise EvidenceError("INVALID_DIGEST")
    return value


@dataclass(frozen=True)
class Limits:
    payload_bytes: int = 1024 * 1024
    max_database_bytes: int = 256 * 1024 * 1024
    minimum_free_bytes: int = 64 * 1024 * 1024
    max_records: int = 250_000
    max_evidence_per_decision: int = 64

    def __post_init__(self):
        if any(type(v) is not int or v <= 0 for v in asdict(self).values()):
            raise EvidenceError("INVALID_LIMITS")
        if self.payload_bytes > 8 * 1024**2 or self.max_evidence_per_decision > 256:
            raise EvidenceError("INVALID_LIMITS")


@dataclass(frozen=True)
class ReleaseBinding:
    code_commit: str
    code_tree: str
    config_sha256: str
    bundle_sha256: str
    rule_fingerprint: str

    def __post_init__(self):
        sha(self.code_commit, 40)
        sha(self.code_tree, 40)
        for v in (self.config_sha256, self.bundle_sha256, self.rule_fingerprint):
            sha(v)


class EvidenceStore:
    """One database per research namespace; no upgrade/migration of V10 databases."""

    def __init__(self, path: Path, namespace: str, *, limits: Limits = Limits(),
                 clock: Callable[[], float] = time.time):
        if not re.fullmatch(r"V11_PAPER|(?:CHALLENGER|ABLATION):[a-zA-Z0-9_-]{1,64}", namespace):
            raise EvidenceError("NONFINANCIAL_NAMESPACE_REQUIRED")
        self.path, self.namespace, self.limits, self.clock = Path(path), namespace, limits, clock
        if not self.path.is_absolute() or ".." in self.path.parts:
            raise EvidenceError("ABSOLUTE_PATH_REQUIRED")
        for p in (self.path, *self.path.parents):
            if p.is_symlink():
                raise EvidenceError("SYMLINK_PATH_REFUSED")
        if not self.path.parent.is_dir():
            raise EvidenceError("PRIVATE_PARENT_REQUIRED")
        if self.path.parent.stat().st_mode & 0o077:
            raise EvidenceError("PRIVATE_PARENT_REQUIRED")
        if self.path.exists():
            if not self.path.is_file() or self.path.stat().st_mode & 0o077:
                raise EvidenceError("PRIVATE_REGULAR_DATABASE_REQUIRED")
            # Read-only inspection BEFORE WAL or schema pragmas can mutate a file.
            with closing(sqlite3.connect(self.path.as_uri() + "?mode=ro", uri=True)) as db:
                tables = {r[0] for r in db.execute("SELECT name FROM sqlite_schema WHERE type='table'")}
                if "v11_meta" not in tables:
                    raise EvidenceError("FOREIGN_DATABASE_REFUSED")
                meta = dict(db.execute("SELECT key,value FROM v11_meta"))
                if meta.get("version") != VERSION or meta.get("namespace") != namespace:
                    raise EvidenceError("NAMESPACE_OR_VERSION_MISMATCH")
        else:
            fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            os.close(fd)
            with self._connect() as db:
                db.executescript("""
                    CREATE TABLE v11_meta(key TEXT PRIMARY KEY,value TEXT NOT NULL);
                    CREATE TABLE v11_records(
                        seq INTEGER PRIMARY KEY, record_id TEXT NOT NULL UNIQUE,
                        kind TEXT NOT NULL, event_id TEXT NOT NULL,
                        recorded_at REAL NOT NULL, available_at REAL NOT NULL,
                        body TEXT NOT NULL, body_sha256 TEXT NOT NULL);
                    CREATE INDEX v11_causal ON v11_records(event_id,available_at,seq);
                    CREATE INDEX v11_kind ON v11_records(kind,seq);
                    CREATE TRIGGER v11_no_update BEFORE UPDATE ON v11_records BEGIN
                        SELECT RAISE(ABORT,'APPEND_ONLY'); END;
                    CREATE TRIGGER v11_no_delete BEFORE DELETE ON v11_records BEGIN
                        SELECT RAISE(ABORT,'APPEND_ONLY'); END;
                """)
                db.executemany("INSERT INTO v11_meta VALUES(?,?)",
                               [("version", VERSION), ("namespace", namespace)])

    @contextmanager
    def _connect(self):
        with closing(sqlite3.connect(self.path, timeout=1.0)) as db:
            db.row_factory = sqlite3.Row
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("PRAGMA synchronous=FULL")
            db.execute("PRAGMA busy_timeout=1000")
            db.execute("PRAGMA wal_autocheckpoint=256")
            db.execute("PRAGMA foreign_keys=ON")
            with db:
                yield db

    def _budget(self, db: sqlite3.Connection, encoded_bytes: int) -> None:
        if encoded_bytes > self.limits.payload_bytes:
            raise EvidenceError("RECORD_BYTES_LIMIT")
        size = sum(p.stat().st_size for p in (self.path, Path(str(self.path) + "-wal")) if p.exists())
        if size + encoded_bytes + 32768 > self.limits.max_database_bytes:
            raise EvidenceError("ARCHIVE_BYTES_LIMIT")
        if shutil.disk_usage(self.path.parent).free < self.limits.minimum_free_bytes + encoded_bytes + 32768:
            raise EvidenceError("ARCHIVE_DISK_HEADROOM")
        if db.execute("SELECT COUNT(*) FROM v11_records").fetchone()[0] >= self.limits.max_records:
            raise EvidenceError("ARCHIVE_RECORD_LIMIT")

    def _append(self, record_id: str, kind: str, event_id: str, body: dict,
                available_at: float, recorded_at: float,
                expected_previous_seq: int | None = None) -> dict:
        identity(record_id)
        identity(event_id)
        body = dict(body, namespace=self.namespace, financial_authority=False,
                    record_id=record_id, kind=kind, event_id=event_id,
                    recorded_at=recorded_at, available_at=available_at)
        encoded = canonical(body)
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            found = db.execute("SELECT * FROM v11_records WHERE record_id=?", (record_id,)).fetchone()
            if found:
                if found["body"] != encoded or found["kind"] != kind or found["event_id"] != event_id:
                    raise EvidenceError("RECORD_ID_CONFLICT")
                return self._decode(found)
            if expected_previous_seq is not None:
                if type(expected_previous_seq) is not int or expected_previous_seq < 0:
                    raise EvidenceError("AUDIT_CAS_INVALID")
                prior = db.execute("SELECT COALESCE(MAX(seq),0) FROM v11_records WHERE kind=? AND event_id=?",
                                   (kind, event_id)).fetchone()[0]
                if prior != expected_previous_seq:
                    raise EvidenceError("AUDIT_STATE_CHANGED")
            self._budget(db, len(encoded.encode()))
            last = db.execute("SELECT recorded_at FROM v11_records ORDER BY seq DESC LIMIT 1").fetchone()
            if last and recorded_at < last[0]:
                raise EvidenceError("CLOCK_REGRESSION")
            db.execute("INSERT INTO v11_records(record_id,kind,event_id,recorded_at,available_at,body,body_sha256) "
                       "VALUES(?,?,?,?,?,?,?)", (record_id, kind, event_id, recorded_at,
                                               available_at, encoded, digest(body)))
            row = db.execute("SELECT * FROM v11_records WHERE record_id=?", (record_id,)).fetchone()
            return self._decode(row)

    @staticmethod
    def _decode(row) -> dict:
        body = json.loads(row["body"])
        if (digest(body) != row["body_sha256"] or body["record_id"] != row["record_id"]
                or body["kind"] != row["kind"] or body["event_id"] != row["event_id"]
                or body["recorded_at"] != row["recorded_at"]
                or body["available_at"] != row["available_at"]):
            raise EvidenceError("RECORD_INTEGRITY_FAILED")
        return {"seq": row["seq"], "id": row["record_id"], "kind": row["kind"],
                "event_id": row["event_id"], "sha256": row["body_sha256"], "body": body}

    def get(self, record_id: str) -> dict:
        with self._connect() as db:
            row = db.execute("SELECT * FROM v11_records WHERE record_id=?", (record_id,)).fetchone()
            if not row:
                raise EvidenceError("EVIDENCE_MISSING")
            return self._decode(row)

    def records(self, *, kind: str, event_id: str | None = None,
                after_seq: int = 0, limit: int = 200) -> list[dict]:
        """Bounded namespace-local audit queries; no cross-ledger read fallback."""
        if (kind not in KINDS | AUDIT_KINDS | {"DECISION", "FUNNEL", "SOURCE_RESULT"}
                or type(after_seq) is not int or after_seq < 0
                or type(limit) is not int or not 1 <= limit <= 1000):
            raise EvidenceError("AUDIT_QUERY_INVALID")
        if event_id is not None:
            identity(event_id)
        with self._connect() as db:
            rows = db.execute("SELECT * FROM v11_records WHERE kind=? AND seq>? "
                              "AND (? IS NULL OR event_id=?) ORDER BY seq LIMIT ?",
                              (kind, after_seq, event_id, event_id, limit)).fetchall()
        return [self._decode(row) for row in rows]

    def audit(self, record_id: str, *, event_id: str, kind: str, details: dict,
              evidence_ids: tuple[str, ...] = (), expected_previous_seq: int | None = None) -> dict:
        if kind not in AUDIT_KINDS or not isinstance(details, dict):
            raise EvidenceError("AUDIT_KIND_INVALID")
        if (len(evidence_ids) > self.limits.max_evidence_per_decision
                or len(set(evidence_ids)) != len(evidence_ids)):
            raise EvidenceError("EVIDENCE_SET_INVALID")
        at = finite(self.clock())
        references = [self.get(key) for key in evidence_ids]
        if any(r["body"]["recorded_at"] > at for r in references):
            raise EvidenceError("AUDIT_CLOCK_REGRESSION")
        return self._append(record_id, kind, event_id,
                            {"details": details, "evidence": [{"id": r["id"], "sha256": r["sha256"]}
                                                            for r in references]}, at, at,
                            expected_previous_seq=expected_previous_seq)

    def capture(self, record_id: str, *, event_id: str, kind: str, provider: str,
                source_identity: str, revision: str, payload: dict,
                observed_at: float | None = None, issued_at: float | None = None,
                published_at: float | None = None, evidence_class: str = "PUBLIC_OBSERVED") -> dict:
        if kind not in KINDS or evidence_class not in CLASSES or not isinstance(payload, dict):
            raise EvidenceError("CAPTURE_SCHEMA_INVALID")
        at = finite(self.clock())
        for timestamp in (observed_at, issued_at, published_at):
            if timestamp is not None and finite(timestamp) > at:
                raise EvidenceError("SOURCE_TIME_IN_FUTURE")
        body = {"provider": identity(provider), "source_identity": identity(source_identity),
                "revision": identity(revision), "payload": payload, "observed_at": observed_at,
                "issued_at": issued_at, "published_at": published_at, "received_at": at,
                "evidence_class": evidence_class, "source_kind": kind}
        return self._append(record_id, kind, event_id, body, at, at)

    def causal_inputs(self, event_id: str, cutoff: float, *, after_seq: int = 0,
                      limit: int = 200) -> list[dict]:
        finite(cutoff)
        if type(limit) is not int or not 1 <= limit <= 1000 or type(after_seq) is not int or after_seq < 0:
            raise EvidenceError("PAGE_BOUNDS_INVALID")
        with self._connect() as db:
            kinds = tuple(sorted(KINDS - {"LABEL"}))
            slots = ",".join("?" for _ in kinds)
            rows = db.execute("SELECT * FROM v11_records WHERE event_id=? AND available_at<=? "
                              f"AND recorded_at<=? AND seq>? AND kind IN ({slots}) "
                              "AND json_extract(body,'$.evidence_class') IN ('PUBLIC_OBSERVED','SYNTHETIC') "
                              "ORDER BY seq LIMIT ?",
                              (event_id, cutoff, cutoff, after_seq, *kinds, limit)).fetchall()
        return [self._decode(row) for row in rows]

    def decision(self, record_id: str, *, event_id: str, strategy: str,
                 binding: ReleaseBinding, evidence_ids: tuple[str, ...],
                 feature_ready_at: float, valuation_type: str, target: str,
                 outcome: str, reason: str, explanation: dict, expires_at: float) -> dict:
        at = finite(self.clock())
        if not isinstance(binding, ReleaseBinding):
            raise EvidenceError("RELEASE_BINDING_REQUIRED")
        if not evidence_ids or len(evidence_ids) > self.limits.max_evidence_per_decision or len(set(evidence_ids)) != len(evidence_ids):
            raise EvidenceError("EVIDENCE_SET_INVALID")
        if valuation_type not in {"SETTLEMENT", "REPRICING", "OBSERVATION_ONLY"}:
            raise EvidenceError("VALUATION_TYPE_INVALID")
        if (outcome not in {"ACCEPT_RESEARCH", "REJECT", "GATED"}
                or (valuation_type == "OBSERVATION_ONLY" and outcome == "ACCEPT_RESEARCH")):
            raise EvidenceError("DECISION_OUTCOME_INVALID")
        ready = finite(feature_ready_at)
        expiry = finite(expires_at)
        if ready > at or expiry <= at:
            raise EvidenceError("DECISION_TIME_INVALID")
        inputs = [self.get(key) for key in evidence_ids]
        for item in inputs:
            if (item["event_id"] != event_id or item["kind"] not in KINDS - {"LABEL"}
                    or item["body"]["available_at"] > ready
                    or item["body"].get("evidence_class") == "HISTORICAL_AVAILABILITY_UNKNOWN"):
                raise EvidenceError("NONCAUSAL_OR_MISMATCHED_DECISION_INPUT")
        body = {"strategy": identity(strategy), "binding": asdict(binding),
                "evidence": [{"id": x["id"], "sha256": x["sha256"]} for x in inputs],
                "feature_ready_at": ready, "valuation_type": valuation_type,
                "target": identity(target), "outcome": outcome, "reason": identity(reason),
                "explanation": explanation, "expires_at": expiry,
                "execution_status": "NONFINANCIAL_NOT_SUBMITTED",
                "evidence_class": "SYNTHETIC" if any(x["body"]["evidence_class"] == "SYNTHETIC" for x in inputs) else "PUBLIC_OBSERVED"}
        return self._append(record_id, "DECISION", event_id, body, at, at)

    def replay(self, record_id: str, evaluator: Callable[[tuple[dict, ...], dict], dict]) -> dict:
        """Recompute from the pinned evidence set and binding, never latest records."""
        item = self.get(record_id)
        if item["kind"] != "DECISION":
            raise EvidenceError("DECISION_REQUIRED")
        body = item["body"]
        inputs = tuple(self.get(x["id"]) for x in body["evidence"])
        for ref, capture in zip(body["evidence"], inputs):
            if capture["sha256"] != ref["sha256"] or capture["body"]["available_at"] > body["feature_ready_at"]:
                raise EvidenceError("REPLAY_EVIDENCE_MISMATCH")
        result = evaluator(inputs, dict(body["binding"]))
        return {"decision_id": record_id, "binding": body["binding"],
                "matches": canonical(result) == canonical(body["explanation"]),
                "recomputed": result, "financial_authority": False}

    def funnel(self, record_id: str, *, event_id: str, strategy: str,
               stage: str, state: str, reason: str, cycle_id: str) -> dict:
        if stage not in FUNNEL_STAGES or state not in {"PASS", "NO_OPPORTUNITY", "NO_DATA", "SUPPRESSED", "FAULT", "GATED"}:
            raise EvidenceError("FUNNEL_ENUM_INVALID")
        at = finite(self.clock())
        return self._append(record_id, "FUNNEL", event_id,
                            {"strategy": identity(strategy), "stage": stage, "state": state,
                             "reason": identity(reason), "cycle_id": identity(cycle_id)}, at, at)

    def source_result(self, record_id: str, *, event_id: str, provider: str,
                      cycle_id: str, state: str, reason: str, elapsed_ms: float,
                      capture_ids: tuple[str, ...] = (), attempts: int = 1,
                      retry_not_before: float | None = None) -> dict:
        if state not in {"SUCCESS", "TRANSPORT_FAILURE", "RATE_LIMIT", "MALFORMED", "STALE", "SEMANTIC_FAILURE", "ABSENT", "BUDGET_EXHAUSTED", "COOLDOWN"}:
            raise EvidenceError("SOURCE_RESULT_ENUM_INVALID")
        if len(capture_ids) > self.limits.max_evidence_per_decision:
            raise EvidenceError("EVIDENCE_SET_INVALID")
        if type(attempts) is not int or not 0 <= attempts <= 3 or (state == "SUCCESS" and attempts == 0):
            raise EvidenceError("SOURCE_ATTEMPTS_INVALID")
        if (state == "SUCCESS") != bool(capture_ids):
            raise EvidenceError("SOURCE_SUCCESS_REQUIRES_CAPTURE")
        for key in capture_ids:
            capture = self.get(key)
            if capture["event_id"] != event_id or capture["kind"] not in KINDS or capture["body"]["provider"] != provider:
                raise EvidenceError("SOURCE_RESULT_EVIDENCE_MISMATCH")
        at = finite(self.clock())
        if retry_not_before is not None:
            finite(retry_not_before)
        return self._append(record_id, "SOURCE_RESULT", event_id,
                            {"provider": identity(provider), "cycle_id": identity(cycle_id),
                             "state": state, "reason": identity(reason),
                             "elapsed_ms": finite(elapsed_ms), "capture_ids": capture_ids,
                             "attempts": attempts, "retry_not_before": retry_not_before}, at, at)
