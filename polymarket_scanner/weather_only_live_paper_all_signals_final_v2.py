from __future__ import annotations

"""Second independently-corrected final all-weather PAPER entrypoint.

This additive layer fixes every execution/research-integrity issue found in the second
full adversarial review while preserving the existing strict contract/source stack.
It adds no authenticated order, wallet, signing, cancellation or financial authority.

Key policy changes are intentionally conservative:
* same-day dynamic-fee semantics fail closed;
* same-day and source-shock theses refetch exact WRH after Telegram receipt;
* every admitted directional V5 leg carries independently recomputable visible size;
* structural baskets remain Telegram-visible but are theoretical-only for P&L until
  atomic/legging execution can be proven;
* maker proposals remain visible, but simulated maker fills are disabled because a
  trade-only public stream cannot certify same-price queue position; and
* any historical maker settlement requires resolved/settled UMA finality.
"""

import argparse
import asyncio
import html
import json
import math
import time
from pathlib import Path

from .weather_only_clob import WeatherCLOBError
from .weather_only_conditioned_wrh import build_wrh_observed_extreme_asof
from .weather_only_contract_strict import StrictWeatherContractError, compile_strict_temperature_event
from .weather_only_contracts import DAILY_HIGH, DAILY_LOW
from .weather_only_independent_review_corrective import (
    IndependentReviewAllPaperCommandController,
)
from .weather_only_independent_review_corrective_v2 import (
    INDEPENDENT_REVIEW_CORRECTIVE_V2_VERSION,
    IndependentReviewPostReceiptStoreV2,
)
from .weather_only_live_paper import (
    DEFAULT_FORECAST_CACHE_SECONDS,
    DEFAULT_FORECAST_RAW_GAP_MIN,
    DEFAULT_INTERVAL_SECONDS,
    DEFAULT_MAX_FORECAST_EVENTS,
    WeatherLivePaperError,
    _atomic_json,
    _event_link,
)
from .weather_only_live_paper_all_signals import (
    SAME_DAY_FRIEND_EVIDENCE,
    SAME_DAY_FRIEND_LANE,
    SAME_DAY_MAX_OBSERVATION_AGE_SECONDS,
    SAME_DAY_MIN_DROP_C,
    SAME_DAY_MIN_DROP_F,
    SAME_DAY_MIN_TREND_STEPS,
    _inside_bucket,
    _trend_steps,
)
from .weather_only_live_paper_all_signals_final import (
    FinalAllPaperWeatherLiveService,
)
from .weather_only_live_paper_all_signals_v3 import (
    MAKER_SETTLEMENT_NOTIFY_SENT,
    MAKER_SETTLEMENT_NOTIFY_UNCERTAIN,
)
from .weather_only_live_paper_all_signals_v8 import (
    SOURCE_SHOCK_EVIDENCE,
    SOURCE_SHOCK_LANE,
    _excluded,
)
from .weather_only_live_paper_v2 import DEFAULT_PAPER_STAKE_USD
from .weather_only_live_paper_v4 import V4InvariantError
from .weather_only_maker_paper_accounting import MAKER_SETTLEMENT_EVENT
from .weather_only_maker_shadow import PARTIALLY_SIMULATED, RESTING
from .weather_only_paper_corrective import (
    CorrectiveSettlementEngine,
    DeliveryUncertain,
    final_token_payout_v4,
)
from .weather_only_paper_post_receipt import PAPER_EXECUTION_PROTOCOL_V5


FINAL_ALL_PAPER_RUNTIME_V2_VERSION = (
    "weather_all_paper_final_v5_second_adversarial_corrective_all_findings"
)
_EPS = 1e-9


class FinalAllPaperV2Error(RuntimeError):
    def __init__(self, code: str):
        self.code = str(code)
        super().__init__(self.code)


def _finite(value: object, code: str) -> float:
    if value is None or isinstance(value, bool):
        raise FinalAllPaperV2Error(code)
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        raise FinalAllPaperV2Error(code) from None
    if not math.isfinite(number):
        raise FinalAllPaperV2Error(code)
    return number


class FinalAllPaperWeatherLiveServiceV2(FinalAllPaperWeatherLiveService):
    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        # Replace only the V5 persistence facade with the serialized/capacity-derived
        # version. Keep the independently reviewed maker store and all source clients.
        self._v2_superseded_settlement = self.settlement
        self._v2_superseded_commands = self.commands
        self.positions = IndependentReviewPostReceiptStoreV2(self.db_path)
        self._v2_recovery = self.positions.reconcile_v5_after_restart()
        self.settlement = CorrectiveSettlementEngine(
            store=self.positions, telegram=self.telegram
        )
        self.commands = IndependentReviewAllPaperCommandController(
            telegram=self.telegram,
            store=self.positions,
            maker_store=self.maker_store,
            status_path=self.status_path,
            paper_stake_usd=self.paper_stake_usd,
        )

    async def close(self) -> None:
        await asyncio.gather(
            self.settlement.close(),
            self.commands.close(),
            self._v2_superseded_settlement.close(),
            self._v2_superseded_commands.close(),
            return_exceptions=True,
        )
        await super().close()

    async def send_startup(self) -> int:
        release = self.release_sha()
        return await self.telegram.send_html(
            "\n".join(
                [
                    "🟢 <b>WEATHER ALL-PAPER BOT ONLINE — SECOND ADVERSARIAL CORRECTIVE</b>",
                    "",
                    "Future-day GEFS-gap PAPER alerts: <b>ON — uncalibrated / upstream run age unknown</b>",
                    "Same-day late-lock PAPER alerts: <b>ON — uncalibrated + post-receipt WRH revalidation</b>",
                    "Official-extreme source-shock PAPER alerts: <b>ON — revision-sensitive + post-receipt WRH revalidation</b>",
                    "Structural underround alerts: <b>ON — theoretical only, excluded from validated P&amp;L</b>",
                    "Maker bid proposals: <b>ON — uncalibrated</b>",
                    "Maker simulated fills/P&amp;L: <b>OFF — public queue position is not certified</b>",
                    "Result-lag: <b>GATED — exact WRH cutoff state not proven</b>",
                    "",
                    "Directional PAPER positions require a fresh exact-CLOB recheck after Telegram receipt.",
                    "V5 accounting recomputes exact leg cost, timing, identity and per-leg capacity under a serialized admission lock.",
                    "🚫 <b>NO REAL ORDERS / NO WALLET / NO SIGNING AUTHORITY</b>",
                    f"Release: <code>{html.escape(release[:12])}</code>",
                ]
            )
        )

    async def _fresh_wrh_state(self, compiled, *, after_time: float):
        result = await asyncio.to_thread(
            self._same_day_wrh.fetch_snapshot,
            station=str(compiled.station_hint or "").upper(),
            target_date=compiled.target_date,
        )
        fetched = float(result.fetched_at)
        if fetched + _EPS < float(after_time):
            raise V4InvariantError("V5_POST_RECEIPT_WRH_NOT_CAUSAL")
        evidence = build_wrh_observed_extreme_asof(
            result.snapshot,
            compiled,
            as_of=fetched,
        )
        return result, evidence

    @staticmethod
    def _fresh_wrh_rows(result) -> list[tuple[float, float]]:
        rows: list[tuple[float, float]] = []
        cutoff = float(result.fetched_at)
        for row in result.snapshot.target_rows:
            if row.displayed_temp_f is None:
                continue
            observed_at = float(row.observation_time_local.timestamp())
            value = float(row.displayed_temp_f)
            if observed_at <= cutoff + _EPS:
                rows.append((observed_at, value))
        rows.sort()
        return rows

    async def _forecast_post_receipt_execution(
        self, candidate: dict, event: dict, *, telegram_sent_at: float
    ) -> dict | None:
        execution = await super()._forecast_post_receipt_execution(
            candidate, event, telegram_sent_at=telegram_sent_at
        )
        if execution is None:
            return None
        if len(execution.get("legs") or []) != 1:
            raise V4InvariantError("V5_FORECAST_LEG_COUNT_INVALID")
        upgraded = dict(execution)
        upgraded["legs"] = [dict(execution["legs"][0])]
        upgraded["legs"][0]["visible_units"] = float(execution["visible_units"])
        return upgraded

    async def _same_day_exact_recheck(
        self,
        candidate: dict,
        event: dict,
        *,
        after_time: float | None,
    ):
        checked = await super()._same_day_exact_recheck(
            candidate, event, after_time=after_time
        )
        if checked is None:
            return None
        fresh, exact, bucket, params = checked
        if params.fee_rate > 0.0 and params.taker_only is not True:
            raise V4InvariantError("V5_SAME_DAY_DYNAMIC_FEE_SEMANTICS_UNPROVEN")
        if after_time is not None:
            compiled = compile_strict_temperature_event(event)
            result, evidence = await self._fresh_wrh_state(
                compiled, after_time=float(after_time)
            )
            rows = self._fresh_wrh_rows(result)
            if len(rows) < SAME_DAY_MIN_TREND_STEPS + 1:
                raise V4InvariantError("V5_SAME_DAY_WRH_THESIS_CHANGED")
            values = [value for _, value in rows]
            latest_at, current = rows[-1]
            extreme = float(evidence.observed_state.extreme_value)
            trend = _trend_steps(values, compiled.family)
            drop = extreme - current if compiled.family == DAILY_HIGH else current - extreme
            required_drop = SAME_DAY_MIN_DROP_C if compiled.unit == "C" else SAME_DAY_MIN_DROP_F
            if (
                not _inside_bucket(extreme, bucket)
                or trend < SAME_DAY_MIN_TREND_STEPS
                or float(result.fetched_at) - latest_at > SAME_DAY_MAX_OBSERVATION_AGE_SECONDS
                or drop + _EPS < required_drop
            ):
                raise V4InvariantError("V5_SAME_DAY_WRH_THESIS_CHANGED")
            refreshed = dict(fresh)
            refreshed.update(
                {
                    "post_receipt_wrh_revalidated": True,
                    "post_receipt_wrh_received_at": float(result.fetched_at),
                    "post_receipt_wrh_transport_sha256": str(result.transport_evidence_sha256),
                    "post_receipt_wrh_observed_sha256": str(evidence.evidence_sha256),
                    "post_receipt_official_extreme": extreme,
                    "post_receipt_official_current": current,
                    "post_receipt_official_trend_steps": trend,
                }
            )
            fresh = refreshed
        return fresh, exact, bucket, params

    async def _save_and_send_same_day(
        self, candidate: dict, event: dict
    ) -> tuple[bool, str | None]:
        # Keep V7's delivery ordering, but persist the post-receipt WRH evidence and
        # per-leg capacity inside the V5 execution object admitted by the v2 store.
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
        from .weather_only_live_paper_v4 import _sha
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
            await asyncio.to_thread(self.positions.set_signal_status, signal_id, "DELIVERY_UNCERTAIN")
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
            await asyncio.to_thread(self.positions.set_signal_status, signal_id, "EXPIRED")
            return True, "DELIVERY_RECEIPT_AFTER_EXPIRY"
        await asyncio.to_thread(self.positions.set_signal_status, signal_id, "POST_RECEIPT_RECHECK")
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
            "post_receipt_weather_evidence": {
                key: post_fresh[key]
                for key in (
                    "post_receipt_wrh_revalidated",
                    "post_receipt_wrh_received_at",
                    "post_receipt_wrh_transport_sha256",
                    "post_receipt_wrh_observed_sha256",
                    "post_receipt_official_extreme",
                    "post_receipt_official_current",
                    "post_receipt_official_trend_steps",
                )
                if key in post_fresh
            },
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
                    "visible_units": float(book.best_ask_size),
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
            await asyncio.to_thread(self.positions.set_signal_status, signal_id, "PAPER_ACCOUNTING_ERROR")
            return True, f"V5_SAME_DAY_ACCOUNTING:{code}"
        self._all_paper_same_day_sent += 1
        return True, None

    async def _structural_exact_recheck(self, *args, **kwargs):
        checked = await super()._structural_exact_recheck(*args, **kwargs)
        if checked is None:
            return None
        fresh, exact, legs = checked
        upgraded: list[dict] = []
        for leg in legs:
            row = dict(leg)
            book = exact.books.get(str(row["token_id"]))
            if book is None or book.best_ask_size <= 0.0:
                raise V4InvariantError("V5_STRUCTURAL_VISIBLE_CAPACITY_MISSING")
            row["visible_units"] = float(book.best_ask_size)
            upgraded.append(row)
        return fresh, exact, upgraded

    async def _source_shock_exact_recheck(
        self,
        candidate: dict,
        event: dict,
        *,
        after_time: float | None,
        decision_expires_at: float | None,
    ):
        checked = await super()._source_shock_exact_recheck(
            candidate,
            event,
            after_time=after_time,
            decision_expires_at=decision_expires_at,
        )
        if checked is None:
            return None
        fresh, exact, bucket, params = checked
        if after_time is not None:
            compiled = compile_strict_temperature_event(event)
            result, evidence = await self._fresh_wrh_state(
                compiled, after_time=float(after_time)
            )
            rows = self._fresh_wrh_rows(result)
            if len(rows) < 2:
                raise V4InvariantError("V5_SOURCE_SHOCK_WRH_THESIS_CHANGED")
            latest_at, _latest_value = rows[-1]
            previous_values = [value for _, value in rows[:-1]]
            previous = max(previous_values) if compiled.family == DAILY_HIGH else min(previous_values)
            current = float(evidence.observed_state.extreme_value)
            if (
                abs(previous - float(candidate["previous_official_extreme"])) > _EPS
                or abs(current - float(candidate["new_official_extreme"])) > _EPS
                or abs(latest_at - float(candidate["latest_official_observed_at"])) > 1.0
                or not _excluded(bucket, current, compiled.family)
            ):
                raise V4InvariantError("V5_SOURCE_SHOCK_WRH_THESIS_CHANGED")
            refreshed = dict(fresh)
            refreshed.update(
                {
                    "post_receipt_wrh_revalidated": True,
                    "post_receipt_wrh_received_at": float(result.fetched_at),
                    "post_receipt_wrh_transport_sha256": str(result.transport_evidence_sha256),
                    "post_receipt_wrh_observed_sha256": str(evidence.evidence_sha256),
                    "post_receipt_official_extreme": current,
                }
            )
            fresh = refreshed
        return fresh, exact, bucket, params

    async def _save_and_send_source_shock(
        self, candidate: dict, event: dict
    ) -> tuple[bool, str | None]:
        # Reuse V8 through Telegram delivery would lose per-leg capacity and refreshed
        # WRH evidence, so the narrow V8 ordering is repeated here with those fields.
        try:
            checked = await self._source_shock_exact_recheck(
                candidate, event, after_time=None, decision_expires_at=None
            )
        except (StrictWeatherContractError, V4InvariantError, WeatherCLOBError) as exc:
            self._source_shock_skipped += 1
            return False, getattr(exc, "code", type(exc).__name__)
        if checked is None:
            self._source_shock_skipped += 1
            return False, None
        fresh, exact, _bucket, _params = checked
        expires_at = float(exact.finished_at) + 20.0
        # Preserve V8's already tighter local-midnight expiry when it supplied one via
        # an inherited decision candidate; never extend an existing bound.
        inherited_expiry = fresh.get("decision_expires_at")
        if inherited_expiry is not None:
            expires_at = min(expires_at, float(inherited_expiry))
        if time.time() >= expires_at:
            self._source_shock_skipped += 1
            return False, None
        from .weather_only_live_paper_v4 import _sha
        decision_id = _sha(
            {
                "lane": SOURCE_SHOCK_LANE,
                "event_id": fresh["event_id"],
                "market_id": fresh["market_id"],
                "observed_extreme": fresh["new_official_extreme"],
                "capture_sha256": fresh["capture_sha256"],
                "book_hash": fresh["book_hash"],
            }
        )
        fresh.update(
            {
                "decision_id": decision_id,
                "decision_expires_at": expires_at,
                "pre_send_recheck_finished_at": float(exact.finished_at),
                "paper_execution_protocol_version": PAPER_EXECUTION_PROTOCOL_V5,
                "paper_target_stake_usd": float(self.paper_stake_usd),
            }
        )
        fingerprint = _sha(
            {
                "lane": SOURCE_SHOCK_LANE,
                "event_id": fresh["event_id"],
                "market_id": fresh["market_id"],
                "side": "NO",
            }
        )
        signal_id = self.positions.save_signal(
            fingerprint=fingerprint,
            lane=SOURCE_SHOCK_LANE,
            evidence_class=SOURCE_SHOCK_EVIDENCE,
            event_id=str(fresh["event_id"]),
            market_id=str(fresh["market_id"]),
            side="NO",
            token_id=str(fresh["token_id"]),
            model_probability=None,
            entry_cost=float(fresh["entry_cost"]),
            raw_gap=None,
            theoretical_payout=1.0,
            created_at=float(exact.finished_at),
            payload=fresh,
        )
        if signal_id is None:
            return False, None
        await asyncio.to_thread(self.positions.set_signal_status, signal_id, "PENDING_DELIVERY")
        try:
            message_id = await self.telegram.send_html(
                self._source_shock_message(fresh),
                url=_event_link(event),
                expires_at=expires_at,
            )
        except DeliveryUncertain as exc:
            await asyncio.to_thread(self.positions.set_signal_status, signal_id, "DELIVERY_UNCERTAIN")
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
            await asyncio.to_thread(self.positions.set_signal_status, signal_id, "EXPIRED")
            return True, "DELIVERY_RECEIPT_AFTER_EXPIRY"
        await asyncio.to_thread(self.positions.set_signal_status, signal_id, "POST_RECEIPT_RECHECK")
        try:
            post = await self._source_shock_exact_recheck(
                fresh, event, after_time=sent_at, decision_expires_at=expires_at
            )
        except (StrictWeatherContractError, V4InvariantError, WeatherCLOBError) as exc:
            code = getattr(exc, "code", type(exc).__name__)
            await self._mark_v5_not_actionable(signal_id, fresh, code)
            return True, code
        if post is None:
            await self._mark_v5_not_actionable(
                signal_id, fresh, "POST_RECEIPT_SOURCE_SHOCK_NO_LONGER_EXECUTABLE"
            )
            return True, None
        post_fresh, post_exact, bucket, params = post
        book = post_exact.books[str(bucket.no_token)]
        execution = {
            "paper_execution_protocol_version": PAPER_EXECUTION_PROTOCOL_V5,
            "decision_id": decision_id,
            "decision_expires_at": expires_at,
            "post_receipt_recheck_started_at": float(post_exact.started_at),
            "post_receipt_recheck_finished_at": float(post_exact.finished_at),
            "entry_cost_per_unit": float(post_fresh["entry_cost"]),
            "visible_units": float(book.best_ask_size),
            "minimum_order_size": float(params.minimum_order_size),
            "theoretical_payout_per_unit": 1.0,
            "post_receipt_weather_evidence": {
                key: post_fresh[key]
                for key in (
                    "post_receipt_wrh_revalidated",
                    "post_receipt_wrh_received_at",
                    "post_receipt_wrh_transport_sha256",
                    "post_receipt_wrh_observed_sha256",
                    "post_receipt_official_extreme",
                )
                if key in post_fresh
            },
            "legs": [
                {
                    "market_id": bucket.market_id,
                    "condition_id": bucket.condition_id,
                    "token_id": str(bucket.no_token),
                    "side": "NO",
                    "ask": float(post_fresh["ask"]),
                    "fee": float(post_fresh["fee"]),
                    "quote_observed_at": float(book.received_at),
                    "minimum_order_size": float(params.minimum_order_size),
                    "visible_units": float(book.best_ask_size),
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
            await asyncio.to_thread(self.positions.set_signal_status, signal_id, "PAPER_ACCOUNTING_ERROR")
            return True, f"SOURCE_SHOCK_ACCOUNTING:{code}"
        self._source_shock_sent += 1
        return True, None

    async def _progress_maker_orders(self, by_id: dict[str, dict]) -> list[str]:
        """Fail closed: trade-only WS cannot certify queue additions ahead of our bid."""
        errors: list[str] = []
        active = await asyncio.to_thread(self.maker_store.active_orders)
        for order in active:
            if order.status not in {RESTING, PARTIALLY_SIMULATED}:
                continue
            try:
                await asyncio.to_thread(
                    self._cancel_maker_order,
                    order,
                    "QUEUE_POSITION_UNCERTIFIED_TRADE_ONLY_STREAM",
                )
            except Exception as exc:
                errors.append(
                    f"MAKER_QUEUE_QUARANTINE:{order.order_id}:"
                    f"{getattr(exc, 'code', type(exc).__name__)}"
                )
        return errors

    async def _settle_maker_orders(self) -> list[str]:
        """Settle historical simulated fills only after strict resolved/settled finality."""
        errors: list[str] = []
        orders = await asyncio.to_thread(self.maker_store.unsettled_filled_orders)
        semaphore = asyncio.Semaphore(4)

        async def fetch(order):
            async with semaphore:
                return order, await self._maker_gamma.market_by_id(order.market_id)

        results = await asyncio.gather(*(fetch(order) for order in orders), return_exceptions=True)
        for result in results:
            if isinstance(result, Exception):
                errors.append(f"MAKER_SETTLEMENT_FETCH:{type(result).__name__}")
                continue
            order, market = result
            if not isinstance(market, dict):
                continue
            try:
                payout = final_token_payout_v4(
                    order.token_id,
                    market,
                    expected_condition_id=order.condition_id,
                    expected_side=str(order.outcome).upper(),
                )
            except Exception as exc:
                errors.append(
                    f"MAKER_SETTLEMENT_FINALITY:{order.order_id}:"
                    f"{getattr(exc, 'code', type(exc).__name__)}"
                )
                continue
            if payout is None:
                continue
            evidence = {
                "source": "GAMMA_RESOLVED_SETTLED_STRICT_TOKEN_PAYOUT_V4",
                "market_id": order.market_id,
                "condition_id": order.condition_id,
                "token_id": order.token_id,
                "payout": float(payout),
                "checked_at": time.time(),
                "uma_finality_required": True,
                "financial_authority": False,
            }
            try:
                await asyncio.to_thread(
                    self.maker_store.record_settlement,
                    order,
                    payout_per_share=float(payout),
                    evidence=evidence,
                )
                self._maker_settlements_recorded += 1
            except Exception as exc:
                errors.append(
                    f"MAKER_SETTLEMENT:{order.order_id}:"
                    f"{getattr(exc, 'code', type(exc).__name__)}"
                )

        pending = await asyncio.to_thread(self._pending_maker_settlement_notifications)
        for order_id, payload in pending:
            try:
                message_id = await self.telegram.send_html(
                    self._maker_settlement_message(payload)
                )
            except DeliveryUncertain as exc:
                await asyncio.to_thread(
                    self.maker_store.append_research_event,
                    order_id,
                    event_type=MAKER_SETTLEMENT_NOTIFY_UNCERTAIN,
                    payload={"error": exc.code, "financial_authority": False},
                )
                continue
            except WeatherLivePaperError as exc:
                errors.append(f"MAKER_SETTLEMENT_TELEGRAM:{order_id}:{exc.code}")
                continue
            await asyncio.to_thread(
                self.maker_store.append_research_event,
                order_id,
                event_type=MAKER_SETTLEMENT_NOTIFY_SENT,
                payload={
                    "telegram_message_id": int(message_id),
                    "financial_authority": False,
                },
            )
        return errors

    async def run_cycle(self) -> dict:
        status = dict(await super().run_cycle())
        status.update(
            {
                "final_all_paper_runtime_v2_version": FINAL_ALL_PAPER_RUNTIME_V2_VERSION,
                "independent_review_corrective_v2_version": INDEPENDENT_REVIEW_CORRECTIVE_V2_VERSION,
                "same_day_dynamic_fee_semantics_fail_closed": True,
                "post_receipt_wrh_thesis_revalidation_required": True,
                "v5_per_leg_visible_capacity_required": True,
                "v5_capacity_recomputed_from_legs": True,
                "v5_admission_cross_process_serialized": True,
                "structural_execution_model": "THEORETICAL_SIMULTANEOUS_BASKET_ONLY",
                "structural_validated_pnl_enabled": False,
                "maker_queue_position_certified": False,
                "maker_simulated_fill_accounting_enabled": False,
                "maker_proposal_delivery_enabled": bool(status.get("maker_paper_delivery_enabled")),
                "maker_settlement_strict_uma_finality": True,
                "future_day_forecast_run_age_known": False,
                "future_day_forecast_run_age_claim_suppressed": True,
                "financial_delivery": False,
                "financial_authority": False,
                "automatic_order_placement": False,
                "wallet_or_order_api_loaded": False,
            }
        )
        _atomic_json(self.status_path, status)
        return status


async def _main(args) -> int:
    service = FinalAllPaperWeatherLiveServiceV2(
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
            return 0 if status.get("operator_all_lanes_healthy") is True else 2
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
