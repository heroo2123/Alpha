from __future__ import annotations

"""Canonical weather LIVE PAPER v4 corrective runtime.

This is the only intended guarded live-paper entrypoint after the September 13
adversarial review.  It keeps real trading disabled while making every retained
paper decision reproducible, time-bounded and explicit about uncertainty.

Same-day raw GEFS remains disabled.  Forecast signals are future-station-local-day
research only until the separate observation-conditioned architecture is validated.
"""

import argparse
import asyncio
import hashlib
import html
import json
import math
import time
from datetime import datetime, time as dt_time, timezone
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .weather_only_clob import WeatherCLOBError, conservative_taker_fee_per_share
from .weather_only_contracts import DAILY_HIGH, DAILY_LOW, compile_weather_event
from .weather_only_forecast import WeatherForecastError, map_ensemble_to_contract_buckets
from .weather_only_forecast_v4 import (
    FORECAST_V4_POLICY_VERSION,
    forecast_cache_key,
    semantic_contract_payload,
    semantic_digest,
    validate_distribution_for_station,
)
from .weather_only_live_paper import (
    DEFAULT_FORECAST_CACHE_SECONDS,
    DEFAULT_FORECAST_RAW_GAP_MIN,
    DEFAULT_INTERVAL_SECONDS,
    DEFAULT_MAX_FORECAST_EVENTS,
    FORECAST_MAPPING_POLICY,
    MODE,
    WeatherLivePaperError,
    WeatherLivePaperService,
    _atomic_json,
    _canonical,
    _event_id,
    _event_link,
    _event_title,
)
from .weather_only_paper_control_v4 import (
    PlainWeatherPaperCommandControllerV4,
    WeatherPaperSettlementEngineV4,
)
from .weather_only_paper_outbox_v4 import (
    ACKNOWLEDGED,
    EXPIRED,
    UNCERTAIN,
    WeatherPaperOutboxV4,
    WeatherPaperTelegramOutboxSenderV4,
)
from .weather_only_paper_positions_v4 import (
    PAPER_EXECUTION_PROTOCOL_V4,
    ValidatedWeatherPaperPositionStore,
)
from .weather_only_rules import apply_rule_authority, compile_temperature_rule_authority
from .weather_only_station_metadata import WeatherStationMetadataError
from .weather_only_structural import complete_bucket_underround


WEATHER_LIVE_PAPER_V4_VERSION = "weather_live_paper_v4_adversarial_corrective_fail_closed"
DEFAULT_PAPER_STAKE_USD = 10.0
QUOTE_VALIDITY_SECONDS = 10.0
TEMPORAL_DISPATCH_MARGIN_SECONDS = 30.0
STATION_METADATA_CACHE_SECONDS = 3600.0
MAX_FORECAST_CACHE_ENTRIES = 128


class WeatherLivePaperV4Error(RuntimeError):
    def __init__(self, code: str):
        self.code = str(code)
        super().__init__(self.code)


def _sha(value: object) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _decision_envelope(payload: dict) -> dict:
    envelope = dict(payload)
    envelope.pop("decision_id", None)
    envelope["decision_id"] = _sha(envelope)
    return envelope


def _question_for_market(event: dict, market_id: str) -> str:
    wanted = str(market_id or "")
    for row in event.get("markets") or []:
        if isinstance(row, dict) and str(row.get("id") or "") == wanted:
            return str(row.get("question") or "").strip()
    return ""


def _bucket_label(bucket, unit: str) -> str:
    if bucket.lower is None:
        return f"≤ {bucket.upper:g}°{unit}"
    if bucket.upper is None:
        return f"≥ {bucket.lower:g}°{unit}"
    if abs(float(bucket.lower) - float(bucket.upper)) < 1e-12:
        return f"{bucket.lower:g}°{unit}"
    return f"{bucket.lower:g}–{bucket.upper:g}°{unit}"


def _money(value: float) -> str:
    return f"${float(value):.2f}"


def _cents(value: float) -> str:
    return f"{100.0 * float(value):.3f}¢"


class OutboxSettlementEngineV4(WeatherPaperSettlementEngineV4):
    """Settlement notifications use the same durable outbox as signals."""

    def __init__(self, *, store, outbox, finality=None) -> None:
        super().__init__(store=store, telegram=None, finality=finality)
        self.outbox = outbox

    async def _notify_resolved(self) -> tuple[int, list[str]]:
        queued = 0
        errors: list[str] = []
        rows = await asyncio.to_thread(self.store.resolved_without_notification, 50)
        for position in rows:
            try:
                self.outbox.enqueue(
                    logical_id=f"paper-result-position-{int(position['id'])}",
                    position_id=int(position["id"]),
                    message_kind="SETTLEMENT_RESULT",
                    body_html=self._resolution_message(position),
                )
            except Exception as exc:
                errors.append(f"SETTLEMENT_OUTBOX:{position.get('id')}:{type(exc).__name__}")
            else:
                queued += 1
        return queued, errors


class WeatherLivePaperV4Service(WeatherLivePaperService):
    def __init__(self, *, paper_stake_usd: float = DEFAULT_PAPER_STAKE_USD, **kwargs) -> None:
        super().__init__(**kwargs)
        stake = float(paper_stake_usd)
        if not 0.10 <= stake <= 10_000.0:
            raise ValueError("paper_stake_usd must be in 0.10..10000")
        self.paper_stake_usd = stake
        self.positions = ValidatedWeatherPaperPositionStore(self.db_path)
        self.outbox = WeatherPaperOutboxV4(self.db_path)
        self.outbox_sender = WeatherPaperTelegramOutboxSenderV4(
            token=self.telegram.token,
            chat_id=self.telegram.chat_id,
            outbox=self.outbox,
        )
        self.settlement = OutboxSettlementEngineV4(store=self.positions, outbox=self.outbox)
        self.commands = PlainWeatherPaperCommandControllerV4(
            telegram=self.telegram,
            store=self.positions,
            status_path=self.status_path,
            paper_stake_usd=self.paper_stake_usd,
        )
        self._v4_station_cache: dict[str, tuple[float, object]] = {}
        self._v4_forecast_cache: dict[str, tuple[float, dict]] = {}

    async def close(self) -> None:
        await asyncio.gather(
            self.commands.close(),
            self.settlement.close(),
            self.outbox_sender.close(),
            return_exceptions=True,
        )
        await super().close()

    async def send_startup(self) -> int:
        release = self.release_sha()
        stats = await asyncio.to_thread(self.positions.stats)
        text = "\n".join([
            "🟢 <b>WEATHER PAPER v4 ONLINE</b>",
            "",
            "This is the adversarial-corrective PAPER runtime.",
            "Same-day raw GEFS: <b>OFF</b>",
            "Final results: <b>on-chain CTF payout only</b>",
            "Old/unverifiable rows: <b>excluded from validated P&amp;L</b>",
            f"Validated open trades now: <b>{stats['open']}</b>",
            "",
            "Commands: /status /stats /positions /history /skipped /recent /help",
            "🚫 <b>REAL ORDERS DISABLED</b>",
            f"Release: <code>{html.escape(release[:12])}</code>",
        ])
        return await self.telegram.send_html(text)

    async def _station_metadata(self, station: str):
        key = str(station or "").strip().upper()
        now = time.monotonic()
        cached = self._v4_station_cache.get(key)
        if cached is not None and now - cached[0] <= STATION_METADATA_CACHE_SECONDS:
            return cached[1]
        metadata = await self.station_client.station(key)
        self._v4_station_cache[key] = (now, metadata)
        if len(self._v4_station_cache) > 256:
            oldest = sorted(self._v4_station_cache.items(), key=lambda item: item[1][0])[:64]
            for old_key, _ in oldest:
                self._v4_station_cache.pop(old_key, None)
        return metadata

    @staticmethod
    def _local_day_window(compiled, metadata, *, now_epoch: float | None = None) -> tuple[bool, float, int]:
        if compiled.target_date is None:
            raise WeatherLivePaperV4Error("TARGET_DATE_UNRESOLVED")
        try:
            zone = ZoneInfo(str(metadata.timezone))
        except (ZoneInfoNotFoundError, ValueError, TypeError):
            raise WeatherLivePaperV4Error("STATION_TIMEZONE_INVALID") from None
        now = time.time() if now_epoch is None else float(now_epoch)
        current_local = datetime.fromtimestamp(now, zone)
        delta_days = (compiled.target_date - current_local.date()).days
        target_midnight = datetime.combine(compiled.target_date, dt_time.min, tzinfo=zone).timestamp()
        deadline = target_midnight - TEMPORAL_DISPATCH_MARGIN_SECONDS
        return bool(delta_days >= 1 and now < deadline), deadline, delta_days

    async def _eligible_forecast_events(self, events: list[dict]) -> tuple[list[tuple[dict, object, object]], dict, list[str]]:
        rows: list[tuple[int, str, dict, object, object]] = []
        counts = {
            "discovered": len(events),
            "temperature_compiled": 0,
            "rule_certified": 0,
            "future_local_eligible": 0,
            "same_day_suppressed": 0,
            "outside_horizon": 0,
            "metadata_failures": 0,
        }
        errors: list[str] = []
        now = time.time()
        for event in events:
            if not isinstance(event, dict):
                continue
            raw = compile_weather_event(event)
            if raw.family not in {DAILY_HIGH, DAILY_LOW}:
                continue
            counts["temperature_compiled"] += 1
            authority = compile_temperature_rule_authority(event, raw)
            compiled = apply_rule_authority(raw, authority)
            if (
                not compiled.shadow_supported
                or not compiled.partition_shape_complete
                or not compiled.exactly_one_outcome_proven
                or compiled.target_date is None
                or not compiled.station_hint
                or not compiled.buckets
            ):
                continue
            counts["rule_certified"] += 1
            try:
                metadata = await self._station_metadata(str(compiled.station_hint))
                eligible, _deadline, delta = self._local_day_window(compiled, metadata, now_epoch=now)
            except (WeatherStationMetadataError, WeatherLivePaperV4Error) as exc:
                counts["metadata_failures"] += 1
                errors.append(f"FORECAST_ELIGIBILITY:{compiled.event_id}:{getattr(exc, 'code', type(exc).__name__)}")
                continue
            if delta <= 0:
                counts["same_day_suppressed"] += 1
                continue
            if delta > 3:
                counts["outside_horizon"] += 1
                continue
            if eligible:
                counts["future_local_eligible"] += 1
                rows.append((delta, compiled.event_id, event, compiled, metadata))

        rows.sort(key=lambda row: (row[0], row[1]))
        if not rows:
            return [], counts, errors
        cursor = int(self.positions.get_state("v4_forecast_schedule_cursor", "0") or 0) % len(rows)
        ordered = rows[cursor:] + rows[:cursor]
        selected = ordered[: self.max_forecast_events]
        self.positions.set_state(
            "v4_forecast_schedule_cursor",
            (cursor + len(selected)) % len(rows),
        )
        return [(event, compiled, metadata) for _, _, event, compiled, metadata in selected], counts, errors

    async def _mapped_forecast_v4(self, compiled, metadata) -> dict:
        key = forecast_cache_key(compiled, station_metadata=metadata, mapping_policy=FORECAST_MAPPING_POLICY)
        now_mono = time.monotonic()
        cached = self._v4_forecast_cache.get(key)
        if cached is not None and now_mono - cached[0] <= self.forecast_cache_seconds:
            return cached[1]
        distribution = await self.forecast_client.daily_extreme(
            station=str(compiled.station_hint).upper(),
            latitude=float(metadata.latitude),
            longitude=float(metadata.longitude),
            target_date=compiled.target_date,
            family=compiled.family,
            unit=compiled.unit,
            timezone=str(metadata.timezone),
        )
        provenance = validate_distribution_for_station(
            distribution,
            station_latitude=float(metadata.latitude),
            station_longitude=float(metadata.longitude),
        )
        mapped = map_ensemble_to_contract_buckets(compiled, distribution, FORECAST_MAPPING_POLICY)
        result = {
            "semantic_digest": key,
            "contract_semantics": semantic_contract_payload(
                compiled, station_metadata=metadata, mapping_policy=FORECAST_MAPPING_POLICY
            ),
            "distribution": distribution,
            "mapped": mapped,
            "forecast_provenance": provenance,
        }
        self._v4_forecast_cache[key] = (now_mono, result)
        if len(self._v4_forecast_cache) > MAX_FORECAST_CACHE_ENTRIES:
            oldest = sorted(self._v4_forecast_cache.items(), key=lambda item: item[1][0])[:32]
            for old_key, _ in oldest:
                self._v4_forecast_cache.pop(old_key, None)
        return result

    async def _forecast_candidate_v4(self, event: dict, compiled, metadata) -> dict | None:
        bundle = await self._mapped_forecast_v4(compiled, metadata)
        mapped = bundle["mapped"]
        exact = await self.runtime.clob.exact_event_snapshot(compiled)
        bucket_by_market = {bucket.market_id: bucket for bucket in compiled.buckets}
        best: dict | None = None
        for row in mapped.bucket_frequencies:
            bucket = bucket_by_market.get(row.market_id)
            if bucket is None:
                continue
            params = exact.parameters.get(bucket.condition_id)
            if params is None or (params.fee_rate > 0.0 and params.taker_only is not True):
                continue
            yes_hits = int(row.member_hits)
            member_count = int(row.member_count)
            for side, token, hits, probability in (
                ("YES", bucket.yes_token, yes_hits, float(row.raw_member_frequency)),
                ("NO", bucket.no_token, member_count - yes_hits, float(row.raw_no_frequency)),
            ):
                if not token:
                    continue
                book = exact.books.get(token)
                if book is None or book.best_ask is None or book.best_ask_size <= 0.0:
                    continue
                ask = float(book.best_ask)
                fee = conservative_taker_fee_per_share(ask, params.fee_rate, params.fee_exponent)
                cost = ask + fee
                gap = probability - cost
                if gap < self.forecast_raw_gap_min:
                    continue
                candidate = {
                    "lane": "weather_forecast_raw_gap",
                    "evidence_class": "FUTURE_DAY_RAW_GEFS_UNCALIBRATED_V4",
                    "event_id": compiled.event_id,
                    "market_id": bucket.market_id,
                    "condition_id": bucket.condition_id,
                    "token_id": token,
                    "side": side,
                    "station": str(compiled.station_hint).upper(),
                    "timezone": str(metadata.timezone),
                    "target_date": compiled.target_date.isoformat(),
                    "family": compiled.family,
                    "unit": compiled.unit,
                    "bucket_label": _bucket_label(bucket, compiled.unit),
                    "raw_probability": probability,
                    "member_hits": hits,
                    "member_count": member_count,
                    "raw_gap": gap,
                    "semantic_digest": bundle["semantic_digest"],
                    "forecast_provenance": bundle["forecast_provenance"],
                    "forecast_source_evidence_sha256": mapped.source_evidence_sha256,
                    "exact_clob": True,
                    "calibrated_probability": False,
                }
                if best is None or float(candidate["raw_gap"]) > float(best["raw_gap"]):
                    best = candidate
        return best

    @staticmethod
    def _capacity_key(legs: list[dict]) -> str:
        return _sha([
            {
                "market_id": row["market_id"],
                "condition_id": row["condition_id"],
                "token_id": row["token_id"],
                "ask": row["ask"],
                "visible_units": row["visible_units"],
                "book_hash": row.get("book_hash"),
                "provider_timestamp": row.get("provider_timestamp"),
                "book_received_at": row.get("book_received_at"),
            }
            for row in legs
        ])

    def _leg_from_exact(self, *, bucket, token: str, outcome: str, exact, fee: float | None = None) -> dict:
        params = exact.parameters.get(bucket.condition_id)
        book = exact.books.get(token)
        if params is None or book is None or book.best_ask is None or book.best_ask_size <= 0.0:
            raise WeatherLivePaperV4Error("FINAL_EXECUTION_LEG_UNAVAILABLE")
        ask = float(book.best_ask)
        actual_fee = conservative_taker_fee_per_share(ask, params.fee_rate, params.fee_exponent) if fee is None else float(fee)
        if params.fee_rate > 0.0 and params.taker_only is not True:
            raise WeatherLivePaperV4Error("FINAL_EXECUTION_FEE_SEMANTICS_UNPROVEN")
        return {
            "market_id": bucket.market_id,
            "condition_id": bucket.condition_id,
            "token_id": token,
            "outcome": str(outcome).upper(),
            "ask": ask,
            "fee": actual_fee,
            "visible_units": float(book.best_ask_size),
            "minimum_order_size": float(params.minimum_order_size),
            "minimum_tick_size": float(params.minimum_tick_size),
            "book_hash": str(book.book_hash or ""),
            "provider_timestamp": str(book.timestamp or ""),
            "book_received_at": float(book.received_at or 0.0),
            "market_parameters_received_at": float(params.received_at),
            "fee_rate": float(params.fee_rate),
            "fee_exponent": int(params.fee_exponent),
        }

    async def _finalize_forecast_decision(self, event: dict, compiled, metadata, candidate: dict) -> dict | None:
        eligible, temporal_deadline, _delta = self._local_day_window(compiled, metadata)
        if not eligible:
            return None
        exact = await self.runtime.clob.exact_event_snapshot(compiled)
        # Revalidate after the network await: this is the actual midnight escape guard.
        eligible, temporal_deadline, _delta = self._local_day_window(compiled, metadata)
        if not eligible:
            return None
        bucket = next((row for row in compiled.buckets if row.market_id == candidate["market_id"]), None)
        if bucket is None:
            return None
        token = bucket.yes_token if candidate["side"] == "YES" else bucket.no_token
        if token != candidate["token_id"]:
            return None
        leg = self._leg_from_exact(bucket=bucket, token=token, outcome=candidate["side"], exact=exact)
        cost = float(leg["ask"]) + float(leg["fee"])
        gap = float(candidate["raw_probability"]) - cost
        if gap < self.forecast_raw_gap_min:
            return None
        quote_at = max(float(leg["book_received_at"]), float(exact.finished_at))
        fill_at = time.time()
        quote_expires = min(quote_at + QUOTE_VALIDITY_SECONDS, temporal_deadline)
        if fill_at >= quote_expires:
            return None
        execution = {
            "version": PAPER_EXECUTION_PROTOCOL_V4,
            "target_stake_usd": self.paper_stake_usd,
            "aggregate_cost_per_unit": cost,
            "quote_observed_at": quote_at,
            "quote_expires_at": quote_expires,
            "paper_fill_at": fill_at,
            "capacity_key": self._capacity_key([leg]),
            "legs": [leg],
        }
        authority = compile_temperature_rule_authority(event, compile_weather_event(event))
        envelope = _decision_envelope({
            "version": WEATHER_LIVE_PAPER_V4_VERSION,
            "release_sha": self.release_sha(),
            "cohort_id": "v4-future-day-raw-gefs",
            "semantic_digest": str(candidate["semantic_digest"]),
            "decision_at": fill_at,
            "contract_event": event,
            "compiled_contract": compiled.as_dict(),
            "rule_authority": authority.as_dict(),
            "station_metadata": {
                "station": str(compiled.station_hint),
                "latitude": float(metadata.latitude),
                "longitude": float(metadata.longitude),
                "timezone": str(metadata.timezone),
            },
            "forecast_evidence": candidate["forecast_provenance"],
            "selected_market": {
                "market_id": candidate["market_id"],
                "condition_id": candidate["condition_id"],
                "side": candidate["side"],
                "token_id": candidate["token_id"],
                "bucket_label": candidate["bucket_label"],
                "member_hits": candidate["member_hits"],
                "member_count": candidate["member_count"],
                "raw_member_frequency": candidate["raw_probability"],
                "raw_gap_at_final_quote": gap,
            },
            "paper_execution_protocol": PAPER_EXECUTION_PROTOCOL_V4,
            "financial_authority": False,
        })
        return {
            **candidate,
            "ask": float(leg["ask"]),
            "fee": float(leg["fee"]),
            "entry_cost": cost,
            "ask_size": float(leg["visible_units"]),
            "raw_gap": gap,
            "decision_envelope": envelope,
            "paper_execution": execution,
            "delivery_not_after": quote_expires,
        }

    def _forecast_message_v4(self, decision: dict, event: dict) -> str:
        env = decision["decision_envelope"]
        did = str(env["decision_id"])
        side = str(decision["side"])
        question = _question_for_market(event, str(decision["market_id"]))
        cost = float(decision["entry_cost"])
        visible = float(decision["ask_size"])
        max_units = min(self.paper_stake_usd / cost, visible) if cost > 0 else 0.0
        paper_money = max_units * cost
        action = (
            f"Paper BUY YES: final {('high' if decision['family'] == DAILY_HIGH else 'low')} IS {decision['bucket_label']}"
            if side == "YES" else
            f"Paper BUY NO: final {('high' if decision['family'] == DAILY_HIGH else 'low')} is NOT {decision['bucket_label']}"
        )
        lines = [
            f"🔬 <b>PAPER WEATHER TRADE {html.escape(did[:8])}</b>",
            f"<b>{html.escape(_event_title(event, decision['event_id']))}</b>",
        ]
        if question:
            lines.append(f"Market: {html.escape(question)}")
        lines.extend([
            "",
            f"👉 <b>{html.escape(action)}</b>",
            f"Model evidence: <b>{int(decision['member_hits'])}/{int(decision['member_count'])} GEFS ensemble members</b>",
            "This is a raw ensemble vote — <b>not a calibrated win probability</b>.",
            f"Station/date: <b>{html.escape(decision['station'])}</b> / <b>{html.escape(decision['target_date'])}</b> ({html.escape(decision['timezone'])})",
            "",
            f"Captured ask: <b>{_cents(decision['ask'])}</b> + fee <b>{_cents(decision['fee'])}</b>",
            f"Paper entry/share: <b>{_cents(cost)}</b>",
            f"Visible quantity at captured ask: <b>{visible:.3f} shares</b>",
            f"Paper tracker can use up to: <b>{max_units:.3f} shares / {_money(paper_money)}</b>",
            f"Raw model-minus-cost gap at final snapshot: <b>{100.0 * float(decision['raw_gap']):+.1f} pp</b>",
            "",
            "⏱ The quote is a short-lived captured paper snapshot, not a promise that the price is still available when you read this.",
            "🧭 Provider model-run initialization time is currently <b>not independently verified</b>; retrieval/source evidence is archived.",
            "✅ If Telegram acknowledges this message before expiry, /recent will show it as TAKEN and /positions will show the exact simulated fill.",
            "🚫 No real order was placed.",
        ])
        return "\n".join(lines)

    async def _queue_forecast_signal(self, event: dict, compiled, metadata, candidate: dict) -> tuple[bool, str | None]:
        try:
            decision = await self._finalize_forecast_decision(event, compiled, metadata, candidate)
        except (WeatherCLOBError, WeatherLivePaperV4Error) as exc:
            return False, f"FORECAST_FINAL:{compiled.event_id}:{getattr(exc, 'code', type(exc).__name__)}"
        if decision is None:
            return False, None
        env = decision["decision_envelope"]
        fingerprint = _sha({
            "lane": decision["lane"],
            "semantic_digest": env["semantic_digest"],
            "market_id": decision["market_id"],
            "side": decision["side"],
            "forecast_source_evidence_sha256": decision["forecast_source_evidence_sha256"],
        })
        payload = {
            **decision,
            "paper_mode": True,
            "event_title": _event_title(event, str(decision["event_id"])),
            "event_url": _event_link(event),
            "execution_verified": False,
            "financial_authority": False,
            "automatic_order_placement": False,
        }
        signal_id = self.store.save_signal(
            fingerprint=fingerprint,
            lane=str(decision["lane"]),
            evidence_class=str(decision["evidence_class"]),
            event_id=str(decision["event_id"]),
            market_id=str(decision["market_id"]),
            side=str(decision["side"]),
            token_id=str(decision["token_id"]),
            model_probability=float(decision["raw_probability"]),
            entry_cost=float(decision["entry_cost"]),
            raw_gap=float(decision["raw_gap"]),
            theoretical_payout=1.0,
            created_at=float(decision["paper_execution"]["paper_fill_at"]),
            payload=payload,
        )
        if signal_id is None:
            return False, None
        body = self._forecast_message_v4(decision, event)
        self.outbox.enqueue(
            logical_id=f"forecast-{env['decision_id']}",
            signal_id=signal_id,
            message_kind="FORECAST_SIGNAL",
            body_html=body,
            url=_event_link(event),
            not_after=float(decision["delivery_not_after"]),
        )
        return True, None

    async def _queue_structural_signal(self, opportunity_hint: dict, event: dict | None) -> tuple[bool, str | None]:
        if not isinstance(event, dict):
            return False, None
        raw = compile_weather_event(event)
        authority = compile_temperature_rule_authority(event, raw)
        compiled = apply_rule_authority(raw, authority)
        if not compiled.exactly_one_outcome_proven or not compiled.partition_shape_complete:
            return False, None
        try:
            exact = await self.runtime.clob.exact_event_snapshot(compiled)
        except WeatherCLOBError as exc:
            return False, f"STRUCTURAL_FINAL:{compiled.event_id}:{exc.code}"
        opportunity = complete_bucket_underround(
            compiled,
            exact.books,
            exact.parameters,
            min_profit_per_set=float(self.runtime.min_shadow_profit_per_set),
        )
        if opportunity is None:
            return False, None
        opp = opportunity.as_dict()
        buckets_by_market = {bucket.market_id: bucket for bucket in compiled.buckets}
        legs: list[dict] = []
        readable_legs: list[dict] = []
        for market_id, token_id, fee in zip(
            opportunity.market_ids,
            opportunity.token_ids,
            opportunity.conservative_fees_per_share,
        ):
            bucket = buckets_by_market.get(str(market_id))
            if bucket is None or bucket.yes_token != token_id:
                return False, "STRUCTURAL_FINAL:IDENTITY_MISMATCH"
            leg = self._leg_from_exact(bucket=bucket, token=str(token_id), outcome="YES", exact=exact, fee=float(fee))
            legs.append(leg)
            readable_legs.append({
                "question": _question_for_market(event, bucket.market_id),
                "bucket": _bucket_label(bucket, compiled.unit),
                "ask": leg["ask"],
                "fee": leg["fee"],
            })
        quote_at = max(float(row["book_received_at"]) for row in legs)
        fill_at = time.time()
        quote_expires = quote_at + QUOTE_VALIDITY_SECONDS
        if fill_at >= quote_expires:
            return False, None
        structural_semantics = {
            "compiled_contract": compiled.as_dict(),
            "rule_authority": authority.as_dict(),
        }
        semantic = _sha(structural_semantics)
        execution = {
            "version": PAPER_EXECUTION_PROTOCOL_V4,
            "target_stake_usd": self.paper_stake_usd,
            "aggregate_cost_per_unit": float(opportunity.gross_cost_per_set),
            "quote_observed_at": quote_at,
            "quote_expires_at": quote_expires,
            "paper_fill_at": fill_at,
            "capacity_key": self._capacity_key(legs),
            "legs": legs,
        }
        envelope = _decision_envelope({
            "version": WEATHER_LIVE_PAPER_V4_VERSION,
            "release_sha": self.release_sha(),
            "cohort_id": "v4-structural-complete-set-current-rule-proof",
            "semantic_digest": semantic,
            "decision_at": fill_at,
            "contract_event": event,
            **structural_semantics,
            "paper_execution_protocol": PAPER_EXECUTION_PROTOCOL_V4,
            "financial_authority": False,
        })
        fingerprint = _sha({
            "lane": opportunity.lane,
            "event_id": compiled.event_id,
            "semantic_digest": semantic,
            "token_ids": list(opportunity.token_ids),
        })
        payload = {
            **opp,
            "paper_mode": True,
            "event_title": _event_title(event, compiled.event_id),
            "event_url": _event_link(event),
            "readable_legs": readable_legs,
            "decision_envelope": envelope,
            "paper_execution": execution,
            "execution_verified": False,
            "financial_authority": False,
            "automatic_order_placement": False,
        }
        signal_id = self.store.save_signal(
            fingerprint=fingerprint,
            lane=str(opportunity.lane),
            evidence_class="STRUCTURAL_RULE_PROOF_PAPER_EXECUTION_V4",
            event_id=compiled.event_id,
            entry_cost=float(opportunity.gross_cost_per_set),
            raw_gap=float(opportunity.locked_profit_per_set),
            theoretical_payout=1.0,
            created_at=fill_at,
            payload=payload,
        )
        if signal_id is None:
            return False, None
        body = self._structural_message_v4(payload)
        self.outbox.enqueue(
            logical_id=f"structural-{envelope['decision_id']}",
            signal_id=signal_id,
            message_kind="STRUCTURAL_SIGNAL",
            body_html=body,
            url=_event_link(event),
            not_after=quote_expires,
        )
        return True, None

    def _structural_message_v4(self, payload: dict) -> str:
        did = str((payload.get("decision_envelope") or {}).get("decision_id") or "")
        legs = payload.get("readable_legs") or []
        lines = [
            f"🧩 <b>PAPER STRUCTURAL SET {html.escape(did[:8])}</b>",
            f"<b>{html.escape(str(payload.get('event_title') or payload.get('event_id') or ''))}</b>",
            "",
            "Paper action: buy YES on every listed temperature bucket as one complete set:",
        ]
        for index, leg in enumerate(legs, 1):
            label = str(leg.get("bucket") or leg.get("question") or f"leg {index}")
            lines.append(f"{index}. <b>{html.escape(label)}</b> — {_cents(float(leg['ask']))} + {_cents(float(leg['fee']))} fee")
        total = float(payload.get("gross_cost_per_set") or 0.0)
        capacity = float(payload.get("common_best_ask_shares") or 0.0)
        sets = min(self.paper_stake_usd / total, capacity) if total > 0 else 0.0
        capital = sets * total
        payout = sets * 1.0
        lines.extend([
            "",
            f"Captured cost per complete set: <b>{_money(total)}</b>",
            f"Current-rule complete-set payout hypothesis: <b>$1.00/set</b>",
            f"Captured margin per set: <b>{_money(1.0 - total)}</b>",
            f"Visible common capacity: <b>{capacity:.3f} sets</b>",
            f"Paper tracker can use: <b>{sets:.3f} sets / {_money(capital)}</b>",
            f"If the certified current-rule payout structure holds: {_money(payout)} payout, {_money(payout-capital)} paper margin.",
            "",
            "⚠️ This is <b>not described as guaranteed</b>. v4 records the exact child rules/tokens/books and will score only final on-chain payouts.",
            "⏱ Captured quote is short-lived; /recent confirms whether the paper fill was actually accepted into the ledger.",
            "🚫 No real order was placed.",
        ])
        return "\n".join(lines)

    def _recover_missing_outbox(self) -> int:
        """Fail-safe: legacy/crashed v4 signals without delivery evidence stay excluded.

        We deliberately do not recreate an alert from mutable current state.  If a
        signal somehow exists without a durable outbox row, mark it unverified when
        the position layer later sees it; the original evidence remains in the DB.
        """
        return 0

    async def _drain_outbox_and_reconcile(self) -> tuple[int, list[str]]:
        rows = await self.outbox_sender.drain(maximum=100)
        acknowledged = 0
        errors: list[str] = []
        for row in rows:
            state = str(row.get("state") or "")
            if state == ACKNOWLEDGED:
                acknowledged += 1
                message_id = int(row["telegram_message_id"])
                if row.get("signal_id") is not None:
                    sid = int(row["signal_id"])
                    try:
                        self.store.mark_telegram_sent(sid, message_id, sent_at=float(row.get("acknowledged_at") or time.time()))
                        await asyncio.to_thread(self.positions.ensure_position_for_signal, sid, self.paper_stake_usd)
                    except Exception as exc:
                        errors.append(f"OUTBOX_SIGNAL_RECONCILE:{sid}:{type(exc).__name__}")
                if row.get("position_id") is not None:
                    pid = int(row["position_id"])
                    try:
                        await asyncio.to_thread(self.positions.mark_settlement_notified, pid, message_id)
                    except Exception as exc:
                        errors.append(f"OUTBOX_SETTLEMENT_RECONCILE:{pid}:{type(exc).__name__}")
            elif state in {UNCERTAIN, EXPIRED}:
                # No validated paper fill is created when visible delivery is unknown
                # or expired. Evidence remains in outbox/signals for audit.
                pass
        return acknowledged, errors

    async def run_cycle(self) -> dict:
        started_wall = time.time()
        started_mono = time.monotonic()
        errors: list[str] = []
        queued_signals = 0

        try:
            structural_report = await self.runtime.run_cycle()
        except Exception as exc:
            structural_report = {"cycle_ok": False, "opportunities": [], "errors": [f"UNHANDLED:{type(exc).__name__}"]}
        if not structural_report.get("cycle_ok"):
            errors.extend(f"STRUCTURAL:{value}" for value in (structural_report.get("errors") or ["UNHEALTHY"]))

        try:
            discovery = await self.runtime.discovery.discover()
            events = list(discovery.events)
        except Exception as exc:
            events = []
            errors.append(f"DISCOVERY:{getattr(exc, 'code', type(exc).__name__)}")
        by_id = {_event_id(event): event for event in events if _event_id(event)}

        structural_hints = list(structural_report.get("opportunities") or [])
        for hint in structural_hints:
            created, error = await self._queue_structural_signal(hint, by_id.get(str(hint.get("event_id") or "")))
            if created:
                queued_signals += 1
            if error:
                errors.append(error)

        forecast_rows, forecast_counts, forecast_errors = await self._eligible_forecast_events(events)
        errors.extend(forecast_errors)
        forecast_candidates = 0
        for event, compiled, metadata in forecast_rows:
            try:
                candidate = await self._forecast_candidate_v4(event, compiled, metadata)
            except (WeatherForecastError, WeatherStationMetadataError, WeatherCLOBError) as exc:
                errors.append(f"FORECAST:{compiled.event_id}:{getattr(exc, 'code', type(exc).__name__)}")
                continue
            except Exception as exc:
                errors.append(f"FORECAST:{compiled.event_id}:{type(exc).__name__}")
                continue
            if candidate is None:
                continue
            forecast_candidates += 1
            created, error = await self._queue_forecast_signal(event, compiled, metadata, candidate)
            if created:
                queued_signals += 1
            if error:
                errors.append(error)

        acked_before, delivery_errors = await self._drain_outbox_and_reconcile()
        errors.extend(delivery_errors)

        try:
            settlement = await self.settlement.settle_once()
        except Exception as exc:
            settlement = {
                "version": "ERROR",
                "open_checked": 0,
                "resolved_now": 0,
                "pending_now": 0,
                "errors": [f"UNHANDLED:{type(exc).__name__}"],
            }
        errors.extend(str(value) for value in settlement.get("errors") or [])
        acked_after, delivery_errors = await self._drain_outbox_and_reconcile()
        errors.extend(delivery_errors)

        try:
            stats = await asyncio.to_thread(self.positions.stats)
            outbox_summary = await asyncio.to_thread(self.outbox.summary)
        except Exception as exc:
            stats = {}
            outbox_summary = {}
            errors.append(f"STATS:{type(exc).__name__}")

        finished = time.time()
        status = {
            "version": WEATHER_LIVE_PAPER_V4_VERSION,
            "mode": MODE,
            "release_sha": self.release_sha(),
            "started_at": started_wall,
            "finished_at": finished,
            "expected_cycle_interval_seconds": self.interval_seconds,
            "cycle_seconds": time.monotonic() - started_mono,
            "cycle_ok": not errors,
            "errors": errors,
            "discovered_weather_events": len(events),
            "structural_candidates_from_prescreen": len(structural_hints),
            "forecast_selection": forecast_counts,
            "forecast_events_evaluated": len(forecast_rows),
            "forecast_candidate_count": forecast_candidates,
            "signals_queued_this_cycle": queued_signals,
            "telegram_acknowledged_this_cycle": acked_before + acked_after,
            "paper_position_stats": stats,
            "paper_settlement": settlement,
            "paper_outbox": outbox_summary,
            "paper_command_last_poll_at": self.positions.get_state("telegram_command_last_poll_at", ""),
            "paper_command_last_error": self.positions.get_state("telegram_command_last_error", ""),
            "forecast_policy": {
                "same_day_raw_gefs": "DISABLED",
                "future_station_local_day_only": True,
                "forecast_integrity_version": FORECAST_V4_POLICY_VERSION,
                "provider_run_age_verified": False,
            },
            "paper_telegram_delivery": True,
            "financial_delivery": False,
            "financial_authority": False,
            "automatic_order_placement": False,
            "wallet_or_order_api_loaded": False,
        }
        _atomic_json(self.status_path, status)
        return status

    async def loop(self) -> None:
        await self.send_startup()
        command_task = asyncio.create_task(self.commands.loop())
        try:
            while True:
                started = time.monotonic()
                try:
                    await self.run_cycle()
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    status = {
                        "version": WEATHER_LIVE_PAPER_V4_VERSION,
                        "mode": MODE,
                        "release_sha": self.release_sha(),
                        "finished_at": time.time(),
                        "expected_cycle_interval_seconds": self.interval_seconds,
                        "cycle_ok": False,
                        "errors": [f"UNHANDLED:{type(exc).__name__}"],
                        "financial_delivery": False,
                        "financial_authority": False,
                        "automatic_order_placement": False,
                        "wallet_or_order_api_loaded": False,
                    }
                    _atomic_json(self.status_path, status)
                elapsed = time.monotonic() - started
                await asyncio.sleep(max(0.0, self.interval_seconds - elapsed))
        finally:
            command_task.cancel()
            await asyncio.gather(command_task, return_exceptions=True)


async def _main(args) -> int:
    service = WeatherLivePaperV4Service(
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
