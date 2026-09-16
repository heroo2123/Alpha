from __future__ import annotations

"""All-weather PAPER runtime v3 with prospective maker lifecycle.

V3 preserves the already-reviewed future-day, same-day and structural PAPER lanes and
adds one new research lane: uncalibrated passive maker bids.  A maker Telegram alert
is only an invitation to a hypothetical experiment.  The virtual order is activated
*after* Telegram acknowledgement, only if a fresh exact-CLOB recheck still produces
the same non-crossing bid and a continuously covered public Market-WebSocket epoch was
already active.  A book touch is never a fill; only causal public SELL-aggressor trade
prints can consume visible queue and create simulated shares.

Any WebSocket gap cancels still-resting virtual orders.  Partial simulated inventory is
preserved and later settled from the same public Gamma exact-token payout authority
used by the ordinary PAPER ledger.  Nothing here imports or calls an authenticated
order, wallet, signing or cancellation API.
"""

import argparse
import asyncio
import html
import json
import math
import time
from dataclasses import replace
from pathlib import Path

from .settlement import exact_token_payout
from .weather_only_clob import WeatherCLOBError
from .weather_only_contract_strict import (
    StrictWeatherContractError,
    compile_strict_temperature_event,
    strict_contract_identity,
)
from .weather_only_forecast import WeatherForecastError
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
from .weather_only_live_paper_all_signals_v2 import (
    ALL_PAPER_V2_RUNTIME_VERSION,
    AllPaperWeatherLiveV2Service,
)
from .weather_only_live_paper_v2 import DEFAULT_PAPER_STAKE_USD
from .weather_only_live_paper_v4 import (
    QUOTE_DECISION_TTL_SECONDS,
    V4InvariantError,
    _sha,
    _validate_exact_snapshot,
)
from .weather_only_maker import FairValueBand, MakerBidProposal, propose_maker_bid
from .weather_only_maker_paper_accounting import (
    MAKER_PAPER_ACCOUNTING_VERSION,
    MAKER_SETTLEMENT_EVENT,
    MakerPaperAccountingStore,
)
from .weather_only_maker_research_policy import (
    MAKER_RESEARCH_POLICY_VERSION,
    MakerResearchPolicy,
    MakerResearchPolicyError,
    build_uncalibrated_gefs_fair_band,
    fair_band_research_metadata,
)
from .weather_only_maker_shadow import (
    CANCELLED,
    EXPIRED,
    PARTIALLY_SIMULATED,
    RESTING,
    SIMULATED_FILLED,
    MakerFillSimulation,
    VirtualMakerOrder,
    WeatherMakerShadowError,
    apply_cancel_decision,
    create_virtual_maker_order,
    evaluate_virtual_order,
    simulate_public_trade_progression,
)
from .weather_only_maker_store import WeatherMakerStoreError
from .weather_only_maker_trade_stream import MakerTradeStreamError
from .weather_only_maker_trade_stream_v2 import ProspectiveMakerTradeStreamV2
from .weather_only_paper_control import PublicGammaSettlementClient
from .weather_only_paper_corrective import DeliveryUncertain
from .weather_only_station_metadata import WeatherStationMetadataError


ALL_PAPER_V3_RUNTIME_VERSION = "weather_all_paper_signals_v3_prospective_maker_ws_fill"
MAKER_LANE = "weather_maker_virtual_bid"
MAKER_EVIDENCE_CLASS = "UNCALIBRATED_GEFS31_MINUS3_PROSPECTIVE_WS_FILL_V1"
MAKER_CURSOR_KEY = "all_paper_v3_maker_event_cursor"
MAKER_SETTLEMENT_NOTIFY_SENT = "PAPER_SETTLEMENT_TELEGRAM_SENT"
MAKER_SETTLEMENT_NOTIFY_UNCERTAIN = "PAPER_SETTLEMENT_TELEGRAM_UNCERTAIN"


class AllPaperV3Error(RuntimeError):
    def __init__(self, code: str):
        self.code = str(code)
        super().__init__(self.code)


def _same_number(left: object, right: object, tolerance: float = 1e-10) -> bool:
    try:
        a, b = float(left), float(right)
    except (TypeError, ValueError, OverflowError):
        return False
    return math.isfinite(a) and math.isfinite(b) and abs(a - b) <= tolerance


def _order_ws_generation(order: VirtualMakerOrder) -> int:
    marker = "|ws_generation="
    source = str(order.source_generation or "")
    if marker not in source:
        raise AllPaperV3Error("MAKER_ORDER_WS_GENERATION_MISSING")
    raw = source.rsplit(marker, 1)[1]
    try:
        value = int(raw)
    except (TypeError, ValueError, OverflowError):
        raise AllPaperV3Error("MAKER_ORDER_WS_GENERATION_INVALID") from None
    if value <= 0:
        raise AllPaperV3Error("MAKER_ORDER_WS_GENERATION_INVALID")
    return value


class AllPaperWeatherLiveV3Service(AllPaperWeatherLiveV2Service):
    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self.maker_policy = MakerResearchPolicy()
        self.maker_shadow_policy = self.maker_policy.shadow_policy()
        self.maker_store = MakerPaperAccountingStore(self.db_path)
        # A process restart necessarily lost prospective WS prints.  Preserve any
        # partial inventory, but never pretend an old resting order remained observed.
        self._maker_restart_cancelled = self.maker_store.cancel_open_after_restart()
        self.maker_stream = ProspectiveMakerTradeStreamV2()
        self._maker_gamma = PublicGammaSettlementClient()
        self._maker_proposals_sent = 0
        self._maker_orders_activated = 0
        self._maker_simulated_fill_events = 0
        self._maker_settlements_recorded = 0
        self._maker_last_errors: list[str] = []

    async def close(self) -> None:
        await asyncio.gather(
            self.maker_stream.close(),
            self._maker_gamma.close(),
            return_exceptions=True,
        )
        try:
            self.maker_store.close()
        finally:
            await super().close()

    async def send_startup(self) -> int:
        release = self.release_sha()
        return await self.telegram.send_html("\n".join([
            "🟢 <b>WEATHER ALL-PAPER BOT V3 ONLINE</b>",
            "",
            "Future-day forecast PAPER alerts: <b>ON</b>",
            "Same-day late-lock PAPER alerts: <b>ON — uncalibrated</b>",
            "Hardened structural underround PAPER alerts: <b>ON</b>",
            "Maker PAPER bids: <b>ON when prospective public-WS coverage is proven</b>",
            "Result-lag: <b>still gated — exact WRH cutoff state is not proven</b>",
            "",
            "Maker fills are simulated only from causal public SELL trade prints after queue-ahead consumption.",
            "A book touch alone is never counted as a fill.",
            "Every simulated maker fill is later scored from exact public token payout.",
            "🚫 <b>NO REAL ORDERS / NO WALLET OR SIGNING AUTHORITY</b>",
            f"Release: <code>{html.escape(release[:12])}</code>",
        ]))

    def _maker_event_order(self, events: tuple[dict, ...]) -> list[tuple[str, dict, object]]:
        rows: list[tuple[str, dict, object]] = []
        for event in events:
            if not isinstance(event, dict):
                continue
            try:
                compiled = compile_strict_temperature_event(event)
            except StrictWeatherContractError:
                continue
            rows.append((compiled.event_id, event, compiled))
        rows.sort(key=lambda row: row[0])
        if not rows:
            return []
        cursor = self.positions.get_state(MAKER_CURSOR_KEY, "")
        start = 0
        if cursor:
            for index, (event_id, _event, _compiled) in enumerate(rows):
                if event_id > cursor:
                    start = index
                    break
            else:
                start = 0
        return rows[start:] + rows[:start]

    @staticmethod
    def _bucket_for_market(compiled, market_id: str):
        return next(
            (bucket for bucket in compiled.buckets if bucket.market_id == str(market_id)),
            None,
        )

    @staticmethod
    def _forecast_row_for_market(forecast, market_id: str):
        return next(
            (row for row in forecast.bucket_frequencies if row.market_id == str(market_id)),
            None,
        )

    def _fair_for_side(
        self,
        *,
        token_id: str,
        side: str,
        forecast_row,
        forecast_evidence_sha256: str,
        forecast_received_at: float,
    ) -> FairValueBand:
        wanted = str(side).upper()
        if wanted == "YES":
            hits = int(forecast_row.member_hits)
        elif wanted == "NO":
            hits = int(forecast_row.member_count - forecast_row.member_hits)
        else:
            raise AllPaperV3Error("MAKER_SIDE_INVALID")
        return build_uncalibrated_gefs_fair_band(
            token_id=str(token_id),
            member_hits=hits,
            member_count=int(forecast_row.member_count),
            as_of=float(forecast_received_at),
            source_evidence_sha256=str(forecast_evidence_sha256),
            policy=self.maker_policy,
        )

    async def _maker_best_candidate_for_event(self, event: dict, compiled) -> dict | None:
        metadata = await self._station_local_eligibility(compiled)
        forecast = await self._mapped_forecast(compiled.event_id, compiled)
        distribution = self._forecast_distribution_by_sha.get(forecast.source_evidence_sha256)
        if distribution is None:
            raise AllPaperV3Error("MAKER_FORECAST_RAW_EVIDENCE_MISSING")
        exact = await self.runtime.clob.exact_event_snapshot(compiled)
        try:
            _validate_exact_snapshot(compiled, exact)
        except V4InvariantError as exc:
            if exc.code == "V4_BOOK_PROVIDER_TIMESTAMP_STALE":
                return None
            raise

        contract = strict_contract_identity(event, compiled)
        bucket_by_market = {bucket.market_id: bucket for bucket in compiled.buckets}
        active_tokens = self.maker_store.active_token_ids()
        best: dict | None = None
        for row in forecast.bucket_frequencies:
            bucket = bucket_by_market.get(row.market_id)
            if bucket is None or not bucket.trade_open:
                continue
            params = exact.parameters.get(bucket.condition_id)
            if params is None:
                continue
            for side, token in (("YES", bucket.yes_token), ("NO", bucket.no_token)):
                token = str(token or "")
                if not token or token in active_tokens:
                    continue
                book = exact.books.get(token)
                if book is None:
                    continue
                fair = self._fair_for_side(
                    token_id=token,
                    side=side,
                    forecast_row=row,
                    forecast_evidence_sha256=forecast.source_evidence_sha256,
                    forecast_received_at=float(distribution.received_at),
                )
                proposal = propose_maker_bid(
                    event_id=compiled.event_id,
                    bucket=bucket,
                    outcome=side.lower(),
                    book=book,
                    parameters=params,
                    fair=fair,
                    max_notional=float(self.paper_stake_usd),
                    minimum_conditional_edge=self.maker_policy.minimum_edge_per_share,
                )
                if proposal is None:
                    continue
                candidate = {
                    "event": event,
                    "compiled": compiled,
                    "proposal": proposal,
                    "fair": fair,
                    "forecast_evidence_sha256": forecast.source_evidence_sha256,
                    "forecast_received_at": float(distribution.received_at),
                    "contract_sha256": str(contract["sha256"]),
                    "contract_version": str(contract["version"]),
                    "book_received_at": float(book.received_at),
                    "book_hash": str(book.book_hash or ""),
                    "pre_delivery_queue_at_bid": sum(
                        float(size)
                        for price, size in book.bids
                        if abs(float(price) - float(proposal.bid_price)) <= 1e-12
                    ),
                    "exact_finished_at": float(exact.finished_at),
                }
                score = float(proposal.conditional_edge_per_share)
                if best is None or score > float(best["proposal"].conditional_edge_per_share):
                    best = candidate
        return best

    def _maker_signal_payload(self, candidate: dict, *, coverage_generation: int) -> dict:
        proposal: MakerBidProposal = candidate["proposal"]
        fair: FairValueBand = candidate["fair"]
        event = candidate["event"]
        fingerprint = _sha({
            "lane": MAKER_LANE,
            "policy_sha256": self.maker_policy.policy_sha256,
            "event_id": proposal.event_id,
            "token_id": proposal.token_id,
            "forecast_evidence_sha256": candidate["forecast_evidence_sha256"],
        })
        order_id = "maker-" + fingerprint[:40]
        expires_at = float(candidate["exact_finished_at"]) + QUOTE_DECISION_TTL_SECONDS
        return {
            "lane": MAKER_LANE,
            "evidence_class": MAKER_EVIDENCE_CLASS,
            "fingerprint": fingerprint,
            "order_id": order_id,
            "event_id": proposal.event_id,
            "market_id": proposal.market_id,
            "condition_id": proposal.condition_id,
            "token_id": proposal.token_id,
            "side": proposal.outcome.upper(),
            "bid_price": float(proposal.bid_price),
            "max_shares": float(proposal.max_shares),
            "max_notional": float(proposal.max_notional),
            "current_best_bid": proposal.current_best_bid,
            "current_best_ask": proposal.current_best_ask,
            "conditional_edge_per_share": float(proposal.conditional_edge_per_share),
            "pre_delivery_queue_at_bid": float(candidate["pre_delivery_queue_at_bid"]),
            "minimum_tick_size": float(proposal.minimum_tick_size),
            "minimum_order_size": float(proposal.minimum_order_size),
            "forecast_evidence_sha256": candidate["forecast_evidence_sha256"],
            "forecast_received_at": candidate["forecast_received_at"],
            "contract_sha256": candidate["contract_sha256"],
            "contract_version": candidate["contract_version"],
            "book_received_at": candidate["book_received_at"],
            "book_hash": candidate["book_hash"],
            "decision_expires_at": expires_at,
            "ws_generation_at_signal": int(coverage_generation),
            "fair_value_research": fair_band_research_metadata(fair, self.maker_policy),
            "maker_policy_version": self.maker_policy.version,
            "maker_policy_sha256": self.maker_policy.policy_sha256,
            "event_title": _event_title(event, proposal.event_id),
            "event_url": _event_link(event),
            "calibrated_probability": False,
            "confidence_interval": False,
            "actual_order_placed": False,
            "actual_fill_authority": False,
            "financial_authority": False,
            "automatic_order_placement": False,
        }

    @staticmethod
    def _maker_proposal_message(payload: dict) -> str:
        meta = payload["fair_value_research"]
        best_bid = payload.get("current_best_bid")
        best_ask = payload.get("current_best_ask")
        return "\n".join([
            "📌 <b>WEATHER PAPER MAKER BID — UNCALIBRATED</b>",
            f"<b>{html.escape(str(payload['event_title']))}</b>",
            f"Side: <b>{html.escape(str(payload['side']))}</b>",
            "",
            f"Hypothetical resting bid: <b>${float(payload['bid_price']):.4f}</b>",
            f"Pre-delivery best bid / ask: <b>{'n/a' if best_bid is None else f'${float(best_bid):.4f}'}</b> / <b>{'n/a' if best_ask is None else f'${float(best_ask):.4f}'}</b>",
            f"Maximum PAPER notional: <b>${float(payload['max_notional']):.2f}</b>",
            f"Visible same-price queue before delivery: <b>{float(payload['pre_delivery_queue_at_bid']):.2f} shares</b>",
            "",
            f"Raw GEFS support: <b>{100.0 * float(meta['raw_member_frequency']):.1f}%</b>",
            f"Fixed 3-member stressed reference: <b>{100.0 * float(meta['fair_lower_reference']):.1f}%</b>",
            f"Conditional reference gap at bid: <b>{100.0 * float(payload['conditional_edge_per_share']):.1f} pp</b>",
            "",
            "⚠️ The stressed reference is NOT a calibrated probability or confidence interval.",
            "The virtual order activates only after this Telegram receipt and a fresh exact-book recheck still supports the same bid.",
            "A book touch is NOT a fill. Only prospective public SELL trade prints can consume queue and create simulated shares.",
            "🚫 No real order was placed.",
        ])

    async def _maker_rebuild_same_proposal(self, payload: dict, event: dict):
        compiled = compile_strict_temperature_event(event)
        current_contract = strict_contract_identity(event, compiled)
        if str(current_contract["sha256"]) != str(payload["contract_sha256"]):
            raise AllPaperV3Error("MAKER_CONTRACT_CHANGED_AFTER_SIGNAL")
        await self._station_local_eligibility(compiled)
        forecast = await self._mapped_forecast(compiled.event_id, compiled)
        if forecast.source_evidence_sha256 != str(payload["forecast_evidence_sha256"]):
            raise AllPaperV3Error("MAKER_FORECAST_GENERATION_CHANGED_AFTER_SIGNAL")
        distribution = self._forecast_distribution_by_sha.get(forecast.source_evidence_sha256)
        if distribution is None:
            raise AllPaperV3Error("MAKER_FORECAST_RAW_EVIDENCE_MISSING")
        bucket = self._bucket_for_market(compiled, str(payload["market_id"]))
        row = self._forecast_row_for_market(forecast, str(payload["market_id"]))
        if bucket is None or row is None or not bucket.trade_open:
            raise AllPaperV3Error("MAKER_MARKET_CHANGED_AFTER_SIGNAL")
        side = str(payload["side"]).upper()
        token = bucket.yes_token if side == "YES" else bucket.no_token if side == "NO" else None
        if str(token or "") != str(payload["token_id"]):
            raise AllPaperV3Error("MAKER_TOKEN_MEANING_CHANGED_AFTER_SIGNAL")

        exact = await self.runtime.clob.exact_event_snapshot(compiled)
        _validate_exact_snapshot(compiled, exact)
        params = exact.parameters.get(bucket.condition_id)
        book = exact.books.get(str(token))
        if params is None or book is None:
            raise AllPaperV3Error("MAKER_EXACT_BOOK_OR_PARAMETERS_MISSING")
        fair = self._fair_for_side(
            token_id=str(token),
            side=side,
            forecast_row=row,
            forecast_evidence_sha256=forecast.source_evidence_sha256,
            forecast_received_at=float(distribution.received_at),
        )
        proposal = propose_maker_bid(
            event_id=compiled.event_id,
            bucket=bucket,
            outcome=side.lower(),
            book=book,
            parameters=params,
            fair=fair,
            max_notional=float(self.paper_stake_usd),
            minimum_conditional_edge=self.maker_policy.minimum_edge_per_share,
        )
        if proposal is None:
            raise AllPaperV3Error("MAKER_PROPOSAL_NO_LONGER_VALID")
        if not _same_number(proposal.bid_price, payload["bid_price"]):
            raise AllPaperV3Error("MAKER_BID_CHANGED_AFTER_SIGNAL")
        if not _same_number(proposal.fair_lower, payload["fair_value_research"]["fair_lower_reference"]):
            raise AllPaperV3Error("MAKER_FAIR_REFERENCE_CHANGED_AFTER_SIGNAL")
        return compiled, forecast, fair, proposal, book, params, exact

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

        compiled, forecast, fair, proposal, book, params, exact = await self._maker_rebuild_same_proposal(
            payload, event
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
        saved = await asyncio.to_thread(self.maker_store.save_new_order, order)
        await asyncio.to_thread(
            self.maker_store.append_research_event,
            saved.order_id,
            event_type="TELEGRAM_SIGNAL_LINKED",
            payload={
                "signal_id": int(signal_id),
                "telegram_message_id": int(telegram_message_id),
                "telegram_sent_at": float(telegram_sent_at),
                "post_delivery_exact_finished_at": float(exact.finished_at),
                "financial_authority": False,
            },
            recorded_at=float(exact.finished_at),
        )
        self._maker_orders_activated += 1
        return saved

    async def _send_maker_candidate(self, candidate: dict) -> tuple[bool, str | None]:
        proposal: MakerBidProposal = candidate["proposal"]
        await self.maker_stream.subscribe(proposal.token_id)
        await self.maker_stream.start()
        coverage = self.maker_stream.coverage(proposal.token_id)
        if coverage is None or not self.maker_stream.connected:
            return False, None
        if len(self.maker_store.active_orders()) >= self.maker_policy.max_active_orders:
            return False, None

        payload = self._maker_signal_payload(candidate, coverage_generation=coverage.generation)
        signal_id = self.positions.save_signal(
            fingerprint=str(payload["fingerprint"]),
            lane=MAKER_LANE,
            evidence_class=MAKER_EVIDENCE_CLASS,
            event_id=str(payload["event_id"]),
            market_id=str(payload["market_id"]),
            side=str(payload["side"]),
            token_id=str(payload["token_id"]),
            model_probability=float(payload["fair_value_research"]["raw_member_frequency"]),
            entry_cost=float(payload["bid_price"]),
            raw_gap=float(payload["conditional_edge_per_share"]),
            theoretical_payout=1.0,
            created_at=time.time(),
            payload=payload,
        )
        if signal_id is None:
            return False, None
        await asyncio.to_thread(self.positions.set_signal_status, signal_id, "PENDING_DELIVERY")
        try:
            message_id = await self.telegram.send_html(
                self._maker_proposal_message(payload),
                url=_event_link(candidate["event"]),
                expires_at=float(payload["decision_expires_at"]),
            )
        except DeliveryUncertain as exc:
            await asyncio.to_thread(
                self.positions.set_signal_status, signal_id, "DELIVERY_UNCERTAIN"
            )
            return True, exc.code
        except WeatherLivePaperError as exc:
            await asyncio.to_thread(
                self.positions.set_signal_status, signal_id, "DELIVERY_FAILED"
            )
            return True, exc.code

        sent_at = time.time()
        self.positions.mark_telegram_sent(signal_id, int(message_id), sent_at=sent_at)
        if sent_at >= float(payload["decision_expires_at"]):
            await asyncio.to_thread(self.positions.set_signal_status, signal_id, "EXPIRED")
            return True, "MAKER_DELIVERY_RECEIPT_AFTER_EXPIRY"
        self._maker_proposals_sent += 1
        try:
            await self._activate_maker_after_delivery(
                payload=payload,
                event=candidate["event"],
                signal_id=signal_id,
                telegram_message_id=int(message_id),
                telegram_sent_at=sent_at,
            )
        except Exception as exc:
            code = getattr(exc, "code", type(exc).__name__)
            await asyncio.to_thread(
                self.positions.set_signal_status, signal_id, "MAKER_NOT_ACTIVATED"
            )
            return True, f"MAKER_ACTIVATION:{code}"
        await asyncio.to_thread(self.positions.set_signal_status, signal_id, "MAKER_RESTING")
        return True, None

    def _cancel_maker_order(self, order: VirtualMakerOrder, reason: str) -> VirtualMakerOrder:
        if order.status not in {RESTING, PARTIALLY_SIMULATED}:
            return order
        status = EXPIRED if reason == "ORDER_EXPIRED" else CANCELLED
        updated = replace(order, status=status)
        return self.maker_store.update_order(
            order,
            updated,
            event_type="ORDER_CANCELLED" if status == CANCELLED else "ORDER_EXPIRED",
            payload={
                "reason": str(reason),
                "simulated_filled_shares_preserved": float(order.simulated_filled_shares),
                "actual_order_placed": False,
                "actual_fill_authority": False,
                "financial_authority": False,
            },
        )

    @staticmethod
    def _maker_fill_message(order: VirtualMakerOrder, simulation: MakerFillSimulation) -> str:
        return "\n".join([
            "✅ <b>PAPER MAKER FILL SIMULATED</b>",
            f"Virtual order: <code>{html.escape(order.order_id)}</code>",
            f"Side/token: <b>{html.escape(order.outcome)}</b> / <code>{html.escape(order.token_id[:14])}…</code>",
            f"Resting bid: <b>${float(order.bid_price):.4f}</b>",
            "",
            f"New simulated fill: <b>{float(simulation.new_simulated_fill_shares):.4f} shares</b>",
            f"Total simulated fill: <b>{float(simulation.total_simulated_filled_shares):.4f} / {float(order.shares):.4f}</b>",
            f"Queue ahead remaining: <b>{float(simulation.ending_queue_ahead_shares):.4f} shares</b>",
            f"Eligible public SELL-print volume observed: <b>{float(simulation.eligible_sell_print_shares):.4f}</b>",
            "",
            "This is a causal PAPER fill simulation from the prospective public Market WebSocket.",
            "🚫 No real order or fill exists.",
        ])

    async def _notify_maker_fill(
        self, order: VirtualMakerOrder, simulation: MakerFillSimulation
    ) -> None:
        try:
            message_id = await self.telegram.send_html(
                self._maker_fill_message(order, simulation)
            )
        except DeliveryUncertain:
            await asyncio.to_thread(
                self.maker_store.append_research_event,
                order.order_id,
                event_type="FILL_TELEGRAM_UNCERTAIN",
                payload={
                    "new_simulated_fill_shares": simulation.new_simulated_fill_shares,
                    "financial_authority": False,
                },
            )
            return
        except WeatherLivePaperError as exc:
            await asyncio.to_thread(
                self.maker_store.append_research_event,
                order.order_id,
                event_type="FILL_TELEGRAM_FAILED",
                payload={"error": exc.code, "financial_authority": False},
            )
            return
        await asyncio.to_thread(
            self.maker_store.append_research_event,
            order.order_id,
            event_type="FILL_TELEGRAM_SENT",
            payload={
                "telegram_message_id": int(message_id),
                "new_simulated_fill_shares": simulation.new_simulated_fill_shares,
                "financial_authority": False,
            },
        )

    async def _current_maker_evidence(self, order: VirtualMakerOrder, event: dict):
        compiled = compile_strict_temperature_event(event)
        await self._station_local_eligibility(compiled)
        forecast = await self._mapped_forecast(compiled.event_id, compiled)
        distribution = self._forecast_distribution_by_sha.get(forecast.source_evidence_sha256)
        if distribution is None:
            raise AllPaperV3Error("MAKER_FORECAST_RAW_EVIDENCE_MISSING")
        bucket = self._bucket_for_market(compiled, order.market_id)
        row = self._forecast_row_for_market(forecast, order.market_id)
        if bucket is None or row is None:
            raise AllPaperV3Error("MAKER_MARKET_NO_LONGER_IN_CONTRACT")
        side = str(order.outcome).upper()
        expected = bucket.yes_token if side == "YES" else bucket.no_token if side == "NO" else None
        if str(expected or "") != order.token_id:
            raise AllPaperV3Error("MAKER_TOKEN_MEANING_CHANGED")
        exact = await self.runtime.clob.exact_event_snapshot(compiled)
        _validate_exact_snapshot(compiled, exact)
        params = exact.parameters.get(bucket.condition_id)
        book = exact.books.get(order.token_id)
        if params is None or book is None:
            raise AllPaperV3Error("MAKER_EXACT_BOOK_OR_PARAMETERS_MISSING")
        fair = self._fair_for_side(
            token_id=order.token_id,
            side=side,
            forecast_row=row,
            forecast_evidence_sha256=forecast.source_evidence_sha256,
            forecast_received_at=float(distribution.received_at),
        )
        contract = strict_contract_identity(event, compiled)
        return compiled, forecast, fair, book, params, bucket, exact, contract

    async def _progress_maker_orders(self, by_id: dict[str, dict]) -> list[str]:
        errors: list[str] = []
        active = await asyncio.to_thread(self.maker_store.active_orders)
        for original in active:
            order = original
            try:
                expected_generation = _order_ws_generation(order)
                if (
                    not self.maker_stream.connected
                    or self.maker_stream.buffer.generation != expected_generation
                ):
                    await asyncio.to_thread(
                        self._cancel_maker_order, order, "PROSPECTIVE_WS_COVERAGE_GAP"
                    )
                    continue
                trades = self.maker_stream.buffer.trades_for_order(
                    token_id=order.token_id,
                    order_created_at=order.created_at,
                    required_generation=expected_generation,
                )
                progressed, simulation = simulate_public_trade_progression(order, trades)
                if progressed != order:
                    order = await asyncio.to_thread(
                        self.maker_store.update_order,
                        order,
                        progressed,
                        event_type="TRADE_PROGRESS",
                        payload={
                            "simulation": simulation.as_dict(),
                            "prospective_ws_generation": expected_generation,
                            "financial_authority": False,
                        },
                    )
                    if simulation.new_simulated_fill_shares > 0.0:
                        self._maker_simulated_fill_events += 1
                        await self._notify_maker_fill(order, simulation)

                if order.status == SIMULATED_FILLED:
                    continue
                now = time.time()
                if now >= order.expires_at - 1e-12:
                    await asyncio.to_thread(self._cancel_maker_order, order, "ORDER_EXPIRED")
                    continue
                event = by_id.get(order.event_id)
                if event is None:
                    # Do not infer contract disappearance from a possibly degraded
                    # discovery generation.  The short order TTL still bounds risk.
                    continue
                compiled, forecast, fair, book, params, bucket, exact, contract = (
                    await self._current_maker_evidence(order, event)
                )
                source_generation = (
                    f"{forecast.source_evidence_sha256}|ws_generation={expected_generation}"
                )
                decision = evaluate_virtual_order(
                    order,
                    fair=fair,
                    book=book,
                    parameters=params,
                    policy=self.maker_shadow_policy,
                    evaluated_at=float(exact.finished_at),
                    contract_evidence_sha256=str(contract["sha256"]),
                    source_generation=source_generation,
                    market_open=bool(bucket.trade_open),
                    accepting_orders=bool(bucket.trade_open),
                )
                updated = apply_cancel_decision(order, decision)
                if updated != order:
                    await asyncio.to_thread(
                        self.maker_store.update_order,
                        order,
                        updated,
                        event_type="ORDER_REVALIDATION",
                        payload={
                            "decision": decision.as_dict(),
                            "financial_authority": False,
                        },
                    )
            except Exception as exc:
                code = getattr(exc, "code", type(exc).__name__)
                errors.append(f"MAKER_ORDER:{original.order_id}:{code}")
                try:
                    latest = await asyncio.to_thread(
                        self.maker_store.load_order, original.order_id
                    )
                    if latest.status in {RESTING, PARTIALLY_SIMULATED}:
                        await asyncio.to_thread(
                            self._cancel_maker_order,
                            latest,
                            f"EVIDENCE_ERROR:{code}",
                        )
                except Exception as cancel_exc:
                    errors.append(
                        f"MAKER_CANCEL:{original.order_id}:{getattr(cancel_exc, 'code', type(cancel_exc).__name__)}"
                    )
        return errors

    @staticmethod
    def _maker_settlement_message(payload: dict) -> str:
        pnl = float(payload["paper_pnl"])
        roi = payload.get("paper_roi")
        if float(payload["payout_per_share"]) >= 1.0 - 1e-9:
            result = "WIN"
            icon = "✅"
        elif float(payload["payout_per_share"]) <= 1e-9:
            result = "LOSS"
            icon = "❌"
        else:
            result = "PARTIAL"
            icon = "🟡"
        roi_text = "n/a" if roi is None else f"{100.0 * float(roi):+.1f}%"
        return "\n".join([
            f"{icon} <b>PAPER MAKER RESULT — {result}</b>",
            f"Virtual order: <code>{html.escape(str(payload['order_id']))}</code>",
            f"Outcome token: <code>{html.escape(str(payload['token_id'])[:14])}…</code>",
            "",
            f"Simulated filled shares: <b>{float(payload['simulated_filled_shares']):.4f}</b>",
            f"Entry price/share: <b>${float(payload['entry_price_per_share']):.4f}</b>",
            f"Final payout/share: <b>${float(payload['payout_per_share']):.4f}</b>",
            f"Simulated capital: <b>${float(payload['simulated_capital_used']):.2f}</b>",
            f"Paper proceeds: <b>${float(payload['paper_proceeds']):.2f}</b>",
            f"Paper P&amp;L: <b>{'+' if pnl >= 0 else ''}${pnl:.2f}</b>",
            f"Paper ROI: <b>{roi_text}</b>",
            "",
            "📒 Prospective simulated maker fill only — no real order was placed.",
        ])

    def _pending_maker_settlement_notifications(self) -> list[tuple[str, dict]]:
        rows = self.maker_store.db.execute(
            """
            SELECT s.order_id,s.payload_json
            FROM weather_maker_shadow_events s
            WHERE s.event_type=?
              AND NOT EXISTS (
                SELECT 1 FROM weather_maker_shadow_events n
                WHERE n.order_id=s.order_id
                  AND n.event_type IN (?,?)
              )
            ORDER BY s.seq
            """,
            (
                MAKER_SETTLEMENT_EVENT,
                MAKER_SETTLEMENT_NOTIFY_SENT,
                MAKER_SETTLEMENT_NOTIFY_UNCERTAIN,
            ),
        ).fetchall()
        out: list[tuple[str, dict]] = []
        for row in rows:
            try:
                payload = json.loads(str(row["payload_json"]))
            except json.JSONDecodeError:
                continue
            if isinstance(payload, dict):
                out.append((str(row["order_id"]), payload))
        return out

    async def _settle_maker_orders(self) -> list[str]:
        errors: list[str] = []
        orders = await asyncio.to_thread(self.maker_store.unsettled_filled_orders)
        semaphore = asyncio.Semaphore(4)

        async def fetch(order: VirtualMakerOrder):
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
            payout = exact_token_payout(order.token_id, market)
            if payout is None:
                continue
            evidence = {
                "source": "GAMMA_CLOSED_MARKET_EXACT_TOKEN_PAYOUT",
                "market_id": order.market_id,
                "condition_id": order.condition_id,
                "token_id": order.token_id,
                "payout": float(payout),
                "checked_at": time.time(),
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
                    f"MAKER_SETTLEMENT:{order.order_id}:{getattr(exc, 'code', type(exc).__name__)}"
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

    async def _scan_new_maker_candidates(self, events: tuple[dict, ...]) -> tuple[int, list[str]]:
        errors: list[str] = []
        sent = 0
        if len(await asyncio.to_thread(self.maker_store.active_orders)) >= self.maker_policy.max_active_orders:
            return sent, errors
        evaluated = 0
        for event_id, event, compiled in self._maker_event_order(events):
            if evaluated >= self.maker_policy.max_events_per_cycle:
                break
            try:
                # Station-local eligibility rejects same-day/past/out-of-horizon rows
                # before forecast/CLOB work. Only genuine future-day rows consume the
                # maker event budget.
                await self._station_local_eligibility(compiled)
            except (V4InvariantError, WeatherStationMetadataError):
                self.positions.set_state(MAKER_CURSOR_KEY, event_id)
                continue
            evaluated += 1
            self.positions.set_state(MAKER_CURSOR_KEY, event_id)
            try:
                candidate = await self._maker_best_candidate_for_event(event, compiled)
                if candidate is None:
                    continue
                created, error = await self._send_maker_candidate(candidate)
                if created:
                    sent += 1
                if error:
                    errors.append(f"MAKER_SIGNAL:{event_id}:{error}")
            except (
                StrictWeatherContractError,
                V4InvariantError,
                WeatherCLOBError,
                WeatherForecastError,
                WeatherStationMetadataError,
                MakerResearchPolicyError,
                WeatherMakerShadowError,
                MakerTradeStreamError,
                AllPaperV3Error,
            ) as exc:
                errors.append(
                    f"MAKER_SCAN:{event_id}:{getattr(exc, 'code', type(exc).__name__)}"
                )
            except Exception as exc:
                errors.append(f"MAKER_SCAN:{event_id}:{type(exc).__name__}")
        return sent, errors

    async def run_cycle(self) -> dict:
        status = dict(await super().run_cycle())
        discovery = getattr(getattr(self, "_capturing_discovery", None), "last_result", None)
        events = tuple(getattr(discovery, "events", ()) or ()) if discovery is not None else ()
        by_id = {
            str(event.get("id") or event.get("eventId") or ""): event
            for event in events
            if isinstance(event, dict)
        }

        maker_errors: list[str] = []
        maker_errors.extend(await self._progress_maker_orders(by_id))
        maker_errors.extend(await self._settle_maker_orders())
        maker_new, scan_errors = await self._scan_new_maker_candidates(events)
        maker_errors.extend(scan_errors)
        self._maker_last_errors = maker_errors[-30:]

        summary = await asyncio.to_thread(self.maker_store.summary)
        stream_status = self.maker_stream.status()
        base_errors = list(status.get("errors") or [])
        # A maker invariant/transport failure is visible in the maker health section.
        # Preserve the already-certified weather lanes instead of declaring their
        # source collection unhealthy because the optional maker subsystem degraded.
        status.update({
            "all_paper_v3_runtime_version": ALL_PAPER_V3_RUNTIME_VERSION,
            "all_paper_v2_runtime_version": ALL_PAPER_V2_RUNTIME_VERSION,
            "maker_paper_delivery_enabled": True,
            "maker_paper_policy_version": MAKER_RESEARCH_POLICY_VERSION,
            "maker_paper_policy_sha256": self.maker_policy.policy_sha256,
            "maker_value_calibrated_probability": False,
            "maker_public_ws_prospective_fill_required": True,
            "maker_book_touch_counts_as_fill": False,
            "maker_restart_cancelled_orders": self._maker_restart_cancelled,
            "maker_new_signals_this_cycle": maker_new,
            "maker_proposals_sent_total": self._maker_proposals_sent,
            "maker_orders_activated_total": self._maker_orders_activated,
            "maker_simulated_fill_events_total": self._maker_simulated_fill_events,
            "maker_settlements_recorded_total": self._maker_settlements_recorded,
            "maker_store": summary,
            "maker_trade_stream": stream_status,
            "maker_errors": maker_errors,
            "maker_healthy": not maker_errors,
            "result_lag_paper_delivery_enabled": False,
            "result_lag_block_reason": "EXACT_WRH_CUTOFF_STATE_NOT_PROVEN",
            "financial_delivery": False,
            "financial_authority": False,
            "automatic_order_placement": False,
            "wallet_or_order_api_loaded": False,
            "errors": base_errors,
            "cycle_ok": bool(status.get("cycle_ok")) and not base_errors,
        })
        _atomic_json(self.status_path, status)
        return status


async def _main(args) -> int:
    service = AllPaperWeatherLiveV3Service(
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
