from __future__ import annotations

"""Final all-PAPER wrapper with fail-closed runtime configuration attestation.

The all-weather PAPER service is intended to be determined by the reviewed source SHA,
its explicit CLI arguments and the isolated two-key Telegram environment file.  Generic
``pydantic-settings`` environment overrides and an ignored working-directory ``.env``
must not silently change provider/discovery/runtime behaviour under the same SHA.

This wrapper therefore requires deployment to set ``ALPHA_DISABLE_DOTENV=1`` and
requires every non-Telegram Settings field to equal its compiled default before the
service is constructed.  Telegram credentials remain the only Settings values allowed
to differ because they are explicitly supplied by the isolated service environment.

No authenticated trading, wallet, signing, order or cancellation capability is added.
"""

import argparse
import asyncio
import json
import os
from pathlib import Path

from .config import Settings, settings
from .weather_only_live_paper import (
    DEFAULT_FORECAST_CACHE_SECONDS,
    DEFAULT_FORECAST_RAW_GAP_MIN,
    DEFAULT_INTERVAL_SECONDS,
    DEFAULT_MAX_FORECAST_EVENTS,
    _atomic_json,
)
from .weather_only_live_paper_all_signals_final_v6 import FinalAllPaperWeatherLiveServiceV6
from .weather_only_live_paper_v2 import DEFAULT_PAPER_STAKE_USD


FINAL_ALL_PAPER_RUNTIME_V7_VERSION = (
    "weather_all_paper_final_v10_attested_config_defaults"
)
_DOTENV_DISABLE_TRUE = {"1", "true", "yes", "on"}
_ALLOWED_SETTINGS_OVERRIDES = {"telegram_bot_token", "telegram_chat_id"}


def assert_attested_all_paper_configuration() -> None:
    if os.environ.get("ALPHA_DISABLE_DOTENV", "").strip().lower() not in _DOTENV_DISABLE_TRUE:
        raise RuntimeError("ALL_PAPER_DOTENV_DISABLE_NOT_ASSERTED")

    candidates = {Path.cwd() / ".env", Path(__file__).resolve().parents[1] / ".env"}
    if any(path.exists() for path in candidates):
        raise RuntimeError("ALL_PAPER_IMPLICIT_DOTENV_FORBIDDEN")

    unexpected: list[str] = []
    for name, field in Settings.model_fields.items():
        if name in _ALLOWED_SETTINGS_OVERRIDES:
            continue
        if field.is_required():
            unexpected.append(f"{name}=REQUIRED_WITHOUT_COMPILED_DEFAULT")
            continue
        expected = field.default
        actual = getattr(settings, name)
        if actual != expected:
            unexpected.append(name)
    if unexpected:
        raise RuntimeError(
            "ALL_PAPER_IMPLICIT_SETTINGS_OVERRIDE:" + ",".join(sorted(unexpected))
        )


class FinalAllPaperWeatherLiveServiceV7(FinalAllPaperWeatherLiveServiceV6):
    def __init__(self, **kwargs) -> None:
        assert_attested_all_paper_configuration()
        super().__init__(**kwargs)

    async def run_cycle(self) -> dict:
        status = dict(await super().run_cycle())
        status.update(
            {
                "final_all_paper_runtime_v7_version": FINAL_ALL_PAPER_RUNTIME_V7_VERSION,
                "dotenv_loading_disabled": True,
                "implicit_nontelegram_settings_defaulted": True,
                "isolated_settings_overrides": sorted(_ALLOWED_SETTINGS_OVERRIDES),
                "terminal_invalidation_identity_strict": True,
                "operator_recent_terminal_reason_visible": True,
                "maker_proposal_queue_uncertified_label": True,
                "financial_delivery": False,
                "financial_authority": False,
                "automatic_order_placement": False,
                "wallet_or_order_api_loaded": False,
            }
        )
        _atomic_json(self.status_path, status)
        return status


async def _main(args) -> int:
    service = FinalAllPaperWeatherLiveServiceV7(
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
