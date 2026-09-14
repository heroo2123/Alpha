from __future__ import annotations

import asyncio
import copy
import os
import subprocess
import sys
from datetime import date
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import patch

import pytest

from polymarket_scanner.weather_only_contract_strict import (
    StrictWeatherContractError,
    compile_strict_temperature_event,
)
from polymarket_scanner.weather_only_live_paper_corrective import (
    WeatherLivePaperCorrectiveService,
)
from polymarket_scanner.weather_only_live_paper_final import FinalWeatherLivePaperService


ROOT = Path(__file__).resolve().parents[1]
BRANCH = "weather-same-day-synoptic-pws-2026-09-14"


def _event(*, eid: str = "event-1", target: date = date(2026, 9, 14)) -> dict:
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
    labels = ["69°F or lower", "70-71°F", "72°F or higher"]
    markets = []
    for index, label in enumerate(labels):
        markets.append(
            {
                "id": f"{eid}-m{index}",
                "conditionId": f"{eid}-c{index}",
                "question": f"Will the highest temperature be {label}?",
                "active": True,
                "closed": False,
                "acceptingOrders": True,
                "enableOrderBook": True,
                "outcomes": ["Yes", "No"],
                "clobTokenIds": [f"{eid}-t{index}y", f"{eid}-t{index}n"],
                "slug": f"{eid}-m{index}",
            }
        )
    return {
        "id": eid,
        "slug": eid,
        "title": f"Highest temperature on {target.strftime('%B')} {target.day}?",
        "description": rules,
        "resolutionSource": source,
        "markets": markets,
    }


def test_interrupted_gpt6_b1_opposite_settlement_statistic_fails_closed():
    event = _event()
    event["description"] += (
        " For settlement, the minimum temperature reading determines the applicable range."
    )
    with pytest.raises(
        StrictWeatherContractError,
        match="STRICT_OPERATIVE_RULE_STRUCTURE_UNSUPPORTED",
    ):
        compile_strict_temperature_event(event)


def test_interrupted_gpt6_b1_settlement_alias_cannot_hide_conflicting_rules():
    event = _event()
    event["markets"][1]["settlementRules"] = (
        event["description"]
        + " If the observed value is disputed, this market resolves YES."
    )
    with pytest.raises(StrictWeatherContractError):
        compile_strict_temperature_event(event)


def test_interrupted_gpt6_b1_exact_reported_shared_rule_suffixes_fail_closed():
    suffixes = (
        " Settlement is determined exclusively by Weather Underground. NOAA measurements do not govern resolution.",
        " Correction: the no-data outcome is the highest bracket.",
    )
    for suffix in suffixes:
        event = _event()
        event["description"] += suffix
        for market in event["markets"]:
            market["description"] = event["description"]
        with pytest.raises(
            StrictWeatherContractError,
            match="STRICT_OPERATIVE_RULE_STRUCTURE_UNSUPPORTED",
        ):
            compile_strict_temperature_event(event)


def test_interrupted_gpt6_b1_canonical_contract_still_compiles():
    compiled = compile_strict_temperature_event(_event())
    assert compiled.event_id == "event-1"
    assert compiled.exactly_one_outcome_proven is True
    assert compiled.financial_authority is False


class _StateStore:
    def __init__(self) -> None:
        self.values: dict[str, object] = {}

    def get_state(self, key: str, default=None):
        return self.values.get(key, default)

    def set_state(self, key: str, value) -> None:
        self.values[str(key)] = value


def test_interrupted_gpt6_b4_twenty_four_eligible_events_rotate_six_per_cycle():
    events = [_event(eid=f"event-{index:02d}") for index in range(24)]
    service = object.__new__(FinalWeatherLivePaperService)
    service.positions = _StateStore()
    service.max_forecast_events = 6
    service._v4_eligible_evaluated_this_cycle = 0
    service._strict_rule_sha_by_event = {}
    service._forecast_cache = {}

    evaluated: list[str] = []

    async def fake_parent(self, _event_row, compiled):
        if self._v4_eligible_evaluated_this_cycle >= self.max_forecast_events:
            return None
        self._v4_eligible_evaluated_this_cycle += 1
        evaluated.append(str(compiled.event_id))
        return None

    with patch.object(
        WeatherLivePaperCorrectiveService,
        "_forecast_candidate",
        fake_parent,
    ):
        for _cycle in range(4):
            service._v4_eligible_evaluated_this_cycle = 0
            selected = service._certified_forecast_events(events)
            assert len(selected) == 24
            for event, compiled in selected:
                asyncio.run(service._forecast_candidate(event, compiled))

    assert evaluated == [f"event-{index:02d}" for index in range(24)]
    assert service.positions.get_state("v4_forecast_cursor") == "event-23"


def _write(path: Path, content: str = "") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _make_minimal_candidate_repo(path: Path) -> str:
    subprocess.run(["git", "init", "-b", BRANCH, str(path)], check=True, stdout=subprocess.PIPE)
    required = [
        "deploy/verify-runtime-release.sh",
        "deploy/render-weather-paper-unit.py",
        "deploy/check-weather-paper-network.py",
        "deploy/check-synoptic-pws.py",
        "deploy/check-weather-paper-service-isolation.sh",
        "deploy/pre-release-weather-paper-backup.sh",
        "deploy/setup-weather-paper-backup-service.sh",
        "deploy/preflight-weather-paper-deployment.sh",
        "deploy/start-weather-paper-candidate.sh",
        "deploy/verify-weather-paper-first-cycle.py",
        "deploy/verify-synoptic-pws-status.py",
        "deploy/enable-weather-paper-persistence.sh",
        "deploy/extract-weather-paper-env.py",
        "polymarket_scanner/weather_only_live_paper_corrective.py",
        "polymarket_scanner/weather_only_live_paper_final.py",
        "polymarket_scanner/weather_only_live_paper_synoptic.py",
        "polymarket_scanner/weather_only_synoptic_pws.py",
        "polymarket_scanner/weather_only_synoptic_pws_guarded.py",
        "polymarket_scanner/weather_only_pws.py",
        "polymarket_scanner/weather_only_pws_store.py",
        "polymarket_scanner/weather_only_paper_recovery.py",
        "polymarket_scanner/weather_only_paper_recovery_final.py",
        "polymarket_scanner/weather_only_runtime_attestation.py",
        "polymarket_scanner/weather_only_deployment_acceptance.py",
        "polymarket_scanner/weather_only_network_preflight.py",
        "polymarket_scanner/weather_only_paper_backup.py",
    ]
    for relative in required:
        _write(path / relative)
    _write(path / "requirements.txt")
    _write(path / "polymarket_scanner/__init__.py")
    _write(
        path / "polymarket_scanner/weather_only_live_paper_final.py",
        "FINAL_TEST_IMPORT = True\n",
    )
    _write(
        path / "polymarket_scanner/weather_only_synoptic_pws_guarded.py",
        "GUARDED_SYNOPTIC_TEST_IMPORT = True\n",
    )
    _write(
        path / "polymarket_scanner/weather_only_live_paper_synoptic.py",
        "from . import weather_only_synoptic_pws_guarded\nSYNOPTIC_FINAL_TEST_IMPORT = True\n",
    )
    _write(
        path / "deploy/render-weather-paper-unit.py",
        "polymarket_scanner.weather_only_live_paper_synoptic\nweather-paper-release.sha\n",
    )
    _write(
        path / "deploy/verify-runtime-release.sh",
        "#!/usr/bin/env bash\nset -e\n[[ \"$(git -C \"$1\" rev-parse HEAD)\" == \"$(tr -d '[:space:]' < \"$2\")\" ]]\n",
    )
    subprocess.run(["git", "-C", str(path), "config", "user.email", "ci@example.invalid"], check=True)
    subprocess.run(["git", "-C", str(path), "config", "user.name", "CI"], check=True)
    subprocess.run(["git", "-C", str(path), "add", "."], check=True)
    subprocess.run(["git", "-C", str(path), "commit", "-m", "fixture"], check=True, stdout=subprocess.PIPE)
    return subprocess.check_output(
        ["git", "-C", str(path), "rev-parse", "HEAD"], text=True
    ).strip()


def test_interrupted_gpt6_b5_prepare_script_accepts_its_own_fresh_no_checkout_clone(tmp_path: Path):
    source = tmp_path / "source"
    sha = _make_minimal_candidate_repo(source)
    app = tmp_path / "fresh-app"
    config = tmp_path / "config"
    unrelated = tmp_path / "unrelated"
    unrelated.mkdir()

    wrappers = tmp_path / "bin"
    wrappers.mkdir()
    _write(wrappers / "systemctl", "#!/usr/bin/env bash\nexit 3\n")
    _write(wrappers / "pgrep", "#!/usr/bin/env bash\nexit 1\n")
    os.chmod(wrappers / "systemctl", 0o755)
    os.chmod(wrappers / "pgrep", 0o755)

    env = dict(os.environ)
    env.update(
        {
            "ALPHA_WEATHER_APP_DIR": str(app),
            "ALPHA_CONFIG_DIR": str(config),
            "ALPHA_WEATHER_REPOSITORY_URL": str(source),
            "PATH": str(wrappers) + os.pathsep + env.get("PATH", ""),
        }
    )
    result = subprocess.run(
        [
            "bash",
            str(ROOT / "deploy" / "prepare-weather-paper-candidate.sh"),
            sha,
            BRANCH,
        ],
        cwd=unrelated,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
        timeout=90,
    )
    assert result.returncode == 0, result.stderr
    assert subprocess.check_output(
        ["git", "-C", str(app), "rev-parse", "HEAD"], text=True
    ).strip() == sha
    assert (config / "weather-paper-release.sha").read_text(encoding="utf-8").strip() == sha
    assert "candidate prepared but NOT started or enabled" in result.stdout
