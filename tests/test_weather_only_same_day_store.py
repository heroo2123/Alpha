from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from polymarket_scanner.weather_only_same_day_envelope import (
    SameDayEvidenceEnvelope,
    canonical_evidence_sha256,
)
from polymarket_scanner.weather_only_same_day_store import (
    SAME_DAY_RESEARCH_STATUS,
    SameDayResearchStore,
    SameDayStoreError,
)


def _envelope(*, created_at: float = 1000.0) -> SameDayEvidenceEnvelope:
    final = {
        "event_id": "event-1",
        "station": "KDAL",
        "target_date": "2026-09-12",
        "family": "DAILY_LOW_TEMP",
        "unit": "F",
        "evidence_sha256": "d" * 64,
    }
    shell = SameDayEvidenceEnvelope(
        version="fixture-envelope",
        release_sha="a" * 64,
        config_sha256="b" * 64,
        execution_protocol_id="SAME_DAY_RESEARCH_ONLY_V1",
        created_at=created_at,
        contract={"event_id": "event-1"},
        contract_semantics={
            "observation_population": "WRH_HOURLY_DATA",
            "correction_policy": "fixture",
        },
        mapping_policy={"policy_id": "fixture"},
        official_observations=({"observed_at": 10.0, "value": 70.0},),
        observed_state={"extreme_value": 70.0},
        coverage_plan={"as_of": 20.0},
        near_term_raw_evidence={"provider": "fixture"},
        near_term_verified={"evidence_sha256": "c" * 64},
        hourly_gefs_raw={"member_series": []},
        remaining_path={"path_evidence_sha256": "e" * 64},
        final_decision=final,
        envelope_sha256="0" * 64,
    )
    payload = shell.as_dict()
    payload.pop("envelope_sha256")
    return replace(shell, envelope_sha256=canonical_evidence_sha256(payload))


def test_research_envelope_persists_idempotently_and_never_becomes_position_or_pnl(tmp_path: Path):
    store = SameDayResearchStore(tmp_path / "paper.sqlite")
    envelope = _envelope()
    row_id = store.save_envelope(envelope)
    assert row_id == 1
    assert store.save_envelope(envelope) is None

    summary = store.summary()
    assert summary["total"] == 1
    assert summary["events"] == 1
    assert summary["station_days"] == 1
    assert summary["status"] == SAME_DAY_RESEARCH_STATUS
    assert summary["included_in_validated_pnl"] is False
    assert summary["same_day_delivery_enabled"] is False
    assert summary["financial_authority"] is False

    with store._conn() as db:
        row = db.execute("SELECT * FROM weather_same_day_research").fetchone()
        tables = {
            value[0]
            for value in db.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
    assert row["status"] == "RESEARCH_ONLY"
    assert row["calibrated_probability"] == 0
    assert row["same_day_delivery_enabled"] == 0
    assert row["financial_authority"] == 0
    assert "weather_paper_positions" not in tables


def test_full_envelope_json_preimage_round_trips_from_store(tmp_path: Path):
    store = SameDayResearchStore(tmp_path / "paper.sqlite")
    envelope = _envelope()
    assert store.save_envelope(envelope) == 1
    restored = store.envelope_json(envelope.envelope_sha256)
    assert restored == envelope.as_dict()
    assert canonical_evidence_sha256({k: v for k, v in restored.items() if k != "envelope_sha256"}) == envelope.envelope_sha256


def test_corrupted_envelope_digest_is_rejected_before_database_write(tmp_path: Path):
    store = SameDayResearchStore(tmp_path / "paper.sqlite")
    bad = replace(_envelope(), config_sha256="f" * 64)
    with pytest.raises(SameDayStoreError, match="SAME_DAY_STORE_ENVELOPE_INVALID:SAME_DAY_ENVELOPE_DIGEST_MISMATCH"):
        store.save_envelope(bad)
    assert store.summary()["total"] == 0


def test_same_day_store_requires_absolute_database_path():
    with pytest.raises(SameDayStoreError, match="SAME_DAY_STORE_PATH_NOT_ABSOLUTE"):
        SameDayResearchStore(Path("relative.sqlite"))
