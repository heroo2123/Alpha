from __future__ import annotations

"""Final V10: immutable runtime provenance, semantic truth, terminal sync, maker evidence."""

import argparse
import asyncio
import json
import os
import site
import sys
from pathlib import Path

from .weather_only_live_paper import DEFAULT_FORECAST_CACHE_SECONDS, DEFAULT_FORECAST_RAW_GAP_MIN, DEFAULT_INTERVAL_SECONDS, DEFAULT_MAX_FORECAST_EVENTS, WeatherLivePaperError, _atomic_json
from .weather_only_live_paper_all_signals_final_v9 import (
    FINAL_ALL_PAPER_RUNTIME_V9_VERSION,
    MAX_GLOBAL_RECALL_REUSE_SECONDS,
    TELEGRAM_EDIT_ABSENT,
    FinalAllPaperWeatherLiveServiceV9,
    FinalOperatorStateTelegram,
)
from .weather_only_live_paper_v2 import DEFAULT_PAPER_STAKE_USD
from .weather_only_operator_state_corrective_v5 import OPERATOR_STATE_CORRECTIVE_V5_VERSION, OperatorStatePostReceiptStoreV5
from .weather_only_maker_paper_accounting_v6 import MAKER_PAPER_ACCOUNTING_V6_VERSION, MakerPaperAccountingStoreV6
from .weather_only_operator_commands_v10 import OperatorStateCommandControllerV10
from .weather_only_paper_corrective import CorrectiveSettlementEngine

FINAL_ALL_PAPER_RUNTIME_V10_VERSION = "final_all_paper_v10_immutable_runtime_semantic_terminal_maker_evidence"
FORBIDDEN_CODE_ENV = (
    "PYTHONPATH","PYTHONHOME","PYTHONUSERBASE","PYTHONSTARTUP","PYTHONINSPECT",
    "PYTHONWARNINGS","PYTHONBREAKPOINT","LD_PRELOAD","LD_LIBRARY_PATH",
)


def _approved_module_origins() -> tuple[bool, str | None]:
    raw = os.environ.get("ALPHA_RUNTIME_SOURCE")
    if not raw:
        return False, None
    root = Path(raw).resolve()
    package_root = Path(__file__).resolve().parents[1]
    if package_root != root:
        raise RuntimeError("ALL_PAPER_RUNTIME_SOURCE_MODULE_ROOT_MISMATCH")
    for name, module in tuple(sys.modules.items()):
        if not name.startswith("polymarket_scanner"):
            continue
        path = getattr(module, "__file__", None)
        if not path:
            continue
        resolved = Path(path).resolve()
        if resolved != root and root not in resolved.parents:
            raise RuntimeError("ALL_PAPER_APPLICATION_MODULE_OUTSIDE_APPROVED_SOURCE:" + name)
    return True, str(root)


class FinalAllPaperWeatherLiveServiceV10(FinalAllPaperWeatherLiveServiceV9):
    def __init__(self, **kwargs) -> None:
        leaked = [x for x in FORBIDDEN_CODE_ENV if x in os.environ]
        if leaked:
            raise RuntimeError("ALL_PAPER_CODE_LOADING_ENVIRONMENT_FORBIDDEN:" + ",".join(sorted(leaked)))
        if os.environ.get("PYTHONNOUSERSITE") != "1" or site.ENABLE_USER_SITE is not False:
            raise RuntimeError("ALL_PAPER_USER_SITE_NOT_DISABLED")
        deployment_identity = os.environ.get("ALPHA_RELEASE_SHA")
        if deployment_identity:
            source_ok, source_path = _approved_module_origins()
            if not source_ok:
                raise RuntimeError("ALL_PAPER_APPROVED_RUNTIME_SOURCE_MISSING")
            if deployment_identity != self_release_from_env():
                raise RuntimeError("ALL_PAPER_RELEASE_ENV_INVALID")
        else:
            source_path = None

        super().__init__(**kwargs)
        old_positions = self.positions
        old_maker = self.maker_store
        old_settlement = self.settlement
        old_commands = self.commands
        self.positions = OperatorStatePostReceiptStoreV5(self.db_path)
        self.positions.reconcile_v5_after_restart()
        self.positions.ensure_operator_sync_records()
        old_maker.close()
        self.maker_store = MakerPaperAccountingStoreV6(self.db_path)
        self.settlement = CorrectiveSettlementEngine(store=self.positions, telegram=self.telegram)
        self.commands = OperatorStateCommandControllerV10(
            telegram=self.telegram, store=self.positions, maker_store=self.maker_store,
            status_path=self.status_path, paper_stake_usd=self.paper_stake_usd
        )
        self._v10_old = (old_positions, old_settlement, old_commands)
        self._v10_runtime_source = source_path

    async def _terminalize_delivered_signal(self, signal_id: int, candidate: dict, *, status: str, reason: str) -> None:
        await asyncio.to_thread(
            self.positions.terminalize_delivered_signal,
            signal_id,
            terminal_status=status,
            reason=str(reason),
            decision_id=str(candidate.get("decision_id") or candidate.get("event_id") or signal_id),
            event_id=str(candidate.get("event_id") or ""),
            market_id=str(candidate.get("market_id") or "") or None,
            side=str(candidate.get("side") or "") or None,
            token_id=str(candidate.get("token_id") or "") or None,
        )
        sync = await self._sync_operator_messages()
        if sync.get("healthy") is not True or list(sync.get("errors") or []):
            raise WeatherLivePaperError("DELIVERED_TERMINAL_OPERATOR_SYNC_FAILED")

    async def run_cycle(self) -> dict:
        status = dict(await super().run_cycle())
        recall = dict(status.get("global_weather_recall") or {})
        semantic_complete = recall.get("weather_semantic_coverage_complete") is True
        policy = str(recall.get("weather_semantic_product_policy") or "")
        source_ok, source_path = _approved_module_origins() if os.environ.get("ALPHA_RUNTIME_SOURCE") else (False, None)
        status.update({
            "final_all_paper_runtime_v10_version": FINAL_ALL_PAPER_RUNTIME_V10_VERSION,
            "final_all_paper_runtime_v9_version": FINAL_ALL_PAPER_RUNTIME_V9_VERSION,
            "operator_state_corrective_v5_version": OPERATOR_STATE_CORRECTIVE_V5_VERSION,
            "maker_paper_accounting_v6_version": MAKER_PAPER_ACCOUNTING_V6_VERSION,
            "gamma_census_complete": recall.get("gamma_census_complete") is True,
            "gamma_census_completed_at": recall.get("gamma_census_completed_at"),
            "gamma_census_age_seconds": recall.get("gamma_census_age_seconds"),
            "total_active_events_scanned": int(recall.get("total_active_events_scanned") or 0),
            "weather_looking_events_discovered": int(recall.get("weather_looking_events") or 0),
            "strict_supported_weather_events": int(recall.get("strict_supported_events") or 0),
            "unsupported_weather_events": int(recall.get("unsupported_weather_events") or 0),
            "unsupported_weather_reason_counts": dict(recall.get("unsupported_reason_counts") or {}),
            "unsupported_weather_examples": list(recall.get("unsupported_examples") or []),
            "weather_semantic_coverage_complete": semantic_complete,
            "weather_semantic_coverage_status": recall.get("weather_semantic_coverage_status"),
            "weather_semantic_product_policy": policy,
            "global_weather_coverage_complete": semantic_complete,
            "global_weather_recall_complete": recall.get("gamma_census_complete") is True,
            "code_loading_environment_isolated": True,
            "python_user_site_disabled": True,
            "runtime_sys_path": list(sys.path),
            "runtime_python_executable": str(Path(sys.executable).resolve()),
            "runtime_prefix": sys.prefix,
            "runtime_base_prefix": sys.base_prefix,
            "runtime_source_path": source_path,
            "application_modules_from_approved_source": source_ok,
            "maker_legacy_queue_pnl_excluded": True,
            "financial_authority": False,
            "automatic_order_placement": False,
            "wallet_or_order_api_loaded": False,
        })
        if policy != "STRICT_SUPPORTED_SUBSET":
            status["cycle_ok"] = False
            status["operator_all_lanes_healthy"] = False
            status.setdefault("errors", []).append("WEATHER_SEMANTIC_POLICY_INVALID")
        if os.environ.get("ALPHA_RELEASE_SHA") and not source_ok:
            status["cycle_ok"] = False
            status["operator_all_lanes_healthy"] = False
            status.setdefault("errors", []).append("IMMUTABLE_RUNTIME_SOURCE_UNPROVEN")
        _atomic_json(self.status_path, status)
        return status


def self_release_from_env() -> str:
    value = str(os.environ.get("ALPHA_RELEASE_SHA") or "").strip().lower()
    if len(value) != 40 or any(c not in "0123456789abcdef" for c in value):
        raise RuntimeError("ALL_PAPER_RELEASE_ENV_INVALID")
    return value


async def _main(a):
    service = FinalAllPaperWeatherLiveServiceV10(
        db_path=a.db,status_path=a.status,release_file=a.release_file,
        interval_seconds=a.interval_seconds,forecast_cache_seconds=a.forecast_cache_seconds,
        forecast_raw_gap_min=a.forecast_raw_gap_min,max_forecast_events=a.max_forecast_events,
        paper_stake_usd=a.paper_stake_usd,
    )
    try:
        if a.once:
            await service.send_startup()
            status = await service.run_cycle()
            print(json.dumps(status,sort_keys=True,indent=2))
            return 0 if status.get("operator_all_lanes_healthy") is True else 2
        await service.loop()
        return 0
    finally:
        await service.close()


def main():
    p=argparse.ArgumentParser()
    p.add_argument("--db",type=Path,required=True); p.add_argument("--status",type=Path,required=True); p.add_argument("--release-file",type=Path,required=True)
    p.add_argument("--interval-seconds",type=float,default=DEFAULT_INTERVAL_SECONDS); p.add_argument("--forecast-cache-seconds",type=float,default=DEFAULT_FORECAST_CACHE_SECONDS)
    p.add_argument("--forecast-raw-gap-min",type=float,default=DEFAULT_FORECAST_RAW_GAP_MIN); p.add_argument("--max-forecast-events",type=int,default=DEFAULT_MAX_FORECAST_EVENTS)
    p.add_argument("--paper-stake-usd",type=float,default=DEFAULT_PAPER_STAKE_USD); p.add_argument("--once",action="store_true")
    raise SystemExit(asyncio.run(_main(p.parse_args())))


if __name__=="__main__":
    main()
