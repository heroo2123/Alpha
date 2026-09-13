from __future__ import annotations

"""Final guarded weather PAPER runtime after the deployment adversarial review.

This layer closes only the paper-experiment blockers found after the corrective
candidate review: exact contract/rule identity, fair event traversal, current
market-open state, crash-safe delivery admission/recovery, and a process-lifetime
singleton ledger lease. Same-day delivery, structural guarantees and all real-money
trading remain disabled by the inherited runtime.
"""

import argparse
import asyncio
import hashlib
import json
import time
from pathlib import Path

from .weather_only_clob import MAX_BOOK_AGE_SECONDS
from .weather_only_contract_strict import (
    compile_strict_temperature_event,
    strict_contract_identity,
)
from .weather_only_live_paper import (
    DEFAULT_FORECAST_CACHE_SECONDS,
    DEFAULT_FORECAST_RAW_GAP_MIN,
    DEFAULT_INTERVAL_SECONDS,
    DEFAULT_MAX_FORECAST_EVENTS,
    FORECAST_MAPPING_POLICY,
    _atomic_json,
)
from .weather_only_live_paper_corrective import WeatherLivePaperCorrectiveService
from .weather_only_live_paper_v2 import DEFAULT_PAPER_STAKE_USD
from .weather_only_paper_commands_canonical import CanonicalWeatherPaperCommandController
from .weather_only_paper_corrective import CorrectiveSettlementEngine
from .weather_only_paper_recovery import CrashSafeWeatherPaperStore
from .weather_only_runtime_lease import WeatherPaperRuntimeLease


FINAL_PAPER_RUNTIME_VERSION = "weather_live_paper_final_v2_b1_b6_singleton_guarded"
FINAL_MARKET_STATE_POLICY = "GAMMA_SELECTED_MARKET_OPEN_ACCEPTING_ORDERBOOK_V1"


class FinalPaperInvariantError(RuntimeError):
    def __init__(self, code: str):
        self.code = str(code)
        super().__init__(self.code)


def _canonical(value: object) -> str:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False
    )


def _sha(value: object) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _listish(value: object) -> tuple[str, ...]:
    if isinstance(value, (list, tuple)):
        return tuple(str(item) for item in value)
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except Exception:
            return ()
        if isinstance(parsed, list):
            return tuple(str(item) for item in parsed)
    return ()


class FinalWeatherLivePaperService(WeatherLivePaperCorrectiveService):
    def __init__(self, **kwargs) -> None:
        db_path = kwargs.get("db_path")
        if db_path is None:
            raise FinalPaperInvariantError("FINAL_DB_PATH_REQUIRED")
        # Close B6's post-preflight race before constructing any store or scanner
        # component. The lease is held until close(), so a second process cannot
        # become a concurrent writer after the process inventory was checked.
        self._runtime_lease = WeatherPaperRuntimeLease(db_path)
        try:
            super().__init__(**kwargs)

            # Replace the ordinary facade with a pre-send station/day reservation and
            # restart reconciler. Keep the superseded controller/settlement for close().
            self._final_superseded_settlement = self.settlement
            self._final_superseded_commands = self.commands
            self.positions = CrashSafeWeatherPaperStore(self.db_path)
            self.settlement = CorrectiveSettlementEngine(
                store=self.positions,
                telegram=self.telegram,
            )
            self.commands = CanonicalWeatherPaperCommandController(
                telegram=self.telegram,
                store=self.positions,
                status_path=self.status_path,
                paper_stake_usd=self.paper_stake_usd,
            )
            self._startup_recovery = self.positions.reconcile_crash_states()
            self._strict_rule_sha_by_event: dict[str, str] = {}
        except BaseException:
            self._runtime_lease.close()
            raise

    async def close(self) -> None:
        try:
            await asyncio.gather(
                self._final_superseded_settlement.close(),
                self._final_superseded_commands.close(),
                return_exceptions=True,
            )
            await super().close()
        finally:
            self._runtime_lease.close()

    def _decision_config(self) -> dict:
        return {
            "forecast_mapping_policy": FORECAST_MAPPING_POLICY.policy_id,
            "forecast_raw_gap_min": float(self.forecast_raw_gap_min),
            "forecast_cache_seconds": float(self.forecast_cache_seconds),
            "max_forecast_events": int(self.max_forecast_events),
            "paper_stake_usd": float(self.paper_stake_usd),
            "same_day_delivery_enabled": False,
            "structural_delivery_enabled": False,
            "automatic_order_placement": False,
        }

    async def _forecast_candidate(self, event: dict, compiled) -> dict | None:
        # B4: cursor means "examined", not "eligible". A run of ineligible same-day
        # rows therefore cannot occupy the same bounded window forever.
        examined = compile_strict_temperature_event(event)
        self.positions.set_state("v4_forecast_cursor", examined.event_id)

        rule_identity = strict_contract_identity(event, examined)
        old_rule = self._strict_rule_sha_by_event.get(examined.event_id)
        if old_rule is not None and old_rule != rule_identity["sha256"]:
            # Do not reuse a forecast-cache entry across changed operative rules.
            self._forecast_cache.clear()
        self._strict_rule_sha_by_event[examined.event_id] = rule_identity["sha256"]

        candidate = await super()._forecast_candidate(event, examined)
        if candidate is None:
            return None
        release = self.release_sha()
        config = self._decision_config()
        candidate.update({
            "strict_contract_version": rule_identity["version"],
            "strict_contract_identity": rule_identity,
            "strict_contract_sha256": rule_identity["sha256"],
            "release_sha": release,
            "decision_config": config,
            "decision_config_sha256": _sha(config),
        })
        candidate["decision_semantic_digest"] = _sha({
            "base_semantic_digest": candidate.get("semantic_digest"),
            "strict_contract_sha256": rule_identity["sha256"],
            "release_sha": release,
            "decision_config_sha256": candidate["decision_config_sha256"],
        })
        return candidate

    @staticmethod
    def _validate_current_market_state(
        market: object,
        *,
        market_id: str,
        condition_id: str,
        expected_tokens: set[str],
    ) -> dict:
        if not isinstance(market, dict):
            raise FinalPaperInvariantError("FINAL_MARKET_STATE_UNAVAILABLE")
        actual_id = str(market.get("id") or "").strip()
        actual_condition = str(
            market.get("conditionId") or market.get("condition_id") or ""
        ).strip()
        tokens = set(_listish(market.get("clobTokenIds") or market.get("clob_token_ids")))
        if actual_id != str(market_id):
            raise FinalPaperInvariantError("FINAL_MARKET_ID_MISMATCH")
        if not actual_condition or actual_condition != str(condition_id):
            raise FinalPaperInvariantError("FINAL_MARKET_CONDITION_MISMATCH")
        if tokens != set(expected_tokens):
            raise FinalPaperInvariantError("FINAL_MARKET_TOKEN_SET_MISMATCH")
        # Unknown state is not open state. Require every positive/negative bit exactly.
        if market.get("active") is not True:
            raise FinalPaperInvariantError("FINAL_MARKET_NOT_ACTIVE")
        if market.get("closed") is not False:
            raise FinalPaperInvariantError("FINAL_MARKET_CLOSED_OR_UNKNOWN")
        if market.get("acceptingOrders") is not True:
            raise FinalPaperInvariantError("FINAL_MARKET_NOT_ACCEPTING_ORDERS")
        if market.get("enableOrderBook") is not True:
            raise FinalPaperInvariantError("FINAL_MARKET_ORDERBOOK_DISABLED_OR_UNKNOWN")
        return {
            "policy": FINAL_MARKET_STATE_POLICY,
            "market_id": actual_id,
            "condition_id": actual_condition,
            "clob_token_ids": sorted(tokens),
            "active": True,
            "closed": False,
            "accepting_orders": True,
            "enable_order_book": True,
        }

    async def _dispatch_recheck(self, candidate: dict, event: dict) -> dict | None:
        compiled = compile_strict_temperature_event(event)
        current_rule = strict_contract_identity(event, compiled)
        if current_rule["sha256"] != str(candidate.get("strict_contract_sha256") or ""):
            raise FinalPaperInvariantError("FINAL_OPERATIVE_RULE_CHANGED_BEFORE_DISPATCH")
        current_release = self.release_sha()
        if current_release != str(candidate.get("release_sha") or ""):
            raise FinalPaperInvariantError("FINAL_RELEASE_CHANGED_BEFORE_DISPATCH")
        current_config = self._decision_config()
        if _sha(current_config) != str(candidate.get("decision_config_sha256") or ""):
            raise FinalPaperInvariantError("FINAL_CONFIG_CHANGED_BEFORE_DISPATCH")

        fresh = await super()._dispatch_recheck(candidate, event)
        if fresh is None:
            return None

        market_id = str(fresh.get("market_id") or "")
        condition_id = str(fresh.get("condition_id") or "")
        expected_tokens = set(str(token) for token in (fresh.get("clob_outcome_map") or {}))
        market = await self.settlement.gamma.market_by_id(market_id)
        state = self._validate_current_market_state(
            market,
            market_id=market_id,
            condition_id=condition_id,
            expected_tokens=expected_tokens,
        )
        state_received_at = time.time()
        if state_received_at >= float(fresh.get("decision_expires_at") or 0.0):
            return None
        quote_at = float(fresh.get("quote_observed_at") or 0.0)
        if quote_at <= 0.0 or state_received_at - quote_at > MAX_BOOK_AGE_SECONDS:
            raise FinalPaperInvariantError("FINAL_QUOTE_AGED_DURING_MARKET_STATE_RECHECK")

        fresh.update({
            "strict_contract_version": current_rule["version"],
            "strict_contract_identity": current_rule,
            "strict_contract_sha256": current_rule["sha256"],
            "release_sha": current_release,
            "decision_config": current_config,
            "decision_config_sha256": _sha(current_config),
            "current_market_state": state,
            "current_market_state_sha256": _sha(state),
            "current_market_state_received_at": state_received_at,
            "current_market_state_policy": FINAL_MARKET_STATE_POLICY,
        })
        fresh["decision_semantic_digest"] = _sha({
            "base_semantic_digest": fresh.get("semantic_digest"),
            "strict_contract_sha256": fresh["strict_contract_sha256"],
            "release_sha": fresh["release_sha"],
            "decision_config_sha256": fresh["decision_config_sha256"],
            "current_market_state_sha256": fresh["current_market_state_sha256"],
        })
        return fresh

    async def _save_and_send_forecast(
        self, candidate: dict, event: dict
    ) -> tuple[bool, str | None]:
        try:
            result = await super()._save_and_send_forecast(candidate, event)
        except FinalPaperInvariantError as exc:
            await self._record_skip(candidate, exc.code)
            return False, None
        finally:
            # On an ordinary return, sync the reservation to the durable signal state.
            # An actual process death skips Python cleanup and is reconciled at startup.
            self.positions.sync_reservation_for_station_day(
                str(candidate.get("station") or ""),
                str(candidate.get("target_date") or ""),
            )
        return result

    async def run_cycle(self) -> dict:
        status = dict(await super().run_cycle())
        stats = await asyncio.to_thread(self.positions.stats)
        status.update({
            "final_paper_runtime_version": FINAL_PAPER_RUNTIME_VERSION,
            "startup_crash_recovery": self._startup_recovery,
            "current_market_state_policy": FINAL_MARKET_STATE_POLICY,
            "exclusive_writer_lease": bool(self._runtime_lease.acquired),
            "paper_position_stats": stats,
            "same_day_delivery_enabled": False,
            "structural_delivery_enabled": False,
            "financial_delivery": False,
            "financial_authority": False,
            "automatic_order_placement": False,
        })
        _atomic_json(self.status_path, status)
        return status


async def _main(args) -> int:
    service = FinalWeatherLivePaperService(
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
    parser.add_argument("--paper-stake-usd", type=float, default=DEFAULT_PAPER_STAKE_USD)
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    raise SystemExit(asyncio.run(_main(args)))


if __name__ == "__main__":
    main()
