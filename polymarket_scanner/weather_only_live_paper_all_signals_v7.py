from __future__ import annotations

"""All-weather PAPER runtime v7: post-receipt executable accounting.

This additive wrapper preserves the reviewed V6 public-WebSocket maker lifecycle and
at-most-once maker settlement notifications, while correcting the PAPER experiment
boundary for new taker/structural alerts:

* decision evidence is frozen and Telegram-delivered first;
* a fresh exact-CLOB snapshot must start after Telegram receipt;
* only that post-receipt snapshot can create a new validated V5 PAPER position;
* position/capacity/audit/signal state commits atomically;
* restart never reconstructs a V5 fill that lacked durable post-receipt admission;
* structural baskets persist exact condition/token/side identity per leg; and
* maker virtual-order activation is atomically linked to its Telegram signal.

No real order, wallet, signing or cancellation authority exists.
"""

import argparse
import asyncio
import html
import json
import time
from pathlib import Path

from .weather_only_clob import WeatherCLOBError, conservative_taker_fee_per_share
from .weather_only_contract_strict import StrictWeatherContractError, compile_strict_temperature_event
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
from .weather_only_live_paper_all_signals import (
    SAME_DAY_FRIEND_EVIDENCE,
    SAME_DAY_FRIEND_LANE,
    SAME_DAY_MAX_ASK,
    SAME_DAY_MIN_ASK,
    SAME_DAY_MIN_PAYOUT_LEFT,
)
from .weather_only_live_paper_all_signals_v3 import AllPaperV3Error
from .weather_only_live_paper_all_signals_v6 import AllPaperWeatherLiveV6Service
from .weather_only_live_paper_v2 import DEFAULT_PAPER_STAKE_USD
from .weather_only_live_paper_v4 import (
    QUOTE_DECISION_TTL_SECONDS,
    V4InvariantError,
    _semantic_digest,
    _sha,
    _validate_exact_snapshot,
)
from .weather_only_maker_paper_accounting_v5 import (
    MAKER_PAPER_ACCOUNTING_V5_VERSION,
    MakerPaperAccountingStoreV5,
)
from .weather_only_maker_shadow import VirtualMakerOrder, create_virtual_maker_order
from .weather_only_paper_commands_all import AllPaperCommandController
from .weather_only_paper_corrective import CorrectiveSettlementEngine, DeliveryUncertain
from .weather_only_paper_post_receipt import (
    PAPER_EXECUTION_PROTOCOL_V5,
    PAPER_POSITION_VERSION_V5,
    PostReceiptWeatherPaperStore,
)
from .weather_only_station_metadata import WeatherStationMetadataError
from .weather_only_structural import binary_pair_underround, complete_bucket_underround


ALL_PAPER_V7_RUNTIME_VERSION = (
    "weather_all_paper_signals_v7_post_receipt_atomic_execution_accounting"
)


class AllPaperWeatherLiveV7Service(AllPaperWeatherLiveV6Service):
    def _defer_maker_restart_terminalization(self) -> bool:
        return False

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)

        old_maker_store = self.maker_store
        old_maker_store.close()
        self.maker_store = MakerPaperAccountingStoreV5(self.db_path)
        if self._defer_maker_restart_terminalization():
            self._v7_maker_orphan_signal_ids = (
                self.maker_store.unactivated_receipt_signal_ids()
            )
            self._v7_maker_orphans_recovered = 0
        else:
            self._v7_maker_orphan_signal_ids = []
            self._v7_maker_orphans_recovered = (
                self.maker_store.reconcile_unactivated_receipts_after_restart()
            )

        self._v7_superseded_settlement = self.settlement
        self._v7_superseded_commands = self.commands
        self.positions = PostReceiptWeatherPaperStore(self.db_path)
        self._v7_recovery = self.positions.reconcile_v5_after_restart()
        self.settlement = CorrectiveSettlementEngine(
            store=self.positions, telegram=self.telegram
        )
        self.commands = AllPaperCommandController(
            telegram=self.telegram,
            store=self.positions,
            maker_store=self.maker_store,
            status_path=self.status_path,
            paper_stake_usd=self.paper_stake_usd,
        )

        # The inherited V4 hourly message reports only its older taker ledger. Suppress
        # that partial-truth summary; operator commands in this wrapper are lane-aware.
        self._v4_last_summary_sent_at = float("inf")

    async def close(self) -> None:
        await asyncio.gather(
            self._v7_superseded_settlement.close(),
            self._v7_superseded_commands.close(),
            return_exceptions=True,
        )
        await super().close()

    async def send_startup(self) -> int:
        release = self.release_sha()
        return await self.telegram.send_html(
            "\n".join(
                [
                    "🟢 <b>WEATHER ALL-PAPER BOT V7 ONLINE</b>",
                    "",
                    "Future-day GEFS PAPER alerts: <b>ON — uncalibrated</b>",
                    "Same-day late-lock PAPER alerts: <b>ON — uncalibrated</b>",
                    "Structural underround PAPER alerts: <b>ON</b>",
                    "Prospective maker PAPER bids: <b>ON when public-WS evidence is certified</b>",
                    "Result-lag: <b>GATED — exact WRH cutoff state is not proven</b>",
                    "",
                    "New taker/structural positions are counted only after Telegram receipt and a fresh exact-CLOB recheck.",
                    "Maker virtual orders activate only through an atomic signal/order audit commit.",
                    "🚫 <b>NO REAL ORDERS / NO WALLET OR SIGNING AUTHORITY</b>",
                    f"Release: <code>{html.escape(release[:12])}</code>",
                ]
            )
        )

    async def _terminalize_delivered_signal(self, signal_id: int, candidate: dict, *, status: str, reason: str) -> None:
        """Authoritative hook for every terminal transition after Telegram receipt."""
        method = getattr(self.positions, "terminalize_delivered_signal", None)
        if callable(method):
            await asyncio.to_thread(
                method, signal_id, terminal_status=status, reason=str(reason),
                decision_id=str(candidate.get("decision_id") or candidate.get("event_id") or signal_id),
                event_id=str(candidate.get("event_id") or ""),
                market_id=str(candidate.get("market_id") or "") or None,
                side=str(candidate.get("side") or "") or None,
            )
        else:
            await asyncio.to_thread(self.positions.set_signal_status, signal_id, status)
            await asyncio.to_thread(
                self.positions.record_decision,
                decision_id=str(candidate.get("decision_id") or candidate.get("event_id") or signal_id),
                event_id=str(candidate.get("event_id") or ""), market_id=str(candidate.get("market_id") or "") or None,
                side=str(candidate.get("side") or "") or None, outcome=status, reason=str(reason),
            )
        sync = getattr(self, "_sync_operator_messages", None)
        if callable(sync):
            result = await sync()
            if result.get("healthy") is not True or list(result.get("errors") or []):
                raise WeatherLivePaperError("DELIVERED_TERMINAL_OPERATOR_SYNC_FAILED")

    async def _mark_v5_not_actionable(
        self, signal_id: int, candidate: dict, reason: str
    ) -> None:
        await self._terminalize_delivered_signal(signal_id, candidate, status="POST_RECEIPT_NOT_ACTIONABLE", reason=str(reason))

    async def _forecast_post_receipt_execution(
        self, candidate: dict, event: dict, *, telegram_sent_at: float
    ) -> dict | None:
        compiled = compile_strict_temperature_event(event)
        metadata = await self._station_local_eligibility(compiled)
        if _semantic_digest(compiled, metadata) != str(candidate.get("semantic_digest") or ""):
            raise V4InvariantError("V5_CONTRACT_CHANGED_AFTER_DELIVERY")
        forecast = await self._mapped_forecast(compiled.event_id, compiled)
        if forecast.source_evidence_sha256 != str(candidate.get("forecast_evidence_sha256") or ""):
            raise V4InvariantError("V5_FORECAST_GENERATION_CHANGED_AFTER_DELIVERY")

        bucket = next(
            (row for row in compiled.buckets if row.market_id == str(candidate.get("market_id") or "")),
            None,
        )
        if bucket is None:
            raise V4InvariantError("V5_MARKET_CHANGED_AFTER_DELIVERY")
        side = str(candidate.get("side") or "").upper()
        token = bucket.yes_token if side == "YES" else bucket.no_token if side == "NO" else None
        if str(token or "") != str(candidate.get("token_id") or ""):
            raise V4InvariantError("V5_TOKEN_MEANING_CHANGED_AFTER_DELIVERY")

        exact = await self.runtime.clob.exact_event_snapshot(compiled)
        _validate_exact_snapshot(compiled, exact)
        if float(exact.started_at) + 1e-9 < float(telegram_sent_at):
            raise V4InvariantError("V5_POST_RECEIPT_RECHECK_NOT_CAUSAL")
        params = exact.parameters.get(bucket.condition_id)
        book = exact.books.get(str(token))
        if params is None or book is None or book.best_ask is None or book.best_ask_size <= 0.0:
            return None
        if params.fee_rate > 0.0 and params.taker_only is not True:
            raise V4InvariantError("V5_DYNAMIC_FEE_SEMANTICS_UNPROVEN")
        ask = float(book.best_ask)
        fee = conservative_taker_fee_per_share(ask, params.fee_rate, params.fee_exponent)
        cost = ask + fee
        if float(candidate["raw_probability"]) - cost < self.forecast_raw_gap_min:
            return None
        tick = float(params.minimum_tick_size)
        if tick <= 0.0 or abs(ask / tick - round(ask / tick)) > 1e-6:
            raise V4InvariantError("V5_ASK_OFF_TICK")
        finished = float(exact.finished_at)
        expires = float(candidate["decision_expires_at"])
        if finished >= expires:
            return None
        return {
            "paper_execution_protocol_version": PAPER_EXECUTION_PROTOCOL_V5,
            "decision_id": str(candidate["decision_id"]),
            "decision_expires_at": expires,
            "post_receipt_recheck_started_at": float(exact.started_at),
            "post_receipt_recheck_finished_at": finished,
            "entry_cost_per_unit": cost,
            "visible_units": float(book.best_ask_size),
            "minimum_order_size": float(params.minimum_order_size),
            "theoretical_payout_per_unit": 1.0,
            "legs": [
                {
                    "market_id": bucket.market_id,
                    "condition_id": bucket.condition_id,
                    "token_id": str(token),
                    "side": side,
                    "ask": ask,
                    "fee": fee,
                    "quote_observed_at": float(book.received_at),
                    "minimum_order_size": float(params.minimum_order_size),
                    "book_hash": str(book.book_hash or ""),
                }
            ],
        }

    async def _save_and_send_forecast(
        self, candidate: dict, event: dict
    ) -> tuple[bool, str | None]:
        try:
            fresh = await self._dispatch_recheck(candidate, event)
        except (
            StrictWeatherContractError,
            V4InvariantError,
            WeatherCLOBError,
            WeatherStationMetadataError,
        ) as exc:
            await self._record_skip(candidate, getattr(exc, "code", type(exc).__name__))
            return False, None
        if fresh is None:
            await self._record_skip(candidate, "FINAL_RECHECK_NO_LONGER_ELIGIBLE")
            return False, None

        pre_send_finished = float(fresh.pop("paper_fill_at"))
        fresh["pre_send_recheck_finished_at"] = pre_send_finished
        fresh["paper_execution_protocol_version"] = PAPER_EXECUTION_PROTOCOL_V5
        fingerprint = _sha(
            {
                "lane": fresh["lane"],
                "event_id": fresh["event_id"],
                "market_id": fresh["market_id"],
                "side": fresh["side"],
                "forecast_evidence_sha256": fresh["forecast_evidence_sha256"],
            }
        )
        signal_id = self.positions.save_signal(
            fingerprint=fingerprint,
            lane=str(fresh["lane"]),
            evidence_class=str(fresh["evidence_class"]),
            event_id=str(fresh["event_id"]),
            market_id=str(fresh["market_id"]),
            side=str(fresh["side"]),
            token_id=str(fresh["token_id"]),
            model_probability=float(fresh["raw_probability"]),
            entry_cost=float(fresh["entry_cost"]),
            raw_gap=float(fresh["raw_gap"]),
            theoretical_payout=1.0,
            created_at=pre_send_finished,
            payload=fresh,
        )
        if signal_id is None:
            return False, None
        await asyncio.to_thread(self.positions.set_signal_status, signal_id, "PENDING_DELIVERY")
        try:
            message_id = await self.telegram.send_html(
                self._forecast_message_v4(fresh),
                url=_event_link(event),
                expires_at=float(fresh["decision_expires_at"]),
            )
        except DeliveryUncertain as exc:
            await asyncio.to_thread(
                self.positions.set_signal_status, signal_id, "DELIVERY_UNCERTAIN"
            )
            return True, exc.code
        except WeatherLivePaperError as exc:
            await asyncio.to_thread(
                self.positions.set_signal_status,
                signal_id,
                "EXPIRED" if "EXPIRED" in exc.code else "DELIVERY_FAILED",
            )
            return True, exc.code

        sent_at = time.time()
        self.positions.mark_telegram_sent(signal_id, int(message_id), sent_at=sent_at)
        if sent_at >= float(fresh["decision_expires_at"]):
            await self._terminalize_delivered_signal(signal_id, fresh if "fresh" in locals() else candidate, status="EXPIRED", reason="DELIVERY_RECEIPT_AFTER_EXPIRY")
            return True, "DELIVERY_RECEIPT_AFTER_EXPIRY"
        await asyncio.to_thread(
            self.positions.set_signal_status, signal_id, "POST_RECEIPT_RECHECK"
        )
        try:
            execution = await self._forecast_post_receipt_execution(
                fresh, event, telegram_sent_at=sent_at
            )
        except (
            StrictWeatherContractError,
            V4InvariantError,
            WeatherCLOBError,
            WeatherStationMetadataError,
        ) as exc:
            code = getattr(exc, "code", type(exc).__name__)
            await self._mark_v5_not_actionable(signal_id, fresh, code)
            return True, code
        if execution is None:
            await self._mark_v5_not_actionable(
                signal_id, fresh, "POST_RECEIPT_FINAL_RECHECK_NO_LONGER_ELIGIBLE"
            )
            return True, None
        try:
            await asyncio.to_thread(
                self.positions.admit_post_receipt_position,
                signal_id,
                self.paper_stake_usd,
                execution,
            )
        except Exception as exc:
            code = getattr(exc, "code", type(exc).__name__)
            await self._terminalize_delivered_signal(signal_id, fresh, status="PAPER_ACCOUNTING_ERROR", reason=f"V5_PAPER_ACCOUNTING:{code}")
            return True, f"V5_PAPER_ACCOUNTING:{code}"
        return True, None

    async def _same_day_exact_recheck(
        self,
        candidate: dict,
        event: dict,
        *,
        after_time: float | None,
    ) -> tuple[dict, object, object, object] | None:
        compiled = compile_strict_temperature_event(event)
        bucket = next(
            (row for row in compiled.buckets if row.market_id == str(candidate.get("market_id") or "")),
            None,
        )
        if bucket is None or str(bucket.yes_token or "") != str(candidate.get("token_id") or ""):
            raise V4InvariantError("V5_SAME_DAY_MARKET_CHANGED")
        exact = await self.runtime.clob.exact_event_snapshot(compiled)
        _validate_exact_snapshot(compiled, exact)
        if after_time is not None and float(exact.started_at) + 1e-9 < float(after_time):
            raise V4InvariantError("V5_POST_RECEIPT_RECHECK_NOT_CAUSAL")
        params = exact.parameters.get(bucket.condition_id)
        book = exact.books.get(str(bucket.yes_token))
        if params is None or book is None or book.best_ask is None or book.best_ask_size <= 0.0:
            return None
        ask = float(book.best_ask)
        if ask < SAME_DAY_MIN_ASK or ask > SAME_DAY_MAX_ASK:
            return None
        fee = conservative_taker_fee_per_share(ask, params.fee_rate, params.fee_exponent)
        cost = ask + fee
        support = float(candidate["raw_probability"])
        if 1.0 - cost < SAME_DAY_MIN_PAYOUT_LEFT or support - cost <= 0.0:
            return None
        tick = float(params.minimum_tick_size)
        if tick <= 0.0 or abs(ask / tick - round(ask / tick)) > 1e-6:
            raise V4InvariantError("V5_SAME_DAY_ASK_OFF_TICK")
        if float(exact.finished_at) >= float(candidate["decision_expires_at"]):
            return None
        fresh = dict(candidate)
        fresh.update(
            {
                "ask": ask,
                "fee": fee,
                "entry_cost": cost,
                "raw_gap": support - cost,
                "payout_left": 1.0 - cost,
                "ask_size": float(book.best_ask_size),
                "quote_observed_at": float(book.received_at),
                "book_hash": str(book.book_hash or ""),
                "minimum_order_size": float(params.minimum_order_size),
                "minimum_tick_size": tick,
            }
        )
        return fresh, exact, bucket, params

    async def _save_and_send_same_day(
        self, candidate: dict, event: dict
    ) -> tuple[bool, str | None]:
        try:
            checked = await self._same_day_exact_recheck(candidate, event, after_time=None)
        except (StrictWeatherContractError, V4InvariantError, WeatherCLOBError) as exc:
            self._all_paper_same_day_skipped += 1
            return False, getattr(exc, "code", type(exc).__name__)
        if checked is None:
            self._all_paper_same_day_skipped += 1
            return False, None
        fresh, exact, _bucket, _params = checked
        fresh["pre_send_recheck_finished_at"] = float(exact.finished_at)
        fresh["paper_execution_protocol_version"] = PAPER_EXECUTION_PROTOCOL_V5

        fingerprint = _sha(
            {
                "lane": SAME_DAY_FRIEND_LANE,
                "event_id": fresh["event_id"],
                "market_id": fresh["market_id"],
                "observed_extreme": fresh["observed_extreme"],
            }
        )
        signal_id = self.positions.save_signal(
            fingerprint=fingerprint,
            lane=SAME_DAY_FRIEND_LANE,
            evidence_class=SAME_DAY_FRIEND_EVIDENCE,
            event_id=str(fresh["event_id"]),
            market_id=str(fresh["market_id"]),
            side="YES",
            token_id=str(fresh["token_id"]),
            model_probability=float(fresh["raw_probability"]),
            entry_cost=float(fresh["entry_cost"]),
            raw_gap=float(fresh["raw_gap"]),
            theoretical_payout=1.0,
            created_at=float(exact.finished_at),
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
            await asyncio.to_thread(
                self.positions.set_signal_status, signal_id, "DELIVERY_UNCERTAIN"
            )
            return True, exc.code
        except WeatherLivePaperError as exc:
            await asyncio.to_thread(
                self.positions.set_signal_status,
                signal_id,
                "EXPIRED" if "EXPIRED" in exc.code else "DELIVERY_FAILED",
            )
            return True, exc.code

        sent_at = time.time()
        self.positions.mark_telegram_sent(signal_id, int(message_id), sent_at=sent_at)
        if sent_at >= float(fresh["decision_expires_at"]):
            await self._terminalize_delivered_signal(signal_id, fresh if "fresh" in locals() else candidate, status="EXPIRED", reason="DELIVERY_RECEIPT_AFTER_EXPIRY")
            return True, "DELIVERY_RECEIPT_AFTER_EXPIRY"
        await asyncio.to_thread(
            self.positions.set_signal_status, signal_id, "POST_RECEIPT_RECHECK"
        )
        try:
            post = await self._same_day_exact_recheck(fresh, event, after_time=sent_at)
        except (StrictWeatherContractError, V4InvariantError, WeatherCLOBError) as exc:
            code = getattr(exc, "code", type(exc).__name__)
            await self._mark_v5_not_actionable(signal_id, fresh, code)
            return True, code
        if post is None:
            await self._mark_v5_not_actionable(
                signal_id, fresh, "POST_RECEIPT_SAME_DAY_NO_LONGER_ELIGIBLE"
            )
            return True, None
        post_fresh, post_exact, bucket, params = post
        book = post_exact.books[str(bucket.yes_token)]
        execution = {
            "paper_execution_protocol_version": PAPER_EXECUTION_PROTOCOL_V5,
            "decision_id": str(fresh["decision_id"]),
            "decision_expires_at": float(fresh["decision_expires_at"]),
            "post_receipt_recheck_started_at": float(post_exact.started_at),
            "post_receipt_recheck_finished_at": float(post_exact.finished_at),
            "entry_cost_per_unit": float(post_fresh["entry_cost"]),
            "visible_units": float(book.best_ask_size),
            "minimum_order_size": float(params.minimum_order_size),
            "theoretical_payout_per_unit": 1.0,
            "legs": [
                {
                    "market_id": bucket.market_id,
                    "condition_id": bucket.condition_id,
                    "token_id": str(bucket.yes_token),
                    "side": "YES",
                    "ask": float(post_fresh["ask"]),
                    "fee": float(post_fresh["fee"]),
                    "quote_observed_at": float(book.received_at),
                    "minimum_order_size": float(params.minimum_order_size),
                    "book_hash": str(book.book_hash or ""),
                }
            ],
        }
        try:
            await asyncio.to_thread(
                self.positions.admit_post_receipt_position,
                signal_id,
                self.paper_stake_usd,
                execution,
            )
        except Exception as exc:
            code = getattr(exc, "code", type(exc).__name__)
            await self._terminalize_delivered_signal(signal_id, fresh, status="PAPER_ACCOUNTING_ERROR", reason=f"V5_SAME_DAY_ACCOUNTING:{code}")
            return True, f"V5_SAME_DAY_ACCOUNTING:{code}"
        self._all_paper_same_day_sent += 1
        return True, None

    async def _structural_exact_recheck(
        self,
        opportunity: dict,
        event: dict | None,
        *,
        after_time: float | None,
        decision_expires_at: float | None,
    ) -> tuple[dict, object, list[dict]] | None:
        if not isinstance(event, dict):
            return None
        compiled = compile_strict_temperature_event(event)
        exact = await self.runtime.clob.exact_event_snapshot(compiled)
        _validate_exact_snapshot(compiled, exact)
        if after_time is not None and float(exact.started_at) + 1e-9 < float(after_time):
            raise V4InvariantError("V5_POST_RECEIPT_RECHECK_NOT_CAUSAL")
        if decision_expires_at is not None and float(exact.finished_at) >= float(decision_expires_at):
            return None

        lane = str(opportunity.get("lane") or "")
        if lane == "weather_binary_pair_underround":
            rows = binary_pair_underround(compiled, exact.books, exact.parameters)
        elif lane == "weather_complete_bucket_underround":
            candidate = complete_bucket_underround(compiled, exact.books, exact.parameters)
            rows = [] if candidate is None else [candidate]
        else:
            return None
        wanted = tuple(str(value) for value in (opportunity.get("token_ids") or ()))
        value = None
        for row in rows:
            raw = row.as_dict()
            if tuple(str(token) for token in raw.get("token_ids") or ()) == wanted:
                value = raw
                break
        if value is None or value.get("contract_partition_proven") is not True:
            return None

        legs: list[dict] = []
        for market_id, token_id in zip(value["market_ids"], value["token_ids"]):
            bucket = next(
                (item for item in compiled.buckets if item.market_id == str(market_id)),
                None,
            )
            if bucket is None:
                raise V4InvariantError("V5_STRUCTURAL_BUCKET_IDENTITY_MISSING")
            if str(token_id) == str(bucket.yes_token):
                side = "YES"
            elif str(token_id) == str(bucket.no_token):
                side = "NO"
            else:
                raise V4InvariantError("V5_STRUCTURAL_TOKEN_MEANING_MISMATCH")
            params = exact.parameters.get(bucket.condition_id)
            book = exact.books.get(str(token_id))
            if params is None or book is None or book.best_ask is None or book.best_ask_size <= 0.0:
                return None
            ask = float(book.best_ask)
            fee = conservative_taker_fee_per_share(
                ask, params.fee_rate, params.fee_exponent
            )
            legs.append(
                {
                    "market_id": str(market_id),
                    "condition_id": bucket.condition_id,
                    "token_id": str(token_id),
                    "side": side,
                    "ask": ask,
                    "fee": fee,
                    "quote_observed_at": float(book.received_at),
                    "minimum_order_size": float(params.minimum_order_size),
                    "book_hash": str(book.book_hash or ""),
                }
            )
        return value, exact, legs

    def _structural_paper_message(self, fresh: dict, event: dict | None) -> str:
        asks = [float(value) for value in fresh.get("ask_prices") or ()]
        fees = [float(value) for value in fresh.get("conservative_fees_per_share") or ()]
        lines = [
            f"{idx}. ask ${ask:.4f} + fee ${fees[idx - 1]:.5f}"
            for idx, ask in enumerate(asks, 1)
        ]
        lane = str(fresh.get("lane") or "")
        if lane == "weather_binary_pair_underround":
            semantic = "Same-condition YES+NO complete set: final binary token payouts sum to $1."
        else:
            semantic = (
                "Complete-bucket set: $1 total payout is conditional on the certified "
                "contract/rule exactly-one resolution semantics remaining valid."
            )
        return "\n".join(
            [
                "🧪 <b>PAPER STRUCTURAL WEATHER SIGNAL</b>",
                f"<b>{html.escape(_event_title(event, str(fresh.get('event_id') or '')))}</b>",
                f"Lane: <code>{html.escape(lane)}</code>",
                "",
                *lines,
                "",
                f"Pre-send exact total cost/set: <b>${float(fresh['gross_cost_per_set']):.4f}</b>",
                f"Reference payout/set: <b>${float(fresh['locked_payout_per_set']):.4f}</b>",
                f"Reference spread/set: <b>${float(fresh['locked_profit_per_set']):.4f}</b>",
                f"Common visible capacity: <b>{float(fresh['common_best_ask_shares']):.2f} sets</b>",
                "",
                html.escape(semantic),
                "A fresh exact-CLOB basket recheck after Telegram receipt is required before this counts in PAPER P&amp;L.",
                "🚫 No real order was placed.",
            ]
        )

    async def _save_and_send_structural(
        self, opportunity: dict, event: dict | None
    ) -> tuple[bool, str | None]:
        try:
            checked = await self._structural_exact_recheck(
                opportunity, event, after_time=None, decision_expires_at=None
            )
        except (StrictWeatherContractError, V4InvariantError, WeatherCLOBError) as exc:
            self._all_paper_structural_skipped += 1
            return False, getattr(exc, "code", type(exc).__name__)
        if checked is None:
            self._all_paper_structural_skipped += 1
            return False, None
        fresh, exact, _pre_legs = checked
        event_id = str(fresh.get("event_id") or "")
        expires_at = float(exact.finished_at) + QUOTE_DECISION_TTL_SECONDS
        decision_id = _sha(
            {
                "lane": fresh["lane"],
                "event_id": event_id,
                "token_ids": list(fresh["token_ids"]),
                "book_hashes": [str(exact.books[str(token)].book_hash or "") for token in fresh["token_ids"]],
                "pre_send_exact_finished_at": float(exact.finished_at),
            }
        )
        fresh = dict(fresh)
        fresh.update(
            {
                "paper_mode": True,
                "event_title": _event_title(event, event_id),
                "event_url": _event_link(event),
                "decision_id": decision_id,
                "decision_expires_at": expires_at,
                "pre_send_recheck_finished_at": float(exact.finished_at),
                "paper_execution_protocol_version": PAPER_EXECUTION_PROTOCOL_V5,
                "paper_target_stake_usd": float(self.paper_stake_usd),
                "financial_authority": False,
                "automatic_order_placement": False,
            }
        )
        fp = _fingerprint(
            {
                "lane": fresh["lane"],
                "event_id": event_id,
                "tokens": list(fresh["token_ids"]),
            },
            bucket_seconds=STRUCTURAL_FINGERPRINT_SECONDS,
            at=float(exact.finished_at),
        )
        signal_id = self.positions.save_signal(
            fingerprint=fp,
            lane=str(fresh["lane"]),
            evidence_class=str(fresh.get("evidence_class") or "DETERMINISTIC_EXACT_CLOB_PAPER"),
            event_id=event_id,
            payload=fresh,
            entry_cost=float(fresh["gross_cost_per_set"]),
            raw_gap=float(fresh["locked_profit_per_set"]),
            theoretical_payout=float(fresh["locked_payout_per_set"]),
            created_at=float(exact.finished_at),
        )
        if signal_id is None:
            return False, None
        await asyncio.to_thread(self.positions.set_signal_status, signal_id, "PENDING_DELIVERY")
        try:
            message_id = await self.telegram.send_html(
                self._structural_paper_message(fresh, event),
                url=_event_link(event),
                expires_at=expires_at,
            )
        except DeliveryUncertain as exc:
            await asyncio.to_thread(
                self.positions.set_signal_status, signal_id, "DELIVERY_UNCERTAIN"
            )
            return True, exc.code
        except WeatherLivePaperError as exc:
            await asyncio.to_thread(
                self.positions.set_signal_status,
                signal_id,
                "EXPIRED" if "EXPIRED" in exc.code else "DELIVERY_FAILED",
            )
            return True, exc.code

        sent_at = time.time()
        self.positions.mark_telegram_sent(signal_id, int(message_id), sent_at=sent_at)
        if sent_at >= expires_at:
            await self._terminalize_delivered_signal(signal_id, fresh if "fresh" in locals() else candidate, status="EXPIRED", reason="DELIVERY_RECEIPT_AFTER_EXPIRY")
            return True, "DELIVERY_RECEIPT_AFTER_EXPIRY"
        await asyncio.to_thread(
            self.positions.set_signal_status, signal_id, "POST_RECEIPT_RECHECK"
        )
        try:
            post = await self._structural_exact_recheck(
                fresh, event, after_time=sent_at, decision_expires_at=expires_at
            )
        except (StrictWeatherContractError, V4InvariantError, WeatherCLOBError) as exc:
            code = getattr(exc, "code", type(exc).__name__)
            await self._mark_v5_not_actionable(signal_id, fresh, code)
            return True, code
        if post is None:
            await self._mark_v5_not_actionable(
                signal_id, fresh, "POST_RECEIPT_STRUCTURAL_NO_LONGER_ELIGIBLE"
            )
            return True, None
        post_fresh, post_exact, post_legs = post
        execution = {
            "paper_execution_protocol_version": PAPER_EXECUTION_PROTOCOL_V5,
            "decision_id": decision_id,
            "decision_expires_at": expires_at,
            "post_receipt_recheck_started_at": float(post_exact.started_at),
            "post_receipt_recheck_finished_at": float(post_exact.finished_at),
            "entry_cost_per_unit": float(post_fresh["gross_cost_per_set"]),
            "visible_units": float(post_fresh["common_best_ask_shares"]),
            "minimum_order_size": max(float(leg["minimum_order_size"]) for leg in post_legs),
            "theoretical_payout_per_unit": float(post_fresh["locked_payout_per_set"]),
            "legs": post_legs,
        }
        try:
            await asyncio.to_thread(
                self.positions.admit_post_receipt_position,
                signal_id,
                self.paper_stake_usd,
                execution,
            )
        except Exception as exc:
            code = getattr(exc, "code", type(exc).__name__)
            await self._terminalize_delivered_signal(signal_id, fresh, status="PAPER_ACCOUNTING_ERROR", reason=f"V5_STRUCTURAL_ACCOUNTING:{code}")
            return True, f"V5_STRUCTURAL_ACCOUNTING:{code}"
        self._all_paper_structural_sent += 1
        return True, None

    async def _activate_maker_after_delivery(
        self,
        *,
        payload: dict,
        event: dict,
        signal_id: int,
        telegram_message_id: int,
        telegram_sent_at: float,
    ) -> VirtualMakerOrder:
        if time.time() >= float(payload["decision_expires_at"]):
            raise AllPaperV3Error("MAKER_SIGNAL_EXPIRED_BEFORE_ACTIVATION")
        if not self.maker_stream.connected:
            raise AllPaperV3Error("MAKER_STREAM_NOT_CONNECTED_AFTER_DELIVERY")
        coverage = self.maker_stream.coverage(str(payload["token_id"]))
        if coverage is None:
            raise AllPaperV3Error("MAKER_STREAM_COVERAGE_MISSING_AFTER_DELIVERY")
        if coverage.generation != int(payload["ws_generation_at_signal"]):
            raise AllPaperV3Error("MAKER_STREAM_GENERATION_CHANGED_DURING_DELIVERY")
        if coverage.started_at > float(telegram_sent_at) + 1e-9:
            raise AllPaperV3Error("MAKER_STREAM_COVERAGE_STARTED_AFTER_DELIVERY")

        compiled, forecast, fair, proposal, book, params, exact = (
            await self._maker_rebuild_same_proposal(payload, event)
        )
        if float(exact.started_at) + 1e-9 < float(telegram_sent_at):
            raise AllPaperV3Error("MAKER_POST_DELIVERY_RECHECK_NOT_CAUSAL")
        current_coverage = self.maker_stream.coverage(str(payload["token_id"]))
        if (
            current_coverage is None
            or current_coverage.generation != coverage.generation
            or current_coverage.started_at > float(exact.finished_at) + 1e-9
        ):
            raise AllPaperV3Error("MAKER_STREAM_COVERAGE_LOST_DURING_RECHECK")

        source_generation = (
            f"{forecast.source_evidence_sha256}|ws_generation={current_coverage.generation}"
        )
        order = create_virtual_maker_order(
            order_id=str(payload["order_id"]),
            proposal=proposal,
            fair=fair,
            book=book,
            parameters=params,
            policy=self.maker_shadow_policy,
            created_at=float(exact.finished_at),
            contract_evidence_sha256=str(payload["contract_sha256"]),
            source_generation=source_generation,
        )
        saved = await asyncio.to_thread(
            self.maker_store.activate_after_telegram,
            order,
            signal_id=int(signal_id),
            telegram_message_id=int(telegram_message_id),
            telegram_sent_at=float(telegram_sent_at),
            post_delivery_exact_finished_at=float(exact.finished_at),
            recorded_at=float(exact.finished_at),
        )
        self._maker_orders_activated += 1
        return saved

    async def run_cycle(self) -> dict:
        status = dict(await super().run_cycle())
        status.update(
            {
                "all_paper_v7_runtime_version": ALL_PAPER_V7_RUNTIME_VERSION,
                "paper_execution_protocol_version": PAPER_EXECUTION_PROTOCOL_V5,
                "paper_position_version": PAPER_POSITION_VERSION_V5,
                "maker_paper_accounting_version": MAKER_PAPER_ACCOUNTING_V5_VERSION,
                "post_receipt_execution_required": True,
                "post_receipt_exact_clob_required": True,
                "v5_restart_recovery": self._v7_recovery,
                "maker_unactivated_receipts_recovered": self._v7_maker_orphans_recovered,
                "legacy_partial_hourly_summary_suppressed": True,
                "result_lag_paper_delivery_enabled": False,
                "result_lag_block_reason": "EXACT_WRH_CUTOFF_STATE_NOT_PROVEN",
                "financial_delivery": False,
                "financial_authority": False,
                "automatic_order_placement": False,
                "wallet_or_order_api_loaded": False,
            }
        )
        _atomic_json(self.status_path, status)
        return status


async def _main(args) -> int:
    service = AllPaperWeatherLiveV7Service(
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
            return 0 if status.get("cycle_ok") and status.get("maker_healthy") else 2
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
    parser.add_argument(
        "--forecast-cache-seconds", type=float, default=DEFAULT_FORECAST_CACHE_SECONDS
    )
    parser.add_argument(
        "--forecast-raw-gap-min", type=float, default=DEFAULT_FORECAST_RAW_GAP_MIN
    )
    parser.add_argument(
        "--max-forecast-events", type=int, default=DEFAULT_MAX_FORECAST_EVENTS
    )
    parser.add_argument(
        "--paper-stake-usd", type=float, default=DEFAULT_PAPER_STAKE_USD
    )
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    raise SystemExit(asyncio.run(_main(args)))


if __name__ == "__main__":
    main()
