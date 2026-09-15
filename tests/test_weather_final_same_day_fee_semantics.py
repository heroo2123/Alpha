from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from polymarket_scanner.weather_only_live_paper_all_signals_final import (
    FinalAllPaperWeatherLiveService,
)
from polymarket_scanner.weather_only_live_paper_all_signals_v8 import (
    AllPaperWeatherLiveV8Service,
)
from polymarket_scanner.weather_only_live_paper_v4 import V4InvariantError


def _service() -> FinalAllPaperWeatherLiveService:
    return object.__new__(FinalAllPaperWeatherLiveService)


def _checked(*, fee_rate: float, taker_only: bool | None):
    params = SimpleNamespace(fee_rate=fee_rate, taker_only=taker_only)
    return ({"entry_cost": 0.5}, object(), object(), params)


def _assert_same_components(actual, expected) -> None:
    assert actual is not None
    assert len(actual) == len(expected) == 4
    for observed, wanted in zip(actual, expected):
        assert observed is wanted


def test_same_day_positive_fee_without_taker_only_proof_fails_closed(monkeypatch):
    async def inherited(self, candidate, event, *, after_time):
        return _checked(fee_rate=0.02, taker_only=None)

    monkeypatch.setattr(AllPaperWeatherLiveV8Service, "_same_day_exact_recheck", inherited)
    with pytest.raises(V4InvariantError) as exc:
        asyncio.run(
            _service()._same_day_exact_recheck({}, {}, after_time=None)
        )
    assert exc.value.code == "V5_SAME_DAY_DYNAMIC_FEE_SEMANTICS_UNPROVEN"


def test_same_day_positive_fee_with_taker_only_proof_is_allowed(monkeypatch):
    expected = _checked(fee_rate=0.02, taker_only=True)

    async def inherited(self, candidate, event, *, after_time):
        return expected

    monkeypatch.setattr(AllPaperWeatherLiveV8Service, "_same_day_exact_recheck", inherited)
    actual = asyncio.run(_service()._same_day_exact_recheck({}, {}, after_time=123.0))
    _assert_same_components(actual, expected)


def test_same_day_zero_fee_does_not_require_taker_only_metadata(monkeypatch):
    expected = _checked(fee_rate=0.0, taker_only=None)

    async def inherited(self, candidate, event, *, after_time):
        return expected

    monkeypatch.setattr(AllPaperWeatherLiveV8Service, "_same_day_exact_recheck", inherited)
    actual = asyncio.run(_service()._same_day_exact_recheck({}, {}, after_time=None))
    _assert_same_components(actual, expected)


def test_same_day_none_from_inherited_recheck_stays_none(monkeypatch):
    async def inherited(self, candidate, event, *, after_time):
        return None

    monkeypatch.setattr(AllPaperWeatherLiveV8Service, "_same_day_exact_recheck", inherited)
    assert asyncio.run(_service()._same_day_exact_recheck({}, {}, after_time=None)) is None
