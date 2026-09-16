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
import re
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
from .weather_only_paper_recovery_final import FinalCrashSafeWeatherPaperStore


FINAL_PAPER_RUNTIME_VERSION = "weather_live_paper_final_v4_fresh_gamma_semantic_binding"
FINAL_MARKET_STATE_POLICY = "GAMMA_SELECTED_MARKET_OPEN_SEMANTICALLY_BOUND_V2"
_FRESH_RULE_FIELDS = (
    "description",
    "rules",
    "resolutionRules",
    "resolution_rules",
    "resolutionCriteria",
    "resolution_criteria",
    "settlementRules",
    "settlement_rules",
    "settlementCriteria",
    "settlement_criteria",
)
_FRESH_SOURCE_FIELDS = (
    "resolutionSource",
    "resolution_source",
    "settlementSource",
    "settlement_source",
)


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


def _norm_semantic_text(value: object) -> str:
    text = str(value or "")
    text = text.replace("“", '"').replace("”", '"').replace("’", "'").replace("‘", "'")
    return re.sub(r"\s+", " ", text).strip().lower()


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


def _selected_market_question(event: dict, market_id: str) -> str:
    for row in event.get("markets") or []:
        if not isinstance(row, dict):
            continue
        if str(row.get("id") or "").strip() != str(market_id):
            continue
        question = _norm_semantic_text(row.get("question"))
        if question:
            return question
        break
    raise FinalPaperInvariantError("FINAL_FROZEN_MARKET_QUESTION_MISSING")


class FinalWeatherLivePaperService(WeatherLivePaperCorrectiveService):
    def __init__(self, **kwargs) -> None:
        if kwargs.get("db_path") is None:
            raise FinalPaperInvariantError("FINAL_DB_PATH_REQUIRED")
        # Singleton admission is acquired exactly once by the common writer base
        # before any ledger is opened.  Final must not take a second flock itself;
        # every retained older writer inherits the same boundary and therefore cannot
        # bypass a live final owner.
        super().__init__(**kwargs)
        try:
            self._final_superseded_settlement = self.settlement
            self._final_superseded_commands = self.commands
            self.positions = FinalCrashSafeWeatherPaperStore(self.db_path)
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
        await asyncio.gather(
            self._final_superseded_settlement.close(),
            self._final_superseded_commands.close(),
            return_exceptions=True,
        )
        await super().close()

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
        examined = compile_strict_temperature_event(event)

        rule_identity = strict_contract_identity(event, examined)
        old_rule = self._strict_rule_sha_by_event.get(examined.event_id)
        if old_rule is not None and old_rule != rule_identity["sha256"]:
            self._forecast_cache.clear()
        self._strict_rule_sha_by_event[examined.event_id] = rule_identity["sha256"]

        # Never move the durable cursor past a row that was skipped only because the
        # expensive eligible-event budget was already exhausted.  This is the key to
        # rotating 24 eligible rows through a six-row budget over successive cycles.
        budget_before = int(getattr(self, "_v4_eligible_evaluated_this_cycle", 0))
        if budget_before >= int(self.max_forecast_events):
            return None

        # With budget available, the inherited gate either rejects the row cheaply
        # (same-day/past/out-of-horizon) or consumes one eligible evaluation.  Either
        # way this row was genuinely examined, so it is safe to move the cursor past
        # it.  Advancing in finally also prevents one repeatedly failing row from
        # starving every later event forever.
        try:
            candidate = await super()._forecast_candidate(event, examined)
        finally:
            self.positions.set_state("v4_forecast_cursor", examined.event_id)

        if candidate is None:
            return None
        release = self.release_sha()
        config = self._decision_config()
        frozen_question = _selected_market_question(event, str(candidate.get("market_id") or ""))
        frozen_market_semantics = {
            "market_id": str(candidate.get("market_id") or ""),
            "question": frozen_question,
            "operative_rules": str(rule_identity.get("operative_rules") or ""),
            "operative_source": str(rule_identity.get("operative_source") or ""),
        }
        frozen_market_semantics_sha = _sha(frozen_market_semantics)
        candidate.update({
            "strict_contract_version": rule_identity["version"],
            "strict_contract_identity": rule_identity,
            "strict_contract_sha256": rule_identity["sha256"],
            "frozen_market_semantics": frozen_market_semantics,
            "frozen_market_semantics_sha256": frozen_market_semantics_sha,
            "release_sha": release,
            "decision_config": config,
            "decision_config_sha256": _sha(config),
        })
        candidate["decision_semantic_digest"] = _sha({
            "base_semantic_digest": candidate.get("semantic_digest"),
            "strict_contract_sha256": rule_identity["sha256"],
            "frozen_market_semantics_sha256": frozen_market_semantics_sha,
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
        expected_question: str | None = None,
        expected_rules: str | None = None,
        expected_source: str | None = None,
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

        current_question = _norm_semantic_text(market.get("question"))
        if expected_question is not None:
            if not current_question or current_question != _norm_semantic_text(expected_question):
                raise FinalPaperInvariantError("FINAL_MARKET_QUESTION_CHANGED")

        expected_rules_norm = _norm_semantic_text(expected_rules)
        for field in _FRESH_RULE_FIELDS:
            if field not in market or market.get(field) in (None, ""):
                continue
            value = market.get(field)
            if not isinstance(value, str):
                raise FinalPaperInvariantError("FINAL_MARKET_RULE_FIELD_INVALID")
            if not expected_rules_norm or _norm_semantic_text(value) != expected_rules_norm:
                raise FinalPaperInvariantError("FINAL_MARKET_RULES_CHANGED")

        expected_source_norm = _norm_semantic_text(expected_source)
        for field in _FRESH_SOURCE_FIELDS:
            if field not in market or market.get(field) in (None, ""):
                continue
            value = market.get(field)
            if not isinstance(value, str):
                raise FinalPaperInvariantError("FINAL_MARKET_SOURCE_FIELD_INVALID")
            if not expected_source_norm or _norm_semantic_text(value) != expected_source_norm:
                raise FinalPaperInvariantError("FINAL_MARKET_SOURCE_CHANGED")

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
            "question": current_question,
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

        frozen_market = candidate.get("frozen_market_semantics")
        if not isinstance(frozen_market, dict):
            raise FinalPaperInvariantError("FINAL_FROZEN_MARKET_SEMANTICS_MISSING")
        if _sha(frozen_market) != str(candidate.get("frozen_market_semantics_sha256") or ""):
            raise FinalPaperInvariantError("FINAL_FROZEN_MARKET_SEMANTICS_TAMPERED")

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
            expected_question=str(frozen_market.get("question") or ""),
            expected_rules=str(frozen_market.get("operative_rules") or ""),
            expected_source=str(frozen_market.get("operative_source") or ""),
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
            "frozen_market_semantics": frozen_market,
            "frozen_market_semantics_sha256": candidate["frozen_market_semantics_sha256"],
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
            "frozen_market_semantics_sha256": fresh["frozen_market_semantics_sha256"],
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
