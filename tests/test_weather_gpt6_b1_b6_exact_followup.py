from __future__ import annotations

import asyncio
import copy
import os
import subprocess
import sys
from datetime import date
from pathlib import Path

import pytest

from polymarket_scanner.weather_only_contract_strict import (
    StrictWeatherContractError,
    compile_strict_temperature_event,
)
from polymarket_scanner.weather_only_live_paper import PaperTelegram
from polymarket_scanner.weather_only_live_paper_final import (
    FinalPaperInvariantError,
    FinalWeatherLivePaperService,
)


ROOT = Path(__file__).resolve().parents[1]
RELEASE = "f" * 40


def _event() -> dict:
    target = date(2026, 9, 14)
    station = "KLGA"
    source = f"https://www.weather.gov/wrh/timeseries?site={station}"
    rules = (
        "This market resolves to the range containing the highest temperature on 14 Sep '26, "
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
        "id": "event-1",
        "slug": "event-1",
        "title": "Highest temperature on September 14?",
        "description": rules,
        "resolutionSource": source,
        "markets": markets,
    }


@pytest.mark.parametrize(
    "suffix",
    [
        " Settlement is determined exclusively by Weather Underground. NOAA measurements do not govern resolution.",
        " Correction: the no-data outcome is the highest bracket.",
    ],
)
def test_gpt6_b1_identical_conflicting_rule_suffix_is_rejected(suffix: str) -> None:
    event = _event()
    event["description"] += suffix
    for market in event["markets"]:
        market["description"] = event["description"]
    with pytest.raises(StrictWeatherContractError) as raised:
        compile_strict_temperature_event(event)
    assert raised.value.code == "STRICT_OPERATIVE_RULE_STRUCTURE_UNSUPPORTED"


def test_gpt6_b1_fresh_gamma_question_change_invalidates_frozen_decision() -> None:
    event = _event()
    compiled = compile_strict_temperature_event(event)
    bucket = next(row for row in compiled.buckets if row.lower == 70 and row.upper == 70)
    frozen = next(row for row in event["markets"] if row["id"] == bucket.market_id)
    fresh = copy.deepcopy(frozen)
    fresh["question"] = "Will the highest temperature be higher than 70°F?"

    with pytest.raises(FinalPaperInvariantError) as raised:
        FinalWeatherLivePaperService._validate_current_market_state(
            fresh,
            market_id=bucket.market_id,
            condition_id=bucket.condition_id,
            expected_tokens={bucket.yes_token, bucket.no_token},
            expected_question=frozen["question"],
        )
    assert raised.value.code == "FINAL_MARKET_QUESTION_CHANGED"


def test_gpt6_b6_corrective_predecessor_cannot_write_while_final_owner_is_alive(tmp_path: Path) -> None:
    release_file = tmp_path / "release.sha"
    release_file.write_text(RELEASE + "\n", encoding="utf-8")
    db_path = tmp_path / "paper.sqlite"
    status_path = tmp_path / "status.json"
    telegram = PaperTelegram(token="synthetic-not-a-token", chat_id="synthetic-not-a-chat")

    owner = FinalWeatherLivePaperService(
        db_path=db_path,
        status_path=status_path,
        release_file=release_file,
        telegram=telegram,
    )
    child_code = r'''
import asyncio
import sys
from pathlib import Path
from polymarket_scanner.weather_only_live_paper import PaperTelegram
from polymarket_scanner.weather_only_live_paper_corrective import WeatherLivePaperCorrectiveService

async def main():
    folder = Path(sys.argv[1])
    tg = PaperTelegram(token="synthetic-not-a-token", chat_id="synthetic-not-a-chat")
    service = WeatherLivePaperCorrectiveService(
        db_path=folder / "paper.sqlite",
        status_path=folder / "child-status.json",
        release_file=folder / "release.sha",
        telegram=tg,
    )
    try:
        service.positions.set_state("gpt6_b6_second_writer", "wrote")
        print("SECOND_WRITER_COMMITTED", flush=True)
    finally:
        await service.close()

asyncio.run(main())
'''
    try:
        env = dict(os.environ)
        env["PYTHONPATH"] = str(ROOT)
        child = subprocess.run(
            [sys.executable, "-c", child_code, str(tmp_path)],
            cwd=tmp_path,
            env=env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=30,
            check=False,
        )
        assert owner._runtime_lease.acquired is True
        assert child.returncode != 0, child.stdout
        assert "SECOND_WRITER_COMMITTED" not in child.stdout
        assert owner.positions.get_state("gpt6_b6_second_writer", "") == ""
        assert "WEATHER_PAPER_RUNTIME_ALREADY_OWNED" in child.stderr
    finally:
        asyncio.run(owner.close())
