from __future__ import annotations

"""Canonical weather LIVE PAPER v4 corrective runtime.

V4 keeps real-money authority at zero.  It narrows the experiment until the paper
ledger is trustworthy: structural guaranteed-basket emission is disabled, legacy
history is excluded, and future-day raw GEFS decisions are admitted only through the
strict contract gate and a final dispatch-time exact-CLOB/temporal recheck.

The three-layer same-day model foundation is implemented in
``weather_only_conditioned_extremes`` but live same-day emission remains disabled
until exact official-observation and remaining-hours data adapters pass acceptance.
"""

import argparse
import asyncio
import hashlib
import html
import json
import math
import time
from datetime import date, datetime, time as datetime_time
from pathlib import Path
from zoneinfo import ZoneInfo

from .weather_only_clob import (
    MAX_BOOK_AGE_SECONDS,
    WeatherCLOBError,
    conservative_taker_fee_per_share,
)
from .weather_only_contract_strict import (
    StrictWeatherContractError,
    compile_strict_temperature_event,
)
from .weather_only_forecast import WeatherForecastError, map_ensemble_to_contract_buckets
from .weather_only_live_paper import (
    DEFAULT_FORECAST_CACHE_SECONDS,
    DEFAULT_FORECAST_RAW_GAP_MIN,
    DEFAULT_INTERVAL_SECONDS,
    DEFAULT_MAX_FORECAST_EVENTS,
    FORECAST_MAPPING_POLICY,
    SUMMARY_INTERVAL_SECONDS,
    WeatherLivePaperError,
    _atomic_json,
    _event_link,
    _event_title,
)
from .weather_only_live_paper_v2 import DEFAULT_PAPER_STAKE_USD
from .weather_only_live_paper_v3 import WeatherLivePaperV3Service
from .weather_only_paper_corrective import (
    PAPER_EXECUTION_PROTOCOL_V4,
    CorrectivePaperTelegram,
    CorrectiveSettlementEngine,
    CorrectiveWeatherPaperPositionStore,
    ClearWeatherPaperCommandController,
    DeliveryUncertain,
)
from .weather_only_paper_positions import WeatherPaperPositionError, _payload
from .weather_only_station_metadata import WeatherStationMetadataError


WEATHER_LIVE_PAPER_V4_VERSION = "weather_live_paper_v4_fail_closed_corrective"
PRE_V4_QUARANTINE_REASON = "PRE_V4_OR_UNRECONSTRUCTABLE_HISTORY"
QUOTE_DECISION_TTL_SECONDS = 20.0
LOCAL_MIDNIGHT_MARGIN_SECONDS = 5.0
PROVIDER_BOOK_MAX_SKEW_SECONDS = 30.0
GRID_MAX_DISTANCE_KM = 100.0
MAX_CORRELATED_STATION_DAY_POSITIONS = 1


class V4InvariantError(RuntimeError):
    def __init__(self, code: str):
        self.code = str(code)
        super().__init__(self.code)


def _canonical(value: object) -> str:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False
    )


def _sha(value: object) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _provider_epoch(raw: object) -> float:
    text = str(raw or "").strip()
    if not text:
        raise V4InvariantError("V4_BOOK_PROVIDER_TIMESTAMP_MISSING")
    try:
        value = float(text)
    except (TypeError, ValueError, OverflowError):
        raise V4InvariantError("V4_BOOK_PROVIDER_TIMESTAMP_INVALID") from None
    if not math.isfinite(value) or value <= 0:
        raise V4InvariantError("V4_BOOK_PROVIDER_TIMESTAMP_INVALID")
    if value >= 100_000_000_000:
        value /= 1000.0
    if value < 1_000_000_000:
        raise V4InvariantError("V4_BOOK_PROVIDER_TIMESTAMP_INVALID")
    return value


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    from math import asin, cos, radians, sin, sqrt

    earth = 6371.0088
    p1, p2 = radians(lat1), radians(lat2)
    dlat = radians(lat2 - lat1)
    dlon = radians(lon2 - lon1)
    value = sin(dlat / 2) ** 2 + cos(p1) * cos(p2) * sin(dlon / 2) ** 2
    return 2 * earth * asin(sqrt(value))


def _semantic_digest(compiled, metadata) -> str:
    return _sha({
        "event_id": compiled.event_id,
        "station": str(compiled.station_hint).upper(),
        "target_date": compiled.target_date.isoformat(),
        "family": compiled.family,
        "unit": compiled.unit,
        "timezone": str(metadata.timezone),
        "latitude": float(metadata.latitude),
        "longitude": float(metadata.longitude),
        "mapping_policy": FORECAST_MAPPING_POLICY.policy_id,
        "buckets": [
            {
                "market_id": bucket.market_id,
                "condition_id": bucket.condition_id,
                "yes": bucket.yes_token,
                "no": bucket.no_token,
                "lower": bucket.lower,
                "upper": bucket.upper,
            }
            for bucket in compiled.buckets
        ],
    })


def _validate_exact_snapshot(compiled, snapshot) -> None:
    expected_tokens: set[str] = set()
    for bucket in compiled.buckets:
        expected_tokens.update((bucket.yes_token, bucket.no_token))
        info = snapshot.parameters.get(bucket.condition_id)
        if info is None:
            raise V4InvariantError("V4_CLOB_CONDITION_MISSING")
        outcome_map = {
            str(token): str(outcome).strip().lower()
            for token, outcome in info.token_outcomes
        }
        if (
            outcome_map.get(bucket.yes_token) != "yes"
            or outcome_map.get(bucket.no_token) != "no"
        ):
            raise V4InvariantError("V4_CLOB_OUTCOME_MEANING_MISMATCH")
    if set(snapshot.books) != expected_tokens:
        raise V4InvariantError("V4_CLOB_BOOK_SET_MISMATCH")

    now = time.time()
    for book in snapshot.books.values():
        if book.received_at is None:
            raise V4InvariantError("V4_BOOK_RECEIPT_MISSING")
        received = float(book.received_at)
        if now - received > MAX_BOOK_AGE_SECONDS or received - now > 2.0:
            raise V4InvariantError("V4_BOOK_RECEIPT_STALE")
        provider = _provider_epoch(book.timestamp)
        if abs(received - provider) > PROVIDER_BOOK_MAX_SKEW_SECONDS:
            raise V4InvariantError("V4_BOOK_PROVIDER_TIMESTAMP_STALE")
        if not str(book.book_hash or "").strip():
            raise V4InvariantError("V4_BOOK_HASH_MISSING")


class WeatherLivePaperV4Service(WeatherLivePaperV3Service):
    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        old_settlement = self.settlement
        old_commands = self.commands
        old_telegram = self.telegram

        self.positions = CorrectiveWeatherPaperPositionStore(self.db_path)
        self.telegram = CorrectivePaperTelegram(
            token=old_telegram.token,
            chat_id=old_telegram.chat_id,
        )
        self.settlement = CorrectiveSettlementEngine(
            store=self.positions,
            telegram=self.telegram,
        )
        self.commands = ClearWeatherPaperCommandController(
            telegram=self.telegram,
            store=self.positions,
            status_path=self.status_path,
            paper_stake_usd=self.paper_stake_usd,
        )
        self._v4_old_settlement = old_settlement
        self._v4_old_commands = old_commands
        self._v4_old_telegram = old_telegram

        self._forecast_cache = {}
        self._forecast_distribution_by_sha: dict[str, object] = {}
        self._v4_structural_suppressed_total = 0
        self._v4_dispatch_skipped_total = 0
        self._v4_forecast_nonfatal_skips_this_cycle: list[str] = []
        self._v4_eligible_evaluated_this_cycle = 0
        self._v4_last_summary_sent_at = 0.0
        # The inherited base emits its summary before v2/v3 settlement/tracking.
        # Disable that mid-cycle message; v4 emits one after the full cycle.
        self._last_summary_sent_at = float("inf")

    async def close(self) -> None:
        await asyncio.gather(
            self._v4_old_settlement.close(),
            self._v4_old_commands.close(),
            self._v4_old_telegram.close(),
            return_exceptions=True,
        )
        await super().close()

    async def _save_and_send_structural(self, opportunity: dict, event: dict | None):
        # R02 showed that the old complete-set certificate could advertise a locked
        # payout when every purchased YES leg loses. Keep discovery evidence, but do
        # not emit or paper-fill structural baskets until common-resolution proof is
        # replaced and independently accepted.
        self._v4_structural_suppressed_total += 1
        return False, None

    async def quarantine_observation_blind_history(self) -> dict:
        rows = await asyncio.to_thread(self._forecast_signal_rows)
        quarantined = 0
        errors: list[str] = []
        for row in rows:
            try:
                payload = _payload(row.get("payload_json"))
                if payload.get("paper_execution_protocol_version") == PAPER_EXECUTION_PROTOCOL_V4:
                    continue
                changed = await asyncio.to_thread(
                    self.positions.quarantine_signal,
                    int(row["id"]),
                    PRE_V4_QUARANTINE_REASON,
                )
                if changed:
                    quarantined += 1
            except Exception as exc:
                errors.append(
                    f"V4_HISTORY_QUARANTINE:{row.get('id')}:{type(exc).__name__}"
                )
        self._forecast_history_quarantined_total += quarantined
        self._quarantine_errors = errors[-20:]
        return {"quarantined_now": quarantined, "errors": errors}

    def _certified_forecast_events(self, events) -> list[tuple[dict, object]]:
        rows: list[tuple[str, dict, object]] = []
        for event in events:
            if not isinstance(event, dict):
                continue
            try:
                compiled = compile_strict_temperature_event(event)
            except StrictWeatherContractError:
                continue
            rows.append((compiled.event_id, event, compiled))
        rows.sort(key=lambda item: item[0])
        if not rows:
            return []

        cursor = self.positions.get_state("v4_forecast_cursor", "")
        start = 0
        if cursor:
            for index, (event_id, _, _) in enumerate(rows):
                if event_id > cursor:
                    start = index
                    break
            else:
                start = 0
        ordered = rows[start:] + rows[:start]
        # Return extra cheap-to-gate rows. Same-day rows are rejected in
        # _forecast_candidate before forecast/CLOB work and therefore cannot consume
        # the actual per-cycle eligible budget.
        cap = max(self.max_forecast_events * 4, self.max_forecast_events)
        return [(event, compiled) for _, event, compiled in ordered[:cap]]

    async def _station_local_eligibility(self, compiled):
        metadata = await self._station_metadata_for_compiled(compiled)
        if metadata is None:
            raise V4InvariantError("V4_STATION_METADATA_MISSING")
        local_today = self._local_date(self._now_epoch(), str(metadata.timezone))
        if compiled.target_date <= local_today:
            raise V4InvariantError("V4_RAW_GEFS_NOT_FUTURE_LOCAL_DAY")
        if (compiled.target_date - local_today).days > 3:
            raise V4InvariantError("V4_FORECAST_HORIZON_OUT_OF_RANGE")
        return metadata

    async def _mapped_forecast(self, event_id: str, compiled):
        metadata = await self._station_metadata_for_compiled(compiled)
        if metadata is None:
            raise V4InvariantError("V4_STATION_METADATA_MISSING")
        semantic = _semantic_digest(compiled, metadata)
        now_mono = time.monotonic()
        cached = self._forecast_cache.get(semantic)
        if cached is not None:
            age = now_mono - float(cached[0])
            if 0.0 <= age <= self.forecast_cache_seconds:
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
        distance = _haversine_km(
            float(metadata.latitude),
            float(metadata.longitude),
            float(distribution.resolved_latitude),
            float(distribution.resolved_longitude),
        )
        if distance > GRID_MAX_DISTANCE_KM:
            raise V4InvariantError("V4_FORECAST_GRID_TOO_FAR_FROM_STATION")
        forecast = map_ensemble_to_contract_buckets(
            compiled,
            distribution,
            FORECAST_MAPPING_POLICY,
        )
        self._forecast_distribution_by_sha[forecast.source_evidence_sha256] = distribution
        self._forecast_cache[semantic] = (now_mono, forecast)
        if len(self._forecast_cache) > 128:
            oldest = min(
                self._forecast_cache.items(), key=lambda item: float(item[1][0])
            )[0]
            self._forecast_cache.pop(oldest, None)
        if len(self._forecast_distribution_by_sha) > 256:
            for key in list(self._forecast_distribution_by_sha)[:64]:
                self._forecast_distribution_by_sha.pop(key, None)
        return forecast

    async def _forecast_candidate(self, event: dict, compiled) -> dict | None:
        try:
            # Recompile rather than trusting the object selected earlier.
            compiled = compile_strict_temperature_event(event)
            metadata = await self._station_local_eligibility(compiled)
        except (StrictWeatherContractError, V4InvariantError, WeatherStationMetadataError):
            self._forecast_same_day_suppressed_total += 1
            return None

        if self._v4_eligible_evaluated_this_cycle >= self.max_forecast_events:
            return None
        self._v4_eligible_evaluated_this_cycle += 1
        self.positions.set_state("v4_forecast_cursor", compiled.event_id)

        forecast = await self._mapped_forecast(compiled.event_id, compiled)
        exact = await self.runtime.clob.exact_event_snapshot(compiled)
        try:
            _validate_exact_snapshot(compiled, exact)
        except V4InvariantError as exc:
            # Live acceptance on 2026-09-15 proved that Polymarket can return an
            # otherwise well-formed exact snapshot whose provider timestamp is more
            # than the guarded skew bound behind our receipt time.  That snapshot is
            # still unusable for a PAPER decision, so fail closed for this event, but
            # do not classify a transient stale quote as a failure of the whole
            # collector cycle.  Semantic/identity invariants continue to propagate.
            if exc.code == "V4_BOOK_PROVIDER_TIMESTAMP_STALE":
                self._v4_forecast_nonfatal_skips_this_cycle.append(
                    f"FORECAST:{compiled.event_id}:{exc.code}"
                )
                return None
            raise
        distribution = self._forecast_distribution_by_sha.get(
            forecast.source_evidence_sha256
        )
        if distribution is None:
            raise V4InvariantError("V4_FORECAST_RAW_EVIDENCE_MISSING")

        bucket_by_market = {bucket.market_id: bucket for bucket in compiled.buckets}
        best: dict | None = None
        for row in forecast.bucket_frequencies:
            bucket = bucket_by_market.get(row.market_id)
            if bucket is None:
                continue
            params = exact.parameters.get(bucket.condition_id)
            if params is None or (
                params.fee_rate > 0.0 and params.taker_only is not True
            ):
                continue
            for side, token, frequency, hits in (
                (
                    "YES",
                    bucket.yes_token,
                    float(row.raw_member_frequency),
                    int(row.member_hits),
                ),
                (
                    "NO",
                    bucket.no_token,
                    float(row.raw_no_frequency),
                    int(row.member_count - row.member_hits),
                ),
            ):
                book = exact.books.get(token)
                if book is None or book.best_ask is None or book.best_ask_size <= 0:
                    continue
                ask = float(book.best_ask)
                fee = conservative_taker_fee_per_share(
                    ask, params.fee_rate, params.fee_exponent
                )
                cost = ask + fee
                gap = frequency - cost
                if gap < self.forecast_raw_gap_min:
                    continue
                label = (
                    f"≤ {bucket.upper:g}{compiled.unit}"
                    if bucket.lower is None
                    else f"≥ {bucket.lower:g}{compiled.unit}"
                    if bucket.upper is None
                    else f"{bucket.lower:g}–{bucket.upper:g}{compiled.unit}"
                )
                candidate = {
                    "lane": "weather_forecast_raw_gap",
                    "evidence_class": "RESEARCH_ONLY_UNCALIBRATED_V4",
                    "event_id": compiled.event_id,
                    "market_id": bucket.market_id,
                    "condition_id": bucket.condition_id,
                    "token_id": token,
                    "side": side,
                    "station": str(compiled.station_hint).upper(),
                    "station_timezone": str(metadata.timezone),
                    "target_date": compiled.target_date.isoformat(),
                    "family": compiled.family,
                    "unit": compiled.unit,
                    "bucket_label": label,
                    "raw_probability": frequency,
                    "member_hits": hits,
                    "member_count": int(row.member_count),
                    "ask": ask,
                    "fee": fee,
                    "entry_cost": cost,
                    "raw_gap": gap,
                    "forecast_evidence_sha256": forecast.source_evidence_sha256,
                    "forecast_mapping_policy": forecast.mapping_policy_id,
                    "forecast_run_id": forecast.source_evidence_sha256,
                    "forecast_run_age_known": False,
                    "forecast_raw_evidence": distribution.as_dict(),
                    "semantic_digest": _semantic_digest(compiled, metadata),
                    "calibrated_probability": False,
                    "execution_verified": False,
                    "financial_authority": False,
                    "automatic_order_placement": False,
                }
                if best is None or gap > float(best["raw_gap"]):
                    best = candidate
        return best

    @staticmethod
    def _target_midnight_epoch(target: date, timezone_name: str) -> float:
        zone = ZoneInfo(str(timezone_name))
        return datetime.combine(target, datetime_time.min, tzinfo=zone).timestamp()

    async def _dispatch_recheck(self, candidate: dict, event: dict) -> dict | None:
        compiled = compile_strict_temperature_event(event)
        metadata = await self._station_local_eligibility(compiled)
        semantic = _semantic_digest(compiled, metadata)
        if semantic != str(candidate.get("semantic_digest") or ""):
            raise V4InvariantError("V4_CONTRACT_CHANGED_BEFORE_DISPATCH")

        bucket = next(
            (
                row
                for row in compiled.buckets
                if row.market_id == str(candidate.get("market_id") or "")
            ),
            None,
        )
        if bucket is None:
            raise V4InvariantError("V4_MARKET_CHANGED_BEFORE_DISPATCH")
        side = str(candidate.get("side") or "")
        expected_token = bucket.yes_token if side == "YES" else bucket.no_token
        if expected_token != str(candidate.get("token_id") or ""):
            raise V4InvariantError("V4_TOKEN_MEANING_CHANGED_BEFORE_DISPATCH")

        exact = await self.runtime.clob.exact_event_snapshot(compiled)
        _validate_exact_snapshot(compiled, exact)
        params = exact.parameters.get(bucket.condition_id)
        book = exact.books.get(str(expected_token))
        if (
            params is None
            or book is None
            or book.best_ask is None
            or book.best_ask_size <= 0
        ):
            return None
        ask = float(book.best_ask)
        fee = conservative_taker_fee_per_share(
            ask, params.fee_rate, params.fee_exponent
        )
        cost = ask + fee
        gap = float(candidate["raw_probability"]) - cost
        if gap < self.forecast_raw_gap_min:
            return None

        # Enforce the actual executable price grid before a signal is saved.
        tick = float(params.minimum_tick_size)
        if tick <= 0 or abs(ask / tick - round(ask / tick)) > 1e-6:
            raise V4InvariantError("V4_ASK_OFF_TICK")

        quote_at = float(book.received_at)
        fill_at = float(exact.finished_at)
        midnight = self._target_midnight_epoch(
            compiled.target_date, str(metadata.timezone)
        )
        expires_at = min(
            fill_at + QUOTE_DECISION_TTL_SECONDS,
            midnight - LOCAL_MIDNIGHT_MARGIN_SECONDS,
        )
        if time.time() >= expires_at:
            return None

        fresh = dict(candidate)
        fresh.update({
            "ask": ask,
            "fee": fee,
            "entry_cost": cost,
            "raw_gap": gap,
            "ask_size": float(book.best_ask_size),
            "quote_observed_at": quote_at,
            "paper_fill_at": fill_at,
            "decision_expires_at": expires_at,
            "book_provider_timestamp": str(book.timestamp or ""),
            "book_hash": str(book.book_hash or ""),
            "minimum_order_size": float(params.minimum_order_size),
            "minimum_tick_size": tick,
            "clob_outcome_map": {
                str(token): str(outcome).strip().lower()
                for token, outcome in params.token_outcomes
            },
            "paper_target_stake_usd": float(self.paper_stake_usd),
            "paper_execution_protocol_version": PAPER_EXECUTION_PROTOCOL_V4,
            "event_title": _event_title(event, str(candidate["event_id"])),
            "event_url": _event_link(event),
            "paper_mode": True,
        })
        fresh["decision_id"] = _sha({
            "event_id": fresh["event_id"],
            "market_id": fresh["market_id"],
            "side": fresh["side"],
            "token_id": fresh["token_id"],
            "semantic_digest": fresh["semantic_digest"],
            "forecast_run_id": fresh["forecast_run_id"],
            "book_hash": fresh["book_hash"],
            "ask": fresh["ask"],
            "quote_observed_at": fresh["quote_observed_at"],
        })
        return fresh

    def _has_correlated_station_day_position(self, station: str, target_date: str) -> bool:
        count = 0
        with self.positions._conn() as db:
            rows = db.execute(
                """
                SELECT s.payload_json
                FROM weather_paper_positions p
                JOIN weather_paper_signals s ON s.id=p.signal_id
                WHERE p.validation_state='VALIDATED'
                  AND p.status IN ('OPEN','WON','LOST','RESOLVED_PARTIAL')
                """
            ).fetchall()
        for row in rows:
            payload = _payload(row["payload_json"])
            if (
                str(payload.get("station") or "").upper() == str(station).upper()
                and str(payload.get("target_date") or "") == str(target_date)
            ):
                count += 1
                if count >= MAX_CORRELATED_STATION_DAY_POSITIONS:
                    return True
        return False

    def _forecast_message_v4(self, candidate: dict) -> str:
        return "\n".join([
            "🚨 <b>PAPER WEATHER SIGNAL</b>",
            f"<b>{html.escape(str(candidate.get('event_title') or candidate['event_id']))}</b>",
            "",
            f"Simulated action: <b>BUY {html.escape(str(candidate['side']))} — {html.escape(str(candidate['bucket_label']))}</b>",
            f"Exact ask now: <b>${float(candidate['ask']):.4f}</b> + fee ${float(candidate['fee']):.5f}",
            f"Paper stake target: <b>${float(candidate['paper_target_stake_usd']):.2f}</b>",
            f"Visible shares at captured ask: <b>{float(candidate['ask_size']):.4f}</b>",
            "",
            f"GEFS members on this side: <b>{int(candidate['member_hits'])}/{int(candidate['member_count'])}</b>",
            f"Raw member frequency: <b>{100.0 * float(candidate['raw_probability']):.1f}%</b> (uncalibrated)",
            f"Raw frequency − executable cost: <b>{100.0 * float(candidate['raw_gap']):.1f} pts</b>",
            f"Station/date: <b>{html.escape(str(candidate['station']))} / {html.escape(str(candidate['target_date']))}</b>",
            "",
            "Quote + future-day eligibility were rechecked immediately before send.",
            "This snapshot expires quickly; expired/uncertain delivery is excluded from P&amp;L.",
            "⚠️ Member frequency is model evidence, not a calibrated win probability.",
            "📒 PAPER ONLY — no real order was placed.",
            f"ID: <code>{html.escape(str(candidate['decision_id'])[:12])}</code>",
        ])

    async def _record_skip(self, candidate: dict, reason: str) -> None:
        self._v4_dispatch_skipped_total += 1
        await asyncio.to_thread(
            self.positions.record_decision,
            decision_id=str(
                candidate.get("decision_id")
                or candidate.get("forecast_evidence_sha256")
                or candidate.get("event_id")
                or "unknown"
            ),
            event_id=str(candidate.get("event_id") or ""),
            market_id=str(candidate.get("market_id") or "") or None,
            side=str(candidate.get("side") or "") or None,
            outcome="SKIPPED",
            reason=str(reason),
        )

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
        if self._has_correlated_station_day_position(
            str(fresh["station"]), str(fresh["target_date"])
        ):
            await self._record_skip(fresh, "STATION_DAY_EXPOSURE_LIMIT")
            return False, None

        signal_id = self.positions.save_signal(
            fingerprint=str(fresh["decision_id"]),
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
            created_at=float(fresh["paper_fill_at"]),
            payload=fresh,
        )
        if signal_id is None:
            return False, None
        await asyncio.to_thread(
            self.positions.set_signal_status, signal_id, "PENDING_DELIVERY"
        )
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
            await asyncio.to_thread(
                self.positions.record_decision,
                decision_id=str(fresh["decision_id"]),
                event_id=str(fresh["event_id"]),
                market_id=str(fresh["market_id"]),
                side=str(fresh["side"]),
                outcome="DELIVERY_UNCERTAIN",
                reason=exc.code,
            )
            return True, exc.code
        except WeatherLivePaperError as exc:
            status = "EXPIRED" if "EXPIRED" in exc.code else "DELIVERY_FAILED"
            await asyncio.to_thread(
                self.positions.set_signal_status, signal_id, status
            )
            await self._record_skip(fresh, exc.code)
            return True, exc.code

        sent_at = time.time()
        self.positions.mark_telegram_sent(signal_id, int(message_id), sent_at=sent_at)
        if sent_at >= float(fresh["decision_expires_at"]):
            await asyncio.to_thread(
                self.positions.set_signal_status, signal_id, "EXPIRED"
            )
            await self._record_skip(fresh, "DELIVERY_RECEIPT_AFTER_EXPIRY")
            return True, "DELIVERY_RECEIPT_AFTER_EXPIRY"

        await asyncio.to_thread(
            self.positions.set_signal_status, signal_id, "ACKNOWLEDGED"
        )
        try:
            position = await asyncio.to_thread(
                self.positions.ensure_position_for_signal,
                signal_id,
                self.paper_stake_usd,
            )
        except (WeatherPaperPositionError, Exception) as exc:
            # The broad fallback is intentional at the accounting boundary: never let
            # a failed simulated-fill write become an implicitly valid position.
            await asyncio.to_thread(
                self.positions.set_signal_status, signal_id, "PAPER_ACCOUNTING_ERROR"
            )
            code = getattr(exc, "code", type(exc).__name__)
            await self._record_skip(fresh, f"PAPER_ACCOUNTING:{code}")
            return True, f"PAPER_ACCOUNTING:{code}"

        outcome = "OPENED" if position and position.get("status") == "OPEN" else "NO_FILL"
        reason = None if outcome == "OPENED" else str(
            (position or {}).get("no_fill_reason") or "NO_POSITION"
        )
        await asyncio.to_thread(
            self.positions.record_decision,
            decision_id=str(fresh["decision_id"]),
            event_id=str(fresh["event_id"]),
            market_id=str(fresh["market_id"]),
            side=str(fresh["side"]),
            outcome=outcome,
            reason=reason,
        )
        return True, None

    async def send_startup(self) -> int:
        release = self.release_sha()
        return await self.telegram.send_html("\n".join([
            "🟢 <b>WEATHER PAPER BOT ONLINE — V4 CORRECTIVE MODE</b>",
            "",
            "Future-day signals use strict contract semantics + dispatch-time exact CLOB rechecks.",
            "Old/unreconstructable rows are excluded from validated performance.",
            "Structural guaranteed-basket alerts: <b>OFF</b> pending strict common-resolution proof.",
            "Same-day conditioned alerts: <b>OFF</b> pending source/remaining-hours acceptance.",
            "",
            f"Paper stake target: <b>${self.paper_stake_usd:.2f}</b> per valid decision.",
            "Use /positions for what is open and /stats for wins/losses/P&amp;L.",
            "🚫 <b>NO REAL ORDERS</b>",
            f"Release: <code>{html.escape(release[:12])}</code>",
        ]))

    async def run_cycle(self) -> dict:
        self._v4_eligible_evaluated_this_cycle = 0
        self._v4_forecast_nonfatal_skips_this_cycle = []
        status = dict(await super().run_cycle())
        position_stats = await asyncio.to_thread(self.positions.stats)
        status.update({
            "version": WEATHER_LIVE_PAPER_V4_VERSION,
            "forecast_policy": "STRICT_FUTURE_LOCAL_DAY_RAW_GEFS_V4",
            "same_day_conditioned_policy": (
                "FOUNDATION_PRESENT_BUT_EMISSION_DISABLED_PENDING_ACCEPTANCE"
            ),
            "structural_policy": "DISABLED_PENDING_COMMON_RESOLUTION_PROOF",
            "structural_suppressed_total": self._v4_structural_suppressed_total,
            "dispatch_skipped_total": self._v4_dispatch_skipped_total,
            "forecast_nonfatal_skip_count": len(
                self._v4_forecast_nonfatal_skips_this_cycle
            ),
            "forecast_nonfatal_skips": list(
                self._v4_forecast_nonfatal_skips_this_cycle
            ),
            "eligible_forecast_events_evaluated": self._v4_eligible_evaluated_this_cycle,
            "paper_position_stats": position_stats,
            "financial_delivery": False,
            "financial_authority": False,
            "automatic_order_placement": False,
            "wallet_or_order_api_loaded": False,
        })
        _atomic_json(self.status_path, status)

        finished = float(status.get("finished_at") or time.time())
        if (
            self._v4_last_summary_sent_at == 0.0
            or finished - self._v4_last_summary_sent_at >= SUMMARY_INTERVAL_SECONDS
        ):
            stats = position_stats
            text = "\n".join([
                "📡 <b>WEATHER PAPER — HOURLY SUMMARY</b>",
                f"Bot health: <b>{'OK' if status.get('cycle_ok') else 'NEEDS ATTENTION'}</b>",
                f"Open: <b>{int(stats.get('open') or 0)}</b> paper trades (${float(stats.get('open_capital') or 0.0):.2f} at risk)",
                f"Finished: <b>{int(stats.get('won') or 0)}W / {int(stats.get('lost') or 0)}L / {int(stats.get('partial') or 0)} push/partial</b>",
                f"Net validated paper P&amp;L: <b>${float(stats.get('pnl') or 0.0):+.2f}</b>",
                f"No-fill: <b>{int(stats.get('no_fill') or 0)}</b> | skipped: <b>{int(stats.get('skipped') or 0)}</b> | delivery uncertain: <b>{int(stats.get('delivery_uncertain') or 0)}</b>",
                "Use /positions for open trades and /stats for the complete ledger.",
                "🚫 No real orders.",
            ])
            try:
                await self.telegram.send_html(text)
                self._v4_last_summary_sent_at = finished
            except WeatherLivePaperError as exc:
                status["errors"] = list(status.get("errors") or []) + [exc.code]
                status["cycle_ok"] = False
                _atomic_json(self.status_path, status)
        return status


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
