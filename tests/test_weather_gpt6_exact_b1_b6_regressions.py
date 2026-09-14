from __future__ import annotations

import asyncio
import subprocess
import sys
import time
from datetime import date
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import patch

import pytest

from polymarket_scanner.weather_only_contract_strict import (
    compile_strict_temperature_event,
    strict_contract_identity,
)
from polymarket_scanner.weather_only_live_paper_corrective import WeatherLivePaperCorrectiveService
from polymarket_scanner.weather_only_live_paper_final import (
    FinalPaperInvariantError,
    FinalWeatherLivePaperService,
    _sha,
)
from polymarket_scanner.weather_only_runtime_lease import WeatherPaperRuntimeLease


def _exact_70_event() -> dict:
    target = date(2026, 9, 15)
    station = "KLGA"
    source = f"https://www.weather.gov/wrh/timeseries?site={station}"
    rules = (
        f"This market resolves to the range containing the highest temperature on {target.strftime('%d %b')} '{target.year % 100:02d}, "
        "in degrees Fahrenheit. The source is NOAA, the highest reading under the \"Temp\" column for all times on this day. "
        f"{source} The source measures temperatures to whole degrees Fahrenheit. "
        "If NOAA data is unavailable by 11:59 PM ET on the day following the observation date, "
        "the Weather Underground Daily Observations table is used. If there is no data, this market resolves to the lowest bracket. "
        "Resolution occurs once the first data point for the following date is published, or at the deadline, whichever comes first. "
        "Revisions are considered until the first datapoint for the following date, after which any alterations will not be considered."
    )
    labels = ["69°F or lower", "70°F", "71°F or higher"]
    markets = []
    for index, label in enumerate(labels):
        markets.append(
            {
                "id": f"m{index}",
                "conditionId": f"c{index}",
                "question": f"Will the highest temperature be {label}?",
                "active": True,
                "closed": False,
                "acceptingOrders": True,
                "enableOrderBook": True,
                "outcomes": ["Yes", "No"],
                "clobTokenIds": [f"t{index}y", f"t{index}n"],
                "slug": f"m{index}",
            }
        )
    return {
        "id": "event-exact-70",
        "slug": "event-exact-70",
        "title": "Highest temperature on September 15?",
        "description": rules,
        "resolutionSource": source,
        "markets": markets,
    }


class _FreshGamma:
    def __init__(self, market: dict) -> None:
        self.market = market

    async def market_by_id(self, _market_id: str):
        return dict(self.market)


def test_gpt6_p14_fresh_gamma_question_change_invalidates_frozen_decision():
    event = _exact_70_event()
    compiled = compile_strict_temperature_event(event)
    identity = strict_contract_identity(event, compiled)

    service = object.__new__(FinalWeatherLivePaperService)
    service.forecast_raw_gap_min = 0.08
    service.forecast_cache_seconds = 900.0
    service.max_forecast_events = 6
    service.paper_stake_usd = 10.0
    service.release_sha = lambda: "a" * 40

    config = FinalWeatherLivePaperService._decision_config(service)
    frozen_market = {
        "market_id": "m1",
        "question": "will the highest temperature be 70°f?",
        "operative_rules": identity["operative_rules"],
        "operative_source": identity["operative_source"],
    }
    candidate = {
        "market_id": "m1",
        "condition_id": "c1",
        "strict_contract_sha256": identity["sha256"],
        "release_sha": "a" * 40,
        "decision_config_sha256": _sha(config),
        "frozen_market_semantics": frozen_market,
        "frozen_market_semantics_sha256": _sha(frozen_market),
    }
    parent_result = {
        **candidate,
        "clob_outcome_map": {"t1y": "YES", "t1n": "NO"},
        "decision_expires_at": time.time() + 30.0,
        "quote_observed_at": time.time(),
        "semantic_digest": "parent",
    }
    changed_market = {
        "id": "m1",
        "conditionId": "c1",
        "question": "Will the highest temperature be higher than 70°F?",
        "clobTokenIds": ["t1y", "t1n"],
        "active": True,
        "closed": False,
        "acceptingOrders": True,
        "enableOrderBook": True,
    }
    service.settlement = NS(gamma=_FreshGamma(changed_market))

    async def fake_parent(_self, _candidate, _event):
        return dict(parent_result)

    with patch.object(WeatherLivePaperCorrectiveService, "_dispatch_recheck", fake_parent):
        with pytest.raises(FinalPaperInvariantError, match="FINAL_MARKET_QUESTION_CHANGED"):
            asyncio.run(service._dispatch_recheck(candidate, event))


_WRITER_CLASSES = (
    ("polymarket_scanner.weather_only_live_paper", "WeatherLivePaperService"),
    ("polymarket_scanner.weather_only_live_paper_human", "HumanReadableWeatherLivePaperService"),
    ("polymarket_scanner.weather_only_live_paper_v2", "WeatherLivePaperV2Service"),
    ("polymarket_scanner.weather_only_live_paper_v3", "WeatherLivePaperV3Service"),
    ("polymarket_scanner.weather_only_live_paper_v4", "WeatherLivePaperV4Service"),
    ("polymarket_scanner.weather_only_live_paper_corrective", "WeatherLivePaperCorrectiveService"),
    ("polymarket_scanner.weather_only_live_paper_final", "FinalWeatherLivePaperService"),
)


def test_gpt6_p44_every_retained_weather_writer_hits_common_lock_before_ledger(tmp_path: Path):
    db = tmp_path / "paper.sqlite"
    status = tmp_path / "status.json"
    release = tmp_path / "release.sha"
    release.write_text("a" * 40 + "\n", encoding="utf-8")
    owner = WeatherPaperRuntimeLease(db)
    try:
        child = r'''
import importlib
import sys
from pathlib import Path
from polymarket_scanner.weather_only_runtime_lease import WeatherPaperRuntimeLeaseError
module_name, class_name, db, status, release = sys.argv[1:]
cls = getattr(importlib.import_module(module_name), class_name)
try:
    cls(db_path=Path(db), status_path=Path(status), release_file=Path(release))
except WeatherPaperRuntimeLeaseError as exc:
    print(exc.code)
    raise SystemExit(23)
except BaseException as exc:
    print(type(exc).__name__ + ":" + str(getattr(exc, "code", exc)))
    raise SystemExit(24)
else:
    print("WRITER_CONSTRUCTOR_BYPASSED_LOCK")
    raise SystemExit(0)
'''
        for module_name, class_name in _WRITER_CLASSES:
            proc = subprocess.run(
                [
                    sys.executable,
                    "-c",
                    child,
                    module_name,
                    class_name,
                    str(db),
                    str(status),
                    str(release),
                ],
                cwd=Path(__file__).resolve().parents[1],
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
                timeout=30,
            )
            assert proc.returncode == 23, (
                f"{module_name}.{class_name} bypassed or failed after the lock boundary: "
                f"rc={proc.returncode} stdout={proc.stdout!r} stderr={proc.stderr!r}"
            )
            assert "WEATHER_PAPER_RUNTIME_ALREADY_OWNED" in proc.stdout
    finally:
        owner.close()
