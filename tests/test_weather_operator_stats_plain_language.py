from __future__ import annotations

from polymarket_scanner.weather_only_operator_commands_v10 import (
    OperatorStateCommandControllerV10,
)
from polymarket_scanner.weather_only_paper_corrective import PAPER_EXECUTION_PROTOCOL_V4
from polymarket_scanner.weather_only_paper_post_receipt import PAPER_EXECUTION_PROTOCOL_V5


class _ResultLag:
    def stats(self):
        return {
            "total": 2,
            "open": 1,
            "resolved": 1,
            "won": 1,
            "lost": 0,
            "partial": 0,
            "resolved_capital": 10.0,
            "resolved_proceeds": 11.0,
            "pnl": 1.0,
            "roi": 0.10,
        }


class _Maker:
    def summary(self):
        return {"active_orders": 3}

    def maker_performance_by_evidence(self):
        zero = {
            "settled": 0,
            "capital": 0.0,
            "proceeds": 0.0,
            "pnl": 0.0,
            "roi": None,
        }
        research = {
            "settled": 2,
            "capital": 10.0,
            "proceeds": 11.0,
            "pnl": 1.0,
            "roi": 0.10,
        }
        return {
            "validated": dict(zero),
            "legacy_excluded": dict(zero),
            "research_excluded": research,
        }


def test_stats_is_plain_language_and_shows_all_six_strategies():
    controller = object.__new__(OperatorStateCommandControllerV10)
    controller.result_lag_store = _ResultLag()
    controller.maker_store = _Maker()

    controller._read_status = lambda: {
        "paper_telegram_delivery": True,
        "same_day_paper_delivery_enabled": True,
        "source_shock_paper_delivery_enabled": True,
        "structural_paper_delivery_enabled": True,
        "maker_paper_delivery_enabled": True,
        "result_lag_paper_delivery_enabled": True,
    }

    v5 = {
        "total": 9,
        "open": 2,
        "no_fill": 1,
        "quarantined": 3,
        "resolved": 3,
        "won": 2,
        "lost": 1,
        "partial": 0,
        "resolved_capital": 30.0,
        "resolved_proceeds": 33.0,
        "pnl": 3.0,
        "resolved_roi": 0.10,
    }
    lanes = [
        {
            "lane": "weather_forecast_raw_gap",
            "total": 5,
            "open_n": 1,
            "no_fill": 1,
            "quarantined": 1,
            "won": 1,
            "lost": 1,
            "partial": 0,
            "resolved_capital": 20.0,
            "pnl": 2.0,
        },
        {
            "lane": "weather_same_day_friend_lock",
            "total": 1,
            "open_n": 1,
            "no_fill": 0,
            "quarantined": 0,
            "won": 0,
            "lost": 0,
            "partial": 0,
            "resolved_capital": 0.0,
            "pnl": 0.0,
        },
        {
            "lane": "weather_official_extreme_new_exclusion",
            "total": 1,
            "open_n": 0,
            "no_fill": 0,
            "quarantined": 0,
            "won": 1,
            "lost": 0,
            "partial": 0,
            "resolved_capital": 10.0,
            "pnl": 1.0,
        },
        {
            "lane": "weather_binary_pair_underround",
            "total": 2,
            "open_n": 0,
            "no_fill": 0,
            "quarantined": 2,
            "won": 0,
            "lost": 0,
            "partial": 0,
            "resolved_capital": 0.0,
            "pnl": 0.0,
        },
    ]
    old = {
        "total": 4,
        "open": 0,
        "no_fill": 0,
        "quarantined": 0,
        "resolved": 4,
        "won": 2,
        "lost": 2,
        "partial": 0,
        "resolved_capital": 40.0,
        "resolved_proceeds": 39.0,
        "pnl": -1.0,
        "resolved_roi": -0.025,
    }

    controller._protocol_performance = (
        lambda protocol: (v5, lanes)
        if protocol == PAPER_EXECUTION_PROTOCOL_V5
        else (old, [])
        if protocol == PAPER_EXECUTION_PROTOCOL_V4
        else (_ for _ in ()).throw(AssertionError(protocol))
    )

    text = controller._stats_text()
    for label in (
        "Future-day forecast",
        "Same-day late-lock",
        "Official-source shock",
        "Structural underround",
        "Maker research",
        "End-of-day Result-Lag",
    ):
        assert label in text
    assert text.count("🟢 ON") == 6
    assert "Excluded old/invalid records" in text
    assert "Old test data kept only for audit" in text

    upper = text.upper()
    assert "V5" not in upper
    assert "V4" not in upper
    assert "LEGACY" not in upper
    assert "QUEUE-CERTIFIED" not in upper
    assert len(text) < 3900


def test_status_is_plain_language_and_humanizes_incomplete_book_issue(monkeypatch):
    controller = object.__new__(OperatorStateCommandControllerV10)
    controller.result_lag_store = _ResultLag()
    controller.maker_store = _Maker()
    monkeypatch.setattr(
        "polymarket_scanner.weather_only_operator_commands_v10.time.time",
        lambda: 1100.0,
    )
    controller._read_status = lambda: {
        "finished_at": 1090.0,
        "cycle_ok": False,
        "operator_all_lanes_healthy": False,
        "paper_telegram_delivery": True,
        "same_day_paper_delivery_enabled": True,
        "source_shock_paper_delivery_enabled": True,
        "structural_paper_delivery_enabled": True,
        "maker_paper_delivery_enabled": True,
        "result_lag_paper_delivery_enabled": True,
        "financial_authority": False,
        "automatic_order_placement": False,
        "errors": ["PRESCREEN_CLOB:BOOK_BATCH_INCOMPLETE"],
    }
    main = {
        "total": 127, "open": 5, "resolved": 0, "no_fill": 0,
        "quarantined": 122, "won": 0, "lost": 0, "partial": 0,
        "resolved_capital": 0.0, "resolved_proceeds": 0.0, "pnl": 0.0,
        "resolved_roi": None,
    }
    controller._protocol_performance = (
        lambda protocol: (main, [])
        if protocol == PAPER_EXECUTION_PROTOCOL_V5
        else (_ for _ in ()).throw(AssertionError(protocol))
    )

    text = controller._status_text()
    for label in (
        "Future-day forecast",
        "Same-day late-lock",
        "Official-source shock",
        "Structural underround",
        "Maker research",
        "End-of-day Result-Lag",
    ):
        assert label in text
    assert "Open main paper trades: <b>5</b>" in text
    assert "Excluded old/invalid records: <b>122</b>" in text
    assert "incomplete order-book batch" in text
    assert "V5" not in text.upper()
    assert "V4" not in text.upper()
    assert "LEGACY" not in text.upper()
    assert "CERTIFIED_QUEUE_MODEL" not in text
    assert len(text) < 3900
