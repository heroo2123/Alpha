from __future__ import annotations

"""Live weather research signals with Telegram delivery and zero order authority.

This service deliberately sits between silent shadow and real-money delivery.  It
reads real Polymarket/weather data, emits clearly-labelled PAPER/RESEARCH Telegram
messages, and persists every candidate in an isolated SQLite database for later
analysis.  It imports no authenticated trading client and exposes no order/cancel
method.

Two lanes are enabled initially:

* exact-CLOB deterministic structural underrounds produced by the hardened
  WeatherOnlyShadowRuntime; and
* raw GEFS ensemble-vs-exact-CLOB gaps.  GEFS member frequency is explicitly
  UNCALIBRATED research evidence, never a certified probability or profit claim.
"""

import argparse
import asyncio
import hashlib
import html
import json
import math
import os
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx

from .config import settings
from .weather_only_clob import WeatherCLOBError, conservative_taker_fee_per_share
from .weather_only_contracts import DAILY_HIGH, DAILY_LOW, compile_weather_event
from .weather_only_forecast import (
    EnsembleMappingPolicy,
    OpenMeteoGEFSEnsembleClient,
    WeatherForecastError,
    map_ensemble_to_contract_buckets,
)
from .weather_only_paper_store import WeatherPaperStore
from .weather_only_rules import apply_rule_authority, compile_temperature_rule_authority
from .weather_only_runtime import WeatherOnlyShadowRuntime
from .weather_only_station_metadata import NWSStationMetadataClient, WeatherStationMetadataError


WEATHER_LIVE_PAPER_VERSION = "weather_live_paper_v1_structural_plus_raw_gefs_exact_clob"
MODE = "LIVE_PAPER_RESEARCH"
DEFAULT_INTERVAL_SECONDS = 180.0
DEFAULT_FORECAST_CACHE_SECONDS = 900.0
DEFAULT_FORECAST_RAW_GAP_MIN = 0.08
DEFAULT_MAX_FORECAST_EVENTS = 6
STRUCTURAL_FINGERPRINT_SECONDS = 1800
FORECAST_FINGERPRINT_SECONDS = 3600
SUMMARY_INTERVAL_SECONDS = 3600.0
FORECAST_MAPPING_POLICY = EnsembleMappingPolicy(
    policy_id="LIVE_PAPER_GEFS_ALL_31_NEAREST_WHOLE_V1",
    include_control=True,
)


class WeatherLivePaperError(RuntimeError):
    def __init__(self, code: str):
        self.code = str(code)
        super().__init__(self.code)


def _canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False)


def _fingerprint(identity: dict, *, bucket_seconds: int, at: float | None = None) -> str:
    now = time.time() if at is None else float(at)
    bucket = int(now // max(60, int(bucket_seconds)))
    body = {"identity": identity, "time_bucket": bucket}
    return hashlib.sha256(_canonical(body).encode("utf-8")).hexdigest()


def _event_id(event: object) -> str:
    return str(event.get("id") or "").strip() if isinstance(event, dict) else ""


def _event_link(event: dict | None) -> str | None:
    if not isinstance(event, dict):
        return None
    slug = str(event.get("slug") or "").strip()
    return f"https://polymarket.com/event/{slug}" if slug else None


def _event_title(event: dict | None, event_id: str) -> str:
    if isinstance(event, dict):
        title = str(event.get("title") or "").strip()
        if title:
            return title
    return event_id


def _atomic_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=path.parent,
            prefix=path.name + ".", suffix=".tmp", delete=False,
        ) as handle:
            temporary = handle.name
            os.chmod(temporary, 0o600)
            json.dump(payload, handle, sort_keys=True, indent=2, allow_nan=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        temporary = None
    finally:
        if temporary is not None:
            try:
                os.unlink(temporary)
            except OSError:
                pass


class PaperTelegram:
    """Telegram transport for non-financial paper/research messages only."""

    def __init__(self, *, token: str | None = None, chat_id: str | None = None) -> None:
        self.token = str(settings.telegram_bot_token if token is None else token).strip()
        self.chat_id = str(settings.telegram_chat_id if chat_id is None else chat_id).strip()
        if not self.token or not self.chat_id:
            raise WeatherLivePaperError("PAPER_TELEGRAM_NOT_CONFIGURED")
        self.http = httpx.AsyncClient(
            timeout=httpx.Timeout(20.0, connect=8.0),
            limits=httpx.Limits(max_connections=4, max_keepalive_connections=2, keepalive_expiry=60.0),
            trust_env=False,
        )

    async def close(self) -> None:
        await self.http.aclose()

    async def send_html(self, text: str, *, url: str | None = None) -> int:
        payload: dict[str, object] = {
            "chat_id": self.chat_id,
            "text": text[:3900],
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }
        if isinstance(url, str) and url.startswith("https://"):
            payload["reply_markup"] = {"inline_keyboard": [[{"text": "OPEN POLYMARKET", "url": url}]]}
        endpoint = f"https://api.telegram.org/bot{self.token}/sendMessage"
        for attempt in range(3):
            try:
                response = await self.http.post(endpoint, json=payload)
            except httpx.HTTPError:
                if attempt >= 2:
                    raise WeatherLivePaperError("PAPER_TELEGRAM_TRANSPORT") from None
                await asyncio.sleep(0.75 * (attempt + 1))
                continue
            if response.status_code == 429:
                retry_after = 1.0
                try:
                    retry_after = float((response.json().get("parameters") or {}).get("retry_after", 1.0))
                except Exception:
                    retry_after = 1.0
                if attempt >= 2:
                    raise WeatherLivePaperError("PAPER_TELEGRAM_RATE_LIMITED")
                await asyncio.sleep(min(15.0, max(1.0, retry_after)))
                continue
            if response.status_code >= 500:
                if attempt >= 2:
                    raise WeatherLivePaperError("PAPER_TELEGRAM_SERVER_ERROR")
                await asyncio.sleep(0.75 * (attempt + 1))
                continue
            if response.status_code >= 400:
                raise WeatherLivePaperError(f"PAPER_TELEGRAM_HTTP_{response.status_code}")
            try:
                body = response.json()
            except ValueError:
                raise WeatherLivePaperError("PAPER_TELEGRAM_RESPONSE_INVALID") from None
            result = body.get("result") if isinstance(body, dict) and body.get("ok") is True else None
            message_id = result.get("message_id") if isinstance(result, dict) else None
            if type(message_id) is not int:
                raise WeatherLivePaperError("PAPER_TELEGRAM_RECEIPT_MISSING")
            return message_id
        raise WeatherLivePaperError("PAPER_TELEGRAM_RETRY_EXHAUSTED")


class WeatherLivePaperService:
    def __init__(
        self,
        *,
        db_path: Path,
        status_path: Path,
        release_file: Path,
        interval_seconds: float = DEFAULT_INTERVAL_SECONDS,
        forecast_cache_seconds: float = DEFAULT_FORECAST_CACHE_SECONDS,
        forecast_raw_gap_min: float = DEFAULT_FORECAST_RAW_GAP_MIN,
        max_forecast_events: int = DEFAULT_MAX_FORECAST_EVENTS,
        runtime: WeatherOnlyShadowRuntime | None = None,
        station_client: NWSStationMetadataClient | None = None,
        forecast_client: OpenMeteoGEFSEnsembleClient | None = None,
        telegram: PaperTelegram | None = None,
        store: WeatherPaperStore | None = None,
    ) -> None:
        if not 30.0 <= float(interval_seconds) <= 3600.0:
            raise ValueError("interval_seconds must be in 30..3600")
        if not 60.0 <= float(forecast_cache_seconds) <= 21600.0:
            raise ValueError("forecast_cache_seconds must be in 60..21600")
        if not 0.0 <= float(forecast_raw_gap_min) <= 0.75:
            raise ValueError("forecast_raw_gap_min must be in 0..0.75")
        if not 1 <= int(max_forecast_events) <= 24:
            raise ValueError("max_forecast_events must be in 1..24")
        self.db_path = Path(db_path)
        self.status_path = Path(status_path)
        self.release_file = Path(release_file)
        self.interval_seconds = float(interval_seconds)
        self.forecast_cache_seconds = float(forecast_cache_seconds)
        self.forecast_raw_gap_min = float(forecast_raw_gap_min)
        self.max_forecast_events = int(max_forecast_events)
        self.runtime = runtime or WeatherOnlyShadowRuntime()
        self.station_client = station_client or NWSStationMetadataClient()
        self.forecast_client = forecast_client or OpenMeteoGEFSEnsembleClient()
        self.telegram = telegram or PaperTelegram()
        self.store = store or WeatherPaperStore(self.db_path)
        self._forecast_cache: dict[str, tuple[float, object]] = {}
        self._station_cache: dict[str, object] = {}
        self._last_summary_sent_at = 0.0

    def release_sha(self) -> str:
        try:
            value = self.release_file.read_text(encoding="utf-8").strip().lower()
        except (OSError, UnicodeError):
            raise WeatherLivePaperError("PAPER_RELEASE_MARKER_UNREADABLE") from None
        if len(value) != 40 or any(ch not in "0123456789abcdef" for ch in value):
            raise WeatherLivePaperError("PAPER_RELEASE_MARKER_INVALID")
        return value

    async def close(self) -> None:
        await asyncio.gather(
            self.runtime.close(), self.station_client.close(), self.forecast_client.close(), self.telegram.close(),
            return_exceptions=True,
        )

    async def send_startup(self) -> int:
        release = self.release_sha()
        text = (
            "🟢 <b>WEATHER LIVE PAPER ONLINE</b>\n\n"
            "Real Polymarket books + real weather/forecast data are being monitored.\n"
            "Telegram research signals are ON.\n\n"
            "🧪 <b>MODE:</b> LIVE PAPER / RESEARCH\n"
            "🚫 <b>NO ORDERS</b> — no wallet/order/cancel authority exists in this service.\n"
            "📊 Every emitted candidate is stored for later evaluation.\n\n"
            f"Release: <code>{html.escape(release[:12])}</code>"
        )
        return await self.telegram.send_html(text)

    def _structural_message(self, opportunity: dict, event: dict | None) -> str:
        asks = [float(x) for x in opportunity.get("ask_prices") or ()]
        fees = [float(x) for x in opportunity.get("conservative_fees_per_share") or ()]
        tokens = [str(x) for x in opportunity.get("token_ids") or ()]
        legs = []
        for index, (token, ask) in enumerate(zip(tokens, asks), 1):
            fee = fees[index - 1] if index - 1 < len(fees) else 0.0
            legs.append(f"{index}. ask {ask:.4f} + fee {fee:.5f} | token <code>{html.escape(token[:12])}…</code>")
        title = _event_title(event, str(opportunity.get("event_id") or ""))
        return "\n".join([
            "🧪 <b>WEATHER PAPER SIGNAL — DETERMINISTIC STRUCTURAL</b>",
            f"<b>{html.escape(title)}</b>",
            f"Lane: <code>{html.escape(str(opportunity.get('lane') or ''))}</code>",
            "",
            *legs,
            "",
            f"Total exact-book cost/set: <b>{float(opportunity.get('gross_cost_per_set') or 0.0):.4f}</b>",
            f"Locked payout if every leg fills: <b>${float(opportunity.get('locked_payout_per_set') or 0.0):.4f}</b>",
            f"Theoretical locked profit/set: <b>${float(opportunity.get('locked_profit_per_set') or 0.0):.4f}</b>",
            f"Common top-book capacity: <b>{float(opportunity.get('common_best_ask_shares') or 0.0):.2f} sets</b>",
            f"Theoretical profit at visible top level: <b>${float(opportunity.get('max_locked_profit_at_best_level') or 0.0):.2f}</b>",
            "",
            "⚠️ <b>PAPER ONLY.</b> Profit assumes every required leg fills at the quoted asks. No execution is inferred and no order is sent.",
        ])

    def _forecast_message(self, candidate: dict, event: dict | None) -> str:
        title = _event_title(event, str(candidate["event_id"]))
        unit = html.escape(str(candidate.get("unit") or ""))
        return "\n".join([
            "🔬 <b>WEATHER PAPER SIGNAL — UNCALIBRATED FORECAST</b>",
            f"<b>{html.escape(title)}</b>",
            f"Station/date: <b>{html.escape(str(candidate['station']))}</b> / {html.escape(str(candidate['target_date']))}",
            f"Bucket: <b>{html.escape(str(candidate['bucket_label']))}</b> | Side: <b>{html.escape(str(candidate['side']))}</b>",
            "",
            f"GEFS raw member frequency: <b>{100.0 * float(candidate['raw_probability']):.1f}%</b>",
            f"Exact CLOB ask: <b>{float(candidate['ask']):.4f}</b>",
            f"Conservative taker fee/share: <b>{float(candidate['fee']):.5f}</b>",
            f"Exact cost/share: <b>{float(candidate['entry_cost']):.4f}</b>",
            f"Raw model-vs-cost gap: <b>{100.0 * float(candidate['raw_gap']):+.1f} pp</b>",
            f"Visible ask size: <b>{float(candidate['ask_size']):.2f} shares</b>",
            "",
            f"Model: <code>GEFS 31-member daily extreme ({unit})</code>",
            "⚠️ <b>RESEARCH ONLY.</b> Raw ensemble frequency is NOT calibrated probability. This alert exists so we can measure whether these gaps actually predict profitable outcomes. No order is sent.",
        ])

    async def _save_and_send_structural(self, opportunity: dict, event: dict | None) -> tuple[bool, str | None]:
        now = time.time()
        event_id = str(opportunity.get("event_id") or "")
        identity = {
            "lane": opportunity.get("lane"),
            "event_id": event_id,
            "tokens": list(opportunity.get("token_ids") or ()),
        }
        fp = _fingerprint(identity, bucket_seconds=STRUCTURAL_FINGERPRINT_SECONDS, at=now)
        payload = dict(opportunity)
        payload.update({
            "paper_mode": True,
            "execution_verified": False,
            "source_timestamp": now,
            "event_title": _event_title(event, event_id),
            "event_url": _event_link(event),
            "financial_authority": False,
            "automatic_order_placement": False,
        })
        signal_id = self.store.save_signal(
            fingerprint=fp,
            lane=str(opportunity.get("lane") or "weather_structural"),
            evidence_class="DETERMINISTIC_EXECUTION_UNVERIFIED",
            event_id=event_id,
            payload=payload,
            entry_cost=float(opportunity.get("gross_cost_per_set") or 0.0),
            raw_gap=float(opportunity.get("locked_profit_per_set") or 0.0),
            theoretical_payout=float(opportunity.get("locked_payout_per_set") or 1.0),
            created_at=now,
        )
        if signal_id is None:
            return False, None
        try:
            message_id = await self.telegram.send_html(self._structural_message(opportunity, event), url=_event_link(event))
        except WeatherLivePaperError as exc:
            return True, exc.code
        self.store.mark_telegram_sent(signal_id, message_id)
        return True, None

    def _certified_forecast_events(self, events: tuple[dict, ...] | list[dict]) -> list[tuple[dict, object]]:
        today = datetime.now(timezone.utc).date()
        rows: list[tuple[int, str, dict, object]] = []
        for event in events:
            if not isinstance(event, dict):
                continue
            raw = compile_weather_event(event)
            if raw.family not in {DAILY_HIGH, DAILY_LOW}:
                continue
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
                continue
            delta = (compiled.target_date - today).days
            if delta < 0 or delta > 3:
                continue
            rows.append((delta, compiled.event_id, event, compiled))
        rows.sort(key=lambda row: (row[0], row[1]))
        return [(event, compiled) for _, _, event, compiled in rows[: self.max_forecast_events]]

    async def _mapped_forecast(self, event_id: str, compiled) -> object:
        cached = self._forecast_cache.get(event_id)
        now = time.time()
        if cached is not None and now - cached[0] <= self.forecast_cache_seconds:
            return cached[1]
        station_id = str(compiled.station_hint).upper()
        metadata = self._station_cache.get(station_id)
        if metadata is None:
            metadata = await self.station_client.station(station_id)
            self._station_cache[station_id] = metadata
        distribution = await self.forecast_client.daily_extreme(
            station=station_id,
            latitude=float(metadata.latitude),
            longitude=float(metadata.longitude),
            target_date=compiled.target_date,
            family=compiled.family,
            unit=compiled.unit,
            timezone=str(metadata.timezone),
        )
        forecast = map_ensemble_to_contract_buckets(compiled, distribution, FORECAST_MAPPING_POLICY)
        self._forecast_cache[event_id] = (now, forecast)
        return forecast

    async def _forecast_candidate(self, event: dict, compiled) -> dict | None:
        forecast = await self._mapped_forecast(compiled.event_id, compiled)
        exact = await self.runtime.clob.exact_event_snapshot(compiled)
        bucket_by_market = {bucket.market_id: bucket for bucket in compiled.buckets}
        best: dict | None = None
        for row in forecast.bucket_frequencies:
            bucket = bucket_by_market.get(row.market_id)
            if bucket is None:
                continue
            params = exact.parameters.get(bucket.condition_id)
            if params is None or (params.fee_rate > 0.0 and params.taker_only is not True):
                continue
            for side, token, probability in (
                ("YES", bucket.yes_token, float(row.raw_member_frequency)),
                ("NO", bucket.no_token, float(row.raw_no_frequency)),
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
                label = (
                    f"≤ {bucket.upper:g}{compiled.unit}" if bucket.lower is None
                    else f"≥ {bucket.lower:g}{compiled.unit}" if bucket.upper is None
                    else f"{bucket.lower:g}–{bucket.upper:g}{compiled.unit}"
                )
                candidate = {
                    "lane": "weather_forecast_raw_gap",
                    "evidence_class": "RESEARCH_ONLY_UNCALIBRATED",
                    "event_id": compiled.event_id,
                    "market_id": bucket.market_id,
                    "condition_id": bucket.condition_id,
                    "token_id": token,
                    "side": side,
                    "station": str(compiled.station_hint).upper(),
                    "target_date": compiled.target_date.isoformat(),
                    "family": compiled.family,
                    "unit": compiled.unit,
                    "bucket_label": label,
                    "raw_probability": probability,
                    "ask": ask,
                    "fee": fee,
                    "entry_cost": cost,
                    "raw_gap": gap,
                    "ask_size": float(book.best_ask_size),
                    "forecast_evidence_sha256": forecast.source_evidence_sha256,
                    "forecast_mapping_policy": forecast.mapping_policy_id,
                    "exact_clob": True,
                    "calibrated_probability": False,
                    "execution_verified": False,
                    "financial_authority": False,
                    "automatic_order_placement": False,
                }
                if best is None or candidate["raw_gap"] > best["raw_gap"]:
                    best = candidate
        return best

    async def _save_and_send_forecast(self, candidate: dict, event: dict) -> tuple[bool, str | None]:
        now = time.time()
        identity = {
            "lane": candidate["lane"],
            "event_id": candidate["event_id"],
            "market_id": candidate["market_id"],
            "side": candidate["side"],
        }
        fp = _fingerprint(identity, bucket_seconds=FORECAST_FINGERPRINT_SECONDS, at=now)
        payload = dict(candidate)
        payload.update({
            "paper_mode": True,
            "source_timestamp": now,
            "event_title": _event_title(event, str(candidate["event_id"])),
            "event_url": _event_link(event),
        })
        signal_id = self.store.save_signal(
            fingerprint=fp,
            lane=str(candidate["lane"]),
            evidence_class=str(candidate["evidence_class"]),
            event_id=str(candidate["event_id"]),
            market_id=str(candidate["market_id"]),
            side=str(candidate["side"]),
            token_id=str(candidate["token_id"]),
            model_probability=float(candidate["raw_probability"]),
            entry_cost=float(candidate["entry_cost"]),
            raw_gap=float(candidate["raw_gap"]),
            theoretical_payout=1.0,
            created_at=now,
            payload=payload,
        )
        if signal_id is None:
            return False, None
        try:
            message_id = await self.telegram.send_html(self._forecast_message(candidate, event), url=_event_link(event))
        except WeatherLivePaperError as exc:
            return True, exc.code
        self.store.mark_telegram_sent(signal_id, message_id)
        return True, None

    async def run_cycle(self) -> dict:
        started_wall = time.time()
        started = time.monotonic()
        errors: list[str] = []
        new_signals = 0
        sent_signals = 0

        structural_report = await self.runtime.run_cycle()
        if not structural_report.get("cycle_ok"):
            errors.extend(str(value) for value in structural_report.get("errors") or ["STRUCTURAL_RUNTIME_UNHEALTHY"])

        try:
            discovery = await self.runtime.discovery.discover()
            events = tuple(discovery.events)
        except Exception as exc:
            code = getattr(exc, "code", type(exc).__name__)
            errors.append(f"PAPER_DISCOVERY:{code}")
            events = tuple()

        by_id = {_event_id(event): event for event in events if _event_id(event)}
        structural_opportunities = list(structural_report.get("opportunities") or [])
        for opportunity in structural_opportunities:
            created, error = await self._save_and_send_structural(
                opportunity, by_id.get(str(opportunity.get("event_id") or ""))
            )
            if created:
                new_signals += 1
            if error is None and created:
                sent_signals += 1
            elif error:
                errors.append(error)

        forecast_events = self._certified_forecast_events(list(events))
        forecast_candidates = 0
        for event, compiled in forecast_events:
            try:
                candidate = await self._forecast_candidate(event, compiled)
            except (WeatherForecastError, WeatherStationMetadataError, WeatherCLOBError) as exc:
                errors.append(f"FORECAST:{compiled.event_id}:{exc.code}")
                continue
            except Exception as exc:
                errors.append(f"FORECAST:{compiled.event_id}:{type(exc).__name__}")
                continue
            if candidate is None:
                continue
            forecast_candidates += 1
            created, error = await self._save_and_send_forecast(candidate, event)
            if created:
                new_signals += 1
            if error is None and created:
                sent_signals += 1
            elif error:
                errors.append(error)

        summary = self.store.summary()
        finished = time.time()
        status = {
            "version": WEATHER_LIVE_PAPER_VERSION,
            "mode": MODE,
            "release_sha": self.release_sha(),
            "started_at": started_wall,
            "finished_at": finished,
            "cycle_seconds": time.monotonic() - started,
            "cycle_ok": not errors,
            "errors": errors,
            "discovered_weather_events": len(events),
            "structural_opportunity_count": len(structural_opportunities),
            "forecast_events_evaluated": len(forecast_events),
            "forecast_candidate_count": forecast_candidates,
            "new_signal_count": new_signals,
            "telegram_sent_count": sent_signals,
            "store": summary,
            "paper_telegram_delivery": True,
            "financial_delivery": False,
            "financial_authority": False,
            "automatic_order_placement": False,
            "wallet_or_order_api_loaded": False,
        }
        _atomic_json(self.status_path, status)

        if self._last_summary_sent_at == 0.0 or finished - self._last_summary_sent_at >= SUMMARY_INTERVAL_SECONDS:
            text = "\n".join([
                "📡 <b>WEATHER LIVE PAPER — CYCLE SUMMARY</b>",
                f"Cycle healthy: <b>{'YES' if status['cycle_ok'] else 'NO'}</b>",
                f"Weather events discovered: <b>{len(events)}</b>",
                f"Deterministic structural opportunities now: <b>{len(structural_opportunities)}</b>",
                f"Forecast events evaluated: <b>{len(forecast_events)}</b>",
                f"Raw forecast-gap candidates now: <b>{forecast_candidates}</b>",
                f"New paper signals this cycle: <b>{new_signals}</b>",
                f"Stored paper signals total: <b>{summary['total']}</b>",
                "",
                "🚫 No orders. No wallet trading. Signals are research evidence for later profitability review.",
            ])
            try:
                await self.telegram.send_html(text)
                self._last_summary_sent_at = finished
            except WeatherLivePaperError as exc:
                errors.append(exc.code)
                status["errors"] = errors
                status["cycle_ok"] = False
                _atomic_json(self.status_path, status)
        return status

    async def loop(self) -> None:
        await self.send_startup()
        while True:
            cycle_started = time.monotonic()
            try:
                await self.run_cycle()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                status = {
                    "version": WEATHER_LIVE_PAPER_VERSION,
                    "mode": MODE,
                    "release_sha": self.release_sha(),
                    "finished_at": time.time(),
                    "cycle_ok": False,
                    "errors": [f"UNHANDLED:{type(exc).__name__}"],
                    "paper_telegram_delivery": True,
                    "financial_delivery": False,
                    "financial_authority": False,
                    "automatic_order_placement": False,
                    "wallet_or_order_api_loaded": False,
                }
                _atomic_json(self.status_path, status)
            elapsed = time.monotonic() - cycle_started
            await asyncio.sleep(max(0.0, self.interval_seconds - elapsed))


async def _main(args) -> int:
    service = WeatherLivePaperService(
        db_path=args.db,
        status_path=args.status,
        release_file=args.release_file,
        interval_seconds=args.interval_seconds,
        forecast_cache_seconds=args.forecast_cache_seconds,
        forecast_raw_gap_min=args.forecast_raw_gap_min,
        max_forecast_events=args.max_forecast_events,
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
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    raise SystemExit(asyncio.run(_main(args)))


if __name__ == "__main__":
    main()
