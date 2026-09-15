from __future__ import annotations

"""All-weather PAPER runtime v8: newly observed official-extreme exclusion signals.

V8 preserves V7 post-receipt exact-CLOB accounting and adds a narrow same-day source
shock experiment. It does not alert on every bucket already inconsistent with the
running official extreme. A candidate exists only when the latest accepted official
WRH observation moves the running daily high/low enough to *newly* exclude a bucket
that was not excluded by all prior observations in that capture.

The exclusion is provisional, not deterministic: WRH revisions remain possible until
the contract cutoff. Telegram wording and status therefore label this lane
UNCALIBRATED/REVISION-SENSITIVE. A fresh exact-CLOB check after Telegram receipt is
still mandatory before a PAPER position is counted.

No real order, wallet, signing, cancellation or financial authority exists.
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
from .weather_only_contract_strict import StrictWeatherContractError, compile_strict_temperature_event
from .weather_only_contracts import DAILY_HIGH, DAILY_LOW
from .weather_only_live_paper import (
    DEFAULT_FORECAST_CACHE_SECONDS,
    DEFAULT_FORECAST_RAW_GAP_MIN,
    DEFAULT_INTERVAL_SECONDS,
    DEFAULT_MAX_FORECAST_EVENTS,
    WeatherLivePaperError,
    _atomic_json,
    _event_link,
    _event_title,
)
from .weather_only_live_paper_all_signals import (
    LOCAL_MIDNIGHT_MARGIN_SECONDS,
    SAME_DAY_MAX_OBSERVATION_AGE_SECONDS,
    _bucket_label,
)
from .weather_only_live_paper_all_signals_v7 import AllPaperWeatherLiveV7Service
from .weather_only_live_paper_v2 import DEFAULT_PAPER_STAKE_USD
from .weather_only_live_paper_v4 import QUOTE_DECISION_TTL_SECONDS, V4InvariantError, _sha, _validate_exact_snapshot
from .weather_only_paper_corrective import DeliveryUncertain
from .weather_only_paper_post_receipt import PAPER_EXECUTION_PROTOCOL_V5
from .weather_only_station_metadata import WeatherStationMetadataError


ALL_PAPER_V8_RUNTIME_VERSION = (
    "weather_all_paper_signals_v8_new_official_extreme_exclusion_post_receipt"
)
SOURCE_SHOCK_LANE = "weather_official_extreme_new_exclusion"
SOURCE_SHOCK_EVIDENCE = "PROVISIONAL_WRH_NEW_EXTREME_EXCLUSION_REVISION_SENSITIVE_V1"
SOURCE_SHOCK_MIN_PAYOUT_LEFT = 0.015


class SourceShockError(RuntimeError):
    def __init__(self, code: str):
        self.code = str(code)
        super().__init__(self.code)


def _new_extreme_transition(capture: dict, family: str) -> tuple[float, float, float] | None:
    """Return (previous_extreme,current_extreme,latest_observed_at) only on a new extreme."""
    observations = capture.get("official_observations")
    observed = capture.get("observed_state")
    if not isinstance(observations, list) or not isinstance(observed, dict):
        return None
    try:
        as_of = float(capture["as_of"])
        published_extreme = float(observed["extreme_value"])
    except (KeyError, TypeError, ValueError, OverflowError):
        return None
    if not math.isfinite(as_of) or not math.isfinite(published_extreme):
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
        if (
            math.isfinite(observed_at)
            and math.isfinite(value)
            and observed_at <= as_of + 1e-6
        ):
            rows.append((observed_at, value))
    rows.sort()
    if len(rows) < 2:
        return None
    latest_at, latest_value = rows[-1]
    if as_of - latest_at > SAME_DAY_MAX_OBSERVATION_AGE_SECONDS:
        return None
    previous_values = [value for _, value in rows[:-1]]
    if family == DAILY_HIGH:
        previous = max(previous_values)
        current = max(previous, latest_value)
        if latest_value <= previous:
            return None
    elif family == DAILY_LOW:
        previous = min(previous_values)
        current = min(previous, latest_value)
        if latest_value >= previous:
            return None
    else:
        return None
    if abs(current - published_extreme) > 1e-9:
        return None
    return float(previous), float(current), float(latest_at)


def _excluded(bucket, extreme: float, family: str) -> bool:
    if family == DAILY_HIGH:
        return bucket.upper is not None and extreme > float(bucket.upper)
    if family == DAILY_LOW:
        return bucket.lower is not None and extreme < float(bucket.lower)
    return False


class AllPaperWeatherLiveV8Service(AllPaperWeatherLiveV7Service):
    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self._source_shock_sent = 0
        self._source_shock_skipped = 0

    def _source_shock_capture_candidates(self, capture: dict, event: dict) -> list[dict]:
        compiled = compile_strict_temperature_event(event)
        if compiled.family not in {DAILY_HIGH, DAILY_LOW}:
            return []
        if str(capture.get("event_id") or "") != str(compiled.event_id):
            raise SourceShockError("SOURCE_SHOCK_CAPTURE_EVENT_MISMATCH")
        if str(capture.get("station") or "").upper() != str(compiled.station_hint or "").upper():
            raise SourceShockError("SOURCE_SHOCK_CAPTURE_STATION_MISMATCH")
        if str(capture.get("target_date") or "") != compiled.target_date.isoformat():
            raise SourceShockError("SOURCE_SHOCK_CAPTURE_DATE_MISMATCH")
        reasons = {str(value) for value in (capture.get("block_reasons") or [])}
        if reasons - {"WRH_TO_MODEL_POPULATION_ALIGNMENT_UNPROVEN"}:
            return []
        metadata = capture.get("station_metadata")
        if not isinstance(metadata, dict):
            return []
        timezone_name = str(metadata.get("timezone") or "").strip()
        if not timezone_name:
            return []
        try:
            as_of = float(capture["as_of"])
            local = datetime.fromtimestamp(as_of, tz=timezone.utc).astimezone(ZoneInfo(timezone_name))
        except Exception:
            return []
        if local.date() != compiled.target_date:
            return []
        transition = _new_extreme_transition(capture, compiled.family)
        if transition is None:
            return []
        previous, current, latest_at = transition

        candidates: list[dict] = []
        for bucket in compiled.buckets:
            if not bucket.trade_open or not bucket.no_token:
                continue
            if _excluded(bucket, previous, compiled.family):
                continue
            if not _excluded(bucket, current, compiled.family):
                continue
            candidates.append(
                {
                    "lane": SOURCE_SHOCK_LANE,
                    "evidence_class": SOURCE_SHOCK_EVIDENCE,
                    "event_id": compiled.event_id,
                    "market_id": bucket.market_id,
                    "condition_id": bucket.condition_id,
                    "token_id": str(bucket.no_token),
                    "side": "NO",
                    "station": str(compiled.station_hint).upper(),
                    "station_timezone": timezone_name,
                    "target_date": compiled.target_date.isoformat(),
                    "family": compiled.family,
                    "unit": compiled.unit,
                    "bucket_label": _bucket_label(bucket, str(compiled.unit)),
                    "previous_official_extreme": previous,
                    "new_official_extreme": current,
                    "latest_official_observed_at": latest_at,
                    "capture_sha256": str(capture.get("capture_sha256") or ""),
                    "event_title": _event_title(event, compiled.event_id),
                    "event_url": _event_link(event),
                    "calibrated_probability": False,
                    "revision_sensitive": True,
                    "financial_authority": False,
                    "automatic_order_placement": False,
                }
            )
        return candidates

    async def _source_shock_exact_recheck(
        self,
        candidate: dict,
        event: dict,
        *,
        after_time: float | None,
        decision_expires_at: float | None,
    ) -> tuple[dict, object, object, object] | None:
        compiled = compile_strict_temperature_event(event)
        bucket = next(
            (row for row in compiled.buckets if row.market_id == str(candidate["market_id"])),
            None,
        )
        if bucket is None or str(bucket.no_token or "") != str(candidate["token_id"]):
            raise V4InvariantError("SOURCE_SHOCK_TOKEN_MEANING_CHANGED")
        current_extreme = float(candidate["new_official_extreme"])
        if not _excluded(bucket, current_extreme, compiled.family):
            raise V4InvariantError("SOURCE_SHOCK_BUCKET_NOT_EXCLUDED")

        exact = await self.runtime.clob.exact_event_snapshot(compiled)
        _validate_exact_snapshot(compiled, exact)
        if after_time is not None and float(exact.started_at) + 1e-9 < float(after_time):
            raise V4InvariantError("V5_POST_RECEIPT_RECHECK_NOT_CAUSAL")
        if decision_expires_at is not None and float(exact.finished_at) >= float(decision_expires_at):
            return None
        params = exact.parameters.get(bucket.condition_id)
        book = exact.books.get(str(bucket.no_token))
        if params is None or book is None or book.best_ask is None or book.best_ask_size <= 0.0:
            return None
        if params.fee_rate > 0.0 and params.taker_only is not True:
            raise V4InvariantError("SOURCE_SHOCK_DYNAMIC_FEE_SEMANTICS_UNPROVEN")
        ask = float(book.best_ask)
        fee = conservative_taker_fee_per_share(ask, params.fee_rate, params.fee_exponent)
        cost = ask + fee
        payout_left = 1.0 - cost
        if payout_left + 1e-12 < SOURCE_SHOCK_MIN_PAYOUT_LEFT:
            return None
        tick = float(params.minimum_tick_size)
        if tick <= 0.0 or abs(ask / tick - round(ask / tick)) > 1e-6:
            raise V4InvariantError("SOURCE_SHOCK_ASK_OFF_TICK")
        fresh = dict(candidate)
        fresh.update(
            {
                "ask": ask,
                "fee": fee,
                "entry_cost": cost,
                "payout_left": payout_left,
                "ask_size": float(book.best_ask_size),
                "quote_observed_at": float(book.received_at),
                "book_hash": str(book.book_hash or ""),
                "minimum_order_size": float(params.minimum_order_size),
                "minimum_tick_size": tick,
            }
        )
        return fresh, exact, bucket, params

    @staticmethod
    def _source_shock_message(candidate: dict) -> str:
        return "\n".join(
            [
                "⚡ <b>PAPER OFFICIAL-SOURCE EXTREME SHOCK</b>",
                f"<b>{html.escape(str(candidate['event_title']))}</b>",
                "",
                f"Simulated thesis: <b>BUY NO — {html.escape(str(candidate['bucket_label']))}</b>",
                f"Official running extreme moved: <b>{float(candidate['previous_official_extreme']):g}{html.escape(str(candidate['unit']))} → {float(candidate['new_official_extreme']):g}{html.escape(str(candidate['unit']))}</b>",
                "That latest accepted official observation newly excludes this bucket under the currently published source state.",
                f"Pre-send exact NO ask: <b>${float(candidate['ask']):.4f}</b> + fee ${float(candidate['fee']):.5f}",
                f"Payout remaining at the pre-send quote: <b>{100.0 * float(candidate['payout_left']):.2f}%</b>",
                "",
                "⚠️ REVISION-SENSITIVE / UNCALIBRATED: WRH may revise observations before the contract cutoff, so this is not deterministic finality.",
                "A fresh exact-CLOB recheck after this Telegram receipt is required before it counts as a PAPER position.",
                "📒 PAPER ONLY — no real order was placed.",
            ]
        )

    async def _save_and_send_source_shock(
        self, candidate: dict, event: dict
    ) -> tuple[bool, str | None]:
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
        midnight = datetime.combine(
            compiled_date := compile_strict_temperature_event(event).target_date,
            datetime.min.time(),
            tzinfo=ZoneInfo(str(fresh["station_timezone"])),
        ).timestamp()
        expires_at = min(
            float(exact.finished_at) + QUOTE_DECISION_TTL_SECONDS,
            midnight - LOCAL_MIDNIGHT_MARGIN_SECONDS,
        )
        if time.time() >= expires_at:
            self._source_shock_skipped += 1
            return False, None
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
            await asyncio.to_thread(self.positions.set_signal_status, signal_id, "EXPIRED")
            return True, "DELIVERY_RECEIPT_AFTER_EXPIRY"
        await asyncio.to_thread(
            self.positions.set_signal_status, signal_id, "POST_RECEIPT_RECHECK"
        )
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
            await asyncio.to_thread(
                self.positions.set_signal_status, signal_id, "PAPER_ACCOUNTING_ERROR"
            )
            return True, f"SOURCE_SHOCK_ACCOUNTING:{code}"
        self._source_shock_sent += 1
        return True, None

    async def run_cycle(self) -> dict:
        status = dict(await super().run_cycle())
        cycle_start = float(status.get("started_at") or time.time())
        discovery = getattr(getattr(self, "_capturing_discovery", None), "last_result", None)
        events = tuple(getattr(discovery, "events", ()) or ()) if discovery is not None else ()
        by_id = {
            str(event.get("id") or event.get("eventId") or ""): event
            for event in events
            if isinstance(event, dict)
        }
        new_signals = 0
        errors: list[str] = []
        for capture in self._captures_since(cycle_start):
            event_id = str(capture.get("event_id") or "")
            event = by_id.get(event_id)
            if event is None:
                continue
            try:
                candidates = self._source_shock_capture_candidates(capture, event)
                for candidate in candidates:
                    created, error = await self._save_and_send_source_shock(candidate, event)
                    if created:
                        new_signals += 1
                    if error:
                        errors.append(f"SOURCE_SHOCK:{event_id}:{error}")
            except (
                StrictWeatherContractError,
                SourceShockError,
                WeatherStationMetadataError,
            ) as exc:
                errors.append(
                    f"SOURCE_SHOCK:{event_id}:{getattr(exc, 'code', type(exc).__name__)}"
                )
            except Exception as exc:
                errors.append(f"SOURCE_SHOCK:{event_id}:{type(exc).__name__}")

        all_errors = list(status.get("errors") or []) + errors
        status.update(
            {
                "all_paper_v8_runtime_version": ALL_PAPER_V8_RUNTIME_VERSION,
                "source_shock_paper_delivery_enabled": True,
                "source_shock_calibrated_probability": False,
                "source_shock_revision_sensitive": True,
                "source_shock_trigger_policy": "LATEST_OFFICIAL_OBSERVATION_NEWLY_EXCLUDES_BUCKET",
                "source_shock_new_signals": new_signals,
                "source_shock_sent_total": self._source_shock_sent,
                "source_shock_skipped_total": self._source_shock_skipped,
                "source_shock_errors": errors,
                "errors": all_errors,
                "cycle_ok": bool(status.get("cycle_ok")) and not errors,
                "financial_delivery": False,
                "financial_authority": False,
                "automatic_order_placement": False,
                "wallet_or_order_api_loaded": False,
            }
        )
        _atomic_json(self.status_path, status)
        return status


async def _main(args) -> int:
    service = AllPaperWeatherLiveV8Service(
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
    parser.add_argument("--forecast-cache-seconds", type=float, default=DEFAULT_FORECAST_CACHE_SECONDS)
    parser.add_argument("--forecast-raw-gap-min", type=float, default=DEFAULT_FORECAST_RAW_GAP_MIN)
    parser.add_argument("--max-forecast-events", type=int, default=DEFAULT_MAX_FORECAST_EVENTS)
    parser.add_argument("--paper-stake-usd", type=float, default=DEFAULT_PAPER_STAKE_USD)
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    raise SystemExit(asyncio.run(_main(args)))


if __name__ == "__main__":
    main()
