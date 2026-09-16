from __future__ import annotations

from polymarket_scanner.weather_only_live_paper_all_signals_v4 import (
    _maker_stream_health,
)


def test_stream_error_makes_maker_health_fail_closed_without_touching_cycle_health():
    status = {
        "cycle_ok": True,
        "maker_healthy": True,
        "maker_trade_stream": {
            "connected": False,
            "covered_tokens": 0,
            "last_error": "MAKER_STREAM_MESSAGE_SHAPE_INVALID",
        },
    }
    health = _maker_stream_health(status)
    assert health["maker_healthy"] is False
    assert health["maker_stream_degraded"] is True
    assert health["maker_stream_last_error"] == "MAKER_STREAM_MESSAGE_SHAPE_INVALID"
    assert health["maker_fill_evidence_ready"] is False
    assert status["cycle_ok"] is True


def test_connected_covered_clean_stream_is_ready_for_fill_evidence():
    health = _maker_stream_health({
        "maker_healthy": True,
        "maker_trade_stream": {
            "connected": True,
            "covered_tokens": 3,
            "last_error": None,
        },
    })
    assert health == {
        "maker_healthy": True,
        "maker_stream_degraded": False,
        "maker_stream_last_error": None,
        "maker_fill_evidence_ready": True,
    }


def test_clean_but_not_yet_connected_stream_is_not_falsely_ready():
    health = _maker_stream_health({
        "maker_healthy": True,
        "maker_trade_stream": {
            "connected": False,
            "covered_tokens": 0,
            "last_error": None,
        },
    })
    assert health["maker_healthy"] is True
    assert health["maker_stream_degraded"] is False
    assert health["maker_fill_evidence_ready"] is False


def test_invalid_covered_token_status_is_degraded_not_exception():
    health = _maker_stream_health({
        "maker_healthy": True,
        "maker_trade_stream": {
            "connected": True,
            "covered_tokens": "not-an-int",
            "last_error": None,
        },
    })
    assert health["maker_healthy"] is False
    assert health["maker_stream_degraded"] is True
    assert health["maker_stream_last_error"] == "MAKER_STREAM_STATUS_COVERED_TOKENS_INVALID"
    assert health["maker_fill_evidence_ready"] is False


def test_existing_maker_error_stays_unhealthy_even_with_clean_stream():
    health = _maker_stream_health({
        "maker_healthy": False,
        "maker_trade_stream": {
            "connected": True,
            "covered_tokens": 1,
            "last_error": None,
        },
    })
    assert health["maker_healthy"] is False
    assert health["maker_fill_evidence_ready"] is True
