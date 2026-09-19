from __future__ import annotations

"""Revision-sensitive Result-Lag PAPER research.

This module deliberately does not claim exact WRH publication finality.  It uses the
currently published target-day WRH high/low only after a following-date row is visible,
then requires an exact public CLOB quote.  Positions are simulated and later resolved
from the market's final token payout, but are always excluded from validated P&L.

It is a forward-research sleeve for learning whether apparent post-day source/market lag
has value before the stricter deterministic Result-Lag finality proof exists.
"""

import hashlib
import json
import math
import os
import sqlite3
import time
from dataclasses import asdict, dataclass, field
from datetime import timedelta
from pathlib import Path

from .weather_only_contracts import DAILY_HIGH
from .weather_only_result_lag import (
    WeatherResultLagError,
    WeatherResultLagShadowPolicy,
    _bucket_contains,
    _compiled_with_rule_authority,
    _execution_digest,
    _winning_quote,
)
from .weather_only_wrh import WRHSourceSnapshot


RESULT_LAG_RESEARCH_VERSION = (
    "weather_result_lag_research_v1_provisional_wrh_post_day_exact_clob"
)
RESULT_LAG_RESEARCH_LANE = "weather_result_lag_research"
RESULT_LAG_RESEARCH_EVIDENCE = (
    "PROVISIONAL_WRH_CURRENT_PUBLICATION_REVISION_SENSITIVE_EXCLUDED"
)
MAX_RESEARCH_ATTEMPTS_PER_EVENT = 3

_OPEN = "OPEN"
_NO_FILL = "NO_FILL"
_WON = "WON"
_LOST = "LOST"
_PARTIAL = "RESOLVED_PARTIAL"
_PENDING = "PENDING_DELIVERY"
_DELIVERY_UNCERTAIN = "DELIVERY_UNCERTAIN"
_DELIVERY_FAILED = "DELIVERY_FAILED"
_POST_REJECTED = "POST_RECEIPT_REJECTED"
_ACTIONABILITY_UNPROVEN = "ACTIONABILITY_UNPROVEN"


class ResultLagResearchError(RuntimeError):
    def __init__(self, code: str):
        self.code = str(code)
        super().__init__(self.code)


def _canonical(value: object) -> str:
    try:
        return json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        )
    except (TypeError, ValueError):
        raise ResultLagResearchError("RESULT_LAG_RESEARCH_JSON_INVALID") from None


def _sha(value: object) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _finite(value: object, code: str) -> float:
    if isinstance(value, bool):
        raise ResultLagResearchError(code)
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        raise ResultLagResearchError(code) from None
    if not math.isfinite(number) or number < 0.0:
        raise ResultLagResearchError(code)
    return number


@dataclass(frozen=True, slots=True)
class ProvisionalResultLagResearchCandidate:
    version: str
    event_id: str
    market_id: str
    condition_id: str
    token_id: str
    station: str
    target_date: str
    family: str
    provisional_value_f: int
    source_snapshot_sha256: str
    target_state_sha256: str
    rule_evidence_sha256: str
    first_following_observation_time: str
    clob_evidence_sha256: str
    executable_ask: float
    visible_ask_shares: float
    minimum_order_size: float
    conservative_fee_per_share: float
    total_cost_per_share: float
    provisional_edge_per_share: float
    observed_at: float
    candidate_evidence_sha256: str
    revision_sensitive: bool = field(init=False, default=True)
    deterministic_result: bool = field(init=False, default=False)
    settlement_label_authority: bool = field(init=False, default=False)
    included_in_validated_pnl: bool = field(init=False, default=False)
    financial_authority: bool = field(init=False, default=False)
    automatic_order_placement: bool = field(init=False, default=False)

    def as_dict(self) -> dict:
        return asdict(self)


def _candidate_digest_payload(value: ProvisionalResultLagResearchCandidate) -> dict:
    data = value.as_dict()
    data.pop("candidate_evidence_sha256", None)
    return data


async def evaluate_provisional_result_lag_research(
    event: dict,
    snapshot: WRHSourceSnapshot,
    *,
    clob,
    policy: WeatherResultLagShadowPolicy | None = None,
) -> tuple[ProvisionalResultLagResearchCandidate | None, object | None]:
    """Evaluate current-publication Result-Lag without claiming deterministic finality."""

    frozen = policy or WeatherResultLagShadowPolicy()
    compiled, rule_sha = _compiled_with_rule_authority(event)
    if not isinstance(snapshot, WRHSourceSnapshot):
        raise ResultLagResearchError("RESULT_LAG_RESEARCH_SNAPSHOT_TYPE_INVALID")
    station = str(compiled.station_hint or "").strip().upper()
    if snapshot.station != station or snapshot.target_date != compiled.target_date:
        raise ResultLagResearchError("RESULT_LAG_RESEARCH_SOURCE_IDENTITY_MISMATCH")
    if (
        snapshot.financial_authority
        or not snapshot.exact_wrh_snapshot
        or not snapshot.transport_semantics_certified
    ):
        raise ResultLagResearchError("RESULT_LAG_RESEARCH_SOURCE_AUTHORITY_INVALID")
    if snapshot.first_following_row is None:
        return None, None
    if snapshot.first_following_row.local_date != snapshot.target_date + timedelta(days=1):
        raise ResultLagResearchError("RESULT_LAG_RESEARCH_FOLLOWING_DATE_INVALID")
    if not snapshot.target_display_temperatures_f:
        return None, None
    if snapshot.target_high_f is None or snapshot.target_low_f is None:
        return None, None

    provisional = (
        int(snapshot.target_high_f)
        if compiled.family == DAILY_HIGH
        else int(snapshot.target_low_f)
    )
    winners = tuple(
        bucket for bucket in compiled.buckets if _bucket_contains(bucket, provisional)
    )
    if len(winners) != 1:
        raise ResultLagResearchError("RESULT_LAG_RESEARCH_WINNER_NOT_EXACTLY_ONE")
    winner = winners[0]
    if not winner.trade_open or not winner.yes_token or not winner.condition_id:
        return None, None

    exact = await clob.exact_event_snapshot(compiled)
    try:
        clob_sha = _execution_digest(
            compiled,
            exact,
            source_received_at=float(snapshot.received_at),
        )
        quote = _winning_quote(winner, exact, policy=frozen)
    except WeatherResultLagError as exc:
        raise ResultLagResearchError(exc.code) from exc
    if quote is None:
        return None, None

    ask, size, _rate, _exp, fee, total, edge = quote
    params = exact.parameters[winner.condition_id]
    shell = ProvisionalResultLagResearchCandidate(
        version=RESULT_LAG_RESEARCH_VERSION,
        event_id=compiled.event_id,
        market_id=winner.market_id,
        condition_id=winner.condition_id,
        token_id=str(winner.yes_token),
        station=station,
        target_date=compiled.target_date.isoformat(),
        family=compiled.family,
        provisional_value_f=provisional,
        source_snapshot_sha256=snapshot.evidence_sha256,
        target_state_sha256=snapshot.target_state_sha256,
        rule_evidence_sha256=rule_sha,
        first_following_observation_time=(
            snapshot.first_following_row.observation_time_local.isoformat()
        ),
        clob_evidence_sha256=clob_sha,
        executable_ask=float(ask),
        visible_ask_shares=float(size),
        minimum_order_size=float(params.minimum_order_size),
        conservative_fee_per_share=float(fee),
        total_cost_per_share=float(total),
        provisional_edge_per_share=float(edge),
        observed_at=float(exact.finished_at),
        candidate_evidence_sha256="0" * 64,
    )
    value = ProvisionalResultLagResearchCandidate(
        **{
            name: getattr(shell, name)
            for name, definition in shell.__dataclass_fields__.items()
            if definition.init and name != "candidate_evidence_sha256"
        },
        candidate_evidence_sha256=_sha(_candidate_digest_payload(shell)),
    )
    return value, exact


class ResultLagResearchStore:
    """Separate PAPER ledger. Nothing here is included in validated performance."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._init()
        self.recovery = self.recover_after_restart()

    def _conn(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.path, timeout=5.0)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("PRAGMA synchronous=FULL")
        db.execute("PRAGMA busy_timeout=5000")
        return db

    def _init(self) -> None:
        with self._conn() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS weather_result_lag_research_positions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    fingerprint TEXT NOT NULL UNIQUE,
                    event_id TEXT NOT NULL,
                    market_id TEXT NOT NULL,
                    condition_id TEXT NOT NULL,
                    token_id TEXT NOT NULL,
                    station TEXT NOT NULL,
                    target_date TEXT NOT NULL,
                    family TEXT NOT NULL,
                    provisional_value_f INTEGER NOT NULL,
                    pre_source_snapshot_sha256 TEXT NOT NULL,
                    post_source_snapshot_sha256 TEXT,
                    pre_target_state_sha256 TEXT NOT NULL,
                    post_target_state_sha256 TEXT,
                    pre_clob_evidence_sha256 TEXT NOT NULL,
                    post_clob_evidence_sha256 TEXT,
                    target_stake_usd REAL NOT NULL,
                    entry_ask REAL,
                    fee_per_share REAL,
                    total_cost_per_share REAL,
                    visible_shares REAL,
                    minimum_order_size REAL,
                    filled_shares REAL NOT NULL DEFAULT 0,
                    capital_used REAL NOT NULL DEFAULT 0,
                    maximum_payout REAL NOT NULL DEFAULT 0,
                    created_at REAL NOT NULL,
                    telegram_message_id INTEGER,
                    telegram_sent_at REAL,
                    opened_at REAL,
                    status TEXT NOT NULL,
                    rejection_reason TEXT,
                    settlement_payout_per_share REAL,
                    proceeds REAL,
                    pnl REAL,
                    roi REAL,
                    settled_at REAL,
                    settlement_evidence_json TEXT,
                    settlement_notification_state TEXT,
                    settlement_telegram_message_id INTEGER,
                    candidate_json TEXT NOT NULL,
                    revision_sensitive INTEGER NOT NULL DEFAULT 1 CHECK(revision_sensitive=1),
                    deterministic_result INTEGER NOT NULL DEFAULT 0 CHECK(deterministic_result=0),
                    included_in_validated_pnl INTEGER NOT NULL DEFAULT 0 CHECK(included_in_validated_pnl=0),
                    financial_authority INTEGER NOT NULL DEFAULT 0 CHECK(financial_authority=0),
                    automatic_order_placement INTEGER NOT NULL DEFAULT 0 CHECK(automatic_order_placement=0)
                );
                CREATE INDEX IF NOT EXISTS idx_result_lag_research_status
                    ON weather_result_lag_research_positions(status,id);
                CREATE INDEX IF NOT EXISTS idx_result_lag_research_event
                    ON weather_result_lag_research_positions(event_id,id);
                """
            )
        if self.path.exists() and self.path.stat().st_mode & 0o077:
            os.chmod(self.path, 0o600)

    def recover_after_restart(self) -> dict:
        with self._conn() as db:
            no_receipt = int(
                db.execute(
                    "SELECT COUNT(*) FROM weather_result_lag_research_positions "
                    "WHERE status=? AND telegram_message_id IS NULL",
                    (_PENDING,),
                ).fetchone()[0]
            )
            with_receipt = int(
                db.execute(
                    "SELECT COUNT(*) FROM weather_result_lag_research_positions "
                    "WHERE status=? AND telegram_message_id IS NOT NULL",
                    (_PENDING,),
                ).fetchone()[0]
            )
            db.execute(
                "UPDATE weather_result_lag_research_positions SET status=?,"
                "rejection_reason='PROCESS_RESTART_WITH_AMBIGUOUS_DELIVERY' "
                "WHERE status=? AND telegram_message_id IS NULL",
                (_DELIVERY_UNCERTAIN, _PENDING),
            )
            db.execute(
                "UPDATE weather_result_lag_research_positions SET status=?,"
                "rejection_reason='PROCESS_RESTART_BEFORE_POST_RECEIPT_ADMISSION' "
                "WHERE status=? AND telegram_message_id IS NOT NULL",
                (_ACTIONABILITY_UNPROVEN, _PENDING),
            )
        return {
            "delivery_uncertain": no_receipt,
            "actionability_unproven": with_receipt,
            "reconstructed_fills": 0,
        }

    def attempts_for_event(self, event_id: str) -> int:
        with self._conn() as db:
            return int(
                db.execute(
                    "SELECT COUNT(*) FROM weather_result_lag_research_positions "
                    "WHERE event_id=?",
                    (str(event_id),),
                ).fetchone()[0]
            )

    def event_has_position(self, event_id: str) -> bool:
        with self._conn() as db:
            return (
                db.execute(
                    "SELECT 1 FROM weather_result_lag_research_positions "
                    "WHERE event_id=? AND status IN (?,?,?,?) LIMIT 1",
                    (str(event_id), _OPEN, _WON, _LOST, _PARTIAL),
                ).fetchone()
                is not None
            )

    def save_pending(
        self,
        candidate: ProvisionalResultLagResearchCandidate,
        *,
        fingerprint: str,
        target_stake_usd: float,
        created_at: float | None = None,
    ) -> int | None:
        stake = _finite(target_stake_usd, "RESULT_LAG_RESEARCH_STAKE_INVALID")
        if stake <= 0:
            raise ResultLagResearchError("RESULT_LAG_RESEARCH_STAKE_INVALID")
        at = time.time() if created_at is None else _finite(
            created_at, "RESULT_LAG_RESEARCH_TIME_INVALID"
        )
        with self._conn() as db:
            cur = db.execute(
                """
                INSERT OR IGNORE INTO weather_result_lag_research_positions(
                    fingerprint,event_id,market_id,condition_id,token_id,station,
                    target_date,family,provisional_value_f,pre_source_snapshot_sha256,
                    pre_target_state_sha256,pre_clob_evidence_sha256,target_stake_usd,
                    created_at,status,candidate_json
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    str(fingerprint),
                    candidate.event_id,
                    candidate.market_id,
                    candidate.condition_id,
                    candidate.token_id,
                    candidate.station,
                    candidate.target_date,
                    candidate.family,
                    int(candidate.provisional_value_f),
                    candidate.source_snapshot_sha256,
                    candidate.target_state_sha256,
                    candidate.clob_evidence_sha256,
                    stake,
                    at,
                    _PENDING,
                    _canonical(candidate.as_dict()),
                ),
            )
            return int(cur.lastrowid) if cur.rowcount == 1 else None

    def mark_telegram_sent(self, row_id: int, message_id: int, sent_at: float) -> None:
        with self._conn() as db:
            cur = db.execute(
                "UPDATE weather_result_lag_research_positions "
                "SET telegram_message_id=?,telegram_sent_at=? "
                "WHERE id=? AND status=?",
                (int(message_id), _finite(sent_at, "RESULT_LAG_RESEARCH_TIME_INVALID"), int(row_id), _PENDING),
            )
            if cur.rowcount != 1:
                raise ResultLagResearchError("RESULT_LAG_RESEARCH_RECEIPT_STATE_MISMATCH")

    def mark_delivery_failed(self, row_id: int, *, uncertain: bool, reason: str) -> None:
        status = _DELIVERY_UNCERTAIN if uncertain else _DELIVERY_FAILED
        with self._conn() as db:
            db.execute(
                "UPDATE weather_result_lag_research_positions "
                "SET status=?,rejection_reason=? WHERE id=? AND status=?",
                (status, str(reason), int(row_id), _PENDING),
            )

    def reject_post_receipt(self, row_id: int, reason: str) -> None:
        with self._conn() as db:
            db.execute(
                "UPDATE weather_result_lag_research_positions "
                "SET status=?,rejection_reason=? WHERE id=? AND status=?",
                (_POST_REJECTED, str(reason), int(row_id), _PENDING),
            )

    def open_post_receipt(
        self,
        row_id: int,
        candidate: ProvisionalResultLagResearchCandidate,
        *,
        telegram_sent_at: float,
        target_stake_usd: float,
    ) -> dict:
        sent = _finite(telegram_sent_at, "RESULT_LAG_RESEARCH_TIME_INVALID")
        stake = _finite(target_stake_usd, "RESULT_LAG_RESEARCH_STAKE_INVALID")
        if candidate.observed_at + 1e-9 < sent:
            raise ResultLagResearchError("RESULT_LAG_RESEARCH_POST_RECEIPT_NOT_CAUSAL")
        cost = float(candidate.total_cost_per_share)
        requested = stake / cost
        filled = min(requested, float(candidate.visible_ask_shares))
        if filled + 1e-12 < float(candidate.minimum_order_size):
            filled = 0.0
            status = _NO_FILL
            reason = "BELOW_MINIMUM_ORDER_SIZE"
        else:
            status = _OPEN
            reason = None
        capital = filled * cost
        maximum = filled
        with self._conn() as db:
            row = db.execute(
                "SELECT status FROM weather_result_lag_research_positions WHERE id=?",
                (int(row_id),),
            ).fetchone()
            if row is None or str(row["status"]) != _PENDING:
                raise ResultLagResearchError("RESULT_LAG_RESEARCH_OPEN_STATE_MISMATCH")
            db.execute(
                """
                UPDATE weather_result_lag_research_positions
                SET post_source_snapshot_sha256=?,post_target_state_sha256=?,
                    post_clob_evidence_sha256=?,entry_ask=?,fee_per_share=?,
                    total_cost_per_share=?,visible_shares=?,minimum_order_size=?,
                    filled_shares=?,capital_used=?,maximum_payout=?,opened_at=?,
                    status=?,rejection_reason=?,settlement_notification_state=?
                WHERE id=?
                """,
                (
                    candidate.source_snapshot_sha256,
                    candidate.target_state_sha256,
                    candidate.clob_evidence_sha256,
                    float(candidate.executable_ask),
                    float(candidate.conservative_fee_per_share),
                    cost,
                    float(candidate.visible_ask_shares),
                    float(candidate.minimum_order_size),
                    filled,
                    capital,
                    maximum,
                    float(candidate.observed_at),
                    status,
                    reason,
                    "PENDING" if status == _OPEN else None,
                    int(row_id),
                ),
            )
            out = db.execute(
                "SELECT * FROM weather_result_lag_research_positions WHERE id=?",
                (int(row_id),),
            ).fetchone()
        return dict(out)

    def open_positions(self, limit: int = 100) -> list[dict]:
        with self._conn() as db:
            return [
                dict(r)
                for r in db.execute(
                    "SELECT * FROM weather_result_lag_research_positions "
                    "WHERE status=? ORDER BY id LIMIT ?",
                    (_OPEN, max(1, min(500, int(limit)))),
                )
            ]

    def resolve(self, row_id: int, payout_per_share: float, evidence: dict) -> dict | None:
        payout = _finite(payout_per_share, "RESULT_LAG_RESEARCH_PAYOUT_INVALID")
        with self._conn() as db:
            row = db.execute(
                "SELECT * FROM weather_result_lag_research_positions "
                "WHERE id=? AND status=?",
                (int(row_id), _OPEN),
            ).fetchone()
            if row is None:
                return None
            item = dict(row)
            units = float(item["filled_shares"])
            capital = float(item["capital_used"])
            proceeds = units * payout
            pnl = proceeds - capital
            roi = pnl / capital if capital > 0 else None
            if payout >= 1.0 - 1e-9:
                status = _WON
            elif payout <= 1e-9:
                status = _LOST
            else:
                status = _PARTIAL
            db.execute(
                """
                UPDATE weather_result_lag_research_positions
                SET status=?,settlement_payout_per_share=?,proceeds=?,pnl=?,roi=?,
                    settled_at=?,settlement_evidence_json=?,
                    settlement_notification_state='PENDING'
                WHERE id=?
                """,
                (
                    status,
                    payout,
                    proceeds,
                    pnl,
                    roi,
                    time.time(),
                    _canonical(evidence),
                    int(row_id),
                ),
            )
            out = db.execute(
                "SELECT * FROM weather_result_lag_research_positions WHERE id=?",
                (int(row_id),),
            ).fetchone()
        return dict(out)

    def pending_notifications(self, limit: int = 50) -> list[dict]:
        with self._conn() as db:
            return [
                dict(r)
                for r in db.execute(
                    "SELECT * FROM weather_result_lag_research_positions "
                    "WHERE status IN (?,?,?) AND settlement_notification_state='PENDING' "
                    "ORDER BY id LIMIT ?",
                    (_WON, _LOST, _PARTIAL, max(1, min(200, int(limit)))),
                )
            ]

    def set_notification(self, row_id: int, state: str, message_id: int | None = None) -> None:
        with self._conn() as db:
            db.execute(
                "UPDATE weather_result_lag_research_positions "
                "SET settlement_notification_state=?,"
                "settlement_telegram_message_id=COALESCE(?,settlement_telegram_message_id) "
                "WHERE id=?",
                (str(state), message_id, int(row_id)),
            )

    def stats(self) -> dict:
        with self._conn() as db:
            row = db.execute(
                """
                SELECT COUNT(*) total,
                       SUM(CASE WHEN status='OPEN' THEN 1 ELSE 0 END) open_n,
                       SUM(CASE WHEN status='NO_FILL' THEN 1 ELSE 0 END) no_fill,
                       SUM(CASE WHEN status='WON' THEN 1 ELSE 0 END) won,
                       SUM(CASE WHEN status='LOST' THEN 1 ELSE 0 END) lost,
                       SUM(CASE WHEN status='RESOLVED_PARTIAL' THEN 1 ELSE 0 END) partial,
                       SUM(CASE WHEN status='POST_RECEIPT_REJECTED' THEN 1 ELSE 0 END) rejected,
                       SUM(CASE WHEN status='DELIVERY_UNCERTAIN' THEN 1 ELSE 0 END) delivery_uncertain,
                       SUM(CASE WHEN status IN ('WON','LOST','RESOLVED_PARTIAL')
                                THEN capital_used ELSE 0 END) resolved_capital,
                       SUM(CASE WHEN status IN ('WON','LOST','RESOLVED_PARTIAL')
                                THEN proceeds ELSE 0 END) resolved_proceeds,
                       SUM(CASE WHEN status IN ('WON','LOST','RESOLVED_PARTIAL')
                                THEN pnl ELSE 0 END) pnl
                FROM weather_result_lag_research_positions
                """
            ).fetchone()
        d = dict(row) if row else {}
        capital = float(d.get("resolved_capital") or 0.0)
        pnl = float(d.get("pnl") or 0.0)
        won = int(d.get("won") or 0)
        lost = int(d.get("lost") or 0)
        partial = int(d.get("partial") or 0)
        return {
            "total": int(d.get("total") or 0),
            "open": int(d.get("open_n") or 0),
            "no_fill": int(d.get("no_fill") or 0),
            "won": won,
            "lost": lost,
            "partial": partial,
            "resolved": won + lost + partial,
            "post_receipt_rejected": int(d.get("rejected") or 0),
            "delivery_uncertain": int(d.get("delivery_uncertain") or 0),
            "resolved_capital": capital,
            "resolved_proceeds": float(d.get("resolved_proceeds") or 0.0),
            "pnl": pnl,
            "roi": pnl / capital if capital > 0 else None,
            "included_in_validated_pnl": False,
            "revision_sensitive": True,
            "financial_authority": False,
        }
