from __future__ import annotations

from polymarket_scanner.weather_only_live_paper_all_signals_v2 import (
    AllSignalsV2CrashSafeStore,
)
from polymarket_scanner.weather_only_paper_positions import WeatherPaperPositionError


class _Store(AllSignalsV2CrashSafeStore):
    def __init__(self, signal):
        self._signal = signal

    def _load_signal(self, signal_id: int):
        assert signal_id == 7
        return dict(self._signal)


def _signal(*, sent=100.0, fill=99.0, expires=120.0):
    return {
        "id": 7,
        "status": "ACKNOWLEDGED",
        "lane": "weather_binary_pair_underround",
        "telegram_sent_at": sent,
        "payload_json": {
            "paper_fill_at": fill,
            "decision_expires_at": expires,
        },
    }


def test_structural_expiry_guard_rejects_receipt_at_boundary(monkeypatch):
    store = _Store(_signal(sent=120.0, fill=99.0, expires=120.0))
    monkeypatch.setattr(
        AllSignalsV2CrashSafeStore.__mro__[1],
        "ensure_position_for_signal",
        lambda self, signal_id, target_stake_usd: {"id": 1},
    )
    try:
        store.ensure_position_for_signal(7, 10.0)
    except WeatherPaperPositionError as exc:
        assert exc.code == "ALL_PAPER_STRUCTURAL_DECISION_EXPIRED"
    else:
        raise AssertionError("expired Telegram receipt was admitted")


def test_structural_expiry_guard_rejects_fill_at_boundary(monkeypatch):
    store = _Store(_signal(sent=100.0, fill=120.0, expires=120.0))
    monkeypatch.setattr(
        AllSignalsV2CrashSafeStore.__mro__[1],
        "ensure_position_for_signal",
        lambda self, signal_id, target_stake_usd: {"id": 1},
    )
    try:
        store.ensure_position_for_signal(7, 10.0)
    except WeatherPaperPositionError as exc:
        assert exc.code == "ALL_PAPER_STRUCTURAL_DECISION_EXPIRED"
    else:
        raise AssertionError("expired paper fill was admitted")


def test_structural_expiry_guard_allows_pre_expiry_evidence(monkeypatch):
    store = _Store(_signal(sent=100.0, fill=99.0, expires=120.0))
    monkeypatch.setattr(
        AllSignalsV2CrashSafeStore.__mro__[1],
        "ensure_position_for_signal",
        lambda self, signal_id, target_stake_usd: {"id": 1, "lane": "ok"},
    )
    assert store.ensure_position_for_signal(7, 10.0) == {"id": 1, "lane": "ok"}


def test_non_structural_lane_does_not_use_structural_expiry_guard(monkeypatch):
    signal = _signal(sent=120.0, fill=120.0, expires=120.0)
    signal["lane"] = "weather_same_day_friend_lock"
    store = _Store(signal)
    monkeypatch.setattr(
        AllSignalsV2CrashSafeStore.__mro__[1],
        "ensure_position_for_signal",
        lambda self, signal_id, target_stake_usd: {"id": 2},
    )
    assert store.ensure_position_for_signal(7, 10.0) == {"id": 2}
