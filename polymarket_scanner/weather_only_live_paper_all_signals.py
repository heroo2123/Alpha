from __future__ import annotations

"""Unified weather PAPER signal runtime.

This promotion layer keeps every real-money boundary closed while making reviewed
weather strategies visible to the user as Telegram PAPER signals.  The inherited
future-day V4 lane is unchanged.  Hardened structural underrounds are rechecked at
send time, and freshly persisted same-day WRH/NWS/GEFS captures can produce an
explicitly *uncalibrated* friend-style late-lock PAPER signal.

Nothing in this module signs, places, amends or cancels an order.  Same-day model
support remains research evidence rather than a calibrated probability.
"""

import argparse
import asyncio
import html
import json
import math
import time
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from .weather_only_clob import WeatherCLOBError, conservative_taker_fee_per_share
from .weather_only_contract_strict import (
    StrictWeatherContractError,
    compile_strict_temperature_event,
    strict_contract_identity,
)
from .weather_only_contracts import DAILY_HIGH, DAILY_LOW
from .weather_only_live_paper import (
    DEFAULT_FORECAST_CACHE_SECONDS,
    DEFAULT_FORECAST_RAW_GAP_MIN,
    DEFAULT_INTERVAL_SECONDS,
    DEFAULT_MAX_FORECAST_EVENTS,
    STRUCTURAL_FINGERPRINT_SECONDS,
    WeatherLivePaperError,
    _atomic_json,
    _event_link,
    _event_title,
    _fingerprint,
)
from .weather_only_live_paper_three_layer_validation import (
    ThreeLayerValidationWeatherLivePaperService,
)
from .weather_only_live_paper_v2 import DEFAULT_PAPER_STAKE_USD
from .weather_only_live_paper_v4 import (
    LOCAL_MIDNIGHT_MARGIN_SECONDS,
    PAPER_EXECUTION_PROTOCOL_V4,
    QUOTE_DECISION_TTL_SECONDS,
    V4InvariantError,
    _sha,
    _validate_exact_snapshot,
)
from .weather_only_paper_commands_canonical import CanonicalWeatherPaperCommandController
from .weather_only_paper_corrective import CorrectiveSettlementEngine, DeliveryUncertain
from .weather_only_paper_positions import WeatherPaperPositionError
from .weather_only_paper_recovery_final import FinalCrashSafeWeatherPaperStore
from .weather_only_station_metadata import WeatherStationMetadataError
from .weather_only_structural import binary_pair_underround, complete_bucket_underround


ALL_PAPER_RUNTIME_VERSION = "weather_all_paper_signals_v1_same_day_structural"
SAME_DAY_FRIEND_LANE = "weather_same_day_friend_lock"
SAME_DAY_FRIEND_EVIDENCE = "HEURISTIC_UNCALIBRATED_WRH_NWS_GEFS_V1"
SAME_DAY_MIN_ASK = 0.90
SAME_DAY_MAX_ASK = 0.975
SAME_DAY_MIN_PAYOUT_LEFT = 0.015
SAME_DAY_MIN_LOCAL_HOUR = 14.0
SAME_DAY_MIN_TREND_STEPS = 3
SAME_DAY_MAX_OBSERVATION_AGE_SECONDS = 100.0 * 60.0
SAME_DAY_MIN_GEFS_SUPPORT = 30.0 / 31.0
SAME_DAY_MIN_DROP_F = 2.0
SAME_DAY_MIN_DROP_C = 1.0

_DIRECTIONAL_PROMOTED_LANES = {SAME_DAY_FRIEND_LANE, "weather_result_lag"}


class AllPaperInvariantError(RuntimeError):
    def __init__(self, code: str):
        self.code = str(code)
        super().__init__(self.code)


class AllSignalsCrashSafeWeatherPaperStore(FinalCrashSafeWeatherPaperStore):
    """Extend the validated PAPER position facade to reviewed directional lanes.

    The base position store already understands a directional exact-token position,
    but historically admitted only the future-day lane by name.  Reuse that exact
    sizing/accounting implementation for explicitly promoted single-token PAPER lanes.
    """

    def _position_spec(self, signal: dict, target_stake_usd: float) -> dict | None:
        lane = str(signal.get("lane") or "").strip()
        if lane in _DIRECTIONAL_PROMOTED_LANES:
            shim = dict(signal)
            shim["lane"] = "weather_forecast_raw_gap"
            spec = super()._position_spec(shim, target_stake_usd)
            if spec is not None:
                spec["lane"] = lane
                spec["evidence_class"] = str(signal.get("evidence_class") or "")
            return spec
        return super()._position_spec(signal, target_stake_usd)


def _inside_bucket(value: float, bucket) -> bool:
    return (
        (bucket.lower is None or value >= float(bucket.lower))
        and (bucket.upper is None or value <= float(bucket.upper))
    )


def _bucket_label(bucket, unit: str) -> str:
    if bucket.lower is None:
        return f"≤ {float(bucket.upper):g}{unit}"
    if bucket.upper is None:
        return f"≥ {float(bucket.lower):g}{unit}"
    return f"{float(bucket.lower):g}–{float(bucket.upper):g}{unit}"


def _trend_steps(values: list[float], family: str) -> int:
    if len(values) < 2:
        return 0
    count = 0
    for left, right in zip(reversed(values[:-1]), reversed(values[1:])):
        okay = right <= left if family == DAILY_HIGH else right >= left
        if not okay:
            break
        count += 1
    return count


def _member_support(capture: dict, bucket, observed_extreme: float, family: str) -> tuple[int, int] | None:
    remaining = capture.get("remaining_hours_path")
    if not isinstance(remaining, dict):
        return None
    ensemble = remaining.get("ensemble")
    if not isinstance(ensemble, dict):
        return None
    members = ensemble.get("members")
    if not isinstance(members, list) or not members:
        return None
    hits = 0
    total = 0
    for row in members:
        if not isinstance(row, dict):
            return None
        try:
            unresolved = float(row["extreme_value"])
        except (KeyError, TypeError, ValueError, OverflowError):
            return None
        if not math.isfinite(unresolved):
            return None
        final_extreme = (
            max(observed_extreme, unresolved)
            if family == DAILY_HIGH
            else min(observed_extreme, unresolved)
        )
        total += 1
        if _inside_bucket(final_extreme, bucket):
            hits += 1
    return hits, total


def _friend_capture_gate(capture: dict, compiled, bucket) -> dict | None:
    """Apply only explicit, replayable research gates to a saved three-layer capture."""
    if not isinstance(capture, dict):
        return None
    # Population alignment is intentionally still unproven.  That one reason is
    # allowed because this lane is explicitly heuristic; any additional evidence gap
    # suppresses the alert.
    reasons = {str(value) for value in (capture.get("block_reasons") or [])}
    if reasons - {"WRH_TO_MODEL_POPULATION_ALIGNMENT_UNPROVEN"}:
        return None
    if capture.get("near_term_path") is None or capture.get("remaining_hours_path") is None:
        return None

    observed = capture.get("observed_state")
    observations = capture.get("official_observations")
    metadata = capture.get("station_metadata")
    if not isinstance(observed, dict) or not isinstance(observations, list) or not isinstance(metadata, dict):
        return None
    try:
        extreme = float(observed["extreme_value"])
        as_of = float(capture["as_of"])
        timezone_name = str(metadata["timezone"])
    except (KeyError, TypeError, ValueError, OverflowError):
        return None
    if not (math.isfinite(extreme) and math.isfinite(as_of)):
        return None
    if not _inside_bucket(extreme, bucket):
        return None

    rows: list[tuple[float, float]] = []
    for row in observations:
        if not isinstance(row, dict):
            continue
        try:
            observed_at = float(row["observed_at"])
            value = float(row["value"])
        except (KeyError, TypeError, ValueError, OverflowError):
            continue
        if math.isfinite(observed_at) and math.isfinite(value) and observed_at <= as_of + 1e-6:
            rows.append((observed_at, value))
    rows.sort()
    if len(rows) < SAME_DAY_MIN_TREND_STEPS + 1:
        return None
    latest_at, current = rows[-1]
    if as_of - latest_at > SAME_DAY_MAX_OBSERVATION_AGE_SECONDS:
        return None
    values = [value for _, value in rows]
    trend = _trend_steps(values, compiled.family)
    if trend < SAME_DAY_MIN_TREND_STEPS:
        return None

    try:
        local = datetime.fromtimestamp(as_of, tz=timezone.utc).astimezone(ZoneInfo(timezone_name))
    except Exception:
        return None
    local_hour = local.hour + local.minute / 60.0 + local.second / 3600.0
    if local_hour < SAME_DAY_MIN_LOCAL_HOUR:
        return None

    drop = extreme - current if compiled.family == DAILY_HIGH else current - extreme
    required_drop = SAME_DAY_MIN_DROP_C if compiled.unit == "C" else SAME_DAY_MIN_DROP_F
    if drop < required_drop:
        return None

    near = capture.get("near_term_path")
    if not isinstance(near, dict):
        return None
    try:
        near_extreme = float(near["sampled_extreme"])
    except (KeyError, TypeError, ValueError, OverflowError):
        return None
    if not math.isfinite(near_extreme):
        return None
    near_final = (
        max(extreme, near_extreme)
        if compiled.family == DAILY_HIGH
        else min(extreme, near_extreme)
    )
    if not _inside_bucket(near_final, bucket):
        return None

    support = _member_support(capture, bucket, extreme, compiled.family)
    if support is None:
        return None
    hits, total = support
    if total != 31:
        return None
    raw_support = hits / total
    if raw_support + 1e-12 < SAME_DAY_MIN_GEFS_SUPPORT:
        return None

    return {
        "observed_extreme": extreme,
        "current": current,
        "trend_steps": trend,
        "observed_drop": drop,
        "local_time": local.isoformat(timespec="minutes"),
        "nws_sampled_extreme": near_extreme,
        "gefs_hits": hits,
        "gefs_total": total,
        "raw_support": raw_support,
        "capture_sha256": str(capture.get("capture_sha256") or ""),
    }


class AllPaperWeatherLiveService(ThreeLayerValidationWeatherLivePaperService):
    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self._all_paper_superseded_settlement = self.settlement
        self._all_paper_superseded_commands = self.commands
        self.positions = AllSignalsCrashSafeWeatherPaperStore(self.db_path)
        self.settlement = CorrectiveSettlementEngine(store=self.positions, telegram=self.telegram)
        self.commands = CanonicalWeatherPaperCommandController(
            telegram=self.telegram,
            store=self.positions,
            status_path=self.status_path,
            paper_stake_usd=self.paper_stake_usd,
        )
        self._all_paper_same_day_sent = 0
        self._all_paper_same_day_skipped = 0
        self._all_paper_structural_sent = 0
        self._all_paper_structural_skipped = 0

    async def close(self) -> None:
        await asyncio.gather(
            self._all_paper_superseded_settlement.close(),
            self._all_paper_superseded_commands.close(),
            return_exceptions=True,
        )
        await super().close()

    async def send_startup(self) -> int:
        release = self.release_sha()
        return await self.telegram.send_html("\n".join([
            "🟢 <b>WEATHER ALL-PAPER BOT ONLINE</b>",
            "",
            "Future-day forecast PAPER alerts: <b>ON</b>",
            "Same-day friend-style late-lock PAPER alerts: <b>ON (uncalibrated)</b>",
            "Hardened structural underround PAPER alerts: <b>ON</b>",
            "Result-lag and maker lifecycle promotion: <b>still gated in this release</b>",
            "",
            "Every promoted alert is recorded for outcome/performance review.",
            "🚫 <b>NO REAL ORDERS / NO WALLET AUTHORITY</b>",
            f"Release: <code>{html.escape(release[:12])}</code>",
        ]))

    async def _recheck_structural(self, opportunity: dict, event: dict | None) -> dict | None:
        if not isinstance(event, dict):
            return None
        compiled = compile_strict_temperature_event(event)
        exact = await self.runtime.clob.exact_event_snapshot(compiled)
        _validate_exact_snapshot(compiled, exact)
        lane = str(opportunity.get("lane") or "")
        if lane == "weather_binary_pair_underround":
            rows = binary_pair_underround(compiled, exact.books, exact.parameters)
        elif lane == "weather_complete_bucket_underround":
            candidate = complete_bucket_underround(compiled, exact.books, exact.parameters)
            rows = [] if candidate is None else [candidate]
        else:
            return None
        wanted = tuple(str(value) for value in (opportunity.get("token_ids") or ()))
        for row in rows:
            value = row.as_dict()
            if tuple(str(token) for token in value.get("token_ids") or ()) == wanted:
                return value
        return None

    def _structural_paper_message(self, fresh: dict, event: dict | None) -> str:
        asks = [float(value) for value in fresh.get("ask_prices") or ()]
        fees = [float(value) for value in fresh.get("conservative_fees_per_share") or ()]
        legs = [
            f"{idx}. ask ${ask:.4f} + fee ${fees[idx - 1]:.5f}"
            for idx, ask in enumerate(asks, 1)
        ]
        return "\n".join([
            "🧪 <b>PAPER STRUCTURAL WEATHER SIGNAL</b>",
            f"<b>{html.escape(_event_title(event, str(fresh.get('event_id') or '')))}</b>",
            f"Lane: <code>{html.escape(str(fresh.get('lane') or ''))}</code>",
            "",
            *legs,
            "",
            f"Exact total cost/set: <b>${float(fresh['gross_cost_per_set']):.4f}</b>",
            f"Locked payout/set if every leg fills: <b>${float(fresh['locked_payout_per_set']):.4f}</b>",
            f"Locked profit/set: <b>${float(fresh['locked_profit_per_set']):.4f}</b>",
            f"Common visible capacity: <b>{float(fresh['common_best_ask_shares']):.2f} sets</b>",
            "",
            "⚠️ PAPER ONLY. The simulated basket assumes every required leg fills at the captured asks.",
            "Partial/manual execution can create directional risk; verify every leg before doing anything yourself.",
            "🚫 No real order was placed.",
        ])

    async def _save_and_send_structural(self, opportunity: dict, event: dict | None) -> tuple[bool, str | None]:
        try:
            fresh = await self._recheck_structural(opportunity, event)
        except (StrictWeatherContractError, V4InvariantError, WeatherCLOBError) as exc:
            self._all_paper_structural_skipped += 1
            return False, getattr(exc, "code", type(exc).__name__)
        if fresh is None:
            self._all_paper_structural_skipped += 1
            return False, None
        if not fresh.get("contract_partition_proven"):
            self._all_paper_structural_skipped += 1
            return False, "STRUCTURAL_COMMON_PAYOUT_UNPROVEN"

        now = time.time()
        event_id = str(fresh.get("event_id") or "")
        fp = _fingerprint(
            {"lane": fresh.get("lane"), "event_id": event_id, "tokens": list(fresh.get("token_ids") or ())},
            bucket_seconds=STRUCTURAL_FINGERPRINT_SECONDS,
            at=now,
        )
        payload = dict(fresh)
        payload.update({
            "paper_mode": True,
            "event_title": _event_title(event, event_id),
            "event_url": _event_link(event),
            "source_timestamp": now,
            "quote_observed_at": now,
            "paper_fill_at": now,
            "decision_expires_at": now + QUOTE_DECISION_TTL_SECONDS,
            "paper_target_stake_usd": float(self.paper_stake_usd),
            "execution_verified": False,
            "financial_authority": False,
            "automatic_order_placement": False,
        })
        signal_id = self.positions.save_signal(
            fingerprint=fp,
            lane=str(fresh.get("lane") or "weather_structural"),
            evidence_class="DETERMINISTIC_EXACT_CLOB_PAPER",
            event_id=event_id,
            payload=payload,
            entry_cost=float(fresh["gross_cost_per_set"]),
            raw_gap=float(fresh["locked_profit_per_set"]),
            theoretical_payout=float(fresh["locked_payout_per_set"]),
            created_at=now,
        )
        if signal_id is None:
            return False, None
        await asyncio.to_thread(self.positions.set_signal_status, signal_id, "PENDING_DELIVERY")
        try:
            message_id = await self.telegram.send_html(
                self._structural_paper_message(fresh, event),
                url=_event_link(event),
                expires_at=float(payload["decision_expires_at"]),
            )
        except DeliveryUncertain as exc:
            await asyncio.to_thread(self.positions.set_signal_status, signal_id, "DELIVERY_UNCERTAIN")
            return True, exc.code
        except WeatherLivePaperError as exc:
            await asyncio.to_thread(self.positions.set_signal_status, signal_id, "DELIVERY_FAILED")
            return True, exc.code
        sent_at = time.time()
        self.positions.mark_telegram_sent(signal_id, int(message_id), sent_at=sent_at)
        if sent_at >= float(payload["decision_expires_at"]):
            await asyncio.to_thread(self.positions.set_signal_status, signal_id, "EXPIRED")
            return True, "DELIVERY_RECEIPT_AFTER_EXPIRY"
        await asyncio.to_thread(self.positions.set_signal_status, signal_id, "ACKNOWLEDGED")
        try:
            await asyncio.to_thread(self.positions.ensure_position_for_signal, signal_id, self.paper_stake_usd)
        except Exception as exc:
            await asyncio.to_thread(self.positions.set_signal_status, signal_id, "PAPER_ACCOUNTING_ERROR")
            return True, f"STRUCTURAL_PAPER_ACCOUNTING:{getattr(exc, 'code', type(exc).__name__)}"
        self._all_paper_structural_sent += 1
        return True, None

    def _captures_since(self, not_before: float) -> list[dict]:
        with self.same_day_captures._conn() as db:
            rows = db.execute(
                """
                SELECT capture_sha256
                FROM weather_same_day_captures
                WHERE as_of>=?
                ORDER BY id
                """,
                (float(not_before),),
            ).fetchall()
        captures: list[dict] = []
        for row in rows:
            value = self.same_day_captures.capture_json(str(row["capture_sha256"]))
            if isinstance(value, dict):
                captures.append(value)
        return captures

    async def _same_day_friend_candidate(self, capture: dict, event: dict) -> dict | None:
        compiled = compile_strict_temperature_event(event)
        if compiled.family not in {DAILY_HIGH, DAILY_LOW}:
            return None
        if str(capture.get("event_id") or "") != str(compiled.event_id):
            raise AllPaperInvariantError("ALL_PAPER_CAPTURE_EVENT_MISMATCH")
        if str(capture.get("station") or "").upper() != str(compiled.station_hint or "").upper():
            raise AllPaperInvariantError("ALL_PAPER_CAPTURE_STATION_MISMATCH")
        if str(capture.get("target_date") or "") != compiled.target_date.isoformat():
            raise AllPaperInvariantError("ALL_PAPER_CAPTURE_DATE_MISMATCH")

        observed = capture.get("observed_state") or {}
        try:
            extreme = float(observed["extreme_value"])
        except (KeyError, TypeError, ValueError, OverflowError):
            return None
        bucket = next((row for row in compiled.buckets if row.trade_open and row.yes_token and _inside_bucket(extreme, row)), None)
        if bucket is None:
            return None
        gates = _friend_capture_gate(capture, compiled, bucket)
        if gates is None:
            return None

        metadata = await self._station_metadata_for_compiled(compiled)
        if metadata is None:
            return None
        zone = ZoneInfo(str(metadata.timezone))
        if datetime.now(tz=timezone.utc).astimezone(zone).date() != compiled.target_date:
            return None

        exact = await self.runtime.clob.exact_event_snapshot(compiled)
        _validate_exact_snapshot(compiled, exact)
        params = exact.parameters.get(bucket.condition_id)
        book = exact.books.get(str(bucket.yes_token))
        if params is None or book is None or book.best_ask is None or book.best_ask_size <= 0.0:
            return None
        ask = float(book.best_ask)
        if ask < SAME_DAY_MIN_ASK or ask > SAME_DAY_MAX_ASK:
            return None
        fee = conservative_taker_fee_per_share(ask, params.fee_rate, params.fee_exponent)
        cost = ask + fee
        payout_left = 1.0 - cost
        raw_support = float(gates["raw_support"])
        raw_gap = raw_support - cost
        if payout_left < SAME_DAY_MIN_PAYOUT_LEFT or raw_gap <= 0.0:
            return None
        tick = float(params.minimum_tick_size)
        if tick <= 0.0 or abs(ask / tick - round(ask / tick)) > 1e-6:
            raise AllPaperInvariantError("ALL_PAPER_SAME_DAY_ASK_OFF_TICK")

        now = time.time()
        midnight = self._target_midnight_epoch(compiled.target_date, str(metadata.timezone))
        expires_at = min(now + QUOTE_DECISION_TTL_SECONDS, midnight - LOCAL_MIDNIGHT_MARGIN_SECONDS)
        if now >= expires_at:
            return None
        identity = strict_contract_identity(event, compiled)
        decision_id = _sha({
            "lane": SAME_DAY_FRIEND_LANE,
            "event_id": compiled.event_id,
            "market_id": bucket.market_id,
            "observed_extreme": gates["observed_extreme"],
            "capture_sha256": gates["capture_sha256"],
            "book_hash": str(book.book_hash or ""),
        })
        return {
            "lane": SAME_DAY_FRIEND_LANE,
            "evidence_class": SAME_DAY_FRIEND_EVIDENCE,
            "event_id": compiled.event_id,
            "market_id": bucket.market_id,
            "condition_id": bucket.condition_id,
            "token_id": bucket.yes_token,
            "side": "YES",
            "station": str(compiled.station_hint).upper(),
            "station_timezone": str(metadata.timezone),
            "target_date": compiled.target_date.isoformat(),
            "family": compiled.family,
            "unit": compiled.unit,
            "bucket_label": _bucket_label(bucket, str(compiled.unit)),
            "raw_probability": raw_support,
            "gefs_hits": int(gates["gefs_hits"]),
            "gefs_total": int(gates["gefs_total"]),
            "observed_extreme": float(gates["observed_extreme"]),
            "current": float(gates["current"]),
            "trend_steps": int(gates["trend_steps"]),
            "observed_drop": float(gates["observed_drop"]),
            "local_time": str(gates["local_time"]),
            "nws_sampled_extreme": float(gates["nws_sampled_extreme"]),
            "capture_sha256": str(gates["capture_sha256"]),
            "ask": ask,
            "fee": fee,
            "entry_cost": cost,
            "raw_gap": raw_gap,
            "payout_left": payout_left,
            "ask_size": float(book.best_ask_size),
            "quote_observed_at": float(book.received_at),
            "paper_fill_at": float(exact.finished_at),
            "decision_expires_at": expires_at,
            "book_provider_timestamp": str(book.timestamp or ""),
            "book_hash": str(book.book_hash or ""),
            "minimum_order_size": float(params.minimum_order_size),
            "minimum_tick_size": tick,
            "strict_contract_sha256": str(identity["sha256"]),
            "decision_id": decision_id,
            "paper_target_stake_usd": float(self.paper_stake_usd),
            "paper_execution_protocol_version": PAPER_EXECUTION_PROTOCOL_V4,
            "event_title": _event_title(event, compiled.event_id),
            "event_url": _event_link(event),
            "paper_mode": True,
            "calibrated_probability": False,
            "execution_verified": False,
            "financial_authority": False,
            "automatic_order_placement": False,
        }

    def _same_day_message(self, candidate: dict) -> str:
        return "\n".join([
            "🌡️ <b>PAPER SAME-DAY LATE-LOCK SIGNAL</b>",
            f"<b>{html.escape(str(candidate['event_title']))}</b>",
            "",
            f"Simulated action: <b>BUY YES — {html.escape(str(candidate['bucket_label']))}</b>",
            f"Exact ask: <b>${float(candidate['ask']):.4f}</b> + fee ${float(candidate['fee']):.5f}",
            f"Payout remaining after captured cost: <b>{100.0 * float(candidate['payout_left']):.2f}%</b>",
            "",
            f"Official extreme so far: <b>{float(candidate['observed_extreme']):g}{html.escape(str(candidate['unit']))}</b>",
            f"Latest official observation: <b>{float(candidate['current']):g}{html.escape(str(candidate['unit']))}</b>",
            f"Non-adverse official steps: <b>{int(candidate['trend_steps'])}</b>",
            f"NWS sampled remaining extreme: <b>{float(candidate['nws_sampled_extreme']):g}{html.escape(str(candidate['unit']))}</b>",
            f"GEFS remaining paths finishing in this bucket: <b>{int(candidate['gefs_hits'])}/{int(candidate['gefs_total'])}</b> ({100.0 * float(candidate['raw_probability']):.1f}%, uncalibrated)",
            f"Station/date: <b>{html.escape(str(candidate['station']))} / {html.escape(str(candidate['target_date']))}</b>",
            "",
            "⚠️ This is the friend-style late-lock experiment. GEFS path frequency is NOT a calibrated win probability.",
            "Verify the official source and live price yourself before manually trading anything.",
            "📒 PAPER ONLY — no real order was placed.",
            f"ID: <code>{html.escape(str(candidate['decision_id'])[:12])}</code>",
        ])

    async def _save_and_send_same_day(self, candidate: dict, event: dict) -> tuple[bool, str | None]:
        # One more exact snapshot immediately before persistence/delivery.  A change in
        # price, token semantics or book freshness suppresses the alert rather than
        # inheriting the earlier capture as execution authority.
        compiled = compile_strict_temperature_event(event)
        bucket = next((row for row in compiled.buckets if row.market_id == str(candidate["market_id"])), None)
        if bucket is None or bucket.yes_token != str(candidate["token_id"]):
            return False, "ALL_PAPER_SAME_DAY_MARKET_CHANGED"
        try:
            exact = await self.runtime.clob.exact_event_snapshot(compiled)
            _validate_exact_snapshot(compiled, exact)
        except (WeatherCLOBError, V4InvariantError) as exc:
            self._all_paper_same_day_skipped += 1
            return False, getattr(exc, "code", type(exc).__name__)
        params = exact.parameters.get(bucket.condition_id)
        book = exact.books.get(str(bucket.yes_token))
        if params is None or book is None or book.best_ask is None or book.best_ask_size <= 0.0:
            self._all_paper_same_day_skipped += 1
            return False, None
        ask = float(book.best_ask)
        if ask < SAME_DAY_MIN_ASK or ask > SAME_DAY_MAX_ASK:
            self._all_paper_same_day_skipped += 1
            return False, None
        fee = conservative_taker_fee_per_share(ask, params.fee_rate, params.fee_exponent)
        cost = ask + fee
        raw_support = float(candidate["raw_probability"])
        if 1.0 - cost < SAME_DAY_MIN_PAYOUT_LEFT or raw_support - cost <= 0.0:
            self._all_paper_same_day_skipped += 1
            return False, None
        now = time.time()
        if now >= float(candidate["decision_expires_at"]):
            self._all_paper_same_day_skipped += 1
            return False, None
        fresh = dict(candidate)
        fresh.update({
            "ask": ask,
            "fee": fee,
            "entry_cost": cost,
            "raw_gap": raw_support - cost,
            "payout_left": 1.0 - cost,
            "ask_size": float(book.best_ask_size),
            "quote_observed_at": float(book.received_at),
            "paper_fill_at": float(exact.finished_at),
            "book_provider_timestamp": str(book.timestamp or ""),
            "book_hash": str(book.book_hash or ""),
        })
        # Dedupe by the official observed extreme rather than every hourly quote.  A
        # new official extreme can legitimately create a new prospective experiment.
        fingerprint = _sha({
            "lane": SAME_DAY_FRIEND_LANE,
            "event_id": fresh["event_id"],
            "market_id": fresh["market_id"],
            "observed_extreme": fresh["observed_extreme"],
        })
        signal_id = self.positions.save_signal(
            fingerprint=fingerprint,
            lane=SAME_DAY_FRIEND_LANE,
            evidence_class=SAME_DAY_FRIEND_EVIDENCE,
            event_id=str(fresh["event_id"]),
            market_id=str(fresh["market_id"]),
            side="YES",
            token_id=str(fresh["token_id"]),
            model_probability=raw_support,
            entry_cost=cost,
            raw_gap=float(fresh["raw_gap"]),
            theoretical_payout=1.0,
            created_at=float(fresh["paper_fill_at"]),
            payload=fresh,
        )
        if signal_id is None:
            return False, None
        await asyncio.to_thread(self.positions.set_signal_status, signal_id, "PENDING_DELIVERY")
        try:
            message_id = await self.telegram.send_html(
                self._same_day_message(fresh),
                url=_event_link(event),
                expires_at=float(fresh["decision_expires_at"]),
            )
        except DeliveryUncertain as exc:
            await asyncio.to_thread(self.positions.set_signal_status, signal_id, "DELIVERY_UNCERTAIN")
            return True, exc.code
        except WeatherLivePaperError as exc:
            await asyncio.to_thread(self.positions.set_signal_status, signal_id, "DELIVERY_FAILED")
            return True, exc.code
        sent_at = time.time()
        self.positions.mark_telegram_sent(signal_id, int(message_id), sent_at=sent_at)
        if sent_at >= float(fresh["decision_expires_at"]):
            await asyncio.to_thread(self.positions.set_signal_status, signal_id, "EXPIRED")
            return True, "DELIVERY_RECEIPT_AFTER_EXPIRY"
        await asyncio.to_thread(self.positions.set_signal_status, signal_id, "ACKNOWLEDGED")
        try:
            position = await asyncio.to_thread(self.positions.ensure_position_for_signal, signal_id, self.paper_stake_usd)
        except (WeatherPaperPositionError, Exception) as exc:
            await asyncio.to_thread(self.positions.set_signal_status, signal_id, "PAPER_ACCOUNTING_ERROR")
            return True, f"SAME_DAY_PAPER_ACCOUNTING:{getattr(exc, 'code', type(exc).__name__)}"
        if position is None:
            await asyncio.to_thread(self.positions.set_signal_status, signal_id, "PAPER_ACCOUNTING_ERROR")
            return True, "SAME_DAY_PAPER_POSITION_NOT_CREATED"
        self._all_paper_same_day_sent += 1
        return True, None

    async def run_cycle(self) -> dict:
        status = dict(await super().run_cycle())
        cycle_start = float(status.get("started_at") or time.time())
        events = ()
        discovery = getattr(getattr(self, "_capturing_discovery", None), "last_result", None)
        if discovery is not None:
            events = tuple(getattr(discovery, "events", ()) or ())
        by_id = {
            str(event.get("id") or event.get("eventId") or ""): event
            for event in events
            if isinstance(event, dict)
        }

        same_day_new = 0
        same_day_errors: list[str] = []
        for capture in self._captures_since(cycle_start):
            event_id = str(capture.get("event_id") or "")
            event = by_id.get(event_id)
            if event is None:
                continue
            try:
                candidate = await self._same_day_friend_candidate(capture, event)
                if candidate is None:
                    continue
                created, error = await self._save_and_send_same_day(candidate, event)
                if created:
                    same_day_new += 1
                if error:
                    same_day_errors.append(f"SAME_DAY_PAPER:{event_id}:{error}")
            except (StrictWeatherContractError, AllPaperInvariantError, WeatherStationMetadataError) as exc:
                same_day_errors.append(
                    f"SAME_DAY_PAPER:{event_id}:{getattr(exc, 'code', type(exc).__name__)}"
                )
            except Exception as exc:
                same_day_errors.append(f"SAME_DAY_PAPER:{event_id}:{type(exc).__name__}")

        errors = list(status.get("errors") or [])
        errors.extend(same_day_errors)
        status.update({
            "all_paper_runtime_version": ALL_PAPER_RUNTIME_VERSION,
            "same_day_paper_delivery_enabled": True,
            "same_day_paper_calibrated_probability": False,
            "same_day_paper_new_signals": same_day_new,
            "same_day_paper_sent_total": self._all_paper_same_day_sent,
            "same_day_paper_skipped_total": self._all_paper_same_day_skipped,
            "same_day_paper_errors": same_day_errors,
            "structural_paper_delivery_enabled": True,
            "structural_paper_sent_total": self._all_paper_structural_sent,
            "structural_paper_skipped_total": self._all_paper_structural_skipped,
            "result_lag_paper_delivery_enabled": False,
            "maker_paper_delivery_enabled": False,
            "financial_delivery": False,
            "financial_authority": False,
            "automatic_order_placement": False,
            "wallet_or_order_api_loaded": False,
            "errors": errors,
            "cycle_ok": not errors,
        })
        _atomic_json(self.status_path, status)
        return status


async def _main(args) -> int:
    service = AllPaperWeatherLiveService(
        db_path=args.db,
        status_path=args.status,
        release_file=args.release_file,
        interval_seconds=args.interval_seconds,
        forecast_cache_seconds=args.forecast_cache_seconds,
        forecast_raw_gap_min=args.forecast_raw_gap_min,
        max_forecast_events=args.max_forecast_events,
        paper_stake_usd=args.paper_stake_usd,
    )
    try:
        if args.once:
            await service.send_startup()
            status = await service.run_cycle()
            print(json.dumps(status, sort_keys=True, indent=2))
            return 0 if status.get("cycle_ok") else 2
        await service.loop()
        return 0
    finally:
        await service.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--status", type=Path, required=True)
    parser.add_argument("--release-file", type=Path, required=True)
    parser.add_argument("--interval-seconds", type=float, default=DEFAULT_INTERVAL_SECONDS)
    parser.add_argument("--forecast-cache-seconds", type=float, default=DEFAULT_FORECAST_CACHE_SECONDS)
    parser.add_argument("--forecast-raw-gap-min", type=float, default=DEFAULT_FORECAST_RAW_GAP_MIN)
    parser.add_argument("--max-forecast-events", type=int, default=DEFAULT_MAX_FORECAST_EVENTS)
    parser.add_argument("--paper-stake-usd", type=float, default=DEFAULT_PAPER_STAKE_USD)
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    raise SystemExit(asyncio.run(_main(args)))


if __name__ == "__main__":
    main()
