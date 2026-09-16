from __future__ import annotations

"""Corrective paper-experiment protocol for weather LIVE PAPER v4.

This module intentionally contains no authenticated Polymarket trading capability.
It strengthens only simulated execution, settlement, Telegram delivery bookkeeping
and operator-facing reporting.
"""

import asyncio
import hashlib
import html
import json
import math
import time
from pathlib import Path

import httpx

from .settlement import _json_list
from .weather_only_live_paper import PaperTelegram, WeatherLivePaperError
from .weather_only_live_paper_v3 import GuardedWeatherPaperPositionStore
from .weather_only_paper_control import PublicGammaSettlementClient, WeatherPaperCommandController
from .weather_only_paper_positions import WeatherPaperPositionError, _json, _payload


PAPER_EXECUTION_PROTOCOL_V4 = "weather_paper_execution_v4_frozen_quote_capacity"
PAPER_POSITION_VERSION_V4 = "weather_paper_position_v4_validated_frozen_execution"
PAPER_SETTLEMENT_VERSION_V4 = "weather_paper_settlement_v4_finality_vector_exact_token"
STATUS_MAX_AGE_SECONDS = 600.0
SETTLEMENT_PAGE_SIZE = 200
MAX_FILL_QUOTE_AGE_SECONDS = 10.0


class CorrectivePaperError(RuntimeError):
    def __init__(self, code: str):
        self.code = str(code)
        super().__init__(self.code)


class DeliveryUncertain(WeatherLivePaperError):
    """Transport outcome where remote acceptance cannot be proved either way."""


def _finite(value: object, code: str) -> float:
    if value is None or isinstance(value, bool):
        raise CorrectivePaperError(code)
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        raise CorrectivePaperError(code) from None
    if not math.isfinite(number):
        raise CorrectivePaperError(code)
    return number


def _sha(value: object) -> str:
    encoded = json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


class CorrectivePaperTelegram(PaperTelegram):
    """Telegram transport with explicit ambiguous-delivery handling.

    Explicit 429 responses may be retried because Telegram has said the message was
    not accepted.  A transport exception, 5xx, malformed reply or missing receipt is
    *not* retried: doing so could duplicate a message that the server already accepted.
    """

    async def send_html(
        self,
        text: str,
        *,
        url: str | None = None,
        expires_at: float | None = None,
    ) -> int:
        if len(text) > 3900:
            raise WeatherLivePaperError("PAPER_TELEGRAM_MESSAGE_TOO_LONG")
        payload: dict[str, object] = {
            "chat_id": self.chat_id,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }
        if isinstance(url, str) and url.startswith("https://"):
            payload["reply_markup"] = {
                "inline_keyboard": [[{"text": "OPEN POLYMARKET", "url": url}]]
            }
        endpoint = f"https://api.telegram.org/bot{self.token}/sendMessage"
        for attempt in range(3):
            if expires_at is not None and time.time() >= float(expires_at):
                raise WeatherLivePaperError("PAPER_TELEGRAM_DECISION_EXPIRED")
            try:
                response = await self.http.post(endpoint, json=payload)
            except httpx.RequestError:
                raise DeliveryUncertain("PAPER_TELEGRAM_DELIVERY_UNCERTAIN") from None
            if response.status_code == 429:
                try:
                    retry_after = float(
                        (response.json().get("parameters") or {}).get("retry_after", 1.0)
                    )
                except Exception:
                    retry_after = 1.0
                if attempt >= 2:
                    raise WeatherLivePaperError("PAPER_TELEGRAM_RATE_LIMITED")
                wait = min(15.0, max(1.0, retry_after))
                if expires_at is not None and time.time() + wait >= float(expires_at):
                    raise WeatherLivePaperError("PAPER_TELEGRAM_DECISION_EXPIRED")
                await asyncio.sleep(wait)
                continue
            if response.status_code >= 500:
                raise DeliveryUncertain("PAPER_TELEGRAM_SERVER_UNCERTAIN")
            if response.status_code >= 400:
                raise WeatherLivePaperError(f"PAPER_TELEGRAM_HTTP_{response.status_code}")
            try:
                body = response.json()
            except ValueError:
                raise DeliveryUncertain("PAPER_TELEGRAM_RESPONSE_UNCERTAIN") from None
            result = body.get("result") if isinstance(body, dict) and body.get("ok") is True else None
            message_id = result.get("message_id") if isinstance(result, dict) else None
            if isinstance(message_id, bool) or not isinstance(message_id, int) or message_id <= 0:
                raise DeliveryUncertain("PAPER_TELEGRAM_RECEIPT_UNCERTAIN")
            return int(message_id)
        raise WeatherLivePaperError("PAPER_TELEGRAM_RETRY_EXHAUSTED")


class CorrectiveWeatherPaperPositionStore(GuardedWeatherPaperPositionStore):
    """Only v4 frozen decisions are admitted to validated performance."""

    def __init__(self, path: str | Path) -> None:
        super().__init__(path)
        self._migrate()

    def _migrate(self) -> None:
        additions = {
            "decision_id": "TEXT",
            "execution_protocol": "TEXT",
            "decision_expires_at": "REAL",
            "paper_fill_at": "REAL",
            "validation_state": "TEXT NOT NULL DEFAULT 'UNVERIFIED'",
            "capacity_key": "TEXT",
            "settlement_notification_state": "TEXT",
        }
        with self._conn() as db:
            existing = {
                str(row["name"])
                for row in db.execute("PRAGMA table_info(weather_paper_positions)")
            }
            for name, ddl in additions.items():
                if name not in existing:
                    db.execute(f"ALTER TABLE weather_paper_positions ADD COLUMN {name} {ddl}")
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS weather_paper_capacity_usage (
                    capacity_key TEXT PRIMARY KEY,
                    visible_units REAL NOT NULL,
                    consumed_units REAL NOT NULL,
                    updated_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS weather_paper_decisions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    decision_id TEXT NOT NULL,
                    event_id TEXT NOT NULL,
                    market_id TEXT,
                    side TEXT,
                    outcome TEXT NOT NULL,
                    reason TEXT,
                    created_at REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_weather_paper_decision_outcome
                    ON weather_paper_decisions(outcome, created_at);
                """
            )

    def set_signal_status(self, signal_id: int, status: str) -> None:
        with self._conn() as db:
            cur = db.execute(
                "UPDATE weather_paper_signals SET status=? WHERE id=?",
                (str(status), int(signal_id)),
            )
            if cur.rowcount != 1:
                raise WeatherPaperPositionError("PAPER_SIGNAL_STATUS_NOT_FOUND")

    def record_decision(
        self,
        *,
        decision_id: str,
        event_id: str,
        market_id: str | None,
        side: str | None,
        outcome: str,
        reason: str | None = None,
    ) -> None:
        with self._conn() as db:
            db.execute(
                """
                INSERT INTO weather_paper_decisions(
                    decision_id,event_id,market_id,side,outcome,reason,created_at
                ) VALUES(?,?,?,?,?,?,?)
                """,
                (
                    str(decision_id), str(event_id), market_id, side,
                    str(outcome), reason, time.time(),
                ),
            )

    def decision_counts(self) -> dict[str, int]:
        with self._conn() as db:
            rows = db.execute(
                "SELECT outcome,COUNT(*) AS n FROM weather_paper_decisions GROUP BY outcome"
            ).fetchall()
        return {str(row["outcome"]): int(row["n"]) for row in rows}

    def _load_signal(self, signal_id: int) -> dict | None:
        with self._conn() as db:
            row = db.execute(
                "SELECT * FROM weather_paper_signals WHERE id=?", (int(signal_id),)
            ).fetchone()
        return dict(row) if row else None

    def ensure_position_for_signal(
        self, signal_id: int, target_stake_usd: float
    ) -> dict | None:
        signal = self._load_signal(int(signal_id))
        if signal is None:
            return None
        payload = _payload(signal.get("payload_json"))
        if payload.get("paper_execution_protocol_version") != PAPER_EXECUTION_PROTOCOL_V4:
            return None
        if str(signal.get("status") or "") != "ACKNOWLEDGED":
            return None

        frozen_stake = _finite(payload.get("paper_target_stake_usd"), "V4_STAKE_MISSING")
        quote_at = _finite(payload.get("quote_observed_at"), "V4_QUOTE_TIME_MISSING")
        fill_at = _finite(payload.get("paper_fill_at"), "V4_FILL_TIME_MISSING")
        expires_at = _finite(payload.get("decision_expires_at"), "V4_EXPIRY_MISSING")
        sent_at = _finite(signal.get("telegram_sent_at"), "V4_TELEGRAM_TIME_MISSING")
        ask = _finite(payload.get("ask"), "V4_ASK_MISSING")
        tick = _finite(payload.get("minimum_tick_size"), "V4_TICK_MISSING")
        minimum = _finite(payload.get("minimum_order_size"), "V4_MINIMUM_MISSING")
        visible = _finite(payload.get("ask_size"), "V4_VISIBLE_SIZE_MISSING")
        if frozen_stake <= 0 or ask <= 0 or tick <= 0 or minimum < 0 or visible < 0:
            raise WeatherPaperPositionError("V4_EXECUTION_CONSTRAINT_INVALID")
        if quote_at > fill_at or fill_at - quote_at > MAX_FILL_QUOTE_AGE_SECONDS:
            raise WeatherPaperPositionError("V4_QUOTE_TOO_OLD_FOR_FILL")
        if fill_at > expires_at or sent_at > expires_at:
            raise WeatherPaperPositionError("V4_DECISION_EXPIRED")
        units_on_tick = ask / tick
        if abs(units_on_tick - round(units_on_tick)) > 1e-6:
            raise WeatherPaperPositionError("V4_ASK_OFF_TICK")

        decision_id = str(payload.get("decision_id") or "").strip()
        book_hash = str(payload.get("book_hash") or "").strip()
        token = str(signal.get("token_id") or "").strip()
        if not decision_id or not book_hash or not token:
            raise WeatherPaperPositionError("V4_DECISION_IDENTITY_INVALID")
        capacity_key = _sha(
            {"token": token, "book_hash": book_hash, "quote_at": quote_at, "ask": ask}
        )

        with self._conn() as db:
            existing = db.execute(
                "SELECT * FROM weather_paper_positions WHERE signal_id=?", (int(signal_id),)
            ).fetchone()
            if existing:
                return self._decode_position(dict(existing))
            cap = db.execute(
                "SELECT visible_units,consumed_units FROM weather_paper_capacity_usage "
                "WHERE capacity_key=?",
                (capacity_key,),
            ).fetchone()
            consumed = float(cap["consumed_units"]) if cap else 0.0
            if cap and abs(float(cap["visible_units"]) - visible) > 1e-9:
                raise WeatherPaperPositionError("V4_CAPACITY_IDENTITY_CONFLICT")
            available = max(0.0, visible - consumed)
            # The inherited position builder will cap to this remaining captured
            # quantity. Updating the immutable signal payload is safe here because
            # the original visible quantity is separately retained below.
            payload_for_fill = dict(payload)
            payload_for_fill["ask_size"] = available
            db.execute(
                "UPDATE weather_paper_signals SET payload_json=? WHERE id=?",
                (_json(payload_for_fill), int(signal_id)),
            )

        row = super().ensure_position_for_signal(int(signal_id), frozen_stake)
        if row is None:
            return None
        filled = float(row.get("filled_units") or 0.0)
        if filled > 0.0 and filled + 1e-12 < minimum:
            with self._conn() as db:
                db.execute(
                    """
                    UPDATE weather_paper_positions
                    SET status='NO_FILL',filled_units=0,capital_used=0,maximum_payout=0,
                        no_fill_reason='BELOW_MINIMUM_ORDER_SIZE'
                    WHERE signal_id=?
                    """,
                    (int(signal_id),),
                )
            filled = 0.0

        condition_id = str(payload.get("condition_id") or "").strip()
        side = str(signal.get("side") or "").strip().upper()
        if not condition_id or side not in {"YES", "NO"}:
            raise WeatherPaperPositionError("V4_SETTLEMENT_IDENTITY_MISSING")
        legs = [{
            "market_id": str(signal.get("market_id") or ""),
            "condition_id": condition_id,
            "token_id": token,
            "side": side,
        }]
        with self._conn() as db:
            db.execute(
                """
                UPDATE weather_paper_positions
                SET position_version=?,target_stake_usd=?,legs_json=?,quote_observed_at=?,
                    opened_at=?,decision_id=?,execution_protocol=?,decision_expires_at=?,
                    paper_fill_at=?,validation_state='VALIDATED',capacity_key=?,
                    settlement_notification_state=CASE WHEN status='OPEN' THEN 'PENDING' ELSE NULL END
                WHERE signal_id=?
                """,
                (
                    PAPER_POSITION_VERSION_V4, frozen_stake, _json(legs), quote_at,
                    fill_at, decision_id, PAPER_EXECUTION_PROTOCOL_V4, expires_at,
                    fill_at, capacity_key, int(signal_id),
                ),
            )
            if filled > 0.0:
                cap = db.execute(
                    "SELECT visible_units,consumed_units FROM weather_paper_capacity_usage "
                    "WHERE capacity_key=?", (capacity_key,),
                ).fetchone()
                if cap is None:
                    db.execute(
                        "INSERT INTO weather_paper_capacity_usage VALUES(?,?,?,?)",
                        (capacity_key, visible, filled, time.time()),
                    )
                else:
                    db.execute(
                        "UPDATE weather_paper_capacity_usage SET consumed_units=?,updated_at=? "
                        "WHERE capacity_key=?",
                        (float(cap["consumed_units"]) + filled, time.time(), capacity_key),
                    )
            final = db.execute(
                "SELECT * FROM weather_paper_positions WHERE signal_id=?", (int(signal_id),)
            ).fetchone()
        return self._decode_position(dict(final)) if final else None

    def ensure_sent_positions(self, target_stake_usd: float) -> list[dict]:
        with self._conn() as db:
            rows = [dict(row) for row in db.execute(
                """
                SELECT s.* FROM weather_paper_signals s
                LEFT JOIN weather_paper_positions p ON p.signal_id=s.id
                WHERE s.telegram_message_id IS NOT NULL AND p.id IS NULL
                ORDER BY s.id
                """
            )]
        created: list[dict] = []
        for row in rows:
            payload = _payload(row.get("payload_json"))
            if (
                payload.get("paper_execution_protocol_version") != PAPER_EXECUTION_PROTOCOL_V4
                or str(row.get("status") or "") != "ACKNOWLEDGED"
            ):
                continue
            position = self.ensure_position_for_signal(int(row["id"]), target_stake_usd)
            if position is not None:
                created.append(position)
        return created

    def open_positions(self, limit: int = 200) -> list[dict]:
        count = max(1, min(1000, int(limit)))
        with self._conn() as db:
            rows = [dict(row) for row in db.execute(
                """
                SELECT * FROM weather_paper_positions
                WHERE status='OPEN' AND validation_state='VALIDATED'
                ORDER BY id LIMIT ?
                """,
                (count,),
            )]
        return [self._decode_position(row) for row in rows]

    def open_positions_for_settlement(self, limit: int = SETTLEMENT_PAGE_SIZE) -> list[dict]:
        count = max(1, min(1000, int(limit)))
        cursor = int(self.get_state("v4_settlement_cursor", "0") or 0)
        with self._conn() as db:
            rows = [dict(row) for row in db.execute(
                """
                SELECT * FROM weather_paper_positions
                WHERE status='OPEN' AND validation_state='VALIDATED' AND id>?
                ORDER BY id LIMIT ?
                """,
                (cursor, count),
            )]
            if len(rows) < count:
                seen = {int(row["id"]) for row in rows}
                wrapped = [dict(row) for row in db.execute(
                    """
                    SELECT * FROM weather_paper_positions
                    WHERE status='OPEN' AND validation_state='VALIDATED' AND id<=?
                    ORDER BY id LIMIT ?
                    """,
                    (cursor, count - len(rows)),
                )]
                rows.extend(row for row in wrapped if int(row["id"]) not in seen)
        if rows:
            self.set_state("v4_settlement_cursor", int(rows[-1]["id"]))
        return [self._decode_position(row) for row in rows]

    def recent_positions(self, limit: int = 10, *, resolved_only: bool = False) -> list[dict]:
        count = max(1, min(100, int(limit)))
        resolved = "AND status IN ('WON','LOST','RESOLVED_PARTIAL')" if resolved_only else ""
        with self._conn() as db:
            rows = [dict(row) for row in db.execute(
                f"SELECT * FROM weather_paper_positions WHERE validation_state='VALIDATED' {resolved} "
                "ORDER BY id DESC LIMIT ?",
                (count,),
            )]
        return [self._decode_position(row) for row in rows]

    def resolved_pending_notification(self, limit: int = 50) -> list[dict]:
        with self._conn() as db:
            rows = [dict(row) for row in db.execute(
                """
                SELECT * FROM weather_paper_positions
                WHERE validation_state='VALIDATED'
                  AND status IN ('WON','LOST','RESOLVED_PARTIAL')
                  AND COALESCE(settlement_notification_state,'PENDING')='PENDING'
                ORDER BY id LIMIT ?
                """,
                (max(1, min(200, int(limit))),),
            )]
        return [self._decode_position(row) for row in rows]

    def set_notification_state(
        self, position_id: int, state: str, message_id: int | None = None
    ) -> None:
        with self._conn() as db:
            db.execute(
                """
                UPDATE weather_paper_positions
                SET settlement_notification_state=?,
                    settlement_telegram_message_id=COALESCE(?,settlement_telegram_message_id)
                WHERE id=?
                """,
                (str(state), message_id, int(position_id)),
            )

    def stats(self) -> dict:
        with self._conn() as db:
            row = db.execute(
                """
                SELECT
                    COUNT(*) AS all_rows,
                    SUM(CASE WHEN validation_state='VALIDATED' THEN 1 ELSE 0 END) AS validated,
                    SUM(CASE WHEN validation_state!='VALIDATED' OR validation_state IS NULL THEN 1 ELSE 0 END) AS unverified,
                    SUM(CASE WHEN validation_state='VALIDATED' AND status='OPEN' THEN 1 ELSE 0 END) AS open_n,
                    SUM(CASE WHEN validation_state='VALIDATED' AND status='NO_FILL' THEN 1 ELSE 0 END) AS no_fill,
                    SUM(CASE WHEN validation_state='VALIDATED' AND status='WON' THEN 1 ELSE 0 END) AS won,
                    SUM(CASE WHEN validation_state='VALIDATED' AND status='LOST' THEN 1 ELSE 0 END) AS lost,
                    SUM(CASE WHEN validation_state='VALIDATED' AND status='RESOLVED_PARTIAL' THEN 1 ELSE 0 END) AS partial,
                    SUM(CASE WHEN validation_state='VALIDATED' AND status='OPEN' THEN capital_used ELSE 0 END) AS open_capital,
                    SUM(CASE WHEN validation_state='VALIDATED' AND status IN ('WON','LOST','RESOLVED_PARTIAL') THEN capital_used ELSE 0 END) AS resolved_capital,
                    SUM(CASE WHEN validation_state='VALIDATED' AND status IN ('WON','LOST','RESOLVED_PARTIAL') THEN proceeds ELSE 0 END) AS resolved_proceeds,
                    SUM(CASE WHEN validation_state='VALIDATED' AND status IN ('WON','LOST','RESOLVED_PARTIAL') THEN pnl ELSE 0 END) AS pnl
                FROM weather_paper_positions
                """
            ).fetchone()
            quarantined = int(db.execute(
                "SELECT COUNT(*) FROM weather_paper_signals WHERE status='QUARANTINED'"
            ).fetchone()[0])
            uncertain = int(db.execute(
                "SELECT COUNT(*) FROM weather_paper_signals WHERE status='DELIVERY_UNCERTAIN'"
            ).fetchone()[0])
        data = dict(row) if row else {}
        won = int(data.get("won") or 0)
        lost = int(data.get("lost") or 0)
        partial = int(data.get("partial") or 0)
        resolved = won + lost + partial
        capital = float(data.get("resolved_capital") or 0.0)
        pnl = float(data.get("pnl") or 0.0)
        decisions = self.decision_counts()
        return {
            "version": PAPER_POSITION_VERSION_V4,
            "total": int(data.get("validated") or 0),
            "all_rows": int(data.get("all_rows") or 0),
            "unverified": int(data.get("unverified") or 0),
            "quarantined": quarantined,
            "delivery_uncertain": uncertain,
            "open": int(data.get("open_n") or 0),
            "no_fill": int(data.get("no_fill") or 0),
            "resolved": resolved,
            "won": won,
            "lost": lost,
            "partial": partial,
            "win_rate": won / (won + lost) if won + lost else None,
            "open_capital": float(data.get("open_capital") or 0.0),
            "resolved_capital": capital,
            "resolved_proceeds": float(data.get("resolved_proceeds") or 0.0),
            "pnl": pnl,
            "resolved_roi": pnl / capital if capital > 0 else None,
            "skipped": int(decisions.get("SKIPPED", 0)),
            "decision_counts": decisions,
            "financial_authority": False,
            "automatic_order_placement": False,
        }


def final_token_payout_v4(
    token_id: str,
    market: dict,
    *,
    expected_condition_id: str,
    expected_side: str,
) -> float | None:
    """Return a payout only from an explicitly final coherent binary vector."""
    if not isinstance(market, dict) or market.get("closed") is not True:
        return None
    resolution = str(
        market.get("umaResolutionStatus") or market.get("uma_resolution_status") or ""
    ).strip().lower()
    if resolution not in {"resolved", "settled"}:
        return None
    condition = str(market.get("conditionId") or market.get("condition_id") or "").strip()
    if not condition or condition != str(expected_condition_id):
        raise CorrectivePaperError("V4_SETTLEMENT_CONDITION_MISMATCH")

    tokens = [str(value) for value in _json_list(market.get("clobTokenIds"))]
    prices_raw = _json_list(market.get("outcomePrices"))
    outcomes = [str(value).strip().lower() for value in _json_list(market.get("outcomes"))]
    if (
        len(tokens) != 2
        or len(prices_raw) != 2
        or len(set(tokens)) != 2
        or tokens.count(str(token_id)) != 1
    ):
        raise CorrectivePaperError("V4_SETTLEMENT_TOKEN_VECTOR_INVALID")
    prices: list[float] = []
    for raw in prices_raw:
        if isinstance(raw, bool):
            raise CorrectivePaperError("V4_SETTLEMENT_PAYOUT_VECTOR_INVALID")
        value = _finite(raw, "V4_SETTLEMENT_PAYOUT_VECTOR_INVALID")
        if value < 0.0 or value > 1.0:
            raise CorrectivePaperError("V4_SETTLEMENT_PAYOUT_VECTOR_INVALID")
        prices.append(value)
    allowed = (
        (abs(prices[0] - 1.0) <= 1e-9 and abs(prices[1]) <= 1e-9)
        or (abs(prices[1] - 1.0) <= 1e-9 and abs(prices[0]) <= 1e-9)
        or (abs(prices[0] - 0.5) <= 1e-9 and abs(prices[1] - 0.5) <= 1e-9)
    )
    if not allowed or abs(sum(prices) - 1.0) > 1e-9:
        raise CorrectivePaperError("V4_SETTLEMENT_PAYOUT_VECTOR_INVALID")
    if outcomes:
        if len(outcomes) != 2 or set(outcomes) != {"yes", "no"}:
            raise CorrectivePaperError("V4_SETTLEMENT_OUTCOME_LABEL_INVALID")
        selected_label = outcomes[tokens.index(str(token_id))]
        if selected_label != str(expected_side).strip().lower():
            raise CorrectivePaperError("V4_SETTLEMENT_TOKEN_MEANING_MISMATCH")
    return prices[tokens.index(str(token_id))]


class CorrectiveSettlementEngine:
    def __init__(
        self,
        *,
        store: CorrectiveWeatherPaperPositionStore,
        telegram,
        gamma: PublicGammaSettlementClient | None = None,
    ) -> None:
        self.store = store
        self.telegram = telegram
        self.gamma = gamma or PublicGammaSettlementClient()
        self._owns_gamma = gamma is None

    async def close(self) -> None:
        if self._owns_gamma:
            await self.gamma.close()

    @staticmethod
    def _resolution_message(position: dict) -> str:
        status = str(position.get("status") or "")
        label = "WIN" if status == "WON" else "LOSS" if status == "LOST" else "PUSH / PARTIAL"
        icon = "✅" if status == "WON" else "❌" if status == "LOST" else "🟰"
        pnl = float(position.get("pnl") or 0.0)
        return "\n".join([
            f"{icon} <b>PAPER TRADE FINISHED — {label}</b>",
            html.escape(str(position.get("title") or ""))[:120],
            f"Position: <b>#{int(position['id'])}</b> | Side: <b>{html.escape(str(position.get('side') or ''))}</b>",
            f"Paper amount used: <b>${float(position.get('capital_used') or 0.0):.2f}</b>",
            f"Paper payout: <b>${float(position.get('proceeds') or 0.0):.2f}</b>",
            f"Result: <b>${pnl:+.2f}</b>",
            "📒 Simulated fill only. No real order was placed.",
        ])

    async def _notify_resolved(self) -> tuple[int, list[str]]:
        sent = 0
        errors: list[str] = []
        rows = await asyncio.to_thread(self.store.resolved_pending_notification, 50)
        for row in rows:
            pid = int(row["id"])
            await asyncio.to_thread(self.store.set_notification_state, pid, "SENDING", None)
            try:
                message_id = await self.telegram.send_html(self._resolution_message(row))
            except DeliveryUncertain as exc:
                await asyncio.to_thread(self.store.set_notification_state, pid, "UNCERTAIN", None)
                errors.append(f"SETTLEMENT_NOTIFY_UNCERTAIN:{pid}:{exc.code}")
                continue
            except Exception as exc:
                await asyncio.to_thread(self.store.set_notification_state, pid, "PENDING", None)
                errors.append(f"SETTLEMENT_NOTIFY_FAILED:{pid}:{type(exc).__name__}")
                continue
            await asyncio.to_thread(
                self.store.set_notification_state, pid, "ACKNOWLEDGED", int(message_id)
            )
            sent += 1
        return sent, errors

    async def settle_once(self) -> dict:
        positions = await asyncio.to_thread(
            self.store.open_positions_for_settlement, SETTLEMENT_PAGE_SIZE
        )
        market_ids = sorted({
            str(leg.get("market_id") or "")
            for position in positions
            for leg in (position.get("legs") or [])
            if str(leg.get("market_id") or "")
        })
        semaphore = asyncio.Semaphore(6)

        async def fetch(mid: str):
            async with semaphore:
                try:
                    return mid, await self.gamma.market_by_id(mid), None
                except Exception as exc:
                    return mid, None, type(exc).__name__

        results = await asyncio.gather(*(fetch(mid) for mid in market_ids))
        markets: dict[str, dict] = {}
        errors: list[str] = []
        for mid, market, error in results:
            if error or not isinstance(market, dict):
                errors.append(
                    f"SETTLEMENT_LOOKUP_UNAVAILABLE:{mid}:{error or 'NO_DATA'}"
                )
            else:
                markets[mid] = market

        resolved = 0
        for position in positions:
            payouts: list[dict] = []
            complete = True
            for leg in position.get("legs") or []:
                mid = str(leg.get("market_id") or "")
                market = markets.get(mid)
                if market is None:
                    complete = False
                    break
                try:
                    payout = final_token_payout_v4(
                        str(leg.get("token_id") or ""),
                        market,
                        expected_condition_id=str(leg.get("condition_id") or ""),
                        expected_side=str(leg.get("side") or ""),
                    )
                except CorrectivePaperError as exc:
                    errors.append(
                        f"SETTLEMENT_INVALID:{position.get('id')}:{exc.code}"
                    )
                    complete = False
                    break
                if payout is None:
                    complete = False
                    break
                payouts.append({
                    "market_id": mid,
                    "condition_id": str(leg.get("condition_id") or ""),
                    "token_id": str(leg.get("token_id") or ""),
                    "side": str(leg.get("side") or ""),
                    "payout": float(payout),
                    "uma_resolution_status": str(
                        market.get("umaResolutionStatus") or ""
                    ),
                })
            if not complete or not payouts:
                continue
            evidence = {
                "version": PAPER_SETTLEMENT_VERSION_V4,
                "source": "GAMMA_RESOLVED_EXACT_TOKEN_FINAL_VECTOR",
                "checked_at": time.time(),
                "legs": payouts,
                "financial_authority": False,
            }
            row = await asyncio.to_thread(
                self.store.resolve_position,
                int(position["id"]),
                sum(item["payout"] for item in payouts),
                evidence,
            )
            if row is not None:
                resolved += 1
        notified, notification_errors = await self._notify_resolved()
        errors.extend(notification_errors)
        return {
            "version": PAPER_SETTLEMENT_VERSION_V4,
            "open_checked": len(positions),
            "market_ids_checked": len(market_ids),
            "resolved_now": resolved,
            "resolution_messages_sent": notified,
            "errors": errors,
        }


class ClearWeatherPaperCommandController(WeatherPaperCommandController):
    """Operator UI: answer what is open, finished, skipped and excluded plainly."""

    @staticmethod
    def _pct(value: object) -> str:
        try:
            number = float(value)
        except (TypeError, ValueError, OverflowError):
            return "n/a"
        return f"{100.0 * number:.1f}%" if math.isfinite(number) else "n/a"

    def _status_text(self) -> str:
        status = self._read_status()
        stats = self.store.stats()
        try:
            finished = float(status.get("finished_at") or 0.0)
        except (TypeError, ValueError, OverflowError):
            finished = 0.0
        age = time.time() - finished if finished > 0 else float("inf")
        healthy = bool(status.get("cycle_ok")) and 0.0 <= age <= STATUS_MAX_AGE_SECONDS
        icon = "🟢" if healthy else "🔴"
        lines = [
            f"{icon} <b>WEATHER PAPER BOT — {'RUNNING NORMALLY' if healthy else 'NEEDS ATTENTION'}</b>",
            f"Last full cycle: <b>{int(max(0.0, age)) if math.isfinite(age) else 'unknown'}s ago</b>",
            f"Open paper trades: <b>{int(stats.get('open') or 0)}</b>",
            f"Finished: <b>{int(stats.get('won') or 0)} wins / {int(stats.get('lost') or 0)} losses / {int(stats.get('partial') or 0)} push/partial</b>",
            f"Paper P&amp;L: <b>${float(stats.get('pnl') or 0.0):+.2f}</b>",
            f"Delivery uncertain: <b>{int(stats.get('delivery_uncertain') or 0)}</b>",
            "",
            "Same-day conditioned lane: <b>OFF pending observation/remaining-hours acceptance</b>",
            "Structural guarantee lane: <b>OFF pending strict common-resolution proof</b>",
            "Real orders: <b>DISABLED</b>",
        ]
        errors = status.get("errors") or []
        if errors:
            lines.append(
                "⚠️ Last cycle issues: <code>"
                + html.escape(", ".join(map(str, errors))[:700])
                + "</code>"
            )
        return "\n".join(lines)

    def _stats_text(self) -> str:
        stats = self.store.stats()
        excluded = int(stats.get("unverified") or 0) + int(stats.get("quarantined") or 0)
        return "\n".join([
            "📊 <b>PAPER TRADING SUMMARY</b>",
            "",
            "<b>OPEN NOW</b>",
            f"Positions: <b>{int(stats.get('open') or 0)}</b>",
            f"Paper money currently at risk: <b>${float(stats.get('open_capital') or 0.0):.2f}</b>",
            "",
            "<b>FINISHED TRADES</b>",
            f"Completed: <b>{int(stats.get('resolved') or 0)}</b>",
            f"Wins: <b>{int(stats.get('won') or 0)}</b> | Losses: <b>{int(stats.get('lost') or 0)}</b> | Push/partial: <b>{int(stats.get('partial') or 0)}</b>",
            f"Paper amount used: <b>${float(stats.get('resolved_capital') or 0.0):.2f}</b>",
            f"Paper payout received: <b>${float(stats.get('resolved_proceeds') or 0.0):.2f}</b>",
            f"Net paper P&amp;L: <b>${float(stats.get('pnl') or 0.0):+.2f}</b>",
            f"ROI on finished validated trades: <b>{self._pct(stats.get('resolved_roi'))}</b>",
            "",
            "<b>NOT TRADED / NOT COUNTED</b>",
            f"No fill: <b>{int(stats.get('no_fill') or 0)}</b>",
            f"Skipped at final recheck: <b>{int(stats.get('skipped') or 0)}</b>",
            f"Delivery uncertain: <b>{int(stats.get('delivery_uncertain') or 0)}</b>",
            f"Old/unverified/excluded: <b>{excluded}</b>",
            "",
            "Only v4 VALIDATED rows count in wins, losses, ROI and P&amp;L.",
            "📒 Everything is simulated; no real order was placed.",
        ])

    def _positions_text(self) -> str:
        rows = self.store.open_positions(10)
        lines = [f"📂 <b>OPEN PAPER POSITIONS — {len(rows)}</b>"]
        if not rows:
            return "\n".join(lines + ["No validated paper trades are open right now."])
        for row in rows:
            lines.extend([
                "",
                f"<b>#{int(row['id'])} — {html.escape(str(row.get('side') or ''))}</b>",
                html.escape(str(row.get("title") or ""))[:100],
                f"Entry cost/share: <b>${float(row.get('entry_cost_per_unit') or 0.0):.4f}</b>",
                f"Paper amount used: <b>${float(row.get('capital_used') or 0.0):.2f}</b>",
                f"Shares: <b>{float(row.get('filled_units') or 0.0):.4f}</b>",
                "Status: <b>🟡 OPEN — waiting for final market result</b>",
            ])
        return "\n".join(lines)

    def _history_text(self) -> str:
        rows = self.store.recent_positions(10, resolved_only=True)
        lines = ["🏁 <b>RECENT FINISHED PAPER TRADES</b>"]
        if not rows:
            return "\n".join(lines + ["No validated trades have finished yet."])
        for row in rows:
            status = str(row.get("status") or "")
            result = (
                "✅ WIN" if status == "WON"
                else "❌ LOSS" if status == "LOST"
                else "🟰 PUSH/PARTIAL"
            )
            pnl = float(row.get("pnl") or 0.0)
            lines.extend([
                "",
                f"<b>#{int(row['id'])} {result}</b>",
                html.escape(str(row.get("title") or ""))[:95],
                f"Paper amount: ${float(row.get('capital_used') or 0.0):.2f} | P&amp;L: <b>${pnl:+.2f}</b>",
            ])
        return "\n".join(lines)

    def _recent_text(self) -> str:
        rows = self.store.recent_signals(10)
        lines = ["🧾 <b>RECENT SIGNALS / WHAT HAPPENED</b>"]
        if not rows:
            return "\n".join(lines + ["No signals stored yet."])
        for row in rows:
            position = str(row.get("position_status") or row.get("status") or "NOT TRADED")
            lines.append(
                f"#{int(row['id'])} {html.escape(str(row.get('side') or ''))} @ "
                f"${float(row.get('entry_cost') or 0.0):.4f} → <b>{html.escape(position)}</b>"
            )
        return "\n".join(lines)

    @staticmethod
    def _help_text() -> str:
        return "\n".join([
            "🤖 <b>WEATHER PAPER COMMANDS</b>",
            "/status — is the bot healthy and what is enabled?",
            "/stats — open trades, wins/losses, money used and P&amp;L",
            "/positions — exactly what paper trades are open now",
            "/history — latest finished wins/losses",
            "/recent — latest signals and whether they became trades",
            "/help — this list",
            "",
            "No /took command is needed. Valid delivered signals are tracked automatically.",
        ])
