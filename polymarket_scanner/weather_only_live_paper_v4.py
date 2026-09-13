from __future__ import annotations

"""Weather LIVE PAPER v4: P0 validated prospective experiment.

This runtime intentionally does less than v3 until the adversarial P0 invariants are
proved.  It disables complete-bucket structural alerts, keeps same-day whole-day GEFS
off, independently validates contract/station/date/bucket/token semantics, binds the
forecast cache to semantic evidence, performs a final exact-CLOB reprice immediately
before the paper decision, freezes the simulated execution protocol before Telegram,
and settles only from finalized on-chain CTF payouts.

No wallet, authenticated trading client, order, cancel, approval, signing or redemption
surface is imported.
"""

import argparse
import asyncio
import hashlib
import html
import json
import math
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx

from .weather_only_clob import WeatherCLOBError, conservative_taker_fee_per_share
from .weather_only_contracts import DAILY_HIGH, DAILY_LOW, compile_weather_event
from .weather_only_forecast import (
    FORECAST_MAPPING_POLICY if False else EnsembleMappingPolicy,  # type: ignore
)
from .weather_only_forecast import WeatherForecastError, map_ensemble_to_contract_buckets
from .weather_only_live_paper import (
    DEFAULT_FORECAST_CACHE_SECONDS,
    DEFAULT_FORECAST_RAW_GAP_MIN,
    DEFAULT_INTERVAL_SECONDS,
    DEFAULT_MAX_FORECAST_EVENTS,
    FORECAST_MAPPING_POLICY,
    MODE,
    _atomic_json,
    _event_id,
    _event_link,
    _event_title,
)
from .weather_only_live_paper_v2 import DEFAULT_PAPER_STAKE_USD, WeatherLivePaperV2Service
from .weather_only_p0_control import P0WeatherPaperCommandController, P0WeatherPaperSettlementEngine
from .weather_only_p0_guards import (
    DISPATCH_MIDNIGHT_MARGIN_SECONDS,
    WEATHER_P0_GUARD_VERSION,
    WeatherP0GuardError,
    assert_future_day_dispatch,
    semantic_digest,
    semantic_envelope,
    validate_clob_snapshot,
    validate_forecast_distribution,
)
from .weather_only_p0_positions import (
    DELIVERY_ACKNOWLEDGED,
    DELIVERY_EXPIRED,
    DELIVERY_PENDING,
    DELIVERY_SENDING,
    DELIVERY_UNCERTAIN,
    P0_COHORT_ID,
    P0WeatherPaperPositionStore,
    freeze_directional_execution,
)
from .weather_only_rules import apply_rule_authority, compile_temperature_rule_authority
from .weather_only_station_metadata import WeatherStationMetadataError


WEATHER_LIVE_PAPER_V4_VERSION = "weather_live_paper_v4_p0_validated_prospective_ctf_finality"
P0_FORECAST_CACHE_MAX = 64
P0_FORECAST_CACHE_SECONDS_MAX = 900.0
P0_STATION_CACHE_MAX = 64
P0_SUMMARY_INTERVAL_SECONDS = 3600.0
P0_STRUCTURAL_POLICY = "DISABLED_UNTIL_CANONICAL_RESOLUTION_FUNCTION_PROOF"
P0_FORECAST_POLICY = "RAW_WHOLE_DAY_GEFS_FUTURE_STATION_LOCAL_DAY_ONLY_RECHECKED_AT_DISPATCH"


class WeatherLivePaperV4Service(WeatherLivePaperV2Service):
    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        old_positions = self.positions
        old_settlement = self.settlement
        old_commands = self.commands
        self.positions = P0WeatherPaperPositionStore(self.db_path)
        self.settlement = P0WeatherPaperSettlementEngine(store=self.positions, telegram=self.telegram)
        self.commands = P0WeatherPaperCommandController(
            telegram=self.telegram,
            store=self.positions,
            status_path=self.status_path,
            paper_stake_usd=self.paper_stake_usd,
            expected_cycle_seconds=self.interval_seconds,
        )
        self._p0_superseded_positions = old_positions
        self._p0_superseded_settlement = old_settlement
        self._p0_superseded_commands = old_commands
        self._p0_forecast_cache: dict[str, tuple[float, object, object, object]] = {}
        self._p0_structural_suppressed_total = 0
        self._p0_guard_rejections: dict[str, int] = {}
        self._p0_last_summary_at = 0.0
        self._ensure_message_table()

    def _ensure_message_table(self) -> None:
        with self.positions._conn() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS weather_p0_delivery_messages (
                    signal_id INTEGER PRIMARY KEY,
                    message_html TEXT NOT NULL,
                    event_url TEXT,
                    created_at REAL NOT NULL,
                    FOREIGN KEY(signal_id) REFERENCES weather_paper_signals(id)
                );
                """
            )

    async def close(self) -> None:
        await asyncio.gather(
            self._p0_superseded_commands.close(),
            self._p0_superseded_settlement.close(),
            return_exceptions=True,
        )
        await super().close()

    def _guard_reject(self, code: str) -> None:
        key = str(code or "UNKNOWN")
        self._p0_guard_rejections[key] = int(self._p0_guard_rejections.get(key, 0)) + 1

    async def send_startup(self) -> int:
        release = self.release_sha()
        return await self.telegram.send_html("\n".join([
            "🟢 <b>WEATHER PAPER BOT — P0 VALIDATED MODE</b>",
            "",
            "A new prospective paper cohort is starting. Old/unverifiable rows remain in the database but do NOT count toward validated P&amp;L.",
            "",
            "✅ Contract/station/date/bucket/token semantics rechecked",
            "✅ Exact CLOB quote rechecked immediately before paper entry",
            "✅ Paper quantity frozen before Telegram delivery",
            "✅ Final results require finalized on-chain CTF payouts",
            "🛑 Same-day raw whole-day GEFS remains OFF",
            "🛑 Structural complete-set alerts remain OFF until the payout proof is rebuilt",
            "",
            "Commands: /status /stats /positions /history /recent /help",
            "🚫 <b>NO REAL ORDERS / NO WALLET AUTHORITY</b>",
            f"Release: <code>{html.escape(release[:12])}</code>",
        ]))

    async def _metadata(self, compiled):
        station = str(compiled.station_hint or "").strip().upper()
        cached = self._station_cache.get(station)
        if cached is not None:
            return cached
        value = await self.station_client.station(station)
        if len(self._station_cache) >= P0_STATION_CACHE_MAX:
            self._station_cache.clear()
        self._station_cache[station] = value
        return value

    async def _eligible_forecast_events(self, events: tuple[dict, ...]) -> tuple[list[tuple[dict, object, dict, str, object]], dict]:
        now = time.time()
        eligible: list[tuple[dict, object, dict, str, object]] = []
        counts = {
            "discovered": len(events),
            "temperature_compiled": 0,
            "semantic_validated": 0,
            "future_day_eligible": 0,
            "selected": 0,
            "same_day_or_expired": 0,
            "semantic_rejected": 0,
            "metadata_failed": 0,
        }
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
                or compiled.financial_authority
            ):
                counts["semantic_rejected"] += 1
                self._guard_reject("P0_BASE_CERTIFICATION_REJECTED")
                continue
            try:
                envelope = semantic_envelope(event, compiled)
                digest = semantic_digest(event, compiled)
            except WeatherP0GuardError as exc:
                counts["semantic_rejected"] += 1
                self._guard_reject(exc.code)
                continue
            counts["semantic_validated"] += 1
            try:
                metadata = await self._metadata(compiled)
                assert_future_day_dispatch(
                    target_date=compiled.target_date,
                    timezone_name=str(metadata.timezone),
                    now_epoch=now,
                    margin_seconds=max(DISPATCH_MIDNIGHT_MARGIN_SECONDS, 2.0 * self.interval_seconds),
                )
            except WeatherP0GuardError as exc:
                counts["same_day_or_expired"] += 1
                self._guard_reject(exc.code)
                continue
            except (WeatherStationMetadataError, ValueError) as exc:
                counts["metadata_failed"] += 1
                self._guard_reject(getattr(exc, "code", type(exc).__name__))
                continue
            counts["future_day_eligible"] += 1
            eligible.append((event, compiled, envelope, digest, metadata))

        eligible.sort(key=lambda row: (row[1].target_date, row[1].event_id))
        if len(eligible) > self.max_forecast_events:
            cursor = int(self.positions.get_state("p0_forecast_rotation_cursor", "0") or 0) % len(eligible)
            rotated = eligible[cursor:] + eligible[:cursor]
            selected = rotated[: self.max_forecast_events]
            self.positions.set_state(
                "p0_forecast_rotation_cursor",
                (cursor + self.max_forecast_events) % len(eligible),
            )
        else:
            selected = eligible
        counts["selected"] = len(selected)
        return selected, counts

    async def _p0_mapped_forecast(self, event, compiled, semantic_sha: str, metadata):
        metadata_digest = str(getattr(metadata, "evidence_sha256", "") or "")
        cache_key = hashlib.sha256(
            f"{semantic_sha}|{metadata_digest}|{FORECAST_MAPPING_POLICY.policy_id}".encode("utf-8")
        ).hexdigest()
        now_mono = time.monotonic()
        now_wall = time.time()
        cached = self._p0_forecast_cache.get(cache_key)
        ttl = min(self.forecast_cache_seconds, P0_FORECAST_CACHE_SECONDS_MAX)
        if cached is not None and 0.0 <= now_mono - cached[0] <= ttl:
            forecast, distribution, cached_metadata = cached[1], cached[2], cached[3]
            provenance = validate_forecast_distribution(
                compiled,
                distribution,
                timezone_name=str(cached_metadata.timezone),
                decision_time=now_wall,
            )
            return forecast, distribution, provenance

        distribution = await self.forecast_client.daily_extreme(
            station=str(compiled.station_hint).upper(),
            latitude=float(metadata.latitude),
            longitude=float(metadata.longitude),
            target_date=compiled.target_date,
            family=compiled.family,
            unit=compiled.unit,
            timezone=str(metadata.timezone),
        )
        provenance = validate_forecast_distribution(
            compiled,
            distribution,
            timezone_name=str(metadata.timezone),
            decision_time=time.time(),
        )
        forecast = map_ensemble_to_contract_buckets(compiled, distribution, FORECAST_MAPPING_POLICY)
        if forecast.source_evidence_sha256 != distribution.evidence_sha256:
            raise WeatherP0GuardError("P0_FORECAST_MAPPING_EVIDENCE_MISMATCH")
        if len(self._p0_forecast_cache) >= P0_FORECAST_CACHE_MAX:
            oldest = min(self._p0_forecast_cache, key=lambda key: self._p0_forecast_cache[key][0])
            self._p0_forecast_cache.pop(oldest, None)
        self._p0_forecast_cache[cache_key] = (now_mono, forecast, distribution, metadata)
        return forecast, distribution, provenance

    @staticmethod
    def _outcome_index(params, token: str, side: str) -> int:
        matches = [
            index
            for index, (candidate_token, outcome) in enumerate(params.token_outcomes)
            if str(candidate_token) == str(token) and str(outcome).strip().lower() == str(side).strip().lower()
        ]
        if len(matches) != 1 or matches[0] not in {0, 1}:
            raise WeatherP0GuardError("P0_CLOB_OUTCOME_INDEX_UNPROVEN")
        return int(matches[0])

    async def _forecast_candidate_p0(self, event, compiled, envelope: dict, semantic_sha: str, metadata) -> dict | None:
        now = time.time()
        expiry = assert_future_day_dispatch(
            target_date=compiled.target_date,
            timezone_name=str(metadata.timezone),
            now_epoch=now,
            margin_seconds=max(DISPATCH_MIDNIGHT_MARGIN_SECONDS, 2.0 * self.interval_seconds),
        )
        forecast, distribution, forecast_provenance = await self._p0_mapped_forecast(
            event, compiled, semantic_sha, metadata
        )
        exact = await self.runtime.clob.exact_event_snapshot(compiled)
        clob_provenance = validate_clob_snapshot(compiled, exact, decision_time=time.time())
        bucket_by_market = {bucket.market_id: bucket for bucket in compiled.buckets}
        best = None
        for row in forecast.bucket_frequencies:
            bucket = bucket_by_market.get(row.market_id)
            if bucket is None:
                continue
            params = exact.parameters.get(bucket.condition_id)
            if params is None or (params.fee_rate > 0.0 and params.taker_only is not True):
                continue
            for side, token, frequency in (
                ("YES", bucket.yes_token, float(row.raw_member_frequency)),
                ("NO", bucket.no_token, float(row.raw_no_frequency)),
            ):
                if not token:
                    continue
                book = exact.books.get(token)
                if book is None or book.best_ask is None or float(book.best_ask_size) <= 0.0:
                    continue
                ask = float(book.best_ask)
                fee = conservative_taker_fee_per_share(ask, params.fee_rate, params.fee_exponent)
                cost = ask + fee
                gap = frequency - cost
                if gap < self.forecast_raw_gap_min:
                    continue
                outcome_index = self._outcome_index(params, token, side)
                label = (
                    f"≤ {bucket.upper:g}{compiled.unit}" if bucket.lower is None
                    else f"≥ {bucket.lower:g}{compiled.unit}" if bucket.upper is None
                    else f"{bucket.lower:g}–{bucket.upper:g}{compiled.unit}"
                )
                provider_info = clob_provenance["books"][token]
                candidate = {
                    "lane": "weather_forecast_raw_gap",
                    "evidence_class": "P0_VALIDATED_RESEARCH_UNCALIBRATED",
                    "event_id": compiled.event_id,
                    "market_id": bucket.market_id,
                    "condition_id": bucket.condition_id,
                    "token_id": token,
                    "ctf_outcome_index": outcome_index,
                    "side": side,
                    "station": str(compiled.station_hint).upper(),
                    "station_timezone": str(metadata.timezone),
                    "station_metadata_evidence_sha256": str(getattr(metadata, "evidence_sha256", "")),
                    "target_date": compiled.target_date.isoformat(),
                    "family": compiled.family,
                    "unit": compiled.unit,
                    "bucket_label": label,
                    "market_question": bucket.question,
                    "member_hits": int(round(frequency * int(forecast.member_count))),
                    "member_count": int(forecast.member_count),
                    "raw_probability": frequency,
                    "ask": ask,
                    "fee": fee,
                    "entry_cost": cost,
                    "raw_gap": gap,
                    "ask_size": float(book.best_ask_size),
                    "minimum_order_size": float(params.minimum_order_size),
                    "minimum_tick_size": float(params.minimum_tick_size),
                    "quote_observed_at": float(book.received_at),
                    "provider_timestamp": float(provider_info["provider_timestamp"]),
                    "book_hash": str(book.book_hash or ""),
                    "semantic_digest": semantic_sha,
                    "semantic_envelope": envelope,
                    "forecast_evidence_sha256": distribution.evidence_sha256,
                    "forecast_mapping_policy": forecast.mapping_policy_id,
                    "forecast_provenance": forecast_provenance,
                    "decision_expires_at": expiry,
                    "exact_clob": True,
                    "calibrated_probability": False,
                    "model_run_initialization_proven": False,
                    "financial_authority": False,
                    "automatic_order_placement": False,
                    "__compiled": compiled,
                    "__metadata": metadata,
                }
                if best is None or candidate["raw_gap"] > best["raw_gap"]:
                    best = candidate
        return best

    async def _final_reprice(self, candidate: dict) -> tuple[dict, object]:
        compiled = candidate.get("__compiled")
        metadata = candidate.get("__metadata")
        if compiled is None or metadata is None:
            raise WeatherP0GuardError("P0_DECISION_CONTEXT_MISSING")
        now = time.time()
        expiry = assert_future_day_dispatch(
            target_date=compiled.target_date,
            timezone_name=str(metadata.timezone),
            now_epoch=now,
            margin_seconds=DISPATCH_MIDNIGHT_MARGIN_SECONDS,
        )
        exact = await self.runtime.clob.exact_event_snapshot(compiled)
        clob = validate_clob_snapshot(compiled, exact, decision_time=time.time())
        bucket = next((b for b in compiled.buckets if b.market_id == candidate["market_id"]), None)
        if bucket is None:
            raise WeatherP0GuardError("P0_FINAL_REPRICE_BUCKET_MISSING")
        params = exact.parameters.get(bucket.condition_id)
        book = exact.books.get(str(candidate["token_id"]))
        if params is None or book is None or book.best_ask is None or float(book.best_ask_size) <= 0.0:
            raise WeatherP0GuardError("P0_FINAL_REPRICE_BOOK_UNAVAILABLE")
        outcome_index = self._outcome_index(params, str(candidate["token_id"]), str(candidate["side"]))
        if outcome_index != int(candidate["ctf_outcome_index"]):
            raise WeatherP0GuardError("P0_FINAL_REPRICE_OUTCOME_IDENTITY_CHANGED")
        ask = float(book.best_ask)
        fee = conservative_taker_fee_per_share(ask, params.fee_rate, params.fee_exponent)
        cost = ask + fee
        gap = float(candidate["raw_probability"]) - cost
        if gap < self.forecast_raw_gap_min:
            raise WeatherP0GuardError("P0_FINAL_REPRICE_EDGE_EXPIRED")
        provider = clob["books"][str(candidate["token_id"])]
        updated = {key: value for key, value in candidate.items() if not str(key).startswith("__")}
        updated.update({
            "ask": ask,
            "fee": fee,
            "entry_cost": cost,
            "raw_gap": gap,
            "ask_size": float(book.best_ask_size),
            "minimum_order_size": float(params.minimum_order_size),
            "minimum_tick_size": float(params.minimum_tick_size),
            "quote_observed_at": float(book.received_at),
            "provider_timestamp": float(provider["provider_timestamp"]),
            "book_hash": str(book.book_hash or ""),
            "decision_expires_at": expiry,
            "final_clob_provenance": clob,
        })
        return updated, exact

    @staticmethod
    def _decision_identity(candidate: dict) -> str:
        body = {
            "lane": candidate["lane"],
            "semantic_digest": candidate["semantic_digest"],
            "forecast_evidence_sha256": candidate["forecast_evidence_sha256"],
            "market_id": candidate["market_id"],
            "token_id": candidate["token_id"],
            "side": candidate["side"],
        }
        return hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()

    @staticmethod
    def _human_forecast_message(candidate: dict, execution, stable_alert_id: str, event: dict) -> str:
        side = str(candidate["side"]).upper()
        bucket = str(candidate["bucket_label"])
        meaning = (
            f"the final {str(candidate['family']).replace('daily_', '').replace('_temperature','').replace('_',' ')} is IN {bucket}"
            if side == "YES"
            else f"the final daily {'high' if candidate['family']==DAILY_HIGH else 'low'} is NOT in {bucket}"
        )
        status_line = (
            f"Paper fill: <b>{execution.filled_units:.4f} shares</b> using <b>${execution.capital_used:.2f}</b>"
            if execution.fill_status == "OPEN"
            else f"Paper position: <b>SKIPPED / NO FILL</b> — {html.escape(str(execution.no_fill_reason or 'not executable under paper protocol'))}"
        )
        max_return = execution.maximum_payout
        max_profit = max_return - execution.capital_used
        return "\n".join([
            f"🔬 <b>WEATHER PAPER #{html.escape(stable_alert_id)}</b>",
            f"<b>{html.escape(_event_title(event, str(candidate['event_id'])))}</b>",
            "",
            f"👉 <b>PAPER ACTION: BUY {html.escape(side)}</b>",
            f"Meaning: {html.escape(meaning)}.",
            f"Bucket: <b>{html.escape(bucket)}</b>",
            f"Station: <b>{html.escape(str(candidate['station']))}</b> • target date: <b>{html.escape(str(candidate['target_date']))}</b> ({html.escape(str(candidate['station_timezone']))})",
            "",
            f"🌦 Model vote: <b>{int(candidate['member_hits'])} of {int(candidate['member_count'])} ensemble members</b> support {html.escape(side)}.",
            "This is an uncalibrated member frequency — NOT a guaranteed or calibrated probability.",
            "Model initialization time is not proven by the current provider adapter; this is recorded in the evidence.",
            "",
            f"💵 Exact rechecked ask: <b>{100.0*float(candidate['ask']):.3f}¢</b> + fee <b>{100.0*float(candidate['fee']):.3f}¢</b> per share",
            f"Total simulated entry cost/share: <b>${float(candidate['entry_cost']):.5f}</b>",
            f"Visible shares at that exact top level: <b>{float(candidate['ask_size']):.4f}</b>",
            status_line,
            *( [f"If the token pays $1: paper return <b>${max_return:.2f}</b> • max paper P&amp;L <b>${max_profit:+.2f}</b>"] if execution.fill_status == "OPEN" else [] ),
            "",
            f"Why it triggered: raw member frequency minus rechecked paper entry cost = <b>{100.0*float(candidate['raw_gap']):+.1f} percentage points</b>.",
            "🛡 Future-day only; contract/station/date/token semantics and quote freshness passed P0 guards at this decision.",
            "🚫 No real order was placed.",
        ])

    def _store_delivery_message(self, signal_id: int, text: str, event_url: str | None) -> None:
        with self.positions._conn() as db:
            db.execute(
                "INSERT OR REPLACE INTO weather_p0_delivery_messages(signal_id,message_html,event_url,created_at) VALUES(?,?,?,?)",
                (int(signal_id), str(text), event_url, time.time()),
            )

    def _load_delivery_message(self, signal_id: int) -> tuple[str, str | None] | None:
        with self.positions._conn() as db:
            row = db.execute(
                "SELECT message_html,event_url FROM weather_p0_delivery_messages WHERE signal_id=?",
                (int(signal_id),),
            ).fetchone()
        if not row:
            return None
        return str(row["message_html"]), (str(row["event_url"]) if row["event_url"] else None)

    async def _telegram_single_attempt(self, signal_id: int) -> tuple[bool, str | None]:
        outbox = self.positions.outbox_for_signal(signal_id)
        message = self._load_delivery_message(signal_id)
        if not outbox or not message:
            return False, "P0_OUTBOX_MESSAGE_MISSING"
        state = str(outbox.get("delivery_state") or "")
        if state == DELIVERY_ACKNOWLEDGED:
            return True, None
        if state == DELIVERY_UNCERTAIN:
            return False, "P0_DELIVERY_UNCERTAIN"
        now = time.time()
        if now >= float(outbox["decision_expires_at"]):
            self.positions.transition_delivery(signal_id, DELIVERY_EXPIRED, error="DECISION_EXPIRED_BEFORE_DELIVERY", at=now)
            return False, "P0_DELIVERY_EXPIRED"
        self.positions.transition_delivery(signal_id, DELIVERY_SENDING, at=now)
        text, url = message
        payload: dict[str, object] = {
            "chat_id": self.telegram.chat_id,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }
        if isinstance(url, str) and url.startswith("https://"):
            payload["reply_markup"] = {"inline_keyboard": [[{"text": "OPEN POLYMARKET", "url": url}]]}
        endpoint = f"https://api.telegram.org/bot{self.telegram.token}/sendMessage"
        try:
            response = await self.telegram.http.post(endpoint, json=payload)
        except httpx.HTTPError:
            # Timeout/transport errors after request dispatch are ambiguous: do not
            # retry and risk duplicate visible alerts.
            self.positions.transition_delivery(signal_id, DELIVERY_UNCERTAIN, error="TELEGRAM_TRANSPORT_AMBIGUOUS")
            return False, "P0_DELIVERY_UNCERTAIN"
        if response.status_code == 429:
            self.positions.transition_delivery(signal_id, DELIVERY_PENDING, error="TELEGRAM_RATE_LIMITED")
            return False, "P0_DELIVERY_RETRYABLE"
        if response.status_code >= 500:
            self.positions.transition_delivery(signal_id, DELIVERY_UNCERTAIN, error=f"TELEGRAM_HTTP_{response.status_code}_AMBIGUOUS")
            return False, "P0_DELIVERY_UNCERTAIN"
        if response.status_code >= 400:
            self.positions.transition_delivery(signal_id, DELIVERY_EXPIRED, error=f"TELEGRAM_HTTP_{response.status_code}_REJECTED")
            return False, "P0_DELIVERY_REJECTED"
        try:
            body = response.json()
        except ValueError:
            self.positions.transition_delivery(signal_id, DELIVERY_UNCERTAIN, error="TELEGRAM_RESPONSE_INVALID")
            return False, "P0_DELIVERY_UNCERTAIN"
        result = body.get("result") if isinstance(body, dict) and body.get("ok") is True else None
        message_id = result.get("message_id") if isinstance(result, dict) else None
        if type(message_id) is not int:
            self.positions.transition_delivery(signal_id, DELIVERY_UNCERTAIN, error="TELEGRAM_RECEIPT_MISSING")
            return False, "P0_DELIVERY_UNCERTAIN"
        ack_at = time.time()
        self.positions.transition_delivery(
            signal_id,
            DELIVERY_ACKNOWLEDGED,
            message_id=message_id,
            at=ack_at,
        )
        self.store.mark_telegram_sent(signal_id, message_id, sent_at=ack_at)
        await asyncio.to_thread(self.positions.ensure_position_for_signal, signal_id)
        return True, None

    async def _recover_outbox(self) -> dict:
        # A process crash while SENDING has an unknowable remote outcome. Never retry
        # it as if unsent; make the uncertainty explicit and exclude it from positions.
        with self.positions._conn() as db:
            sending = [int(row[0]) for row in db.execute(
                "SELECT signal_id FROM weather_p0_delivery_outbox WHERE delivery_state=?",
                (DELIVERY_SENDING,),
            )]
            pending = [int(row[0]) for row in db.execute(
                "SELECT signal_id FROM weather_p0_delivery_outbox WHERE delivery_state=? ORDER BY signal_id LIMIT 20",
                (DELIVERY_PENDING,),
            )]
            acknowledged = [dict(row) for row in db.execute(
                """
                SELECT o.signal_id,o.telegram_message_id,o.acknowledged_at,s.telegram_message_id AS signal_message
                FROM weather_p0_delivery_outbox o
                JOIN weather_paper_signals s ON s.id=o.signal_id
                WHERE o.delivery_state=?
                """,
                (DELIVERY_ACKNOWLEDGED,),
            )]
        for sid in sending:
            self.positions.transition_delivery(sid, DELIVERY_UNCERTAIN, error="PROCESS_RESTARTED_FROM_SENDING")
        reconciled = 0
        for row in acknowledged:
            if row.get("signal_message") is None and row.get("telegram_message_id") is not None:
                self.store.mark_telegram_sent(
                    int(row["signal_id"]),
                    int(row["telegram_message_id"]),
                    sent_at=float(row.get("acknowledged_at") or time.time()),
                )
                reconciled += 1
            await asyncio.to_thread(self.positions.ensure_position_for_signal, int(row["signal_id"]))
        retried = 0
        for sid in pending:
            ok, _ = await self._telegram_single_attempt(sid)
            retried += int(ok)
        return {"sending_to_uncertain": len(sending), "ack_reconciled": reconciled, "pending_acknowledged": retried}

    async def _save_and_send_p0_forecast(self, candidate: dict, event: dict) -> tuple[bool, bool, str | None]:
        repriced, _ = await self._final_reprice(candidate)
        freeze_time = time.time()
        assert_future_day_dispatch(
            target_date=datetime.fromisoformat(str(repriced["target_date"])).date(),
            timezone_name=str(repriced["station_timezone"]),
            now_epoch=freeze_time,
            margin_seconds=DISPATCH_MIDNIGHT_MARGIN_SECONDS,
        )
        execution = freeze_directional_execution(
            frozen_at=freeze_time,
            decision_expires_at=float(repriced["decision_expires_at"]),
            target_stake_usd=self.paper_stake_usd,
            market_id=str(repriced["market_id"]),
            condition_id=str(repriced["condition_id"]),
            token_id=str(repriced["token_id"]),
            side=str(repriced["side"]),
            ask=float(repriced["ask"]),
            fee_per_share=float(repriced["fee"]),
            visible_units=float(repriced["ask_size"]),
            minimum_order_size=float(repriced["minimum_order_size"]),
            minimum_tick_size=float(repriced["minimum_tick_size"]),
            quote_observed_at=float(repriced["quote_observed_at"]),
            provider_timestamp=float(repriced["provider_timestamp"]),
            book_hash=str(repriced["book_hash"]),
        )
        fingerprint = self._decision_identity(repriced)
        payload = dict(repriced)
        payload.update({
            "paper_mode": True,
            "source_timestamp": float(repriced["quote_observed_at"]),
            "event_title": _event_title(event, str(repriced["event_id"])),
            "event_url": _event_link(event),
            "release_sha": self.release_sha(),
            "p0_guard_version": WEATHER_P0_GUARD_VERSION,
            "p0_cohort_id": P0_COHORT_ID,
            "paper_execution_protocol": execution.as_dict(),
        })
        signal_id = self.store.save_signal(
            fingerprint=fingerprint,
            lane=str(repriced["lane"]),
            evidence_class=str(repriced["evidence_class"]),
            event_id=str(repriced["event_id"]),
            market_id=str(repriced["market_id"]),
            side=str(repriced["side"]),
            token_id=str(repriced["token_id"]),
            model_probability=float(repriced["raw_probability"]),
            entry_cost=float(repriced["entry_cost"]),
            raw_gap=float(repriced["raw_gap"]),
            theoretical_payout=1.0,
            created_at=freeze_time,
            payload=payload,
        )
        if signal_id is None:
            return False, False, None
        decision_envelope = {
            "version": WEATHER_LIVE_PAPER_V4_VERSION,
            "semantic": repriced["semantic_envelope"],
            "semantic_digest": repriced["semantic_digest"],
            "forecast_provenance": repriced["forecast_provenance"],
            "forecast_evidence_sha256": repriced["forecast_evidence_sha256"],
            "forecast_mapping_policy": repriced["forecast_mapping_policy"],
            "final_clob_provenance": repriced["final_clob_provenance"],
            "selected_market_id": repriced["market_id"],
            "selected_condition_id": repriced["condition_id"],
            "selected_token_id": repriced["token_id"],
            "selected_side": repriced["side"],
            "ctf_outcome_index": repriced["ctf_outcome_index"],
            "raw_member_hits": repriced["member_hits"],
            "raw_member_count": repriced["member_count"],
            "raw_member_frequency": repriced["raw_probability"],
            "calibrated_probability": False,
            "release_sha": self.release_sha(),
            "financial_authority": False,
        }
        self.positions.record_validated_decision(
            signal_id,
            release_sha=self.release_sha(),
            semantic_digest=str(repriced["semantic_digest"]),
            decision_envelope=decision_envelope,
            execution=execution,
        )
        stable_id = f"WP{signal_id:06d}"
        text = self._human_forecast_message(repriced, execution, stable_id, event)
        message_sha = hashlib.sha256(text.encode("utf-8")).hexdigest()
        self.positions.create_outbox(
            signal_id,
            stable_alert_id=stable_id,
            message_sha256=message_sha,
            decision_expires_at=float(repriced["decision_expires_at"]),
        )
        self._store_delivery_message(signal_id, text, _event_link(event))
        acknowledged, error = await self._telegram_single_attempt(signal_id)
        return True, acknowledged, error

    async def run_cycle(self) -> dict:
        started_wall = time.time()
        started_mono = time.monotonic()
        errors: list[str] = []
        history = await asyncio.to_thread(self.positions.quarantine_unvalidated_history)
        recovery = await self._recover_outbox()

        try:
            discovery = await self.runtime.discovery.discover()
            events = tuple(event for event in discovery.events if isinstance(event, dict))
        except Exception as exc:
            errors.append(f"DISCOVERY:{getattr(exc, 'code', type(exc).__name__)}")
            events = tuple()

        selected, coverage = await self._eligible_forecast_events(events)
        created = 0
        acknowledged = 0
        no_fill = 0
        evaluated = 0
        for event, compiled, envelope, sem_digest, metadata in selected:
            try:
                candidate = await self._forecast_candidate_p0(event, compiled, envelope, sem_digest, metadata)
                evaluated += 1
            except (WeatherForecastError, WeatherStationMetadataError, WeatherCLOBError, WeatherP0GuardError) as exc:
                self._guard_reject(getattr(exc, "code", type(exc).__name__))
                continue
            except Exception as exc:
                errors.append(f"FORECAST:{compiled.event_id}:{type(exc).__name__}")
                continue
            if candidate is None:
                continue
            try:
                was_created, was_ack, send_error = await self._save_and_send_p0_forecast(candidate, event)
            except (WeatherP0GuardError, WeatherCLOBError) as exc:
                self._guard_reject(getattr(exc, "code", type(exc).__name__))
                continue
            except Exception as exc:
                errors.append(f"DECISION:{compiled.event_id}:{type(exc).__name__}")
                continue
            created += int(was_created)
            acknowledged += int(was_ack)
            if send_error and send_error not in {"P0_DELIVERY_RETRYABLE", "P0_DELIVERY_UNCERTAIN"}:
                errors.append(send_error)

        # Any positions created from ACKs are based only on the frozen decision-time
        # protocol.  No current configuration or later quote is used for backfill.
        await asyncio.to_thread(self.positions.ensure_sent_positions, None)
        try:
            settlement = await self.settlement.settle_once()
        except Exception as exc:
            settlement = {"errors": [f"SETTLEMENT_UNHANDLED:{type(exc).__name__}"], "open_checked": 0, "resolved_now": 0}
        errors.extend(str(value) for value in settlement.get("errors") or [])
        stats = await asyncio.to_thread(self.positions.stats)
        no_fill = int(stats.get("no_fill") or 0)
        finished = time.time()
        status = {
            "version": WEATHER_LIVE_PAPER_V4_VERSION,
            "mode": MODE,
            "release_sha": self.release_sha(),
            "started_at": started_wall,
            "finished_at": finished,
            "cycle_seconds": time.monotonic() - started_mono,
            "expected_cycle_interval_seconds": self.interval_seconds,
            "cycle_ok": not errors,
            "errors": errors,
            "structural_policy": P0_STRUCTURAL_POLICY,
            "structural_opportunity_count": 0,
            "structural_suppressed_total": self._p0_structural_suppressed_total,
            "forecast_policy": P0_FORECAST_POLICY,
            "forecast_coverage": coverage,
            "forecast_events_evaluated": evaluated,
            "new_signal_count": created,
            "telegram_acknowledged_count": acknowledged,
            "history_validation": history,
            "outbox_recovery": recovery,
            "paper_position_stats": stats,
            "paper_settlement": settlement,
            "guard_rejections_total": sum(self._p0_guard_rejections.values()),
            "guard_rejections_by_code": dict(sorted(self._p0_guard_rejections.items())),
            "paper_target_stake_usd": self.paper_stake_usd,
            "paper_telegram_delivery": True,
            "financial_delivery": False,
            "financial_authority": False,
            "automatic_order_placement": False,
            "wallet_or_order_api_loaded": False,
        }
        _atomic_json(self.status_path, status)

        if self._p0_last_summary_at == 0.0 or finished - self._p0_last_summary_at >= P0_SUMMARY_INTERVAL_SECONDS:
            summary = "\n".join([
                "📡 <b>WEATHER PAPER — HOURLY SUMMARY</b>",
                f"Bot health: <b>{'OK' if status['cycle_ok'] else 'DEGRADED'}</b>",
                f"Eligible future-day markets checked: <b>{evaluated}</b>",
                f"New validated signals: <b>{created}</b> • Telegram acknowledged: <b>{acknowledged}</b>",
                f"Open paper positions now: <b>{int(stats.get('open') or 0)}</b>",
                f"Finished: <b>{int(stats.get('won') or 0)} won / {int(stats.get('lost') or 0)} lost / {int(stats.get('partial') or 0)} partial</b>",
                f"No-fill: <b>{no_fill}</b> • quarantined/excluded: <b>{int(stats.get('quarantined') or 0)}</b>",
                f"Realized paper P&amp;L: <b>${float(stats.get('pnl') or 0.0):+.2f}</b>",
                "",
                "Structural complete-set alerts: OFF pending proof rebuild. Same-day raw GEFS: OFF pending observation-conditioned model.",
                "🚫 No real orders.",
            ])
            try:
                await self.telegram.send_html(summary)
                self._p0_last_summary_at = finished
            except Exception as exc:
                status["cycle_ok"] = False
                status["errors"] = list(status["errors"]) + [f"SUMMARY:{type(exc).__name__}"]
                _atomic_json(self.status_path, status)
        return status

    async def loop(self) -> None:
        await self.send_startup()
        await asyncio.to_thread(self.positions.quarantine_unvalidated_history)
        await self._recover_outbox()
        command_task = asyncio.create_task(self.commands.loop())
        try:
            while True:
                started = time.monotonic()
                try:
                    await self.run_cycle()
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    _atomic_json(self.status_path, {
                        "version": WEATHER_LIVE_PAPER_V4_VERSION,
                        "mode": MODE,
                        "release_sha": self.release_sha(),
                        "finished_at": time.time(),
                        "cycle_ok": False,
                        "errors": [f"UNHANDLED:{type(exc).__name__}"],
                        "structural_policy": P0_STRUCTURAL_POLICY,
                        "forecast_policy": P0_FORECAST_POLICY,
                        "financial_delivery": False,
                        "financial_authority": False,
                        "automatic_order_placement": False,
                        "wallet_or_order_api_loaded": False,
                    })
                await asyncio.sleep(max(0.0, self.interval_seconds - (time.monotonic() - started)))
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
