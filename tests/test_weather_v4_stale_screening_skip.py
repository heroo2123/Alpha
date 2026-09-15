from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from polymarket_scanner import weather_only_live_paper_v4 as v4


class _State:
    def set_state(self, *_args, **_kwargs):
        return None


class _CLOB:
    async def exact_event_snapshot(self, _compiled):
        return object()


def _service() -> v4.WeatherLivePaperV4Service:
    service = object.__new__(v4.WeatherLivePaperV4Service)
    service.max_forecast_events = 6
    service._v4_eligible_evaluated_this_cycle = 0
    service._v4_forecast_nonfatal_skips_this_cycle = []
    service.positions = _State()
    service.runtime = SimpleNamespace(clob=_CLOB())
    service._forecast_distribution_by_sha = {
        "forecast-evidence": SimpleNamespace(as_dict=lambda: {})
    }

    async def eligible(compiled):
        return SimpleNamespace(
            timezone="America/New_York",
            latitude=40.0,
            longitude=-74.0,
        )

    async def mapped(_event_id, _compiled):
        return SimpleNamespace(
            source_evidence_sha256="forecast-evidence",
            bucket_frequencies=(),
            mapping_policy_id="fixture",
        )

    service._station_local_eligibility = eligible
    service._mapped_forecast = mapped
    return service


def _compiled():
    return SimpleNamespace(
        event_id="123",
        station_hint="KLGA",
        target_date=SimpleNamespace(isoformat=lambda: "2026-09-16"),
        family="daily_high_temperature",
        unit="F",
        buckets=(),
    )


def test_stale_provider_timestamp_is_fail_closed_event_skip(monkeypatch):
    service = _service()
    compiled = _compiled()

    monkeypatch.setattr(
        v4,
        "compile_strict_temperature_event",
        lambda _event: compiled,
    )
    monkeypatch.setattr(
        v4,
        "_validate_exact_snapshot",
        lambda *_args: (_ for _ in ()).throw(
            v4.V4InvariantError("V4_BOOK_PROVIDER_TIMESTAMP_STALE")
        ),
    )

    result = asyncio.run(service._forecast_candidate({}, compiled))

    assert result is None
    assert service._v4_forecast_nonfatal_skips_this_cycle == [
        "FORECAST:123:V4_BOOK_PROVIDER_TIMESTAMP_STALE"
    ]


@pytest.mark.parametrize(
    "code",
    [
        "V4_CLOB_CONDITION_MISSING",
        "V4_CLOB_OUTCOME_MEANING_MISMATCH",
        "V4_CLOB_BOOK_SET_MISMATCH",
        "V4_BOOK_HASH_MISSING",
        "V4_BOOK_PROVIDER_TIMESTAMP_INVALID",
    ],
)
def test_semantic_and_identity_invariants_remain_fatal(monkeypatch, code):
    service = _service()
    compiled = _compiled()

    monkeypatch.setattr(
        v4,
        "compile_strict_temperature_event",
        lambda _event: compiled,
    )
    monkeypatch.setattr(
        v4,
        "_validate_exact_snapshot",
        lambda *_args: (_ for _ in ()).throw(v4.V4InvariantError(code)),
    )

    with pytest.raises(v4.V4InvariantError) as raised:
        asyncio.run(service._forecast_candidate({}, compiled))

    assert raised.value.code == code
    assert service._v4_forecast_nonfatal_skips_this_cycle == []
