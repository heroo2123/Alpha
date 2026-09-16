from __future__ import annotations

"""Fourth/final corrective wrapper: causal weather refresh before executable PAPER fill.

The previous corrective stack refreshed important weather evidence after Telegram, but
same-day late-lock still refreshed WRH only and the refreshed weather could arrive
*after* the exact CLOB quote used as the simulated fill. Future-day validation could
also reuse the ordinary 15-minute forecast cache.

This layer makes the causal decision boundary explicit for every directional lane:

* future-day: bypass the forecast cache, fetch the provider again, then CLOB;
* same-day late-lock: fetch and persist a fresh WRH+NWS+31-member-GEFS capture, rerun
  the exact friend-style gate, then obtain an exact CLOB snapshot that starts later;
* source shock: refetch/validate WRH first, then obtain the exact CLOB snapshot; and
* the compact weather evidence that justified admission is required by the V5 store,
  survives restart, and is part of strong execution identity.

Provider/source/assembly/storage failures in the post-receipt weather walk are
normalized to V4 invariant errors so the inherited delivery layer records a terminal
POST_RECEIPT_NOT_ACTIONABLE audit instead of turning an already delivered signal into
an unaudited lane exception.

All probabilities remain explicitly uncalibrated. No real order, wallet, signing,
cancellation or financial authority is introduced.
"""

import argparse
import asyncio
import json
import time
from pathlib import Path

from .weather_only_contract_strict import compile_strict_temperature_event
from .weather_only_contracts import DAILY_HIGH
from .weather_only_forecast import WeatherForecastError
from .weather_only_independent_review_corrective import IndependentReviewAllPaperCommandController
from .weather_only_independent_review_corrective_v3 import (
    FUTURE_DAY_KIND,
    INDEPENDENT_REVIEW_CORRECTIVE_V3_VERSION,
    POST_RECEIPT_WEATHER_EVIDENCE_VERSION,
    SAME_DAY_KIND,
    SOURCE_SHOCK_KIND,
    IndependentReviewPostReceiptStoreV3,
)
from .weather_only_live_paper import (
    DEFAULT_FORECAST_CACHE_SECONDS,
    DEFAULT_FORECAST_RAW_GAP_MIN,
    DEFAULT_INTERVAL_SECONDS,
    DEFAULT_MAX_FORECAST_EVENTS,
    _atomic_json,
)
from .weather_only_live_paper_all_signals import _friend_capture_gate
from .weather_only_live_paper_all_signals_final_v3 import FinalAllPaperWeatherLiveServiceV3
from .weather_only_live_paper_all_signals_v7 import AllPaperWeatherLiveV7Service
from .weather_only_live_paper_all_signals_v8 import AllPaperWeatherLiveV8Service, _excluded
from .weather_only_live_paper_corrective import SAME_DAY_MAPPING_POLICY
from .weather_only_live_paper_v2 import DEFAULT_PAPER_STAKE_USD
from .weather_only_live_paper_v4 import V4InvariantError
from .weather_only_paper_corrective import CorrectiveSettlementEngine
from .weather_only_rules import compile_temperature_rule_authority
from .weather_only_same_day_capture import assemble_same_day_capture
from .weather_only_same_day_contract import build_same_day_contract_semantics


FINAL_ALL_PAPER_RUNTIME_V4_VERSION = (
    "weather_all_paper_final_v7_causal_full_weather_post_receipt_refresh"
)
_EPS = 1e-9


def _post_receipt_invariant(prefix: str, exc: Exception) -> V4InvariantError:
    code = getattr(exc, "code", type(exc).__name__)
    return V4InvariantError(f"{prefix}:{code}")


class FinalAllPaperWeatherLiveServiceV4(FinalAllPaperWeatherLiveServiceV3):
    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self._v4_superseded_settlement = self.settlement
        self._v4_superseded_commands = self.commands
        self.positions = IndependentReviewPostReceiptStoreV3(self.db_path)
        self._v4_recovery = self.positions.reconcile_v5_after_restart()
        self.settlement = CorrectiveSettlementEngine(store=self.positions, telegram=self.telegram)
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
            self._v4_superseded_settlement.close(),
            self._v4_superseded_commands.close(),
            return_exceptions=True,
        )
        await super().close()

    async def _forecast_post_receipt_execution(
        self, candidate: dict, event: dict, *, telegram_sent_at: float
    ) -> dict | None:
        """Force a provider fetch after receipt; exact CLOB follows that completed fetch."""
        compiled = compile_strict_temperature_event(event)
        refresh_started = time.time()
        if refresh_started + _EPS < float(telegram_sent_at):
            raise V4InvariantError("V5_FORECAST_REFRESH_NOT_CAUSAL")
        try:
            forecast = await self._mapped_forecast(compiled.event_id, compiled, force_refresh=True)
        except WeatherForecastError as exc:
            raise V4InvariantError(f"V5_FORECAST_REFRESH_FAILED:{exc.code}") from exc
        except Exception as exc:
            raise _post_receipt_invariant("V5_FORECAST_REFRESH_FAILED", exc) from exc
        refresh_finished = time.time()
        if str(forecast.source_evidence_sha256) != str(
            candidate.get("forecast_evidence_sha256") or ""
        ):
            raise V4InvariantError("V5_FORECAST_GENERATION_CHANGED_AFTER_DELIVERY")

        execution = await super()._forecast_post_receipt_execution(
            candidate, event, telegram_sent_at=telegram_sent_at
        )
        if execution is None:
            return None
        if float(execution["post_receipt_recheck_started_at"]) + _EPS < refresh_finished:
            raise V4InvariantError("V5_FORECAST_CLOB_PRECEDES_PROVIDER_REFRESH")
        upgraded = dict(execution)
        upgraded["post_receipt_weather_evidence"] = {
            "version": POST_RECEIPT_WEATHER_EVIDENCE_VERSION,
            "kind": FUTURE_DAY_KIND,
            "provider_refresh_started_at": refresh_started,
            "provider_refresh_finished_at": refresh_finished,
            "forecast_source_evidence_sha256": str(forecast.source_evidence_sha256),
            "provider_run_age_known": False,
        }
        return upgraded

    async def _fresh_three_layer_friend_gate(
        self, candidate: dict, event: dict, *, after_time: float
    ):
        compiled = compile_strict_temperature_event(event)
        bucket = next(
            (
                row
                for row in compiled.buckets
                if row.market_id == str(candidate.get("market_id") or "")
            ),
            None,
        )
        if bucket is None or str(bucket.yes_token or "") != str(candidate.get("token_id") or ""):
            raise V4InvariantError("V5_SAME_DAY_MARKET_CHANGED")
        try:
            metadata = await self._station_metadata_for_compiled(compiled)
            wrh_result, nws_snapshot, hourly_gefs = await self._fetch_same_day_source_bundle(
                compiled, metadata
            )
        except Exception as exc:
            raise _post_receipt_invariant(
                "V5_SAME_DAY_THREE_LAYER_REFRESH_FAILED", exc
            ) from exc

        if (
            float(wrh_result.fetched_at) + _EPS < float(after_time)
            or float(nws_snapshot.received_at) + _EPS < float(after_time)
            or float(hourly_gefs.received_at) + _EPS < float(after_time)
        ):
            raise V4InvariantError("V5_SAME_DAY_THREE_LAYER_REFRESH_NOT_CAUSAL")

        as_of = time.time()
        try:
            authority = compile_temperature_rule_authority(event, compiled)
            semantics = build_same_day_contract_semantics(compiled, authority)
            capture = assemble_same_day_capture(
                compiled=compiled,
                contract_semantics=semantics,
                station_metadata=metadata,
                wrh_snapshot=wrh_result.snapshot,
                near_term_raw_snapshot=nws_snapshot,
                hourly_gefs=hourly_gefs,
                as_of=as_of,
                mapping_policy=SAME_DAY_MAPPING_POLICY,
                population_alignment_certified=False,
            )
        except Exception as exc:
            raise _post_receipt_invariant(
                "V5_SAME_DAY_THREE_LAYER_ASSEMBLY_FAILED", exc
            ) from exc

        gate = _friend_capture_gate(capture.as_dict(), compiled, bucket)
        if gate is None:
            raise V4InvariantError("V5_SAME_DAY_THREE_LAYER_THESIS_CHANGED")
        previous_support = float(candidate.get("raw_probability") or 0.0)
        fresh_support = float(gate["raw_support"])
        if fresh_support + _EPS < previous_support:
            raise V4InvariantError("V5_SAME_DAY_THREE_LAYER_SUPPORT_DETERIORATED")

        try:
            await asyncio.to_thread(self.three_layer_store.save, capture)
        except Exception as exc:
            raise _post_receipt_invariant(
                "V5_SAME_DAY_THREE_LAYER_PERSIST_FAILED", exc
            ) from exc
        evidence = {
            "version": POST_RECEIPT_WEATHER_EVIDENCE_VERSION,
            "kind": SAME_DAY_KIND,
            "as_of": float(capture.as_of),
            "capture_sha256": str(capture.capture_sha256),
            "wrh_evidence_sha256": str(wrh_result.snapshot.evidence_sha256),
            "nws_evidence_sha256": str(nws_snapshot.evidence_sha256),
            "gefs_evidence_sha256": str(hourly_gefs.evidence_sha256),
            "gefs_hits": int(gate["gefs_hits"]),
            "gefs_total": int(gate["gefs_total"]),
            "nws_sampled_extreme": float(gate["nws_sampled_extreme"]),
            "observed_extreme": float(gate["observed_extreme"]),
        }
        return compiled, bucket, capture, gate, evidence

    async def _same_day_exact_recheck(
        self, candidate: dict, event: dict, *, after_time: float | None
    ):
        if after_time is None:
            return await super()._same_day_exact_recheck(candidate, event, after_time=None)

        _compiled, _bucket, capture, gate, weather_evidence = (
            await self._fresh_three_layer_friend_gate(
                candidate, event, after_time=float(after_time)
            )
        )
        checked = await AllPaperWeatherLiveV7Service._same_day_exact_recheck(
            self, candidate, event, after_time=float(capture.as_of)
        )
        if checked is None:
            return None
        fresh, exact, bucket, params = checked
        if params.fee_rate > 0.0 and params.taker_only is not True:
            raise V4InvariantError("V5_SAME_DAY_DYNAMIC_FEE_SEMANTICS_UNPROVEN")
        if float(exact.started_at) + _EPS < float(capture.as_of):
            raise V4InvariantError("V5_SAME_DAY_CLOB_PRECEDES_THREE_LAYER_REFRESH")
        support = float(gate["raw_support"])
        cost = float(fresh["entry_cost"])
        if support - cost <= 0.0:
            return None
        upgraded = dict(fresh)
        upgraded.update(
            {
                "raw_probability": support,
                "raw_gap": support - cost,
                "post_receipt_weather_evidence": weather_evidence,
                "post_receipt_three_layer_revalidated": True,
            }
        )
        return upgraded, exact, bucket, params

    async def _source_shock_exact_recheck(
        self,
        candidate: dict,
        event: dict,
        *,
        after_time: float | None,
        decision_expires_at: float | None,
    ):
        if after_time is None:
            return await super()._source_shock_exact_recheck(
                candidate,
                event,
                after_time=None,
                decision_expires_at=decision_expires_at,
            )

        compiled = compile_strict_temperature_event(event)
        bucket = next(
            (
                row
                for row in compiled.buckets
                if row.market_id == str(candidate.get("market_id") or "")
            ),
            None,
        )
        if bucket is None or str(bucket.no_token or "") != str(candidate.get("token_id") or ""):
            raise V4InvariantError("SOURCE_SHOCK_TOKEN_MEANING_CHANGED")

        try:
            result, observed = await self._fresh_wrh_state(
                compiled, after_time=float(after_time)
            )
        except Exception as exc:
            raise _post_receipt_invariant(
                "V5_SOURCE_SHOCK_WRH_REFRESH_FAILED", exc
            ) from exc
        rows = self._fresh_wrh_rows(result)
        if len(rows) < 2:
            raise V4InvariantError("V5_SOURCE_SHOCK_WRH_THESIS_CHANGED")
        latest_at, _latest_value = rows[-1]
        previous_values = [value for _, value in rows[:-1]]
        previous = max(previous_values) if compiled.family == DAILY_HIGH else min(previous_values)
        current = float(observed.observed_state.extreme_value)
        if (
            abs(previous - float(candidate["previous_official_extreme"])) > _EPS
            or abs(current - float(candidate["new_official_extreme"])) > _EPS
            or abs(latest_at - float(candidate["latest_official_observed_at"])) > 1.0
            or not _excluded(bucket, current, compiled.family)
        ):
            raise V4InvariantError("V5_SOURCE_SHOCK_WRH_THESIS_CHANGED")

        wrh_received = float(result.fetched_at)
        checked = await AllPaperWeatherLiveV8Service._source_shock_exact_recheck(
            self,
            candidate,
            event,
            after_time=wrh_received,
            decision_expires_at=decision_expires_at,
        )
        if checked is None:
            return None
        fresh, exact, bucket, params = checked
        if float(exact.started_at) + _EPS < wrh_received:
            raise V4InvariantError("V5_SOURCE_SHOCK_CLOB_PRECEDES_WRH_REFRESH")
        upgraded = dict(fresh)
        upgraded["post_receipt_weather_evidence"] = {
            "version": POST_RECEIPT_WEATHER_EVIDENCE_VERSION,
            "kind": SOURCE_SHOCK_KIND,
            "wrh_received_at": wrh_received,
            "wrh_evidence_sha256": str(result.snapshot.evidence_sha256),
            "observed_extreme": current,
        }
        return upgraded, exact, bucket, params

    async def run_cycle(self) -> dict:
        status = dict(await super().run_cycle())
        status.update(
            {
                "final_all_paper_runtime_v4_version": FINAL_ALL_PAPER_RUNTIME_V4_VERSION,
                "independent_review_corrective_v3_version": INDEPENDENT_REVIEW_CORRECTIVE_V3_VERSION,
                "post_receipt_future_day_provider_refetch_required": True,
                "post_receipt_three_layer_thesis_revalidation_required": True,
                "post_receipt_weather_before_clob_required": True,
                "post_receipt_weather_evidence_durable": True,
                "post_receipt_weather_evidence_identity_bound": True,
                "post_receipt_source_failures_audited_not_actionable": True,
                "same_day_post_receipt_layers": ["WRH", "NWS", "GEFS31"],
                "financial_delivery": False,
                "financial_authority": False,
                "automatic_order_placement": False,
                "wallet_or_order_api_loaded": False,
            }
        )
        _atomic_json(self.status_path, status)
        return status


async def _main(args) -> int:
    service = FinalAllPaperWeatherLiveServiceV4(
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
