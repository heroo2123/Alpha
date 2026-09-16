from __future__ import annotations

import asyncio
import time
from pathlib import Path
from types import SimpleNamespace

from polymarket_scanner.weather_only_live_paper_all_signals_final_v8 import (
    FinalAllPaperWeatherLiveServiceV8,
)
from polymarket_scanner.weather_only_live_paper_all_signals_final_v9 import (
    MAX_GLOBAL_RECALL_REUSE_SECONDS,
    TELEGRAM_EDIT_ABSENT,
    FinalAllPaperWeatherLiveServiceV9,
    FinalOperatorStateTelegram,
)


ROOT = Path(__file__).resolve().parents[1]


class _Response:
    def __init__(self, status_code: int, payload: dict):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        return self._payload


class _HTTP:
    def __init__(self, response):
        self.response = response
        self.calls = 0

    async def post(self, *_args, **_kwargs):
        self.calls += 1
        return self.response


class _Telegram:
    async def edit_html(self, _message_id: int, _text: str) -> str:
        return "APPLIED"


class _BacklogStore:
    def __init__(self, count: int):
        self.rows = {
            i: {
                "signal_id": i,
                "telegram_message_id": 10_000 + i,
                "message_text": f"invalidate {i}",
            }
            for i in range(1, count + 1)
        }
        self.applied: list[int] = []

    def ensure_operator_sync_records(self):
        return 0

    def pending_operator_sync(self, limit: int = 50):
        return [self.rows[key] for key in sorted(self.rows)[:limit]]

    def mark_operator_sync_applied(self, signal_id: int):
        self.applied.append(int(signal_id))
        self.rows.pop(int(signal_id))

    def mark_operator_sync_absent(self, signal_id: int, _message_id: int):
        self.mark_operator_sync_applied(signal_id)

    def mark_operator_sync_failed(self, _signal_id: int, _error: str):
        raise AssertionError("no sync failure expected")

    def operator_sync_summary(self):
        total = len(self.rows) + len(self.applied)
        return {
            "total": total,
            "applied": len(self.applied),
            "unconfirmed": len(self.rows),
            "failed": 0,
            "healthy": not self.rows,
        }


def test_explicit_deleted_telegram_message_is_terminal_absence_not_startup_poison():
    telegram = object.__new__(FinalOperatorStateTelegram)
    telegram.token = "fixture-token"
    telegram.chat_id = "fixture-chat"
    telegram.http = _HTTP(
        _Response(
            400,
            {"ok": False, "description": "Bad Request: message to edit not found"},
        )
    )
    outcome = asyncio.run(telegram.edit_html(123, "invalidated"))
    assert outcome == TELEGRAM_EDIT_ABSENT
    assert telegram.http.calls == 1


def test_operator_sync_drains_more_than_three_restart_sized_batches_in_one_process():
    service = object.__new__(FinalAllPaperWeatherLiveServiceV9)
    service.positions = _BacklogStore(475)
    service.telegram = _Telegram()
    summary = asyncio.run(service._sync_operator_messages())
    assert summary["healthy"] is True
    assert summary["unconfirmed"] == 0
    assert summary["processed_this_pass"] == 475
    assert summary["batches_this_pass"] >= 3
    assert summary["restart_pagination_required"] is False
    assert len(service.positions.applied) == 475


def test_global_recall_cache_is_invalidated_after_five_minute_budget():
    discovery = SimpleNamespace(_global_cache_at=123.0)
    service = object.__new__(FinalAllPaperWeatherLiveServiceV9)
    service.runtime = SimpleNamespace(discovery=discovery)
    service._global_recall_certified_at = time.time() - MAX_GLOBAL_RECALL_REUSE_SECONDS - 1.0
    service._force_global_recall_if_due()
    assert discovery._global_cache_at == 0.0


def test_fresh_completed_global_census_is_timestamped_and_healthy(monkeypatch, tmp_path: Path):
    async def parent_cycle(_self):
        return {
            "cycle_ok": True,
            "operator_all_lanes_healthy": True,
            "global_weather_recall": {"complete": True, "cache_hit": False},
            "errors": [],
        }

    monkeypatch.setattr(FinalAllPaperWeatherLiveServiceV8, "run_cycle", parent_cycle)
    service = object.__new__(FinalAllPaperWeatherLiveServiceV9)
    service.runtime = SimpleNamespace(discovery=SimpleNamespace(_global_cache_at=1.0))
    service._global_recall_certified_at = 0.0
    service.status_path = tmp_path / "status.json"
    status = asyncio.run(service.run_cycle())
    assert status["global_weather_recall_fresh"] is True
    assert status["global_weather_recall_age_seconds"] is not None
    assert status["global_weather_recall_age_seconds"] <= MAX_GLOBAL_RECALL_REUSE_SECONDS
    assert status["operator_all_lanes_healthy"] is True


def test_root_custody_and_final_v9_are_wired_into_deployment_files():
    install = (ROOT / "deploy/install-weather-paper-host-trust.sh").read_text()
    snapshot = (ROOT / "deploy/weather-paper-host-snapshot.sh").read_text()
    wrapper = (ROOT / "deploy/snapshot-all-paper-rollback.sh").read_text()
    recovery = (ROOT / "deploy/weather-paper-host-recovery.sh").read_text()
    prepare = (ROOT / "deploy/prepare-all-paper-candidate-v3.sh").read_text()
    renderer = (ROOT / "deploy/render-all-paper-unit.py").read_text()
    attester = (ROOT / "deploy/attest-all-paper-runtime-v2.py").read_text()

    root_dir = "/var/lib/polymarket-weather-paper-rollback"
    assert f'ROLLBACK_DIR="{root_dir}"' in install
    assert 'install -d -o root -g "${DEPLOY_GID}" -m 0750 "${ROLLBACK_DIR}"' in install
    assert '[[ "${EUID}" == "0" ]]' in snapshot
    assert "all-paper-rollback-v4-root-custody-hash-bound" in snapshot
    assert "rollback-manifest-v4.json" in snapshot
    assert "exec sudo /usr/bin/env -i" in wrapper

    assert root_dir in recovery
    assert "PASS_ROOT_CUSTODY_ROLLBACK_MANIFEST" in recovery
    assert "rollback artifact digest invalid" in recovery
    assert recovery.index("PASS_ROOT_CUSTODY_ROLLBACK_MANIFEST") < recovery.index("sudo install -o root -g root -m 0644")

    assert root_dir in prepare
    assert "rollback-manifest-v4.json" in prepare
    assert "snapshot-generation-v4" in prepare
    assert "weather_only_live_paper_all_signals_final_v9" in prepare
    assert "weather_only_live_paper_all_signals_final_v9" in renderer
    assert "weather_only_live_paper_all_signals_final_v9" in attester


def test_legacy_user_writable_rollback_path_is_not_authoritative_anymore():
    authoritative = [
        ROOT / "deploy/weather-paper-host-snapshot.sh",
        ROOT / "deploy/weather-paper-host-recovery.sh",
        ROOT / "deploy/prepare-all-paper-candidate-v3.sh",
    ]
    for path in authoritative:
        text = path.read_text()
        assert '${CONFIG_DIR}/all-paper-rollback' not in text
